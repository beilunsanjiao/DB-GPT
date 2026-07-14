"""Tests for the governed Qingpu semantic catalog."""

from pathlib import Path

import pytest
import yaml

from dbgpt_app.scene.chat_db.semantic_catalog import (
    CatalogValidationError,
    SemanticCatalog,
    UnknownMetricError,
)

PROJECT_DIR = Path(__file__).resolve().parents[1]
CATALOG_PATH = PROJECT_DIR / "config" / "catalog.yaml"


def test_load_qingpu_catalog():
    catalog = SemanticCatalog.load(CATALOG_PATH)

    assert catalog.domain == "qingpu_greenhouse"
    assert len(catalog.tables) == 2
    assert len(catalog.metrics) == 15
    assert catalog.default_time_range_days == 14


def test_resolve_metric_by_name_display_name_and_alias():
    catalog = SemanticCatalog.load(CATALOG_PATH)

    assert catalog.resolve_metric("gsi").display_name == "环境综合适宜度"
    assert catalog.resolve_metric("环境综合适宜度").name == "gsi"
    assert catalog.resolve_metric("棚内均温").name == "avg_air_temp"


def test_table_mapping_and_allowed_columns():
    catalog = SemanticCatalog.load(CATALOG_PATH)

    table = catalog.resolve_table("green_test.ads_greenhouse_indicator")
    assert table.physical_name == "ads_greenhouse_indicator"
    assert catalog.resolve_table("ads_greenhouse_indicator") == table
    assert "gsi" in catalog.allowed_columns[table.name]


def test_metric_governance_notes_are_explicit():
    catalog = SemanticCatalog.load(CATALOG_PATH)

    gsi = catalog.resolve_metric("gsi")
    cgp = catalog.resolve_metric("cgp")
    assert gsi.status == "sql_authoritative"
    assert "运行SQL" in gsi.note
    assert cgp.status == "semantic_uncertain"
    assert "DLI" in cgp.note


def test_render_prompt_context_contains_authorized_facts():
    catalog = SemanticCatalog.load(CATALOG_PATH)

    context = catalog.render_prompt_context(["gsi", "平均温度"])
    assert "green_test.ads_greenhouse_indicator" in context
    assert "green_test.dws_greenhouse_daily" in context
    assert "环境综合适宜度" in context
    assert "日均空气温度" in context
    assert "Default time range: last 14 days" in context


def test_unknown_metric_fails_closed():
    catalog = SemanticCatalog.load(CATALOG_PATH)

    with pytest.raises(UnknownMetricError):
        catalog.resolve_metric("不存在的指标")


def test_duplicate_alias_is_rejected(tmp_path):
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    raw["metrics"][1]["aliases"].append(raw["metrics"][0]["aliases"][0])
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    with pytest.raises(CatalogValidationError, match="Duplicate metric alias"):
        SemanticCatalog.load(path)


def test_metric_unknown_column_is_rejected(tmp_path):
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    raw["metrics"][0]["column"] = "missing_column"
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    with pytest.raises(CatalogValidationError, match="unknown column"):
        SemanticCatalog.load(path)


def test_missing_time_column_is_rejected(tmp_path):
    raw = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    raw["tables"][0]["time_column"] = "missing_dt"
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    with pytest.raises(CatalogValidationError, match="Unknown time column"):
        SemanticCatalog.load(path)
