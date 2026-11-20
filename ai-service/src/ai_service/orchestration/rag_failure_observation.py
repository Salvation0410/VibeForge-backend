"""客服失败观测与人工评测入口：仅本地读写，不初始化模型、数据库或在线资源。"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

from ai_service.orchestration import rag_blind_review as blind
from ai_service.orchestration.rag_evidence_observation import STAGES, observe_evidence
from ai_service.orchestration.rag_rerank_contracts import ROOT, fingerprint, load_rubric

SCHEMAS = {
    'rag-rerank-20261118-first-round-v1': 'rag-rerank-experiment/v1',
    'rag-cap-20261118-answer-round-v1': 'rag-cap-experiment/v1',
    'rag-json-answer-20261119-v1': 'rag-json-answer-experiment/v1',
}
MAX_INPUT_BYTES = 64 * 1024 * 1024


def fail(code):
    raise ValueError('RAG_OBSERVATION_' + code)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail('DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        fail('NONFINITE_JSON')
    return number


def load_bound(path, digest):
    """摘要基于本次读到的同一份字节；拒绝重复键及非有限数值，避免解析歧义。"""
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        fail('HASH_REQUIRED')
    with Path(path).open('rb') as source:
        data = source.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES or hashlib.sha256(data).hexdigest() != digest:
        fail('INPUT_HASH_OR_SIZE_INVALID')
    return json.loads(data.decode('utf8'), object_pairs_hook=_object, parse_float=_finite_float,
                      parse_constant=lambda _: fail('NONFINITE_JSON'))


def load_document_mapping(report, rubric, path):
    expected = report.get('sourceFilesSha256', {}).get('document-map.json')
    data = load_bound(path, expected)
    if data.get('schemaVersion') != 'product-knowledge-document-map/v1':
        fail('DOCUMENT_MAP_INVALID')
    documents = data.get('documents')
    refs = {ref['documentId']: ref['documentSha256'] for entry in rubric['entries']
            for fact in entry['requiredFacts'] if fact['basisType'] == 'public-document'
            for ref in fact['evidence']}
    if not isinstance(documents, dict) or not set(refs) <= set(documents):
        fail('DOCUMENT_MAP_INVALID')
    result = {}
    for logical, item in documents.items():
        if (not isinstance(item, dict) or not isinstance(item.get('documentId'), str)
                or not item['documentId'] or item['documentId'] in result
                or type(item.get('documentVersion')) is not int or item['documentVersion'] < 1
                or logical not in refs or item.get('sha256') != refs[logical]):
            fail('DOCUMENT_MAP_INVALID')
        result[item['documentId']] = (logical, item['documentVersion'])
    return result


def _safe_reason(row):
    from ai_service.orchestration.customer_service_trace import TERMINAL_REASON_CODES
    terminal = row.get('trace', {}).get('terminal', {})
    raw = row.get('errorCode') if row['status'] == 'error' else terminal.get('reasonCode')
    return raw if isinstance(raw, str) and raw in TERMINAL_REASON_CODES else 'UNKNOWN'


def _safe_phase(row):
    from ai_service.orchestration.customer_service_trace import TERMINAL_PHASES
    raw = row.get('trace', {}).get('terminal', {}).get('phase')
    return raw if isinstance(raw, str) and raw in TERMINAL_PHASES else 'UNOBSERVED'


def analyze(report, rubric, mapping):
    if report.get('schemaVersion') != SCHEMAS.get(report.get('studyId')):
        fail('REPORT_SCHEMA_INVALID')
    lookup = blind.validate_results(report, rubric)
    entries = {e['id']: e for e in rubric['entries']}
    cases = []
    for row in lookup.values():
        status = row['status']
        kind = status if status != 'ok' else ('answered' if row['answered'] else 'refused')
        cases.append({'id': row['id'], 'arm': row['arm'], 'status': kind,
                      'reasonCode': _safe_reason(row), 'phase': _safe_phase(row),
                      'evidence': observe_evidence(row, entries[row['id']], mapping)})
    arms = {}
    for arm in blind._study(report['studyId']).arms:
        rows = [r for r in cases if r['arm'] == arm]
        counts = Counter(r['status'] for r in rows)
        arms[arm] = {'denominator': len(rows),
            'statuses': {k: counts[k] for k in ('answered', 'refused', 'error', 'not_run')},
            'reasonCodes': dict(sorted(Counter(r['reasonCode'] for r in rows).items())),
            'phases': dict(sorted(Counter(r['phase'] for r in rows).items())),
            'stages': {s: dict(Counter(r['evidence']['stages'][s]['state'] for r in rows)) for s in STAGES},
            'snippetFirstLoss': dict(sorted(Counter(f['firstObservedLoss'] or 'SURVIVED'
                for r in rows for f in r['evidence']['facts']).items())),
            'selectionDropReasons': dict(sorted(Counter(reason for r in rows
                for f in r['evidence']['facts'] for reason in f['selectionDropReasons']).items()))}
    return {'schemaVersion': 'rag-failure-observation/v1', 'studyId': report['studyId'],
            'arms': arms, 'cases': cases, 'humanReview': 'pending',
            'humanOverallQualityImprovement': None,
            'boundary': '重复开发集的离线观测；片段存活不证明语义充分，引用校验不消除幻觉；非当前线上错误率'}


def review_summary(report, rubric, public, private, reviews):
    # 复用严格评审，同时核对公共卡确实来自报告，不能只重算映射摘要后替换答案。
    lookup = blind.validate_results(report, rubric)
    entries = {e['id']: e for e in rubric['entries']}
    if len(public.get('cards', [])) != len(lookup):
        fail('PUBLIC_PACKAGE_CHANGED')
    for card in public['cards']:
        identity = private.get('mapping', {}).get(card.get('blindId'), {})
        row = lookup.get(identity.get('id', '') + ':' + identity.get('arm', ''))
        if row is None:
            fail('PUBLIC_PACKAGE_CHANGED')
        entry = entries[row['id']]
        for key in ('question', 'expectedAnswerable', 'optionalFacts', 'forbiddenClaims'):
            if card.get(key) != entry[key]:
                fail('PUBLIC_PACKAGE_CHANGED')
        for key in ('status', 'answer', 'answered', 'citedChunkIds'):
            if card.get(key) != row.get(key):
                fail('PUBLIC_PACKAGE_CHANGED')
        contexts = [{k: c[k] for k in ('chunkId', 'documentId', 'documentVersion', 'locator', 'content')}
                    for c in row.get('contexts', [])]
        facts = [{k: f[k] for k in ('factId', 'statement', 'equivalentExpressions',
                  'applicableConditions', 'basisType', 'evidence', 'contractNotes', 'protocolReferences') if k in f}
                 for f in entry['requiredFacts']]
        if (card.get('contexts') != contexts or card.get('requiredFacts') != facts
                or card.get('sources') != blind._public_sources(row.get('sources', []))):
            fail('PUBLIC_PACKAGE_CHANGED')
    return blind.summarize(report, public, private, reviews, rubric)


def _write(path, value):
    with path.open('x', encoding='utf8') as output:
        json.dump(value, output, ensure_ascii=False, allow_nan=False, indent=2)
        output.write('\n')


def write_run(report, rubric, mapping, output, bindings, *, review_inputs=None):
    output = Path(output).resolve()
    if output == (ROOT / 'tmp').resolve() or not output.is_relative_to((ROOT / 'tmp').resolve()) or output.exists():
        fail('OUTPUT_MUST_BE_NEW_UNDER_TMP')
    result = analyze(report, rubric, mapping)
    reviewed = review_summary(report, rubric, *review_inputs) if review_inputs else None
    output.mkdir(parents=True, exist_ok=False)
    if reviewed is None:
        public = blind.export_blind(report, output / 'blind', rubric)
        # 只读取本次创建的模板；它没有任何自动填写的质量评分。
        private = json.loads((output / 'blind/private-mapping.json').read_text(encoding='utf8'))
        template = json.loads((output / 'blind/reviews-template.json').read_text(encoding='utf8'))
        reviewed = review_summary(report, rubric, public, private, template)
    result.update(bindings=bindings, humanReview=reviewed['humanReview'],
                  humanOverallQualityImprovement=reviewed['humanOverallQualityImprovement'])
    _write(output / 'observation.json', result)
    _write(output / 'review-analysis.json', reviewed)
    _write(output / 'summary.json', {k: v for k, v in result.items() if k != 'cases'})
    lines = ['# 本轮离线观测', '', result['boundary'], '',
             '人工评审状态：' + result['humanReview'], '',
             '| 组 | 分母 | 回答 | 正常拒答 | 工程错误 | 未运行 |', '| --- | ---: | ---: | ---: | ---: | ---: |']
    for arm, data in result['arms'].items():
        counts = data['statuses']
        lines.append(f"| {arm} | {data['denominator']} | {counts['answered']} | {counts['refused']} | {counts['error']} | {counts['not_run']} |")
    lines += ['', '逐题阶段存活见 observation.json。UNKNOWN 表示材料不足，不能当作未召回。',
              '新建包的 blind/cards.md 供人工阅读，复制 reviews-template.json 后填写；私有映射不要交给盲审人。',
              '实际人工填写后用 --package 与 --reviews 导入；role 是评审人声明，软件不认证身份。']
    with (output / 'README.md').open('x', encoding='utf8') as stream:
        stream.write('\n'.join(lines) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--report-sha256', required=True)
    parser.add_argument('--document-map', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--reviews', type=Path)
    parser.add_argument('--reviews-sha256')
    args = parser.parse_args()
    try:
        if any((args.package, args.reviews, args.reviews_sha256)) and not all((args.package, args.reviews, args.reviews_sha256)):
            fail('REVIEW_ARGUMENTS_REQUIRED')
        report = load_bound(args.report, args.report_sha256)
        rubric = load_rubric()
        mapping = load_document_mapping(report, rubric, args.document_map) if args.document_map else {}
        bindings = {'reportSha256': args.report_sha256, 'reportCanonicalSha256': fingerprint(report),
                    'rubricSha256': fingerprint(rubric), 'documentMappingProvided': bool(args.document_map)}
        review_inputs = None
        if args.package:
            reviews = load_bound(args.reviews, args.reviews_sha256)
            # 卡片规范摘要已绑定于 reviews，私有映射再由正式 summarize 复核报告及全量样本。
            def local_json(path):
                with path.open('rb') as source:
                    data = source.read(MAX_INPUT_BYTES + 1)
                if len(data) > MAX_INPUT_BYTES:
                    fail('INPUT_HASH_OR_SIZE_INVALID')
                return json.loads(data.decode('utf8'), object_pairs_hook=_object, parse_float=_finite_float,
                                  parse_constant=lambda _: fail('NONFINITE_JSON'))
            public = local_json(args.package / 'cards.json')
            private = local_json(args.package / 'private-mapping.json')
            review_inputs = (public, private, reviews)
            bindings['reviewsSha256'] = args.reviews_sha256
        result = write_run(report, rubric, mapping, args.output, bindings, review_inputs=review_inputs)
        print(json.dumps({'status': 'written', 'humanReview': result['humanReview'], 'newProviderCalls': 0}))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # CLI 不回显不可信 JSON、路径或 Pydantic 输入正文；详细定位用离线测试/API。
        parser.exit(2, 'RAG_OBSERVATION_INPUT_OR_OUTPUT_INVALID\n')


if __name__ == '__main__':
    main()
