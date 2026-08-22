"""In-process dense + BM25 search fused with reciprocal-rank fusion."""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from plotline_rag.config import CACHE_DIR, S3_KEYS
from plotline_rag.index.build import read_chunks, sha256_file, tokenize
from plotline_rag.index.embed import Embedder, FastEmbedder
from plotline_rag.schema import KBChunk, ScoreComponents, SearchRequest, SearchResult


class Reranker(Protocol):
    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]: ...


class HybridSearch:
    def __init__(
        self,
        root: Path = CACHE_DIR,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.root = Path(root)
        self.manifest_path = self.root / S3_KEYS["manifest"]
        self.embeddings_path = self.root / S3_KEYS["embeddings"]
        self.bm25_path = self.root / S3_KEYS["bm25"]
        self.corpus_path = self.root / S3_KEYS["corpus"]
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self._verify_artifacts()

        with np.load(self.embeddings_path, allow_pickle=False) as payload:
            self.embeddings = payload["embeddings"].astype(np.float32, copy=False)
            self.ids = [str(value) for value in payload["ids"].tolist()]
        with self.bm25_path.open("rb") as handle:
            self.bm25 = pickle.load(handle)

        chunks_by_id = {chunk.source_id_full: chunk for chunk in read_chunks(self.corpus_path)}
        try:
            self.chunks = [chunks_by_id[source_id] for source_id in self.ids]
        except KeyError as exc:
            raise ValueError(f"embedding id missing from corpus: {exc.args[0]}") from exc
        if self.embeddings.shape[0] != len(self.chunks):
            raise ValueError("embedding matrix row count does not match the corpus")

        self.embedder = embedder or FastEmbedder(model_name=self.manifest["model"])
        if self.embedder.model_name != self.manifest["model"]:
            raise ValueError(
                f"embedder {self.embedder.model_name!r} does not match manifest "
                f"{self.manifest['model']!r}"
            )
        self.reranker = reranker
        self.source_ids = set(self.ids)

    def _verify_artifacts(self) -> None:
        if sha256_file(self.corpus_path) != self.manifest["jsonl_hash"]:
            raise ValueError("cached corpus hash does not match manifest")
        for name, path in (("embeddings", self.embeddings_path), ("bm25", self.bm25_path)):
            expected = self.manifest.get("artifact_hashes", {}).get(name)
            if not expected or sha256_file(path) != expected:
                raise ValueError(f"cached {name} hash does not match manifest")

    @staticmethod
    def _matches(chunk: KBChunk, request: SearchRequest) -> bool:
        if chunk.corpus != request.corpus:
            return False
        filters = request.filters
        list_fields = (
            (filters.platform, chunk.platform),
            (filters.topic, chunk.topic),
            (filters.content_type, chunk.content_type),
        )
        for selected, actual in list_fields:
            if selected and not set(selected).intersection(actual):
                return False
        if filters.tier and chunk.source_tier.lower() not in filters.tier:
            return False
        if filters.claim_type and chunk.claim_type.lower() not in filters.claim_type:
            return False
        return True

    def search(self, request: SearchRequest) -> list[SearchResult]:
        candidate_indices = [
            index for index, chunk in enumerate(self.chunks) if self._matches(chunk, request)
        ]
        if not candidate_indices:
            return []

        query_vector = self.embedder.embed_query(request.query).astype(np.float32, copy=False)
        if query_vector.shape[0] != self.embeddings.shape[1]:
            raise ValueError("query embedding dimension does not match the index")
        dense_all = self.embeddings @ query_vector
        bm25_all = np.asarray(self.bm25.get_scores(tokenize(request.query)), dtype=np.float32)

        dense_order = sorted(candidate_indices, key=lambda index: (-float(dense_all[index]), self.ids[index]))
        bm25_order = sorted(candidate_indices, key=lambda index: (-float(bm25_all[index]), self.ids[index]))
        dense_rank = {index: rank for rank, index in enumerate(dense_order, start=1)}
        bm25_rank = {index: rank for rank, index in enumerate(bm25_order, start=1)}
        fused = {
            index: (1.0 / (60 + dense_rank[index])) + (1.0 / (60 + bm25_rank[index]))
            for index in candidate_indices
        }
        fused_order = sorted(candidate_indices, key=lambda index: (-fused[index], self.ids[index]))

        max_bm25 = max((float(bm25_all[index]) for index in candidate_indices), default=0.0)
        rrf_ceiling = 2.0 / 61.0
        results: list[SearchResult] = []
        for index in fused_order[: request.k]:
            chunk = self.chunks[index]
            result = SearchResult(
                source_id=chunk.source_id_full,
                tier=chunk.source_tier,
                title=chunk.title,
                text=chunk.text,
                claim_type=chunk.claim_type,
                stats=chunk.stats,
                score=round(min(1.0, fused[index] / rrf_ceiling), 6),
                components=ScoreComponents(
                    dense=round(max(0.0, min(1.0, float(dense_all[index]))), 6),
                    bm25=round(max(0.0, float(bm25_all[index]) / max_bm25), 6)
                    if max_bm25 > 0
                    else 0.0,
                ),
            )
            results.append(result)
        if request.rerank:
            if self.reranker is None:
                raise ValueError("rerank=true is reserved but no v1 reranker is configured")
            results = self.reranker.rerank(request.query, results)
        return results

    def public_manifest(self) -> dict[str, Any]:
        return {
            "model": self.manifest["model"].rsplit("/", 1)[-1],
            "chunks": self.manifest["chunks"],
            "as_of": self.manifest["as_of"],
        }

