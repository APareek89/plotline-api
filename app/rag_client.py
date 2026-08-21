"""HTTP clients for the plotline-rag frozen contract (§7 build handoff).

plotline-api must NOT reimplement retrieval, embeddings, or KB ingestion —
it consumes retrieval over HTTP:

    GET  /health             manifest (model, chunks, as_of), index_source,
                             resolver, cache_age_seconds
    POST /search_corpus      {query, k, filters, corpus, rerank:false}
                             -> {results: [{source_id, tier, title, text,
                                claim_type, stats, score, components}], manifest}
    POST /resolve_source_ids {source_ids: [...]}
                             -> {resolved: [...], unresolved: [...]}

Two backends serve this contract today:
  primary — Codex's plotline-rag (best_practice chunks; DynamoDB resolver;
            authoritative for chunk:* ids)
  aux     — devrag sample fixtures for the corpora plotline-rag doesn't host
            yet (asset:/stat:/trend:); optional, dev only

RoutingRag fans out by corpus kind / id prefix and fails CLOSED: if a backend
needed to validate a citation is unreachable, the agent output is rejected,
never silently accepted.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from app import config

logger = logging.getLogger("plotline.rag")

# Contract limits
_RESOLVE_BATCH_MAX = 100
_QUERY_MAX_CHARS = 4000
_RETRIES = 2  # bounded: retry connect/timeout/5xx only, never 4xx

# Filter dimensions the plotline-rag contract supports (ANDed dims, ORed values)
_CONTRACT_FILTER_DIMS = ("platform", "topic", "content_type", "tier", "claim_type")


class RagUnavailable(RuntimeError):
    """Connection failure, timeout, or 5xx after bounded retries — or a
    backend required for validation that is not configured."""


class RagDisabled(RagUnavailable):
    """PLOTLINE_RAG_ENABLED=0: evidence-requiring flows must refuse to run,
    loudly, instead of planning without retrieval."""


class RagBadRequest(RuntimeError):
    """HTTP 4xx — the request itself is wrong; retrying verbatim is banned."""


class RagClient:
    """Typed client for ONE service speaking the frozen contract."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.base_url = (base_url or config.RAG_BASE_URL).rstrip("/")
        self.timeout = config.RAG_TIMEOUT if timeout is None else timeout
        self._client = httpx.Client(
            base_url=self.base_url, timeout=self.timeout, transport=transport
        )

    # ------------------------------------------------------------- plumbing --

    def _request(self, method: str, path: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        last_exc: Optional[Exception] = None
        for attempt in range(_RETRIES + 1):
            started = time.time()
            try:
                resp = self._client.request(method, path, json=payload)
                latency_ms = round((time.time() - started) * 1000)
                if 400 <= resp.status_code < 500:
                    # 4xx is a contract violation on our side — never retried.
                    logger.warning("rag %s %s -> %s in %dms (bad request, no retry)",
                                   method, path, resp.status_code, latency_ms)
                    raise RagBadRequest(
                        f"{path} rejected with HTTP {resp.status_code}: {resp.text[:300]}"
                    )
                resp.raise_for_status()
                data = resp.json()
                manifest = data.get("manifest") or {}
                logger.info(
                    "rag %s %s -> ok in %dms results=%s as_of=%s",
                    method, path, latency_ms,
                    len(data.get("results", [])) if "results" in data else "-",
                    manifest.get("as_of", "-"),
                )
                return data
            except RagBadRequest:
                raise
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                # connect failure / timeout / 5xx — bounded retry
                last_exc = exc
                category = type(exc).__name__
                logger.warning("rag %s %s attempt %d/%d failed (%s)",
                               method, path, attempt + 1, _RETRIES + 1, category)
                if attempt < _RETRIES:
                    time.sleep(0.2 * (attempt + 1))
        raise RagUnavailable(
            f"RAG service unreachable at {self.base_url}{path} after {_RETRIES + 1} attempts: {last_exc}"
        ) from last_exc

    # ------------------------------------------------------------- contract --

    def search_corpus(
        self,
        query: str,
        k: int = 8,
        filters: Optional[dict[str, Any]] = None,
        corpus: Optional[str] = "best_practice",
    ) -> list[dict[str, Any]]:
        """corpus=None sends the legacy devrag payload (no corpus/rerank keys,
        scalar filters pass through untouched)."""
        payload: dict[str, Any] = {
            "query": query[:_QUERY_MAX_CHARS],
            "k": max(1, min(int(k), 50)),
            "filters": filters or {},
        }
        if corpus is not None:
            payload["corpus"] = corpus
            payload["rerank"] = False  # v1: reranker unsupported, True is an HTTP 400
        data = self._request("POST", "/search_corpus", payload)
        results = data.get("results", [])
        if not config.ALLOW_SEED_EVIDENCE:
            dropped = [r for r in results if r.get("tier") == "seed"]
            if dropped:
                logger.info("rag search: dropped %d tier=seed result(s) (PLOTLINE_ALLOW_SEED_EVIDENCE=0)",
                            len(dropped))
            results = [r for r in results if r.get("tier") != "seed"]
        return results

    def resolve_source_ids(self, source_ids: list[str]) -> dict[str, bool]:
        """Normalizes both wire shapes to {id: bool}:
        real contract {resolved: [...], unresolved: [...]} and the earlier
        devrag shape {resolved: {id: bool}}."""
        ids = sorted(set(source_ids))
        if not ids:
            return {}
        out: dict[str, bool] = {}
        for start in range(0, len(ids), _RESOLVE_BATCH_MAX):
            batch = ids[start : start + _RESOLVE_BATCH_MAX]
            data = self._request("POST", "/resolve_source_ids", {"source_ids": batch})
            resolved = data.get("resolved", [])
            if isinstance(resolved, dict):
                out.update({str(k): bool(v) for k, v in resolved.items()})
            else:
                out.update({str(i): True for i in resolved})
                out.update({str(i): False for i in data.get("unresolved", [])})
            for i in batch:  # anything the service didn't mention is dead
                out.setdefault(i, False)
        return out

    def health(self) -> dict[str, Any]:
        try:
            resp = self._client.get("/health", timeout=3.0)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise RagUnavailable(f"health check failed for {self.base_url}: {exc}") from exc


# ---------------------------------------------------------------- routing ---


class RoutingRag:
    """Facade the app talks to. Routes by corpus kind / source_id prefix:

        chunk:*              -> primary (plotline-rag, DynamoDB resolver)
        asset:/stat:/trend:* -> aux (devrag fixtures) until plotline-kb lands

    The app-internal `kind` filter key picks the corpus and is never sent to
    the primary service (its contract only knows platform/topic/content_type/
    tier/claim_type).
    """

    _AUX_PREFIXES = ("asset:", "stat:", "trend:")
    _UNSET: Any = object()

    def __init__(
        self,
        primary: Optional[RagClient] = None,
        aux: Any = _UNSET,  # explicit None = no aux; unset = from config
        enabled: Optional[bool] = None,
    ):
        self.primary = primary or RagClient(config.RAG_BASE_URL)
        if aux is self._UNSET:
            aux = RagClient(config.AUX_RAG_URL) if config.AUX_RAG_URL else None
        self.aux = aux
        self.enabled = config.RAG_ENABLED if enabled is None else enabled
        self._ready_logged = False

    @property
    def base_url(self) -> str:
        return self.primary.base_url

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise RagDisabled(
                "retrieval is disabled (PLOTLINE_RAG_ENABLED=0) — evidence-requiring "
                "flows cannot run; enable RAG or use a workflow that needs no evidence"
            )

    def ensure_ready(self) -> dict[str, Any]:
        """Health-check the primary before the first retrieval (once per
        process). Raises RagUnavailable when the required backend is down."""
        self._require_enabled()
        if self._ready_logged:
            return {"status": "ok", "cached": True}
        status = self.primary.health()
        if not self._ready_logged:
            manifest = status.get("manifest") or {}
            logger.info(
                "plotline-rag ready: model=%s chunks=%s as_of=%s index_source=%s resolver=%s cache_age=%ss",
                manifest.get("model"), manifest.get("chunks"), manifest.get("as_of"),
                status.get("index_source"), status.get("resolver"),
                status.get("cache_age_seconds"),
            )
            self._ready_logged = True
        return status

    # ------------------------------------------------------------- contract --

    def search_corpus(
        self,
        query: str,
        k: int = 8,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        self._require_enabled()
        filters = dict(filters or {})
        kind = filters.pop("kind", "chunk")

        if kind in ("asset", "stat", "trend"):
            if self.aux is None:
                logger.info("rag search: corpus kind=%s not hosted yet (no aux service) — returning empty", kind)
                return []
            return self.aux.search_corpus(query, k=k, filters={**filters, "kind": kind}, corpus=None)

        self.ensure_ready()
        contract_filters = {
            dim: v if isinstance(v, list) else [v]
            for dim, v in filters.items()
            if dim in _CONTRACT_FILTER_DIMS and v
        }
        return self.primary.search_corpus(query, k=k, filters=contract_filters)

    def resolve_source_ids(self, source_ids: list[str]) -> dict[str, bool]:
        """Fail closed: a backend needed for any cited id must answer, or the
        whole resolution raises and the agent output is rejected."""
        self._require_enabled()
        ids = sorted(set(source_ids))
        if not ids:
            return {}
        primary_ids = [i for i in ids if i.startswith("chunk:")]
        aux_ids = [i for i in ids if i.startswith(self._AUX_PREFIXES)]
        unknown = [i for i in ids if i not in primary_ids and i not in aux_ids]

        out: dict[str, bool] = {i: False for i in unknown}  # unknown prefix = dead citation
        if primary_ids:
            out.update(self.primary.resolve_source_ids(primary_ids))
        if aux_ids:
            if self.aux is None:
                raise RagUnavailable(
                    f"cited ids {aux_ids} belong to corpora with no configured resolver "
                    "(PLOTLINE_AUX_RAG_URL unset) — failing closed"
                )
            out.update(self.aux.resolve_source_ids(aux_ids))
        return out

    def health(self) -> dict[str, Any]:
        status = self.primary.health()
        if self.aux is not None:
            try:
                status["aux"] = self.aux.health()
            except RagUnavailable as exc:
                status["aux"] = {"error": str(exc)}
        return status


rag = RoutingRag()
