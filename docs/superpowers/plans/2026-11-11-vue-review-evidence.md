# Vue 审查证据实施计划

采用本会话顺序执行，不委派代理。目标是在已有授权内修正审查输入并取得有限对照证据。

- [x] 新增适配器回归：构造含过时源码、工具历史和规划反馈的 VUE_PROJECT 上下文，检查发送内容排除这些字段、保留原文/基线/意图、无原地修改；构造超限快照检查零调用。旧实现 1 项失败、1 项通过。
- [x] 在 `models/vue_review_context.py` 实现字段投影，在 `openai_compatible.py` 的单审查入口调用；`nodes/verification.py` 显式传入生成类型；`prompts/review.py` 单/多角色共用组件核对约束。调整原工具历史测试仅改变 review 分支预期。
- [x] 增加真实嵌套图的类型传递回归，运行审查/修复/子图测试及 compileall、全量 pytest、uv lock --check。最终 1289 passed、14 skipped。
- [x] 预检代理端口、配置及原账本，私有设施只启动计数代理；对已有成功/失败样本旧/新审查各一次。原成功样本因多余图片不适合作纯净正例，保留 mismatch，额外两个手写合法正例均 PASS；总 6 调用，无重试。
- [x] 更新 README、AGENTS、测试集及交接第 17 节，停止本轮代理，核对旧账本和两仓库，纳入中文 Conventional Commits 提交。保留用户客服修改与原未跟踪计划。
