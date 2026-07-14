# 招聘版项目简介

## 一句话定位

基于 DB-GPT ChatDB 二次开发合成温室指标可信问数链路，为 DWS/ADS 示例数据增加统一语义目录、AST 只读 SQL 治理、受控执行和可复现离线评测。

## 项目背景

本项目使用合成温室 DWS/ADS 指标场景演示可信问数改造。DB-GPT 能把自然语言转换为 SQL，不过模型输出本身不可信：Prompt 约束不能阻止 DDL/DML、越权表列、危险函数或无界结果触达数据库；同时，如果评测在运行时执行与 candidate 同源的 gold SQL，还会形成循环验证。

项目目标是在不重写 DB-GPT `BaseChat` 生命周期的前提下，将 ChatDB 改造成一个本地可复现、失败关闭、结果可追溯的可信查询闭环。

## 个人贡献与上游边界

| DB-GPT 上游能力 | 个人新增/重构 |
|---|---|
| ChatDB 场景和 LLM 调用 | `SemanticCatalog`：15 项指标、别名、单位、方向、口径状态 |
| connector 抽象和 SQLite 支持 | `ReadOnlySqlGuard`：sqlglot AST 查询形态、表列函数授权 |
| `BaseChat` 生命周期 | `SafeSqlExecutor`：connector 只能接收 `PreparedSql.sql` |
| chart-view 输出协议 | `GuardedQueryResult`：绑定结果、candidate SQL 和实际执行 SQL |
| 通用测试框架 | 确定性 fixture、dataset lint、Gold/Hardened、冻结 oracle 和 evidence snapshot |

没有把 DB-GPT 的 Agent、RAG、Web Server、connector 等上游能力描述为个人独立实现。

## 核心技术设计

1. **语义与授权同源**：Prompt 可见指标和执行白名单来自同一份不可变 catalog。
2. **AST 安全边界**：只允许 SQLite 单条 `SELECT`；未知表列函数、歧义列、星号和未支持结构全部拒绝。
3. **确定性改写**：把示例逻辑表映射到本地 `main` 物理表，注入或收紧最外层 LIMIT ≤ 50，并重解析验证物理闭包。
4. **执行入口收敛**：`SafeSqlExecutor` 只执行 guard 返回的 SQL；output parser 不二次触库。
5. **独立冻结 oracle**：47 条 answer 的期望结果离线封存，正常评分不执行 gold SQL。
6. **正负安全样本**：Hardened 同时包含 20 个 block 和 5 个 allow，避免通过“拒绝一切”获得虚假高分。

## 量化成果

事实源：[`manifest.json`](../../../projects/qingpu_chatdb/evidence/2026-07-13-final/manifest.json)

| 成果 | 数值 | 范围 | 不代表什么 |
|---|---:|---|---|
| 业务指标目录 | 15 项 | 两张逻辑表 | 生产指标体系全量 |
| 定向测试 | 98/98 | Qingpu + ChatDB auto-execute | 全仓测试全部通过 |
| Gold | 50/50 | 47 answer + 3 refusal，预置 candidate，无 LLM | Text2SQL 准确率 |
| Hardened | 25/25 | 20 block + 5 allow | 所有 SQL 攻击覆盖 |
| Frozen oracle | 47 answer | artifact/lock/fixture/hash | 业务口径无需人工审核 |
| Gold P50/P95 | 3.6865/5.8892 ms | 本地 SQLite 案例路径 | 生产端到端性能 |
| Hardened P50/P95 | 0.3296/4.2371 ms | 多数为数据库前拒绝 | 正常查询性能 |

## 可直接用于简历的项目描述

### 版本 A：三条精简版

- 基于 DB-GPT ChatDB 二次开发温室指标可信问数平台，构建 15 项 DWS/ADS 指标语义目录，实现中英文别名、口径状态和示例逻辑表到 SQLite 物理表映射。
- 使用 sqlglot 实现只读 SQL AST 治理链，覆盖单语句 SELECT、表列函数白名单、危险结构拒绝、LIMIT≤50 和重写后物理闭包复核，并将 connector 执行入口收敛至 `SafeSqlExecutor`。
- 建立 50 条固定业务执行契约、25 条安全规则回归和 47 条冻结 expected results；在固定 SQLite、无 LLM 验收中取得 98 项定向测试通过、Gold 50/50、Hardened 25/25。

### 版本 B：强调评测可信度

- 设计独立 frozen oracle 与 dataset lint，校验 artifact、fixture、gold SQL hash、类别配额和 15 项指标覆盖，消除 candidate 与运行时 gold SQL 同源造成的循环验证，并输出可审计 JSON/Markdown 证据包。

## 已验证与未验证

| 已验证 | 未验证 |
|---|---|
| SQLite 单语句 SELECT 治理 | MySQL/PostgreSQL/Hive/Impala 方言 |
| 表、列、函数授权和 LIMIT | 行级权限、租户隔离、脱敏 |
| 固定合成 fixture 结果比较 | 外部生产数据和集群 |
| 20 block + 5 allow 安全回归 | 未知攻击和正式渗透测试 |
| 无 LLM 演示和评测 | LLM/Text2SQL 端到端准确率 |
| 单进程本地案例延迟 | 并发、吞吐、容量和生产 P95 |

## 面试时应主动说明

- 这是基于 DB-GPT 的二次开发，不是独立开发整个平台；
- 50/50 验证 candidate 之后的治理与执行，不是模型准确率；
- LIMIT 限制返回行数，不等于限制数据库扫描和排序开销；
- AST guard 是应用层纵深防御，生产仍需只读账号、timeout、资源组和数据库权限；
- 当前只实现 SQLite，目标方言必须分别做 parser/identifier/differential tests。
