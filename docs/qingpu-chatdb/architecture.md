# 架构设计

## 1. 目标与非目标

青圃智析以合成温室 DWS/ADS 场景为案例，在典型 Kafka/Flink/HDFS/Hive 数仓架构之上设计一条**可审计、只读、失败关闭**的 ChatDB 问数链路。示例逻辑表为：

- `green_test.ads_greenhouse_indicator`
- `green_test.dws_greenhouse_daily`

本阶段的目标是约束“候选 SQL 如何被授权、改写和执行”，而不是证明模型能够稳定理解自然语言。当前仓库使用完全由代码生成的 SQLite 合成 fixture；它不对应外部生产库，也不是 Hive/Impala 集群的性能替身。

明确不在当前能力范围内：

- 不声明 LLM/Text2SQL 准确率；
- 不声明 MySQL、Hive、Impala 已完成治理适配；
- 不声明生产吞吐、并发容量或大数据规模性能；
- 不把本地种子数据解释为生产采样或业务预测结果。

## 2. 改造前后与归属边界

| 维度 | DB-GPT 上游/改造前 | 青圃项目新增或重构 |
|---|---|---|
| 场景生命周期 | `BaseChat` 的 parse/action/render、ChatDB 场景 | 保持生命周期不变，在 `do_action()` 接入受控执行 |
| 数据访问 | 通用 connector 与 `run_to_df` | `SafeSqlExecutor` 保证只执行 `PreparedSql.sql` |
| SQL 处理 | 模型输出解析和 Prompt 约束 | sqlglot AST 形态、表、列、函数授权与 LIMIT 改写 |
| 语义 | Schema/Prompt 上下文 | `SemanticCatalog` 同时驱动 Prompt 与执行授权 |
| 结果对象 | DataFrame/视图协议 | `GuardedQueryResult` 绑定数据与实际执行 SQL |
| 评测 | 通用测试能力 | 固定 fixture、dataset lint、frozen oracle、Gold/Hardened |

本项目是基于 DB-GPT 的二次开发，不把上游 connector、LLM、Agent 或 Web 平台描述为个人独立实现。

## 3. 组件视图

```text
用户 / OpenAPI
  │
  ▼
ChatFactory → ChatWithDbAutoExecute
  │             │
  │             ├─ SemanticCatalog
  │             │    ├─ Prompt 可见表、列、指标说明
  │             │    └─ 执行授权与逻辑/物理表映射
  │             │
  │             ├─ LLM → 候选 SQL / direct response
  │             │
  │             ├─ DbChatOutputParser → SqlAction
  │             │
  │             └─ SafeSqlExecutor
  │                    ├─ ReadOnlySqlGuard
  │                    │    ├─ sqlglot AST 解析
  │                    │    ├─ 查询形态、表、列、函数授权
  │                    │    ├─ 逻辑表 → SQLite 物理表
  │                    │    ├─ LIMIT ≤ 50
  │                    │    └─ 物理闭包复核
  │                    └─ RDBMSConnector.run_to_df(PreparedSql.sql)
  │
  ▼
GuardedQueryResult → DbChatOutputParser → chart-view
```

## 4. 深模块与责任边界

| 模块 | 责任 | 不承担的责任 |
|---|---|---|
| `SemanticCatalog` | 加载表列、指标说明、别名和物理映射；同时生成 Prompt 上下文与授权快照 | 不编译业务指标 SQL，不判定自然语言意图 |
| `DbChatOutputParser` | 把模型文本解析为 `SqlAction`；把受控结果序列化为视图 | 不执行 SQL，不接受裸 `DataFrame` 作为 SQL 结果 |
| `ReadOnlySqlGuard` | 对单条 SQLite `SELECT` 做 AST 校验、授权、改写和重新序列化 | 不访问数据库，不保证候选 SQL 的业务含义正确 |
| `SafeSqlExecutor` | 保证 connector 仅接收 `PreparedSql.sql` | 不绕过 guard，不负责模型调用 |
| `GuardedQueryResult` | 将结果数据与实际执行计划绑定 | 不代表端到端请求追踪或生产审计存储 |

这一划分将不可信文本与数据库 connector 隔开。唯一允许进入 connector 的值是 guard 返回的 `PreparedSql.sql`。

## 5. 核心不变量

1. **同源语义与授权**：Prompt 披露和执行授权来自同一份不可变 catalog，避免“模型看见一套、执行器授权另一套”。
2. **执行前必须解析**：候选 SQL 必须被 sqlglot 解析为恰好一条 `SELECT`；字符串前缀判断不能替代 AST 校验。
3. **默认拒绝**：未知表、未知列、未知函数、歧义列和首版未支持查询形态全部拒绝。
4. **物理闭包**：逻辑表改写后再次解析，只允许 catalog 指定的 SQLite `main` 物理对象。
5. **有界结果**：最外层 `LIMIT` 缺失时注入，过大时收紧，硬上限为 50 行。
6. **展示即执行**：界面展示 `PreparedSql.sql` 作为实际执行 SQL，同时保留 `original_sql` 供审计。
7. **direct response 不触库**：没有 SQL 的模型响应不访问数据库。
8. **方言失败关闭**：当前只接受 `dialect == sqlite`，其他方言在场景初始化阶段拒绝。

## 6. 数据与部署边界

```text
示例上游架构：Kafka → Flink → HDFS/Hive → DWS/ADS
合成测试执行面：固定 schema/seed → 本地 SQLite → 治理链路回归
```

SQLite 测试面验证的是治理契约、确定性查询结果与拒绝规则。要进入生产，至少还需要：

- 为 Hive/Impala/MySQL 分别定义解析、标识符和物理命名空间规则；
- 使用数据库只读账号、statement timeout 和资源组；
- 增加行级权限、多租户隔离与敏感结果脱敏；
- 增加连接池、网络、模型服务和数据库端的分段追踪；
- 在代表性数据量与并发下另行执行容量测试。

## 7. 证据与声明边界

Canonical 证据统一由 [`manifest.json`](../../projects/qingpu_chatdb/evidence/2026-07-13-final/manifest.json) 管理：

- 冻结 oracle：47 个 answer expected results 及 lock；
- [Gold](../../projects/qingpu_chatdb/evidence/2026-07-13-final/reports/gold.md)：50/50 固定业务执行契约；
- [Hardened](../../projects/qingpu_chatdb/evidence/2026-07-13-final/reports/hardened.md)：20/20 block + 5/5 allow；
- 定向测试：98/98 Qingpu/ChatDB 测试。

当前证据在本地分阶段提交后重新生成，并包含推送前尚未提交的脱敏修正，因此暂不绑定 commit；manifest 使用关键输入文件 SHA-256 建立可追溯性。上述证据验证固定 SQLite、无 LLM 条件下的治理、执行和安全规则，**不测量 LLM 的自然语言理解、SQL 生成或回答准确率**。
