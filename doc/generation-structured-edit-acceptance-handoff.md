# 结构化编辑真实验收交接

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
