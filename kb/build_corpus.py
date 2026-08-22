#!/usr/bin/env python3
"""plotline-kb: compile kb/sources/*.yaml into rag/data/best_practice.jsonl.

The KB lane owns corpus QUALITY; the rag service owns retrieval. Hard rules
enforced here (fail loud, never write a partial corpus):

  - source_id: bare C\\d{4} (the service exposes it as chunk:C\\d{4}); ids are
    CITATIONS — never renumber or reuse one, retire ids by deleting the chunk
  - tier vocabulary: seed | curated | official | expert
      * official/expert REQUIRE a real http(s) source_url (checkable claims)
      * curated/seed use urn:plotline:<tier>:<id> — honest about having no
        external source; tier=seed is allowed only in sources/seed.yaml and
        is rejected at serving time unless PLOTLINE_ALLOW_SEED_EVIDENCE=1
  - claim_type: heuristic | practice | method | compliance | stat
  - text 40..2000 chars, non-empty title, >=1 topic tag
  - hash: sha256(text)[:16] unless the chunk pins one explicitly (seed chunks
    pin theirs so the compiled output stays byte-identical and `make sync`
    stays a no-op until content really changes)

Usage:  python kb/build_corpus.py [--check]   (--check = validate + diff only)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

KB_DIR = Path(__file__).resolve().parent
SOURCES_DIR = KB_DIR / "sources"
OUTPUT = KB_DIR.parent / "rag" / "data" / "best_practice.jsonl"

TIERS = {"seed", "curated", "official", "expert"}
CLAIM_TYPES = {"heuristic", "practice", "method", "compliance", "stat"}
# "twitter" is legacy (seed corpus) — new chunks use "x"; drop the alias when
# the seed file retires.
PLATFORMS = {"youtube", "instagram", "tiktok", "linkedin", "x", "twitter", "generic"}
ID_RE = re.compile(r"^C\d{4}$")

# Emit keys in the exact order the existing corpus uses — deterministic output
# means `git diff`/`make sync` only fire on real content changes.
KEY_ORDER = [
    "id", "corpus", "source_id", "source_url", "source_tier", "title", "text",
    "platform", "topic", "content_type", "claim_type", "quotes", "stats",
    "published", "retrieved_at", "hash",
]


def fail(errors: list[str]) -> None:
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    raise SystemExit(1)


def load_sources() -> list[dict]:
    files = sorted(SOURCES_DIR.glob("*.yaml"))
    if not files:
        fail([f"no source files in {SOURCES_DIR}"])
    chunks: list[dict] = []
    errors: list[str] = []
    seen_ids: dict[str, str] = {}

    for path in files:
        doc = yaml.safe_load(path.read_text()) or {}
        for i, chunk in enumerate(doc.get("chunks", [])):
            where = f"{path.name}#{i} ({chunk.get('source_id', '?')})"
            sid = chunk.get("source_id", "")
            if not ID_RE.match(sid):
                errors.append(f"{where}: source_id must match C\\d{{4}}")
                continue
            if sid in seen_ids:
                errors.append(f"{where}: duplicate source_id (also in {seen_ids[sid]})")
                continue
            seen_ids[sid] = path.name

            tier = chunk.get("source_tier", "")
            if tier not in TIERS:
                errors.append(f"{where}: source_tier {tier!r} not in {sorted(TIERS)}")
            if tier == "seed" and path.name != "seed.yaml":
                errors.append(f"{where}: tier=seed is only allowed in sources/seed.yaml")

            url = chunk.get("source_url", "")
            if tier in ("official", "expert"):
                if not url.startswith(("http://", "https://")):
                    errors.append(f"{where}: tier={tier} requires a real http(s) source_url")
            elif not url.startswith("urn:plotline:"):
                errors.append(f"{where}: tier={tier} must use a urn:plotline:* source_url")

            text = chunk.get("text", "")
            if not (40 <= len(text) <= 2000):
                errors.append(f"{where}: text length {len(text)} outside 40..2000")
            if not chunk.get("title"):
                errors.append(f"{where}: title required")
            if not chunk.get("topic"):
                errors.append(f"{where}: at least one topic tag required")
            if chunk.get("claim_type") not in CLAIM_TYPES:
                errors.append(f"{where}: claim_type {chunk.get('claim_type')!r} not in {sorted(CLAIM_TYPES)}")
            bad_platforms = set(chunk.get("platform", [])) - PLATFORMS
            if bad_platforms:
                errors.append(f"{where}: unknown platform(s) {sorted(bad_platforms)}")

            chunks.append({"_file": path.name, **chunk})

    if errors:
        fail(errors)
    return sorted(chunks, key=lambda c: c["source_id"])


def compile_chunk(chunk: dict) -> dict:
    text = chunk["text"]
    record = {
        "id": chunk.get("id") or f"kb-{chunk['source_id'].lower()}",
        "corpus": "best_practice",
        "source_id": chunk["source_id"],
        "source_url": chunk["source_url"],
        "source_tier": chunk["source_tier"],
        "title": chunk["title"],
        "text": text,
        "platform": chunk.get("platform", ["generic"]),
        "topic": chunk["topic"],
        "content_type": chunk.get("content_type", []),
        "claim_type": chunk["claim_type"],
        "quotes": chunk.get("quotes", []),
        "stats": chunk.get("stats", []),
        "published": chunk.get("published"),
        "retrieved_at": chunk.get("retrieved_at"),
        "hash": chunk.get("hash") or "sha256-" + hashlib.sha256(text.encode()).hexdigest()[:16],
    }
    return {k: record[k] for k in KEY_ORDER}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate + diff, write nothing")
    args = parser.parse_args()

    records = [compile_chunk(c) for c in load_sources()]
    compiled = "\n".join(
        json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in records
    ) + "\n"

    current = OUTPUT.read_text() if OUTPUT.exists() else ""
    changed = compiled != current
    print(f"{len(records)} chunk(s) valid; output {'CHANGED' if changed else 'unchanged'} vs {OUTPUT.name}")
    if args.check:
        raise SystemExit(1 if changed else 0)
    if changed:
        OUTPUT.write_text(compiled)
        print(f"wrote {OUTPUT}")
        print("next: cd rag && make index && make eval && make sync-dry-run && make sync")


if __name__ == "__main__":
    main()
