"""第30轮冻结混合候选的离线追加实验；不修改正式三块上限或引用协议。"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path

from ai_service.models.base import CustomerServiceContext
from ai_service.orchestration.customer_service_rag import (
    MAX_CONTEXT_CHARS, MAX_TOTAL_CONTEXT_CHARS, _bounded_contexts,
    _clean_text, _filter_and_dedupe, _select_documents, _validate_ranked,
)
from ai_service.orchestration.rag_qa_linked_cap_evaluation import (
    STAGES, _record, _projection, load_candidates, reconstruct, safe_path,
    select_arm, validate_output,
)
from ai_service.orchestration.rag_hybrid_cap_evaluation import expand_archive, quote_failure
from ai_service.orchestration.rag_answer_first_evaluation import block_network
from ai_service.orchestration.rag_qa_linked_budget import fingerprint
from ai_service.orchestration.rag_qa_linked_corpus import PRODUCT, ROOT, VERSION, read_json, sha, write_json
from ai_service.prompts.customer_service import customer_service_user_prompt, fit_customer_service_contexts

ARMS = ('cap1', 'append1')
ARCHIVE_SHA256 = '3e30596e07385bc9e2efc82351855329eb01600c6e3d52d0058b013bd0265bf8'
COMPARISON_SHA256 = 'e6056a380775e556c67714f56ab5ce8e932aaf3b359c409361d8cbb7aa26147a'


def fail(code):
    raise ValueError('HYBRID_APPEND_' + code)


def bounded_append_contexts(selected):
    """实验仅放宽顺序扫描到四块，单块与总字数及清洗均沿用正式常量。"""
    contexts, by_id = [], {}
    remaining = MAX_TOTAL_CONTEXT_CHARS
    for item in selected[:4]:
        content = _clean_text(item.chunk.content, min(MAX_CONTEXT_CHARS, remaining))
        if not content:
            continue
        contexts.append(CustomerServiceContext(item.chunk.chunk_id, content))
        by_id[item.chunk.chunk_id] = item.chunk
        remaining -= len(content)
        if remaining <= 0:
            break
    return contexts, by_id


def select_append(question, retrieved, ranked):
    """标签不可进入选择；保留正式cap1顺序，只追加首个符合条件的候选。"""
    filtered = _filter_and_dedupe(retrieved)
    ranked = _validate_ranked(ranked, filtered, len(filtered))
    selected, baseline_drops = _select_documents(ranked, 3, per_document_limit=1)
    baseline_count = len(selected)
    ids = {r.chunk.chunk_id for r in selected}
    counts = Counter(r.chunk.document_id for r in selected)
    added, skips = None, {}
    for item in ranked:
        chunk = item.chunk
        if chunk.chunk_id in ids:
            skips[chunk.chunk_id] = 'already_selected'
        elif counts[chunk.document_id] >= 2:
            skips[chunk.chunk_id] = 'document_chunk_limit'
        elif added is not None:
            skips[chunk.chunk_id] = 'append_limit'
        else:
            selected.append(item)
            added = chunk.chunk_id
            ids.add(added)
            counts[chunk.document_id] += 1
    character, by_id = bounded_append_contexts(selected)
    fitted = fit_customer_service_contexts(question, character, max_bytes=16384)
    if fitted is None:
        fail('PROMPT_BUDGET_INVALID')
    stages = dict(retrieved=[_record(c) for c in retrieved], filtered=[_record(c) for c in filtered],
        reranked=[_record(r.chunk) | dict(rerankScore=r.score) for r in ranked],
        thresholded=[_record(r.chunk) | dict(rerankScore=r.score) for r in ranked],
        selected=[_record(r.chunk) for r in selected])
    stages['characterBudget'] = [_projection(_record(by_id[c.chunk_id])) | dict(content=c.content) for c in character]
    stages['finalContext'] = [_projection(_record(by_id[key])) | dict(content=content) for key, content in fitted if content]
    payload = customer_service_user_prompt(question, [CustomerServiceContext(k, t) for k, t in fitted], max_bytes=16384)
    lengths = {c.chunk_id: len(c.content) for c in character}
    return dict(stages=stages, contexts=stages['finalContext'],
        selectionDrops={k: v for k, v in skips.items() if k not in ids},
        append=dict(addedChunkId=added, reason='appended' if added else 'no_eligible_candidate',
            baselineSelectedCount=baseline_count, baselineSelectionDrops=baseline_drops, candidateDecisions=skips),
        documentDiversity=len({c['documentId'] for c in stages['finalContext']}),
        characterTruncated=any(len(_clean_text(r.chunk.content, len(r.chunk.content)+1)) > lengths.get(r.chunk.chunk_id, 0) for r in selected),
        byteTruncated=[(c.chunk_id, c.content) for c in character] != fitted,
        budgets=dict(selectedChars=sum(len(r.chunk.content) for r in selected), characterBudgetChars=sum(len(c.content) for c in character),
            finalChars=sum(len(t) for _, t in fitted), promptBytes=len(payload.encode('utf8'))))


def stage_transitions(arm):
    """按块身份记录相邻阶段丢失、增加、正文变化，区分截断与选择顺序变化。"""
    result = {}
    for before, after in zip(STAGES, STAGES[1:]):
        old, new = arm['stages'][before], arm['stages'][after]
        old_by_id, new_by_id = ({c['chunkId']: c for c in rows} for rows in (old, new))
        result[after] = dict(droppedChunkIds=[k for k in old_by_id if k not in new_by_id],
            addedChunkIds=[k for k in new_by_id if k not in old_by_id],
            contentChangedChunkIds=[k for k in new_by_id if k in old_by_id and new_by_id[k]['content'] != old_by_id[k]['content']],
            truncatedChunkIds=[k for k in new_by_id if k in old_by_id and
                len(_clean_text(new_by_id[k]['content'], len(new_by_id[k]['content'])+1)) <
                len(_clean_text(old_by_id[k]['content'], len(old_by_id[k]['content'])+1))])
    return result


def evaluate(frozen, rubric, candidates, *, expected_cases=90, expected_facts=130):
    entries = rubric['entries']
    ids = [e['id'] for e in entries]
    if len(ids) != expected_cases or len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i for i in ids):
        fail('CASE_IDENTITIES')
    source = frozen['cases']
    if [c['id'] for c in source] != ids:
        fail('CASE_ORDER_OR_IDENTITIES')
    cases = []
    for row, entry in zip(source, entries, strict=True):
        if row['question'] != entry['question']:
            fail('CASE_QUESTION')
        if not isinstance(row['arms']['cap1'].get('stages'), dict):
            fail('BASELINE_DRIFT')
        retrieved, ranked = reconstruct(row['arms']['cap1'], candidates)
        baseline = select_arm(entry['question'].strip(), retrieved, ranked, 1)
        if baseline != row['arms']['cap1']:
            fail('BASELINE_DRIFT')
        alternative = select_append(entry['question'].strip(), retrieved, ranked)
        old, new = baseline['contexts'], alternative['contexts']
        old_ids, new_ids = ([c['chunkId'] for c in rows] for rows in (old, new))
        cases.append(dict(id=entry['id'], question=entry['question'], arms=dict(cap1=baseline, append1=alternative),
            stageTransitions={arm: stage_transitions(value) for arm, value in zip(ARMS, (baseline, alternative))},
            change=dict(contextsChanged=old != new, orderChanged=old_ids != new_ids,
                baselineSelectedPrefixPreserved=alternative['stages']['selected'][:len(baseline['stages']['selected'])] == baseline['stages']['selected'],
                baselineFinalContextsPreserved=new[:len(old)] == old,
                addedChunkIds=[k for k in new_ids if k not in old_ids], removedChunkIds=[k for k in old_ids if k not in new_ids],
                changedContextChunkIds=[c['chunkId'] for c in new if c['chunkId'] in old_ids and c != old[old_ids.index(c['chunkId'])]],
                documentDiversityDelta=alternative['documentDiversity']-baseline['documentDiversity'],
                budgetDelta={k: alternative['budgets'][k]-baseline['budgets'][k] for k in baseline['budgets']})))
    # 所有题目的两组选择全部完成后才消费标注，防止事实标签影响候选选择。
    seen, documents = set(), {c['document_id'] for c in candidates}
    for case, entry in zip(cases, entries, strict=True):
        case['requiredFacts'], case['facts'] = deepcopy(entry['requiredFacts']), []
        for fact in entry['requiredFacts']:
            identity = fact['factId']
            if not isinstance(identity, str) or not identity or identity in seen:
                fail('FACT_IDENTITY')
            seen.add(identity)
            if fact['basisType'] != 'public-document':
                continue
            refs = fact['evidence']
            if not isinstance(refs, list) or not refs or any(not isinstance(r.get('quote'), str) or
                not _clean_text(r['quote'], len(r['quote'])+1) or r.get('documentId') not in documents for r in refs):
                fail('FACT_EVIDENCE_INVALID')
            arms = {}
            for arm in ARMS:
                stages = {}
                for stage in STAGES:
                    survives = [any(c['documentId'] == ref['documentId'] and
                        _clean_text(ref['quote'], len(ref['quote'])+1) in _clean_text(c['content'], len(c['content'])+1)
                        for c in case['arms'][arm]['stages'][stage]) for ref in refs]
                    stages[stage] = dict(anyWholeQuoteSurvived=any(survives), allWholeQuotesSurvived=all(survives), referenceWholeQuoteSurvival=survives)
                arms[arm] = dict(stages['finalContext'], stages=stages, quoteFailure=quote_failure(stages))
            old, new = (arms[a]['anyWholeQuoteSurvived'] for a in ARMS)
            case['facts'].append(dict(factId=identity, arms=arms, change='gain' if not old and new else 'loss' if old and not new else 'unchanged'))
        case['change']['gainedFactIds'] = [f['factId'] for f in case['facts'] if f['change'] == 'gain']
        case['change']['lostFactIds'] = [f['factId'] for f in case['facts'] if f['change'] == 'loss']
    count = sum(len(c['facts']) for c in cases)
    if count != expected_facts:
        fail('PUBLIC_FACT_COUNT')
    return dict(schemaVersion='rag-hybrid-append-selection/v1', cases=cases, baselineExactMatches=len(cases), publicFactCount=count,
        humanReview='pending', newProviderCalls=0, newBgeCalls=0, newEmbeddingCalls=0, newChatCalls=0, serviceCalls=0,
        indexWrites=0, productionWrites=0, answerQuality=None, refusalQuality=None,
        experimentContextLimit=4, productionCitationLimit=3,
        boundary='同一开发集冻结候选离线追加；引文存活不等于语义支持或回答质量。正式三块与引用上限不变，不自动推广。')


def summarize(result):
    summary = {k: deepcopy(v) for k, v in result.items() if k != 'cases'}
    cases = result['cases']
    summary['arms'] = {arm: dict(wholeQuoteSurvived=sum(f['arms'][arm]['anyWholeQuoteSurvived'] for c in cases for f in c['facts']),
        allWholeQuotesSurvived=sum(f['arms'][arm]['allWholeQuotesSurvived'] for c in cases for f in c['facts']),
        wholeQuoteSurvivalByStage={s: sum(f['arms'][arm]['stages'][s]['anyWholeQuoteSurvived'] for c in cases for f in c['facts']) for s in STAGES},
        totalDocumentDiversity=sum(c['arms'][arm]['documentDiversity'] for c in cases),
        totalPromptBytes=sum(c['arms'][arm]['budgets']['promptBytes'] for c in cases),
        totalFinalChars=sum(c['arms'][arm]['budgets']['finalChars'] for c in cases),
        characterTruncatedCases=sum(c['arms'][arm]['characterTruncated'] for c in cases),
        byteTruncatedCases=sum(c['arms'][arm]['byteTruncated'] for c in cases),
        quoteFailureCounts=dict(Counter(f['arms'][arm]['quoteFailure'] for c in cases for f in c['facts']))) for arm in ARMS}
    summary['comparison'] = dict(quoteGains=sum(f['change'] == 'gain' for c in cases for f in c['facts']),
        quoteLosses=sum(f['change'] == 'loss' for c in cases for f in c['facts']),
        changedCases=sum(c['change']['contextsChanged'] for c in cases),
        diversityLossCases=sum(c['change']['documentDiversityDelta'] < 0 for c in cases),
        selectedPrefixPreservedCases=sum(c['change']['baselineSelectedPrefixPreserved'] for c in cases),
        finalContextsPreservedCases=sum(c['change']['baselineFinalContextsPreserved'] for c in cases),
        appendedCases=sum(c['arms']['append1']['append']['addedChunkId'] is not None for c in cases))
    summary['noPublicFactCases'] = sum(not c['facts'] for c in cases)
    return summary


def verify_protected(protected):
    if any(sha(path) != digest for path, digest in protected.items()):
        fail('INPUT_CHANGED')


def validate_integrity(integrity, root, *, expected_count=55):
    if integrity.get('unchanged') is not True or integrity.get('fileCount') != expected_count or len(integrity.get('sha256', {})) != expected_count:
        fail('INTEGRITY_SCHEMA')
    protected = {}
    for name, digest in integrity['sha256'].items():
        path = safe_path(name, root)
        if not Path(name).is_absolute() or str(path.resolve()) in protected:
            fail('INTEGRITY_PATH')
        protected[str(path.resolve())] = digest
    verify_protected(protected)
    return protected


def dependency_paths(root):
    """绑定全部本地Python源码覆盖传递导入，另绑定实际测试及实验规范，避免漏掉间接依赖。"""
    root = Path(root)
    return sorted((root / 'ai-service/src/ai_service').rglob('*.py')) + [
        root / 'ai-service/tests/test_rag_hybrid_append_evaluation.py',
        root / 'ai-service/tests/test_rag_qa_linked_cap_evaluation.py',
        root / 'ai-service/pyproject.toml', root / 'ai-service/uv.lock',
        root / 'docs/superpowers/specs/2026-11-20-rag-hybrid-append-round30-design.md',
        root / 'docs/superpowers/plans/2026-11-20-rag-hybrid-append-round30.md']


def validate_archive(archive, comparison, candidates):
    if archive.get('schemaVersion') != 'rag-hybrid-cap-validation-bundle/v1':
        fail('ARCHIVE_SCHEMA')
    chunks = {c['chunk_id']: dict(chunkId=c['chunk_id'], documentId=c['document_id'], documentVersion=c['document_version'], locator=c['source_locator'], content=c['content']) for c in candidates}
    if archive['chunksById'] != chunks:
        fail('ARCHIVE_CHUNKS')
    if expand_archive(archive['comparison'], chunks) != comparison:
        fail('ARCHIVE_COMPARISON_DRIFT')


def run(output, *, network_audit=None):
    # 即使被库调用也强制屏蔽网络，CLI的外层审计保持同一份结果。
    if network_audit is None:
        with block_network() as audit:
            return run(output, network_audit=audit)
    output = validate_output(output, ROOT / 'tmp')
    archive_path = safe_path(PRODUCT / 'evaluation/results/hybrid-cap-selection-20261120-v1.json', ROOT)
    comparison_path = safe_path(ROOT / 'tmp/rag-hybrid-cap-20261120/run-final/comparison.json', ROOT)
    integrity_path = safe_path(comparison_path.with_name('input-integrity.json'), ROOT)
    if sha(archive_path) != ARCHIVE_SHA256 or sha(comparison_path) != COMPARISON_SHA256:
        fail('ARCHIVE_HASH')
    archive, frozen = read_json(archive_path), read_json(comparison_path)
    if read_json(integrity_path) != archive['inputIntegrity']:
        fail('INTEGRITY_ARCHIVE_DRIFT')
    protected = validate_integrity(archive['inputIntegrity'], ROOT)
    original_count = len(protected)
    for path in [archive_path, comparison_path, integrity_path, *dependency_paths(ROOT)]:
        path = safe_path(path, ROOT)
        digest = sha(path)
        if str(path.resolve()) in protected and protected[str(path.resolve())] != digest:
            fail('INPUT_CHANGED')
        protected[str(path.resolve())] = digest
    candidates = load_candidates(PRODUCT / f'experiments/{VERSION}')
    validate_archive(archive, frozen, candidates)
    result = evaluate(frozen, read_json(PRODUCT / 'evaluation/rubric-20261118-v1.json'), candidates)
    verify_protected(protected)
    if not network_audit.get('enabled') or network_audit['attempts']:
        fail('NETWORK_FORBIDDEN')
    result['bindings'] = dict(archiveSha256=ARCHIVE_SHA256, comparisonSha256=COMPARISON_SHA256,
        originalBindingCount=original_count, fileCount=len(protected), sha256=deepcopy(protected))
    result['networkAudit'] = deepcopy(network_audit)
    result['historicalCap2'] = dict(scope='第28轮冻结统计，非本轮第三实验组', summary=deepcopy(frozen['summary']['arms']['cap2']))
    template = dict(schemaVersion='rag-hybrid-append-review/v1', status='pending', reviewer=None,
        evidencePackageSha256=fingerprint(result), boundary='真人复核待填写；代理语义复核另存。',
        cases=[dict(id=c['id'], status='pending', facts=[dict(factId=f['factId'], semanticSupportByArm={a: None for a in ARMS}, notes='') for f in c['facts']], notes='') for c in result['cases']])
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'comparison.json', result)
    write_json(output / 'summary.json', summarize(result))
    write_json(output / 'reviews-template.json', template)
    verify_protected(protected)
    if network_audit['attempts']:
        fail('NETWORK_FORBIDDEN')
    write_json(output / 'input-integrity.json', dict(unchanged=True, fileCount=len(protected), originalBindingCount=original_count, sha256=protected))
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
        parser.exit(2, 'HYBRID_APPEND_INPUT_OR_OUTPUT_INVALID\n')


if __name__ == '__main__':
    main()
