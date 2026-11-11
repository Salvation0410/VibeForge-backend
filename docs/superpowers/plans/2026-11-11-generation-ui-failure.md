# 生成界面失败验收实施计划

目标：验证三类应用失败/停止不刷新旧预览，并修复浏览器可复现的违反项。

- [x] 前端新增 `tests/browser/generationFailure.html` 与 `generationFailure.ts`，挂载真实生成页面，使用 DOM 输入/点击和可控 EventSource 注入。
- [x] 新增 `scripts/test-generation-ui.mjs`，启动无代理的隔离 Vite 与合成 API，运行本机 Chrome headless 并检查结构化报告；缺少 Chrome 明确失败，不静默跳过。
- [x] 浏览器矩阵复现 6 项失败；移除停止直接 IDLE 和 COMMITTING 结束后的自动预览重载，不将执行结束等同成功发布。
- [x] 浏览器 18/18、相关 Node 测试 27/27、type-check、build-only 通过；失败前后报告位于前端忽略目录 doc/。
- [x] README 与交接文档更新，旧账本哈希不变，新账本仍 15/20；前端代码与后端文档分别提交，提交哈希见交付消息，客服修改保留。
