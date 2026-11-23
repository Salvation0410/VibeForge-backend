"""逐轮摘要在验证私有状态留存，二次投影不接纳源码、路径或自由问题码。"""

from copy import deepcopy
import os
from uuid import uuid4

import pytest

from ai_service.infrastructure.postgres_checkpoint import PostgresCheckpoint, STATUS_UPSERT_SQL
from ai_service.models.single_quality_review import parse_single_review
from ai_service.models.verification_diagnostics import safe_verification_history
from ai_service.orchestration.workflow import GenerationWorkflow
from conftest import FakeModel, MemoryCheckpoint
from test_quality_review_diagnostics import REPAIR
from test_subgraphs import request_for
from test_verification_failure_diagnostics import BuildRepairsGateway
from test_vue_subgraph import make_workflow


async def test_private_history_retains_build_failures_and_final_review_without_leaking(settings):
    checkpoint, model = MemoryCheckpoint(), FakeModel(reviews=[parse_single_review(REPAIR, "STOP")])
    events = await make_workflow(settings, model, checkpoint, BuildRepairsGateway()).run(request_for("VUE_PROJECT"))
    assert events[-1].error.code == "QUALITY_REVIEW_FAILED"
    history = checkpoint.saved["42:subgraph-test"][-1]["verification_history"]
    assert [(r["round"], r["stage"], r["verdict"]) for r in history] == [
        (0, "artifact_validation", "PASS"), (0, "project_build", "REPAIR"),
        (1, "artifact_validation", "PASS"), (1, "project_build", "REPAIR"),
        (2, "artifact_validation", "PASS"), (2, "project_build", "PASS"),
        (2, "quality_review", "REPAIR"),
    ]
    assert history[-1]["issueCodes"] == ["SCOPE_VIOLATION"]
    assert all("verification_history" not in str(payload) for _, payload in model.calls)
    assert "private-" not in str(checkpoint.saved)
    assert "verification_history" not in str([event.model_dump() for event in events])


def record():
    return {"round": 1, "stage": "project_build", "verdict": "REPAIR", "errorCode": "VUE_EVENT_CONTRACT_FAILED",
            "toolCount": 20, "phaseReadCount": 5, "phaseWriteCount": 2, "repeatedReadCount": 2,
            "issueCount": 0, "issueCodes": []}


@pytest.mark.parametrize("change", [
    {"path": "private-source"}, {"errorCode": "private-error"}, {"round": True}, {"toolCount": -1},
    {"stage": "private-stage"}, {"issueCodes": ["private-code"]}, {"issueCount": True},
    {"repeatedReadCount": 6}, {"phaseReadCount": 21}, {"verdict": "private-verdict"},
])
def test_both_projection_boundaries_drop_untrusted_history(change):
    good = [record()]
    before = deepcopy(good)
    assert safe_verification_history(good) == good
    assert good == before
    bad = [{**record(), **change}]
    base = {"app_id": "42", "request_id": "req", "code_gen_type": "VUE_PROJECT"}
    assert "verification_history" not in GenerationWorkflow._checkpoint_payload({**base, "verification_history": bad}, "project_build")
    payload = GenerationWorkflow._checkpoint_payload(base, "project_build")
    payload["verification_history"] = bad
    assert "verification_history" not in PostgresCheckpoint._normalize_state(payload)


def test_history_is_bounded_and_absence_is_not_fabricated():
    assert safe_verification_history(None) is None
    assert safe_verification_history([record()] * 37) is None


@pytest.mark.skipif(os.getenv("AI_SERVICE_POSTGRES_INTEGRATION") != "true", reason="需显式开启真实 PostgreSQL")
async def test_real_postgres_upsert_preserves_and_replaces_private_history():
    from psycopg import AsyncConnection
    from psycopg.types.json import Jsonb
    from ai_service.config import Settings

    # 只操作唯一测试行，始终回滚，不启动服务的全局清理或修改业务请求。
    request_id = uuid4().hex
    thread_id = f"integration-history:{request_id}"
    connection = await AsyncConnection.connect(Settings().checkpoint_postgres_url)
    try:
        for payload, expected in [
            ({"verification_history": [record()]}, [record()]),
            ({"node": "repair"}, [record()]),
            ({"verification_history": [{**record(), "round": 2}]}, [{**record(), "round": 2}]),
        ]:
            await connection.execute(STATUS_UPSERT_SQL, (
                thread_id, 42, request_id, "VUE_PROJECT", "project_build", None, 1, 20, Jsonb(payload), 60,
            ))
            cursor = await connection.execute(
                "SELECT status_payload FROM ai_workflow_status WHERE thread_id = %s", (thread_id,),
            )
            row = await cursor.fetchone()
            assert row[0]["verification_history"] == expected
    finally:
        await connection.rollback()
        await connection.close()
