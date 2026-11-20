"""独立问答关联语料实验；仅转换本地源材料，不接入线上检索或模型。"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import tempfile

from ai_service.config import Settings
from ai_service.orchestration.document_etl import parse_document, split_sections

ROOT = Path(__file__).resolve().parents[4]
PRODUCT = ROOT / 'doc/knowledge/product'
OUTPUT_ROOT = ROOT / 'tmp'
VERSION = 'qa-linked-20261120-v1'


def fail(code):
    raise ValueError('QA_LINK_' + code)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail('DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    if path.stat().st_size > 8 * 1024 * 1024:
        fail('INPUT_TOO_LARGE')
    return json.loads(path.read_text(encoding='utf8'), object_pairs_hook=unique,
                      parse_constant=lambda _: fail('NONFINITE_JSON'))


def fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected.split()):
        fail('SCHEMA')


def source_path(root, relative):
    if not isinstance(relative, str) or not re.fullmatch(r'documents/[A-Za-z0-9_-]+\.md', relative):
        fail('SOURCE_PATH')
    path = root / relative
    if any(p.is_symlink() or p.is_junction() for p in (root, root/'documents', path)):
        fail('SOURCE_PATH')
    if not path.resolve().is_relative_to(root.resolve() / 'documents'):
        fail('SOURCE_PATH')
    return path


def transform(text, links):
    """只接受本轮有限H1/H2语法，未知结构拒绝，避免静默丢正文。"""
    if re.search(r'^#{3,}|^\s*(```|~~~)', text, re.M):
        fail('MARKDOWN_STRUCTURE')
    parts = re.split(r'^## (.+)\n', text.replace('\r\n', '\n'), flags=re.M)
    if not re.fullmatch(r'# [^\n]+\n\s*', parts[0]):
        fail('MARKDOWN_STRUCTURE')
    headings = parts[1::2]
    bodies = dict(zip(headings, parts[2::2], strict=True))
    if not headings or len(set(headings)) != len(headings):
        fail('DUPLICATE_HEADING')
    if '常见问法' not in bodies:
        if links:
            fail('UNEXPECTED_LINKS')
        return text, '', [], []
    if headings[-1] != '常见问法':
        fail('QUESTION_SECTION_POSITION')
    raw = bodies['常见问法'].strip()
    match = re.fullmatch(r'((?:“[^”\n]+”\s*)+)。?(.*)', raw, re.S)
    if not match:
        fail('QUESTION_SYNTAX')
    questions = re.findall('“([^”]+)”', match[1])
    if len(set(questions)) != len(questions):
        fail('DUPLICATE_QUESTION')
    navigation = match[2].strip()
    if navigation and not re.fullmatch(r'(?:相关说明：)?(?:[\u4e00-\u9fff/]{1,20}(?:详见|见)\s*\d{2})(?:；\s*[\u4e00-\u9fff/]{1,20}(?:详见|见)\s*\d{2})*。?', navigation):
        fail('NAVIGATION_NOT_REFERENCE')
    aliases = {h: [] for h in headings if h != '常见问法'}
    seen, unresolved, associations = [], [], []
    for link in links:
        fields(link, 'question targets status reason')
        question, targets = link['question'], link['targets']
        if (not isinstance(question, str) or not isinstance(targets, list)
                or not isinstance(link['reason'], str) or not link['reason'].strip()
                or any(not isinstance(t, str) for t in targets)
                or len(targets) > 3 or len(targets) != len(set(targets))
                or any(t not in aliases for t in targets)
                or link['status'] != ('linked' if targets else 'unresolved')):
            fail('LINK_INVALID')
        seen.append(question)
        if not targets:
            unresolved.append(question)
        for target in targets:
            aliases[target].append(question)
            associations.append(dict(question=question, target=target))
    if Counter(seen) != Counter(questions):
        fail('QUESTION_COVERAGE')
    output = parts[0]
    for heading in headings:
        if heading == '常见问法':
            if unresolved:
                output += '## 常见问法\n\n' + ''.join(f'“{q}”' for q in unresolved) + '。\n'
            continue
        output += f'## {heading}\n{bodies[heading]}'
        if aliases[heading]:
            output = output.rstrip() + '\n\n常见问法：' + ''.join(f'“{q}”' for q in aliases[heading]) + '\n\n'
    return output.rstrip()+'\n', navigation, unresolved, associations


def build(root, mapping, *, chunk_size=1000, overlap=150):
    root = Path(root)
    fields(mapping, 'schemaVersion candidateVersion sourceManifestSha256 reviewerType humanReview documents')
    if (mapping['schemaVersion'] != 'rag-qa-links/v1' or mapping['candidateVersion'] != VERSION
            or mapping['reviewerType'] != 'agent' or mapping['humanReview'] != 'pending'
            or sha(root/'manifest.json') != mapping['sourceManifestSha256']):
        fail('BINDING')
    source = read_json(root/'manifest.json')
    rows = source['documents']
    ids = [r['id'] for r in rows]
    if len(set(ids)) != len(ids) or not isinstance(mapping['documents'], list):
        fail('DOCUMENT_IDENTITIES')
    for row in mapping['documents']:
        fields(row, 'documentId sourceSha256 links')
        if not isinstance(row['links'], list):
            fail('LINKS_TYPE')
    if Counter(r['documentId'] for r in mapping['documents']) != Counter(ids):
        fail('DOCUMENT_IDENTITIES')
    rules = {r['documentId']: r for r in mapping['documents']}
    settings = Settings(_env_file=None, rag_chunk_size=chunk_size, rag_chunk_overlap=overlap)
    documents = []
    for row in rows:
        path = source_path(root, row['path'])
        rule = rules[row['id']]
        if path.stat().st_size > 100_000 or sha(path) != rule['sourceSha256']:
            fail('SOURCE_HASH')
        raw = path.read_bytes().decode('utf8')
        text, navigation, unresolved, associations = transform(raw, rule['links'])
        before = parse_document(path, 'md')
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory)/path.name
            generated.write_bytes(text.encode('utf8'))
            after = parse_document(generated, 'md')
        chunks = {}
        for name, sections, version in [('before', before, 1), ('after', after, 2)]:
            chunks[name] = [asdict(c) for c in split_sections(sections, document_id=row['id'],
                document_version=version, source_name=path.name, settings=settings)]
        by_locator = {s.source_locator: s for s in after}
        for section in before:
            if section.source_locator.split(' / ')[-1] == '常见问法':
                continue
            new = by_locator.get(section.source_locator)
            if new is None or section.content not in new.content:
                fail('SOURCE_CONTENT_LOST')
            # 限定本轮小语料：完整答案不能跨块，不能把只含问法的块算关联成功。
            if not any(c['source_locator'] == section.source_locator and section.content in c['content']
                       for c in chunks['after']):
                fail('COLOCATION')
        checked = []
        for association in associations:
            matching = [s for s in before if s.source_locator.split(' / ')[-1] == association['target']]
            if len(matching) != 1:
                fail('TARGET_AMBIGUOUS')
            original = matching[0]
            found = [c for c in chunks['after'] if c['source_locator'] == original.source_locator
                     and original.content in c['content'] and f'“{association["question"]}”' in c['content']]
            if len(found) != 1:
                fail('COLOCATION')
            checked.append(association | dict(chunkId=found[0]['chunk_id'], humanReview='pending'))
        if sha(path) != rule['sourceSha256']:
            fail('INPUT_CHANGED')
        documents.append(dict(documentId=row['id'], sourceMetadata=row, sourceSha256=rule['sourceSha256'],
            text=text, candidateSha256=hashlib.sha256(text.encode('utf8')).hexdigest(),
            navigationText=navigation, unresolvedQuestions=unresolved, associations=checked,
            chunks=chunks))
    return dict(schemaVersion='rag-qa-candidate/v1', candidateVersion=VERSION, documents=documents,
        summary=dict(documents=len(documents), questions=sum(len(r['links']) for r in rules.values()),
            linkedQuestions=sum(bool(l['targets']) for r in rules.values() for l in r['links']),
            unresolvedQuestions=sum(len(d['unresolvedQuestions']) for d in documents),
            associationPairs=sum(len(d['associations']) for d in documents),
            beforeChunks=sum(len(d['chunks']['before']) for d in documents),
            afterChunks=sum(len(d['chunks']['after']) for d in documents),
            chunkSize=chunk_size, overlap=overlap, liveMetrics=None, humanReview='pending',
            newProviderCalls=0, productionWrites=0))


def observe_facts(result, rubric):
    """只测整库字面可用性，不借用旧排序，也不将问题文字计为答案证据。"""
    docs = {d['documentId']: d for d in result['documents']}
    cases = []
    for entry in rubric['entries']:
        facts = []
        for fact in entry['requiredFacts']:
            if fact['basisType'] != 'public-document':
                continue
            available = {arm: any(ref['quote'] in c['content']
                and any(original['source_locator'] == c['source_locator']
                        and original['source_locator'].split(' / ')[-1] != '常见问法'
                        and ref['quote'] in original['content']
                        for original in docs.get(ref['documentId'], {}).get('chunks', {}).get('before', []))
                for ref in fact['evidence'] for c in docs.get(ref['documentId'], {}).get('chunks', {}).get(arm, []))
                for arm in ['before', 'after']}
            facts.append(dict(factId=fact['factId'], **available))
        cases.append(dict(id=entry['id'], facts=facts))
    return dict(cases=cases, publicFacts=sum(len(c['facts']) for c in cases),
        beforeAvailable=sum(f['before'] for c in cases for f in c['facts']),
        afterAvailable=sum(f['after'] for c in cases for f in c['facts']),
        boundary='全库标注原文字面可用性，非Top8召回、证据选择、语义正确率或回答质量。')


def write_json(path, value):
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')


def run(root, rules, output, rubric_path=None):
    output = Path(output)
    if (output.exists() or not output.resolve().is_relative_to(OUTPUT_ROOT.resolve())
            or output.resolve() == OUTPUT_ROOT.resolve()
            or any(p.is_symlink() or p.is_junction() for p in (output, *output.parents))):
        fail('OUTPUT_PATH')
    inputs = [Path(root)/'manifest.json', Path(rules), Path(__file__),
              *[source_path(Path(root), r['path']) for r in read_json(Path(root)/'manifest.json')['documents']]]
    if rubric_path:
        inputs.append(Path(rubric_path))
    protected = {str(p.resolve()): sha(p) for p in inputs}
    result = build(root, read_json(Path(rules)))
    if rubric_path:
        result['factAvailability'] = observe_facts(result, read_json(Path(rubric_path)))
    if any(sha(Path(p)) != digest for p, digest in protected.items()):
        fail('INPUT_CHANGED')
    output.mkdir(parents=True, exist_ok=False)
    (output/'documents').mkdir()
    for document in result['documents']:
        path = output / document['sourceMetadata']['path']
        with path.open('xb') as stream:
            stream.write(document['text'].encode('utf8'))
    manifest = dict(schemaVersion='rag-qa-candidate-manifest/v1', candidateVersion=VERSION,
        identityScope='offline-logical-identities-not-spring-versions', humanReview='pending',
        sourceManifestSha256=sha(Path(root)/'manifest.json'), associationsSha256=sha(Path(rules)),
        documents=[{k:v for k,v in d.items() if k not in {'text', 'chunks'}} for d in result['documents']])
    write_json(output/'manifest.json', manifest)
    write_json(output/'report.json', result)
    write_json(output/'reviews-template.json', dict(schemaVersion='rag-qa-link-review/v1', reviewer=None,
        status='pending', manifestSha256=sha(output/'manifest.json'),
        links=[dict(documentId=d['documentId'], **a, verdict=None, reason='')
               for d in result['documents'] for a in d['associations']],
        unresolved=[dict(documentId=d['documentId'], question=q, verdict=None, reason='')
                    for d in result['documents'] for q in d['unresolvedQuestions']]))
    if any(sha(Path(p)) != digest for p, digest in protected.items()):
        fail('INPUT_CHANGED')
    write_json(output/'input-integrity.json', dict(unchanged=True, sha256=protected))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(PRODUCT, PRODUCT/f'experiments/{VERSION}/associations.json', args.output,
                     PRODUCT/'evaluation/rubric-20261118-v1.json')
        print(json.dumps(result['summary'], ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(2, 'QA_LINK_INPUT_OR_OUTPUT_INVALID\n')


if __name__ == '__main__':
    main()
