"""Tests for the AST-based read-only SQL guard."""

from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from dbgpt_app.scene.chat_db.safe_sql import (
    ReadOnlySqlGuard,
    SafeSqlExecutor,
    SqlGuardError,
)
from dbgpt_app.scene.chat_db.semantic_catalog import SemanticCatalog

PROJECT_DIR = Path(__file__).resolve().parents[1]
CATALOG_PATH = PROJECT_DIR / "config" / "catalog.yaml"


@pytest.fixture
def guard():
    return ReadOnlySqlGuard(SemanticCatalog.load(CATALOG_PATH))


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT dt, gsi FROM green_test.ads_greenhouse_indicator WHERE gsi > 0.5",
        "SELECT AVG(gsi) AS avg_gsi FROM green_test.ads_greenhouse_indicator",
        "SELECT greenhouse_type, AVG(gsi) AS avg_gsi "
        "FROM green_test.ads_greenhouse_indicator GROUP BY greenhouse_type",
        "SELECT dt, gsi FROM green_test.ads_greenhouse_indicator ORDER BY dt DESC",
        "SELECT DISTINCT greenhouse_type FROM green_test.ads_greenhouse_indicator",
        "SELECT a.gsi, d.avg_air_temp "
        "FROM green_test.ads_greenhouse_indicator AS a "
        "JOIN green_test.dws_greenhouse_daily AS d "
        "ON a.dt = d.dt AND a.greenhouse_type = d.greenhouse_type",
        "WITH recent AS (SELECT dt, gsi "
        "FROM green_test.ads_greenhouse_indicator) "
        "SELECT dt, gsi FROM recent",
        "SELECT dt, gsi FROM green_test.ads_greenhouse_indicator "
        "WHERE gsi > (SELECT AVG(gsi) "
        "FROM green_test.ads_greenhouse_indicator)",
        "SELECT COUNT(*) AS row_count FROM green_test.ads_greenhouse_indicator",
        "SELECT '--not a comment' AS marker, dt "
        "FROM green_test.ads_greenhouse_indicator LIMIT 1",
        "SELECT greenhouse_type, ROUND(AVG(gsi), 2) AS avg_gsi "
        "FROM green_test.ads_greenhouse_indicator "
        "GROUP BY greenhouse_type HAVING AVG(gsi) > 0.5 "
        "ORDER BY avg_gsi DESC",
    ],
)
def test_accepts_safe_selects(guard, sql):
    prepared = guard.prepare(sql, dialect="sqlite", max_rows=50)

    assert prepared.sql
    assert prepared.limit <= 50
    assert "main." in prepared.sql


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("", "EMPTY_SQL"),
        ("SELECT (dt FROM green_test.ads_greenhouse_indicator", "PARSE_ERROR"),
        (
            "SELECT dt FROM green_test.ads_greenhouse_indicator "
            "WHERE greenhouse_type = 'needle",
            "PARSE_ERROR",
        ),
        ("SELECT 1; SELECT 2", "MULTI_STATEMENT"),
        ("INSERT INTO x VALUES (1)", "NON_QUERY_STATEMENT"),
        ("UPDATE x SET a = 1", "NON_QUERY_STATEMENT"),
        ("DELETE FROM x", "NON_QUERY_STATEMENT"),
        ("DROP TABLE x", "NON_QUERY_STATEMENT"),
        ("CREATE TABLE x(a INT)", "NON_QUERY_STATEMENT"),
        ("PRAGMA table_info(x)", "NON_QUERY_STATEMENT"),
        ("ATTACH DATABASE 'x' AS evil", "PARSE_ERROR"),
        ("SELECT dt FROM green_test.unknown_table", "UNKNOWN_TABLE"),
        (
            "SELECT missing FROM green_test.ads_greenhouse_indicator",
            "UNKNOWN_COLUMN",
        ),
        ("SELECT * FROM green_test.ads_greenhouse_indicator", "STAR_NOT_ALLOWED"),
        (
            "SELECT dt FROM green_test.ads_greenhouse_indicator a "
            "JOIN green_test.dws_greenhouse_daily d ON a.dt = d.dt",
            "AMBIGUOUS_COLUMN",
        ),
        ("SELECT dt FROM ads_greenhouse_indicator", "UNKNOWN_TABLE"),
        ("SELECT dt FROM evil.ads_greenhouse_indicator", "UNKNOWN_TABLE"),
        (
            "SELECT dt FROM green_test.ads_greenhouse_indicator -- comment",
            "COMMENT_FORBIDDEN",
        ),
        (
            "SELECT load_extension('x') FROM green_test.ads_greenhouse_indicator",
            "FUNCTION_NOT_ALLOWED",
        ),
        (
            "SELECT dt FROM green_test.ads_greenhouse_indicator FOR UPDATE",
            "UNSUPPORTED_QUERY_SHAPE",
        ),
        (
            "SELECT dt FROM green_test.ads_greenhouse_indicator "
            "UNION SELECT dt FROM green_test.dws_greenhouse_daily",
            "NON_QUERY_STATEMENT",
        ),
        (
            "SELECT x.dt FROM (SELECT dt "
            "FROM green_test.ads_greenhouse_indicator) AS x",
            "UNSUPPORTED_QUERY_SHAPE",
        ),
        (
            "SELECT a.dt FROM green_test.ads_greenhouse_indicator AS a "
            "WHERE EXISTS (SELECT 1 FROM green_test.dws_greenhouse_daily AS d "
            "WHERE d.dt = a.dt)",
            "UNSUPPORTED_QUERY_SHAPE",
        ),
        (
            "SELECT AVG(gsi) OVER () FROM green_test.ads_greenhouse_indicator",
            "UNSUPPORTED_QUERY_SHAPE",
        ),
        (
            "SELECT dt FROM green_test.ads_greenhouse_indicator LIMIT 10 OFFSET 1",
            "UNSUPPORTED_QUERY_SHAPE",
        ),
        (
            "SELECT a.dt FROM green_test.ads_greenhouse_indicator AS a "
            "CROSS JOIN green_test.dws_greenhouse_daily AS d",
            "UNSUPPORTED_QUERY_SHAPE",
        ),
        (
            "SELECT a.dt FROM green_test.ads_greenhouse_indicator AS a "
            "RIGHT JOIN green_test.dws_greenhouse_daily AS d ON a.dt = d.dt",
            "UNSUPPORTED_QUERY_SHAPE",
        ),
    ],
)
def test_rejects_unsafe_sql(guard, sql, code):
    with pytest.raises(SqlGuardError) as exc_info:
        guard.prepare(sql, dialect="sqlite", max_rows=50)

    assert exc_info.value.code == code


@pytest.mark.parametrize(
    ("sql", "expected_limit", "rewrite_kind"),
    [
        (
            "SELECT dt FROM green_test.ads_greenhouse_indicator",
            50,
            "limit_added",
        ),
        (
            "SELECT dt FROM green_test.ads_greenhouse_indicator LIMIT 10",
            10,
            None,
        ),
        (
            "SELECT dt FROM green_test.ads_greenhouse_indicator LIMIT 50",
            50,
            None,
        ),
        (
            "SELECT dt FROM green_test.ads_greenhouse_indicator LIMIT 51",
            50,
            "limit_reduced",
        ),
        (
            "SELECT dt FROM green_test.ads_greenhouse_indicator LIMIT 100000",
            50,
            "limit_reduced",
        ),
        (
            "SELECT dt FROM green_test.ads_greenhouse_indicator LIMIT 0",
            0,
            None,
        ),
        (
            "SELECT dt FROM green_test.ads_greenhouse_indicator "
            "WHERE gsi > (SELECT AVG(gsi) "
            "FROM green_test.ads_greenhouse_indicator LIMIT 1)",
            50,
            "limit_added",
        ),
    ],
)
def test_applies_hard_limit(guard, sql, expected_limit, rewrite_kind):
    prepared = guard.prepare(sql, dialect="sqlite", max_rows=50)

    assert prepared.limit == expected_limit
    kinds = {rewrite.kind for rewrite in prepared.rewrites}
    if rewrite_kind:
        assert rewrite_kind in kinds
    else:
        assert "limit_added" not in kinds
        assert "limit_reduced" not in kinds


def test_preserves_logical_qualifier_with_physical_mapping(guard):
    prepared = guard.prepare(
        "SELECT ads_greenhouse_indicator.dt FROM green_test.ads_greenhouse_indicator",
        dialect="sqlite",
        max_rows=50,
    )

    assert "main.ads_greenhouse_indicator AS ads_greenhouse_indicator" in prepared.sql


def test_rejects_non_sqlite_dialect_until_adapter_exists(guard):
    with pytest.raises(SqlGuardError) as exc_info:
        guard.prepare(
            "SELECT dt FROM green_test.ads_greenhouse_indicator",
            dialect="mysql",
            max_rows=50,
        )

    assert exc_info.value.code == "UNSUPPORTED_DIALECT"


def test_multi_stage_cte_uses_only_selected_sources(guard):
    prepared = guard.prepare(
        "WITH first_stage AS (SELECT dt "
        "FROM green_test.ads_greenhouse_indicator), "
        "second_stage AS (SELECT dt FROM first_stage) "
        "SELECT dt FROM second_stage",
        dialect="sqlite",
        max_rows=50,
    )

    assert "main.ads_greenhouse_indicator" in prepared.sql


def test_duplicate_explicit_alias_is_rejected(guard):
    with pytest.raises(SqlGuardError) as exc_info:
        guard.prepare(
            "SELECT a.dt FROM green_test.ads_greenhouse_indicator AS a "
            "JOIN green_test.dws_greenhouse_daily AS a ON a.dt = a.dt",
            dialect="sqlite",
            max_rows=50,
        )

    assert exc_info.value.code == "INVALID_QUALIFIER"


def test_rewrites_both_logical_tables_and_preserves_cte(guard):
    prepared = guard.prepare(
        "WITH combined AS ("
        "SELECT a.dt, a.greenhouse_type, a.gsi, d.avg_air_temp "
        "FROM green_test.ads_greenhouse_indicator a "
        "JOIN green_test.dws_greenhouse_daily d "
        "ON a.dt = d.dt AND a.greenhouse_type = d.greenhouse_type"
        ") SELECT dt, greenhouse_type, gsi, avg_air_temp FROM combined",
        dialect="sqlite",
        max_rows=50,
    )

    assert "main.ads_greenhouse_indicator" in prepared.sql
    assert "main.dws_greenhouse_daily" in prepared.sql
    assert "FROM combined" in prepared.sql
    assert prepared.referenced_tables == (
        "green_test.ads_greenhouse_indicator",
        "green_test.dws_greenhouse_daily",
    )


def test_safe_executor_never_calls_connector_for_rejected_sql(guard):
    connector = Mock(return_value=pd.DataFrame())
    executor = SafeSqlExecutor(guard, connector, "sqlite", 50)

    with pytest.raises(SqlGuardError):
        executor.execute("DELETE FROM green_test.ads_greenhouse_indicator")

    connector.assert_not_called()


def test_safe_executor_calls_connector_once_with_prepared_sql(guard):
    expected = pd.DataFrame([{"greenhouse_type": "needle", "count": 14}])
    connector = Mock(return_value=expected)
    executor = SafeSqlExecutor(guard, connector, "sqlite", 50)

    result = executor.execute(
        "SELECT greenhouse_type, COUNT(*) AS count "
        "FROM green_test.dws_greenhouse_daily GROUP BY greenhouse_type"
    )

    assert result.dataframe is expected
    connector.assert_called_once_with(result.prepared.sql)
    assert "main.dws_greenhouse_daily" in result.prepared.sql
