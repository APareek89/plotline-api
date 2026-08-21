"""plotline-rag integration: client contract, reliability, routing, seed
safety, and the two-layer citation validation (local subset + remote resolve).

Zero network — HTTP paths run against httpx.MockTransport, validator paths
against fakes.
"""
from __future__ import annotations

import httpx
import pytest

from app import config
from app.rag_client import (
    RagBadRequest,
    RagClient,
    RagDisabled,
    RagUnavailable,
    RoutingRag,
)
from app.schemas import Evidence
from app.validators import AgentValidationError, resolve_or_fail
from tests import conftest as cf


def _client(handler, timeout: float = 1.0) -> RagClient:
    """RagClient with the REAL request plumbing + a mock transport."""
    client = RagClient("http://rag.test", timeout=timeout, transport=httpx.MockTransport(handler))
    client._request = cf.REAL_REQUEST.__get__(client, RagClient)
    return client


SEARCH_RESPONSE = {
    "results": [
        {
            "source_id": "chunk:C0001",
            "tier": "official",
            "title": "First-frame rules",
            "text": "Readable on mute, max 3 objects.",
            "claim_type": "practice",
            "stats": [],
            "score": 0.83,
            "components": {"dense": 0.71, "bm25": 0.65},
        },
        {
            "source_id": "chunk:C0002",
            "tier": "seed",
            "title": "Seed guidance",
            "text": "Dev-only seed chunk.",
            "claim_type": "heuristic",
            "stats": [],
            "score": 0.61,
            "components": {"dense": 0.5, "bm25": 0.4},
        },
    ],
    "manifest": {"model": "bge-small-en-v1.5", "chunks": 10, "as_of": "2026-08-21T00:00:00Z"},
}


# ------------------------------------------------------------------ client --


def test_search_parses_results_and_preserves_fields(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_SEED_EVIDENCE", True)
    client = _client(lambda req: httpx.Response(200, json=SEARCH_RESPONSE))
    results = client.search_corpus("hook retention", k=6)
    assert [r["source_id"] for r in results] == ["chunk:C0001", "chunk:C0002"]
    first = results[0]
    for field in ("source_id", "tier", "title", "text", "claim_type", "stats", "score"):
        assert field in first


def test_search_sends_corpus_rerank_and_filters():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"results": [], "manifest": {}})

    client = _client(handler)
    client.search_corpus("q", k=6, filters={"platform": ["youtube"], "tier": ["official", "expert"]})
    assert seen["corpus"] == "best_practice"
    assert seen["rerank"] is False
    assert seen["filters"] == {"platform": ["youtube"], "tier": ["official", "expert"]}
    assert seen["k"] == 6


def test_empty_results_is_insufficient_evidence_not_an_error():
    client = _client(lambda req: httpx.Response(200, json={"results": [], "manifest": {}}))
    assert client.search_corpus("obscure query") == []


def test_timeout_and_5xx_use_bounded_retries():
    attempts = {"n": 0}

    def flaky(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectTimeout("boom")
        return httpx.Response(200, json={"results": [], "manifest": {}})

    assert _client(flaky).search_corpus("q") == []
    assert attempts["n"] == 3  # 2 retries then success

    attempts["n"] = 0

    def always_500(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(500, text="err")

    with pytest.raises(RagUnavailable):
        _client(always_500).search_corpus("q")
    assert attempts["n"] == 3  # bounded: exactly 1 + 2 retries, no loop


def test_http_400_is_not_retried():
    attempts = {"n": 0}

    def reject(req: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(400, text="rerank unsupported in v1")

    with pytest.raises(RagBadRequest):
        _client(reject).search_corpus("q")
    assert attempts["n"] == 1


def test_resolve_normalizes_both_wire_shapes():
    list_shape = {"resolved": ["chunk:C0001"], "unresolved": ["stat:S999"]}
    client = _client(lambda req: httpx.Response(200, json=list_shape))
    assert client.resolve_source_ids(["chunk:C0001", "stat:S999"]) == {
        "chunk:C0001": True,
        "stat:S999": False,
    }

    dict_shape = {"resolved": {"chunk:C0001": True, "stat:S999": False}}
    client = _client(lambda req: httpx.Response(200, json=dict_shape))
    assert client.resolve_source_ids(["chunk:C0001", "stat:S999"]) == {
        "chunk:C0001": True,
        "stat:S999": False,
    }


def test_duplicate_citations_deduplicated_before_validation():
    payloads: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        payloads.append(json.loads(req.content))
        return httpx.Response(200, json={"resolved": ["chunk:C0001"], "unresolved": []})

    client = _client(handler)
    client.resolve_source_ids(["chunk:C0001", "chunk:C0001", "chunk:C0001"])
    assert payloads == [{"source_ids": ["chunk:C0001"]}]


def test_seed_evidence_rejected_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_SEED_EVIDENCE", False)
    client = _client(lambda req: httpx.Response(200, json=SEARCH_RESPONSE))
    results = client.search_corpus("hook retention")
    assert [r["source_id"] for r in results] == ["chunk:C0001"]
    assert all(r.get("tier") != "seed" for r in results)

    monkeypatch.setattr(config, "ALLOW_SEED_EVIDENCE", True)
    assert len(client.search_corpus("hook retention")) == 2


# ----------------------------------------------------------------- routing --


class FakeBackend:
    """Stands in for a RagClient inside RoutingRag."""

    def __init__(self, resolve_map=None, results=None, fail=False):
        self.resolve_map = resolve_map or {}
        self.results = results or []
        self.fail = fail
        self.search_calls: list = []
        self.resolve_calls: list = []

    def search_corpus(self, query, k=8, filters=None, corpus="best_practice"):
        if self.fail:
            raise RagUnavailable("backend down")
        self.search_calls.append({"query": query, "k": k, "filters": filters, "corpus": corpus})
        return self.results

    def resolve_source_ids(self, ids):
        if self.fail:
            raise RagUnavailable("backend down")
        self.resolve_calls.append(sorted(ids))
        return {i: self.resolve_map.get(i, False) for i in ids}

    def health(self):
        if self.fail:
            raise RagUnavailable("backend down")
        return {"status": "ok", "manifest": {"model": "bge-small-en-v1.5", "chunks": 10}}


def test_routing_chunk_search_strips_kind_and_listifies_filters():
    primary = FakeBackend(results=[{"source_id": "chunk:C0001", "tier": "official"}])
    routing = RoutingRag(primary=primary, aux=None, enabled=True)
    routing.search_corpus("q", filters={"kind": "chunk", "topic": "first_frame", "niche": "ai_tools"})
    sent = primary.search_calls[0]["filters"]
    assert sent == {"topic": ["first_frame"]}  # kind stripped, non-contract dims dropped


def test_routing_aux_corpora_and_resolution_split():
    primary = FakeBackend(resolve_map={"chunk:C0001": True})
    aux = FakeBackend(resolve_map={"asset:A118": True}, results=[{"source_id": "asset:A118"}])
    routing = RoutingRag(primary=primary, aux=aux, enabled=True)

    routing.search_corpus("q", filters={"kind": "asset", "niche": "ai_tools"})
    assert aux.search_calls[0]["filters"]["kind"] == "asset"  # aux keeps legacy shape
    assert primary.search_calls == []

    resolved = routing.resolve_source_ids(["chunk:C0001", "asset:A118", "bogus:X1"])
    assert resolved == {"chunk:C0001": True, "asset:A118": True, "bogus:X1": False}
    assert primary.resolve_calls == [["chunk:C0001"]]
    assert aux.resolve_calls == [["asset:A118"]]


def test_routing_missing_aux_resolver_fails_closed():
    routing = RoutingRag(primary=FakeBackend(), aux=None, enabled=True)
    with pytest.raises(RagUnavailable):
        routing.resolve_source_ids(["asset:A118"])


def test_rag_disabled_is_explicit():
    routing = RoutingRag(primary=FakeBackend(), aux=None, enabled=False)
    with pytest.raises(RagDisabled):
        routing.search_corpus("q")
    with pytest.raises(RagDisabled):
        routing.resolve_source_ids(["chunk:C0001"])
    with pytest.raises(RagDisabled):
        routing.ensure_ready()


# ------------------------------------------- two-layer citation validation --


def _evidence(*ids: str) -> list[Evidence]:
    return [Evidence(tag="REF", source_id=i, claim="c", as_of=None) for i in ids]


def _routing(resolve_map: dict) -> RoutingRag:
    return RoutingRag(primary=FakeBackend(resolve_map=resolve_map), aux=None, enabled=True)


def test_valid_retrieved_citation_passes_both_checks():
    errors: list = []
    resolve_or_fail(
        _evidence("chunk:C0001"),
        _routing({"chunk:C0001": True}),
        errors,
        "concept c01",
        retrieved_ids={"chunk:C0001"},
    )
    assert errors == []


def test_invented_but_db_valid_citation_fails_local_subset():
    errors: list = []
    resolve_or_fail(
        _evidence("chunk:C0001"),
        _routing({"chunk:C0001": True}),  # DynamoDB would say yes…
        errors,
        "concept c01",
        retrieved_ids={"chunk:C0009"},  # …but it was never retrieved this run
    )
    assert len(errors) == 1 and "NOT returned by any retrieval tool" in errors[0]


def test_retrieved_but_unresolved_citation_fails_remote_check():
    errors: list = []
    resolve_or_fail(
        _evidence("chunk:C0404"),
        _routing({"chunk:C0404": False}),
        errors,
        "concept c01",
        retrieved_ids={"chunk:C0404"},
    )
    assert len(errors) == 1 and "unresolvable" in errors[0]


def test_validator_outage_fails_closed():
    routing = RoutingRag(primary=FakeBackend(fail=True), aux=None, enabled=True)
    with pytest.raises(RagUnavailable):
        resolve_or_fail(_evidence("chunk:C0001"), routing, [], "concept c01",
                        retrieved_ids={"chunk:C0001"})


def test_generation_retries_are_bounded_on_citation_failure():
    """An agent that keeps inventing citations is re-run with the error a
    bounded number of times (§3.9: max 2 retries) and then hard-fails —
    citations are never silently stripped."""
    from app.agents.runner import AgentHardFail, run_agent
    from app.schemas import CreatorContext

    calls = {"n": 0}

    def bad_mock(payload, dispatcher):
        calls["n"] += 1
        return {"clarifying_questions": ["a?", "b?", "c?", "d?"]}  # always invalid

    with pytest.raises(AgentHardFail):
        run_agent(
            agent="intake.test",
            prompt_name="intake",
            model="mock",
            user_payload={},
            schema=CreatorContext,
            validate=lambda c: __import__("app.validators", fromlist=["validate_intake"]).validate_intake(c),
            mock_fn=bad_mock,
            use_tools=False,
        )
    assert calls["n"] == config.MAX_VALIDATION_RETRIES + 1  # bounded, no infinite loop


# ------------------------------------------------- mocked end-to-end flow --


def test_mocked_planning_flow_accepts_cited_retrieved_evidence():
    """user request -> retrieval -> evidence to agent -> output cites the
    retrieved chunk -> local subset check -> /resolve_source_ids -> accepted."""
    from app.tools import ToolDispatcher

    chunk = {
        "source_id": "chunk:C0001",
        "tier": "official",
        "title": "First-frame rules",
        "text": "Readable on mute.",
        "claim_type": "practice",
        "stats": [],
        "score": 0.9,
    }
    primary = FakeBackend(results=[chunk], resolve_map={"chunk:C0001": True})
    routing = RoutingRag(primary=primary, aux=None, enabled=True)

    run_retrieved: set = set()
    dispatcher = ToolDispatcher(routing, lambda: {}, surfaced_ids=run_retrieved)
    import json as _json

    evidence_json = dispatcher.dispatch("search_corpus", {"query": "first frame rules"})
    assert _json.loads(evidence_json)[0]["source_id"] == "chunk:C0001"
    assert run_retrieved == {"chunk:C0001"}  # retrieval recorded for the subset check

    errors: list = []
    resolve_or_fail(_evidence("chunk:C0001"), routing, errors, "concept c01",
                    retrieved_ids=run_retrieved)
    assert errors == []
    assert primary.resolve_calls == [["chunk:C0001"]]

    # the same output citing a non-retrieved id is rejected end-to-end
    errors = []
    resolve_or_fail(_evidence("chunk:C0777"), routing, errors, "concept c01",
                    retrieved_ids=run_retrieved)
    assert errors and "NOT returned" in errors[0]
