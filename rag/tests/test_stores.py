from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import boto3
from botocore.exceptions import EndpointConnectionError
from moto import mock_aws

from plotline_rag.index.build import read_chunks
from plotline_rag.store.ddb import DynamoChunkStore
from plotline_rag.store.s3 import S3Store


def test_s3_round_trip_and_offline_cache(corpus_path: Path, tmp_path: Path) -> None:
    with mock_aws():
        session = boto3.Session(
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
            region_name="ap-south-1",
        )
        client = session.client("s3")
        client.create_bucket(
            Bucket="plotline-test",
            CreateBucketConfiguration={"LocationConstraint": "ap-south-1"},
        )
        store = S3Store(bucket="plotline-test", cache_dir=tmp_path / "cache", session=session)
        store.put_file(corpus_path, "kb/best_practice.jsonl")
        cached = store.download_cached("kb/best_practice.jsonl")
        assert cached.read_bytes() == corpus_path.read_bytes()

        with patch.object(
            store,
            "head",
            side_effect=EndpointConnectionError(endpoint_url="https://offline.invalid"),
        ):
            assert store.download_cached("kb/best_practice.jsonl") == cached
        assert store.download_cached("kb/best_practice.jsonl", offline=True) == cached


def test_dynamodb_sync_is_idempotent_and_resolves_ids(corpus_path: Path) -> None:
    with mock_aws():
        session = boto3.Session(
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
            region_name="ap-south-1",
        )
        client = session.client("dynamodb")
        client.create_table(
            TableName="plotline_chunks",
            AttributeDefinitions=[{"AttributeName": "source_id_full", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "source_id_full", "KeyType": "HASH"}],
            ProvisionedThroughput={"ReadCapacityUnits": 1, "WriteCapacityUnits": 1},
        )
        store = DynamoChunkStore(
            session=session,
            write_capacity=10_000,
            sleep_fn=lambda _seconds: None,
        )
        chunks = read_chunks(corpus_path)
        assert store.validate_free_tier_fence() == {"rcu": 1, "wcu": 1}
        first = store.sync_chunks(chunks, progress=False)
        second = store.sync_chunks(chunks, progress=False)

        assert first == {"written": 3, "skipped": 0, "total": 3}
        assert second == {"written": 0, "skipped": 3, "total": 3}
        assert store.exists_many(["chunk:C0001", "chunk:C9999"]) == {"chunk:C0001"}
        assert store.get("chunk:C0001")["type"] == "chunk"


def test_store_names_cannot_escape_permission_fences(tmp_path: Path) -> None:
    with mock_aws():
        session = boto3.Session(
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
            region_name="ap-south-1",
        )
        try:
            S3Store(bucket="other-project", cache_dir=tmp_path, session=session)
        except ValueError as exc:
            assert "plotline-*" in str(exc)
        else:
            raise AssertionError("non-Plotline bucket was accepted")

        try:
            DynamoChunkStore(table_name="other_project", session=session)
        except ValueError as exc:
            assert "plotline_*" in str(exc)
        else:
            raise AssertionError("non-Plotline table was accepted")
