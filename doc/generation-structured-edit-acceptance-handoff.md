# 结构化编辑真实验收交接

## 持续维护的硬性要求

每轮优化结束后，必须在给用户的最终回复中用文字总结，不能只给提交哈希、测试数字或文档链接。总结必须包含：

1. 本轮优化了什么，原先为什么需要优化，以及什么具体使用场景会出问题。
2. 改动如何缓解该问题、本轮实际验证结果及尚未解决或未验证的边界；区分历史证据、Fake/受控测试与自然模型/浏览器验收。
3. 下一轮建议优先优化什么，为什么值得做，不处理时什么场景仍可能失败；建议保持在用户当前授权范围，不因未覆盖项自行扩展任务。
4. 本轮提交哈希、是否推送、真实模型调用增量和必要的环境收尾说明。若受阻或仅完成评估，也必须解释原因与下一步。
5. 必须解释当前优化具体如何实现：用普通语言说明触发条件、判断依据、处理步骤、涉及的关键模块，以及为什么采用这种实现；不能只说“已优化”或罗列文件名。清楚区分本轮新增机制和已有机制，并解释什么情况不会触发或仍可能失败。

若下一步涉及多个可行方案，必须先向用户说明各方案的实现方式、收益、代价、风险和推荐理由，并等待用户确认后再实施所选行为。确认前可继续源码核对、只读证据分析和方案文档整理，不擅自选择并修改生产行为。此要求来自用户 2026-11-12 的明确指示。

同步将上述结论追加到本交接文档的本轮记录，保留失败证据，不把计划写成已完成。此要求自 2026-11-12 起适用于后续每轮维护。

日期：2026-11-11。承接 [可视化结构化编辑交接](generation-structured-edit-handoff.md)。本文记录本轮本地真实模型与浏览器验收，不能将上一轮离线验证结果视为本轮重新执行。

> 本轮最终账本为 **60/60 次**，剩余 0 次；已停止真实模型验收，不能追加调用或另开账本绕过授权。

> 第 1～7 节保留 `9f805d0` 的历史真实验收事实；后续同日离线优化见第 8 节，不能混为重新运行的真实验收。

## 1. 结论与验收范围

本轮已在隔离应用上验证真实 Spring → Python → 模型 → 工具/发布 → SSE 链路。MULTI_FILE 和 VUE_PROJECT 的文字颜色修改，在本轮固定样例中达到目标，页面文字、图片与计数操作保持。HTML 字号修改虽然成功发布且变为 32px，却同时补做了前一次失败的颜色修改，因此不能判定单属性编辑通过。HTML 颜色、对齐以及 MULTI_FILE 背景色未完成成功发布。

这是一组局部验收结果，尚未完成三类产物的四属性矩阵，也不证明任意 DOM 路径能可靠映射源码或页面视觉质量稳定。HTML 与 MULTI_FILE 的初始页面来自真实模型生成；Vue 初始页面是通过 Spring 工具网关写入并构建的手工 fixture，后续颜色修改才使用真实模型。不得描述成“三类应用均完成真实模型从零生成”。

固定样例包含标题“星光实验室”、说明文字、一张内嵌山景 SVG 图片和点击后由 0 变为 1 的计数按钮。目标元素为 `#edit-target`，提交的 selector 由真实 DOM 父子路径构造。初始属性为文字颜色 `#223344`、背景色 `#eef2f6`、字号 `24px`、左对齐。

## 2. 属性结果矩阵

“通过”仅表示本轮样例的目标属性与已记录保持项通过；“未测”不能推断成功或失败。调用数包含该请求中的审核、生成、审查和修复，不等于编辑次数。

| 产物类型 | 文字颜色 `#123456` | 背景颜色 | 字号 `32px` | 对齐 `center` |
| --- | --- | --- | --- | --- |
| HTML | 失败；真实 AppChat 表单提交，7 次调用，`done=0`、`failed=1`、预览刷新 0 次 | 未测 | 范围保持失败；3 次调用，`done=1`、刷新 1 次，字号达到 32px，但颜色也从 `#223344` 变为 `#123456` | 失败；同一 UI 检查出现两个请求，共 14 次调用；可观察结果 `done=0`、`failed=1`、刷新 0 次 |
| MULTI_FILE | 通过；3 次调用，唯一成功终态；实际颜色为 `rgb(18, 52, 86)`，其他三属性、文字、图片、计数操作保持 | 失败；6 次调用，唯一 `GENERATION_FAILED`，旧产物属性及计数操作保持 | 新规则复测未通过；3 次调用，审查 REPAIR，修复被额度拦截，唯一失败终态，旧产物保持 | 未测 |
| VUE_PROJECT | 通过；6 次调用，唯一成功终态；实际颜色为 `rgb(18, 52, 86)`，其他三属性、文字、图片、计数操作保持 | 未测 | 未测 | 未测 |

MULTI_FILE 颜色结果报告耗时 5.610 秒，Vue 颜色 8.594 秒，MULTI_FILE 背景色失败 14.329 秒。这些是单次 HTTP/SSE 观测耗时，不是性能基准。HTML UI 检查没有同口径耗时报告，不作横向速度结论。

HTML 字号的最终快照与初始快照对比，除字号外，颜色也变化；文字、图片和计数按钮仍可用。此前失败的颜色需求仍在历史中，后续模型将它一并实现，暴露了“历史需求仅作背景”边界不足。这个失败应保留为回归样例，不能因为任务台账成功或审查 PASS 而改记通过。

HTML 对齐检查期间，前端使用开发服务器及临时 UI harness，观察到两个请求。HMR 或检查页重载是待排查因素，目前证据不足以归因生产前端重复提交；也不能把两次请求合并成一次模型调用。后续复测应固定前端构建、确认单次提交并关联 requestId。

## 3. 预览、终态与取消

| 场景 | 已记录证据 | 结论边界 |
| --- | --- | --- |
| HTML 颜色失败 | 真实 AppChat 属性表单 DOM 点击；旧预览保持；失败 1 次、刷新 0 次 | 该失败场景未刷新预览 |
| HTML 字号成功 | 真实 AppChat 表单；成功 1 次、刷新 1 次，最终字号 32px | `oldPreservedDuringGeneration=false`，不能声明此场景生成期间旧预览检查已通过 |
| HTML 对齐失败 | 旧预览在进度回调中保持，`progressCount=2845`；失败 1 次、刷新 0 次 | 同检查存在两个请求，不能作无重复请求证明 |
| MULTI_FILE 背景色失败 | 失败后独立浏览器快照与上次成功颜色快照一致，图片加载、计数 0→1 | 证明所观测旧产物保持；没有测量 AppChat iframe 刷新次数 |
| HTML HTTP 取消 | 1 次调用，取消 API 返回 `STOPPING`；SSE 唯一 `CANCELLED`，同 requestId 台账为 `CANCELLED`；取消后快照与字号修改后快照一致 | HTTP 取消与独立新浏览器快照，不是真实 UI“停止”按钮验收，也不是取消副作用即时停止证明 |

HTML 取消报告耗时 0.328 秒，未收到源码字符；`publicationState=NOT_CONFIRMED`。取消后页面仍为颜色 `#123456`、字号 `32px`、背景 `#eef2f6`、左对齐，文字与图片保持，计数仍可从 0 变为 1。

SSE 成功帧本身没有 requestId 的场景，运行器通过“最新任务”查询补充 requestId，并明确标记 `latest-task-query-unverified`。最新台账不是原子快照，不能声称已由 SSE 与台账严格关联。失败/取消帧携带 requestId 时，报告以 `business-error` 为来源。台账 `COMMITTED` 不等于当前活动版本；Vue 为 `NOT_VERSIONED`，没有静态发布的同等回滚保证。

## 4. 本轮发现与修改

### 4.1 PostgreSQL 状态投影遗漏准备诊断

HTML 初次真实生成消耗 2 次调用后失败，原因是 checkpoint 状态投影新增了 `preparation_diagnostics`，但 PostgreSQL `STATUS_FIELDS` 未允许该字段。一次重试在模型调用前失败，额外调用为 0。修复后 HTML 初次生成消耗 4 次调用成功，MULTI_FILE 初次生成也消耗 4 次调用成功。

修复位置为 `ai-service/src/ai_service/infrastructure/postgres_checkpoint.py`：允许该可选字段，并用 `safe_preparation_diagnostics` 进行固定字段投影；无效诊断省略，不能让诊断阻断原有状态写入。没有新增 SQL 表或迁移，不把状态投影描述成完整 checkpoint 脱敏。完整 checkpoint 仍可能含业务上下文。

相关 PostgreSQL 验证已执行 19 项，其中 2 项使用真实 PostgreSQL。失败日志还出现 `another command already in progress` 清理警告，本轮未修复，不能声称 PostgreSQL 并发清理问题已解决。

### 4.2 审查观测补充

`models/openai_compatible.py` 增加固定字段日志 `quality_review_result`：`verdict` 为 `PASS`、`REPAIR` 或 `INVALID_OUTPUT`，另记录 `responseChars` 和白名单 `finishReason`。原布尔判定未改变，仍仅规范化后的完整响应等于 `PASS` 时通过；没有新增自由文本拒绝理由，也不记录提示词、候选源码或模型响应原文。

本轮曾用一个额外真实调用诊断 HTML 颜色失败。该调用修改了输出指令和 `max_tokens`，而且缺少原审查中的 validation/build 信息，得到 JSON 形式的 PASS。它不是等价回放，不能证明生产审查误判，也不能以该回答绕过既有质量门槛。

### 4.3 当前单属性范围规则

针对 HTML 字号补做历史颜色的真实失败，本轮新增 `structured-edit-scope` v1 本地规则：当前结构化意图只授权本次属性修改，历史需求只能用于背景理解，不补做旧失败修改。规则须进入生成、审查及修复的既有准备结果，保持原始 prompt、editIntent 和预算边界。

规则仅由校验后的顶层意图且前端规则开关开启时添加；自由 prompt 或 metadata 不能触发。缓存继续复用既有准备结果，不回填新规则。Fake 测试覆盖三类分支及生成、审查、修复入口，也覆盖开关、版本白名单和原始上下文不变，规格与代码质量审查通过。

最后 3 次调用用于 MULTI_FILE 字号修改：准备日志确认 `structured-edit-scope@1` 已选中，审查返回 `REPAIR responseChars=6 finishReason=STOP`。此时额度到 60，后续修复请求及 SDK 本地重试均由代理返回 429，未向供应商继续转发；SSE 和同 requestId 台账记录失败，耗时 8.093 秒。最终快照仍为 24px、背景 `#eef2f6`，颜色、文字、图片和计数保持。这不能证明新规则已解决范围偏移，亦不能把旧产物保留当成新修改成功。没有在本轮实现源码 CSS 确定性补丁、DOM 到源码的可证明定位或结构化审查理由。

## 5. 验收工具与复用方式

三个工具只用于本地验收，不是生产生成链路。默认 dry-run 不发起真实模型请求；运行器和代理不因失败自动退款或重置调用数。

| 文件 | 作用 | 已完成专项验证 |
| --- | --- | --- |
| `scripts/acceptance_model_proxy.py` | 回环供应商代理；请求前持久化预占、独占账本、总额上限 60；失败/取消仍计数 | 28 项测试 |
| `scripts/structured_edit_acceptance.py` | 单应用 HTTP/SSE 运行器；经正常 Spring 入口提交 canonical 意图，提取脱敏终态、预算、耗时、台账及可选取消结果 | 15 项测试 |
| `scripts/structured-edit-browser.mjs` | 同源 iframe DOM 快照、计算样式、图片状态、DOM 点击计数与 PNG；Chrome headless 无调试端口 | 5 项测试 |

代理计数是“已持久化发送许可”，崩溃窗口可能高估实际到达供应商的请求数；它不是供应商计费对账。所有本轮调用必须复用同一账本，包括应用创建时的路由、失败、诊断和取消。不得删除、编辑或改用新账本绕过 60 次总额度。

本轮 Chrome remote-debugging 方案被自动审批拒绝，实际改用不开放调试端口的 headless DOM/截图检查。DOM 点击不是物理鼠标操作；快照与截图可以支撑样例属性、内容、图片及计数行为核验，不能单独证明所有交互或视觉质量。

### 5.1 私有会话格式

运行器通过绝对路径读取私有 JSON。最小格式如下，所有值仅为占位；私有文件不得提交或打印：

```json
{
  "cookies": {"SESSION": "<隔离测试账号的会话 Cookie>"},
  "proxyToken": "<本轮随机本地代理令牌>"
}
```

代理进程从原始 `Settings` 读取供应商配置，并通过 `ACCEPTANCE_PROXY_TOKEN` 接收本地令牌；AI 服务使用进程环境覆盖模型地址为 `http://127.0.0.1:18001/v1`、模型 key 为本地代理令牌。代理本身必须保留真实上游地址，不能递归指向自己。不要在仓库脚本中硬编码密钥或提交本机 `.env`。

### 5.2 命令模板

以下命令展示参数，不代表允许再次启动真实验收；先核对剩余额度与当前进程，真实调用仍计入同一轮上限。后端仓库根目录可运行浏览器工具测试：

```powershell
node --test scripts/structured-edit-browser.test.mjs
```

在 `ai-service` 执行 dry-run、专项测试和必要全量回归：

```powershell
uv run python ../scripts/acceptance_model_proxy.py
uv run python ../scripts/structured_edit_acceptance.py
uv run pytest tests/test_acceptance_model_proxy.py tests/test_structured_edit_acceptance.py
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

进程令牌已安全装载时，代理启动模板如下；同一轮必须使用已有账本绝对路径：

```powershell
uv run python ../scripts/acceptance_model_proxy.py --execute --max-calls 60 --ledger '<本轮已有账本绝对路径>' --port 18001
```

单次意图提交模板（输出必须位于后端仓库 Git 忽略目录，并使用尚不存在的 JSON 文件名）：

```powershell
uv run python ../scripts/structured_edit_acceptance.py --execute --session-file '<私有会话绝对路径>' --app-id <隔离应用ID> --intent-file '<意图JSON路径>' --output '<忽略目录中的新结果JSON路径>'
```

初始普通生成将 `--intent-file` 换为 `--message-file`；HTTP 取消场景追加 `--cancel-after-seconds <秒数>`。运行器不创建应用、不重试、不直连模型。

浏览器快照需要可通过前端同源代理访问的预览 URL，以及前端仓库已被 Git 忽略的检查页目录。脚本仅创建并删除随机命名的临时检查页：

```powershell
node scripts/structured-edit-browser.mjs --execute --url 'http://localhost:5173/api/static/<实际预览路径>' --output '<后端忽略目录中的快照JSON绝对路径>' --screenshot '<后端忽略目录中的PNG绝对路径>' --harness-dir '<前端已忽略检查页目录绝对路径>' --harness-base 'http://localhost:5173/<对应公开路径>'
```

预览 URL 不允许凭据、查询参数和 fragment；`--harness-base` 必须与预览同源。可用 `--chrome` 指定 Chrome 路径，`--no-click` 只观察而不触发计数。使用 `#edit-target`、`#count`、`#counter` 或 `data-testid="counter"` 的固定样例约定，不把工具当作任意页面通用验证器。

## 6. 调用账本与本地证据

最终 `tmp/structured-edit-acceptance/model-ledger.json` 及场景报告合计为 **60/60**：

| 场景 | 调用数 |
| --- | ---: |
| 三个隔离应用创建/路由 | 6 |
| HTML 初次生成 PostgreSQL 投影失败 | 2 |
| HTML 模型调用前重试失败 | 0 |
| 修复后 HTML 初次生成 | 4 |
| MULTI_FILE 初次生成 | 4 |
| HTML 颜色编辑失败 | 7 |
| 非等价审查诊断 | 1 |
| MULTI_FILE 颜色编辑 | 3 |
| Vue 颜色编辑 | 6 |
| MULTI_FILE 背景色编辑失败 | 6 |
| HTML 字号编辑 | 3 |
| HTML 对齐 UI 检查中的两个请求 | 14 |
| HTML HTTP 取消 | 1 |
| MULTI_FILE 字号新规则复测（后续修复被额度拦截） | 3 |
| 最终合计 | 60 |

本地证据位于忽略目录 `tmp/structured-edit-acceptance/`，包括 `*-result.json`、`*-snapshot.json`、初始快照与 PNG。主要结果可由 `HTML-color-ui-result.json`、`HTML-fontSize-ui-result.json`、`HTML-textAlign-ui-result.json`、`MULTI_FILE-color-result.json`、`MULTI_FILE-backgroundColor-result.json`、`VUE_PROJECT-color-result.json`、`HTML-cancel-result.json`、`MULTI_FILE-fontSize-scope-result.json` 交叉核对。浏览器保持项使用对应初始与结果快照比较。

专用测试应用 ID：HTML `477952435427049472`、MULTI_FILE `477952444251865088`、Vue `477952449779957760`。Vue 正确预览路径为 `/api/static/vue_project_477952449779957760/dist/index.html`；根目录预览的首次空快照不计通过。手工 Vue 基线在 `scripts/fixtures/structured-edit-vue-v1.json`，通过 Spring 工具写入和构建，Python 未直接操作工程目录。

交付前已停止本轮 Spring、Python、限额代理和 Vite 进程，以及本轮专用 Redis 和启动用于验收的 PostgreSQL 容器；保留测试数据和证据，无公网部署。Docker Desktop 及随其启动的其他用户容器未停止或删除，`.env` 未修改。

私有会话、凭据、原始上下文、候选源码、服务日志、模型账本、PNG 及生成项目均不提交。报告已尽量使用固定字段，但页面快照仍包含业务文字和图片地址，仍按本地验收资料保管，不称为可公开的完全脱敏数据。

## 7. 下一步与交付核对

1. 为审查设计有边界的结构化 verdict/拒绝原因，保持拒绝原因与候选分离，禁止日志携带完整提示词或源码。先保存等价请求证据，再分析质量审查与修复；不能用改变提示词的诊断证明原审查误判。
2. 为修复增加无进展检测或原因感知控制，避免同一候选反复送审耗尽预算。必须保持既有修复上限、取消传播和唯一终态，不因诊断扩大真实调用额度。
3. 以 HTML 历史失败颜色被补做的样例复测单属性范围规则。对照基线应是当前活动产物，明确哪些保持项改变；不能只检查目标值或 SSE done。
4. 在独立授权的后续预算内补齐未测属性与三类取消/失败/UI 刷新矩阵。Vue 从零模型生成、真实 UI 停止按钮、部署/下载、跨页定位、无唯一元素和复杂动态页面均未在本轮完整验收。
5. 单独排查 PostgreSQL `another command already in progress` 清理告警，避免与本次状态字段缺失修复混为一项。

本轮最终验证：

- `uv run python -m compileall -q src`、`uv lock --check` 均通过。
- `uv run pytest`：**1194 passed、10 skipped、2 warnings**，176.78 秒；跳过真实资源 opt-in 测试，警告来自 Starlette/AnyIO 与 LangGraph 依赖弃用提示。
- 显式设置 `AI_SERVICE_POSTGRES_INTEGRATION=true`，运行 `tests/test_postgres_checkpoint.py tests/test_postgres_checkpoint_integration.py`：**19 passed**，含 2 项真实 PostgreSQL 测试；其中图节点为 Fake，不能当作完整模型恢复验收。
- `node --test scripts/structured-edit-browser.test.mjs`：**5 passed**。
- 本轮没有修改 Java 或前端生产代码，未重跑 Maven、前端 type-check/build；真实应用构建和浏览器观测仅限上述专用样例。
- 后端与前端分别检查 `git status --short`、`git diff --check`。前端既有客服三文件保留，无本轮前端提交。

最终调用为 **60/60**，最后样例已记录于第 4.3 节。历史真实验收的后端代码、测试及本文同批提交于 `9f805d0`，提交说明为 `fix(ai): 修复准备诊断落库并补充结构化编辑验收`。无部署或推送。

## 8. 后续离线优化：结构化拒绝理由与无进展检测

本节承接提交 `9f805d0`，日期仍为 2026-11-11。按用户授权仅做离线实现与测试，真实模型新增调用 **0 次**，原账本保持 **60/60**，没有启动真实验收代理或修改额度。设计和实施步骤见 `docs/superpowers/specs/2026-11-11-quality-review-progress-design.md` 与对应 plans 文档。

### 8.1 当前实现

- 单 Reviewer 的生产模型返回严格 JSON `verdict`/`issues`，PASS 对应空列表，REPAIR 必须有 1～4 个阻断问题。问题码限定需求缺失、功能错误、技术错误、范围越界四类；每条包含简短摘要、证据和修复建议，字段及总响应有长度上限。裸 PASS/REPAIR、额外字段、重复键、判定矛盾及空理由均明确失败；长度截断/内容过滤也不得放行，不为这些格式错误新增调用。
- 有效拒绝理由通过既有 `qualityReview.repairFeedback` JSON 字符串交给三类产物修复，最多 4000 字符。它是模型诊断数据，不提升为系统指令或新增用户授权；新候选硬校验前清除旧反馈。生产日志只记录固定问题码、数量、判定、响应大小及白名单结束原因；证据/建议不进入公共事件或状态摘要。执行期完整 checkpoint 仍可能包含有界反馈和源码，沿用原访问与清理边界。
- HTML/MULTI_FILE 每个候选在硬校验前计算原文 SHA-256；重复候选或 A→B→A 循环直接 `REPAIR_NO_PROGRESS`，不再验证、审查或发布。只做精确内容判重，空白变化仍视为不同候选，不声称识别语义相同代码。
- Vue 单 Reviewer 与多 Reviewer 首轮起均在成功构建后读 Spring 源码快照。只对明确完整、非空、计数一致且所有文件无截断的快照计算路径/内容指纹，重复时停止送审。文件排序不影响指纹，路径和内容变化会影响指纹；截断/不完整快照不作无进展结论，构建失败仍走原有限修复。快照是网关有界选择，不是全工程相等证明。
- 指纹只在验证子图内保存，不进入父图、修复输入、模型、状态摘要或公共事件。保持原修复上限、共享 Vue 工具预算、取消传播及唯一终态；未增加 Java/前端修改、SQL 迁移、配置项或 SSE 事件类型。

稳定失败码为 `QUALITY_REVIEW_INVALID_OUTPUT`、`QUALITY_REVIEW_INCOMPLETE`、`REPAIR_NO_PROGRESS`。格式错误与真正的模型拒绝明确区分，模型拒绝事实本身也不证明拒绝正确。旧注入/Fake 模型仍兼容严格 bool，false 没有结构化理由，不虚构证据。

### 8.2 验证与限制

新增离线用例覆盖严格协议与长度、日志脱敏、单次调用、取消、三类重复/循环候选、内容变化后再次审查、有界反馈传递、截断快照、原修复上限、静态无效候选、嵌套 checkpoint 的指纹隔离。既有预算与恢复测试改用显式变化的 Fake 源码，固定源码专用于无进展测试，避免以固定快照假装修复有进展。

最初超长参数用例触发 Windows `PYTEST_CURRENT_TEST` 环境变量 32767 字符限制，未进入业务代码；为长输入设置简短测试 ID 后通过。首轮全量失败的旧夹具已更新为严格 JSON 和明确变化的 Vue 快照，保留原准备规则、诊断隔离与修复预算断言。

本次最终验证：

- `uv run python -m compileall -q src`、`uv lock --check`、两仓库 `git diff --check` 均通过。
- 进程显式设置 `AI_SERVICE_POSTGRES_INTEGRATION=false` 后运行 `uv run pytest --tb=short`：**1250 passed、10 skipped、2 warnings**，182.77 秒；警告仍为 Starlette/AnyIO 与 LangGraph 依赖弃用提示。
- 自查补充 Vue 快照/单 Reviewer 抛出 `GenerationCancelled` 的直通边界后，执行 `uv run pytest tests/test_repair_progress.py tests/test_api.py -q --tb=short`：**156 项通过**（含两项新增取消用例），并重新执行 compileall。此专项与全量存在重叠，不能相加作为独立用例总数。
- 源码核对 Java `LangGraphAiGenerationGateway` 继续通过既有 failed/error.code 通道传递稳定错误；未修改静态展示/候选帧协议。未运行 Java 测试，不能将源码核对称为跨服务运行验收。

本轮提交说明为 `feat(ai): 增加结构化审查理由与无进展修复检测`，可用 `git log -1 --format='%h %s' -- doc/generation-structured-edit-acceptance-handoff.md` 查询。没有推送或部署。

本轮没有真实模型、真实 PostgreSQL/MySQL/Redis、浏览器、Maven 或前端构建验收。前端既有客服三文件保持，无前端提交。未证明新 JSON 提示在真实供应商上的遵循率，也未复测第 2 节属性失败矩阵或历史需求补做问题。无进展检测仅能节省精确重复候选的后续调用，不能保证每次修复提升质量；Vue 工程仍无自动回滚。

后续真实验证必须另行明确调用额度并遵守持久化账本，不能继续使用已耗尽预算。优先复测 HTML 历史失败颜色被补做的范围样例，并对照当前活动产物检查文字、图片和交互保持。PostgreSQL 清理告警和未测 UI/取消矩阵仍按第 7 节独立处理。若回退本改动，应整体回退 Python 模型协议、提示词和子图判重逻辑，保留先前代际/仲裁安全机制；不进行数据或前端回退。

## 9. 新授权下的有限真实复测与错误提示完善

本节承接 `2b080ed`，执行日期 2026-11-11。用户明确新增授权最多 **20 次**真实调用（失败、重试和诊断同样计入）；最终使用 **15/20**，剩余 **5 次**未使用。新授权固定账本为 `tmp/quality-review-acceptance-20261111/model-ledger.json`。旧账本仍为 60/60，收尾 SHA-256 与本轮启动前保存的值一致。后续使用余量必须复用此新账本，不能重置为 20 次；超出余量须重新取得授权。

### 9.1 实际应用复测

复用第 6 节三个隔离应用，逐次经真实 Spring SSE 入口、Python 工作流和限额代理执行。模型为本地配置的 `deepseek-chat`。以本轮读取的当前活动产物为基线，保留原历史需求；四次准备诊断均为 `skipped/STRUCTURED_EDIT`，审查实际适配器消息均包含 `structured-edit-scope@1`。没有为了获取通过结果改变审查提示、追加诊断调用或绕过质量门槛。

| 顺序 / 场景 | 本次基线 → 目标 | 调用数 | SSE 耗时 | 结果 |
| --- | --- | ---: | ---: | --- |
| HTML 字号及历史范围 | 32px → 36px | 3 | 9.828 秒 | 目标生效，当前颜色 `#123456`、背景 `#eef2f6`、left 对齐保持 |
| MULTI_FILE 背景色 | `#eef2f6` → `#ddeeff` | 3 | 5.328 秒 | 目标生效，颜色 `#123456`、24px、left 对齐保持 |
| Vue 对齐 | left → center | 5 | 16.453 秒 | 目标生效，颜色 `#123456`、背景 `#eef2f6`、24px 保持 |
| HTML 对齐 | left → center | 3 | 9.593 秒 | 目标生效，颜色 `#123456`、背景 `#eef2f6`、36px 保持 |
| 受控缺陷审查探针（见下节） | 真实 Reviewer + Fake 生成/修复/工具 | 1 | 不计应用耗时 | 返回结构化拒绝，重复候选停止 |
| 合计 | | **15** | | 剩余 **5** |

四个应用样例的审查均返回严格 JSON `{"verdict":"PASS","issues":[]}`、STOP；SSE 都只有一次 done、没有 business-error。每次最新任务查询为 SUCCEEDED，静态产物为 COMMITTED，Vue 为 NOT_VERSIONED。成功 SSE 未携带 requestId，因此运行器仍标为 `latest-task-query-unverified`，不能将最新台账查询称为严格同 requestId 关联证明。

每个样例都采集修改前后同源 iframe DOM/PNG，检查目标四项计算样式、页面文字、图片 src/alt/加载状态、计数按钮启用及 DOM 点击 0→1。目标属性以外的上述观测项保持一致。HTML 对齐的直接基线是本轮字号成功后的 36px 页面。截图人工查看未见这些样例出现明显布局异常，但这些观测不是全页面 CSS/交互相等证明，也不是任意页面视觉质量保证。

HTML 字号样例没有再次补做历史失败修改，说明当前样例通过范围复测；不是证明历史补做问题在所有页面上已消失。Vue 仍基于先前手工工程，不能称为从零真实模型生成验收。

### 9.2 真实拒绝理由与重复候选探针

四次真实应用生成未自然进入 REPAIR。为直接验证拒绝路径，另用独立受控缺陷夹具：需求为启用按钮且每次点击加 1，候选 HTML 的按钮带 `disabled` 且完全没有脚本。仅 `OpenAICompatibleModel.review` 使用真实供应商调用；生成、修复、硬校验工具和 checkpoint 均为既有 Fake，修复固定返回完全相同候选。使用生产审查提示与解析器，没有回放或改写任何历史失败请求。

结果为合法 REPAIR，1 条 `REQUIREMENT_MISSING`，证据指出按钮禁用及缺少脚本，建议移除 disabled 并补充点击计数。完整有界反馈进入 Fake 修复上下文后，同一候选在二次硬校验前被 `REPAIR_NO_PROGRESS` 拦截。真实 Reviewer 调用 **1 次**、Fake 校验 **1 次**、Fake 发布 **0 次**，工作流只有 **1 个失败终态**。这验证了真实拒绝响应与重复候选停止逻辑的组合；不证明真实生成模型能依据理由正确修复，也不属于真实文件构建、Spring SSE 或失败预览端到端验收。

实际应用审查及此探针均在 `_invoke` 调用前保存适配器消息，并保存原响应和元数据；未修改模型提示、温度或输出上限。这些是适配器层私有观测，不是逐字节供应商请求回放，也不能用于断言旧审查误判。

### 9.3 本轮代码与离线验证

- Java 生产逻辑无改动；网关和控制器参数化测试覆盖 `QUALITY_REVIEW_INVALID_OUTPUT`、`QUALITY_REVIEW_INCOMPLETE`、`REPAIR_NO_PROGRESS` 及既有截断码，验证错误码/requestId 保留，控制器仅一次 business-error、无 done 或命名 error。网关使用本地 HTTP Fake NDJSON，控制器使用 Mock Service，不称为真实跨服务失败验收。
- 前端新增 `generationErrorMessage.ts`，提取原有固定码文案并添加三种中文错误原因与重试建议，保留未知错误回退及请求 ID。固定码匹配排除对象原型属性。沿用失败预览控制，不承诺 Vue 源码回滚。前端 README 同步说明；既有客服三个文件未修改或纳入提交。
- `mvn test "-Dtest=LangGraphAiGenerationGatewayTest,AppControllerSseTest"`：**31 项通过**；随后停止本轮服务，`mvn clean -DskipTests compile` 通过。编译仍有现有 Lombok、varargs、过时 API/unchecked 提示。
- 前端 `node --test --experimental-strip-types tests/generationErrorMessage.test.ts tests/optimizePrompt.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts`：**16 项通过**，覆盖失败/取消和迟到成功不刷新预览等既有边界。`npm run type-check`、`npm run build-only` 通过。最初使用 `Object.hasOwn` 超出项目 TS lib，已改为兼容的 `hasOwnProperty.call` 后重新验证；构建保留既有混合导入和大 chunk 警告。
- Python 生产代码未改动。本轮设置 `AI_SERVICE_POSTGRES_INTEGRATION=false`，执行 `uv run pytest tests/test_quality_review.py tests/test_quality_review_diagnostics.py tests/test_repair_progress.py tests/test_api.py -q --tb=short`：**222 项通过**，2 项依赖弃用警告；另经 collect-only 核对数量。`uv run python -m compileall -q src`、`uv lock --check` 通过。未重跑 Python 全量或真实资源 opt-in 测试。
- 两仓库 `git diff --check` 通过；只提交本次测试、错误提示和文档。无 SQL、接口、预算配置或 Python 运行行为变更。

### 9.4 证据、收尾与后续

本地证据位于忽略目录 `tmp/quality-review-acceptance-20261111/`：四个 `*-result.json`、`*-before.json` / `*-after.json` 和 PNG、四份 `review-*.json`、`negative-probe-result.json` 与私有原始证据、验证日志和固定新账本。运行器和浏览器仍为仓库已有脚本，探针及观测启动脚本仅在忽略目录。原始消息、源码、令牌、Cookie 和截图均不提交。

完成全部请求后，已停止本轮 Spring/Python/限额代理/Vite 进程树，以及本轮启动的 `structured-edit-redis-20261111`、`langgraph-postgres` 容器；核对四个服务端口无监听。原有 MySQL 和其他用户容器保留，`.env` 未修改，没有公网部署或推送。

后续优先级：

1. 用真实 UI 故障注入验收新中文错误显示、失败后旧预览保持、迟到 done 不刷新；当前只有分层离线测试。扩展三类取消矩阵，不能把探针失败称为 UI 失败验收。
2. 对自然出现的真实 REPAIR 保存同样的私有原请求证据，检查修复是否遵循具体理由。当前只观察到 4 个真实应用 PASS 和 1 个受控 REPAIR，不能估计供应商协议遵循率、误拒率或节省额度比例。
3. HTML 颜色等未覆盖属性、跨页/动态 DOM、Vue 从零生成、部署/下载继续按第 7 节保留。未改动 checkpoint 清理；本轮日志未检出 `another command already in progress`，不代表该历史问题已修复。

回退本轮只需回退前端文案提取/新增映射及本轮测试文档，不影响 `2b080ed` 的审查协议和判重行为，也不回退产物、数据或用户客服修改。

前端提交为 `3cb74c1 fix(generation): 补充质量审查与无进展失败提示`；后端本节与测试同批提交，说明为 `test(ai): 补充结构化审查真实验收与错误传递验证`，可用 `git log -1 --format='%h %s' -- doc/generation-structured-edit-acceptance-handoff.md` 查询对应哈希。

## 10. 生成界面故障注入与停止预览修复

2026-11-11 承接第 9.4 节第一优先级，本轮真实模型新增 **0 次**。旧账本仍为 60/60，哈希与原记录一致；新授权账本仍为 15/20，剩余 5 次，未启动代理或更改额度。

### 10.1 复现与修复

真实 Chrome headless 中挂载生产 `AppChatView`、Ant Design Vue、Pinia 和 Router，通过 DOM 输入、发送/停止按钮点击执行。业务 HTTP、用户、历史、预览和 EventSource 均为合成夹具；测试 Vite 无 Spring 代理，不加载真实 Cookie，不连接数据库或模型。EventSource 故意允许 close 后继续投递事件，模拟关闭时已排队的迟到回调。

最初 18 项矩阵中有 **6 项失败**：HTML、MULTI_FILE、VUE_PROJECT 各自在停止直接返回 IDLE、或返回 COMMITTING 后查询为 IDLE 时，额外请求一次预览。根因是 `stopGeneration` 和 `waitForGenerationIdle` 两条分支直接调用 `loadPreviewDocument`，绕过仅成功 done 刷新的协调器；旧 iframe 内的交互状态会丢失。三个质量码和 STOPPING→IDLE 路径当时已通过，不能笼统描述成所有失败均会刷新。

本轮移除这两条停止分支的自动预览同步/加载。IDLE 表示执行结束，COMMITTING 不证明发布成功；保留当前预览，提示用户可手动点击“刷新预览”查看最新结果。后台停止请求、状态轮询和聊天回源保留；未改 SSE、生成成功刷新、2000 字符/80ms 流窗口、后端发布协议或 Vue 回滚边界。

### 10.2 可重复运行的浏览器测试

前端新增：

- `scripts/test-generation-ui.mjs`：启动回环隔离 Vite 与合成 HTTP 路由，运行本机 Chrome headless，不开放调试端口，结束时关闭 Vite。
- `tests/browser/generationFailure.html`、`generationFailure.ts`：挂载真实页面，通过生产 API 封装和组件回调消费注入事件，输出逐场景结果。

在前端仓库运行 `node scripts/test-generation-ui.mjs`。默认使用 `C:/Program Files/Google/Chrome/Application/chrome.exe`，其他安装位置用环境变量 `CHROME_PATH` 指定。缺少 Chrome、缺少结果或断言失败返回非零退出码。首次夹具启动遇到 Vue 运行时模板及依赖预构建时序问题，已改为 `h(RouterView)` 和固定依赖预构建/独立缓存，随后取得稳定矩阵结果；这些是测试设施问题，不计作生产缺陷。

最终 **18 项浏览器场景全部通过**：三类生成 ×（三个质量失败码 + IDLE/STOPPING/COMMITTING 三种停止响应）。逐场景核对：

1. 正确应用与生成类型的预览 URL，Vue 指向 `dist/index.html`。
2. 质量失败显示对应中文原因和 requestId；失败/停止后重新允许发送且事件流关闭。
3. 不新增预览请求，原 iframe 和 document 身份不变，预览内计数点击 0→1 的状态保留。
4. 迟到 done、草稿和传输错误不刷新、不污染对话。
5. 同一页面再次发送并成功后，预览恰好刷新一次；重复 done 不导致二次刷新。

以上是实际浏览器组件级故障注入，DOM 点击并非物理鼠标，夹具不是原生网络 SSE 服务；不能称为真实 Spring/Python 取消、数据库仲裁或模型端到端验收。没有验证停止请求被拒绝/网络失败的所有竞态，也未验证真实自然 REPAIR 后模型修复的质量。

### 10.3 验证、证据与后续

- `node scripts/test-generation-ui.mjs`：最终 **18/18 通过**。
- `node --test --experimental-strip-types tests/generationErrorMessage.test.ts tests/generationLifecycle.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts tests/structuredEdit.test.ts tests/optimizePrompt.test.ts`：**27/27 通过**。
- `npm run type-check`、`npm run build-only` 通过；保留原有社区组件混合导入和大 chunk 警告。
- 两仓库 `git diff --check` 通过。后端仅更新设计、计划与本交接文档，未重跑 Maven/Python 或真实资源测试；未修改 Python 行为，因此不更新 AI 服务运行说明。

前端忽略目录 `doc/` 保存 `generation-ui-before.json`（6 项失败）、`generation-ui-result.json`（最终结果）、`generation-ui-before.log` / `generation-ui-after.log`、`generation-ui-unit.log`、`generation-ui-build.log`。这些全为合成数据，本轮没有真实页面截图或模型请求证据。测试启动的 Vite 随脚本关闭，未启动 Spring、AI 服务或容器；前端既有客服三文件仍保留且不纳入提交。

下一步优先检查停止请求失败、跨应用导航及异步回源期间的新请求竞态，并在需要时用真实后端扩展取消矩阵。自然 REPAIR 的修复质量、未覆盖属性及 PostgreSQL 清理告警仍按第 9.4 节保留。真实调用若继续使用剩余 5 次，必须复用原 15/20 账本，不能重置。

回退本轮只涉及前端停止分支、测试夹具和文档；不回退客服修改、数据库、已有生成产物或审查协议。

前端提交 `2a9b858 fix(generation): 停止生成时保留预览并补充浏览器故障回归`。本节随后端提交 `docs(ai): 更新生成界面故障验收与停止预览交接`，哈希可用前述文档 git log 命令查询。未推送或部署。

## 11. 停止失败与异步回源竞态隔离

2026-11-11 承接第 10 节后续。真实模型新增 **0 次**，旧账本 60/60 且哈希保持，新账本仍为 15/20、剩余 5 次。本轮只改前端及文档，不启动 Spring、Python、模型代理或真实资源容器。

### 11.1 已复现与修复的问题

新增可手动释放的 HTTP 响应门闩，控制停止、状态查询、详情、历史和预览响应到达顺序。初版竞态矩阵复现了停止失败丢失流终态、旧停止响应关闭新流、旧详情/历史覆盖新页面或新一轮消息。完善时钟推进和扩展用例后，使用提交前 `2a9b858` 的 `AppChatView.vue` 重跑**同一套最终 54 项测试**，确认 **30 项失败、24 项通过**；测试后立即恢复本轮文件，再跑修复版本 **54 项全部通过**。该对照只替换本轮负责的页面文件，既有客服修改未动。

实现边界：

- 详情、历史、预览请求捕获当前页面与生成轮次，响应成功、异常提示和 finally 写入前复核。详情/历史还核对同类请求序号，防止早发晚到响应覆盖新请求。新一轮开始清理旧加载标志，避免丢弃旧响应后一直显示加载。
- 成功 done 的详情→历史→预览→成功提示各异步边界都复核轮次；失效回调不再借新页面的 appId 继续请求。导航/卸载使已有预览请求失效，已开始但迟到的预览不能重建新 iframe。
- 停止及状态查询固定使用发起时 appId，返回后再次核对 stopId。新页面/新一轮使旧停止结果失效，旧响应不能关闭新事件流、覆盖消息或改变新页面执行状态。
- 停止请求等待期间，流层可能已消费终态并关闭连接；页面现在暂存终态动作。停止被接受则丢弃并按原停止流程确认；停止失败则消费已收到的 done、business-error 或传输错误，或继续尚未结束的原流，不再等待一个不会再次到达的终态。已点击停止的本轮不自动刷新预览；下一轮成功仍走原单次刷新路径。

这些是界面状态隔离，不是服务端取消成功证明、自动恢复或跨请求长期记忆；后台任务可能继续执行，停止请求失败不能据此认定没有后端副作用。SSE 协议、修复/工具预算、2000 字符与 80ms 流展示边界没有改动。

### 11.2 浏览器测试与测试设施修正

原 18 项保留，新增三类应用 × 12 个时序场景，共 **54 项**：停止失败前到达 done、business-error、传输错误，以及停止失败时流仍运行；停止响应/状态响应迟到时导航；详情成功或错误响应迟到时导航；历史回源时导航或开始新轮次；预览请求迟到时导航或开始新轮次。断言新应用标识、消息、事件流和 iframe/document 不被旧响应污染，同时保留停止不刷新与新轮次成功单次刷新检查。

故障夹具首轮曾卡住：Chrome 虚拟时钟会在 HTTP 响应挂起时暂停，不能用同一虚拟时钟等待后再释放门闩。现改为门闩挂起期间用隔离服务器时钟推进观测，释放后再显式推进浏览器定时器，避免把尚未执行的 80ms 展示刷新误判为消息丢失。Chrome 每次使用独立临时配置；退出时释放挂起连接、关闭 Vite并清理本次配置。仍不开放调试端口，不连接真实 SSE、业务库或模型，DOM 点击不是物理鼠标验收。

### 11.3 本轮验证、交付和未测项

- `node scripts/test-generation-ui.mjs`：最终 **54/54 通过**，基线对照 **30 项失败**已留档。
- `node --test --experimental-strip-types tests/generationErrorMessage.test.ts tests/generationLifecycle.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts tests/structuredEdit.test.ts tests/optimizePrompt.test.ts`：**27/27 通过**。
- `npm run type-check`、`npm run build-only` 通过；构建仍有原社区组件混合导入及大 chunk 警告。
- 两仓库 `git diff --check` 通过。后端只改本文与设计/计划，不重跑 Maven、Python 或真实资源测试。前端客服三个既有修改保持且不提交。

前端忽略目录 `doc/` 保存 `generation-races-baseline.json` / `.log`（最终夹具运行旧页面）、`generation-ui-result.json` / `generation-races-after.log`（修复后结果）、`generation-races-unit.log`、`generation-races-build.log`。初版/诊断日志不能替代最终对照结果。测试结束后无本轮 Vite 监听；自动审批拒绝了删除中途强制结束测试遗留的 `doc/chrome-races-q4nA60` 目录，仅返回 `blocked by policy`，因此保留该 Git 忽略目录，没有改用其他工具绕过删除限制。正常完成的测试配置由脚本清理。

后续优先使用真实 Spring/SSE 验证取消失败与终态到达顺序；当前全部新增竞态均为真实浏览器组件加合成接口，不能作为端到端通过。长时间离线/重连、真实多标签并发、更多动态页面以及自然 REPAIR 的修复质量仍未覆盖；PostgreSQL 历史清理告警也未处理。需要模型时继续复用 15/20 原账本，不重置余量。

回退需整体回退本轮页面请求归属和暂存终态逻辑，保留上一轮停止不刷新预览的修复；无数据库、生成产物、审查协议或客服数据回退。

前端提交为 `73220e3 fix(generation): 隔离迟到响应并恢复停止失败后的终态`。后端本文、设计和计划随 `docs(ai): 记录生成界面竞态修复与浏览器验收` 提交，哈希可通过本文 git log 查询。未推送或部署。

## 12. 真实前端、生成发布与停止操作验收

2026-11-11 按用户明确要求执行第 11 节之后的小范围真实验收。本轮使用原授权剩余 **5 次**，账本从 **15/20 → 20/20**；正常修改 3 次、取消场景 2 次。旧 60/60 账本 SHA-256 保持不变。没有换账本或提高额度，后续真实模型验证必须重新明确新增授权。

### 12.1 环境和执行方式

复用 HTML 隔离应用 `477952435427049472` 和原隔离账号；旧登录会话过期后通过真实登录接口更新。启动真实 Spring、Python、原限额代理及前端 Vite，Python 使用原模型 `deepseek-chat`，代理仍绑定 `tmp/quality-review-acceptance-20261111/model-ledger.json`、`max_calls=20`。本机已有 MySQL 和原生 Redis 服务保持运行，本轮只额外启动 PostgreSQL 容器 `langgraph-postgres`。

当前会话没有可用的桌面控制工具，因此使用 Chrome headless 加载真实前端完整应用，包括登录态、路由、权限、AppChatView、可视化选中及属性表单，并通过 DOM 点击“应用修改”和“停止生成”。使用原生 EventSource、真实 HTTP/SSE、Spring 内部工具、模型、文件校验/发布及 PostgreSQL checkpoint；没有注入 Fake 响应或直接调用组件内部函数。被动观察器只统计原生事件、预览 fetch 和停止响应，不改事件内容、提示或供应商参数。浏览器无远程调试端口，DOM 点击不等于物理鼠标验收。

先执行不提交的预检，确认当前标题为 36px、居中、颜色 `#123456`、背景 `#eef2f6`，图片加载、计数 0→1 和字号表单均正常，额度仍为 15。每个真实场景提交前持久化独占标记，避免脚本失败后重复点击耗费额度。

### 12.2 正常单属性修改：通过

通过可视化表单将标题字号 **36px → 40px**，只点击一次“应用修改”。

| 核对项 | 实际结果 |
| --- | --- |
| requestId | `da4779a3-8df8-40ab-9e80-355493cd0200` |
| 模型调用 | 3 次，账本 15→18：输入审核、生成、质量审查 |
| 准备与审查 | `skipped/STRUCTURED_EDIT`，含 `structured-edit-scope@1`；严格 JSON PASS、空 issues、STOP |
| 原生 SSE | 1 个连接、1 次 done、0 次 business-error、0 次传输错误；观测字符数 2738（JS length） |
| 任务和发布 | SUCCEEDED / COMMITTED，错误码为空；14:11:27 创建，14:11:38 完成 |
| 预览 | 生成期间旧 document 保持，成功后新增 1 次预览请求并换为新 document |
| 修改效果 | 计算字号 40px；其余三项样式、页面文字、图片 src/alt/加载状态保持 |
| 交互 | 新 document 的计数初始为 0，点击后为 1，功能正常 |

成功后 document 更换及计数重置是本次成功刷新的预期行为，不将报告里的 `samePreviewDocument=false`、`counterStatePreserved=false` 当作失败。页面重新加载完整聊天历史，截图显示新产物源码和实际 40px 页面。

### 12.3 真实停止操作：通过

以刚发布的 40px 页面为基线，先点击计数到 **1**。通过同一表单提交 **40px → 44px**，观察代理已预占本轮第 2 次调用、生成尚未结束时，点击实际“停止生成”按钮一次。

| 核对项 | 实际结果 |
| --- | --- |
| requestId | `5dcb0135-91d8-4891-bfcb-807b1b027d43` |
| 模型调用 | 2 次，账本 18→20；停止时第 2 次为 reserved，最终账本 last_status=cancelled |
| 停止请求 | 前端 HTTP 200、业务 code=0、STOPPING；Python 取消接口 HTTP 202 Accepted |
| 任务 | CANCELLED / 错误码 CANCELLED / heartbeat TERMINAL；14:13:05 创建，14:13:06 结束 |
| 发布观测 | 台账 publicationState=NOT_CONFIRMED，不能单独解释为数据库“未发布”的原子证明 |
| 前端 | 0 次 done，关闭原流后未观测 business-error；页面恢复发送按钮，提示后台任务已停止 |
| 预览 | 新增预览请求 0，原 iframe/document 保持，字号仍为 40px，计数仍为 1；再次点击可变为 2 |

停止前后另通过 Spring `artifact_context` 只读核对当前活动源码，内容完全相同，SHA-256 为 `0c58bdf88dc39b56e58dbe683d5d2cd7aa4038012f0e477c25b5316e5a01f196`，证明并非仅浏览器缓存保留旧画面。没有直接操作项目目录或连接业务 MySQL；该接口不返回活动版本 ID，因此不声称逐字段核验了数据库活动指针。

两个 requestId 均通过本应用时间窗口内的新任务、Python 日志关联，再用显式 appId + requestId 查询确认终态。成功 SSE 本身仍没有 requestId，取消后前端会主动关闭连接；不虚构 SSE 中出现了取消失败帧或严格原子关联字段。本轮没有额度拦截导致的失败，也没有再发额外修复或诊断模型请求。

### 12.4 新观察到的告警、证据与收尾

正常完成和取消完成后各观察到 PostgreSQL 清理告警：`Explicit rollback() forbidden within a Transaction context`，随后连接池关闭/丢弃连接。两次请求终态、正常发布和取消保持项符合预期，但这不代表 checkpoint 清理健康；需要独立排查事务/连接生命周期。不能未经定位就与历史 `another command already in progress` 认定为同一根因。本轮未修改 Python 以消除告警。

本轮证据位于后端忽略目录 `tmp/real-ui-acceptance-20261111/`：

- `inspect-ui-report.json` / `inspect-ui.png`：真实登录与表单预检；0 模型调用。
- `edit-ui-report.json` / `edit-ui.png`、`edit-confirmed-task.json`：正常修改、原生事件、保持项及显式任务查询。
- `cancel-ui-report.json` / `cancel-ui.png`、`cancel-confirmed-task.json`：真实停止、原 document 保留及任务终态。
- `artifact-before-cancel.json` / `artifact-after-cancel.json`：Spring 返回的当前活动源码，按私有证据保管。
- `review-67226f585a254d628ad3ee4090dfa39f.json`：实际审查适配器消息与原始响应；无改写提示的诊断回放。
- 服务日志、私有会话与启动记录、提交独占标记；前端临时观察器位于被忽略的 `doc/real-ui-acceptance.mjs` 和 `.html`。

所有原始上下文、Cookie、凭据、源码、账本及截图不提交。完成只读复核后已停止本轮 Spring/Python/代理进程和 PostgreSQL 容器，Vite 随各次浏览器运行关闭；原有 MySQL、原生 Redis 和其他容器保留，`.env` 未改动。没有发布到公网或推送 Git。

本轮无生产代码变更，只更新本交接文档；两仓库检查 status/diff，保留前端客服三文件。未重跑离线全量、Maven 或前端构建，不能将第 11 节结果改称本轮新跑。此次通过的是 **HTML 的一次完整真实 UI 修改链路与一次有效真实停止**，不覆盖三类全部属性、停止请求失败竞态、Vue 从零生成、部署/下载或自然 REPAIR 后的模型修复质量。

下一步优先离线定位已复现的 PostgreSQL 事务清理告警，再按明确新额度扩展真实失败及 MULTI_FILE/Vue 端到端矩阵。新授权账本现已 20/20，用完后不得复用“剩余 5 次”的旧结论。

## 13. 三类应用真实端到端测试集与新增 30 次验收

2026-11-11 用户要求提供测试集并开始执行，随后明确授权**新增最多 30 次**，包含失败、重试、修复和诊断。本轮账本为 `tmp/real-e2e-matrix-20261111/model-ledger.json`，最终 **30/30**；原 60/60、20/20 账本 SHA-256 保持不变。代理发送前持久化预占，最终 last_status=completed，没有额度拦截造成的业务失败，不得再使用旧授权继续调用。

可执行步骤、通过标准与覆盖矩阵见[真实端到端测试集](generation-real-e2e-test-suite.md)。复用第 12 节隔离账号和三类应用，通过已有会话真实鉴权。真实前端完整应用、原生 EventSource、Spring/Python/DeepSeek、Spring 工具、实际构建/发布及 PostgreSQL 均参与；Chrome headless 通过 DOM 操作真实表单，没有 Fake 生成、审查、工具或 SSE。Vue 基线仍是历史 fixture，不能宣称从零生成通过。

### 13.1 真实请求与调用账本

| 请求 | requestId | 调用 | 真实终态与结果 |
| --- | --- | --- | --- |
| HTML 字号 40→42px | `43acf814-a24e-43ed-add8-832d1b4888ee` | 3 | SUCCEEDED / COMMITTED；完整 UI 断言通过 |
| MULTI_FILE 实际提交 34px | `49dfcfa5-8d88-4b70-982a-3ba6880e4d3b` | 6 | SUCCEEDED / COMMITTED；自然 REPAIR→PASS；测试采样及目标有偏差，不计原定步骤通过 |
| Vue 字号 24→26px | `2fd5bc12-ff31-4b80-9c06-800494c72622` | 6 | SUCCEEDED / NOT_VERSIONED；构建、审查、UI 通过，含一次工具输出格式重试 |
| HTML 42→44px 时取消 | `7ab490de-93d7-44ee-83a8-160b09af1382` | 2 | CANCELLED，预览保持 42px |
| MULTI_FILE 34→36px 时取消 | `da467398-0d92-445b-802c-0b31e1a09d63` | 2 | CANCELLED，预览保持 34px |
| Vue 26→28px 时取消 | `02a6d0ac-5e9f-49ec-ad07-f6630bf7f9d5` | 2 | CANCELLED，预览保持 26px |
| MULTI_FILE 新请求 34→36px | `1a4e9dc0-3884-4e00-8841-7d70e0de4a52` | 3 | SUCCEEDED / COMMITTED；原始断言过早采样，零模型补验通过 |
| HTML 文字颜色 #123456→#345678 | `aa4cdb13-f28b-43d1-9f3a-3bb6fa179d96` | 3 | SUCCEEDED / COMMITTED；完整 UI 断言通过 |
| MULTI_FILE 对齐 left→center | `26d30097-371b-475f-9404-d5c3bf1559a3` | 3 | SUCCEEDED / COMMITTED；完整 UI 断言通过 |
| 合计 | 9 个请求 | **30** | 7 个完整原始 UI 报告通过、1 个零模型补验通过、1 个测试步骤偏差；不写成 9/9 全通过 |

所有请求最终又以 appId + requestId 只读确认台账，保存独立 `*-confirmed-task.json`。成功 SSE 本身仍不携带 requestId；第一次 MULTI_FILE 因报告中途退出，使用同应用/时间窗口和 Reviewer/Python 日志识别 requestId 后显式查询。成功刷新检查为新增 1 次预览请求，生成期间旧 document 保留；取消为 0 次、document 与计数保持，终态后点击计数仍加一。固定文字、图片 src/alt/加载、目标唯一性及其他三个样式分别核对。

### 13.2 自然结构化拒绝与真实修复

第一次 MULTI_FILE 请求真实 Reviewer 返回严格 JSON `REPAIR`，唯一问题码 `FUNCTIONAL_ERROR`：基础样式已为要求的 34px，但 `@media (max-width: 480px)` 仍为 22px。证据引用该 CSS，建议同步窄屏字号。后续真实修复的候选将该处改为 34px，Reviewer 再次返回 `PASS`、空 issues。

保存的两次审查候选差异为窄屏 `22px → 34px` 及代码块间空行；并非将完全相同候选重新送审。此次自然拒绝、结构化理由、实际候选改变和最终发布均有真实证据。未捕获独立的修复请求完整消息，因此不据此宣称逐字验证了拒绝反馈在每一层的传递。两个候选 SHA 不同；本轮**没有自然触发 REPAIR_NO_PROGRESS**，该终止分支仍需另行覆盖。

### 13.3 测试采样缺陷与零模型补验

初版私有运行器只等待目标 DOM，第一次 MULTI_FILE 在外链 CSS 加载前读到黑色、透明背景、默认 32px，因此实际表单填写 34px，而不是稳定页面 24px + 2px 的 26px。提交后的真实模型遵循了实际 34px 请求。完成后脚本又在新 iframe 目标尚不可读时退出，原报告 `Error: Missing target` 保留。该次不能计为原定字号步骤通过。

增加 document.readyState 等待后，新请求使用稳定的 34px 基线提交 36px；但终态采样仍短暂读到无样式 32px，原报告 passed=false。截图与后续稳定页面均显示样式已加载。最终运行器增加外部 link 的 stylesheet/CSSOM 及图片就绪检查，并用**零模型** `inspect-MULTI_FILE-fontSize-css-ready` 补验：36px、#123456、#ddeeff、left、13 条样式规则加载、固定文字/图片及计数 0→1 均通过。它与该请求的稳定基线、一次预览刷新和显式 SUCCEEDED/COMMITTED 共同提供补充证据；没有改写两个原始失败报告。

之后 MULTI_FILE 对齐请求使用相同修正后的等待条件，完整报告直接通过，字号仍为 36px。不能将采样修正写成生产预览缺陷已修复，也未验证短暂无样式闪现是否影响普通用户体验。

### 13.4 取消保持与基础设施观测

三类请求均在输入审核后、生成调用预占时点击停止，最终 CANCELLED；每类 2 次调用。Spring 只读源码比较结果：

| 类型 | 取消前后比较 | SHA-256 |
| --- | --- | --- |
| HTML | 活动 artifact 原文相同 | `c0d01978a5d5c51ff0f0b4c53b22c46df51166258a6a8d661bcae9f03c6a5fb8` |
| MULTI_FILE | 活动 artifact 原文相同 | `c7cca6182250b6a0b621846912c2fa4a92f5bdd6ead53e61c7296d8326532023` |
| Vue | 5 文件快照相同、truncated=false | `863abc671848c10669cd02fa457d54dd3bf4e6afe41c15edf604726f5f66e0fc` |

Vue 摘要按 files 的排序键 JSON、UTF-8 计算，仅说明本次早期取消没有改变所见源码；不证明任意取消时刻回滚、dist 的事务性或全工程一致性。静态台账 NOT_CONFIRMED 也不单独作为未发布原子证明，保持结论来自活动源码比较。未直连业务 MySQL 或直接读写 projects。

本轮 Python 日志仍出现 `Explicit rollback() forbidden within a Transaction context`（文本出现 2 次）、`another command is already in progress`（6 次）和连接池丢弃（4 次）；文本计数不等于独立请求数。结束前 `/health/ready` 返回 ready/checkpoint=true，但 checkpoint 事务清理健康项仍**未通过**。本轮未修复、未定位两种告警的共同根因。

### 13.5 文件、收尾及下一步

全部原始证据在后端忽略目录 `tmp/real-e2e-matrix-20261111/`：`summary.json`、各次原始 UI 报告/截图、显式任务查询、审查消息和响应、候选差异、取消前后及最终源码、调用账本、旧账本 SHA、私有启动信息与 cleanup.json。前端私有运行器在忽略的 `doc/real-e2e-matrix.mjs` / `.html`。原始失败报告保留，凭据、源码、日志和截图均不纳入 Git。

本轮只交付测试集与交接文档，未修改生产代码或配置；前端既有客服三文件保持。没有为文档重跑 Maven/Python 离线全量或前端构建，不沿用第 11 节结果当作本轮新跑。实际运行了三类浏览器预检、9 次真实请求及零模型补验、显式任务与源码核对，文档提交前检查两仓库 diff。

完成后停止本轮 Spring/Python/代理进程及 PostgreSQL 容器，Vite/Chrome 随运行器退出；保留原有 MySQL、Redis 和其他容器，未推送或公网部署。

建议下一轮首先离线定位 PostgreSQL 事务/连接清理告警；随后固化外部资源就绪条件到可复用的验收设施。真实补测优先自然无进展终止、剩余属性及停止失败竞态；本轮未覆盖完整属性矩阵、从零 Vue 生成、部署/下载、跨标签并发或自动恢复。新增真实调用必须再次明确额度，不继续使用本轮已耗尽的 30 次授权。

## 14. 无次数上限续验与终态收尾修复

2026-11-11 用户明确要求“不限制调用次数，继续完成真实端到端测验”，因此本轮不再适用此前 30 次上限。私有 `tmp/real-e2e-unlimited-20261111/counting_proxy.py` 复用原代理的认证、零重试和发送前持久化流程，以 `max_calls=null` 表示本轮不设总上限；生产 `scripts/acceptance_model_proxy.py` 的限额默认及行为未变。私有计数器先做超过 60 次预占及重启保持的离线自检，未访问供应商。

本轮实际 **67 次模型调用、19 个真实生成/取消请求**，旧 60/20/30 账本 SHA 均保持。真实请求的明细为 `tmp/real-e2e-unlimited-20261111/summary.json`，逐项调用差值之和为 67，没有未归属的调用。18 份完整 UI 报告直接通过，1 份修复前 Vue 报告采样退出后零模型补验通过；三类四属性的 12 个核心用例均另有本轮直接通过报告。

### 14.1 核心覆盖与观测边界

- HTML：字号 42→44px、文字色 #345678→#456789、背景 #eef2f6→#dce8f2、center→left，均直接通过，共 12 次调用。
- MULTI_FILE：字号 36→38px、文字色 #123456→#345678、背景 #ddeeff→#dce8f2、center→left，均直接通过，共 12 次。
- Vue 修复前字号 26→28px：供应商/构建/审查成功、6 次；脚本在旧文档就绪后遇到新文档替换，原始报告 Missing target，零模型补验确认 28px。没有重写失败报告。
- 修复后 Vue：字号 28→30px、文字色 #123456→#345678、背景 #eef2f6→#dce8f2、center→left，4 份完整报告直接通过，分别 6/5/5/6 次。Vue 工具输出格式重试计入调用。
- 修复后 HTML 44→46px、MULTI_FILE 38→40px 再验，各 3 次，直接通过。
- 修复后三类分别在生成调用开始后取消，各 2 次；全部 CANCELLED、旧 iframe/document 和计数保持、零预览刷新。
- 受控停止鉴权失败 1 次 HTML：3 次模型调用，真实停止 HTTP 200/code=40100，原流 1 次 done/无业务失败、最终 SUCCEEDED；旧预览 46px 保持、零刷新，服务端已发布新源码。请求为 `e165d4b3-0226-4012-b8b4-679149a6f3c6`。故障由仅对停止请求移除 Cookie 触发，返回真实 Spring 响应，非自然网络故障、非取消成功，也未伪造 SSE。

全部成功用例核对目标计算样式、另外三个样式、固定文字、图片 src/alt/加载、计数操作、唯一目标、生成期间旧预览与成功后一次刷新。全部请求以显式 appId/requestId 复核终态；缺失 UI requestId 的早期 Vue 用例依据独立 Python/Reviewer 日志定位 `d39d5694-8352-4573-a4f1-24e930049745` 再查询。成功 SSE 仍不提供 requestId。

私有浏览器夹具进一步等待“相对于本次旧文档的新 document、文档/外链 CSSOM/图片就绪、跨一次观测 tick 仍为同一 document”，再同步读取快照，避免先检查旧文档就绪再采到新空文档。使用完整真实应用和原生 EventSource，DOM 点击仍不等于物理鼠标验收。

### 14.2 终态过早发送：复现、修复与真实复测

`GenerationWorkflow.stream` 原来直接转发已入队的 completed/failed；此时后台 graph.ainvoke 及 checkpoint 清理可能尚未结束。Spring 收到终态后关闭下游连接，会使流 finally 取消仍在数据库收尾的后台任务。

新增 `tests/test_stream_terminal_cleanup.py`，用清理门闩和“收到终态立即关闭”的消费者分别覆盖成功、失败。修改前两项都失败，证明终态能在清理完成前被观察。修复仅在流边界缓存终态，等队列结束、后台 task 完成后投递；普通分片继续实时发送，不改事件结构、发布规则、模型提示或预算。清理失败仍不反转已确定业务终态。

Python 重启前后独立日志对照：重启前 9 个真实请求中 `another command is already in progress` 文本出现 3 次、连接丢弃 1 次；重启后的 **10 个真实请求**（Vue 四属性、HTML/MULTI_FILE 字号、三类取消、一次停止失败）中两种原始 rollback 告警及连接丢弃均为 **0**，结束前 ready/checkpoint=true。已修复可复现的终态时序缺陷，本轮未再复现清理告警；不据此声称所有 psycopg 问题同源或任意重复取消/并发压力均已验证。

该修复不改变 Java SSE schema 或前端 API；真实 Java/Python/前端联调覆盖了新投递时机，未修改 Java 文件或单独重跑 Maven 单元测试。变更已同步 AI README 和 AGENTS。

### 14.3 取消源码与真实 HTTP 反例

三类取消前后通过 Spring 工具只读比较，HTML/MULTI_FILE 的活动 artifact 相同，Vue 5 文件非截断源码快照相同：

| 类型 | 取消后 SHA-256 |
| --- | --- |
| HTML | `752b89884ff1dd3f3ad986b1481ac455352d45093c6549a1655cda4a44db7b74` |
| MULTI_FILE | `5c9f718d1b4bfa786c38b028c78b93fbf4e126115792cd94da9aa6b8bb3f35df` |
| Vue | `9cea0f0d814a83a081f0121a08040e1416cf131b68ca24984adf96d76ab722c6` |

Vue 按 files 的排序键 JSON/UTF-8 计算摘要，不构成任意取消时刻的工程回滚保证。后续 HTML 停止鉴权失败的源码与表中取消后源码不同，符合“停止未被接受、原生成继续成功”的预期。

真实 API 边界反例最终 9/9 通过：三类各自匿名生成/停止返回 40100，已登录非法结构化属性返回 40000，模型新增 0 次。入口异常 SSE 当前为 business-error 后接 done，未开始图执行，不把结束标记算作业务成功。预检先遇到匿名 Cookie 与登录 Cookie 混用，修正会话隔离后又遇正常 5次/60秒 限流返回 42900；保存这些原始观测，等待限流窗口后重跑并断言精确错误码，不关闭限流、不改 Redis。

### 14.4 本轮验证、文件与未覆盖项

- 新增成功/失败门闩测试：旧代码 2 失败；修复后通过。
- `uv run pytest tests/test_stream_terminal_cleanup.py tests/test_static_model_streaming.py tests/test_api.py`：142 passed。
- `uv run python -m compileall -q src`：通过；`uv lock --check`：通过。
- 第一次完整 pytest：1253 passed、10 skipped、1 failed，为客服重排器 30ms 超时恢复测试；未改客服逻辑，单独重跑该项通过。结束浏览器测试后再次完整运行：**1254 passed、10 skipped、2 warnings**（171.72 秒），原弃用警告保留。真实资源 opt-in 测试未另行开启；不能把被跳过的集成测试写成通过。
- 真实生成/取消 19 请求、API 反例最终 9 项、零模型补验和显式任务/源码核对完成。真实日志修复前后分别保存，不混报。

证据保存在忽略目录 `tmp/real-e2e-unlimited-20261111/`：summary、原始 UI 报告/截图、逐项显式台账、审查消息/响应、取消前后源码、失败停止后源码、计数账本、负例预检与最终报告、pytest-final.log、旧账本 hash、私有启动状态和 cleanup.json。前端私有运行器为忽略的 `doc/real-e2e-unlimited.mjs` / `.html`。凭据、日志、源码和截图不提交。

已停止本轮 Spring/Python/代理及 PostgreSQL 容器，Vite/Chrome 随运行退出；保留原有 MySQL、Redis、其他容器及前端客服三个既有修改。未改 `.env`、未推送或公网部署。

本轮完成三类四属性矩阵及所列取消/受控故障验收。自然 REPAIR→PASS 仍以第 13 节为证，本轮未触发自然 REPAIR_NO_PROGRESS，不以构造的假候选补齐真实覆盖。Vue 从零生成、部署/下载、长时间并发、全部停止失败竞态及自动恢复仍不在通过结论中。用户对本任务的无次数上限授权持续有效，但不代表需要无限重复采样或扩展到无关模型任务。

## 15. 重复取消、断连与 checkpoint 事务收尾回归

2026-11-11 承接第 14 节后续维护。本轮真实模型新增 **0 次**，无失败模型调用或模型重试；无上限账本仍为 **67、max_calls=null**，未启动 Spring、Python 服务、计数代理或前端浏览器。第 14 节的 19 请求与 67 次调用均为历史验收，不计作本轮修复后结果。

### 15.1 确定性复现及修复范围

对 `3f2e406` 的实现新增门闩测试，原两项终态等待测试保持通过，新增 **7 项失败**：停止重复注入取消、消费者重复取消、混合取消打断模型退出，及正常成功/失败的清理被迟到停止或断连中断。随后新增真实 FastAPI/Starlette ASGI 应用回归，模型退出/清理阶段 × ASGI 2.3/2.4 共 **4 项失败**。ASGI 2.4 发送异常发生在迭代器 yield 之外，原响应可能在工作流关闭前返回；ASGI 2.3 的取消作用域还会在每次等待时再次取消。

修复包括：

- 终态内部入队前同步进入当前执行的收尾状态，保护 `graph.ainvoke` 返回前的最后写入；无终态异常也在维护 finally 进入收尾。
- 首次取消继续中断执行，重复停止不再次调用 `Task.cancel()`。消费者用 asyncio shield 和 AnyIO 取消屏蔽排空后台任务，再传播外层取消；收尾失败不能遗留在途登记。
- 生成专用响应和路由显式关闭两层迭代器；内层完成后不在 HTTP finally 重建取消标记。注册冲突不启动第二份未登记执行，旧所有者不能取消或清除替代执行。
- checkpoint 维护失败沿用原业务结果规则；即使注入的旁路观测抛出异常也尝试清理、释放标记，异常本身继续传播。

事件字段、普通分片实时输出、展示/候选分离、Java SSE、三类产物发布、修复与工具预算均未变。只读核对了 Java 网关、`StaticGenerationStreamFrame` 和 `SimpleTextStreamHandler`；没有修改 Java 或前端代码。本轮不重新运行 Maven 或前端构建。

### 15.2 真实数据库与证据边界

本轮只额外启动已有 `langgraph-postgres` 容器，测试后已停止；启动前确认 5432、8000、8123、18001、5173 无监听，原 MySQL/Redis 与其他容器保留。测试使用私有 Settings 数据库连接，不输出连接串或凭据。

新增 `test_postgres_generation_drain_integration.py` 四项：主动停止、消费者断连、正常成功、生成失败。使用真实 LangGraph 和官方 PostgreSQL saver，模型和 Spring 工具均为 Fake。在官方 DELETE 已执行、事务提交前挂起，独立连接确认仍可见旧 checkpoint，再发重复取消，释放门闩后检查唯一业务终态或外层取消、连接池可用、三张 checkpoint 物理表无本轮 thread 残留。四项与已有两项生命周期测试合计 **6 passed**，新四项未出现 checkpoint/psycopg 警告。

每项使用 UUID 请求，过期扫描限制到本轮 ID，结束只清除本轮状态与 checkpoint，不修改其他生成数据。没有运行其他 PostgreSQL 子图恢复、Java MySQL/Redis opt-in 测试。ASGI 测试直接调用真实应用，断连消息/发送异常与模型、存储均是受控夹具；没有真实网络、浏览器或自然供应商时序，不能汇总成真实模型端到端通过。

### 15.3 本地证据与后续范围

本轮最终验证：

- 定向流式/ASGI/API 回归（追加观测异常测试前）：**157 passed**。
- `uv run python -m compileall -q src`、`uv lock --check`：通过。
- 最终 `uv run pytest -ra --tb=short`：**1270 passed、14 skipped、2 warnings，175.34 秒**；其中 14 项为默认关闭的 PostgreSQL opt-in 测试，已单独执行的 6 项不在这次离线运行中计为通过。两条原有 Starlette/LangGraph 弃用警告保留。
- 显式启用 PostgreSQL 后运行新增事务测试及原生命周期测试：**6 passed、2 warnings**。
- 两仓库 `git diff --check` 通过；前端只核对工作树，不执行构建或测试。未推送或部署。

忽略目录 `tmp/` 保存 `cancellation-drain-before.log`、`cancellation-asgi-before.log`、`cancellation-drain-targeted.log`、`cancellation-drain-postgres.log` 和 `cancellation-drain-pytest-final.log`。这是合成回归与数据库测试日志，不包含新增真实模型或 UI 报告；不提交这些日志，也不读取/提交 `private-runtime.json`、Cookie、密钥、生成源码或截图。

本轮没有为命中罕见分支重复采样。自然 REPAIR_NO_PROGRESS 仍未覆盖；自然 REPAIR→PASS 仍引用第 13 节。Vue 从零生成、部署/下载、长期或跨实例并发、全部停止失败时序、数据库永久挂起、进程强制退出和自动恢复均未验收。保护范围是 HTTP/stream 当前进程生命周期，不承诺直接 `run()` 调试入口的任意重复取消安全；没有新增收尾超时、强杀、生产恢复或 Vue 回滚。

前端既有客服三文件及后端启动时已有未跟踪计划 `docs/superpowers/plans/2026-11-11-cancellation-drain-regression.md` 均保留，不纳入本次提交。AI README、AGENTS、终态设计与本轮维护记录已同步。回退需整体回退执行登记、事件内部收尾回调、工作流排空与生成响应/路由关闭逻辑，保留上一轮终态等待；不涉及数据库迁移、模型配置、用户数据或前端回退。

本轮提交说明为 `fix(ai): 保护重复取消与断连后的工作流收尾`，可用 `git log -1 --format='%h %s' -- doc/generation-structured-edit-acceptance-handoff.md` 查询对应哈希。

## 16. Vue 从零生成与交付维护

2026-11-11 承接 `e6967ea`，用户授权继续优化并直接进行必要真实测验。先检查两仓库，再选择 Vue 从零生成、部署/下载/导出及迟到交付响应；第 14 节属性矩阵和第 15 节 PostgreSQL 受控事务测试没有重新执行。沿用 `tmp/real-e2e-unlimited-20261111/model-ledger.json`，**新增 51 次，67→118，max_calls=null**；旧 60/20/30 账本 SHA-256 均保持，生产限额代理未改。

### 16.1 三项修复与确定性证据

1. **前端交付回调归属。** 部署、源码下载、Markdown 导出固定发起时的 appId、备用文件名和页面代次，在成功、异常、Blob 错误正文异步读取后和 finally 核对归属。导航/卸载后的旧响应不再修改新页面、触发旧下载或解除新请求 loading；同页新生成不使已发起交付失效。加入重复触发保护，不承诺撤销服务端已开始的部署。独立前端新增 `tests/browser/deliveryLifecycle.*`，使用真实组件和合成 HTTP 门闩，旧代码 **14 失败、6 通过**，修复后 **20/20**。原生成矩阵 **54/54**。早期两次夹具超时源于挂起 HTTP 与 Chrome 虚拟时钟相互等待，改为服务器 tick 后重新取得完整基线，不能计为生产缺陷。
2. **Markdown 源码附录剪枝。** 第二样本导出 19,732,869 字节、540,959 行，包含 462 个 node_modules 和 3 个 dist 文件章节。原实现全目录遍历后仅按扩展名过滤；改为 `Files.walkFileTree` 在目录入口排除 `node_modules/dist/build/target/.git/.mvn/.idea/.vscode`，不跟随符号链接，保留项目文本、锁文件、文档与排序。三类型及“仅有依赖不能当源码”共 4 项旧实现失败，修复后通过。重启 Spring 后，对原应用零模型导出为 **56,311 字节**，无依赖/构建章节，源码/锁文件/需求保留。原失败导出不覆盖。
3. **有限并列不配图识别。** 实测“不需要登录、远程后端或外部图片”被旧规则漏掉，第二样本模型上下文包含 6 项 imageAssets，结束后还收到使用图片反馈。扩展同分句内 80 字符的并列否定、外部/网络/在线修饰及英文 `without external images`；固定动作排除避免把保护旧图当禁止配图，后句明确新增仍优先。新增 API 四场景在旧实现失败，修复后相关 20 项通过。工具/修复预算、模型参数和公共 SSE 未变，规则不等于通用自然语言理解。

### 16.2 真实调用与产物结果

三个全新隔离应用都只提交一次生成，需求相同：Vue 中文“慢读书房”，三本未读书、添加及空白校验、已读切换、全部/未读/已读筛选、计数、响应式，并明确不需要外部图片。每个创建入口路由各使用 2 次真实调用，共 6 次；生成共 45 次，失败及格式重试均占账本。

| 样本 | appId / requestId | 调用与终态 | 结果 |
| --- | --- | --- | --- |
| 1，修复前 | `478025956899532800` / `ad5408a7-25c7-4d19-aa8b-a28c331e3a7d` | 创建 2 + 生成 18；FAILED / VUE_TOOL_BATCH_LIMIT_EXCEEDED | done=0、业务错误=1、预览刷新=0；已有 9 文件写入，不能算生成通过，也无 Vue 回滚 |
| 2，修复前 | `478027690220826624` / `949c9c12-9428-428a-b34c-3bde9590991b` | 创建 2 + 生成 16；SUCCEEDED / NOT_VERSIONED | 唯一 done、无业务/传输错误、刷新 1 次；预览与本机部署的添加、空白拒绝、切换及三筛选通过；导出内容另发现缺陷并修复补验 |
| 3，配图规则修复后 | `478030502103154688` / `349eeb2c-0739-4624-94a6-dfcab286d39b` | 创建 2 + 生成 11；SUCCEEDED / NOT_VERSIONED | 唯一成功终态、刷新 1 次；添加和空白校验通过，但筛选按钮缺失，完整功能验收失败 |

三项结束后均以 appId/requestId 显式重新查询，heartbeat=TERMINAL。样本 1 未捕获完整逐轮原始响应，不断言超限的具体模型根因。首次 UI 预检曾把新应用“暂无任务”的 40400 误当失败，当时未提交生成；原报告保留，修正夹具后才正式提交。

样本 2 的 13 轮 Vue 模型调用包括规划、分批写文件、一次无效 JSON，以及图片使用反馈后的读写；另有输入审核、需求整理和质量审查 3 次。样本 3 为 8 轮 Vue（含一次无效 JSON）及另外 3 次。修复后所有被动观测到的 Vue 消息 imageAssets=0、无 imageUsageFeedback。单次调用 16→11 不是普遍性能保证；两者都没有自然 REPAIR→PASS，额外轮次是格式/配图反馈，不得当作质量修复证据。

样本 3 的真实 Reviewer 严格返回 PASS/空 issues，但 `FilterBar.vue` 要求 filters/active/total/readCount 属性及 change 事件，父组件仅使用 v-model，没有提供筛选数据、计数或对应事件接线，按钮未渲染。浏览器原报告在筛选检查中断；截图和 Spring 非截断源码快照确认组件契约不匹配。零模型观察继续下载、导出和部署，在部署页复现相同缺陷，整体报告仍 passed=false。没有调整断言让失败变通过，没有手改生成源码，也没有为换一个成功样本继续调用。后续应优先评估 Vue 运行期/功能检查如何捕捉这种构建成功但组件缺数据的情况。

### 16.3 交付证据与范围

样本 2 ZIP **14,665 字节/9 条目**，与 Spring 8 个非截断快照文件逐字节一致，额外为 package-lock.json；样本 3 ZIP **15,243 字节/10 条目**，与 9 个快照文件一致，额外同为锁文件。均检查路径安全且无依赖/构建目录。样本 3 Markdown **55,083 字节**，无依赖/构建章节，保留锁文件。没有把 ZIP 解压到工程或修改项目文件。

真实链路包含完整 Vue 前端、Chrome headless DOM 操作、原生 EventSource、Spring、Python、真实供应商、实际工具/构建及 PostgreSQL。适配器观测仅在私有脚本被动保存调用输入输出，不修改提示或供应商返回；不是 Fake 端到端，也不是物理鼠标测试。本机部署访问 localhost/回环地址，两个部署页面 390px 检查无横向溢出；样本 3 的布局/交付通过不能抵消功能失败，不能称公网完整部署验收。

各 Python 运行日志中 `Explicit rollback() forbidden`、`another command is already in progress`、`discarding closed connection` 均为 0。此轮三个生成样本没有覆盖新的取消/并发时序，不扩大第 15 节生命周期保护结论。

### 16.4 本轮验证与私有证据

- Python：`uv run python -m compileall -q src`、`uv lock --check` 通过；最终 `uv run pytest -ra --tb=short` 为 **1280 passed、14 skipped、2 warnings，183.85 秒**。14 项 PostgreSQL opt-in 本轮未独立执行；两条既有弃用警告保留。
- Java：本机没有 `mvnw.cmd`，尝试 Wrapper 命令失败后改用系统 Maven。`mvn clean -DskipTests compile` 通过；`mvn test -Dtest=ChatHistoryExportServiceTest,DeploymentResourceControllerTest,AppControllerSseTest` **12/12** 通过。
- 前端：交付浏览器 **20/20**，既有生成浏览器 **54/54**；三项纯函数测试文件合计 **27/27**；`npm run type-check` 和 `npm run build-only` 通过，既有大 chunk 提示保留。浏览器命令分别为 `node scripts/test-generation-ui.mjs --delivery` 与 `node scripts/test-generation-ui.mjs`。

私有证据目录：`tmp/delivery-e2e-20261111/`（首样本、服务/离线日志、逐轮观测、delivery-summary.json），`tmp/delivery-e2e-fresh2-20261111/`（第二样本、原/修复后导出和 ZIP 比较），`tmp/delivery-e2e-no-images-20261111/`（第三样本及失败补验）。前端忽略目录 `doc/` 保存运行器、浏览器基线及修复后报告。原报告、源码、截图、Cookie、密钥和 private-runtime.json 均不提交、不在交付消息中输出。

自然 REPAIR_NO_PROGRESS、全部停止失败时序、长期/跨实例并发、自动恢复、Vue 回滚和公网部署仍未验收；自然 REPAIR→PASS 仍为第 13 节历史证据。下一轮优先补 Vue 功能漏检的确定性回归，再选有价值的真实对照，不为罕见分支无限采样。无上限授权持续适用于本任务，但必须沿用当前 **118 次**账本，不重跑已有提交标记。

### 16.5 收尾与提交范围

最后一次零模型观察已保存 complete 阶段报告、ZIP、Markdown 和截图，功能结果仍 false；Chrome 没有自行退出，核对本次独立 profile 后显式停止，运行器退出码为 1，不计为完整脚本通过。三项生成台账均确认终态后，核对主私有状态 PID 和实际进程归属，停止本轮 Spring/Python/代理及其浏览器子进程，并停止 `langgraph-postgres`。保留原 MySQL、Redis、用户浏览器及其他容器，不删除私有证据。

两仓库仅提交本轮生产修改、必要测试和文档，保留前端客服三文件及后端原未跟踪 cancellation-drain-regression 计划。Java 测试目录被根 `.gitignore` 的 `test/` 规则忽略，新增导出回归需明确纳入提交。没有推送；后端与前端提交分别使用中文 Conventional Commits，不把前端用户修改混入交付。

## 17. Vue 审查证据投影与组件契约核对

2026-11-11 承接 `bdc7600` / `becdf0a`。本轮聚焦第 16 节的 Vue 筛选漏检：只读核对保存的供应商消息，确认 App.vue 和 FilterBar.vue 同时在未截断的 9 文件快照中，审查还携带 9 项生成工具历史。因此不是“没有提供父子源码”；不能把删除工具历史视为已证明的唯一根因修复。

### 17.1 实现与确定性验证

工作流单 Reviewer 上下文明传权威 codeGenType。适配器遇到 VUE_PROJECT 时白名单保留当前 prompt、有限用户历史、metadata 中有界 initialPrompt、currentArtifact、imageAssets、editIntent、generationPreparation、validation 和 build，artifact 参数仍为当前快照；不再发送生成工具历史、助手消息、规划/格式反馈、旧 artifact 和任意扩展元数据。原 context 不变，生成/修复工具历史、checkpoint 和无进展指纹保持原语义。

Vue 专用说明由单/多 Reviewer 共用：按可见父子源码核对 required props、默认值、emit/监听、默认或具名 v-model/defineModel，以及列表/筛选/计数状态流。要求证据指出调用方与被调用方的不匹配，避免把合法转发、对象展开、动态组件或不可见文件直接判错。通用审查说明不再暗示 Vue 构建等于浏览器功能检查。JSON 协议、问题上限、修复次数、工具预算、公共 SSE 均未修改，也没有新增静态语义硬门禁或浏览器检查。

旧实现新增两项定向回归为 **1 失败、1 通过**：失败项证明过时工具/规划内容仍进入模型输入；超限快照原本就会在供应商调用前失败。修复后新增 9 项覆盖输入隔离、保留约束、不原地修改、UTF-16/历史上限、完整消息预算、静态分支兼容、三角色说明以及真实嵌套图向适配器边界传递 Vue 类型与当前快照。原生成/修复历史预算测试继续通过。这些使用 Fake/捕获客户端，证明输入和控制流，不能证明模型语义判断准确。

### 17.2 六次真实审查及标注偏差

只启动原无上限计数代理，未启动 Spring/Python 生成服务、PostgreSQL 或浏览器。使用相同模型参数、无 SDK 重试的私有运行器：旧组原样重放第 16 节保存的审查消息，新组将相同 artifact 交给本次生产适配器。旧/新每个样本的源码 SHA 相同，没有修改工程或生成源码。每项发送前建立独占标记，沿用原账本 **118→124，新增 6 次、max_calls=null**；无供应商/解析失败，无重试。旧 60/20/30 账本 SHA 保持。

| 样本与分组 | 真实结果 | 判读 |
| --- | --- | --- |
| 第 16 节样本 3，旧消息重审 | REPAIR，2 条 FUNCTIONAL_ERROR | 本次旧版也识别筛选接线问题；历史 PASS 不代表每次都漏检 |
| 同一失败样本，新审查 | REPAIR，1 条 FUNCTIONAL_ERROR | 证据包含 FilterBar 所需属性/事件与父组件 v-model 不匹配 |
| 第 16 节样本 2，旧消息重审 | PASS | 符合运行器预设，但该样本不是干净正例 |
| 同一功能通过样本，新审查 | REPAIR，1 条 SCOPE_VIOLATION | 指出明确不需要外部图片却添加了图片；与第 16 节已知配图偏差一致 |
| 手写 explicit-props-and-event，新审查 | PASS | 合法显式 props 与 change 事件接线 |
| 手写 named-define-model，新审查 | PASS | 合法具名 v-model 与 defineModel |

最初四次对照预设样本 2 应为 PASS，新版出现 `matchesExpected=false`。复核后明确“交互通过”不等于满足全部原文限制；原 expected、原响应和 mismatch 均保留，另存判读说明，不能改表算 4/4 全通过。由此补充两个手写合法接线正例，各一次真实审查，不重新抽样历史样本。公开正例在 `ai-service/evaluation/vue-review-contract-v1.json`，仅提供相关 SFC，私有输入明确局部/非完整快照，没有伪造实际构建摘要。这是开发材料，不是独立留出集或自然生成产物。

历史失败样本的新旧输入字符数为 **20,518→18,620**，另一历史样本为 **33,162→19,222**；包含系统说明，不等同于供应商 token/费用。此结果证明具体输入缩减和上述判定，**不能证明检出率普遍提高**，也没有证明工具历史是历史漏检的唯一原因。生产模型审查仍可能漏检、误判；下一步应独立设计 Vue 运行期/交互证据与有边界的确定性检查，而不是仅增加提示或反复重审同样样本。

### 17.3 本轮验证、收尾与未覆盖项

- `uv run python -m compileall -q src`、`uv lock --check` 通过。
- 审查/修复/适配器定向测试通过；最终 `uv run pytest -ra --tb=short`：**1289 passed、14 skipped、2 warnings，184.60 秒**。PostgreSQL opt-in 未启用，两条原有弃用警告保留。
- 本轮没有修改 Java 或前端，没有重跑 Maven、前端构建或浏览器测试；第 16 节对应结果保持历史身份。
- 六次真实审查完成后核对 PID/命令归属，停止本轮代理。8000/8123/18001/5432 无监听，原 MySQL/Redis 保留。无新增生成、修复、发布、下载、部署或应用任务，不据此宣称端到端通过。

私有证据为 `tmp/vue-review-evidence-20261111/` 的原始消息/响应、submitted 标记、summary.json、positive-summary.json、final-summary.json；离线日志为 `tmp/vue-review-evidence-before.log`、`tmp/vue-review-evidence-targeted.log`、`tmp/vue-review-evidence-final.log`。只提交手写测试/正例、生产代码及必要文档，私有配置、令牌、日志和历史生成源码不提交。前端客服三文件及后端原未跟踪 cancellation-drain-regression 计划保持。

自然 REPAIR_NO_PROGRESS、新优化后的自然生成→审查→修复→交互全链路、长期并发、全部停止失败时序、公网部署仍未覆盖；自动恢复、Vue 回滚和通用组件静态验证未实现。自然 REPAIR→PASS 仍引用第 13 节。后续真实调用授权持续，但应沿用 **124 次**账本并保留本轮全部结果，不重跑已有标记。

## 18. Vue 显式必填属性的有限确定性门禁

2026-11-11 承接 `fdae652`。本轮从模型提示转向可复现的组件契约检查：HTML 烟测侧重脚本异常与可见内容，无法保证发现生产 Vue 中静默缺失的筛选按钮。新增官方 Vue SFC/Babel AST 检查，未把正则或模型判定作为硬校验。**新增真实模型调用 0 次，账本仍为 124、max_calls=null**，旧 60/20/30 账本 SHA 均保持。

### 18.1 门禁位置和范围

Spring `VueProjectBuilder` 的强制构建在 npm 成功、dist/index.html 存在后，执行随 JAR 发布的 `vue/component-contract-check.cjs`。脚本经临时资源文件和 Node 直接参数运行，命令等待 15 秒；进程树/输出收尾沿用原额外限时，临时文件 finally 删除。加载编译器遵循项目 Node 依赖解析，支持 `@vue/compiler-sfc` 或 `vue/compiler-sfc` 的 parse/babelParse 接口；本轮实际测试版本 3.5.18。没有加载应用组件模块或执行 props 表达式。

仅比较静态相对 `.vue` 默认导入、script setup 中直接运行时 defineProps/defineModel 的显式 required:true 属性，以及父模板的静态属性、具名绑定、默认/具名 v-model；支持 kebab/camel 对齐。没有提供必填属性时返回最多四条路径/组件/属性证据。带默认值、类型 props、Options API、未知配置、声明展开、动态绑定、作用域内调用、保留属性、宏同名合并和 onX 事件属性声明等不确定情况跳过，不猜测运行时结果。事件语义、必填值是否非空、点击后状态是否正确仍不在门禁范围。

扫描最多 20000 项、64 层目录、24 个 Vue 文件，单文件 12000 字符/读取 48000 字节，合计 60000 字符。不跟随符号链接，剪枝隐藏路径与 node_modules/dist/build/target；只使用读到的完整文件。日志只记录检查/跳过计数和截断标记。Windows 上 lstat.dev 与 fstat.dev 可能不同，使用 inode 和实际路径归属校验；其他平台同时检查设备号。读取不是原子全工程快照，跳过范围不能计作已验收。

`project_build` 仍返回 built/errorCode/message，不新增工具或公共 SSE 字段：

- 确定缺失：`VUE_COMPONENT_CONTRACT_FAILED`，按原构建失败进入最多两次修复，修复后强制重建、重检，再做模型审查。
- 检查器/Node/编译器不可用、输出损坏或超时：`VUE_COMPONENT_CHECK_UNAVAILABLE`，Python 直接失败，不花模型调用修复环境。
- 取消：保留 `VUE_BUILD_CANCELLED`，沿现有所有者和进程清理流程。

Java 校验 JSON 版本、类型、计数、最多四条诊断、相对路径及长度，拒绝重复键/尾随正文等损坏结果，不将检查器原始 stdout/stderr 或异常正文回显。门禁通过只表示支持范围内没有发现这类缺失。已有 dist 的 ensure 快路径不重检；失败不删除或回滚源码/dist，也不保证阻止旧产物部署或直接预览访问。Vue 仍没有静态版本回滚能力。

### 18.2 复现、历史材料与真实运行时检查

旧构建器新增回归 **1 项失败**：检查结果明确指出缺少 props，但原实现只要 npm/dist 成功就返回成功。新增环境不可用 Python 回归也在旧路由失败，因为旧路由继续修复；修正后直接终止。

提交前补充事件属性反例：`@click` 可提供 `onClick`，原检查会误报缺失；新增回归先失败，修正为保守跳过含 onX 事件属性的声明后通过，defineProps/defineModel 均覆盖。没有将事件监听器转换扩展为完整事件语义检查。

最终 Node **21/21**：覆盖必填缺失、正确显式属性、默认/具名 defineModel、kebab-case、未知/重复/宏合并声明、动态/作用域/v-pre、部分文件、证据上限、依赖剪枝、符号链接和文件数上限。Java 最终 **53/53**：Vue 构建器 18、检查器 10、LangGraph 网关 25；其中一项显式 opt-in 测试使用真实 Node 与本机已安装编译器，通过生产资源提取/命令执行入口验证手写组件“缺失被拒绝→补齐通过”，不运行模型或 npm 构建。该测试只在自身临时目录链接已有依赖，finally 先移除链接，再交给临时目录清理，不修改前端 node_modules。

读取第 16 节已保存的 Spring 非截断快照，对其中 Vue 文件零模型执行同一分析器，没有读写真实项目目录：

| 历史材料 | 本轮结果 | 边界 |
| --- | --- | --- |
| 第 16 节样本 3 | FAILED，FilterBar 缺少 filters、active、total、readCount；检查 2 处、跳过 1 处用法 | 确定命中原筛选接线缺陷，不代表本轮修复了该应用 |
| 第 16 节样本 2 | OK，检查 1 处、跳过 1 处用法 | 循环内用法跳过；该样本的额外图片问题不属于 props 门禁范围，不能重新称为全需求通过 |

这是历史材料重检与手写夹具验证，不是新的自然生成或浏览器端到端。没有为了验证门禁反复采样模型，也没有手改历史产物。

### 18.3 验证命令与一次既有测试波动

在后端根目录执行：

```powershell
mvn clean -DskipTests compile
$env:VUE_CHECK_COMPILER_ROOT = (Resolve-Path ../yu-ai-code-mother-frontend).Path
node --test scripts/tests/vue-component-contract.test.cjs
$env:VUE_COMPONENT_INTEGRATION = 'true'
mvn test '-Dtest=VueProjectBuilderTest,VueComponentContractCheckTest,LangGraphAiGenerationGatewayTest'
```

以上均通过；Node 测试需要指定一个已安装 Vue compiler 的依赖根，默认不会安装依赖。Java opt-in 未设置时该真实 Node 用例跳过，其余仍离线。Java 编译保留既有弃用/unchecked 提示。

Python compileall、uv lock --check 通过；组件环境失败和构建修复定向 **5 passed**。首次全量为 **1 failed、1291 passed、14 skipped**：既有 `test_spawn_worker_cancelled_request_keeps_ipc_lock_until_shutdown` 在取消后检查锁时读到 unlocked。该测试用 30ms 固定等待选择取消时序，源代码发送取消与接收取消走不同收尾路径；本轮未捕获足以确认失败分支的证据，不能断言根因已修复。单独复核 **1 passed**，随后不并行 Maven 的全量重跑为 **1292 passed、14 skipped、2 warnings，207.88 秒**。没有修改重排器生产代码或测试；首次失败日志保留，建议后续单独用门闩消除时序猜测。14 项 PostgreSQL opt-in 未启用，两条原有弃用警告保留。

本轮没有修改前端，没有执行前端构建、真实浏览器或 Spring/Python 服务联调；第 16/17 节结果不计为本轮新门禁端到端证据。工具响应 schema 和 Java/Python 错误传递已核对，现有 errorCode 为字符串，无接口迁移。

### 18.4 证据、交付及下一步

私有证据在 `tmp/`：`vue-required-props-before.log`、`vue-required-props-events-before.log`、`vue-required-props-python-before.log`、`vue-required-props-node-final.log`、`vue-required-props-compile.log`、`vue-required-props-java-verified.log`、`vue-required-props-python-targeted.log`、首次全量 `vue-required-props-python-final.log`、单项复核 `vue-required-props-reranker-recheck.log`、最终全量 `vue-required-props-python-rerun.log`、`vue-required-props-history-final.json`。日志、原始源码和凭据不提交；仅新增手写测试及可信检查器。根 ignore 会忽略 scripts/ 和 test/，新增 Node/Java 回归需明确纳入提交。

本轮未启动生成服务、代理、PostgreSQL 或浏览器，无需关闭用户服务；测试子进程由测试执行器收尾。结束检查 8000/8123/18001/5432 无监听，原 MySQL/Redis 保留。前端客服三文件与原未跟踪 cancellation-drain-regression 计划保持，提交仅涉及后端本轮代码/测试/文档，未推送。

下一步优先针对新门禁做隔离应用的实际生成/修复/交互验证，再评估未支持的事件和运行期证据；不得把这里的四项属性命中扩大成通用 Vue 语义验证。自然 REPAIR_NO_PROGRESS、全部停止失败时序、长期并发、公网部署仍未覆盖；自动恢复和 Vue 回滚未实现。真实调用继续沿用 **124 次**账本、保留新提交标记，不复用旧 requestId 或按任务状态自动恢复。

## 19. Vue 门禁真实续验与读书清单交互探针

2026-11-11 承接 `e1c1d4c`，用户认可先验证真实闭环、再补运行期证据的顺序。本轮新建一个隔离 Vue 应用，只提交一次生成，复用第 16 节“慢读书房”的完整需求；不改历史应用或生成源码，不调整修复/工具预算。私有无上限账本 **124→136，新增 12 次**，旧 60/20/30 账本 SHA 均保持。

### 19.1 真实生成、检查与调用

- 新应用 `478056342471905280`，请求 `239e684a-e11c-410e-aab3-b1d5a9b5314a`。真实前端原生 EventSource、Spring/Python、供应商、实际文件工具/npm 构建、组件检查和 PostgreSQL 均参与。
- 显式按 appId/requestId 查询：`SUCCEEDED`、`heartbeatState=TERMINAL`、`publicationState=NOT_VERSIONED`。唯一 done、无业务/传输错误、预览刷新一次。
- 构建 `project_build` 返回 built=true。生成及后续本机部署的 Spring 检查日志均为：checkedComponents=3、checkedUsages=1、skippedFiles=0、skippedUsages=1、truncated=false。不能把跳过的用法当成已检查。真实 Reviewer 返回 PASS、空 issues。
- 创建 2 次、生成 10 次，共 12 次供应商 HTTP 200。Vue 生成共 7 轮，包含规划、四轮各两个工具、一次非 JSON 输出，以及格式反馈后的正常结束；另外是输入审核、需求整理和质量审查。格式失败及重试均计入账本，没有执行失败轮工具，没有供应商自动重试。
- 没有触发 `VUE_COMPONENT_CONTRACT_FAILED`、质量修复或 `REPAIR_NO_PROGRESS`。本次证明新检查不阻断该正常应用，不能证明自然缺陷触发后的修复质量。

### 19.2 真实页面与交付结果

原 UI 报告 32 项通过：初始书目、添加、空白拒绝、已读切换、全部/未读/已读筛选、无 img 元素、源码下载、本机部署及 390px 无横向溢出。原报告完成后 Chrome 没有自行退出；核对本轮独立 profile 后停止，原运行器退出码 1，报告与运行器故障均保留。

私有运行器后续关闭 HMR 并缩短虚拟时间预算，两次**零模型**观察正常退出。第一次补齐计数状态，第二次使用下述共享探针；没有重发生成或重试供应商。没有把原运行器故障断言成已确定的唯一根因，也没有将原退出码改为成功。

共享探针对新应用预览与本机部署页共 **44 项通过**（每页 22 项），含 **16 项计数检查**：初始 3/0、添加后 4/0、拒绝空白保持 4/0、切换后 4/1、三种筛选仍保持总量 4/1、切回未读后 4/0。这里的操作是 Chrome headless DOM 操作，不是物理鼠标测试。只检查这一固定需求和指定尺寸，不宣称任意事件、动态组件或所有响应式布局正确。

源码 ZIP 9 条目与 Spring 8 个非截断快照文件逐字节一致，额外为 package-lock.json，无依赖/构建目录和不安全路径。Markdown 53,982 字节，无 node_modules/dist 章节。部署仅访问 localhost/回环地址，不计为公网部署验收。

### 19.3 可复用运行期探针与确定性反例

新增 `scripts/acceptance_reading_list_checks.mjs`，导出 `verifyReadingList(document, { tick, prefix, record })`。调用方提供同源隔离页面，探针执行固定书单交互，按实际条件有界等待；按钮和输入框要求唯一，错误只报告固定检查名，结果仅含布尔值和计数。它会在页面中添加一本“验收新书”，不能直接作用于用户正在使用的页面。

该探针属于验收设施，**没有接入生产工作流**，不调用模型。它要求固定初始书目、按钮和“共 N 本 · 已读 M 本”文案，不是任意生成应用的通用语义检查。无 img 元素不等于没有 CSS 背景图片；不新增 Vue 回滚、自动恢复或事件静态分析。

新增 `scripts/tests/reading-list-browser.test.mjs` 与手写 HTML 夹具。真实独立 Chrome 运行四种情况：合法页面通过；缺筛选按钮、计数不更新、筛选无效分别在 read-filter-button、added-counts、read-filter 检查点失败。**Node 1 项测试通过，内部四个场景均得到预期结果**；这是探针的确定性验证，不是四次自然 Vue 生成。

本轮执行 `node --test scripts/tests/reading-list-browser.test.mjs` 和两个模块的 `node --check`，均通过。没有修改 Java/Python 或前端生产源码，不重跑其单元全量、干净编译或前端构建；第 18 节的 1292 passed 等保持历史身份。AI README 和真实测试集仅补充当前验收设施及边界。

### 19.4 证据、收尾和下一步

私有证据位于 `tmp/vue-contract-real-20261111/`：summary.json、创建/生成独占标记、原 UI 报告、两份零模型观察报告、显式任务查询、被动模型/工具记录、下载比较材料、浏览器与服务日志。被动观测不改变生产消息、返回对象或重试。原失败运行器日志保留；凭据、日志、源码和截图不提交或输出。

收尾核对时，本轮记录的 Spring/Python/代理 PID 已不存在，8000/8123/18001/5174 无监听；本轮启动的 langgraph-postgres 已停止，5432 无监听。原 MySQL 3306、Redis 6379 保留。服务日志中此前三类 PostgreSQL 清理告警均未检出；本轮没有新取消或并发请求，不扩大其稳定性结论。前端客服三文件和原未跟踪 cancellation-drain-regression 计划保留，仅后端仓库提交本次探针、夹具和文档，未推送。

后续优先稳定第 18 节出现波动的重排器取消锁回归，先用明确同步条件捕获取消分支再判断是否存在生产缺陷；本轮没有修改或宣称修复它。自然新门禁拒绝→修复→交互、自然 REPAIR_NO_PROGRESS、全部停止失败时序、长期/跨实例并发和公网部署仍未覆盖。无需为触发这些分支重复采样；下一次业务上有价值的真实请求继续沿用 **136 次**账本。自动恢复和 Vue 回滚仍未实现。

## 20. 重排器取消阶段的确定性回归

2026-11-11 承接 `17353e8`。本轮处理第 18 节出现过一次失败的 `test_spawn_worker_cancelled_request_keeps_ipc_lock_until_shutdown`，没有扩展真实客服模型任务。新增模型调用 **0 次**，无上限账本仍 **136**。

### 20.1 已确认的问题与历史证据边界

原测试启动请求后等待 30ms 就取消，随后无条件要求 IPC 锁仍持有。源码实际区分阶段：发送取消会终止子进程并排空发送线程，最后释放锁；响应等待取消才把锁交给 drain，等待响应或进程退出后释放。固定等待无法区分这两种阶段。

本轮在真实发送线程入口用门闩固定取消点，沿真实 `_send` 取消收尾，确认子进程关闭、发送任务排空后，复用旧无条件持锁断言会稳定失败；同时固定进入接收后的原持锁断言通过。反例运行 **1 passed、1 failed**，保留在 `tmp/reranker-cancellation-before.log`。这证明旧断言范围过宽；第 18 节历史失败没有保存取消阶段，不能反推当时必然发生发送取消，也不能宣称修复了生产重排器根因。

### 20.2 测试改动

仅修改 `ai-service/tests/test_reranker.py`，生产 `reranker.py` 保持不变：

- 接收取消：等待实际进入 `_receive` 后取消，检查子进程仍存活、锁由 drain 持有，关闭并排空后释放。
- 发送取消：在发送线程入口挂起，收到连接关闭信号后确认线程未结束且请求未返回；释放门闩后确认进程关闭、发送任务排空、锁释放。
- 排队取消：确认第二个请求进入真实锁等待，再取消；原请求和所持锁不受影响。
- close 取消：等待真实关闭流程进入 `_abort_process` 后挂起，取消调用方，确认唯一清理任务仍运行；释放门闩并再次 close 后确认仅执行一次终止流程。

测试用 UUID 模型名称隔离操作系统所有者锁；finally 释放门闩、回收请求、关闭子进程并排空 sends/drains，断言失败也不遗留该测试的活动资源。5 秒等待和 10 秒门闩保护用于报告测试超时，不作为所处阶段的证据。四项定向验证通过后追加锁名称隔离，再进行最终验证。

使用真实 Windows spawn 子进程、Pipe 与操作系统锁，子进程是手写协议夹具，没有加载 CUDA/FlagEmbedding 重排模型或调用供应商。不能称为真实 GPU 重排、客服端到端或生产全部取消时序通过；生成工作流的无收尾强杀边界也没有改变。

### 20.3 本轮验证、提交与后续

在 `ai-service` 目录执行：

```powershell
uv run pytest tests/test_reranker.py -ra --tb=short
uv run python -m compileall -q src
uv lock --check
uv run pytest -ra --tb=short
```

最终重排器全组 **38 passed、2 warnings，104.41 秒**；Python 全量首次运行 **1294 passed、14 skipped、2 warnings，207.14 秒**，compileall 和锁文件检查通过。14 项为未启用的 PostgreSQL opt-in，两条既有 Starlette/LangGraph 弃用警告保留。本轮没有运行真实模型、GPU 推理、浏览器、Java 或前端构建，不把第 19 节真实应用通过结果记为新验证。

私有日志：`tmp/reranker-cancellation-before.log`、`tmp/reranker-cancellation-targeted.log`、`tmp/reranker-cancellation-suite.log`、`tmp/reranker-cancellation-full.log`；保留最初的断言反例，不提交日志。新增/修改的四个场景以 finally 和进程/任务断言确认本测试资源已回收。本轮未启动生成服务、代理或数据库，不停止用户原有服务；计数账本仍为 **136**。

两仓库仅后端有本轮测试/文档改动，前端客服三文件及原未跟踪 cancellation-drain-regression 计划保留，中文提交且不推送。下一步可按独立场景扩展跨标签取消与并发证据；不要为证明新 Vue 门禁的自然拒绝→修复或 REPAIR_NO_PROGRESS 无限采样。真实 GPU 重排、全部停止失败时序、长期/跨实例并发、公网部署仍未覆盖；自动恢复和 Vue 回滚仍未实现。

## 21. 同客户端线程复用下的旧租约迟到释放

2026-11-11 承接 `814e43f`。先核对两仓库状态、本文第 18–20 节、真实测试集、AI README、取消仲裁及 fencing 的 specs/plans，再选择现有测试未覆盖的“同 Redisson 客户端、同工作线程在失锁后取得后继租约”。新增真实模型 **0 次**，无供应商失败或重试；原账本保持 **136、max_calls=null**，未重跑已有真实生成提交标记。

### 21.1 失败证据与修复

原 `GenerationLeaseService.acquire` 以 Java 线程 ID 作为 Redisson 应用锁 owner；release 虽按 requestId 防止删除后继状态，finally 仍以原线程 ID 异步解锁。同客户端同线程先后处理请求时，两次租约共享锁身份。已有不同客户端测试不能揭示此问题，其检查也没有等待异步解锁完成。

新增 `GenerationTaskResourcesIT#sameClientSuccessorSurvivesLateUnlock`，两个明确顺序在未修改生产代码时均失败，断言为“旧解锁不得释放后继请求的锁”，不是超时或依赖失败：

1. 显式失效本测试旧锁，同客户端同线程取得新租约，再执行旧 release。
2. 在旧 release 调用 `unlockAsync` 的边界捕获命令，失效旧锁、取得新租约后才发送原命令。

两种情况都等待实际 Redis 命令完成后才检查锁；5 秒只是等待失败上限，不用固定 sleep 猜测时序。失锁由随机测试 appId 的 `forceUnlock` 控制，不代表自然网络故障，也不恢复真实任务。

修复仅改变 Spring 应用锁身份：静态负向 long 序列为每次获取提供独立逻辑 owner，达到下界失败而不复用；通过 Redisson 显式 owner 的无等待入口获取锁，正常释放及初始化失败释放都沿用该 owner。字段 `GenerationLease.ownerThreadId` 保留名称并说明含义，不再代表 Java 工作线程。Redis 客户端 ID 继续隔离客户端；watchdog、锁键、transition lock、MySQL fencing/仲裁/台账/心跳字段、三类生成和公共 SSE 协议未改。

修复后新锁与新 requestId 状态保持，第三请求仍被拒绝；旧取消/旧提交不能作用于新请求，两条执行记录仍为 RUNNING，不因失锁或 release 改写终态。新请求可执行受控提交动作，正常 release 完成后返回 IDLE。测试提交动作是固定返回值，没有生成或发布应用产物。

### 21.2 本轮实际验证

后端根目录执行，所有 Maven 原始输出保存在 ignored 的 `tmp/`：

```powershell
mvn clean -DskipTests compile
$env:AI_TASK_INTEGRATION = 'true'
mvn test '-Dtest=GenerationLeaseServiceTest,GenerationTaskResourcesIT#sameClientSuccessorSurvivesLateUnlock,AppServiceGenerationCancellationTest,AppGenerationTaskTest,GenerationTaskServiceTest,GenerationTaskMapperTest,GenerationTaskHeartbeatConfigurationTest,ArtifactPublicationServiceTest,ArtifactPublicationEvidenceTest,ArtifactPathResolverTest,LangGraphAiGenerationGatewayTest,DelegatingAiGenerationGatewayTest,AppControllerSseTest,GenerationJsonErrorBoundaryTest,VueProjectBuilderTest'
Remove-Item Env:AI_TASK_INTEGRATION
```

- 原实现新增两个资源时序：**2 failed、0 errors**，保留 `tmp/lease-owner-before.log`。
- 修复后首次定向：租约单元 13 项与真实资源 2 项，**15 passed**，日志 `tmp/lease-owner-targeted.log`。
- 补充正常 release 完成后的 IDLE 断言后，最终限定回归 **147 passed、0 failed、0 skipped**，含两个新增真实 MySQL/Redis 时序；日志 `tmp/lease-owner-regression.log`。
- 干净编译通过，日志 `tmp/lease-owner-clean-compile.log`；已有弃用/unchecked 编译提示保留。
- 单元覆盖逻辑 owner 在同客户端不同服务对象中不复用、忙时拒绝、代际签发失败/状态初始化失败按所获 owner 释放，以及原台账、仲裁、发布、取消、网关与心跳回归。

真实资源测试使用现有本地夹具的真实 Mapper/XML、事务和两个 Redisson 客户端，仅选新增两个时序；其他 `GenerationTaskResourcesIT` 场景本轮未重跑。模型、Spring/Python HTTP、浏览器与自然供应商不参与。Vue 构建器单元使用测试 runner，不能据此称实际 npm 构建或 Vue 功能通过。

Python 源码与配置未变，没有重跑 compileall/pytest/uv lock、PostgreSQL 集成或前端测试/构建。第 20 节 1294 passed、第 19 节真实浏览器结果均保持历史身份。AI README 只同步 Spring 边界说明。

### 21.3 收尾与下一步

测试前确认仅原 MySQL 3306 与 Redis 6379 监听，核对对应进程；未启动或停止 Spring、Python、模型代理、PostgreSQL、Vite 或 Chrome。测试自己的 Redis 客户端由夹具关闭，捕获的解锁命令在 finally 排空，随机测试行及精确键由既有 AfterEach 清理；未做 DDL、清空数据库或读写历史生成项目。

前端客服三文件及后端原未跟踪 `docs/superpowers/plans/2026-11-11-cancellation-drain-regression.md` 保留。仅后端本次 Java/测试/文档提交，中文 Conventional Commits，不推送；无凭据、日志、生成源码或截图入库。

下一步优先评估**跨标签停止请求绑定**：当前 `AppGenerationCancelRequest` 仅接收 appId，Spring 选择调用时的活动 requestId。历史主动取消设计中的“因此可以防止旧页面取消新任务”没有客户端请求身份比较支撑，已补充说明；本轮不宣称该行为已修复。应先用独立标签/请求门闩建立旧停止请求在新任务建立后抵达的证据，再设计兼容的目标绑定与 UI 状态同步。

仍未覆盖自然新门禁拒绝→修复→浏览器交互、自然 REPAIR_NO_PROGRESS、全部停止失败时序、长期/跨实例压力、自然网络失锁、真实 GPU 和公网部署。自动恢复、任务重新认领、SSE 重连与 Vue 回滚仍未实现。回退本轮 owner 分配无需数据库迁移，但会重新暴露已复现窗口；不能把 Redis 无锁作为恢复授权。

## 22. Vue 自然门禁拒绝、修复阶段纠正与后续功能验收

2026-11-12 承接 `de925f3`，按用户最新范围优先处理 Vue 功能与有限修复，不继续跨标签、长期压力、跨实例或恢复。开始时后端在 dev，仅有原未跟踪 cancellation-drain-regression 计划；前端仍是指定的三个客服修改。先核对第 18–21 节、真实测试集、AI README、必填属性与浏览器探针设计/计划，并阅读现有确定性测试及实际构建路由。

### 22.1 原控制流与本轮自然失败

原测试已覆盖构建错误传入修复、重新构建后才审查、两次修复耗尽及检查器不可用直接失败。源码确认每轮 project_build 使用修复次数生成不同工具调用 ID，调用强制构建，不走已有 dist 快路径。因此先执行一个新的隔离应用，不为命中门禁无限采样。

沿用第 19 节固定“慢读书房”需求，新应用 `478319810483597312`，只创建一次、只提交一次从零生成：

| 请求 | 模型调用 | 实际结果 |
| --- | --- | --- |
| 原从零生成 `2fb6a47d-e510-44cd-a9f5-26ad3e1b973d` | 创建 2 + 请求内 31，共 33；136→169 | 首次 npm 构建后 AST 拒绝 FilterBar 缺少 filters/active/total/readCount，进入第一次修复，最终 FAILED / VUE_TOOL_CALL_LIMIT_EXCEEDED |
| 后续明确用户修复 `336490b9-1fb2-49a0-bf61-8c0410ccad4c` | 28；169→197 | 两次构建及门禁通过，真实 SCOPE_VIOLATION → 修复 → PASS，SUCCEEDED |

原请求使用 9 次生成工具及 31 次修复工具，最后一个越界工具请求未执行；21 轮修复模型调用始终没有交回验证子图，因此只有一次构建、没有 Reviewer、没有浏览器功能检查。前端收到一个业务错误、done=0、无传输错误、预览刷新=0；显式查询 heartbeat=TERMINAL、publicationState=NOT_VERSIONED。保留原失败 UI 报告、退出码 1、模型/工具观测和提交标记。

这是本轮首次取得的**自然 AST 拒绝→进入修复**证据，但没有取得该请求的自动修复→重建→交互成功证据。工具内出现往返修改及重复读取，不等于验证子图的 `REPAIR_NO_PROGRESS`；原机制没有在工具循环中做指纹判重，本轮未扩大该机制或预算。

### 22.2 明确发现的问题与最小修复

旁路模型输入显示：首次生成时 currentArtifact.exists=false 被完整保留，修复子图又初始化空 filePlan，原阶段选择遂向修复模型发送 plan_files；原生成的 planningFeedback 和 vueFormatFeedback 也继续传入。它们指向已结束的规划/重试阶段，与实际修复任务不一致。不能据此断言模型反复读写只有这一原因，或纠正阶段提示就必然收敛。

仅修改两个 Python 编排文件：

- `vue_tool_loop.py`：无本阶段 filePlan 时，repair 使用 modify_files；已有产物生成原逻辑不变，有本阶段新规划仍为 write_files。不把原始 currentArtifact 伪造为存在。
- `subgraphs/vue.py`：新修复初始化时移除 planningFeedback、batchRetryFeedback、vueFormatFeedback。保留原文、准备结果、构建/校验诊断、完整工具历史、修复次数和累计工具预算；本阶段新格式反馈仍按原机制保留。恢复现有子图不重新初始化。

新增 `test_vue_repair_phase.py` 四场景，交叉初始/已有产物与首次/第二次修复。旧代码四项均失败：空基线错误 phase，已有基线仍携带旧反馈；修复后验证新格式重试反馈继续可见、历史保留、33→33→32 剩余预算、一次实际工具调用及修复次数。使用 Fake 模型/工具，不能当作真实生成质量证据。

没有更改提示词、模型参数、工具/修复预算、24 KiB 投影、静态生成、公共 SSE 或前端生产代码。设计与执行记录见 `docs/superpowers/specs/2026-11-12-vue-repair-phase-design.md`、`docs/superpowers/plans/2026-11-12-vue-functional-validation.md`。

### 22.3 后续用户修复与浏览器证据

保留原失败请求后，对同一隔离应用提交新的普通修复需求，明确要求恢复添加、空白拒绝、已读切换、三筛选及总量计数，核对父子组件接线，保留文字/样式，不添加外部图片。使用新 requestId、新独占标记和同一账本；未重发原生成，不手改供应商返回或生成源码，不恢复旧图，也没有新建第二个从零样本。

该请求的两次 project_build 均 built=true，工具 ID 分别以 `:project_build:0`、`:project_build:1` 结尾。单 Reviewer 自然返回一次 SCOPE_VIOLATION，修复后 PASS；本次修复的 10 轮模型输入均为 modify_files，首轮无上一阶段规划/批次/格式反馈。生成/修复总计实际使用 30 次工具。AST 成功统计为 checkedComponents=4、checkedUsages=2、skippedFiles=0、skippedUsages=1、truncated=false；跳过用法不计入通过范围。

显式任务查询为 SUCCEEDED / TERMINAL / NOT_VERSIONED。真实前端原生 SSE 唯一 done、无业务/传输错误，当前生成后预览刷新一次（页面进入时原预览加载另计）。原 UI 报告 **53 项通过**，其中预览与本机部署共享探针 **44 项、16 项计数**：添加、空白拒绝、已读及未读切换、全部/未读/已读筛选和总数一致；另有无 img、下载非空及 390px 无横向溢出检查。没有新增源码 ZIP 与快照逐字节核对，不能沿用历史下载内容通过结论。

原 UI 报告保存后 Chrome 超过一分钟仍未退出，确认专用 profile 归属后停止，运行器退出 **1**；该失败记录保留，不能称原运行器完整通过。私有 HTML 运行器在保存最终报告后移除测试 iframe，停止其后台活动；随后一次**零模型**观察正常退出 **0**，49 项检查通过，含相同 44 项共享探针和 16 项计数。未更改生产前端或探针断言，也不把一次收尾成功称为所有浏览器生命周期均已修复。

该请求证明一次后续用户修复及自然 Reviewer 修复后的功能可用，**不证明原门禁失败请求自动修复成功，也不是修复前后受控模型对照**。本轮没有继续抽样以追求自然门禁拒绝→修复→重建→交互全链路成功。

### 22.4 本轮验证与调用账本

- Python 修复前四个新场景失败，修复后定向 **20 passed**；全量 **1298 passed、14 skipped、2 warnings，206.33 秒**。compileall 与 uv lock --check 通过。14 项 PostgreSQL opt-in 未开启，两条既有弃用警告保留；运行期使用真实 PostgreSQL 不等于运行过这些集成测试。
- 维护开始时另执行原 Python 构建修复/检查器定向 **5 passed**，Node AST **21 passed**，Java VueProjectBuilderTest/VueComponentContractCheckTest **28 passed、0 skipped**（含真实 Node opt-in），共享探针测试 **1 passed**（内部四种手写夹具）。Java 无代码变动，未执行干净编译；未修改前端生产代码，未重跑前端构建。
- 原账本 `tmp/real-e2e-unlimited-20261111/model-ledger.json` **136→197，新增 61，max_calls=null**。两次生成请求分别有一次 Vue 非法 JSON 格式重试，该轮未执行工具；共 61 次调用均有 HTTP 200 记录，没有供应商失败/重试。第一次业务失败不能因供应商 HTTP 成功改记通过。旧 60/20/30 次账本保持不变。
- 两段 Python 服务日志中 `Explicit rollback() forbidden`、`another command is already in progress`、`discarding closed connection` 均为 0；没有新取消/并发样本，不能扩大稳定性结论。

复现命令（后端根目录运行 Java/Node，Python 在 ai-service 目录）：

```powershell
$env:VUE_CHECK_COMPILER_ROOT = 'D:/VibeForge/yu-ai-code-mother-frontend'
node --test scripts/tests/vue-component-contract.test.cjs
$env:VUE_COMPONENT_INTEGRATION = 'true'
mvn test '-Dtest=VueProjectBuilderTest,VueComponentContractCheckTest'
node --test scripts/tests/reading-list-browser.test.mjs
uv run pytest tests/test_vue_repair_phase.py tests/test_vue_subgraph.py tests/test_vue_component_check.py 'tests/test_api.py::test_vue_build_failure_is_repaired_and_rebuilt_before_review' 'tests/test_api.py::test_vue_build_failure_exhausts_two_repairs_without_review_or_completion' -q
uv run python -m compileall -q src
uv lock --check
uv run pytest -ra --tb=short
```

### 22.5 环境、证据与后续边界

启动前仅 MySQL 3306 监听；Docker 未运行、Redis 未监听。Docker 启动被昨日残留的本地 socket 阻塞，先保存日志，再改名保留仅含 socket 的目录；没有重置 Docker 或修改数据库配置。引擎可用后容器/镜像/卷列表为空，未找到旧 langgraph-postgres；本轮创建隔离 `vue-functional-postgres-20261112`（PostgreSQL 16），使用本地配置的独立 checkpoint 库，不宣称恢复了历史 checkpoint。Redis 随后恢复监听，保留用户原 MySQL/Redis。

私有证据分置 `tmp/vue-functional-20261112/` 与 `tmp/vue-functional-continuation-20261112/`，原 Python 定向日志另在 `tmp/vue-functional-20261112-python-targeted.log`；包括原始失败、调用观测、明确任务查询、后续修复与零模型报告。凭据、运行日志、源码、下载及截图不输出或提交。隔离应用及失败记录保留；本轮专用服务和容器按实际进程归属收尾，容器数据保留供后续核对。

下一步若继续优化，应依据本轮工具内重复读取/往返修改证据单独评估收敛策略；不要把本次阶段提示修正当作已解决所有工具浪费，也不要直接将固定读书清单探针加入生产。原自然 AST 拒绝后的自动修复成功、自然 REPAIR_NO_PROGRESS、所有交互及动态组件语义仍未验证。跨标签、长期/跨实例压力、生产恢复和公网部署继续不在本轮范围。

## 23. 修复重复读取交回验证与每轮文字交付要求

2026-11-12 承接 `4c0851e`。本轮按用户要求，在文档开头新增每轮结束必须文字说明“本轮优化、原因、问题场景、验证限制、下一轮建议及原因”的硬性要求，并同步 AGENTS。保持原本地功能优先范围。

### 23.1 为什么需要优化、何时会出问题

重新核对第 22 节原失败请求的私有完整工具结果：修复工具 20 修改后，21–30 持续读取 App.vue 和 FilterBar.vue，同路径内容各自保持一致。模型没有停止工具调用，导致还未重新构建就用尽额度。上轮阶段提示修正不能确定性阻止此类没有新信息的反复确认。

本轮选择只处理重复完整读取；没有提高预算，没有跳过真实读取，没有用固定读书清单功能探针充当生产门禁。往返写入的风险不同，暂不混入同一策略。

### 23.2 本轮改动及实际作用

新增 `orchestration/vue_read_stall.py`。正式 Vue 修复阶段显式成功写入后，当前连续读取段内出现连续四次“同路径、同完整内容”重读，且纯读取批次已全部执行、无待写规划、配图条件满足时，结束工具子图并交回原验证/构建/审查。读取新路径或变化内容打断计数；非读取、失败、未知字段、截断、超界结果清空本段记录。单份参与内容上限 12000 字符/48000 UTF-8 字节。

按完整工具 ID 的阶段前缀隔离历史，不新增 checkpoint 状态字段，仅从现有私有历史推导；不输出源码/哈希/路径。已发出的读取照常实际执行并计入总预算。生成阶段和旧内部兼容入口不启用；取消仍在判断前传播，同批次后续写入不能跳过。两次修复、40 次工具、24 KiB 模型投影与公共 SSE 不变。

这让系统有机会把已修改的文件交给构建检查，不代表文件已正确、模型已理解源码或全工程没有变化。构建和 Reviewer 仍可失败，也不能替代浏览器交互。该策略不是 `REPAIR_NO_PROGRESS`，不处理所有写入往返或一般语义循环。

### 23.3 证据及验证身份

新增两个父图回归：Fake 模型第一次修复写入后反复读取同一对文件。旧实现两项都失败于工具额度耗尽；新实现每轮修复实际执行 1 次写入和 6 次读取后返回构建。可修复夹具完成第二次构建和审查后成功；不可修复夹具两轮共 14 次工具后仍以 VUE_COMPONENT_CONTRACT_FAILED 失败，构建三次、Reviewer 零次，唯一失败终态。

边界覆盖新文件/内容变化、未知/截断/失败结果、缺少显式成功写入、阶段隔离、混合批次/未执行写入、待写文件/配图和取消先行。第一次扩展测试两项失败：阶段前缀匹配过宽已改成完整前缀相等；取消夹具原按异常正文匹配，但 GenerationCancelled 无正文，改为核对精确异常类型。中间失败日志保留。最终定向 45 passed，compileall 和 uv lock --check 通过。

零模型历史回放首次在原请求修复工具序号 **26** 后满足条件；原请求实际执行至 31 后耗尽总预算。回放没有执行反事实构建、模型或浏览器，不能声称减少了五次实际调用或修复了原应用。原失败报告保持。

证据位于 ignored 的 `tmp/vue-read-stall-before.log`、`vue-read-stall-first.log`、`vue-read-stall-targeted.log`、`vue-read-stall-targeted-final.log`、`vue-read-stall-full.log`、`vue-read-stall-lock.log`、`vue-read-stall-replay.json`。生产与测试代码不包含历史生成源码，私有证据不提交。

直接执行全量首次为 **5 failed、1318 passed、14 skipped**：一个客服关闭测试继承了本机开启配置，四个手写重排器进程测试遇到默认模型 owner 锁已占用（本机同时运行着 Python 服务，但没有进一步追查具体持锁进程）；错误为 CUSTOMER_SERVICE_RERANKER_UNAVAILABLE，其中子进程记录明确落在 `_GpuOwnerLock.acquire`。本轮没有停止用户服务或修改 .env。后续仅对测试进程设置 `AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED=false`，通过 ignored 的 `tmp/vue_read_stall_test_isolation.py` pytest 插件为这四个夹具的 rag_settings 注入每测试唯一的模型标识，保持同一测试内争锁断言不变，不加载真实 GPU 模型。该隔离运行不等同于默认环境裸命令全通过。第一次运行隔离脚本时工作目录路径写错，退出 2，日志 `vue-read-stall-full-isolated.log` 保留；修正后的独立输出为 `vue-read-stall-full-isolated-rerun.log`。

本轮真实模型新增 **0 次**，无供应商失败或重试；原无上限账本仍 **197、max_calls=null**。没有新的浏览器、npm、Java 或真实资源集成验收，不沿用第 22 节的浏览器成绩。收尾核对看到已有 Spring 8123、Python 8000、PostgreSQL 5432、MySQL 3306、Redis 6379，本轮未启动或停止这些服务；正在运行的 Python 是否加载新代码未验证，需要用户在适当时机重启以生效。前端三处客服修改及原未跟踪 cancellation-drain-regression 计划保留。

最终隔离全量为 **1323 passed、14 skipped、2 warnings，147.52 秒**，退出 0。新增测试 25 项；14 个 PostgreSQL opt-in 未启用，两条既有弃用警告保留。常规命令为 `uv run python -m compileall -q src`、`uv lock --check`、`uv run pytest -ra --tb=short`；本机已有服务运行时，本轮完整成功命令为 ai-service 目录下 `uv run python ../tmp/vue_read_stall_test_isolation.py`，其临时隔离步骤如上，不作为通用生产启动脚本。无需真实资源的定向命令为 `uv run pytest tests/test_vue_repair_read_stall.py tests/test_vue_subgraph.py tests/test_vue_repair_phase.py tests/test_vue_context.py -ra --tb=short`。

### 23.4 下一轮建议及仍可能失败的场景

优先评估**修复工具内的往返写入 A→B→A**：模型在两个版本间反复修改时，每次写入都会重置本轮读取计数，因此仍可能耗尽额度。应先定义足以判断当前文件集合及版本一致的证据，并用“合法多文件联动修改”反例验证，再决定是否提前交回构建；不能只靠单个写入文本或局部 hash 直接判定全工程无进展，也不能新增自动成功。

下一轮无需重跑已提交的自然生成样本来追求触发。当前自然门禁拒绝后的自动修复→重新构建→浏览器成功仍未证明；本轮受控测试不填补该空白。用户若继续本地功能验收，应使用独立新请求、原账本和明确交互断言。跨标签、长期/跨实例压力、生产恢复和公网部署继续不属于当前优先范围。

## 24. 实现说明要求与往返写入优化方案评估

2026-11-12 承接 `c741857`。本轮新增每次交付必须解释实现方式、多个可行方案必须先让用户确认的要求，并同步 AGENTS。源码与文档核对后继续评估第 23 节建议的工具循环内 A→B→A；尚未实施新生产行为，待用户选择方案。

### 24.1 问题场景与现有实现

模型把同一组件从版本 A 改为 B，又改回 A，期间不断读取确认时，上一轮 `vue_read_stall.py` 会因写入清空连续读取计数，可能一直到工具预算耗尽。现有 `repair_progress.py` 仅在验证阶段比较源码快照，工具循环不退出就无法执行该检查。历史私有分析中有 7 次 file_modify 请求，其中 4 次与先前补丁互为逆操作；这只证明补丁往返，不能证明整套源码回到旧版本。

### 24.2 待确认方案

**A：对修复批次后的源码快照判重，推荐。** 每个包含成功写入的完整批次结束后，通过已有 Spring `vue_source_snapshot` 读取有界源码集合；只对明确完整、非空的文件集合计算指纹，识别修复阶段内 A→B→A 或重复集合。命中且无待写文件/配图要求时交回原构建和审查，不直接成功、不复用验证阶段的失败结论。保存的是私有阶段指纹，日志不包含源码；不改变模型工具与修复预算。代价是增加内部 HTTP/文件扫描，快照最多 24 文件、单文件 12000 字符、总计 60000 字符，截断或不完整不能用于提前退出；它仍不是全工程原子快照或页面功能证明。需验证其他文件正在推进时不会因单文件回退而误触发，并定义取消、快照故障及阶段恢复边界。

**B：仅识别逆向补丁，给模型一次有界提醒。** 在修复阶段保存已确认成功的 file_modify 参数指纹，发现同一路径 old/new 往返时，提示模型避免继续改回旧方案并在完成后交回检查。无需新增源码扫描，改动较轻；但只看局部补丁，无法可靠判断多文件进展，也无法强制模型停止，可能依旧耗尽预算，file_write 整文件往返也不能由该方案自然覆盖。不能把提醒触发当作修复成功。

推荐 A，因为当前问题是模型不退出工具循环，需要比局部补丁更可靠的源码集合证据。以上是待确认设计方向，不是已实现或已验证能力。用户确认后再细化设计、补旧代码失败回归、实施及验证；不同时默认实施两种方案。

### 24.3 本轮交付与下一步

本轮仅修改交接要求与方案记录，已核对对应源码和工具边界，执行 Git 空白及提交范围检查；不为文档更新运行模型、浏览器或测试全量。模型新增 0，账本仍 197、max_calls=null；未启停服务，前端三个客服修改和后端原未跟踪计划保持。下一步由用户确认 A/B 后实施，原因是两者在额外扫描成本、证据可靠性及是否能确定性退出方面有实际取舍。

## 25. 方案 A：Vue 修复往返写入交回验证

2026-11-12 承接 `b92c45d`，用户明确“按照方案A执行”后实施。第 24 节待确认保留为历史记录，本节记录已选 A；没有同时实现 B，也没有扩大到跨标签、生产恢复或公网部署。顶部的每轮文字总结、实现解释、下一轮建议及多方案先确认要求继续有效。

### 25.1 为什么优化及如何实现

具体问题是模型在修复工具内把组件反复从 A 改成 B 再改回 A。写入会重置连续读取计数，验证阶段指纹又只有工具循环结束后才执行，因此可能用尽 40 次工具仍没有重新构建。历史逆补丁只能说明局部往返，本轮没有把它伪装成全项目相同或自然触发证据。

本轮只改 Python 的 `vue_tool_loop.py` 与正式子图开关 `subgraphs/vue.py`：

1. 每个新批次重置成功写入标记；只有 file_write/file_modify 明确返回 `ok is True` 才置位，未知或失败不算。
2. 等同批次所有工具完成（包括后续修改、读取或删除），经现有 Spring 网关调用 vue_source_snapshot。调用 ID 带本阶段 prefix 及批末 ordinal；Python 不直接读取项目目录。
3. 复用 repair_progress 的完整集合指纹函数，比较排序后的路径及内容；只接受明确完整、非空、计数一致且每文件未截断的快照。其他文件变化、文件增删或改名也会改变指纹，不能因单组件回退就提前退出。
4. 指纹列表保存在本阶段私有 vue_loop；出现已见集合，且 filePlan 无待写文件、配图已满足时结束工具阶段，交回原硬校验、强制构建及审查。不直接成功、不产生 REPAIR_NO_PROGRESS、不增加修复或工具预算。

选择完整集合而非逆补丁，是为了同时覆盖 file_write/file_modify，并避免把合法多文件推进误判为停滞。代价是每个成功写入批次增加一次内部 HTTP/扫描，正常执行的次数受原工具批次预算约束；内部快照不进入模型工具历史、也不计入模型 40 次工具数。事件仍只有快照计数，退出日志只记录固定原因 REPEATED_SOURCE_SNAPSHOT，不新增事件类型、不记录源码/指纹。

### 25.2 故障、恢复和不适用边界

每次新修复初始化清空本阶段指纹；checkpoint 保存批次成功标记和指纹，恢复不重新开始该阶段。已保存 execute_tool 后恢复不重做写入；已保存 continue_loop 后恢复保留已见集合。扫描后、节点结果保存前中断可能再次取实时快照，不保证扫描恰好一次；这是既有测试内存 checkpoint 的显式恢复验证，不是生产自动续跑。

快照调用错误沿原工具失败路径传播，不吞取消、鉴权或环境故障，不额外重试或让模型修环境；扫描前后检查协作取消。空、未知或截断快照不记录、不触发退出，但保留更早的有效指纹供未来完整集合比较，不推断中间版本。首次取证在首次成功写入批次后，不补读阶段开始基线。

快照仍限 24 文件、单文件 12000 字符、总 60000 字符，不是全工程原子一致性证明。持续变化、单独删除、失败写入或快照截断仍可能耗尽预算；计划/图片未完成也不会提前退出。源码重复不证明功能正确，交回后原验证仍可构建失败、审查拒绝，或按已有验证指纹报 REPAIR_NO_PROGRESS。Vue 不回滚，生成阶段和旧内部兼容入口不启用新保护。

### 25.3 本轮实际验证与证据身份

新增 `test_vue_repair_write_cycle.py` 45 个 Fake/受控场景，不调用真实模型、Spring HTTP、npm 或浏览器。父图使用真实 LangGraph 调度，但模型、网关和构建结果为夹具。

- 修改前最初三个回归均失败，两个 A→B→A 父图场景都耗尽工具预算，不能重新构建；日志 `tmp/vue-write-cycle-before.log` 保留。
- 修改后 A→B→A 每阶段三次写入交回验证。可修复夹具第二次构建和一次审查后成功；不可修复夹具两个阶段共六次写入、三次构建后以 VUE_COMPONENT_CONTRACT_FAILED 失败，Reviewer 零次，唯一失败终态。第二阶段也完成三次写入，证明阶段指纹不串用。
- 相同连续写入两批即交回验证；生成及兼容入口不扫描。多文件持续推进、同批后续删除、显式成功、计划/配图、无效快照、故障/取消、共享预算及两种 checkpoint 边界覆盖。
- 首次定向 **81 passed**；补齐生成/兼容及删除批次后最终定向 **85 passed、2 warnings**，日志 `tmp/vue-write-cycle-targeted.log`、`tmp/vue-write-cycle-targeted-final.log`。
- compileall 与 uv lock --check 通过。首次隔离全量 **1 failed、1363 passed、14 skipped、2 warnings**：旧 API 测试的调用序列未计入新增内部快照；终态与构建正常。已更新该断言并明确新调用 ID，仍要求模型工具计数为 2；原日志 `tmp/vue-write-cycle-full-isolated.log` 保留。

全量沿用第 23 节 ignored 的 `tmp/vue_read_stall_test_isolation.py`：只对测试进程关闭本机 RAG 开关、为四个进程测试分配唯一假模型锁标识，同一测试内争锁条件保持，不载入模型、不修改 .env。不能描述为默认本机配置裸命令全通过。最终隔离全量 **1368 passed、14 skipped、2 warnings，133.41 秒**，退出 0，日志 `tmp/vue-write-cycle-full-isolated-final.log`。14 项为未启用的 PostgreSQL opt-in，两个既有弃用警告保留。首次全量收集了新增测试中的 41 项，随后补齐四项边界；最终全量包含全部新增 45 项。

命令（ai-service 目录）：

```powershell
uv run pytest tests/test_vue_repair_write_cycle.py tests/test_vue_repair_read_stall.py tests/test_vue_subgraph.py tests/test_vue_repair_phase.py -ra --tb=short
uv run python -m compileall -q src
uv lock --check
uv run python ../tmp/vue_read_stall_test_isolation.py
```

### 25.4 交付、下一轮建议及未覆盖项

本轮真实模型新增 **0 次**，无供应商失败或重试；沿用账本 `tmp/real-e2e-unlimited-20261111/model-ledger.json`，核对仍为 **used=197、max_calls=null**，旧账本未动。没有新增自然模型、浏览器交互、npm、Java、前端构建或 PostgreSQL opt-in 验收，不沿用旧成绩。

端口及进程核对看到已有 Java/Spring 8123、Python 8000、Docker PostgreSQL 5432、MySQL 3306、Redis 6379；本轮未启动、停止或重启这些服务，没有验证运行中的 Python 已加载新代码。下一次真实验收前应排空并重启加载，再重新预检端口、配置和登录。前端原客服三文件及后端原未跟踪 cancellation-drain-regression 计划保留；本轮仅后端相关生产、测试及文档提交，不推送，私有日志与生成产物不入库。

下一轮优先做**一次新的隔离 Vue 本地功能验收**，沿用原账本和固定读书清单探针，用有业务价值的新请求观察自然生成及修复是否能交回构建。理由是修复阶段提示、重复读取和往返写入三个已知阻碍已有确定性保护，继续凭猜测叠加策略不如确认真实页面行为。重点验证添加、空白拒绝、已读切换、三种筛选和计数；即便构建与 Reviewer 通过，事件接线、筛选和计数仍可能失效。

自然未触发门禁或新退出原因就明确记未触发，不重发旧提交标记、不无限采样、不篡改源码制造自然成功。当前“自然 AST 拒绝→同一请求自动修复→重建→浏览器成功”仍未补齐，本轮 Fake 成功不能填补。若真实功能失败，先保存证据、定位最小缺陷；出现多个有实际取舍的改造方案，再按顶部硬性要求解释并等待用户选择。

## 26. 方案 A 后的自然 Vue 本地功能验收

2026-11-12 承接 17579f0，按用户“根据交接文档继续执行下一轮的优化”执行第 25 节建议：一个新隔离应用、一次自然从零生成及浏览器功能检查。本轮未发现该样本的功能缺陷，未为增加改动修改生产控制流。执行计划为 docs/superpowers/plans/2026-11-12-vue-write-cycle-real-validation.md。

### 26.1 选择原因与执行方式

前面三轮已纠正修复阶段提示、重复读取及往返写入不能及时退出的问题，但 Fake 回归不能证明真实页面好用；过去出现过构建成功、Reviewer PASS 而筛选按钮缺失。因此本轮直接检查新页面的真实交互。

预检确认原 8123/8000 是已有 Spring/Python 服务，未停止或复用为本轮模型验收服务。本轮使用临时 Spring 18123、Python 18000、计数代理 18001，加载当前工作区源码，使用原 MySQL、Redis、PostgreSQL 和同一无上限模型账本。只为新 appId 工作，不验证跨实例并发、恢复或压力。原配置文件不改，代理及模型/工具旁路观察脚本仅位于 ignored 私有目录，原样传递模型与工具返回。

需求仍为固定“慢读书房”：初始三本未读书、添加及空白拒绝、已读切换、三种筛选、总量/已读计数、不配图。本轮明确计数文案“共 N 本 · 已读 M 本”，与既有固定探针约定一致，避免把文案差异误报为逻辑错误；不是重发旧提交标记，也未诱导模型制造必填属性缺失。

创建及生成均在发送前独占保存新标记。生成通过真实 Spring HTTP/SSE，SSE 解析复用 scripts/structured_edit_acceptance.py 的摘要类；其原 60 次限额生产脚本未修改，私有运行器直接读取同一无上限账本。浏览器通过本地 HTTP 转发原始预览/部署响应，让同源 iframe 可使用已有 verifyReadingList；没有改写生成源码、响应或探针断言。浏览器完成后移除 iframe 并等待 Chrome 正常退出。

### 26.2 自然生成和浏览器结果

- 新 appId：478367931301433344。
- 新 requestId：5ed232d5-563d-499d-be75-3646536b4897。
- 真实 HTTP/SSE：HTTP 200，done=1、business-error=0、invalidFrame=0、唯一终态；生成请求约 59.23 秒。
- 用上述明确 requestId 再查询：SUCCEEDED / TERMINAL / NOT_VERSIONED。SSE 摘要未提取 requestId，先从隔离应用最新台账取得，再显式确认，没有将旧任务冒认为本次任务。
- 一次 project_build，工具 ID 为该 requestId 加 :project_build:0，built=true；实际 npm、Spring AST 与 PostgreSQL 参与。AST 日志统计 checkedComponents=3、checkedUsages=1、skippedFiles=0、skippedUsages=1、truncated=false，跳过用法不计入通过。
- 单 Reviewer PASS、issues=0；修复模型轮次为 0，未触发 REPEATED_SOURCE_SNAPSHOT、REPEATED_IDENTICAL_READS 或 REPAIR_NO_PROGRESS，也没有自然门禁拒绝。
- 真实浏览器预览和本机部署共 **46 项通过**：共享探针 **44 项（含 16 项计数）**，另两项 390px 无横向溢出。添加、空白拒绝、已读/未读切换、全部/未读/已读筛选和全局计数均按操作后的 DOM 验证。
- Chrome 与本轮浏览器运行器退出 **0**，forced=false，无需终止 Chrome；没有覆盖或改写历史退出 1 的报告。

开始前重新执行 node --test scripts/tests/reading-list-browser.test.mjs，**1 个测试通过**，内部四个手写夹具验证合法页面通过，以及缺按钮、静态计数和无效筛选在预期步骤失败。它是受控探针验证，和上述新应用自然结果分开记录。

### 26.3 模型账本、验证边界与收尾

原账本 tmp/real-e2e-unlimited-20261111/model-ledger.json：**197→209，增量 12，max_calls=null**。创建 2 次、生成请求 10 次；12 个模型代理 HTTP 响应均为 200，无供应商失败/重试记录。生成阶段有 **1 次 JSON 格式重试**，已计入上述调用，不隐去。普通生成/审查适配器旁路文件只有 9 份，路由/审核等入口并非全部经过该观察函数；总调用数以持久化账本和 HTTP 观测为准，不能用 9 份文件替代。旧三个有限额度账本哈希核对不变。

私有证据位于 tmp/vue-write-cycle-real-20261112/：创建/生成标记、generation-report、explicit-task、工具/模型观察、browser-report、browser-exit、acceptance-summary 和 cleanup-summary；完整日志、源码及凭据不输出或提交。本轮未请求截图或下载产物，也未检查下载内容。临时脚本编写期间的工具调用语法错误发生在实际执行前，没有模型调用或供应商请求重试。

本轮只有计划和验收文档入库，生产 AI 行为未改变，因此不重跑 Python 全量、Java 单元/干净编译或前端构建；第 25 节 1368 passed 不改记为本轮结果。Spring 启动执行 Maven 编译并真实构建了新生成工程，但不将其称为干净 Java 编译验证。未运行 AppChat 原生 UI/SSE 联动，不能宣称其预览刷新次数、停止或下载行为已重验。没有真实修复触发，所以自然 AST 拒绝→同请求自动修复→重建→浏览器成功仍未补齐。

终态与部署/浏览器结束后，按本轮启动根 PID 和实时父子进程树核对归属，只关闭本轮临时 Spring/Python/代理；18123/18000/18001 已释放。原 8123/8000、MySQL3306、Redis6379、PostgreSQL5432 保持；原 Python 未重启，不声称日常服务已加载前几轮新代码。原前端客服三文件和后端原未跟踪 cancellation-drain-regression 计划保持。本轮仅后端文档提交，不推送。

### 26.4 下一轮值得做什么

建议优先验证**已有 Vue 页面追加一项会影响列表状态的功能后，原操作仍正确**，例如为书籍增加删除能力，并检查删除已读/未读书后总量、已读数和当前筛选同步。原因是本轮只证明从零生成样本好用；多轮增量修改可能接错组件事件、只删除筛选后的显示副本、或漏更新全局计数，即使构建与 Reviewer 通过仍会给用户错误结果。

应在新的隔离应用/明确新请求中定义该增量需求与专用交互断言，不直接将固定读书清单探针扩充为通用生产门禁，不修改历史验收应用冒充本轮结果。先围绕可复现的业务变化验证，有实际缺陷再提出最小修复；多个有取舍的方案按顶部要求确认。无需继续采样追求自然拒绝或方案 A 命中，跨标签、长期压力、生产恢复和公网部署仍不属于当前范围。

## 27. 删除增量探针与基线自然失败（待选择后续路径）

2026-11-12 承接 f8cc4af，用户要求按交接继续。新增固定业务删除验收设施，但自然基线请求在修复后遭遇 Python 验收进程退出，删除增量请求尚未发送，本轮不能称为删除功能真实验收完成。

### 27.1 本轮改进及实现

问题场景是已有列表新增删除能力后，可能只改了当前筛选视图、没有更新已读总数，或按筛选索引删错全局书籍。新增 scripts/acceptance_reading_delete_checks.mjs，先运行原 verifyReadingList 保证添加、空白拒绝、切换和三筛选保持，再依次验证已读筛选删除、未读筛选删除、切回全部、删空后添加及切换。每步核对指定书名是否消失、剩余书名及全局计数，不仅看按钮数量。

探针从唯一书名叶节点向上最多八层，寻找恰好一个状态按钮和删除按钮的最小容器；歧义失败，不任取元素。结果仅固定步骤与计数。这是固定需求的同源隔离验收模块，会删除全部测试书籍，不用于用户正在使用的页面，不接入生产工作流，不要求刷新持久化。对应设计/计划为 docs/superpowers/specs/2026-11-12-reading-delete-acceptance-design.md 和 plans/2026-11-12-reading-delete-acceptance.md。

新增 reading-delete-browser 手写夹具，分别模拟合法实现、仅隐藏筛选视图、删除已读后计数不变及删除错误书籍。首次运行合法夹具失败于 before-delete：写入新探针时正则反斜线丢失；定位后修正，失败日志 probe-tests.log 保留。最终命令 node --test scripts/tests/reading-delete-browser.test.mjs scripts/tests/reading-list-browser.test.mjs：**2 passed**，共两组各四类手写浏览器夹具；错误分别在 read-delete-counts、read-delete-counts、unread-delete-removed 被拒绝。它们是受控探针证据，不是自然删除功能通过。

### 27.2 新自然基线失败及零模型观察

一个新隔离 appId **478380244322594816**，基线 requestId **0b612d62-b2d8-4899-a46d-4bd2ef0f0765**。沿用第 26 节本地独立端口运行器，原 8123/8000 服务不动；创建/生成各独占新提交标记，没有重发旧场景或手改源码。

首轮真实 npm 后 project_build 返回 VUE_COMPONENT_CONTRACT_FAILED，进入修复。修复模型共十轮，日志自然出现一次 **REPEATED_IDENTICAL_READS**，说明重复读取保护已在本次真实请求中交回验证；Spring 随后开始第二次构建。未触发 REPEATED_SOURCE_SNAPSHOT，不把读取保护称作方案 A 自然命中。

但随后 Python 验收进程退出：18000 无监听，启动根 PID 及其 Python 子进程均不在进程列表，Python 日志没有异常堆栈或正常关闭记录；Spring 聊天失败原因为连接 closed。任务显式查询 **FAILED / TERMINAL / NOT_VERSIONED，GENERATION_FAILED**；SSE done=0、business-error=1、唯一失败终态，约 52.28 秒。Python 旁路未收到第二次构建工具结果，Reviewer 未取得结论。查询近时段 Windows 应用崩溃事件未得到匹配记录，不能因此排除崩溃或认定具体退出原因。未取得退出码，也没有证据将退出归因于修复逻辑；不扩大为生产恢复改造。

保留失败后，零模型读取残留预览 HTTP 200，再运行原探针：**23 项通过**（22 项原交互、8 项计数包含在内，加 390px 无横向溢出），Chrome 退出 0、forced=false。没有调用部署接口，不能把私有运行器中的复用 URL 字段当成部署验收。该结果仅说明失败后当前残留页面交互可用，不改写失败任务，不证明第二次门禁/Reviewer/自动修复成功；Vue 无回滚边界继续存在。

### 27.3 账本、收尾及未完成项

原账本 **209→231，新增 22 次，max_calls=null**；创建 2、基线生成 20。22 次模型 HTTP 200，生成一次 JSON 格式重试已计入；没有已观测到的供应商失败重试。旧三个有限账本哈希保持。私有证据位于 tmp/vue-delete-real-20261112/，包含原生成失败、工具/模型观察、explicit-failed-task、failure-history、probe-tests、browser-report/exit、summary 与 cleanup-summary；不提交凭据、日志、生成源码或截图。

仅新增 Node 验收设施及文档，未改 Spring/Python/前端生产行为，故未重跑 Python 全量、Java 干净编译或前端构建；不沿用历史结果。提交前执行新旧探针测试和 git diff --check。按实时父子关系核对后关闭本轮剩余 Spring/代理，验收 Python 已退出；18123/18000/18001 释放，原 8123/8000、MySQL、Redis、PostgreSQL 保留。前端客服三修改和后端旧未跟踪计划保持，仅提交本轮文件，不推送。

### 27.4 下一步方案，待用户确认

**A（推荐）：先补本地验收进程退出取证，再继续删除验收。** 为 ignored 运行器增加父进程等待/退出码记录及 Python faulthandler，重新启动必要服务；不动生产恢复机制。随后对本轮隔离应用残留源码读取快照、明确作为“失败后残留页面基线”，用新的用户请求追加删除功能并运行新旧探针，不冒充原请求自动恢复。收益是下一次异常能区分外部终止、原生崩溃与 Python 异常；代价是增加少量本地运行器维护和一次新请求。

**B：仅重启必要服务，直接对残留基线发起删除增量请求。** 更快取得业务功能证据，但若再次退出仍可能缺少根因信息；必须承认失败基线来源，保留原失败，不能声称原自动修复已成功。

两者都是新请求，不重发原生成，不自动续跑旧图。按用户多方案先确认要求，本轮未自动选择 A 或 B。删除增量及浏览器新探针自然验收尚未完成；下一轮先处理上述选择，不另建样本追求自然拒绝。

## 28. A 方案执行、直接否定配图修复与删除功能通过

用户明确选择第 27 节 A 后执行。承接 ae26292，在同一隔离应用 478380244322594816 的失败后残留源码上继续，先经 Spring 快照保存基线，不恢复旧图、不重发旧请求。

### 28.1 退出取证实现及边界

ignored 目录新增独立监督父进程：启动服务子进程、保存 PID/开始时间、等待退出并记录十进制/十六进制退出码、结束时间和 stopRequested。Python 通过 PYTHONFAULTHANDLER=1 启用崩溃诊断，PYTHONUNBUFFERED=1 便于及时落盘。监督器不自动重启、不恢复生成；收尾标记只针对本轮拥有的子进程树。

控制子进程退出 7，监督器正确记录。首次版本未显式转发日志句柄，服务日志为空，退出码记录仍正常；发现后修正 stdout/stderr 转发，再用输出123、退出9的零模型子进程验证。原空日志保留，不能声称取证覆盖了已经丢失的信息。之后加载修复的 Python 使用修正版监督器，日志正常。旧第27节退出根因仍未查明，本轮没有自然进程崩溃，不能证明 faulthandler 捕获了真实崩溃。

### 28.2 自然失败暴露的生产缺陷与最小修复

第一个新删除请求 a4f1219f-8ac4-4cb2-8e46-bb2f17f07466 使用28次模型调用，231→259，最终 FAILED / VUE_IMAGE_USAGE_MISSING，约49.06秒，SSE唯一业务失败。监督状态确认 Python 仍运行，与第27节连接closed不同。

原文明确“不增加外部图片”，但 image_intent.requests_image_assets 返回true，declines_image_assets 返回false；模型上下文被加入六张图片，随后配图使用门禁阻止完成。这是明确的自动搜图规则误判，不是用户要求删除书籍必然需要图片。

生产只改 ai-service/orchestration/image_intent.py：对新增动作前紧邻的“不”识别否定；首次不配图判断另支持“不增加/不添加/不新增”加有限图片修饰词。没有把分句任意“不”变成全局否定，“不但添加图片”仍为正向，后句明确添加仍优先；保护旧图规则保持。不增加模型、预算、工具权限或修改配图完成门禁。

新增三种直接否定旧代码均失败，保留 image-before.log；修复后定向34 passed，覆盖首次/已有Vue不自动搜图、正向及转折边界。不是通用自然语言理解。README同步当前行为，设计/计划见 direct-image-negation-design 和 delete-supervised-validation 文档。

### 28.3 修复后新请求与真实删除交互

通过监督器主动停止旧Python，stopRequested=true、exitCode=1（主动终止，不是自然崩溃），新进程加载修复。对刚才失败留下的当前源码重新取快照，提交同一明确需求的新请求 **2fd8e691-2fb5-4bb6-b0da-c52157ebac9e**，未覆盖旧失败。

本次 **6次模型调用，259→265**，约13.58秒，HTTP/SSE done=1、业务错误0、唯一成功终态；显式requestId查询 SUCCEEDED / TERMINAL / NOT_VERSIONED。一轮project_build成功、Reviewer PASS/issues0；观察到的四份生成上下文 imageAssets 均为空，未再触发图片使用失败。新请求成功不等于第27节旧AST失败请求自动恢复，也不是无状态模型受控对照。

浏览器首次失败在删除前 unique-title：页面显示“《小王子》”，探针仅匹配“小王子”；原功能22项已经通过。零模型DOM检查确认显示书名号，未发现该步骤的页面功能缺陷。探针最小兼容一对中文书名号，仍要求精确整段及唯一定位；合法手写夹具改成带书名号，错误夹具仍保留普通书名，删除和计数断言未放宽。原browser-report/exit失败保留。

修正后的零模型真实复测：预览和本机部署 **120项全部通过，含30项计数**。覆盖原添加/空白拒绝/切换/筛选保持，已读筛选删除、未读筛选删除、切回全部确认全局消失、删空、重新添加及切换，以及两项390px无横向溢出。Chrome和运行器退出0、forced=false。探针只用于固定隔离页面，不接入生产；本轮未验证AppChat原生UI刷新、下载或刷新持久化。

### 28.4 本轮验证、账本及交付

- Python新增10项测试（意图4、API参数6），定向34 passed；compileall、uv lock --check通过。
- 隔离全量 **1378 passed、14 skipped、2 warnings，143.78秒**。沿用第23节ignored隔离脚本，仅测试进程关闭本机RAG并隔离四个假模型锁，不能称默认裸环境全通过。14项PostgreSQL opt-in未启用，两条既有弃用警告保留。
- 新旧浏览器夹具最终2 passed，四类删除夹具和四类原功能夹具；这是受控探针测试，真实120项另外记录。
- 模型总增量 **34，231→265，max_calls=null**，失败28、成功6；两个请求各有一次JSON格式重试，均计入。第一个请求有26份适配器响应，其中1份非JSON，监督日志句柄问题导致无完整HTTP记录，不能宣称28次全为HTTP200；未观察到供应商失败。第二个请求6次HTTP200。旧有限账本哈希保持。

私有证据分别位于 tmp/vue-delete-supervised-20261112/ 和 tmp/vue-delete-fixed-20261112/，包括监督过程/控制测试、两次请求标记及结果、基线快照、原浏览器失败和最终recheck、测试日志，不输出/提交源码、密钥、Cookie或日志。只提交最小生产修复、回归、探针兼容及必要文档。前端三处客服修改和后端旧未跟踪计划保持，不推送。

收尾通过监督器停止本轮服务并保存主动停止退出码，原8123/8000及MySQL/Redis/PostgreSQL保持。现有日常Python未重启，不能声称自动加载了本轮修复；需要在实际使用前适时重启该服务。没有修改Java或前端生产代码，未重跑Java干净编译和前端构建。

### 28.5 下一轮建议

优先补**同名书籍和筛选删除目标身份**的有限功能验收。当前探针故意要求书名唯一，尚未覆盖两本同名书、其中一本已读时删除指定卡片；页面若按书名或筛选索引删除，可能误删另一条或多条，构建/PASS不能发现。先定义是否允许重名及指定卡片身份，再用独立场景验证，不放宽现有唯一定位断言冒充覆盖。若需要改变产品的重名规则，先给用户说明方案并确认。无需再采样追求自然门禁或往返写入触发，生产恢复与公网部署仍不在范围。

## 29. 2026-11-12 潮牌网页失败请求只读排查

用户反馈请求 `2c0c3467-2f55-4f01-9cd3-7a46a4c15ec2` 报错 `Artifact did not pass validation or quality review: []`。本轮未实施生产优化，仅核对 MySQL 任务、PostgreSQL 摘要、Redis 三轮工具结果、LangSmith 节点元数据与应用源码。

确认首次构建因导入不存在的 categories 失败，第一轮修复后因 HeroSection.image 必填属性缺失失败；第二轮修复后构建与硬校验通过，但质量审查未通过，此时两次修复额度已用完。工具计数为36，并非40次工具预算耗尽。fail_quality 打印已通过的 validation.errors，掩盖了质量拒绝来源。当前源码另有多个缺失导航锚点，但审查理由正文已随终态 checkpoint 清理，不能冒充当时的模型拒绝依据。

完整证据、方案收益与代价、推荐顺序见 [失败请求排查与修复建议](generation-quality-review-incident-20261112.md)。建议先修失败诊断，再修当前应用最小接线问题并做浏览器点击验收，最后评估预算；均尚未实施。未新增真实模型调用，未运行浏览器或完整测试，未修改应用文件、重启服务或推送。仅交付排查文档，保留已有 README 修改和未跟踪取消回归计划。

## 30. 2026-11-12 质量失败诊断与静态锚点平台修复

### 30.1 实现与触发边界

用户确认按第29节方案实施后，本轮修复落在后续生成平台；原失败应用只读，不手改其源码、dist 或任务状态。原触发场景是首次构建导出错误、第一次修复缺少必填属性占满两次预算，第二次修复后首次质量审查拒绝，终态却打印已通过硬校验的空错误列表。

`nodes/verification.py` 现在先区分本轮硬校验，再核对 Vue built=true，最后处理质量拒绝。终态分别使用 ARTIFACT_VALIDATION_FAILED、具体构建错误及 QUALITY_REVIEW_FAILED，后者明确构建/产物校验已通过以及已用修复次数。不再回显任意硬校验正文或模型诊断正文。详细硬校验仍沿原工具结果供内部修复使用。两次修复和40次工具预算不变，无新增重试或质量绕过。

`models/review_diagnostics.py` 仅提取固定 stage/verdict/issueCount/issueCodes；新候选清空旧诊断，字段只留在验证子图，不向父图和修复输入投影。工作流 payload 与 PostgreSQL 存储边界再次核验后写入既有 status_payload，节点旁路同样只导出固定标签。多 Reviewer 任意阻断代码归为 UNCLASSIFIED；布尔兼容记录零问题数。选择固定字段而非保存原始 Reviewer 回复，是为了保留可关联请求的失败类别，同时遵守不把提示词、源码和自由文本理由写入日志/旁路的边界；不能据摘要恢复完整理由。

Spring `VueProjectBuilder` 在 npm/dist 和必填属性检查成功后，运行原可信脚本的独立锚点模式。有限静态入口、可解析本地组件闭包和 index.html 提供目标集合，仅对无点击接管的 ASCII 字面同页链接判缺失。VUE_ANCHOR_TARGET_MISSING 进入原有限修复，VUE_ANCHOR_CHECK_UNAVAILABLE 直接失败；取消沿原 CommandRunner，dist 快路径不重检，失败不回滚。选择 AST 检查而非额外模型审查，是因为该有限静态错误可以给出确定路径和目标，且无需增加供应商调用。

路由/插件/未知入口、动态 ID/组件、未知调用、插槽/v-html/自定义指令、不完整扫描等跳过。top/空片段/动态链接/点击接管/下载或新窗口链接不判缺失；数据模块内部副作用不在范围。检查不证明目标唯一、条件渲染可见或浏览器交互正确，不实现购物车，也不是独立预览/部署拦截器。

### 30.2 验证与证据限制

- Python `uv run python -m compileall -q src`、`uv lock --check` 通过。
- 默认 `uv run pytest -ra --tb=short` 日志中出现本机 RAG 配置影响，未取得完整终态；另以 API 定向命令复核到客服关闭场景期待503却收到200。本轮没有为此改生产配置或既有客服代码。
- 沿用 ignored `tmp/vue_read_stall_test_isolation.py` 重跑，只在测试进程关闭本机 RAG，并为四个手写重排器进程夹具分配独立模型锁。初次隔离全量为1388 passed、1 failed、14 skipped；唯一失败是旧断言要求终态回显硬校验标记。将其改为核对稳定阶段码、旧构建错误不残留，以及详细信息仍在原工具结果中。
- 最终隔离全量 **1389 passed、14 skipped、2 warnings，135.60秒**。14项 PostgreSQL opt-in 未启用，两条既有弃用警告保留；不能称默认裸环境全通过。最终日志 `tmp/generation-diagnostics-pytest-final.log`。
- `mvn clean -DskipTests compile` 最终通过；Java七类定向测试 **93项全部通过**。最终测试显式启用 VUE_COMPONENT_INTEGRATION，使用前端现有编译器依赖和临时手写组件，测试结束移除依赖链接。包括新增锚点协议/强制构建/取消和网关、SSE、任务落账代码透传。日志位于 `tmp/generation-diagnostics-clean-final.log`、`tmp/generation-diagnostics-java-final.log`。本仓库无可用 mvnw.cmd，使用本机 mvn。
- Node组件与锚点测试 **38项通过**，包括真实 Node CLI 的隔离手写工程：缺失失败、补齐目标通过、插件入口跳过。日志 `tmp/generation-diagnostics-node-final.log`。没有安装额外依赖或执行生成组件。
- 原失败应用只读检查返回 SiteHeader.shop、HeroSection.series、SiteFooter.new/shop 四条有界问题，不能冒充历史 Reviewer 的拒绝依据。未做新增真实生成、浏览器点击验收或完整端到端验证；真实模型调用增量 **0**。

### 30.3 交付与后续建议

同步 AI README、AGENTS 和排查报告。前端通用 business-error 适配可消费新增稳定代码，未修改前端；保留其三处既有客服修改。README原有末尾围栏修改、后端未跟踪取消回归计划保持，不混入提交。源码、日志、临时工程、密钥和环境文件不提交，不推送，不主动重启任何日常服务。

生效及页面验证需要重启 Spring 后端，并启动或重启 Python AI 服务；前端和数据库不需要因此重启。旧请求不会自动恢复或改成成功，新机制只对加载新代码后的执行生效。下一步优先从正常生成入口核对明确错误展示、实际修复和浏览器导航，再依据失败类别分布评估预算；单纯提高次数会增加耗时/成本，仍不能保证模型收敛。构建问题耗尽共享预算、被跳过的动态范围、购物流程和模型语义误判仍可能失败。

## 31. 2026-11-12 第二次潮牌首页测试工具预算耗尽排查

请求 `7a77b16e-21ac-4918-9168-80e0cf5d0987`，应用 `478401640100646912`，需求“做一个适合潮牌的电商首页”。本轮仅调查并提出待确认方案，未修改生产代码或生成应用。完整证据、模块链路、方案比较和机制见 [Vue工具预算排查](vue-tool-budget-incident-20261112.md)。

MySQL任务16:18:19至16:19:39以 VUE_TOOL_CALL_LIMIT_EXCEEDED 失败。Redis确认生成14次写入，第一次修复21次读取、1次写入及4次修改，共40次文件工具；随后下一批被整批预算校验拒绝。首次构建是三个 required props 缺失，不是新增锚点检查；本请求未到质量审查或修复后的构建，不是第二次修复次数耗尽。当前 App.vue 已有三个属性绑定，只能证明发生了修改，不能证明产物已通过。

最后修改后11次连续读取中穿插新文件，按结果哈希复算连续重复上界仅3次；当前保护要求连续四次无新信息，因此不触发。写入快照保护不覆盖持续读取。PostgreSQL摘要14次工具/0次修复停留在上个已完成阶段，正式子图中途异常未finish导致观测滞后，不能当作真实消耗。终态checkpoint清理，未恢复模型原文、私有计划及失败下一批参数。

推荐保持40次及原验证门禁，聚焦构建报告涉及文件；正式修复明确成功写入且无待写文件、配图满足后，在完整纯读批次上限或低剩余预算时交回原验证，并补中途计数诊断。上限初值与收益/代价已在报告比较，按协作规则等待用户确认再改变生产行为。提前验证仍可能消耗有限修复机会，不能保证收敛；单改提示缺少确定性保障，提高预算可能放大重复读取，当前配置le=40也不允许仅改.env到60。

本轮离线读取保护测试25项通过、2条既有弃用警告；哈希复算最大连续重复3。无新增真实模型调用、工具重放、应用构建、浏览器验收或重启，不将既有/离线结果写成真实修复通过。后续先对混合新旧读取补Fake复现和交回验证回归，再加载改动进行新请求与浏览器验收。只提交排查及本节交接，保留README、前端客服既有修改和未跟踪取消回归计划，不推送。

## 32. 2026-11-12 Vue 修复读取与预算收口实施

用户确认按第31节推荐方案实施。本轮只修改 Python 平台及必要文档，不手改失败应用；原触发场景是 required props 构建失败后已写入修复，但持续读取新旧文件耗尽全请求40次额度，原“四次连续无新信息”保护未命中。实现细节和取舍另见 [事故报告实施补充](vue-tool-budget-incident-20261112.md#用户确认后的实施补充)。

### 32.1 触发、流程与边界

`vue_tool_loop.py` 的私有 successful_write 只凭本阶段 file_write/file_modify 返回 ok is True 设置；完整成功的纯 file_read 批次增加 read_only_batches，混合批次或失败/未知/截断/超界读取清零。新文件也有读取开销，不再依赖所有读取内容重复才收口。

正式修复在完整批次结束、无待写文件且配图已满足时，本阶段已成功写入且连续三个纯读批次，或剩余共享工具不多于四次，交回原产物校验、强制构建和质量审查。低额度可以在完整写入批次后触发，不跳过同批第二个工具，不直接成功、不额外修复、不提高40次/两次上限。生成和内部兼容入口不启用。选择固定批次与预算条件，是为覆盖本次“穿插新文件”的读取开销，并让确定性构建判断修复；提示单独不能保证模型停止，提高预算可能延长循环，故未采用。

新字段仅保存在子图checkpoint，阶段初始化清零，同阶段恢复保留，不进入父图、模型或公共事件；旧checkpoint缺字段时从未触发起算，不追补旧批次。原四次重复读取和写入快照保护继续保留；没有新增生产恢复、Vue回滚、浏览器门禁或工具权限。合理大范围核对可能较早消耗一次修复机会，保留四次也不保证够下一轮完成。

`vue_budget.py` 复用完整有界读取判定并统计全请求/本阶段读写及其他工具数。预算拒绝和交回日志仅输出固定原因、阶段、修复次数、累计数、剩余额度、本批请求数与批次数；不输出路径、源码、参数或模型反馈。PostgreSQL摘要仍是最后完成节点，中途失败须对照现场日志，不谎称摘要已实时修正。无数据库schema或公共SSE变更。

生成提示增加required props、导入导出及事件/v-model接线要求；修复提示优先读取构建报告涉及文件，必要依赖才扩大范围，完成必要写入后空工具列表交回构建。提示不是确定性语义证明，不代表所有浏览器交互通过。

### 32.2 验证证据

- 新增24项Fake测试，修复前两个全图场景均在工具预算失败，修复后分别进入成功终态或保留注入的构建失败。持续质量拒绝仍为QUALITY_REVIEW_FAILED，修复次数最多2。
- 覆盖无写入/非布尔成功、待写计划、配图、批次内后续写入、不同新文件、未知/截断结果、取消、跨阶段总预算与阶段清零、内存saver恢复；恢复可见计数0→0→1→1→2，证明批次中途不增加计数。生成/兼容入口保持原流程，低预算正式修复写循环测试同步新预期。
- 日志测试同时覆盖40次耗尽，以及全请求39次/本阶段25次、剩1次时再请求2个整批拒绝；核对阶段和全请求读写数及不含路径。最终相关定向 **109 passed、2 warnings**；新增测试单独 **24 passed、2 warnings**。
- `uv run python -m compileall -q src`、`uv lock --check` 通过。默认 `uv run pytest -ra --tb=short` 完整结束为 **5 failed、1408 passed、14 skipped、2 warnings，156.82秒**；一个本机RAG开启导致关闭场景收到200而非503，四个重排器手写进程夹具发生模型所有者锁冲突。生产配置及客服代码未修改。
- 沿用既有ignored隔离脚本，仅测试进程关闭RAG并隔离四个假模型锁；`uv run python ../tmp/vue_read_stall_test_isolation.py` 最终 **1413 passed、14 skipped、2 warnings，149.96秒**。14项PostgreSQL真实资源opt-in未开启，2条既有弃用警告，不能称默认环境全通过。日志均在 tmp/vue-budget-*，不提交。

### 32.3 交付及后续

同步AI README、AGENTS、实施计划和事故报告。只提交本轮代码、回归及文档，README原有末尾围栏修改与未跟踪取消计划保持；独立前端三处客服修改保持，不推送。没有Java/前端改动，未重新执行其构建测试；没有新增真实模型调用、生成工程构建、浏览器验收或主动重启日常服务。

本轮需重启Python AI服务加载新代码，Spring/数据库/前端无需因此重启；上轮Java锚点改动若尚未加载仍需另行重启Spring。旧失败任务不自动恢复。下一轮优先从正常入口提交新请求，核对修复后的构建、真实页面与购物/筛选/导航交互；未写完计划、配图未满足、生成阶段过度消耗或真实源码/质量错误仍可能失败，不能凭本轮Fake结果宣称该应用已生成成功。

## 33. 2026-11-12 用户真实页面布局与部署封面延迟排查

用户提供应用478409384794894336（部署后截图报错但稍后成功）及478410744286261248（布局不协调）。本轮只读排查，完整证据、机制和待选方案见[布局与封面排查](layout-cover-investigation-20261112.md)，没有修改生成应用或生产行为。

第一个应用16:53:23部署，封面对象16:54:47完成且当前HEAD 200，约84秒；当前Spring截图驱动到16:54:40才创建。现存驱动早在11:03已缓存，JVM未显式配置驱动路径。时间线指向默认解析/初始化路径，原IDEA异常文字仍缺失，不能写成已确认下载失败、OSS慢或重试恢复。同一本地驱动隔离WebDriver计时：浏览器0.797秒就绪、导航0.281秒、包含原2秒等待后3.453秒截图，8张图片可用；未测原Java完整方法、压缩/上传/封面回写，未重现或修复历史84秒。新增虚拟线程与ThreadLocal复用不匹配及单次任务不关闭驱动是源码发现的生命周期缺口，不冒称本次延迟根因。

第二个读书清单通过PostgreSQL请求摘要确认首次及后续修改均选择前端规则、准备ready、qualityPassed=true、修复0次。1440/900/390px正常预览观察发现delete-btn没有样式：桌面删除按钮27px高而标记按钮约38px高、间距0；窄屏标记按钮324px宽但删除按钮80px宽，落在下一行左侧且无间距。主容器640px的窄列属于待确认的设计选择，不能直接判错。未点击书籍操作，不称本轮功能通过。通用规则与Reviewer均未拦住视觉缺口，完整第三方Skill或视觉审查没有执行。

离线规则选择还确认“电商首页”漏选商品预设，前一潮牌请求摘要同样为product-content；此遗漏不解释读书清单控件样式。原流程源码、规则传播和三条安全请求摘要均核对，没有读取模型原文或输出源码。

下一步建议方案A：本地截图初始化诊断/资源释放，并增强新增控件样式和窄屏按钮组规则、补“电商”预设，再做隔离真实浏览器验收。理由是当前具体控件和初始化路径已有证据；仅增加修复预算不会检查出视觉不一致。方案B在A上新增受限多模态视觉审查，会增加模型调用、耗时和误报处理，需要单独设计。按顶部硬性要求先交用户选择，不把建议写成已实现。原截图异常仍待原文补证，不扩展公网部署、并发或恢复。

私有证据位于tmp/layout-cover-investigation-20261112。首次观察误用目录地址导致资源失败的记录保留，correct-preview使用前端实际dist/index.html地址的观察单独保存；脚本运行退出0。原模型账本265→265、增量0，无模型请求/部署/重启。本轮仅文档提交，未跑Java/Python全量或前端构建；前端客服三文件、README既有围栏修改及原未跟踪取消计划保持。本轮独立浏览器已结束，日常服务不动，不推送。

## 34. 2026-11-12 方案 A：本地截图与控件一致性实施及真实验收

用户选择第33节方案A。本轮修改生成平台和截图服务，原问题应用478409384794894336及478410744286261248保持只读。设计与计划为docs/superpowers/specs/2026-11-12-local-cover-controls-design.md及plans/2026-11-12-local-cover-controls.md；没有增加自动视觉审查、修复预算、浏览器池或公网部署能力。

### 34.1 截图如何改进

原场景是部署记录到封面完成约84秒，驱动在约77秒后才出现，同时每次虚拟线程与ThreadLocal的复用方式不匹配。WebScreenshotUtils现在每次独占WebDriver，在finally尝试quit，包括超时配置、导航、截图、压缩失败；关闭失败有记录但不丢弃已取得的图片，不保证故障进程被强制杀掉。失败只清理本次UUID临时目录，成功图片交回上传服务清理。

ScreenshotServiceImpl读取app.screenshot.driver-path（可用APP_SCREENSHOT_DRIVER_PATH），优先于原JVM webdriver.chrome.driver和CHROMEDRIVER_PATH；路径必须是现有普通文件，显式错误直接失败。使用ChromeDriverService配置可执行文件，不修改全局系统属性；均未配置则保持Selenium默认解析。默认路径仍可能受网络影响，页面30秒超时不限制驱动解析，Chrome升级仍需核对兼容驱动。已在本机ignored application-local.yml追加核对过的驱动路径，其他配置保持；没有把个人路径提交到Git，也没有重启用户IDE服务。

ScreenshotTimings以内部关联ID记录固定阶段、success/failed/degraded、耗时与异常类型，不输出URL、路径或异常正文。导航/就绪超时保留降级尝试截图，中断恢复标志并停止。AppService仅在原异步截图任务边界捕获普通异常，封面失败不撤销部署、不自动重试。上传键增加独立UUID，避免固定临时文件名使同日封面相互覆盖；这是审查过程中发现并修正的本轮实现问题，已补同名源文件回归。

### 34.2 控件规则与本轮自然失败后的修正

为三种生成类型的新准备结果追加独立控件规则，生成、修复和Reviewer共用；明确主次/删除按钮的基础样式、间距、窄屏排列，以及已有页面新增控件继承样式。原文和结构化单属性范围仍优先，不能据此全局改版。选择器版本3补中文“电商”，保留有限否定和edit模式规则。design-core、preserve-style等既有版本不变。

首次试验control-consistency@1的新自然样本功能通过，但浏览器发现添加按钮引用未定义的btn/btn--primary样式；手机输入框保留横排flex-basis=220px，切到column后高度变为220px。它不是浏览器检查器自动发现，原失败截图与指标保留。最终升级为control-consistency@2，增加共享样式确实定义/加载、Vue scoped作用范围和Flex方向切换时重置basis的提醒，然后发出明确的新视觉修正请求。没有手改源码或供应商回复。

最终白名单只解析control-consistency@2；本轮试验@1如留在旧准备结果中会被忽略，不自动升级。正常请求的已有准备结果不追补规则。这仍是模型指导，不是CSS硬门禁、截图审查或视觉质量保证；本轮曾出现规则存在且Reviewer PASS而视觉失败，不能省略该证据。

### 34.3 新隔离应用真实链路

新appId为478425259132399616，独立Spring18123、Python18000、代理18001使用当前源码、原MySQL/Redis/PostgreSQL及同一模型账本。会话重新登录核对，提交标记均新建；原8123/8000/5173服务不动。内存偏紧，临时Spring采用限定堆的直接Java启动，编译与验收顺序执行。

| 阶段 | requestId | 实际结果 | 模型调用 |
| --- | --- | --- | --- |
| 创建应用 | 创建接口 | 真实路由为vue_project | 2 |
| 首次自然生成，规则试验@1 | 063d0556-152e-4e08-ae20-8f63bc5e957a | 55.12秒，SUCCEEDED；一次修复、19次工具；功能通过、视觉失败 | 18 |
| 明确修正上述视觉问题，最终规则@2 | 32e4e478-b2e8-4b1d-9107-9c8e809dbf83 | 21.59秒，SUCCEEDED；零修复、7次工具 | 11 |
| 新增删除功能，最终规则@2 | f46a0e76-a2d2-4da1-ba8f-8726368f0b70 | 20.84秒，SUCCEEDED；零修复、8次工具 | 10 |

三个生成请求均真实HTTP/SSE 200，done=1、business-error=0、invalidFrame=0、唯一终态。SSE没有提取requestId，先取隔离应用台账，再按明确requestId重查SUCCEEDED/TERMINAL/NOT_VERSIONED；PostgreSQL摘要确认各阶段规则版本、qualityPassed及修复/工具数。模型输入/输出与构建结果旁路原样保存，不改变响应。

首次生成自然出现VUE_COMPONENT_CONTRACT_FAILED，经原有限修复返回第二次project_build built=true，然后Reviewer PASS；已有REPEATED_SOURCE_SNAPSHOT保护自然触发。本轮补到了“自然必填属性拒绝→同请求自动修复→重建→浏览器功能通过”的有限样本证据，但该样本视觉失败，不称为完整页面质量通过。没有REPAIR_NO_PROGRESS，不保证所有门禁或浏览器语义。

### 34.4 浏览器与封面验证

全部浏览器使用同源隔离页面，读取真实预览/部署响应，不改写产物。1440px和390px各运行原共享交互探针；私有浏览器profile隔离本地存储，避免修改用户使用中的列表。截图和几何测量在探针操作前完成。

| 页面阶段 | 交互断言 | 计数断言（包含在前列） | 视觉结果 |
| --- | --- | --- | --- |
| 首次基线预览 | 44通过 | 16 | 失败：默认添加按钮、手机输入框220px |
| 明确视觉修正后预览 | 44通过 | 16 | 添加按钮完整样式；手机输入框与按钮均44px高 |
| 删除增量后预览 | 118通过 | 30 | 桌面及手机按钮均37px高、间距8px；手机两按钮各154px宽 |
| 最终本机部署 | 118通过 | 30 | 与最终预览一致 |

最终预览与部署合计236项交互通过、其中60项计数；全部四阶段合计324项交互通过，但不能用这个合计掩盖首次视觉失败。添加、空白拒绝、已读切换、三筛选、删除已读/未读/删空/重添及计数均按实际DOM检查；没有横向溢出或观测到的资源失败。最终视觉修正是新请求，不是首次请求内自动视觉修复。原AppChat UI流式展示、停止、下载和刷新次数未重验。

新应用通过真实部署接口触发截图及OSS上传，封面HEAD 200。截图阶段耗时：driver_init 1270ms、navigate 114ms、ready_wait 2024ms、capture 41ms、compress 307ms、driver_close 194ms、upload 526ms、cleanup 1ms，合计4477ms；部署记录18:10:39、封面对象及应用更新时间18:10:43。它是新隔离样本，不是旧潮牌同页面同环境的严格性能对照；不能据此断言历史84秒的确切网络原因。私有cover-timing脚本的外层5.375/10.032秒包含脚本加载、任务核对和页面请求，不应当作纯部署接口/截图延迟。

关闭日志成功且进程核对没有本轮残留chromedriver；仍存在的29256属于原IDE Spring，本轮未关闭。独立浏览器运行器均退出0，原失败记录不覆盖。

### 34.5 确定性验证与模型账本

- Java先用两项测试复现旧成功截图和超时设置失败都未quit。最终干净编译通过；最终定向16项通过、0跳过，其中15项离线及1项显式本机Chrome测试。覆盖路径优先级、错误路径不回退、成功/失败清理、中断、超时降级、日志脱敏、异步失败、上传失败清理和同名源图对象键不同。IT本身不含OSS；上节新应用部署才包含真实上传与封面回写。
- 一次中途Maven native malloc失败保留；限定Maven堆512MiB和测试堆384MiB后通过。日志tmp/screenshot-red.log、screenshot-green.log、screenshot-green-final.log、cover-controls-clean.log及cover-controls-java-final.log。
- Python选择回归先验证“电商”漏选和新规则缺失。首次定向旧“四条规则”断言失败，更新到五条；相关169项通过。首次默认全量8 failed、1414 passed、14 skipped，其中三项保守重写规则集合断言随新增规则更新，另五项为本机RAG配置和重排器假模型锁影响。没有改用户客服配置消除失败。
- 按既有ignored脚本，仅测试进程关闭RAG并隔离四个假模型锁，试验@1全量1422 passed、14 skipped；最终@2相关229 passed，最终隔离全量1422 passed、14 skipped、2 warnings（141.65秒）。compileall与uv lock --check通过。14项PostgreSQL opt-in未启用，真实生成参与PostgreSQL不等于这14项资源测试通过。未改前端生产代码，未重跑前端构建。
- 原账本265→306，增量41，max_calls=null。Python HTTP日志核对41次代理响应均200；三个生成请求各一次格式重试，共3次，全部计入上述调用。没有观测到供应商HTTP失败或重试；构建拒绝及视觉失败分别如实保留。旧三个有限账本哈希不变。

### 34.6 交付、生效及下一轮

私有证据在tmp/local-cover-controls-real-20261112，模型/工具旁路、日志、源码、截图、账户和private-runtime不提交。同步AI README、根README、AGENTS及设计/计划，只提交本轮代码、测试及文档；Java测试被原test/忽略规则覆盖，精确强制加入四个相关测试，不能批量添加其他测试。README原末尾围栏改动、原未跟踪取消计划、前端三处客服修改保持，不推送。

临时服务按实时进程树核对并关闭，18123/18000/18001释放；原8123/8000/5173及数据库保持。用户日常Spring/Python尚需重启才能加载本轮生产代码；本机驱动配置已准备，不需重启前端或数据库。原两个问题应用不会自动改版或重新截图。

下一轮建议优先用正常入口验证最终规则@2下新的首次页面，重点看共享控件样式、手机表单高度和长书名/按钮换行。原因是本轮最终@2证明了明确视觉修正和删除增量，但尚未证明从零生成能稳定避免这些缺陷；长内容与其他页面结构仍可能让Flex或样式作用域出错。先做有界浏览器验收，保留失败，再决定最小规则或具体功能优化；不要继续无限采样、提高工具预算或直接接入方案B视觉模型。

## 35. 2026-11-13 最终控件规则首次验收与样式审查证据方案 A

承接第34节，在最终 control-consistency@2 下创建新隔离应用478660512224747520。用户在观察失败证据及方案取舍后选择A：收紧现有Reviewer样式证据核对，不新增CSS静态分析器或视觉模型。设计/计划见 docs/superpowers/specs/2026-11-13-vue-css-review-evidence-design.md 与 plans/2026-11-13-vue-css-review-evidence.md。

### 35.1 新自然失败与真实浏览器证据

新requestId为6b4edbc3-0338-4482-9fa5-dabebf2f827c，65.91秒，FAILED / TERMINAL / NOT_VERSIONED，QUALITY_REVIEW_FAILED。SSE HTTP200、done0、business-error1、invalidFrame0、唯一终态1；两次修复、三次project_build均built=true。首次Reviewer报告BookCard props/事件不一致及共享类缺失；本轮不能据最终残留页面反推初始候选正确，也没有自然AST拒绝、REPAIR_NO_PROGRESS或读取保护命中证据。

后两次Reviewer仍以共享样式缺失/近似类名不一致拒绝。原末轮供应商输入的当前快照实际同时含main.js的CSS导入、main.css的.btn--primary/.btn--danger、FilterBar组件scoped的.btn--filter。模板添加按钮使用btn--primary，局部未使用btn-primary不能据此推断全局主色失效；浏览器确认主色和按钮样式已生效。这是当前样本的具体误判证据，不推断所有审查问题均误判。

失败后只读打开残留预览，1440/390px共享添加、空白拒绝、切换、三筛选、指定删除、删空重添探针共118项通过，含30项原计数断言。另经真实输入添加长中文及连续英文标题，核对标题/按钮容器边界、页宽、状态、已读筛选及全局删除，共66项通过（其中固定counts检查18项）；合计184项，未发现资源加载失败。手机输入框43px，添加按钮约40.23px；两类长标题按钮均约39.42px高、垂直排列间距10px，完整文字换行未溢出。桌面按钮亦等高、间距10px。这是失败后残留页面的局部可用证据，任务失败保持，不称首次生成整体成功或部署成功。

长标题探针首次失败unique-long-title：原删除探针结束在已读筛选，新添加未读书被隐藏。修正私有验收流程先切全部并恢复未读基线，零模型复测通过；原browser-baseline失败保留，后续browser-visual-fix目录只是复用运行器的阶段名，本轮没有发送视觉修正生成请求。截图在长探针操作结束后只显示剩余基线，长内容布局以操作中保存的几何数据为依据，不能误称该截图包含已删除的长标题。

### 35.2 优化如何实现及限制

仅修改 prompts/review.py 的 VUE_REVIEW_EVIDENCE_INSTRUCTIONS，正式单Reviewer及三角色共用。判断样式缺失前，沿可见CSS入口、全局选择器、目标组件scoped和准确模板类核对；全局共享类不要求每组件重复定义，目标组件本地scoped合法，其他组件scoped不能当全局。近似类或未使用重复定义不自动阻断，基础类存在而变体缺失时须说明当前需求的实质影响；明确要求的样式/布局缺失仍可阻断。已有可见props/事件契约核对及快照缺失/截断不得猜测的限制保持。

选择该方案是因为末轮证据已经传到模型，新增摘要不能直接解决模型忽略可见定义；提示改动范围及维护成本更小。未增加模型次数、重试、40次工具/两次修复预算或强制PASS机制，不改变SSE、Spring工具、CSS解析或业务目录。新增四个注入式测试核对正式单审查及三个角色实际消息包含规则且当前快照原样保留，只证明传播和协议，不证明Fake能识别CSS语义。

使用原末轮固定快照及上下文，修改提示后作一次独占标记的新真实诊断审查，SDK重试关闭；329→330，PASS/issues0。不生成、写入、部署、恢复旧图或改变失败台账。相同快照的新提示结果支持本次修正，但单次模型波动仍可能影响结果，不能证明稳定消除误判；修正后新的端到端生成尚未执行。

### 35.3 环境、账本与交付

原端口预检仅MySQL3306监听。Docker启动两处旧运行时socket失败，分别为Secrets Engine engine.sock及Inference manager dockerInference，普通socket文件rename亦失败；停止本轮启动的Docker进程后，将仅含故障socket的父目录分别保留为docker-secrets-engine.before-20261113和Docker/run.before-20261113，再启动成功。未删除、迁移或重置容器数据；PostgreSQL恢复，Docker自身也自动启动既有Milvus/etcd。Redis服务启动因无服务控制权限失败，使用原安装路径/配置启动独立Redis进程供验收。原错误保留，不将Docker环境错误归因业务代码。

临时Spring18123/Python18000/代理18001健康及隔离账号预检通过，新标记新应用，继续原无上限账本306→330，增量24：创建2、首次失败21、诊断1。首次生成一次JSON格式重试，已计入；服务日志23次代理HTTP200（创建和生成），诊断另1次代理响应，旧三个有限账本哈希保持。无额外无限采样或重复旧请求。

监督器已记录三个本轮服务主动停止stopRequested=true、exitCode1，非自然崩溃；临时端口释放，独立Redis进程已停止。Docker及恢复的既有容器保留运行，MySQL不动；原日常Spring/Python/Vite本轮没有启动或重启。真实私有运行器、源码、模型/工具日志、截图和凭据位于tmp/controls-v2-first-real-20261113，不提交。

定向142项通过，compileall与uv lock --check通过。默认全量为1425 passed、1 failed、14 skipped、2 warnings（144.80秒）；唯一失败test_customer_service_answer_auth_disabled_and_question_bounds预期503实际200，本机RAG开启影响关闭场景。未改用户客服配置。既有隔离脚本仅测试进程关闭RAG并隔离四个假模型锁，隔离全量1426 passed、14 skipped、2 warnings（140.51秒），不能称默认环境全通过。14项真实PostgreSQL opt-in未启用，已有弃用警告保留。未改Java/前端生产代码，未重跑其编译/构建，未验证部署封面、下载、AppChat流式展示/停止/刷新次数、刷新持久化、同名书籍或其他页面。README原末尾双反引号修改、旧未跟踪取消回归计划、前端三处客服修改保留。仅提交本轮提示、测试及文档，不推送。

下一轮优先对修正后的审查提示做一次新正常入口生成验收，原因是本轮只有固定候选诊断PASS，尚未验证生成/修复/新审查整链路成功。仍可能因真实props/事件错误、动态CSS/路由、不完整快照、窄屏长内容或模型忽略证据失败；不要通过增加预算、删除质量门禁或采样直到PASS掩盖失败。

## 36. 2026-11-13 新审查提示下首次生成整链路续验

用户要求按交接继续，本轮执行第35节建议，只做一次新隔离应用的正常Spring生成入口验收。未新增生产代码或规则，不重复旧失败请求，不为追求首次零修复或新门禁命中继续采样。重点是新提示已加载时，生成、既有有限修复、审查和实际预览能否共同完成。

### 36.1 实际请求与有限修复

新appId **478668026865614848**，requestId **6697c7d3-9116-4fbe-bdfc-2bad0ee35bdd**。创建和生成均新独占提交标记，原读书清单需求包含添加、空白拒绝、已读切换、三筛选、删除和总数/已读数，不添加外部图片。通过/apps/chat/gen/code入口发起一次SSE，不是旧快照诊断或直接模型生成；未通过原AppChat UI操作。

请求78.05秒，SSE HTTP200、done1、business-error0、invalidFrame0、唯一终态1。SSE成功流未提取requestId，先取隔离应用任务台账，再按上述明确requestId重查 **SUCCEEDED / TERMINAL / NOT_VERSIONED**。PostgreSQL摘要finalize、qualityPassed=true、repairCount=2、toolCallCount=32；准备ready，规则含control-consistency@2，供应商单Reviewer输入确认含上一轮新增CSS证据要求。

首次project_build自然返回 **VUE_COMPONENT_CONTRACT_FAILED**：App.vue调用FilterBar缺filters/active/total必填属性。进入原有限修复，第一轮修复显式写入后连续三个完整纯读批次，自然触发 **READ_BATCH_LIMIT**，全请求24次工具、剩余16次，交回验证；这不是REPAIR_NO_PROGRESS或直接成功。第二次build built=true，Reviewer返回一条FUNCTIONAL_ERROR，指出BookForm的btn-primary和FilterBar的btn-filter与共享样式类不一致。当前审查快照没有这两类的明确选择器定义，但已有基础btn，未在这一中间阶段运行浏览器，不能把模型“浏览器默认外观”描述当成已观测事实或断言所有基础样式无效。

第二轮修复统一按钮类并补筛选样式，第三次build built=true，最终Reviewer **PASS/issues0**，然后完成。不是扩大修复预算，不是人工修补产物；未出现REPAIR_NO_PROGRESS、工具耗尽或样式存在却被末轮拒绝的相同失败。一次格式重试发生在生成阶段，包含于模型总量。本样本证明修正后能完成这个有限流程，不能证明零误判、首次候选正确或任意页面质量稳定。

### 36.2 浏览器检查与实现方式

复用共享verifyReadingList/verifyReadingDeletion及上一轮私有长标题探针，在新隔离Chrome profile内打开实际dist/index.html，分别1440px/390px，先检查并截图初始布局，再点击真实添加、筛选、切换和指定删除操作。旧应用和项目源码不动。原删除探针停在已读筛选，长探针显式回全部并恢复一条未读基线，然后通过输入事件添加长中文/连续英文标题，核对卡片边界、完整文本容器、按钮高度/间距、页宽及筛选删除后的全局计数。

两视口原探针各59项、长标题各33项，合计 **184项通过**，其中原探针30项计数、长探针18项固定counts检查（均包含在184项中）。无页级横向溢出或观测到的资源加载失败。手机输入框约43px、添加和卡片按钮40px，长标题按钮横排、间距约9.59px；长中文/连续英文均换行并留在卡片内。单次截图的滚动条会改变可用宽度，部分几何宽度316/301px反映这个差异，不是严格性能或视口对照。

原probe运行结束截图只剩基线书籍，另一次零模型capture运行在英文标题删除前保存截图，确认长内容实际渲染；中文通过操作中的几何记录核对。该额外capture不是第二次生成，也不重复计算功能通过项。共享探针受控测试命令 `node --test scripts/tests/reading-list-browser.test.mjs scripts/tests/reading-delete-browser.test.mjs` 为 **2 passed**；手写夹具只验证探针，本轮真实184项单独记录。

没有发现需要新增生产机制的明确缺陷，因此本轮只补验收证据，不为制造改动而重构。现有Reviewer先依据快照判定，再将有界反馈交回原修复；确定性必填属性门禁、32次实际工具执行与两次修复限制保持，最后用浏览器验证固定业务，不将这些探针接入所有生成流程。

### 36.3 账本、环境和交付

原账本 **330→358，新增28次，max_calls=null**：创建2、首次生成及修复26。28次代理HTTP响应均200，生成一次JSON格式重试已经计入；没有观测到供应商HTTP失败或额外重试。旧三个有限账本哈希保持，未运行诊断复审或第二个生成请求。

预检仅MySQL3306及PostgreSQL5432监听，独立启动Redis和Spring18123/Python18000/代理18001，健康及隔离账号验证通过。Chrome已更新为157.0.8103.51，本轮直接Chrome探针成功，不因此声称旧157.0.8103.78驱动已通过新的Java截图验收。本轮不部署或触发OSS封面。

私有confirm运行器首次因Windows服务日志混合编码发生UnicodeDecodeError；原命令失败保留，随后仅对日志诊断读取使用errors=replace，固定ASCII状态统计正常，原日志和模型证据不改写，不把此错误记成生成失败。原始消息、日志、源码、截图和凭据保存在tmp/vue-css-review-e2e-20261113，不提交。

收尾监督器确认三个临时服务stopRequested=true、exitCode1（主动终止，非自然崩溃），18123/18000/18001释放，独立Redis已停止。MySQL、Docker及原容器保持，日常Spring/Python/Vite未启动或重启。保留后端README原围栏修改、未跟踪取消计划、前端三处客服修改。本轮仅文档提交、不推送；未改Java/Python/前端生产行为，因此未重跑全量、Java编译或前端构建，不把第35节结果称为本轮重新通过。

### 36.4 下一轮建议与仍可能失败的场景

当前固定读书清单的正常首次生成、两次修复和长内容交互已有有限成功样本，不继续采样追求零修复。建议下一轮优先定义并验证 **同名书籍的目标身份与筛选删除**：现有探针要求书名唯一，尚未验证两本同名但状态不同的书，若实现按标题或筛选索引删除，可能误删一条或多条。先明确是否允许重名及目标身份，再给用户说明验收/产品策略的差异并确认，不能擅自修改产品重名规则。

本轮未覆盖同名书、刷新持久化、部署/封面、下载、AppChat流式展示/停止/刷新次数、其他页面结构、动态CSS/路由或不完整快照。Reviewer仍可能忽略证据或误判，初始生成缺props和类不一致也仍会消耗修复机会。需要改变这些行为时先保留具体失败证据并比较最小方案，不提升预算、不接入视觉模型或扩展生产恢复。

## 37. 2026-11-13 同名记录身份验收：方案 A

用户选择保留重名、增强目标身份验收。当前第36节成功样本的addBook分配独立ID，toggle/remove按ID操作；本轮不改变其产品规则或生成源码，只新增固定验收设施。设计/计划见 reading-duplicate-identity-design.md 与 reading-duplicate-identity.md。原书名唯一探针保持，不能放宽它来冒充重名覆盖。

### 37.1 改进原因与实现

旧验收按书名唯一定位，不能发现同名书按标题删全部、按筛选视图索引删错全局记录或按标题删第一条的问题。新增scripts/acceptance_reading_duplicate_checks.mjs，先核对三本原书及3/0计数，添加同名书并标已读，再添加第二本同名未读。沿准确书名叶节点向上最多八层，接受恰好一个状态按钮和一个删除按钮的最小容器，再以书名与状态筛选，必须唯一；不读取Vue私有实例或源码ID，不任取第一条。

在已读筛选删除第一本，核对未读同名仍在且原书未变；将剩余同名标已读，重新添加同名未读，在未读筛选删除后保留已读同名，最后删除已读同名并恢复原3/0。每阶段核对计数、同名数量、状态及原书保持，不能仅看按钮数量或标题是否消失。记录仅固定步骤与计数，不输出页面正文、源码或Cookie。探针只用于隔离页面，会改变内存记录，不接入生产生成流程，不要求刷新持久化。

新增手写夹具和Node浏览器测试，合法按ID删除接受，按标题删全部、筛选索引误删、按标题删第一条、已读计数滞留及同名同状态歧义均拒绝。最终歧义夹具保留两条记录但显示相同状态，明确在unique-state-target失败，不靠数量异常代替歧义验证。手写夹具只证明探针检测能力，不是自然生成或真实模型通过。

### 37.2 本轮验证与失败保留

最终 `node --test scripts/tests/reading-duplicate-browser.test.mjs scripts/tests/reading-delete-browser.test.mjs scripts/tests/reading-list-browser.test.mjs` **3 passed**，包含新夹具的一个合法及五类错误、旧两组回归。第一次断言复用旧探针失败步骤，预期read-filter-button实际read-delete-counts，纠正测试预期；未修改探针接受错误实现。

启动隔离Spring18123及独立Redis，读取第36节实际预览appId478668026865614848的dist/index.html，独立Chrome profile分别1440/390px运行新探针：各 **82项通过，合计164项**，每视口9项计数包含在其中，无观测到的资源加载失败。同名已读删除保留未读、同名未读删除保留已读、原书与计数均正确。是既有自然生成产物的新增浏览器场景，未发送新生成请求，不重算第36节184项或声称本轮新模型生成成功。

私有浏览器运行器首次ReferenceError listProbe未定义，复用旧运行器时注入变量残留；首次浏览器日志保留，改为注入duplicateProbe后零模型复测通过。原页面源码未修改。真实操作记录、截图、日志和凭据在tmp/reading-duplicate-real-20261113，不提交。

### 37.3 边界、交付及下一轮

本轮模型新增 **0次，账本358→358**，没有启动Python或模型代理，没有生成、部署、OSS或恢复。Spring监督stopRequested=true、exitCode1为主动停止，18123释放，独立Redis停止；MySQL/容器保持。前端三处客服修改、后端README围栏及未跟踪取消计划保留。仅提交新探针、测试和设计/交接，不推送；未改Java/Python/前端生产行为，未重跑其全量或构建。

两个同名且同状态的任意个体仍不能仅凭书名/状态区分，探针会拒绝歧义；本轮没有新增面向用户的ID显示、键盘操作、确认删除或持久化要求。刷新、跨请求增量后身份保持以及其他模型生成结构未测，不能据一个ID正确样本证明所有生成应用不误删。

下一轮可优先验证 **真实增量修改后重名身份与旧功能保持**：例如明确新增排序功能后，按标题或排序索引操作可能误删；需先约定排序规则和操作范围，再发一次有业务价值的新请求并复用这些探针。不要为追求自然失败再造多个首次样本，也不要把同状态歧义默认改成任取卡片；若要支持用户区分同名同状态记录，应单独比较可见身份方案并确认。

## 38. 2026-11-16 需求整理下一轮：离线复核与待确认方案

用户在询问查询改写实现后要求继续优化，本轮范围收敛到生成链路的需求整理，不修改客服 RAG 查询。当前工作区另有知识库权威发布相关改动，均保留；本节只记录只读评估与方案，未实现新的生产行为或评估工具。

### 38.1 问题、实现现状与本轮证据

现有 generation_prepare 保留原始 prompt，将有界结构化 brief 和本地前端规则放入 context.generationPreparation，供生成、审查与修复共用。重写最多一次，普通故障降级，取消和硬预算错误沿原路径抛出。默认策略仍 always；有效结构化单属性编辑可跳过辅助重写，可选 conservative 只覆盖有限自由文本语法。引用能在原文找到，不代表需求完整或语义忠实；准备成功及 Reviewer PASS 也不代表页面功能通过。

本轮实际执行以下离线命令，均退出 0，无真实模型或应用生成：

```powershell
cd ai-service
uv run python -m ai_service.orchestration.rewrite_policy_evaluation --dataset evaluation/rewrite-policy-holdout-v2.json --output ../tmp/rewrite-policy-current-20261116-check.json
uv run python -m ai_service.orchestration.preparation_evaluation --dataset evaluation/generation-preparation-v1.json --output ../tmp/preparation-current-20261116-check.json
```

准备集 12 条、三组共 36 条记录均保留原始输入；skills_only/enhanced 两组规则选择分别 12/12。增强组离线为 not_run，不能写成 12 次重写成功或需求覆盖通过。旧独立策略集已在历史诊断中查看，本轮是已知集回归，不是新的独立验证：24 条中正确跳过 0、误跳过 0、漏识别 9、正确重写 15，预计调用 24→24，跳过精确率 null。当前证据仍不支持将 conservative 推广为默认，也未证明辅助重写对产物的增益。

### 38.2 下一轮方案与取舍（待用户确认）

**A，推荐：固定小范围产物对照。** 先限定一个 Vue 读书清单增量请求及明确业务验收，在两个独立应用副本中从相同已验证源码基线开始，保持原文、前端规则、模型配置及原有工具/修复上限一致，仅切换辅助重写。原始成功应用不动。各执行一次正常生成入口；复用现有添加、筛选、删除、重名身份探针，并补充该增量请求所需的有限验收。分别记录准备状态、功能保持、实际调用、修复次数及总耗时；失败样本保留，不采样到成功，不将模型波动或单对结果称为普遍收益。新增结构化编辑会绕过对照，因此选普通自由输入；运行前还需确认基线复制与应用上下文确实等价，不能只复制 dist。继续使用原 model-ledger.json，不新增或重置授权账本。此方案成本是两次真实生成及有限验收，但能补当前最缺的产物证据；不改变生产默认。

**B：扩大 conservative 的有限语法覆盖。** 可针对更多明确单属性修改省去调用，但需要新标注样本和误跳过验证；现有已知集不能重新宣称独立收益，且省略重写是否损失产物质量仍未解决。此方案直接改变运行判断，维护成本及漏约束风险更高，暂不推荐先做。

两种方案尚未获得本轮选择确认，不把方案写成已实现能力。按 AGENTS.md“涉及多个可行方案时先向用户说明差异、收益、代价与推荐理由，等待确认再改生产行为”，以及 brainstorming 技能的设计确认要求，先提交本轮证据与方案，再等待选择。选 A 后需确定具体增量需求、验收与两侧配置，完成设计确认后实施；当前尚未启动真实对照。

### 38.3 交付、限制与环境

本轮仅更新交接文档，真实模型新增 0，原账本 used=358、max_calls=null、last_status=completed，未启动或停止服务、浏览器、代理及容器，未生成、部署或修改项目文件。离线 JSON 保存在 tmp，不提交。不重跑 Java/Python 全量或前端构建，不把历史通过数记成本轮结果。仅提交本节文档，不推送，不包含知识库发布或其他既有工作区修改。下一轮优先 A，因为现有评估只能说明准备机制和有限分类，无法回答额外重写是否减少错误、修复与总体成本；即使一对通过，动态样式、不同页面结构和随机模型输出仍可能失败。

## 39. 2026-11-16 方案 A：开启/关闭辅助改写的配对产物验收

用户明确选择 A，本轮执行固定 Vue 读书清单增量对照，不扩大 conservative 语法、不改变生产默认。执行计划见 `docs/superpowers/plans/2026-11-16-rewrite-artifact-comparison.md`。本轮补上“准备成功之外，实际产物是否保持功能、消耗多少调用”的有限证据，不把一对样本作为统计性收益或所有页面质量结论。

### 39.1 触发、固定条件与处理流程

选用第36节成功读书清单作为基线。两个独立应用使用相同初始需求正常创建，经 Spring 内部工具读取原应用并向两个空目录写入完全相同的10个源码/配置文件，含 package-lock.json，不复制 node_modules/dist；分别 project_build 成功。两侧每个文件 SHA-256 相同，artifact_context 完全一致，原成功应用运行前后源码指纹保持。两个新应用都没有既往生成聊天记录；不能把新副本写成复制了旧应用的完整聊天/checkpoint。appId、requestId、目录身份不同为必要隔离差异。

增量普通文本需求为增加“添加顺序”“书名升序”按钮，默认添加顺序，中文 localeCompare 升序，同名保持添加先后；切换筛选保持排序、切回恢复添加顺序。保留原文字、样式、添加/空白拒绝/筛选/状态切换/按记录删除/计数，允许重名，不增加图片、后端或持久化。不传 editIntent，避免结构化跳过掩盖开关对照。

仅在本轮独占 Python 进程分别设 AI_SERVICE_PROMPT_REWRITE_ENABLED=false/true；前端规则均开启、policy=always，保持原模型配置与40次共享工具/两次修复上限。先关闭组、再清洁启动开启组，Spring与累计账本代理不变，每组各一次正常 `/apps/chat/gen/code` SSE，提交标记发送前独占落盘，不重发或采样到成功。两侧均选 design-core@1、accessibility@1、vue-native@1、control-consistency@2、preserve-style@1。生成后明确 requestId 重查台账，并从 PostgreSQL 状态摘要核对 finalize/quality/repair/tool 计数；实际浏览器验证独立于 Reviewer。

| 项目 | 关闭辅助改写 | 开启辅助改写 |
| --- | --- | --- |
| appId | 479853126940717056 | 479853210306703360 |
| requestId | 7f4b91aa-511c-445e-b25a-32573b3fa1e8 | 3cf4bc99-f011-49a1-a150-e2cb2b7a9c66 |
| 准备状态/原因 | disabled/DISABLED，未尝试模型 | ready/NONE，尝试一次，brief 3620 bytes |
| 准备耗时 | 约0.015秒 | 约4.156秒 |
| 生成入口总耗时 | 26.06秒 | 20.92秒 |
| 本请求模型调用（含失败/格式重试） | 13 | 10 |
| 工具次数/修复次数 | 17 / 0 | 8 / 0 |
| 首轮工具JSON格式重试 | 1 | 1 |
| 台账/摘要 | SUCCEEDED、TERMINAL、NOT_VERSIONED；finalize/qualityPassed | 同左 |
| 实际浏览器检查 | 326/326 | 326/326 |

两侧 SSE HTTP200、done1、business-error0、invalidFrame0、唯一终态1。成功SSE未携带可提取的requestId，先查隔离应用最近任务，再用明确requestId复核；不据此声称公共事件增加了字段。关闭组说明本案例不依赖辅助摘要也可完成；开启组在增加一次整理调用后仍少3次请求调用、少9次工具，总时间少5.14秒，但没有显示修复率优势（均0）。固定先关闭后开启，存在缓存预热、供应商时延及随机输出差异，不能将差值全部归因于摘要或据此推广策略；没有测量金额、吞吐或延迟分位数。

### 39.2 新验收实现、验证与未覆盖项

新增 `scripts/acceptance_reading_sort_checks.mjs`。触发于调用方指定的隔离页面，使用唯一可见文字按钮和书名叶节点定位，不读取Vue内部数组。先不点击添加顺序，以免掩盖默认排序错误；添加Z/A前缀两本测试书，核对默认顺序、中文环境升序、未读筛选后排序不变、恢复添加顺序，然后只删除两本测试书，核对原书及3/0计数，最后留在升序继续执行已有重名身份探针。明确控件/书名歧义直接失败，不任取首个匹配。此探针只服务固定需求，不成为所有应用的生产门禁。

手写浏览器夹具包含正确实现、排序无效、原地排序破坏添加顺序、筛选重置排序四种；新测试必须接受正确实现，并分别在 ascending、restore-insertion、filter-keeps-sort 拒绝三个错误。实际执行 `node --test scripts/tests/reading-sort-browser.test.mjs scripts/tests/reading-duplicate-browser.test.mjs scripts/tests/reading-delete-browser.test.mjs scripts/tests/reading-list-browser.test.mjs`，**4 passed，0 failed**。手写夹具证明探针拒绝能力，不是模型产物成功。

两个真实增量产物分别在1440/390px运行：每视口重新加载后旧功能/删除59项，另行重新加载后排序22项及升序下重名身份82项，合计每组326项、两组652项通过，不累计旧轮检查数。无观测到资源加载失败或初始/验收结束页面横向溢出；查看两组手机初始截图，新增按钮保有间距，样式实现并不完全相同，不将截图当全面视觉门禁。两份生成源码分别使用复制数组排序，关闭组另按ID打破同名平局，开启组依赖现代JS稳定sort；未单独对两个同名且同状态记录的显示次序做浏览器身份断言。任意未知页面、中文排序全部边界、刷新持久化、键盘/无障碍、长内容、新生成类型仍未覆盖。

首次汇总脚本读取UTF-8浏览器报告时使用Windows默认GBK，发生UnicodeDecodeError；修正该诊断读取为明确UTF-8后汇总成功，原JSON及生成结果未改写，没有重发生成。两侧自然发生的JSON格式重试均保留且计入账本，不能写成首次响应全正确。

### 39.3 调用、环境、提交与下一轮

原累计账本 **358→385，新增27次**：两次正常创建共4次，关闭组13次，开启组10次；max_calls=null、last_status=completed。旧三个有限账本哈希不变，未换账本、诊断复审、部署或OSS调用。原成功应用源码经Spring读取复核不变。私有源码基线、模型/工具输入输出、SSE摘要、截图和凭据位于 `tmp/rewrite-artifact-pair-20261116`，不提交。

启动本轮独占Spring18123/Python18000/代理18001，开启组前仅重启独占Python，关闭组监督记录已退出；最后三个服务均stopRequested=true、exitCode1（主动终止），端口全部释放。原已运行Redis6379、MySQL3306、PostgreSQL5432仍监听，未停止或重启其服务/容器；未操作日常前端。Spring复用已有target/classes，未为本轮重编译，不把本轮称为最新Java源码干净构建。并行知识库发布工作及README既有改动保持，本轮不涉及Python/Java/前端生产行为，未重跑其全量或构建。

本轮只提交新探针、夹具、测试、执行计划和交接，中文提交、不推送。生产always默认保持，当前配对样本不足以调整默认。下一轮建议先分析**两组共同出现的首轮Vue工具JSON格式重试**：这是本次实际发生且可避免额外调用的候选成本，优先用保留的响应与严格解析器做离线重放，区分模型未遵循协议、提示冲突或解析边界，再比较最小提示修正与解析规则调整；确认方案后才改运行行为。不要增加预算或放松非法响应执行边界。仍可能遇到真实语义错误、动态CSS/路由、不完整快照及不同需求无收益；本轮不承诺解决这些问题。

## 40. 2026-11-16 Vue 首轮 JSON 提示修正：方案 A

### 40.1 问题与根因证据

承接第39节，先离线使用当前 `_vue_model_turn` 重放两组已保存的18份Vue生成响应：16份接受，两份首轮均拒绝，正常STOP结束、各210字符；两份响应SHA-256一致，前20字符是对象外读取说明，随后为完整的两工具读取JSON。只在私有诊断副本去除说明后，原解析器均能识别两个工具。因此不是本例JSON字段损坏或长度截断，也没有理由扩大格式重试次数。原失败响应保持拒绝，未执行工具。

初始共用提示要求工具调用使用严格JSON，但没有像 `vueFormatFeedback` 一样明确禁止整轮响应的前后说明。事实能证明这一协议表达缺口及模型违规，不能证明已找到模型内部生成该说明的全部原因。两份输出相同也不足以推断供应商缓存机制。

比较A（提前显式提示，保持解析边界）与B（从说明中尝试提取JSON）；用户明确选择A。前者改动与维护成本小，后者会放宽接受范围并引入多对象歧义。按A只改Vue共用提示，不改变解析器、工具契约、每阶段两次额外格式重试或40次共享工具/两次修复上限。

### 40.2 实现条件、模块和流程

`ai-service/src/ai_service/prompts/generation.py` 的Vue工具协议开头增加两条规则：每轮只返回一个完整JSON对象，禁止JSON前后说明、前言、总结、多段JSON及Markdown围栏；计划、读取原因和完成状态等简短说明必须放入content。`OpenAICompatibleModel.generate` 与Vue `repair` 已共用此提示，无需新增路由、状态字段或开关，首次生成、已有页面修改及修复首轮均收到限制。静态HTML/MULTI_FILE、准备摘要和Reviewer协议不变。

这是提示约束，不是接受范围改变：原解析器对完整围栏的兼容仍在，对象外说明仍拒绝；非法轮次整轮丢弃，不从其内提取合法子片段。后续仍可能因模型不遵循提示、截断、非法参数或语义错误失败，不能宣称强制JSON模式或必然零重试。README在对应生成行为段同步此边界。

### 40.3 离线与真实诊断

新增三个适配器消息测试，分别覆盖首次generate、已有项目generate和repair，在无vueFormatFeedback时核对输出规则已出现在真实系统消息且原始需求/基线保持。先在旧提示上执行三项均失败，补提示后适配器/Vue子图/修复专项共64项通过；已有非法JSON和子图回归继续覆盖拒绝与工具边界，不新增重复解析器实现。compileall与uv lock --check通过。

默认完整pytest为1605 passed、4 failed、22 skipped、2 warnings（171.50秒）。四项均在test_reranker的手写进程夹具因共享模型GPU所有者锁拒绝而失败；不解锁、不停止既有模型。后续仅测试进程使用既有Isolation夹具为这四项分配独立模型锁名，RAG及其他生产配置不变。首次私有隔离运行器缺少Windows多进程入口保护，子进程再次进入pytest而阻塞；定位后核对本轮uv进程身份，停止其进程树，保留原日志，不算作完成的全量结果。补上__main__保护后隔离全量为 **1622 passed、22 skipped、2 warnings（197.59秒）**，不能称默认环境全通过。两次收集总数不同，工作区存在并行知识库改动，不将增加项算成本轮新增覆盖。22项真实资源opt-in未开启，两个依赖弃用警告保留。

一次只解析的真实诊断复用第39节开启组首次上下文。新旧human消息逐字一致，system消息差异核对仅新增两条输出规则；没有改变已选规则、原文、预算或工具结果。SDK已构造异步客户端使用with_options(max_retries=0)，重新绑定实际completions资源，不仅修改LangChain字段。发送前独占落盘标记，原累计账本385→386，仅一次调用，约1.5秒；响应STOP、原始正文直接json.loads成功、适配器invalid=false、识别两个读取工具，filePlan为空。未调用任何模型请求的工具，未创建、生成、写入或发布应用；不是新增端到端成功，不据单次诊断计算稳定格式成功率。

### 40.4 环境、提交与下一轮

本轮只为诊断独占启动代理18001，结束后监督记录stopRequested=true、exitCode1，端口释放；没有启动Spring/Python服务或浏览器，不改变第39节应用。原账本used=386、max_calls=null，失败也计费的预占机制保留。私有重放摘要、原消息、诊断输出及代理日志位于 `tmp/vue-format-replay-20261116`，不提交。知识库权威发布的并行源码、配置、测试和README既有修改均保留；仅暂存本轮README段落，不混入其他变更。中文提交，不推送。

下一轮优先将此提示放回一次有业务价值的正常增量生成，核对首轮是否自然避免说明前缀、后续是否仍有格式重试，并复用旧功能及重名身份验收；原因是本轮只有初始模型响应的真实诊断，未检验完整生成、构建、审查、修复及浏览器链路。不要重新创建多个首次样本追求零重试，不增加预算；若没有新的增量需求，先整理现有观测而不反复采样。不同文件大小、首次规划、修复提示和供应商随机输出仍可能失败，工具JSON合法也不证明交互正确。

## 41. 2026-11-16 新 JSON 提示下的单次正常增量验收

### 41.1 范围、触发与实现流程

本轮按用户交接执行一次第39节已定义的排序增量，不改变生产行为，不重复对照或采样到成功。先核对当前 AGENTS、交接第38–40节、提示与适配器、准备节点、Vue工具子图和固定浏览器探针。后端已有 `ai-service/README.md` 修改及未跟踪取消回归计划、前端三处客服修改均保留；当前 HEAD 已包含并行客服发布提交，不回退或混入本轮提交。

第40节生产修正仍只有共用 Vue 提示的单对象要求：generate/repair 在发送前收到规则，严格解析器继续整轮拒绝对象外说明，非法响应不执行工具。本轮没有再次修改提示、解析器、工具契约、每阶段格式重试、40次共享工具预算或两次修复上限，也没有加入视觉模型或恢复机制。辅助需求整理仍开启、策略 always，原始自由文本和基线通过原链路传递。

从原成功应用 `478668026865614848` 经 Spring 工具读取10个源码/配置文件（含锁文件），向一个正常创建的新应用写入相同内容；不复制 node_modules/dist。逐文件 SHA-256 与原应用及第39节基线一致，artifact_context 非截断，种子工程 project_build 成功。新副本未复制历史聊天或 checkpoint。发送前独占落盘标记，然后只调用一次正常 `/api/apps/chat/gen/code` SSE 入口；不手工修改生成工程、不部署。

### 41.2 本轮真实结果与工具消耗

| 项目 | 本轮结果 |
| --- | --- |
| appId | 479861830440259584 |
| requestId | 76918890-279b-474e-8229-58b8ea091201 |
| 准备 | ready/NONE，一次整理尝试，约3.422秒，brief 3011 bytes |
| 规则 | design-core@1、accessibility@1、vue-native@1、control-consistency@2、preserve-style@1 |
| 入口总耗时 | 43.80秒 |
| 请求模型调用 | 29次，含审核、整理、生成和审查；创建另2次 |
| Vue响应 | 26份全部正常STOP，原始正文直接 json.loads 为对象，原适配器全部接受 |
| 格式重试 | 0；首轮无格式反馈即返回两个读取工具，所有轮次均收到新规则 |
| 工具 / 修复 | 38 / 0 |
| 台账及PostgreSQL摘要 | SUCCEEDED、TERMINAL、NOT_VERSIONED；明确requestId复核，finalize、qualityPassed=true |
| SSE | HTTP200，done1、business-error0、invalidFrame0、唯一终态1 |
| 浏览器固定检查 | 326/326，1440与390px各163项 |

成功SSE仍没有可提取的requestId，先取隔离应用最新任务，再以明确requestId复核，不声称协议新增字段。正式构建返回 built=true、空errorCode；种子与正式构建均提示有限静态锚点检查跳过不支持或不完整页面，不能将构建通过写成导航门禁全部执行或浏览器交互证明。未触发修复，故本轮没有真实修复路径证据。

26份响应没有再次出现第39节的说明前缀；这是一次完整增量请求的格式表现，不是稳定成功率提升。成本也没有改善：本次29次请求模型调用、38次工具、43.80秒，高于第39节开启组10次、8次、20.92秒。不同运行存在模型随机性、顺序与缓存影响，不能据单次差值认定新提示导致回退，亦不能宣称整体提速。

旁路响应统计和终态工具数一致：35次 file_read、2次 file_write、1次 file_modify。读取涉及9个不同文件，三份文件分别读11、9、8次；写入涉及2个不同文件。最后一次修改之后仍有13个纯读模型批次、18次读取，随后才返回空工具列表。这里只证明重复读取和消耗，不据路径重复宣称每次结果完整相同或全工程无变化。`subgraphs/vue.py` 仅为正式 repair 开启三类读取/写入/预算收口保护，本轮是已有工程的生成阶段，因此未启用这些保护；不能称修复保护失效，也不在本轮直接扩展它的适用范围。

### 41.3 实际验证、失败与未覆盖项

本轮重新运行四项固定探针夹具测试，**4 passed、0 failed**：排序、重名身份、删除和基础读书清单。实际产物每视口分别重新加载后运行旧功能/删除59项，再重新加载运行排序22项及升序下重名身份82项，1440/390px合计326项全部通过，包括添加、空白拒绝、已读切换、筛选、默认添加顺序、中文localeCompare预期、筛选保持排序、恢复添加顺序、排序后重名删除和计数。网络加载失败0，初始及验收结束没有观测到横向溢出；人工查看本轮390px截图，排序按钮与卡片操作保有间距。截图和固定探针不是任意页面通用质量门禁。

正常请求、种子/正式构建及浏览器探针没有失败；一条额外就绪探测使用不存在的Spring `/api/health` 返回404，随后通过Python ready、Spring正常登录/工具/API执行确认服务可用，不将404当作健康接口通过。事后构建摘要脚本曾用 tool-*.json 误匹配新生成的统计JSON，发生 KeyError: name；原错误说明保存在私有诊断文件，将统计文件改名并防御性读取后确认只有一次正式构建且成功。没有重发生成或改动结果。

未覆盖首次规划、真实修复、失败/取消、同名同状态记录的显示次序身份、中文排序全部边界、刷新持久化、键盘/无障碍、长内容、其他应用或生成类型、公网部署和OSS。Reviewer PASS不代替浏览器检查；本轮固定探针也不证明所有交互正确。没有修改Python/Java/前端生产代码，因此未重跑其全量测试、干净编译或前端构建；Spring复用已有 target/classes，不能称最新Java源码干净构建通过。

### 41.4 调用、清理、提交与下一轮

仍使用 `tmp/real-e2e-unlimited-20261111/model-ledger.json`：**386→417，新增31次**（正常创建2次、唯一增量请求29次），max_calls=null、last_status=completed。未新增/重置账本，旧三个有限账本哈希不变。没有额外模型诊断、工具响应重放执行或失败重试生成。私有运行器、原消息及响应、源码基线、SSE/台账/状态摘要、截图和失败说明均在 `tmp/vue-json-e2e-20261116/`，不提交。

本轮独占启动Spring18123、Python18000、代理18001，结束后仅停止本轮监督器所属进程树；三个监督记录均exited、stopRequested=true、exitCode1（主动终止），三端口释放。原应用源码最终经Spring读取再次核对不变。日常Spring8123/Python8000的PID分别保持34320/35956，MySQL3306、Redis6379、PostgreSQL5432仍监听；未停止或重启日常服务、前端或容器。

本轮仅提交本节交接，中文Conventional Commit，不推送；提交哈希以本节文档提交及交付消息为准（避免自引用哈希）。README既有修改、未跟踪取消计划和前端客服三文件保持原状。

下一轮优先离线分析本次**生成阶段写入后的重复读取**：复用现有26份消息及结果，核对模型历史投影是否使旧结果省略/失效、读取是否完整，以及模型是否已有足够信息交回验证，再比较最小提示调整与有界生成收口。原因是格式成本本次为零，而读取已用35/38次工具，距离40次总额仅余2次；若后续发生质量拒绝，可用修复工具很少。先提供证据和收益/代价，获用户确认后再改变生产行为，不直接把repair阈值推广到生成，不增加预算、放宽非法响应或继续真实采样追求低调用。较早验证仍可能遇到未完成修改，历史投影、复杂依赖或模型语义问题也可能继续失败。

## 42. 2026-11-17 生成阶段重复读取离线定位与待确认优化

承接第41节，本轮复用保存的26份模型消息调查读取成本，没有新增真实采样或生产行为修改。具体证据、候选文案、实现模块、方案收益/代价和验证范围见[重复读取分析与方案](vue-generation-read-analysis-20261117.md)。用户已明确授权后续需要Docker、Spring或Python服务时自行核对启动，无需重复请求服务启动授权；保留其他会话已运行资源，只清理本轮自己启动的进程。

### 42.1 问题、流程和本轮证据

恢复38条完整工具历史，用当前OpenAICompatibleModel._vue_messages及本地预算重建26轮Human消息，全部逐字段一致；35次读取均符合现有成功完整有界读取判定。工具历史最大23707字节，低于24576上限，history_budget省略0；旧记录摘要均为superseded，最新结果可见。23次读取请求已有最新完整相同源码可见，返回内容仍相同。消息投影没有因预算丢失最新所需文件的证据，不应通过扩大上下文解决本例。

共用提示一面限制重复确认，另一面未限定首次创建地要求组件/import目标“后续批次写入、全部写完后读取确认”。这存在已有工程修改的范围歧义；状态说明反复确认文件/样式支持过度核对的观察，但不能证明模型内部成因。建议A只调整提示：已有完整当前源码直接复用，首次创建才要求完整文件计划及必要目标全部写入，已有工程只完成受影响修改与必要依赖核对，完成后空工具列表交回平台构建/审查。严格JSON、工具权限、40次/两次和repair原收口保持。

备选B为已有工程生成新增确定性有界收口，需要单独阶段/批次/checkpoint/取消与未完成修改测试，不能直接套用repair阈值。按保存轨迹，三批纯读条件第5轮累计8工具、四次重复条件第9轮累计15工具即命中，但原第12轮仍实际修改比较器，增加ID同名平局处理。现代稳定sort可能已满足旧实现，因此不称后续修改必然必要；提前结束确实会改变轨迹。反事实命中不是实测节省或质量通过，未验证被截断产物。推荐先A，代价是提示不保证阻止所有重复读取；B可能提前消耗修复机会，暂不实施。

### 42.2 验证、失败、环境和交付

本轮重新运行 `uv run pytest tests/test_vue_context.py tests/test_vue_repair_read_stall.py -q`，**30项通过、2条既有依赖弃用警告**。离线消息重建/计数/条件检查重复执行结果一致，23次“最新可见且返回内容相同”另作交叉核对；原26份证据哈希不变。一次rg查找使用PowerShell下不展开的通配文件参数返回路径错误，随后以rg --files定位准确文件，不影响分析或测试。无生产修正、真实生成、工具执行、应用改动、浏览器或服务操作；未跑Python全量、Java/前端构建，不将第41节的真实成功记成本轮通过。

原累计账本used=417、max_calls=null，字节哈希不变，新增模型调用0；私有分析脚本/JSON在tmp/vue-generation-read-analysis-20261116，不提交。当前后端并行会话已新增知识库发布Controller/Mapper/协议/Service/XML修改，全部保留，README及未跟踪取消计划保持；前端三处客服修改保持。本轮仅提交分析报告和本节，中文提交、不推送，哈希以对应提交与交付消息为准。

下一轮先请用户确认报告中A/B具体取舍，已完成只读调查和可审阅文案后再修改生产行为。此确认来自AGENTS“涉及多个可行方案时先向用户说明差异、收益、代价与推荐理由，等待确认再改生产行为”，并符合brainstorming设计确认要求；不是服务启动或真实模型额度再次授权。A确认后补适配器实际消息测试、同步README、必要Python验证，再以明确范围评估效果；不保证提示收敛，不放宽非法响应，不增加预算，不触碰客服发布或恢复机制。

## 43. 2026-11-17 方案A：已有工程读取范围提示实施

用户明确选择第42节A。本轮只优化Vue共用提示，保持模型必要读取与继续修改的机会，不采用生成阶段自动收口。实施计划为 `docs/superpowers/plans/2026-11-17-vue-existing-project-read-scope.md`，设计与原问题证据见[重复读取分析](vue-generation-read-analysis-20261117.md)。第41节的成功及第42节的23次重复读取是历史证据，不写成本轮重新验收。

### 43.1 触发、判断、流程与模块

原触发场景是已有读书清单增加排序后，模型在最新完整源码仍可见时反复确认文件和共享样式。旧提示要求每次修改前读取，又未限定首次创建地要求所有组件/import目标后续写入及读取确认，存在已有工程修改的范围歧义。直接推广repair收口可能截断后续修改，故按A先减少提示歧义；这不能证明模型内部根因或保证成本下降。

`prompts/generation.py` 现在明确：当前请求已有最新完整file_read结果时直接复用；源码尚未读取、被后续写入替代或明确省略，且确实需要时才再次读取。currentArtifact.entries仍只是清单，不能当源码。首次创建必须写齐必要入口与本地导入目标；已有工程只写本次新增/修改文件，不重建首次计划，不重写未修改依赖。必要写入后仅核对受影响的导入、props、事件及共享样式，已有完整结果时不重复请求工具；发现具体遗漏继续读取/修改，文件、引用、本次修改和配图齐备后空toolCalls交回原构建及质量审查，不虚报构建结果。

OpenAICompatibleModel.generate/repair已共用此提示，无需改路由、适配器执行代码或子图状态。首次创建、已有工程修改与修复都会收到这些规则，首次计划/pendingFiles和实际修复反馈仍沿原路径处理。没有新增读取缓存、工具拒绝、自动收口、状态字段、SSE或数据库结构；严格JSON/对象外说明拒绝、原格式重试、24 KiB历史、40次共享工具/两次修复和repair确定性保护不变。HTML/MULTI_FILE提示经AST与HEAD值比较确认未变。

### 43.2 验证与失败

新增三个参数化适配器实际消息测试，覆盖首次generate、已有工程generate和repair：无额外反馈时系统消息已含范围和复用规则；原始prompt、currentArtifact、完整toolResults保持，输入不被修改，合法必要file_read仍被解析为工具调用。旧提示上三项均失败，修改后定向适配器/Vue子图/上下文/修复读取/修复预算测试全部通过（117项、两条既有警告）。这验证消息传播和边界保持，不模拟真实模型一定停止重复读取。

`uv run python -m compileall -q src`、`uv lock --check`通过。默认 `uv run pytest -ra --tb=short` 完整结束为 **1634 passed、24 skipped、2 warnings，198.48秒**，本次没有失败，也未使用隔离GPU锁或关闭RAG的测试运行器。跳过项需要显式真实资源opt-in；两条依赖弃用警告保留。运行期间客服发布并行修改继续增长，不能将相较历史的测试数量增长全部算作本轮新增覆盖；本轮新增仅上述三项。全量日志 `tmp/vue-read-scope-20261117/pytest-full.log`，不提交。

没有本轮真实生成、应用文件修改、构建/Reviewer/浏览器或端到端结果；未重跑Java编译、前端构建或真实资源集成。本轮不能声称调用减少、稳定提速、页面交互通过或第41节重复读取已真实消失。首轮规划、大文件、模型不遵循提示、动态依赖和语义遗漏仍可能失败。

### 43.3 文档、调用、清理、提交与后续

README同步当前提示行为和限制，仅暂存本轮新增段落。后端AGENTS、README既有围栏、客服权威发布Controller/Mapper/协议/Service/XML、Python发布及容量模块、测试和未跟踪文件均保留；前端三处客服修改保持。只提交本轮提示、适配器测试、实施计划、README增量及本节，中文Conventional Commit，不推送；提交哈希以交付消息及本节对应提交为准。暂存前检查发现并行会话已暂存客服README段落，保护断言拒绝直接混合操作；采用独立临时Git索引只提交本轮内容，保留原索引其他暂存内容。

累计真实模型账本仍used=417、max_calls=null，本轮新增调用0。没有启动或停止Docker、Spring、Python、代理或浏览器，无需本轮资源清理。用户已授权后续必要服务自行核对启动；本次提示生效需要加载新代码的Python进程，未主动重启日常服务。Spring、前端和数据库无需因本次提示调整重启。

下一轮优先一次新提示下的正常已有工程增量验收：复用原成功应用的相同源码基线建立新隔离副本，固定原排序需求，唯一一次请求保留成功或失败。重点核对首次及后续读取、写入后重复读取、共享工具剩余额度、格式重试、构建审查/有限修复和旧功能/排序/重名身份探针。仍沿用原累计账本，不采样到低成本或成功，不手补工程、不增加预算。原因是本轮只有消息和Fake执行回归，缺少新提示下的真实产物及成本证据；即使一次通过也不代表普遍收益，不将repair收口直接推广为生成门禁。

## 44. 2026-11-17 读取范围提示后的单次真实增量验收

### 44.1 范围、流程与实现模块

用户要求继续下一步，本轮落实第43节的真实验证，不再修改生产提示或预算。从原成功应用 `478668026865614848` 经Spring工具读取相同10个源码/配置文件（含package-lock.json），正常创建一个新隔离应用后写入，不复制node_modules/dist；种子构建通过，逐文件指纹与第39/41节基线一致，artifact_context非截断，原应用源码前后不变。副本没有复制原聊天/checkpoint。

本轮加载 `f58da78` 的Vue共用提示；generate/repair仍通过原适配器、24 KiB历史投影和Vue工具子图，必要读取、实际写入及原构建/质量审查正常执行。辅助整理开启、策略always，原固定排序需求逐字复用，规则与第41节相同。发送前独占落盘标记，只调用一次正常SSE生成入口，不重发、不手改生成工程、不部署。提示文件运行前后SHA-256一致，原始模型消息也核对含第43节读取复用规则；没有生成阶段自动收口、读取缓存或额外模型门禁。

### 44.2 本轮真实结果与比较限制

| 项目 | 第41节历史结果 | 本轮实际结果 |
| --- | --- | --- |
| appId | 479861830440259584 | 480114972604096512 |
| requestId | 76918890-279b-474e-8229-58b8ea091201 | 778e5fcf-1772-4da0-a84b-d99bc3424560 |
| 整理状态/耗时 | ready，约3.422秒 | ready/NONE，约4.454秒，brief 3191 bytes |
| 请求模型调用 | 29 | 10 |
| Vue模型响应 | 26 | 7，全部STOP且直接JSON对象接受 |
| 工具/修复 | 38 / 0 | 10 / 0 |
| 文件读取/写入/修改 | 35 / 2 / 1 | 8 / 2 / 0 |
| 最新完整相同源码可见时的重复读取 | 23 | 0 |
| 最后写入后的纯读批次/读取 | 13 / 18 | 4 / 6 |
| 生成入口耗时 | 43.80秒 | 22.67秒 |
| 固定浏览器检查 | 326/326 | 326/326 |

本轮8次读取涉及6个不同文件，全部恢复为符合现有完整读取资格的结果；写入后的两个目标文件需读取当前源码，后续受影响依赖/共享样式检查未重复请求最新已可见同内容文件。历史预算省略0。没有自然格式重试，也没有触发修复。本轮比第41节少19次请求模型调用、28次工具、21.13秒，但固定时序、供应商缓存、时延和随机输出均可能影响差值；本轮整理摘要也自然不同，不能把全部差值归因于提示。第39节开启组本就为10次模型/8次工具/20.92秒，本轮没有超过该历史低成本结果，不宣称稳定提速、平均收益或统计成功率，也不改变always默认。

显式requestId复查MySQL台账为SUCCEEDED、heartbeat TERMINAL、publication NOT_VERSIONED，PostgreSQL摘要为finalize、qualityPassed=true、toolCallCount10、repairCount0。SSE HTTP200、done1、business-error0、invalidFrame0、唯一终态1；成功事件仍无可提取requestId，使用隔离应用最新任务定位后明确复核。规则为design-core@1、accessibility@1、vue-native@1、control-consistency@2、preserve-style@1。

### 44.3 验证、失败与未覆盖项

本轮四项固定浏览器探针夹具测试 **4 passed、0 failed**。实际产物在1440/390px分别重新加载：旧功能/删除59项，排序22项及升序重名身份82项，每视口163项、合计 **326/326通过**，覆盖添加、空白拒绝、已读切换、筛选、默认顺序、中文localeCompare预期、筛选保持排序、恢复添加顺序、排序后重名删除和计数。资源加载失败0、初始与检查结束未观测横向溢出；查看本轮390px截图，排序组及卡片操作有间距。截图与固定探针不能作为任意页面通用门禁。

种子和正式project_build均built=true、空errorCode，但有限静态锚点检查均提示跳过不支持或不完整页面，不将构建通过写成锚点或浏览器交互全部验证。本轮请求、构建、浏览器及离线响应统计没有失败，没有再次生成。未覆盖真实repair/失败/取消、首次文件规划、同名同状态显示身份、全部中文排序边界、持久化、键盘/无障碍、长内容、其他页面/类型、公网部署和OSS。没有本轮生产代码变动，未重跑Python全量、Java干净编译或前端构建，不沿用第43节测试数作为本轮结果；Spring复用已有target/classes，不称本轮最新Java源码干净编译通过。

### 44.4 调用、清理、提交与下一轮

原累计账本 **417→429，新增12次**（正常创建2次、唯一增量请求10次），max_calls=null、last_status=completed。仍沿用同一账本，旧三个有限账本哈希不变；没有额外真实诊断、模型重审或重放执行。私有运行器、输入输出、源码基线、SSE/台账/摘要、布局截图与响应统计位于 `tmp/vue-read-scope-e2e-20261117/`，不提交。

本轮独占启动Spring18123、Python18000、代理18001，结束后只停止本轮监督器进程树；均exited、stopRequested=true、exitCode1（主动终止），三端口释放。原Spring8123的PID27904保持；结束核对时Python8000另有PID18176运行，不属于本轮资源，未操作。MySQL3306、Redis6379、PostgreSQL5432始终监听，不需启动或重启Docker/容器。原应用最终经Spring工具读取确认源码不变。

并行客服工作在本轮期间继续提交/变化，保留其代码、文档和所有未跟踪文件，前端三处客服修改保持。本轮仅提交本节，中文Conventional Commit、不推送，哈希见对应提交与交付消息。下一轮不重复排序场景采样追求低调用；优先在新的明确增量需求中复用现有观测与功能探针，或针对真实修复/失败证据继续分析。原因是本例重复确认本次未再发生，而尚无跨场景和真实修复证据；暂不增加生成自动收口、扩大预算或推广默认策略，模型仍可能违反提示、遗漏依赖或消耗修复额度。

## 45. 2026-11-17 书名搜索：新场景单次自然增量验收

### 45.1 问题、选择、触发与实现

第44节只在排序场景得到一次低重复读取结果，不能推断跨场景效果。本轮先核对AGENTS、主交接第41–44节、离线读取分析、已完成提示计划、当前提示及固定探针，再提供两个方向：新书名搜索产物验收，或只读分析已有真实修复/失败。用户选择书名搜索并要求继续执行；服务启动和原累计模型账本授权延续，不重新请求同一授权、不派生代理。

固定需求是新增独立搜索框，首尾trim后按书名包含匹配，与当前已读筛选/排序叠加，空白或清空恢复；全局共N本/已读M本不随搜索结果变动，添加和搜索输入互不干扰。保留添加、空白拒绝、删除、状态切换、中文localeCompare及同名添加先后顺序、身份操作、原文字和暖白深绿色样式，不增加图片、后端或持久化。相比重复排序采样，该场景增加状态叠加和双输入证据；相比只读失败分析，它可观察新提示下真实产物，但不能保证自然触发repair。

生产generation.py未改。流程仍为generation_prepare整理原文，再由generate/repair共用提示和原Vue子图执行必要读取/写入，交回原构建与Reviewer。原文不被brief覆盖，always、严格JSON、每阶段格式重试、24 KiB模型历史、40次共享工具和两次修复、SSE保持；没有生成自动收口、读取缓存或拒绝工具。选择以验收补证据，不凭单例继续改生产阈值。

旧基础/删除/排序/重名探针假定单个文本输入，新增搜索后会误拒绝正常页面。本轮在四个固定探针增加可选addInput，默认单输入路径保持；显式输入必须属于当前document、仍连接、为非禁用非隐藏文本输入。搜索探针readingSearchInputs从唯一添加按钮所在最小单输入容器定位添加框，将其余唯一文本框作为搜索，歧义直接失败，不按位置猜测。verifyReadingSearch核对trim、包含、筛选/排序叠加、清空、全局计数、搜索中添加/删除与同名状态/删除身份。它只支持固定清单和有限DOM边界，不是任意Vue语义门禁。计划为docs/superpowers/plans/2026-11-17-vue-reading-search-acceptance.md。

### 45.2 基线、唯一请求与实际结果

基线改用第44节含排序的成功应用480114972604096512，经Spring工具复制相同10个源码/配置文件及锁文件到正常创建的新应用，不复制node_modules/dist、聊天或checkpoint。逐文件指纹相同，种子构建通过、artifact_context非截断，发送前独占落盘标记，仅发送一次普通自由文本SSE生成；不手补生成工程、不部署。原基线结束前再次经Spring读取核对不变。

| 项目 | 本轮实际结果 |
| --- | --- |
| appId / requestId | 480178039794745344 / 5b360183-a05f-430e-a11e-7be86d354dda |
| 整理 | ready/NONE，一次尝试，约3.641秒，brief2896 bytes |
| 规则 | design-core@1、accessibility@1、vue-native@1、control-consistency@2、preserve-style@1 |
| 请求耗时 / 模型调用 | 28.38秒 / 14次；正常创建另2次 |
| Vue响应 / 格式重试 | 11份，全部STOP且直接JSON对象接受 / 0 |
| 工具 / 修复 / 剩余工具 | 16 / 0 / 24 |
| 工具构成 | 11次file_read、1次file_write、4次file_modify |
| 读取证据 | 5个文件，11份完整结果，history_budget省略0 |
| 最新完整相同源码可见时重复读取 | 2次 |
| 最后写入后纯读批次 / 读取 | 2 / 3 |
| 台账 / PostgreSQL | SUCCEEDED、TERMINAL、NOT_VERSIONED；finalize、qualityPassed=true、toolCallCount16、repairCount0 |
| SSE | HTTP200、done1、business-error0、invalidFrame0、唯一终态1 |
| 最终固定浏览器检查 | 556/556，1440与390px各278项 |

写入只涉及FilterBar.vue（1次）和App.vue（4次）；仅说明观察到的写入范围，不证明全部变化都语义必要。模型消息确认收到第43节读取范围提示；此次仍有2次最新同内容重复读取，不能宣称提示彻底阻止重复确认。成本高于第44节历史的10次模型/10次工具/22.67秒，本轮需求和产物基线不同，不做因果或平均提速结论；仍有24次工具可用不证明后续repair必然收敛。

成功SSE仍不能提取requestId，先取隔离应用最新任务，再以明确requestId复核，未新增协议字段。正式及种子构建成功，有限静态锚点检查均提示跳过不支持/不完整页面，不能称导航门禁全部执行。本轮没有真实repair，不把前述历史修复证据写成本轮已验证。

### 45.3 验证、失败与未覆盖项

最终运行node --test scripts/tests/reading-search-browser.test.mjs scripts/tests/reading-sort-browser.test.mjs scripts/tests/reading-duplicate-browser.test.mjs scripts/tests/reading-delete-browser.test.mjs scripts/tests/reading-list-browser.test.mjs，5 passed、0 failed。搜索夹具接受双输入合法页面及旧功能，拒绝无效搜索、未trim、切筛选清除查询、计数仅算可见记录、搜索重置排序、添加清除搜索等6类故障；另核对其他文档和脱离页面输入被拒绝。四个旧套件保留各自原故障拒绝断言。

早期夹具先因缺少“慢读书房”标题在旧初始文字检查失败，补齐手写夹具后通过；新增无效输入负向案例把预期失败混入“全部成功”汇总，又导致一次夹具断言失败，改为独立记录预期拒绝后通过。不是模型产物失败，不放松真实探针。首次健康探测与seed先于独占服务就绪，连接拒绝；当时未创建应用、未发送生成、账本仍429。重新核对监听和ready后执行唯一创建及请求，没有重发模型生成。

初步浏览器检查为486/486，随后发现还需显式覆盖搜索中同名身份，因此补探针并在同一已生成产物上重新加载复核，没有新模型调用或应用源码修改。最终每视口旧功能/删除59项、排序/重名104项、搜索含同名身份115项，合计556项全部通过。搜索中第二本同名书的状态切换、已读交集删除、未读同名保留及清空后的全局记录恢复均有固定检查。网络加载失败0，初始及套件结束未观测横向溢出；查看390px截图，搜索框和按钮保有间距。Reviewer PASS、截图和这些有限探针均不证明任意页面全部交互正确。

搜索身份补验时独占服务已清理，通过日常Spring8123只读取同一隔离应用的静态产物；浏览器操作只改变新页面内存，未操作日常服务进程或用户页面。初步browser-new证据保留，最终写入browser-new-identity及summary-final.json，不覆盖旧轮记录。未覆盖所有中文/大小写/拼音边界、同名同状态的全部显示身份、长内容、键盘/无障碍、持久化、首次生成、真实repair、失败/取消、其他页面/类型、公网部署及OSS。

仅改验收脚本和夹具，无生产Python/Java/前端行为改变，未重跑Python全量、Java干净编译或前端构建。Spring复用target/classes，不称本轮最新Java源码构建通过。新探针无需修改AI服务README；README既有客服修改保留。交付前git diff --check及任务暂存检查通过。

### 45.4 调用、清理、提交与下一轮

累计账本tmp/real-e2e-unlimited-20261111/model-ledger.json为429→445，新增16次（创建2、唯一生成14），max_calls=null、last_status=completed；旧三个有限账本哈希不变。失败启动探测及浏览器补验未调用模型，没有重置或换账本。私有运行器、基线、原模型消息/响应、SSE、台账、PostgreSQL摘要、截图、失败说明和清理记录位于tmp/vue-search-e2e-20261117，不提交；模型凭据未输出或提交。

独占Spring18123、Python18000、代理18001均由本轮监督器启动并清理，exited、stopRequested=true、exitCode1（主动终止），三端口释放。最终复核Spring8123仍为27904，MySQL3306、Redis6379、PostgreSQL5432监听；本轮开始Python8000为18176，最后复核8000未再监听，本轮未操作该进程，不推断外部变化原因。未停止或重启日常服务/容器，原源码基线最终不变。最终浏览器只读取8123静态资源，不新增模型或数据库操作。

提交只含本轮四个旧探针增量、搜索探针/夹具/测试、计划及本节；中文Conventional Commit，不推送，哈希以本节对应提交和交付消息为准。客服README、交接、HTTP故障源码/计划及未跟踪文件，前端三处客服修改全部保留，不混入任务提交。

下一轮推荐先只读分析已有真实repair/失败轨迹：本轮跨场景产物通过，但2次重复读取仍存在，且连续两轮新提示验收均未触发repair。先核对准备结果、预算剩余、完整历史和既有修复收口的触发证据，辨别模型遗漏、上下文可见性与预算耗尽，避免继续成功采样或直接推广生成收口。若据证据提出新生产方案，先说明收益/代价并确认；仍可能在复杂依赖、动态交互、模型违反协议或有限修复额度不足时失败。
