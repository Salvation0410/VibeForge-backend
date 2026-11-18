"""旧评测固定文本基线；配置拒绝必须先于输出、授权与真实资源。"""
from pathlib import Path
import os
import sys

import pytest

from ai_service.config import Settings
from ai_service.orchestration import rag_cap_contracts as cap_contracts
from ai_service.orchestration import rag_cap_experiment as cap
from ai_service.orchestration import rag_refusal_experiment as refusal
from ai_service.orchestration import rag_rerank_experiment as rerank
from ai_service.orchestration import rag_snapshot_experiment as snapshot
from test_rag_cap_contracts import frozen_source
from test_rag_cap_experiment import approve, prepared


class BoundaryReached(AssertionError):
    """标记允许文本模式到达的边界，同时阻止任何实际副作用。"""


@pytest.fixture(autouse=True)
def clean_ai_environment(monkeypatch):
    # 本组测试不继承本机配置或凭据，也不读取真实 .env。
    for name in tuple(os.environ):
        if name.startswith('AI_SERVICE_'):
            monkeypatch.delenv(name)
    monkeypatch.setenv('AI_SERVICE_INTERNAL_BEARER_TOKEN', 'test-only-token')
    monkeypatch.setenv('AI_SERVICE_SPRING_GATEWAY_BASE_URL', 'https://spring.invalid/api')
    monkeypatch.setenv('AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN', 'test-only-token')


@pytest.fixture
def baseline():
    cfg = Settings(_env_file=None, customer_service_rag_enabled=False,
        rag_publication_enabled=True, model_api_key='test-key',
        model_base_url='https://model.invalid/v1', closeai_api_key='test-key',
        closeai_base_url='https://embedding.invalid/v1')
    assert cfg.rag_answer_response_format == 'text'
    return cfg


def barrier(touched, name):
    def forbidden(*args, **kwargs):
        touched.append(name)
        raise BoundaryReached(name)
    return forbidden


def block_resources(monkeypatch, output, touched):
    """每个潜在外部资源均独立拦截，旧代码失败时也不会访问网络或写输出。"""
    import httpx
    import langchain_openai
    import openai
    from ai_service.infrastructure import knowledge_publication_store, milvus_knowledge
    from ai_service.models import openai_compatible, reranker
    from ai_service.orchestration import rag_cap_budget, rag_rerank_budget

    monkeypatch.setattr(langchain_openai, 'ChatOpenAI', barrier(touched, 'answer-sdk'))
    monkeypatch.setattr(openai_compatible, 'ChatOpenAI', barrier(touched, 'answer-sdk'))
    monkeypatch.setattr(openai, 'AsyncOpenAI', barrier(touched, 'embedding-sdk'))
    monkeypatch.setattr(httpx, 'AsyncClient', barrier(touched, 'http-client'))
    monkeypatch.setattr(milvus_knowledge.MilvusKnowledgeStore, '__init__',
                        barrier(touched, 'milvus-store'))
    monkeypatch.setattr(knowledge_publication_store.PublicationStore, '__init__',
                        barrier(touched, 'publication-store'))
    monkeypatch.setattr(reranker.LocalCrossEncoderReranker, 'create',
                        barrier(touched, 'bge-initialization'))
    monkeypatch.setattr(reranker._GpuOwnerLock, 'acquire', barrier(touched, 'gpu-owner'))
    monkeypatch.setattr(snapshot.CallBudget, '__init__', barrier(touched, 'call-budget'))
    monkeypatch.setattr(snapshot.CallBudget, 'reserve', barrier(touched, 'call-reservation'))
    monkeypatch.setattr(rag_rerank_budget.AuthorizationBudget, '__init__',
                        barrier(touched, 'rerank-budget'))
    monkeypatch.setattr(rag_rerank_budget.AuthorizationBudget, 'reserve_chat',
                        barrier(touched, 'rerank-chat-reservation'))
    monkeypatch.setattr(rag_rerank_budget.AuthorizationBudget, 'reserve_embedding',
                        barrier(touched, 'rerank-embedding-reservation'))
    monkeypatch.setattr(rag_cap_budget.CapAuthorizationBudget, '__init__',
                        barrier(touched, 'cap-budget'))
    monkeypatch.setattr(rag_cap_budget.CapAuthorizationBudget, 'reserve_chat',
                        barrier(touched, 'cap-chat-reservation'))
    original_mkdir = Path.mkdir

    def guarded_mkdir(path, *args, **kwargs):
        if path.resolve() == output.resolve():
            barrier(touched, 'output-directory')()
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'mkdir', guarded_mkdir)


def block_authorization(monkeypatch, runner, touched):
    class Authorization:
        exists = staticmethod(barrier(touched, 'authorization-anchor'))
    monkeypatch.setattr(runner, 'AUTHORIZATION', Authorization())


@pytest.mark.parametrize('mode', ['text', 'json_object'])
@pytest.mark.parametrize('experiment,code,text_boundary', [
    ('rerank', 'RAG_RERANK_UNSUPPORTED_BASELINE_SETTINGS', 'authorization-anchor'),
    ('snapshot', 'RAG_EVAL_UNSUPPORTED_BASELINE_CONFIG', 'output-directory'),
    ('refusal', 'RAG_EVAL_UNSUPPORTED_BASELINE_CONFIG', 'answer-sdk'),
])
@pytest.mark.asyncio
async def test_live_rejects_json_before_every_side_effect(
        baseline, tmp_path, monkeypatch, mode, experiment, code, text_boundary):
    import ai_service.config as config

    cfg = baseline if mode == 'text' else baseline.model_copy(
        update={'rag_answer_response_format': mode})
    monkeypatch.setattr(config, 'Settings', lambda **kwargs: cfg)
    touched = []
    output = tmp_path / 'tmp' / 'must-not-create'
    block_resources(monkeypatch, output, touched)
    if experiment == 'rerank':
        monkeypatch.setattr(rerank, 'ROOT', tmp_path)
        # rubric/mapping 属于原入口的只读前置，不替换真正的配置检查。
        monkeypatch.setattr(rerank, 'load_rubric', lambda: {})
        monkeypatch.setattr(rerank, 'load_special', lambda: {})
        monkeypatch.setattr(rerank, 'load_mapping', lambda *args: {})
        block_authorization(monkeypatch, rerank, touched)
        monkeypatch.setattr(rerank, 'source_manifest', barrier(touched, 'source-manifest'))
        monkeypatch.setattr(rerank, '_local_model_manifest', barrier(touched, 'local-bge-cache'))
        monkeypatch.setattr(rerank, 'publication_client', barrier(touched, 'publication-client'))
        call = rerank.run_live(output, tmp_path / 'mapping.json')
    elif experiment == 'snapshot':
        call = snapshot.run_live({'entries': [{'id': 'q'}]}, [], output,
                                 max_chat=2, judge=False)
    else:
        monkeypatch.setattr(refusal, 'execute', barrier(touched, 'refusal-execution'))
        call = refusal.run_live({}, {}, output, rounds=1, max_chat=2)
    expected = BoundaryReached if mode == 'text' else ValueError
    with pytest.raises(expected, match=f'^{text_boundary if mode == "text" else code}$'):
        await call
    assert touched == ([text_boundary] if mode == 'text' else [])
    assert not output.exists()
    assert not output.parent.exists()


@pytest.mark.parametrize('mode', ['text', 'json_object'])
@pytest.mark.parametrize('experiment', ['rerank', 'cap'])
def test_checkers_keep_default_text_and_frozen_manifest(baseline, experiment, mode):
    original = cap_contracts.settings_manifest(baseline)
    cfg = baseline if mode == 'text' else baseline.model_copy(
        update={'rag_answer_response_format': mode})
    # 冻结结构不追加模式字段，非文本必须由独立 checker 拒绝。
    assert cap_contracts.settings_manifest(cfg) == original
    assert 'rag_answer_response_format' not in original
    if experiment == 'rerank':
        if mode == 'text':
            assert rerank.check_settings(cfg) is None
        else:
            with pytest.raises(ValueError, match='^RAG_RERANK_UNSUPPORTED_BASELINE_SETTINGS$'):
                rerank.check_settings(cfg)
    elif mode == 'text':
        assert cap_contracts.check_settings(cfg, original) == original
    else:
        with pytest.raises(ValueError, match='^RAG_CAP_SETTINGS_CHANGED$'):
            cap_contracts.check_settings(cfg, original)


@pytest.mark.parametrize('mode', ['text', 'json_object'])
@pytest.mark.parametrize('entrypoint', ['prepare', 'live'])
@pytest.mark.asyncio
async def test_cap_entrypoints_check_mode_before_output_and_authorization(
        frozen_source, tmp_path, monkeypatch, entrypoint, mode):
    _, cfg, _, _, _ = frozen_source
    if entrypoint == 'live':
        # 合成 text 来源和 pending 计划仅在 pytest 临时目录建立，不复用历史授权。
        cfg, plan = await prepared(frozen_source, tmp_path, monkeypatch)
        approval = approve(plan, tmp_path)
        output = Path(plan['liveOutput'])
    else:
        output = tmp_path / 'must-not-create'
        monkeypatch.setattr(cap, 'TMP', tmp_path)
    cfg = cfg if mode == 'text' else cfg.model_copy(update={'rag_answer_response_format': mode})
    monkeypatch.setattr(cap, 'settings', lambda: cfg)
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    touched = []
    block_resources(monkeypatch, output, touched)
    block_authorization(monkeypatch, cap, touched)
    monkeypatch.setattr(cap, '_runtime_model', barrier(touched, 'runtime-sdk'))
    monkeypatch.setattr(cap, 'prepare_contexts', barrier(touched, 'context-preparation'))
    call = (cap.prepare(output) if entrypoint == 'prepare'
            else cap.run_live(tmp_path / 'prepared/plan.json', approval))
    expected = BoundaryReached if mode == 'text' else ValueError
    with pytest.raises(expected, match=('^context-preparation$' if mode == 'text'
                                        else '^RAG_CAP_SETTINGS_CHANGED$')):
        await call
    assert touched == (['context-preparation'] if mode == 'text' else [])
    assert not output.exists()
    assert before == {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


@pytest.mark.parametrize('experiment', ['snapshot', 'refusal'])
def test_cli_safe_output_stays_read_only_before_json_rejection(
        baseline, tmp_path, monkeypatch, capsys, experiment):
    import ai_service.config as config

    monkeypatch.setattr(config, 'Settings', lambda **kwargs: baseline.model_copy(
        update={'rag_answer_response_format': 'json_object'}))
    output = tmp_path / 'tmp' / 'must-not-create'
    touched = []
    block_resources(monkeypatch, output, touched)
    monkeypatch.setattr(snapshot, 'ROOT', tmp_path)
    # 调用真实 main → safe_output → run_live，只替换只读数据载入。
    if experiment == 'snapshot':
        runner = snapshot
        monkeypatch.setattr(runner, 'load_study', lambda *args: ({'entries': [{'id': 'q'}]}, []))
    else:
        runner = refusal
        monkeypatch.setattr(runner, 'load_diagnostic', lambda *args, **kwargs:
                            ({'entries': [{'id': 'q'}]}, {}))
    monkeypatch.setattr(sys, 'argv', ['experiment', '--live', '--output', str(output),
                                   '--max-chat-calls', '2', *(['--rounds', '1']
                                                           if experiment == 'refusal' else [])])
    with pytest.raises(SystemExit) as stopped:
        runner.main()
    assert stopped.value.code == 1
    assert '"status": "failed"' in capsys.readouterr().out
    assert touched == []
    assert not output.exists()
    assert not output.parent.exists()
