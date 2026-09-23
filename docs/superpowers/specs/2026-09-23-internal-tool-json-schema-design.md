# Spring/Python 内部工具 JSON Schema 契约设计

## 1. 背景

Spring 内部工具网关和 Python LangGraph 已通过 `internal-ai-tools-v1.json` 共享工具名称、历史别名、模型调用权限和模型可提供的参数名，但工具参数类型、必填字段和返回结构仍由 Java `Map<String, Object>` 与 Python `dict[str, Any]` 隐式约定。

这种约定无法在编译期发现跨服务漂移。例如 Spring 将 `built` 从布尔值改为字符串、发布结果重命名 `versionId`，或 Python 拼错文件参数时，两侧代码和各自单元测试仍可能通过，错误只能在工作流运行到构建、修复或发布阶段后暴露。

本轮将现有契约扩展为可执行的 JSON Schema，使 Python 在 HTTP 边界校验请求与成功响应，并让 Java 测试使用同一份契约验证控制器结果。

## 2. 目标

1. 为九个内部工具定义共享的请求参数和成功响应 Schema。
2. Python 在发送 HTTP 前校验参数，在解析 Spring 成功响应后校验工具结果。
3. Spring 生产代码继续保留现有业务校验和 `Map<String, Object>` 返回形式，不增加每次请求的通用 Schema 校验开销。
4. Java 测试使用与 Python 相同的契约验证真实控制器结果，阻止单边字段修改。
5. Schema 错误只暴露稳定、脱敏的错误信息，不泄露源码、路径、响应正文或工具参数值。
6. 保持现有幂等、重试、发布、构建、NDJSON 和公共 SSE 行为不变。

## 3. 非目标

本轮不包含：

- 把 Java 工具返回值重构为 DTO；
- 为生成请求、内部事件、公共 SSE 或错误响应建立完整 Schema；
- 在 Spring 生产请求中运行 JSON Schema 引擎；
- 修改工具名称、别名、模型工具白名单或受控参数规则；
- 修改文件沙箱、受保护文件、解析、校验、构建、发布或幂等语义；
- 为源码或文件内容新增独立长度限制；
- 运行真实 Spring/Python 网络、Redis、模型或端到端生成验收。

## 4. 方案选择

采用扩展现有单一契约文件的方案。契约顶层增加 `schemaDialect`，固定为 `https://json-schema.org/draft/2020-12/schema`；每个工具条目增加 `requestSchema` 和 `responseSchema`。Java 和 Python 读取 `schemaDialect` 后显式选择 Draft 2020-12 校验器。该契约是包含多个嵌入式 Schema 的工具清单，不把整个文件误当成单个 JSON Schema。

不采用每个工具独立文件，因为九个工具会产生大量文件和版本同步点。不采用 Schema 代码生成，因为它会同时改写 Java 控制器、幂等指纹和 Python 类型边界，超出本轮降低协议漂移风险的目标。

契约继续位于：

```text
ai-service/src/ai_service/contracts/internal-ai-tools-v1.json
```

Java 测试从仓库路径读取该文件，Python 从已打包资源读取它。该文件是工具名称、别名、调用权限、请求参数和成功响应结构的唯一事实来源。

## 5. 数据流与运行时边界

```text
Python workflow
  -> 按工具名称或历史别名解析 ToolSpec
  -> 使用 requestSchema 校验 arguments
  -> 组装 appId/requestId/toolCallId/toolName
  -> 调用 Spring /internal/ai-tools/invoke
  -> 解析 BaseResponse 或精确旧版 data envelope
  -> 非零业务码沿用 SpringToolError
  -> 使用 responseSchema 校验成功 data
  -> 返回 LangGraph 工作流
```

`requestSchema` 只描述 `arguments`，不重复描述顶层的 `appId`、`requestId`、`toolCallId` 和 `toolName`。这些字段继续由 `SpringToolGateway.invoke()` 和 Spring `ToolRequest` 负责。

Python 使用 Draft 2020-12 校验器加载并缓存 Schema。Schema 文件自身不合法、工具重名、别名冲突、工具缺少请求或响应 Schema 时，应在契约首次加载时明确失败，不得退化为无校验调用。

Spring 仍是业务规则和文件操作的唯一权威执行端。JSON Schema 不替代目录沙箱、路径穿越防护、受保护文件检查、生成类型边界、构建判断或发布校验。

## 6. 兼容策略

### 6.1 请求严格

所有 `requestSchema` 使用 `additionalProperties: false`。缺少必填字段、字段类型错误或出现未声明字段时，Python 在发送 HTTP 前拒绝调用。

请求 Schema 不设置源码、文件内容或替换文本的最大长度。现有模型预算、上下文限制和 Spring 业务限制仍是唯一长度治理来源，避免产生相互矛盾的第二套限制。

`codeGenType` 使用标准值：

```json
["HTML", "MULTI_FILE", "VUE_PROJECT"]
```

工具的业务类型边界继续由 Spring 执行。例如 `artifact_publish` 即使通过通用字符串与枚举校验，也只能由 Spring 发布 HTML 或 MULTI_FILE。

### 6.2 响应向前兼容

所有 `responseSchema` 允许额外字段。Spring 可以先增加可选结果字段，再部署能够消费它们的 Python 版本；已声明的必填字段及类型不得变化。

历史别名继续映射到规范工具，并复用规范工具的请求与响应 Schema。Python 现有工作流仍发送规范名称；该兼容仅保护已有调用边界，不鼓励新增别名。

精确旧版响应 `{"data": {...}}` 继续兼容，但其中 `data` 必须通过对应工具的 `responseSchema`。带额外顶层字段的旧版 envelope 仍按现有规则拒绝。

## 7. 九个工具契约

### 7.1 请求参数

| 工具 | 必填参数 |
| --- | --- |
| `dir_read` | `relativeDirPath: string`, `codeGenType: enum` |
| `file_read` | `relativeFilePath: string`, `codeGenType: enum` |
| `file_write` | `relativeFilePath: string`, `content: string`, `codeGenType: enum` |
| `file_modify` | `relativeFilePath: string`, `oldContent: string`, `newContent: string`, `codeGenType: enum` |
| `file_delete` | `relativeFilePath: string`, `codeGenType: enum` |
| `artifact_context` | `codeGenType: enum` |
| `artifact_validate` | `artifact: string`, `codeGenType: enum` |
| `artifact_publish` | `artifact: string`, `codeGenType: enum`, `engine: string`, `finishReason: string` |
| `project_build` | `codeGenType: enum` |

模型仍只允许提供现有 `modelArguments`。`appId` 和 `codeGenType` 的模型侧受控规则保持不变：工作流先验证模型参数，再注入 `codeGenType`，最终完整参数由网关 Schema 校验。

### 7.2 成功响应

| 工具 | 必填响应字段 |
| --- | --- |
| `dir_read` | `entries: string[]` |
| `file_read` | `content: string` |
| `file_write` | `ok: boolean`; `path: string` 可选 |
| `file_modify` | `ok: boolean`; `message: string` 可选 |
| `file_delete` | `ok: boolean` |
| `artifact_context` | 按下述多形态规则 |
| `artifact_validate` | `valid: boolean`, `errors: array` |
| `artifact_publish` | `published: boolean`, `versionId: string`, `hashes: object<string,string>` |
| `project_build` | `built: boolean`, `errorCode: string`, `message: string` |

`artifact_context` 使用互斥分支表达现有结果：

- 不存在：`exists=false`、`codeGenType`；
- HTML/MULTI_FILE：`exists=true`、对应 `codeGenType`、`artifact: string`，HTML 允许现有 `entry: string`；
- VUE_PROJECT：`exists=true`、`codeGenType=VUE_PROJECT`、`entries: string[]`、`truncated: boolean`。

`artifact_validate.errors` 中每项至少包含 `code: string` 和 `message: string`，允许可选 `file: string` 以及未来新增字段。

`hashes` 的每个值必须为字符串，但不在本轮强制具体哈希算法或长度，避免把当前实现细节误写成长期协议。

## 8. Python 实现

Python 增加正式依赖 `jsonschema`，并扩展 `ToolSpec`：

```python
@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    aliases: tuple[str, ...]
    model_callable: bool
    model_arguments: tuple[str, ...]
    description: str
    request_schema: dict[str, Any]
    response_schema: dict[str, Any]
```

契约模块提供以下职责明确的接口：

- 加载并缓存全部工具；
- 校验契约自身和名称/别名唯一性；
- 按规范名称或别名解析 `ToolSpec`；
- 校验完整工具参数；
- 校验成功工具结果；
- 保留现有 Vue 模型工具提示和模型参数校验。

`SpringToolGateway.invoke()` 在创建网络请求前校验参数。成功解析 Spring `BaseResponse.data` 或精确旧版 `data` envelope 后，再校验对应结果。校验器不得修改、补默认值或清洗数据；通过后返回原有字典，避免改变幂等指纹和工作流状态。

## 9. 错误处理与安全

新增请求边界异常 `InvalidSpringToolRequest`，使用稳定消息，例如：

```text
INVALID_SPRING_TOOL_REQUEST: Spring tool request did not match expected schema
```

以下情况在发送 HTTP 前抛出该异常：

- 未知工具；
- 缺少必填参数；
- 参数类型错误；
- 出现额外参数；
- `codeGenType` 不是标准值。

成功响应不符合工具 Schema 时继续使用现有 `SpringToolProtocolError`：

```text
SPRING_TOOL_PROTOCOL_ERROR: Spring tool response did not match expected schema
```

异常消息不得包含校验器生成的实例值、JSON 路径中的用户文件名、源码、绝对路径、响应正文、Bearer Token 或 Spring 内部异常。详细校验原因只用于测试断言或受控的内部诊断对象，不写入默认异常文本。

Spring 非零业务码继续转换为 `SpringToolError`，不执行成功结果 Schema 校验。HTTP 状态、网络异常、构建长读取超时和 `artifact_publish` 同 `toolCallId` 有界重试保持现状。请求 Schema 失败不是网络不确定状态，不得触发发布重试。

## 10. Java 测试实现

Java 仅增加 test scope 的 Draft 2020-12 JSON Schema 校验依赖，生产包不增加运行时依赖。

`InternalAiToolContractTest` 扩展为：

- 验证契约声明 Draft 2020-12；
- 验证九个工具均有对象类型的请求与响应 Schema；
- 验证规范名称、别名和 `modelCallable` 与 Java 枚举一致；
- 验证名称与别名无冲突；
- 编译所有 Schema，阻止无效关键字和引用进入仓库。

`InternalAiToolsControllerTest` 使用共享测试辅助器，对各工具已有或新增的真实控制器结果执行对应响应 Schema 校验。文件类工具继续使用临时目录，产物、发布和构建工具继续使用现有 mock 依赖；测试验证控制器实际生成的 Map，而不是维护第二套手写 Java 字段清单。

本轮不接管主工作树中未跟踪的 `InternalAiToolsHttpContractTest.java`。该文件属于用户现有工作，后续如单独提交，可以复用同一测试辅助器，但不是本设计完成条件。

## 11. 测试范围

Python 测试覆盖：

- 九个工具的合法请求和响应；
- 缺少字段、类型错误、额外请求字段在 HTTP 前失败；
- 未知工具失败；
- 历史别名复用规范 Schema；
- 成功响应缺少必填字段或类型错误时产生脱敏协议错误；
- 响应增加未知字段仍通过；
- 非零 Spring 业务响应不受成功结果 Schema 干扰；
- 精确旧版 `data` envelope 仍兼容且执行结果校验；
- 发布重试、构建长超时、Vue 工具白名单和受控参数规则不回归；
- 契约文件自身无效、工具重复、别名冲突或缺少 Schema 时加载失败。

Java 测试覆盖：

- 契约符合 Draft 2020-12 并能编译全部 Schema；
- Java 枚举和共享契约一致；
- 九个控制器工具的真实成功结果符合响应 Schema；
- 请求 Schema 拒绝额外字段，响应 Schema允许未来字段；
- 现有控制器业务校验、路径安全和生成类型测试不回归。

## 12. 文档与版本

契约顶层版本仍为 `1`。本轮是在现有工具集合上补充此前缺失的约束，不改变线上请求或响应形状，因此不升级为版本 2。

`ai-service/README.md` 说明 Python 的边界运行时校验和请求严格/响应兼容策略。交接文档将“缺少共享 Schema”更新为已处理，并明确 Java 生产端仍使用业务校验，真实网络联调尚未执行。

任何后续破坏性契约变更，包括删除字段、改变必填字段类型、禁止现有响应字段或移除历史别名，都必须使用新契约版本或先完成双读兼容，不得直接修改 v1。

## 13. 验证与完成标准

执行：

```powershell
mvn "-Dtest=InternalAiToolContractTest,InternalAiToolsControllerTest" test
mvn clean -DskipTests compile

Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check

Set-Location ..
git diff --check
```

完成标准：

- 九个工具均包含可编译的请求和响应 Schema；
- Python 在请求发送前和成功响应解析后执行对应校验；
- 请求严格拒绝未知字段，响应接受额外字段；
- Java 控制器结果由同一份 Schema 验证；
- 错误输出不包含参数值、源码、路径、令牌或响应正文；
- 现有幂等、发布重试、构建超时、模型工具限制和外部 SSE 行为不变；
- Java/Python 离线验证全部通过；
- 真实网络、Redis、模型和端到端验收明确保留为后续步骤。
