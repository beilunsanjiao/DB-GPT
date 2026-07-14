# 性能报告与声明边界

## 1. 当前结论

Canonical evidence：[`manifest.json`](../../projects/qingpu_chatdb/evidence/2026-07-13-final/manifest.json)

| 案例路径 | 构成 | P50 | P95 | Max |
|---|---|---:|---:|---:|
| [Gold](../../projects/qingpu_chatdb/evidence/2026-07-13-final/reports/gold.md) | 47 answer + 3 refusal | 3.6865 ms | 5.8892 ms | 8.9624 ms |
| [Hardened](../../projects/qingpu_chatdb/evidence/2026-07-13-final/reports/hardened.md) | 20 block + 5 allow | 0.3296 ms | 4.2371 ms | 4.2924 ms |

测量条件是固定本地 SQLite fixture、无 LLM、单进程案例执行。**这些不是生产端到端性能，也不包含模型推理延迟。**

Gold 与 Hardened 的工作负载不同：Gold answer 通常执行 SQLite 并比较冻结结果；Hardened 多数 block 案例在数据库前拒绝。因此不能合并成一个“系统 P95”，也不能用 Hardened 的低中位数推导正常查询性能。

## 2. 测量边界

### Gold `eval_case_elapsed`

包含：

- 读取对应 frozen expected result；
- 校验该案例 gold SQL hash；
- `ReadOnlySqlGuard.prepare()`；
- SQLite 只读执行；
- 表列契约与 DataFrame 结果比较。

不包含：LLM、HTTP、认证、前端渲染和远程数据库网络。

### Hardened `eval_case_elapsed`

- Block 案例主要覆盖解析、形态和授权拒绝路径，不执行 SQLite；
- Allow-contract 案例包含 guard、SQLite 执行、rewrite/LIMIT/row-count 校验。

因此 Hardened 总体延迟不是 allow 查询性能样本。

## 3. 历史微基准

`benchmark.py` 定义 3 个 allow 和 3 个 deny 固定案例，分别测量：

- `guard_prepare`：AST 解析、校验、授权、改写、序列化和物理闭包；
- `guarded_execution`：包含 guard 与本地 SQLite connector 执行。

历史 90 个 allow 样本曾记录：

| 边界 | P50 | P95 |
|---|---:|---:|
| `guard_prepare` | 2.3867 ms | 4.4811 ms |
| `guarded_execution` | 4.2899 ms | 6.6005 ms |

这组数字仅作为历史同机微基准，不是当前 canonical 验收结论，也不能与 Gold/Hardened 的案例路径直接相减或横向排名。

## 4. 明确未测量的内容

当前证据没有覆盖：

- LLM 排队、首 token 和完整生成延迟；
- LLM/Text2SQL/自然语言理解准确率；
- OpenAPI、认证、网络、序列化和浏览器渲染；
- 远程数据库、Hive/Impala、连接池和网络抖动；
- 并发、吞吐、压力、容量、长稳和故障恢复；
- 生产数据规模、复杂执行计划和缓存冷热差异；
- CPU、内存、I/O、连接数和成本。

因此严禁把 `5.8892 ms`、`4.2371 ms` 或 `6.6005 ms` 表述为生产 P95，也不能据此宣称支持高并发或亿级数据。

## 5. 本地微基准复现

```bash
uv run python projects/qingpu_chatdb/scripts/benchmark.py \
  --iterations 30 \
  --output projects/qingpu_chatdb/eval/benchmark.json
```

该命令生成临时同机报告，不会自动成为 canonical evidence。若需要对外引用，必须审核输入、环境和结果，并发布到新的 evidence snapshot。

完整性能披露至少应包含：

- 源码 commit 或工作树归档/输入文件 hash；
- OS、CPU、内存；
- Python、SQLite、sqlglot、pandas 版本；
- 数据库文件 hash、行数和大小；
- warm-up、迭代、案例和样本数；
- 单进程/并发模式和后台负载；
- percentile 算法；
- allow/deny 分组和每案例结果；
- 原始报告 SHA-256。

## 6. 生产性能评测方案

上线前需另建代表性测试：

1. 分段追踪入口、LLM、解析、guard、连接池、数据库和序列化；
2. 按真实查询类型、租户、时间范围和 allow/deny 比例构造负载；
3. 逐级增加并发，报告吞吐、P50/P95/P99、错误率和资源利用率；
4. 区分冷启动、解析器缓存和数据库页缓存；
5. 执行长稳测试，观察连接泄漏、内存增长和尾延迟；
6. 以预定义 SLO 判定容量，不只展示最好一次结果；
7. 保存硬件、软件、数据集、配置和完整原始报告。

## 7. 推荐对外表述

> 在固定本地 SQLite fixture、无 LLM、单进程条件下，canonical 离线验收记录 Gold 案例路径 P50/P95 为 3.6865/5.8892 ms，Hardened 案例路径为 0.3296/4.2371 ms。两者工作负载构成不同，只用于治理链同机回归，不代表生产端到端性能。
