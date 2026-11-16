"""评估流程专项测试，防止缺失、伪引用或候选策略造成虚高成绩。"""
import json

import pytest

from ai_service.orchestration.rag_evaluation_workflow import (
    DEFAULT_DATASET, aggregate, analyze_study, bounded_contexts, evidence_metrics,
    load_study, select_contexts, standard_samples,
)
from ai_service.orchestration.rag_snapshot_experiment import CallBudget, safe_output
from ai_service.orchestration.rag_evaluation_judge import parse_judgment


@pytest.fixture
def study():
    return load_study()


def test_new_dataset_preserves_old_suite_and_checks_all_exact_evidence(study):
    data, chunks = study
    assert len(data['entries']) == 48
    assert sum(e['expectedAnswerable'] for e in data['entries']) == 40
    assert sum(len(e['requiredFacts']) for e in data['entries']) == 63
    assert len(chunks) == 72
    assert len({e['groupId'] for e in data['entries']}) == 24
    assert {e['partition'] for e in data['entries']} == {'diagnostic'}
    assert all(e['reviewStatus'].endswith('human-review-pending') for e in data['entries'])


@pytest.mark.parametrize('damage', ['hash', 'span', 'duplicate', 'split', 'empty-facts'])
def test_invalid_data_fails_before_external_initialization(tmp_path, damage):
    data = json.loads(DEFAULT_DATASET.read_text(encoding='utf8'))
    if damage == 'hash': data['corpusHashes']['01-platform-account'] = '0'*64
    if damage == 'span': data['entries'][0]['requiredFacts'][0]['evidence'][0]['quote'] = '未出现的证据'
    if damage == 'duplicate': data['entries'][1]['question'] = data['entries'][0]['question']
    if damage == 'split': data['entries'][1]['partition'] = 'holdout'
    if damage == 'empty-facts': data['entries'][0]['requiredFacts'] = []
    path = tmp_path/'dataset.json'
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
    with pytest.raises(ValueError, match='RAG_EVAL_'):
        load_study(path)


def row(key, content, doc='a', locator='section'):
    return {'chunkId':key,'documentId':doc,'locator':locator,'content':content}


def entry():
    return {'id':'test','question':'格式与生效条件？','expectedAnswerable':True,
            'requiredFacts':[
                {'id':'f1','statement':'支持PDF','evidence':[{'documentId':'a','locator':'section','quote':'PDF'}]},
                {'id':'f2','statement':'等待索引','evidence':[{'documentId':'a','locator':'section','quote':'等待'}]}]}


def test_same_document_complementary_evidence_and_duplicate_identity():
    rows = [row('a1','PDF'),row('a1','PDF'),row('a2','等待'),row('b1','别的',doc='b')]
    baseline = select_contexts(rows, per_document=1)
    candidate = select_contexts(rows, per_document=2)
    assert [r['chunkId'] for r in baseline] == ['a1','b1']
    assert [r['chunkId'] for r in candidate] == ['a1','a2','b1']
    assert evidence_metrics(entry(), baseline, corpus_chunks=rows)['evidenceCoverage'] == .5
    assert evidence_metrics(entry(), candidate, corpus_chunks=rows)['evidenceCoverage'] == 1


def test_ir_formulas_use_full_annotated_denominator_and_fixed_k():
    rows = [row('noise','无关',doc='b'),row('a1','PDF'),row('a2','等待')]
    result = evidence_metrics(entry(), rows, k=2, corpus_chunks=rows)
    assert result['annotatedPrecisionAtK'] == .5
    assert result['annotatedRecallAtK'] == .5
    assert result['mrrAtK'] == .5
    assert result['apAtK'] == .25
    assert evidence_metrics(entry(), [], corpus_chunks=rows)['evidenceCoverage'] == 0


def test_wrong_locator_or_truncated_span_does_not_preserve_necessary_fact():
    correct = row('a1','支持PDF，等待索引')
    wrong = row('a1','支持PDF，等',locator='wrong')
    assert evidence_metrics(entry(),[wrong],corpus_chunks=[correct])['evidenceCoverage'] == 0
    clipped = {**correct,'content':'支持PDF，等'}
    assert evidence_metrics(entry(),[clipped],corpus_chunks=[correct])['evidenceCoverage'] == .5


def test_real_character_and_json_budget_keep_identity_and_bound_text():
    rows = [row(str(i), '中文"\\🙂'*900,doc=str(i)) for i in range(3)]
    contexts = bounded_contexts('问题',rows,max_bytes=2000)
    assert contexts and sum(len(r['content']) for r in contexts) <= 3000
    assert all(len(r['content'])<=1200 for r in contexts)
    from ai_service.prompts.customer_service import customer_service_user_prompt
    from ai_service.models.base import CustomerServiceContext
    prompt = customer_service_user_prompt('问题',[CustomerServiceContext(r['chunkId'],r['content']) for r in contexts],max_bytes=2000)
    assert len(prompt.encode()) <= 2000
    assert {r['chunkId'] for r in contexts} <= {'0','1','2'}


def test_missing_and_error_refusal_are_never_correct_refusal():
    data={'entries':[entry(),{**entry(),'id':'refuse','expectedAnswerable':False,'requiredFacts':[]}]}
    cases=[{'id':'refuse','arm':'baseline','status':'error','answered':False}]
    report=aggregate(data,cases)
    assert report['baseline']['correctRefusal']==0
    assert report['baseline']['refusalDenominator']==1
    assert report['baseline']['errors']==1
    assert report['two_chunks']['missingResults']==2
    assert report['baseline']['faithfulnessClaimsMicro'] is None


def test_successful_refusal_is_counted_and_false_refusal_does_not_answer():
    data={'entries':[entry(),{**entry(),'id':'refuse','expectedAnswerable':False,'requiredFacts':[]}]}
    cases=[{'id':e['id'],'arm':'baseline','status':'ok','answered':False,'answerLatencyMs':1}
           for e in data['entries']]
    report=aggregate(data,cases)['baseline']
    assert report['correctRefusal']==1 and report['answerableAnswered']==0


def test_budget_is_preallocated_persistent_nonrefundable_and_never_overwritten(tmp_path):
    path=tmp_path/'calls.jsonl'
    budget=CallBudget(path,max_chat=1)
    budget.reserve('chat','failed-call')
    assert json.loads(path.read_text().splitlines()[-1])['key']=='failed-call'
    with pytest.raises(ValueError,match='EXCEEDED'): budget.reserve('chat','retry')
    with pytest.raises(FileExistsError): CallBudget(path,max_chat=1)
    assert len(path.read_text().splitlines())==2


def test_output_cannot_escape_ignored_directory(tmp_path):
    with pytest.raises(ValueError,match='OUTPUT'): safe_output(tmp_path/'public.json')


def test_baseline_selector_matches_production_on_distinct_documents():
    from ai_service.orchestration.customer_service_rag import _select_documents
    from ai_service.infrastructure.milvus_knowledge import RetrievedChunk
    from ai_service.models.reranker import RerankedChunk
    rows=[row('a1','正文'),row('a2','正文'),row('b1','正文',doc='b'),row('c1','正文',doc='c')]
    ranked=[RerankedChunk(RetrievedChunk(r['chunkId'],r['documentId'],1,0,r['content'],
             'test.md','md',r['locator'],'hash',.8,.8,True,1),.8) for r in rows]
    selected,_=_select_documents(ranked,3)
    assert [r['chunkId'] for r in select_contexts(rows,per_document=1)] == [r.chunk.chunk_id for r in selected]


def judgment_fixture():
    result={'answered':True,'answer':'支持PDF','contexts':[row('a1','支持PDF')], 'citedChunkIds':['a1']}
    verdict={'claims':[{'text':'支持PDF','supported':True,'chunkId':'a1','quote':'支持PDF'}],
             'coveredFactIds':['f1'],'answerRelevance':1,'citationSupport':'supported'}
    return result,verdict


def test_judge_contract_keeps_fact_and_claim_denominators_separate():
    result,verdict=judgment_fixture()
    parsed=parse_judgment(json.dumps(verdict),entry(),result)
    assert parsed['supportedClaims']==1 and parsed['requiredFactCoverage']==.5
    assert parsed['answerRelevance']==.5


@pytest.mark.parametrize('damage',['quote','claim','fact','empty','unsupported'])
def test_judge_invalid_semantic_evidence_is_error_not_perfect_score(damage):
    result,verdict=judgment_fixture()
    if damage=='quote': verdict['claims'][0]['quote']='其他文档原文'
    if damage=='claim': verdict['claims'][0]['text']='回答没有说过'
    if damage=='fact': verdict['coveredFactIds']=['f404']
    if damage=='empty': verdict['claims']=[]
    if damage=='unsupported': verdict['claims'][0]['supported']=False
    with pytest.raises(ValueError): parse_judgment(json.dumps(verdict),entry(),result)


def test_judge_boolean_score_and_repeated_claim_cannot_inflate_score():
    result, verdict = judgment_fixture()
    verdict['answerRelevance'] = True
    with pytest.raises(ValueError): parse_judgment(json.dumps(verdict),entry(),result)
    verdict['answerRelevance'] = 2
    verdict['claims'] *= 2
    with pytest.raises(ValueError): parse_judgment(json.dumps(verdict),entry(),result)


def test_export_keeps_errors_null_and_uses_final_context_not_original_document():
    e = {**entry(), 'groupId':'g', 'referenceAnswer':'支持PDF，等待', 'reviewStatus':'pending'}
    data = {'entries':[e], 'datasetVersion':'v2'}
    cases = [{'id':'test','arm':'baseline','status':'error','contexts':[row('a','仅实际片段')]}]
    samples = standard_samples(data,cases)
    assert samples[0]['response'] is None
    assert samples[0]['retrieved_contexts'] == ['仅实际片段']
    summary = analyze_study(data, cases)
    assert summary['metrics']['baseline']['errors'] == 1
    assert 'g' in summary['byGroup']
