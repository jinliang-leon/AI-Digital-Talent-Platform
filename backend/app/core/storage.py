from io import BytesIO
from pathlib import Path
from uuid import uuid4


class LocalStorage:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def check(self):
        return self.directory.is_dir()

    def put(self, key, contents, media_type):
        temporary = self.directory / (key + "." + uuid4().hex + ".tmp")
        try:
            temporary.write_bytes(contents)
            temporary.replace(self.directory / key)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, key):
        return (self.directory / key).read_bytes()

    def remove(self, key):
        (self.directory / key).unlink(missing_ok=True)


class MinioStorage:
    def __init__(self, settings):
        from minio import Minio
        from urllib3 import PoolManager, Timeout

        self.bucket = settings.minio_bucket
        self.client = Minio(settings.minio_endpoint, access_key=settings.minio_access_key,
                            secret_key=settings.minio_secret_key, secure=settings.minio_secure,
                            http_client=PoolManager(timeout=Timeout(connect=3, read=15), retries=False))

    def check(self):
        return self.client.bucket_exists(self.bucket)

    def ensure_bucket(self):
        from time import sleep
        from urllib3.exceptions import HTTPError

        for attempt in range(12):
            try:
                if not self.check():
                    self.client.make_bucket(self.bucket)
                return
            except (HTTPError, OSError):
                if attempt == 11:
                    raise
                sleep(2)

    def put(self, key, contents, media_type):
        self.client.put_object(self.bucket, key, BytesIO(contents), len(contents), content_type=media_type)

    def read(self, key):
        response = self.client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def remove(self, key):
        self.client.remove_object(self.bucket, key)
