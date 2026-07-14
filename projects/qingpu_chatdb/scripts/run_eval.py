#!/usr/bin/env python3
"""Run deterministic Qingpu SQL evaluations.

The module exposes one deep interface, :func:`run_evaluation`.  ``gold`` mode
runs answer/refusal contracts, while ``hardened`` mode exercises SQL-guard
allow/reject contracts.  Both modes share database setup, guarded execution,
metrics, failure classification, and report rendering.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PROJECT_DIR.parents[1]
APP_SRC = REPO_DIR / "packages" / "dbgpt-app" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from dbgpt_app.scene.chat_db.safe_sql import (  # noqa: E402
    ReadOnlySqlGuard,
    SafeSqlExecutor,
    SqlGuardError,
)
from dbgpt_app.scene.chat_db.semantic_catalog import SemanticCatalog  # noqa: E402

try:
    from .expected_results import (
        DEFAULT_ARTIFACT,
        DEFAULT_LOCK,
        ExpectedResultsBundle,
        load_expected_results,
    )
    from .init_local_db import initialize_database
except ImportError:  # direct script execution
    from expected_results import (
        DEFAULT_ARTIFACT,
        DEFAULT_LOCK,
        ExpectedResultsBundle,
        load_expected_results,
    )
    from init_local_db import initialize_database

DEFAULT_DATABASE = PROJECT_DIR / "data" / "qingpu.db"
DEFAULT_CATALOG = PROJECT_DIR / "config" / "catalog.yaml"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "eval" / "runs"
DEFAULT_DATASETS = {
    "gold": PROJECT_DIR / "eval" / "gold.jsonl",
    "hardened": PROJECT_DIR / "eval" / "hardened.jsonl",
}


@dataclass(frozen=True)
class ComparisonResult:
    matched: bool
    schema_matched: bool
    reason: str | None = None


@dataclass
class CaseResult:
    case_id: str
    category: str
    expected_action: str
    actual_action: str
    passed: bool
    elapsed_ms: float
    table_accuracy: float | None = None
    column_accuracy: float | None = None
    result_correct: bool | None = None
    safety_correct: bool | None = None
    expected_code: str | None = None
    actual_code: str | None = None
    row_count: int | None = None
    prepared_sql: str | None = None
    rewrites: list[str] = field(default_factory=list)
    failure_reason: str | None = None


@dataclass(frozen=True)
class EvaluationConfig:
    mode: str
    dataset_path: Path
    database_path: Path = DEFAULT_DATABASE
    catalog_path: Path = DEFAULT_CATALOG
    output_dir: Path = DEFAULT_OUTPUT_DIR
    expected_results_path: Path = DEFAULT_ARTIFACT
    expected_results_lock_path: Path = DEFAULT_LOCK
    max_rows: int = 50
    reset_database: bool = False


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load JSONL contracts and reject structurally invalid datasets."""
    if not path.exists():
        raise FileNotFoundError(f"Evaluation dataset does not exist: {path}")
    records: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON on {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(f"Expected object on {path}:{line_number}")
            case_id = record.get("case_id")
            if not isinstance(case_id, str) or not case_id.strip():
                raise ValueError(f"Missing case_id on {path}:{line_number}")
            if case_id in case_ids:
                raise ValueError(f"Duplicate case_id {case_id!r} in {path}")
            case_ids.add(case_id)
            records.append(record)
    if not records:
        raise ValueError(f"Evaluation dataset is empty: {path}")
    return records


def _connect_executor(
    database_path: Path, catalog_path: Path, max_rows: int
) -> SafeSqlExecutor:
    catalog = SemanticCatalog.load(catalog_path)

    def execute_to_df(sql: str) -> pd.DataFrame:
        uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            return pd.read_sql_query(sql, connection)

    return SafeSqlExecutor(
        ReadOnlySqlGuard(catalog), execute_to_df, dialect="sqlite", max_rows=max_rows
    )


def _nested(record: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    value: Any = record
    for key in keys:
        if not isinstance(value, Mapping) or key not in value:
            return default
        value = value[key]
    return value


def _expectation(record: Mapping[str, Any], name: str, default: Any = None) -> Any:
    direct = record.get(name)
    if direct is not None:
        return direct
    return _nested(record, "expectation", name, default=default)


def _as_names(values: Any) -> set[str]:
    if not values:
        return set()
    if isinstance(values, str):
        return {values.casefold()}
    if isinstance(values, Mapping):
        values = values.get("required", [])
    return {str(value).casefold() for value in values}


def _required_sources(record: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    tables = _expectation(record, "tables", {})
    if isinstance(tables, Mapping):
        required_tables = _as_names(tables.get("required", []))
        required_columns = tables.get("required_columns", {})
        columns = {
            f"{table}.{column}".casefold()
            for table, names in required_columns.items()
            for column in names
        }
    else:
        required_tables = _as_names(tables)
        columns = set()
    columns.update(_as_names(_expectation(record, "required_columns", [])))
    return required_tables, columns


def _set_accuracy(expected: set[str], actual: set[str]) -> float | None:
    """Exact-set accuracy; absent expectations are not included in aggregates."""
    if not expected:
        return None
    return 1.0 if expected == actual else 0.0


def _is_null(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _values_equal(left: Any, right: Any, abs_tol: float, rel_tol: float) -> bool:
    if _is_null(left) or _is_null(right):
        return _is_null(left) and _is_null(right)
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), abs_tol=abs_tol, rel_tol=rel_tol)
    return left == right


def _row_equal(
    left: Sequence[Any],
    right: Sequence[Any],
    tolerances: Sequence[tuple[float, float]],
) -> bool:
    return len(left) == len(right) and all(
        _values_equal(a, b, abs_tol, rel_tol)
        for a, b, (abs_tol, rel_tol) in zip(left, right, tolerances)
    )


def compare_dataframes(
    actual: pd.DataFrame,
    expected: pd.DataFrame,
    policy: Mapping[str, Any] | None = None,
) -> ComparisonResult:
    """Compare DataFrames with schema, order, duplicate, null, and numeric semantics."""
    policy = policy or {}
    mapping = policy.get("column_mapping", "by_name")
    mode = policy.get("mode", "rows_unordered")
    numeric = policy.get("numeric", {})
    default_abs = float(numeric.get("abs", policy.get("abs_tol", 1e-6)))
    default_rel = float(numeric.get("rel", policy.get("rel_tol", 1e-9)))
    per_column = policy.get("per_column", {})

    if mapping == "by_name":
        if Counter(map(str, actual.columns)) != Counter(map(str, expected.columns)):
            return ComparisonResult(False, False, "schema_mismatch")
        actual = actual.loc[:, list(expected.columns)]
    elif mapping == "by_position":
        if len(actual.columns) != len(expected.columns):
            return ComparisonResult(False, False, "schema_mismatch")
    else:
        return ComparisonResult(False, False, f"unsupported_column_mapping:{mapping}")

    if mode == "schema_only":
        return ComparisonResult(True, True)
    if actual.shape != expected.shape:
        return ComparisonResult(False, True, "shape_mismatch")

    tolerances = []
    for column in expected.columns:
        override = per_column.get(str(column), {}).get("numeric", {})
        tolerances.append(
            (
                float(override.get("abs", default_abs)),
                float(override.get("rel", default_rel)),
            )
        )

    actual_rows = list(actual.itertuples(index=False, name=None))
    expected_rows = list(expected.itertuples(index=False, name=None))
    if mode in {"scalar", "rows_ordered"}:
        matched = all(
            _row_equal(actual_row, expected_row, tolerances)
            for actual_row, expected_row in zip(actual_rows, expected_rows)
        )
    elif mode == "rows_unordered":
        unmatched = list(actual_rows)
        matched = True
        for expected_row in expected_rows:
            index = next(
                (
                    i
                    for i, actual_row in enumerate(unmatched)
                    if _row_equal(actual_row, expected_row, tolerances)
                ),
                None,
            )
            if index is None:
                matched = False
                break
            unmatched.pop(index)
        matched = matched and not unmatched
    else:
        return ComparisonResult(False, True, f"unsupported_comparison_mode:{mode}")
    return ComparisonResult(matched, True, None if matched else "value_mismatch")


def _literal_dataframe(
    literal: Any, columns: Sequence[str] | None = None
) -> pd.DataFrame:
    if isinstance(literal, list):
        if not literal:
            return pd.DataFrame(columns=columns)
        if isinstance(literal[0], Mapping):
            return pd.DataFrame(literal)
        return pd.DataFrame(literal, columns=columns)
    if isinstance(literal, Mapping):
        return pd.DataFrame([literal])
    name = columns[0] if columns else "value"
    return pd.DataFrame([{name: literal}])


def _expected_dataframe(
    record: Mapping[str, Any], expected_results: ExpectedResultsBundle
) -> pd.DataFrame:
    result = _expectation(record, "result", {}) or {}
    if not isinstance(result, Mapping):
        return _literal_dataframe(result)
    source = result.get("source")
    if source == "frozen":
        case_id = str(record.get("case_id") or "")
        gold_sql = _expectation(record, "gold_sql")
        if not gold_sql:
            raise ValueError("answer case with frozen result requires gold_sql")
        return expected_results.dataframe(case_id, str(gold_sql))
    if source == "literal":
        columns = [
            item.get("name")
            for item in _nested(record, "expectation", "columns", "items", default=[])
            if item.get("name")
        ]
        return _literal_dataframe(result.get("literal"), columns)
    if source == "execute_gold":
        raise ValueError(
            "execute_gold is forbidden during scoring; seal a frozen artifact first"
        )
    raise ValueError(f"Unsupported result source: {source}")


def _comparison_policy(record: Mapping[str, Any]) -> Mapping[str, Any]:
    return _nested(record, "evaluation", "comparison", default={}) or record.get(
        "comparison", {}
    )


def _run_gold_case(
    record: Mapping[str, Any],
    executor: SafeSqlExecutor,
    expected_results: ExpectedResultsBundle,
) -> CaseResult:
    case_id = str(record["case_id"])
    category = str(
        _nested(
            record, "category", "task", default=record.get("category", "uncategorized")
        )
    )
    expected_action = str(_expectation(record, "action", "answer"))
    started = time.perf_counter()
    base = dict(case_id=case_id, category=category, expected_action=expected_action)
    if expected_action == "refuse":
        actual_action = record.get("prediction_action")
        expected_reason = _nested(
            record, "expectation", "refusal", "reason_code", default=None
        )
        actual_reason = record.get("prediction_refusal_reason_code")
        correct = actual_action == "refuse" and (
            expected_reason is None or actual_reason == expected_reason
        )
        return CaseResult(
            **base,
            actual_action=str(actual_action or "missing"),
            passed=correct,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            safety_correct=correct,
            failure_reason=None if correct else "refusal_prediction_mismatch",
        )

    candidate_sql = record.get("candidate_sql")
    if not candidate_sql:
        return CaseResult(
            **base,
            actual_action="missing",
            passed=False,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            failure_reason="missing_candidate_sql",
        )
    try:
        expected = _expected_dataframe(record, expected_results)
        guarded = executor.execute(str(candidate_sql))
        comparison = compare_dataframes(
            guarded.dataframe, expected, _comparison_policy(record)
        )
        expected_tables, expected_columns = _required_sources(record)
        actual_tables = {name.casefold() for name in guarded.prepared.referenced_tables}
        actual_columns = {
            name.casefold() for name in guarded.prepared.referenced_columns
        }
        table_accuracy = _set_accuracy(expected_tables, actual_tables)
        column_accuracy = _set_accuracy(expected_columns, actual_columns)
        table_gate = table_accuracy is None or table_accuracy == 1.0
        column_gate = column_accuracy is None or column_accuracy == 1.0
        passed = comparison.matched and table_gate and column_gate
        return CaseResult(
            **base,
            actual_action="answer",
            passed=passed,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            table_accuracy=table_accuracy,
            column_accuracy=column_accuracy,
            result_correct=comparison.matched,
            row_count=len(guarded.dataframe),
            prepared_sql=guarded.prepared.sql,
            rewrites=[rewrite.kind for rewrite in guarded.prepared.rewrites],
            failure_reason=(
                comparison.reason
                if not comparison.matched
                else "required_source_mismatch"
                if not table_gate or not column_gate
                else None
            ),
        )
    except SqlGuardError as exc:
        return CaseResult(
            **base,
            actual_action="blocked",
            passed=False,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            actual_code=exc.code,
            result_correct=False,
            failure_reason=f"guard:{exc.code}",
        )
    except Exception as exc:  # evaluation must record one bad case, not abort the run
        return CaseResult(
            **base,
            actual_action="error",
            passed=False,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            result_correct=False,
            failure_reason=f"execution:{type(exc).__name__}:{exc}",
        )


def _run_hardened_case(
    record: Mapping[str, Any], executor: SafeSqlExecutor
) -> CaseResult:
    case_id = str(record["case_id"])
    category = str(record.get("category", "security"))
    expected_code = str(record.get("expected_code", "OK"))
    expected_allowed = bool(record.get("expected_allowed", expected_code == "OK"))
    started = time.perf_counter()
    sql = record.get("input_sql") or record.get("sql")
    if not sql:
        return CaseResult(
            case_id=case_id,
            category=category,
            expected_action="allow" if expected_allowed else "block",
            actual_action="missing",
            passed=False,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            expected_code=expected_code,
            safety_correct=False,
            failure_reason="missing_input_sql",
        )
    try:
        guarded = executor.execute(str(sql))
        actual_code = "OK"
        rewrite_kinds = [rewrite.kind for rewrite in guarded.prepared.rewrites]
        expected_rewrite = record.get("expected_rewrite")
        rewrite_correct = not expected_rewrite or expected_rewrite in rewrite_kinds
        expected_limit = record.get("expected_limit")
        limit_correct = expected_limit is None or guarded.prepared.limit == int(
            expected_limit
        )
        expected_row_count = record.get("expected_row_count")
        row_count_correct = expected_row_count is None or len(guarded.dataframe) == int(
            expected_row_count
        )
        correct = (
            expected_allowed
            and expected_code == actual_code
            and rewrite_correct
            and limit_correct
            and row_count_correct
        )
        reason = None
        if not rewrite_correct:
            reason = "rewrite_mismatch"
        elif not limit_correct:
            reason = "limit_mismatch"
        elif not row_count_correct:
            reason = "row_count_mismatch"
        elif not correct:
            reason = "expected_block_but_allowed"
        return CaseResult(
            case_id=case_id,
            category=category,
            expected_action="allow" if expected_allowed else "block",
            actual_action="allow",
            passed=correct,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            safety_correct=correct,
            expected_code=expected_code,
            actual_code=actual_code,
            row_count=len(guarded.dataframe),
            prepared_sql=guarded.prepared.sql,
            rewrites=rewrite_kinds,
            failure_reason=reason,
        )
    except SqlGuardError as exc:
        correct = not expected_allowed and exc.code == expected_code
        return CaseResult(
            case_id=case_id,
            category=category,
            expected_action="allow" if expected_allowed else "block",
            actual_action="block",
            passed=correct,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            safety_correct=correct,
            expected_code=expected_code,
            actual_code=exc.code,
            failure_reason=None if correct else "guard_code_mismatch",
        )
    except Exception as exc:
        return CaseResult(
            case_id=case_id,
            category=category,
            expected_action="allow" if expected_allowed else "block",
            actual_action="error",
            passed=False,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            safety_correct=False,
            expected_code=expected_code,
            failure_reason=f"execution:{type(exc).__name__}:{exc}",
        )


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def summarize(results: Sequence[CaseResult]) -> dict[str, Any]:
    def average(name: str) -> float | None:
        values = [getattr(result, name) for result in results]
        present = [float(value) for value in values if value is not None]
        return statistics.fmean(present) if present else None

    failures = Counter(
        result.failure_reason for result in results if result.failure_reason is not None
    )
    latencies = [result.elapsed_ms for result in results]
    blocked_contracts = [
        result for result in results if result.expected_action == "block"
    ]
    allowed_contracts = [
        result for result in results if result.expected_action == "allow"
    ]

    def contract_rate(cases: Sequence[CaseResult]) -> float | None:
        if not cases:
            return None
        return statistics.fmean(result.safety_correct for result in cases)

    return {
        "total": len(results),
        "passed": sum(result.passed for result in results),
        "failed": sum(not result.passed for result in results),
        "pass_rate": statistics.fmean(result.passed for result in results),
        "table_accuracy": average("table_accuracy"),
        "column_accuracy": average("column_accuracy"),
        "result_accuracy": average("result_correct"),
        "dangerous_interception_rate": contract_rate(blocked_contracts),
        "allow_contract_rate": contract_rate(allowed_contracts),
        "safety_interception_accuracy": contract_rate(blocked_contracts),
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies, default=0.0),
        },
        "failure_reasons": dict(failures.most_common()),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]

    def pct(value: Any) -> str:
        return "N/A" if value is None else f"{float(value) * 100:.2f}%"

    lines = [
        f"# SQL Evaluation Report: {report['run']['mode']}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Cases | {summary['total']} |",
        f"| Passed | {summary['passed']} |",
        f"| Failed | {summary['failed']} |",
        f"| Pass rate | {pct(summary['pass_rate'])} |",
        f"| Table accuracy | {pct(summary['table_accuracy'])} |",
        f"| Column accuracy | {pct(summary['column_accuracy'])} |",
        f"| Result accuracy | {pct(summary['result_accuracy'])} |",
        (
            "| Dangerous interception rate | "
            f"{pct(summary['dangerous_interception_rate'])} |"
        ),
        f"| Allow contract rate | {pct(summary['allow_contract_rate'])} |",
        f"| P50 latency | {summary['latency_ms']['p50']:.3f} ms |",
        f"| P95 latency | {summary['latency_ms']['p95']:.3f} ms |",
        "",
        "## Failure Reasons",
        "",
    ]
    failures = summary["failure_reasons"]
    if failures:
        lines.extend(["| Reason | Count |", "|---|---:|"])
        lines.extend(f"| `{reason}` | {count} |" for reason, count in failures.items())
    else:
        lines.append("No failures.")
    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| Case | Category | Expected | Actual | Status | Latency ms | Failure |",
            "|---|---|---|---|---|---:|---|",
        ]
    )
    for case in report["cases"]:
        failure = (case.get("failure_reason") or "").replace("|", "\\|")
        lines.append(
            f"| {case['case_id']} | {case['category']} | "
            f"{case['expected_action']} | {case['actual_action']} | "
            f"{'PASS' if case['passed'] else 'FAIL'} | "
            f"{case['elapsed_ms']:.3f} | {failure} |"
        )
    return "\n".join(lines) + "\n"


def run_evaluation(config: EvaluationConfig) -> dict[str, Any]:
    """Initialize the fixture, execute one dataset, and persist both reports."""
    if config.mode not in DEFAULT_DATASETS:
        raise ValueError(f"Unsupported mode: {config.mode}")
    if config.reset_database or not config.database_path.exists():
        initialize_database(config.database_path, reset=config.reset_database)
    records = load_jsonl(config.dataset_path)
    executor = _connect_executor(
        config.database_path, config.catalog_path, config.max_rows
    )
    expected_results: ExpectedResultsBundle | None = None
    if config.mode == "gold":
        expected_results = load_expected_results(
            config.expected_results_path,
            config.expected_results_lock_path,
            catalog_path=config.catalog_path,
        )
        answer_case_ids = {
            str(record["case_id"])
            for record in records
            if _expectation(record, "action", "answer") == "answer"
        }
        artifact_case_ids = set(expected_results.cases)
        if answer_case_ids != artifact_case_ids:
            missing = sorted(answer_case_ids - artifact_case_ids)
            orphaned = sorted(artifact_case_ids - answer_case_ids)
            raise ValueError(
                "Frozen expected-result coverage mismatch: "
                f"missing={missing}, orphaned={orphaned}"
            )
        results = [
            _run_gold_case(record, executor, expected_results) for record in records
        ]
    else:
        results = [_run_hardened_case(record, executor) for record in records]

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report = {
        "run": {
            "run_id": run_id,
            "mode": config.mode,
            "dataset": str(config.dataset_path.resolve()),
            "database": str(config.database_path.resolve()),
            "catalog": str(config.catalog_path.resolve()),
            "expected_results": (
                str(expected_results.artifact_path)
                if expected_results is not None
                else None
            ),
            "expected_results_sha256": (
                expected_results.artifact_sha256
                if expected_results is not None
                else None
            ),
            "max_rows": config.max_rows,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "summary": summarize(results),
        "cases": [asdict(result) for result in results],
    }
    config.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{config.mode}_{run_id}"
    json_path = config.output_dir / f"{stem}.json"
    markdown_path = config.output_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    report["artifacts"] = {
        "json": str(json_path.resolve()),
        "markdown": str(markdown_path.resolve()),
    }
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=sorted(DEFAULT_DATASETS), required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--expected-results", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--expected-results-lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--max-rows", type=int, default=50)
    parser.add_argument("--reset-db", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_evaluation(
        EvaluationConfig(
            mode=args.mode,
            dataset_path=(args.dataset or DEFAULT_DATASETS[args.mode]).resolve(),
            database_path=args.database.resolve(),
            catalog_path=args.catalog.resolve(),
            output_dir=args.output_dir.resolve(),
            expected_results_path=args.expected_results.resolve(),
            expected_results_lock_path=args.expected_results_lock.resolve(),
            max_rows=args.max_rows,
            reset_database=args.reset_db,
        )
    )
    summary = report["summary"]
    json_artifact = report["artifacts"]["json"]
    markdown_artifact = report["artifacts"]["markdown"]
    print(
        f"{args.mode}: {summary['passed']}/{summary['total']} passed; "
        f"JSON={json_artifact}; Markdown={markdown_artifact}"
    )
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
