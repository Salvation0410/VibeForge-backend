from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从 `AI_SERVICE_` 环境变量和可选 `.env` 文件加载服务配置。"""
    model_config = SettingsConfigDict(
        env_prefix="AI_SERVICE_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    internal_bearer_token: str = Field(min_length=1)
    spring_gateway_base_url: HttpUrl
    spring_gateway_bearer_token: str = Field(min_length=1)

    model_api_key: str = Field(default="not-configured", min_length=1)
    model_base_url: str = "https://api.deepseek.com/v1"
    model_name: str = "deepseek-chat"
    model_temperature: float = 0.1
    model_max_tokens: int = Field(default=8192, ge=1)

    checkpoint_enabled: bool = True
    checkpoint_required: bool = False
    checkpoint_postgres_url: str = (
        "postgresql://postgres:replace-with-password@localhost:5432/yu_ai_checkpoint"
    )
    checkpoint_auto_setup: bool = True
    checkpoint_ttl_seconds: int = Field(default=86400, ge=60)
    checkpoint_pool_min_size: int = Field(default=1, ge=1, le=20)
    checkpoint_pool_max_size: int = Field(default=5, ge=1, le=50)
    vue_max_tool_calls: int = Field(default=4, ge=1, le=20)
    max_repair_attempts: int = Field(default=2, ge=0, le=5)
    multi_agent_review_enabled: bool = False
    multi_agent_review_timeout_seconds: float = Field(default=60.0, gt=0, le=300)

    customer_service_rag_enabled: bool = False
    closeai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("AI_SERVICE_CLOSEAI_API_KEY", "CLOSEAI_API_KEY"),
        repr=False,
    )
    closeai_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("AI_SERVICE_CLOSEAI_BASE_URL", "CLOSEAI_BASE_URL"),
    )
    rag_embedding_model: str = Field(default="openai:text-embedding-3-large", min_length=1)
    rag_embedding_dimension: int = Field(default=3072, ge=1, le=100_000)
    rag_embedding_batch_size: int = Field(default=32, ge=1, le=256)
    rag_max_embedding_elements: int = Field(default=8_000_000, ge=1, le=100_000_000)
    rag_etl_max_concurrency: int = Field(default=1, ge=1, le=32)
    rag_rebuild_max_documents: int = Field(default=1000, ge=1, le=10_000)
    rag_rebuild_max_chunks: int = Field(default=1_000_000, ge=1, le=1_000_000)
    rag_rebuild_max_text_bytes: int = Field(
        default=64 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024
    )

    milvus_uri: str = "http://localhost:19530"
    milvus_token: str = Field(default="", repr=False)
    milvus_database: str = Field(default="default", min_length=1)
    milvus_collection_alias: str = Field(
        default="customer_service_knowledge",
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
    )
    milvus_rpc_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    rag_chunk_size: int = Field(default=1000, ge=1, le=100000)
    rag_chunk_overlap: int = Field(default=150, ge=0, le=99999)
    rag_retrieval_top_k: int = Field(default=8, ge=1, le=100)
    rag_final_top_k: int = Field(default=3, ge=1, le=100)
    rag_min_rerank_score: float | None = Field(default=None, allow_inf_nan=False)

    rag_reranker_provider: Literal["local_cross_encoder", "remote_api", "disabled"] = "local_cross_encoder"
    rag_reranker_model: str = Field(default="BAAI/bge-reranker-v2-m3", min_length=1)
    rag_reranker_device: str = "cuda"
    rag_reranker_timeout_seconds: float = Field(default=5.0, gt=0, le=300)
    rag_reranker_batch_size: int = Field(default=4, ge=1, le=128)
    rag_reranker_workers: int = Field(default=1, ge=1, le=8)
    rag_reranker_max_concurrency: int = Field(default=1, ge=1, le=8)

    rag_oss_allowed_hosts: str = ""
    rag_download_max_bytes: int = Field(default=20971520, ge=1, le=104857600)
    rag_download_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    rag_download_read_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    @model_validator(mode="after")
    def validate_checkpoint_pool(self) -> "Settings":
        """拒绝无法创建的 PostgreSQL 连接池边界。"""
        if self.checkpoint_pool_max_size < self.checkpoint_pool_min_size:
            raise ValueError(
                "checkpoint_pool_max_size must be greater than or equal to "
                "checkpoint_pool_min_size"
            )
        return self

    @model_validator(mode="after")
    def validate_customer_service_rag(self) -> "Settings":
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("rag_chunk_overlap must be smaller than rag_chunk_size")
        if self.rag_final_top_k > self.rag_retrieval_top_k:
            raise ValueError("rag_final_top_k must not exceed rag_retrieval_top_k")
        if (
            self.rag_reranker_provider == "local_cross_encoder"
            and self.rag_reranker_device != "cuda"
        ):
            raise ValueError("rag_reranker_device must be cuda for local_cross_encoder")
        if self.customer_service_rag_enabled:
            for name in ("milvus_uri", "closeai_api_key", "closeai_base_url"):
                if not getattr(self, name).strip():
                    raise ValueError(f"{name} is required when customer_service_rag_enabled")
        return self


@lru_cache
def get_settings() -> Settings:
    """返回进程内缓存的配置对象，避免重复解析环境变量。"""
    return Settings()  # type: ignore[call-arg]

