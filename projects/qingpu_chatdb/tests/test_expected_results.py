"""Tests for sealed Qingpu expected-result artifacts."""

import json
from pathlib import Path

import pytest

from projects.qingpu_chatdb.scripts.expected_results import (
    load_expected_results,
    seal_expected_results,
)

PROJECT_DIR = Path(__file__).resolve().parents[1]


def _write_dataset(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "case_id": "G001",
                "expectation": {
                    "action": "answer",
                    "gold_sql": "SELECT COUNT(*) AS count FROM dws_greenhouse_daily",
                    "result": {"source": "frozen"},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_repository_expected_results_are_sealed_and_complete():
    bundle = load_expected_results()

    assert len(bundle.cases) == 47
    assert bundle.dataframe(
        "Q001",
        (
            "SELECT gsi FROM ads_greenhouse_indicator "
            "WHERE dt='2026-06-01' AND greenhouse_type='needle'"
        ),
    ).iloc[0, 0] == pytest.approx(0.864357)


def test_tampered_artifact_is_rejected(tmp_path):
    dataset = tmp_path / "gold.jsonl"
    artifact = tmp_path / "answers.json"
    lock = tmp_path / "answers.lock.json"
    _write_dataset(dataset)
    seal_expected_results(dataset, artifact, lock)
    artifact.write_text(artifact.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_expected_results(artifact, lock)


def test_gold_sql_drift_is_rejected(tmp_path):
    dataset = tmp_path / "gold.jsonl"
    artifact = tmp_path / "answers.json"
    lock = tmp_path / "answers.lock.json"
    _write_dataset(dataset)
    seal_expected_results(dataset, artifact, lock)
    bundle = load_expected_results(artifact, lock)

    with pytest.raises(ValueError, match="does not match gold_sql"):
        bundle.dataframe("G001", "SELECT 0 AS count")
