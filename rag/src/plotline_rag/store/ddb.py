"""Provisioned-capacity DynamoDB adapter with deliberately throttled writes."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Iterable
from typing import Any

import boto3
from boto3.dynamodb.types import TypeSerializer

from plotline_rag.config import AWS_PROFILE, AWS_REGION, DDB_TABLE
from plotline_rag.schema import KBChunk


class DynamoChunkStore:
    def __init__(
        self,
        table_name: str = DDB_TABLE,
        session: boto3.Session | None = None,
        write_capacity: float = 1.0,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not table_name.startswith("plotline_"):
            raise ValueError("DynamoDB table must stay inside the plotline_* permission fence")
        self.table_name = table_name
        self.session = session or boto3.Session(
            profile_name=AWS_PROFILE,
            region_name=AWS_REGION,
        )
        self.resource = self.session.resource("dynamodb", region_name=AWS_REGION)
        self.table = self.resource.Table(table_name)
        # Use an unmodified low-level client for explicitly serialized batch writes.
        # The resource's client adds its own serializer and would double-encode items.
        self.client = self.session.client("dynamodb", region_name=AWS_REGION)
        self.write_capacity = write_capacity
        self.sleep_fn = sleep_fn
        self.serializer = TypeSerializer()

    @staticmethod
    def chunk_item(chunk: KBChunk) -> dict[str, Any]:
        return {
            "source_id_full": chunk.source_id_full,
            "type": "chunk",
            "text": chunk.text,
            "tier": chunk.source_tier,
            "claim_type": chunk.claim_type,
            "url": chunk.source_url,
            "hash": chunk.hash,
            "title": chunk.title,
        }

    @staticmethod
    def _write_units(item: dict[str, Any]) -> int:
        encoded = json.dumps(item, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return max(1, math.ceil(len(encoded) / 1024))

    def get(self, source_id: str, *, consistent: bool = True) -> dict[str, Any] | None:
        response = self.table.get_item(
            Key={"source_id_full": source_id},
            ConsistentRead=consistent,
        )
        return response.get("Item")

    def exists(self, source_id: str) -> bool:
        return self.get(source_id) is not None

    def exists_many(self, source_ids: Iterable[str]) -> set[str]:
        return {source_id for source_id in source_ids if self.exists(source_id)}

    def validate_free_tier_fence(self, project_capacity_cap: int = 10) -> dict[str, int]:
        names: list[str] = []
        paginator = self.client.get_paginator("list_tables")
        for page in paginator.paginate():
            names.extend(name for name in page.get("TableNames", []) if name.startswith("plotline_"))

        total_rcu = 0
        total_wcu = 0
        target: dict[str, Any] | None = None
        for name in names:
            description = self.client.describe_table(TableName=name)["Table"]
            billing_mode = description.get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED")
            if billing_mode != "PROVISIONED":
                raise RuntimeError(f"{name} uses forbidden billing mode {billing_mode}")
            throughput = description["ProvisionedThroughput"]
            total_rcu += int(throughput["ReadCapacityUnits"])
            total_wcu += int(throughput["WriteCapacityUnits"])
            if name == self.table_name:
                target = description

        if target is None:
            raise RuntimeError(f"required DynamoDB table does not exist: {self.table_name}")
        if target.get("KeySchema") != [{"AttributeName": "source_id_full", "KeyType": "HASH"}]:
            raise RuntimeError(f"{self.table_name} has an unexpected key schema")
        target_throughput = target["ProvisionedThroughput"]
        if (
            int(target_throughput["ReadCapacityUnits"]) != 1
            or int(target_throughput["WriteCapacityUnits"]) != 1
        ):
            raise RuntimeError(f"{self.table_name} must remain provisioned at exactly 1 RCU / 1 WCU")
        if total_rcu > project_capacity_cap or total_wcu > project_capacity_cap:
            raise RuntimeError(
                f"Plotline provisioned capacity {total_rcu} RCU / {total_wcu} WCU exceeds "
                f"the {project_capacity_cap}/{project_capacity_cap} project cap"
            )
        return {"rcu": total_rcu, "wcu": total_wcu}

    def _batch_put_one(self, item: dict[str, Any]) -> None:
        typed = {key: self.serializer.serialize(value) for key, value in item.items()}
        request = {self.table_name: [{"PutRequest": {"Item": typed}}]}
        unprocessed_attempts = 0
        while request.get(self.table_name):
            response = self.client.batch_write_item(RequestItems=request)
            request = response.get("UnprocessedItems", {})
            if request.get(self.table_name):
                unprocessed_attempts += 1
                if unprocessed_attempts >= 8:
                    raise RuntimeError("DynamoDB left an item unprocessed after 8 throttled retries")
                self.sleep_fn(1.0)

    def sync_chunks(self, chunks: list[KBChunk], progress: bool = True) -> dict[str, int]:
        written = 0
        skipped = 0
        total = len(chunks)
        checkpoint = max(1, total // 10)
        for position, chunk in enumerate(chunks, start=1):
            item = self.chunk_item(chunk)
            existing = self.get(chunk.source_id_full, consistent=False)
            if existing and existing.get("hash") == chunk.hash:
                skipped += 1
            else:
                started = time.monotonic()
                self._batch_put_one(item)
                written += 1
                required_delay = self._write_units(item) / self.write_capacity
                elapsed = time.monotonic() - started
                self.sleep_fn(max(0.0, required_delay - elapsed))
            if progress and (position == total or position % checkpoint == 0):
                print(f"DynamoDB sync {position}/{total}: written={written} skipped={skipped}", flush=True)
        return {"written": written, "skipped": skipped, "total": total}
