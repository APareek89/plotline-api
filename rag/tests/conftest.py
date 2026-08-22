from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Location-independent safety: these tests are moto-backed and must never
# touch real AWS, no matter where pytest is invoked from (make test sets the
# same guards; this covers repo-root collection).
os.environ.setdefault("PLOTLINE_OFFLINE", "1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")


def chunk_dict(
    source_id: str,
    text: str,
    *,
    platform: list[str] | None = None,
    topic: list[str] | None = None,
) -> dict:
    return {
        "id": f"id-{source_id}",
        "corpus": "best_practice",
        "source_id": source_id,
        "source_url": f"urn:test:{source_id}",
        "source_tier": "official",
        "title": f"Title {source_id}",
        "text": text,
        "platform": platform or ["youtube"],
        "topic": topic or ["retention"],
        "content_type": ["short_video"],
        "claim_type": "heuristic",
        "quotes": [],
        "stats": [],
        "published": None,
        "retrieved_at": "2026-08-21T00:00:00Z",
        "hash": f"hash-{source_id}",
    }


@pytest.fixture
def corpus_path(tmp_path: Path) -> Path:
    chunks = [
        chunk_dict(
            "C0001",
            "YouTube Shorts retention improves when the length delivers the idea without a slow intro.",
            platform=["youtube"],
            topic=["retention", "duration"],
        ),
        chunk_dict(
            "C0002",
            "Instagram Reels need a clear visual hook in the opening moments.",
            platform=["instagram"],
            topic=["hooks"],
        ),
        chunk_dict(
            "C0003",
            "Accurate captions improve accessibility when viewers watch video without sound.",
            platform=["youtube", "instagram"],
            topic=["captions", "accessibility"],
        ),
    ]
    path = tmp_path / "best_practice.jsonl"
    path.write_text("".join(json.dumps(chunk) + "\n" for chunk in chunks), encoding="utf-8")
    return path

