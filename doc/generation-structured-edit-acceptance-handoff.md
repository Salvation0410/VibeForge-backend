# 结构化编辑真实验收交接

日期：2026-11-11。承接 [可视化结构化编辑交接](generation-structured-edit-handoff.md)。本文记录本轮本地真实模型与浏览器验收，不能将上一轮离线验证结果视为本轮重新执行。

> 本轮最终账本为 **60/60 次**，剩余 0 次；已停止真实模型验收，不能追加调用或另开账本绕过授权。

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

最终调用为 **60/60**，最后样例已记录于第 4.3 节。后端代码、测试及本文同批提交，提交说明为 `fix(ai): 修复准备诊断落库并补充结构化编辑验收`；可用 `git log -1 --format='%h %s' -- doc/generation-structured-edit-acceptance-handoff.md` 获取提交哈希。无部署或推送。
