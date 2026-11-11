# 结构化审查与无进展检测实施计划

**目标：** 将有界拒绝依据传给修复，阻止同一候选反复送审，完成离线验证与中文 Git 提交。

**架构：** 模型层解析严格审查结果；验证子图保存候选指纹；沿用修复反馈通道和现有请求终态。技术栈为 Python、Pydantic、LangGraph、pytest。

- [x] 在 models/single_quality_review.py 增加严格模型、输出解析及固定错误；修改 models/base.py、openai_compatible.py、prompts/review.py。验证 PASS/REPAIR 一致性、字段长度、非法 JSON、截断、日志和取消。
- [x] 在 orchestration/repair_progress.py 增加静态/完整 Vue 快照指纹；在 nodes/verification.py 和 subgraphs/schemas.py 接入，只让内部状态保存历史，修复仍接收有界反馈。
- [x] 增加 tests/test_repair_progress.py，使用 Fake Model/Gateway 验证三种类型重复、循环候选、真实内容变化、快照截断、无发布、取消、修复上限及嵌套 checkpoint 字段隔离；更新受协议影响的旧测试。
- [x] 执行 `uv run python -m compileall -q src`、`uv run pytest`、`uv lock --check`，不用真实模型/资源 opt-in；同步 README、AGENTS 与验收交接的新增离线章节。
- [x] 核对两仓库状态、前端客服差异未变、`git diff --check` 和提交信息，只提交本轮后端文件，不推送或部署。

验证结果：全量 1250 passed、10 skipped；补充取消边界后专项 156 项通过；compileall、uv lock --check 与 diff --check 通过。真实调用新增 0，前端客服修改保留。详见验收交接第 8 节。
