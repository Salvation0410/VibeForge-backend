# LangGraph Java 接入代码中文注释实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 LangGraph 生成网关、AI 引擎配置、Spring 工具边界及两个应用服务接入方法补充完整中文 Javadoc，不改变可执行逻辑。

**Architecture:** 注释按网关契约、引擎选择、HTTP 协议适配、工具安全边界和应用服务接入五类职责组织。所有显式方法包含用途、参数、返回值和异常或副作用说明，Lombok 生成方法与 record 自动访问器不虚构注释。

**Tech Stack:** Java 21、Spring Boot、Reactor、Java HttpClient、Jackson、Maven、Javadoc

---

### Task 1: 注释 AI 网关契约与实现

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/ai/gateway/AiGenerationGateway.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/ai/gateway/DelegatingAiGenerationGateway.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/ai/gateway/LangGraphAiGenerationGateway.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/ai/gateway/LegacyAiGenerationGateway.java`

- [ ] **Step 1: 注释接口方法**

为 `route`、`generate` 写明输入上下文、代码类型和流式输出契约。

- [ ] **Step 2: 注释委派网关方法**

为两个覆写方法和 `delegate` 写明固定引擎、用户白名单、稳定灰度桶和 Legacy 默认回退规则。

- [ ] **Step 3: 注释 LangGraph HTTP 网关方法**

覆盖 `route`、`generate`、`buildRequest`、`eventToLegacyMessage`、`ensureSuccess`，说明同步路由、异步 NDJSON、Bearer 请求、旧消息兼容和 HTTP 错误转换。

- [ ] **Step 4: 注释 Legacy 网关方法**

说明类型路由工厂和现有 `AiCodeGeneratorFacade` 的适配关系。

### Task 2: 注释配置与内部工具边界

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/config/AiEngineConfig.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/config/AiEngineProperties.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java`

- [ ] **Step 1: 注释配置类与字段**

说明配置绑定入口、引擎取值、服务地址、共享令牌、白名单和灰度比例。

- [ ] **Step 2: 注释工具入口和分发方法**

覆盖 `invoke`、`execute`，说明 Bearer 校验、`toolCallId` 幂等和工具名分发。

- [ ] **Step 3: 注释所有文件与构建方法**

覆盖写入、修改、删除、校验、构建、读取文件和读取目录方法。

- [ ] **Step 4: 注释所有安全与转换辅助方法**

覆盖 `sandboxPath`、`projectRoot`、`authenticate`、`number`、`text`、`readResult`、`failure`，并为 `ToolRequest` record 字段写说明。

### Task 3: 注释应用服务接入点

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java`

- [ ] **Step 1: 完善 `chatToGenCode` Javadoc**

说明权限校验、聊天记录、统一生成网关和既有流处理器的职责。

- [ ] **Step 2: 完善 `createApp` Javadoc**

说明统一网关路由生成类型、应用初始化和持久化结果。

### Task 4: 覆盖检查、编译和提交

**Files:**
- Verify: 上述 Java 文件

- [ ] **Step 1: 列出显式方法并人工核对 Javadoc**

Run:

```powershell
rg -n "^\s*(public|private|protected|default)\s+.*\(" src/main/java/com/yupi/yuaicodemother/ai/gateway src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java
```

Expected: 每个显式方法前均存在中文 `/** ... */`。

- [ ] **Step 2: 运行干净编译**

Run: `mvn clean -DskipTests compile`

Expected: `BUILD SUCCESS`。

- [ ] **Step 3: 检查差异**

Run: `git diff --check`，并审查 diff 不包含可执行语句变化。

- [ ] **Step 4: 中文提交**

```powershell
git commit -m "docs: 补充 LangGraph Java 接入中文注释" -m "为生成网关、引擎配置、内部工具边界和应用服务接入方法补充完整 Javadoc，说明参数、返回值、安全约束与异常行为。"
```
