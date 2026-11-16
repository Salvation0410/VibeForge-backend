"""候选绑定权威上下文；alias 只作镜像，不补偿回滚。"""
from ai_service.api.knowledge_publication_schemas import identity, ReconcileResponse
from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError


def _invalid():
    raise MilvusKnowledgeError('KNOWLEDGE_PUBLICATION_INVALID')


async def validate_candidate_request(request, client, settings):
    snapshot = await client.snapshot()
    context, mutation = request.context, request.mutation
    if context.base_epoch != snapshot.epoch:
        _invalid()
    if context.publication_id.startswith('bootstrap_'):
        # registry 和维护开关仍由 Spring 提交事务复核。
        if (snapshot.enabled or mutation.operation != 'REBUILD'
                or mutation.collection_alias != settings.milvus_collection_alias
                or mutation.lease.scope != 'collection:' + settings.milvus_collection_alias
                or not context.source_collection):
            _invalid()
        return
    if not snapshot.enabled or context.source_collection != snapshot.collection_name:
        _invalid()
    expected = {item.document_id: item for item in snapshot.documents}
    if mutation.operation == 'INDEX':
        expected[mutation.document_id] = identity(mutation)
    elif mutation.operation == 'DELETE':
        # 管理变更已先推进 epoch 并撤出白名单，不允许用候选请求代替业务禁用。
        if mutation.document_id in expected:
            _invalid()
    elif (mutation.collection_alias != settings.milvus_collection_alias
          or mutation.lease.scope != 'collection:' + settings.milvus_collection_alias):
        _invalid()
    if context.documents != [expected[key] for key in sorted(expected)]:
        _invalid()


async def reconcile_alias(request, client, publications, settings):
    response = dict(publication_id=request.publication_id, epoch=request.epoch,
                    collection_name=request.collection_name)
    def matches(snapshot):
        return (snapshot.enabled and snapshot.epoch == request.epoch
                and snapshot.publication_id == request.publication_id
                and snapshot.collection_name == request.collection_name)
    if request.collection_alias != settings.milvus_collection_alias:
        _invalid()
    before = await client.snapshot()
    if not matches(before):
        return ReconcileResponse(**response, reconciled=False)
    await publications.validate_publication(before)
    store = publications.store
    target = await store._alias_target()
    if target != request.collection_name:
        # RPC 未知或迟到不得向旧目标执行补偿写。
        await store._call('create_alias' if target is None else 'alter_alias',
                          request.collection_name, request.collection_alias)
    after = await client.snapshot()
    current = await store._alias_target()
    return ReconcileResponse(**response, reconciled=matches(after) and after == before
                              and current == request.collection_name)
