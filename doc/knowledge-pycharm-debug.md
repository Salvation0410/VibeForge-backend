# 知识库索引与 PyCharm 日志

## 在 PyCharm 中启动

1. 用 PyCharm 打开 `ai-service` 目录（作为项目根目录）。
2. Python Interpreter 选择项目中的 `.venv/Scripts/python.exe`，Python 版本为 3.12。
3. 选择仓库提供的 `AI Service` 运行配置。如果未识别 `.run/AI Service.run.xml`，新增 Python 配置：运行目标选择 **Module name**，填 `ai_service.server`；工作目录为 `ai-service`；环境变量为 `PYTHONUNBUFFERED=1`，并将 `src` 标为 Sources Root 或设置 `PYTHONPATH=src`。
4. 点击 Run 或 Debug。不要改成直接运行 Uvicorn，Windows 入口需要 SelectorEventLoop。
5. Run 控制台应出现 `Uvicorn running on http://0.0.0.0:8000`。配置从 `ai-service/.env` 读取，不要把密钥复制到运行配置。

## 排查上传后的状态

- 等待索引：文件已保存并创建任务，尚未能用于新版本的客服回答。
- 索引中：Spring 已调用 Python 解析、切分、生成向量和写入索引。
- 已启用：索引版本已更新，文档可用于检索。
- 处理失败：查看任务详情里的错误码、重试次数及 Spring 日志。

Spring 控制台搜索 `Knowledge task` 可见 queued、started、succeeded、failed 以及任务编号。
PyCharm Run 控制台搜索 `Knowledge INDEX` 可见文档编号、索引开始、完成后的分片数量以及失败类型；Uvicorn 访问日志显示请求是否到达。
未出现 Python 请求日志时，应先查 Spring 调度日志和任务状态，不代表 Python 正在处理。

如果向量生成成功，但写入前租约校验返回 404，核对 Spring 对外路径为
`POST /api/internal/customer-service/knowledge-mutation-leases:validate`（冒号前没有额外斜杠）。
健康接口是 `GET /api/internal/customer-service/knowledge-mutation-leases/health`；健康检查通过不代表校验路径已正确注册。
升级并重启 Spring 后，对已经标记失败的文档点击“重试索引”，不需要重新上传文档或重跑时间修复 SQL。

如果校验接口返回 200，Python 仍报 `KNOWLEDGE_MUTATION_LEASE_INVALID`，还需检查响应契约：
`data.verified`、`data.current` 必须为布尔真；`data.fence`、`data.expiresAt` 必须为 JSON 数字且与请求一致。
内部租约校验的这两个 long 字段使用数字序列化，避免受到前端雪花 ID 字符串序列化规则影响。

旧版本首次任务可能用本地时间写入 nextRetryTime，而工作线程按 UTC 扫描，导致北京时间环境等待约 8 小时。
先重启 Spring 使 UTC 修复生效，再在业务数据库执行 `sql/alter_customer_service_knowledge_pending_time.sql`，预览并恢复这些旧任务。脚本不重置已有重试或失败任务。

## 验收

启动 MySQL、Redis、Milvus、Spring 与 PyCharm 中的 AI 服务后，上传一份新的测试文档。
前端应从等待索引进入索引中，再进入已启用；任务详情显示完成，分片数大于零，索引版本等于文档版本。
使用文档中有明确答案的问题测试客服，并检查来源引用。
停止 PyCharm 服务后上传/重试应提示文档处理服务不可用；恢复服务后检查任务重试及完成日志。
