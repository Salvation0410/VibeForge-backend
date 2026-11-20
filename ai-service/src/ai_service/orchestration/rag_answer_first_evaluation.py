"""第22轮冻结规则的离线选择对照；不生成回答、不初始化外部客户端。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import socket
from unittest.mock import patch

from ai_service.infrastructure.milvus_knowledge import RetrievedChunk
from ai_service.models.reranker import RerankedChunk
from ai_service.orchestration.customer_service_rag import _bounded_contexts, _clean_text, _select_documents
from ai_service.orchestration.rag_failure_observation import analyze, load_bound, load_document_mapping
from ai_service.orchestration.rag_evidence_observation import STAGES
from ai_service.orchestration.rag_rerank_contracts import PRODUCT, ROOT, RUBRIC, fingerprint, load_rubric
from ai_service.prompts.customer_service import customer_service_user_prompt, fit_customer_service_contexts
from ai_service.models.base import CustomerServiceContext

RULE_VERSION = 'answer-first-question-list@2'
ARMS = ('A0', 'A1', 'A2')
FIELDS = ('chunkId', 'documentId', 'documentVersion', 'locator', 'content')
TRACE_CONFIG = dict(retrievalTopK=8, finalTopK=3, documentChunkLimit=1,
                    minRerankScore=None, maxContextChars=1200,
                    maxTotalContextChars=3000, promptMaxBytes=16384)
REPORT_CONFIG = dict(rag_retrieval_top_k=8, rag_final_top_k=3, perDocument=1,
                     rag_min_rerank_score=None, maxContextChars=1200, maxContexts=3,
                     maxTotalContextChars=3000, rag_prompt_max_bytes=16384)
_MARKER = re.compile('怎么|如何|为什么|为何|哪里|哪|什么|多少|多久|几|是否|能否|是不是|吗|要不要|可不可以|能不能|有没有|？|[?]')
_ITEM = re.compile(r'“([^“”\r\n。；：，;,.:]{2,100})”')
_GUIDE = re.compile(r'(?:相关说明：\s*)?[\u4e00-\u9fff]{1,16}\s*(?:详见|见)\s*[0-9]{2}(?:\s*；\s*[\u4e00-\u9fff]{1,16}\s*(?:详见|见)\s*[0-9]{2})*\s*。?')


def fail(code):
    raise ValueError('RAG_ANSWER_FIRST_' + code)


def is_question_list(chunk):
    """只匹配预先冻结的有限句法，未知与混合回答一律不降序。"""
    locator, content = chunk.get('locator'), chunk.get('content')
    if not isinstance(locator, str) or locator.split('/')[-1].strip() != '常见问法' or not isinstance(content, str):
        return False
    position, count = 0, 0
    while True:
        while position < len(content) and content[position].isspace():
            position += 1
        match = _ITEM.match(content, position)
        if match is None:
            break
        if not _MARKER.search(match[1]):
            return False
        count += 1
        position = match.end()
    if not 2 <= count <= 20:
        return False
    tail = content[position:].lstrip('。 \t\r\n')
    return not tail or _GUIDE.fullmatch(tail.strip()) is not None


def _projection(chunk):
    return {key: chunk[key] for key in FIELDS}


def _ranked(row):
    chunks, metadata = row['stages']['thresholded'], row['trace']['thresholded']
    if not isinstance(chunks, list) or not isinstance(metadata, list) or len(chunks) > 8 or len(chunks) != len(metadata):
        fail('TRACE_INVALID')
    ranked, seen = [], set()
    previous_score = math.inf
    for i, (chunk, meta) in enumerate(zip(chunks, metadata, strict=True)):
        score = meta.get('score')
        if (type(score) not in (float, int) or not math.isfinite(score)
                or score > previous_score or meta.get('scoreSource') != 'reranker'
                or meta.get('kept') is not True or type(meta.get('rank')) is not int
                or meta['rank'] != i + 1 or meta.get('length') != len(chunk['content'])
                or any(meta.get(k) != chunk[k] for k in FIELDS[:3])
                or chunk['chunkId'] in seen):
            fail('TRACE_INVALID')
        seen.add(chunk['chunkId'])
        previous_score = score
        # 此处只重建选择所需的不可变值；不伪造真实索引、距离或文件来源观测。
        retrieved = RetrievedChunk(chunk_id=chunk['chunkId'], document_id=chunk['documentId'],
            document_version=chunk['documentVersion'], chunk_index=0, content=chunk['content'],
            file_name='', file_type='', source_locator=chunk['locator'], content_hash='',
            distance=0.0, score=0.0)
        ranked.append(RerankedChunk(retrieved, score))
    return ranked


def _arm(ranked, originals, question, cap):
    selected, drops = _select_documents(ranked, 3, per_document_limit=cap)
    bounded, _ = _bounded_contexts(selected)
    fitted = fit_customer_service_contexts(question, bounded, max_bytes=16384)
    if fitted is None:
        fail('PROMPT_BUDGET_INVALID')
    contexts = [deepcopy(originals[key]) | {'content': content} for key, content in fitted]
    payload = customer_service_user_prompt(question, [CustomerServiceContext(k, c) for k, c in fitted], max_bytes=16384)
    bounded_lengths = {c.chunk_id: len(c.content) for c in bounded}
    return dict(selected=[deepcopy(originals[r.chunk.chunk_id]) | {'score': r.score} for r in selected],
                contexts=contexts, drops=drops, documentDiversity=len({c['documentId'] for c in contexts}),
                characterTruncated=any(len(_clean_text(r.chunk.content, len(r.chunk.content) + 1)) >
                    bounded_lengths.get(r.chunk.chunk_id, 0) for r in selected),
                byteTruncated=[(c.chunk_id, c.content) for c in bounded] != fitted,
                budgets=dict(selectedChars=sum(len(r.chunk.content) for r in selected),
                    characterBudgetChars=sum(len(c.content) for c in bounded),
                    finalChars=sum(len(c) for _, c in fitted),
                    promptBytes=len(payload.encode('utf8'))))


def compare_case(row, question):
    """rubric 不参与选择；先复现 A0，再计算另外两组及配对差异。"""
    ranked = _ranked(row)
    originals = {c['chunkId']: _projection(c) for c in row['stages']['thresholded']}
    baseline = _arm(ranked, originals, question, 1)
    if baseline['contexts'] != row['contexts']:
        fail('BASELINE_DRIFT')
    flags = {key: is_question_list(c) for key, c in originals.items()}
    partitioned = [r for r in ranked if not flags[r.chunk.chunk_id]] + [r for r in ranked if flags[r.chunk.chunk_id]]
    arms = dict(A0=baseline, A1=_arm(ranked, originals, question, 2),
                A2=_arm(partitioned, originals, question, 2))
    pairs = {}
    for first, second in [('A0', 'A1'), ('A1', 'A2'), ('A0', 'A2')]:
        old, new = arms[first], arms[second]
        old_map = {c['chunkId']: c for c in old['contexts']}
        new_map = {c['chunkId']: c for c in new['contexts']}
        pairs[first + '_' + second] = dict(
            orderChanged=list(old_map) != list(new_map),
            addedChunkIds=[k for k in new_map if k not in old_map],
            removedChunkIds=[k for k in old_map if k not in new_map],
            changedContextChunkIds=[k for k in new_map if k in old_map and new_map[k] != old_map[k]],
            documentDiversityDelta=new['documentDiversity'] - old['documentDiversity'],
            budgetDelta={k: new['budgets'][k] - old['budgets'][k] for k in old['budgets']})
    return dict(id=row['id'], candidates=[deepcopy(originals[r.chunk.chunk_id]) |
                dict(score=r.score, originalRank=i + 1, questionList=flags[r.chunk.chunk_id])
                for i, r in enumerate(ranked)], arms=arms, pairs=pairs)


def _config(actual, expected):
    if not isinstance(actual, dict) or any(k not in actual or type(actual[k]) is not type(v)
            or actual[k] != v for k, v in expected.items()):
        fail('CONFIG_INVALID')


def validate_report(report, rubric, mapping):
    if report.get('studyId') != 'rag-json-answer-20261119-v1':
        fail('STUDY_INVALID')
    _config(report.get('config'), REPORT_CONFIG)
    observation = analyze(report, rubric, mapping)
    if len(rubric['entries']) != 90 or len(observation['cases']) != 180:
        fail('CASE_COUNT_INVALID')
    if any(stage['state'] != 'OBSERVED' for c in observation['cases'] for stage in c['evidence']['stages'].values()):
        fail('STAGE_UNOBSERVED')
    by_id = {}
    for row in report['results']:
        _config(row.get('trace', {}).get('configFingerprint'), TRACE_CONFIG)
        _ranked(row)
        # 所有分数均为真实有限数；重排来源必须明确，不能把距离冒称 BGE 分数。
        for stage in ('retrieved', 'filtered', 'reranked', 'thresholded', 'selected'):
            for meta in row['trace'][stage]:
                score = meta.get('score')
                if (type(score) not in (float, int) or not math.isfinite(score)
                        or (stage in ('reranked', 'thresholded', 'selected') and meta.get('scoreSource') != 'reranker')):
                    fail('TRACE_INVALID')
        reranked = row['trace']['reranked']
        filtered = [m for m in row['trace']['filtered'] if m['kept']]
        if ({m['chunkId'] for m in filtered} != {m['chunkId'] for m in reranked}
                or len(row['trace']['retrieved']) > 8):
            fail('TRACE_INVALID')
        retrieval_order = {m['chunkId']: i for i, m in enumerate(row['trace']['retrieved'])}
        expected_order = sorted(reranked, key=lambda m: (-m['score'], retrieval_order[m['chunkId']]))
        if reranked != expected_order:
            fail('TRACE_INVALID')
        retrieved_scores = {m['chunkId']: m['score'] for m in row['trace']['retrieved']}
        reranked_scores = {m['chunkId']: m['score'] for m in reranked}
        retrieved_sources = {m['chunkId']: m.get('scoreSource') for m in row['trace']['retrieved']}
        if (any(m['score'] != retrieved_scores[m['chunkId']] or
                m.get('scoreSource') != retrieved_sources[m['chunkId']] for m in row['trace']['filtered'])
                or any(m['score'] != reranked_scores[m['chunkId']] for m in row['trace']['selected'])):
            fail('TRACE_INVALID')
        if [{k: m[k] for k in ('chunkId', 'score', 'scoreSource')} for m in reranked] != [
                {k: m[k] for k in ('chunkId', 'score', 'scoreSource')} for m in row['trace']['thresholded']]:
            fail('TRACE_INVALID')
        by_id.setdefault(row['id'], {})[row['arm']] = row
    for rows in by_id.values():
        if set(rows) != {'text', 'json_object'}:
            fail('ARMS_INVALID')
        first, second = rows['text'], rows['json_object']
        if (first['stages']['thresholded'] != second['stages']['thresholded']
                or first['trace']['thresholded'] != second['trace']['thresholded']
                or any(first['trace'][stage] != second['trace'][stage] for stage in STAGES)
                or first['stages'] != second['stages']
                or first['contexts'] != second['contexts']):
            fail('ARMS_DIFFER')
    return by_id


def evaluate(report, rubric, mapping):
    rows = validate_report(report, rubric, mapping)
    # 全部基线成功后才读取 fact 进行字面核对，防止评审标签影响选择。
    cases = [compare_case(rows[e['id']]['text'], e['question']) for e in rubric['entries']]
    count = 0
    for case, entry in zip(cases, rubric['entries'], strict=True):
        case['question'] = entry['question']
        case['requiredFacts'] = [{k: deepcopy(f[k]) for k in ('factId', 'statement',
            'equivalentExpressions', 'applicableConditions', 'basisType', 'evidence') if k in f}
            for f in entry['requiredFacts']]
        facts = []
        for fact in entry['requiredFacts']:
            if fact['basisType'] != 'public-document':
                continue
            count += 1
            by_arm = {}
            for arm in ARMS:
                contexts = case['arms'][arm]['contexts']
                refs = [any(mapping[c['documentId']][0] == ref['documentId'] and
                    _clean_text(ref['quote'], len(ref['quote']) + 1) in c['content'] for c in contexts)
                    for ref in fact['evidence']]
                by_arm[arm] = dict(anyWholeQuoteSurvived=any(refs), allWholeQuotesSurvived=all(refs),
                                   referenceWholeQuoteSurvival=refs)
            facts.append(dict(factId=fact['factId'], arms=by_arm))
        case['facts'] = facts
    if count != 130:
        fail('PUBLIC_FACT_COUNT_INVALID')
    return dict(schemaVersion='rag-answer-first-selection/v1', ruleVersion=RULE_VERSION,
                sourceStudyId=report['studyId'], cases=cases, baselineExactMatches=len(cases),
                publicFactCount=count, humanReview='pending', newProviderCalls=0,
                boundary='重复开发集离线选择对照；整段引文字面存活不等于语义覆盖，不生成新回答，不证明质量提升。')


def validate_output(output):
    output, root = Path(output).resolve(), (ROOT / 'tmp').resolve()
    if output == root or not output.is_relative_to(root) or output.exists():
        fail('OUTPUT_MUST_BE_NEW_UNDER_TMP')
    return output


@contextmanager
def block_network():
    """CLI 主动阻断常见 Python socket 出站与名称解析，记录任何尝试。"""
    audit = dict(enabled=True, attempts=0)
    def denied(*args, **kwargs):
        audit['attempts'] += 1
        fail('NETWORK_FORBIDDEN')
    with patch.object(socket, 'create_connection', denied), patch.object(socket, 'getaddrinfo', denied), \
         patch.object(socket.socket, 'connect', denied), patch.object(socket.socket, 'connect_ex', denied), \
         patch.object(socket.socket, 'sendto', denied):
        yield audit


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')


def run(report_path, report_sha256, document_map, output, *, network_audit=None):
    output = validate_output(output)
    report = load_bound(report_path, report_sha256)
    rubric = load_rubric()
    mapping = load_document_mapping(report, rubric, document_map)
    protected = {Path(report_path).resolve(), Path(document_map).resolve(), RUBRIC.resolve(),
                 Path(__file__).resolve(), (ROOT / 'docs/superpowers/plans/2026-11-20-rag-answer-first-selection.md').resolve()}
    for area, base in [('inputs', PRODUCT), ('documents', PRODUCT), ('sourceFiles', ROOT)]:
        protected.update((base / item['path']).resolve() for item in rubric['freeze'][area])
    before = {str(p): _hash(p) for p in protected}
    if before[str(Path(report_path).resolve())] != report_sha256 or before[str(Path(document_map).resolve())] != report['sourceFilesSha256']['document-map.json']:
        fail('INPUT_CHANGED')
    result = evaluate(report, rubric, mapping)
    if any(_hash(p) != digest for p, digest in before.items()):
        fail('INPUT_CHANGED')
    if network_audit is not None and network_audit['attempts']:
        fail('NETWORK_FORBIDDEN')
    result['bindings'] = dict(reportSha256=report_sha256, reportCanonicalSha256=fingerprint(report),
        rubricSha256=fingerprint(rubric), rubricFileSha256=_hash(RUBRIC),
        documentMapSha256=report['sourceFilesSha256']['document-map.json'],
        ruleVersion=RULE_VERSION, moduleSha256=_hash(Path(__file__)),
        planSha256=_hash(ROOT / 'docs/superpowers/plans/2026-11-20-rag-answer-first-selection.md'))
    result['networkAudit'] = deepcopy(network_audit) if network_audit is not None else dict(enabled=False, attempts=None)
    summary = {k: v for k, v in result.items() if k != 'cases'}
    summary['arms'] = {arm: dict(wholeQuoteSurvived=sum(f['arms'][arm]['anyWholeQuoteSurvived']
        for c in result['cases'] for f in c['facts']), totalDocumentDiversity=sum(c['arms'][arm]['documentDiversity'] for c in result['cases'])) for arm in ARMS}
    summary['pairs'] = {pair: dict(changedCases=sum(c['arms'][pair[:2]]['contexts'] != c['arms'][pair[3:]]['contexts'] for c in result['cases']),
        quoteGains=sum(not f['arms'][pair[:2]]['anyWholeQuoteSurvived'] and f['arms'][pair[3:]]['anyWholeQuoteSurvived'] for c in result['cases'] for f in c['facts']),
        quoteLosses=sum(f['arms'][pair[:2]]['anyWholeQuoteSurvived'] and not f['arms'][pair[3:]]['anyWholeQuoteSurvived'] for c in result['cases'] for f in c['facts'])) for pair in ('A0_A1', 'A1_A2', 'A0_A2')}
    template = dict(schemaVersion='rag-answer-first-evidence-review/v1', status='pending', reviewer=None,
        evidencePackageSha256=fingerprint(result), boundary='仅评审证据变化；代理填写与人工填写必须分别标注，不是回答质量评审。',
        cases=[dict(id=c['id'], status='pending', facts=[dict(factId=f['factId'], semanticSupportByArm={a: None for a in ARMS}, annotationDispute=None, notes='') for f in c['facts']], removedEvidenceRegression=None, notes='') for c in result['cases']])
    output.mkdir(parents=True, exist_ok=False)
    _write(output / 'comparison.json', result)
    _write(output / 'summary.json', summary)
    _write(output / 'reviews-template.json', template)
    if any(_hash(p) != digest for p, digest in before.items()):
        fail('INPUT_CHANGED')
    _write(output / 'input-integrity.json', dict(unchanged=True, fileCount=len(before), sha256=before))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--report-sha256', required=True)
    parser.add_argument('--document-map', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        with block_network() as audit:
            result = run(args.report, args.report_sha256, args.document_map, args.output, network_audit=audit)
        print(json.dumps(dict(status='written', baselineExactMatches=result['baselineExactMatches'],
                              humanReview='pending', newProviderCalls=0, networkAudit=audit)))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # 不向终端回显不可信正文、路径或供应商响应。
        parser.exit(2, 'RAG_ANSWER_FIRST_INPUT_OR_OUTPUT_INVALID\n')


if __name__ == '__main__':
    main()
