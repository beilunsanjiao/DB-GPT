#!/usr/bin/env python3
"""Lint Qingpu gold evaluation datasets before they are scored.

The external seam is :func:`lint_dataset`: callers provide a JSONL path and a
semantic catalog, and receive every structural and corpus-level problem in one
report.  SQL parsing, quota accounting, duplicate detection, metric coverage,
and determinism rules remain implementation details of this module.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PROJECT_DIR.parents[1]
APP_SRC = REPO_DIR / "packages" / "dbgpt-app" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from dbgpt_app.scene.chat_db.semantic_catalog import SemanticCatalog  # noqa: E402

DEFAULT_DATASET = PROJECT_DIR / "eval" / "gold.jsonl"
DEFAULT_CATALOG = PROJECT_DIR / "config" / "catalog.yaml"
DEFAULT_CATEGORY_QUOTAS = {
    "single_metric": 15,
    "aggregation": 7,
    "trend": 6,
    "comparison": 6,
    "topn": 5,
    "join": 5,
    "null_handling": 3,
    "refusal": 3,
}
_NONDETERMINISTIC_FUNCTIONS = {
    "CURRENT_DATE",
    "CURRENT_DATETIME",
    "CURRENT_TIME",
    "CURRENT_TIMESTAMP",
    # SQLGlot normalizes SQLite RANDOM() to the cross-dialect RAND node/name.
    "RAND",
    "RANDOMBLOB",
    "UUID",
}
_DYNAMIC_TIME_LITERALS = {"now", "localtime", "utc"}
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class DatasetLintPolicy:
    category_quotas: Mapping[str, int] = field(
        default_factory=lambda: dict(DEFAULT_CATEGORY_QUOTAS)
    )
    require_all_catalog_metrics: bool = True


@dataclass(frozen=True)
class LintIssue:
    code: str
    message: str
    case_id: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class DatasetLintReport:
    dataset: str
    case_count: int
    category_counts: Mapping[str, int]
    metric_count: int
    issues: tuple[LintIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def require_valid(self) -> None:
        if not self.ok:
            details = "; ".join(
                f"{issue.code}{f'[{issue.case_id}]' if issue.case_id else ''}: "
                f"{issue.message}"
                for issue in self.issues
            )
            raise ValueError(f"Dataset lint failed: {details}")


def _load_records(
    path: Path,
) -> tuple[list[tuple[int, dict[str, Any]]], list[LintIssue]]:
    records: list[tuple[int, dict[str, Any]]] = []
    issues: list[LintIssue] = []
    if not path.exists():
        return [], [LintIssue("DATASET_NOT_FOUND", str(path))]
    with path.open(encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                issues.append(LintIssue("INVALID_JSON", str(exc), line=line_number))
                continue
            if not isinstance(record, dict):
                issues.append(
                    LintIssue(
                        "RECORD_NOT_OBJECT",
                        "record must be an object",
                        line=line_number,
                    )
                )
                continue
            records.append((line_number, record))
    if not records and not issues:
        issues.append(LintIssue("EMPTY_DATASET", "dataset contains no records"))
    return records, issues


def _category(record: Mapping[str, Any]) -> str:
    category = record.get("category")
    if isinstance(category, Mapping):
        return str(category.get("task", ""))
    return str(category or "")


def _normalize_text(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").strip()).casefold()


def _add_duplicates(
    issues: list[LintIssue],
    seen: dict[str, tuple[str, str]],
    kind: str,
    value: Any,
    case_id: str,
) -> None:
    normalized = _normalize_text(value)
    if not normalized:
        return
    if normalized in seen:
        first_id, _ = seen[normalized]
        issues.append(
            LintIssue(
                f"DUPLICATE_{kind}",
                f"duplicates {first_id}",
                case_id=case_id,
            )
        )
    else:
        seen[normalized] = (case_id, str(value))


def _validate_date_window(
    window: Any, *, case_id: str, line: int, issues: list[LintIssue]
) -> None:
    if not isinstance(window, Mapping):
        issues.append(
            LintIssue(
                "MISSING_DATE_WINDOW",
                "answer must declare expectation.date_window with start and end",
                case_id,
                line,
            )
        )
        return
    start = window.get("start")
    end = window.get("end")
    try:
        start_date = date.fromisoformat(str(start))
        end_date = date.fromisoformat(str(end))
    except (TypeError, ValueError):
        issues.append(
            LintIssue(
                "INVALID_DATE_WINDOW",
                "start and end must be ISO dates",
                case_id,
                line,
            )
        )
        return
    if start_date > end_date:
        issues.append(
            LintIssue(
                "INVALID_DATE_WINDOW",
                "start must not be after end",
                case_id,
                line,
            )
        )


def _validate_answer_contract(
    record: Mapping[str, Any], *, case_id: str, line: int, issues: list[LintIssue]
) -> None:
    expectation = record.get("expectation")
    if not isinstance(expectation, Mapping):
        issues.append(
            LintIssue("MISSING_EXPECTATION", "answer needs expectation", case_id, line)
        )
        return
    tables = expectation.get("tables")
    required_tables = tables.get("required") if isinstance(tables, Mapping) else None
    required_columns = (
        tables.get("required_columns") if isinstance(tables, Mapping) else None
    )
    if not isinstance(required_tables, list) or not required_tables:
        issues.append(
            LintIssue(
                "MISSING_REQUIRED_TABLES",
                "answer must list required tables",
                case_id,
                line,
            )
        )
    if not isinstance(required_columns, Mapping) or not required_columns:
        issues.append(
            LintIssue(
                "MISSING_REQUIRED_COLUMNS",
                "answer must map required columns",
                case_id,
                line,
            )
        )
    elif isinstance(required_tables, list):
        for table in required_tables:
            columns = required_columns.get(table)
            if not isinstance(columns, list) or not columns:
                issues.append(
                    LintIssue(
                        "MISSING_REQUIRED_COLUMNS",
                        f"required table {table!r} has no required columns",
                        case_id,
                        line,
                    )
                )
    _validate_date_window(
        expectation.get("date_window"), case_id=case_id, line=line, issues=issues
    )
    result = expectation.get("result")
    if not isinstance(result, Mapping):
        issues.append(
            LintIssue(
                "MISSING_RESULT_ARTIFACT",
                "answer must declare expectation.result",
                case_id,
                line,
            )
        )
    else:
        source = result.get("source")
        if source not in {"literal", "frozen"}:
            issues.append(
                LintIssue(
                    "INVALID_RESULT_ARTIFACT",
                    "result.source must be literal or frozen",
                    case_id,
                    line,
                )
            )
        if source == "literal" and "literal" not in result:
            issues.append(
                LintIssue(
                    "INVALID_RESULT_ARTIFACT",
                    "literal result must contain result.literal",
                    case_id,
                    line,
                )
            )
        if source == "frozen" and not expectation.get("gold_sql"):
            issues.append(
                LintIssue(
                    "INVALID_RESULT_ARTIFACT",
                    "frozen result requires expectation.gold_sql",
                    case_id,
                    line,
                )
            )


def _validate_refusal_contract(
    record: Mapping[str, Any], *, case_id: str, line: int, issues: list[LintIssue]
) -> None:
    expectation = record.get("expectation")
    refusal = expectation.get("refusal") if isinstance(expectation, Mapping) else None
    reason = refusal.get("reason_code") if isinstance(refusal, Mapping) else None
    if not isinstance(reason, str) or not reason.strip():
        issues.append(
            LintIssue(
                "MISSING_REFUSAL_REASON",
                "refusal must declare expectation.refusal.reason_code",
                case_id,
                line,
            )
        )


def _has_unique_point_filter(tree: exp.Select) -> bool:
    where = tree.args.get("where")
    if where is None:
        return False
    equal_columns = {
        node.this.name.casefold()
        for node in where.find_all(exp.EQ)
        if isinstance(node.this, exp.Column)
        and isinstance(node.expression, exp.Literal)
    }
    return {"dt", "greenhouse_type"}.issubset(equal_columns)


def _is_single_row(tree: exp.Select) -> bool:
    if tree.args.get("group") is None and any(
        expression.find(exp.AggFunc) is not None for expression in tree.expressions
    ):
        return True
    limit = tree.args.get("limit")
    if limit is not None and str(limit.expression) == "1":
        return True
    return _has_unique_point_filter(tree)


def _validate_sql(
    sql: Any,
    comparison_mode: str,
    *,
    case_id: str,
    line: int,
    issues: list[LintIssue],
) -> str | None:
    if not isinstance(sql, str) or not sql.strip():
        issues.append(
            LintIssue("MISSING_GOLD_SQL", "answer must contain gold SQL", case_id, line)
        )
        return None
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except ParseError as exc:
        issues.append(LintIssue("INVALID_GOLD_SQL", str(exc), case_id, line))
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        issues.append(
            LintIssue("INVALID_GOLD_SQL", "gold SQL must be one SELECT", case_id, line)
        )
        return None
    tree = statements[0]
    for function in tree.find_all(exp.Func):
        name = function.sql_name().upper()
        if name in _NONDETERMINISTIC_FUNCTIONS:
            issues.append(
                LintIssue(
                    "NONDETERMINISTIC_SQL",
                    f"function {name} is nondeterministic",
                    case_id,
                    line,
                )
            )
    for literal in tree.find_all(exp.Literal):
        if literal.is_string and literal.this.casefold() in _DYNAMIC_TIME_LITERALS:
            issues.append(
                LintIssue(
                    "NONDETERMINISTIC_SQL",
                    f"dynamic time literal {literal.this!r} is forbidden",
                    case_id,
                    line,
                )
            )
    order = tree.args.get("order")
    if comparison_mode == "rows_ordered" and order is None and not _is_single_row(tree):
        issues.append(
            LintIssue(
                "NONDETERMINISTIC_SQL",
                "rows_ordered requires ORDER BY unless SQL is provably single-row",
                case_id,
                line,
            )
        )
    if (
        tree.args.get("limit") is not None
        and order is None
        and not _is_single_row(tree)
    ):
        issues.append(
            LintIssue(
                "NONDETERMINISTIC_SQL",
                "multi-row LIMIT requires ORDER BY",
                case_id,
                line,
            )
        )
    return tree.sql(dialect="sqlite", pretty=False, normalize=True)


def lint_dataset(
    dataset_path: Path,
    catalog_path: Path = DEFAULT_CATALOG,
    policy: DatasetLintPolicy | None = None,
) -> DatasetLintReport:
    """Return all contract and corpus violations for one gold JSONL dataset."""
    policy = policy or DatasetLintPolicy()
    records, issues = _load_records(dataset_path)
    try:
        catalog = SemanticCatalog.load(catalog_path)
        metric_names = {metric.name.casefold() for metric in catalog.metrics}
    except Exception as exc:
        issues.append(LintIssue("INVALID_CATALOG", f"{type(exc).__name__}: {exc}"))
        metric_names = set()

    category_counts: Counter[str] = Counter()
    covered_metrics: set[str] = set()
    seen: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)

    for line, record in records:
        case_id = str(record.get("case_id") or "").strip()
        if not case_id:
            issues.append(
                LintIssue("MISSING_CASE_ID", "record needs case_id", line=line)
            )
            case_id = f"line-{line}"
        _add_duplicates(issues, seen["CASE_ID"], "CASE_ID", case_id, case_id)
        _add_duplicates(
            issues, seen["QUESTION"], "QUESTION", record.get("question"), case_id
        )
        signature = record.get("semantic_signature")
        _add_duplicates(
            issues, seen["SEMANTIC_SIGNATURE"], "SEMANTIC_SIGNATURE", signature, case_id
        )

        category = _category(record)
        category_counts[category] += 1
        expectation = record.get("expectation")
        action = expectation.get("action") if isinstance(expectation, Mapping) else None
        if action == "answer":
            _validate_answer_contract(record, case_id=case_id, line=line, issues=issues)
            comparison = record.get("evaluation", {})
            comparison = (
                comparison.get("comparison", {})
                if isinstance(comparison, Mapping)
                else {}
            )
            mode = str(comparison.get("mode", "rows_unordered"))
            canonical_sql = _validate_sql(
                expectation.get("gold_sql")
                if isinstance(expectation, Mapping)
                else None,
                mode,
                case_id=case_id,
                line=line,
                issues=issues,
            )
            _add_duplicates(
                issues, seen["GOLD_SQL"], "GOLD_SQL", canonical_sql, case_id
            )
        elif action == "refuse":
            _validate_refusal_contract(
                record, case_id=case_id, line=line, issues=issues
            )
        else:
            issues.append(
                LintIssue(
                    "INVALID_ACTION",
                    "expectation.action must be answer or refuse",
                    case_id,
                    line,
                )
            )

        if isinstance(signature, str):
            tokens = {token.casefold() for token in signature.split("|")}
            covered_metrics.update(tokens & metric_names)

    expected_categories = Counter(policy.category_quotas)
    all_categories = set(category_counts) | set(expected_categories)
    for category in sorted(all_categories):
        actual = category_counts.get(category, 0)
        expected = expected_categories.get(category, 0)
        if actual != expected:
            issues.append(
                LintIssue(
                    "CATEGORY_QUOTA_MISMATCH",
                    f"{category}: expected {expected}, found {actual}",
                )
            )

    if policy.require_all_catalog_metrics:
        missing_metrics = sorted(metric_names - covered_metrics)
        if missing_metrics:
            issues.append(
                LintIssue(
                    "MISSING_METRIC_COVERAGE",
                    "missing catalog metrics: " + ", ".join(missing_metrics),
                )
            )

    return DatasetLintReport(
        dataset=str(dataset_path.resolve()),
        case_count=len(records),
        category_counts=dict(sorted(category_counts.items())),
        metric_count=len(covered_metrics),
        issues=tuple(issues),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = lint_dataset(args.dataset.resolve(), args.catalog.resolve())
    if report.ok:
        print(
            f"dataset lint passed: {report.case_count} cases, "
            f"{report.metric_count} metrics, categories={dict(report.category_counts)}"
        )
        return 0
    for issue in report.issues:
        location = f" line={issue.line}" if issue.line is not None else ""
        case = f" case={issue.case_id}" if issue.case_id else ""
        print(f"{issue.code}:{case}{location} {issue.message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
