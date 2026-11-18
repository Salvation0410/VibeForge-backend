"""只使用注入式组件，核对正式回答路径和本轮唯一调用授权。"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_service.config import Settings
from ai_service.infrastructure.milvus_knowledge import RetrievedChunk
from ai_service.models.base import CustomerServiceModelAnswer
from ai_service.models.reranker import RerankedChunk
from ai_service.orchestration.rag_rerank_budget import AuthorizationBudget
from ai_service.orchestration.rag_rerank_contracts import (
    ARMS, PRODUCT, RUBRIC, load_mapping, load_rubric, load_special, read_json, verify_corpus,
)
from ai_service.orchestration.rag_rerank_experiment import (
    answer_once, execute_answers, new_report, schedule, snippet_survival,
)


@pytest.fixture(scope='module')
def rubric():
    return load_rubric()


def cfg(**overrides):
    return Settings(_env_file=None, customer_service_rag_enabled=False,
                    rag_embedding_dimension=2, **overrides)


@pytest.mark.asyncio
@pytest.mark.parametrize('experiment', ['rerank', 'snapshot'])
async def test_fixed_cap_one_live_experiments_reject_cap_two_before_resources(experiment, tmp_path, monkeypatch):
    import ai_service.config as config
    import ai_service.models.reranker as providers
    import openai
    from ai_service.orchestration import rag_rerank_experiment, rag_snapshot_experiment

    config_value = Settings(_env_file=None, customer_service_rag_enabled=False,
                            rag_publication_enabled=True, rag_document_chunk_limit=2)
    monkeypatch.setattr(config, 'Settings', lambda **kwargs: config_value)
    touched = []
    def forbidden(*args, **kwargs):
        touched.append('resource-or-authorization')
        raise AssertionError('配置拒绝前不得初始化资源或账本')
    monkeypatch.setattr(openai, 'AsyncOpenAI', forbidden)
    monkeypatch.setattr(providers.LocalCrossEncoderReranker, 'create', forbidden)
    output = tmp_path / 'must-not-create'
    if experiment == 'rerank':
        runner = rag_rerank_experiment
        monkeypatch.setattr(runner, 'load_rubric', lambda: {})
        monkeypatch.setattr(runner, 'load_special', lambda: {})
        monkeypatch.setattr(runner, 'load_mapping', lambda *args: {})
        monkeypatch.setattr(runner, 'source_manifest', forbidden)
        monkeypatch.setattr(runner, '_local_model_manifest', forbidden)
        monkeypatch.setattr(runner, 'publication_client', forbidden)
        monkeypatch.setattr(runner, 'AuthorizationBudget', forbidden)
        with pytest.raises(ValueError, match='RAG_RERANK_UNSUPPORTED_BASELINE_SETTINGS'):
            await runner.run_live(output, tmp_path / 'mapping.json')
    else:
        runner = rag_snapshot_experiment
        monkeypatch.setattr(runner, 'CallBudget', forbidden)
        with pytest.raises(ValueError, match='RAG_EVAL_UNSUPPORTED_BASELINE_CONFIG'):
            await runner.run_live({'entries': [{'id': 'q'}]}, [], output, max_chat=2, judge=False)
    assert touched == []
    assert not output.exists()


def chunk(key, doc, content='正文', score=.8):
    return RetrievedChunk(key, doc, 1, 0, content, doc + '.md', 'md', '小节',
                          'content-hash', 1 - score, score, True, 1)


class Model:
    def __init__(self, value=None, error=None):
        self.value = value or CustomerServiceModelAnswer(True, '正文', ('a1',))
        self.error = error
        self.calls = []

    async def answer_customer_service(self, question, contexts):
        self.calls.append((question, list(contexts)))
        if self.error:
            raise self.error
        return self.value


def test_frozen_rubric_preserves_all_old_questions_and_evidence(rubric):
    assert len(rubric['entries']) == 90
    assert sum(e['expectedAnswerable'] for e in rubric['entries']) == 81
    assert sum(len(e['requiredFacts']) for e in rubric['entries']) == 157
    assert sum(len(e['optionalFacts']) for e in rubric['entries']) == 29
    assert rubric['humanGold'] is False
    assert len(rubric['preAnswerIndependentReview']['factMoves']) == 7
    assert len(load_special()['entries']) == 8
    historical = read_json(PRODUCT / 'evaluation/annotations.json')
    assert [e['originalAnnotation'] for e in rubric['entries']] == historical['entries']


@pytest.mark.parametrize('damage', ['offset', 'quote', 'hash', 'question', 'answerability', 'factId', 'boolean', 'status'])
def test_rubric_damage_rejected_before_external_resources(tmp_path, rubric, damage):
    data = deepcopy(rubric)
    e = data['entries'][0]
    fact = e['requiredFacts'][0]
    if damage == 'offset': fact['evidence'][0]['quoteStart'] += 1
    if damage == 'quote': fact['evidence'][0]['quote'] += '不存在'
    if damage == 'hash': fact['evidence'][0]['documentSha256'] = '0' * 64
    if damage == 'question': e['question'] += '变化'
    if damage == 'answerability': e['expectedAnswerable'] = False
    if damage == 'factId': e['requiredFacts'][1]['factId'] = fact['factId']
    if damage == 'boolean': fact['evidence'][0]['quoteStart'] = True
    if damage == 'status': data['humanGold'] = True
    path = tmp_path / 'rubric.json'
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
    with pytest.raises(ValueError, match='RAG_RERANK_'):
        load_rubric(path)


def budget(tmp_path):
    questions = [f'q{i}' for i in range(90)]
    keys = [f'q{i}:{arm}' for i in range(90) for arm in ARMS]
    return AuthorizationBudget(tmp_path / 'authorization.jsonl', output=tmp_path / 'run',
        specification={'frozen': True}, questions=questions, schedule=keys), questions, keys


def test_authorization_is_fixed_nonrefundable_and_refuses_new_output(tmp_path):
    ledger, questions, keys = budget(tmp_path)
    with pytest.raises(ValueError, match='CHAT_BUDGET'):
        ledger.reserve_chat(keys[0])
    for index in range(3):
        ledger.reserve_embedding(index, questions[index * 32:(index + 1) * 32])
    for key in keys:
        ledger.reserve_chat(key)
    assert ledger.counts == {'embeddingRequests': 3, 'queryInputs': 90, 'chat': 180}
    with pytest.raises(ValueError, match='CHAT_BUDGET'):
        ledger.reserve_chat(keys[0])
    with pytest.raises(ValueError, match='EMBEDDING_BUDGET'):
        ledger.reserve_embedding(0, questions[:32])
    ledger.close()
    rows = [json.loads(line) for line in (tmp_path / 'authorization.jsonl').read_text().splitlines()]
    assert len(rows) == 184 and rows[-1]['key'] == keys[-1]
    with pytest.raises(FileExistsError):
        AuthorizationBudget(tmp_path / 'authorization.jsonl', output=tmp_path / 'different',
            specification={}, questions=questions, schedule=keys)


def test_fsync_failure_poisoned_ledger_cannot_send_again(tmp_path, monkeypatch):
    import ai_service.orchestration.rag_rerank_budget as module
    ledger, questions, _ = budget(tmp_path)
    monkeypatch.setattr(module.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError): ledger.reserve_embedding(0, questions[:32])
    assert ledger.counts['embeddingRequests'] == 0
    with pytest.raises(ValueError, match='LEDGER_UNAVAILABLE'):
        ledger.reserve_embedding(0, questions[:32])
    ledger.close()


def test_embedding_plan_rejects_extra_inputs_or_out_of_order(tmp_path):
    ledger, questions, _ = budget(tmp_path)
    with pytest.raises(ValueError): ledger.reserve_embedding(0, questions[:31])
    with pytest.raises(ValueError): ledger.reserve_embedding(1, questions[32:64])
    ledger.close()


@pytest.mark.asyncio
async def test_formal_service_single_variable_and_true_off():
    rows = [chunk('a1', 'a', score=.9), chunk('a2', 'a', score=.8), chunk('b', 'b', score=.7),
            chunk('c', 'c', score=.6), chunk('d', 'd', score=.5)]
    ranked = [RerankedChunk(c, float(i)) for i, c in enumerate(rows, 1)][::-1]
    models = [Model(), Model(CustomerServiceModelAnswer(True, '正文', ('d',)))]
    results = [await answer_once(cfg(), {'id': 'q', 'question': '问题'}, arm, [.1, .2],
                rows, rows, ranked, model) for arm, model in zip(ARMS, models)]
    assert results[0]['rerankerReplayCalls'] == 0
    assert results[1]['rerankerReplayCalls'] == 1
    assert [r['chunkId'] for r in results[0]['contexts']] == ['a1', 'b', 'c']
    assert [r['chunkId'] for r in results[1]['contexts']] == ['d', 'c', 'b']
    assert results[0]['stages']['retrieved'] == results[1]['stages']['retrieved']
    assert all(r['status'] == 'ok' and not r['degraded'] for r in results)
    assert all(r['trace']['configFingerprint']['promptMaxBytes'] == cfg().rag_prompt_max_bytes for r in results)


@pytest.mark.asyncio
async def test_on_degraded_is_error_and_never_counted_as_real_bge():
    rows = [chunk('a1', 'a')]
    row = await answer_once(cfg(), {'id': 'q', 'question': '问题'}, ARMS[1], [.1, .2],
        rows, rows, [RerankedChunk(rows[0], float('nan'))], Model())
    assert row['status'] == 'error' and row['errorCode'] == 'RAG_RERANK_ON_DEGRADED'


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['model_error', 'invalid_citation', 'refusal', 'budget'])
async def test_formal_failure_refusal_source_and_budget_semantics(mode):
    text = '中文"\\🙂\n' * 1000
    rows = [chunk('a1', 'a', text)]
    model = Model()
    question = '问题'
    settings = cfg()
    if mode == 'model_error': model = Model(error=RuntimeError('不得暴露供应商详情'))
    if mode == 'invalid_citation': model = Model(CustomerServiceModelAnswer(True, '答案', ('unknown',)))
    if mode == 'refusal': model = Model(CustomerServiceModelAnswer(False, '', ()))
    if mode == 'budget':
        settings = cfg(rag_prompt_max_bytes=1024)
        question = '中' * 2000
    row = await answer_once(settings, {'id': 'q', 'question': question}, ARMS[0], [.1, .2],
        rows, rows, [], model)
    from ai_service.orchestration.customer_service_rag import NO_ANSWER_TEXT, SYSTEM_FALLBACK_TEXT
    if mode in {'model_error', 'invalid_citation'}:
        assert row['status'] == 'error' and row['answer'] == SYSTEM_FALLBACK_TEXT
    else:
        assert row['status'] == 'ok' and row['answer'] == NO_ANSWER_TEXT
    assert not row['sources']
    if mode == 'budget': assert not model.calls
    elif model.calls:
        from ai_service.prompts.customer_service import customer_service_user_prompt
        payload = customer_service_user_prompt(question, model.calls[0][1], max_bytes=settings.rag_prompt_max_bytes)
        assert len(payload.encode()) <= settings.rag_prompt_max_bytes
        assert len(model.calls[0][1][0].content) <= 1200


@pytest.mark.asyncio
async def test_frozen_snapshot_change_discards_formal_answer():
    rows = [chunk('a1', 'a')]
    class Client:
        calls = 0
        async def snapshot(self):
            self.calls += 1
            return 'frozen' if self.calls < 3 else 'changed'
    row = await answer_once(cfg(), {'id': 'q', 'question': '问题'}, ARMS[0], [.1, .2],
        rows, rows, [], Model(), snapshot='frozen', client=Client())
    assert row['status'] == 'error' and row['snapshotInvalid'] and not row['sources']


class BatchModel(Model):
    def begin(self, key):
        self.key = key
        self.observation = {'providerCalled': False}

    async def answer_customer_service(self, question, contexts):
        self.observation['providerCalled'] = True
        return await super().answer_customer_service(question, contexts)


@pytest.mark.asyncio
async def test_all_180_placeholders_alternating_order_and_three_failures_stop(rubric):
    report = new_report(rubric)
    assert len(report['results']) == 180
    assert [r['arm'] for r in report['results'][:4]] == [*ARMS, *ARMS[::-1]]
    rows = [chunk('a1', 'a')]
    cached = {'retrieved': rows, 'filtered': rows, 'ranked': [RerankedChunk(rows[0], 1.0)]}
    prepared = {e['id']: cached for e in rubric['entries']}
    model = BatchModel(error=RuntimeError('故障'))
    saves = []
    await execute_answers(report, rubric, cfg(), prepared,
        {e['id']: [.1, .2] for e in rubric['entries']}, model, lambda: saves.append(1))
    assert report['status'] == 'stopped' and len(model.calls) == 3
    assert sum(r['status'] == 'error' for r in report['results']) == 3
    assert sum(r['status'] == 'not_run' for r in report['results']) == 177


@pytest.mark.asyncio
async def test_cancellation_preserves_current_error_and_unrun_denominator(rubric):
    report = new_report(rubric)
    rows = [chunk('a1', 'a')]
    cached = {'retrieved': rows, 'filtered': rows, 'ranked': [RerankedChunk(rows[0], 1.)]}
    model = BatchModel(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await execute_answers(report, rubric, cfg(), {e['id']: cached for e in rubric['entries']},
            {e['id']: [.1, .2] for e in rubric['entries']}, model, lambda: None)
    assert report['status'] == 'cancelled'
    assert report['results'][0]['status'] == 'error'
    assert sum(r['status'] == 'not_run' for r in report['results']) == 179


def test_support_snippet_normalizes_whitespace_without_claiming_fact_quality():
    e = {'requiredFacts': [{'factId': 'f', 'basisType': 'public-document',
          'evidence': [{'documentId': 'logical', 'quote': '第一句\n第二句'}]}]}
    result = snippet_survival(e, {'final': [{'documentId': 'actual', 'content': '第一句 第二句'}]}, {'actual': 'logical'})
    assert result['stages']['final'][0]['annotatedSupportSnippetSurvived'] is True
    assert '非语义事实覆盖' in result['meaning']


def test_corpus_checks_content_locator_version_hash_but_uses_actual_ids(rubric):
    from ai_service.orchestration.document_etl import parse_document, split_sections
    import hashlib
    mapping = {'documents': {d['documentId']: {'documentId': str(i), 'documentVersion': d['version'], 'sha256': d['sha256']}
                for i, d in enumerate(rubric['freeze']['documents'], 1)}}
    identities, rows = [], []
    for logical, m in mapping['documents'].items():
        identity = SimpleNamespace(document_id=m['documentId'], document_version=1,
                                   sha256=m['sha256'], file_name='实际上传文件名.md')
        identities.append(identity)
        for c in split_sections(parse_document(PRODUCT / f'documents/{logical}.md', 'md'),
            document_id=identity.document_id, document_version=1, source_name=identity.file_name, settings=cfg()):
            rows.append({'recordType': 'chunk', 'documentId': identity.document_id,
                'documentVersion': 1, 'documentContentHash': m['sha256'], 'chunkIndex': c.chunk_index,
                'content': c.content, 'sourceLocator': c.source_locator,
                'contentHash': hashlib.sha256(c.content.encode()).hexdigest(), 'chunkId': f'actual-{len(rows)}'})
    snapshot = SimpleNamespace(enabled=True, documents=identities)
    receipt = SimpleNamespace(documents=identities)
    assert len(verify_corpus(snapshot, receipt, rows, mapping, cfg())) == 72
    damaged = deepcopy(rows)
    damaged[0]['sourceLocator'] = '错误小节'
    with pytest.raises(ValueError, match='CORPUS_CONTENT'):
        verify_corpus(snapshot, receipt, damaged, mapping, cfg())


def test_offline_main_does_not_enter_live(monkeypatch, capsys):
    import ai_service.orchestration.rag_rerank_experiment as runner
    monkeypatch.setattr('sys.argv', ['runner'])
    monkeypatch.setattr(runner, 'run_live', lambda *a: pytest.fail('离线调用了live'))
    runner.main()
    assert "'providerCalls': 0" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_metered_model_reserves_before_send_has_zero_retries_and_keeps_usage(tmp_path, monkeypatch):
    from langchain_core.messages import AIMessage
    import langchain_openai
    import ai_service.models.openai_compatible as adapter
    from ai_service.orchestration.rag_rerank_experiment import _runtime_model
    ledger, questions, keys = budget(tmp_path)
    for i in range(3): ledger.reserve_embedding(i, questions[i * 32:(i + 1) * 32])
    constructors = []
    class FakeChat:
        def __init__(self, **kwargs):
            constructors.append(kwargs)

        async def ainvoke(self, messages):
            lines = (tmp_path / 'authorization.jsonl').read_text().splitlines()
            assert json.loads(lines[-1])['key'] == keys[0]
            return AIMessage(content='{"answered":true,"answer":"正文","citedChunkIds":["a1"]}',
                usage_metadata={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15},
                response_metadata={'finish_reason': 'stop', 'model_name': 'response-model-v1',
                                   'unsafe_extra_header': '不得写入'})
    # 基类模块已绑定独立符号，单独封锁才能证明没有偷偷构造真实 SDK/HTTP 客户端。
    monkeypatch.setattr(adapter, 'ChatOpenAI', lambda **kwargs: pytest.fail('不得构造基类真实客户端'))
    monkeypatch.setattr(langchain_openai, 'ChatOpenAI', FakeChat)
    model = _runtime_model(cfg(model_api_key='offline-key'), ledger)
    model.begin(keys[0])
    from ai_service.models.base import CustomerServiceContext
    answer = await model.answer_customer_service('问题', [CustomerServiceContext('a1', '正文')])
    assert answer.answered
    assert len(constructors) == 1 and constructors[0]['max_retries'] == 0
    assert model.observation['usage']['total_tokens'] == 15
    assert model.observation['responseMetadata'] == {'finish_reason': 'stop', 'model_name': 'response-model-v1'}
    assert len(model.observation['actualMessages']) == 2
    assert ledger.counts['chat'] == 1
    ledger.close()


def test_abrupt_process_exit_leaves_charged_reservation_and_reuse_refused(tmp_path):
    import os
    import subprocess
    import sys
    anchor = tmp_path / 'abrupt.jsonl'
    script = '''
import os, sys
from ai_service.orchestration.rag_rerank_budget import AuthorizationBudget
questions = [str(i) for i in range(90)]
keys = [str(i) for i in range(180)]
ledger = AuthorizationBudget(sys.argv[1], output=sys.argv[2], specification={}, questions=questions, schedule=keys)
for i in range(3): ledger.reserve_embedding(i, questions[i*32:(i+1)*32])
ledger.reserve_chat(keys[0])
os._exit(79)
'''
    environment = {**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1] / 'src')}
    result = subprocess.run([sys.executable, '-c', script, str(anchor), str(tmp_path / 'run')],
                            env=environment, timeout=30, capture_output=True)
    assert result.returncode == 79
    rows = [json.loads(line) for line in anchor.read_text().splitlines()]
    assert rows[-1]['kind'] == 'chat' and rows[-1]['key'] == '0'
    with pytest.raises(FileExistsError):
        AuthorizationBudget(anchor, output=tmp_path / 'changed', specification={},
            questions=[str(i) for i in range(90)], schedule=[str(i) for i in range(180)])


@pytest.mark.asyncio
async def test_settings_url_types_are_normalized_at_client_boundary():
    from ai_service.orchestration.rag_rerank_experiment import publication_client
    settings = cfg()
    assert not isinstance(settings.spring_gateway_base_url, str)
    assert isinstance(settings.model_base_url, str)
    assert isinstance(settings.closeai_base_url, str)
    client = publication_client(settings)
    assert client._url.endswith('/internal/customer-service/knowledge-publications')
    await client.close()


@pytest.mark.asyncio
async def test_validated_actual_mapping_is_frozen_before_resource_initialization(rubric, tmp_path, monkeypatch):
    import uuid
    import ai_service.config as config
    import ai_service.orchestration.rag_rerank_experiment as runner
    from ai_service.orchestration.rag_rerank_contracts import ROOT, fingerprint
    mapping = {'schemaVersion': 'product-knowledge-document-map/v1', 'documents': {
        d['documentId']: {'documentId': str(index), 'documentVersion': 1, 'sha256': d['sha256']}
        for index, d in enumerate(rubric['freeze']['documents'], 1)}}
    source = tmp_path / 'mapping.json'
    source.write_text(json.dumps(mapping), encoding='utf8')
    output = ROOT / 'tmp/rag-verification-20261118/tool-validation' / ('map-fixture-' + uuid.uuid4().hex)
    settings = Settings(_env_file=None, customer_service_rag_enabled=False, rag_publication_enabled=True)
    monkeypatch.setattr(config, 'Settings', lambda **kwargs: settings)
    monkeypatch.setattr(runner, 'AUTHORIZATION', tmp_path / 'must-not-create.jsonl')
    monkeypatch.setattr(runner, 'source_manifest', lambda rubric: {})
    monkeypatch.setattr(runner, '_local_model_manifest', lambda cfg: (_ for _ in ()).throw(ValueError('RAG_RERANK_TEST_STOP')))
    report = await runner.run_live(output, source)
    assert report['status'] == 'stopped'
    assert read_json(output / 'document-map.json') == mapping
    assert report['mappingSha256'] == fingerprint(read_json(output / 'document-map.json'))
    assert report['callsReserved'] == {'embeddingRequests': 0, 'queryInputs': 0, 'chat': 0}
    assert not (tmp_path / 'must-not-create.jsonl').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [False, True])
async def test_final_report_save_failure_still_closes_resources_and_preserves_cancel(rubric, tmp_path, monkeypatch, cancelled):
    import uuid
    import ai_service.config as config
    import ai_service.infrastructure.milvus_knowledge as milvus
    import ai_service.infrastructure.knowledge_publication_store as publications
    import ai_service.orchestration.rag_rerank_experiment as runner
    from ai_service.orchestration.rag_rerank_contracts import ROOT
    mapping = {'schemaVersion': 'product-knowledge-document-map/v1', 'documents': {
        d['documentId']: {'documentId': str(index), 'documentVersion': 1, 'sha256': d['sha256']}
        for index, d in enumerate(rubric['freeze']['documents'], 1)}}
    source = tmp_path / 'mapping.json'
    source.write_text(json.dumps(mapping), encoding='utf8')
    output = ROOT / 'tmp/rag-verification-20261118/tool-validation' / ('cleanup-fixture-' + uuid.uuid4().hex)
    settings = Settings(_env_file=None, customer_service_rag_enabled=False, rag_publication_enabled=True)
    primary = asyncio.CancelledError('原始取消') if cancelled else ValueError('RAG_RERANK_TEST_STOP')
    closed = []
    class Client:
        async def snapshot(self):
            return SimpleNamespace(collection_name='frozen')
        async def close(self):
            closed.append('client')
    class Store:
        async def close(self):
            closed.append('store')
    class Publications:
        async def completed(self, collection):
            raise primary
    real_write = runner.write_json
    def fail_final_save(path, value):
        if Path(path).name == 'report.json' and 'finishedAtUtc' in value:
            raise OSError('不得记录异常正文')
        real_write(path, value)
    monkeypatch.setattr(config, 'Settings', lambda **kwargs: settings)
    monkeypatch.setattr(runner, 'AUTHORIZATION', tmp_path / 'must-not-create.jsonl')
    monkeypatch.setattr(runner, 'source_manifest', lambda rubric: {})
    monkeypatch.setattr(runner, '_local_model_manifest', lambda cfg: (tmp_path, {}))
    monkeypatch.setattr(runner, 'publication_client', lambda cfg: Client())
    monkeypatch.setattr(milvus, 'MilvusKnowledgeStore', lambda cfg: Store())
    monkeypatch.setattr(publications, 'PublicationStore', lambda *args: Publications())
    monkeypatch.setattr(runner, 'write_json', fail_final_save)
    if cancelled:
        with pytest.raises(asyncio.CancelledError) as observed:
            await runner.run_live(output, source)
        assert observed.value is primary
    else:
        report = await runner.run_live(output, source)
        assert report['status'] == 'stopped'
        assert report['reportSaveFailures'] == ['before_cleanup', 'after_cleanup']
    assert closed == ['client', 'store']
    assert not (tmp_path / 'must-not-create.jsonl').exists()


@pytest.mark.asyncio
async def test_each_failing_closer_and_gpu_release_are_attempted_even_when_report_saves_fail():
    from ai_service.orchestration.rag_rerank_experiment import _finish_run
    calls = []
    def fail_sync(name):
        def close():
            calls.append(name)
            raise OSError('不得记录异常正文')
        return close
    def fail_async(name):
        async def close():
            calls.append(name)
            raise OSError('不得记录异常正文')
        return close
    resources = tuple((name, SimpleNamespace(close=fail_async(name)))
                      for name in ('reranker', 'embedding', 'client', 'store'))
    model = SimpleNamespace(_client=SimpleNamespace(
        root_async_client=SimpleNamespace(close=fail_async('answer_model_async')),
        root_client=SimpleNamespace(close=fail_sync('answer_model_sync'))))
    report = {'status': 'complete'}
    await _finish_run(report, fail_sync('save'),
        budget=SimpleNamespace(close=fail_sync('budget')), resources=resources, model=model,
        gpu_owner=SimpleNamespace(release=fail_sync('gpu_owner')))
    expected = ['budget', 'reranker', 'embedding', 'client', 'store',
                'answer_model_async', 'answer_model_sync', 'gpu_owner']
    assert calls == ['save', *expected, 'save']
    assert report['cleanupErrors'] == expected
    assert report['reportSaveFailures'] == ['before_cleanup', 'after_cleanup']
    assert report['status'] == 'stopped'
    assert report['stopReason'] == 'RAG_RERANK_REPORT_SAVE_FAILED'
    assert '不得记录异常正文' not in str(report)


@pytest.mark.asyncio
@pytest.mark.parametrize('already_cancelled', [False, True])
async def test_cleanup_cancellation_keeps_remaining_closers_and_original_identity(already_cancelled):
    from ai_service.orchestration.rag_rerank_experiment import _finish_run
    original = asyncio.CancelledError('原取消')
    during_cleanup = asyncio.CancelledError('收尾取消')
    closed = []
    async def cancel_close():
        closed.append('cancel')
        raise during_cleanup
    async def later_close():
        closed.append('later')
    report = {'status': 'cancelled' if already_cancelled else 'complete'}
    async def run():
        if not already_cancelled:
            await _finish_run(report, lambda: None, resources=(('cancel', SimpleNamespace(close=cancel_close)),
                ('later', SimpleNamespace(close=later_close))))
        else:
            try:
                raise original
            finally:
                await _finish_run(report, lambda: None,
                    resources=(('cancel', SimpleNamespace(close=cancel_close)),
                               ('later', SimpleNamespace(close=later_close))), original_cancel=original)
    with pytest.raises(asyncio.CancelledError) as observed:
        await run()
    assert observed.value is (original if already_cancelled else during_cleanup)
    assert closed == ['cancel', 'later']
    assert report['status'] == 'cancelled'
