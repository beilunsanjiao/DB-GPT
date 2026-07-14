# 青圃智析验收证据

本目录保存经过审核、可提交的验收快照。临时评测输出位于 `eval/runs/` 或 `data/`，可能被忽略、覆盖或包含本机绝对路径，不作为对外事实源。

当前快照：[`2026-07-13-final/manifest.json`](2026-07-13-final/manifest.json)

## 发布规则

1. 每个快照使用独立目录，发布后不原地覆盖。
2. `manifest.json` 是量化结果、SHA-256、输入资产和声明边界的唯一机器可读事实源。
3. `reports/` 中的 JSON 仅规范化路径字段，案例结果、SQL、reason code 和延迟保持原始报告值。
4. 当前快照在本地分阶段提交后重新生成，并包含推送前尚未提交的脱敏修正；因此暂不绑定 commit，manifest 使用关键输入文件 SHA-256 绑定证据。
5. 后续代码、数据集或口径变化时，应重新运行验证并创建新的 evidence snapshot。

## 当前验收范围

- 固定本地 SQLite fixture；
- 无 LLM 离线评测；
- 98 项 Qingpu/ChatDB 定向测试；
- 50 条固定业务执行契约；
- 25 条固定安全规则契约；
- 47 条冻结 answer expected results。

该证据不代表 LLM/Text2SQL 准确率、未知攻击覆盖率、生产端到端性能、并发能力或非 SQLite 方言支持。
