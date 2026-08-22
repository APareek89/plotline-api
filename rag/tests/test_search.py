from __future__ import annotations

from pathlib import Path

import pytest

from plotline_rag.index.build import build_index, read_chunks
from plotline_rag.index.embed import HashingEmbedder
from plotline_rag.schema import SearchFilters, SearchRequest
from plotline_rag.search.hybrid import HybridSearch


def build_test_engine(corpus_path: Path, root: Path) -> HybridSearch:
    embedder = HashingEmbedder(dim=256)
    build_index(corpus_path, root, embedder=embedder)
    return HybridSearch(root=root, embedder=HashingEmbedder(dim=256))


def test_hybrid_search_returns_expected_chunk(corpus_path: Path, tmp_path: Path) -> None:
    engine = build_test_engine(corpus_path, tmp_path / "cache")
    results = engine.search(SearchRequest(query="YouTube Shorts length for retention", k=2))
    assert results[0].source_id == "chunk:C0001"
    assert results[0].components.dense > 0
    assert results[0].components.bm25 > 0


def test_filters_are_applied_before_fusion(corpus_path: Path, tmp_path: Path) -> None:
    engine = build_test_engine(corpus_path, tmp_path / "cache")
    results = engine.search(
        SearchRequest(
            query="video opening",
            k=5,
            filters=SearchFilters(platform=["instagram"], topic=["hooks"]),
        )
    )
    assert [result.source_id for result in results] == ["chunk:C0002"]


def test_reserved_reranker_requires_implementation(corpus_path: Path, tmp_path: Path) -> None:
    engine = build_test_engine(corpus_path, tmp_path / "cache")
    with pytest.raises(ValueError, match="no v1 reranker"):
        engine.search(SearchRequest(query="captions", rerank=True))


def test_duplicate_source_ids_are_rejected(corpus_path: Path) -> None:
    corpus_path.write_text(corpus_path.read_text() * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate source id"):
        read_chunks(corpus_path)

