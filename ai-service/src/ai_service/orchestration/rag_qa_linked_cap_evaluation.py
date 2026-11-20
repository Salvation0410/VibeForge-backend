"""第24轮冻结候选的离线文档配额对照；只重放选择，不调用供应商。"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import math
from pathlib import Path
import re
from types import SimpleNamespace

from ai_service.infrastructure.milvus_knowledge import RetrievedChunk
from ai_service.models.base import CustomerServiceContext
from ai_service.models.reranker import RerankedChunk
from ai_service.orchestration.customer_service_rag import (
    _bounded_contexts, _clean_text, _filter_and_dedupe, _select_documents, _validate_ranked,
)
from ai_service.orchestration.document_etl import parse_document, split_sections
from ai_service.orchestration.rag_answer_first_evaluation import block_network
from ai_service.orchestration.rag_qa_linked_budget import fingerprint
from ai_service.orchestration.rag_qa_linked_corpus import (
    PRODUCT, ROOT, VERSION, read_json, sha, write_json,
)
from ai_service.prompts.customer_service import customer_service_user_prompt, fit_customer_service_contexts

ARMS = ('cap1', 'cap2')
FIELDS = ('chunkId', 'documentId', 'documentVersion', 'locator', 'content')
CONFIG = dict(topK=8, finalK=3, perDocument=1, minRerankScore=None,
              maxPromptBytes=16384, maxContextChars=1200, maxTotalContextChars=3000)
STAGES = ('retrieved', 'filtered', 'reranked', 'thresholded', 'selected', 'characterBudget', 'finalContext')


def fail(code):
    raise ValueError('QA_CAP_' + code)


def safe_path(path, root):
    """解析前检查每级链接，避免解析结果掩盖符号链接或目录联接。"""
    path, root = Path(path).absolute(), Path(root).absolute()
    if (any(p.is_symlink() or p.is_junction() for p in (path, *path.parents))
            or not path.resolve().is_relative_to(root.resolve())):
        fail('PATH_INVALID')
    return path


def validate_output(output, root=None):
    root = ROOT / 'tmp' if root is None else Path(root)
    output = safe_path(output, root)
    if output.resolve() == root.resolve() or output.exists():
        fail('OUTPUT_MUST_BE_NEW_UNDER_TMP')
    return output


def validate_bindings(plan, expected, report_path, ledger_path, root):
    """文件摘要和计划规范化摘要分别比较，不混用两种口径。"""
    if (fingerprint(plan) != expected['planSha256']
            or sha(safe_path(report_path, root)) != expected['reportSha256']
            or sha(safe_path(ledger_path, root)) != expected['ledgerSha256']):
        fail('SOURCE_HASH')
    if not isinstance(plan.get('bindings'), dict) or not plan['bindings']:
        fail('BINDINGS_INVALID')
    protected = {str(safe_path(report_path, root)): expected['reportSha256'],
                 str(safe_path(ledger_path, root)): expected['ledgerSha256']}
    for relative, digest in plan['bindings'].items():
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            fail('PATH_INVALID')
        path = safe_path(Path(root) / relative, root)
        if sha(path) != digest:
            fail('BINDING_CHANGED')
        protected[str(path)] = digest
    return protected



def load_candidates(corpus, *, expected_documents=11, expected_chunks=63):
    """从冻结候选原文重建块；不重做转换、不读取环境配置、不写临时文件。"""
    corpus = Path(corpus)
    manifest = read_json(safe_path(corpus / 'manifest.json', corpus))
    if (manifest.get('schemaVersion') != 'rag-qa-candidate-manifest/v1'
            or manifest.get('candidateVersion') != VERSION
            or not isinstance(manifest.get('documents'), list)
            or len(manifest['documents']) != expected_documents):
        fail('CANDIDATE_MANIFEST')
    ids, paths, chunks = set(), set(), []
    # 分块只消费这两个冻结字段，不实例化会读取环境变量的Settings。
    options = SimpleNamespace(rag_chunk_size=1000, rag_chunk_overlap=150)
    for row in manifest['documents']:
        identity, metadata = row['documentId'], row['sourceMetadata']
        relative = metadata['path']
        if (not isinstance(identity, str) or not identity or identity in ids
                or metadata.get('id') != identity
                or not isinstance(relative, str)
                or not re.fullmatch(r'documents/[A-Za-z0-9_-]+\.md', relative)):
            fail('CANDIDATE_IDENTITY_OR_PATH')
        path = safe_path(corpus / relative, corpus)
        if path.resolve() in paths:
            fail('CANDIDATE_IDENTITY_OR_PATH')
        ids.add(identity)
        paths.add(path.resolve())
        if sha(path) != row['candidateSha256']:
            fail('CANDIDATE_HASH')
        sections = parse_document(path, 'md')
        chunks.extend(asdict(c) for c in split_sections(sections, document_id=identity,
            document_version=2, source_name=path.name, settings=options))
    if len(chunks) != expected_chunks or len({c['chunk_id'] for c in chunks}) != len(chunks):
        fail('CANDIDATE_COUNT')
    return chunks


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _record(chunk):
    return dict(chunkId=chunk.chunk_id, documentId=chunk.document_id,
                documentVersion=chunk.document_version, locator=chunk.source_locator,
                content=chunk.content, retrievalScore=chunk.score)


def _projection(record):
    return {key: record[key] for key in FIELDS}


def reconstruct(row, candidates):
    """只从已重建候选补回元数据，报告只提供冻结排序和有限分数。"""
    source = {c['chunk_id']: c for c in candidates}
    if len(source) != len(candidates):
        fail('CANDIDATE_DUPLICATE')
    records = row['stages']['retrieved']
    if not isinstance(records, list) or not 1 <= len(records) <= 8:
        fail('RETRIEVED_INVALID')
    retrieved, seen = [], set()
    for record in records:
        key = record['chunkId']
        if key not in source or key in seen or not _finite(record.get('retrievalScore')):
            fail('RETRIEVED_INVALID')
        seen.add(key)
        c = source[key]
        chunk = RetrievedChunk(chunk_id=key, document_id=c['document_id'],
            document_version=c['document_version'], current_document_version=c['document_version'],
            chunk_index=c['chunk_index'], content=c['content'], file_name=c['source_name'],
            file_type='MD', source_locator=c['source_locator'], content_hash=fingerprint(c['content']),
            distance=record['retrievalScore'], score=record['retrievalScore'])
        if _record(chunk) != record:
            fail('CANDIDATE_CHANGED')
        retrieved.append(chunk)
    order = {key: i for i, key in enumerate(source)}
    if retrieved != sorted(retrieved, key=lambda c: (-c.score, order[c.chunk_id])):
        fail('RETRIEVAL_ORDER')
    filtered = _filter_and_dedupe(retrieved)
    if row['stages']['filtered'] != [_record(c) for c in filtered]:
        fail('FILTER_DRIFT')
    allowed = {c.chunk_id: c for c in filtered}
    reranked = row['stages']['reranked']
    if len(reranked) != len(filtered):
        fail('RERANK_SET')
    ranked, seen = [], set()
    for record in reranked:
        key = record['chunkId']
        if key not in allowed or key in seen or not _finite(record.get('rerankScore')):
            fail('RERANK_INVALID')
        seen.add(key)
        c = allowed[key]
        if record != _record(c) | dict(rerankScore=record['rerankScore']):
            fail('RERANK_CHANGED')
        ranked.append(RerankedChunk(c, record['rerankScore']))
    if ranked != _validate_ranked(ranked, filtered, len(filtered)):
        fail('RERANK_ORDER')
    if row['stages']['thresholded'] != reranked:
        fail('THRESHOLD_DRIFT')
    return retrieved, ranked


def select_arm(question, retrieved, ranked, cap):
    """调用生产过滤、稳定排序、配额和两层预算；事实标注不进入本函数。"""
    candidates = _filter_and_dedupe(retrieved)
    ranked = _validate_ranked(ranked, candidates, len(candidates))
    selected, drops = _select_documents(ranked, 3, per_document_limit=cap)
    character, by_id = _bounded_contexts(selected)
    fitted = fit_customer_service_contexts(question, character, max_bytes=16384)
    if fitted is None:
        fail('PROMPT_BUDGET_INVALID')
    stages = dict(retrieved=[_record(c) for c in retrieved], filtered=[_record(c) for c in candidates],
        reranked=[_record(r.chunk) | dict(rerankScore=r.score) for r in ranked],
        thresholded=[_record(r.chunk) | dict(rerankScore=r.score) for r in ranked],
        selected=[_record(r.chunk) for r in selected])
    stages['characterBudget'] = [_projection(_record(by_id[c.chunk_id])) | dict(content=c.content) for c in character]
    stages['finalContext'] = [_projection(_record(by_id[key])) | dict(content=content)
                              for key, content in fitted if content]
    payload = customer_service_user_prompt(question,
        [CustomerServiceContext(key, text) for key, text in fitted], max_bytes=16384)
    lengths = {c.chunk_id: len(c.content) for c in character}
    return dict(stages=stages, contexts=stages['finalContext'], selectionDrops=drops,
        documentDiversity=len({c['documentId'] for c in stages['finalContext']}),
        characterTruncated=any(len(_clean_text(r.chunk.content, len(r.chunk.content)+1)) >
            lengths.get(r.chunk.chunk_id, 0) for r in selected),
        byteTruncated=[(c.chunk_id, c.content) for c in character] != fitted,
        budgets=dict(selectedChars=sum(len(r.chunk.content) for r in selected),
            characterBudgetChars=sum(len(c.content) for c in character),
            finalChars=sum(len(text) for _, text in fitted), promptBytes=len(payload.encode('utf8'))))


def validate_report(report, rubric, candidates, expected_plan=None, expected_cases=90):
    entries = rubric['entries']
    ids = [entry['id'] for entry in entries]
    if len(ids) != expected_cases or len(set(ids)) != len(ids):
        fail('RUBRIC_CASES')
    if (report.get('schemaVersion') != 'rag-qa-linked-live-report/v1'
            or report.get('status') != 'completed'
            or (expected_plan is not None and report.get('planSha256') != expected_plan)):
        fail('REPORT_INVALID')
    rows = {}
    for row in report['results']:
        key = (row['id'], row['arm'])
        if key in rows or key[0] not in ids or key[1] not in ('before', 'after'):
            fail('ROW_DUPLICATE_OR_UNKNOWN')
        rows[key] = row
    if set(rows) != {(key, arm) for key in ids for arm in ('before', 'after')}:
        fail('ROW_MISSING')
    cases = []
    for entry in entries:
        row = rows[(entry['id'], 'after')]
        retrieved, ranked = reconstruct(row, candidates)
        baseline = select_arm(entry['question'].strip(), retrieved, ranked, 1)
        if baseline['stages'] != row['stages'] or baseline['selectionDrops'] != row['selectionDrops']:
            fail('BASELINE_DRIFT')
        alternative = select_arm(entry['question'].strip(), retrieved, ranked, 2)
        old, new = baseline['contexts'], alternative['contexts']
        old_ids, new_ids = [c['chunkId'] for c in old], [c['chunkId'] for c in new]
        cases.append(dict(id=entry['id'], question=entry['question'], arms=dict(cap1=baseline, cap2=alternative),
            change=dict(contextsChanged=old != new, orderChanged=old_ids != new_ids,
                addedChunkIds=[k for k in new_ids if k not in old_ids],
                removedChunkIds=[k for k in old_ids if k not in new_ids],
                changedContextChunkIds=[c['chunkId'] for c in new if c['chunkId'] in old_ids and c != old[old_ids.index(c['chunkId'])]],
                documentDiversityDelta=alternative['documentDiversity']-baseline['documentDiversity'],
                budgetDelta={k: alternative['budgets'][k]-baseline['budgets'][k] for k in baseline['budgets']})))
    return cases


def evaluate(report, rubric, candidates, *, expected_plan=None, expected_cases=90, expected_facts=130):
    cases = validate_report(report, rubric, candidates, expected_plan, expected_cases)
    count = 0
    # 全部基线及选择已完成，再读取事实；整段原文匹配不是语义正确率。
    for case, entry in zip(cases, rubric['entries'], strict=True):
        case['requiredFacts'] = deepcopy(entry['requiredFacts'])
        case['facts'] = []
        for fact in entry['requiredFacts']:
            if fact['basisType'] != 'public-document':
                continue
            count += 1
            arms = {}
            for arm in ARMS:
                refs = [any(c['documentId'] == ref['documentId'] and
                    _clean_text(ref['quote'], len(ref['quote'])+1) in c['content']
                    for c in case['arms'][arm]['contexts']) for ref in fact['evidence']]
                if not refs or any(not ref['quote'] for ref in fact['evidence']):
                    fail('FACT_EVIDENCE_INVALID')
                arms[arm] = dict(anyWholeQuoteSurvived=any(refs), allWholeQuotesSurvived=all(refs),
                                 referenceWholeQuoteSurvival=refs)
            case['facts'].append(dict(factId=fact['factId'], arms=arms))
    if count != expected_facts:
        fail('PUBLIC_FACT_COUNT')
    return dict(schemaVersion='rag-qa-linked-cap-selection/v1', sourceStudyId=VERSION, cases=cases,
        baselineExactMatches=len(cases), publicFactCount=count, humanReview='pending',
        newProviderCalls=0, productionWrites=0,
        boundary='同一开发集离线配额对照；整段引文字面存活不等于语义覆盖或回答质量，不自动推广生产。')


def summarize(result):
    summary = {k: deepcopy(v) for k, v in result.items() if k != 'cases'}
    summary['arms'] = {arm: dict(wholeQuoteSurvived=sum(f['arms'][arm]['anyWholeQuoteSurvived']
        for c in result['cases'] for f in c['facts']),
        allWholeQuotesSurvived=sum(f['arms'][arm]['allWholeQuotesSurvived'] for c in result['cases'] for f in c['facts']),
        totalDocumentDiversity=sum(c['arms'][arm]['documentDiversity'] for c in result['cases']),
        characterTruncatedCases=sum(c['arms'][arm]['characterTruncated'] for c in result['cases']),
        byteTruncatedCases=sum(c['arms'][arm]['byteTruncated'] for c in result['cases'])) for arm in ARMS}
    summary['comparison'] = dict(changedCases=sum(c['change']['contextsChanged'] for c in result['cases']),
        diversityLossCases=sum(c['change']['documentDiversityDelta'] < 0 for c in result['cases']),
        quoteGains=sum(not f['arms']['cap1']['anyWholeQuoteSurvived'] and f['arms']['cap2']['anyWholeQuoteSurvived'] for c in result['cases'] for f in c['facts']),
        quoteLosses=sum(f['arms']['cap1']['anyWholeQuoteSurvived'] and not f['arms']['cap2']['anyWholeQuoteSurvived'] for c in result['cases'] for f in c['facts']))
    return summary


def run(output, *, network_audit=None):
    output = validate_output(output)
    corpus = PRODUCT / f'experiments/{VERSION}'
    report_path = ROOT / 'tmp/rag-qa-linked-20261120/live/report.json'
    ledger_path = ROOT / 'tmp/rag-qa-linked-20261120/authorization.jsonl'
    plan_path = corpus / 'live-plan.json'
    result_path = PRODUCT / 'evaluation/results/qa-linked-retrieval-20261120-v1.json'
    rubric_path = PRODUCT / 'evaluation/rubric-20261118-v1.json'
    plan, expected = read_json(plan_path), read_json(result_path)
    protected = validate_bindings(plan, expected, report_path, ledger_path, ROOT)
    if any(type(plan['config'].get(k)) is not type(v) or plan['config'].get(k) != v for k,v in CONFIG.items()):
        fail('CONFIG_CHANGED')
    for path in (report_path, ledger_path, plan_path, result_path, rubric_path, Path(__file__),
                 ROOT / 'docs/superpowers/plans/2026-11-20-rag-qa-linked-cap-selection.md',
                 Path(__file__).with_name('rag_answer_first_evaluation.py')):
        protected.setdefault(str(safe_path(path, ROOT)), sha(path))
    candidates = load_candidates(corpus)
    result = evaluate(read_json(report_path), read_json(rubric_path), candidates,
                      expected_plan=expected['planSha256'])
    if any(sha(path) != digest for path, digest in protected.items()):
        fail('INPUT_CHANGED')
    if network_audit is not None and network_audit['attempts']:
        fail('NETWORK_FORBIDDEN')
    result['bindings'] = dict(planCanonicalSha256=expected['planSha256'], reportSha256=expected['reportSha256'],
        ledgerSha256=expected['ledgerSha256'], moduleSha256=sha(Path(__file__)),
        evaluationPlanSha256=sha(ROOT / 'docs/superpowers/plans/2026-11-20-rag-qa-linked-cap-selection.md'))
    result['networkAudit'] = deepcopy(network_audit) if network_audit is not None else dict(enabled=False, attempts=None)
    template = dict(schemaVersion='rag-qa-linked-cap-review/v1', status='pending', reviewer=None,
        evidencePackageSha256=fingerprint(result),
        boundary='真人复核待填写；代理判断另存，不将字面统计当作语义判断。',
        cases=[dict(id=c['id'], status='pending', addedEvidenceSupport=None, removedEvidenceRegression=None,
            facts=[dict(factId=f['factId'], semanticSupportByArm={a: None for a in ARMS}, notes='') for f in c['facts']], notes='') for c in result['cases']])
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'comparison.json', result)
    write_json(output / 'summary.json', summarize(result))
    write_json(output / 'reviews-template.json', template)
    if any(sha(path) != digest for path, digest in protected.items()):
        fail('INPUT_CHANGED')
    write_json(output / 'input-integrity.json', dict(unchanged=True, fileCount=len(protected), sha256=protected))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        with block_network() as audit:
            result = run(args.output, network_audit=audit)
        print(f'baselineExactMatches={result["baselineExactMatches"]} newProviderCalls=0 humanReview=pending')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        parser.exit(2, 'QA_CAP_INPUT_OR_OUTPUT_INVALID\n')


if __name__ == '__main__':
    main()
