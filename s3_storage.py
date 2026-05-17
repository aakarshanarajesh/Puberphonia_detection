import json
import logging
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError, NoCredentialsError


logger = logging.getLogger(__name__)


class S3Storage:
    """Small wrapper around S3 for uploaded audio and analysis outputs."""

    def __init__(self) -> None:
        self.bucket = self._env("AWS_S3_BUCKET")
        self.region = self._env("AWS_REGION", "eu-north-1")
        self.prefix = self._env("AWS_S3_PREFIX", "puberphonia").strip("/")
        self.enabled = self._env("S3_ENABLED", "false").lower() in {"1", "true", "yes"}
        self.access_key_id = self._env("AWS_ACCESS_KEY_ID")
        self.secret_access_key = self._env("AWS_SECRET_ACCESS_KEY")

        if self.enabled and not self.bucket:
            raise RuntimeError("AWS_S3_BUCKET is required when S3_ENABLED=true")

        client_kwargs = {"region_name": self.region}
        if self.access_key_id and self.secret_access_key:
            client_kwargs["aws_access_key_id"] = self.access_key_id
            client_kwargs["aws_secret_access_key"] = self.secret_access_key

        self.client = boto3.client("s3", **client_kwargs) if self.enabled else None
        logger.info(
            "S3 storage %s | bucket=%s | region=%s | prefix=%s",
            "enabled" if self.enabled else "disabled",
            self.bucket or "-",
            self.region,
            self.prefix,
        )

    def is_enabled(self) -> bool:
        return bool(self.enabled and self.client and self.bucket)

    def build_key(self, folder: str, filename: str, patient_id: str = "anonymous") -> str:
        safe_patient = self._safe_part(patient_id)
        safe_name = self._safe_filename(filename)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        unique = uuid4().hex[:10]
        return f"{self.prefix}/{safe_patient}/{folder}/{stamp}-{unique}-{safe_name}"

    def upload_bytes(
        self,
        data: bytes,
        key: str,
        content_type: Optional[str] = None,
        metadata: Optional[dict[str, str]] = None,
    ) -> str:
        if not self.is_enabled():
            logger.info("S3 upload skipped because S3_ENABLED is false")
            return ""

        guessed_type = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        extra_args: dict[str, Any] = {
            "ContentType": guessed_type,
            "ServerSideEncryption": "AES256",
        }
        if metadata:
            extra_args["Metadata"] = {str(k): str(v) for k, v in metadata.items()}

        try:
            logger.info("Uploading to S3: s3://%s/%s", self.bucket, key)
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                **extra_args,
            )
            logger.info("Upload success: s3://%s/%s", self.bucket, key)
        except NoCredentialsError as exc:
            logger.exception("S3 ERROR: AWS credentials are not configured")
            raise RuntimeError("AWS credentials are not configured") from exc
        except ClientError as exc:
            logger.exception("S3 ERROR: upload failed")
            raise RuntimeError(f"S3 upload failed: {exc.response['Error'].get('Message', exc)}") from exc

        return f"s3://{self.bucket}/{key}"

    def upload_json(self, payload: dict[str, Any], key: str) -> str:
        data = json.dumps(payload, indent=2, default=str).encode("utf-8")
        return self.upload_bytes(data, key, content_type="application/json")

    def upload_file(self, local_path: str | Path, key: str, content_type: Optional[str] = None) -> str:
        path = Path(local_path)
        return self.upload_bytes(path.read_bytes(), key, content_type=content_type)

    def create_presigned_url(self, key: str, expires_in: int = 900) -> str:
        if not self.is_enabled():
            return ""

        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except ClientError as exc:
            raise RuntimeError(f"S3 presigned URL failed: {exc.response['Error'].get('Message', exc)}") from exc

    @staticmethod
    def _safe_part(value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
        return cleaned or "anonymous"

    @staticmethod
    def _safe_filename(value: str) -> str:
        name = Path(value or "recording.wav").name
        cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in name)
        return cleaned or "recording.wav"

    @staticmethod
    def _env(name: str, default: str = "") -> str:
        return os.getenv(name, default).strip()
