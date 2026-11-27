import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def configuration():
    values = {}
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key.startswith("TALENT_"):
                values[key] = value.strip()
    values.update(os.environ)
    return values


@dataclass(frozen=True)
class Settings:
    profile: str
    database_url: str
    jwt_secret: str
    contact_key: str
    redis_url: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_secure: bool
    storage_path: Path
    model_url: str = ""
    model_key: str = ""
    chat_model: str = ""
    embedding_model: str = ""
    external_ai_enabled: bool = False
    langfuse_url: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    model_timeout: float = 35

    @classmethod
    def load(cls):
        values = configuration()
        def setting(name, default=""):
            return values.get(f"TALENT_{name}", default)

        profile = setting("PROFILE", "compose")
        if profile not in {"compose", "local"}:
            raise ValueError("TALENT_PROFILE must be compose or local")
        if profile == "local":
            database = setting("LOCAL_DATABASE_URL", "sqlite:///" + str(ROOT / ".runtime" / "talent.db"))
        else:
            database = setting("DATABASE_URL")
            if not database.startswith("postgresql+psycopg://"):
                raise ValueError("TALENT_DATABASE_URL must use postgresql+psycopg")
        secret = setting("JWT_SECRET")
        if len(secret) < 32:
            raise ValueError("TALENT_JWT_SECRET requires at least 32 characters")
        timeout = float(setting("MODEL_TIMEOUT_SECONDS", "35"))
        if not 5 <= timeout <= 120:
            raise ValueError("TALENT_MODEL_TIMEOUT_SECONDS must be between 5 and 120")
        return cls(profile, database, secret, setting("CONTACT_KEY"), setting("REDIS_URL"),
                   setting("MINIO_ENDPOINT"), setting("MINIO_ACCESS_KEY"), setting("MINIO_SECRET_KEY"),
                   setting("MINIO_BUCKET", "talent-resumes"), setting("MINIO_SECURE") == "true",
                   Path(setting("STORAGE_PATH", str(ROOT / ".runtime" / "files"))),
                   setting("MODEL_URL"), setting("MODEL_API_KEY"), setting("CHAT_MODEL"),
                   setting("EMBEDDING_MODEL"), setting("EXTERNAL_AI_ENABLED") == "true",
                   setting("LANGFUSE_URL"), setting("LANGFUSE_PUBLIC_KEY"), setting("LANGFUSE_SECRET_KEY"), timeout)
