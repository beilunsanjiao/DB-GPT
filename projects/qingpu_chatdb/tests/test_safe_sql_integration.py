"""End-to-end tests for guarded execution against the Qingpu SQLite fixture."""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from dbgpt_app.scene.chat_db.safe_sql import ReadOnlySqlGuard, SafeSqlExecutor
from dbgpt_app.scene.chat_db.semantic_catalog import SemanticCatalog

PROJECT_DIR = Path(__file__).resolve().parents[1]
CATALOG_PATH = PROJECT_DIR / "config" / "catalog.yaml"
INIT_SCRIPT = PROJECT_DIR / "scripts" / "init_local_db.py"


@pytest.fixture
def executor(tmp_path):
    database = tmp_path / "qingpu.db"
    subprocess.run(
        [sys.executable, str(INIT_SCRIPT), "--database", str(database)],
        check=True,
        capture_output=True,
        text=True,
    )

    def execute_to_df(sql: str) -> pd.DataFrame:
        with sqlite3.connect(database) as connection:
            return pd.read_sql_query(sql, connection)

    catalog = SemanticCatalog.load(CATALOG_PATH)
    return SafeSqlExecutor(ReadOnlySqlGuard(catalog), execute_to_df, "sqlite", 50)


def test_executes_grouped_counts(executor):
    result = executor.execute(
        "SELECT greenhouse_type, COUNT(*) AS row_count "
        "FROM green_test.dws_greenhouse_daily "
        "GROUP BY greenhouse_type ORDER BY greenhouse_type"
    )

    assert result.dataframe.to_dict(orient="records") == [
        {"greenhouse_type": "broad", "row_count": 14},
        {"greenhouse_type": "needle", "row_count": 14},
    ]


def test_executes_temperature_aggregates(executor):
    result = executor.execute(
        "SELECT greenhouse_type, ROUND(AVG(avg_air_temp), 2) AS avg_temp, "
        "ROUND(MIN(avg_air_temp), 2) AS min_temp, "
        "ROUND(MAX(avg_air_temp), 2) AS max_temp "
        "FROM green_test.dws_greenhouse_daily "
        "GROUP BY greenhouse_type ORDER BY greenhouse_type"
    )

    assert result.dataframe.to_dict(orient="records") == [
        {
            "greenhouse_type": "broad",
            "avg_temp": 23.94,
            "min_temp": 22.9,
            "max_temp": 24.98,
        },
        {
            "greenhouse_type": "needle",
            "avg_temp": 19.24,
            "min_temp": 18.2,
            "max_temp": 20.28,
        },
    ]


def test_executes_null_propagation_query(executor):
    result = executor.execute(
        "SELECT dt, greenhouse_type, pue "
        "FROM green_test.ads_greenhouse_indicator WHERE pue IS NULL"
    )

    assert result.dataframe.to_dict(orient="records") == [
        {"dt": "2026-06-11", "greenhouse_type": "needle", "pue": None}
    ]


def test_executes_authorized_join(executor):
    result = executor.execute(
        "SELECT a.dt, a.greenhouse_type, a.gsi, d.avg_air_temp "
        "FROM green_test.ads_greenhouse_indicator AS a "
        "JOIN green_test.dws_greenhouse_daily AS d "
        "ON a.dt = d.dt AND a.greenhouse_type = d.greenhouse_type "
        "ORDER BY a.dt, a.greenhouse_type"
    )

    assert len(result.dataframe) == 28
    assert result.prepared.referenced_tables == (
        "green_test.ads_greenhouse_indicator",
        "green_test.dws_greenhouse_daily",
    )
    assert "main.ads_greenhouse_indicator" in result.prepared.sql
    assert "main.dws_greenhouse_daily" in result.prepared.sql
