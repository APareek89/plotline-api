# %% [markdown]
# EXPLORE THE PLOTLINE RAG — end to end, layer by layer
# ======================================================
# Personal exploration scratchpad — NOT part of the product code.
# Everything here is READ-ONLY (S3 list/get, DynamoDB scan/get, local files).
#
# How to use:
#   - VS Code: open this file, pick the interpreter .venv/bin/python
#     (Cmd+Shift+P → "Python: Select Interpreter" → ./venv shown as .venv),
#     then run each "# %%" cell with Shift+Enter (Interactive Window).
#   - Or plain terminal:  .venv/bin/python explore_rag.py
#
# The 10 layers, bottom to top:
#   1. authoring YAML (kb/sources/)          — what a human edits
#   2. compiled corpus JSONL (rag/data/)     — what the indexer eats
#   3. AWS source of truth (S3 + DynamoDB)   — what production trusts
#   4. local index cache (rag/.cache/)       — embeddings.npz, bm25.pkl, manifest
#   5. dense retrieval by hand               — embed a query, cosine, rank
#   6. BM25 retrieval by hand                — tokenize, score, rank
#   7. reciprocal-rank fusion by hand        — how the two lists become one
#   8. the real engine in-process            — HybridSearch, filters and all
#   9. the live HTTP contract (:8788)        — what plotline-api actually calls
#  10. aux sample corpora (devrag :8787)     — assets/stats/trends the app also uses

# %% ------------------------------------------------------------- 0. setup
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "rag" / "src"))  # import plotline_rag without install

import numpy as np
import yaml

# importing config anchors PROJECT_ROOT to rag/, loads rag/.env, pins CA bundle
from plotline_rag.config import AWS_REGION, CACHE_DIR, DDB_TABLE, MODEL_NAME, S3_BUCKET

AWS_PROFILE = "plotline-agent"  # the only profile this project may use (read-only here)
print(f"bucket={S3_BUCKET}  table={DDB_TABLE}  region={AWS_REGION}  model={MODEL_NAME}")
print(f"cache={CACHE_DIR}")

# %% ------------------------- 1. LAYER 1: the authoring YAML a human edits
# kb/sources/*.yaml is where corpus content is written. kb/build_corpus.py
# validates it (id format, tier vocab, real URLs for official/expert tiers)
# and compiles it. Ids are citations — never renumbered.
seed = yaml.safe_load((REPO / "kb" / "sources" / "seed.yaml").read_text())
print("source note:", seed["source"], "\n")
first = seed["chunks"][0]
print(yaml.safe_dump(first, sort_keys=False, allow_unicode=True))

# %% --------------------- 2. LAYER 2: the compiled corpus the indexer eats
corpus_path = REPO / "rag" / "data" / "best_practice.jsonl"
records = [json.loads(l) for l in corpus_path.read_text().splitlines() if l.strip()]
print(f"{len(records)} chunks in {corpus_path.relative_to(REPO)}\n")
for r in records:
    print(f"  chunk:{r['source_id']}  [{r['source_tier']:8s}] {r['claim_type']:10s}"
          f" {','.join(r['platform']):20s} {r['title']}")
# Note source_url on seed chunks: urn:plotline:seed:* — honest "no external
# source". Real official/expert chunks must carry checkable http(s) URLs.

# %% -------------------- 3a. LAYER 3: S3 — the production source of truth
import boto3

session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
s3 = session.client("s3")

print(f"objects in s3://{S3_BUCKET}:")
for obj in s3.list_objects_v2(Bucket=S3_BUCKET)["Contents"]:
    print(f"  {obj['Key']:28s} {obj['Size']:>8,} bytes  {obj['LastModified']:%Y-%m-%d %H:%M}")

manifest_s3 = json.loads(
    s3.get_object(Bucket=S3_BUCKET, Key="index/manifest.json")["Body"].read()
)
print("\nS3 manifest:", json.dumps(manifest_s3, indent=2)[:600])

# %% ------------- 3b. LAYER 3: DynamoDB — the citation-resolution authority
# When plotline-api validates a citation, /resolve_source_ids ends here:
# one item per resolvable chunk, keyed by source_id_full. 1 RCU provisioned —
# a 10-item scan is nothing, but never point a loop at this in production.
ddb = session.resource("dynamodb").Table(DDB_TABLE)
items = ddb.scan()["Items"]
print(f"{len(items)} items in {DDB_TABLE}\n")
print("item shape:", json.dumps(items[0], indent=2, default=str)[:500], "\n")
one = ddb.get_item(Key={"source_id_full": "chunk:C0001"}).get("Item")
print("GetItem chunk:C0001 →", one["title"] if one else "NOT FOUND")
ghost = ddb.get_item(Key={"source_id_full": "chunk:C9999"}).get("Item")
print("GetItem chunk:C9999 →", ghost or "NOT FOUND  ← why invented citations die")

# %% -------------------- 4. LAYER 4: the local index cache (ETag-refreshed)
# The service never queries S3 per-search. It refreshes .cache/ via ETags at
# startup and searches in-process from these three artifacts:
idx = CACHE_DIR / "index"
manifest = json.loads((idx / "manifest.json").read_text())
print("cache manifest:", json.dumps(manifest, indent=2)[:500], "\n")

with np.load(idx / "embeddings.npz", allow_pickle=False) as payload:
    emb = payload["embeddings"].astype(np.float32)
    ids = [str(v) for v in payload["ids"].tolist()]
print(f"embedding matrix: {emb.shape} float32  (chunks x dims)")
print(f"row norms ≈ 1.0 (pre-normalized): {np.linalg.norm(emb, axis=1)[:4].round(4)}")
print(f"ids: {ids[:3]} …")
print(f"first 8 dims of chunk:C0001: {emb[0][:8].round(3)}")

import pickle
with (idx / "bm25.pkl").open("rb") as fh:
    bm25 = pickle.load(fh)
print(f"\nBM25: {bm25.corpus_size} docs, avgdl={bm25.avgdl:.1f}, vocab={len(bm25.idf)} terms")
print("sample idf:", dict(list(sorted(bm25.idf.items(), key=lambda kv: -kv[1]))[:5]))

# %% ------------------- 5. LAYER 5: dense retrieval by hand (embed + cosine)
# First run loads the ONNX model from rag/.cache/models (~130MB, already local).
from plotline_rag.index.embed import FastEmbedder

QUERY = "how long should a short-form video be to keep viewers watching"

embedder = FastEmbedder(model_name=MODEL_NAME)
q = embedder.embed_query(QUERY)  # returns a normalized 384-dim float32 vector

dense_scores = emb @ q  # both sides normalized → dot product IS cosine
dense_rank = np.argsort(-dense_scores)
print(f"query: {QUERY!r}\n\ndense (cosine) top 5:")
for i in dense_rank[:5]:
    print(f"  {dense_scores[i]:.4f}  {ids[i]}  {records[i]['title']}")

# %% ----------------------------- 6. LAYER 6: BM25 retrieval by hand
from plotline_rag.index.build import tokenize

tokens = tokenize(QUERY)
print("tokenized query:", tokens, "\n")
bm25_scores = bm25.get_scores(tokens)
bm25_order = np.argsort(-bm25_scores)
print("BM25 top 5:")
for i in bm25_order[:5]:
    print(f"  {bm25_scores[i]:.4f}  {ids[i]}  {records[i]['title']}")

# %% -------------------- 7. LAYER 7: reciprocal-rank fusion, exactly theirs
# hybrid.py line ~105: score_i = 1/(60+rank_dense) + 1/(60+rank_bm25),
# then normalized by the ceiling 2/61 so a double-#1 scores 1.0.
d_rank = {i: r for r, i in enumerate(dense_rank)}
b_rank = {i: r for r, i in enumerate(bm25_order)}
fused = {i: 1.0 / (60 + d_rank[i]) + 1.0 / (60 + b_rank[i]) for i in range(len(ids))}
ceiling = 2.0 / 61.0
print("RRF-fused top 5 (dense rank + bm25 rank → fused):")
for i in sorted(fused, key=fused.get, reverse=True)[:5]:
    print(f"  fused={min(1.0, fused[i]/ceiling):.4f}  (dense #{d_rank[i]+1}, bm25 #{b_rank[i]+1})"
          f"  {ids[i]}  {records[i]['title']}")

# %% --------------- 8. LAYER 8: the real engine in-process, filters and all
from plotline_rag.schema import SearchRequest
from plotline_rag.search.hybrid import HybridSearch

engine = HybridSearch(root=CACHE_DIR, embedder=embedder)
req = SearchRequest(query=QUERY, k=3, corpus="best_practice",
                    filters={"platform": ["youtube"], "tier": ["seed"]})
for res in engine.search(req):
    print(f"  {res.score:.4f}  {res.source_id}  [{res.tier}] {res.title}")
    print(f"          components: dense={res.components.dense} bm25={res.components.bm25}")
# Try changing filters: platform=["linkedin"], claim_type=["heuristic"], k=10 …
# Filters are ANDed across dimensions, ORed within one (see HybridSearch._matches).

# %% ----------------- 9. LAYER 9: the live HTTP contract plotline-api calls
# Needs the service up:  cd rag && make serve-s3 PORT=8788   (or bash run.sh)
import httpx

RAG = "http://127.0.0.1:8788"
try:
    print("health:", json.dumps(httpx.get(f"{RAG}/health", timeout=3).json(), indent=2), "\n")
    hits = httpx.post(f"{RAG}/search_corpus", timeout=10, json={
        "query": QUERY, "k": 3, "corpus": "best_practice",
        "filters": {"tier": ["seed"]}, "rerank": False,
    }).json()
    for r in hits["results"]:
        print(f"  {r['score']:.4f}  {r['source_id']}  {r['title']}")
    resolved = httpx.post(f"{RAG}/resolve_source_ids", timeout=10,
                          json={"source_ids": ["chunk:C0001", "chunk:C9999"]}).json()
    print("\nresolve:", resolved, " ← the fail-closed check every citation passes")
except httpx.TransportError:
    print(f"service not running — start it:  cd rag && make serve-s3 PORT=8788")

# %% ---------- 10. LAYER 10: aux sample corpora the app ALSO retrieves from
# Until asset:/stat:/trend: content lives in rag/, plotline-api routes those
# corpora to the devrag stub (:8787, hand-curated fixtures, honestly labeled).
DEVRAG = "http://127.0.0.1:8787"
try:
    assets = httpx.post(f"{DEVRAG}/search_corpus", timeout=5, json={
        "query": "ai tools hook", "k": 3, "filters": {"kind": "asset"},
    }).json()["results"]
    for a in assets:
        print(f"  {a['source_id']}  {a.get('platform','?'):16s} {a.get('title','')[:60]}")
    print("\nfixture files: devrag/fixtures/{assets,chunks,stats,trends}.json")
except httpx.TransportError:
    print(f"devrag not running — start it:  bash run.sh  (or uvicorn devrag.server:app --port 8787)")

# %% [markdown]
# WHERE TO READ NEXT (the whole retrieval path, ~600 lines total):
#   rag/src/plotline_rag/schema.py        — the wire contract as pydantic
#   rag/src/plotline_rag/index/build.py   — corpus → embeddings/bm25/manifest + S3/DDB sync
#   rag/src/plotline_rag/search/hybrid.py — filters → dense+bm25 → RRF (what you just did by hand)
#   rag/src/plotline_rag/store/{s3,ddb}.py— ETag cache · hash-diffed 1-WCU-throttled sync
#   rag/src/plotline_rag/service.py       — the FastAPI surface (:8788)
#   app/rag_client.py                     — how plotline-api consumes + routes all of it
#   app/validators.py                     — two-layer citation validation (subset + resolve)
#   kb/build_corpus.py                    — the authoring gate you'd use to add chunks
#   rag/docs/architecture-flow.html       — diagrams (open in a browser)
