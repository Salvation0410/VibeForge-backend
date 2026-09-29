# Generation Resilience and Active Cancellation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve truncated-output guidance and preview layout, configure Python model output tokens, and make LangGraph generation actively cancellable without weakening Spring's publish and lease safety gates.

**Architecture:** Spring remains the authority for application ownership, generation lease state, project files, builds, and publication. The frontend calls explicit cancel/status APIs and renders `running`, `stopping`, and `idle`; Python tracks the active workflow task and cancels it; Spring terminates any active Vue build process before the generation lease is released. Legacy keeps cooperative cancellation and uses the same truthful frontend state.

**Tech Stack:** Java 21, Spring Boot, Reactor, Redisson, Python 3.12, FastAPI, asyncio, LangGraph 0.6.11, LangChain OpenAI, Vue 3, TypeScript, Ant Design Vue.

---

### Task 1: Configure model output tokens and document static multi-file limits

**Files:**
- Modify: `ai-service/src/ai_service/config.py`
- Modify: `ai-service/src/ai_service/models/openai_compatible.py`
- Modify: `ai-service/.env.example`
- Modify: `ai-service/tests/test_openai_compatible.py`
- Modify: `ai-service/tests/test_gateway_and_config.py`
- Modify: `doc/ai-service-phase-one-handoff.md`

- [ ] Add a failing settings test asserting `AI_SERVICE_MODEL_MAX_TOKENS` defaults to `8192` and rejects non-positive values.
- [ ] Add a failing model adapter test asserting `ChatOpenAI` receives `max_tokens=settings.model_max_tokens`.
- [ ] Add `model_max_tokens: int = Field(default=8192, ge=1)` to `Settings` and pass it to `ChatOpenAI`.
- [ ] Add `AI_SERVICE_MODEL_MAX_TOKENS=8192` to `.env.example`.
- [ ] Document that MULTI_FILE still requires one complete three-file response, the configurable limit only reduces truncation risk, and staged/tool-based static generation remains a future optimization.
- [ ] Run `uv run pytest tests/test_openai_compatible.py tests/test_gateway_and_config.py -q` and expect zero failures.

### Task 2: Add Spring generation status and explicit cancellation boundary

**Files:**
- Create: `src/main/java/com/yupi/yuaicodemother/model/dto/app/AppGenerationCancelRequest.java`
- Create: `src/main/java/com/yupi/yuaicodemother/model/vo/AppGenerationStatusVO.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/ai/gateway/GenerationLeaseService.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/service/AppService.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/AppController.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/service/impl/AppServiceGenerationCancellationTest.java`

- [ ] Add failing tests for `ACTIVE -> STOPPING`, repeated cancellation, no active request, `COMMITTING`, ownership checks, and status mapping.
- [ ] Add a generation state snapshot to `GenerationLeaseService` that parses the existing `requestId:status` Redis value under the transition lock.
- [ ] Add an idempotent `cancelActive(appId)` transition that returns the authoritative request ID and public state without releasing the application lock.
- [ ] Add `AppService.cancelGeneration(appId, user)` and `AppService.getGenerationStatus(appId, user)`; cancellation calls the selected gateway with the authoritative request ID.
- [ ] Add `POST /apps/chat/gen/code/cancel` and `GET /apps/chat/gen/code/status` with the standard `BaseResponse` wrapper.
- [ ] Run `mvn "-Dtest=AppServiceGenerationCancellationTest" test` and expect zero failures.

### Task 3: Actively cancel the LangGraph workflow task

**Files:**
- Create: `ai-service/src/ai_service/orchestration/active_generations.py`
- Modify: `ai-service/src/ai_service/app.py`
- Modify: `ai-service/src/ai_service/api/routes.py`
- Modify: `ai-service/src/ai_service/orchestration/workflow.py`
- Modify: `ai-service/tests/conftest.py`
- Modify: `ai-service/tests/test_api.py`
- Modify: `ai-service/tests/test_package_structure.py`

- [ ] Add failing async tests showing the cancel endpoint cancels a blocking model task, emits no completed event, and clears graph checkpoints and registry state.
- [ ] Implement `ActiveGenerationRegistry` with `register`, `cancel`, `clear`, and `is_active` operations scoped to one Python process.
- [ ] Register the task created by `GenerationWorkflow.stream` and remove it during terminal cleanup.
- [ ] Make explicit cancel and HTTP disconnect call the same active cancellation function while retaining `CancellationRegistry` as a boundary-check fallback.
- [ ] Convert task cancellation into one stable `cancelled` event before closing the NDJSON stream.
- [ ] Run `uv run pytest tests/test_api.py tests/test_package_structure.py -q` and expect zero failures.

### Task 4: Terminate active Vue build processes on cancellation

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/core/builder/VueProjectBuilder.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/core/builder/VueProjectBuilderTest.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java`

- [ ] Add a failing builder test with a blocking command runner and assert `cancelBuild(requestId)` unblocks it with `VUE_BUILD_CANCELLED`.
- [ ] Carry the internal tool request ID into `buildProjectDetailed(projectPath, requestId)`.
- [ ] Track active build execution by request ID, including a cancellation flag and the running command/process termination hook.
- [ ] Make `cancelBuild(requestId)` idempotently terminate the current command and return only after bounded cleanup has been requested.
- [ ] On application cancellation, request both AI engine cancellation and Vue build cancellation while retaining the generation lease until the stream terminates.
- [ ] Run `mvn "-Dtest=VueProjectBuilderTest,InternalAiToolsControllerTest,AppServiceGenerationCancellationTest" test` and expect zero failures.

### Task 5: Implement truthful frontend stopping state and truncated-output preview guidance

**Files:**
- Create: `src/utils/generationLifecycle.ts`
- Create: `tests/generationLifecycle.test.ts`
- Modify: `src/api/app.ts`
- Modify: `src/pages/AppChatView.vue`

- [ ] Add failing pure-function tests for `running -> stopping -> idle`, polling delays, `COMMITTING`, and truncated-output presentation.
- [ ] Add typed `cancelAppGeneration(appId)` and `getAppGenerationStatus(appId)` API functions.
- [ ] Replace immediate local stop completion with `generationUiState: idle | running | stopping`; disable input and sending while stopping.
- [ ] On stop, call explicit cancel, suppress late stream chunks, close SSE after acknowledgement, and poll status until `IDLE`.
- [ ] Keep the prior preview mounted; only display the centered failure empty state when no successful preview exists.
- [ ] Change `MODEL_OUTPUT_TRUNCATED` copy to `生成内容达到模型输出上限，本轮结果未发布。请减少页面模块，或拆分为多次生成后重试。`.
- [ ] Center the no-preview status content horizontally and vertically without changing the iframe or overlay layout for successful previews.
- [ ] Run the Node tests, `npm run type-check`, and `npm run build-only` with zero errors.

### Task 6: Cross-service verification and focused Git commits

**Files:**
- Modify: `doc/ai-service-phase-one-handoff.md`
- Modify: `docs/superpowers/plans/2026-09-29-generation-resilience-and-cancellation.md`

- [ ] Run `cd ai-service && uv run python -m compileall -q src && uv run pytest && uv lock --check`.
- [ ] Run the focused Java cancellation/build/controller tests and `mvn clean -DskipTests compile`.
- [ ] Run frontend Node tests, `npm run type-check`, and `npm run build-only`.
- [ ] Run `git diff --check` in both repositories and inspect `git status --short`.
- [ ] Commit backend/Python/docs changes without staging `.gitignore`, `projects/`, or the unrelated untracked HTTP plan.
- [ ] Commit frontend changes separately without including unrelated lockfile changes.

## 2026-09-29 execution record

- Python: `compileall`, full `pytest` (168 passed), and `uv lock --check` passed.
- Java: cancellation/build/controller focused tests (47 passed) and clean compile passed.
- Frontend: 17 Node tests, `vue-tsc --build`, and Vite production build passed; only existing chunk-size and mixed dynamic/static import warnings remain.
- Git: backend and frontend are committed separately; unrelated `.gitignore`, `projects/`, and the 2026-09-22 HTTP contract plan are excluded.
