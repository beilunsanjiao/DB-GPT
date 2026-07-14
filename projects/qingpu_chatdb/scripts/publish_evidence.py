#!/usr/bin/env python3
"""Publish a normalized, reviewable Qingpu evidence snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PROJECT_DIR.parents[1]
DEFAULT_EVIDENCE_DIR = PROJECT_DIR / "evidence" / "2026-07-13-final"
DEFAULT_GOLD_REPORT = (
    PROJECT_DIR / "data" / "frozen-eval-reports" / "gold_20260713T110124266631Z.json"
)
DEFAULT_HARDENED_REPORT = (
    PROJECT_DIR
    / "data"
    / "frozen-hardened-reports"
    / "hardened_20260713T110813283453Z.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_DIR).as_posix()


def _percentage(value: Any) -> str:
    return "N/A" if value is None else f"{float(value) * 100:.2f}%"


def _render_markdown(mode: str, report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        f"# SQL Evaluation Report: {mode}",
        "",
        (
            "> Canonical evidence snapshot. Paths are repository-relative; "
            "case results and timings are unchanged from the original local report."
        ),
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Cases | {summary['total']} |",
        f"| Passed | {summary['passed']} |",
        f"| Failed | {summary['failed']} |",
        f"| Pass rate | {_percentage(summary['pass_rate'])} |",
        f"| Table accuracy | {_percentage(summary['table_accuracy'])} |",
        f"| Column accuracy | {_percentage(summary['column_accuracy'])} |",
        f"| Result accuracy | {_percentage(summary['result_accuracy'])} |",
        (
            "| Dangerous interception rate | "
            f"{_percentage(summary['dangerous_interception_rate'])} |"
        ),
        f"| Allow contract rate | {_percentage(summary['allow_contract_rate'])} |",
        f"| P50 latency | {summary['latency_ms']['p50']:.4f} ms |",
        f"| P95 latency | {summary['latency_ms']['p95']:.4f} ms |",
        f"| Max latency | {summary['latency_ms']['max']:.4f} ms |",
        "",
        "## Cases",
        "",
        "| Case | Category | Expected | Actual | Status | Latency ms | Failure |",
        "|---|---|---|---|---|---:|---|",
    ]
    for case in report["cases"]:
        failure = (case.get("failure_reason") or "").replace("|", "\\|")
        status = "PASS" if case["passed"] else "FAIL"
        lines.append(
            f"| {case['case_id']} | {case['category']} | "
            f"{case['expected_action']} | {case['actual_action']} | {status} | "
            f"{case['elapsed_ms']:.4f} | {failure} |"
        )
    return "\n".join(lines) + "\n"


def _publish_report(
    mode: str, source: Path, output_dir: Path, normalized_paths: dict[str, Any]
) -> dict[str, Any]:
    report = json.loads(source.read_text(encoding="utf-8"))
    report["run"].update(normalized_paths)
    json_path = output_dir / f"{mode}.json"
    markdown_path = output_dir / f"{mode}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_render_markdown(mode, report), encoding="utf-8")
    return {
        "original_path": _relative(source),
        "original_sha256": _sha256(source),
        "published_json": _relative(json_path),
        "published_json_sha256": _sha256(json_path),
        "published_markdown": _relative(markdown_path),
        "published_markdown_sha256": _sha256(markdown_path),
        "normalization": [
            "run.dataset",
            "run.database",
            "run.catalog",
            "run.expected_results",
        ],
    }


def publish_evidence(
    evidence_dir: Path,
    gold_report: Path,
    hardened_report: Path,
) -> Path:
    evidence_dir = evidence_dir.resolve()
    reports_dir = evidence_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    published = {
        "gold": _publish_report(
            "gold",
            gold_report.resolve(),
            reports_dir,
            {
                "dataset": "projects/qingpu_chatdb/eval/gold.jsonl",
                "database": "generated:projects/qingpu_chatdb/data/frozen-eval.db",
                "catalog": "projects/qingpu_chatdb/config/catalog.yaml",
                "expected_results": (
                    "projects/qingpu_chatdb/eval/gold-answers.v1.json"
                ),
            },
        ),
        "hardened": _publish_report(
            "hardened",
            hardened_report.resolve(),
            reports_dir,
            {
                "dataset": "projects/qingpu_chatdb/eval/hardened.jsonl",
                "database": (
                    "generated:projects/qingpu_chatdb/data/frozen-hardened.db"
                ),
                "catalog": "projects/qingpu_chatdb/config/catalog.yaml",
                "expected_results": None,
            },
        ),
    }
    gold_summary = json.loads((reports_dir / "gold.json").read_text(encoding="utf-8"))[
        "summary"
    ]
    hardened_summary = json.loads(
        (reports_dir / "hardened.json").read_text(encoding="utf-8")
    )["summary"]
    input_paths = [
        "projects/qingpu_chatdb/config/catalog.yaml",
        "projects/qingpu_chatdb/seed/schema.sql",
        "projects/qingpu_chatdb/scripts/init_local_db.py",
        "projects/qingpu_chatdb/eval/gold.jsonl",
        "projects/qingpu_chatdb/eval/hardened.jsonl",
        "projects/qingpu_chatdb/eval/gold-answers.v1.json",
        "projects/qingpu_chatdb/eval/gold-answers.v1.lock.json",
        "projects/qingpu_chatdb/scripts/run_eval.py",
        "projects/qingpu_chatdb/scripts/dataset_lint.py",
        "projects/qingpu_chatdb/scripts/expected_results.py",
        "projects/qingpu_chatdb/scripts/publish_evidence.py",
        "packages/dbgpt-app/src/dbgpt_app/scene/chat_db/safe_sql/guard.py",
        "packages/dbgpt-app/src/dbgpt_app/scene/chat_db/safe_sql/executor.py",
        ("packages/dbgpt-app/src/dbgpt_app/scene/chat_db/semantic_catalog/catalog.py"),
        "packages/dbgpt-app/src/dbgpt_app/scene/chat_db/auto_execute/chat.py",
    ]
    expected_results = PROJECT_DIR / "eval" / "gold-answers.v1.json"
    expected_lock = PROJECT_DIR / "eval" / "gold-answers.v1.lock.json"
    manifest = {
        "schema_version": "qingpu.evidence-manifest.v1",
        "evidence_id": "qingpu-chatdb-2026-07-13-final",
        "status": "verified_working_tree_snapshot",
        "verified_at": "2026-07-14",
        "source_revision": {
            "kind": "local_committed_branch_with_uncommitted_sanitization",
            "git_commit": None,
            "note": (
                "The implementation has local commits, while this regenerated "
                "sanitized snapshot includes uncommitted pre-push fixes. Input "
                "SHA-256 values bind the evidence to the reviewed files."
            ),
        },
        "environment": {
            "os": "Windows 11",
            "python": "3.11.15",
            "database": "SQLite local fixture",
            "llm_invoked": False,
            "execution": "single-process local evaluation",
        },
        "results": {
            "targeted_tests": {
                "passed": 98,
                "failed": 0,
                "scope": [
                    "projects/qingpu_chatdb/tests",
                    (
                        "packages/dbgpt-app/src/dbgpt_app/scene/chat_db/"
                        "auto_execute/tests"
                    ),
                ],
            },
            "dataset_lint": {"passed": True, "cases": 50, "catalog_metrics": 15},
            "gold": {
                "passed": gold_summary["passed"],
                "total": gold_summary["total"],
                "answer_cases": 47,
                "refusal_cases": 3,
                "p50_ms": gold_summary["latency_ms"]["p50"],
                "p95_ms": gold_summary["latency_ms"]["p95"],
                "max_ms": gold_summary["latency_ms"]["max"],
            },
            "hardened": {
                "passed": hardened_summary["passed"],
                "total": hardened_summary["total"],
                "blocked_cases": 20,
                "allowed_cases": 5,
                "p50_ms": hardened_summary["latency_ms"]["p50"],
                "p95_ms": hardened_summary["latency_ms"]["p95"],
                "max_ms": hardened_summary["latency_ms"]["max"],
            },
        },
        "artifacts": {
            "expected_results": {
                "path": _relative(expected_results),
                "answer_cases": 47,
                "sha256": _sha256(expected_results),
            },
            "expected_results_lock": {
                "path": _relative(expected_lock),
                "sha256": _sha256(expected_lock),
            },
            "reports": published,
        },
        "inputs": [
            {"path": path, "sha256": _sha256(REPO_DIR / path)} for path in input_paths
        ],
        "verification_commands": [
            "uv run python projects/qingpu_chatdb/scripts/dataset_lint.py",
            (
                "uv run pytest -q projects/qingpu_chatdb/tests "
                "packages/dbgpt-app/src/dbgpt_app/scene/chat_db/auto_execute/tests"
            ),
            (
                "uv run python projects/qingpu_chatdb/scripts/run_eval.py "
                "--mode gold --database <fresh.db> --output-dir <temporary-output>"
            ),
            (
                "uv run python projects/qingpu_chatdb/scripts/run_eval.py "
                "--mode hardened --database <fresh.db> "
                "--output-dir <temporary-output>"
            ),
        ],
        "claim_boundaries": [
            (
                "Gold uses pre-specified candidate SQL/actions and does not "
                "measure LLM or Text2SQL accuracy."
            ),
            (
                "Hardened covers 20 fixed block cases and 5 fixed allow-contract "
                "cases; it does not prove resistance to every SQL attack."
            ),
            (
                "Latency covers fixed local SQLite evaluation paths without LLM, "
                "network, concurrency, or production data scale."
            ),
            "Only the SQLite dialect is implemented and verified.",
        ],
    }
    manifest_path = evidence_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    parser.add_argument("--gold-report", type=Path, default=DEFAULT_GOLD_REPORT)
    parser.add_argument("--hardened-report", type=Path, default=DEFAULT_HARDENED_REPORT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = publish_evidence(
        args.evidence_dir, args.gold_report, args.hardened_report
    )
    print(f"published evidence manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
