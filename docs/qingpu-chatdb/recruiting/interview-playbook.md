# 面试讲解手册

## 1. 30 秒项目定位

> 我基于 DB-GPT ChatDB 二次开发了一个温室指标可信问数项目。原链路可以生成和执行 SQL，但模型输出不能作为安全边界。我增加了 15 项指标的 SemanticCatalog、基于 sqlglot 的 AST 只读治理、SafeSqlExecutor 和实际执行 SQL 追溯，并用确定性 SQLite fixture、50 条业务契约、25 条安全规则契约和冻结 expected results 做离线验收。当前结果是 98 项定向测试通过、Gold 50/50、Hardened 25/25，但这些都不包含 LLM，因此我不会把它写成 Text2SQL 准确率。

## 2. 3 分钟 STAR 讲解

### Situation

青圃数仓已经有 DWS/ADS 指标，但查询依赖固定报表。DB-GPT 能生成 SQL，不过 Prompt 只能改善输出，不能阻止模型产生写操作、越权表列、危险函数或无界查询。原输出层如果直接触达 connector，还会让安全边界分散。

### Task

在不重写 DB-GPT `BaseChat` 生命周期的前提下，建立一个可在 Windows 本地复现、失败关闭、结果可审计的可信问数闭环，并让所有简历数字都有证据和明确边界。

### Action

1. 建立 `SemanticCatalog`，将 15 项温室指标、别名、单位、方向、口径和表列映射集中管理，同时服务 Prompt 与授权。
2. 使用 sqlglot 实现 `ReadOnlySqlGuard`，只允许 SQLite 单条 `SELECT`，执行表列函数白名单、查询形态拒绝、物理表改写和 LIMIT≤50。
3. 使用 `SafeSqlExecutor` 收敛 connector 入口，返回 `GuardedQueryResult`，让展示 SQL 与实际执行 SQL 一致。
4. 构造 14 天 × 2 类温室的确定性 fixture，建立 dataset lint、50 条 Gold 和 25 条 Hardened。
5. 将 47 条 answer 结果离线封存，正常评分不执行 gold SQL，避免同源循环验证。

### Result

固定 SQLite、无 LLM 验收中：98 项定向测试通过；Gold 50/50，其中 47 answer 和 3 refusal 全部满足契约；Hardened 25/25，其中 20 block 和 5 allow 全部符合预期。所有报告和输入 SHA 收敛到 canonical evidence manifest。

## 3. 8–10 分钟技术深讲提纲

1. **业务背景**：青圃 DWS/ADS 的数据和指标是什么，为什么需要自然语言问数。
2. **改造前风险**：Prompt 不是安全边界；通用 connector 需要兼容写场景；显示层不能承担执行职责。
3. **架构边界**：不改 `BaseChat`，在 `ChatWithDbAutoExecute.do_action()` 接入安全执行器。
4. **语义目录**：同一 catalog 同时产生 Prompt 上下文和执行白名单，避免语义/授权漂移。
5. **AST 治理**：解析、形态校验、scope 绑定、表列函数授权、改写、LIMIT、物理闭包。
6. **执行对象**：candidate SQL、prepared SQL、referenced tables/columns 和 rewrites 如何绑定。
7. **评测设计**：Gold 验证治理/执行/结果；Hardened 同时测试 block 和 allow；dataset lint 检查数据集质量。
8. **冻结 oracle**：为什么 candidate 与 gold SQL 不能在评分时同源执行，artifact/lock 如何 fail closed。
9. **证据边界**：为什么 50/50 不是 Text2SQL，为什么本地 P95 不是生产 P95。
10. **生产化路线**：目标方言、只读账号、timeout、资源组、行列权限、脱敏和端到端 LLM 盲测。

## 4. 高频追问

### 为什么不用正则或字符串前缀判断？

- **已实现**：使用 sqlglot AST 和 scope 分析，检查根节点、底层表、列归属、函数、JOIN 和查询形态。
- **正则问题**：处理不了注释、CTE、别名、嵌套表达式、方言差异和多语句，容易误拒或绕过。
- **生产化**：每个目标方言仍需做 parser differential tests，不能假设一个 AST 规则适用于所有数据库。

### 有 AST guard，为什么还需要数据库只读账号？

- **已实现**：应用层在 connector 前 fail closed。
- **未实现**：无法证明解析器、驱动、其他代码入口永远无缺陷。
- **生产化**：只读账号、schema 权限、statement timeout 和资源组是独立防线；应用 guard 不能替代数据库权限。

### LIMIT 能否防止资源耗尽？

- **已实现**：限制最外层返回行数和序列化/泄露面。
- **不能解决**：数据库仍可能扫描、JOIN、GROUP BY、ORDER BY 大量数据后再返回 50 行。
- **生产化**：需要 timeout、成本估计、扫描量限制、资源组和查询队列。

### 为什么禁止 `SELECT *`？

它扩大敏感字段泄露面，让列授权和审计不明确，也会增加结果大小。当前要求显式列出字段；生产还需字段分类和脱敏。

### 如何处理 CTE、子查询和 UNION？

首版采取安全优先的收敛策略：允许经过 scope 验证的安全 CTE，拒绝集合运算、派生表、相关子查询、窗口等复杂形态。不是 SQL 能力越多越好，而是每种形态都要有明确授权和测试闭包。

### 为什么只支持 SQLite？

- **已实现**：SQLite 标识符、物理命名空间、函数白名单和执行闭包。
- **未实现**：Hive/MySQL 等在 catalog、函数、schema、LIMIT 和 parser 行为上不同。
- **扩展方式**：为每个方言新增显式 adapter/policy，并建立 allow/block/differential tests；未知方言继续 fail closed。

### 如何做行列权限和多租户？

当前 catalog 是静态领域白名单，不是用户级 RBAC。生产化应在请求上下文中生成租户/角色授权快照，guard 使用该快照绑定表列，并在数据库账号、视图或 RLS 层再次强制执行。

### 为什么 expected results 必须冻结？

如果 candidate SQL 和 gold SQL 本质相同，评分时两者都在同一 fixture 上执行，只能证明“同一逻辑得到同一结果”，无法发现共同错误。当前做法是维护者离线封存 rows/columns 和 gold SQL hash；正常评分只读 artifact，并对 fixture 和 case coverage 做完整性校验。

### 50/50 为什么不是 Text2SQL 准确率？

因为 candidate SQL/action 已预置，模型生成阶段不在闭包内。50/50 表示固定 candidate 经过治理后满足表列和结果契约。要测 Text2SQL，必须只输入自然语言、固定模型和 Prompt，并报告 execution accuracy、result equivalence、拒答 precision/recall 等。

### 当前最大的技术债是什么？

1. 当前只支持 SQLite；
2. 没有端到端 LLM 盲测；
3. 没有生产级行列权限、timeout 和资源治理；
4. 没有真实集群、并发和大规模数据验证；
5. 已形成分阶段本地 Git 历史；当前 evidence 仍以输入 SHA 绑定，待脱敏提交完成后再生成 commit-bound snapshot。

## 5. 回答结构模板

面对任何追问，按三层回答：

1. **当前已经实现什么**；
2. **当前明确没有实现什么**；
3. **如果生产化，下一步会如何做**。

这种回答方式比只讲“用了 sqlglot、DB-GPT、SQLite”更能体现工程边界和方案完整性。
