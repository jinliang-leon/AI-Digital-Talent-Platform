"""环境配置；凭据不参与对象 repr 与接口序列化。"""

from functools import lru_cache
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    app_name: str = "AI数字化人才平台"
    app_env: str = "development"
    project_namespace: str = "ai_talent_v1"
    database_url: SecretStr = Field(repr=False)
    database_host_override: str | None = None
    jwt_secret: SecretStr = Field(repr=False)
    redis_url: SecretStr = Field(default=SecretStr("redis://127.0.0.1:6379/9"), repr=False)
    minio_endpoint: str = "127.0.0.1:9000"
    minio_public_endpoint: str | None = None
    minio_access_key: SecretStr = Field(repr=False)
    minio_secret_key: SecretStr = Field(repr=False)
    minio_bucket: str = "ai-talent-resumes"
    minio_secure: bool = False
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = Field(repr=False)
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: SecretStr = Field(default=SecretStr(""), repr=False)
    llm_base_url: str = "http://127.0.0.1:20128/v1"
    llm_model: str = "xingchen/spark-x2.6"
    llm_api_key: SecretStr = Field(repr=False)
    llm_timeout_seconds: float = Field(default=90, gt=0, le=120)
    llm_thinking_mode: str = "disabled"
    embedding_base_url: str = "http://127.0.0.1:11434"
    embedding_model: str = "bge-m3:latest"
    embedding_dimension: int = 1024
    max_upload_bytes: int = 10 * 1024 * 1024
    task_timeout: int = 600
    worker_stale_seconds: int = 720

    @field_validator("jwt_secret")
    @classmethod
    def strong_secret(cls, v):
        if len(v.get_secret_value()) < 32 or "请" in v.get_secret_value():
            raise ValueError("请在本地 .env 设置至少 32 字符的随机 JWT_SECRET")
        return v

    @field_validator("database_url")
    @classmethod
    def mysql_only(cls, v):
        if not v.get_secret_value().startswith("mysql+pymysql://"):
            raise ValueError("必须使用本地 MySQL（mysql+pymysql）")
        return v

    @field_validator("project_namespace")
    @classmethod
    def safe_namespace(cls, v):
        if not v.replace("_", "").isalnum() or len(v) > 40 or not v.isascii():
            raise ValueError("项目命名空间只能包含不超过40个 ASCII 字母、数字和下划线")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
