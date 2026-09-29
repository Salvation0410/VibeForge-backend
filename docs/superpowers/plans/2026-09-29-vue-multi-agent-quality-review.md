# Vue Multi-Agent Quality Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `VUE_PROJECT` 的质量审查增加三个并行只读 Reviewer 和确定性聚合器，将 critical/major 问题作为有界反馈传给现有 repair 节点，同时保持其他生成类型、公开 SSE、Spring 文件所有权和两次修复上限不变。

**Architecture:** 保留现有 `GenerationModel.review()` 供 HTML、MULTI_FILE 和关闭开关时使用，新增按角色返回结构化结果的 `review_role()`。独立的 `multi_agent_review` 编排模块使用 `asyncio.TaskGroup` 并行执行 requirement、function、technical 三个 Reviewer，严格校验输出后用纯 Python 聚合；`workflow.py` 只负责取得 Spring 有界源码快照、调用协调器、写入有界状态并把阻断反馈交给现有 repair。

**Tech Stack:** Python 3.12、FastAPI、LangChain、LangGraph、Pydantic v2、pytest、pytest-asyncio、uv。

---

## 已锁定的实施决策

- **B1：** 第一阶段仅覆盖 `VUE_PROJECT`，HTML 和 MULTI_FILE 保持现有单 Reviewer。
- **F1：** 任一 Reviewer 超时、模型异常或输出非法时整次生成失败，不回退、不使用部分结果、不进入 repair。
- **S1：** critical 和 major 触发 repair；minor 只记录，不消耗修复次数。
- 功能开关默认关闭，三个 Reviewer 共享 60 秒整体超时且不自动重试。
- 不启用长期记忆，不引入 `PostgresStore`，不修改 PostgreSQL checkpoint schema。

---

## 文件结构

新增文件：

- `ai-service/src/ai_service/models/quality_review.py`：Reviewer 角色、严重度、结构化结果、输出异常和字段上限。
- `ai-service/src/ai_service/orchestration/multi_agent_review.py`：三方并发、整体超时、确定性聚合和 repair feedback 格式化。
- `ai-service/tests/test_quality_review.py`：领域模型、重复归并、严重度和长度边界测试。
- `ai-service/tests/test_multi_agent_review.py`：并发、超时、兄弟任务取消和错误映射测试。

修改文件：

- `ai-service/src/ai_service/config.py`
- `ai-service/src/ai_service/models/base.py`
- `ai-service/src/ai_service/models/openai_compatible.py`
- `ai-service/src/ai_service/prompts/__init__.py`
- `ai-service/src/ai_service/prompts/review.py`
- `ai-service/src/ai_service/orchestration/workflow.py`
- `ai-service/tests/conftest.py`
- `ai-service/tests/test_openai_compatible.py`
- `ai-service/tests/test_api.py`
- `ai-service/tests/test_gateway_and_config.py`
- `ai-service/tests/test_prompts.py`
- `ai-service/tests/test_package_structure.py`
- `ai-service/.env.example`
- `ai-service/README.md`
- `doc/ai-service-phase-one-handoff.md`

不修改 Spring、前端、`internal-ai-tools-v1.json`、PostgreSQL 表结构和数据库迁移文件。

---

### Task 1: 定义结构化审查契约和确定性聚合规则

**Files:**
- Create: `ai-service/src/ai_service/models/quality_review.py`
- Create: `ai-service/tests/test_quality_review.py`

- [ ] **Step 1: 写领域模型和聚合行为的失败测试**

创建 `ai-service/tests/test_quality_review.py`，至少包含以下测试：

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_service.models.quality_review import (
    IssueSeverity,
    QualityIssue,
    QualityReviewOutputError,
    ReviewerResult,
    ReviewerRole,
)
from ai_service.orchestration.multi_agent_review import aggregate_review_results


def issue(code: str, severity: IssueSeverity, summary: str = "问题") -> QualityIssue:
    return QualityIssue(
        code=code,
        category="functionality",
        severity=severity,
        summary=summary,
        evidence="源码中缺少对应行为",
        repair_hint="补充对应行为并保留其他功能",
    )


def result(role: ReviewerRole, *issues: QualityIssue) -> ReviewerResult:
    return ReviewerResult(reviewer=role, summary="审查完成", issues=list(issues))


def test_aggregate_passes_when_reviewers_report_no_blocking_issues():
    review = aggregate_review_results(
        [
            result(ReviewerRole.REQUIREMENT),
            result(ReviewerRole.FUNCTION, issue("FUNC_MINOR", IssueSeverity.MINOR)),
            result(ReviewerRole.TECHNICAL),
        ]
    )

    assert review.passed is True
    assert review.blocking_issues == []
    assert [item.code for item in review.minor_issues] == ["FUNC_MINOR"]
    assert review.repair_feedback == ""


@pytest.mark.parametrize("severity", [IssueSeverity.CRITICAL, IssueSeverity.MAJOR])
def test_aggregate_blocks_on_critical_or_major(severity: IssueSeverity):
    review = aggregate_review_results(
        [
            result(ReviewerRole.REQUIREMENT, issue("REQ_1", severity)),
            result(ReviewerRole.FUNCTION),
            result(ReviewerRole.TECHNICAL),
        ]
    )

    assert review.passed is False
    assert [item.code for item in review.blocking_issues] == ["REQ_1"]
    assert "REQ_1" in review.repair_feedback


def test_aggregate_deduplicates_and_keeps_highest_severity():
    minor = issue("SHARED", IssueSeverity.MINOR, "表单没有提交行为")
    critical = issue("SHARED", IssueSeverity.CRITICAL, "表单没有提交行为")

    review = aggregate_review_results(
        [
            result(ReviewerRole.REQUIREMENT, minor),
            result(ReviewerRole.FUNCTION, critical),
            result(ReviewerRole.TECHNICAL),
        ]
    )

    assert len(review.blocking_issues) == 1
    assert review.blocking_issues[0].severity is IssueSeverity.CRITICAL


def test_aggregate_rejects_missing_or_duplicate_roles():
    with pytest.raises(QualityReviewOutputError, match="MULTI_AGENT_REVIEW_INVALID_OUTPUT"):
        aggregate_review_results(
            [
                result(ReviewerRole.REQUIREMENT),
                result(ReviewerRole.REQUIREMENT),
                result(ReviewerRole.TECHNICAL),
            ]
        )


def test_reviewer_result_rejects_more_than_five_issues():
    with pytest.raises(ValidationError):
        ReviewerResult(
            reviewer=ReviewerRole.FUNCTION,
            summary="审查完成",
            issues=[issue(f"FUNC_{index}", IssueSeverity.MINOR) for index in range(6)],
        )


def test_issue_rejects_oversized_fields():
    with pytest.raises(ValidationError):
        issue("X" * 65, IssueSeverity.MAJOR)
```

- [ ] **Step 2: 运行测试并确认因模块尚不存在而失败**

Run:

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother/ai-service
uv run pytest tests/test_quality_review.py -q
```

Expected: FAIL，提示 `ai_service.models.quality_review` 或 `ai_service.orchestration.multi_agent_review` 无法导入。

- [ ] **Step 3: 实现领域模型和聚合器最小版本**

在 `models/quality_review.py` 定义：

```python
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ReviewerRole(StrEnum):
    REQUIREMENT = "requirement"
    FUNCTION = "function"
    TECHNICAL = "technical"


class IssueSeverity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class QualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=64)
    category: str = Field(min_length=1, max_length=64)
    severity: IssueSeverity
    summary: str = Field(min_length=1, max_length=300)
    evidence: str = Field(min_length=1, max_length=600)
    repair_hint: str = Field(min_length=1, max_length=600)


class ReviewerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reviewer: ReviewerRole
    summary: str = Field(min_length=1, max_length=300)
    issues: list[QualityIssue] = Field(default_factory=list, max_length=5)


class QualityReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    reviewer_results: list[ReviewerResult] = Field(min_length=3, max_length=3)
    blocking_issues: list[QualityIssue] = Field(max_length=12)
    minor_issues: list[QualityIssue] = Field(max_length=12)
    repair_feedback: str = Field(max_length=4000)


class QualityReviewOutputError(ValueError):
    """模型审查输出不满足结构、身份或有界长度要求。"""
```

在 `orchestration/multi_agent_review.py` 先实现不含并发的聚合函数：

```python
from __future__ import annotations

import re

from ai_service.models.quality_review import (
    IssueSeverity,
    QualityIssue,
    QualityReviewOutputError,
    QualityReviewResult,
    ReviewerResult,
    ReviewerRole,
)

_SEVERITY_RANK = {
    IssueSeverity.MINOR: 0,
    IssueSeverity.MAJOR: 1,
    IssueSeverity.CRITICAL: 2,
}


def _normalized_summary(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _issue_key(issue: QualityIssue) -> tuple[str, str, str]:
    return (issue.code.casefold(), issue.category.casefold(), _normalized_summary(issue.summary))


def _repair_feedback(issues: list[QualityIssue]) -> str:
    if not issues:
        return ""
    lines = ["质量审查未通过，请修复以下阻断问题，并保持未提及的现有功能不变。"]
    for index, item in enumerate(issues, 1):
        lines.extend(
            [
                "",
                f"{index}. [{item.severity.value}/{item.code}]",
                f"问题：{item.summary}",
                f"证据：{item.evidence}",
                f"修复要求：{item.repair_hint}",
            ]
        )
    feedback = "\n".join(lines)
    if len(feedback) > 4000:
        raise QualityReviewOutputError(
            "MULTI_AGENT_REVIEW_INVALID_OUTPUT: repair feedback exceeds 4000 characters"
        )
    return feedback


def aggregate_review_results(results: list[ReviewerResult]) -> QualityReviewResult:
    expected = set(ReviewerRole)
    actual = [item.reviewer for item in results]
    if len(results) != 3 or set(actual) != expected or len(set(actual)) != 3:
        raise QualityReviewOutputError(
            "MULTI_AGENT_REVIEW_INVALID_OUTPUT: expected exactly one result per reviewer"
        )

    deduplicated: dict[tuple[str, str, str], QualityIssue] = {}
    for reviewer_result in results:
        for issue in reviewer_result.issues:
            key = _issue_key(issue)
            current = deduplicated.get(key)
            if current is None or _SEVERITY_RANK[issue.severity] > _SEVERITY_RANK[current.severity]:
                deduplicated[key] = issue
    if len(deduplicated) > 12:
        raise QualityReviewOutputError(
            "MULTI_AGENT_REVIEW_INVALID_OUTPUT: aggregated issues exceed 12"
        )

    issues = list(deduplicated.values())
    blocking = [item for item in issues if item.severity is not IssueSeverity.MINOR]
    minor = [item for item in issues if item.severity is IssueSeverity.MINOR]
    return QualityReviewResult(
        passed=not blocking,
        reviewer_results=results,
        blocking_issues=blocking,
        minor_issues=minor,
        repair_feedback=_repair_feedback(blocking),
    )
```

- [ ] **Step 4: 运行领域测试并修正类型或边界错误**

Run:

```powershell
uv run pytest tests/test_quality_review.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交领域契约**

```powershell
git add ai-service/src/ai_service/models/quality_review.py ai-service/src/ai_service/orchestration/multi_agent_review.py ai-service/tests/test_quality_review.py
git commit -m "feat: 定义多 Agent 质量审查契约"
```

---

### Task 2: 增加三个 Reviewer 提示词和模型适配

**Files:**
- Modify: `ai-service/src/ai_service/prompts/review.py`
- Modify: `ai-service/src/ai_service/prompts/__init__.py`
- Modify: `ai-service/src/ai_service/models/base.py`
- Modify: `ai-service/src/ai_service/models/openai_compatible.py`
- Modify: `ai-service/tests/test_prompts.py`
- Modify: `ai-service/tests/test_openai_compatible.py`

- [ ] **Step 1: 写角色提示词和 JSON 解析失败测试**

在 `test_prompts.py` 增加参数化测试，要求每个提示词：

```python
@pytest.mark.parametrize("role", ["requirement", "function", "technical"])
def test_role_review_prompt_requires_bounded_json_without_tools(role):
    prompt = quality_review_system_prompt(role)

    assert '"reviewer"' in prompt
    assert '"issues"' in prompt
    assert "critical" in prompt
    assert "major" in prompt
    assert "minor" in prompt
    assert "最多 5 条" in prompt
    assert "不得调用工具" in prompt
    assert "只返回 JSON" in prompt
```

在 `test_openai_compatible.py` 增加：

```python
@pytest.mark.asyncio
async def test_role_review_parses_strict_json_result():
    model = model_with_response(
        '{"reviewer":"requirement","summary":"完成","issues":[]}'
    )

    result = await model.review_role(
        ReviewerRole.REQUIREMENT,
        "snapshot",
        {"prompt": "做一个搜索页面"},
    )

    assert result.reviewer is ReviewerRole.REQUIREMENT
    assert model._client.messages[0].content == quality_review_system_prompt("requirement")


@pytest.mark.asyncio
async def test_role_review_rejects_invalid_json_without_leaking_raw_content():
    model = model_with_response("not-json-secret-source")

    with pytest.raises(QualityReviewOutputError) as error:
        await model.review_role(
            ReviewerRole.FUNCTION,
            "snapshot",
            {"prompt": "检查功能"},
        )

    assert str(error.value).startswith("MULTI_AGENT_REVIEW_INVALID_OUTPUT:")
    assert "not-json-secret-source" not in str(error.value)


@pytest.mark.asyncio
async def test_role_review_rejects_reviewer_identity_mismatch():
    model = model_with_response(
        '{"reviewer":"technical","summary":"完成","issues":[]}'
    )

    with pytest.raises(QualityReviewOutputError, match="reviewer identity mismatch"):
        await model.review_role(
            ReviewerRole.REQUIREMENT,
            "snapshot",
            {"prompt": "检查需求"},
        )
```

- [ ] **Step 2: 运行提示词和模型测试确认失败**

```powershell
uv run pytest tests/test_prompts.py tests/test_openai_compatible.py -q
```

Expected: FAIL，提示 `quality_review_system_prompt` 或 `review_role` 尚不存在。

- [ ] **Step 3: 实现角色提示词工厂**

保留现有 `QUALITY_REVIEW_SYSTEM_PROMPT`，新增 `quality_review_system_prompt(role: str)`。三个角色共享严格 JSON schema 和严重度定义，角色差异只放在职责段。提示词明确：

```text
- 只审查给定需求、构建摘要和源码快照；
- 不得调用工具；
- 不得建议与用户目标无关的重构；
- critical 表示核心流程不可用或严重违背用户目标；
- major 表示明确功能缺失、错误或高概率运行问题；
- minor 表示不影响核心目标的局部质量问题；
- 最多返回 5 条问题；
- 只返回 JSON，不使用 Markdown 代码围栏。
```

导出函数：

```python
from ai_service.prompts.review import (
    QUALITY_REVIEW_SYSTEM_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    quality_review_system_prompt,
)
```

- [ ] **Step 4: 扩展模型协议并解析严格结果**

在 `GenerationModel` 中保留 `review()`，新增：

```python
async def review_role(
    self,
    role: ReviewerRole,
    artifact: str,
    context: dict[str, Any],
) -> ReviewerResult:
    """以指定只读角色审查 Vue 源码快照并返回结构化结果。"""
    raise NotImplementedError
```

在 `OpenAICompatibleModel` 中实现：

```python
async def review_role(
    self,
    role: ReviewerRole,
    artifact: str,
    context: dict[str, Any],
) -> ReviewerResult:
    response = await self._client.ainvoke(
        [
            SystemMessage(content=quality_review_system_prompt(role.value)),
            HumanMessage(content=json.dumps({"artifact": artifact, **context}, ensure_ascii=False)),
        ]
    )
    try:
        payload = json.loads(_strip_json_fence(str(response.content)))
        result = ReviewerResult.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise QualityReviewOutputError(
            "MULTI_AGENT_REVIEW_INVALID_OUTPUT: reviewer returned invalid JSON"
        ) from exc
    if result.reviewer is not role:
        raise QualityReviewOutputError(
            "MULTI_AGENT_REVIEW_INVALID_OUTPUT: reviewer identity mismatch"
        )
    return result
```

不要把原始模型内容拼入异常消息或日志。

- [ ] **Step 5: 运行提示词和模型适配测试**

```powershell
uv run pytest tests/test_prompts.py tests/test_openai_compatible.py -q
```

Expected: PASS；现有 HTML、MULTI_FILE 和 Vue 生成/修复提示词测试继续通过。

- [ ] **Step 6: 提交提示词和模型适配**

```powershell
git add ai-service/src/ai_service/prompts/review.py ai-service/src/ai_service/prompts/__init__.py ai-service/src/ai_service/models/base.py ai-service/src/ai_service/models/openai_compatible.py ai-service/tests/test_prompts.py ai-service/tests/test_openai_compatible.py
git commit -m "feat: 增加多角色质量审查模型接口"
```

---

### Task 3: 实现并发协调器、超时和 F1 失败策略

**Files:**
- Modify: `ai-service/src/ai_service/orchestration/multi_agent_review.py`
- Create: `ai-service/tests/test_multi_agent_review.py`

- [ ] **Step 1: 写并发、超时和取消测试**

测试模型使用 `asyncio.Event` 控制三个调用，不依赖真实模型：

```python
from __future__ import annotations

import asyncio

import pytest

from ai_service.models.quality_review import ReviewerResult, ReviewerRole
from ai_service.orchestration.multi_agent_review import (
    MultiAgentReviewError,
    run_multi_agent_review,
)


class CoordinatedReviewModel:
    def __init__(self):
        self.started = {role: asyncio.Event() for role in ReviewerRole}
        self.release = asyncio.Event()

    async def review_role(self, role, artifact, context):
        self.started[role].set()
        await self.release.wait()
        return ReviewerResult(reviewer=role, summary="完成", issues=[])


@pytest.mark.asyncio
async def test_all_reviewers_start_before_any_one_finishes():
    model = CoordinatedReviewModel()
    task = asyncio.create_task(
        run_multi_agent_review(model, "snapshot", {"prompt": "build"}, timeout_seconds=1)
    )

    await asyncio.gather(*(event.wait() for event in model.started.values()))
    assert not task.done()
    model.release.set()

    result = await task
    assert result.passed is True


@pytest.mark.asyncio
async def test_timeout_raises_stable_error_and_cancels_reviewers():
    model = CoordinatedReviewModel()

    with pytest.raises(MultiAgentReviewError, match="MULTI_AGENT_REVIEW_TIMEOUT"):
        await run_multi_agent_review(
            model,
            "snapshot",
            {"prompt": "build"},
            timeout_seconds=0.01,
        )


@pytest.mark.asyncio
async def test_invalid_reviewer_output_becomes_invalid_output_error():
    class InvalidModel:
        async def review_role(self, role, artifact, context):
            if role is ReviewerRole.FUNCTION:
                raise QualityReviewOutputError(
                    "MULTI_AGENT_REVIEW_INVALID_OUTPUT: invalid JSON"
                )
            await asyncio.sleep(10)

    with pytest.raises(MultiAgentReviewError, match="MULTI_AGENT_REVIEW_INVALID_OUTPUT"):
        await run_multi_agent_review(
            InvalidModel(), "snapshot", {"prompt": "build"}, timeout_seconds=1
        )


@pytest.mark.asyncio
async def test_parent_cancellation_is_not_wrapped():
    model = CoordinatedReviewModel()
    task = asyncio.create_task(
        run_multi_agent_review(model, "snapshot", {"prompt": "build"}, timeout_seconds=10)
    )
    await asyncio.gather(*(event.wait() for event in model.started.values()))

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
```

同时增加一个普通模型异常测试，断言错误码为 `MULTI_AGENT_REVIEW_MODEL_ERROR`，且异常文本不包含模型输入或源码内容。

- [ ] **Step 2: 运行协调器测试确认失败**

```powershell
uv run pytest tests/test_multi_agent_review.py -q
```

Expected: FAIL，提示 `run_multi_agent_review` 或 `MultiAgentReviewError` 尚不存在。

- [ ] **Step 3: 实现 TaskGroup 并发和错误映射**

在 `multi_agent_review.py` 增加：

```python
import asyncio
from typing import Any

from ai_service.models.base import GenerationModel


class MultiAgentReviewError(RuntimeError):
    """多 Agent 审查的稳定、脱敏工作流错误。"""


async def run_multi_agent_review(
    model: GenerationModel,
    artifact: str,
    context: dict[str, Any],
    *,
    timeout_seconds: float,
) -> QualityReviewResult:
    tasks: dict[ReviewerRole, asyncio.Task[ReviewerResult]] = {}
    try:
        async with asyncio.timeout(timeout_seconds):
            async with asyncio.TaskGroup() as group:
                for role in ReviewerRole:
                    tasks[role] = group.create_task(
                        model.review_role(role, artifact, dict(context)),
                        name=f"quality-review-{role.value}",
                    )
    except asyncio.CancelledError:
        raise
    except TimeoutError as exc:
        raise MultiAgentReviewError(
            "MULTI_AGENT_REVIEW_TIMEOUT: quality reviewers exceeded the configured timeout"
        ) from exc
    except ExceptionGroup as group:
        flattened = list(_leaf_exceptions(group))
        invalid = next(
            (item for item in flattened if isinstance(item, QualityReviewOutputError)),
            None,
        )
        if invalid is not None:
            raise MultiAgentReviewError(str(invalid)) from invalid
        raise MultiAgentReviewError(
            "MULTI_AGENT_REVIEW_MODEL_ERROR: quality reviewer call failed"
        ) from group

    return aggregate_review_results([tasks[role].result() for role in ReviewerRole])
```

实现 `_leaf_exceptions()` 递归展开嵌套 `BaseExceptionGroup`，但只映射异常类型，不能把未脱敏的下游异常正文复制到外部消息。

- [ ] **Step 4: 运行协调器和聚合测试**

```powershell
uv run pytest tests/test_multi_agent_review.py tests/test_quality_review.py -q
```

Expected: PASS，无挂起任务警告。

- [ ] **Step 5: 提交协调器**

```powershell
git add ai-service/src/ai_service/orchestration/multi_agent_review.py ai-service/tests/test_multi_agent_review.py
git commit -m "feat: 并发执行多 Agent 质量审查"
```

---

### Task 4: 增加功能开关和超时配置

**Files:**
- Modify: `ai-service/src/ai_service/config.py`
- Modify: `ai-service/tests/test_gateway_and_config.py`
- Modify: `ai-service/.env.example`

- [ ] **Step 1: 写配置默认值和边界测试**

在 `test_gateway_and_config.py` 增加：

```python
def test_multi_agent_review_is_disabled_by_default():
    settings = Settings(
        internal_bearer_token="internal-token",
        spring_gateway_base_url="http://spring.test",
        spring_gateway_bearer_token="gateway-token",
    )

    assert settings.multi_agent_review_enabled is False
    assert settings.multi_agent_review_timeout_seconds == 60.0


@pytest.mark.parametrize("timeout", [0, -1, 301])
def test_multi_agent_review_timeout_rejects_invalid_bounds(timeout):
    with pytest.raises(ValidationError):
        Settings(
            internal_bearer_token="internal-token",
            spring_gateway_base_url="http://spring.test",
            spring_gateway_bearer_token="gateway-token",
            multi_agent_review_timeout_seconds=timeout,
        )
```

- [ ] **Step 2: 运行配置测试确认失败**

```powershell
uv run pytest tests/test_gateway_and_config.py -q
```

Expected: FAIL，提示 Settings 没有对应字段。

- [ ] **Step 3: 增加配置字段**

在 `Settings` 中增加：

```python
multi_agent_review_enabled: bool = False
multi_agent_review_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
```

在 `.env.example` 增加：

```dotenv
# Vue 项目三角色并行质量审查；第一阶段默认关闭
AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED=false
# 三个 Reviewer 共享的整体超时，单位秒
AI_SERVICE_MULTI_AGENT_REVIEW_TIMEOUT_SECONDS=60
```

- [ ] **Step 4: 运行配置测试**

```powershell
uv run pytest tests/test_gateway_and_config.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交配置**

```powershell
git add ai-service/src/ai_service/config.py ai-service/tests/test_gateway_and_config.py ai-service/.env.example
git commit -m "feat: 增加多 Agent 审查配置"
```

---

### Task 5: 将 Vue 工作流接入真实源码快照和 repair feedback

**Files:**
- Modify: `ai-service/src/ai_service/orchestration/workflow.py`
- Modify: `ai-service/tests/conftest.py`
- Modify: `ai-service/tests/test_api.py`

- [ ] **Step 1: 扩展 FakeModel 支持角色审查**

在 `tests/conftest.py`：

```python
from ai_service.models.quality_review import ReviewerResult, ReviewerRole


class FakeModel:
    def __init__(
        self,
        *,
        reviews: list[bool] | None = None,
        role_reviews: dict[ReviewerRole, list[ReviewerResult]] | None = None,
        vue_tool_calls: int = 0,
    ):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.reviews = list(reviews or [True])
        self.vue_tool_calls = vue_tool_calls
        self._vue_turn = 0
        self.role_reviews = {
            role: list((role_reviews or {}).get(role, []))
            for role in ReviewerRole
        }

    async def review_role(self, role, artifact, context):
        self.calls.append(
            ("review_role", {"role": role, "artifact": artifact, "context": context})
        )
        queued = self.role_reviews[role]
        if queued:
            return queued.pop(0)
        return ReviewerResult(reviewer=role, summary="审查通过", issues=[])
```

保留现有 `review()`，确保静态生成和关闭开关路径不受影响。

- [ ] **Step 2: 写工作流失败测试**

在 `test_api.py` 增加以下场景：

1. 开关关闭时 Vue 仍只调用一次 `review()`，首次生成不新增快照调用。
2. 开关开启时首次 Vue 构建后调用一次 `vue_source_snapshot`，ID 为 `req-1:vue-source-snapshot:0`。
3. 三个 `review_role` 调用的 artifact 都包含快照文件内容，不包含普通模型回复替代品。
4. requirement 返回 major 后进入 repair，repair context 的 `qualityReview.repairFeedback` 包含问题 code 和修复要求。
5. 只有 minor 时直接 completed，`repair` 调用次数为零。
6. Reviewer 返回 major、第一次 repair 后，再次调用快照 ID `:1` 并重新执行三个角色。
7. 两次 repair 后仍有阻断问题时进入 failed，修复次数仍为 2。
8. 快照失败时不启动任何 Reviewer，错误码为 `MULTI_AGENT_REVIEW_SNAPSHOT_ERROR`。
9. Reviewer 异常时不调用 repair，错误码保持对应稳定码。
10. NDJSON 事件中不出现快照文件内容、evidence 或 repair feedback 全文。

用于 major 场景的测试结果：

```python
blocking_result = ReviewerResult(
    reviewer=ReviewerRole.REQUIREMENT,
    summary="缺少搜索功能",
    issues=[
        QualityIssue(
            code="REQ_MISSING_SEARCH",
            category="requirements",
            severity=IssueSeverity.MAJOR,
            summary="搜索功能未实现",
            evidence="源码只有输入框，没有搜索处理逻辑",
            repair_hint="补充搜索触发和结果过滤",
        )
    ],
)
```

- [ ] **Step 3: 运行新增工作流测试确认失败**

```powershell
uv run pytest tests/test_api.py -q -k "multi_agent or role_review or initial_vue_snapshot"
```

Expected: FAIL，当前工作流不会在首次生成时获取快照，也不会调用 `review_role()`。

- [ ] **Step 4: 扩展 WorkflowState**

增加有界状态字段：

```python
reviewer_results: list[dict[str, Any]]
quality_issues: list[dict[str, Any]]
repair_feedback: str
```

业务摘要 `_checkpoint_payload()` 不增加这些字段，继续只保存节点、请求标识、生成类型、通过状态、修复次数和工具次数。

- [ ] **Step 5: 接入开启和关闭两条审查路径**

在 `quality_review()` 中：

```python
multi_agent_enabled = (
    state["code_gen_type"] == "VUE_PROJECT"
    and self.settings.multi_agent_review_enabled
)
uses_vue_snapshot = (
    state["code_gen_type"] == "VUE_PROJECT"
    and state.get("build", {}).get("built") is True
    and (multi_agent_enabled or repair_count > 0)
)
```

获取快照异常时仅在多 Agent 路径包装为：

```python
raise RuntimeError(
    "MULTI_AGENT_REVIEW_SNAPSHOT_ERROR: Vue source snapshot is unavailable"
) from exc
```

多 Agent 路径调用：

```python
result = await run_multi_agent_review(
    self.model,
    review_artifact,
    review_context,
    timeout_seconds=self.settings.multi_agent_review_timeout_seconds,
)
self._raise_if_cancelled(thread_id)
return {
    "quality_passed": result.passed,
    "reviewer_results": [item.model_dump(mode="json") for item in result.reviewer_results],
    "quality_issues": [
        item.model_dump(mode="json")
        for item in [*result.blocking_issues, *result.minor_issues]
    ],
    "repair_feedback": result.repair_feedback,
}
```

关闭开关、HTML 和 MULTI_FILE 继续执行现有 `self.model.review()`。

- [ ] **Step 6: 将阻断反馈传入 repair 并避免污染下一轮审查上下文**

在 `repair_context` 中增加：

```python
"qualityReview": {
    "repairFeedback": state.get("repair_feedback", ""),
},
```

Vue repair 工具循环结束后，从下一轮常规上下文中移除临时质量反馈：

```python
next_context = dict(result["context"])
next_context.pop("qualityReview", None)
```

返回 `next_context`，并清空上一轮审查状态：

```python
"context": next_context,
"reviewer_results": [],
"quality_issues": [],
"repair_feedback": "",
```

- [ ] **Step 7: 运行 Vue 工作流和现有 API 测试**

```powershell
uv run pytest tests/test_api.py -q
```

Expected: PASS；现有取消、快照、两次修复、HTML 发布和 MULTI_FILE 发布测试不回归。

- [ ] **Step 8: 提交工作流接入**

```powershell
git add ai-service/src/ai_service/orchestration/workflow.py ai-service/tests/conftest.py ai-service/tests/test_api.py
git commit -m "feat: 接入 Vue 多 Agent 质量审查"
```

---

### Task 6: 补充取消、checkpoint、包结构和泄漏回归测试

**Files:**
- Modify: `ai-service/tests/test_api.py`
- Modify: `ai-service/tests/test_package_structure.py`
- Modify: `ai-service/tests/test_multi_agent_review.py`

- [ ] **Step 1: 写活跃审查取消测试**

创建一个 Reviewer 调用全部阻塞的 Fake Model，启动流式生成，等待三个角色全部进入后调用现有取消入口。断言：

```python
assert events[-1]["type"] == "failed"
assert events[-1]["error"]["code"] == "cancelled"
assert not [call for call in model.calls if call[0] == "repair"]
assert checkpoint.cleaned_graph_threads == ["42:req-review-cancel"]
```

同时记录三个角色收到 `CancelledError`，断言全部为真。

- [ ] **Step 2: 写业务 checkpoint 脱敏测试**

使用包含唯一敏感标记的 evidence 和 repair hint 执行一次阻断审查，检查 `MemoryCheckpoint.saved` 的序列化文本：

```python
serialized = json.dumps(checkpoint.saved, ensure_ascii=False)
assert "private-review-evidence" not in serialized
assert "private-repair-hint" not in serialized
assert "qualityPassed" in serialized
```

该测试验证 `_checkpoint_payload()`，不声称 LangGraph 官方 graph checkpoint 不保存恢复所需的有界状态。

- [ ] **Step 3: 写包导入测试**

在 `test_package_structure.py` 增加：

```python
from ai_service.models import quality_review
from ai_service.orchestration import multi_agent_review

assert quality_review.ReviewerRole
assert multi_agent_review.run_multi_agent_review
```

- [ ] **Step 4: 运行定向回归测试**

```powershell
uv run pytest tests/test_multi_agent_review.py tests/test_package_structure.py tests/test_api.py -q
```

Expected: PASS，无 pending task、未捕获 ExceptionGroup 或源码泄漏断言失败。

- [ ] **Step 5: 提交回归测试**

```powershell
git add ai-service/tests/test_api.py ai-service/tests/test_package_structure.py ai-service/tests/test_multi_agent_review.py
git commit -m "test: 覆盖多 Agent 审查取消与脱敏"
```

---

### Task 7: 同步文档、执行全量验证并记录人工测试入口

**Files:**
- Modify: `ai-service/README.md`
- Modify: `doc/ai-service-phase-one-handoff.md`

- [ ] **Step 1: 更新 AI 服务 README**

增加“Vue 多 Agent 质量审查”章节，明确：

- 默认关闭及两个环境变量。
- 仅适用于 `VUE_PROJECT`。
- 三个 Reviewer 的职责。
- critical/major 触发 repair，minor 不触发。
- 任一 Reviewer 系统异常直接失败。
- 不启用长期记忆，不使用 `PostgresStore`。
- 本地开启示例：

```dotenv
AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED=true
AI_SERVICE_MULTI_AGENT_REVIEW_TIMEOUT_SECONDS=60
```

- [ ] **Step 2: 同步阶段交接文档**

在 `doc/ai-service-phase-one-handoff.md` 增加本轮记录：

```text
- 实施范围：仅 Vue quality_review 多 Agent。
- 已完成自动测试：逐项记录实际执行的命令和结果。
- 尚需人工测试：真实模型、停止传播、超时、修复针对性、延迟和 token 成本。
- 明确未启用长期记忆，未引入 PostgresStore。
- PostgreSQL 仅继续承担 LangGraph checkpoint，不承担跨请求记忆。
```

不能预填“人工测试通过”；未执行的项目必须标记为“待人工验证”。

- [ ] **Step 3: 运行 Python 全量验证**

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother/ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

Expected:

- `compileall` exit code 0。
- pytest 0 failures。
- `uv lock --check` exit code 0。

- [ ] **Step 4: 检查仓库差异和无关文件**

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother
git diff --check
git status --short
git diff --stat
```

Expected:

- `git diff --check` 无输出。
- 不包含 `.env`、`.venv`、日志、`projects/` 或用户原有未跟踪文件。
- 不包含 Spring、前端或 PostgreSQL schema 修改。

- [ ] **Step 5: 更新交接文档中的真实验证结果**

把 Step 3 和 Step 4 的实际日期、命令、通过数量或失败原因写入交接文档。人工测试保持待验证，除非本轮确实启动 Docker PostgreSQL、Spring、Python、前端和真实模型并完成相应场景。

- [ ] **Step 6: 提交文档和最终验证记录**

```powershell
git add ai-service/README.md doc/ai-service-phase-one-handoff.md
git commit -m "docs: 补充 Vue 多 Agent 审查交接说明"
```

- [ ] **Step 7: 提交后重新验证工作树和提交序列**

```powershell
git status --short
git log --oneline -7
```

Expected: 仅显示实施前已经存在的用户修改或未跟踪文件；最近提交依次覆盖契约、模型接口、并发协调、配置、工作流、回归测试和文档。

---

## 人工验收清单

自动测试完成后，由人工在本地完整环境执行：

- [ ] Docker PostgreSQL checkpoint 可用，`/health/ready` 返回 ready。
- [ ] Spring、Python 和 Vue 前端均已启动。
- [ ] 开启 `AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED=true`。
- [ ] 首次 Vue 生成构建后审查真实源码快照。
- [ ] 明显需求遗漏能够产生 major 或 critical，并进入针对性 repair。
- [ ] repair 保留未提及功能，修复后重新构建和重新审查。
- [ ] 审查期间停止生成，前端不刷新旧预览，服务端返回 cancelled。
- [ ] 模拟模型超时或错误凭据，候选版本不发布。
- [ ] 记录开启前后总耗时、三个 Reviewer 耗时、token 成本和修复成功率。

所有结果同步到 `doc/ai-service-phase-one-handoff.md`，未执行项目保留“待人工验证”，不得推断为通过。
