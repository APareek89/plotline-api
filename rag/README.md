# plotline-rag

Local-first storage and hybrid retrieval for Plotline planning agents. The service embeds validated knowledge-base chunks with FastEmbed, combines dense cosine and BM25 rankings through reciprocal-rank fusion, and returns only resolvable `source_id` values.

## Architecture and limits

- S3 is the source of truth: `kb/best_practice.jsonl`, `index/embeddings.npz`, `index/bm25.pkl`, and `index/manifest.json`.
- DynamoDB table `plotline_chunks` stores one item per resolvable chunk, keyed by `source_id_full`.
- Search runs in process over a normalized NumPy `float32` matrix plus `rank-bm25`. Metadata filters are applied before fusion.
- FastAPI binds to `127.0.0.1:8787`; no cloud compute or vector database is used.
- The design ceiling is 20,000 chunks. RDS/pgvector is only a future scale-up path on a separately funded account.

Cost: **$0 AWS within the free tier**, approximately **130 MB one-time model download**, and **no LLM calls in this repository**. The table uses provisioned 1 RCU / 1 WCU. S3 remains far below its 5 GB free-tier allowance.

## Important seed-corpus notice

`data/best_practice.jsonl` contains 10 explicitly labeled development seed chunks because the separate `plotline-kb` output was not available during setup. Their URLs are `urn:plotline:seed:*`, so they cannot be mistaken for production citations. Replace this file with the validated `plotline-kb/out/best_practice.jsonl` before production use, then rerun `make index`, `make eval`, and `make sync`.

## Setup

Requirements: macOS Homebrew Python 3.12 and AWS CLI profile `plotline-agent` in `ap-south-1`.

```bash
cd ~/Documents/plotline-rag
cp .env.example .env
make venv
```

The virtualenv activation script exports its `certifi` CA bundle for the one-time model fetch and AWS HTTPS. `.env` is ignored by Git and contains no secret. This repository does not use an Anthropic key.

Create the fenced AWS resources idempotently if needed:

```bash
./scripts/bootstrap_aws.sh
```

The scoped identity cannot set bucket-level public-access-block configuration. The bucket is created with AWS's default private ACL; the project never creates public ACLs or bucket policies.

## Build, test, evaluate, and sync

```bash
make test             # moto-backed, zero real network
make index            # local corpus -> .cache artifacts
make eval             # required recall@5 >= 8/10
make sync-dry-run     # validates without AWS writes
make sync             # idempotent S3 + throttled DynamoDB sync
```

`make sync` uploads only when the manifest/corpus hashes change. DynamoDB compares each chunk hash, writes one item at a time, and sleeps according to the provisioned WCU. A 500-chunk first sync therefore takes minutes by design.

## Serve

```bash
make serve             # auto: S3 after first sync, local before it
make serve-offline     # cache only; no AWS call
make serve-s3          # require S3 refresh and DynamoDB validation
```

Port 8787 is the contract default. If another local service already owns it, use `make serve PORT=8788`.

Search:

```bash
curl -sS http://127.0.0.1:8787/search_corpus \
  -H 'content-type: application/json' \
  -d '{"query":"how long should a short be for retention","k":6,"filters":{"platform":["youtube"],"tier":["seed"]},"corpus":"best_practice"}'
```

Validate citations:

```bash
curl -sS http://127.0.0.1:8787/resolve_source_ids \
  -H 'content-type: application/json' \
  -d '{"source_ids":["chunk:C0001","stat:S999"]}'
```

Health and cache age:

```bash
curl -sS http://127.0.0.1:8787/health
```

`rerank` is reserved in the request schema and defaults to `false`. V1 rejects `rerank=true` because no reranker is configured.

## Offline cache behavior

S3 downloads are cached under `.cache/` with ETags. When the network is unavailable, a previously complete cache remains usable. Artifact SHA-256 values and the corpus hash are verified before the BM25 pickle is loaded.

## Teardown

The teardown script targets only the two resources created by this project and uses `plotline-agent` on every call:

```bash
./scripts/teardown_aws.sh
```

It empties and deletes `plotline-kb-apsouth1`, then deletes `plotline_chunks`. This cannot be undone.

See [docs/ARCHITECTURE_FLOW.md](docs/ARCHITECTURE_FLOW.md) for the runtime and indexing flows.

