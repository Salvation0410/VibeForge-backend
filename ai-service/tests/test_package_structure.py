"""职责分包的导入契约测试。"""


def test_public_modules_can_be_imported_from_new_packages():
    """确保启动入口和四类职责模块均可独立加载。"""

    from ai_service.api import routes, schemas
    from ai_service.app import create_app
    from ai_service.infrastructure import checkpoint, spring_tools
    from ai_service.models import GenerationModel, OpenAICompatibleModel
    from ai_service.orchestration import cancellation, events, workflow

    assert callable(create_app)
    assert routes.register_routes
    assert schemas.GenerationRequest
    assert checkpoint.RedisCheckpoint
    assert spring_tools.SpringToolGateway
    assert GenerationModel
    assert OpenAICompatibleModel
    assert cancellation.CancellationRegistry
    assert events.EventEmitter
    assert workflow.GenerationWorkflow
