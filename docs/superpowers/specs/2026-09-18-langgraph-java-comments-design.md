# LangGraph Java 接入代码中文注释设计

## 目标

为提交 `f32c618` 引入且当前仍保留的 LangGraph 生成网关、AI 引擎配置和 Spring 工具边界代码补充完整中文注释。每个显式方法都说明用途，使维护者能够理解请求流转、灰度选择、协议转换、文件沙箱和幂等行为，不改变任何运行逻辑。

## 文件范围

需要补充注释的文件：

- `ai/gateway/AiGenerationGateway.java`
- `ai/gateway/DelegatingAiGenerationGateway.java`
- `ai/gateway/LangGraphAiGenerationGateway.java`
- `ai/gateway/LegacyAiGenerationGateway.java`
- `config/AiEngineConfig.java`
- `config/AiEngineProperties.java`
- `controller/InternalAiToolsController.java`
- `service/impl/AppServiceImpl.java` 中 `chatToGenCode` 与 `createApp` 两个接入方法

提交中曾新增的 `RoutingAiGenerationGateway.java` 后续已删除，不恢复也不注释。`AppServiceImpl` 其他历史方法不在本次范围内。

## 注释规范

- 类和接口使用中文 Javadoc 说明职责、使用方和边界。
- 每个源码中显式声明的方法，包括 private 辅助方法，都使用中文 Javadoc。
- 方法注释包含：用途、每个参数的业务含义、返回值含义；无返回值方法不写 `@return`。
- 对可能抛出或包装异常的方法说明异常条件；不声明实现中不存在的 checked exception。
- 接口方法写完整契约，实现类覆写方法说明具体实现方式，避免只写“实现接口方法”。
- Lombok 自动生成的 getter、setter、构造器以及 record 自动访问器不添加虚构方法注释；`ToolRequest` record 本身补充字段语义说明。
- 灰度哈希、NDJSON 事件转换、幂等缓存、Bearer 常量时间比较和路径沙箱校验可增加少量中文行内注释。
- 注释描述当前行为，不顺带修复、重命名或重构代码。

## 重点说明

### 网关层

解释 `route` 和 `generate` 的输入输出契约、Legacy 与 LangGraph 的差异，以及 `DelegatingAiGenerationGateway` 如何根据固定引擎、白名单和灰度比例选择实现。

### LangGraph HTTP 适配

解释内部 Bearer 请求构造、NDJSON 流读取、事件到旧消息格式的转换和非 2xx 响应处理。说明返回的 `Flux<String>` 用于兼容既有 Spring SSE 处理链。

### 工具边界

解释工具分发、`toolCallId` 幂等、应用目录计算、相对路径规范化、重要文件保护、构建调用和统一错误转换。明确 Python 服务不能直接访问项目文件。

### 应用服务接入

`chatToGenCode` 说明通过统一网关生成并继续复用聊天记录与流处理器；`createApp` 说明通过统一网关路由生成类型并保存应用。

## 验证

- 搜索范围文件中的显式方法，逐一确认其前方存在中文 Javadoc。
- 运行 `mvn clean -DskipTests compile`，确认注释未影响 Java 编译。
- 运行 `git diff --check`，确认无空白错误。
- 审查 Git diff，确认只修改注释，不改变可执行语句、方法签名和配置。

## 非目标

- 不恢复已删除的 `RoutingAiGenerationGateway`。
- 不修改网关选择、HTTP 调用、文件工具、幂等和异常处理逻辑。
- 不为 `AppServiceImpl` 中无关历史方法补写注释。
- 不修改 Python AI 服务。
