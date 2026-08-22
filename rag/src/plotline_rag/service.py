"""FastAPI service implementing Plotline's frozen retrieval contract."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException

from plotline_rag.config import CACHE_DIR, INDEX_SOURCE, OFFLINE, S3_KEYS
from plotline_rag.schema import (
    ResolveSourceIdsRequest,
    ResolveSourceIdsResponse,
    SearchRequest,
)
from plotline_rag.search.hybrid import HybridSearch
from plotline_rag.store.ddb import DynamoChunkStore
from plotline_rag.store.s3 import S3Store


logger = logging.getLogger(__name__)


class SourceResolver(Protocol):
    name: str

    def resolve(self, source_ids: list[str]) -> set[str]: ...


class LocalSourceResolver:
    name = "local-cache"

    def __init__(self, source_ids: set[str]) -> None:
        self.source_ids = source_ids

    def resolve(self, source_ids: list[str]) -> set[str]:
        return set(source_ids).intersection(self.source_ids)


class DynamoSourceResolver:
    name = "dynamodb"

    def __init__(self, store: DynamoChunkStore | None = None) -> None:
        self.store = store or DynamoChunkStore()

    def resolve(self, source_ids: list[str]) -> set[str]:
        return self.store.exists_many(source_ids)


def select_runtime_root() -> tuple[Path, str]:
    local_manifest = CACHE_DIR / S3_KEYS["manifest"]
    synced_marker = CACHE_DIR / "aws-live"
    should_use_s3 = INDEX_SOURCE == "s3" or (
        INDEX_SOURCE == "auto" and (synced_marker.exists() or not local_manifest.exists())
    )
    if should_use_s3:
        S3Store(cache_dir=CACHE_DIR).materialize_bundle(offline=OFFLINE)
        return CACHE_DIR, "s3-cache" if not OFFLINE else "offline-s3-cache"
    if not local_manifest.is_file():
        raise FileNotFoundError("no local index; run `make index` or sync from S3")
    return CACHE_DIR, "local-cache"


def create_app(
    engine: HybridSearch | None = None,
    resolver: SourceResolver | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active_engine = engine
        source = "injected"
        if active_engine is None:
            root, source = select_runtime_root()
            active_engine = HybridSearch(root=root)
        active_resolver = resolver
        if active_resolver is None:
            if source == "s3-cache" and not OFFLINE:
                active_resolver = DynamoSourceResolver()
            else:
                active_resolver = LocalSourceResolver(active_engine.source_ids)
        app.state.engine = active_engine
        app.state.resolver = active_resolver
        app.state.index_source = source
        yield

    application = FastAPI(title="plotline-rag", version="0.1.0", lifespan=lifespan)

    @application.post("/search_corpus")
    def search_corpus(request: SearchRequest) -> dict[str, Any]:
        try:
            results = application.state.engine.search(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "results": [result.model_dump() for result in results],
            "manifest": application.state.engine.public_manifest(),
        }

    @application.post("/resolve_source_ids", response_model=ResolveSourceIdsResponse)
    def resolve_source_ids(request: ResolveSourceIdsRequest) -> ResolveSourceIdsResponse:
        try:
            resolved_set = application.state.resolver.resolve(request.source_ids)
        except Exception as exc:
            logger.exception("source-id resolver failed")
            raise HTTPException(status_code=503, detail="source-id resolver unavailable") from exc
        return ResolveSourceIdsResponse(
            resolved=[source_id for source_id in request.source_ids if source_id in resolved_set],
            unresolved=[source_id for source_id in request.source_ids if source_id not in resolved_set],
        )

    @application.get("/health")
    def health() -> dict[str, Any]:
        manifest_path = application.state.engine.manifest_path
        return {
            "status": "ok",
            "manifest": application.state.engine.public_manifest(),
            "cache_age_seconds": round(max(0.0, time.time() - manifest_path.stat().st_mtime), 3),
            "index_source": application.state.index_source,
            "resolver": application.state.resolver.name,
        }

    return application


app = create_app()
