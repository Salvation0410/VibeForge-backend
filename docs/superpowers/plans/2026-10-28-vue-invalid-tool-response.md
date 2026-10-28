# Vue 工具响应格式修复实施计划

**目标：** 修复说明文字混入工具 JSON 后被静默降级、最终误报文件缺失的问题。

**已确认设计：** 保留严格 JSON 解析，不从自由文本提取工具。解析失败通过 ModelTurn 标记交给 Vue 循环，生成和修复阶段各最多重试两次；第三次失败返回 VUE_MODEL_INVALID_OUTPUT。无效轮次不展示、不执行工具、不改变文件计划或成功文件记录。结束原因校验优先，截断与过滤维持原有行为。文件完整性、工具预算和公共 SSE 保持兼容。

**证据：** 请求 75fed0c4-e5ec-4f2a-9c3e-a6f94baa98c8 的检查点记录计划 19 个文件、成功写入 9 个；最后响应为说明文字加工具 JSON，解析器回退为普通文本。

## 执行步骤

- [x] 在 test_openai_compatible.py 验证混合文本和错误结构标记为无效，合法 JSON 兼容。
- [x] 在 test_api.py 通过真实响应解析器复现无效轮次、纠正后继续写文件和持续无效最多三次调用；确认无无效写入、无源码展示和无提前构建。
- [x] 先运行新增测试确认失败，再添加 ModelTurn.vue_output_invalid 与 VueLoopProgress.format_retries，实现固定脱敏反馈与稳定错误码。
- [x] 更新 ai-service/README.md，执行 compileall、完整 pytest、uv lock --check、git diff --check。
- [x] 只提交本任务文件，使用中文 Conventional Commits 信息，报告验证结果和提交哈希。

PostgreSQL 回滚警告单独调查，本次不以调整 checkpoint 或放宽文件完整性检查规避生成协议错误。

## 验证记录

- 新增初始 9 项回归测试在修复前全部失败；修复后 Vue 相关测试通过，另验证修复入口保留截断元数据。
- 真实 PostgreSQL 专项 10 项通过，覆盖图失败/取消后的连接复用及子图恢复，本轮未复现日志中的 rollback 警告。
- 默认完整测试有一项客服 RAG 默认关闭断言受本地 .env 的启用开关影响；仅为测试进程设置 AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED=false 后复跑，不修改本地配置或无关测试。
- 隔离该开关后的完整 pytest 退出码为 0（PostgreSQL opt-in 测试默认跳过，已另行通过）；compileall、uv lock --check 与 git diff --check 均通过。
- 未执行真实模型生成及完整前端 SSE 端到端复验；生效需重启 Python AI 服务。
