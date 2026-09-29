from __future__ import annotations

from functools import lru_cache

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从 `AI_SERVICE_` 环境变量和可选 `.env` 文件加载服务配置。"""
    model_config = SettingsConfigDict(
        env_prefix="AI_SERVICE_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
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

    @model_validator(mode="after")
    def validate_checkpoint_pool(self) -> "Settings":
        """拒绝无法创建的 PostgreSQL 连接池边界。"""
        if self.checkpoint_pool_max_size < self.checkpoint_pool_min_size:
            raise ValueError(
                "checkpoint_pool_max_size must be greater than or equal to "
                "checkpoint_pool_min_size"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """返回进程内缓存的配置对象，避免重复解析环境变量。"""
    return Settings()  # type: ignore[call-arg]

