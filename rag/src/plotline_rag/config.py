"""Configuration and TLS bootstrap shared by services, CLIs, and notebooks."""

from __future__ import annotations

import os
import ssl
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def bootstrap_ca_bundle() -> str | None:
    """Set common CA variables before libraries create HTTP clients."""
    existing = os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE")
    if existing and Path(existing).is_file():
        bundle = existing
    else:
        verify_paths = ssl.get_default_verify_paths()
        candidates = [
            verify_paths.cafile,
            "/opt/homebrew/etc/ca-certificates/cert.pem",
            "/etc/ssl/cert.pem",
        ]
        bundle = next((path for path in candidates if path and Path(path).is_file()), None)
    if bundle:
        os.environ.setdefault("SSL_CERT_FILE", bundle)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)
    return bundle


CA_BUNDLE = bootstrap_ca_bundle()

# This repo is intentionally fenced to the dedicated named AWS profile.
AWS_PROFILE = "plotline-agent"
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
S3_BUCKET = os.getenv("PLOTLINE_S3_BUCKET", "plotline-kb-apsouth1")
DDB_TABLE = os.getenv("PLOTLINE_DDB_TABLE", "plotline_chunks")

MODEL_NAME = os.getenv("PLOTLINE_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
MODEL_SHORT_NAME = MODEL_NAME.rsplit("/", 1)[-1]

DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = PROJECT_ROOT / ".cache"
LOCAL_CORPUS = DATA_DIR / "best_practice.jsonl"
CACHE_CORPUS = CACHE_DIR / "kb" / "best_practice.jsonl"
CACHE_INDEX_DIR = CACHE_DIR / "index"

INDEX_SOURCE = os.getenv("PLOTLINE_INDEX_SOURCE", "auto").lower()
OFFLINE = os.getenv("PLOTLINE_OFFLINE", "0").lower() in {"1", "true", "yes", "on"}

S3_KEYS = {
    "corpus": "kb/best_practice.jsonl",
    "embeddings": "index/embeddings.npz",
    "bm25": "index/bm25.pkl",
    "manifest": "index/manifest.json",
}
