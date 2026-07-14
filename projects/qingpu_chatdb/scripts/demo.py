#!/usr/bin/env python3
"""Run deterministic governed-query demonstrations without an LLM.

The module exposes one deep interface, :func:`run_demo`: callers supply SQL cases
and receive the complete governance decision (rewritten SQL, referenced schema,
LIMIT, rows, or a stable refusal code). No prompt, model, network, or fallback
SQL generation is involved.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

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
    from .init_local_db import initialize_database
except ImportError:  # direct script execution
    from init_local_db import initialize_database

DEFAULT_DATABASE = PROJECT_DIR / "data" / "qingpu.db"
DEFAULT_CATALOG = PROJECT_DIR / "config" / "catalog.yaml"
DEFAULT_MAX_ROWS = 50


@dataclass(frozen=True)
class DemoCase:
    """One deterministic SQL input and its expected governance decision."""

    case_id: str
    description: str
    sql: str
    expected_decision: str
    expected_code: str | None = None


DEFAULT_CASES = (
    DemoCase(
        "allow_limit_added",
        "合法聚合查询：映射逻辑表并自动补 LIMIT",
        "SELECT greenhouse_type, ROUND(AVG(gsi), 6) AS avg_gsi "
        "FROM green_test.ads_greenhouse_indicator "
        "GROUP BY greenhouse_type ORDER BY avg_gsi DESC",
        "allow",
    ),
    DemoCase(
        "allow_limit_reduced",
        "合法明细查询：将过大的 LIMIT 收紧到硬上限",
        "SELECT dt, greenhouse_type, avg_air_temp "
        "FROM green_test.dws_greenhouse_daily "
        "ORDER BY dt, greenhouse_type LIMIT 100000",
        "allow",
    ),
    DemoCase(
        "deny_write",
        "拒绝写操作，且不会调用数据库执行器",
        "DELETE FROM green_test.ads_greenhouse_indicator WHERE dt < '2026-06-05'",
        "deny",
        "NON_QUERY_STATEMENT",
    ),
    DemoCase(
        "deny_unknown_column",
        "拒绝目录之外的列",
        "SELECT password FROM green_test.ads_greenhouse_indicator",
        "deny",
        "UNKNOWN_COLUMN",
    ),
)


def build_runtime(
    database: Path, catalog_path: Path, max_rows: int = DEFAULT_MAX_ROWS
) -> tuple[SemanticCatalog, ReadOnlySqlGuard, SafeSqlExecutor]:
    """Build the catalog, guard, and read-only SQLite adapter once."""
    catalog = SemanticCatalog.load(catalog_path)
    guard = ReadOnlySqlGuard(catalog)

    def execute_to_df(sql: str) -> pd.DataFrame:
        uri = f"file:{database.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            return pd.read_sql_query(sql, connection)

    executor = SafeSqlExecutor(guard, execute_to_df, "sqlite", max_rows)
    return catalog, guard, executor


def _source_schema(
    catalog: SemanticCatalog, referenced_tables: Sequence[str]
) -> list[dict[str, Any]]:
    """Render governed table and column metadata for the selected sources."""
    sources = []
    for table_name in referenced_tables:
        table = catalog.resolve_table(table_name)
        sources.append(
            {
                "table": table.name,
                "physical_table": table.physical_name,
                "grain": table.grain,
                "columns": [
                    {
                        "name": column.name,
                        "type": column.data_type,
                        "description": column.description,
                    }
                    for column in table.columns
                ],
            }
        )
    return sources


def run_query(
    case: DemoCase, catalog: SemanticCatalog, executor: SafeSqlExecutor
) -> dict[str, Any]:
    """Execute one case through the governance seam and return an audit record."""
    record: dict[str, Any] = {
        "case_id": case.case_id,
        "description": case.description,
        "input_sql": case.sql,
        "expected_decision": case.expected_decision,
    }
    try:
        result = executor.execute(case.sql)
        prepared = result.prepared
        record.update(
            {
                "decision": "allow",
                "decision_matches": case.expected_decision == "allow",
                "executed_sql": prepared.sql,
                "referenced_tables": list(prepared.referenced_tables),
                "referenced_columns": list(prepared.referenced_columns),
                "source_schema": _source_schema(catalog, prepared.referenced_tables),
                "limit": prepared.limit,
                "rewrites": [asdict(rewrite) for rewrite in prepared.rewrites],
                "row_count": len(result.dataframe),
                "result_columns": [str(name) for name in result.dataframe.columns],
                "rows": result.dataframe.to_dict(orient="records"),
            }
        )
    except SqlGuardError as exc:
        record.update(
            {
                "decision": "deny",
                "code": exc.code,
                "message": exc.message,
                "decision_matches": case.expected_decision == "deny"
                and (case.expected_code is None or case.expected_code == exc.code),
            }
        )
    return record


def run_demo(
    database: Path,
    catalog_path: Path,
    cases: Sequence[DemoCase] = DEFAULT_CASES,
    max_rows: int = DEFAULT_MAX_ROWS,
    reset_database: bool = False,
) -> dict[str, Any]:
    """Initialize the fixture and run accepted and refused SQL cases."""
    if reset_database or not database.exists():
        initialize_database(database, reset=reset_database)
    catalog, _, executor = build_runtime(database, catalog_path, max_rows)
    results = [run_query(case, catalog, executor) for case in cases]
    return {
        "mode": "offline_no_llm",
        "database": str(database.resolve()),
        "catalog": str(catalog_path.resolve()),
        "hard_max_rows": max_rows,
        "summary": {
            "total": len(results),
            "allowed": sum(item["decision"] == "allow" for item in results),
            "denied": sum(item["decision"] == "deny" for item in results),
            "matched": sum(bool(item["decision_matches"]) for item in results),
        },
        "cases": results,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    parser.add_argument("--reset-db", action="store_true")
    parser.add_argument(
        "--sql", help="Run one custom SQL statement instead of the built-in suite"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    cases = DEFAULT_CASES
    if args.sql:
        cases = (DemoCase("custom", "命令行自定义 SQL", args.sql, "allow"),)
    report = run_demo(
        args.database.resolve(),
        args.catalog.resolve(),
        cases,
        max_rows=args.max_rows,
        reset_database=args.reset_db,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["summary"]["matched"] == report["summary"]["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
