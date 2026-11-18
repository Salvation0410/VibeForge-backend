"""全量答案盲包、严格评审导入及题级配对统计；不调用任何模型。"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import math
from pathlib import Path
import random
import secrets
from statistics import fmean
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_service.orchestration.rag_rerank_budget import write_json
from ai_service.orchestration.rag_rerank_contracts import (
    ARMS, STUDY_ID, fail, fingerprint, load_rubric, read_json,
)

CATEGORIES = ('not_recalled', 'ranking_truncation', 'quota_or_budget_loss',
              'evidence_present_generation_error', 'annotation_dispute')


@dataclass(frozen=True)
class Study:
    study_id: str
    arms: tuple[str, str]
    analysis_schema: str
    paired_keys: tuple[str, str, str]
    timing_meaning: str


# 研究与实验臂成对冻结，不能将两组白名单取并集后接受交叉报告。
_STUDIES = {study.study_id: study for study in (
    Study(STUDY_ID, ARMS, 'rag-rerank-analysis/v1', ('offOnly', 'onOnly', 'onMinusOff'),
          '共享检索/BGE准备与回答耗时分账，不是两次在线E2E'),
    Study('rag-cap-20261118-answer-round-v1', ('cap1', 'cap2'), 'rag-cap-analysis/v1',
          ('cap1Only', 'cap2Only', 'cap2MinusCap1'),
          '历史候选与BGE结果回放；仅统计本轮新回答耗时，未计当前检索/BGE耗时，不是在线E2E'),
)}


def _study(study_id):
    if not isinstance(study_id, str) or study_id not in _STUDIES:
        fail('STUDY_INVALID')
    return _STUDIES[study_id]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Support(Strict):
    chunkId: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class FactReview(Strict):
    factId: str = Field(min_length=1)
    verdict: Literal['pending', 'met', 'missing', 'contradicted']
    answerQuote: str
    notes: str


class ClaimReview(Strict):
    text: str = Field(min_length=1)
    supported: bool
    evidence: list[Support]


class CitationReview(Strict):
    chunkId: str = Field(min_length=1)
    supported: bool
    claimTexts: list[str]


class CardReview(Strict):
    blindId: str = Field(min_length=1)
    status: Literal['pending', 'complete']
    facts: list[FactReview]
    claims: list[ClaimReview]
    claimsComplete: bool | None
    citations: list[CitationReview]
    answerRelevance: int | None = Field(ge=0, le=2)
    factualError: bool | None
    safetyViolation: bool | None
    annotationDispute: bool | None
    failureCategories: list[Literal['not_recalled', 'ranking_truncation', 'quota_or_budget_loss',
        'evidence_present_generation_error', 'annotation_dispute']]
    notes: str


class Reviewer(Strict):
    name: str = Field(min_length=1)
    role: Literal['human', 'agent']


class Reviews(Strict):
    schemaVersion: Literal['rag-blind-reviews/v1']
    studyId: Literal['rag-rerank-20261118-first-round-v1', 'rag-cap-20261118-answer-round-v1']
    publicPackageSha256: str
    reviewer: Reviewer
    reviews: list[CardReview]


def result_key(record):
    return record['id'] + ':' + record['arm']


def validate_results(report, rubric):
    study = _study(report.get('studyId'))
    expected = {e['id'] + ':' + arm for e in rubric['entries'] for arm in study.arms}
    records = report.get('results', [])
    if (len(records) != 180
            or len({result_key(r) for r in records}) != 180
            or {result_key(r) for r in records} != expected
            or report.get('rubricSha256') != fingerprint(rubric)):
        fail('RESULTS_INCOMPLETE')
    for row in records:
        if row['status'] not in {'not_run', 'error', 'ok'}:
            fail('RESULT_STATUS_INVALID')
        if row['status'] == 'ok' and (type(row.get('answered')) is not bool
                or not isinstance(row.get('answer'), str)
                or not isinstance(row.get('sources'), list)):
            fail('RESULT_STATUS_INVALID')
        if row['status'] == 'ok':
            from ai_service.orchestration.customer_service_rag import NO_ANSWER_TEXT
            terminal = row.get('trace', {}).get('terminal', {})
            cited = row.get('citedChunkIds', [])
            source_ids = [s.get('chunk_id') for s in row['sources'] if isinstance(s, dict)]
            allowed = {c['chunkId'] for c in row.get('contexts', [])}
            if (terminal.get('outcome') != ('answered' if row['answered'] else 'refused')
                    or row.get('degraded') is not False
                    or len(source_ids) != len(row['sources']) or source_ids != cited
                    or len(set(cited)) != len(cited) or not set(cited) <= allowed
                    or (row['answered'] and (not row['answer'].strip() or not cited))
                    or (not row['answered'] and (row['answer'] != NO_ANSWER_TEXT or cited))):
                fail('RESULT_TRACE_OR_REFUSAL_INVALID')
    return {result_key(r): r for r in records}


def export_blind(report, output, rubric=None):
    """公共包使用白名单投影；seed、执行顺序、分数和 arm 仅留私有映射。"""
    rubric = rubric or load_rubric()
    lookup = validate_results(report, rubric)
    study = _study(report['studyId'])
    output = Path(output).resolve()
    from ai_service.orchestration.rag_rerank_contracts import ROOT
    if not output.is_relative_to((ROOT / 'tmp').resolve()) or output.exists():
        fail('BLIND_OUTPUT_MUST_BE_NEW_UNDER_TMP')
    output.mkdir(parents=True)
    seed = secrets.randbits(128)
    keys = list(lookup)
    random.Random(seed).shuffle(keys)
    entries = {e['id']: e for e in rubric['entries']}
    cards, mapping, template = [], {}, []
    for key in keys:
        row = lookup[key]
        entry = entries[row['id']]
        blind_id = secrets.token_hex(12)
        while blind_id in mapping:
            blind_id = secrets.token_hex(12)
        mapping[blind_id] = {'id': row['id'], 'arm': row['arm']}
        facts = [{k: f[k] for k in ('factId', 'statement', 'equivalentExpressions',
                  'applicableConditions', 'basisType', 'evidence')} for f in entry['requiredFacts']]
        for projected, original in zip(facts, entry['requiredFacts'], strict=True):
            for key in ('contractNotes', 'protocolReferences'):
                if key in original:
                    projected[key] = original[key]
        contexts = [{k: c[k] for k in ('chunkId', 'documentId', 'documentVersion', 'locator', 'content')}
                    for c in row.get('contexts', [])]
        card = {'blindId': blind_id, 'question': entry['question'],
                'expectedAnswerable': entry['expectedAnswerable'], 'requiredFacts': facts,
                'optionalFacts': entry['optionalFacts'], 'forbiddenClaims': entry['forbiddenClaims'],
                'contexts': contexts, 'status': row['status'],
                'failure': None if row['status'] == 'ok' else 'request_failed_or_not_run',
                'answered': row.get('answered'), 'answer': row.get('answer'),
                'sources': row.get('sources', []), 'citedChunkIds': row.get('citedChunkIds', [])}
        cards.append(card)
        template.append({'blindId': blind_id, 'status': 'pending',
            'facts': [{'factId': f['factId'], 'verdict': 'pending', 'answerQuote': '', 'notes': ''} for f in facts],
            'claims': [], 'claimsComplete': None, 'citations': [], 'answerRelevance': None,
            'factualError': None, 'safetyViolation': None, 'annotationDispute': None,
            'failureCategories': [], 'notes': ''})
    public = {'schemaVersion': 'rag-blind-package/v1', 'studyId': study.study_id,
        'rubricStatus': 'agent-source-reviewed-human-pending', 'humanGold': False,
        'instructions': ['全部卡片均需保留；故障不能算正确拒答。',
            '逐事实、逐断言与逐引用核对，等价表达及精确引文不等于人工 gold。',
            '公共必要事实判 met 时逐字摘录答案；协议事实无需空答案 quote。',
            'supported claim 的 quote 必须来自实际 contexts，不能用旁列公共全文代替。',
            'claimsComplete 是评审人对覆盖全部可核验断言的声明，软件无法证明语义穷尽。',
            'NO_ANSWER_TEXT 是正常拒答；系统可靠性 fallback 和未运行是故障。'], 'cards': cards}
    digest = fingerprint(public)
    write_json(output / 'cards.json', public)
    write_json(output / 'private-mapping.json', {'schemaVersion': 'rag-blind-private/v1',
        'studyId': study.study_id, 'publicPackageSha256': digest, 'reportSha256': fingerprint(report),
        'shuffleSeed': seed, 'mapping': mapping})
    write_json(output / 'reviews-template.json', {'schemaVersion': 'rag-blind-reviews/v1',
        'studyId': study.study_id, 'publicPackageSha256': digest,
        'reviewer': {'name': '请填写实际评审人', 'role': 'human'}, 'reviews': template})
    lines = ['# 匿名答案审阅包', '', '全部 180 份按匿名随机顺序展示，评审结果填写 reviews-template.json。',
             '私有映射不得交给盲审人；人工未填时保持 pending。', '']
    import json
    for card in cards:
        lines += [f"## {card['blindId']}", '', card['question'], '', '```json',
                  json.dumps(card, ensure_ascii=False, indent=2), '```', '']
    (output / 'cards.md').write_text('\n'.join(lines), encoding='utf8')
    return public


def validate_reviews(raw, public):
    """只验证可追踪性和结构；人类填写 role 仍是身份声明，不是身份认证。"""
    reviews = Reviews.model_validate(raw)
    study = _study(public.get('studyId'))
    if reviews.studyId != study.study_id:
        fail('REVIEW_STUDY_INVALID')
    if (not reviews.reviewer.name.strip() or
            (any(r.status == 'complete' for r in reviews.reviews)
             and reviews.reviewer.name == '请填写实际评审人')):
        fail('REVIEWER_REQUIRED')
    cards = {c['blindId']: c for c in public['cards']}
    if (reviews.publicPackageSha256 != fingerprint(public)
            or len(cards) != 180 or len(reviews.reviews) != 180
            or len({r.blindId for r in reviews.reviews}) != 180
            or {r.blindId for r in reviews.reviews} != set(cards)):
        fail('REVIEW_IDS_INVALID')
    for review in reviews.reviews:
        card = cards[review.blindId]
        facts = {f['factId']: f for f in card['requiredFacts']}
        if (len(review.facts) != len(facts)
                or len({f.factId for f in review.facts}) != len(facts)
                or {f.factId for f in review.facts} != set(facts)
                or len(set(review.failureCategories)) != len(review.failureCategories)):
            fail('REVIEW_FACT_IDS_INVALID')
        if review.status == 'pending':
            if (any(f.verdict != 'pending' or f.answerQuote for f in review.facts)
                    or review.claims or review.citations or review.failureCategories
                    or any(getattr(review, key) is not None for key in
                           ('claimsComplete', 'answerRelevance', 'factualError', 'safetyViolation', 'annotationDispute'))):
                fail('PENDING_REVIEW_HAS_SCORES')
            continue
        if (any(f.verdict == 'pending' for f in review.facts)
                or any(getattr(review, key) is None for key in
                       ('claimsComplete', 'answerRelevance', 'factualError', 'safetyViolation', 'annotationDispute'))
                or not review.claimsComplete
                or (review.annotationDispute != ('annotation_dispute' in review.failureCategories))):
            fail('REVIEW_INCOMPLETE')
        answer = card['answer'] or ''
        for fact in review.facts:
            protocol = facts[fact.factId]['basisType'] == 'evaluation-protocol'
            if protocol:
                if fact.answerQuote:
                    fail('PROTOCOL_QUOTE_INVALID')
                ordinal = int(fact.factId.rsplit(':', 1)[-1])
                actual = card['status'] == 'ok' and (
                    card['answered'] is False if ordinal == 1 else
                    not card['sources'] and not card['citedChunkIds'] if ordinal == 2 else
                    review.safetyViolation is False)
                if (fact.verdict == 'met') != actual:
                    fail('PROTOCOL_VERDICT_INVALID')
            elif fact.verdict == 'met':
                if not fact.answerQuote or fact.answerQuote not in answer:
                    fail('FACT_ANSWER_QUOTE_INVALID')
            elif fact.answerQuote and fact.answerQuote not in answer:
                fail('FACT_ANSWER_QUOTE_INVALID')
        contexts = {c['chunkId']: c['content'] for c in card['contexts']}
        if (len({c.text for c in review.claims}) != len(review.claims)
                or (card['answered'] is True and not review.claims)
                or (card['answered'] is not True and review.claims)):
            fail('CLAIMS_INVALID')
        for claim in review.claims:
            if (claim.text not in answer or claim.supported != bool(claim.evidence)
                    or len({(s.chunkId, s.quote) for s in claim.evidence}) != len(claim.evidence)):
                fail('CLAIM_EVIDENCE_INVALID')
            for support in claim.evidence:
                if support.quote not in contexts.get(support.chunkId, ''):
                    fail('CLAIM_EVIDENCE_INVALID')
        cited = card['citedChunkIds']
        if (len(review.citations) != len(cited)
                or {c.chunkId for c in review.citations} != set(cited)
                or len({c.chunkId for c in review.citations}) != len(cited)):
            fail('CITATION_REVIEWS_INVALID')
        claims = {c.text: c for c in review.claims}
        for citation in review.citations:
            if (len(set(citation.claimTexts)) != len(citation.claimTexts)
                    or not set(citation.claimTexts) <= set(claims)
                    or citation.supported != bool(citation.claimTexts)):
                fail('CITATION_SUPPORT_INVALID')
            if any(not any(s.chunkId == citation.chunkId for s in claims[t].evidence)
                   for t in citation.claimTexts):
                fail('CITATION_SUPPORT_INVALID')
    return reviews


def paired_statistics(off, on, *, seed=20261118, draws=10000, study_id=STUDY_ID):
    """以题为重采样单位，同一题的两组始终一起抽取。"""
    first_only, second_only, delta_key = _study(study_id).paired_keys
    if len(off) != len(on) or not off or any(type(v) is not bool for v in off + on):
        fail('PAIRED_INPUT_INVALID')
    table = Counter(('bothPass' if a and b else first_only if a else second_only if b else 'bothFail')
                    for a, b in zip(off, on, strict=True))
    deltas = [int(b) - int(a) for a, b in zip(off, on, strict=True)]
    rng = random.Random(seed)
    samples = sorted(sum(rng.choices(deltas, k=len(deltas))) / len(deltas) for _ in range(draws))
    return {'questionPairs': len(off), **{k: table[k] for k in ('bothPass', first_only, second_only, 'bothFail')},
            delta_key: sum(deltas) / len(deltas), 'bootstrap95':
            [samples[math.floor(.025 * (draws - 1))], samples[math.ceil(.975 * (draws - 1))]],
            'bootstrapSeed': seed, 'bootstrapDraws': draws}


def _passed(card, review):
    return (card['status'] == 'ok' and review.status == 'complete'
            and not review.annotationDispute and not review.factualError and not review.safetyViolation
            and review.answerRelevance == 2 and all(f.verdict == 'met' for f in review.facts)
            and all(c.supported for c in review.claims) and all(c.supported for c in review.citations)
            and card['answered'] == card['expectedAnswerable'])


def _latencies(values):
    values = sorted(v for v in values if type(v) in {int, float} and math.isfinite(v))
    return {'count': len(values), 'mean': fmean(values) if values else None,
            'p95': values[max(0, math.ceil(.95 * len(values)) - 1)] if values else None}


def _tokens(usages):
    usages = [u for u in usages if isinstance(u, dict)]
    return {key: sum(u[key] for u in usages if type(u.get(key)) is int)
            if any(type(u.get(key)) is int for u in usages) else None
            for key in ('input_tokens', 'output_tokens', 'total_tokens')}


def summarize(report, public, private, reviews, rubric=None):
    rubric = rubric or load_rubric()
    lookup = validate_results(report, rubric)
    study = _study(report['studyId'])
    # 摘要允许重新计算，因此研究身份必须独立于内容摘要逐层核对。
    if (public.get('studyId') != study.study_id or private.get('studyId') != study.study_id
            or private.get('reportSha256') != fingerprint(report)
            or private.get('publicPackageSha256') != fingerprint(public)
            or set(private['mapping']) != {c['blindId'] for c in public['cards']}
            or {m['id'] + ':' + m['arm'] for m in private['mapping'].values()} != set(lookup)):
        fail('PRIVATE_MAPPING_INVALID')
    parsed = validate_reviews(reviews, public)
    cards = {c['blindId']: c for c in public['cards']}
    by_key = {result_key(private['mapping'][r.blindId]): (cards[r.blindId], r) for r in parsed.reviews}
    complete = all(r.status == 'complete' for r in parsed.reviews)
    result = {'schemaVersion': study.analysis_schema, 'studyId': study.study_id,
              'reviewer': parsed.reviewer.model_dump(), 'humanReview':
              'complete' if complete and parsed.reviewer.role == 'human' else 'pending',
              'qualityStatus': 'complete' if complete else 'pending',
              'generalization': '反复使用的90题开发回归集，非独立盲测泛化成绩',
              'arms': {}, 'paired': None, 'humanOverallQualityImprovement': None}
    # 配额实验只回放历史候选；旧准备耗时不能冒充本轮新发生的检索或 BGE 工作。
    retrieval = list(report.get('retrieval', {}).values()) if study.study_id == STUDY_ID else []
    result.update(callsReserved=report.get('callsReserved'), embeddingUsage=report.get('embeddingUsage', []),
        sharedRetrievalLatencyMs=_latencies(r.get('sharedRetrievalLatencyMs') for r in retrieval),
        realBgeLatencyMs=_latencies(r.get('realBgeLatencyMs') for r in retrieval),
        timingMeaning=study.timing_meaning, monetaryCost=None)
    for arm in study.arms:
        pairs = [by_key[e['id'] + ':' + arm] for e in rubric['entries']]
        done = [(c, r) for c, r in pairs if r.status == 'complete']
        public_facts = [f for c, r in done if c['expectedAnswerable'] for f in r.facts]
        claims = [claim for _, r in done for claim in r.claims]
        citations = [citation for _, r in done for citation in r.citations]
        rows = [r for r in lookup.values() if r['arm'] == arm]
        metrics = {'denominator': 90, 'answerableDenominator': 81, 'refusalDenominator': 9,
            'reviewed': len(done), 'pending': 90 - len(done),
            'errors': sum(c['status'] == 'error' for c, _ in pairs),
            'notRun': sum(c['status'] == 'not_run' for c, _ in pairs),
            'falseRefusals': sum(c['status'] == 'ok' and c['expectedAnswerable'] and not c['answered'] for c, _ in pairs),
            'correctContractRefusals': sum(c['status'] == 'ok' and not c['expectedAnswerable']
                    and not c['answered'] and not c['sources'] for c, _ in pairs),
            'requiredPublicFactsDenominator': 130,
            'reviewedPublicFactCoverage': sum(f.verdict == 'met' for f in public_facts) / len(public_facts) if public_facts else None,
            'publicFactCoverage': sum(f.verdict == 'met' for f in public_facts) / 130 if complete else None,
            'unsupportedClaims': sum(not c.supported for c in claims), 'reviewedClaims': len(claims),
            'supportedCitations': sum(c.supported for c in citations), 'reviewedCitations': len(citations),
            'reviewedSafeCorrectRefusals': sum(_passed(c, r) for c, r in done if not c['expectedAnswerable']),
            'reviewedFactualErrors': sum(bool(r.factualError) for _, r in done),
            'reviewedSafetyViolations': sum(bool(r.safetyViolation) for _, r in done),
            'failureCategories': dict(Counter(k for _, r in done for k in r.failureCategories)),
            'overallPassRate': sum(_passed(c, r) for c, r in pairs) / 90 if complete else None,
            'answerablePassRate': sum(_passed(c, r) for c, r in pairs if c['expectedAnswerable']) / 81 if complete else None,
            'refusalPassRate': sum(_passed(c, r) for c, r in pairs if not c['expectedAnswerable']) / 9 if complete else None,
            'answerUsage': [r.get('usage') for r in rows if r.get('usage') is not None],
            'answerTokens': _tokens(r.get('usage') for r in rows),
            'answerLatencyMs': _latencies(r.get('answerLatencyMs') for r in rows)}
        result['arms'][arm] = metrics
    if complete:
        outcomes = {arm: [_passed(*by_key[e['id'] + ':' + arm]) for e in rubric['entries']] for arm in study.arms}
        result['paired'] = paired_statistics(outcomes[study.arms[0]], outcomes[study.arms[1]], study_id=study.study_id)
        if parsed.reviewer.role == 'human':
            result['humanOverallQualityImprovement'] = result['paired'][study.paired_keys[2]]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--export', type=Path)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--reviews', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = read_json(args.report)
    if args.export and not (args.package or args.reviews or args.output):
        public = export_blind(report, args.export)
        print({'blindCards': len(public['cards']), 'humanReview': 'pending'})
    elif args.package and args.reviews and args.output and not args.export:
        from ai_service.orchestration.rag_rerank_contracts import ROOT
        output = args.output.resolve()
        if not output.is_relative_to((ROOT / 'tmp').resolve()) or output.exists():
            fail('ANALYSIS_OUTPUT_MUST_BE_NEW_UNDER_TMP')
        analysis = summarize(report, read_json(args.package / 'cards.json'),
            read_json(args.package / 'private-mapping.json'), read_json(args.reviews))
        write_json(output, analysis)
        print({'humanReview': analysis['humanReview'], 'qualityStatus': analysis['qualityStatus']})
    else:
        parser.error('使用 --export，或同时使用 --package --reviews --output')


if __name__ == '__main__':
    main()
