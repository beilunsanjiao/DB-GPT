"""Tests for the Qingpu evaluation runner."""

import json
from pathlib import Path

import pandas as pd

from projects.qingpu_chatdb.scripts.expected_results import seal_expected_results
from projects.qingpu_chatdb.scripts.run_eval import (
    EvaluationConfig,
    compare_dataframes,
    load_jsonl,
    run_evaluation,
)

PROJECT_DIR = Path(__file__).resolve().parents[1]


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _gold_config(tmp_path: Path, dataset: Path) -> EvaluationConfig:
    artifact = tmp_path / "gold-answers.v1.json"
    lock = tmp_path / "gold-answers.v1.lock.json"
    seal_expected_results(dataset, artifact, lock)
    return EvaluationConfig(
        mode="gold",
        dataset_path=dataset,
        database_path=tmp_path / "fixture.db",
        output_dir=tmp_path / "reports",
        expected_results_path=artifact,
        expected_results_lock_path=lock,
        reset_database=True,
    )


def test_compare_dataframes_is_numeric_tolerant_and_multiset_aware():
    actual = pd.DataFrame(
        [{"name": "a", "value": 1.0000001}, {"name": "a", "value": 2.0}]
    )
    expected = pd.DataFrame([{"name": "a", "value": 2.0}, {"name": "a", "value": 1.0}])

    result = compare_dataframes(
        actual,
        expected,
        {"mode": "rows_unordered", "numeric": {"abs": 1e-5, "rel": 0.0}},
    )

    assert result.matched
    assert result.schema_matched


def test_gold_mode_initializes_database_and_writes_reports(tmp_path):
    dataset = tmp_path / "gold.jsonl"
    _write_jsonl(
        dataset,
        [
            {
                "case_id": "G001",
                "category": {"task": "aggregation"},
                "expectation": {
                    "action": "answer",
                    "gold_sql": (
                        "SELECT greenhouse_type, COUNT(*) AS row_count "
                        "FROM dws_greenhouse_daily GROUP BY greenhouse_type"
                    ),
                    "tables": {
                        "required": ["green_test.dws_greenhouse_daily"],
                        "required_columns": {
                            "green_test.dws_greenhouse_daily": ["greenhouse_type"]
                        },
                    },
                    "result": {"source": "frozen"},
                },
                "candidate_sql": (
                    "SELECT greenhouse_type, COUNT(*) AS row_count "
                    "FROM green_test.dws_greenhouse_daily "
                    "GROUP BY greenhouse_type"
                ),
                "evaluation": {"comparison": {"mode": "rows_unordered"}},
            },
            {
                "case_id": "G002",
                "category": {"task": "refusal"},
                "expectation": {
                    "action": "refuse",
                    "refusal": {"reason_code": "missing_metric"},
                },
                "prediction_action": "refuse",
                "prediction_refusal_reason_code": "missing_metric",
            },
        ],
    )
    report = run_evaluation(_gold_config(tmp_path, dataset))

    assert report["summary"]["passed"] == 2
    assert report["summary"]["result_accuracy"] == 1.0
    assert Path(report["artifacts"]["json"]).exists()
    assert Path(report["artifacts"]["markdown"]).exists()


def test_load_jsonl_rejects_duplicate_case_ids(tmp_path):
    dataset = tmp_path / "duplicate.jsonl"
    _write_jsonl(dataset, [{"case_id": "X001"}, {"case_id": "X001"}])

    try:
        load_jsonl(dataset)
    except ValueError as exc:
        assert "Duplicate case_id" in str(exc)
    else:
        raise AssertionError("duplicate case ids must be rejected")


def test_gold_mode_does_not_fall_back_to_gold_sql(tmp_path):
    dataset = tmp_path / "gold.jsonl"
    _write_jsonl(
        dataset,
        [
            {
                "case_id": "G001",
                "expectation": {
                    "action": "answer",
                    "gold_sql": "SELECT COUNT(*) AS count FROM dws_greenhouse_daily",
                    "result": {"source": "frozen"},
                },
            }
        ],
    )

    report = run_evaluation(_gold_config(tmp_path, dataset))

    assert report["summary"]["passed"] == 0
    assert report["cases"][0]["failure_reason"] == "missing_candidate_sql"


def test_refusal_without_prediction_fails(tmp_path):
    dataset = tmp_path / "gold.jsonl"
    _write_jsonl(
        dataset,
        [
            {
                "case_id": "G001",
                "expectation": {
                    "action": "refuse",
                    "refusal": {"reason_code": "missing_metric"},
                },
            }
        ],
    )

    report = run_evaluation(_gold_config(tmp_path, dataset))

    assert report["summary"]["passed"] == 0
    assert report["cases"][0]["actual_action"] == "missing"


def test_required_table_mismatch_fails_even_when_result_matches(tmp_path):
    dataset = tmp_path / "gold.jsonl"
    _write_jsonl(
        dataset,
        [
            {
                "case_id": "G001",
                "expectation": {
                    "action": "answer",
                    "gold_sql": "SELECT COUNT(*) AS count FROM dws_greenhouse_daily",
                    "tables": {"required": ["green_test.ads_greenhouse_indicator"]},
                    "result": {"source": "frozen"},
                },
                "candidate_sql": (
                    "SELECT COUNT(*) AS count FROM green_test.dws_greenhouse_daily"
                ),
            }
        ],
    )

    report = run_evaluation(_gold_config(tmp_path, dataset))

    assert report["summary"]["passed"] == 0
    assert report["cases"][0]["failure_reason"] == "required_source_mismatch"


def test_hardened_mode_scores_codes_and_rewrites(tmp_path):
    dataset = tmp_path / "hardened.jsonl"
    _write_jsonl(
        dataset,
        [
            {
                "case_id": "H001",
                "category": "write",
                "input_sql": "DELETE FROM green_test.ads_greenhouse_indicator",
                "expected_allowed": False,
                "expected_code": "NON_QUERY_STATEMENT",
            },
            {
                "case_id": "H002",
                "category": "limit",
                "input_sql": (
                    "SELECT dt FROM green_test.ads_greenhouse_indicator LIMIT 51"
                ),
                "expected_allowed": True,
                "expected_code": "OK",
                "expected_rewrite": "limit_reduced",
                "expected_limit": 50,
                "expected_row_count": 28,
            },
        ],
    )
    report = run_evaluation(
        EvaluationConfig(
            mode="hardened",
            dataset_path=dataset,
            database_path=tmp_path / "fixture.db",
            output_dir=tmp_path / "reports",
            reset_database=True,
        )
    )

    assert report["summary"]["passed"] == 2
    assert report["summary"]["dangerous_interception_rate"] == 1.0
    assert report["summary"]["allow_contract_rate"] == 1.0
    assert report["summary"]["safety_interception_accuracy"] == 1.0
    assert report["cases"][0]["actual_code"] == "NON_QUERY_STATEMENT"
    assert "limit_reduced" in report["cases"][1]["rewrites"]


def test_hardened_mode_splits_block_and_allow_contract_rates(tmp_path):
    dataset = tmp_path / "hardened.jsonl"
    _write_jsonl(
        dataset,
        [
            {
                "case_id": "H001",
                "category": "root",
                "input_sql": "DELETE FROM green_test.ads_greenhouse_indicator",
                "expected_allowed": False,
                "expected_code": "PARSE_ERROR",
            },
            {
                "case_id": "H002",
                "category": "allow_contract",
                "input_sql": "SELECT dt FROM green_test.ads_greenhouse_indicator",
                "expected_allowed": True,
                "expected_code": "OK",
                "expected_row_count": 999,
            },
        ],
    )

    report = run_evaluation(
        EvaluationConfig(
            mode="hardened",
            dataset_path=dataset,
            database_path=tmp_path / "fixture.db",
            output_dir=tmp_path / "reports",
            reset_database=True,
        )
    )

    assert report["summary"]["dangerous_interception_rate"] == 0.0
    assert report["summary"]["allow_contract_rate"] == 0.0
    assert report["cases"][1]["failure_reason"] == "row_count_mismatch"
