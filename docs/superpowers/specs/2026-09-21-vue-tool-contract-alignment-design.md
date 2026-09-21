# Vue 工具契约对齐设计

## 背景

Python `OpenAICompatibleModel` 当前提示 Vue Agent 调用 `search_reference`，测试也把它视为合法工具，但 Spring `InternalAiToolsController` 不支持该名称。真实模型一旦按提示调用，Spring 会返回 `Unsupported tool`，导致 Vue 生成链路失败。

## 目标

- Vue Agent 只能请求 Spring 实际支持的文件工具。
- Python 在请求离开进程前校验工具名和参数，非法调用不得到达 Spring。
- Java 与 Python 通过一份版本化 JSON 契约检测工具名称漂移。
- 保留 Spring 的历史 camelCase 和 snake_case 别名兼容。
- 不改变现有工具调用上限、事件顺序、`toolCallId` 或外部 SSE 协议。

## 工具边界

模型可直接调用的标准工具为：

| 工具 | 模型必填参数 | 工作流注入参数 |
| --- | --- | --- |
| `dir_read` | `relativeDirPath` | `appId`, `codeGenType` |
| `file_read` | `relativeFilePath` | `appId`, `codeGenType` |
| `file_write` | `relativeFilePath`, `content` | `appId`, `codeGenType` |
| `file_modify` | `relativeFilePath`, `oldContent`, `newContent` | `appId`, `codeGenType` |
| `file_delete` | `relativeFilePath` | `appId`, `codeGenType` |

`artifact_validate`、`artifact_publish` 和 `project_build` 是编排内部工具，不向模型开放。`search_reference` 不增加占位实现，因为项目当前没有对应能力。

## 架构

Python 包内新增 `ai_service/contracts/internal-ai-tools-v1.json`，记录 Spring 支持的标准名称、历史别名、模型可调用标记和模型参数，确保现有 wheel 与 Docker 构建都会携带契约。Python 的 `models/tool_contract.py` 加载该契约，负责生成提示和校验模型工具调用。Spring 使用集中式工具枚举完成别名规范化，Java 测试直接读取同一 JSON 文件，Java 与 Python 测试分别断言运行时代码与契约一致。

模型返回工具调用后，工作流先执行本地校验，再注入可信的 `appId` 和 `codeGenType`，最后调用 `SpringToolGateway`。未知工具、内部工具、缺失参数或模型伪造受控参数时，以稳定的 `InvalidVueToolCall` 失败，不产生 Spring HTTP 请求。

## 错误处理

- 未知工具：返回包含工具名的明确错误。
- 内部工具被模型调用：按未授权模型工具拒绝。
- 缺少必填参数：列出缺失字段。
- 模型传入 `appId` 或 `codeGenType`：拒绝，而不是静默覆盖。
- Spring 历史别名继续可用，但模型提示只展示标准名称。

## 测试

- Python 契约测试验证 JSON 加载、提示内容、合法调用和四类非法调用。
- 工作流测试使用 `file_read` 替代不存在的 `search_reference`，并确认调用上限和 ID 不变。
- Java 测试验证所有标准名称和历史别名均可解析，`search_reference` 被拒绝，运行时枚举与 JSON 契约一致。
- 执行 Python 全量测试、Java 定向测试、Java 干净编译、锁文件检查和 `git diff --check`。

## 非目标

本次不实现参考资料搜索、Redis 工具幂等、多实例取消、灰度路由调整或真实三类型端到端验收。
