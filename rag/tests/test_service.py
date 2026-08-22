from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from plotline_rag.index.build import build_index
from plotline_rag.index.embed import HashingEmbedder
from plotline_rag.search.hybrid import HybridSearch
from plotline_rag.service import LocalSourceResolver, create_app


def test_frozen_service_contract(corpus_path: Path, tmp_path: Path) -> None:
    root = tmp_path / "cache"
    build_index(corpus_path, root, embedder=HashingEmbedder(dim=256))
    engine = HybridSearch(root=root, embedder=HashingEmbedder(dim=256))
    app = create_app(engine=engine, resolver=LocalSourceResolver(engine.source_ids))

    async def exercise_contract() -> None:
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                search = await client.post(
                    "/search_corpus",
                    json={
                        "query": "how long should a short be for retention",
                        "k": 2,
                        "filters": {"platform": ["youtube"], "tier": ["official"]},
                        "corpus": "best_practice",
                    },
                )
                assert search.status_code == 200
                payload = search.json()
                assert payload["results"][0]["source_id"] == "chunk:C0001"
                assert set(payload["results"][0]["components"]) == {"dense", "bm25"}
                assert payload["manifest"]["chunks"] == 3

                resolved = await client.post(
                    "/resolve_source_ids",
                    json={"source_ids": ["chunk:C0001", "stat:S999"]},
                )
                assert resolved.json() == {
                    "resolved": ["chunk:C0001"],
                    "unresolved": ["stat:S999"],
                }

                health = await client.get("/health")
                assert health.status_code == 200
                assert health.json()["resolver"] == "local-cache"

    asyncio.run(exercise_contract())
