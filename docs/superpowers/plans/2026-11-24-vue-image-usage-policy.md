# Vue 图片使用策略修复执行计划

> 按用户已确认方案在当前会话顺序实施，完成回归和文档后提交。

**目标：** 默认自动搜图不再阻断不需要图片的 Vue 应用；明确配图请求保持原检查。

**结构：** `nodes/input.py` 仅为 Vue 上下文增加 `imageUsagePolicy`（`optional` / `required`）。`vue_tool_loop.py` 在阶段初始化时只豁免显式 `optional`，缺字段保留旧行为。生成、单/多审查和修复共享策略。

**技术：** Python、LangGraph、pytest；Spring 工具契约及公共事件不变。

- [x] 在 `tests/test_vue_image_policy.py` 添加真实触发需求的离线流程回归，覆盖默认有图不写图、明确要求仍失败、修复继承、旧上下文兼容和静态类型不受影响；先执行并确认新行为断言失败。
- [x] 在 `orchestration/nodes/input.py` 用 `requests_image_assets(state["prompt"])` 决定新字段，Vue 请求显式配图为 `required`、其他为 `optional`。保留现有搜图条件、无结果降级与否定规则。
- [x] 在 `orchestration/vue_tool_loop.py` 将初始化改为 `context.get("imageUsagePolicy") == "optional" or bool(context.get("imageAssetsApplied")) or not bool(context.get("imageAssets"))`；后续提醒、成功写入检查、共享预算保持不变。
- [x] 更新 `prompts/generation.py` 和 `prompts/review.py`：显式 optional 素材可不采用，required 及缺字段保持原检查语义，不能把可选素材当用户新增需求。
- [x] 在 `models/vue_review_context.py` 保留策略字段，在 `nodes/verification.py` 多审查分支仅对存在的新字段透传；为实际供应商消息补策略投影测试。
- [x] 将 `tests/test_api.py` 原强制用图两项测试改成明确配图请求，保留两次提醒、成功写入和缺图失败断言。
- [x] 执行 `uv run pytest tests/test_vue_image_policy.py tests/test_vue_review_evidence.py tests/test_api.py -q`，通过后执行完整 `uv run pytest`、`uv run python -m compileall -q src`、`uv lock --check`。
- [x] 更新 `ai-service/README.md`、已确认设计与 `doc/vue-image-usage-policy-handoff.md`，记录证据、行为、测试及未做在线重启/真实生成的边界。
- [x] 执行 `git diff --check`，审阅本轮差异，仅提交本轮文件，提交说明使用中文 Conventional Commit，并报告哈希。
