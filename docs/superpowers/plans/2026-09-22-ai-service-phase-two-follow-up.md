# AI Service Phase Two Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已完成 Python 提示词迁移的基础上，为二次修改提供可信的活动产物上下文，统一 Vue 生成与修复工具循环，将构建失败纳入确定性修复状态机，并在真实环境验收后再提高 LangGraph 灰度比例。

**Architecture:** Spring 继续拥有应用目录、活动版本、构建和发布；新增仅工作流可调用的 `artifact_context` 工具，以结构化且有界的方式把当前活动产物提供给 Python。Python 将 Vue 初次生成和修复复用同一套受限工具循环，并依据 Spring 返回的结构化构建结果决定修复或失败。Legacy 路径全程保留，灰度选择改为稳定用户桶，最终通过真实 HTTP、Redis 和三类型端到端验收门。

**Tech Stack:** Java 21、Spring Boot 3.5、MyBatis-Flex、Redisson、Python 3.12、FastAPI、LangChain、LangGraph、pytest、JUnit 5、MockMvc。

---

## 阶段边界

- 阶段 1-4 是代码实现，每阶段独立测试并提交。
- 阶段 5 是差异对比与稳定灰度，只在前三类行为测试通过后实施。
- 阶段 6 依赖本机或集成环境的 Spring、Python、Redis、MySQL、Chrome 和真实模型；任何依赖不可用时记录命令和原因，不伪造通过结果。
- 不删除 Legacy LangChain4j、Java 工具执行代码或历史 LangGraph4j 实验目录。
- 不让 Python 访问 MySQL、挂载项目目录或绕过 Spring 工具网关。

### Task 1: 增加有界活动产物上下文工具

**Files:**
- Modify: `ai-service/src/ai_service/contracts/internal-ai-tools-v1.json`
- Modify: `src/main/java/com/yupi/yuaicodemother/ai/gateway/InternalAiTool.java`
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactContextReader.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java`
- Create: `src/test/java/com/yupi/yuaicodemother/core/artifact/ArtifactContextReaderTest.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java`
- Modify: `ai-service/tests/test_tool_contract.py`

- [x] **Step 1: 写失败测试，锁定上下文形状和安全边界**

Java 测试使用 `@TempDir` 创建 HTML、MULTI_FILE 和 Vue 活动目录，断言：

```java
assertEquals(Map.of("exists", false, "codeGenType", "HTML"),
        reader.read(CodeGenTypeEnum.HTML, 42L));

Map<String, Object> html = reader.read(CodeGenTypeEnum.HTML, 42L);
assertEquals(true, html.get("exists"));
assertEquals("index.html", html.get("entry"));
assertTrue(((String) html.get("artifact")).startsWith("```html"));
```

MULTI_FILE 必须按 `index.html`、`style.css`、`script.js` 重建现有三代码块协议；Vue 只返回排序后的项目相对文件列表和 `exists`，不把整套源码塞进上下文。`.releases`、`.current`、`dist`、`node_modules` 和隐藏提交元数据不得出现在 Vue 列表中。

- [x] **Step 2: 运行测试并确认失败**

Run: `.\mvnw.cmd test -Dtest=ArtifactContextReaderTest,InternalAiToolsControllerTest`

Expected: `ArtifactContextReader` 和 `ARTIFACT_CONTEXT` 尚不存在，测试编译失败。

- [x] **Step 3: 实现只读上下文工具**

在唯一的版本化 JSON 契约中加入非模型工具；Java 的 `InternalAiToolContractTest` 继续直接读取该文件并与枚举比对：

```json
{
  "name": "artifact_context",
  "aliases": [],
  "modelCallable": false,
  "modelArguments": [],
  "description": "Read the current active artifact context inside the workflow."
}
```

`ArtifactContextReader` 通过 `ArtifactPathResolver.resolveActiveRoot()` 读取活动版本；静态产物最多返回 100000 字符，超过时抛出稳定错误 `ARTIFACT_CONTEXT_TOO_LARGE`，禁止静默截断。Vue 文件列表最多 200 项，超过时返回 `truncated=true`，模型仍需通过 `dir_read`/`file_read` 选择性读取源码。

控制器新增：

```java
case ARTIFACT_CONTEXT -> artifactContextReader.read(artifactType(args), appId);
```

该工具只接受工作流注入的 `codeGenType`，不加入模型可调用白名单。

- [x] **Step 4: 运行跨语言契约与控制器测试**

Run: `.\mvnw.cmd test -Dtest=ArtifactContextReaderTest,InternalAiToolsControllerTest,InternalAiToolContractTest`

Run: `Set-Location ai-service; uv run pytest tests/test_tool_contract.py -q`

Expected: Java 和 Python 都识别 `artifact_context`，但 `vue_model_tool_specs()` 不包含它。

- [x] **Step 5: 提交阶段 1**

```powershell
git add -- ai-service/src/ai_service/contracts/internal-ai-tools-v1.json src/main/java/com/yupi/yuaicodemother/ai/gateway/InternalAiTool.java src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactContextReader.java src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java src/test/java/com/yupi/yuaicodemother/core/artifact/ArtifactContextReaderTest.java src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java ai-service/tests/test_tool_contract.py
git commit -m "feat: 提供活动产物上下文工具"
```

### Task 2: 在生成前注入活动产物上下文

**Files:**
- Modify: `ai-service/src/ai_service/orchestration/workflow.py`
- Modify: `ai-service/tests/conftest.py`
- Modify: `ai-service/tests/test_api.py`
- Modify: `ai-service/src/ai_service/prompts/generation.py`
- Modify: `ai-service/tests/test_prompts.py`

- [x] **Step 1: 写失败测试，区分首次生成和二次修改**

为 `FakeToolGateway` 增加可配置的 `artifact_context` 返回值，断言 `context_prepare` 在调用模型前执行一次：

```python
context_calls = [call for call in gateway.calls if call["name"] == "artifact_context"]
assert len(context_calls) == 1
assert context_calls[0]["toolCallId"] == "req-1:artifact_context"
generate_context = next(data for name, data in model.calls if name == "generate")["context"]
assert generate_context["currentArtifact"]["exists"] is True
```

另加首次生成用例，Spring 返回 `exists=false` 时模型仍正常生成；工具读取失败必须产生 `failed` 终态，不得假装没有旧版本。

- [x] **Step 2: 运行定向测试并确认失败**

Run: `Set-Location ai-service; uv run pytest tests/test_api.py -k "artifact_context or existing_artifact" -q`

Expected: 当前 `context_prepare` 不调用 Spring，新增断言失败。

- [x] **Step 3: 在 `context_prepare` 调用工作流工具**

使用稳定调用 ID：

```python
result = await self._invoke_tool(
    emitter,
    "context_prepare",
    "artifact_context",
    {"codeGenType": state["code_gen_type"]},
    state["app_id"],
    state["request_id"],
    f"{state['request_id']}:artifact_context",
)
```

把结果写入 `context["currentArtifact"]`。不把聊天数据库、文件绝对路径或用户密钥写入上下文、事件或 checkpoint。

- [x] **Step 4: 更新三类生成提示词的上下文规则**

HTML/MULTI_FILE 明确：`currentArtifact.exists=true` 时基于完整活动产物修改并保留未指定内容；`false` 时视为首次生成。Vue 明确：文件列表只用于判断项目现状，修改源码前仍必须调用 `file_read`。

- [x] **Step 5: 运行 Python 完整测试并提交**

Run: `Set-Location ai-service; uv run pytest`

Expected: 全部通过，且原有事件顺序断言已更新为包含 `context_prepare` 内部工具事件。

```powershell
git add -- ai-service/src/ai_service/orchestration/workflow.py ai-service/tests/conftest.py ai-service/tests/test_api.py ai-service/src/ai_service/prompts/generation.py ai-service/tests/test_prompts.py
git commit -m "feat: 注入当前活动产物上下文"
```

### Task 3: 统一 Vue 初次生成与修复工具循环

**Files:**
- Modify: `ai-service/src/ai_service/models/openai_compatible.py`
- Modify: `ai-service/src/ai_service/orchestration/workflow.py`
- Modify: `ai-service/tests/test_openai_compatible.py`
- Modify: `ai-service/tests/test_api.py`
- Modify: `ai-service/tests/conftest.py`

- [ ] **Step 1: 写失败测试，证明 Vue 修复必须真实执行文件工具**

构造第一次构建失败、修复模型返回 `file_read` 后再返回 `file_modify` 的场景，断言：

```python
repair_calls = [call for call in gateway.calls if call["toolCallId"].startswith("req-1:vue-repair:1:")]
assert [call["name"] for call in repair_calls] == ["file_read", "file_modify"]
assert all(call["arguments"]["codeGenType"] == "VUE_PROJECT" for call in repair_calls)
```

同时覆盖非法工具、模型提供受控参数、工具上限耗尽和取消；任何失败都不得绕过 `validate_vue_tool_call()` 或 Spring 网关。

- [ ] **Step 2: 让 `repair()` 解析 Vue JSON 工具调用**

从 `generate()` 提取共享解析器：

```python
def _vue_model_turn(response: Any) -> ModelTurn:
    raw = str(response.content)
    try:
        payload = json.loads(_strip_json_fence(raw))
        calls = [ToolCall(name=item["name"], arguments=item.get("arguments", {}))
                 for item in payload.get("toolCalls", [])]
        return _model_turn(response, payload.get("content", ""), calls)
    except (json.JSONDecodeError, KeyError, TypeError):
        return _model_turn(response, raw)
```

当修复上下文的 `codeGenType` 为 `VUE_PROJECT` 时，`repair()` 使用该解析器；静态产物修复行为保持原样。

- [ ] **Step 3: 提取受限 Vue 工具循环**

在 `GenerationWorkflow` 内提取 `_run_vue_tool_loop(...)` 或等价私有方法，由初次生成传入 `model.generate`，由修复传入 `model.repair`。工具调用 ID 分别使用：

```text
<requestId>:vue-generate:<ordinal>
<requestId>:vue-repair:<repairCount>:<ordinal>
```

总工具预算仍由 `AI_SERVICE_VUE_MAX_TOOL_CALLS` 控制；每轮上下文追加结构化 `toolResults`，每次调用前检查取消状态。

- [ ] **Step 4: 修复完成后重新进入确定性校验和构建**

Vue 修复节点不把简短完成文案当作源码产物。状态中保留工具执行摘要，随后回到 `artifact_validation -> project_build`；HTML/MULTI_FILE 仍使用完整文本候选。

- [ ] **Step 5: 运行测试并提交**

Run: `Set-Location ai-service; uv run pytest tests/test_openai_compatible.py tests/test_api.py -q`

Expected: Vue 初次生成、修复、非法工具、工具上限和取消测试全部通过。

```powershell
git add -- ai-service/src/ai_service/models/openai_compatible.py ai-service/src/ai_service/orchestration/workflow.py ai-service/tests/test_openai_compatible.py ai-service/tests/test_api.py ai-service/tests/conftest.py
git commit -m "refactor: 统一 Vue 生成与修复工具循环"
```

### Task 4: 将 Vue 构建失败纳入确定性修复状态机

**Files:**
- Create: `src/main/java/com/yupi/yuaicodemother/core/builder/VueBuildResult.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/core/builder/VueProjectBuilder.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java`
- Create: `src/test/java/com/yupi/yuaicodemother/core/builder/VueProjectBuilderTest.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java`
- Modify: `ai-service/src/ai_service/orchestration/workflow.py`
- Modify: `ai-service/tests/test_api.py`

- [ ] **Step 1: 写 Java 失败测试，要求结构化且有界的构建结果**

引入结果类型：

```java
public record VueBuildResult(boolean built, String errorCode, String message) {
    public static VueBuildResult success() { return new VueBuildResult(true, "", ""); }
    public static VueBuildResult failure(String code, String message) {
        return new VueBuildResult(false, code, message);
    }
}
```

测试缺少 `package.json`、`npm install` 失败、`npm run build` 失败和缺少 `dist/index.html` 的稳定错误码。`message` 最多 4000 字符，不包含绝对项目根路径或环境变量。

- [ ] **Step 2: 保留旧布尔 API并增加详细构建 API**

`ensureProjectBuilt()` 和 `buildProject()` 继续返回 boolean 供现有调用者使用；新增 `ensureProjectBuiltDetailed()` 返回 `VueBuildResult`。控制器的 `project_build` 返回：

```json
{"built": false, "errorCode": "VUE_BUILD_FAILED", "message": "npm run build failed: ..."}
```

成功时返回 `{"built": true, "errorCode": "", "message": ""}`，不再向 Python暴露绝对路径。

- [ ] **Step 3: 写 Python 失败测试，阻止 `built=false` 完成**

增加路由函数：

```python
def _after_build(state: WorkflowState, max_attempts: int) -> str:
    if state.get("build", {}).get("built", False):
        return "review"
    return "fail" if state.get("repair_count", 0) >= max_attempts else "repair"
```

测试第一次失败后执行修复并重新构建；连续失败达到两次修复上限后只发送 `failed`，不得发送 `completed`。

- [ ] **Step 4: 接入 LangGraph 条件边**

将固定的 `project_build -> quality_review` 改为 `_after_build` 条件边。修复上下文必须包含 Spring 返回的 `errorCode` 和 `message`；质量检查只在构建成功后运行。

- [ ] **Step 5: 运行 Java/Python 验证并提交**

Run: `.\mvnw.cmd test -Dtest=VueProjectBuilderTest,InternalAiToolsControllerTest`

Run: `Set-Location ai-service; uv run pytest tests/test_api.py -q`

Expected: 构建失败修复、修复上限和成功路径全部通过。

```powershell
git add -- src/main/java/com/yupi/yuaicodemother/core/builder/VueBuildResult.java src/main/java/com/yupi/yuaicodemother/core/builder/VueProjectBuilder.java src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java src/test/java/com/yupi/yuaicodemother/core/builder/VueProjectBuilderTest.java src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java ai-service/src/ai_service/orchestration/workflow.py ai-service/tests/test_api.py
git commit -m "fix: 阻止 Vue 构建失败进入完成终态"
```

### Task 5: 建立 Legacy/LangGraph 对比基线并修正稳定灰度

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/ai/gateway/DelegatingAiGenerationGateway.java`
- Create: `src/test/java/com/yupi/yuaicodemother/ai/gateway/DelegatingAiGenerationGatewayTest.java`
- Create: `scripts/compare-ai-generation-engines.ps1`
- Modify: `doc/ai-service-startup.md`

- [ ] **Step 1: 写失败测试，证明同一用户稳定落桶**

测试相同 `userId` 在不同 `requestId` 下选择同一引擎、不同 `graySalt` 可以改变桶、白名单始终进入 LangGraph、空用户使用 `appId/requestId` 的确定性回退键。

- [ ] **Step 2: 使用稳定业务键和盐计算灰度桶**

将当前 `Objects.hash(userId, requestId)` 改为 SHA-256 稳定桶：

```java
String subject = userId == null ? "request:" + requestId : "user:" + userId;
byte[] digest = MessageDigest.getInstance("SHA-256")
        .digest((properties.getGraySalt() + ":" + subject).getBytes(StandardCharsets.UTF_8));
int bucket = ByteBuffer.wrap(digest, 0, 4).getInt() & Integer.MAX_VALUE;
return bucket % 100;
```

不得使用 Java 进程随机状态；`cancel()` 必须能用同一业务键选回生成时的引擎。

- [ ] **Step 3: 增加可重复的双引擎对比脚本**

脚本接收两个已存在的测试应用 ID 和三类固定提示词，分别以 `AI_ENGINE=legacy` 与 `AI_ENGINE=langgraph` 执行，不自动修改生产配置。输出 JSON 报告字段固定为：`engine`、`appId`、`codeGenType`、`requestId`、`terminalStatus`、`toolNames`、`artifactHashes`、`buildStatus`、`errorCode`、`durationMs`。

报告只保存摘要和哈希，不保存完整提示词、源码、工具参数或令牌。

- [ ] **Step 4: 运行定向测试并提交**

Run: `.\mvnw.cmd test -Dtest=DelegatingAiGenerationGatewayTest,LangGraphAiGenerationGatewayTest`

Expected: 相同用户跨请求稳定落桶，取消路由一致，现有 NDJSON 适配测试不回归。

```powershell
git add -- src/main/java/com/yupi/yuaicodemother/ai/gateway/DelegatingAiGenerationGateway.java src/test/java/com/yupi/yuaicodemother/ai/gateway/DelegatingAiGenerationGatewayTest.java scripts/compare-ai-generation-engines.ps1 doc/ai-service-startup.md
git commit -m "fix: 稳定 AI 引擎灰度路由"
```

### Task 6: 真实 HTTP、Redis 与三类型端到端验收门

**Files:**
- Create: `scripts/test-ai-service.ps1`
- Create: `scripts/test-ai-phase-two-e2e.ps1`
- Modify: `src/test/java/com/yupi/yuaicodemother/ai/gateway/ToolInvocationIdempotencyRedisIT.java`
- Modify: `ai-service/README.md`
- Modify: `doc/ai-service-phase-one-handoff.md`

- [ ] **Step 1: 验证真实 Spring/Python 网络契约**

启动 MySQL、Redis、Spring 和 Python 后执行：

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
.\scripts\test-ai-service.ps1
```

验收认证失败、缺字段、成功工具调用、非零 Spring 业务码、畸形响应和错误脱敏。必须记录实际端口、服务版本和命令退出码。

- [ ] **Step 2: 执行真实 Redis 多客户端幂等测试**

```powershell
$env:AI_REDIS_INTEGRATION = "true"
$env:AI_REDIS_URL = "redis://127.0.0.1:6379/1"
.\mvnw.cmd test -Dtest=ToolInvocationIdempotencyRedisIT
```

扩展 `ToolInvocationIdempotencyRedisIT`：先直接写入一条 `RUNNING` 状态模拟进程中止，再断言同作用域重试返回 indeterminate；为 action 成功后状态写回失败增加可关闭的第二 Redisson 客户端或等价故障注入，断言不会再次执行 action。随后验收共享结果回放、参数冲突、锁竞争和这两个故障窗口。未连接 Redis 时此门失败，不得提高灰度。

- [ ] **Step 3: 在独立测试应用执行三类型首次生成与二次修改**

`test-ai-phase-two-e2e.ps1` 必须要求显式传入三个测试应用 ID，拒绝使用事故应用或生产应用。每类验证：

- 首次生成收到唯一成功终态。
- 二次修改保留未指定的文字、图片、功能和操作方式。
- HTML/MULTI_FILE 仅在发布成功后切换活动版本。
- Vue 文件操作全部经过 Spring 工具事件，构建失败会修复或明确失败。
- 停止、网络中断和迟到回调不刷新预览、不覆盖活动版本。

- [ ] **Step 4: 执行完整自动化验证**

```powershell
.\mvnw.cmd test
mvn clean -DskipTests compile
Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
Set-Location ..
git diff --check
```

前端若因验收脚本或事件显示发生改动，还必须在独立前端仓库执行既有 Node 测试、`npm run type-check` 和 `npm run build-only`。

- [ ] **Step 5: 记录证据并决定灰度**

只在以下条件全部满足时把 `gray-percentage` 从 0 调到一个明确的小比例：三类型首次生成和二次修改通过；真实 Redis 幂等通过；取消和网络中断不发布失败候选；Legacy 回滚路径仍可用；日志不包含源码、完整提示词、工具参数或令牌。

交接文档记录通过项、失败项、命令、退出码、测试应用 ID、Git 提交和回滚配置。灰度配置变更单独提交，不与功能代码混在同一提交中。
