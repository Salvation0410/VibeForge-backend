# AI 多文件生成截断防护与安全发布方案设计

## 1. 文档状态

- 日期：2026-09-20
- 状态：待评审
- 适用范围：`MULTI_FILE` 生成链路，兼顾 Legacy 与 Python LangGraph 两种引擎
- 关联事故：`doc/ai-multifile-truncation-incident-handoff.md`

本文定义多文件生成产物从模型输出到正式预览目录的完整性校验、版本发布、失败传播和并发控制方案。本文是方案设计，不是实施计划；具体任务拆分、文件级改动顺序和验证命令在本设计确认后另行编写。

## 2. 背景与问题

当前 `MULTI_FILE` 链路要求模型一次输出 `index.html`、`style.css` 和 `script.js`。模型响应达到输出上限时可能在任意文件中部停止，但现有实现仍可能把不完整响应当作成功结果：

1. Legacy 的 `Flux<String>` 只暴露文本分片，丢失模型完成响应中的 `finishReason`。
2. `MultiFileCodeParser` 存在模糊兜底，会从 HTML 资源引用中的 `style.css` 或 `script.js` 开始寻找任意 Markdown 围栏。
3. 保存器只检查三个字段非空，无法区分真实代码与文件名、章节标题或截断片段。
4. 三个文件直接覆盖正式目录，没有候选版本、整体提交或失败回滚。
5. 解析和保存位于 `doOnComplete` 副作用中，异常被捕获后只写日志，不能阻止 SSE `done`。
6. LangGraph 的 `artifact_validate` 目前只检查 artifact 非空，且 HTML/MULTI_FILE 尚未形成安全保存闭环。

事故中的 `style.css` 和 `script.js` 均被覆盖为字符串 `style.css`，证明该问题会破坏已有可用产物，而不只是导致当次响应失败。

## 3. 目标

本方案必须满足以下目标：

1. 截断、缺块、重复区块、错误语言标识或明显无效内容不能进入正式产物。
2. 任一解析、校验、写入或发布步骤失败时，上一成功版本保持可访问。
3. 只有产物完成发布后才发送 SSE `done`；失败必须发送明确的 SSE `error`。
4. Spring 继续作为业务文件的唯一所有者，Python 不直接访问生成目录。
5. Legacy 与 LangGraph 使用同一套 Spring 解析、校验和版本发布能力。
6. 同一应用的生成请求互斥，避免模型上下文、版本提交和聊天记录交叉。
7. 保留最近 3 个成功版本，包含当前版本，支持快速回退和问题审计。
8. 保持现有对外内容分片格式和预览 URL 兼容。

## 4. 非目标

- 不在本次改造中迁移或删除历史 Java LangGraph4j 实验代码。
- 不依赖提高 `max-tokens` 解决正确性问题。
- 不自动拼接被 `LENGTH` 截断的续写内容。
- 不在本阶段把 MULTI_FILE 改造成任意数量、任意名称的项目文件。
- 不改变 Vue 工具调用和构建链路；仅复用通用的错误语义和应用级互斥能力。
- 不要求 Python 直接连接 MySQL、Redis 业务库或项目文件目录。

## 5. 设计原则

### 5.1 候选产物不等于成功产物

模型返回的文本始终是候选产物。候选产物只有完成严格解析、确定性校验和版本发布后，才能成为应用当前版本。模型流正常关闭不代表生成成功。

### 5.2 确定性校验是硬门禁

Markdown 结构、必要文件、资源引用和基础内容完整性由确定性代码判断。LLM 质量审查只能用于视觉或语义改进，不能覆盖硬校验失败，也不能把无效候选提升为成功产物。

### 5.3 发布版本，不覆盖文件

每次成功生成形成不可变版本目录。正式版本切换只更新一个小型指针文件，不依次覆盖三个业务文件。失败候选删除后不会影响当前版本。

### 5.4 完成事件代表已提交

内部 `completed` 和对外 SSE `done` 的统一语义是：候选产物已经通过硬校验，并已成为当前可访问版本。任何更早阶段不得发送完成事件。

## 6. 总体架构

Spring 新增统一的产物管线，Legacy 在进程内调用，LangGraph 通过内部工具网关调用：

```text
Legacy TokenStream                         Python LangGraph
  -> 收集内容与 finishReason                 -> 生成/有限修复
  -> ArtifactPipeline.publish(...)           -> artifact_validate
                                               -> 质量检查
                                               -> artifact_publish
                                                       |
                                                       v
                                      Spring ArtifactPipeline
                                        -> 严格解析
                                        -> 确定性校验
                                        -> 写入不可变 release
                                        -> 原子切换 .current
                                        -> 清理旧版本
                                                       |
                                                       v
                                      SSE done / completed
```

核心边界分为四个职责单一的组件：

| 组件 | 职责 | 不负责 |
| --- | --- | --- |
| `MultiFileArtifactParser` | 按固定协议解析三个闭合文件区块 | 猜测缺失内容、写文件 |
| `MultiFileArtifactValidator` | 执行确定性结构和内容校验 | 调用模型、修改候选内容 |
| `ArtifactPublicationService` | 写入版本目录、切换当前指针、回滚和保留版本 | 解释 Markdown |
| `ArtifactPathResolver` | 为预览、部署、下载、导出和内部工具解析当前版本目录 | 创建或修改版本 |

`ArtifactPipeline` 组合上述组件，对外提供 `validate` 和 `publish` 两类能力。`publish` 必须在服务端再次执行完整解析和校验，不能信任此前的 `validate` 结果，避免校验与发布之间内容变化。

## 7. 多文件协议与解析规则

### 7.1 接受的唯一结构

候选文本去除首尾空白后，必须严格匹配以下结构：

````text
index.html
```html
<完整 HTML>
```

style.css
```css
<完整 CSS>
```

script.js
```javascript
<完整 JavaScript>
```
````

规则如下：

- 文件名必须独占一行，并按 `index.html`、`style.css`、`script.js` 顺序出现。
- 每个文件恰好出现一次。
- 语言标识分别为 `html`、`css`、`javascript`；JavaScript 可兼容现有的 `js`。
- 三个围栏都必须闭合，围栏外只允许空白。
- 文件内容不能为空。
- 不再从章节名、HTML 属性值或任意通用围栏推断文件类型。
- 不再从 HTML 内联 `<style>` 或 `<script>` 自动拆分为多文件。旧兼容能力会掩盖协议错误，与当前系统提示约束冲突。

解析失败返回稳定错误码和错误列表，不返回部分填充的 `MultiFileCodeResult`。

### 7.2 确定性校验

解析成功后执行以下硬校验：

- HTML 包含 `html`、`head`、`body` 的完整开始和结束结构。
- HTML 在 `head` 中引用相对路径 `style.css`。
- HTML 在 `body` 结束前引用相对路径 `script.js`。
- HTML 不包含内联样式块或无 `src` 的业务脚本块。
- CSS 至少包含一个合法规则块，注释和字符串处理后花括号平衡。
- JavaScript 不是文件名、章节标题、Markdown 或自然语言说明，并通过基础词法完整性检查。
- 三个文件均不得包含残留代码围栏。

P0 不引入依赖 Node.js 的强制语法检查，避免静态站点生成依赖外部运行时。若后续引入成熟且可嵌入的 CSS/JavaScript 解析器，可在不改变管线接口的情况下增强校验器。

### 7.3 模型结束原因

模型元数据作为提前失败信号：

- `LENGTH`：判定为截断，禁止解析和发布。
- `CONTENT_FILTER`：判定为未完成，禁止发布。
- `STOP`：允许进入严格解析和校验，但不代表自动通过。
- `null`、`OTHER` 或供应商未知值：记录降级指标，仍必须通过全部严格解析和确定性校验。

结构校验始终保留，因为网络中断、供应商兼容差异或错误元数据都可能使 `finishReason` 不可靠。

## 8. 版本存储与原子发布

### 8.1 目录布局

在保持现有应用根目录名称的前提下，引入内部版本目录：

```text
tmp/code_output/multi_file_<appId>/
  .current
  .releases/
    <requestId-A>/
      index.html
      style.css
      script.js
      manifest.json
    <requestId-B>/
      ...
  .staging/
    <requestId>/
      ...
```

`.current` 仅保存当前 `requestId`。`manifest.json` 保存请求 ID、应用 ID、生成引擎、创建时间、三个文件的 SHA-256、模型结束原因和校验器版本，不保存密钥或完整提示词。

### 8.2 发布流程

1. 在 `.staging/<requestId>` 写入三个文件和 manifest 临时文件。
2. 从磁盘重新读取候选文件并核对 SHA-256，确认写入内容与已校验内容一致。
3. 将 staging 目录移动为 `.releases/<requestId>`。版本目录一旦生成便不可修改。
4. 在相同目录创建 `.current.tmp-<requestId>`，写入新版本 ID 并强制刷新文件内容。
5. 使用同文件系统的原子移动替换 `.current`。
6. 指针切换成功后才返回发布成功。
7. 异步清理非当前旧版本，最终只保留最近 3 个成功版本；清理失败只记录告警，不回滚成功发布。

如果底层文件系统不支持原子替换 `.current`，发布必须失败并保留旧指针，不能退化为先删除旧指针再写新指针。

### 8.3 兼容现有平铺目录

`ArtifactPathResolver` 采用以下读取规则：

1. `.current` 存在且指向完整 release 时，返回该 release。
2. `.current` 不存在时，回退到现有平铺目录，保证升级前应用仍可预览、部署和下载。
3. `.current` 损坏或指向不存在版本时，记录高优先级告警并回退到最近一个 manifest 完整且校验通过的 release；没有可用 release 时才回退平铺目录。

首次成功发布前，现有平铺文件如果通过新校验，则归档为一个 legacy release；如果无法通过校验，则复制到隔离归档区用于人工排查，但不计入最近 3 个成功版本，也不能自动回滚为当前版本。

预览、部署、下载、聊天记录源码导出和内部文件读取必须统一通过 `ArtifactPathResolver` 获取活动目录，禁止各自拼接 `multi_file_<appId>` 后直接读取文件。

## 9. 并发与幂等

### 9.1 应用级生成租约

生成开始前获取以 `appId` 为维度的 Redis 租约，租约值为 `requestId`：

- 同一应用已有生成任务时，新请求立即返回 `GENERATION_IN_PROGRESS`，不进入排队。
- 不同应用可以并行生成。
- 租约带有限期并在请求存活期间续期，避免服务崩溃后永久锁定。
- 释放租约时使用“值仍等于当前 requestId 才删除”的比较删除，禁止旧请求释放新请求的租约。
- SSE 取消、模型错误、校验失败和发布异常都必须在终止路径释放租约。

锁覆盖从模型调用开始到发布或失败结束的完整生命周期，而不是只覆盖 `.current` 切换。这同时保护聊天上下文、模型记忆和产物顺序。

### 9.2 发布幂等

`requestId` 同时作为版本 ID 和发布幂等键：

- 同一 `requestId`、相同文件哈希重复发布时返回原成功结果。
- 同一 `requestId`、不同文件哈希时返回 `ARTIFACT_VERSION_CONFLICT`。
- 幂等判断以磁盘 manifest 为准，不依赖当前进程内 `ConcurrentHashMap`，因此服务重启后仍成立。

## 10. Legacy 链路调整

### 10.1 保留完成元数据

HTML/MULTI_FILE 的 LangChain4j AI Service 流式方法改为能够接收完整 `ChatResponse` 的 `TokenStream`，不再以 `Flux<String>` 作为模型边界。适配层继续向前端实时发送文本分片，同时在完成回调中取得 `finishReason` 和 token usage。

### 10.2 将发布纳入主响应链

Legacy 的终止顺序固定为：

1. 模型完成回调返回。
2. 检查 `finishReason`。
3. 调用 `ArtifactPipeline.publish`。
4. 发布成功后完成 Reactor 流。
5. 任一步失败时调用 `sink.error`，不得捕获后继续 `sink.complete`。

现有 `doOnComplete` 中解析、保存并吞异常的逻辑删除。日志不得输出完整生成代码，只记录 `requestId`、`appId`、引擎、字符数、token usage、结束原因、校验错误码和版本 ID，避免日志体积和潜在敏感内容泄露。

### 10.3 聊天历史与模型记忆

- 只有版本发布成功后，完整 AI 响应才写入聊天历史。
- 发布失败时不把部分响应或“AI 回复失败”伪装为正常 AI 内容写入模型上下文。
- 用户消息可以保留为一次失败尝试，但失败响应只进入业务日志和 SSE 错误。
- 如果 LangChain4j 在候选验证前已把响应写入 Redis chat memory，失败终止路径必须从持久化成功历史重建该应用的模型记忆，移除未发布响应。

## 11. LangGraph 链路调整

Python 继续负责编排，Spring 负责文件校验和发布。

### 11.1 模型元数据

`ModelTurn` 增加规范化结束原因和 token usage。OpenAI 兼容适配器从模型响应元数据中提取结束原因，生成和 repair 两类调用均需检查；截断的 repair 结果同样不能进入下一节点。

### 11.2 工作流顺序

MULTI_FILE 分支调整为：

```text
generate_multi_file
  -> finish_reason_guard
  -> artifact_validate（Spring）
       -> 无效：repair
       -> 有效：quality_review
                    -> 不通过：repair
  -> repair（最多 2 次，每次重新检查结束原因和硬校验）
  -> artifact_publish（Spring）
  -> completed
```

MULTI_FILE 不进入 `project_build`。硬校验失败时跳过 `quality_review`，把结构化错误直接交给 repair；硬校验通过但质量审查不通过时，也可以进入 repair。达到修复上限后仍有任一门禁不通过时发送 `failed`，不得调用发布，也不得发送带 `qualityPassed=false` 的 `completed`。

新增内部工具 `artifact_publish`。请求至少包含 `appId`、`codeGenType`、`requestId`、`artifact`、`engine` 和 `finishReason`；Spring 必须重新校验 artifact 后再发布。响应包含 `published`、`versionId` 和三个文件哈希摘要。

`artifact_validate` 返回结构化结果：

```json
{
  "valid": false,
  "errors": [
    {
      "code": "MULTI_FILE_UNCLOSED_FENCE",
      "file": "style.css",
      "message": "style.css code fence is not closed"
    }
  ]
}
```

Python 可以把这些错误用于有限修复，但不能改变 Spring 的最终判定。

## 12. SSE 与前端语义

现有普通内容事件继续使用：

```json
{"d":"模型生成的文本分片"}
```

成功时，仅在发布完成后发送现有命名事件 `done`。失败时发送命名事件 `error`：

```json
{
  "error": true,
  "code": "ARTIFACT_VALIDATION_FAILED",
  "message": "生成结果不完整，已保留上一版本，请重试",
  "requestId": "..."
}
```

控制器必须将终止异常转换为一个 `error` 事件并结束连接，不能再拼接 `done`。前端行为如下：

- `done`：重新加载应用信息、聊天历史和预览，然后提示成功。
- `error`：停止 loading，保留旧预览，不执行预览刷新，展示后端业务消息。
- 网络错误：保持现有通用中断提示。
- 前端不得根据“收到过内容分片”推断成功。

推荐的稳定错误码包括：

| 错误码 | 含义 |
| --- | --- |
| `GENERATION_IN_PROGRESS` | 同一应用已有生成任务 |
| `MODEL_OUTPUT_TRUNCATED` | 模型因长度限制结束 |
| `MODEL_OUTPUT_BLOCKED` | 模型因内容过滤结束 |
| `MULTI_FILE_FORMAT_INVALID` | 三文件 Markdown 协议不合法 |
| `ARTIFACT_VALIDATION_FAILED` | 文件内容确定性校验失败 |
| `ARTIFACT_PUBLISH_FAILED` | 候选写入或当前指针切换失败 |
| `ARTIFACT_VERSION_CONFLICT` | 同一 requestId 对应不同内容 |

错误消息面向用户时不暴露本地路径、堆栈、模型供应商原始响应或内部令牌。

## 13. 可观测性

每次生成使用同一个 `requestId` 贯穿 Spring、Python、Redis 租约、版本目录和日志。记录以下结构化字段：

- `requestId`、`appId`、`userId`、`engine`、`codeGenType`
- 输出字符数、token usage、finish reason
- 解析结果、校验错误码、repair 次数
- 发布版本 ID、文件哈希、总耗时和各阶段耗时
- 是否回退到旧版本、是否发生租约冲突

建议指标：

- `ai_generation_total{engine,type,result}`
- `ai_generation_truncated_total{engine}`
- `artifact_validation_failed_total{code}`
- `artifact_publish_total{result}`
- `artifact_publish_duration_seconds`
- `generation_lease_conflict_total`

不得在生产日志或指标标签中写入完整提示词、完整代码、API Key 或 Bearer Token。

## 14. 测试设计

### 14.1 解析器与校验器单元测试

- 标准三文件响应正确解析。
- CSS 或 JavaScript 围栏缺少结束标记时失败。
- 围栏数为奇数、文件缺失、重复、乱序或语言标识错误时失败。
- HTML 中存在 `style.css`、`script.js` 引用时不会影响章节定位。
- 文件标题存在但代码块为空时失败。
- 围栏外存在解释文字时失败。
- 文件正文等于文件名、包含 Markdown 残留或基础结构不完整时失败。
- 使用事故响应的最小化样例验证不会返回 `style.css` 作为 CSS 或 JavaScript。

### 14.2 版本发布测试

- 首次发布建立 release 和 `.current`。
- 成功发布后所有读取方解析到同一版本。
- staging 写入失败时当前版本不变。
- manifest 校验失败时当前版本不变。
- 指针替换失败时旧 `.current` 保持有效。
- 相同 requestId 和哈希重复提交具有幂等性。
- 相同 requestId、不同哈希返回冲突。
- 只保留包含当前版本在内的最近 3 个成功版本。
- 损坏 `.current` 时按规则回退，并产生告警。
- 旧平铺目录在首次新版本发布前仍可读取。

### 14.3 并发测试

- 同一应用第二个生成请求立即收到 `GENERATION_IN_PROGRESS`。
- 不同应用能够并行生成。
- 请求成功、失败、取消和客户端断连后租约都能释放。
- 过期旧请求不能释放新请求持有的租约。

### 14.4 Legacy 集成测试

- `STOP` 且产物有效时发布并最终发送 `done`。
- `LENGTH` 时不调用发布，发送 `error`，旧版本不变。
- 解析、校验和发布异常均通过 Reactor 错误信号传播。
- 失败响应不写入成功聊天历史，Redis 模型记忆不保留失败候选。

### 14.5 LangGraph 契约测试

- MULTI_FILE 不调用 `project_build`。
- `artifact_validate` 失败后进入有限 repair。
- repair 响应截断时不能继续发布。
- 两次 repair 后仍失败时最后事件为 `failed`。
- 只有 `artifact_publish.published=true` 后才能发送 `completed`。
- `artifact_publish` 请求和响应 schema 与 Spring 保持一致。

### 14.6 前端测试

- SSE `error` 不触发成功提示或预览刷新。
- SSE `done` 才触发应用、历史和预览刷新。
- 失败时继续显示上一成功版本。
- `GENERATION_IN_PROGRESS` 展示明确提示并恢复输入状态。

## 15. 发布与回滚策略

改造按以下顺序上线：

1. 先上线 Spring 解析器、校验器、版本存储、路径解析器和 Legacy 终态修复。
2. 完成事故样例、发布失败和 SSE 测试后，继续保持 `AI_ENGINE=legacy` 观察指标。
3. 再上线 Python finish reason、严格校验、发布工具和工作流终态调整。
4. LangGraph 契约测试和联调通过后，先对白名单启用，再逐步提高灰度比例。

代码回滚时不得删除 `.releases`。旧代码不能识别 `.current`，因此正式切换前需要保留或生成兼容的平铺当前版本；实施计划必须包含兼容同步或明确的回滚脚本。数据回滚优先通过切换 `.current` 到上一成功版本完成，不直接修改 release 内文件。

## 16. P2 后续演进

核心防护稳定后，再评估将一次性大文本生成改为文件级生成或候选工作区工具调用：

- 每个文件单独生成和校验，降低单次输出达到 token 上限的概率。
- 修改请求优先只生成受影响文件，但提交前仍构造并校验完整三文件版本。
- 工具写入目标只能是 requestId 对应的 staging 工作区，不能写活动 release。
- 多文件一致性检查通过后才切换 `.current`。

该演进会增加模型调用次数、上下文组织和跨文件一致性复杂度，不纳入本轮 P0/P1 实施计划。

## 17. 验收标准

设计落地后必须同时满足：

1. 本次事故响应和任意缺失闭合围栏的响应均被明确拒绝。
2. 被拒绝的生成不会改变 `.current` 或任何成功 release。
3. `style.css`、`script.js` 不可能由文件名、标题或说明文字生成。
4. Legacy 能记录并处理模型结束原因，`LENGTH` 不发布产物。
5. LangGraph 只有在 Spring 返回发布成功后才发送 `completed`。
6. 对外只在发布成功后发送 SSE `done`，所有终止失败都发送可识别的 `error`。
7. 前端失败时保留上一预览，不显示“生成完成”。
8. 同一应用不能并发生成，不同应用不受影响。
9. 当前版本和最近两个历史成功版本可读取，失败候选不计入版本保留数量。
10. 预览、部署、下载和导出读取同一个活动版本。
11. Java 相关测试和干净编译通过；前端类型检查与测试通过；涉及 Python 时 `compileall`、`pytest` 和 `uv lock --check` 通过。
