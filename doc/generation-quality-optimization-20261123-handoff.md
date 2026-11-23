# 生成质量优化交接（2026-11-23）

## 交付状态

本轮已实现修复首次写入前的重复读取保护、有限 Vue 事件契约检查、构建检查摘要组合、逐轮安全诊断，以及生成/审查/修复提示补充。Python 和 Spring 源码均有变动，独立前端仓库未改动。默认仍为两次修复、40 次共享模型工具调用。

当前交付为代码及测试验证；没有重启运行中的服务，没有调用真实生成模型，没有执行浏览器业务验收，也没有手工修改失败应用。因此不能宣称线上问题已消失或生成成功率已经提升。

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

1. 选择无活动生成的维护窗口，排空当前任务，再同步部署本次 Spring 和 Python 代码并重启两端；本轮没有执行重启。
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
