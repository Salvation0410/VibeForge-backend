# 已有Vue修改写后交验实施计划

> **For agentic workers:** 使用 executing-plans 按步骤实施。

**Goal:** 已有Vue普通修改成功写入后复用有限读取及预算交验，不改变最终验证。

**Architecture:** 子图仅向正式vue_agent开放资格；工具循环以原始currentArtifact.exists显式true选择既有预算策略。复用私有进度，不扩展写前保护、工作集或快照。

**Tech Stack:** Python、LangGraph、pytest。

用户2026-11-24已确认设计，当前分支codex/python-ai-only继续实施。

- [x] 新建tests/test_vue_existing_edit_yield.py，模型回放6读→2写→14读；断言正式已有修改第14工具结束，首次/legacy仍22工具。运行 `.venv/Scripts/python.exe -m pytest tests/test_vue_existing_edit_yield.py -q`，先确认已有修改断言失败。
- [x] 在subgraphs/vue.py传入 `modify_budget_guard=node == "vue_agent" and not legacy`；vue_tool_loop.py新增默认false参数，并按 `repair_budget_guard or (modify_budget_guard and state["context"].get("currentArtifact", {}).get("exists") is True)` 选择两处预算处理。其他保护不变。
- [x] 测试显式基线、写入资格、读取重置、同批工具、预算预留、取消与checkpoint恢复；主图验证构建/REPAIR/工作集/复验仍执行。运行新文件及原repair_budget_yield、vue_subgraph、repair_workset相关专项。
- [x] 停止确认过命令行的本地Python服务；运行完整pytest、compileall、uv lock --check，记录真实结果。恢复普通配置Python，探测live/ready、Spring、前端和Docker。
- [x] 更新README、AGENTS、设计状态及交接第十二节，明确本轮真实失败与60/60额度；运行git diff --check，中文Conventional Commit提交本轮代码测试文档。

追加授权：用户明确允许不限制次数的真实调用，保留原账本并记录扩额后继续真实验收；不手改生成工程、不修改前端仓库，离线通过不能证明原应用已修复。

执行结果：全量2829通过/29跳过，后增边界随相关专项113通过；真实三轮共56次许可，第三轮9工具触发READ_BATCH_LIMIT并构建通过，后续repair仍PLAN_INVALID。最终细节与下一轮见交接第十二节。
