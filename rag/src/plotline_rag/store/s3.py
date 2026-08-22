"""S3 source-of-truth adapter with ETag-aware local caching."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from plotline_rag.config import AWS_PROFILE, AWS_REGION, CACHE_DIR, S3_BUCKET, S3_KEYS


logger = logging.getLogger(__name__)


class S3Store:
    def __init__(
        self,
        bucket: str = S3_BUCKET,
        cache_dir: Path = CACHE_DIR,
        session: boto3.Session | None = None,
    ) -> None:
        if not bucket.startswith("plotline-"):
            raise ValueError("S3 bucket must stay inside the plotline-* permission fence")
        self.bucket = bucket
        self.cache_dir = Path(cache_dir)
        self.session = session or boto3.Session(
            profile_name=AWS_PROFILE,
            region_name=AWS_REGION,
        )
        self.client = self.session.client("s3", region_name=AWS_REGION)

    def cache_path(self, key: str) -> Path:
        return self.cache_dir / key

    def _etag_path(self, key: str) -> Path:
        path = self.cache_path(key)
        return path.with_name(f"{path.name}.etag")

    def head(self, key: str) -> dict[str, Any]:
        return self.client.head_object(Bucket=self.bucket, Key=key)

    def put_file(self, local_path: Path, key: str) -> dict[str, Any]:
        digest = hashlib.sha256(Path(local_path).read_bytes()).hexdigest()
        self.client.upload_file(
            str(local_path),
            self.bucket,
            key,
            ExtraArgs={"Metadata": {"sha256": digest}},
        )
        return self.head(key)

    def read_json(self, key: str) -> dict[str, Any] | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return json.loads(response["Body"].read().decode("utf-8"))

    def download_cached(self, key: str, offline: bool = False) -> Path:
        target = self.cache_path(key)
        etag_path = self._etag_path(key)
        if offline:
            if not target.is_file():
                raise FileNotFoundError(f"offline cache miss for s3://{self.bucket}/{key}")
            return target

        try:
            remote_etag = self.head(key)["ETag"].strip('"')
            local_etag = etag_path.read_text(encoding="utf-8").strip() if etag_path.exists() else ""
            if target.is_file() and local_etag == remote_etag:
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f"{target.name}.download")
            self.client.download_file(self.bucket, key, str(temporary))
            temporary.replace(target)
            etag_path.write_text(f"{remote_etag}\n", encoding="utf-8")
            return target
        except (BotoCoreError, ClientError, OSError) as exc:
            if target.is_file():
                logger.warning(
                    "S3 refresh failed for %s; using the verified local cache (%s)",
                    key,
                    type(exc).__name__,
                )
                return target
            raise

    def materialize_bundle(self, offline: bool = False) -> dict[str, Path]:
        paths = {name: self.download_cached(key, offline=offline) for name, key in S3_KEYS.items()}
        return paths

    def bundle_is_current(self, local_manifest: dict[str, Any]) -> bool:
        remote = self.read_json(S3_KEYS["manifest"])
        if not remote:
            return False
        if remote.get("jsonl_hash") != local_manifest.get("jsonl_hash"):
            return False
        if remote.get("artifact_hashes") != local_manifest.get("artifact_hashes"):
            return False
        try:
            for key in S3_KEYS.values():
                self.head(key)
        except ClientError:
            return False
        return True

    def mark_synced(self) -> None:
        marker = self.cache_dir / "aws-live"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"s3://{self.bucket}\n", encoding="utf-8")
