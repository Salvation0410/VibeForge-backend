"""格式实验必须显式注册且不改变旧研究的配对键。"""
from ai_service.orchestration import rag_blind_review as review
from test_rag_cap_blind_review import make_report, complete_reviews
from ai_service.orchestration.rag_rerank_contracts import ROOT, read_json
from copy import deepcopy
import pytest
import json
import uuid
import shutil


def test_json_study_registered():
    study = review._study('rag-json-answer-20261119-v1')
    assert study.arms == ('text', 'json_object')
    assert study.paired_keys == ('textOnly', 'jsonObjectOnly', 'jsonObjectMinusText')


def test_package_pending_scores_and_private_fields():
    rubric, report = make_report('rag-json-answer-20261119-v1', ('text', 'json_object'))
    report['schemaVersion'] = 'rag-json-answer-experiment/v1'
    path = ROOT / 'tmp/rag-json-answer-20261119/tool-validation' / ('blind-' + uuid.uuid4().hex)
    try:
        public = review.export_blind(report, path, rubric)
        private, template = read_json(path / 'private-mapping.json'), read_json(path / 'reviews-template.json')
        assert len(public['cards']) == 180
        for forbidden in ('json_object', 'actualMessages', 'usage', 'LatencyMs', '"arm"', 'score', 'shuffleSeed'):
            assert forbidden not in json.dumps(public)
        pending = review.summarize(report, public, private, template, rubric)
        assert pending['paired'] is None and pending['humanOverallQualityImprovement'] is None
        result = review.summarize(report, public, private, complete_reviews(template, public), rubric)
        assert result['schemaVersion'] == 'rag-json-answer-analysis/v1'
        assert 'jsonObjectOnly' in result['paired'] and 'cap2Only' not in result['paired']
        wrong = deepcopy(private); wrong['studyId'] = 'rag-cap-20261118-answer-round-v1'
        with pytest.raises(ValueError): review.summarize(report, public, wrong, template, rubric)
        wrong = deepcopy(template); wrong['studyId'] = 'rag-cap-20261118-answer-round-v1'
        with pytest.raises(ValueError): review.validate_reviews(wrong, public)
    finally:
        assert path.resolve().is_relative_to((ROOT / 'tmp').resolve())
        if path.exists(): shutil.rmtree(path)


@pytest.mark.parametrize('schema', [None, 'rag-cap-experiment/v1', 'rag-rerank-experiment/v1'])
def test_new_study_schema_strict(schema):
    rubric, report = make_report('rag-json-answer-20261119-v1', ('text', 'json_object'))
    if schema: report['schemaVersion'] = schema
    with pytest.raises(ValueError, match='RESULT_SCHEMA_INVALID'): review.validate_results(report, rubric)
