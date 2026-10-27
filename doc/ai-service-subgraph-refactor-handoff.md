# AI 服务子图重构交接

日期：2026-10-27。本文描述本轮已实现内容及下一阶段的边界，设计来源为 `docs/superpowers/specs/2026-10-27-generation-subgraphs-design.md`。

> 后续更新：本文第 1—8 节保留第一阶段交接基线；2026-10-27 已完成 Vue 多节点工具子图（第 9 节）、子图输入与私有状态收敛（第 10 节）、静态模型文本流式（第 11 节）、真实 PostgreSQL 子图恢复与清理验收（第 12 节）、Spring 任务账本与查询（第 13 节）、真实 MySQL/Redis 故障矩阵和租约防护（第 14 节）以及持久化生成代际 fencing（第 15 节）。第 6 节优先项一、二及优先项三中的 HTML/MULTI_FILE 流式已完成，生产任务恢复仍未实现；当前能力和验证以第 9—15 节为准。

> 最新更新：2026-10-27 已完成第 15 节 MySQL 持久化代际 fencing 与活动版本指针。真实资源是否完成本轮迁移/集成测试，以第 15 节的本轮记录为准；生产任务恢复仍未实现。

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

## 9. Vue 工具循环多节点子图（后续实施）

设计：`docs/superpowers/specs/2026-10-27-vue-tool-subgraph-design.md`。

- `subgraphs/vue.py` 构建 `initialize -> model_turn -> validate_turn -> execute_tool -> continue_loop` 循环和 `finish` 出口。父图 `vue_generation` 直接注册该图；验证图的 `repair` 是分派图，静态修复走原节点，Vue 修复注册同一工具图实现。
- `vue_tool_loop.py` 提供 `VueLoopState`、`VueLoopProgress` 和四个核心节点。完整响应与待执行工具使用普通可序列化字典；私有字段不回传父图。初始化只在新阶段执行，恢复时 ordinal、文件计划、成功写入文件、重试次数、图片应用状态与工具游标均保留。
- 校验节点先验证整批参数及预算，再执行单个工具；工具结果、ordinal 和游标一起作为步骤结果保存。截断/超批次整轮重试、文件完整性、图片实际写入和跨阶段预算保持原规则。工具 ID 和 NDJSON node 名称保持原样。
- `nodes/common.py` 共用阶段开始/完成逻辑；工具图内部调度名不输出为公共节点。`workflow.py` 按工具和修复上限设置内部 recursion_limit，避免合法 40 次调用被默认步数中止。
- `_run_vue_tool_loop` 仍作为兼容内部入口，实际主链路直接注册编译图，不再在普通节点内执行 while。

`tests/test_vue_subgraph.py` 覆盖生成/修复各两个暂停边界（模型返回、批次第一个工具完成），重建父图后不重复已保存工作，批次第二个 ID 连续、修复计数/反馈和总预算正确、私有状态隔离；另测 40 次调用、截断/无效批次、取消后清理与修复预算耗尽。

恢复仅指测试中使用同一 saver、thread 的图级恢复。外部文件副作用与图 checkpoint 无原子事务，成功写入到 checkpoint 之间崩溃仍可能重试原工具 ID，Spring RUNNING/写回失败不确定性保持不变。未新增生产恢复接口、SSE 重放、租约重获或旧 checkpoint 迁移。

后续建议：继续第 6 节优先项二，缩小子图输入与私有上下文；模型流式和生产恢复仍应独立设计。

本阶段实际验证（测试进程设置 `AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED=false`，未修改 `.env`）：

- `uv run python -m compileall -q src`、`uv lock --check`、`git diff --check` 通过。
- `uv run pytest tests/test_vue_subgraph.py tests/test_subgraphs.py tests/test_api.py tests/test_langsmith_tracing.py -ra`：142 passed，含新增 Vue 专项 9 项。
- `uv run pytest -ra`：682 passed、6 failed、2 skipped（92.96 秒）；六项失败仍是第一阶段记录的 Reranker 子进程测试，错误 `CUSTOMER_SERVICE_RERANKER_UNAVAILABLE`，失败栈包含 `_GpuOwnerLock.acquire` 无法获取模型 owner 锁。
- `uv run pytest --ignore=tests/test_reranker.py -ra`：652 passed、2 skipped。跳过均因未启用真实 PostgreSQL 集成测试。

本阶段没有运行真实模型、Spring/Redis HTTP 工具集成、浏览器或完整 SSE 验收。Java 与前端无代码变更，前端既有客服改动保留。

## 10. 子图输入与私有状态收敛（后续实施）

设计：`docs/superpowers/specs/2026-10-27-subgraph-state-boundaries-design.md`。

### 当前边界

- `subgraphs/schemas.py` 集中定义生成、验证、修复的输入、内部状态和输出；子图不再继承完整 `WorkflowState`。`state.py` 仍是父图请求状态，既有内部导入入口保留。
- 父图和验证图注册子图时显式指定节点 `input_schema`，先投影再调用；仅限制子图自身输入无法阻止原始 `__start__` 检查点保存完整父状态。节点类型注解也会被 LangGraph 推断成输入 schema，因此 Vue 初始化显式按生成/修复选择契约，避免修复诊断被过滤。
- 子图不再复制父图顶层 `thread_id`、`conversation`、`metadata`、`input_review`、`publish`；模型需要的 conversation、metadata、图片和当前产物仍留在 context 中。这是结构收敛，不是嵌套内容脱敏。
- 静态生成只回传 `artifact`、`finish_reason`、`token_usage`；Vue 首次生成额外更新 context 和工具预算。验证从新候选开始，校验/构建/质量结论在阶段内部产生。修复接收 artifact、validation、build、repair_feedback，不接收或回写 quality_passed，不回写请求身份和原 validation。
- Vue 初始化将 `context.toolResults` 移入私有 `vue_tool_results` 通道；模型调用临时组装历史，出口恢复公共 context 中的完整历史，供后续审查与修复使用。没有裁剪历史、外部落库或新增存储。
- 模型、校验和继续节点不重写历史通道；稳定 context 只在初始化、提示变化、首次成功应用图片及出口更新。出口恢复公共数据后清空私有历史和文本副本，避免末尾 checkpoint 重复保存。历史 checkpoint、入口和父图阶段出口仍保存必要上下文，不承诺物理空间按固定比例下降。

### 验证范围

新增 `tests/test_subgraph_state_boundaries.py` 八项测试，覆盖三类分支的实际 checkpoint（含原始入口）、阶段输入与输出隔离、父图字段保留、Vue 生成/修复批次中间重建恢复、历史无重复与连续 ID、私有通道的实际写入及出口清空、配置/回调透传和递归子图发现。原有质量反馈恢复、取消、图片校验、修复上限与 40 次工具预算测试继续运行。

仍使用父图原 saver、thread 和 namespace 继承，没有新增运行时依赖或客户端。NDJSON/SSE、Java 与前端没有协议或代码改动。真实模型、Spring/Redis 工具集成、浏览器、完整 SSE 及真实 PostgreSQL 父子图恢复本阶段未验收。子图 state 结构已变化，不承诺在途旧版本 checkpoint 兼容，部署前排空请求。

本阶段实际结果（仅测试进程设置 `AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED=false`，未修改 `.env`）：

- 新增边界测试在旧实现上先复现五项失败，运行配置/回调测试原本即通过；新增两个静态分支 checkpoint 测试后，当前八项全部通过。
- API、子图、Vue 恢复与追踪专项合计 150 passed；最终代码另通过排除 Reranker 的完整回归。
- `uv run pytest -ra --tb=short`：690 passed、6 failed、2 skipped（115.84 秒）。失败仍为第 7、9 节记录的六个 Reranker 子进程用例，均报 `CUSTOMER_SERVICE_RERANKER_UNAVAILABLE`，失败栈包含 `_GpuOwnerLock.acquire` 无法获取模型 owner 锁。
- `uv run pytest --ignore=tests/test_reranker.py -ra`：660 passed、2 skipped（44.18 秒）；跳过均为未启用真实 PostgreSQL 集成测试。
- `uv run python -m compileall -q src`、`uv lock --check`、`git diff --check` 通过。前端既有四个客服文件修改保留，不纳入本次提交。

### 下一阶段

优先项三仍需要独立设计：模型文本流式输出与生产任务恢复各自有业务协议和验收要求，应分别实施。模型流式需区分展示增量和最终候选，并保留完整 Vue JSON 工具参数校验；生产恢复需落实任务状态、鉴权、租约/取消复核、发布核对和重连策略。真实 PostgreSQL 父子图恢复与清理可作为后续集成验收，不应将内存 saver 测试当作生产自动恢复保证。

## 11. 静态模型文本流式（后续实施）

设计：`docs/superpowers/specs/2026-10-27-static-model-streaming-design.md`。本阶段只实施 HTML/MULTI_FILE 首次生成及静态修复的模型文本流式，生产任务恢复单独推进。

### 当前实现

- `models/base.py` 定义可选 `StreamingGenerationModel` 与展示回调协议；原 generate/repair 保留。`OpenAICompatibleModel` 的 generate_stream/repair_stream 使用 ChatOpenAI.astream 并请求 stream_usage，按序聚合完整 AIMessageChunk，保留结束原因和用量。Vue、路由和质量审查继续使用完整响应。
- `orchestration/static_generation.py` 由静态生成与修复节点共用，将 `content_delta.data.contentMode` 区分为 `display_delta` 与 `candidate`；前者只供展示，后者是本轮完整候选。STOP/END_TURN 才发送 candidate；截断、过滤、无正常结束标记、空流与供应商异常不能进入发布。旧模型未提供可选流式方法时走原接口，流中途失败不会回退重调模型。
- 取消在展示回调和任务取消处生效，供应商异步迭代器在取消/错误时关闭；完整载荷预算和自动 LangSmith 追踪关闭沿用原逻辑。分片不写入 LangGraph state，完整候选仍在模型节点完成后进入检查点，不支持接续已断开的供应商 token 流。
- Java `LangGraphAiGenerationGateway` 识别新内容模式，展示增量不替换完整候选，新一轮草稿开始使旧候选失效。未知模式和流式 completed 缺失候选会明确失败。旧未标记内容事件继续按完整候选兼容。
- `StaticGenerationStreamFrame` 仅在 Java 网关与静态处理器之间区分展示/成功候选；`SimpleTextStreamHandler` 将展示帧解包为原 SSE 文本，只保存成功后的完整候选，不把草稿或修复前候选累加进最终历史，也不向页面重复发送整段最终源码。内部帧不进入浏览器和聊天历史。Legacy 累加和发布后历史落库失败不反转成功的语义保留。
- 公共 SSE、Vue 工具 JSON 校验、修复与工具预算、2000 字符窗口、80ms 刷新、成功后回源及单次预览刷新不变。前端代码未修改，用户既有客服改动保留。

### 部署与回滚

先升级 Java 网关和静态处理器，再升级 Python，以获得实时展示；新 Java 兼容旧无模式事件。供应商需支持 OpenAI 兼容流式和 usage 块；不支持时明确失败，不进行隐式二次生成。没有新增环境变量、数据库迁移或持久化存储。回滚时按同一功能整体回滚 Java/Python，排空在途请求，不承诺跨版本 checkpoint 续跑。

### 本阶段验证

- 新增 `tests/test_static_model_streaming.py` 17 项，覆盖适配器块聚合/用量/追踪关闭/预算/流关闭、首片早于模型完成、两类静态生成、修复候选、截断/过滤/无结束标记/空流/断流零发布、断连取消与 Vue 不增量执行工具。旧实现先复现 12 项失败，Vue 兼容项原本通过，再补充预算、空流、断连、截断修复测试。
- `uv run pytest -ra --tb=short`：707 passed、6 failed、2 skipped（96.28 秒）；六项失败仍是 Reranker owner 锁相关子进程用例，均报 `CUSTOMER_SERVICE_RERANKER_UNAVAILABLE`，失败栈包含 `_GpuOwnerLock.acquire`。未修改 Reranker 或本地 .env。
- `uv run pytest --ignore=tests/test_reranker.py -ra`：677 passed、2 skipped（22.68 秒）。跳过均为未启用真实 PostgreSQL 集成测试。
- `uv run python -m compileall -q src`、`uv lock --check` 通过。
- `mvn clean -DskipTests compile` 与 `mvn test '-Dtest=LangGraphAiGenerationGatewayTest,SimpleTextStreamHandlerTest'` 通过，Java 两类专项共 23 项（新增 8 项）。本地 HTTP 模拟测试确认首片可在服务端发送 candidate/completed 前到达，最终历史仅为完整候选；没有连接真实 Python 或真实模型。
- 前端三类既有流式/提示/预览测试 14 项通过；`npm run type-check`、`npm run build-only` 通过。构建报告既有动态导入和大包体提示，未改前端源码或依赖。

真实供应商、Spring/Python HTTP 联调、Redis/Selenium、浏览器与完整 SSE、真实 PostgreSQL 父子图恢复本阶段均未验收；上述模拟测试不代表生产端到端通过。

### 下一阶段建议

生产任务恢复仍是独立业务能力：先落实持久化任务状态、请求鉴权和应用归属、租约重获/取消复核、Spring 发布权威结果核对、恢复调度与客户端重连策略，再开放恢复入口。可先补真实 PostgreSQL 父子命名空间恢复/清理集成验收作为基础，但不能直接将 ainvoke(None) 当作生产恢复接口。Vue 工具 JSON 如需文本增量展示，应独立设计只展示安全 content 字段的解析，不得流式执行未完整参数。

## 12. 真实 PostgreSQL 子图恢复与清理验收

本阶段落实第 11 节建议的基础验收，不新增业务恢复 API。实施计划：`docs/superpowers/plans/2026-10-27-postgres-subgraph-verification.md`。

### 实现与边界

- 新增 `tests/test_postgres_subgraph_integration.py` 8 项 opt-in 测试，沿用 `AI_SERVICE_POSTGRES_INTEGRATION=true` 和 Settings 连接配置，不新增环境变量或 schema。
- 四种断点组合覆盖 Vue 首次生成/修复、模型轮次完成/首个工具完成；关闭旧连接池，以新 PostgresCheckpoint、saver 和父图恢复。验证保存的工具批次、调用 ID、修复/工具预算、token 用量与私有状态边界，已完成工具不重复调用。
- 成功、失败、取消清理前观测到真实父子命名空间；清理后按 thread 检查 `checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 三张物理表均无残留，连接池仍可用。
- TTL 测试创建两个唯一 thread，仅令其中一个过期；检查该 thread 的父子记录和状态摘要删除，另一活跃 thread 的嵌套记录和摘要保留。测试收尾只删除本测试 thread，未修改 .env、业务源码或真实项目文件。
- 跨连接池恢复复用 Fake Model/Fake Spring 网关和事件接收器，不是跨进程崩溃恢复或真实工具 exactly-once 保证；请求生命周期、NDJSON/SSE、租约与取消规则没有变更。修正原内存测试文件头，明确区分内存 saver 与真实 PostgreSQL。

### 本轮验证

测试进程设置 `AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED=false`，避免本地 .env 客服开关干扰既有离线测试；真实数据库专项和全量另设置 `AI_SERVICE_POSTGRES_INTEGRATION=true`。未输出连接凭据。

- `uv run pytest tests/test_postgres_subgraph_integration.py tests/test_postgres_checkpoint_integration.py -ra --tb=short`：10 passed（4.99 秒），包含新增 8 项和既有 2 项。
- 不设置 opt-in 开关运行新增测试：8 skipped，不连接数据库。
- `uv run pytest -ra --tb=short`：717 passed、6 failed（104.91 秒），真实 PostgreSQL 10 项此次均执行。六项失败仍为第 11 节记录的 Reranker owner 锁用例；报 `CUSTOMER_SERVICE_RERANKER_UNAVAILABLE`，堆栈包含 `_GpuOwnerLock.acquire`。未停止服务、修改锁或绕过保护。
- `uv run pytest --ignore=tests/test_reranker.py -ra --tb=short`（开启 PostgreSQL）：687 passed（29.07 秒）。
- `uv run python -m compileall -q src`、`uv lock --check` 通过。

Java、前端没有代码改动，未重跑其测试；前端既有四个客服文件修改保留。未运行真实模型、Spring/Python HTTP、Redis/Selenium 或完整浏览器端到端。

### 下一阶段

真实 PostgreSQL 的底层恢复/清理证据已补齐，下一步应先设计并实现 Spring 所有的持久化生成任务状态与查询边界：定义请求唯一性、应用/用户归属、执行与发布终态、取消墓碑和保留期限，并与 GenerationLeaseService 和 VersionedArtifactStore 的权威状态核对。之后再分阶段增加恢复认领、调度和客户端重连；在这些约束落地前继续保持生产恢复入口关闭。不要将 `ai_workflow_status` 脱敏节点摘要提升为业务任务账本，也不要把本轮测试当作生产自动恢复已完成。

## 13. Spring 持久化生成任务账本与只读查询

本阶段实现第 12 节建议的任务状态基础边界。设计与计划分别见 `docs/superpowers/specs/2026-10-27-generation-task-ledger-design.md`、`docs/superpowers/plans/2026-10-27-generation-task-ledger.md`；字段、API 和部署详情见 `doc/generation-task-ledger.md`。

### 当前实现

- 新增 MySQL `generation_task`、实体、Mapper/XML、GenerationTaskService 和 VO，服务端请求 ID 唯一，保存 appId/userId/生成类型/执行状态/取消观测/稳定代码及时间，不保存提示词、源码或底层异常正文。获取应用租约后、历史和模型调用前插入；插入失败关闭执行并释放租约。重复订阅生成新 UUID，不能以同一 ID 执行第二次。
- RUNNING 仅能条件更新为 SUCCEEDED、FAILED、CANCELLED，终态不可被迟到回调覆盖。终态默认查询 30 天，RUNNING 不自动过期；当前没有物理清理调度，保留唯一性和取消墓碑。应用删除不级联删除账本，查询仍要求现存应用所有权。
- 取消仍由 Redis 提交状态门仲裁，获胜后 best-effort 保存首次 `cancelRequestedAt` 再通知引擎。补查租约获得至任务插入之间的取消窗口，已停止则不写历史或调用模型。引擎先到终态时允许随后补首次取消观测，但不改终态或保留期限。记录失败不会影响下游取消通知；提交胜出的取消不创建墓碑。
- `/api/apps/chat/gen/code/task` 提供按 appId 和可选 requestId 的只读查询；省略请求 ID 返回当前用户最近未过期任务。查询校验登录、现存应用所有权及记录 appId/userId，分别返回 executionState、匹配请求的 leaseState 和 publicationState。Redis 不可用/租约不存在/属于其他请求时为 UNKNOWN，不推断执行失败。
- 静态 COMMITTED 证据来自 VersionedArtifactStore 的提交标记和墓碑；保留 release 时复核 manifest、序号、文件集和哈希。仅墓碑不证明提交；版本被保留策略删除仍可核对历史提交。损坏/读取异常为 UNAVAILABLE，证据缺失为 NOT_CONFIRMED，Vue 为 NOT_VERSIONED。查询不写回、不修复指针或发布；执行 FAILED 和发布 COMMITTED 可以同时出现。
- 原活动 status、cancel 和公共 SSE 继续兼容；修复全局异常处理器误将 JSON 查询/取消子资源权限错误写成 SSE 的前缀判断。新增任务 JDBC 写入错误经脱敏稳定码返回，不暴露 SQL/连接信息。

### 部署与一致性限制

**升级 Spring 前在业务库执行 `sql/alter_generation_task.sql`。** 首次初始化 SQL 已同步。没有自动迁移；未建表会在调用 Python 前失败关闭。回滚 Spring 保留新表，无 Python schema/配置、Redis 或前端迁移。本轮未对真实业务 MySQL 执行迁移。

MySQL、Redis 与文件系统没有跨资源事务：取消仲裁至墓碑写入、发布至任务终态写入仍有不确定窗口。插入结果丢失也可能留下未实际调用模型的 RUNNING 行；终态写失败只记录日志，不逆转已发布成功。因此该账本只提供执行记录和独立证据，不能作为生产恢复授权。Python 请求正常终态仍清理 checkpoint，未新增任务心跳/认领/调度/恢复接口或客户端重连。

### 本轮验证

- `mvn clean -DskipTests compile`：BUILD SUCCESS（271 个 Java 源文件），仅报告既有 varargs、弃用和未检查操作提示。
- `mvn test '-Dtest=GenerationTaskMapperTest,GenerationTaskServiceTest,AppGenerationTaskTest,AppServiceGenerationCancellationTest,ArtifactPublicationEvidenceTest,GenerationJsonErrorBoundaryTest,GenerationLeaseServiceTest,AppControllerSseTest,LangGraphAiGenerationGatewayTest,SimpleTextStreamHandlerTest,ArtifactPublicationServiceTest'`：80 项通过、0 失败/错误/跳过（19.587 秒），其中新增 22 项。
- SQL 专项使用测试依赖 H2 的 MySQL 模式执行真实 Mapper XML，覆盖唯一请求、双写者终态竞争、取消先后乱序、保留截止与用户/应用范围。H2 仅作为 test 依赖；DDL 测试去除字符集和索引方言，不代表真实 MySQL 迁移验收。
- 真实文件证据测试只写 JUnit 临时目录，模型、Redis 和业务库使用 mock；未调用真实模型、Spring/Python HTTP、Selenium 或浏览器端到端。
- `git diff --check` 通过；前端既有四个客服文件修改保留。Python/前端源码未改，本轮未重跑其测试。

### 下一阶段

先在隔离环境执行真实 MySQL 迁移并完成 Redis/文件存储联调，覆盖插入确认丢失、取消落账失败、发布后终态落账失败、租约丢失和服务重启等矩阵。随后设计持久化执行所有者、心跳与过期判定、取消权威状态和恢复认领协议，并核对 Spring 发布事实，再接续 Python checkpoint。不要仅根据 RUNNING、无租约或 `ainvoke(None)` 自动恢复任务。

## 14. 真实 MySQL/Redis 故障矩阵与租约防护

执行计划：`docs/superpowers/plans/2026-10-27-generation-task-real-integration.md`；业务账本接口与数据边界继续见 `doc/generation-task-ledger.md`。

### 建表执行

2026-10-27 按用户指示执行 `sql/alter_generation_task.sql`。目标经本地配置与安全护栏确认是 `localhost` 的 `yu_ai_code_mother`，服务版本 MySQL 8.0.41；执行前表不存在，执行后检查到 12 列、`request_id` 的 `ascii_bin` 排序规则、PRIMARY/owner/retention 三个索引，以及已启用的 `chk_generation_task_state`。未输出连接凭据。部署配置与迁移脚本一致。

### 故障矩阵

新增默认 opt-in 的 `GenerationTaskResourcesIT`，设置进程环境变量 `AI_TASK_INTEGRATION=true` 才连接 application 配置指定的本机业务库与 Spring Redis database 1；本地 host/database/schema 不符合护栏则拒绝运行。夹具检查业务表已存在，不执行 DDL。每项生成唯一 requestId/appId，结束只删除精确账本行及对应三个 Redis 键，产物写 JUnit 临时目录。不停止服务、不清空共享库或 Redis、不触碰 `projects/`。

- 真实 MySQL Mapper/XML 检查大小写敏感请求唯一性与状态 CHECK，通过两个并发数据库连接确认终态 CAS 只有一个赢家；检查跨用户读隔离。
- 模拟任务 INSERT 已落库而调用方收到异常：任务保留 RUNNING，新的 Mapper/服务可查询，实时 lease 为 UNKNOWN、发布为 NOT_CONFIRMED，模型/历史均未调用。这说明请求结果不确定时失败关闭会牺牲自动续跑，但不因查询到 RUNNING 就擅自恢复。
- 模拟 MySQL 取消落账失败：Redis 已仲裁的取消仍继续通知下游并阻止提交，任务账本短时缺少墓碑；随后落终态/补记可恢复可观测性。
- 使用真实 Redis 提交临时目录发布，再模拟 MySQL 终态写失败并重建 Mapper/服务：查询返回 execution RUNNING、lease UNKNOWN、publication COMMITTED，指针保持不变。证明独立证据可观察不一致，不自动翻转账本或重新发布。
- 两个真实 Redis 客户端验证旧租约丢失后新请求可获得租约、旧请求提交被拒且旧释放不清除新状态；同客户端/线程第二次获取也必须立即拒绝。测试正常结束后查询结果不残留。
- 真实资源测试 7 项通过；与租约单测合计 16 项，0 失败/错误。未开启 opt-in 重跑时 7 项默认跳过，数据库/Redis fixture 未初始化。

### 修复与边界

真实 Redis 测试先复现两处问题：仅 Redis 状态 bucket 有 `ACTIVE` 残留时，原 `commit()` 不核对仍有效的应用锁，仍可执行发布；Redisson 在同一客户端同线程可重入，原 `acquire()` 可覆盖成另一个请求。现在 `GenerationLeaseService.acquire()` 用 transition lock 保护租约获取与状态初始化，并显式拒绝已有应用锁；提交前确认应用锁仍持有；状态查询与取消把无锁残留状态呈现为 IDLE 且不自动删除记录。

该检查避免“在进入发布临界区前已失锁”的竞态，但不能防止锁在 `isLocked()` 检查之后、版本文件切换期间过期。当前锁也没有可写入 manifest/publish pointer 的 fencing token；因此租约超时/Redis 分区期间的迟到文件写入仍需后续修复。不能将本次状态检查视为分布式 fencing 或 exactly-once 发布。

### 验证

- 建表前存在数为 0；建表后 schema 检查成功。
- `mvn test '-Dtest=GenerationLeaseServiceTest,GenerationTaskResourcesIT'` 且 `AI_TASK_INTEGRATION=true`：16 项通过，其中 7 项访问真实 MySQL/Redis。
- 同一命令不设置开关：9 项通过，7 项 opt-in 测试跳过。
- `mvn clean -DskipTests compile`：271 个 Java 源文件 BUILD SUCCESS；账本、artifact、SSE、网关、取消相关集合 `mvn test '-Dtest=GenerationTaskMapperTest,GenerationTaskServiceTest,AppGenerationTaskTest,AppServiceGenerationCancellationTest,ArtifactPublicationEvidenceTest,GenerationJsonErrorBoundaryTest,GenerationLeaseServiceTest,AppControllerSseTest,LangGraphAiGenerationGatewayTest,SimpleTextStreamHandlerTest,ArtifactPublicationServiceTest'`：84 项全部通过。
- 测试中的一次真实租约提交/两次状态转换日志仅包含唯一测试 requestId 与异常类型，不包含连接信息；测试后测试 ID 行与 Redis 状态键清理。
- 本轮仅 Java/文档变化，Python 与前端源码未改；真实模型、Spring/Python HTTP、完整浏览器 SSE 未执行。前端仓库用户既有客服改动保留。

### 下一阶段

实现持久化执行所有者与租约代际/fencing：发布写入必须携带并校验当前代际；Redis 丢失、锁过期、应用进程暂停恢复后旧代际不得切换 `.current`。同时让取消墓碑在 MySQL/Redis 不可用窗口具有单一权威来源，定义 RUNNING 心跳超时之后如何安全认领，而不是直接把记录认作可恢复。完成真实数据库/Redis/文件并发与进程故障验证后，再设计 Python checkpoint 接续和客户端重连；生产自动恢复在此前保持关闭。

## 15. MySQL 持久化生成代际与静态活动指针

设计与计划：`docs/superpowers/specs/2026-10-27-generation-fencing-design.md`、`docs/superpowers/plans/2026-10-27-generation-fencing.md`。数据库部署详情见 `doc/generation-task-ledger.md`。

### 当前实现

- Redis 应用锁获得后，`GenerationFenceService` 在 MySQL 事务中原子递增 `generation_fence.current_token`；token 放入 `GenerationLease`，并在模型调用前写入 `generation_task.fence_token`。MySQL 分配失败会释放 Redis 锁并停止请求；token 空洞允许存在，不会复用。
- 新增不可变 `generation_publication` 发布事实与 `(appId, codeGenType)` 的 `generation_active_artifact` 权威指针。release、摘要校验和墓碑先落共享文件系统；数据库事务锁住应用代际行，只有 token 仍是当前代际才能记录发布历史并切换活动指针。
- DB 指针提交后才更新 `.current` 和提交标记作为兼容镜像；镜像失败记录脱敏异常类型，不逆转已提交结果。管理员人工 HTML 恢复同样先取得新 Redis 租约和 MySQL token。
- 首次发布前从既有 `.current` 种入 token 0 的活动基线，再创建 `.db-pointer-enabled` 标记。标记存在后，预览/部署/下载/上下文读取走 MySQL 活动指针并复核 release；数据库不可用或目标 release 无效时失败关闭，不使用可能过期的 `.current`。未启用标记的旧项目保留旧解析路径。
- 发布状态从 MySQL 不可变发布事实核对，release 保留时再核对文件摘要；历史发布事实独立于当前指针与 release 清理。文件系统与数据库仍非跨资源事务，失败可能留下未激活 release/墓碑；不会因此改变活动指针。
- Vue 工程目录仍没有不可变版本切换，本轮不声称其构建受到该 fencing 保护。没有恢复认领、自动重试、心跳、SSE 重放或 Python checkpoint 恢复入口。
- fencing manifest 序号直接使用 MySQL token，避免多实例竞争旧共享序号文件。fencing 路径暂不自动清理 release，防止 DB 提交后暂停的旧发布者恢复并误删后继活动版本；需要后续增加受数据库保护的保留调度并预留磁盘空间。升级须所有实例统一切换，不能混跑旧文件指针权威版本；回滚前停生成、停止旧执行者并按 DB 指针重新核对镜像。

### 本轮验证

当前完成：

- `mvn -DskipTests compile`：273 个 Java 源码编译成功。
- `mvn test '-Dtest=GenerationTaskMapperTest,GenerationTaskServiceTest,GenerationLeaseServiceTest,ArtifactPublicationServiceTest,ArtifactPathResolverTest,AppGenerationTaskTest'`：59 项通过。
- `mvn test '-Dtest=GenerationTaskMapperTest,ArtifactPathResolverTest,GenerationTaskResourcesIT'`：本地 SQL/解析器 13 项通过；真实资源 8 项因未设置 `AI_TASK_INTEGRATION=true` 而跳过。
- H2 MySQL 模式测试实际执行新增 fencing Mapper XML，覆盖单调 token、发布历史和活动指针；服务事务测试验证旧 token 在新代际后被拒且不能替换当前指针。临时文件测试验证迟到 `.current` 不改变数据库指针解析结果，数据库查询失败时不回退。

### 迁移与本轮验证

- 目标经配置护栏确认是 `localhost/yu_ai_code_mother`，服务版本 MySQL 8.0.41；首次试跑发现该版本不支持 `ADD COLUMN IF NOT EXISTS`，迁移脚本随即改为 `information_schema` 条件 DDL。首次试跑仅第一条 DDL 失败，没有创建 fencing 列或表；修正后执行成功并核验 `generation_task.fence_token` 及三张 fencing 表。
- `mvn clean -DskipTests compile`：273 个 Java 源文件 BUILD SUCCESS。
- 较早一次限定集合：102 项执行，94 项通过、8 项真实资源用例因未设置 opt-in 跳过；最终回归以本节末尾记录为准。
- `$env:AI_TASK_INTEGRATION='true'; mvn test '-Dtest=GenerationLeaseServiceTest,GenerationTaskResourcesIT'`：17 项通过，其中 8 项访问真实 MySQL 8.0.41、Spring Redis database 1 和 JUnit 临时文件目录。覆盖 fencing token 递增、旧代际迟到拒绝、DB 活动指针胜过迟到 `.current` 镜像，以及账本/租约故障矩阵。测试不触碰 `projects/`，测试后按 appId 清理账本、发布、活动指针、代际行和 Redis 键。
- 另有 H2 MySQL 模式和 JUnit 临时目录专项：验证事务 fencing、旧版本解析、DB 读取失败关闭。fencing 专项未连接模型或 Python HTTP，不代表完整浏览器 SSE 验收。
- 后续增补 `publisherPausedInsideFileVerificationCannotActivateAfterSuccessorCommits`：不同 store 实例共用真实 MySQL 与临时文件目录，旧发布在校验中阻塞，后继签新代际并提交，旧发布恢复后返回 `GENERATION_FENCE_REJECTED`。同时新增 H2 并发签发、活动指针写失败回滚发布事实，以及文件镜像写失败不反转 DB 提交测试。`AI_TASK_INTEGRATION=true` 运行 Mapper/解析器/真实资源三类共 24 项全部通过，其中真实资源 9 项。
- 执行无筛选 `mvn test` 时发现历史 `AiCodeGeneratorServiceTest` 会直接访问真实供应商并写 `tmp/code_output/vue_project_1`，已中断该次运行；无筛选全量不计通过。该调用不构成完整业务端到端验收，后续使用明确限定的回归集合，生成临时产物不提交。
- 最终验证：`mvn clean -DskipTests compile` 成功；开启 `AI_TASK_INTEGRATION=true` 执行上述限定回归集合并加入 `InternalAiToolsControllerTest,ArtifactRecoveryControllerTest,AiCodeGeneratorFacadeTest`：145 项通过，0 失败/错误/跳过，其中真实 MySQL/Redis 测试 9 项。Python 与前端源码未修改，未重跑其测试。`git diff --check` 通过。
