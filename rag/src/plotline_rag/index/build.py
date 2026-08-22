"""Build and synchronize Plotline's local dense/BM25 index."""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from rank_bm25 import BM25Okapi

from plotline_rag.config import CACHE_DIR, LOCAL_CORPUS, MODEL_NAME, S3_KEYS
from plotline_rag.index.embed import Embedder, FastEmbedder
from plotline_rag.schema import KBChunk
from plotline_rag.store.ddb import DynamoChunkStore
from plotline_rag.store.s3 import S3Store


TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_chunks(path: Path) -> list[KBChunk]:
    chunks: list[KBChunk] = []
    seen: set[str] = set()
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                chunk = KBChunk.model_validate_json(line)
            except Exception as exc:
                raise ValueError(f"invalid JSONL at line {line_number}: {exc}") from exc
            if chunk.source_id_full in seen:
                raise ValueError(f"duplicate source id: {chunk.source_id_full}")
            seen.add(chunk.source_id_full)
            chunks.append(chunk)
    if not chunks:
        raise ValueError("corpus is empty")
    if len(chunks) > 20_000:
        raise ValueError("corpus exceeds the v1 ceiling of 20,000 chunks")
    return chunks


def build_index(
    input_path: Path = LOCAL_CORPUS,
    output_root: Path = CACHE_DIR,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    input_path = Path(input_path)
    output_root = Path(output_root)
    chunks = read_chunks(input_path)
    active_embedder = embedder or FastEmbedder(model_name=MODEL_NAME)

    texts = [chunk.text for chunk in chunks]
    matrix = active_embedder.embed_documents(texts).astype(np.float32, copy=False)
    if matrix.shape[0] != len(chunks):
        raise ValueError("embedder returned a row count that does not match the corpus")

    corpus_target = output_root / S3_KEYS["corpus"]
    index_dir = output_root / "index"
    corpus_target.parent.mkdir(parents=True, exist_ok=True)
    index_dir.mkdir(parents=True, exist_ok=True)
    if input_path.resolve() != corpus_target.resolve():
        shutil.copy2(input_path, corpus_target)

    embeddings_path = output_root / S3_KEYS["embeddings"]
    np.savez_compressed(
        embeddings_path,
        embeddings=matrix,
        ids=np.asarray([chunk.source_id_full for chunk in chunks], dtype=str),
    )

    bm25_path = output_root / S3_KEYS["bm25"]
    with bm25_path.open("wb") as handle:
        pickle.dump(BM25Okapi([tokenize(text) for text in texts]), handle, protocol=5)

    as_of_values = [chunk.retrieved_at for chunk in chunks if chunk.retrieved_at]
    manifest = {
        "schema_version": 1,
        "model": active_embedder.model_name,
        "dim": int(matrix.shape[1]),
        "chunks": len(chunks),
        "jsonl_hash": sha256_file(corpus_target),
        "artifact_hashes": {
            "embeddings": sha256_file(embeddings_path),
            "bm25": sha256_file(bm25_path),
        },
        "as_of": max(as_of_values) if as_of_values else datetime.now(UTC).isoformat(),
        "built_at": datetime.now(UTC).isoformat(),
    }
    manifest_path = output_root / S3_KEYS["manifest"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def ensure_current_index(input_path: Path, output_root: Path) -> dict[str, Any]:
    manifest_path = output_root / S3_KEYS["manifest"]
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        required = [output_root / key for key in S3_KEYS.values()]
        if (
            all(path.is_file() for path in required)
            and manifest.get("jsonl_hash") == sha256_file(input_path)
            and manifest.get("model") == MODEL_NAME
        ):
            return manifest
    return build_index(input_path=input_path, output_root=output_root)


def sync_index(input_path: Path, output_root: Path, dry_run: bool = False) -> dict[str, Any]:
    manifest = ensure_current_index(input_path, output_root)
    chunks = read_chunks(input_path)
    if dry_run:
        return {"dry_run": True, "chunks": len(chunks), "manifest": manifest}

    ddb = DynamoChunkStore()
    capacity = ddb.validate_free_tier_fence()
    s3 = S3Store(cache_dir=output_root)
    uploaded = not s3.bundle_is_current(manifest)
    if uploaded:
        for name in ("corpus", "embeddings", "bm25", "manifest"):
            s3.put_file(output_root / S3_KEYS[name], S3_KEYS[name])
    ddb_result = ddb.sync_chunks(chunks)
    s3.mark_synced()
    return {
        "dry_run": False,
        "uploaded": uploaded,
        "chunks": len(chunks),
        "ddb": ddb_result,
        "plotline_capacity": capacity,
        "manifest": manifest,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "sync"):
        child = subparsers.add_parser(command)
        child.add_argument("--input", type=Path, default=LOCAL_CORPUS)
        child.add_argument("--output", type=Path, default=CACHE_DIR)
        if command == "sync":
            child.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "build":
        result = build_index(args.input, args.output)
    else:
        result = sync_index(args.input, args.output, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
