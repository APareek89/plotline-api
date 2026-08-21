"""HTTP client for the plotline-rag frozen contract (§7 build handoff).

plotline-api must NOT reimplement retrieval, embeddings, or KB ingestion —
it consumes the RAG service over HTTP:

    POST /search_corpus      {query, k, filters} -> Evidence-shaped results
                             with source_id, tier, score
    POST /resolve_source_ids {source_ids: [...]} -> {resolved: {id: bool}}

Until Codex's plotline-rag lands, devrag/ serves the same contract from
sample fixtures on the same port.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from app import config


class RagUnavailable(RuntimeError):
    pass


class RagClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 10.0):
        self.base_url = (base_url or config.RAG_BASE_URL).rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = httpx.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise RagUnavailable(f"RAG service unreachable at {self.base_url}{path}: {exc}") from exc

    def search_corpus(
        self,
        query: str,
        k: int = 8,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        data = self._post("/search_corpus", {"query": query, "k": k, "filters": filters or {}})
        return data.get("results", [])

    def resolve_source_ids(self, source_ids: list[str]) -> dict[str, bool]:
        if not source_ids:
            return {}
        data = self._post("/resolve_source_ids", {"source_ids": source_ids})
        return data.get("resolved", {})

    def health(self) -> dict[str, Any]:
        try:
            resp = httpx.get(f"{self.base_url}/health", timeout=3.0)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise RagUnavailable(str(exc)) from exc


rag = RagClient()
