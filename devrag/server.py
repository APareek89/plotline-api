"""DEV STUB for the plotline-rag frozen HTTP contract (§7 build handoff).

Honest label: this is SAMPLE DATA served through the real contract shape —
POST /search_corpus and POST /resolve_source_ids on 127.0.0.1:8787. It does
no embeddings and no real retrieval (that is Codex's plotline-rag scope);
it keyword-filters ~45 hand-curated fixture records so that plotline-api's
wiring — retrieval calls, evidence handoff, citation resolution — is real
and the swap to the production service is a no-op.

Run:  uvicorn devrag.server:app --port 8787
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

FIXTURES = Path(__file__).parent / "fixtures"

STAT_MIN_N = 30  # never publish a benchmark with n < 30 (§10)


def _load() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for name in ("assets.json", "chunks.json", "stats.json", "trends.json"):
        records.extend(json.loads((FIXTURES / name).read_text()))
    return records


RECORDS = _load()
BY_ID = {r["source_id"]: r for r in RECORDS}

app = FastAPI(title="plotline-rag DEV STUB (sample data)", version="0.1.0")


class SearchRequest(BaseModel):
    query: str = ""
    k: int = Field(default=8, ge=1, le=50)
    filters: dict[str, Any] = Field(default_factory=dict)


class ResolveRequest(BaseModel):
    source_ids: list[str]


def _searchable_text(rec: dict[str, Any]) -> str:
    parts = [
        str(rec.get(key, ""))
        for key in ("title", "text", "description", "why_it_works", "hook_text", "keyword", "topic", "niche", "format", "metric")
    ]
    return " ".join(parts).lower()


def _passes_filters(rec: dict[str, Any], filters: dict[str, Any]) -> bool:
    for key, wanted in filters.items():
        if wanted in (None, "", [], "all"):
            continue
        have = rec.get(key)
        if have is None:
            return False
        wanted_list = wanted if isinstance(wanted, list) else [wanted]
        if str(have) == "all":
            continue
        if str(have) not in [str(w) for w in wanted_list]:
            return False
    return True


def _score(rec: dict[str, Any], query: str) -> float:
    if not query:
        return 0.5
    text = _searchable_text(rec)
    terms = [t for t in query.lower().split() if len(t) > 2]
    if not terms:
        return 0.5
    hits = sum(1 for t in terms if t in text)
    return round(hits / len(terms), 4)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "service": "plotline-rag",
        "mode": "DEV_STUB_SAMPLE_DATA",
        "note": "sample data — pipeline pending; contract-compatible with plotline-rag",
        "records": len(RECORDS),
    }


@app.post("/search_corpus")
def search_corpus(req: SearchRequest) -> dict[str, Any]:
    candidates = []
    for rec in RECORDS:
        if rec["kind"] == "stat" and rec.get("n", 0) < STAT_MIN_N:
            continue  # never surface a small-sample benchmark
        if not _passes_filters(rec, req.filters):
            continue
        score = _score(rec, req.query)
        candidates.append((score, rec))
    candidates.sort(key=lambda pair: (-pair[0], pair[1]["source_id"]))
    results = []
    for score, rec in candidates[: req.k]:
        out = dict(rec)
        out["score"] = score
        out["sample_data"] = True
        results.append(out)
    return {"results": results, "sample_data": True}


@app.post("/resolve_source_ids")
def resolve_source_ids(req: ResolveRequest) -> dict[str, Any]:
    resolved: dict[str, bool] = {}
    for source_id in req.source_ids:
        rec = BY_ID.get(source_id)
        if rec is None:
            resolved[source_id] = False
        elif rec["kind"] == "stat" and rec.get("n", 0) < STAT_MIN_N:
            resolved[source_id] = False  # unpublishable benchmark = dead citation
        else:
            resolved[source_id] = True
    return {"resolved": resolved}
