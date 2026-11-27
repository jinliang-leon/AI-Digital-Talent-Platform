import argparse
from pathlib import Path
from secrets import token_urlsafe

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["local", "compose"], default="compose")
    args = parser.parse_args()
    pg_password = token_urlsafe(24)
    entries = {
        "TALENT_PROFILE": args.profile,
        "TALENT_DATABASE_URL": f"postgresql+psycopg://talent:{pg_password}@127.0.0.1:15432/talent",
        "TALENT_POSTGRES_PASSWORD": pg_password,
        "TALENT_REDIS_URL": "redis://127.0.0.1:16379/0",
        "TALENT_MINIO_ENDPOINT": "127.0.0.1:19000",
        "TALENT_MINIO_ACCESS_KEY": "talent-local",
        "TALENT_MINIO_SECRET_KEY": token_urlsafe(24),
        "TALENT_MINIO_BUCKET": "talent-resumes",
        "TALENT_JWT_SECRET": token_urlsafe(48),
        "TALENT_CONTACT_KEY": Fernet.generate_key().decode(),
        "TALENT_DEMO_PASSWORD": token_urlsafe(16),
    }
    destination = ROOT / ".env"
    try:
        with destination.open("x", encoding="utf-8") as configuration:
            configuration.write("".join(f"{key}={value}\n" for key, value in entries.items()))
    except FileExistsError:
        parser.exit(1, ".env already exists. Existing secrets were preserved.\n")
    print(f"Created .env for {args.profile}. Demo password is TALENT_DEMO_PASSWORD.")


if __name__ == "__main__":
    main()
