# Spring/Python HTTP Contract Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Spring MVC 和 Python 网关测试锁定内部 AI 工具接口的认证、统一响应、业务错误和协议边界。

**Architecture:** Java 测试通过 MockMvc 走真实 Controller 与全局异常处理器；Python 测试继续使用 httpx MockTransport 验证客户端解析。两侧共享同一组 JSON 响应契约，但不启动真实服务、不连接数据库或模型。

**Tech Stack:** Java 21、Spring Boot Test、MockMvc、JUnit 5、Mockito、Python 3.12、httpx、pytest。

---

### Task 1: 确认 Spring MVC 测试入口和响应契约

**Files:**
- Inspect: `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java`
- Inspect: `src/main/java/com/yupi/yuaicodemother/exception/GlobalExceptionHandler.java`
- Inspect: `ai-service/src/ai_service/infrastructure/spring_tools.py`

- [ ] 确认 MockMvc 测试所需的 Controller、异常处理器和最小 Bean 集合。
- [ ] 保持生产路径 `/api/internal/ai-tools/invoke`、Bearer 认证和 `{code,data,message}` 响应结构不变。

### Task 2: 增加 Spring MVC HTTP 契约测试

**Files:**
- Create: `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsHttpContractTest.java`

- [ ] 使用 `@WebMvcTest(InternalAiToolsController.class)` 和 `@Import` 加载实际异常处理器，Mock 控制器依赖。
- [ ] 验证合法请求携带 Bearer 令牌时返回 HTTP 200、`code=0` 和工具 `data`。
- [ ] 验证缺少或错误令牌返回统一非零业务响应，且不会执行幂等服务。
- [ ] 验证缺少 `appId`、`requestId` 或工具名的 JSON 请求返回参数错误响应。
- [ ] 验证控制器抛出的稳定幂等业务异常仍被编码在 `message` 中，供 Python 网关识别。

### Task 3: 补齐 Python 响应契约样例

**Files:**
- Modify: `ai-service/tests/test_gateway_and_config.py`

- [ ] 使用与 Spring MVC 测试相同的成功、认证失败、参数错误和稳定幂等错误 JSON。
- [ ] 验证成功响应只返回 `data` 字典，非零业务码转换为脱敏后的 `SpringToolError`。
- [ ] 验证错误响应中的源码、路径和内部异常文本不会进入异常消息。
- [ ] 保留旧版精确 `{"data": {...}}` envelope 的兼容边界和 HTTP 5xx 发布重试语义。

### Task 4: 执行验证并记录边界

**Files:**
- Modify: `doc/ai-service-phase-one-handoff.md` (only if verification baseline changes)

- [ ] 运行相关 Maven 测试和 `mvn clean -DskipTests compile`。
- [ ] 运行 Python `compileall`、完整 `pytest` 和 `uv lock --check`。
- [ ] 运行 `git diff --check`，确认未启动服务、未连接真实模型且未混入用户生成文件。

### Task 5: 真实 Redis 幂等验收入口

**Files:**
- Create: `src/test/java/com/yupi/yuaicodemother/ai/gateway/ToolInvocationIdempotencyRedisIT.java`
- Modify: `doc/ai-service-phase-one-handoff.md`

- [x] 增加默认跳过的 opt-in 集成测试，使用 `AI_REDIS_INTEGRATION=true` 和可选 `AI_REDIS_URL` 连接真实 Redis。
- [x] 覆盖两个独立 Redisson 客户端的成功结果回放、参数冲突和锁竞争；未设置环境变量时不连接 Redis。
- [x] 验证测试源码可编译，默认执行该测试类时为跳过而非失败。
