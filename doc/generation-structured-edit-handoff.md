# 可视化结构化编辑交接

日期：2026-11-11。承接 [保守重写策略交接](generation-rewrite-policy-handoff.md)，用户选择复用已有可视化编辑入口。本轮在两个仓库的当前 dev 工作区实施；未使用真实模型，未修改本机 `.env` 或数据库。

## 1. 本轮结果

已有页面进入可视化编辑后，选中元素，使用独立属性表单设置文字颜色、背景颜色、字号或对齐方式，再点击“应用修改”。表单生成固定中文原文及结构化意图；原文进入现有聊天历史，未发送的自由输入草稿保留。

Spring 与 Python 校验字段和原文一致性，有效意图默认省去一次辅助重写。实际代码生成、输入审核、预算、审查、修复、构建及发布仍执行既有流程。自由输入沿用原有策略；默认仍是 always，不推广上一轮 conservative 启发式。

本轮没有实现源码的确定性 CSS 补丁或视觉自动评审。属性修改仍由生成模型执行，DOM 路径与源码的映射尚无真实产物验收证据。

## 2. 三端契约

外部 GET `/api/apps/chat/gen/code` 保留 `appId/message`，新增可选 query `editIntent`（JSON，最多 2048 字符）。Python `/internal/v1/generations:stream` 接收可选顶层 `editIntent`，不信任 metadata 的同名字段。对象严格只有四个字符串字段：

```json
{"version":"1","selector":"body > main:nth-of-type(1) > button:nth-of-type(2)","property":"color","value":"#12aBcF"}
```

| 字段 | 边界 |
| --- | --- |
| version | 仅字符串 `1` |
| selector | body 起始的 1–12 段直接子级 tag:nth-of-type(n)，最长 512 字符；标签最长 31 字符，序号 1–9999；禁止 html/body/script/style/link/meta 段 |
| property/value | color、backgroundColor：六位 hex；fontSize：1–96 整数加 px，无前导零；textAlign：left/center/right |

canonical 原文如下；label 按属性分别为文字颜色、背景颜色、字号、对齐方式。大小写保持原样，不 trim 或追加自由文本：

```text
请只修改选中元素的{label}为“{value}”。
元素定位：{selector}
保留原有功能、文字、图片和操作方式，其余内容不变。请先确认定位唯一，无法唯一定位时不要修改。
```

共享样本为后端 `ai-service/tests/fixtures/structured-edit-v1.json` 与前端 `tests/fixtures/structured-edit-v1.json`，4 条有效、12 条无效样本。Java 额外拒绝重复 JSON 键、尾随 JSON、未知字段及非字符串。Python 对结构化请求的入口校验错误返回固定 422 响应，不回显请求正文。

## 3. 状态和安全边界

- 前端从真实 DOM 父子路径构造 selector，并验证唯一匹配选中元素。发送前复查 iframe、document、URL、元素连接与当前定位；不信任页面自报的选择内容。
- 页面刷新、退出、生成、应用切换清除旧选择。同文档 hash/pop 导航清除并重绑；pushState 在选择读取时复查 URL，拒绝旧选择后重绑。后续可重新选择元素。
- Spring 维持原所有权校验、任务与租约入口，校验原文后向 LangGraph 顶层传递 intent。无 intent 使用旧调用签名；Legacy 明确拒绝 `STRUCTURED_EDIT_UNSUPPORTED`。
- Python 在 API、input_guard 和 preparation 边界重验。准备节点在缓存复用前检查意图和产物；相同请求复用已有准备结果，不重复重写。
- 必须存在 Spring 返回的产物。Vue 的 exists 仅表示目录，额外要求有界 entries 至少含一份 `.vue/.html/.js/.ts/.jsx/.tsx`。这不证明产物可构建、页面可运行或 DOM 定位存在。
- 服务端校验定位语法，不声称校验真实 DOM/源码唯一性。生成模型仍须定位；不能把字段校验当作修改效果保证。
- 诊断仅增加固定 `STRUCTURED_EDIT` 原因，无新增提示词、selector 或 value 日志字段。公共 SSE、静态候选协议、Vue 流式窗口和预览刷新语义保持。

## 4. 配置与兼容

| 配置 | 有效结构化编辑的准备行为 |
| --- | --- |
| `AI_SERVICE_PROMPT_REWRITE_ENABLED=false` | 总开关优先，disabled |
| 重写开启，`AI_SERVICE_STRUCTURED_EDIT_ENABLED=true`（默认） | skipped/STRUCTURED_EDIT、brief=null、modelAttempted=false |
| 重写开启，`AI_SERVICE_STRUCTURED_EDIT_ENABLED=false` | 恢复辅助重写；仍校验意图/原文和已有产物 |

开关修改后重启 Python，作用于新请求，不重写旧 checkpoint。建议按 Python、Spring、前端顺序更新；旧前端可继续发送普通请求。新前端对旧 Spring 的 canonical 可作为普通需求处理，但不承诺跳过重写。切回 Legacy 时结构化请求会明确失败，用户可使用普通消息框。

回退准备策略只需关闭新配置；回退入口可回退前端本次提交。无需 SQL、依赖或锁文件迁移。Vue 仍沿用原工程修改方式，没有静态不可变版本的同等回滚保证。

## 5. 代码入口

| 范围 | 主要文件 |
| --- | --- |
| Java | `StructuredEditIntent.java`、`AppController`、`AppServiceImpl`、三个 GenerationGateway 接口/实现 |
| Python | `models/structured_edit.py`、API schema/routes、workflow/state、nodes/input.py 和 preparation.py、config.py |
| Vue | `components/StructuredEditForm.vue`、`utils/structuredEdit.ts`、`utils/visualEditor.ts`、`pages/AppChatView.vue`、`api/app.ts` |
| 测试 | Java DTO/Gateway/Controller/Service 测试、Python test_structured_edit.py、前端 structuredEdit.test.ts 和共享 fixture |

## 6. 本轮验证

后端根目录：

```powershell
mvn test '-Dtest=StructuredEditIntentTest,DelegatingAiGenerationGatewayTest,LangGraphAiGenerationGatewayTest,AppControllerSseTest,AppServiceGenerationCancellationTest'
mvn clean -DskipTests compile
```

Java 40 项针对测试与干净编译通过。Python 在 `ai-service` 执行 `uv run python -m compileall -q src`、`uv run pytest`、`uv lock --check` 均通过；最终全量为 **1107 passed、10 skipped、2 条依赖弃用警告**，191.77 秒。专项覆盖三分支、无产物与空 Vue 目录、四字段、原文不一致、metadata 伪造、开关、缓存、取消、输入审核和预算。

前端执行：

```powershell
node --test --experimental-strip-types tests/structuredEdit.test.ts tests/optimizePrompt.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts
npm run build
npx eslint src/api/app.ts src/pages/AppChatView.vue src/utils/visualEditor.ts src/utils/structuredEdit.ts src/components/StructuredEditForm.vue tests/structuredEdit.test.ts
```

22 项测试、类型检查与构建、变更范围 ESLint 通过。Vite 保留社区组件混合导入和大包体积警告。独立规格审查发现的 Vue 空目录问题、质量审查发现的 SPA 导航后无法继续选择问题均已修复并复核。

Chrome 隔离检查使用真实 Vue/Antd 表单：宽屏及 360px 容器显示正常，默认颜色提交得到预期 intent；同源 iframe 的 hash、pushState、返回导航后旧选择无效且可重新选择。截图保存在本机忽略目录 `tmp/structured-edit-desktop.png`、`tmp/structured-edit-mobile.png`，导航结果在 `tmp/structured-edit-navigation.html`。临时检查页面及开发服务器在交付前清理。

真实模型调用 **0 次**。未运行完整应用生成/SSE、三类真实工程构建、视觉对照、部署或真实数据库资源验收；上述隔离检查不能替代端到端验收。前端三份既有客服修改未纳入本轮提交。

## 7. 下一步建议

2026-11-11 已在 60 次硬额度内推进有限真实验收并修复准备诊断落库阻断，新增结构化修改范围规则。实测结果、失败及未覆盖项以 [真实验收交接](generation-structured-edit-acceptance-handoff.md) 为准；上文第 6 节保留本次结构化入口交付时的历史验证记录。

优先在隔离应用上进行真实端到端验收：为 HTML、MULTI_FILE、VUE_PROJECT 各准备已有页面，验证四属性修改、原内容/图片/操作保留、失败和取消后预览行为，并记录实际诊断与耗时。应先明确真实模型调用总额度及隔离应用，再执行；本轮未复用此前六次重写额度。

如果发现 DOM 路径难以映射源码，先记录失败样例再设计带源码定位证据的编辑方案，不直接扩大属性范围或把语法通过等同定位成功。
