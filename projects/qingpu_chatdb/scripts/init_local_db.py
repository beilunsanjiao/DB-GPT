#!/usr/bin/env python3
"""Create the deterministic local Qingpu SQLite database."""

import argparse
import sqlite3
from datetime import date, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = PROJECT_DIR / "data" / "qingpu.db"
SCHEMA_PATH = PROJECT_DIR / "seed" / "schema.sql"


def _daily_rows():
    start = date(2026, 6, 1)
    for day_index in range(14):
        current = start + timedelta(days=day_index)
        for greenhouse_type, offset in (("needle", 0.0), ("broad", 1.0)):
            avg_air_temp = 18.2 + offset * 4.7 + day_index * 0.16
            std_air_temp = 1.05 + offset * 0.18 + (day_index % 3) * 0.07
            avg_air_humidity = 68.0 + offset * 2.5 - (day_index % 4) * 0.8
            avg_light = 455.0 + offset * 38.0 + day_index * 5.0
            avg_soil_moisture = 64.0 + offset * 3.0 - day_index * 0.45
            avg_co2 = 610.0 + offset * 32.0 + day_index * 3.0
            avg_par = 430.0 + offset * 55.0 + day_index * 7.0
            day_vpd_max = 1.25 + offset * 0.12 + day_index * 0.025
            night_temp_avg = avg_air_temp - (
                5.9 if greenhouse_type == "needle" else 4.4
            )
            day_temp_avg = avg_air_temp + (5.7 if greenhouse_type == "needle" else 4.6)
            total_dli = 15.8 + offset * 2.2 + day_index * 0.22
            avg_growth_rate = 1.45 + offset * 0.48 + day_index * 0.035
            stress_minutes = int(round(max(0.0, (day_vpd_max - 1.35) * 180.0)))
            avg_vpd = day_vpd_max - 0.28
            avg_spad = 31.5 + offset * 2.0 + day_index * 0.09

            if day_index == 10 and greenhouse_type == "needle":
                avg_spad = None

            yield (
                current.isoformat(),
                greenhouse_type,
                avg_air_temp,
                std_air_temp,
                avg_air_humidity,
                avg_light,
                avg_soil_moisture,
                avg_co2,
                avg_par,
                day_vpd_max,
                night_temp_avg,
                day_temp_avg,
                total_dli,
                avg_growth_rate,
                stress_minutes,
                avg_vpd,
                avg_spad,
            )


def _indicator_rows(daily_rows):
    for row in daily_rows:
        (
            dt,
            greenhouse_type,
            avg_air_temp,
            std_air_temp,
            avg_air_humidity,
            _avg_light,
            avg_soil_moisture,
            avg_co2,
            avg_par,
            _day_vpd_max,
            night_temp_avg,
            day_temp_avg,
            total_dli,
            avg_growth_rate,
            stress_minutes,
            avg_vpd,
            avg_spad,
        ) = row
        opt_temp = 18.0 if greenhouse_type == "needle" else 24.0
        opt_range = 12.0 if greenhouse_type == "needle" else 9.0
        target_dli = 18.0 if greenhouse_type == "needle" else 22.0
        max_growth = 2.2 if greenhouse_type == "needle" else 3.0

        temp_score = max(0.0, 1.0 - abs(avg_air_temp - opt_temp) / 12.0)
        humidity_score = max(0.0, 1.0 - abs(avg_air_humidity - 70.0) / 30.0)
        par_score = min(1.0, max(0.0, avg_par / 700.0))
        co2_score = max(0.0, 1.0 - abs(avg_co2 - 700.0) / 500.0)
        gsi = min(
            1.0,
            max(
                0.0,
                temp_score * 0.35
                + humidity_score * 0.25
                + par_score * 0.2
                + co2_score * 0.2,
            ),
        )
        wsi = min(
            1.0,
            max(
                0.0,
                0.45 * (avg_vpd / 1.8) + 0.55 * (1.0 - avg_soil_moisture / 100.0),
            ),
        )
        pue = (
            None
            if avg_spad is None
            else max(0.0, avg_growth_rate * avg_spad / (avg_par * 50.0))
        )
        dte = min(
            1.0,
            max(
                0.0,
                1.0
                - min(
                    abs(day_temp_avg - night_temp_avg - opt_range) / opt_range,
                    1.0,
                ),
            ),
        )
        efs = max(0.0, 0.5 * (std_air_temp / avg_air_temp))
        cgp = max(
            0.0,
            (total_dli / target_dli) * (avg_growth_rate / max_growth) * temp_score,
        )
        if stress_minutes > 35:
            gsi = max(0.0, gsi - 0.04)
        yield (
            dt,
            greenhouse_type,
            round(gsi, 6),
            round(wsi, 6),
            None if pue is None else round(pue, 8),
            round(dte, 6),
            round(efs, 6),
            round(cgp, 6),
        )


def initialize_database(database_path: Path, reset: bool = False) -> None:
    database_path = database_path.resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if reset and database_path.exists():
        database_path.unlink()

    daily_rows = list(_daily_rows())
    indicator_rows = list(_indicator_rows(daily_rows))
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        connection.execute("DELETE FROM ads_greenhouse_indicator")
        connection.execute("DELETE FROM dws_greenhouse_daily")
        connection.executemany(
            """
            INSERT INTO dws_greenhouse_daily VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            daily_rows,
        )
        connection.executemany(
            "INSERT INTO ads_greenhouse_indicator VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            indicator_rows,
        )
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError(f"foreign key violations: {foreign_key_errors}")
        connection.commit()

    print(
        f"Initialized {database_path} with "
        f"{len(daily_rows)} DWS rows and {len(indicator_rows)} ADS rows."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    initialize_database(args.database, reset=args.reset)


if __name__ == "__main__":
    main()
