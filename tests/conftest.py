from __future__ import annotations

import pytest

from app import config, store
from app.rag_client import RagClient


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    store._conn = None
    yield
    store._conn = None


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    monkeypatch.setattr(config, "MOCK_LLM", True)


@pytest.fixture(autouse=True)
def local_rag(monkeypatch):
    """Route the RagClient at the devrag stub in-process — same fixtures, same
    contract, no server needed."""

    def _post(self, path, payload):
        from devrag import server

        if path == "/search_corpus":
            return server.search_corpus(server.SearchRequest(**payload))
        if path == "/resolve_source_ids":
            return server.resolve_source_ids(server.ResolveRequest(**payload))
        raise AssertionError(f"unexpected RAG path {path}")

    monkeypatch.setattr(RagClient, "_post", _post)
