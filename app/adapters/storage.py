from datetime import timedelta
from io import BytesIO

from minio import Minio
from minio.error import S3Error
from urllib3 import PoolManager, Timeout

from app.errors import AppError


class ObjectStore:
    def __init__(self, settings):
        self.bucket = settings.minio_bucket
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key.get_secret_value(),
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=settings.minio_secure,
            http_client=PoolManager(timeout=Timeout(connect=3, read=15), retries=1),
        )
        self.public_client = self.client
        if settings.minio_public_endpoint:
            self.public_client = Minio(
                settings.minio_public_endpoint,
                access_key=settings.minio_access_key.get_secret_value(),
                secret_key=settings.minio_secret_key.get_secret_value(),
                secure=settings.minio_secure,
                region="us-east-1",
            )

    def ensure_bucket(self):
        try:
            if not self.client.bucket_exists(self.bucket):
                try:
                    self.client.make_bucket(self.bucket)
                except S3Error as exc:
                    if exc.code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                        raise
        except Exception as exc:
            raise AppError("STORAGE_UNAVAILABLE", "简历对象存储暂不可用", 503) from exc

    def put(self, key: str, data: bytes, content_type: str):
        self.ensure_bucket()
        try:
            self.client.put_object(self.bucket, key, BytesIO(data), len(data), content_type=content_type)
        except Exception as exc:
            raise AppError("STORAGE_UNAVAILABLE", "简历文件保存失败", 503) from exc

    def get(self, key: str, max_bytes: int = 10 * 1024 * 1024) -> bytes:
        response = None
        try:
            response = self.client.get_object(self.bucket, key)
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise AppError("FILE_TOO_LARGE", "存储中的文件超过允许大小", 413)
            return data
        except AppError:
            raise
        except Exception as exc:
            raise AppError("STORAGE_UNAVAILABLE", "无法读取简历原文件", 503) from exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def remove(self, key: str):
        try:
            self.client.remove_object(self.bucket, key)
        except Exception as exc:
            raise AppError("STORAGE_UNAVAILABLE", "文件补偿清理失败", 503) from exc

    def download_url(self, key: str):
        try:
            return self.public_client.presigned_get_object(self.bucket, key, expires=timedelta(minutes=3))
        except Exception as exc:
            raise AppError("STORAGE_UNAVAILABLE", "无法生成下载链接", 503) from exc
