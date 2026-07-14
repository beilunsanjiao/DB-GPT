# 调用链

## 1. SQL 分支时序

```text
Client
  │  user input
  ▼
OpenAPI / ChatFactory
  │  scene=chat_with_db_execute
  ▼
ChatWithDbAutoExecute.__init__
  ├─ 解析 typed config
  ├─ 获取 connector
  ├─ 校验 dialect == sqlite
  ├─ 加载 SemanticCatalog；失败则终止
  └─ 构造 ReadOnlySqlGuard + SafeSqlExecutor
  │
  ▼
generate_input_values
  ├─ catalog.render_prompt_context()
  └─ user_input / dialect / top_k / display_type
  │
  ▼
LLM
  │  JSON 或纯 SQL 文本
  ▼
DbChatOutputParser.parse_prompt_response
  │  SqlAction(sql, direct_response, display, thoughts)
  ▼
BaseChat._handle_final_output
  │
  ▼
ChatWithDbAutoExecute.do_action
  │  sql 非空
  ▼
SafeSqlExecutor.execute(original_sql)
  │
  ├─ ReadOnlySqlGuard.prepare
  │    1. 文本与方言预检
  │    2. sqlglot 解析且只允许一条 SELECT
  │    3. 查询形态校验
  │    4. 绑定并授权逻辑表、列、函数
  │    5. 逻辑表改写为 main.<physical_table>
  │    6. 注入或收紧最外层 LIMIT
  │    7. 重新解析并验证物理闭包
  │    8. 返回 PreparedSql
  │
  ├─ connector.run_to_df(PreparedSql.sql)
  └─ 返回 GuardedQueryResult(dataframe, prepared)
  │
  ▼
DbChatOutputParser.parse_view_response
  ├─ 强制 data instanceof GuardedQueryResult
  ├─ param.sql = prepared.sql
  ├─ param.generated_sql = prepared.original_sql
  └─ 序列化 dataframe，不执行 SQL
```

## 2. direct response 分支

当 `SqlAction.sql` 为空且 `direct_response` 非空时：

1. `do_action()` 返回 `None`；
2. 不调用 `ReadOnlySqlGuard`；
3. 不调用 connector；
4. `parse_view_response()` 直接展示文本。

因此“没有 SQL”与“SQL 执行失败”是不同状态，前者不会静默回退到旧执行路径。

## 3. 失败传播

| 阶段 | 典型失败 | 结果 |
|---|---|---|
| 初始化 | catalog 路径缺失、文件无效、非 SQLite 方言 | 场景创建失败，数据库不被访问 |
| 模型输出解析 | 既没有有效 SQL，也没有 direct response | 视图生成失败 |
| AST 解析 | 语法错误、多语句、非查询语句 | `SqlGuardError`，connector 不被调用 |
| 授权 | 未知表列、危险函数、歧义列 | `SqlGuardError`，connector 不被调用 |
| 查询形态 | 星号、集合运算、危险 JOIN、窗口等 | `SqlGuardError`，connector 不被调用 |
| connector | SQLite 执行错误 | 请求失败，不伪造受控结果 |
| 展示 | SQL 分支收到裸 `DataFrame`、callable 或 `None` | 类型错误，不回退执行 SQL |

## 4. 数据对象契约

### `SqlAction`

模型输出的解析结果。`sql` 仍是不可信候选文本；解析成功不代表已获执行授权。

### `PreparedSql`

包含：

- `original_sql`：模型生成或评测注入的候选 SQL；
- `sql`：经授权与改写后唯一允许执行的 SQL；
- `referenced_tables` / `referenced_columns`：已解析引用；
- `limit`：最终结果行上限；
- `rewrites`：表映射和 LIMIT 等确定性改写记录。

### `GuardedQueryResult`

将 connector 返回的 `dataframe` 与对应 `PreparedSql` 绑定，避免展示层丢失“实际执行了什么”的证据。

## 5. Gold 冻结结果评测链

```text
run_evaluation(mode=gold)
  ├─ load gold.jsonl
  ├─ load_expected_results
  │    ├─ 校验 lock 与 artifact SHA-256
  │    ├─ 校验 catalog/schema/seed generator hash
  │    └─ 暴露 case ID → frozen rows/columns
  ├─ 校验 answer case ID 与 artifact 完整覆盖
  └─ 对每个 answer
       ├─ 校验该 case 的 gold SQL hash
       ├─ candidate_sql → SafeSqlExecutor.execute
       └─ guarded dataframe ↔ frozen dataframe 比较
```

正常评分不会执行 `gold_sql` 生成 expected result。`gold_sql` 只在维护者明确运行 `seal_expected_results.py` 时用于重新封存，避免 candidate 与 oracle 在评分时形成同源循环。

## 6. 可观测点设计

生产化时建议为下列阶段分别记录耗时，而不是只保留一个端到端数字：

- `llm_request_ms`
- `output_parse_ms`
- `sql_guard_prepare_ms`
- `connector_wait_ms`
- `database_execute_ms`
- `result_serialize_ms`
- `request_total_ms`

同时记录 `decision=allow|deny`、`reason_code`、`dialect`、`referenced_table_count`、`rewrite_count`、`row_count`，但不得记录敏感查询结果或未经脱敏的用户输入。

当前 canonical 报告只覆盖无 LLM 的固定 SQLite 案例路径，见 [`performance.md`](performance.md) 和 [`manifest.json`](../../projects/qingpu_chatdb/evidence/2026-07-13-final/manifest.json)。其中毫秒值**不能**代替上述端到端分段指标，也**不是 LLM 准确率**。
