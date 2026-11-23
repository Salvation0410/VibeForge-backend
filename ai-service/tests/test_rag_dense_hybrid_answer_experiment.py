"""同期回答实验用合成供应商验证预占、停止与匿名审阅，不连接真实模型。"""
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from ai_service.models.base import CustomerServiceContext
from ai_service.orchestration import rag_dense_hybrid_answer_experiment as exp
from ai_service.orchestration import rag_dense_hybrid_review as review
from ai_service.prompts.customer_service import CUSTOMER_SERVICE_SYSTEM_PROMPT, customer_service_user_prompt


def test_new_cases_and_basis_frozen():
    entries = exp.new_entries()
    assert len(entries)==24 and sum(e['expectedAnswerable'] for e in entries)==18
    assert len(review.basis()) == 114
    ops = exp.schedule(entries,'bge')
    assert len(ops)==48 and ops[:4]==[
        dict(kind='bge',key='new-01:dense'),dict(kind='bge',key='new-01:hybrid'),
        dict(kind='bge',key='new-02:hybrid'),dict(kind='bge',key='new-02:dense')]


def test_ledger_exclusive_order_and_fsync_failure(tmp_path,monkeypatch):
    path = tmp_path/'ledger.jsonl'
    op = dict(kind='chat',key='x',inputSha256='hash')
    ledger = exp.Ledger(path,[op],header=dict(limit=1))
    with pytest.raises(ValueError,match='ORDER'):
        ledger.reserve(dict(kind='chat',key='other'))
    ledger.reserve(op)
    with pytest.raises(ValueError,match='ORDER'):
        ledger.reserve(op)
    ledger.close()
    with pytest.raises(FileExistsError):
        exp.Ledger(path,[op],header={})
    ledger = exp.Ledger(tmp_path/'failed',[op],header={})
    def fail(_):
        raise OSError('synthetic')
    monkeypatch.setattr(exp.os,'fsync',fail)
    with pytest.raises(OSError):
        ledger.reserve(op)
    assert ledger.count==0 and ledger.poisoned
    with pytest.raises(ValueError,match='POISONED'):
        ledger.reserve(op)
    ledger.close()


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(exp,'RUN',tmp_path)
    settings=SimpleNamespace(model_api_key='synthetic',model_base_url='https://synthetic.invalid/v1',
        model_name='synthetic',model_temperature=.1,model_max_tokens=8192,
        rag_answer_timeout_seconds=60,rag_answer_response_format='text',rag_prompt_max_bytes=16384,
        model_context_window_tokens=65536,model_context_overhead_tokens=4096)
    messages=[dict(role='system',content=CUSTOMER_SERVICE_SYSTEM_PROMPT),dict(role='user',
        content=customer_service_user_prompt('问题',[CustomerServiceContext('chunk','证据')],max_bytes=16384))]
    slot=dict(key='x:dense',question='问题',messages=messages,inputSha256=exp.fingerprint(messages),
        contexts=[dict(chunkId='chunk',content='证据')])
    return settings,slot


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[None,'length','bad_contract','http'])
async def test_formal_single_send_after_durable_reservation(setup,failure):
    settings,slot=setup
    ledger=exp.Ledger(exp.RUN/'ledger',[dict(kind='chat',key=slot['key'],inputSha256=slot['inputSha256'])],header={})
    calls=[]
    def handler(request):
        calls.append(request)
        assert ledger.count==1
        assert len((exp.RUN/'ledger').read_text().splitlines())==2
        body=json.loads(request.content)
        assert body['messages']==slot['messages'] and body['stream'] is False
        assert 'response_format' not in body
        return httpx.Response(500 if failure=='http' else 200,json=dict(choices=[dict(
            finish_reason='length' if failure=='length' else 'stop',message=dict(content='bad' if failure=='bad_contract' else
            json.dumps(dict(answered=True,answer='回答',citedChunkIds=['chunk']))))]))
    result={}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model=exp.AnswerModel(settings,client,ledger,slot,result)
        if failure:
            with pytest.raises(Exception):
                await model.answer_customer_service('问题',[CustomerServiceContext('chunk','证据')])
        else:
            answer=await model.answer_customer_service('问题',[CustomerServiceContext('chunk','证据')])
            assert answer.answered and answer.cited_chunk_ids==('chunk',)
    assert len(calls)==1 and result['reserved'] and result['attemptedSend']
    ledger.close()


@pytest.mark.asyncio
async def test_drift_before_reservation(setup):
    settings,slot=setup
    ledger=exp.Ledger(exp.RUN/'ledger',[],header={})
    def handler(_):
        pytest.fail('不应联网')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model=exp.AnswerModel(settings,client,ledger,slot,{})
        with pytest.raises(ValueError,match='MESSAGES_CHANGED'):
            await model.answer_customer_service('不同问题',[CustomerServiceContext('chunk','证据')])
    assert ledger.count==0
    ledger.close()


def test_binding_mutation_fails(tmp_path,monkeypatch):
    monkeypatch.setattr(exp,'ROOT',tmp_path)
    p=tmp_path/'source'
    p.write_text('frozen')
    plan=dict(bindings={'source':exp.sha(p)})
    exp.check_bindings(plan)
    p.write_text('changed')
    with pytest.raises(ValueError,match='INPUT_CHANGED'):
        exp.check_bindings(plan)


def example():
    card=dict(blindId='opaque',facts=[dict(id='f')],answer=dict(answer='回答',answered=True,cited_chunk_ids=['c']),
        contexts=[dict(chunkId='c',content='证据')])
    package=dict(cards=[card])
    row=dict(blindId='opaque',status='complete',claimsComplete=True,erroneousAssertion=False,refusalCorrect=True,
        notes='',facts=[dict(id='f',verdict='met',answerQuote='回答')],
        claims=[dict(text='回答',contextSupported=True,citationSupported=True,evidence=[dict(chunkId='c',quote='证据')])])
    reviews=dict(packageSha256=exp.fingerprint(package),reviewer=dict(name='代理',role='agent'),reviews=[row])
    return package,reviews


@pytest.mark.parametrize('mutation',['duplicate','pending','quote','citation','evidence','missingfact'])
def test_review_rejects_untraceable_or_incomplete(mutation):
    package,reviews=example()
    review.validate(package,reviews)
    row=reviews['reviews'][0]
    if mutation=='duplicate': reviews['reviews'].append(deepcopy(row))
    if mutation=='pending': row['status']='pending'
    if mutation=='quote': row['facts'][0]['answerQuote']='伪造'
    if mutation=='citation': row['claims'][0]['evidence'][0]['chunkId']='other'
    if mutation=='evidence': row['claims'][0]['evidence']=[]
    if mutation=='missingfact': row['facts']=[]
    with pytest.raises(ValueError): review.validate(package,reviews)


def test_paired_question_statistics():
    assert review.paired([0.,0.])['ci95']==[0.,0.]
    assert review.paired([1.,1.])['delta']==1.
    assert review.paired([])['n']==0


@pytest.mark.asyncio
async def test_answer_stops_first_failure_and_cannot_resume(setup,monkeypatch):
    settings,slot=setup
    entries=[dict(id=f'q{i}') for i in range(114)]
    plan=dict(oldCaseIds=[e['id'] for e in entries[:90]],newCases=entries[90:],bindings={})
    monkeypatch.setattr(exp,'load_plan',lambda *_:plan)
    monkeypatch.setattr(exp,'config',lambda _:dict(synthetic=True))
    exp.durable_json(exp.RUN/'retrieval-report.json',{})
    (exp.RUN/'authorization.jsonl').write_text('synthetic\n')
    slots=[dict(slot,key=op['key']) for op in exp.schedule(entries,'chat')]
    frozen=dict(planSha256='digest',config=dict(synthetic=True),slots=slots,
        retrievalReportSha256=exp.sha(exp.RUN/'retrieval-report.json'),
        retrievalLedgerSha256=exp.sha(exp.RUN/'authorization.jsonl'))
    exp.durable_json(exp.RUN/'answer-plan.json',frozen)
    calls=[]
    async def fake_answer(self,*_):
        calls.append(self.slot['key'])
        self.ledger.reserve(dict(kind='chat',key=self.slot['key'],inputSha256=self.slot['inputSha256']))
        raise ValueError('synthetic first error')
    monkeypatch.setattr(exp.AnswerModel,'answer_customer_service',fake_answer)
    with pytest.raises(ValueError):
        await exp.answer(settings,'digest',exp.fingerprint(frozen))
    report=exp.read_json(exp.RUN/'answer-report.json')
    assert len(calls)==1 and report['reservedOperations']==1
    assert report['results'][0]['status']=='failed'
    assert all(r['status']=='not_run' for r in report['results'][1:])
    with pytest.raises((ValueError,FileExistsError)):
        await exp.answer(settings,'digest',exp.fingerprint(frozen))
    assert len(calls)==1
