from __future__ import annotations

import pytest

from app import config, store
from app.rag_client import RagClient, RoutingRag

# Originals, for tests that exercise the real HTTP plumbing via MockTransport
# (the autouse local_rag fixture patches these class-wide).
REAL_REQUEST = RagClient._request
REAL_HEALTH = RagClient.health


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    # LOG_DIR too, not just the database. Tests deliberately drive agents into
    # failure (bad_mock returns an invalid CampaignOptions to prove retries are
    # bounded), and without this those runs append to the REAL
    # data/runs/agent_runs.jsonl — where the observability view then shows them
    # as production failures. A test that pollutes the log it is not supposed to
    # touch is a leak, not a side effect.
    logs = tmp_path / "runs"
    logs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "LOG_DIR", logs)
    store._conn = None
    yield
    store._conn = None


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    monkeypatch.setattr(config, "MOCK_LLM", True)


@pytest.fixture(autouse=True)
def local_rag(monkeypatch):
    """Zero-network: RagClient hits the devrag stub in-process (same fixtures,
    same contract), and the app-wide RoutingRag gets devrag as BOTH primary and
    aux so every corpus + every id prefix resolves like before."""

    def _request(self, method, path, payload=None):
        from devrag import server

        if path == "/search_corpus":
            return server.search_corpus(server.SearchRequest(**payload))
        if path == "/resolve_source_ids":
            return server.resolve_source_ids(server.ResolveRequest(**payload))
        raise AssertionError(f"unexpected RAG path {path}")

    def _health(self):
        from devrag import server

        return server.health()

    monkeypatch.setattr(RagClient, "_request", _request)
    monkeypatch.setattr(RagClient, "health", _health)

    routing = RoutingRag(primary=RagClient("http://test"), aux=RagClient("http://test"), enabled=True)
    for module in ("app.rag_client", "app.threadkit", "app.campaign", "app.main"):
        monkeypatch.setattr(f"{module}.rag", routing, raising=False)
    return routing
