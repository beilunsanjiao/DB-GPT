# 演示与验收手册

## 1. 演示前准备

在仓库根目录执行，使用 Python 3.11 和已同步的 uv 环境。演示使用固定 SQLite fixture，不调用 LLM，也不连接外部青圃集群。

```bash
uv sync --package dbgpt-app --extra base --python 3.11
```

## 2. 8–10 分钟演示流程

### 步骤 1：展示领域语义目录（1 分钟）

打开 `projects/qingpu_chatdb/config/catalog.yaml`。

讲解重点：

- 2 张逻辑表和本地物理表映射；
- 15 项指标及中英文别名、单位、方向和状态；
- GSI `sql_authoritative`、CGP `semantic_uncertain`；
- 同一 catalog 同时服务 Prompt 和执行授权。

### 步骤 2：初始化确定性 fixture（30 秒）

```bash
uv run python projects/qingpu_chatdb/scripts/init_local_db.py --reset
```

预期输出包含：

```text
28 DWS rows and 28 ADS rows
```

讲解重点：14 天 × 2 类温室；固定数据便于人工核算和自动回归。本地 seed 不代表生产数据。

### 步骤 3：运行 dataset lint（30 秒）

```bash
uv run python projects/qingpu_chatdb/scripts/dataset_lint.py
```

预期：

```text
dataset lint passed: 50 cases, 15 metrics
```

讲解重点：评测执行器只负责判分，lint 独立检查来源契约、类别配额、指标覆盖、重复项和 SQL 确定性。

### 步骤 4：演示合法查询与 SQL 改写（1 分钟）

```bash
uv run python projects/qingpu_chatdb/scripts/demo.py
```

观察：

- generated/original SQL；
- 逻辑表改写为 `main.<physical_table>`；
- 缺失 LIMIT 时注入 `LIMIT 50`；
- referenced tables/columns 和 rewrites；
- 实际结果数据。

讲解重点：展示 SQL 是 `PreparedSql.sql`，不是模型原始文本；output parser 不会二次执行。

### 步骤 5：演示 LIMIT 收紧（45 秒）

```bash
uv run python projects/qingpu_chatdb/scripts/demo.py \
  --sql "SELECT dt FROM green_test.ads_greenhouse_indicator LIMIT 500"
```

预期：最终 LIMIT 为 50，并记录 `limit_reduced`。

讲解重点：只限制返回行数，不声称限制数据库扫描量。

### 步骤 6：演示写操作拒绝（45 秒）

```bash
uv run python projects/qingpu_chatdb/scripts/demo.py \
  --sql "DELETE FROM green_test.ads_greenhouse_indicator"
```

预期：decision 为 block，reason code 为 `NON_QUERY_STATEMENT`，数据库不被访问。

### 步骤 7：演示越权或星号拒绝（45 秒）

```bash
uv run python projects/qingpu_chatdb/scripts/demo.py \
  --sql "SELECT password FROM green_test.ads_greenhouse_indicator"
```

或：

```bash
uv run python projects/qingpu_chatdb/scripts/demo.py \
  --sql "SELECT * FROM green_test.ads_greenhouse_indicator"
```

预期：分别得到 `UNKNOWN_COLUMN` 或 `STAR_NOT_ALLOWED`。

### 步骤 8：运行定向测试（1 分钟）

```bash
uv run pytest -q \
  projects/qingpu_chatdb/tests \
  packages/dbgpt-app/src/dbgpt_app/scene/chat_db/auto_execute/tests
```

Canonical 验收值：`98 passed`。

### 步骤 9：运行 Gold 与 Hardened（1–2 分钟）

```bash
uv run python projects/qingpu_chatdb/scripts/run_eval.py --mode gold --reset-db
uv run python projects/qingpu_chatdb/scripts/run_eval.py --mode hardened --reset-db
```

预期功能结果：

```text
Gold: 50/50
Hardened: 25/25
```

讲解重点：

- Gold = 47 answer + 3 refusal；
- Hardened = 20 block + 5 allow；
- 新运行时延会波动，不自动覆盖 canonical snapshot；
- 这些不是 Text2SQL 准确率。

### 步骤 10：展示 evidence manifest（1 分钟）

打开：

- `projects/qingpu_chatdb/evidence/2026-07-13-final/manifest.json`；
- `projects/qingpu_chatdb/evidence/2026-07-13-final/reports/gold.md`；
- `projects/qingpu_chatdb/evidence/2026-07-13-final/reports/hardened.md`。

讲解重点：统一记录结果、输入 SHA、报告 SHA、验证命令和禁止外推的边界；当前 snapshot 绑定工作树文件 hash，不错误引用旧 HEAD。

## 3. 失败排查

| 现象 | 排查 |
|---|---|
| `uv` 找不到 | 使用已安装的 uv 完整路径或重新安装 uv |
| Python 版本不兼容 | `uv python install 3.11`，再执行 sync |
| catalog 加载失败 | 检查 `config/catalog.yaml` 路径和 YAML 结构 |
| expected artifact drift | 检查 lock、catalog/schema/seed hash；不要直接绕过 |
| Gold coverage mismatch | 核对 47 个 answer 与 artifact case IDs |
| 测试数不是 98 | 确认命令包含两个指定测试目录，检查当前工作树变化 |
| 报告结果通过但延迟不同 | 正常计时波动；只有审核后的新 snapshot 才替换 canonical 数据 |

## 4. 验收清单

### 功能与数据

- [ ] SQLite fixture 可从空文件重建；
- [ ] DWS 28 行、ADS 28 行；
- [ ] catalog 包含 15 项指标和 2 张逻辑表；
- [ ] 合法 SQL 经过逻辑/物理表映射；
- [ ] 缺失/过大 LIMIT 被注入或收紧；
- [ ] DDL/DML、未知表列、星号和危险函数在 connector 前拒绝；
- [ ] output parser 不执行 SQL。

### 评测与测试

- [ ] Dataset lint：50 cases、15 metrics；
- [ ] 定向 pytest：98 passed；
- [ ] Gold：50/50，其中 47 answer + 3 refusal；
- [ ] Hardened：25/25，其中 20 block + 5 allow；
- [ ] frozen expected results：47 answer；
- [ ] artifact SHA 与 lock 一致；
- [ ] fixture 和 gold SQL hash 校验通过。

### 文档和证据

- [ ] Evidence manifest JSON 可解析；
- [ ] manifest 内所有 artifact/input SHA 匹配；
- [ ] canonical evidence 文件未被 Git ignore；
- [ ] Markdown 相对链接全部存在；
- [ ] 正式文档不链接 `data/` 或 `eval/runs/` 临时输出；
- [ ] 公开报告不包含本机用户绝对路径；
- [ ] 当前文档不再把旧报告 ID/延迟称为最新结果。

### 声明边界

- [ ] 没有把 50/50 写成 LLM/Text2SQL 准确率；
- [ ] 没有把 25/25 写成“全部攻击被拦截”；
- [ ] 没有把本地毫秒值写成生产 P95；
- [ ] 没有声称已支持 Hive/MySQL 或真实生产集群；
- [ ] 没有声称支持高并发、亿级数据、自动控制或未来预测；
- [ ] 明确使用“基于 DB-GPT 二次开发”。

## 5. 演示收尾话术

> 这个项目当前证明的是：我能在现有开源 ChatDB 框架中识别不可信 SQL 的执行风险，设计语义与授权同源的 AST 治理链，并用正负样本、冻结 oracle 和证据包做可复现验收。它还没有证明模型 Text2SQL 质量或生产性能；下一步会在提交后的版本上补端到端 LLM 盲测、目标数据库方言和生产资源治理。
