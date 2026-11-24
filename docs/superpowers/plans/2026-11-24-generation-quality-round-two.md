# 第二轮生成质量实施计划

**目标：** 落实用户确认的第二轮范围，并更新交接和验证证据。

**架构：** Python 保留唯一模型边界，Spring 保留文件所有权；新增生成专用额度、扩展可信事件摘要、有限阶段预留，修复静态相对资源解析。

- [ ] 配置/模型：修改 `config.py`、`openai_compatible.py`、`orchestration/input_review.py`、`.env.example`；测试生成/修复同步与流式请求为 40960，其他调用保持旧额度；验证预算在供应商调用前拒绝超限。
- [ ] 事件载荷：新增独立可信 AST 辅助模块和手写 Node 夹具；覆盖直接/原样转发缺键与兼容对象、动态/展开/默认值/不完整保守跳过；同步 Java schema 和摘要，保留原事件名判断。
- [ ] 阶段预留：修改 `vue_budget.py` 和 `vue_tool_loop.py`；测试第一轮/末轮阈值、成功写入/待图/待文件、完整批次、取消与恢复，沿用原总额度。
- [ ] 静态预览：修改 `StaticResourceController`，新增独立临时文件 HTTP 测试；覆盖根入口、相对 JS/CSS、dist 兼容和未知资源 404，不接触生成目录。
- [ ] 验证：Node 事件/props/锚点、Java 干净编译及相关测试、Python compileall/pytest/lock；真实模型探测继续复用既有 60 次账本，不重置、不重试追求通过。
- [ ] 交付：更新 `ai-service/README.md`、AGENTS 相关当前行为、交接文档；复核 diff 和秘密，中文 Conventional Commit，仅提交任务文件。
