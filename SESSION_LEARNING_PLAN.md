# DB-GPT 学习与青圃智析项目记录

> **文档定位：早期学习规划与当前成果索引。当前项目事实、数字和 SHA 以 [`projects/qingpu_chatdb/README.md`](projects/qingpu_chatdb/README.md) 和 [evidence manifest](projects/qingpu_chatdb/evidence/2026-07-13-final/manifest.json) 为准。**

## 1. 学习目标

学习 DB-GPT 的目的不是简单部署开源项目，而是面向大数据开发岗位完成源码级二次开发，形成能讲清业务背景、数据模型、SQL 治理、评测方法和生产边界的工程项目。

目标定位：

> **具备 AI 数据应用能力的大数据开发工程师。**

项目重心放在数据链路、数仓指标、SQL、数据治理和工程验证；LLM、RAG、Agent 作为差异化能力，不冒充已经验证的生产能力。

## 2. DB-GPT 架构学习结论

DB-GPT 是 Agentic AI 数据分析平台，包含：

- 数据库、CSV/Excel、数仓和知识库连接；
- Text2SQL、Python 分析和可视化；
- Agent、Skills、AWEL 和 RAG；
- FastAPI/Web 应用、connector 和沙箱执行。

核心包：

| 包 | 主要职责 |
|---|---|
| `dbgpt-core` | 组件、Agent、模型、AWEL 等核心抽象 |
| `dbgpt-ext` | 数据源、RAG、存储等具体实现 |
| `dbgpt-serve` | Service 和 REST API |
| `dbgpt-app` | 完整应用编排和业务场景 |
| `dbgpt-client` | SDK 和客户端 CLI |
| `dbgpt-sandbox` | 隔离执行环境 |

Web 启动主链：

```text
dbgpt CLI
  → dbgpt_app._cli.start_webserver
  → dbgpt_app.dbgpt_server.run_webserver
  → load_config / initialize_app / mount_routers / components
  → uvicorn
```

ChatDB 改造调用链详见 [`call-chain.md`](docs/qingpu-chatdb/call-chain.md)。

## 3. 项目演进

原青圃大数据实训已经具备 Kafka → Flink → HDFS/Hive → DWS/ADS 的数据加工思路，但查询入口以固定接口和报表为主。DB-GPT ChatDB 能生成 SQL，却不能仅靠 Prompt 构成安全边界。

项目最终收敛为：

> **青圃智析：基于 DB-GPT 的温室指标可信问数平台。**

```text
青圃 DWS/ADS 指标
      ↓
SemanticCatalog（语义 + 授权）
      ↓
DB-GPT ChatDB candidate SQL
      ↓
ReadOnlySqlGuard（AST 校验/改写）
      ↓
SafeSqlExecutor
      ↓
SQLite fixture / GuardedQueryResult / 审计报告
```

## 4. 已完成里程碑

### M0 环境与基线

- [x] 建立 Python 3.11 + uv workspace 环境；
- [x] 创建 `feature/qingpu-trusted-query` 工作分支；
- [x] 验证 SQLite connector 和相关测试入口；
- [x] 为 `dbgpt-app` 声明 `sqlglot` 直接依赖并更新 lockfile。

### M1 领域语义目录与本地数据

- [x] 建立 2 张逻辑表与物理表映射；
- [x] 建立 15 项温室指标及中英文别名、单位、方向和口径状态；
- [x] 标记 GSI `sql_authoritative`、CGP `semantic_uncertain`；
- [x] 构造 14 天 × 2 类温室的确定性 SQLite fixture，DWS/ADS 各 28 行；
- [x] 增加 STRICT/CHECK/外键和 NULL 边界数据。

### M2 AST 安全执行器

- [x] 使用 sqlglot 替代字符串或 sqlparse 安全判断；
- [x] 只允许 SQLite 单条 `SELECT` 和受控 CTE；
- [x] 实现表、列、函数白名单；
- [x] 拒绝 DDL/DML、多语句、系统表、星号和未支持形态；
- [x] 实现逻辑表到 `main` 物理表的 AST 改写；
- [x] 注入/收紧最外层 LIMIT，硬上限 50；
- [x] 重写后再次解析并验证物理闭包；
- [x] 通过 `SafeSqlExecutor` 保证 connector 只接收 `PreparedSql.sql`。

### M3 ChatDB 主链路接入

- [x] `ChatWithDbAutoExecute` 初始化时加载 catalog 和 guard；
- [x] Prompt 上下文与执行授权使用同一 catalog；
- [x] SQL 分支经 `SafeSqlExecutor` 执行；
- [x] direct response 不访问数据库；
- [x] output parser 不再执行 SQL；
- [x] `GuardedQueryResult` 同时携带结果、原始 SQL 和实际执行 SQL；
- [x] 未支持方言初始化时 fail closed。

### M4 评测可信度

- [x] 50 条 Gold 固定业务执行契约：47 answer + 3 refusal；
- [x] 25 条 Hardened：20 block + 5 allow-contract；
- [x] dataset lint：类别配额、15 指标覆盖、来源契约、重复项和 SQL 确定性；
- [x] 冻结 47 条 expected results；
- [x] artifact/lock、fixture 和 gold SQL hash 校验；
- [x] 禁止评分时执行 gold SQL 生成 oracle；
- [x] JSON/Markdown 报告和案例延迟统计。

### M5 文档与演示

- [x] 架构、调用链、威胁模型、指标、评测和性能文档；
- [x] 无 LLM demo 和 benchmark；
- [x] canonical evidence snapshot；
- [x] 招聘版项目简介、面试讲解和演示验收材料。

## 5. 当前验收结果

事实源：[`manifest.json`](projects/qingpu_chatdb/evidence/2026-07-13-final/manifest.json)

| 项目 | 结果 | 边界 |
|---|---:|---|
| 定向 pytest | 98/98 | Qingpu + ChatDB auto-execute 测试 |
| Dataset lint | 50 cases / 15 metrics | Gold 数据集门禁 |
| Gold | 50/50 | 固定 candidate SQL/action、无 LLM |
| Hardened | 25/25 | 20 block + 5 allow |
| Frozen oracle | 47 answers | artifact + lock + hash |

这些结果不能写成 Text2SQL 准确率、安全率或生产性能。

## 6. 已达到的简历标准

- [x] 3 个以上源码级改造：SemanticCatalog、AST Guard/SafeSqlExecutor、ChatDB 职责重构、冻结评测；
- [x] 15 项业务指标和明确口径状态；
- [x] 50 条固定业务契约、25 条安全规则回归；
- [x] 98 项定向测试；
- [x] 可复现 SQLite fixture、README、架构图、威胁模型和报告；
- [x] 演示和面试材料；
- [x] 所有当前量化数字均可追溯到 evidence snapshot。

仍未满足或需后续补齐：

- [x] 已形成三阶段本地 Git 提交历史；推送前正在进行脱敏重写与最终验证；
- [ ] 端到端 LLM/Text2SQL 盲测；
- [ ] 非 SQLite 方言适配；
- [ ] 目标 Hive/Impala 方言和合成集成环境验证；
- [ ] 生产级行列权限、审计存储、timeout 和资源治理；
- [ ] 代表性数据规模、并发和容量验证。

## 7. 下一阶段路线

### P0：版本化与交付

1. 整理分阶段提交，确保个人改造可从 Git 历史定位；
2. 在提交后的 clean revision 上创建新 evidence snapshot；
3. 保存机器可读测试报告、环境信息和源码版本。

### P1：端到端 LLM 评测

1. 建立不含 candidate SQL 的盲测集；
2. 固定模型、Prompt、温度和重试；
3. 报告 execution accuracy、result equivalence、拒答 precision/recall、成本和延迟；
4. 与当前“候选 SQL 之后”的治理评测分开披露。

### P2：目标方言与生产防御

1. 为 Hive/Impala/MySQL 分别建立 dialect adapter 和 differential tests；
2. 使用只读账号、statement timeout、资源组和连接池；
3. 增加租户/行列权限和结果脱敏；
4. 建立代表性合成数据量、并发、长稳和故障恢复测试。

## 8. 文档导航

- [项目 README](projects/qingpu_chatdb/README.md)
- [架构设计](docs/qingpu-chatdb/architecture.md)
- [调用链](docs/qingpu-chatdb/call-chain.md)
- [威胁模型](docs/qingpu-chatdb/threat-model.md)
- [评测设计](docs/qingpu-chatdb/evaluation.md)
- [性能边界](docs/qingpu-chatdb/performance.md)
- [招聘版简介](docs/qingpu-chatdb/recruiting/project-brief.md)
- [面试讲解](docs/qingpu-chatdb/recruiting/interview-playbook.md)
- [演示验收](docs/qingpu-chatdb/recruiting/demo-acceptance.md)

## 9. 简历表达原则

必须写“基于 DB-GPT 二次开发”，并明确个人完成的模块。任何数字都必须同时给出被测对象、分子/分母、数据集、是否调用 LLM 和环境边界。

禁止表述：

- “部署 DB-GPT，实现数据库智能问答”；
- “LLM/Text2SQL 准确率 100%”；
- “可防御所有 SQL 攻击”；
- “已支持 Hive/MySQL 生产集群”；
- “生产 P95 为 5.9 ms”；
- “支持高并发或亿级数据”。
