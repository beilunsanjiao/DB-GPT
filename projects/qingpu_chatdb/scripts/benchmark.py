#!/usr/bin/env python3
"""Benchmark deterministic SQL governance and execution without an LLM.

Accepted and rejected cases cross the same guard interface. The report separates
validation-only latency from guarded end-to-end latency and records interpolated
P50/P95 values so results remain useful for small and large sample sets.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PROJECT_DIR.parents[1]
APP_SRC = REPO_DIR / "packages" / "dbgpt-app" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from dbgpt_app.scene.chat_db.safe_sql import SqlGuardError  # noqa: E402

try:
    from .demo import DEFAULT_CATALOG, DEFAULT_DATABASE, build_runtime
    from .init_local_db import initialize_database
except ImportError:  # direct script execution
    from demo import DEFAULT_CATALOG, DEFAULT_DATABASE, build_runtime
    from init_local_db import initialize_database

DEFAULT_MAX_ROWS = 50


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    category: str
    sql: str
    expected_decision: str
    expected_code: str | None = None


CASES = (
    BenchmarkCase(
        "allow_projection",
        "allow",
        "SELECT dt, gsi FROM green_test.ads_greenhouse_indicator "
        "WHERE greenhouse_type = 'needle' ORDER BY dt",
        "allow",
    ),
    BenchmarkCase(
        "allow_aggregate",
        "allow",
        "SELECT greenhouse_type, ROUND(AVG(avg_air_temp), 2) AS avg_temp "
        "FROM green_test.dws_greenhouse_daily GROUP BY greenhouse_type",
        "allow",
    ),
    BenchmarkCase(
        "allow_join",
        "allow",
        "SELECT a.dt, a.greenhouse_type, a.gsi, d.avg_air_temp "
        "FROM green_test.ads_greenhouse_indicator a "
        "JOIN green_test.dws_greenhouse_daily d "
        "ON a.dt = d.dt AND a.greenhouse_type = d.greenhouse_type "
        "ORDER BY a.dt, a.greenhouse_type LIMIT 100000",
        "allow",
    ),
    BenchmarkCase(
        "deny_write",
        "deny",
        "UPDATE green_test.ads_greenhouse_indicator SET gsi = 1",
        "deny",
        "NON_QUERY_STATEMENT",
    ),
    BenchmarkCase(
        "deny_unknown_table",
        "deny",
        "SELECT dt FROM green_test.secret_table",
        "deny",
        "UNKNOWN_TABLE",
    ),
    BenchmarkCase(
        "deny_star",
        "deny",
        "SELECT * FROM green_test.ads_greenhouse_indicator",
        "deny",
        "STAR_NOT_ALLOWED",
    ),
)


def percentile(values: Sequence[float], fraction: float) -> float:
    """Return a linearly interpolated percentile for a non-empty sample."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * fraction
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _latency_summary(values: Sequence[float]) -> dict[str, float | int]:
    return {
        "samples": len(values),
        "mean_ms": statistics.fmean(values) if values else 0.0,
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "min_ms": min(values, default=0.0),
        "max_ms": max(values, default=0.0),
    }


def _time_call(call: Callable[[], Any]) -> tuple[float, Any, SqlGuardError | None]:
    started = time.perf_counter_ns()
    try:
        result = call()
        error = None
    except SqlGuardError as exc:
        result = None
        error = exc
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return elapsed_ms, result, error


def benchmark(
    database: Path,
    catalog_path: Path,
    iterations: int,
    warmup: int = 3,
    max_rows: int = DEFAULT_MAX_ROWS,
    reset_database: bool = False,
) -> dict[str, Any]:
    """Run validation and guarded execution samples for allow/deny cases."""
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    if warmup < 0:
        raise ValueError("warmup cannot be negative")
    if reset_database or not database.exists():
        initialize_database(database, reset=reset_database)
    _, guard, executor = build_runtime(database, catalog_path, max_rows)

    def validate(case: BenchmarkCase):
        return guard.prepare(case.sql, dialect="sqlite", max_rows=max_rows)

    def execute(case: BenchmarkCase):
        return executor.execute(case.sql)

    # Warm-up initializes parser/import/SQLite caches but is excluded from metrics.
    for _ in range(warmup):
        for case in CASES:
            _time_call(lambda case=case: validate(case))
            _time_call(lambda case=case: execute(case))

    prepare_samples: list[float] = []
    execution_samples: list[float] = []
    by_decision: dict[str, dict[str, list[float]]] = {
        "allow": {"prepare": [], "execution": []},
        "deny": {"prepare": [], "execution": []},
    }
    outcomes: dict[str, dict[str, Any]] = {
        case.case_id: {
            "case_id": case.case_id,
            "category": case.category,
            "expected_decision": case.expected_decision,
            "expected_code": case.expected_code,
            "samples": 0,
            "matched": 0,
            "observed_codes": {},
            "prepare_ms": [],
            "execution_ms": [],
        }
        for case in CASES
    }

    for _ in range(iterations):
        for case in CASES:
            prepare_ms, _, prepare_error = _time_call(lambda case=case: validate(case))
            execution_ms, _, execution_error = _time_call(
                lambda case=case: execute(case)
            )
            prepare_samples.append(prepare_ms)
            execution_samples.append(execution_ms)
            by_decision[case.expected_decision]["prepare"].append(prepare_ms)
            by_decision[case.expected_decision]["execution"].append(execution_ms)

            actual_decision = "deny" if execution_error else "allow"
            actual_code = execution_error.code if execution_error else "OK"
            prepare_code = prepare_error.code if prepare_error else "OK"
            matched = actual_decision == case.expected_decision and (
                case.expected_code is None or actual_code == case.expected_code
            )
            # Preparation and execution must make the same decision/code.
            matched = matched and prepare_code == actual_code
            outcome = outcomes[case.case_id]
            outcome["samples"] += 1
            outcome["matched"] += int(matched)
            outcome["observed_codes"][actual_code] = (
                outcome["observed_codes"].get(actual_code, 0) + 1
            )
            outcome["prepare_ms"].append(prepare_ms)
            outcome["execution_ms"].append(execution_ms)

    case_reports = []
    for outcome in outcomes.values():
        case_reports.append(
            {
                "case_id": outcome["case_id"],
                "category": outcome["category"],
                "expected_decision": outcome["expected_decision"],
                "expected_code": outcome["expected_code"],
                "samples": outcome["samples"],
                "matched": outcome["matched"],
                "match_rate": outcome["matched"] / outcome["samples"],
                "observed_codes": outcome["observed_codes"],
                "guard_prepare": _latency_summary(outcome["prepare_ms"]),
                "guarded_execution": _latency_summary(outcome["execution_ms"]),
            }
        )

    total_samples = iterations * len(CASES)
    matched_samples = sum(item["matched"] for item in case_reports)
    return {
        "mode": "offline_no_llm",
        "database": "generated:projects/qingpu_chatdb/data/qingpu.db",
        "catalog": "projects/qingpu_chatdb/config/catalog.yaml",
        "iterations": iterations,
        "warmup_iterations": warmup,
        "case_count": len(CASES),
        "sample_count": total_samples,
        "hard_max_rows": max_rows,
        "correctness": {
            "matched_samples": matched_samples,
            "total_samples": total_samples,
            "match_rate": matched_samples / total_samples,
        },
        "latency_ms": {
            "guard_prepare_all": _latency_summary(prepare_samples),
            "guarded_execution_all": _latency_summary(execution_samples),
            "allow": {
                "guard_prepare": _latency_summary(by_decision["allow"]["prepare"]),
                "guarded_execution": _latency_summary(
                    by_decision["allow"]["execution"]
                ),
            },
            "deny": {
                "guard_prepare": _latency_summary(by_decision["deny"]["prepare"]),
                "guarded_execution": _latency_summary(by_decision["deny"]["execution"]),
            },
        },
        "cases": case_reports,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    parser.add_argument("--reset-db", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = benchmark(
        args.database.resolve(),
        args.catalog.resolve(),
        iterations=args.iterations,
        warmup=args.warmup,
        max_rows=args.max_rows,
        reset_database=args.reset_db,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["correctness"]["match_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
