# 威胁模型

## 1. 范围与信任边界

受保护资产：

- 数据库机密性、完整性和可用性；
- catalog 定义的表列授权边界；
- 实际执行 SQL 与审计展示的一致性；
- 查询结果中的敏感信息；
- ChatDB、connector 和数据库资源。

不可信输入包括用户问题、模型输出、candidate SQL、显示类型和外部配置路径。即使模型来自内部服务，也按可能产生错误或恶意 SQL 的不可信组件处理。

```text
[用户] ──不可信文本──> [LLM] ──不可信 candidate SQL──> |信任边界|
                                                          [SQL Guard]
                                                               │
                                                    仅 PreparedSql.sql
                                                               ▼
                                                         [Connector/DB]
```

## 2. 攻击者与滥用场景

| 攻击者/故障源 | 能力 | 目标 |
|---|---|---|
| 普通用户 | 构造提示词、诱导模型输出任意 SQL | 越权读取、资源耗尽 |
| 恶意模型输出 | 生成 DDL/DML、多语句、混淆标识符或危险函数 | 绕过只读和 catalog 授权 |
| 配置错误 | 指向错误 catalog、接入未支持方言 | 授权漂移、错误执行 |
| 内部调用者 | 绕过场景直接调用 connector | 绕过 guard |
| 依赖缺陷 | 解析器与数据库对 SQL 的解释不一致 | parser differential |

## 3. 威胁—控制—测试—残余风险

| 编号 | 威胁 | 当前控制点 | 固定回归示例 | 残余风险 |
|---|---|---|---|---|
| T1 | DDL/DML/管理语句触库 | 单条 `SELECT` 根节点；connector 只收 `PreparedSql.sql` | H004–H008 | 生产库仍需专用只读账号 |
| T2 | 多语句拼接 | 解析结果必须恰好一条语句 | H003 | 驱动层也应禁用多语句 |
| T3 | 越权表、schema、字段和系统表 | catalog 表列白名单、schema-qualified 逻辑表 | H011–H013 | 尚无行级/租户策略 |
| T4 | `SELECT *` 扩大泄露面 | 拒绝投影星号 | H014 | 显式敏感列仍需分类和脱敏 |
| T5 | 危险或未知函数 | 函数白名单 | H016 | 非 SQLite 方言尚未覆盖 |
| T6 | 解析器差异绕过 | sqlglot 解析、改写后重解析、物理闭包复核 | rewrite/integration tests | 需对目标数据库做 differential tests |
| T7 | 无界结果集 | LIMIT 注入/收紧，硬上限 50 | H021–H023 | 排序/JOIN 可在返回 50 行前消耗资源 |
| T8 | 复杂查询 CPU/IO DoS | 拒绝集合运算、派生表、相关子查询、窗口、OFFSET；限制 JOIN | H009、H017–H020 | 尚无 timeout、progress handler、资源组 |
| T9 | 展示 SQL 与执行 SQL 不一致 | `GuardedQueryResult` 绑定 dataframe 与 `PreparedSql` | output parser tests | 仍需不可篡改服务端审计日志 |
| T10 | 绕过 guard 直接调用 connector | ChatDB `do_action()` 只调用 `SafeSqlExecutor`；parser 不触库 | ChatDB integration tests | 代码库其他入口需单独盘点 |
| T11 | catalog 缺失或方言不支持时宽松降级 | 初始化 fail closed；仅 SQLite | initialization tests | 配置发布签名和变更审批待实现 |
| T12 | Prompt injection 改变授权 | Prompt 只影响候选；授权由确定性 guard 决定 | hardened + integration tests | 仍会影响业务语义和资源消耗 |

## 4. 安全不变量

- 未经 guard 返回的 SQL 不得传给 connector；
- 拒绝路径不得访问数据库；
- 授权对象必须来自加载成功的 catalog；
- 执行 SQL 只能引用 catalog 映射后的 SQLite `main` 物理对象；
- 返回行数不能超过配置硬上限；
- SQL 分支必须返回 `GuardedQueryResult`；
- 未支持方言和不完整元数据不得宽松回退。

## 5. 已验证安全证据

Canonical Hardened 报告：[`hardened.md`](../../projects/qingpu_chatdb/evidence/2026-07-13-final/reports/hardened.md)

| 契约 | 结果 |
|---|---:|
| 固定 block 案例 | 20/20 decision/reason code 匹配 |
| 合法 allow-contract | 5/5 执行、rewrite、LIMIT/row-count 契约匹配 |
| 总体 | 25/25 |
| 案例路径 P50/P95 | 0.3296/4.2371 ms |

原始报告 SHA-256：`c540d6ef7170836127c2c743fbad08d83753edd490f2aee27d6a273b70f684d9`。公开报告和输入文件 SHA 见 [`manifest.json`](../../projects/qingpu_chatdb/evidence/2026-07-13-final/manifest.json)。

解释边界：这是固定样本对确定性规则的回归，不是渗透测试结论，不证明没有其他绕过，也不是 LLM 或“系统安全率”100%。5 个 allow 样本用于证明不是通过拒绝所有 SQL 获得虚假高分。

## 6. 上线前安全门槛

1. 生产数据库使用专用只读账号并限制 schema；
2. 设置 statement timeout、连接超时、并发上限和资源组；
3. 对 SQLite 增加 authorizer/progress handler，其他数据库采用等价控制；
4. 为每个目标方言建立 parser differential 与绕过测试集；
5. 实现用户/租户到行列权限的强制映射；
6. 对结果字段分类、脱敏并限制导出；
7. 审计日志记录 trace ID、用户/租户、decision、reason code、SQL hash、行数和耗时，不落明文敏感值；
8. 对 catalog 配置执行评审、版本化和完整性校验；
9. 对所有可能触达 connector 的代码入口执行独立安全盘点。
