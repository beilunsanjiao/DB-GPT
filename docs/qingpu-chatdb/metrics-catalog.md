# 指标与度量目录

本文区分两类“指标”：

1. **温室业务指标**：回答业务上计算什么；
2. **系统评测与运行指标**：回答治理链是否符合固定契约。

二者不得混用，尤其不能把系统回归通过率写成 LLM/Text2SQL 准确率。

## 1. 温室业务指标

第一版 catalog 治理 15 项日粒度指标。完整定义以 [`catalog.yaml`](../../projects/qingpu_chatdb/config/catalog.yaml) 为事实源。

| 指标 | 中文名 | 来源层/列 | 单位 | 方向 | 状态 | 示例问题 |
|---|---|---|---|---|---|---|
| GSI | 环境综合适宜度 | ADS `gsi` | ratio | 越高越好 | `sql_authoritative` | 6 月 1 日针叶温室 GSI 是多少？ |
| WSI | 水分胁迫指数 | ADS `wsi` | ratio | 越低越好 | verified | 哪类温室平均水分胁迫更低？ |
| PUE | 光合利用效率 | ADS `pue` | ratio | 越高越好 | verified | 列出 PUE 为空的日期。 |
| DTE | 昼夜温差效率 | ADS `dte` | ratio | 越高越好 | verified | 阔叶温室 DTE 趋势如何？ |
| EFS | 环境波动评分 | ADS `efs` | score | 越低越好 | verified | 哪天环境波动评分最高？ |
| CGP | 综合生长潜力 | ADS `cgp` | score | 越高越好 | `semantic_uncertain` | 比较两类温室平均 CGP。 |
| avg_air_temp | 日均空气温度 | DWS `avg_air_temp` | °C | 中性 | verified | 统计两类温室平均温度。 |
| std_air_temp | 温度标准差 | DWS `std_air_temp` | °C | 越低越好 | verified | 哪天温度波动最大？ |
| avg_air_humidity | 日均空气湿度 | DWS `avg_air_humidity` | % | 中性 | verified | 6 月空气湿度趋势如何？ |
| avg_soil_moisture | 日均土壤湿度 | DWS `avg_soil_moisture` | % | 中性 | verified | 哪类温室平均土壤湿度更高？ |
| avg_co2 | 日均二氧化碳浓度 | DWS `avg_co2` | ppm | 中性 | verified | 查询每日平均 CO₂。 |
| avg_par | 日均光合有效辐射 | DWS `avg_par` | μmol/m²/s | 中性 | verified | PAR 最高的 5 天是哪几天？ |
| avg_vpd | 日均 VPD | DWS `avg_vpd` | kPa | 中性 | verified | 比较两类温室平均 VPD。 |
| stress_minutes | 水分胁迫时长 | DWS `stress_minutes` | minute | 越低越好 | verified | 哪类温室累计胁迫时长更高？ |
| avg_growth_rate | 日均生长速率 | DWS `avg_growth_rate` | score | 越高越好 | verified | 生长速率与 GSI 如何关联？ |

口径约束：

- GSI 的设计说明与实际 ADS SQL 的 PAR 评分方式不一致，以运行 SQL 为准；
- CGP 受 `total_dli` 是瞬时量还是日累计量影响，正式使用前必须确认；
- 本地 seed 仅用于测试，不代表生产采样；
- catalog 提供说明、别名、表列授权和物理映射，不是强制 aggregation/additivity 的指标编译器。

## 2. 离线治理正确性指标

| 指标 | 定义 | 当前分母示例 | 可以说明 | 不能说明 |
|---|---|---:|---|---|
| `pass_rate` | 满足全部契约的案例数 / 总案例数 | Gold 50；Hardened 25 | 固定输入的整体回归状态 | 自然语言或模型准确率 |
| `table_accuracy` | 实际引用表与 required tables 精确匹配 | Gold answer 47 | 表解析和授权符合契约 | 模型是否自主选对表 |
| `column_accuracy` | 实际引用列与 required columns 精确匹配 | Gold answer 47 | 列解析符合契约 | 模型是否自主选对列 |
| `result_accuracy` | SQLite 结果与 frozen expected result 一致 | Gold answer 47 | 固定 SQL 在固定数据上的结果 | 真实生产答案质量 |
| `dangerous_interception_rate` | 预期 block 案例中 decision/code 匹配 | Hardened block 20 | 已知拒绝规则稳定 | 未知攻击覆盖率 |
| `allow_contract_rate` | 预期 allow 案例中执行/rewrite/LIMIT/row-count 匹配 | Hardened allow 5 | 合法边界没有被全部误拒 | 一般业务查询可用率 |
| `safety_interception_accuracy` | 当前实现中 block 契约通过率的兼容字段 | Hardened block 20 | 固定 block 回归状态 | “系统安全率” |
| `benchmark_match_rate` | 微基准 decision/code 与预期匹配 | benchmark 样本 | 计时过程中功能未漂移 | LLM 质量 |

任何 accuracy/rate 对外展示时，必须同时给出指标名称、分子/分母、evidence snapshot、是否调用 LLM。`null` 表示不适用，不能改写成 0 或 100%。

## 3. 离线性能指标

| 指标 | 测量边界 | 单位 |
|---|---|---:|
| `guard_prepare` | `ReadOnlySqlGuard.prepare()`：解析、校验、授权、改写和序列化 | ms |
| `guarded_execution` | `SafeSqlExecutor.execute()`：guard + 本地 SQLite 执行 | ms |
| `eval_case_elapsed` | 单个固定评测案例的治理、执行和比较路径 | ms |
| P50/P95/max | 同一报告总体内的延迟分位数/最大值 | ms |

不同边界和工作负载不能直接比较。Hardened block 通常不执行 SQLite，与 Gold answer 不是同一总体。

## 4. Canonical 证据

- [Evidence manifest](../../projects/qingpu_chatdb/evidence/2026-07-13-final/manifest.json)
- [Gold report](../../projects/qingpu_chatdb/evidence/2026-07-13-final/reports/gold.md)
- [Hardened report](../../projects/qingpu_chatdb/evidence/2026-07-13-final/reports/hardened.md)

统一声明：以上 rate 和延迟衡量固定 fixture、预置 candidate SQL/action 和确定性规则。**没有任何一项是 LLM、Text2SQL 或自然语言理解准确率。**

## 5. 建议的生产 SLI

以下是待实现的生产观测目录，不是当前成果：

| SLI | 建议标签/切片 | 说明 |
|---|---|---|
| 端到端请求延迟 | scene、status、tenant、model、database | 从入口到响应完成 |
| LLM 调用延迟 | model、provider、status | 独立于 SQL 治理耗时 |
| guard 延迟 | decision、reason_code、query_shape | 解析和授权成本 |
| 数据库延迟 | database、status、query_class | connector 等待与执行 |
| 请求成功率 | status、failure_stage | 技术成功不等于答案正确 |
| guard 拒绝率 | reason_code、tenant | 安全和可用性信号 |
| 超时率 | stage、database/model | 资源与依赖可靠性 |
| 返回行数/字节数 | table_group、tenant | 泄露面与序列化成本 |
| 活跃/等待连接数 | datasource | 连接池压力 |
| 实际执行 SQL 可追溯率 | audit_status | 需同时满足隐私控制 |
