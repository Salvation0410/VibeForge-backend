# 结构化审查有限验收实施计划

目标：在 20 次新授权内取得真实协议与范围证据，补齐失败提示和跨层验证。

本会话按步骤直接执行；已有继续优化授权。设计见同日 `quality-review-real-acceptance-design.md`。

- [x] 从三个隔离应用记录当前 DOM、样式、图片和点击基线；通过持久化限额代理依次提交结构化编辑，每轮核对消耗和真实审查结果。
- [x] 在 `LangGraphAiGenerationGatewayTest`、`AppControllerSseTest` 对 `QUALITY_REVIEW_INVALID_OUTPUT`、`QUALITY_REVIEW_INCOMPLETE`、`REPAIR_NO_PROGRESS` 参数化断言码与 requestId；控制器只发一次 business-error。
- [x] 将 `AppChatView.vue` 既有文案映射提取至 `src/utils/generationErrorMessage.ts`，新增上述三种中文提示；测试固定码优先、未知码及缺失消息回退。运行既有预览失败/迟到回调测试。
- [x] Java 执行 `mvn test "-Dtest=LangGraphAiGenerationGatewayTest,AppControllerSseTest"` 和 `mvn clean -DskipTests compile`。前端执行 Node 测试、`npm run type-check`、`npm run build-only`；Python 未修改运行逻辑，回归 222 项审查/修复及 API 测试。
- [x] 核对真实结果及保持项，更新交接文档；原账本哈希未变，本轮账本 15/20，服务停止。前端已提交 `3cb74c1`，后端随本计划及交接文档提交，最终哈希在交付消息报告。

实际应用四项修改均通过；另用 1 次真实审查与 Fake 生成/修复/工具的受控缺陷探针验证 REPAIR 反馈传递与重复停止。未执行真实 UI 失败矩阵，不将探针称为端到端验收。详细证据与后续见交接文档第 9 节。
