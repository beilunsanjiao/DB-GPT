# 青圃智析：基于 DB-GPT 的可信问数项目

> **验证状态：固定本地 SQLite、无 LLM 的治理闭环已验证；非 SQLite 方言、外部集群、LLM/Text2SQL、并发和生产性能未验证。**

## 项目定位

青圃智析面向完全合成的温室 DWS/ADS 指标场景，在 DB-GPT ChatDB 基础上增加领域语义目录、AST 只读 SQL 治理、受控执行和可复现评测，使不可信候选 SQL 在触达 connector 前完成授权、表映射和行数收敛。

这不是“部署开源项目”：框架改造位于 `packages/dbgpt-app/src/dbgpt_app/scene/chat_db/`，青圃业务资产、评测与证据位于 `projects/qingpu_chatdb/`。

## 改造前问题与个人实现

| DB-GPT 上游已有能力 | 本项目新增或重构 |
|---|---|
| ChatDB 场景、LLM 调用、connector 抽象、chart-view 协议 | `SemanticCatalog`：15 项指标、别名、口径和表列授权 |
| 通用数据库连接与 `run_to_df` | `ReadOnlySqlGuard`：基于 sqlglot AST 的只读安全链 |
| `BaseChat` 的 parse/action/render 生命周期 | `SafeSqlExecutor` 与 `GuardedQueryResult`：只执行 `PreparedSql.sql` |
| 模型 SQL/JSON 输出解析 | 输出解析器移除触库职责，展示实际执行 SQL |
| 通用测试基础设施 | 固定 SQLite fixture、dataset lint、Gold/Hardened 评测、冻结 expected results |

核心不变量：Prompt 可见语义与执行授权来自同一 catalog；未经 guard 返回的 SQL 不得进入 connector；未知方言、表列、函数和查询形态全部 fail closed。

## 已验证成果

唯一事实源：[`evidence/2026-07-13-final/manifest.json`](evidence/2026-07-13-final/manifest.json)

| 成果 | 当前证据 | 测量边界 | 不代表什么 |
|---|---:|---|---|
| 温室业务指标 | 15 项、2 张逻辑表 | catalog 静态定义 | 生产指标平台全量 |
| 定向测试 | 98/98 | Qingpu tests + ChatDB auto-execute tests | DB-GPT 全仓测试全部通过 |
| Gold | 50/50（47 answer + 3 refusal） | 预置 candidate SQL/action、固定 SQLite、无 LLM | Text2SQL/自然语言准确率 |
| Hardened | 25/25（20 block + 5 allow） | 固定安全规则样本 | 所有 SQL 攻击均可拦截 |
| 冻结 expected results | 47 answer | artifact + lock + fixture/gold SQL hash | 人工业务口径天然正确 |
| 最大返回行数 | 50 | 最外层结果 LIMIT | 数据库扫描量最多 50 行 |

Canonical 报告：

- [Gold](evidence/2026-07-13-final/reports/gold.md)：P50/P95 `3.6865/5.8892 ms`；
- [Hardened](evidence/2026-07-13-final/reports/hardened.md)：P50/P95 `0.3296/4.2371 ms`。

这些是固定 SQLite 案例处理延迟，不包含 LLM、网络、认证、并发或生产数据规模。

## 验证边界

当前没有完成或验证：

- MySQL、PostgreSQL、Hive、Impala 等非 SQLite 安全适配；
- LLM 生成 SQL、自然语言理解和最终回答准确率；
- 外部生产集群与真实生产数据接入；
- 行级权限、多租户隔离、敏感字段脱敏；
- 并发、连接池、网络、亿级数据、吞吐和容量；
- 自动控制处方或未来时序预测。

## 环境与安装

- Python 3.10+，本次证据使用 Python 3.11.15；
- uv；
- Windows 11 已验证。

在仓库根目录执行：

```bash
uv sync --package dbgpt-app --extra base --python 3.11
```

## 一键复现

### 1. 初始化本地数据

```bash
uv run python projects/qingpu_chatdb/scripts/init_local_db.py --reset
```

生成 2026-06-01 至 2026-06-14 的确定性 fixture：DWS 28 行、ADS 28 行。

### 2. 数据集门禁

```bash
uv run python projects/qingpu_chatdb/scripts/dataset_lint.py
```

Lint 检查表列/date-window/result 契约、拒答 reason code、类别配额、15 项指标覆盖、重复项和 SQL 确定性。

### 3. 定向测试

```bash
uv run pytest -q \
  projects/qingpu_chatdb/tests \
  packages/dbgpt-app/src/dbgpt_app/scene/chat_db/auto_execute/tests
```

Canonical snapshot 记录 `98 passed`。

### 4. Gold 与 Hardened

```bash
uv run python projects/qingpu_chatdb/scripts/run_eval.py --mode gold --reset-db
uv run python projects/qingpu_chatdb/scripts/run_eval.py --mode hardened --reset-db
```

新运行只用于复核功能，不会自动替换 canonical evidence snapshot。

## 冻结 expected results

47 条 answer 的结果封存在：

- `eval/gold-answers.v1.json`；
- `eval/gold-answers.v1.lock.json`。

正常评分只读取 artifact，不执行 `gold_sql` 生成 oracle。只有维护者在 fixture、业务口径或 gold SQL 经人工复核后才运行：

```bash
uv run python projects/qingpu_chatdb/scripts/seal_expected_results.py
```

生成后必须审查 artifact/lock diff，再运行 lint、测试和评测。

## 无 LLM 演示

合法查询：

```bash
uv run python projects/qingpu_chatdb/scripts/demo.py
```

拒绝写操作：

```bash
uv run python projects/qingpu_chatdb/scripts/demo.py \
  --sql "DELETE FROM green_test.ads_greenhouse_indicator"
```

输出包含 decision、生成 SQL、实际执行 SQL、引用表列、LIMIT、rewrites 和结果。

## Benchmark

```bash
uv run python projects/qingpu_chatdb/scripts/benchmark.py \
  --iterations 30 \
  --output projects/qingpu_chatdb/eval/benchmark.json
```

Benchmark 仅用于同机回归；详见 [`performance.md`](../../docs/qingpu-chatdb/performance.md)。

## 数据与口径

- `needle`、`broad` 每天各一行；
- ADS 通过复合外键关联 DWS；
- 2026-06-11/needle 的 `avg_spad` 和派生 `pue` 为 `NULL`；
- GSI 为 `sql_authoritative`：设计说明与实际 ADS SQL 的 PAR 公式不一致，以运行 SQL 为准；
- CGP 为 `semantic_uncertain`：受 `total_dli` 源字段语义影响；
- 本地 seed 是完全由代码生成的合成测试数据，不对应生产采样或真实主体。

## 项目结构

```text
projects/qingpu_chatdb/
├── config/{catalog,local}.yaml
├── seed/schema.sql
├── scripts/
│   ├── init_local_db.py
│   ├── dataset_lint.py
│   ├── expected_results.py
│   ├── seal_expected_results.py
│   ├── publish_evidence.py
│   ├── run_eval.py
│   ├── demo.py
│   └── benchmark.py
├── eval/{gold,hardened}.jsonl
├── eval/gold-answers.v1.{json,lock.json}
├── evidence/                    # 可提交的 canonical 验收证据
├── data/                        # 本地 DB 和临时报告，不提交
└── tests/

packages/dbgpt-app/src/dbgpt_app/scene/chat_db/
├── semantic_catalog/
├── safe_sql/
└── auto_execute/
```

## 文档导航

- [架构设计](../../docs/qingpu-chatdb/architecture.md)
- [调用链](../../docs/qingpu-chatdb/call-chain.md)
- [威胁模型](../../docs/qingpu-chatdb/threat-model.md)
- [指标目录](../../docs/qingpu-chatdb/metrics-catalog.md)
- [评测设计](../../docs/qingpu-chatdb/evaluation.md)
- [性能边界](../../docs/qingpu-chatdb/performance.md)
- [招聘版项目简介](../../docs/qingpu-chatdb/recruiting/project-brief.md)
- [面试讲解手册](../../docs/qingpu-chatdb/recruiting/interview-playbook.md)
- [演示与验收](../../docs/qingpu-chatdb/recruiting/demo-acceptance.md)
- [验收证据](evidence/README.md)

## 简历表述边界

可以写：

- 基于 DB-GPT ChatDB 二次开发 15 项温室指标语义目录和逻辑/物理表映射；
- 使用 sqlglot 实现 SQLite 单语句 SELECT 的 AST 表列函数授权、危险结构拒绝和 50 行硬限制；
- 将 connector 执行入口收敛到 `SafeSqlExecutor`，并以 `GuardedQueryResult` 绑定实际执行 SQL；
- 建立 50 条固定业务执行契约、25 条安全规则回归和 47 条冻结 expected results，在固定 SQLite、无 LLM 验收中全部通过。

不得写：

- “LLM/Text2SQL 准确率 100%”；
- “可抵御所有 SQL 攻击”；
- “已支持 MySQL/Hive/生产集群”；
- “生产 P95 为 5.9 ms”或“支持高并发/亿级数据”；
- “具备自动控制或未来预测能力”。
