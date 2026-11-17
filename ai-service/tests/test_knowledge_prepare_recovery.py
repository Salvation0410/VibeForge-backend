"""准备恢复只复用已完整验收向量，不放宽新租约或未知结果边界。"""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from ai_service.api.knowledge_publication_schemas import CandidateRequest
from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError
from ai_service.orchestration.knowledge_publication import KnowledgePublicationPreparer
from test_knowledge_publication import env, value, index_request, Downloader, Embeddings


def anchored(settings, document):
    body = index_request(settings, document).model_dump(by_alias=True)
    body["prepareAnchor"] = {"context": deepcopy(body["context"]), "operationId": "attempt_1", "fence": 1}
    return CandidateRequest.model_validate(body)


def retry(request):
    body = request.model_dump(by_alias=True)
    body["mutation"]["lease"].update(operationId="attempt_2", fence=2)
    return CandidateRequest.model_validate(body)


async def test_recovery_and_same_attempt_repeat_have_one_embedding(env, tmp_path):
    settings, client, knowledge, publications = env
    path = tmp_path / "manual.txt"
    path.write_text("私有知识正文", encoding="utf8")
    embeddings = Embeddings()
    request = anchored(settings, value(settings))
    preparer = KnowledgePublicationPreparer(settings, Downloader(path), embeddings, knowledge)
    first = await preparer.prepare(request)
    assert await preparer.prepare(request) == first
    # 新对象模拟进程内状态丢失，恢复只依赖持久候选和传入anchor。
    second = await KnowledgePublicationPreparer(settings, Downloader(path), embeddings, knowledge).prepare(retry(request))
    assert embeddings.calls == 1
    assert first.candidate_collection != second.candidate_collection
    assert first.operation_id != second.operation_id and second.fence == 2
    assert first.snapshot_hash == second.snapshot_hash
    _, old = await publications.completed(first.candidate_collection)
    _, new = await publications.completed(second.candidate_collection)
    assert [row["embedding"] for row in old] == [row["embedding"] for row in new]


@pytest.mark.parametrize("fault", ["missing", "incomplete", "corrupt", "model", "locator"])
async def test_unknown_or_invalid_recovery_never_embeds(env, tmp_path, fault):
    settings, client, knowledge, _ = env
    path = tmp_path / "manual.txt"
    path.write_text("私有知识正文", encoding="utf8")
    embeddings = Embeddings()
    request = anchored(settings, value(settings))
    preparer = KnowledgePublicationPreparer(settings, Downloader(path), embeddings, knowledge)
    first = await preparer.prepare(request)
    if fault == "missing":
        del client.collections[first.candidate_collection]
    elif fault == "incomplete":
        client.collections[first.candidate_collection] = [row for row in client.collections[first.candidate_collection] if row["recordType"] != "publication_completion"]
    elif fault == "corrupt":
        client.collections[first.candidate_collection][0]["embedding"][0] += 1
    elif fault == "model":
        settings.rag_embedding_model = "other-model"
    else:
        # 原字节hash不变的定位变化由测试解析替身注入，必须被规范行复核拒绝。
        from dataclasses import replace
        import ai_service.orchestration.document_etl as etl
        original = etl.parse_and_split_download
        async def changed(*args, **kwargs):
            chunks = await original(*args, **kwargs)
            return [replace(chunk, source_locator="wrong") for chunk in chunks]
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(etl, "parse_and_split_download", changed)
            with pytest.raises(MilvusKnowledgeError):
                await preparer.prepare(retry(request))
        assert embeddings.calls == 1
        return
    with pytest.raises(MilvusKnowledgeError):
        await preparer.prepare(retry(request))
    assert embeddings.calls == 1


@pytest.mark.parametrize("field", ["baseEpoch", "sourceCollection", "publicationId"])
def test_anchor_context_drift_is_rejected(settings, field):
    request = anchored(settings, value(settings)).model_dump(by_alias=True)
    request["prepareAnchor"]["context"][field] = {"baseEpoch": 1, "sourceCollection": "other", "publicationId": "task_2"}[field]
    with pytest.raises(ValidationError):
        CandidateRequest.model_validate(request)
