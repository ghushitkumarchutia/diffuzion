import hashlib
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings
from app.core.errors import DomainError


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    sha256: str


def new_key() -> str:
    return uuid4().hex


def validate_key(key: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        raise DomainError(422, "invalid_storage_key", "Storage requires an opaque server key")


def chunks(source: BinaryIO, max_bytes: int) -> Iterator[bytes]:
    total = 0
    if not 0 < max_bytes <= 2 * 1024**2:
        raise ValueError("Storage limit must be between one byte and two MiB")
    while data := source.read(min(65536, max_bytes - total + 1)):
        total += len(data)
        if total > max_bytes:
            raise DomainError(413, "input_too_large", "Stored file exceeds its byte limit")
        yield data


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.local_storage_path.absolute()
        self.client = None
        if settings.storage_backend == "s3":
            assert settings.aws_access_key_id and settings.aws_secret_access_key
            self.client = boto3.client(
                "s3",
                region_name=settings.bucket_region,
                aws_access_key_id=settings.aws_access_key_id.get_secret_value(),
                aws_secret_access_key=settings.aws_secret_access_key.get_secret_value(),
            )
        else:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.root.is_symlink() or self.root.resolve() != self.root:
                raise ValueError("Local storage must use a canonical private directory")
            self.root.chmod(0o700)

    def put(self, key: str, source: BinaryIO, *, max_bytes: int) -> StoredObject:
        validate_key(key)
        digest = hashlib.sha256()
        size = 0
        try:
            with tempfile.TemporaryFile() as staged:
                for data in chunks(source, max_bytes):
                    digest.update(data)
                    size += len(data)
                    staged.write(data)
                staged.seek(0)
                if self.client is not None:
                    self.client.put_object(
                        Bucket=self.settings.bucket_name,
                        Key=key,
                        Body=staged,
                        ContentLength=size,
                        ContentType="application/octet-stream",
                        IfNoneMatch="*",
                    )
                else:
                    descriptor = os.open(
                        self.root / key,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                    )
                    try:
                        with os.fdopen(descriptor, "wb") as destination:
                            for data in chunks(staged, max_bytes):
                                destination.write(data)
                    except BaseException:
                        (self.root / key).unlink(missing_ok=True)
                        raise
        except FileExistsError:
            raise DomainError(
                409, "storage_key_exists", "Stored files cannot be overwritten"
            ) from None
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code in {"PreconditionFailed", "ConditionalRequestConflict", "412", "409"}:
                raise DomainError(
                    409, "storage_key_exists", "Stored files cannot be overwritten"
                ) from None
            raise DomainError(503, "storage_failed", "Private storage is unavailable") from None
        except (OSError, BotoCoreError):
            raise DomainError(503, "storage_failed", "Private storage is unavailable") from None
        return StoredObject(key, size, digest.hexdigest())

    def get(self, key: str, destination: BinaryIO, *, max_bytes: int) -> int:
        validate_key(key)
        try:
            if self.client is not None:
                response = self.client.get_object(Bucket=self.settings.bucket_name, Key=key)
                source = response["Body"]
            else:
                source = os.fdopen(os.open(self.root / key, os.O_RDONLY | os.O_NOFOLLOW), "rb")
            try:
                size = 0
                for data in chunks(source, max_bytes):
                    destination.write(data)
                    size += len(data)
                return size
            finally:
                source.close()
        except FileNotFoundError:
            raise DomainError(404, "storage_not_found", "Stored file was not found") from None
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                raise DomainError(404, "storage_not_found", "Stored file was not found") from None
            raise DomainError(503, "storage_failed", "Private storage is unavailable") from None
        except (OSError, BotoCoreError):
            raise DomainError(503, "storage_failed", "Private storage is unavailable") from None

    def delete(self, key: str) -> None:
        validate_key(key)
        try:
            if self.client is not None:
                self.client.delete_object(Bucket=self.settings.bucket_name, Key=key)
            else:
                (self.root / key).unlink(missing_ok=True)
        except (OSError, BotoCoreError, ClientError):
            raise DomainError(503, "storage_failed", "Private storage is unavailable") from None
