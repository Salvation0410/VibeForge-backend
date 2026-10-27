# AI 服务子图重构交接

日期：2026-10-27。本文描述本轮已实现内容及下一阶段的边界，设计来源为 `docs/superpowers/specs/2026-10-27-generation-subgraphs-design.md`。

## 1. 已实现结构

主图位于 `ai-service/src/ai_service/orchestration/workflow.py`，保留请求生命周期、输入准备、路由、发布与终态：

```text
input_guard -> context_prepare
  -> html_generation | multi_file_generation | vue_generation
  -> verification_repair
  -> HTML/MULTI_FILE: artifact_publish -> finalize
  -> Vue: finalize
```

四个子图均以编译图直接注册为主图节点，可通过 `graph.get_subgraphs()` 发现。
三个生成子图由同一构建函数创建独立实例；静态分支目前只有单个生成节点，Vue 节点仍调用普通 Python 工具循环。
验证修复子图包含 artifact_validation、project_build、quality_review、repair、fail_quality。
子图成功出口只表示验证通过；失败以异常返回请求执行器，不在子图内生成公共终态。

## 2. 文件导航

路径均相对于 `ai-service/src/ai_service/orchestration/`：

| 文件 | 职责 |
| --- | --- |
| `workflow.py` | 请求执行、事件队列、父图组装、工具调用边界及终态清理 |
| `state.py` | WorkflowState 公共状态 |
| `routing.py` | 校验、构建、审查后的确定性路由 |
| `nodes/common.py` | 节点取消检查、事件、脱敏状态摘要和追踪 |
| `nodes/input.py` | 输入审核、产物上下文和图片搜索 |
| `nodes/generation.py` | 普通产物生成与 Vue 循环入口 |
| `nodes/verification.py` | 校验、构建、源码快照审查、修复和失败 |
| `nodes/publication.py` | 发布权威结果记录与唯一成功事件 |
| `subgraphs/generation.py` | 三种生成子图的构建与 GenerationOutput |
| `subgraphs/verification_repair.py` | 验证修复子图与 VerificationOutput |
| `vue_tool_loop.py` | 首次生成与修复共用的模型/文件工具循环 |

节点工厂为每个请求绑定工作流依赖、emitter 和终态字典；没有新增模型客户端、数据库或工具客户端。
为既有测试与内部调用保留 `workflow.WorkflowState`、路由函数导入入口和 `_run_vue_tool_loop` 转发方法。

## 3. 状态与预算

父子图共享 WorkflowState，子图 output_schema 限制回传字段，防止请求身份及其他阶段字段被意外覆盖。
生成子图可回传 context、artifact、tool_call_count、finish_reason、token_usage。
验证修复子图额外回传 validation、build、quality_passed、repair_feedback、repair_count。
不增加并行生成分支，因此现有字段使用覆盖语义，不需要叠加 reducer。

工具预算贯穿首次生成和所有修复；默认最多 40 次。修复次数默认最多两次，仍读取配置。
工具 ID 格式保持原样，尤其是 `requestId:vue-generate:ordinal` 和 `requestId:vue-repair:count:ordinal`。
静态生成依然严格解析和不可变发布；Vue 依然通过 Spring 工具操作文件。

## 4. 检查点与恢复的实际边界

父图使用原有 AsyncPostgresSaver；子图 `compile()` 不显式指定 saver，继承父图。
没有使用 `checkpointer=True` 的跨调用记忆，也没有为子图创建独立 thread。
LangGraph 0.6.11 下内存 saver 测试验证了生成子图与验证子图具有独立 checkpoint_ns，父图 namespace 为空字符串。
同一 thread 的 `adelete_thread()` 在内存实现中会删除父子命名空间。

`tests/test_api.py::test_vue_blocking_review_resumes_from_feedback_without_repeating_reviewers` 已改为真实嵌套图测试：

1. 在验证子图编译时设置 quality_review、repair 的静态调试断点。
2. 从父图 `aget_state(config, subgraphs=True)` 取得暂停子任务的配置，读取对应 namespace 中的反馈。
3. 重建父图对象，以相同 saver 和 thread_id 调用 `ainvoke(None, config)`。
4. 确认先修复，不重复第一次的三个 Reviewer；重新校验、构建后才重新审查。

注意：`graph.ainvoke(..., interrupt_after=['quality_review'])` 不再是父图的有效内部节点断点。
应把断点配置在子图构建函数，生产路径默认没有断点。本轮没有对外开放暂停/恢复接口。

生产入口仍使用初始状态启动，终态会清理检查点；没有重启扫描、自动调度、Spring 租约重获或 SSE 重放。
进程内 terminal 字典中的发布成功记录不持久化，不能把本轮恢复测试理解为跨进程发布自动恢复保证。
真实 PostgreSQL 父子图 namespace 清理、崩溃恢复与连接复用仍需集成验收。

## 5. 事件、取消和发布约束

- 请求共享一个 EventEmitter，sequence 递增；子图不会新建独立事件序号。
- NDJSON 的 node 继续使用 generate_html、generate_multi_file、vue_agent、project_build 等旧名称。
- html_generation 等父图调度名称不新增为公共事件；不要直接把内部 namespace 替换为事件 node。
- Java 会匹配 project_build 等名称，后续改名必须同步检查网关和前端。
- 工作流仍使用 graph.ainvoke，节点主动推事件；模型仍使用 ChatOpenAI.ainvoke，无逐 token 文本流。
- 主图 finalize 发 completed，请求级错误处理发 failed；已有 Spring 发布结果优先于后续附属错误。
- 用户取消、协作式取消、工具 ID、Spring 生成租约和提交状态门保持原有逻辑。
- Vue 源码快照、Reviewer 原文不进入图状态；只保留最多 4000 字符阻断修复反馈。

## 6. 下一阶段建议

优先项一：Vue 工具循环拆成真实多节点子图。

建议节点边界为模型轮次、响应完整性/批次校验、工具执行、继续判断。此前必须先设计本轮状态：file_plan、written_files、ordinal、retry_count、batch_limit、planning_turns、image_retries、image_used、待执行工具及工具结果。
当前这些变量均为函数局部状态，仅包一层子图不会获得逐轮恢复。生成与修复必须复用同一套循环实现，同时维护跨阶段 tool_call_count。
恢复时不能把 ordinal 归零或改变工具 ID；不得执行截断响应中的部分工具，不得绕过已规划文件完整性和图片实际写入检查。
Spring 文件操作与 Redis 写回不属于同一事务，工具 RUNNING/写回失败仍是不确定状态，不能用 checkpoint 宣称严格 exactly-once。

优先项二：缩小子图输入和私有状态。

当前子图仅限制输出，输入仍共享完整 WorkflowState。若引入独立输入 schema 或包装节点，应验证配置透传、子图发现、恢复及回传字段；避免把大体积工具历史复制到更多 checkpoint。
如果节点工厂反向依赖 GenerationWorkflow 逐渐阻碍测试，可以提取最小依赖协议，但不要引入重复客户端。

优先项三：独立设计模型文本流式和生产恢复。

流式需区分展示增量和最终候选；Java 静态分支当前缓存最后一个完整候选，直接换成 token 碎片会丢内容。
Vue JSON 工具参数须完整解析后执行。前端继续遵守 2000 字符窗口、80ms 更新和成功后单次预览刷新。
生产恢复需先有任务状态、请求鉴权、租约/取消复核、发布结果核对和重连策略，不能单独开放 `ainvoke(None)` 即上线。

## 7. 验证与本地环境

所有命令在 `ai-service/` 执行。当前本地 .env 开启客服 RAG，会污染测试中默认关闭的假设；本轮仅为测试进程设置：

```powershell
$env:AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED='false'
uv run python -m compileall -q src
uv lock --check
uv run pytest -ra
uv run pytest --ignore=tests/test_reranker.py -ra
```

compileall 和 lock 检查通过；排除 Reranker 后为 643 passed、2 skipped。
新增子图专项 8 项通过，另有既有 API 测试中的嵌套恢复测试通过。
两项跳过均为未设置 AI_SERVICE_POSTGRES_INTEGRATION 的真实 PostgreSQL 测试。

最终全量结果为 673 passed、6 failed、2 skipped（97.55 秒）。六项失败均为既有 Reranker 子进程测试，稳定错误为 CUSTOMER_SERVICE_RERANKER_UNAVAILABLE；本轮修改前的基线已有相同失败。
直接尝试获取当前配置 model/device 的 OS 独占锁同样失败，表明测试使用的锁键当前不可获取。
本轮没有停止现有 Python 服务、修改 .env 或修改 Reranker 来绕过保护。建议下一 Agent 在隔离环境或确认释放模型 owner 后重跑全量测试。

真实模型、Spring/Python HTTP、Redis 幂等、浏览器及完整 SSE 本轮未验收；Java 和前端没有代码修改。
前端仓库存在用户既有客服相关修改，本轮保留且不纳入提交。

## 8. 后续变更与回滚

修改后至少执行上述 Python 检查及 subgraphs/API 恢复测试。修改公共事件时还需跑 Java 网关和前端流式测试。
本轮没有 schema、锁文件或环境变量迁移；回滚本次代码提交即可恢复单图组装。
不要让在途请求跨版本恢复；生产部署应先排空生成请求。新子图 checkpoint 路径与旧单图不同，不能承诺旧 checkpoint 直接续跑。
