"""请求隔离的权威读取；上下文退出只重置本任务的 ContextVar。"""
from contextlib import asynccontextmanager
from contextvars import ContextVar

from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError


class AuthoritativeKnowledgeStore:
    def __init__(self, settings, legacy, publications, client):
        self.settings, self.legacy = settings, legacy
        self.publications, self.client = publications, client
        self._snapshot = ContextVar('knowledge_read_snapshot', default=None)

    @asynccontextmanager
    async def read_scope(self):
        snapshot = await self.client.snapshot()
        if self.settings.rag_publication_enabled:
            await self.publications.validate_publication(snapshot)
        elif snapshot.enabled:
            raise MilvusKnowledgeError('KNOWLEDGE_PUBLICATION_REQUIRED')
        token = self._snapshot.set(snapshot)
        try:
            yield self
        finally:
            self._snapshot.reset(token)

    @property
    def empty(self):
        snapshot = self._snapshot.get()
        return snapshot is not None and snapshot.enabled and not snapshot.documents

    async def assert_current(self):
        snapshot = self._snapshot.get()
        if snapshot is None or await self.client.snapshot() != snapshot:
            raise MilvusKnowledgeError('KNOWLEDGE_PUBLICATION_CHANGED')

    async def search(self, vector, limit):
        snapshot = self._snapshot.get()
        if snapshot is None:
            raise MilvusKnowledgeError('KNOWLEDGE_PUBLICATION_UNAVAILABLE')
        if snapshot.enabled:
            return await self.publications.search_publication(vector, limit, snapshot)
        return await self.legacy.search(vector, limit)


class LegacyAnswerServiceGuard:
    """显式兼容注入的旧回答服务；永不允许其进入 publication 模式。"""
    def __init__(self, service, settings, client):
        from ai_service.orchestration.customer_service_rag import CustomerServiceRagService
        # 官方编排必须使用内部模型前 hook，不能再套旧兼容器绕过此要求。
        if isinstance(service, CustomerServiceRagService):
            raise MilvusKnowledgeError('KNOWLEDGE_PUBLICATION_UNAVAILABLE')
        self.service, self.settings, self.client = service, settings, client

    def health_ready(self):
        return callable(getattr(self.service, 'answer', None))

    async def answer(self, question, **kwargs):
        before = await self.client.require_legacy(publication_enabled=self.settings.rag_publication_enabled)
        result = await self.service.answer(question, **kwargs)
        if await self.client.snapshot() != before:
            raise MilvusKnowledgeError('KNOWLEDGE_PUBLICATION_CHANGED')
        return result
