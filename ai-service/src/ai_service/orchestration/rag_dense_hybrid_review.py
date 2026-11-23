"""第32轮匿名审阅材料和题级配对统计；不调用模型，不自动做语义评分。"""
from __future__ import annotations

import argparse
from collections import Counter
import math
from pathlib import Path
import random
import secrets
from statistics import fmean

from ai_service.orchestration.rag_dense_hybrid_answer_experiment import RUN, NEW, ARMS
from ai_service.orchestration.rag_hybrid_append_answer_experiment import durable_json
from ai_service.orchestration.rag_hybrid_experiment import RUBRIC
from ai_service.orchestration.rag_qa_linked_corpus import read_json
from ai_service.orchestration.rag_qa_linked_budget import fingerprint


def basis():
    old = read_json(RUBRIC)['entries']
    new = read_json(NEW)['entries']
    entries = {}
    for e in old:
        entries[e['id']] = dict(question=e['question'], expectedAnswerable=e['expectedAnswerable'],
            facts=[dict(id=f['factId'], statement=f['statement'], evidence=f['evidence'],
                equivalentExpressions=f['equivalentExpressions'],
                applicableConditions=f['applicableConditions'], basisType=f['basisType']) for f in e['requiredFacts']])
    for e in new:
        entries[e['id']] = dict(question=e['question'], expectedAnswerable=e['expectedAnswerable'],
            facts=[dict(id=f'fact-{i+1}', statement=quote, equivalentExpressions=[],
                applicableConditions=['限题目所问公共产品行为'], basisType='public-document',
                evidence=[dict(documentId=doc, quote=quote)])
                for i, (doc, quote) in enumerate(e['facts'])])
    return entries


def export(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    plan, report = read_json(RUN / 'answer-plan.json'), read_json(RUN / 'answer-report.json')
    entries = basis()
    slots = {s['key']: s for s in plan['slots']}
    if len(report['results']) != len(slots) or len(slots) != 228:
        raise ValueError('REVIEW_INPUT_INVALID')
    cards, mapping, unavailable = [], {}, []
    for result in report['results']:
        slot = slots[result['key']]
        if result['status'] != 'completed':
            unavailable.append(dict(key=slot['key'], status=result['status']))
            continue
        blind = secrets.token_hex(12)
        mapping[blind] = {k:slot[k] for k in ('id','key','arm','cohort')}
        cards.append(dict(blindId=blind, **entries[slot['id']], answer=result['answer'],
            contexts=[{k:c[k] for k in ('chunkId','content')} for c in slot['contexts']]))
    random.SystemRandom().shuffle(cards)
    package = dict(schemaVersion='rag-dense-hybrid-blind/v1', cards=cards,
        boundary='代理知晓研究设计，臂标签隐藏；非独立真人双盲。事实来自预注册代理标签，非human gold。')
    template = dict(packageSha256=fingerprint(package), reviewer=dict(name='', role='human'),
        reviews=[dict(blindId=c['blindId'], status='pending', facts=[], claims=[],
            claimsComplete=False, erroneousAssertion=None, refusalCorrect=None, notes='') for c in cards])
    durable_json(output / 'cards.json', package, exclusive=True)
    durable_json(output / 'human-review-template.json', template, exclusive=True)
    durable_json(output / 'private-mapping.json', dict(mapping=mapping, unavailable=unavailable,
        planSha256=fingerprint(plan), reportSha256=fingerprint(report)), exclusive=True)
    return len(cards)


def validate(package, reviews):
    """摘录只校验审阅可追溯性，不能把字符串校验充当语义正确性证明。"""
    if (reviews.get('packageSha256') != fingerprint(package)
            or reviews.get('reviewer',{}).get('role') not in ('agent','human')
            or not reviews['reviewer'].get('name')):
        raise ValueError('REVIEW_IDENTITY_INVALID')
    cards = {c['blindId']:c for c in package['cards']}
    rows = reviews['reviews']
    if len(rows) != len(cards) or {r['blindId'] for r in rows} != set(cards):
        raise ValueError('REVIEW_COVERAGE_INVALID')
    for r in rows:
        c = cards[r['blindId']]
        answer = c['answer']['answer']
        if (r['status'] != 'complete' or r['claimsComplete'] is not True
                or type(r['erroneousAssertion']) is not bool or type(r['refusalCorrect']) is not bool
                or not isinstance(r['notes'], str)):
            raise ValueError('REVIEW_PENDING')
        facts = {f['id']:f for f in c['facts']}
        if len(r['facts']) != len(facts) or {f['id'] for f in r['facts']} != set(facts):
            raise ValueError('REVIEW_FACTS_INVALID')
        for f in r['facts']:
            if f['verdict'] not in ('met','missing','contradicted'):
                raise ValueError('REVIEW_FACT_VERDICT_INVALID')
            if f['verdict'] != 'missing' and (not f['answerQuote'] or f['answerQuote'] not in answer):
                raise ValueError('REVIEW_ANSWER_QUOTE_INVALID')
        contexts = {v['chunkId']:v['content'] for v in c['contexts']}
        cited = set(c['answer']['cited_chunk_ids'])
        if c['answer']['answered'] and not r['claims']:
            raise ValueError('REVIEW_CLAIMS_MISSING')
        for claim in r['claims']:
            if (not claim['text'] or claim['text'] not in answer
                    or type(claim['contextSupported']) is not bool
                    or type(claim['citationSupported']) is not bool
                    or (claim['citationSupported'] and not claim['contextSupported'])):
                raise ValueError('REVIEW_CLAIM_INVALID')
            evidence = claim['evidence']
            if any(not v['quote'] or v['quote'] not in contexts.get(v['chunkId'],'') for v in evidence):
                raise ValueError('REVIEW_EVIDENCE_INVALID')
            if claim['contextSupported'] and not evidence:
                raise ValueError('REVIEW_EVIDENCE_MISSING')
            if claim['citationSupported'] and not any(v['chunkId'] in cited for v in evidence):
                raise ValueError('REVIEW_CITATION_INVALID')
    return {r['blindId']:r for r in rows}


def paired(values):
    """在问题上重采样；事实不会被当作独立样本放大有效样本量。"""
    if not values:
        return dict(n=0, delta=None, ci95=None)
    rng = random.Random(32023)
    draws = sorted(fmean(rng.choices(values, k=len(values))) for _ in range(10000))
    positive, negative = sum(v>0 for v in values), sum(v<0 for v in values)
    n = positive+negative
    p = min(1., 2*sum(math.comb(n,k) for k in range(min(positive,negative)+1))/2**n) if n else 1.
    return dict(n=len(values), delta=fmean(values), ci95=[draws[249], draws[9749]],
                hybridHigher=positive, denseHigher=negative, exactSignP=p)


def analyze(package, reviews, mapping):
    rows = validate(package, reviews)
    identities = mapping['mapping']
    if set(rows) != set(identities):
        raise ValueError('REVIEW_MAPPING_INVALID')
    groups = {}
    for c in package['cards']:
        identity, r = identities[c['blindId']], rows[c['blindId']]
        facts, claims = r['facts'], r['claims']
        metrics = dict(complete=float(all(f['verdict']=='met' for f in facts)),
            factCoverage=sum(f['verdict']=='met' for f in facts)/len(facts) if facts else 1.,
            contextSupported=float(all(v['contextSupported'] for v in claims)),
            citationSupported=float(all(v['citationSupported'] for v in claims)),
            erroneousAssertion=float(r['erroneousAssertion']), refusalCorrect=float(r['refusalCorrect']),
            refused=float(not c['answer']['answered']))
        groups.setdefault((identity['cohort'],identity['id']), {})[identity['arm']] = (metrics,c['expectedAnswerable'])
    output = {}
    for cohort in ('development','new_preregistered'):
        cohort_pairs = [g for (co,_),g in groups.items() if co==cohort and set(g)==set(ARMS)]
        planned = 90 if cohort == 'development' else 24
        item = dict(plannedQuestions=planned, pairedQuestions=len(cohort_pairs),
            incompletePairs=planned-len(cohort_pairs),
            semanticMetricsDenominator='下列语义指标仅完整配对；执行失败/未运行另列，不推断其断言或拒答。')
        for name in ('complete','factCoverage','contextSupported','citationSupported','erroneousAssertion','refusalCorrect','refused'):
            # 完整性只用于可回答题；拒答正确性和错误断言保留全部配对题。
            selected = [g for g in cohort_pairs if g['dense'][1] or name not in ('complete','factCoverage','contextSupported','citationSupported')]
            item[name] = dict(dense=fmean(g['dense'][0][name] for g in selected) if selected else None,
                hybrid=fmean(g['hybrid'][0][name] for g in selected) if selected else None,
                paired=paired([g['hybrid'][0][name]-g['dense'][0][name] for g in selected]))
        output[cohort] = item
    return dict(schemaVersion='rag-dense-hybrid-analysis/v1', reviewer=reviews['reviewer'],
        packageSha256=fingerprint(package), reviewSha256=fingerprint(reviews), cohorts=output,
        unavailable=Counter(r['status'] for r in mapping['unavailable']), humanReview='pending',
        boundary='单次样本、问题级配对；七维探索性比较，无多重校正，不作为确定提升证明。')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=('export','analyze'))
    p.add_argument('directory', type=Path)
    p.add_argument('--reviews', type=Path)
    a = p.parse_args()
    if a.phase == 'export':
        print(export(a.directory))
    else:
        result = analyze(read_json(a.directory/'cards.json'), read_json(a.reviews),
            read_json(a.directory/'private-mapping.json'))
        durable_json(a.directory/'analysis.json',result,exclusive=True)


if __name__ == '__main__':
    main()
