"""盲包及人工导入均离线测试，错误与缺项不得抬高固定分母成绩。"""
from copy import deepcopy
import json
import uuid

from pydantic import ValidationError
import pytest

from ai_service.orchestration.rag_blind_review import (
    export_blind, paired_statistics, summarize, validate_reviews,
)
from ai_service.orchestration.rag_rerank_contracts import ARMS, ROOT, fingerprint, load_rubric, read_json
from ai_service.orchestration.rag_rerank_experiment import new_report


@pytest.fixture(scope='module')
def package():
    rubric = load_rubric()
    report = new_report(rubric)
    for row in report['results']:
        row.update(status='error', errorCode='MODEL_PARSE_FAILED')
    first = report['results'][0]
    first.update(status='ok', answered=True, answer='有据答案',
        contexts=[{'chunkId': 'c1', 'documentId': 'd1', 'documentVersion': 1,
                   'locator': '小节', 'content': '完整有据答案原文'}],
        citedChunkIds=['c1'], sources=[{'chunk_id': 'c1'}], degraded=False,
        trace={'terminal': {'outcome': 'answered', 'reasonCode': 'ANSWERED'}})
    path = ROOT / 'tmp/rag-verification-20261118/tool-validation' / ('blind-fixture-' + uuid.uuid4().hex)
    public = export_blind(report, path, rubric)
    private = read_json(path / 'private-mapping.json')
    template = read_json(path / 'reviews-template.json')
    return rubric, report, public, private, template


def complete_all(template, public):
    raw = deepcopy(template)
    raw['reviewer'] = {'name': '离线测试代理', 'role': 'agent'}
    by_id = {c['blindId']: c for c in public['cards']}
    for review in raw['reviews']:
        card = by_id[review['blindId']]
        review.update(status='complete', claimsComplete=True, answerRelevance=0,
                      factualError=False, safetyViolation=False, annotationDispute=False)
        for fact in review['facts']:
            fact['verdict'] = 'missing'
        if card['status'] == 'ok':
            review['answerRelevance'] = 2
            for fact in review['facts']:
                fact.update(verdict='met', answerQuote='有据答案')
            review['claims'] = [{'text': '有据答案', 'supported': True,
                                 'evidence': [{'chunkId': 'c1', 'quote': '有据答案'}]}]
            review['citations'] = [{'chunkId': 'c1', 'supported': True, 'claimTexts': ['有据答案']}]
    return raw


def test_all_180_blind_cards_and_templates_have_no_arm_or_private_timing(package):
    rubric, report, public, private, template = package
    assert len(public['cards']) == 180
    assert len({c['blindId'] for c in public['cards']}) == 180
    text = json.dumps(public, ensure_ascii=False)
    for forbidden in ('rerank_off', 'rerank_on', 'shuffleSeed', 'actualMessages',
                      'answerLatencyMs', 'realBgeLatencyMs', 'rerankerProvider', '"arm"'):
        assert forbidden not in text
        assert forbidden not in json.dumps(template)
    assert {m['arm'] for m in private['mapping'].values()} == set(ARMS)
    assert all(r['status'] == 'pending' and r['answerRelevance'] is None for r in template['reviews'])
    assert public['humanGold'] is False
    validate_reviews(template, public)


def test_pending_human_review_never_produces_quality_improvement(package):
    rubric, report, public, private, template = package
    result = summarize(report, public, private, template, rubric)
    assert result['humanReview'] == 'pending'
    assert result['paired'] is None and result['humanOverallQualityImprovement'] is None
    assert all(m['overallPassRate'] is None for m in result['arms'].values())
    assert sum(m['errors'] for m in result['arms'].values()) == 179
    assert sum(m['correctContractRefusals'] for m in result['arms'].values()) == 0


def test_agent_review_and_complete_fixed_denominator_do_not_become_human_gold(package):
    rubric, report, public, private, template = package
    raw = complete_all(template, public)
    result = summarize(report, public, private, raw, rubric)
    assert result['humanReview'] == 'pending' and result['qualityStatus'] == 'complete'
    assert result['humanOverallQualityImprovement'] is None
    assert result['paired']['questionPairs'] == 90
    assert result['paired']['bothFail'] == 89
    assert result['paired']['offOnly'] == 1
    assert result['arms'][ARMS[0]]['overallPassRate'] == 1 / 90
    assert result['arms'][ARMS[0]]['requiredPublicFactsDenominator'] == 130


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'fact', 'fact_duplicate',
    'boolean_score', 'string_bool', 'extra', 'reviewer', 'role', 'pending_score',
    'answer_quote', 'claim_quote', 'claim_text', 'citation', 'claims_incomplete',
    'claims_duplicate', 'unsupported_with_quote'])
def test_malformed_or_untraceable_reviews_rejected(package, damage):
    _, _, public, _, template = package
    raw = complete_all(template, public)
    success = next(r for r in raw['reviews'] if r['claims'])
    if damage == 'missing': raw['reviews'].pop()
    if damage == 'duplicate': raw['reviews'][1] = deepcopy(raw['reviews'][0])
    if damage == 'fact': success['facts'][0]['factId'] = 'unknown'
    if damage == 'fact_duplicate': success['facts'].append(deepcopy(success['facts'][0]))
    if damage == 'boolean_score': success['answerRelevance'] = True
    if damage == 'string_bool': success['safetyViolation'] = 'false'
    if damage == 'extra': success['overallPass'] = True
    if damage == 'reviewer': raw.pop('reviewer')
    if damage == 'role': raw['reviewer']['role'] = 'model-as-human'
    if damage == 'pending_score': success['status'] = 'pending'
    if damage == 'answer_quote': success['facts'][0]['answerQuote'] = '答案没有说'
    if damage == 'claim_quote': success['claims'][0]['evidence'][0]['quote'] = '输入没有给'
    if damage == 'claim_text': success['claims'][0]['text'] = '答案没有说'
    if damage == 'citation': success['citations'][0]['chunkId'] = 'unknown'
    if damage == 'claims_incomplete': success['claimsComplete'] = False
    if damage == 'claims_duplicate': success['claims'] *= 2
    if damage == 'unsupported_with_quote': success['claims'][0]['supported'] = False
    with pytest.raises((ValueError, ValidationError)):
        validate_reviews(raw, public)


def test_protocol_refusal_uses_no_answer_quotes_and_service_error_fails_contract(package):
    _, _, public, _, template = package
    raw = complete_all(template, public)
    cards = {c['blindId']: c for c in public['cards']}
    protocol = next(r for r in raw['reviews'] if not cards[r['blindId']]['expectedAnswerable'])
    protocol['facts'][0]['verdict'] = 'met'
    with pytest.raises(ValueError, match='PROTOCOL_VERDICT'):
        validate_reviews(raw, public)
    protocol['facts'][0]['verdict'] = 'missing'
    protocol['facts'][0]['answerQuote'] = '任意'
    with pytest.raises(ValueError, match='PROTOCOL_QUOTE'):
        validate_reviews(raw, public)


def test_real_normal_refusal_contract_is_distinct_from_fallback(package):
    rubric, report, public, private, template = deepcopy(package)
    from ai_service.orchestration.customer_service_rag import NO_ANSWER_TEXT
    protocol_card = next(c for c in public['cards'] if not c['expectedAnswerable'])
    mapped = private['mapping'][protocol_card['blindId']]
    row = next(r for r in report['results'] if r['id'] == mapped['id'] and r['arm'] == mapped['arm'])
    row.update(status='ok', answered=False, answer=NO_ANSWER_TEXT, degraded=False,
               trace={'terminal': {'outcome': 'refused', 'reasonCode': 'MODEL_REFUSED'}})
    protocol_card.update(status='ok', answered=False, answer=NO_ANSWER_TEXT)
    private.update(publicPackageSha256=fingerprint(public), reportSha256=fingerprint(report))
    template['publicPackageSha256'] = fingerprint(public)
    raw = complete_all(template, public)
    review = next(r for r in raw['reviews'] if r['blindId'] == protocol_card['blindId'])
    review.update(answerRelevance=2, claims=[], citations=[])
    for f in review['facts']: f.update(verdict='met', answerQuote='')
    validate_reviews(raw, public)
    result = summarize(report, public, private, raw, rubric)
    assert sum(a['correctContractRefusals'] for a in result['arms'].values()) == 1


def test_mismatched_private_mapping_or_rubric_is_rejected(package):
    rubric, report, public, private, template = package
    broken = deepcopy(private)
    broken['mapping'][next(iter(broken['mapping']))]['arm'] = 'extra'
    with pytest.raises(ValueError, match='PRIVATE_MAPPING'):
        summarize(report, public, broken, template, rubric)
    broken_report = deepcopy(report)
    broken_report['results'].pop()
    with pytest.raises(ValueError, match='RESULTS_INCOMPLETE'):
        summarize(broken_report, public, private, template, rubric)


def test_paired_bootstrap_has_fixed_seed_and_question_unit():
    off = [True, True, False, False]
    on = [True, False, True, False]
    first = paired_statistics(off, on)
    assert first == paired_statistics(off, on)
    assert first['questionPairs'] == 4
    assert all(first[key] == 1 for key in ('bothPass', 'offOnly', 'onOnly', 'bothFail'))
    assert first['onMinusOff'] == 0
    assert paired_statistics([False] * 90, [False] * 90)['bootstrap95'] == [0, 0]
    with pytest.raises(ValueError): paired_statistics([True], [1])


@pytest.mark.parametrize('damage', ['fallback', 'missing_trace', 'degraded', 'foreign_source'])
def test_fallback_or_missing_trace_cannot_be_exported_as_success(package, damage):
    from ai_service.orchestration.rag_blind_review import validate_results
    rubric, report, _, _, _ = deepcopy(package)
    row = report['results'][0]
    if damage == 'fallback':
        from ai_service.orchestration.customer_service_rag import SYSTEM_FALLBACK_TEXT
        row.update(answered=False, answer=SYSTEM_FALLBACK_TEXT, sources=[], citedChunkIds=[],
            trace={'terminal': {'outcome': 'refused'}})
    if damage == 'missing_trace': row.pop('trace')
    if damage == 'degraded': row['degraded'] = True
    if damage == 'foreign_source': row['sources'][0]['chunk_id'] = 'unknown'
    with pytest.raises(ValueError, match='RESULT_TRACE_OR_REFUSAL'):
        validate_results(report, rubric)
