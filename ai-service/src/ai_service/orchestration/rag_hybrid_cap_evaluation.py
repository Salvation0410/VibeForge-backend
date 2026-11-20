"""第28轮冻结混合Top8与BGE分数的离线配额回放；不加载模型或重算检索。"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from ai_service.orchestration.rag_qa_linked_cap_evaluation import (
    ARMS, CONFIG, FIELDS, STAGES, _clean_text, load_candidates, reconstruct,
    safe_path, select_arm, summarize, validate_bindings, validate_output,
)
from ai_service.orchestration.rag_answer_first_evaluation import block_network
from ai_service.orchestration.rag_qa_linked_budget import fingerprint
from ai_service.orchestration.rag_qa_linked_corpus import PRODUCT, ROOT, VERSION, read_json, sha, write_json

ARCHIVE_SHA256 = 'd5154dac0db33ecc4f848773a412e12530516a9c0b2b09ab4f0f5b06762e0e22'
PLAN_SHA256 = 'ffe29e4a00dedd394a835d6182d28fd42d3518f067f114425fe084d9451ad319'
ORIGINAL_ARMS = ('dense', 'hybrid')


def fail(code):
    raise ValueError('HYBRID_CAP_' + code)


def expand_archive(value, chunks):
    """展开归档引用；只有显式正文覆盖允许替换原块字段。"""
    if isinstance(value, list):
        return [expand_archive(item, chunks) for item in value]
    if not isinstance(value, dict):
        return value
    if 'chunkRef' not in value:
        if 'contentOverride' in value:
            fail('ARCHIVE_OVERRIDE')
        return {key: expand_archive(item, chunks) for key, item in value.items()}
    key = value['chunkRef']
    if key not in chunks or any(field in value for field in FIELDS):
        fail('ARCHIVE_REFERENCE')
    result = deepcopy(chunks[key])
    if 'contentOverride' in value:
        if not isinstance(value['contentOverride'], str):
            fail('ARCHIVE_OVERRIDE')
        result['content'] = value['contentOverride']
    result.update({key: expand_archive(item, chunks) for key, item in value.items()
                   if key not in ('chunkRef', 'contentOverride')})
    return result


def schedule_for(report, entries):
    rows = {(row['id'], row['arm']): row for row in report['results']}
    return [dict(kind='bge', key=f'{entry["id"]}:{arm}', inputSha256=fingerprint(dict(
        question=entry['question'].strip(), chunks=rows[(entry['id'], arm)]['stages']['retrieved'])))
        for index, entry in enumerate(entries)
        for arm in (ORIGINAL_ARMS if index % 2 == 0 else ORIGINAL_ARMS[::-1])]


def validate_schedule(report, entries, plan, ledger):
    schedule = schedule_for(report, entries)
    if (plan['schedule'] != schedule or report.get('planSha256') != fingerprint(plan)
            or report.get('reservedOperations') != len(schedule)
            or [(row['id'], row['arm']) for row in report['results']] !=
                [(item['key'].rsplit(':', 1)[0], item['key'].rsplit(':', 1)[1]) for item in schedule]):
        fail('SCHEDULE_DRIFT')
    expected = [dict(type='authorization', planSha256=fingerprint(plan), bgeLimit=len(schedule),
        embeddingLimit=0, chatLimit=0, indexWrites=0)] + [
        dict(type='reserved', ordinal=index, **item) for index, item in enumerate(schedule)]
    if ledger != expected:
        fail('LEDGER_DRIFT')


def validate_archive(archive, report, candidates, entries):
    if archive.get('schemaVersion') != 'rag-hybrid-validation-bundle/v1':
        fail('ARCHIVE_SCHEMA')
    chunks = archive['chunksById']
    expected = {c['chunk_id']: dict(chunkId=c['chunk_id'], documentId=c['document_id'],
        documentVersion=c['document_version'], locator=c['source_locator'], content=c['content']) for c in candidates}
    if chunks != expected:
        fail('ARCHIVE_CHUNKS')
    comparison = expand_archive(archive['comparison'], chunks)
    cases = comparison['cases']
    if [c['id'] for c in cases] != [e['id'] for e in entries]:
        fail('ARCHIVE_CASES')
    rows = {(r['id'], r['arm']): r for r in report['results']}
    for case, entry in zip(cases, entries, strict=True):
        if case['question'] != entry['question'].strip() or set(case['arms']) != set(ORIGINAL_ARMS):
            fail('ARCHIVE_CASES')
        for arm in ORIGINAL_ARMS:
            original = {k: v for k, v in case['arms'][arm].items() if k != 'facts'}
            if original != rows[(case['id'], arm)]:
                fail('ARCHIVE_REPORT_DRIFT')


def quote_failure(observed):
    for stage, reason in zip(STAGES, ('retrieval', 'filter', 'rerank', 'threshold', 'selection',
                                     'character_budget', 'prompt_budget'), strict=True):
        if not observed[stage]['anyWholeQuoteSurvived']:
            return reason + '_quote_missing'
    return 'survived'


def evaluate(report, rubric, candidates, *, expected_plan=None, expected_cases=90, expected_facts=130):
    """完成全部选择后才访问事实标签；旧报告各组全字段精确复现。"""
    entries = rubric['entries']
    ids = [e['id'] for e in entries]
    if len(ids) != expected_cases or len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i for i in ids):
        fail('CASE_IDENTITIES')
    if (report.get('schemaVersion') != 'rag-hybrid-live/v1' or report.get('status') != 'completed'
            or (expected_plan is not None and report.get('planSha256') != expected_plan)):
        fail('REPORT_INVALID')
    rows = {}
    for row in report['results']:
        key = (row['id'], row['arm'])
        if key in rows or key[0] not in ids or key[1] not in ORIGINAL_ARMS:
            fail('ROW_DUPLICATE_OR_UNKNOWN')
        rows[key] = row
    if set(rows) != {(identity, arm) for identity in ids for arm in ORIGINAL_ARMS}:
        fail('ROW_MISSING')
    cases = []
    for entry in entries:
        for arm in ORIGINAL_ARMS:
            row = rows[(entry['id'], arm)]
            retrieved, ranked = reconstruct(row, candidates)
            baseline = select_arm(entry['question'].strip(), retrieved, ranked, 1)
            if any(key not in row or row[key] != value for key, value in baseline.items()):
                fail('BASELINE_DRIFT')
            if arm == 'hybrid':
                alternative = select_arm(entry['question'].strip(), retrieved, ranked, 2)
        old, new = baseline['contexts'], alternative['contexts']
        old_ids, new_ids = [c['chunkId'] for c in old], [c['chunkId'] for c in new]
        cases.append(dict(id=entry['id'], question=entry['question'], arms=dict(cap1=baseline, cap2=alternative),
            change=dict(contextsChanged=old != new, orderChanged=old_ids != new_ids,
                addedChunkIds=[key for key in new_ids if key not in old_ids],
                removedChunkIds=[key for key in old_ids if key not in new_ids],
                changedContextChunkIds=[c['chunkId'] for c in new if c['chunkId'] in old_ids and c != old[old_ids.index(c['chunkId'])]],
                documentDiversityDelta=alternative['documentDiversity']-baseline['documentDiversity'],
                budgetDelta={k: alternative['budgets'][k]-baseline['budgets'][k] for k in baseline['budgets']})))
    seen = set()
    documents = {c['document_id'] for c in candidates}
    for case, entry in zip(cases, entries, strict=True):
        case['requiredFacts'] = deepcopy(entry['requiredFacts'])
        case['facts'] = []
        for fact in entry['requiredFacts']:
            if fact['basisType'] != 'public-document':
                continue
            identity = fact['factId']
            if not isinstance(identity, str) or not identity or identity in seen:
                fail('FACT_IDENTITY')
            seen.add(identity)
            refs = fact['evidence']
            if not isinstance(refs, list) or not refs or any(not isinstance(r.get('quote'), str)
                or not _clean_text(r['quote'], len(r['quote'])+1) or r.get('documentId') not in documents for r in refs):
                fail('FACT_EVIDENCE_INVALID')
            arms = {}
            for arm in ARMS:
                stages = {}
                for stage in STAGES:
                    # 早期阶段保留原始换行，两侧采用同一空白归一化，避免阶段间伪缺失。
                    survives = [any(c['documentId'] == ref['documentId'] and
                        _clean_text(ref['quote'], len(ref['quote'])+1) in
                        _clean_text(c['content'], len(c['content'])+1)
                        for c in case['arms'][arm]['stages'][stage]) for ref in refs]
                    stages[stage] = dict(anyWholeQuoteSurvived=any(survives), allWholeQuotesSurvived=all(survives),
                                        referenceWholeQuoteSurvival=survives)
                arms[arm] = dict(stages['finalContext'], stages=stages, quoteFailure=quote_failure(stages))
            before, after = (arms[a]['anyWholeQuoteSurvived'] for a in ARMS)
            case['facts'].append(dict(factId=identity, arms=arms,
                change='gain' if not before and after else 'loss' if before and not after else 'unchanged'))
        case['change']['gainedFactIds'] = [f['factId'] for f in case['facts'] if f['change'] == 'gain']
        case['change']['lostFactIds'] = [f['factId'] for f in case['facts'] if f['change'] == 'loss']
    if len(seen) != expected_facts:
        fail('PUBLIC_FACT_COUNT')
    result = dict(schemaVersion='rag-hybrid-cap-selection/v1', cases=cases,
        baselineExactMatches=len(cases), validatedOriginalRows=len(rows), publicFactCount=len(seen),
        humanReview='pending', newProviderCalls=0, newBgeCalls=0, newEmbeddingCalls=0, newChatCalls=0,
        serviceCalls=0, indexWrites=0, productionWrites=0, answerQuality=None, refusalQuality=None,
        boundary='重复开发集离线回放；整段引文字面存活不是语义支持或回答质量，不自动推广生产。')
    result['summary'] = summarize(result)
    result['summary']['noPublicFactCases'] = sum(not c['facts'] for c in cases)
    for arm in ARMS:
        arm_summary = result['summary']['arms'][arm]
        arm_summary['wholeQuoteSurvivalByStage'] = {stage: sum(
            f['arms'][arm]['stages'][stage]['anyWholeQuoteSurvived'] for c in cases for f in c['facts']) for stage in STAGES}
        arm_summary['totalPromptBytes'] = sum(c['arms'][arm]['budgets']['promptBytes'] for c in cases)
        arm_summary['totalFinalChars'] = sum(c['arms'][arm]['budgets']['finalChars'] for c in cases)
        arm_summary['quoteFailureCounts'] = {reason: sum(f['arms'][arm]['quoteFailure'] == reason
            for c in cases for f in c['facts']) for reason in ('retrieval_quote_missing', 'filter_quote_missing',
                'rerank_quote_missing', 'threshold_quote_missing', 'selection_quote_missing',
                'character_budget_quote_missing', 'prompt_budget_quote_missing', 'survived')}
    return result


def run(output, *, network_audit=None):
    output = validate_output(output, ROOT / 'tmp')
    archive_path = PRODUCT / 'evaluation/results/hybrid-retrieval-20261120-v1.json'
    if sha(safe_path(archive_path, ROOT)) != ARCHIVE_SHA256:
        fail('ARCHIVE_HASH')
    archive = read_json(archive_path)
    plan_path = PRODUCT / 'experiments/hybrid-20261120-v1/live-plan.json'
    report_path = ROOT / 'tmp/rag-hybrid-20261120/live/report.json'
    ledger_path = ROOT / 'tmp/rag-hybrid-20261120/authorization.jsonl'
    rubric_path = PRODUCT / 'evaluation/rubric-20261118-v1.json'
    plan, audit = read_json(safe_path(plan_path, ROOT)), archive['audit']
    if (fingerprint(plan) != PLAN_SHA256 or audit['planCanonicalSha256'] != PLAN_SHA256
            or len(plan['bindings']) != 47 or any(type(plan['config'].get(k)) is not type(v)
                or plan['config'].get(k) != v for k,v in CONFIG.items())):
        fail('PLAN_CHANGED')
    protected = validate_bindings(plan, dict(planSha256=PLAN_SHA256,
        reportSha256=audit['reportSha256'], ledgerSha256=audit['ledgerSha256']), report_path, ledger_path, ROOT)
    # 原向量仅由上面的逐文件SHA验证，禁止读入JSON或调用任何检索/推理入口。
    for path in (archive_path, plan_path, rubric_path, Path(__file__),
        Path(__file__).with_name('rag_answer_first_evaluation.py'),
        ROOT / 'docs/superpowers/plans/2026-11-20-rag-hybrid-cap-round28.md',
        ROOT / 'docs/superpowers/specs/2026-11-20-rag-hybrid-cap-round28-design.md'):
        protected.setdefault(str(safe_path(path, ROOT)), sha(path))
    candidates = load_candidates(PRODUCT / f'experiments/{VERSION}')
    report, rubric = read_json(report_path), read_json(rubric_path)
    with ledger_path.open(encoding='utf8') as stream:
        ledger = [json.loads(line) for line in stream]
    # 先完成选择，随后核对带事实诊断的归档；标签不能进入排序或配额输入。
    result = evaluate(report, rubric, candidates, expected_plan=PLAN_SHA256)
    validate_schedule(report, rubric['entries'], plan, ledger)
    validate_archive(archive, report, candidates, rubric['entries'])
    if any(sha(path) != digest for path, digest in protected.items()):
        fail('INPUT_CHANGED')
    if network_audit is not None and network_audit['attempts']:
        fail('NETWORK_FORBIDDEN')
    result['bindings'] = dict(archiveSha256=ARCHIVE_SHA256, planCanonicalSha256=PLAN_SHA256,
        reportSha256=audit['reportSha256'], ledgerSha256=audit['ledgerSha256'], originalBindingCount=len(plan['bindings']))
    result['networkAudit'] = deepcopy(network_audit) if network_audit is not None else dict(enabled=False, attempts=None)
    template = dict(schemaVersion='rag-hybrid-cap-review/v1', status='pending', reviewer=None,
        evidencePackageSha256=fingerprint(result), boundary='真人复核待填写；代理语义复核必须另存。',
        cases=[dict(id=c['id'], status='pending', addedEvidenceSupport=None, removedEvidenceRegression=None,
            facts=[dict(factId=f['factId'], semanticSupportByArm={a: None for a in ARMS}, notes='') for f in c['facts']], notes='') for c in result['cases']])
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'comparison.json', result)
    write_json(output / 'summary.json', result['summary'])
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
        print(f'baselineExactMatches={result["baselineExactMatches"]} validatedOriginalRows={result["validatedOriginalRows"]} newProviderCalls=0 humanReview=pending')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        parser.exit(2, 'HYBRID_CAP_INPUT_OR_OUTPUT_INVALID\n')


if __name__ == '__main__':
    main()
