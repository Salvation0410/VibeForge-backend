"""Spring 权威发布协议；不接受旧版零代际或隐式类型转换。"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from ai_service.api.schemas import (
    ApiModel, KnowledgeDeleteRequest, KnowledgeEtlRequest,
    KnowledgeMutationLeaseRequest, KnowledgeRebuildDocument, KnowledgeRebuildRequest,
)


class PublicationModel(ApiModel):
    model_config = ConfigDict(extra="forbid", strict=True)


PublicationId = Annotated[str, Field(pattern=r"^(task_[1-9][0-9]*|bootstrap_[0-9a-f]{32})$")]
CollectionName = Annotated[str, Field(max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DocumentIdentity(PublicationModel):
    document_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    document_version: int = Field(ge=1)
    index_revision: int = Field(ge=1)
    activation_revision: int = Field(ge=1)
    sha256: Sha256
    file_name: str = Field(min_length=1, max_length=255)
    file_type: Literal["PDF", "DOCX", "MD", "TXT"]

    @model_validator(mode="after")
    def safe_name(self):
        KnowledgeEtlRequest.validate_file_name(self.file_name)
        return self


def _ordered(documents):
    ids = [item.document_id for item in documents]
    if ids != sorted(set(ids)):
        raise ValueError("documents must be strictly sorted by documentId")


class PublicationContext(PublicationModel):
    publication_id: PublicationId
    base_epoch: int = Field(ge=0)
    source_collection: CollectionName | None
    documents: list[DocumentIdentity] = Field(max_length=10_000)

    @model_validator(mode="after")
    def ordered(self):
        _ordered(self.documents)
        return self


class PublicationLease(KnowledgeMutationLeaseRequest):
    model_config = ConfigDict(extra="forbid", strict=True)


class PublicationIndex(KnowledgeEtlRequest):
    model_config = ConfigDict(extra="forbid", strict=True)
    index_revision: int = Field(ge=1)
    activation_revision: int = Field(ge=1)
    lease: PublicationLease


class PublicationDelete(KnowledgeDeleteRequest):
    model_config = ConfigDict(extra="forbid", strict=True)
    activation_revision: int = Field(ge=1)
    lease: PublicationLease


class PublicationRebuildDocument(KnowledgeRebuildDocument):
    model_config = ConfigDict(extra="forbid", strict=True)
    index_revision: int = Field(ge=1)
    activation_revision: int = Field(ge=1)


class PublicationRebuild(KnowledgeRebuildRequest):
    model_config = ConfigDict(extra="forbid", strict=True)
    documents: list[PublicationRebuildDocument] = Field(max_length=10_000)
    lease: PublicationLease


def identity(item) -> DocumentIdentity:
    return DocumentIdentity.model_validate({
        name: getattr(item, name) for name in DocumentIdentity.model_fields
    })


class PrepareAnchor(PublicationModel):
    """无凭据的首次准备身份；仅用于定位证据，不能代替当前租约。"""
    context: PublicationContext
    operation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    fence: int = Field(ge=1)


class CandidateRequest(PublicationModel):
    context: PublicationContext
    mutation: Annotated[PublicationIndex | PublicationDelete | PublicationRebuild,
                        Field(discriminator="operation")]
    prepare_anchor: PrepareAnchor | None = None

    @model_validator(mode="after")
    def consistent(self):
        documents, mutation = self.context.documents, self.mutation
        anchor = self.prepare_anchor
        if anchor is not None:
            if (not isinstance(mutation, PublicationIndex) or anchor.context != self.context
                    or not self.context.publication_id.startswith("task_")
                    or anchor.fence > mutation.lease.fence
                    or (anchor.operation_id == mutation.lease.operation_id) != (anchor.fence == mutation.lease.fence)):
                raise ValueError("prepare anchor mismatch")
        if isinstance(mutation, PublicationIndex):
            if identity(mutation) not in documents:
                raise ValueError("INDEX identity missing from desired documents")
        elif isinstance(mutation, PublicationDelete):
            if any(item.document_id == mutation.document_id for item in documents):
                raise ValueError("DELETE target remains in desired documents")
        else:
            _ordered(mutation.documents)
            if [identity(item) for item in mutation.documents] != documents:
                raise ValueError("REBUILD documents differ from context")
        return self


class ReceiptDocument(DocumentIdentity):
    chunk_count: int = Field(ge=1, le=10_000)


class CandidateReceipt(PublicationModel):
    publication_id: PublicationId
    base_epoch: int = Field(ge=0)
    operation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    fence: int = Field(ge=1)
    candidate_collection: CollectionName
    snapshot_hash: Sha256
    embedding_model_version: str = Field(min_length=1, max_length=255)
    embedding_dimension: int = Field(ge=1, le=65_536)
    documents: list[ReceiptDocument] = Field(max_length=10_000)

    @model_validator(mode="after")
    def ordered(self):
        _ordered(self.documents)
        return self


class ReadSnapshot(PublicationModel):
    enabled: bool
    epoch: int = Field(ge=0)
    publication_id: PublicationId | None
    collection_name: CollectionName | None
    snapshot_hash: Sha256 | None
    embedding_model_version: str | None
    embedding_dimension: int = Field(ge=0, le=65_536)
    documents: list[DocumentIdentity] = Field(max_length=10_000)

    @model_validator(mode="after")
    def head(self):
        _ordered(self.documents)
        fields = (self.publication_id, self.collection_name, self.snapshot_hash,
                  self.embedding_model_version)
        if self.enabled:
            if not all(fields) or self.embedding_dimension < 1:
                raise ValueError("enabled publication requires complete head")
        elif any(value is not None for value in fields) or self.documents or self.embedding_dimension:
            raise ValueError("disabled publication must have empty head")
        return self


class PublicationFact(PublicationModel):
    publication_id: PublicationId
    epoch: int = Field(ge=1)
    receipt: CandidateReceipt

    @model_validator(mode="after")
    def matches(self):
        if self.publication_id != self.receipt.publication_id or self.epoch <= self.receipt.base_epoch:
            raise ValueError("publication fact mismatch")
        return self


class ReconcileRequest(PublicationModel):
    collection_alias: CollectionName
    collection_name: CollectionName
    publication_id: PublicationId
    epoch: int = Field(ge=1)


class ReconcileResponse(PublicationModel):
    publication_id: PublicationId
    epoch: int = Field(ge=1)
    collection_name: CollectionName
    reconciled: bool


class InventoryEntry(PublicationModel):
    publication_id: PublicationId
    epoch: int = Field(ge=1)
    collection_name: CollectionName
    create_time: str = Field(pattern=r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class InventoryPage(PublicationModel):
    enabled: bool
    epoch: int = Field(ge=0)
    publication_id: PublicationId | None
    next_epoch: int = Field(ge=0)
    has_more: bool
    entries: list[InventoryEntry] = Field(max_length=100)
