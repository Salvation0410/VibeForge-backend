# Vue Tool Contract Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the unsupported `search_reference` Vue tool call and enforce one versioned Java/Python tool contract.

**Architecture:** A JSON contract packaged inside `ai_service` describes canonical names, aliases, visibility, and model arguments. Python loads it to build the Vue prompt and validate model calls before invoking Spring; Java normalizes names through a focused enum and verifies its registry against the same repository file.

**Tech Stack:** Python 3.12, LangChain, LangGraph, pytest, Java 21, Spring Boot, Jackson, JUnit 5

---

### Task 1: Add The Versioned Contract And Python Contract Tests

**Files:**
- Create: `ai-service/src/ai_service/contracts/internal-ai-tools-v1.json`
- Create: `ai-service/src/ai_service/models/tool_contract.py`
- Create: `ai-service/tests/test_tool_contract.py`

- [x] **Step 1: Write failing tests for contract loading, prompt rendering, and validation**

Test the five model-callable names, exclusion of `search_reference`, required arguments, rejection of controlled arguments, and rejection of internal tools.

- [x] **Step 2: Run the contract tests and verify they fail**

Run: `uv run pytest tests/test_tool_contract.py -q`

Expected: FAIL because `tool_contract.py` and the JSON contract do not exist.

- [x] **Step 3: Add the minimal JSON contract and Python loader**

Implement immutable `ToolSpec` values, `vue_tool_prompt()` and `validate_vue_tool_call()` without adding a JSON Schema dependency.

- [x] **Step 4: Run the contract tests**

Run: `uv run pytest tests/test_tool_contract.py -q`

Expected: PASS.

### Task 2: Apply The Contract To The Model And Workflow

**Files:**
- Modify: `ai-service/src/ai_service/models/openai_compatible.py`
- Modify: `ai-service/src/ai_service/orchestration/workflow.py`
- Modify: `ai-service/tests/conftest.py`
- Modify: `ai-service/tests/test_api.py`

- [x] **Step 1: Write failing adapter and workflow tests**

Assert the generated prompt contains canonical tools, the fake Vue model calls `file_read`, controlled fields are injected by the workflow, and invalid calls never reach the fake gateway.

- [x] **Step 2: Run the focused tests and verify they fail**

Run: `uv run pytest tests/test_tool_contract.py tests/test_api.py -q`

Expected: FAIL on the old `search_reference` behavior and missing validation.

- [x] **Step 3: Generate the prompt from the contract**

Replace the hard-coded example in `OpenAICompatibleModel.generate()` with `vue_tool_prompt()` for `VUE_PROJECT` only.

- [x] **Step 4: Validate and enrich tool calls before gateway invocation**

Call `validate_vue_tool_call()`, then add the trusted `appId` and `codeGenType="VUE_PROJECT"`. Keep the current call counter, event sequence, and deterministic tool call IDs.

- [x] **Step 5: Run the focused and full Python suites**

Run: `uv run pytest tests/test_tool_contract.py tests/test_api.py -q`

Run: `uv run pytest`

Expected: all tests PASS.

### Task 3: Centralize Spring Tool Name Parsing

**Files:**
- Create: `src/main/java/com/yupi/yuaicodemother/ai/gateway/InternalAiTool.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java`
- Create: `src/test/java/com/yupi/yuaicodemother/ai/gateway/InternalAiToolContractTest.java`

- [x] **Step 1: Write failing parsing and shared-contract tests**

Assert canonical names and aliases resolve, `search_reference` fails, and the Java registry matches `contracts/internal-ai-tools-v1.json`.

- [x] **Step 2: Run tests and verify they fail**

Run: `mvn test -Dtest=InternalAiToolsControllerTest,InternalAiToolContractTest`

Expected: FAIL because `InternalAiTool` does not exist.

- [x] **Step 3: Implement the tool enum and update dispatch**

Parse all existing aliases into a canonical enum and switch on the enum in the controller. Preserve every existing handler and error response.

- [x] **Step 4: Run Java tests**

Run: `mvn test -Dtest=InternalAiToolsControllerTest,InternalAiToolContractTest`

Expected: PASS.

### Task 4: Update Documentation And Run Final Verification

**Files:**
- Modify: `ai-service/README.md`
- Modify: `doc/ai-service-phase-one-handoff.md`

- [x] **Step 1: Document the canonical tool list and validation boundary**

Describe model-callable versus internal tools and remove the resolved P0 mismatch from the handoff risk list.

- [x] **Step 2: Run Python verification**

Run: `uv run python -m compileall -q src`

Run: `uv run pytest`

Run: `uv lock --check`

Expected: exit 0 for all commands.

- [x] **Step 3: Run Java verification**

Run: `mvn test -Dtest=InternalAiToolsControllerTest,InternalAiToolContractTest`

Run: `mvn clean -DskipTests compile`

Expected: BUILD SUCCESS.

- [x] **Step 4: Check repository hygiene**

Run: `git diff --check`

Run: `git status --short`

Expected: no whitespace errors and only task-related files changed.
