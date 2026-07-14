"""Tests for deterministic local Qingpu database initialization."""

import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_DIR / "scripts" / "init_local_db.py"


def test_initialize_local_database_is_repeatable(tmp_path):
    database = tmp_path / "qingpu.db"
    command = [sys.executable, str(SCRIPT_PATH), "--database", str(database), "--reset"]

    subprocess.run(command, check=True, capture_output=True, text=True)
    subprocess.run(command, check=True, capture_output=True, text=True)

    with sqlite3.connect(database) as connection:
        dws_count = connection.execute(
            "SELECT COUNT(*) FROM dws_greenhouse_daily"
        ).fetchone()[0]
        ads_count = connection.execute(
            "SELECT COUNT(*) FROM ads_greenhouse_indicator"
        ).fetchone()[0]
        greenhouse_types = connection.execute(
            "SELECT DISTINCT greenhouse_type FROM ads_greenhouse_indicator "
            "ORDER BY greenhouse_type"
        ).fetchall()
        date_range = connection.execute(
            "SELECT MIN(dt), MAX(dt) FROM ads_greenhouse_indicator"
        ).fetchone()

    assert dws_count == 28
    assert ads_count == 28
    assert greenhouse_types == [("broad",), ("needle",)]
    assert date_range == ("2026-06-01", "2026-06-14")


def test_seed_has_human_checkable_14_day_boundaries(tmp_path):
    database = tmp_path / "qingpu.db"
    subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--database", str(database), "--reset"],
        check=True,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(database) as connection:
        needle = connection.execute(
            """
            SELECT COUNT(*), ROUND(SUM(avg_air_temp), 1),
                   ROUND(AVG(avg_air_temp), 1),
                   ROUND(MIN(avg_air_temp), 2), ROUND(MAX(avg_air_temp), 2)
            FROM dws_greenhouse_daily
            WHERE greenhouse_type = 'needle'
            """
        ).fetchone()
        broad = connection.execute(
            """
            SELECT COUNT(*), ROUND(SUM(avg_air_temp), 1),
                   ROUND(AVG(avg_air_temp), 1),
                   ROUND(MIN(avg_air_temp), 2), ROUND(MAX(avg_air_temp), 2)
            FROM dws_greenhouse_daily
            WHERE greenhouse_type = 'broad'
            """
        ).fetchone()
        first_and_last = connection.execute(
            """
            SELECT dt, greenhouse_type, ROUND(avg_air_temp, 2)
            FROM dws_greenhouse_daily
            WHERE dt IN ('2026-06-01', '2026-06-14')
            ORDER BY dt, greenhouse_type
            """
        ).fetchall()

    assert needle == (14, 269.4, 19.2, 18.2, 20.28)
    assert broad == (14, 335.2, 23.9, 22.9, 24.98)
    assert first_and_last == [
        ("2026-06-01", "broad", 22.9),
        ("2026-06-01", "needle", 18.2),
        ("2026-06-14", "broad", 24.98),
        ("2026-06-14", "needle", 20.28),
    ]


def test_seed_contains_a_documented_null_boundary(tmp_path):
    database = tmp_path / "qingpu.db"
    subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--database", str(database), "--reset"],
        check=True,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(database) as connection:
        null_count = connection.execute(
            "SELECT COUNT(*) FROM dws_greenhouse_daily WHERE avg_spad IS NULL"
        ).fetchone()[0]
        joined_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM ads_greenhouse_indicator a
            JOIN dws_greenhouse_daily d
              ON a.dt = d.dt AND a.greenhouse_type = d.greenhouse_type
            """
        ).fetchone()[0]
        view_count = connection.execute(
            "SELECT COUNT(*) FROM vw_greenhouse_daily_analysis"
        ).fetchone()[0]
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        pue_null_count = connection.execute(
            "SELECT COUNT(*) FROM ads_greenhouse_indicator WHERE pue IS NULL"
        ).fetchone()[0]

    assert null_count == 1
    assert pue_null_count == 1
    assert joined_count == 28
    assert view_count == 28
    assert foreign_key_errors == []
