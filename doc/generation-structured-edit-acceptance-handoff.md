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
