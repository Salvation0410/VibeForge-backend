# Compact LangGraph Completed Event Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 LangGraph 内部 `completed` 事件移除完整 artifact，并以发布、构建和计数摘要保持成功终态可观测性。

**Architecture:** Python 在静态发布成功时缓存 Spring 权威发布摘要，在 Vue 完成时从构建状态生成摘要；正常完成和发布后异常补偿都复用同一小型终态数据。Java继续从最后一个静态 `content_delta` 读取候选，只把 `completed` 作为成功信号，因此生产适配器无需读取新字段。

**Tech Stack:** Python 3.12、FastAPI、LangGraph、pytest、Java 21、JUnit 5、JDK HttpClient。

---

## File Map

- Modify `ai-service/src/ai_service/orchestration/workflow.py`: 构建不含源码的完成摘要，并保存Spring发布元数据。
- Modify `ai-service/tests/test_api.py`: 锁定三分支和发布后补偿终态的数据契约。
- Modify `src/test/java/com/yupi/yuaicodemother/ai/gateway/LangGraphAiGenerationGatewayTest.java`: 锁定Java对无artifact完成事件的兼容行为。
- Modify `ai-service/README.md`: 记录内部完成事件只携带摘要。
- Modify `doc/ai-service-phase-one-handoff.md`: 更新已知大事件风险和验证基线。

### Task 1: Python Completed Summary Contract

**Files:**
- Modify: `ai-service/tests/test_api.py`
- Modify: `ai-service/src/ai_service/orchestration/workflow.py`

- [ ] **Step 1: Write failing contract assertions for all branches**

In `test_all_generation_branches_complete_in_order`, add assertions shared by all branches and branch-specific fields:

```python
completed = events[-1]
assert "artifact" not in completed["data"]
assert completed["data"]["threadId"] == "42:req-1"
assert completed["data"]["codeGenType"] == branch
assert completed["data"]["qualityPassed"] is True
assert completed["data"]["repairCount"] == 0
assert completed["data"]["toolCallCount"] >= 0
if branch in {"HTML", "MULTI_FILE"}:
    assert completed["data"]["published"] is True
    assert completed["data"]["versionId"] == "req-1"
    assert completed["data"]["artifactHashes"] == {}
else:
    assert completed["data"]["built"] is True
```

Replace the existing Vue repair assertion `events[-1]["data"]["artifact"] == "artifact:VUE_PROJECT"` with:

```python
assert "artifact" not in events[-1]["data"]
assert events[-1]["data"]["built"] is True
assert events[-1]["data"]["toolCallCount"] == 4
```

- [ ] **Step 2: Add compensation-path assertions**

For both `test_html_remains_completed_when_graph_checkpoint_fails_after_publication` and `test_multi_file_remains_completed_when_graph_checkpoint_fails_after_publication`, capture the only completed event and assert:

```python
completed = next(event for event in events if event["type"] == "completed")
assert "artifact" not in completed["data"]
assert completed["data"]["published"] is True
assert completed["data"]["versionId"] == "req-1"
assert completed["data"]["artifactHashes"] == {}
```

Also add the same assertions to `test_multi_file_records_publication_before_tool_finished_event`, proving the summary is recorded before auxiliary event delivery can fail.

- [ ] **Step 3: Run focused Python tests and verify they fail on the current artifact field**

Run:

```powershell
Set-Location ai-service
uv run pytest tests/test_api.py -q
```

Expected: the new assertions fail because `completed.data` contains `artifact` and lacks the new summary fields.

- [ ] **Step 4: Implement one completion-data helper**

Add a nested helper in `_build_graph` so normal and compensation paths use identical field names:

```python
def completion_data(state: WorkflowState) -> dict[str, Any]:
    data: dict[str, Any] = {
        "threadId": state["thread_id"],
        "codeGenType": state["code_gen_type"],
        "qualityPassed": state.get("quality_passed", False),
        "repairCount": state.get("repair_count", 0),
        "toolCallCount": state.get("tool_call_count", 0),
    }
    if state["code_gen_type"] in {"HTML", "MULTI_FILE"}:
        publication = state.get("publish", {})
        data.update(
            published=publication.get("published") is True,
            versionId=publication.get("versionId"),
            artifactHashes=publication.get("hashes", {}),
        )
    else:
        data["built"] = state.get("build", {}).get("built") is True
    return data
```

In `remember_publication`, preserve the Spring result and precompute the compensation summary without copying artifact:

```python
terminal["published"] = True
terminal["data"] = {
    "threadId": state["thread_id"],
    "codeGenType": state["code_gen_type"],
    "qualityPassed": state.get("quality_passed", False),
    "repairCount": state.get("repair_count", 0),
    "toolCallCount": state.get("tool_call_count", 0),
    "published": True,
    "versionId": result.get("versionId"),
    "artifactHashes": result.get("hashes", {}),
}
```

Change normal `finalize` emission to `data=completion_data(state)`. Do not remove `artifact` from `WorkflowState`, validation calls, publication arguments, content events, or checkpoint payloads in this task.

- [ ] **Step 5: Run focused and full Python tests**

Run:

```powershell
uv run pytest tests/test_api.py -q
uv run python -m compileall -q src
uv run pytest
uv lock --check
Set-Location ..
```

Expected: all Python tests pass; the existing dependency warnings may remain.

- [ ] **Step 6: Commit the Python contract change**

```powershell
git add -- ai-service/src/ai_service/orchestration/workflow.py ai-service/tests/test_api.py
git diff --cached --check
git commit -m "refactor: 缩减 LangGraph 完成事件"
```

### Task 2: Java Gateway Compatibility

**Files:**
- Modify: `src/test/java/com/yupi/yuaicodemother/ai/gateway/LangGraphAiGenerationGatewayTest.java`

- [ ] **Step 1: Update completed fixtures to the new summary**

Change completed test events from `{}` to an explicit artifact-free summary:

```java
event("completed", "{\"threadId\":\"42:req-1\",\"codeGenType\":\"MULTI_FILE\","
        + "\"qualityPassed\":true,\"repairCount\":1,\"toolCallCount\":0,"
        + "\"published\":true,\"versionId\":\"req-1\",\"artifactHashes\":{}}")
```

Keep the assertion that only the last static candidate is emitted after completed.

- [ ] **Step 2: Add a no-candidate completion test**

Add:

```java
@Test
void completedWithoutStaticCandidateDoesNotInventContent() throws Exception {
    var gateway = gatewayReturning(event("completed",
            "{\"threadId\":\"42:req-empty\",\"codeGenType\":\"HTML\",\"published\":true}"));

    var chunks = gateway.generate("build", CodeGenTypeEnum.HTML, 42L, 7L, "req-empty")
            .collectList().block();

    assertEquals(java.util.List.of(), chunks);
}
```

This locks the current behavior: Java never obtains source code from `completed.data`.

- [ ] **Step 3: Run Java gateway tests**

Run:

```powershell
mvn "-Dtest=LangGraphAiGenerationGatewayTest" test
```

Expected: all gateway tests pass without a Java production-code change.

- [ ] **Step 4: Commit Java compatibility coverage**

```powershell
git add -- src/test/java/com/yupi/yuaicodemother/ai/gateway/LangGraphAiGenerationGatewayTest.java
git diff --cached --check
git commit -m "test: 锁定轻量完成事件兼容性"
```

### Task 3: Documentation and Final Verification

**Files:**
- Modify: `ai-service/README.md`
- Modify: `doc/ai-service-phase-one-handoff.md`

- [ ] **Step 1: Document the internal event boundary**

Add this statement near the event protocol in `ai-service/README.md`:

```markdown
`completed.data` 只携带生成类型、质量、修复/工具计数以及发布或构建摘要，
不重复携带完整源码。HTML/MULTI_FILE 的最终候选仍来自最后一个
`content_delta`，且只有 Spring 发布成功后 Java 才向现有下游提交该候选。
```

Update the handoff limitation from “completed still carries full artifact” to a completed item, while explicitly retaining checkpoint artifact slimming as a separate future concern.

- [ ] **Step 2: Run complete verification**

Run:

```powershell
Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
Set-Location ..
mvn "-Dtest=LangGraphAiGenerationGatewayTest" test
mvn clean -DskipTests compile
git diff --check
git status --short
```

Expected: Python suite, Java gateway tests, lock check and clean compile pass; only task files are modified.

- [ ] **Step 3: Commit documentation**

```powershell
git add -- ai-service/README.md doc/ai-service-phase-one-handoff.md
git diff --cached --check
git commit -m "docs: 记录轻量完成事件契约"
```

- [ ] **Step 4: Record completion evidence**

The final handoff must state:

```text
已验证：Python compileall、完整 pytest、uv lock --check、Java LangGraph 网关测试、Java clean compile、git diff --check。
未执行：真实模型、真实 Spring/Python 端到端生成、真实 Redis。
后续独立事项：业务 checkpoint 与 LangGraph saver 的完整 artifact 持久化评估。
```
