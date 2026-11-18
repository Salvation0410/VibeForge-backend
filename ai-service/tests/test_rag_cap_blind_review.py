"""配额答案盲审只使用公开标注与合成结果，不连接模型或历史本机产物。"""
from copy import deepcopy
import json
import shutil
import uuid

from pydantic import ValidationError
import pytest

from ai_service.orchestration.rag_blind_review import (
    export_blind, paired_statistics, summarize, validate_reviews,
)
from ai_service.orchestration.rag_rerank_contracts import ROOT, RUBRIC, fingerprint, read_json

OLD_STUDY = 'rag-rerank-20261118-first-round-v1'
CAP_STUDY = 'rag-cap-20261118-answer-round-v1'
OLD_ARMS = ('rerank_off', 'rerank_on')
CAP_ARMS = ('cap1', 'cap2')


def make_report(study_id=CAP_STUDY, arms=CAP_ARMS):
    # 读取公开结构即可测试盲包；不依赖冻结源码字节或真实请求的本机文件。
    rubric = read_json(RUBRIC)
    report = {'studyId': study_id, 'rubricSha256': fingerprint(rubric),
        'callsReserved': 1, 'results': [
            {'id': entry['id'], 'arm': arm, 'status': 'error', 'contexts': [],
             'answered': None, 'answer': None, 'sources': [], 'citedChunkIds': []}
            for entry in rubric['entries'] for arm in arms],
        'retrieval': {'historical': {'sharedRetrievalLatencyMs': 111, 'realBgeLatencyMs': 222}}}
    report['results'][1].update(status='ok', answered=True, answer='有据答案',
        contexts=[{'chunkId': 'c1', 'documentId': 'd1', 'documentVersion': 1,
                   'locator': '小节', 'content': '完整有据答案原文', 'score': 999}],
        sources=[{'chunk_id': 'c1'}], citedChunkIds=['c1'], degraded=False,
        trace={'terminal': {'outcome': 'answered'}}, answerLatencyMs=12,
        usage={'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12},
        perDocument=2, executionOrder=1, actualMessages=['私有输入'])
    report['results'][2]['status'] = 'not_run'
    return rubric, report


@pytest.fixture(scope='module')
def packages():
    paths = []
    def create(study_id=CAP_STUDY, arms=CAP_ARMS, *, report=None, output=None):
        rubric, default_report = make_report(study_id, arms)
        report = default_report if report is None else report
        path = output or ROOT / 'tmp/rag-cap-answer-20261118/tool-validation' / ('blind-fixture-' + uuid.uuid4().hex)
        paths.append(path)
        public = export_blind(report, path, rubric)
        return rubric, report, public, read_json(path / 'private-mapping.json'), read_json(path / 'reviews-template.json')
    yield create
    for path in paths:
        # 仅清理本 fixture 创建且仍在仓库 tmp 下的目录，不触及验收账本。
        assert path.resolve().is_relative_to((ROOT / 'tmp').resolve())
        if path.exists():
            shutil.rmtree(path)


@pytest.fixture(scope='module')
def cap_package(packages):
    return packages()


def complete_reviews(template, public, role='agent'):
    raw = deepcopy(template)
    raw['reviewer'] = {'name': '离线合成评审', 'role': role}
    cards = {card['blindId']: card for card in public['cards']}
    for review in raw['reviews']:
        card = cards[review['blindId']]
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


def test_new_study_exports_180_cards_and_strict_pending_template(cap_package):
    rubric, _, public, private, template = cap_package
    assert {package['studyId'] for package in (public, private, template)} == {CAP_STUDY}
    assert len(public['cards']) == len(private['mapping']) == len(template['reviews']) == 180
    assert len({card['blindId'] for card in public['cards']}) == 180
    assert {arm: sum(m['arm'] == arm for m in private['mapping'].values()) for arm in CAP_ARMS} == {'cap1': 90, 'cap2': 90}
    text = json.dumps([public, template], ensure_ascii=False)
    for forbidden in ('cap1', 'cap2', 'rerank_on', 'rerank_off', 'perDocument', 'score',
            'usage', 'LatencyMs', 'shuffleSeed', 'executionOrder', 'actualMessages', '"arm"', '"mapping"'):
        assert forbidden not in text
    assert len([c for c in public['cards'] if c['status'] == 'error']) == 178
    assert len([c for c in public['cards'] if c['status'] == 'not_run']) == 1
    success = next(c for c in public['cards'] if c['status'] == 'ok')
    assert success['answer'] == '有据答案' and success['sources'] == [{'chunk_id': 'c1'}]
    entry = rubric['entries'][0]
    assert success['question'] == entry['question']
    assert success['requiredFacts'][0]['statement'] == entry['requiredFacts'][0]['statement']
    assert success['contexts'][0]['content'] == '完整有据答案原文'
    assert validate_reviews(template, public).studyId == CAP_STUDY


@pytest.mark.parametrize(('study_id', 'arms'), [
    (OLD_STUDY, CAP_ARMS), (CAP_STUDY, OLD_ARMS),
    (OLD_STUDY, ('rerank_off', 'cap2')), (CAP_STUDY, ('cap1', 'rerank_on')),
    (CAP_STUDY, ('cap1', 'cap3')), (OLD_STUDY, ('rerank_off', 'arbitrary')),
    ('unknown-study', CAP_ARMS), ('unknown-study', OLD_ARMS),
])
def test_export_rejects_crossed_unknown_studies_and_arms(study_id, arms):
    rubric, report = make_report(study_id, arms)
    path = ROOT / 'tmp/rag-cap-answer-20261118/tool-validation' / ('rejected-' + uuid.uuid4().hex)
    with pytest.raises(ValueError):
        export_blind(report, path, rubric)
    assert not path.exists()


@pytest.mark.parametrize(('study_id', 'arms', 'other'), [
    (CAP_STUDY, CAP_ARMS, OLD_STUDY), (OLD_STUDY, OLD_ARMS, CAP_STUDY),
])
@pytest.mark.parametrize('layer', ['report', 'public', 'private', 'reviews'])
def test_summarize_rejects_each_cross_study_layer_even_with_recomputed_hashes(packages, study_id, arms, other, layer):
    rubric, report, public, private, reviews = deepcopy(packages(study_id, arms))
    {'report': report, 'public': public, 'private': private, 'reviews': reviews}[layer]['studyId'] = other
    # 摘要只能保证字节关联；不能以重算摘要绕过研究与实验臂的语义边界。
    private.update(reportSha256=fingerprint(report), publicPackageSha256=fingerprint(public))
    reviews['publicPackageSha256'] = fingerprint(public)
    with pytest.raises(ValueError):
        summarize(report, public, private, reviews, rubric)


@pytest.mark.parametrize('layer', ['report', 'public', 'private', 'reviews'])
def test_summarize_rejects_unknown_study_at_every_layer(cap_package, layer):
    rubric, report, public, private, reviews = deepcopy(cap_package)
    {'report': report, 'public': public, 'private': private, 'reviews': reviews}[layer]['studyId'] = 'unknown-study'
    private.update(reportSha256=fingerprint(report), publicPackageSha256=fingerprint(public))
    reviews['publicPackageSha256'] = fingerprint(public)
    with pytest.raises(ValueError):
        summarize(report, public, private, reviews, rubric)


@pytest.mark.parametrize(('public_study', 'review_study'), [
    (CAP_STUDY, OLD_STUDY), (OLD_STUDY, CAP_STUDY),
    ('unknown-study', CAP_STUDY), (CAP_STUDY, 'unknown-study'),
    ('unknown-study', 'unknown-study'),
])
def test_review_import_checks_study_identity_beyond_package_hash(cap_package, public_study, review_study):
    _, _, public, _, reviews = deepcopy(cap_package)
    public['studyId'], reviews['studyId'] = public_study, review_study
    reviews['publicPackageSha256'] = fingerprint(public)
    with pytest.raises(ValueError):
        validate_reviews(reviews, public)


@pytest.mark.parametrize('damage', ['extra', 'boolean_score', 'string_bool', 'pending_score'])
def test_new_study_reviews_keep_strict_forbid_contract(cap_package, damage):
    _, _, public, _, reviews = deepcopy(cap_package)
    first = reviews['reviews'][0]
    if damage == 'extra': reviews['cap'] = 2
    if damage == 'boolean_score': first['answerRelevance'] = True
    if damage == 'string_bool': first['safetyViolation'] = 'false'
    if damage == 'pending_score': first['answerRelevance'] = 2
    with pytest.raises((ValueError, ValidationError)):
        validate_reviews(reviews, public)


def test_cap_pending_has_no_quality_improvement_and_keeps_fixed_denominators(cap_package):
    rubric, report, public, private, reviews = cap_package
    result = summarize(report, public, private, reviews, rubric)
    assert result['schemaVersion'] == 'rag-cap-analysis/v1'
    assert result['studyId'] == CAP_STUDY
    assert result['qualityStatus'] == result['humanReview'] == 'pending'
    assert result['paired'] is result['humanOverallQualityImprovement'] is None
    assert set(result['arms']) == set(CAP_ARMS)
    for metrics in result['arms'].values():
        assert metrics['denominator'] == metrics['pending'] == 90
        assert metrics['answerableDenominator'] == 81 and metrics['refusalDenominator'] == 9
        assert metrics['requiredPublicFactsDenominator'] == 130
        assert metrics['overallPassRate'] is metrics['publicFactCoverage'] is None
    assert sum(m['errors'] for m in result['arms'].values()) == 178
    assert sum(m['notRun'] for m in result['arms'].values()) == 1


@pytest.mark.parametrize('role', ['agent', 'human'])
def test_complete_cap_reviews_use_cap_pair_keys_and_human_role_gate(cap_package, role):
    rubric, report, public, private, template = cap_package
    reviews = complete_reviews(template, public, role)
    validate_reviews(reviews, public)
    result = summarize(report, public, private, reviews, rubric)
    assert result['qualityStatus'] == 'complete'
    assert result['humanReview'] == ('complete' if role == 'human' else 'pending')
    assert result['humanOverallQualityImprovement'] == (1 / 90 if role == 'human' else None)
    paired = result['paired']
    assert paired['questionPairs'] == 90 and paired['bothFail'] == 89
    assert paired['cap1Only'] == 0 and paired['cap2Only'] == 1
    assert paired['cap2MinusCap1'] == 1 / 90
    assert paired['bootstrapSeed'] == 20261118 and paired['bootstrapDraws'] == 10000
    assert not {'offOnly', 'onOnly', 'onMinusOff'} & set(paired)
    assert result['arms']['cap1']['overallPassRate'] == 0
    assert result['arms']['cap2']['overallPassRate'] == 1 / 90
    old = paired_statistics([False] * 90, [True] + [False] * 89)
    assert paired['bootstrap95'] == old['bootstrap95']


def test_partial_human_cap_review_does_not_report_improvement(cap_package):
    rubric, report, public, private, template = cap_package
    reviews = complete_reviews(template, public, 'human')
    reviews['reviews'][0] = deepcopy(template['reviews'][0])
    result = summarize(report, public, private, reviews, rubric)
    assert result['humanReview'] == result['qualityStatus'] == 'pending'
    assert result['paired'] is result['humanOverallQualityImprovement'] is None


def test_cap_timing_counts_only_new_answers_and_marks_historical_replay(cap_package):
    rubric, report, public, private, reviews = cap_package
    result = summarize(report, public, private, reviews, rubric)
    assert result['sharedRetrievalLatencyMs'] == result['realBgeLatencyMs'] == {'count': 0, 'mean': None, 'p95': None}
    assert result['arms']['cap2']['answerLatencyMs'] == {'count': 1, 'mean': 12, 'p95': 12}
    assert result['arms']['cap1']['answerLatencyMs']['count'] == 0
    meaning = result['timingMeaning']
    assert all(word in meaning for word in ('历史', 'BGE', '回放', '新回答', '当前检索', '在线E2E'))


def test_old_study_keeps_output_and_statistics_keys_with_synthetic_rubric(packages):
    rubric, report, public, private, reviews = packages(OLD_STUDY, OLD_ARMS)
    validate_reviews(reviews, public)
    result = summarize(report, public, private, complete_reviews(reviews, public), rubric)
    assert result['schemaVersion'] == 'rag-rerank-analysis/v1' and result['studyId'] == OLD_STUDY
    assert set(result['arms']) == set(OLD_ARMS)
    assert result['paired']['offOnly'] == 0 and result['paired']['onOnly'] == 1
    assert result['paired']['onMinusOff'] == 1 / 90
    assert not {'cap1Only', 'cap2Only', 'cap2MinusCap1'} & set(result['paired'])
    assert result['sharedRetrievalLatencyMs'] == {'count': 1, 'mean': 111, 'p95': 111}
    assert result['realBgeLatencyMs'] == {'count': 1, 'mean': 222, 'p95': 222}
    assert result['timingMeaning'] == '共享检索/BGE准备与回答耗时分账，不是两次在线E2E'


PUBLIC_SOURCE = {'chunk_id': 'c1', 'document_id': 'd1', 'document_name': '公开文档',
                 'document_version': 1, 'locator': '小节', 'excerpt': '有据答案'}


@pytest.mark.parametrize(('study_id', 'arms'), [(OLD_STUDY, OLD_ARMS), (CAP_STUDY, CAP_ARMS)])
def test_source_projection_keeps_public_metadata_without_nested_private_diagnostics(packages, study_id, arms):
    rubric, report = make_report(study_id, arms)
    diagnostics = {'arm': arms[1], 'perDocument': 2, 'score': .91,
        'usage': {'input_tokens': 123, 'arm': arms[1]}, 'answerLatencyMs': 456,
        'executionOrder': 7, 'privateMapping': {'c1': arms[1]},
        'diagnostics': {'nested': {'arm': arms[1], 'quota': 2}}}
    report['results'][1]['sources'] = [{**PUBLIC_SOURCE, **diagnostics}]
    original = deepcopy(report)
    path = ROOT / 'tmp/rag-cap-answer-20261118/tool-validation' / ('blind-fixture-' + uuid.uuid4().hex)
    _, returned_report, public, private, template = packages(study_id, arms, report=report, output=path)
    assert returned_report is report and report == original
    success = next(card for card in public['cards'] if card['status'] == 'ok')
    assert success['sources'] == [PUBLIC_SOURCE]
    assert read_json(path / 'cards.json') == public
    for text in (json.dumps(public, ensure_ascii=False), (path / 'cards.md').read_text(encoding='utf8')):
        for field in diagnostics:
            assert '"' + field + '"' not in text
        assert all(arm not in text for arm in (*OLD_ARMS, *CAP_ARMS))
    assert {mapping['arm'] for mapping in private['mapping'].values()} == set(arms)
    assert private['reportSha256'] == fingerprint(original)
    validate_reviews(template, public)
    assert summarize(report, public, private, template, rubric)['humanReview'] == 'pending'
    # 公共数据必须独立构造，后续审阅侧修改不能回写原始私有报告。
    success['sources'][0]['excerpt'] = '审阅侧修改'
    assert report == original


@pytest.mark.parametrize(('study_id', 'arms'), [(OLD_STUDY, OLD_ARMS), (CAP_STUDY, CAP_ARMS)])
@pytest.mark.parametrize('field', list(PUBLIC_SOURCE))
@pytest.mark.parametrize('nested', [{'arm': 'cap2', 'usage': {'total_tokens': 123}}, [{'arm': 'cap2'}]])
def test_public_source_fields_reject_nested_objects_before_export(packages, study_id, arms, field, nested):
    _, report = make_report(study_id, arms)
    report['results'][1]['sources'] = [{**PUBLIC_SOURCE, field: deepcopy(nested)}]
    original = deepcopy(report)
    path = ROOT / 'tmp/rag-cap-answer-20261118/tool-validation' / ('blind-fixture-' + uuid.uuid4().hex)
    with pytest.raises(ValueError):
        packages(study_id, arms, report=report, output=path)
    assert not path.exists()
    assert report == original


@pytest.mark.parametrize(('study_id', 'arms'), [(OLD_STUDY, OLD_ARMS), (CAP_STUDY, CAP_ARMS)])
@pytest.mark.parametrize(('field', 'value'), [('document_version', True), ('document_version', '1'),
                                            ('excerpt', 7), ('locator', None)])
def test_public_source_fields_reject_non_contract_scalar_types(packages, study_id, arms, field, value):
    _, report = make_report(study_id, arms)
    report['results'][1]['sources'] = [{**PUBLIC_SOURCE, field: value}]
    original = deepcopy(report)
    path = ROOT / 'tmp/rag-cap-answer-20261118/tool-validation' / ('blind-fixture-' + uuid.uuid4().hex)
    with pytest.raises(ValueError):
        packages(study_id, arms, report=report, output=path)
    assert not path.exists()
    assert report == original
