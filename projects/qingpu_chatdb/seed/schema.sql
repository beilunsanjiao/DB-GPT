PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS dws_greenhouse_daily (
    dt TEXT NOT NULL CHECK (dt = date(dt)),
    greenhouse_type TEXT NOT NULL CHECK (greenhouse_type IN ('needle', 'broad')),
    avg_air_temp REAL CHECK (avg_air_temp BETWEEN -20.0 AND 60.0),
    std_air_temp REAL CHECK (std_air_temp >= 0.0),
    avg_air_humidity REAL CHECK (avg_air_humidity BETWEEN 0.0 AND 100.0),
    avg_light REAL CHECK (avg_light >= 0.0),
    avg_soil_moisture REAL CHECK (avg_soil_moisture BETWEEN 0.0 AND 100.0),
    avg_co2 REAL CHECK (avg_co2 >= 0.0),
    avg_par REAL CHECK (avg_par >= 0.0),
    day_vpd_max REAL CHECK (day_vpd_max >= 0.0),
    night_temp_avg REAL CHECK (night_temp_avg BETWEEN -20.0 AND 60.0),
    day_temp_avg REAL CHECK (day_temp_avg BETWEEN -20.0 AND 60.0),
    total_dli REAL CHECK (total_dli >= 0.0),
    avg_growth_rate REAL CHECK (avg_growth_rate >= 0.0),
    stress_minutes INTEGER CHECK (stress_minutes BETWEEN 0 AND 1440),
    avg_vpd REAL CHECK (avg_vpd >= 0.0),
    avg_spad REAL CHECK (avg_spad BETWEEN 0.0 AND 100.0),
    PRIMARY KEY (dt, greenhouse_type)
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS ads_greenhouse_indicator (
    dt TEXT NOT NULL CHECK (dt = date(dt)),
    greenhouse_type TEXT NOT NULL CHECK (greenhouse_type IN ('needle', 'broad')),
    gsi REAL CHECK (gsi BETWEEN 0.0 AND 1.0),
    wsi REAL CHECK (wsi BETWEEN 0.0 AND 1.0),
    pue REAL CHECK (pue >= 0.0),
    dte REAL CHECK (dte BETWEEN 0.0 AND 1.0),
    efs REAL CHECK (efs >= 0.0),
    cgp REAL CHECK (cgp >= 0.0),
    PRIMARY KEY (dt, greenhouse_type),
    FOREIGN KEY (dt, greenhouse_type)
        REFERENCES dws_greenhouse_daily(dt, greenhouse_type)
        ON UPDATE CASCADE ON DELETE CASCADE
) STRICT, WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_dws_greenhouse_type_dt
    ON dws_greenhouse_daily(greenhouse_type, dt);

CREATE INDEX IF NOT EXISTS idx_ads_greenhouse_type_dt
    ON ads_greenhouse_indicator(greenhouse_type, dt);

-- A stable join seam for questions that combine governed ADS and DWS metrics.
CREATE VIEW IF NOT EXISTS vw_greenhouse_daily_analysis AS
SELECT
    d.dt,
    d.greenhouse_type,
    d.avg_air_temp,
    d.std_air_temp,
    d.avg_air_humidity,
    d.avg_light,
    d.avg_soil_moisture,
    d.avg_co2,
    d.avg_par,
    d.day_vpd_max,
    d.night_temp_avg,
    d.day_temp_avg,
    d.total_dli,
    d.avg_growth_rate,
    d.stress_minutes,
    d.avg_vpd,
    d.avg_spad,
    a.gsi,
    a.wsi,
    a.pue,
    a.dte,
    a.efs,
    a.cgp
FROM dws_greenhouse_daily AS d
JOIN ads_greenhouse_indicator AS a
  ON a.dt = d.dt
 AND a.greenhouse_type = d.greenhouse_type;
