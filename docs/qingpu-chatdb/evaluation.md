# 评测设计与结果解释

## 1. 评测回答什么

当前离线评测只回答三个有限问题：

1. 给定固定 candidate SQL/action，治理链是否作出预期 allow/block 决策？
2. Allow 后在固定 SQLite fixture 上执行，表列引用和结果是否符合契约？
3. 固定危险输入和合法边界是否得到预期 reason code、rewrite、LIMIT 和行数？

当前评测**不调用 LLM**，因此不回答用户问题是否被模型正确理解、SQL 是否由模型正确生成、最终自然语言答案是否正确，也不能比较模型或 Prompt 的效果。

## 2. 固定资产

| 资产 | 数量 | 用途 |
|---|---:|---|
| [`gold.jsonl`](../../projects/qingpu_chatdb/eval/gold.jsonl) | 50 | 47 个 answer + 3 个 refusal 的固定业务执行契约 |
| [`gold-answers.v1.json`](../../projects/qingpu_chatdb/eval/gold-answers.v1.json) + [lock](../../projects/qingpu_chatdb/eval/gold-answers.v1.lock.json) | 47 | 冻结期望 rows/columns、gold SQL hash 和 fixture hash |
| [`hardened.jsonl`](../../projects/qingpu_chatdb/eval/hardened.jsonl) | 25 | 20 个预期 block + 5 个合法 allow-contract |
| 固定 SQLite schema/seed | DWS 28 + ADS 28 行 | 确定、可重建的执行结果 |
| catalog | 15 项指标、2 张逻辑表 | Prompt 语义与执行授权的共同事实源 |

正常评分不执行 `gold_sql` 生成 oracle。Gold 模式启动时校验 artifact SHA、catalog/schema/seed generator hash 和 answer case ID 完整覆盖；每个 answer 在执行 candidate SQL 前校验对应 gold SQL hash。

## 3. 判分契约

### Gold answer

通过必须同时满足：

- prediction action 为 `answer`；
- guard 允许 candidate SQL；
- 实际引用表与 required tables 精确匹配；
- 实际引用列与 required columns 精确匹配；
- SQLite 执行结果与 frozen expected result 一致；
- 返回行数不超过硬上限。

结果比较支持有序/无序多重集、标量、schema-only、空值和数值容差。

### Gold refusal

必须显式提供 refusal prediction 和 reason code。candidate 缺失时不得回退到 gold SQL，空结果也不能冒充拒答成功。

### Hardened

- 20 个 block 案例：decision 与 expected code 必须匹配；
- 5 个 allow-contract：必须成功执行，并满足 rewrite、LIMIT 和可选 row-count 契约；
- 只统计固定样本，不外推未知攻击覆盖率。

## 4. Canonical 验收结果

事实源：[`manifest.json`](../../projects/qingpu_chatdb/evidence/2026-07-13-final/manifest.json)

| 套件 | 构成 | 结果 | P50 | P95 | Max |
|---|---|---:|---:|---:|---:|
| [Gold](../../projects/qingpu_chatdb/evidence/2026-07-13-final/reports/gold.md) | 47 answer + 3 refusal | 50/50 | 3.6865 ms | 5.8892 ms | 8.9624 ms |
| [Hardened](../../projects/qingpu_chatdb/evidence/2026-07-13-final/reports/hardened.md) | 20 block + 5 allow | 25/25 | 0.3296 ms | 4.2371 ms | 4.2924 ms |

完整性摘要：

- frozen expected-results SHA-256：`16e5d78812de749d96bb428c58f02b11a63bd776439bd8fb9e27a00f94550d3c`；
- 原始 Gold/Hardened 报告 SHA-256 与规范化公开报告 SHA-256 见 manifest；
- 公开报告只规范化本机路径字段，案例结果和延迟不变。

当前 snapshot 在本地分阶段提交后重新生成，并包含推送前尚未提交的脱敏修正，因此暂不绑定 commit；manifest 使用关键输入文件 SHA-256 绑定证据。

## 5. 正确与错误解释

### 可以说

- “在固定 SQLite、无 LLM 验收中，47/47 个 answer 的表、列和冻结结果契约通过，3/3 个 refusal prediction 匹配。”
- “20/20 个固定 block 案例得到预期 decision/reason code，5/5 个合法边界案例满足 allow 契约。”
- “报告中的毫秒值是该次单进程本地案例路径延迟。”

### 不可以说

- “LLM/Text2SQL 准确率为 100%。”
- “模型正确回答了 50 个自然语言问题。”
- “25 个攻击全部被拦截”或“系统可抵御所有 SQL 攻击”。
- “生产 P95 为 5.8892 ms。”
- “已支持高并发、亿级数据或 Hive/Impala 生产性能。”

根本原因是 candidate SQL/action 已预置，模型生成阶段完全不在评测闭包内；固定规则回归也不能代表未知攻击或生产工作负载。

## 6. 复现顺序

### 数据集门禁

```bash
uv run python projects/qingpu_chatdb/scripts/dataset_lint.py
```

### 定向测试

```bash
uv run pytest -q \
  projects/qingpu_chatdb/tests \
  packages/dbgpt-app/src/dbgpt_app/scene/chat_db/auto_execute/tests
```

### 功能复核

```bash
uv run python projects/qingpu_chatdb/scripts/run_eval.py --mode gold --reset-db
uv run python projects/qingpu_chatdb/scripts/run_eval.py --mode hardened --reset-db
```

新运行的延迟会波动，只用于确认 50/50 和 25/25；经审核并发布为新 snapshot 后才能替代 canonical evidence。

### 维护者重新封存 oracle

```bash
uv run python projects/qingpu_chatdb/scripts/seal_expected_results.py
```

该命令会执行 gold SQL，只用于 fixture、口径或 gold SQL 经人工复核后的维护流程。生成后必须审查 artifact/lock diff，再执行 lint、测试和评测。

## 7. 补充 LLM 准确率所需工作

若未来需要声明 Text2SQL/LLM 质量，必须新增独立端到端模式：

1. 输入只提供自然语言问题和允许上下文，不预置 candidate；
2. 固定模型版本、Prompt、温度、采样和重试策略；
3. 保存原始模型输出、解析结果、执行 SQL 和最终答案；
4. 分开报告 execution accuracy、result equivalence、拒答 precision/recall、无效 SQL 率、成本和延迟；
5. 使用训练隔离的盲测集，并对随机运行报告多次试验和置信区间；
6. 人工复核语义等价 SQL 和不可唯一回答的问题。
