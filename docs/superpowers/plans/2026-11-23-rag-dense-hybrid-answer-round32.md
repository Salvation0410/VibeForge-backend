# 第32轮同期回答评测执行计划

> **For agentic workers:** 按用户已选择的直接对照在当前会话顺序执行，不派生代理；复用当前dev工作区，仅提交本轮文件。

**Goal:** 直接测量固定条件下dense/hybrid的回答及拒答差异，开发题和新编未调试题分开。

**Architecture:** 新独立实验模块复用历史检索/选择和正式回答契约，冻结旧90题上下文及新24题查询；唯一账本分阶段预占，随机匿名评审另存映射。生产策略不改。

**Tech Stack:** Python/httpx/numpy、固定CUDA BGE、pytest、JSON冻结与本地匿名审阅。

- [ ] 提交新题/rubric及设计，先核对每条事实的冻结源引文、去重与来源限制。
- [ ] 新增rag_dense_hybrid_answer_experiment.py与测试，复用第27轮检索和第31轮有界回答协议；prepare无请求，检索1+48额度，回答最多228，首错停止无续跑。
- [ ] 新增匿名包/严格评审导入与题级配对统计；验证匿名映射齐全、无法缺失/重复/伪造摘录、真人pending不变。
- [ ] 定向合成测试通过后冻结实际配置/源码/计划摘要，提交；只执行一次新题检索并冻结最终上下文和消息，再执行同期回答。
- [ ] 隐藏臂标签审阅实际完成卡；未执行/协议错误按失败保留，运行前事实标签不调整。审阅后核对摘录，解盲并分开发/新题统计；建立独立真人填写材料。
- [ ] 全量pytest、compileall及uv lock --check，核对旧保护摘要、唯一账本/实际调用、资源清理；README与两交接同步，中文提交不推送，报告实际失败和未测。

预定命令：`uv run --offline pytest tests/test_rag_dense_hybrid_answer_experiment.py`；`uv run --offline python -m ai_service.orchestration.rag_dense_hybrid_answer_experiment prepare`；后续真实阶段必须传冻结摘要，命令由prepare产物生成，不能重跑耗尽账本。完整回归`uv run --offline pytest -ra --tb=short`、`uv run --offline python -m compileall -q src`、`uv lock --check --offline`。不启动下一项生产改动或重用旧真实额度。

