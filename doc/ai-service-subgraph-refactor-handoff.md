# AI 服务子图重构交接

日期：2026-10-27。本文描述本轮已实现内容及下一阶段的边界，设计来源为 `docs/superpowers/specs/2026-10-27-generation-subgraphs-design.md`。

> 后续更新：本文第 1—8 节保留第一阶段交接基线；2026-10-27 已完成 Vue 多节点工具子图（第 9 节）、子图输入与私有状态收敛（第 10 节）、静态模型文本流式（第 11 节）以及真实 PostgreSQL 子图恢复与清理验收（第 12 节）。第 6 节优先项一、二及优先项三中的 HTML/MULTI_FILE 流式已完成，生产任务恢复仍未实现；当前能力和验证以第 9—12 节为准。

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
