# Internal Tool JSON Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为九个 Spring/Python 内部工具建立共享 Draft 2020-12 请求与响应 Schema，并让 Python 在 HTTP 边界执行校验、Java 测试验证真实控制器结果。

**Architecture:** 继续以 `internal-ai-tools-v1.json` 作为唯一事实来源，在每个工具条目内嵌严格请求 Schema 和向前兼容的响应 Schema。Python 加载并缓存契约，在发起请求前和解析成功结果后校验；Spring 生产代码不增加 Schema 引擎，Java 仅在测试期使用同一契约验证枚举和控制器结果。

**Tech Stack:** Python 3.12、jsonschema 4.x、httpx、pytest、Java 21、Spring Boot 3.5、Jackson、networknt json-schema-validator 1.5.7、JUnit 5、Mockito。

---

## 文件职责

- Modify: `ai-service/src/ai_service/contracts/internal-ai-tools-v1.json`：九个工具的唯一共享请求/响应契约。
- Modify: `ai-service/src/ai_service/models/tool_contract.py`：加载、校验和缓存契约，保留 Vue 模型工具规则。
- Modify: `ai-service/src/ai_service/infrastructure/spring_tools.py`：把契约校验接入 HTTP 调用前后并映射脱敏错误。
- Modify: `ai-service/pyproject.toml`、`ai-service/uv.lock`：加入 Python 运行时 JSON Schema 依赖。
- Modify: `ai-service/tests/test_tool_contract.py`：覆盖契约加载、九工具样例、严格请求和兼容响应。
- Modify: `ai-service/tests/test_gateway_and_config.py`：覆盖 HTTP 前拒绝、成功结果校验、旧 envelope 和错误脱敏。
- Modify: `pom.xml`：加入仅测试使用的 Java Schema 校验器。
- Create: `src/test/java/com/yupi/yuaicodemother/ai/gateway/InternalAiToolSchemaAssertions.java`：Java 测试读取并校验共享契约。
- Modify: `src/test/java/com/yupi/yuaicodemother/ai/gateway/InternalAiToolContractTest.java`：验证方言、Schema 完整性、枚举和别名一致性。
- Modify: `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java`：用共享 Schema 验证九个真实控制器结果。
- Modify: `ai-service/README.md`、`doc/ai-service-phase-one-handoff.md`：记录运行时边界、兼容策略和未执行的真实验收。

主工作树中未跟踪的 `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsHttpContractTest.java` 不属于本计划，不读取、移动或提交它。

### Task 1: 扩展共享工具契约并实现 Python 契约校验器

**Files:**
- Modify: `ai-service/src/ai_service/contracts/internal-ai-tools-v1.json`
- Modify: `ai-service/src/ai_service/models/tool_contract.py`
- Modify: `ai-service/tests/test_tool_contract.py`
- Modify: `ai-service/pyproject.toml`
- Modify: `ai-service/uv.lock`

- [ ] **Step 1: 写契约加载和九工具样例的失败测试**

在 `test_tool_contract.py` 增加固定样例。每个样例必须是当前工作流或 Spring 控制器实际使用的形状：

```python
from ai_service.models.tool_contract import (
    ToolContractValidationError,
    resolve_tool_spec,
    validate_tool_arguments,
    validate_tool_result,
)

VALID_CASES = {
    "dir_read": (
        {"relativeDirPath": "", "codeGenType": "VUE_PROJECT"},
        {"entries": ["src/App.vue"]},
    ),
    "file_read": (
        {"relativeFilePath": "src/App.vue", "codeGenType": "VUE_PROJECT"},
        {"content": "<template />"},
    ),
    "file_write": (
        {"relativeFilePath": "src/App.vue", "content": "<template />", "codeGenType": "VUE_PROJECT"},
        {"ok": True, "path": "App.vue"},
    ),
    "file_modify": (
        {
            "relativeFilePath": "src/App.vue",
            "oldContent": "before",
            "newContent": "after",
            "codeGenType": "VUE_PROJECT",
        },
        {"ok": False, "message": "oldContent not found"},
    ),
    "file_delete": (
        {"relativeFilePath": "src/Old.vue", "codeGenType": "VUE_PROJECT"},
        {"ok": True},
    ),
    "artifact_context": (
        {"codeGenType": "HTML"},
        {"exists": True, "codeGenType": "HTML", "entry": "index.html", "artifact": "```html\nready\n```"},
    ),
    "artifact_validate": (
        {"artifact": "candidate", "codeGenType": "HTML"},
        {"valid": False, "errors": [{"code": "HTML_FORMAT_INVALID", "message": "invalid", "file": "index.html"}]},
    ),
    "artifact_publish": (
        {
            "artifact": "candidate",
            "codeGenType": "MULTI_FILE",
            "engine": "langgraph",
            "finishReason": "STOP",
        },
        {"published": True, "versionId": "release-1", "hashes": {"index.html": "abc"}},
    ),
    "project_build": (
        {"codeGenType": "VUE_PROJECT"},
        {"built": False, "errorCode": "VUE_NPM_BUILD_FAILED", "message": "vite failed"},
    ),
}


@pytest.mark.parametrize(("name", "case"), VALID_CASES.items())
def test_all_tool_request_and_response_examples_match_contract(name, case):
    arguments, result = case
    assert validate_tool_arguments(name, arguments) == arguments
    assert validate_tool_result(name, result) == result


def test_request_rejects_missing_wrong_typed_and_additional_fields():
    invalid = [
        {"codeGenType": "VUE_PROJECT"},
        {"relativeFilePath": 42, "codeGenType": "VUE_PROJECT"},
        {"relativeFilePath": "src/App.vue", "codeGenType": "VUE_PROJECT", "appId": "forged"},
        {"relativeFilePath": "src/App.vue", "codeGenType": "vue_project"},
    ]
    for arguments in invalid:
        with pytest.raises(ToolContractValidationError):
            validate_tool_arguments("file_read", arguments)


def test_response_allows_new_fields_but_checks_declared_fields():
    assert validate_tool_result(
        "project_build",
        {"built": True, "errorCode": "", "message": "", "durationMs": 125},
    )["durationMs"] == 125
    with pytest.raises(ToolContractValidationError):
        validate_tool_result(
            "project_build",
            {"built": "true", "errorCode": "", "message": ""},
        )


def test_alias_uses_canonical_schema_and_unknown_tool_fails():
    assert resolve_tool_spec("readFile").name == "file_read"
    assert validate_tool_arguments(
        "readFile",
        {"relativeFilePath": "index.html", "codeGenType": "HTML"},
    )["relativeFilePath"] == "index.html"
    with pytest.raises(ToolContractValidationError):
        validate_tool_arguments("search_reference", {})
```

另外覆盖 `artifact_context` 的四种互斥成功响应：不存在、HTML、MULTI_FILE、VUE_PROJECT。Vue 样例必须使用当前字段 `entries`，不得写成 `files`。

- [ ] **Step 2: 运行测试并确认因 Schema API 尚不存在而失败**

Run:

```powershell
Set-Location ai-service
uv run pytest tests/test_tool_contract.py -q
```

Expected: collection 或导入阶段因 `ToolContractValidationError`、`resolve_tool_spec`、`validate_tool_arguments`、`validate_tool_result` 尚不存在而失败。

- [ ] **Step 3: 加入 Python 正式依赖并更新锁文件**

Run:

```powershell
Set-Location ai-service
uv add "jsonschema>=4.23,<5"
uv lock --check
```

Expected: `pyproject.toml` 出现 `jsonschema>=4.23,<5`，`uv.lock` 解析成功且 `uv lock --check` 退出码为 0。

- [ ] **Step 4: 为九个工具写入完整 Draft 2020-12 Schema**

在契约顶层加入：

```json
{
  "version": 1,
  "schemaDialect": "https://json-schema.org/draft/2020-12/schema",
  "tools": []
}
```

每个 `requestSchema` 必须是以下模式，其中 `properties` 和 `required` 按工具表展开，禁止额外字段：

```json
{
  "type": "object",
  "properties": {
    "relativeFilePath": {"type": "string"},
    "codeGenType": {"type": "string", "enum": ["HTML", "MULTI_FILE", "VUE_PROJECT"]}
  },
  "required": ["relativeFilePath", "codeGenType"],
  "additionalProperties": false
}
```

九个请求的 `properties` / `required` 精确配置为：

```text
dir_read: relativeDirPath:string, codeGenType:enum
file_read: relativeFilePath:string, codeGenType:enum
file_write: relativeFilePath:string, content:string, codeGenType:enum
file_modify: relativeFilePath:string, oldContent:string, newContent:string, codeGenType:enum
file_delete: relativeFilePath:string, codeGenType:enum
artifact_context: codeGenType:enum
artifact_validate: artifact:string, codeGenType:enum
artifact_publish: artifact:string, codeGenType:enum, engine:string, finishReason:string
project_build: codeGenType:enum
```

普通响应使用 `required` 锁定现有字段，同时显式允许未来字段：

```json
{
  "type": "object",
  "properties": {
    "built": {"type": "boolean"},
    "errorCode": {"type": "string"},
    "message": {"type": "string"}
  },
  "required": ["built", "errorCode", "message"],
  "additionalProperties": true
}
```

其余普通响应精确配置为：

```text
dir_read: required entries; entries=array(items=string)
file_read: required content; content=string
file_write: required ok; ok=boolean, path=string optional
file_modify: required ok; ok=boolean, message=string optional
file_delete: required ok; ok=boolean
artifact_validate: required valid/errors; valid=boolean; errors=array(items object requiring code/message strings, optional file string, additionalProperties=true)
artifact_publish: required published/versionId/hashes; published=boolean; versionId=string; hashes=object(additionalProperties=string)
project_build: required built/errorCode/message; all types as shown above
```

`artifact_context.responseSchema` 使用以下互斥结构：

```json
{
  "oneOf": [
    {
      "type": "object",
      "properties": {
        "exists": {"const": false},
        "codeGenType": {"enum": ["HTML", "MULTI_FILE", "VUE_PROJECT"]}
      },
      "required": ["exists", "codeGenType"],
      "additionalProperties": true
    },
    {
      "type": "object",
      "properties": {
        "exists": {"const": true},
        "codeGenType": {"const": "HTML"},
        "entry": {"type": "string"},
        "artifact": {"type": "string"}
      },
      "required": ["exists", "codeGenType", "artifact"],
      "additionalProperties": true
    },
    {
      "type": "object",
      "properties": {
        "exists": {"const": true},
        "codeGenType": {"const": "MULTI_FILE"},
        "artifact": {"type": "string"}
      },
      "required": ["exists", "codeGenType", "artifact"],
      "additionalProperties": true
    },
    {
      "type": "object",
      "properties": {
        "exists": {"const": true},
        "codeGenType": {"const": "VUE_PROJECT"},
        "entries": {"type": "array", "items": {"type": "string"}},
        "truncated": {"type": "boolean"}
      },
      "required": ["exists", "codeGenType", "entries", "truncated"],
      "additionalProperties": true
    }
  ]
}
```

- [ ] **Step 5: 实现 Python 契约加载与校验接口**

在 `tool_contract.py` 中保留现有公开函数，扩展 `ToolSpec` 并增加以下接口。校验失败不得把实例值写入异常文本：

```python
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

_DIALECT = "https://json-schema.org/draft/2020-12/schema"


class ToolContractValidationError(ValueError):
    """An internal tool name, request, or result violates the shared contract."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    aliases: tuple[str, ...]
    model_callable: bool
    model_arguments: tuple[str, ...]
    description: str
    request_schema: dict[str, Any]
    response_schema: dict[str, Any]


def _parse_contract(payload: dict[str, Any]) -> tuple[ToolSpec, ...]:
    if payload.get("version") != 1:
        raise RuntimeError("Unsupported internal AI tool contract version")
    if payload.get("schemaDialect") != _DIALECT:
        raise RuntimeError("Unsupported internal AI tool schema dialect")
    try:
        specs = tuple(
            ToolSpec(
                name=item["name"],
                aliases=tuple(item.get("aliases", [])),
                model_callable=bool(item["modelCallable"]),
                model_arguments=tuple(item.get("modelArguments", [])),
                description=item["description"],
                request_schema=item["requestSchema"],
                response_schema=item["responseSchema"],
            )
            for item in payload.get("tools", [])
        )
    except (KeyError, TypeError) as error:
        raise RuntimeError("Internal AI tool contract is malformed") from error
    external_names = [value for spec in specs for value in (spec.name, *spec.aliases)]
    if not specs or len(external_names) != len(set(external_names)):
        raise RuntimeError("Internal AI tool contract contains missing or duplicate names")
    try:
        for spec in specs:
            Draft202012Validator.check_schema(spec.request_schema)
            Draft202012Validator.check_schema(spec.response_schema)
    except SchemaError as error:
        raise RuntimeError("Internal AI tool contract contains an invalid schema") from error
    return specs


@lru_cache(maxsize=1)
def all_tool_specs() -> tuple[ToolSpec, ...]:
    contract_path = files(_CONTRACT_PACKAGE).joinpath(_CONTRACT_FILE)
    return _parse_contract(json.loads(contract_path.read_text(encoding="utf-8")))


def resolve_tool_spec(name: str) -> ToolSpec:
    for spec in all_tool_specs():
        if name == spec.name or name in spec.aliases:
            return spec
    raise ToolContractValidationError("Unknown internal AI tool")


def _validate(name: str, value: dict[str, Any], *, response: bool) -> dict[str, Any]:
    spec = resolve_tool_spec(name)
    schema = spec.response_schema if response else spec.request_schema
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as error:
        raise ToolContractValidationError("Internal AI tool payload did not match expected schema") from error
    return value


def validate_tool_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _validate(name, arguments, response=False)


def validate_tool_result(name: str, result: dict[str, Any]) -> dict[str, Any]:
    return _validate(name, result, response=True)
```

调整 `validate_vue_tool_call()` 使用 `resolve_tool_spec()`，但保留现有面向模型的明确错误消息；模型工具只校验 `modelArguments`，完整参数 Schema 在工作流注入 `codeGenType` 后由网关执行。

- [ ] **Step 6: 运行契约测试并确认通过**

Run:

```powershell
Set-Location ai-service
uv run pytest tests/test_tool_contract.py -q
uv lock --check
```

Expected: 全部通过；九工具样例、别名、未知工具、严格请求和兼容响应均被锁定。

- [ ] **Step 7: 提交共享契约与 Python 校验器**

```powershell
git add -- ai-service/src/ai_service/contracts/internal-ai-tools-v1.json ai-service/src/ai_service/models/tool_contract.py ai-service/tests/test_tool_contract.py ai-service/pyproject.toml ai-service/uv.lock
git commit -m "feat: 定义内部工具 JSON Schema 契约"
```

### Task 2: 在 Python Spring 工具网关启用边界运行时校验

**Files:**
- Modify: `ai-service/src/ai_service/infrastructure/spring_tools.py`
- Modify: `ai-service/tests/test_gateway_and_config.py`

- [ ] **Step 1: 写请求在网络前失败的测试**

增加一个计数 transport，证明非法请求没有发送 HTTP：

```python
from ai_service.infrastructure.spring_tools import InvalidSpringToolRequest


@pytest.mark.asyncio
async def test_invalid_tool_request_is_rejected_before_http():
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"code": 0, "data": {"content": "unexpected"}, "message": "ok"})

    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(InvalidSpringToolRequest) as exc_info:
        await gateway.invoke(
            "file_read",
            {"relativeFilePath": "src/App.vue", "codeGenType": "VUE_PROJECT", "appId": "forged"},
            app_id="42",
            request_id="req-1",
            tool_call_id="req-1:file-read",
        )
    assert request_count == 0
    assert str(exc_info.value) == (
        "INVALID_SPRING_TOOL_REQUEST: Spring tool request did not match expected schema"
    )
    assert "App.vue" not in str(exc_info.value)
    await gateway.close()
```

- [ ] **Step 2: 写逐工具响应校验和兼容性失败测试**

把现有成功 mock 更新为符合对应工具 Schema 的结果，并增加：

```python
@pytest.mark.asyncio
async def test_tool_specific_success_result_is_validated_and_allows_new_fields():
    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"built": True, "errorCode": "", "message": "", "durationMs": 125},
                    "message": "ok",
                },
            )
        ),
    )
    result = await gateway.invoke(
        "project_build",
        {"codeGenType": "VUE_PROJECT"},
        app_id="42",
        request_id="req-1",
        tool_call_id="req-1:build",
    )
    assert result["durationMs"] == 125
    await gateway.close()


@pytest.mark.asyncio
async def test_tool_specific_invalid_result_becomes_sanitized_protocol_error():
    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"built": "yes", "errorCode": "C:/private/source", "message": "secret"},
                    "message": "ok",
                },
            )
        ),
    )
    with pytest.raises(SpringToolProtocolError) as exc_info:
        await gateway.invoke(
            "project_build",
            {"codeGenType": "VUE_PROJECT"},
            app_id="42",
            request_id="req-1",
            tool_call_id="req-1:build",
        )
    assert str(exc_info.value) == (
        "SPRING_TOOL_PROTOCOL_ERROR: Spring tool response did not match expected schema"
    )
    assert "private" not in str(exc_info.value)
    assert "secret" not in str(exc_info.value)
    await gateway.close()
```

更新现有 fixture：

- `project_build` 成功 data 使用 `{"built": true, "errorCode": "", "message": ""}`；
- `file_read` 成功 data 使用 `{"content": "ok"}`；
- `artifact_publish` 成功 data 补齐 `hashes`；
- 旧 envelope 测试使用合法 `project_build` data；
- 非零业务码继续允许 `data=null`，因为它不进入成功结果 Schema 校验。

- [ ] **Step 3: 运行测试并确认网关尚未执行校验**

Run:

```powershell
Set-Location ai-service
uv run pytest tests/test_gateway_and_config.py -q
```

Expected: 非法请求仍触发 transport，或畸形工具结果被返回，新增断言失败。

- [ ] **Step 4: 接入请求和成功响应校验**

在 `spring_tools.py` 导入契约 API，并新增稳定异常：

```python
from ai_service.models.tool_contract import (
    ToolContractValidationError,
    validate_tool_arguments,
    validate_tool_result,
)


class InvalidSpringToolRequest(ValueError):
    """Python attempted to send a tool request outside the shared contract."""

    def __init__(self):
        super().__init__(
            "INVALID_SPRING_TOOL_REQUEST: Spring tool request did not match expected schema"
        )
```

在创建 `request_body` 前执行：

```python
try:
    validate_tool_arguments(name, arguments)
except ToolContractValidationError as error:
    raise InvalidSpringToolRequest() from error
```

把两个成功返回点统一经过私有方法，避免 BaseResponse 和旧 envelope 漏掉一条路径：

```python
def _validated_result(name: str, data: dict[str, Any]) -> dict[str, Any]:
    try:
        return validate_tool_result(name, data)
    except ToolContractValidationError as error:
        raise SpringToolProtocolError() from error
```

标准响应和精确旧 envelope 均使用 `return _validated_result(name, payload["data"])`。非零业务码必须在该调用之前抛出 `SpringToolError`。

- [ ] **Step 5: 运行 Python 定向和全量测试**

Run:

```powershell
Set-Location ai-service
uv run pytest tests/test_tool_contract.py tests/test_gateway_and_config.py -q
uv run pytest
```

Expected: 定向测试通过；全量 98 项基线加新增测试全部通过，只有既有依赖弃用警告。

- [ ] **Step 6: 提交 Python 运行时边界**

```powershell
git add -- ai-service/src/ai_service/infrastructure/spring_tools.py ai-service/tests/test_gateway_and_config.py
git commit -m "feat: 校验 Spring 工具请求与响应"
```

### Task 3: 用共享 Schema 验证 Java 工具枚举和控制器结果

**Files:**
- Modify: `pom.xml`
- Create: `src/test/java/com/yupi/yuaicodemother/ai/gateway/InternalAiToolSchemaAssertions.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/ai/gateway/InternalAiToolContractTest.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java`

- [ ] **Step 1: 写 Java 失败测试锁定方言和 Schema 完整性**

在 `InternalAiToolContractTest` 增加：

```java
@Test
void compilesDraft202012RequestAndResponseSchemasForEveryTool() throws Exception {
    assertEquals("https://json-schema.org/draft/2020-12/schema",
            InternalAiToolSchemaAssertions.contract().path("schemaDialect").asText());
    for (InternalAiTool tool : InternalAiTool.values()) {
        assertNotNull(InternalAiToolSchemaAssertions.tool(tool.canonicalName()), tool.canonicalName());
        InternalAiToolSchemaAssertions.assertRequestValid(tool.canonicalName(),
                InternalAiToolSchemaAssertions.validRequestExample(tool.canonicalName()));
        InternalAiToolSchemaAssertions.assertResponseValid(tool.canonicalName(),
                InternalAiToolSchemaAssertions.validResponseExample(tool.canonicalName()));
    }
}

@Test
void requestRejectsAdditionalFieldsButResponseAllowsThem() {
    assertThrows(AssertionError.class, () -> InternalAiToolSchemaAssertions.assertRequestValid(
            "project_build", Map.of("codeGenType", "VUE_PROJECT", "appId", 42)));
    InternalAiToolSchemaAssertions.assertResponseValid("project_build", Map.of(
            "built", true, "errorCode", "", "message", "", "durationMs", 125));
}
```

同时扩展现有名称测试，收集所有规范名称与别名，断言任一字符串最多属于一个工具。

- [ ] **Step 2: 运行测试并确认辅助器和 Java 依赖尚不存在**

Run:

```powershell
mvn "-Dtest=InternalAiToolContractTest" test
```

Expected: 测试编译失败，提示 `InternalAiToolSchemaAssertions` 不存在。

- [ ] **Step 3: 增加仅测试使用的 networknt 校验器**

在 `pom.xml` 的测试依赖区域加入：

```xml
<dependency>
    <groupId>com.networknt</groupId>
    <artifactId>json-schema-validator</artifactId>
    <version>1.5.7</version>
    <scope>test</scope>
</dependency>
```

该依赖不得进入生产 scope。

- [ ] **Step 4: 创建 Java 共享测试辅助器**

创建 `InternalAiToolSchemaAssertions.java`，核心实现如下：

```java
package com.yupi.yuaicodemother.ai.gateway;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;

import java.nio.file.Path;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

public final class InternalAiToolSchemaAssertions {
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final JsonSchemaFactory FACTORY =
            JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012);
    private static final JsonNode CONTRACT = loadContract();

    private InternalAiToolSchemaAssertions() { }

    public static JsonNode contract() {
        return CONTRACT;
    }

    public static JsonNode tool(String name) {
        for (JsonNode candidate : CONTRACT.path("tools")) {
            if (name.equals(candidate.path("name").asText())) return candidate;
        }
        return null;
    }

    public static void assertRequestValid(String name, Map<String, Object> value) {
        assertValid(name, "requestSchema", value);
    }

    public static void assertResponseValid(String name, Map<String, Object> value) {
        assertValid(name, "responseSchema", value);
    }

    private static void assertValid(String name, String field, Map<String, Object> value) {
        JsonNode tool = tool(name);
        assertNotNull(tool, name);
        var errors = FACTORY.getSchema(tool.path(field)).validate(MAPPER.valueToTree(value));
        assertTrue(errors.isEmpty(), () -> name + " " + field + ": " + errors);
    }

    private static JsonNode loadContract() {
        try {
            return MAPPER.readTree(Path.of(
                    "ai-service", "src", "ai_service", "contracts", "internal-ai-tools-v1.json").toFile());
        } catch (Exception error) {
            throw new ExceptionInInitializerError(error);
        }
    }

    public static Map<String, Object> validRequestExample(String name) {
        return switch (name) {
            case "dir_read" -> Map.of("relativeDirPath", "", "codeGenType", "VUE_PROJECT");
            case "file_read", "file_delete" -> Map.of("relativeFilePath", "src/App.vue", "codeGenType", "VUE_PROJECT");
            case "file_write" -> Map.of("relativeFilePath", "src/App.vue", "content", "ready", "codeGenType", "VUE_PROJECT");
            case "file_modify" -> Map.of("relativeFilePath", "src/App.vue", "oldContent", "before", "newContent", "after", "codeGenType", "VUE_PROJECT");
            case "artifact_context", "project_build" -> Map.of("codeGenType", name.equals("project_build") ? "VUE_PROJECT" : "HTML");
            case "artifact_validate" -> Map.of("artifact", "candidate", "codeGenType", "HTML");
            case "artifact_publish" -> Map.of("artifact", "candidate", "codeGenType", "HTML", "engine", "langgraph", "finishReason", "STOP");
            default -> throw new IllegalArgumentException(name);
        };
    }

    public static Map<String, Object> validResponseExample(String name) {
        return switch (name) {
            case "dir_read" -> Map.of("entries", java.util.List.of("src/App.vue"));
            case "file_read" -> Map.of("content", "ready");
            case "file_write" -> Map.of("ok", true, "path", "App.vue");
            case "file_modify", "file_delete" -> Map.of("ok", true);
            case "artifact_context" -> Map.of("exists", false, "codeGenType", "HTML");
            case "artifact_validate" -> Map.of("valid", true, "errors", java.util.List.of());
            case "artifact_publish" -> Map.of("published", true, "versionId", "v1", "hashes", Map.of());
            case "project_build" -> Map.of("built", true, "errorCode", "", "message", "");
            default -> throw new IllegalArgumentException(name);
        };
    }
}
```

辅助器只服务测试，不放入 `src/main`。

- [ ] **Step 5: 用共享 Schema 验证控制器实际结果**

在 `InternalAiToolsControllerTest` 静态导入 `assertResponseValid`。对现有真实结果立即校验：

```java
var validation = invoke(controller, "artifact_validate", "HTML", HTML);
assertResponseValid("artifact_validate", validation);

var publication = controller.invoke("Bearer test-token", request).getData();
assertResponseValid("artifact_publish", publication);

assertResponseValid("project_build", result);
assertResponseValid("artifact_context", result);
```

新增一个使用 `@TempDir` 和真实 pass-through idempotency service 的文件工具测试，依次调用并验证：

```java
assertResponseValid("dir_read", invokeTool(controller, "dir_read",
        Map.of("relativeDirPath", "", "codeGenType", "VUE_PROJECT")));
assertResponseValid("file_write", invokeTool(controller, "file_write",
        Map.of("relativeFilePath", "src/App.vue", "content", "before", "codeGenType", "VUE_PROJECT")));
assertResponseValid("file_read", invokeTool(controller, "file_read",
        Map.of("relativeFilePath", "src/App.vue", "codeGenType", "VUE_PROJECT")));
assertResponseValid("file_modify", invokeTool(controller, "file_modify",
        Map.of("relativeFilePath", "src/App.vue", "oldContent", "before", "newContent", "after", "codeGenType", "VUE_PROJECT")));
assertResponseValid("file_delete", invokeTool(controller, "file_delete",
        Map.of("relativeFilePath", "src/Old.vue", "codeGenType", "VUE_PROJECT")));
```

测试 resolver 必须让 `resolveActiveRoot(VUE_PROJECT, 42L)` 返回 `tempDir`。在调用 `file_delete` 前创建 `src/Old.vue`。新增辅助方法只组装顶层 `ToolRequest`，不绕过控制器：

```java
private Map<String, Object> invokeTool(
        InternalAiToolsController controller,
        String name,
        Map<String, Object> arguments) {
    return controller.invoke("Bearer test-token", new InternalAiToolsController.ToolRequest(
            42L, "req-schema", UUID.randomUUID().toString(), name, arguments)).getData();
}
```

- [ ] **Step 6: 运行 Java 契约和控制器测试**

Run:

```powershell
mvn "-Dtest=InternalAiToolContractTest,InternalAiToolsControllerTest" test
```

Expected: 全部通过；九个规范工具都有可编译 Schema，控制器真实结果符合共享响应契约。

- [ ] **Step 7: 提交 Java 测试边界**

```powershell
git add -- pom.xml src/test/java/com/yupi/yuaicodemother/ai/gateway/InternalAiToolSchemaAssertions.java src/test/java/com/yupi/yuaicodemother/ai/gateway/InternalAiToolContractTest.java src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java
git commit -m "test: 校验 Java 内部工具共享契约"
```

### Task 4: 更新文档并完成离线验收

**Files:**
- Modify: `ai-service/README.md`
- Modify: `doc/ai-service-phase-one-handoff.md`

- [ ] **Step 1: 更新 AI 服务说明**

在 `ai-service/README.md` 的 Spring 工具网关章节增加：

```markdown
内部工具名称、模型权限、请求参数和成功响应由 `contracts/internal-ai-tools-v1.json` 统一定义。Python 在发送请求前执行严格参数校验，未知字段不会到达 Spring；Spring 成功结果返回后按对应工具 Schema 校验。请求不允许额外字段，响应允许新增字段以支持滚动升级。Schema 错误只返回稳定协议错误，不包含源码、路径、参数值或响应正文。
```

- [ ] **Step 2: 更新交接风险和验证边界**

在 `doc/ai-service-phase-one-handoff.md`：

- 将 P1“工具参数和返回值依赖运行时 Map、没有共享 Schema”更新为已完成共享 Draft 2020-12 契约；
- 说明 Python 执行运行时请求/响应校验，Java 生产端仍执行原有业务校验，Java Schema 仅用于测试；
- 记录本轮实际测试数量和命令；
- 保留真实 Spring/Python 网络、Redis 和三类型生成仍未执行的事实；
- 不改写用户在主工作树中的既有交接文档编辑，合并时必须保留并组合两侧内容。

- [ ] **Step 3: 运行完整 Python 验证**

Run:

```powershell
Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

Expected: compileall 和 lock check 退出码为 0；pytest 基线 98 项加新增测试全部通过，允许既有的两条依赖弃用警告。

- [ ] **Step 4: 运行 Java 定向测试与干净编译**

Run:

```powershell
Set-Location ..
mvn "-Dtest=InternalAiToolContractTest,InternalAiToolsControllerTest" test
mvn clean -DskipTests compile
```

Expected: 定向测试 0 failures/errors；Java 21 干净编译 236 个源文件成功。既有 varargs、弃用和 unchecked 警告可记录但不得误报为本轮新增失败。

- [ ] **Step 5: 检查差异、敏感信息和任务范围**

Run:

```powershell
git diff --check
git status --short
rg -n "Bearer |sk-|api[_-]?key|C:/private|D:/" ai-service/src/ai_service/contracts ai-service/src/ai_service/models/tool_contract.py ai-service/src/ai_service/infrastructure/spring_tools.py
```

Expected: `git diff --check` 无输出；只出现本计划列出的任务文件；敏感信息扫描不出现真实令牌、API Key、私人绝对路径或源码样例泄露。

- [ ] **Step 6: 提交文档与验证基线**

```powershell
git add -- ai-service/README.md doc/ai-service-phase-one-handoff.md
git commit -m "docs: 记录内部工具 Schema 校验边界"
```

## 真实环境后续验收点

本计划不执行真实环境请求，但后续人工验收必须包含：

1. 启动 Spring 与 Python 后执行一个合法 `file_read`，确认请求和响应通过 Schema。
2. 使用隔离测试入口发送缺少 `relativeFilePath` 或包含额外字段的请求，确认 Python 在 HTTP 前拒绝。
3. 临时测试桩返回 `built="true"` 或缺少 `errorCode`，确认 Python 返回脱敏协议错误。
4. 验证 `artifact_publish` 网络丢失重试仍使用原 `toolCallId`，合法结果必须包含 `published`、`versionId` 和 `hashes`。
5. 完成 HTML、MULTI_FILE、VUE_PROJECT 首次生成和二次修改，确认 Schema 不改变文件、构建、发布和公共 SSE 行为。
6. 真实 Redis、多实例幂等和模型验收继续使用既有验收脚本，本轮 Schema 结果不能替代这些证据。
