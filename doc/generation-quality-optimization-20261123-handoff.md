# 生成质量优化交接（2026-11-23）

## 交付状态

本轮已实现修复首次写入前的重复读取保护、有限 Vue 事件契约检查、构建检查摘要组合、逐轮安全诊断，以及生成/审查/修复提示补充。Python 和 Spring 源码均有变动，独立前端仓库未改动。默认仍为两次修复、40 次共享模型工具调用。

2026-11-23 交付时仅完成代码及测试验证。2026-11-24 已按用户授权启动 Docker、Spring、Python 和前端，完成一次受控真实生成及失败产物浏览器验收，详情见第六节。真实请求仍以 `QUALITY_REVIEW_FAILED` 结束，不能宣称问题已消失或生成成功率已经提升；两次均未手工修改失败应用。

## 一、原请求及证据边界

- 请求：`a7f728fd-3d17-4901-8135-0358cf8a8b2b`。
- 应用：`482408885784170496`，类型 `VUE_PROJECT`。
- 时间：2026-11-23 北京时间约 17:41:41—17:43:21。
- 证据来源：PostgreSQL 状态摘要、LangSmith 脱敏节点记录、Redis 工具结果及当前生成源码；未找到对应本地控制台日志。

| 阶段 | 已核对的结果 |
| --- | --- |
| 首次生成 | 14 次写入，构建的必填属性检查失败，错误为 `VUE_COMPONENT_CONTRACT_FAILED` |
| 修复 1 | 5 次读取、2 次修改，共享工具累计 21；构建通过，质量拒绝，问题码含 `FUNCTIONAL_ERROR`、`REQUIREMENT_MISSING` |
| 修复 2 | 17 次读取、2 次写入，读取涉及 5 份不同内容，其中 12 次重复；共享工具累计 40 |
| 终态 | 构建通过，质量审查仍有 4 条 `FUNCTIONAL_ERROR`；两次修复耗尽，返回 `QUALITY_REVIEW_FAILED` |

直接终止原因是质量拒绝且修复次数已用完；共享工具额度同时已经用尽。完整图 checkpoint 已清理，不能恢复历史 Reviewer 四条问题的理由正文。

只读源码另发现 ProductCard / ProductGrid / App 的 `add-to-cart` 与 `add` 接线不一致、部分导航目标缺失等问题。这些是当前源码发现，不能冒称已还原历史 Reviewer 的原始意见。原锚点检查为 `DYNAMIC_PAGE` 跳过、`checkedLinks=0`，不是导航验证成功。

## 二、本轮已完成优化

### 1. 首次写入前的读取停滞保护

涉及 `ai-service/src/ai_service/orchestration/vue_read_stall.py`、`vue_tool_loop.py` 和 `subgraphs/vue.py`。

- 仅正式 Vue 修复启用，在本阶段首次显式成功写入前统计同路径同完整内容的连续重读。
- 连续四次重复后给模型一次固定纠偏，要求利用最新结果完成具体修改，并核对完整事件链。
- 纠偏后继续四次重复且未成功写入，返回 `VUE_REPAIR_READ_STALLED`。纠偏后仅声称完成、没有成功写入，也明确失败。
- 判断在完整纯读批次末执行，不跳过同批后续工具；工具实际执行并照常计入原额度，没有缓存读取。
- 新路径/内容、非读取、未知/失败/截断结果打断连续计数；纠偏机会每阶段一次，新阶段清除反馈，checkpoint 恢复保留私有标志。
- 取消先于保护判定。旧的成功写入后交回验证策略继续生效，依然必须经过构建和质量审查。

确定性 Fake 回放中，两个文件交替重读在第 6 次读取后纠偏；模型接受纠偏时随后一次写入，共 7 次工具调用；继续空转时在 10 次读取后明确失败。这只是该测试轨迹的结果，不是实际模型节省比例或成功率结论。

### 2. 有限事件契约检查和覆盖摘要

涉及可信脚本 `src/main/resources/vue/component-contract-check.cjs`，以及 Spring 的 `VueEventCheck`、`VueComponentContractCheck`、`VueProjectBuilder`、`VueAnchorCheck`。

强制构建顺序为 npm/dist → 必填属性 → 事件 → 锚点；前置失败不执行后续检查，全部成功时组合摘要，避免最后一项覆盖先前提示。

| 情况 | 行为 |
| --- | --- |
| 支持的静态本地 SFC，运行时字面 `defineEmits`，显式 `inheritAttrs:false`，父级监听未知自定义事件 | `VUE_EVENT_CONTRACT_FAILED`，进入原有限修复 |
| 默认属性透传下的疑似错接 | 仅输出有界 Reviewer 提示，不单独阻断，不可单凭此提示拒绝 |
| 动态、类型声明、展开、attrs 转发、未知组件、作用域等不支持语法 | 保守跳过 |
| 源码扫描不完整或超过 24 文件 / 单文件 12000 字符 / 总计 60000 字符 | 跳过，明确 `INCOMPLETE` |
| 脚本/Node/compiler/输出协议不可用 | `VUE_EVENT_CHECK_UNAVAILABLE`，Python 直接失败，不让模型修复环境 |

脚本不执行组件；不要求所有声明事件都必须被监听。Java 严格检查协议字段、大小、计数与证据路径，异常输出不回显正文。原生事件及 v-model 不在本期硬判定范围，检查不证明完整数据流、状态更新、导航或浏览器交互正确。已有 dist 快路径不重检。

对原失败项目运行新脚本的只读探测结果：`checkedUsages=4`、`skippedUsages=3`，无确定性阻断，提示 `src/App.vue -> ProductGrid @add-to-cart`。这说明提示能够暴露该疑点，同时保留默认透传边界；没有对原项目写入、重新构建或验收。

### 3. 提示词完善

`prompts/generation.py` 和 `prompts/review.py` 补充事件发出方 → 容器转发 → 父级监听 → 状态更新的完整调用链，以及导航目标核对。审查必须依据当前可见源码，默认透传提示不能单独作为拒绝依据，锚点跳过不等于通过。原始需求和修改范围优先，未扩张业务需求。

### 4. 逐轮安全诊断

涉及 `models/verification_diagnostics.py`、`nodes/verification.py`、验证私有 schema、`workflow.py` 和 `infrastructure/postgres_checkpoint.py`。

- 验证子图私有 `verification_history` 最多 36 项，按轮次记录产物校验、构建、质量审查。
- 固定字段为 `round/stage/verdict/errorCode/toolCount/phaseReadCount/phaseWriteCount/repeatedReadCount/issueCount/issueCodes`，只接受白名单枚举及有界计数。
- 同轮同阶段恢复重放替换记录，新候选清除旧的当前审查诊断并保留历史。
- 经过工作流和 PostgreSQL 两次投影，写入既有 `ai_workflow_status.status_payload`；后续父图节点缺少该私有字段时保留已有历史。无需数据库 schema 迁移，沿用原 TTL。
- 不进入模型、父图状态或公共事件，不保存提示词、源码、路径、Reviewer 理由正文；工具预算日志新增固定原因及重复读取次数。
- 历史只覆盖已经完成的验证节点，修复内部提前异常可能没有本阶段新记录。结合预算日志定位现场，不把摘要当成完整执行审计或完整审查理由。

## 三、本轮验证

所有生成工作流测试使用 Fake Model；真实 PostgreSQL 测试仅操作独立 UUID 行，在事务结束回滚，不启动全局清理、不操作原请求。

| 验证 | 结果 |
| --- | --- |
| Python 生成修复、预算、阶段与新增诊断专项 | 75 passed，1 skipped（显式门控 PostgreSQL 项） |
| 真实 PostgreSQL 历史保留/替换 | 1 passed，独立事务回滚 |
| Node 属性、锚点、事件测试 | 52 passed，0 skipped，使用独立前端仓库已有 Vue compiler |
| Java 构建与三类检查测试 | 37 passed，1 skipped（原有真实集成门控项）；含事件失败短路与中断清理 |
| Python 全量 | 两轮均未单次全绿，详见下方环境复核；生成相关未出现失败 |
| `python -m compileall -q src`、`uv lock --check` | 通过 |
| `mvn -q -DskipTests compile` | 通过 |

首轮 Python 全量为 2636 passed、26 skipped、4 failed，四项均在未修改的 `test_reranker.py` 中，报 `CUSTOMER_SERVICE_RERANKER_UNAVAILABLE`，其中子进程堆栈直接指向 GPU 所有者锁。本机服务正在运行，未停服释放锁；仅将测试进程的模型锁名改为独立 UUID 后，原四项全部通过（4 passed），复核了锁冲突这一环境影响。该测试使用协议 Fake worker，没有加载真实模型。原始全量命令并非全绿。

补齐新增边界测试后，使用独立模型锁名的第二轮全量为 2639 passed、27 skipped、2 failed。失败项 `test_default_factory_selects_process_backend`、`test_async_factory_loads_once_with_cuda_and_closes_model` 直接断言默认模型名，被测试进程的环境覆盖影响；移除该覆盖后单独复核为 2 passed。没有修改客服实现或测试来绕过断言，没有停服或宣称一次完整全量通过。后续可将原四项协议测试的模型锁名在各自夹具内隔离，避免影响默认配置测试。

复现关键命令（PowerShell）：

```powershell
# 仓库根目录
$env:VUE_CHECK_COMPILER_ROOT='D:/VibeForge/yu-ai-code-mother-frontend'
node --test scripts/tests/vue-component-contract.test.cjs scripts/tests/vue-anchor-check.test.cjs scripts/tests/vue-event-check.test.cjs
mvn -q '-Dtest=VueProjectBuilderTest,VueComponentContractCheckTest,VueAnchorCheckTest,VueEventCheckTest' test

# ai-service 目录；默认全量可能受正在运行的重排器独占锁影响
uv run pytest -o addopts='' --tb=short
uv run python -m compileall -q src
uv lock --check

# 仅对原四项协议/锁测试使用独立锁名；不要用于默认模型名断言测试
$env:AI_SERVICE_RAG_RERANKER_MODEL='vue-quality-protocol-'+[guid]::NewGuid().ToString('N')
uv run pytest -o addopts='' tests/test_reranker.py::test_spawn_worker_normal_result_and_unavailable_protocol tests/test_reranker.py::test_spawn_worker_timeout_shutdown_terminates_permanent_child tests/test_reranker.py::test_large_blocked_ipc_send_does_not_block_loop_and_aborts_child tests/test_reranker.py::test_gpu_owner_lock_rejects_same_key_then_releases -q
Remove-Item Env:AI_SERVICE_RAG_RERANKER_MODEL
uv run pytest -o addopts='' tests/test_reranker.py::test_default_factory_selects_process_backend tests/test_reranker.py::test_async_factory_loads_once_with_cuda_and_closes_model -q

# 可选：显式数据库验证，完成后移除当前进程变量
$env:AI_SERVICE_POSTGRES_INTEGRATION='true'
uv run pytest -o addopts='' tests/test_verification_history.py::test_real_postgres_upsert_preserves_and_replaces_private_history -q
Remove-Item Env:AI_SERVICE_POSTGRES_INTEGRATION
```

## 四、生效步骤与交付边界

1. 选择无活动生成的维护窗口，排空当前任务，再同步部署本次 Spring 和 Python 代码并重启两端；11 月 23 日未重启，11 月 24 日本地真实验收已启动两端，见第六节。
2. 不覆盖现有私有配置；本轮无新增配置项、无表迁移，继续使用既有共享令牌和 checkpoint 配置。
3. 检查 Python `/health/live`、`/health/ready` 及 Spring 基础接口，再从正常业务入口创建专用测试应用，验证新请求的检查摘要和逐轮记录。健康接口本身不证明真实模型或业务功能可用。
4. 旧失败请求不会自动恢复；不能通过修改任务台账、重置代际或手工补文件冒充平台修复成功。Vue 仍无版本回滚保证，失败前的源码/dist 可能已改变。

## 五、下一轮优化顺序与验收条件

| 优先级 | 工作 | 验收要求 |
| --- | --- | --- |
| P0 | 在专用应用上进行受控真实生成与修复对照 | 运行前明确模型总调用/费用上限；记录每阶段工具数、读写比例、重复读取、触发原因、轮次、错误码和耗时；同时保留失败样本，不以单次 PASS 宣称成功率提升 |
| P0 | 浏览器功能验收 | 验证加入购物车、规格/数量、金额、删除、抽屉开关，以及每个导航/CTA 的实际目标；桌面和窄屏分别记录结果，不能以 npm/Reviewer PASS 代替 |
| P1 | 校准首次写入前阈值和提示投影 | 比较保护触发前后的真实修复轨迹，关注必要读取被误停、24 KiB 模型历史省略旧结果造成的重读，以及模型是否在纠偏后完成有效修改；证据充分后再调阈值 |
| P1 | 扩大事件与导航覆盖 | 先增加误报/漏报样本，再讨论可证明的中间转发、作用域和动态导航支持；默认透传疑点继续保持提示级，不通过压低跳过率制造覆盖假象 |
| P2 | 拆分构建修复与质量修复预算 | 在真实成本/收敛数据基础上讨论预算预留或分阶段额度；本轮仍两次/40次，不直接提高上限掩盖空转 |
| P2 | 面向用户展示可操作失败摘要 | 评估稳定错误分类与检查覆盖的前端表达，保持公共 SSE 兼容；Reviewer 正文继续按敏感/不可信内容处理，不直接外传 |
| P2 | 重排器测试锁隔离 | 将独立锁名限制在协议测试夹具，保留默认配置断言，消除与运行中服务的竞争；独立干净环境再次完成全量回归 |

下一轮完成标准应包含真实业务运行记录和浏览器操作证据。代码测试通过只证明本轮机制及边界符合测试，不证明历史应用已经修好，也不证明全部生成场景收敛。

## 六、2026-11-24 本地真实验收

### 环境与授权范围

用户明确授权自行启动 Python、Java、Docker 并进行真实验收。使用已提交的 `de94f38`，保留真实私有配置；未覆盖 `.env`、未改表、未重置任务或代际。通过正常业务接口与已有有效登录会话创建专用应用，不修改原失败应用。

Docker Desktop 首次启动因 `dockerInference` 残留重解析端点失败。停止 Desktop 相关进程，核对本地 `Docker/run` 父目录后将其整体改名保留，再启动成功；未恢复出厂设置、未删除容器或卷。PostgreSQL、Milvus standalone、etcd 已启动，PostgreSQL 和 Milvus 健康；MySQL、Redis 使用本机已有服务。Spring 在 8123、Python 在 8000、独立前端 Vite 在 5173 启动。Python 验收前 `/health/ready` 为 `ready/checkpoint=true`。

实际模型为 `deepseek-chat`，保留两次修复、40 次共享工具额度、8192 输出配置。使用本地预算代理和同一持久账本，最多 60 次供应商请求，失败/取消也计入；未通过重启换账本。创建应用消耗 2 次，本次生成消耗 29 次，合计 **31/60**。此计数不是模型工具调用数，也不是金额统计。终态后停止验收代理，Python 重新按原 `.env` 的正常供应商配置启动，不将剩余额度代理留在日常调用链。

本地证据保留在忽略目录 `tmp/quality-acceptance-20261124/`：SSE 报告、模型计数、服务日志、桌面/窄屏截图。该目录含私有会话，禁止提交或整体分享；交接仅记录安全摘要。

### 真实请求与逐轮结果

- 应用：`482647272084541440`，`VUE_PROJECT`。
- 请求：`2d8fb280-4ef8-4796-b9e7-050cbf9f0c79`。
- 需求：青屿服装电商，三款商品 199/299/399 元；S/M/L 默认 M；同商品同尺码合并、不同尺码分行，数量、金额、删除、抽屉与导航；纯色块、不配图，桌面与 390px 窄屏。
- SSE 耗时 **124.812 秒**，北京时间约 09:29—09:31；1 个 `business-error`、0 个 `done`、0 个非法帧，只有 1 个终态。
- 业务台账：`FAILED / QUALITY_REVIEW_FAILED`，心跳 `TERMINAL`，Vue 发布状态 `NOT_VERSIONED`。不是成功生成，也没有自动重试同一需求追求绿灯。

| 轮次 | 本阶段读/写工具 | 累计工具 | 校验与构建 | Reviewer |
| --- | --- | --- | --- | --- |
| 初始生成 0 | 0 / 13 | 13 | 均通过 | REPAIR，4 项，FUNCTIONAL_ERROR |
| 修复 1 | 18 / 5 | 36 | 均通过 | REPAIR，2 项，FUNCTIONAL_ERROR |
| 修复 2 | 2 / 2 | 40 | 均通过 | REPAIR，2 项，FUNCTIONAL_ERROR |

PostgreSQL `ai_workflow_status.status_payload.verification_history` 实际保留 9 项，覆盖三轮的产物校验、构建、审查，最终记录为 round=2/toolCount=40。只按本请求查询，未运行清理或修改记录。摘要能证明轮次与错误类别，不能还原两项 Reviewer 理由正文。

修复 1 在累计 25 次工具、阶段 12 次读取且 0 写入时出现 `PREWRITE_READ_WARNING`，日志重复读取计数为 5；随后发生 5 次成功写入。累计 36 次工具时以 `LOW_TOOL_BUDGET` 交回验证，为下一轮留下 4 次。修复 2 用完 4 次后再次交回验证，仍质量拒绝。真实覆盖了“纠偏后继续写入”分支，**未覆盖** `VUE_REPAIR_READ_STALLED` 终止分支；不能仅凭这条轨迹断言纠偏导致了写入或提高了成功率。

三轮 Spring 检查均实际执行：属性 `checkedComponents=8/checkedUsages=6/skippedUsages=1`；事件 `checkedUsages=4/skippedUsages=3`，提示数依次 3→1→0，无硬阻断；锚点均为 `DYNAMIC_PAGE`，`checkedLinks=0`。事件提示消失后运行时仍然出错，表明名称匹配不证明载荷兼容；动态导航仍需浏览器验证。

### 浏览器实际验收与阻断项

只读取、操作失败后留存的构建产物，未手工补源码或重新发布。初始静态根地址 `/api/static/vue_project_482647272084541440/` 空白：HTML 引用 `./assets/...`，相应资源返回 404；`/dist/assets/...` 返回 200。改为源码控制器支持的 `/api/static/vue_project_482647272084541440/dist/index.html` 后页面正常呈现。这是独立的根地址资源解析问题，不是这次 Python 质量终态的原因；尚未修复，也未将它扩大为前端实际预览地址一定错误的结论。

| 浏览器操作 | 实测结果 |
| --- | --- |
| 三款商品与价格、默认 M | 页面呈现正确；切换第一款 S，选中状态改变 |
| 桌面 M 加入购物车 | 失败，抽屉不自动打开，计数仍为 0；捕获 `TypeError: Cannot read properties of undefined (reading 'id')` |
| 窄屏 S 加入购物车 | 同样未加入；手动打开后显示共 0 件、合计 ¥0 |
| 购物车打开、关闭、再打开 | 可操作；空态可见，关闭后 `.cart-root` 无 `is-open` |
| 商品导航、品牌故事、首页、故事区查看商品 | 实际滚动到相应区块；桌面商品区顶距 76px、故事区约 0px、首页 0px。此检查只确认滚动，不声称标题完全不被固定导航遮挡 |
| 立即选购 | 点击后发生滚动；随后切换窄屏导致几何变化，未单独固定桌面最终位置，不列为完整通过 |
| 响应式横向溢出 | 默认 1280px 截图视口，DOM clientWidth/scrollWidth 均 1265；390×844 override 下均 375（扣除滚动条），抽样未见页面横向溢出；结束恢复默认视口 |
| 数量合并、分尺码行、数量下限、398/597/896 元路径、删除与非空持久性 | 加入入口被阻断，未能验收，不能标为通过 |

源码只读核对定位了实际加入失败：`ProductCard.handleAdd` 发出 `{id, size}`，`ProductSection` 原样转发，但 `App.handleAddToCart` 解构 `{product, size}` 并读取 `product.id`。事件名称 `add → add-to-cart` 一致，载荷结构却断裂。不能将该观察冒称 Reviewer 原始两项意见，也不能据一次样本评价整体误报率。

截图：`cart-failure-desktop.jpg`、`cart-failure-mobile.jpg`，保留在上述本地证据目录。没有为取得通过而手工修改生成项目，也没有提高模型/工具/修复额度。

### 下一轮调整后的优先级

1. **P0：事件载荷与接收方契约样本。** 先将 `{id,size}` 与 `{product,size}` 的真实失败加入离线回放，覆盖直接传递和容器原样转发；明确有限静态可证明范围、动态跳过与误报边界，再决定可信检查是否可阻断。Reviewer 应对照发出表达式与处理函数解构，但提示不视为确定性保证。
2. **P0：为第二次修复预留可用预算。** 本例第一修复消耗 23 次（18 读/5 写），第二修复仅余 4 次；应设计阶段预留和提前交回策略，先做真实失败轨迹回放，记录必要读取误停与修复机会损耗，再进行受控对照。保持原总额度，不直接扩大预算。
3. **P0：独立修复静态根地址相对资源解析。** 增加根入口、dist 入口和相对 JS/CSS 的 HTTP 回归，确认独立前端使用的实际 URL；避免只验证 HTML 200。此项本轮未改代码。
4. **P0：再次真实生成与浏览器算术验收。** 修复平台策略后，通过正常入口使用同一预算账本的剩余授权范围；记录失败样本，只有加入入口恢复后才继续验证合并、数量、金额、删除。不得手工改此失败产物冒充平台修复。
5. P1：补充真实读取停滞终止分支及检查环境失败的受控验收；P2 重排器测试锁隔离仍未完成。11 月 23 日全量测试环境失败记录继续保留，本次仅运行真实链路观测，没有重跑或宣称全量回归通过。

本次真实验收结论为：服务与链路已运行、新诊断和纠偏可观测，但质量修复仍未收敛，购物车功能验收失败。原请求不会因新请求验收自动修复。

## 七、2026-11-24 第二轮优化

用户确认按“有限事件载荷建议、下一轮修复预算预留、静态预览路径”范围执行，并明确将应用生成输出额度提升到 40,960。本节只描述本轮实现与本轮证据，第六节真实失败记录继续有效。设计与执行清单位于 `docs/superpowers/specs/2026-11-24-generation-quality-round-two-design.md` 和对应 `plans` 文件。

### 已完成的机制

| 问题与触发 | 本轮实现 | 边界 |
| --- | --- | --- |
| 生成输出额度需扩容 | 新增 `AI_SERVICE_GENERATION_MAX_TOKENS=40960` 默认值；HTML/MULTI_FILE/VUE_PROJECT 同步生成、修复及静态文本流使用请求级覆盖，输入审核和实际完整消息预算按同额度预留 | 普通 `MODEL_MAX_TOKENS=8192` 不变；输入语义审核1024、辅助重写独立；默认上下文65536减输出40960及协议4096，保守输入空间20480；24 KiB工具历史不变。不静默降额、切模型或提高上下文默认值 |
| 事件名匹配仍可能载荷断裂 | 可信脚本事件协议v2新增独立载荷覆盖计数与 `MISSING_DESTRUCTURED_KEYS` 线索；Java严格校验、组合到构建摘要，Reviewer对照实际源码判断 | 仅字面对象、最多两跳单参数原样转发、接收函数顶层对象解构；至多4条、每条4键。只作建议，不直接阻断；动态、展开、默认、类型、不完整等保守跳过，不模拟原型链 |
| 首次修复耗尽工具，下一轮只余4次 | 正式修复成功写入且批次完整、无待写文件/配图后，尚有下一轮时剩余不多于12触发 `NEXT_REPAIR_RESERVE`，末轮沿用4及 `LOW_TOOL_BUDGET` | 40次共享工具/两次修复不变；同批照常执行，因此可能跨过12；旧状态缺轮次按末轮。不是严格保证12次，也不保证足够或收敛，可能提前消耗验证机会 |
| Vue根URL的HTML引用相对assets404 | 工程根目标不存在时回退既有dist资源，保持显式dist入口兼容；只有缺失index入口可触发构建，未知资源仍404 | 原有工程根文件优先；HTML200不代表功能通过。直读和回退共用规范路径沙箱，拒绝穿越/反斜杠/冒号及可解析的逃逸链接；未承诺抵御任意文件系统替换竞态 |

没有修改前端仓库、私有 `.env`、共享密钥、数据库schema或生成产物来伪造修复结果。Spring与Python需要同步重启；旧请求不会自动恢复，Vue仍无版本回滚保证。

### 原故障离线回放与覆盖校正

原失败项目只读执行 `component-contract-check.cjs --events`，得到名称覆盖4/跳过3、载荷覆盖1/跳过10，定位 `src/App.vue -> ProductSection @add-to-cart` 缺 `product`。最初简化夹具没有原商品列表的 `v-for`，真实回放暴露了漏报；随后加入完整三文件夹具，并为载荷建议建立独立有限循环遍历。仅支持单变量 `in/of` 单标识符、别名不遮蔽组件导入/顶层函数/emitter及直接Identifier监听；复杂循环、slot与遮蔽继续跳过。名称硬门禁仍跳过循环作用域，没有扩大硬判错范围。该样本命中不代表通用数据流分析或误报率已验证。

### 本轮验证记录

- 针对性Python回归286项通过，包含SDK实际HTTP载荷断言：生成/修复发送 `max_completion_tokens=40960`，并发客服仍8192；路由、审查、重写和输入审核额度分别保持原约束。
- Node props/事件/锚点合计61项通过，0跳过；Java相关7组测试61项，其中60通过、1因未设置 `VUE_COMPONENT_INTEGRATION` 跳过，0失败。Java先执行干净编译及组合测试，再在最终事件协议改动后重跑组合测试。未把跳过项标为通过。
- `compileall` 与 `uv lock --check` 通过。首轮Python全量19失败/2649通过/27跳过，失败由18个假客户端未接受新增请求参数、1个新增checkpoint夹具缺requestId引起，已修正夹具。为避免运行中重排器与测试竞争GPU锁，本次暂时停Python服务后重新跑完整测试；没有改变客服默认配置或跳过失败用例。
- 最终全量收集2695项，2667通过/27跳过/1失败；唯一失败为未修改的 `test_reranker.py::test_timeout_returns_promptly_but_holds_permit_until_gpu_work_finishes`，在恢复推理时触发30ms超时。原样单独复跑1项通过（0.23秒），未修改该测试或客服代码；仅能说明未单独复现，不能断言故障已排除。本轮全量仍未全绿，后续需隔离时序抖动。生成专项已通过，不把专项通过替代完整结果。
- 供应商通过同一持久预算代理实际接受40960参数：HTTP200、finish_reason=stop，prompt16/completion1/total17。仅是参数接受探测，不是40k输出或质量证明；累计账本由31增为32/60，未重置。
- Spring重启后的真实HTTP：原失败应用根入口200、引用的相对JS200/CSS200；浏览器从原先空白的根入口显示三款商品及正常页面。未以旧故障产物的页面呈现代替购物车功能通过。

### 第二轮真实业务请求与浏览器结果

继续在专用应用 `482647272084541440` 上从正常Spring SSE生成入口发送明确的最小购物车修复需求，保留当前布局、商品、尺码和不配图策略；没有直接改生成源码。新请求为 `412bfd81-6ed9-4cf5-9389-75f8c15f36f1`，本地约10:13执行，31.343秒结束。账本由32增至40/60，本次8次供应商调用，剩余20；未再次生成追求PASS。

**请求失败：`QUALITY_REVIEW_INVALID_OUTPUT`。** SSE为1个business-error、0个done、0非法帧；业务台账FAILED、心跳TERMINAL、Vue为NOT_VERSIONED。模型审查固定日志为INVALID_OUTPUT、615字符、finishReason=STOP；未保存理由正文，不能由现有日志判断具体JSON/schema错误或把它归因于输出长度。无自动重试或绕过严格审查。

PostgreSQL只读查到本请求两条验证历史：round0/toolCount5，阶段读取4/写入1，产物校验PASS、构建PASS。审查格式异常没有形成新的quality_review历史项；当前摘要不能补全异常内部现场。真实Spring事件v2日志为名称checked4/skipped3、payloadChecked1/payloadSkipped10、payloadAdvisories0；原product缺键线索消失，但这本身不是功能证明。本次未进入正式修复子图，**未真实覆盖12次预留触发分支**，该策略目前由单元/工作流/恢复测试覆盖。

Vue失败前已写入并构建，浏览器仅验收其留存产物，结果如下；不能据此将请求改称成功。

| 操作 | 本轮实际结果 |
| --- | --- |
| 同款同尺码加入两次 | 白衬衫M合并为2件，398元 |
| 不同尺码分行 | 再加白衬衫S为3件、597元；M/S单独两行 |
| 跨商品累计 | 再加针织衫M，共4件、896元 |
| 数量增加、减少、下限 | M增至3时共5件1095元；减回1时共3件697元，减号禁用，数量未降到0 |
| 关闭再打开 | 3件697元内容保留；关闭时drawer的aria-hidden=true，重开可操作 |
| 390×844窄屏删除与空态 | 删除S后498元，再删白衬衫M后299元，最后删除针织衫后0件0元；窄屏重新加入S得到1件199元 |
| 窄屏布局 | DOM clientWidth/scrollWidth均375（扣滚动条），抽样无横向溢出；最终截图再次显示4件896元，结束恢复默认视口 |
| 窄屏导航抽样 | 品牌故事滚动后story顶距约54px；故事区“查看商品”后products顶距约76px；未对全部CTA、焦点管理或所有固定导航遮挡完成验收 |

截图位于忽略目录 `tmp/quality-acceptance-20261124/cart-round-two-desktop.jpg`、`cart-round-two-mobile.jpg`；安全SSE摘要为 `round-two-repair-report.json`。同目录含私有会话，不提交或整体分享。验收后排空本请求，停止预算代理，Python恢复原 `.env` 供应商配置；交付前检查live/ready通过、checkpoint=true，Spring8123/Python8000/前端5173在监听，代理18001不再监听，Docker PostgreSQL/Milvus健康。生效配置为生成40960/普通8192/共享工具40/修复2，本次未改私有配置。

因此，本轮已验证购物车载荷修复后的主要算术路径及静态根资源修复；**端到端请求仍失败**，不能声称本次生成成功、整体成功率提升或全部质量问题已解决。

### 下一轮工作

0. P0：为严格Reviewer解析失败增加有界固定原因诊断（如JSON/schema/判定组合），并让异常阶段留下安全验证记录；继续不保存供应商理由正文、不放宽JSON契约、不通过静默重试掩盖错误。先固定真实INVALID_OUTPUT样本的安全结构夹具，再考虑修复方案。当前只有固定INVALID_OUTPUT/长度/结束原因，具体错误未定位。
1. 扩充多个真实生成/修复样本，在固定总费用与同一账本限制下比较初轮和修复工具分配、必要读取误停、载荷建议误报/漏报。当前单样本及离线测试不足以声称总体成功率提升。
2. 购物车之外，补充数量输入、规格切换、事件多参数、动态转发和导航的浏览器功能验收。先固定误报/漏报夹具，再讨论扩大静态建议范围；不得直接把当前建议升级为硬门禁。
3. 校准12次预留阈值与24 KiB历史投影之间的关系，保留原总工具/修复上限。输出40,960减少默认上下文可用输入，应记录真实上下文预算拒绝与成本，不能靠输出额度扩容掩盖修复空转。
4. 重排器测试夹具锁隔离、真实首次写入前停滞终止、环境失败受控验收和前端稳定失败摘要仍为后续工作；自动恢复、Vue回滚和全工程交互门禁仍未实现。
