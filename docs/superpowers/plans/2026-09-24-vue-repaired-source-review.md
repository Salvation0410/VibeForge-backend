# Vue 修复后源码质量检查 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vue 项目至少完成一次修复并重新构建成功后，由 Spring 返回有界最终源码快照，让 Python Reviewer 基于真实修复结果做质量判断，同时避免源码进入幂等 Redis、checkpoint 和事件流。

**Architecture:** 共享工具契约新增模型不可调用的 `vue_source_snapshot`；Spring 的独立读取器负责目录剪枝、文本筛选、稳定排序和内容限额，控制器对该只读工具旁路成功结果幂等缓存。Python 只在修复后 Vue 质量检查中请求快照，将确定性 JSON 作为 Reviewer artifact，并用事件摘要函数隐藏源码。

**Tech Stack:** Java 21、Spring Boot、Java NIO、JUnit 5、Mockito、Python 3.12、FastAPI、LangGraph、pytest、JSON Schema、uv。

---

## File Map

- Modify `ai-service/src/ai_service/contracts/internal-ai-tools-v1.json`: 增加跨服务快照请求与响应 Schema。
- Modify `src/main/java/com/yupi/yuaicodemother/ai/gateway/InternalAiTool.java`: 注册模型不可调用的工具枚举。
- Modify `src/test/java/com/yupi/yuaicodemother/ai/gateway/InternalAiToolContractTest.java`: 锁定 Java 枚举与共享契约一致性。
- Modify `src/test/java/com/yupi/yuaicodemother/ai/gateway/InternalAiToolSchemaAssertions.java`: 增加 Java 契约示例。
- Modify `ai-service/tests/test_tool_contract.py`: 锁定 Python 请求、响应和模型工具白名单。
- Create `src/main/java/com/yupi/yuaicodemother/core/artifact/VueSourceSnapshotReader.java`: 生成有界、稳定且不跟随链接的源码快照。
- Create `src/test/java/com/yupi/yuaicodemother/core/artifact/VueSourceSnapshotReaderTest.java`: 覆盖过滤、排序、截断和失败语义。
- Modify `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java`: 接入快照工具并只对该工具旁路幂等缓存。
- Modify `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java`: 覆盖控制器快照和缓存例外。
- Modify `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsHttpContractTest.java`: 适配构造参数并锁定 HTTP 响应。
- Modify `ai-service/src/ai_service/orchestration/workflow.py`: 修复后读取快照、序列化给 Reviewer、脱敏工具事件。
- Modify `ai-service/tests/conftest.py`: 为 Fake Gateway 提供可配置快照结果。
- Modify `ai-service/tests/test_api.py`: 覆盖工作流调用条件、最终源码、失败和事件脱敏。
- Modify `ai-service/tests/test_gateway_and_config.py`: 覆盖真实网关的快照 Schema 校验。
- Modify `ai-service/README.md`: 记录修复后源码审查边界。
- Modify `doc/ai-service-phase-one-handoff.md`: 更新完成项、验证基线和后续优先级。

### Task 1: Add the Shared Snapshot Contract

**Files:**
- Modify: `ai-service/src/ai_service/contracts/internal-ai-tools-v1.json`
- Modify: `src/main/java/com/yupi/yuaicodemother/ai/gateway/InternalAiTool.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/ai/gateway/InternalAiToolContractTest.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/ai/gateway/InternalAiToolSchemaAssertions.java`
- Modify: `ai-service/tests/test_tool_contract.py`

- [ ] **Step 1: Add failing Java and Python contract expectations**

在 Java 枚举映射期望中增加：

```java
Map.entry("vue_source_snapshot", InternalAiTool.VUE_SOURCE_SNAPSHOT)
```

在 `InternalAiToolSchemaAssertions` 中增加请求和响应示例：

```java
case "vue_source_snapshot" -> Map.of("codeGenType", "VUE_PROJECT");
```

```java
case "vue_source_snapshot" -> Map.of(
        "files", List.of(Map.of(
                "path", "src/App.vue",
                "content", "<template />",
                "truncated", false)),
        "eligibleFileCount", 1,
        "includedFileCount", 1,
        "omittedFileCount", 0,
        "truncated", false);
```

在 Python `VALID_CASES` 中增加同等请求响应，并把 `vue_source_snapshot` 加入工作流内部工具拒绝模型调用的参数列表：

```python
"vue_source_snapshot": (
    {"codeGenType": "VUE_PROJECT"},
    {
        "files": [
            {"path": "src/App.vue", "content": "<template />", "truncated": False}
        ],
        "eligibleFileCount": 1,
        "includedFileCount": 1,
        "omittedFileCount": 0,
        "truncated": False,
    },
),
```

增加请求拒绝测试：

```python
@pytest.mark.parametrize("code_gen_type", ["HTML", "MULTI_FILE", "vue_project"])
def test_vue_source_snapshot_accepts_only_standard_vue_type(code_gen_type):
    with pytest.raises(ToolContractValidationError):
        validate_tool_arguments("vue_source_snapshot", {"codeGenType": code_gen_type})
```

- [ ] **Step 2: Run contract tests and verify they fail**

Run:

```powershell
mvn "-Dtest=InternalAiToolContractTest" test
Set-Location ai-service
uv run pytest tests/test_tool_contract.py -q
Set-Location ..
```

Expected: Java fails because the enum/contract entry is missing；Python fails because the tool is unknown。

- [ ] **Step 3: Add the enum and JSON Schema entry**

在 `InternalAiTool` 中增加：

```java
VUE_SOURCE_SNAPSHOT("vue_source_snapshot", false),
```

在 `internal-ai-tools-v1.json` 的 `artifact_context` 后加入：

```json
{
  "name": "vue_source_snapshot",
  "aliases": [],
  "modelCallable": false,
  "modelArguments": [],
  "description": "Read a bounded final Vue source snapshot for workflow quality review.",
  "requestSchema": {
    "type": "object",
    "properties": {
      "codeGenType": {"const": "VUE_PROJECT"}
    },
    "required": ["codeGenType"],
    "additionalProperties": false
  },
  "responseSchema": {
    "type": "object",
    "properties": {
      "files": {
        "type": "array",
        "minItems": 1,
        "maxItems": 24,
        "items": {
          "type": "object",
          "properties": {
            "path": {"type": "string", "minLength": 1},
            "content": {"type": "string"},
            "truncated": {"type": "boolean"}
          },
          "required": ["path", "content", "truncated"],
          "additionalProperties": false
        }
      },
      "eligibleFileCount": {"type": "integer", "minimum": 1},
      "includedFileCount": {"type": "integer", "minimum": 1, "maximum": 24},
      "omittedFileCount": {"type": "integer", "minimum": 0},
      "truncated": {"type": "boolean"}
    },
    "required": [
      "files", "eligibleFileCount", "includedFileCount", "omittedFileCount", "truncated"
    ],
    "additionalProperties": false
  }
}
```

- [ ] **Step 4: Run both contract suites**

Run:

```powershell
mvn "-Dtest=InternalAiToolContractTest" test
Set-Location ai-service
uv run pytest tests/test_tool_contract.py -q
Set-Location ..
```

Expected: both suites PASS；`vue_model_tool_specs()` remains the original five file tools。

- [ ] **Step 5: Commit the cross-service contract**

```powershell
git add -- ai-service/src/ai_service/contracts/internal-ai-tools-v1.json ai-service/tests/test_tool_contract.py src/main/java/com/yupi/yuaicodemother/ai/gateway/InternalAiTool.java src/test/java/com/yupi/yuaicodemother/ai/gateway/InternalAiToolContractTest.java src/test/java/com/yupi/yuaicodemother/ai/gateway/InternalAiToolSchemaAssertions.java
git diff --cached --check
git commit -m "feat: 定义 Vue 源码快照工具契约"
```

### Task 2: Build the Bounded Spring Source Snapshot

**Files:**
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/VueSourceSnapshotReader.java`
- Create: `src/test/java/com/yupi/yuaicodemother/core/artifact/VueSourceSnapshotReaderTest.java`

- [ ] **Step 1: Write failing reader tests**

创建测试类，使用 `@TempDir Path root` 和 mock `ArtifactPathResolver`。至少包含以下测试：

```java
@Test
void prioritizesEntryFilesAndSkipsGeneratedHiddenBinaryLockAndSymlinkContent() throws Exception {
    Path project = root.resolve("vue_project_42");
    Files.createDirectories(project.resolve("src/components"));
    Files.createDirectories(project.resolve("node_modules/pkg"));
    Files.createDirectories(project.resolve("dist"));
    Files.createDirectories(project.resolve("build"));
    Files.createDirectories(project.resolve(".git"));
    Files.writeString(project.resolve("package.json"), "{\"name\":\"demo\"}");
    Files.writeString(project.resolve("src/main.ts"), "createApp(App).mount('#app')");
    Files.writeString(project.resolve("src/App.vue"), "<template>fixed</template>");
    Files.writeString(project.resolve("src/components/ZCard.vue"), "<template>card</template>");
    Files.writeString(project.resolve("package-lock.json"), "secret lock");
    Files.write(project.resolve("src/logo.png"), new byte[]{0, 1, 2});
    Files.writeString(project.resolve("node_modules/pkg/index.js"), "ignored dependency");
    Files.writeString(project.resolve("dist/index.html"), "ignored dist");
    Files.writeString(project.resolve("build/output.js"), "ignored build");
    Files.writeString(project.resolve(".git/config"), "ignored hidden");
    try {
        Files.createSymbolicLink(project.resolve("src/external.ts"), project.resolve("src/main.ts"));
    } catch (UnsupportedOperationException | java.nio.file.FileSystemException ignored) {
        // 当前文件系统不支持符号链接时，其他过滤断言仍应执行。
    }
    when(resolver.resolveActiveRoot(CodeGenTypeEnum.VUE_PROJECT, 42L)).thenReturn(project);

    Map<String, Object> snapshot = new VueSourceSnapshotReader(resolver).read(42L);

    List<?> files = (List<?>) snapshot.get("files");
    assertEquals(List.of("package.json", "src/main.ts", "src/App.vue", "src/components/ZCard.vue"),
            files.stream().map(item -> ((Map<?, ?>) item).get("path")).toList());
    assertEquals(4, snapshot.get("eligibleFileCount"));
    assertEquals(false, snapshot.get("truncated"));
}
```

另外分别测试：

- 25 个合格文件时只返回 24 个，`omittedFileCount=1`。
- 12,001 字符文件返回不超过 12,000 字符、含 `...[truncated]...`、文件和整体 `truncated=true`。
- 多文件内容超过 60,000 字符时总内容不超过上限，最后一个文件按剩余额度截断或后续文件被遗漏。
- 不存在目录和空项目分别抛出以 `VUE_SOURCE_SNAPSHOT_` 开头的稳定 `BusinessException`。
- 读取异常不返回部分成功快照。

- [ ] **Step 2: Run the reader test and verify it fails to compile**

Run:

```powershell
mvn "-Dtest=VueSourceSnapshotReaderTest" test
```

Expected: FAIL because `VueSourceSnapshotReader` does not exist。

- [ ] **Step 3: Implement the dedicated reader**

创建组件，公开常量供同包测试使用：

```java
@Component
@RequiredArgsConstructor
public class VueSourceSnapshotReader {
    static final int MAX_FILES = 24;
    static final int MAX_FILE_CHARS = 12_000;
    static final int MAX_TOTAL_CHARS = 60_000;
    static final String TRUNCATION_MARKER = "\n...[truncated]...\n";

    private static final Set<String> EXCLUDED_DIRECTORIES =
            Set.of("node_modules", "dist", "build");
    private static final Set<String> EXCLUDED_FILES =
            Set.of("package-lock.json", "pnpm-lock.yaml", "yarn.lock");
    private static final Set<String> ALLOWED_EXTENSIONS =
            Set.of("vue", "ts", "tsx", "js", "jsx", "css", "scss", "less", "html", "json");

    private final ArtifactPathResolver artifactPathResolver;

    public Map<String, Object> read(long appId) {
        Path root = artifactPathResolver.resolveActiveRoot(CodeGenTypeEnum.VUE_PROJECT, appId);
        if (!Files.isDirectory(root)) {
            throw failure("VUE_SOURCE_SNAPSHOT_MISSING: Vue project directory does not exist");
        }
        List<Path> eligible = collectEligibleFiles(root);
        if (eligible.isEmpty()) {
            throw failure("VUE_SOURCE_SNAPSHOT_EMPTY: no eligible Vue source files");
        }
        return buildSnapshot(root, eligible);
    }
}
```

使用 `Files.walkFileTree(root, EnumSet.noneOf(FileVisitOption.class), Integer.MAX_VALUE, visitor)`：

- `preVisitDirectory` 对根目录外的隐藏名和排除目录返回 `SKIP_SUBTREE`。
- `visitFile` 只加入普通文件、非符号链接、非隐藏名、非锁文件和允许扩展名。
- 遍历失败抛 `VUE_SOURCE_SNAPSHOT_READ_FAILED`，不能吞掉异常。

排序器依次使用优先级和可移植路径：

```java
private int priority(String path) {
    if ("package.json".equals(path)) return 0;
    if (path.matches("src/main\\.(ts|tsx|js|jsx)")) return 1;
    if ("src/App.vue".equals(path)) return 2;
    return 3;
}
```

字符截断函数必须把标记计算在 12,000 和 60,000 上限内，保留头尾，并返回内容与是否截断。`eligibleFileCount` 是全部合格文件数；`includedFileCount` 是实际响应文件数；`omittedFileCount = eligible - included`；任一文件截断或有遗漏时整体 `truncated=true`。

- [ ] **Step 4: Run reader tests and inspect limit assertions**

Run:

```powershell
mvn "-Dtest=VueSourceSnapshotReaderTest" test
```

Expected: all reader tests PASS；快照总字符和每文件字符均不超过固定上限。

- [ ] **Step 5: Commit the reader**

```powershell
git add -- src/main/java/com/yupi/yuaicodemother/core/artifact/VueSourceSnapshotReader.java src/test/java/com/yupi/yuaicodemother/core/artifact/VueSourceSnapshotReaderTest.java
git diff --cached --check
git commit -m "feat: 生成有界 Vue 源码快照"
```

### Task 3: Expose the Snapshot Without Redis Result Caching

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsHttpContractTest.java`

- [ ] **Step 1: Add failing controller tests**

给所有控制器构造辅助方法增加 `VueSourceSnapshotReader` mock，然后新增：

```java
@Test
void readsVueSourceSnapshotWithoutCachingSourceInIdempotencyRedis() {
    var snapshotReader = mock(VueSourceSnapshotReader.class);
    var idempotencyService = mock(ToolInvocationIdempotencyService.class);
    Map<String, Object> snapshot = InternalAiToolSchemaAssertions.validResponseExample("vue_source_snapshot");
    when(snapshotReader.read(42L)).thenReturn(snapshot);
    var controller = controller(
            mock(ArtifactPublicationService.class),
            mock(ArtifactPathResolver.class),
            mock(ArtifactContextReader.class),
            snapshotReader,
            idempotencyService);

    Map<String, Object> result = controller.invoke("Bearer test-token", new ToolRequest(
            42L, "req-snapshot", "call-snapshot", "vue_source_snapshot",
            Map.of("codeGenType", "VUE_PROJECT"))).getData();

    assertEquals(snapshot, result);
    InternalAiToolSchemaAssertions.assertResponseValid("vue_source_snapshot", result);
    verify(snapshotReader).read(42L);
    verifyNoInteractions(idempotencyService);
}
```

新增非 Vue 类型拒绝测试，断言稳定消息：

```text
vue_source_snapshot requires VUE_PROJECT
```

并保留一个普通 `file_read` 测试证明仍调用 `idempotencyService.execute(...)`。

在 HTTP 契约测试中 mock 新 Reader、更新控制器构造，并增加成功 envelope 测试，断言 `$.data.files[0].path` 和计数字段。

- [ ] **Step 2: Run controller tests and verify they fail**

Run:

```powershell
mvn "-Dtest=InternalAiToolsControllerTest,InternalAiToolsHttpContractTest" test
```

Expected: FAIL because the controller has no snapshot dependency or dispatch branch。

- [ ] **Step 3: Inject and dispatch the snapshot reader**

在控制器增加：

```java
private final VueSourceSnapshotReader vueSourceSnapshotReader;
```

在 `execute` switch 增加：

```java
case VUE_SOURCE_SNAPSHOT -> vueSourceSnapshot(args, appId);
```

实现：

```java
private Map<String, Object> vueSourceSnapshot(Map<String, Object> args, long appId) {
    if (!"VUE_PROJECT".equals(text(args.get("codeGenType")))) {
        throw new BusinessException(ErrorCode.PARAMS_ERROR,
                "vue_source_snapshot requires VUE_PROJECT");
    }
    return vueSourceSnapshotReader.read(appId);
}
```

在 `invoke` 完成认证、必填字段、appId、工具解析和 arguments 规范化后增加唯一旁路：

```java
if (tool == InternalAiTool.VUE_SOURCE_SNAPSHOT) {
    return ResultUtils.success(execute(tool, request.appId(), request.requestId(), arguments));
}
```

其他工具继续进入 `idempotencyService.execute(...)`。旁路必须位于工具解析之后，不能绕过认证、顶层字段和 appId 校验。

- [ ] **Step 4: Run controller and contract tests**

Run:

```powershell
mvn "-Dtest=InternalAiToolsControllerTest,InternalAiToolsHttpContractTest,InternalAiToolContractTest,VueSourceSnapshotReaderTest" test
```

Expected: PASS；快照调用不接触幂等服务，普通工具仍使用幂等服务。

- [ ] **Step 5: Commit the Spring endpoint**

```powershell
git add -- src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsHttpContractTest.java
git diff --cached --check
git commit -m "feat: 提供瞬时 Vue 源码快照工具"
```

### Task 4: Review the Repaired Vue Source in Python

**Files:**
- Modify: `ai-service/src/ai_service/orchestration/workflow.py`
- Modify: `ai-service/tests/conftest.py`
- Modify: `ai-service/tests/test_api.py`
- Modify: `ai-service/tests/test_gateway_and_config.py`

- [ ] **Step 1: Make FakeToolGateway return a configurable snapshot**

把构造函数扩展为：

```python
def __init__(
    self,
    *,
    artifact_context: dict[str, Any] | None = None,
    vue_source_snapshot: dict[str, Any] | None = None,
):
    self.calls: list[dict[str, Any]] = []
    self.artifact_context = artifact_context or {"exists": False}
    self.vue_source_snapshot = vue_source_snapshot or {
        "files": [
            {
                "path": "src/App.vue",
                "content": "<template>fixed</template>",
                "truncated": False,
            }
        ],
        "eligibleFileCount": 1,
        "includedFileCount": 1,
        "omittedFileCount": 0,
        "truncated": False,
    }
```

在 `invoke` 中增加：

```python
if name == "vue_source_snapshot":
    return dict(self.vue_source_snapshot)
```

- [ ] **Step 2: Add failing workflow tests**

新增或扩展 Vue 修复测试，断言：

```python
snapshot_calls = [call for call in gateway.calls if call["name"] == "vue_source_snapshot"]
assert len(snapshot_calls) == 1
assert snapshot_calls[0]["toolCallId"] == "req-1:vue-source-snapshot:1"
review_call = next(data for name, data in model.calls if name == "review")
reviewed = json.loads(review_call["artifact"])
assert reviewed["files"][0]["content"] == "<template>fixed</template>"
assert "vueSourceSnapshot" not in review_call["context"]
snapshot_finished = next(
    event for event in events
    if event["type"] == "tool_finished"
    and event["data"]["tool"] == "vue_source_snapshot"
)
assert snapshot_finished["data"]["result"] == {
    "eligibleFileCount": 1,
    "includedFileCount": 1,
    "omittedFileCount": 0,
    "truncated": False,
}
assert "fixed" not in json.dumps(snapshot_finished, ensure_ascii=False)
```

新增以下独立场景：

- 未修复的 Vue 完成时不调用 `vue_source_snapshot`。
- HTML 和 MULTI_FILE 即使发生修复也不调用快照。
- Snapshot Gateway 抛错时最后事件为 `failed`，Reviewer 未被调用，不回退旧 artifact。
- Snapshot 返回截断元数据时 Reviewer artifact 原样包含 `truncated=true`。
- Reviewer 对快照返回 `False` 后仍进入下一次修复，最多两次限制和已有模型工具预算保持不变。

在 `test_gateway_and_config.py` 增加 MockTransport 成功与非法响应测试，证明 `SpringToolGateway.invoke("vue_source_snapshot", ...)` 执行请求和响应 Schema 校验。

- [ ] **Step 3: Run focused Python tests and verify they fail**

Run:

```powershell
Set-Location ai-service
uv run pytest tests/test_api.py tests/test_gateway_and_config.py tests/test_tool_contract.py -q
```

Expected: new workflow assertions fail because quality review still uses the old artifact and no snapshot call exists。

- [ ] **Step 4: Add snapshot rendering and event summarization helpers**

在 `workflow.py` 顶部导入 `json`。增加模块级纯函数：

```python
def _vue_snapshot_artifact(snapshot: dict[str, Any]) -> str:
    """以确定性 JSON 传递修复后源码和截断元数据。"""
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _vue_snapshot_event_result(snapshot: dict[str, Any]) -> dict[str, Any]:
    """事件只暴露计数与截断状态，避免源码进入 NDJSON。"""
    return {
        "eligibleFileCount": snapshot["eligibleFileCount"],
        "includedFileCount": snapshot["includedFileCount"],
        "omittedFileCount": snapshot["omittedFileCount"],
        "truncated": snapshot["truncated"],
    }
```

给 `_invoke_tool` 增加可选参数：

```python
event_result: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
```

发送完成事件时使用：

```python
visible_result = event_result(result) if event_result is not None else result
await emitter.emit(
    "tool_finished",
    node,
    data={"tool": name, "toolCallId": tool_call_id, "result": visible_result},
)
```

完整 `result` 仍作为 `_invoke_tool` 返回值，不写入状态。

- [ ] **Step 5: Read the snapshot only for repaired successful Vue builds**

将 `quality_review` 调整为：

```python
async def quality_review(state: WorkflowState) -> dict[str, Any]:
    """硬校验后执行质量检查；修复后的 Vue 使用 Spring 权威源码快照。"""
    if not state.get("validation", {}).get("valid", False):
        return {"quality_passed": False}
    review_artifact = state.get("artifact", "")
    if (
        state["code_gen_type"] == "VUE_PROJECT"
        and state.get("repair_count", 0) > 0
        and state.get("build", {}).get("built") is True
    ):
        snapshot = await self._invoke_tool(
            emitter,
            "quality_review",
            "vue_source_snapshot",
            {"codeGenType": "VUE_PROJECT"},
            state["app_id"],
            state["request_id"],
            f"{state['request_id']}:vue-source-snapshot:{state['repair_count']}",
            event_result=_vue_snapshot_event_result,
        )
        review_artifact = _vue_snapshot_artifact(snapshot)
    passed = await self.model.review(
        review_artifact,
        {**state["context"], "validation": state.get("validation"), "build": state.get("build")},
    )
    return {"quality_passed": passed}
```

快照不得加入返回 update、`state.context`、业务 checkpoint 或 completed 数据。快照工具调用不修改 `tool_call_count`。

- [ ] **Step 6: Run focused and full Python verification**

Run:

```powershell
uv run pytest tests/test_api.py tests/test_gateway_and_config.py tests/test_tool_contract.py -q
uv run python -m compileall -q src
uv run pytest
uv lock --check
Set-Location ..
```

Expected: all Python tests PASS；事件断言中没有源码；三种生成分支和两次修复上限保持通过。

- [ ] **Step 7: Commit the Python workflow integration**

`workflow.py` 在主工作区存在用户中文注释，若在隔离 worktree 执行则可正常暂存；若在主工作区执行，必须确认该注释不被无关改写。

```powershell
git add -- ai-service/src/ai_service/orchestration/workflow.py ai-service/tests/conftest.py ai-service/tests/test_api.py ai-service/tests/test_gateway_and_config.py
git diff --cached --check
git commit -m "feat: 审查 Vue 修复后真实源码"
```

### Task 5: Documentation and Full Cross-Service Verification

**Files:**
- Modify: `ai-service/README.md`
- Modify: `doc/ai-service-phase-one-handoff.md`

- [ ] **Step 1: Document the final-source review boundary**

在 README 中记录：

```markdown
Vue 至少发生一次修复并重新构建成功后，质量检查会通过 Spring 工作流专用工具读取有界最终源码快照。快照排除依赖、构建产物、隐藏目录、符号链接、二进制和锁文件，并受文件数、单文件及总字符上限约束。完整源码只传给当前 Reviewer，不进入工具幂等 Redis、业务 checkpoint、LangGraph state 或 NDJSON 事件；事件只携带文件计数和截断状态。
```

在 handoff 中：

- 将 P1“让 Vue 修复后的质量检查读取修复后源码视图”标记完成。
- 记录准确限额、瞬时执行和失败不回退语义。
- 更新 Java/Python 测试数量，以本轮实际输出为准。
- 下一项 P1 调整为“明确应用创建阶段的灰度身份键”。
- 保留真实 Redis、真实模型、Spring/Python HTTP 和前端首次生成/二次修改为后续验收。

- [ ] **Step 2: Run focused Java tests**

Run:

```powershell
mvn "-Dtest=InternalAiToolContractTest,VueSourceSnapshotReaderTest,InternalAiToolsControllerTest,InternalAiToolsHttpContractTest" test
```

Expected: all selected Java tests PASS。

- [ ] **Step 3: Run complete verification**

Run:

```powershell
Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
Set-Location ..
mvn clean -DskipTests compile
git diff --check
git status --short
```

Expected: Python suite, lock check, Java clean compile and whitespace check PASS。未启动真实服务，不声明真实 HTTP、Redis、模型或前端通过。

- [ ] **Step 4: Inspect scope and protect user files**

Run:

```powershell
git diff --stat
git diff -- ai-service/src/ai_service/contracts/internal-ai-tools-v1.json ai-service/src/ai_service/orchestration/workflow.py src/main/java/com/yupi/yuaicodemother/ai/gateway/InternalAiTool.java src/main/java/com/yupi/yuaicodemother/core/artifact/VueSourceSnapshotReader.java src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java
git status --short
```

Expected: `.gitignore`、用户中文注释、未跟踪旧 HTTP 计划和 `projects/` 未被删除、恢复或混入任务提交。

- [ ] **Step 5: Commit documentation**

```powershell
git add -- ai-service/README.md doc/ai-service-phase-one-handoff.md docs/superpowers/plans/2026-09-24-vue-repaired-source-review.md
git diff --cached --check
git commit -m "docs: 更新 Vue 修复后源码审查交接"
```

- [ ] **Step 6: Record completion evidence**

最终交付必须列出：

```text
已验证：共享 Schema 契约、Spring 源码快照读取、控制器瞬时执行边界、Python 修复后 Reviewer 流程、Python 完整 pytest、compileall、uv lock --check、Java 定向测试、Java clean compile、git diff --check。
未执行：真实 Redis、真实模型、Spring/Python 真实 HTTP、前端首次生成与二次修改。
人工验收重点：Vue 修复后功能/文字/图片/交互保留、快照截断时 Reviewer 行为、失败保留旧预览和单次成功刷新。
```
