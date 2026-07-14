"""Frozen expected-result artifacts for the Qingpu SQL evaluation."""

from __future__ import annotations

import gc
import hashlib
import json
import math
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

try:
    from .init_local_db import initialize_database
except ImportError:  # direct script execution
    from init_local_db import initialize_database

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_DIR / "eval" / "gold.jsonl"
DEFAULT_ARTIFACT = PROJECT_DIR / "eval" / "gold-answers.v1.json"
DEFAULT_LOCK = PROJECT_DIR / "eval" / "gold-answers.v1.lock.json"
DEFAULT_CATALOG = PROJECT_DIR / "config" / "catalog.yaml"
DEFAULT_SCHEMA = PROJECT_DIR / "seed" / "schema.sql"
DEFAULT_SEED_GENERATOR = PROJECT_DIR / "scripts" / "init_local_db.py"
_ARTIFACT_SCHEMA = "qingpu.expected-results.v1"
_LOCK_SCHEMA = "qingpu.expected-results-lock.v1"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sql_sha256(sql: str) -> str:
    return sha256_bytes(sql.strip().encode("utf-8"))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise ValueError("Expected-result values must be finite")
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@dataclass(frozen=True)
class ExpectedResultsBundle:
    artifact_path: Path
    artifact_sha256: str
    cases: Mapping[str, Mapping[str, Any]]

    def dataframe(self, case_id: str, gold_sql: str) -> pd.DataFrame:
        case = self.cases.get(case_id)
        if not isinstance(case, Mapping):
            raise ValueError(f"Missing frozen expected result for {case_id}")
        expected_sql_hash = case.get("gold_sql_sha256")
        actual_sql_hash = sql_sha256(gold_sql)
        if expected_sql_hash != actual_sql_hash:
            raise ValueError(
                f"Frozen expected result for {case_id} does not match gold_sql"
            )
        columns = case.get("columns")
        rows = case.get("rows")
        if not isinstance(columns, list) or not all(
            isinstance(column, str) for column in columns
        ):
            raise ValueError(f"Invalid frozen columns for {case_id}")
        if not isinstance(rows, list) or not all(isinstance(row, list) for row in rows):
            raise ValueError(f"Invalid frozen rows for {case_id}")
        if any(len(row) != len(columns) for row in rows):
            raise ValueError(f"Frozen row width mismatch for {case_id}")
        return pd.DataFrame(rows, columns=columns)


def load_expected_results(
    artifact_path: Path = DEFAULT_ARTIFACT,
    lock_path: Path = DEFAULT_LOCK,
    *,
    catalog_path: Path = DEFAULT_CATALOG,
    schema_path: Path = DEFAULT_SCHEMA,
    seed_generator_path: Path = DEFAULT_SEED_GENERATOR,
) -> ExpectedResultsBundle:
    """Load a sealed artifact and fail closed on any integrity drift."""
    artifact_path = artifact_path.resolve()
    lock_path = lock_path.resolve()
    artifact = _read_json(artifact_path)
    lock = _read_json(lock_path)
    if artifact.get("schema_version") != _ARTIFACT_SCHEMA:
        raise ValueError("Unsupported expected-result artifact schema")
    if lock.get("schema_version") != _LOCK_SCHEMA:
        raise ValueError("Unsupported expected-result lock schema")
    artifact_hash = sha256_file(artifact_path)
    if lock.get("artifact") != artifact_path.name:
        raise ValueError("Expected-result lock names a different artifact")
    if lock.get("artifact_sha256") != artifact_hash:
        raise ValueError("Expected-result artifact SHA-256 mismatch")

    fixture = artifact.get("fixture")
    if not isinstance(fixture, Mapping):
        raise ValueError("Expected-result artifact is missing fixture metadata")
    expected_fixture_hashes = {
        "catalog_sha256": sha256_file(catalog_path.resolve()),
        "schema_sha256": sha256_file(schema_path.resolve()),
        "seed_generator_sha256": sha256_file(seed_generator_path.resolve()),
    }
    for name, actual in expected_fixture_hashes.items():
        if fixture.get(name) != actual:
            raise ValueError(f"Expected-result fixture drift: {name}")

    cases = artifact.get("cases")
    if not isinstance(cases, Mapping):
        raise ValueError("Expected-result artifact cases must be an object")
    return ExpectedResultsBundle(artifact_path, artifact_hash, cases)


def seal_expected_results(
    dataset_path: Path = DEFAULT_DATASET,
    artifact_path: Path = DEFAULT_ARTIFACT,
    lock_path: Path = DEFAULT_LOCK,
    *,
    catalog_path: Path = DEFAULT_CATALOG,
    schema_path: Path = DEFAULT_SCHEMA,
    seed_generator_path: Path = DEFAULT_SEED_GENERATOR,
) -> tuple[Path, Path]:
    """Materialize gold SQL once into a deterministic, reviewable artifact."""
    records = []
    with dataset_path.resolve().open(encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"Expected object on {dataset_path}:{line_number}")
            records.append(record)

    cases: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="qingpu-expected-") as directory:
        database_path = Path(directory) / "fixture.db"
        initialize_database(database_path, reset=False)
        uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            for record in records:
                expectation = record.get("expectation") or {}
                if expectation.get("action") != "answer":
                    continue
                case_id = str(record.get("case_id") or "").strip()
                gold_sql = expectation.get("gold_sql")
                if not case_id or not isinstance(gold_sql, str) or not gold_sql.strip():
                    raise ValueError("Every answer case needs case_id and gold_sql")
                if case_id in cases:
                    raise ValueError(f"Duplicate answer case: {case_id}")
                cursor = connection.execute(gold_sql)
                try:
                    columns = [str(item[0]) for item in cursor.description or ()]
                    rows = [
                        [_json_value(value) for value in row]
                        for row in cursor.fetchall()
                    ]
                finally:
                    cursor.close()
                cases[case_id] = {
                    "gold_sql_sha256": sql_sha256(gold_sql),
                    "columns": columns,
                    "rows": rows,
                }
        finally:
            connection.close()
            del connection
            gc.collect()

    artifact = {
        "schema_version": _ARTIFACT_SCHEMA,
        "fixture": {
            "catalog_sha256": sha256_file(catalog_path.resolve()),
            "schema_sha256": sha256_file(schema_path.resolve()),
            "seed_generator_sha256": sha256_file(seed_generator_path.resolve()),
            "answer_case_count": len(cases),
        },
        "cases": dict(sorted(cases.items())),
    }
    artifact_path = artifact_path.resolve()
    lock_path = lock_path.resolve()
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    lock = {
        "schema_version": _LOCK_SCHEMA,
        "artifact": artifact_path.name,
        "artifact_sha256": sha256_file(artifact_path),
    }
    lock_path.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return artifact_path, lock_path
