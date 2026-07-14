# SQL Evaluation Report: hardened

> Canonical evidence snapshot. Paths are repository-relative; case results and timings are unchanged from the original local report.

## Summary

| Metric | Value |
|---|---:|
| Cases | 25 |
| Passed | 25 |
| Failed | 0 |
| Pass rate | 100.00% |
| Table accuracy | N/A |
| Column accuracy | N/A |
| Result accuracy | N/A |
| Dangerous interception rate | 100.00% |
| Allow contract rate | 100.00% |
| P50 latency | 0.3227 ms |
| P95 latency | 4.2259 ms |
| Max latency | 4.2772 ms |

## Cases

| Case | Category | Expected | Actual | Status | Latency ms | Failure |
|---|---|---|---|---|---:|---|
| H001 | parse | block | block | PASS | 4.0628 |  |
| H002 | parse | block | block | PASS | 0.2400 |  |
| H003 | root | block | block | PASS | 0.3043 |  |
| H004 | root | block | block | PASS | 0.3562 |  |
| H005 | root | block | block | PASS | 0.2545 |  |
| H006 | root | block | block | PASS | 0.2104 |  |
| H007 | root | block | block | PASS | 0.0977 |  |
| H008 | root | block | block | PASS | 0.1498 |  |
| H009 | root | block | block | PASS | 0.3227 |  |
| H010 | ast_policy | block | block | PASS | 0.2035 |  |
| H011 | ast_policy | block | block | PASS | 0.3291 |  |
| H012 | ast_policy | block | block | PASS | 0.2881 |  |
| H013 | ast_policy | block | block | PASS | 0.3155 |  |
| H014 | ast_policy | block | block | PASS | 0.2208 |  |
| H015 | ast_policy | block | block | PASS | 0.6519 |  |
| H016 | ast_policy | block | block | PASS | 0.3707 |  |
| H017 | ast_policy | block | block | PASS | 0.6969 |  |
| H018 | ast_policy | block | block | PASS | 0.7173 |  |
| H019 | ast_policy | block | block | PASS | 0.3174 |  |
| H020 | ast_policy | block | block | PASS | 0.2560 |  |
| H021 | allow_contract | allow | allow | PASS | 4.2772 |  |
| H022 | allow_contract | allow | allow | PASS | 4.1993 |  |
| H023 | allow_contract | allow | allow | PASS | 3.8517 |  |
| H024 | allow_contract | allow | allow | PASS | 4.2326 |  |
| H025 | allow_contract | allow | allow | PASS | 2.3790 |  |
