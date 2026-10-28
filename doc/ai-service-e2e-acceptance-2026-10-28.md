# 子图重构端到端验收记录（2026-10-28）

本轮结论：HTML 与 MULTI_FILE 主链路通过，VUE_PROJECT 两次首次生成失败，整体验收未通过。用户明确不做自动恢复，本轮只验收并记录问题，未修改业务代码、模型策略或服务配置。

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
