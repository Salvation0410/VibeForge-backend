"""观测工具只分析本地材料；缺记录、代理评审和工程失败不能成为人工质量成绩。"""
from copy import deepcopy
import hashlib
import json

import pytest

from ai_service.orchestration import rag_failure_observation as observation
from ai_service.orchestration.rag_evidence_observation import observe_evidence
from ai_service.orchestration.rag_rerank_contracts import fingerprint
from test_rag_cap_blind_review import make_report, complete_reviews


def evidence_case():
    chunk = {'chunkId': 'c1', 'documentId': 'actual', 'documentVersion': 1,
             'locator': 'heading', 'content': '前文必要依据后文'}
    metadata = {k: chunk[k] for k in ('chunkId', 'documentId', 'documentVersion')}
    metadata.update(length=len(chunk['content']), rank=1)
    stages = {name: [deepcopy(chunk)] for name in
              ('retrieved', 'reranked', 'thresholded', 'selected', 'characterBudget', 'byteBudget')}
    trace = {'schemaVersion': 'customer-service-rag-trace/v1'}
    for name in observation.STAGES:
        item = deepcopy(metadata)
        if name in {'filtered', 'thresholded', 'selected', 'characterBudget', 'byteBudget'}:
            item['kept'] = True
        if name in {'characterBudget', 'byteBudget'}:
            item.update(beforeChars=len(chunk['content']), afterChars=len(chunk['content']))
        trace[name] = [item]
    row = {'stages': stages, 'trace': trace, 'contexts': [deepcopy(chunk)]}
    fact = {'factId': 'f1', 'basisType': 'public-document',
            'evidence': [{'documentId': 'logical', 'quote': '必要依据'}]}
    mapping = {'actual': ('logical', 1)}
    return row, {'requiredFacts': [fact]}, mapping


def test_complete_stages_preserve_snippet_without_quality_claim():
    row, entry, mapping = evidence_case()
    result = observe_evidence(row, entry, mapping)
    assert all(v['state'] == 'OBSERVED' for v in result['stages'].values())
    assert result['facts'][0]['finalSnippetPresent'] is True
    assert result['facts'][0]['firstObservedLoss'] is None
    assert 'qualityPass' not in result


@pytest.mark.parametrize('damage', ['missing', 'length', 'duplicate', 'version', 'truncated'])
def test_incomplete_retrieval_is_unknown_not_not_recalled(damage):
    row, entry, mapping = evidence_case()
    if damage == 'missing': row['stages'].pop('retrieved')
    if damage == 'length': row['stages']['retrieved'][0]['content'] = '前文'
    if damage == 'duplicate': row['stages']['retrieved'] *= 2
    if damage == 'version': row['stages']['retrieved'][0]['documentVersion'] = 2
    if damage == 'truncated': row['stagesTruncated'] = True
    result = observe_evidence(row, entry, mapping)
    assert result['stages']['retrieved']['state'] == 'UNKNOWN'
    assert result['facts'][0]['firstObservedLoss'] != 'NOT_IN_RETRIEVED'


def test_budget_loss_is_adjacent_observation_not_semantic_verdict():
    row, entry, mapping = evidence_case()
    for stage in ('characterBudget', 'byteBudget'):
        row['stages'][stage][0]['content'] = '前文'
        row['trace'][stage][0].update(length=2, afterChars=2)
    row['trace']['byteBudget'][0]['beforeChars'] = 2
    row['contexts'] = deepcopy(row['stages']['byteBudget'])
    fact = observe_evidence(row, entry, mapping)['facts'][0]
    assert fact['firstObservedLoss'] == 'characterBudget'
    assert fact['finalSnippetPresent'] is False


def test_selection_loss_and_missing_intermediate_not_invented():
    row, entry, mapping = evidence_case()
    for stage in ('selected', 'characterBudget', 'byteBudget'):
        row['stages'][stage] = []
        row['trace'][stage][0]['kept'] = False
    row['contexts'] = []
    assert observe_evidence(row, entry, mapping)['facts'][0]['firstObservedLoss'] == 'selected'
    row['stages'].pop('thresholded')
    result = observe_evidence(row, entry, mapping)
    assert result['facts'][0]['firstObservedLoss'] == 'UNKNOWN'


@pytest.mark.parametrize('reason', ['duplicate_document', 'document_chunk_limit', 'top_k', 'private-text'])
def test_selection_loss_keeps_only_known_quota_reasons(reason):
    row, entry, mapping = evidence_case()
    row['stages']['selected'] = []
    row['trace']['selected'][0].update(kept=False, dropReason=reason)
    fact = observe_evidence(row, entry, mapping)['facts'][0]
    assert fact['selectionDropReasons'] == ['UNKNOWN' if reason == 'private-text' else reason]


def test_final_context_mismatch_is_unknown():
    row, entry, mapping = evidence_case()
    row['contexts'][0]['content'] = '不同内容'
    assert observe_evidence(row, entry, mapping)['facts'][0]['finalSnippetPresent'] is None


@pytest.mark.parametrize('field', ['documentId', 'chunkId', 'documentVersion'])
def test_malformed_stage_identity_cannot_crash_or_become_absence(field):
    row, entry, mapping = evidence_case()
    row['stages']['retrieved'][0][field] = []
    assert observe_evidence(row, entry, mapping)['stages']['retrieved']['state'] == 'UNKNOWN'


def test_missing_mapping_and_trace_limit_are_unknown():
    row, entry, mapping = evidence_case()
    assert all(s['state'] == 'UNKNOWN' for s in observe_evidence(row, entry, {})['stages'].values())
    row['trace']['retrieved'] *= 100
    assert observe_evidence(row, entry, mapping)['stages']['retrieved']['state'] == 'UNKNOWN'


def test_omitted_dropped_candidate_metadata_is_unknown():
    row, entry, mapping = evidence_case()
    row['stages']['selected'] = []
    row['trace']['selected'] = []
    assert observe_evidence(row, entry, mapping)['stages']['selected']['state'] == 'UNKNOWN'


def test_observed_empty_retrieval_is_distinct_from_missing_stage():
    row, entry, mapping = evidence_case()
    row['stages']['retrieved'] = []
    row['trace']['retrieved'] = []
    result = observe_evidence(row, entry, mapping)
    assert result['stages']['retrieved']['state'] == 'OBSERVED'
    assert result['facts'][0]['firstObservedLoss'] == 'NOT_IN_RETRIEVED'


def report_case():
    rubric, report = make_report('rag-json-answer-20261119-v1', ('text', 'json_object'))
    report['schemaVersion'] = 'rag-json-answer-experiment/v1'
    return rubric, report


def test_engineering_counts_fixed_denominator_and_unknown_sanitized():
    rubric, report = report_case()
    report['results'][0].update(status='error', errorCode='private-provider-content')
    report['results'][1].update(status='not_run')
    result = observation.analyze(report, rubric, {})
    assert sum(a['denominator'] for a in result['arms'].values()) == 180
    assert sum(a['statuses']['error'] for a in result['arms'].values()) == 178
    assert sum(a['statuses']['not_run'] for a in result['arms'].values()) == 2
    assert 'private-provider-content' not in json.dumps(result)
    assert result['humanOverallQualityImprovement'] is None
    assert all(a['phases'] == {'UNOBSERVED': 90} for a in result['arms'].values())


@pytest.mark.parametrize('damage', ['duplicate', 'missing', 'rubric', 'schema'])
def test_bad_report_rejected(damage):
    rubric, report = report_case()
    if damage == 'duplicate': report['results'][1] = deepcopy(report['results'][0])
    if damage == 'missing': report['results'].pop()
    if damage == 'rubric': report['rubricSha256'] = '0' * 64
    if damage == 'schema': report['schemaVersion'] = 'other'
    with pytest.raises(ValueError): observation.analyze(report, rubric, {})


def test_hash_loader_rejects_mismatch_duplicate_keys_and_nonfinite(tmp_path):
    path = tmp_path / 'input.json'
    for content in ('{"a":1}', '{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}'):
        path.write_text(content, encoding='utf8')
        with pytest.raises(ValueError): observation.load_bound(path, '0' * 64)
        if content != '{"a":1}':
            with pytest.raises(ValueError):
                observation.load_bound(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_export_pending_and_import_agent_do_not_claim_human(tmp_path, monkeypatch):
    from ai_service.orchestration import rag_rerank_contracts as contracts
    monkeypatch.setattr(contracts, 'ROOT', tmp_path)
    monkeypatch.setattr(observation, 'ROOT', tmp_path)
    rubric, report = report_case()
    output = tmp_path / 'tmp/run'
    result = observation.write_run(report, rubric, {}, output, {'reportSha256': fingerprint(report)})
    public = json.loads((output / 'blind/cards.json').read_text(encoding='utf8'))
    template = json.loads((output / 'blind/reviews-template.json').read_text(encoding='utf8'))
    private = json.loads((output / 'blind/private-mapping.json').read_text(encoding='utf8'))
    assert result['humanReview'] == 'pending'
    assert all(r['status'] == 'pending' for r in template['reviews'])
    agent = complete_reviews(template, public)
    agent['reviewer']['role'] = 'agent'
    reviewed = observation.review_summary(report, rubric, public, private, agent)
    assert reviewed['humanOverallQualityImprovement'] is None
    human = complete_reviews(template, public, role='human')
    human_reviewed = observation.review_summary(report, rubric, public, private, human)
    assert human_reviewed['humanReview'] == 'complete'
    assert human_reviewed['humanOverallQualityImprovement'] is not None
    changed = deepcopy(public)
    changed['cards'][0]['question'] = '篡改问题'
    with pytest.raises(ValueError): observation.review_summary(report, rubric, changed, private, template)
    outside = tmp_path / 'outside'
    with pytest.raises(ValueError): observation.write_run(report, rubric, {}, outside, {})
    assert not outside.exists()
    with pytest.raises(ValueError):
        observation.write_run(report, rubric, {}, output, {})
    invalid = deepcopy(template)
    invalid['reviews'].pop()
    with pytest.raises(ValueError): observation.review_summary(report, rubric, public, private, invalid)


def test_document_mapping_must_be_bound_and_match_frozen_sources(tmp_path):
    rubric, report = report_case()
    path = tmp_path / 'map.json'
    path.write_text('{"schemaVersion":"product-knowledge-document-map/v1","documents":{}}', encoding='utf8')
    with pytest.raises(ValueError): observation.load_document_mapping(report, rubric, path)
    report['sourceFilesSha256'] = {'document-map.json': hashlib.sha256(path.read_bytes()).hexdigest()}
    with pytest.raises(ValueError): observation.load_document_mapping(report, rubric, path)
