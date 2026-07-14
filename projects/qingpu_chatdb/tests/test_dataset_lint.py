"""Tests for the Qingpu gold dataset linter."""

import json
from pathlib import Path

from projects.qingpu_chatdb.scripts.dataset_lint import (
    DatasetLintPolicy,
    lint_dataset,
)

PROJECT_DIR = Path(__file__).resolve().parents[1]
CATALOG = PROJECT_DIR / "config" / "catalog.yaml"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _answer(case_id: str = "Q001") -> dict:
    return {
        "schema_version": "sql-eval.v1",
        "case_id": case_id,
        "question": "2026年6月1日针叶温室的GSI是多少？",
        "category": {"task": "single_metric"},
        "semantic_signature": "gsi|point|needle|2026-06-01",
        "expectation": {
            "action": "answer",
            "gold_sql": (
                "SELECT gsi FROM ads_greenhouse_indicator "
                "WHERE dt='2026-06-01' AND greenhouse_type='needle'"
            ),
            "tables": {
                "required": ["green_test.ads_greenhouse_indicator"],
                "required_columns": {
                    "green_test.ads_greenhouse_indicator": [
                        "gsi",
                        "dt",
                        "greenhouse_type",
                    ]
                },
            },
            "date_window": {"start": "2026-06-01", "end": "2026-06-01"},
            "result": {"source": "frozen"},
        },
        "evaluation": {"comparison": {"mode": "rows_ordered"}},
    }


def _policy(**quotas: int) -> DatasetLintPolicy:
    return DatasetLintPolicy(category_quotas=quotas, require_all_catalog_metrics=False)


def test_repository_gold_dataset_passes_lint():
    report = lint_dataset(PROJECT_DIR / "eval" / "gold.jsonl", CATALOG)

    assert report.ok, report.issues
    assert report.case_count == 50
    assert report.metric_count == 15


def test_answer_requires_sources_window_and_result(tmp_path):
    record = _answer()
    record["expectation"].pop("tables")
    record["expectation"].pop("date_window")
    record["expectation"].pop("result")
    dataset = tmp_path / "bad.jsonl"
    _write_jsonl(dataset, [record])

    report = lint_dataset(dataset, CATALOG, _policy(single_metric=1))
    codes = {issue.code for issue in report.issues}

    assert "MISSING_REQUIRED_TABLES" in codes
    assert "MISSING_REQUIRED_COLUMNS" in codes
    assert "MISSING_DATE_WINDOW" in codes
    assert "MISSING_RESULT_ARTIFACT" in codes


def test_refusal_requires_reason(tmp_path):
    dataset = tmp_path / "bad.jsonl"
    _write_jsonl(
        dataset,
        [
            {
                "case_id": "Q001",
                "question": "预测明天温度",
                "category": {"task": "refusal"},
                "semantic_signature": "prediction|tomorrow",
                "expectation": {"action": "refuse", "refusal": {}},
            }
        ],
    )

    report = lint_dataset(dataset, CATALOG, _policy(refusal=1))

    assert any(issue.code == "MISSING_REFUSAL_REASON" for issue in report.issues)


def test_detects_quota_metric_and_duplicate_violations(tmp_path):
    first = _answer("Q001")
    second = _answer("Q002")
    dataset = tmp_path / "bad.jsonl"
    _write_jsonl(dataset, [first, second])

    report = lint_dataset(
        dataset,
        CATALOG,
        DatasetLintPolicy(
            category_quotas={"single_metric": 1}, require_all_catalog_metrics=True
        ),
    )
    codes = {issue.code for issue in report.issues}

    assert "CATEGORY_QUOTA_MISMATCH" in codes
    assert "MISSING_METRIC_COVERAGE" in codes
    assert "DUPLICATE_QUESTION" in codes
    assert "DUPLICATE_SEMANTIC_SIGNATURE" in codes
    assert "DUPLICATE_GOLD_SQL" in codes


def test_nondeterministic_function_is_rejected_after_dialect_normalization(tmp_path):
    record = _answer()
    record["question"] = "随机返回一个GSI"
    record["semantic_signature"] = "gsi|random"
    record["expectation"]["gold_sql"] = (
        "SELECT gsi FROM ads_greenhouse_indicator ORDER BY random() LIMIT 1"
    )
    dataset = tmp_path / "bad.jsonl"
    _write_jsonl(dataset, [record])

    report = lint_dataset(dataset, CATALOG, _policy(single_metric=1))

    assert any(issue.code == "NONDETERMINISTIC_SQL" for issue in report.issues)


def test_rows_ordered_and_limit_queries_must_be_deterministic(tmp_path):
    record = _answer()
    record["question"] = "列出GSI"
    record["semantic_signature"] = "gsi|list"
    record["expectation"]["gold_sql"] = (
        "SELECT dt,gsi FROM ads_greenhouse_indicator LIMIT 3"
    )
    record["expectation"]["date_window"] = {
        "start": "2026-06-01",
        "end": "2026-06-14",
    }
    dataset = tmp_path / "bad.jsonl"
    _write_jsonl(dataset, [record])

    report = lint_dataset(dataset, CATALOG, _policy(single_metric=1))

    assert any(issue.code == "NONDETERMINISTIC_SQL" for issue in report.issues)
