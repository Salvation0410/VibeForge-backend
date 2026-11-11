"""检查器环境失败不能浪费模型修复；组件缺陷仍受原修复预算约束。"""

from conftest import FakeModel, FakeToolGateway, MemoryCheckpoint
from test_subgraphs import request_for
from test_vue_subgraph import make_workflow


async def test_unavailable_component_checker_fails_without_model_repair(settings):
    class UnavailableGateway(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            result = await super().invoke(name, arguments, **kwargs)
            if name == "project_build":
                return {"built": False, "errorCode": "VUE_COMPONENT_CHECK_UNAVAILABLE", "message": "checker unavailable"}
            return result

    model, gateway = FakeModel(), UnavailableGateway()
    events = await make_workflow(settings, model, MemoryCheckpoint(), gateway).run(request_for("VUE_PROJECT"))
    assert events[-1].type == "failed"
    assert events[-1].error.code == "VUE_COMPONENT_CHECK_UNAVAILABLE"
    assert not any(name in {"repair", "review", "review_role"} for name, _ in model.calls)
    assert sum(call["name"] == "project_build" for call in gateway.calls) == 1
    assert sum(e.type in {"completed", "failed"} for e in events) == 1
