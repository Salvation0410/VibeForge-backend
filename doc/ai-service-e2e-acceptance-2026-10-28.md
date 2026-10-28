# 子图重构端到端验收记录（2026-10-28）

本轮结论：HTML 与 MULTI_FILE 主链路通过，VUE_PROJECT 两次首次生成失败，整体验收未通过。用户明确不做自动恢复，本轮只验收并记录问题，未修改业务代码、模型策略或服务配置。

**后续修复复验（同日 13:21–13:28）**：用户随后授权定位并修复 Vue。已修复重复规划与首次不配图要求的处理，新建 Vue 应用完成首次生成、有限构建修复、二次修改、交互、部署、下载、历史回源以及停止/并发拒绝验收。原始失败证据保留，详细结果见文末“Vue 修复与复验”。

## 环境与方法

- 后端基线：`b7bf508`；Spring 8123、Python 8000、Vue Vite 5173，以及本机 MySQL、Redis、PostgreSQL。
- Python 健康检查 live/ready 正常，ready 报告 checkpoint=true。使用已有本地运行服务，通过前端代理调用真实生成接口，没有以 Fake Model 或模拟 SSE 替换生成。
- 使用用户指定测试账号的隔离 Playwright 会话，通过正常验证码登录；仅创建独立验收应用，不修改已有应用。浏览器使用本机已有 Chromium 1223。
- 同时核对浏览器交互、SSE done/business-error、任务查询、静态资源摘要、部署页面和 ZIP 内容。HTTP 200 本身不作为业务成功依据。
- 临时脚本、截图、JSON 证据与 ZIP 位于被忽略的 `target/e2e-acceptance/`。会话文件含认证信息，不提交、不公开。实际验收约为本地时间 11:43–11:49。

## 结果矩阵

| 项目 | HTML | MULTI_FILE | VUE_PROJECT |
| --- | --- | --- | --- |
| 类型路由 | html | multi_file | vue_project |
| 首次生成 | 通过 | 通过 | 失败 |
| 浏览器交互 | 计数 0→1 | 添加并勾选任务 | 无可用页面 |
| 二次修改 | 主标题变为第二版 | 主标题变为第二版 | 被首次失败阻塞 |
| 修改后自动预览刷新 | 1 次 | 1 次 | 未覆盖 |
| 停止与旧版本保留 | 通过 | 通过 | 未覆盖 |
| 并发请求拒绝与旧预览保留 | 通过 | 通过 | 未覆盖 |
| 部署与部署页交互 | 通过 | 通过 | 未覆盖 |
| 下载 ZIP 内容和 CRC | 通过 | 通过 | 未覆盖 |
| 聊天历史回源 | 完整源码恢复 | 完整源码恢复 | 两次失败记录恢复 |
| Markdown 导出 | 成功 | 成功 | 业务拒绝：尚无生成产物 |

验收应用保留供人工复核：

| 类型 | appId | 最终页面 |
| --- | --- | --- |
| HTML | `472895584616394752` | 验收咖啡馆第二版 |
| MULTI_FILE | `472895590039629824` | 验收任务板第二版 |
| VUE_PROJECT | `472895594644975616` | 无成功版本 |

## 静态成功与心跳证据

| 请求 | requestId | 终态 | 发布状态 | SSE |
| --- | --- | --- | --- | --- |
| HTML 首次 | `7633f66d-ac25-4c6e-a277-f1f330b743d3` | SUCCEEDED | COMMITTED | done=1，business-error=0 |
| HTML 修改 | `26304af4-8621-4aec-9f48-0ee848d3f2bc` | SUCCEEDED | COMMITTED | done=1，business-error=0 |
| 多文件首次 | `35df0f1e-4ec0-407f-909c-20e746d7ccb1` | SUCCEEDED | COMMITTED | done=1，business-error=0 |
| 多文件修改 | `3bef923c-b949-45d8-ac52-e587820d8a5f` | SUCCEEDED | COMMITTED | done=1，business-error=0 |

HTML 首次观测到 RUNNING/LIVE，heartbeatAt 晚于 createdAt，执行期限为最后心跳后 90 秒；终态查询变为 TERMINAL。其他已结束任务也返回 TERMINAL。该结果证明本次运行的心跳观测有效，不证明进程故障后恢复能力。

HTML 两次完整 AI 消息长度分别为 7678、7684，多文件分别为 6169、6175；重新打开页面后 DOM 中完整消息长度与服务端历史一致，未将临时流式窗口当作最终消息。

停止后再次验证预览与部署页：HTML 按钮使计数从 0 变为 1；多文件添加指定任务后可勾选完成。部署地址分别为 `http://127.0.0.1:8123/api/deployed/YT7LYo/`、`http://127.0.0.1:8123/api/deployed/iUKsa4/`。部署返回成功且页面可用，不代表异步截图/OSS 封面上传已验收。

HTML ZIP 包含 index.html、manifest.json；多文件 ZIP 包含 index.html、style.css、script.js、manifest.json。两包 CRC 校验通过，HTML 内容均包含第二版标题。

## 停止及真实并发失败

在已有成功版本的聊天页发起真实修改，另开同应用聊天页提交并发请求，随后点击第一页面“停止生成”。

- HTML 取消请求：`7caca49c-d3e5-42a7-a424-9e361f6cf25f`。
- 多文件取消请求：`6aedeaa9-c53e-4e0b-b64f-fcb050950ca9`。
- 两者任务查询均为 CANCELLED，errorCode=CANCELLED，cancelRequestedAt 非空，heartbeatState=TERMINAL；活动状态最终为 IDLE。
- 两者静态 HTML 的 SHA-256 均与停止前相同；生成中、停止后的 iframe srcdoc 均未改变；停止流程静态 HTML 刷新次数均为 0。
- 第二页面均收到真实 `business-error: GENERATION_IN_PROGRESS`，无 done；失败页面 iframe 保持原内容。
- 取消任务的 publicationState 为 NOT_CONFIRMED；旧活动版本仍可读取与交互，不将此值误解为旧版本被撤销。

本项覆盖运行中取消和入口并发拒绝。没有执行“文件已准备、激活前”故障注入，也未制造 MySQL/Redis 中断或跨进程暂停。

## Vue 阻塞与调查边界

首次需求为简单 Vue3/Vite 任务清单，明确无需图片。首次请求 `3d05322d-b7ae-49f9-bfeb-70a2484a9019` 返回 `VUE_IMAGE_USAGE_MISSING`；任务 FAILED，publicationState=NOT_VERSIONED，heartbeatState=TERMINAL，未产生 done，首次预览 HTML 请求次数为 0。

为排除“无需图片”的约束冲突，随后明确接受并要求使用 imageAssets 配图，要求逐文件调用 file_write。请求 `c03a06d1-c578-4a75-81f6-c66d245ff5a3` 仍以同一错误失败，未得到可构建项目。

源码可确认：`orchestration/nodes/input.py` 对没有既有产物的 Vue 请求主动检索图片；`vue_tool_loop.py` 在存在 imageAssets 却没有成功写入匹配 URL 时，经过两次提醒后抛出该错误。该机制与首次用户“不需要图片”存在冲突。不过接受配图后仍失败，因此不能仅将图片策略当作完整根因；模型响应、工具调用解析及成功写入链路仍需进一步定位。本轮没有修改或绕过图片检查。

最终重新打开 Vue 聊天页能看到两次失败记录，活动状态为 IDLE；页面存在一个空 iframe，没有可用预览。Markdown 导出 HTTP 200，但正文为业务错误 code=50001、“请先完成一次代码生成后再导出”，不得记为导出成功。

## 未覆盖与后续范围

- Vue 成功生成、构建、二次修改、交互、取消后副作用、部署和下载尚未通过。
- 2000 字符流式窗口上限与 80ms 提交间隔没有在本轮作精确浏览器时序测量；仅确认最终历史全文恢复，不把既有单元测试当成本轮端到端证据。
- 未执行模型长度截断、HTML 烟测失败、构建失败、静态提交前故障、数据库/Redis/进程中断矩阵；并发入口拒绝不等价于这些失败场景。
- 未验证部署异步封面上传、生产多实例、长时间磁盘保留或真实 checkpoint 恢复。
- 用户已明确不做自动恢复，不能以此次验收为由继续认领、恢复扫描或 checkpoint 接续实现。

下一步若继续修复，应优先定位 Vue 两次 VUE_IMAGE_USAGE_MISSING 与空预览，再重新执行 Vue 全链路及相关回归。当前不能宣布子图重构全类型端到端验收通过。

## 本轮工作区与验证

本轮只新增验收文档并更新交接说明；未修改 Java、Python 或前端业务源码，因此未重跑源码编译和单元测试。执行了实际浏览器验收脚本、接口核对、ZIP CRC/内容检查以及 `git diff --check`。前端四个既有客服文件修改原样保留，未提交；登录凭据、会话、临时脚本、截图、生成产物和下载包均不纳入提交。

## Vue 修复与复验

### 根因与修复

通过独立的真实模型只读诊断复现：给出首次任务需求、空工具结果和文件进度，三轮均返回相同 filePlan，toolCalls 均为空。模型每次调用只收到当前上下文，没有隐式对话记忆；原提示无条件要求“首次生成先规划”，没有明确区分规划已完成的执行轮次。原循环仅把“清单发生变化”的空工具响应当作规划；相同规划再次出现就落入完成检查，于是因尚未写入图片抛出 VUE_IMAGE_USAGE_MISSING。错误码掩盖了实际没有进入文件写入的问题。

修复在 Python 内部完成：

- vueGeneration.phase 明确区分 plan_files、write_files、modify_files；提示只在空清单的规划阶段要求规划，其余阶段直接处理待写文件或读取既有文件。
- 仍有待写文件时，重复相同规划也继续计入规划上限并给出明确写文件反馈。超过三轮仅规划返回 VUE_FILE_PLAN_LIMIT_EXCEEDED；不将重复规划误当成完成，也不以无限重试消耗模型。
- 首次需求明确“无需图片”“不需要任何配图”“without images”时跳过自动搜图；明确要求新增图片仍可触发。存在 imageAssets 时，原有真实 URL 成功写入检查、两次提醒、工具预算和有限修复上限均保留。

新增五个回归用例先在旧代码上全部失败；修复后验证重复规划可纠正并写文件、持续重复规划返回准确错误、不配图首次生成不触发搜图。Python README 同步更新。没有改 Spring、Vue 前端、公共 SSE 或生产恢复能力。

### 真实复验结果

新应用 `472921285608419328`，使用原始明确“无需图片”的 Vue 任务清单需求，经真实类型路由确认 vue_project。

| 项目 | 证据与结果 |
| --- | --- |
| 首次生成 | requestId `d9b1fd00-80a5-40aa-8fbd-b319f7fc76ad`；SUCCEEDED；done=1、business-error=0；首次预览刷新 1 次 |
| 构建故障与有限修复 | 首次 npm install 在 300 秒上限超时；既有有限修复流程读取文件并锁定依赖版本，下一次安装与构建成功；整个首次请求约 341 秒，未人工代写项目文件或绕过构建 |
| 首次交互 | 真实浏览器新增指定任务并勾选完成通过 |
| 二次修改 | requestId `dde2ca86-f4bd-4375-8bf7-1744d0e258c0`；SUCCEEDED；done=1、business-error=0；主标题变为“验收Vue清单第二版”，自动预览仅刷新 1 次 |
| 修改后交互 | 重新打开聊天页，新增任务及勾选通过 |
| 部署 | `http://127.0.0.1:8123/api/deployed/sWCMeo/`；HTTP 200，真实浏览器显示第二版标题，新增与勾选通过 |
| ZIP | HTTP 200，CRC 校验通过，包含 package.json、package-lock.json、vite.config.js、index.html、src/main.js、src/App.vue、src/style.css、src/components/TaskList.vue；App.vue 含第二版标题，不含 node_modules |
| 历史与导出 | 两次 AI 历史长度 2500/225 字符，重新打开页面后完整恢复，与服务端一致；Markdown 返回实际文本并含原始需求 |
| 心跳 | 运行中 RUNNING/LIVE、终态 TERMINAL；Vue 发布状态保持 NOT_VERSIONED，不能据此声称其具备静态不可变发布保护 |
| 并发失败 | 真实并发请求返回 GENERATION_IN_PROGRESS、无 done，原 iframe 不变 |
| 停止 | requestId `1320ea64-7c8b-4597-ad97-77cfc98a2932`；CANCELLED，cancelRequestedAt 非空，状态最终 IDLE；dist/index.html 摘要与 iframe 均不变，预览刷新 0 次 |

Vue 是脚本加载页面，部署 HTML 壳本身不包含组件标题，不能用 HTML 字符串搜索代替浏览器验收。自动脚本的 deployedTitle 静态字符串结果为 false；后续真实部署页浏览器验证与截图确认标题及交互均正确。

临时复验证据位于 `target/e2e-vue-fix/`，不提交会话、源码产物、截图或下载包。首次安装超时日志仅能证明该次依赖解析/下载未在上限内结束；公开 registry 后续可访问，不能据此断言供应商持续故障。现有修复成功不代表所有网络故障都能恢复。

### 代码验证与限制

- `uv run python -m compileall -q src`、`uv lock --check` 通过。
- 生成链路相关集合（API、Vue 子图、子图边界、父子图、模型适配、提示、工具契约、质量审查）：252 项通过。
- 全量第一次受宿主进程 RAG 开关与运行服务的 reranker 锁影响，出现一项开关预期失败和六项 worker 锁冲突；未将该次计为通过。
- 仅在测试子进程中关闭 RAG 并设置独立 reranker 模型名以隔离锁后，728 项中 716 通过、10 项 opt-in PostgreSQL 测试跳过、2 项断言默认模型名的用例失败。移除测试模型名覆盖后，这两项单独复测通过（2 passed、34 deselected）。分环境累计验证 718 项通过、10 项跳过，不声称单次无配置全量全部通过。没有修改客服代码、测试断言或本机 .env 来消除失败。
- `git diff --check` 通过。仅 Python 业务代码变化，Java 和前端源码未改，未重跑其编译；前端四个既有客服修改保留。

结合本报告此前 HTML/MULTI_FILE 结果，三类生成的主链路已有真实验收证据，Vue 原始阻塞已解除。故障注入、多实例、精确流式刷新间隔、部署异步封面、Vue 在途源码回滚等仍按前述边界未覆盖；本次只证明早期协作式停止保留 dist 预览，不承诺任意时点取消均无源码副作用。生产自动恢复继续不做。
