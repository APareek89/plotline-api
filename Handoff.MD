# Handoff.MD — Plotline (plotline-api + plotline-web)
last-synced: `dc5379c` (plotline-api) · `2d01541` (plotline-web)
> AGENT: read this fully at session start. FIRST run `git log --oneline <last-synced>..HEAD`
> in BOTH repos (~/Documents/plotline-api and ~/Documents/plotline-web) — reconcile
> anything changed underneath this file before trusting the state below. Then open with
> the pending points. This file is the single project brain for both repos.
>
> UPDATE on concrete events: before every checkpoint commit · end of every phase ·
> low-context signal (secondary). Snapshot, not journal; re-stamp `last-synced`.
> Cap: >~40 lines or ~15 ✅ → collapse ✅ into one "Shipped:" line.

## Compact summary of previous session
Phase 1 built from the PRD (docs/PRD-v1.txt) and verified end-to-end in the browser:
context form → intake → inspiration cards (sample-data label) → plan pipeline (plan →
red-team → refine → options) → concept cards with CCF scoresheets → approve/regenerate/
reorder → Plans tab → My Space (profile + performance paste). 23 tests, 20/20 evals.

## Ownership (changed 2026-08-22)
- Claude owns the WHOLE stack in THIS repo: api + rag/ (retrieval service) + kb/ (corpus
  pipeline) + devrag. Codex gets specific delegated tasks only — no standing lane.
- ~/Documents/plotline-rag is SUPERSEDED by rag/ here (moved 2026-08-22, no git history
  existed). Safe for the user to archive/delete.
- AWS fence (binding, from Codex's handoff): profile `plotline-agent`, region ap-south-1,
  `plotline-*` buckets + `plotline_*` tables ONLY; never touch aarav-*/Handson. DynamoDB
  stays PROVISIONED 1RCU/1WCU. No LLM calls inside rag/.

## Current state
- ✅ Shipped: full Phase-1 planning product + integrated RAG/KB (see git log & Learning.MD)
- ✅ rag/ (S3+DynamoDB, hybrid RRF, :8788): RoutingRag routes chunk:* → rag :8788,
  asset:/stat:/trend:* → devrag aux :8787. Two-layer citation validation, seed gate,
  bounded retries. 53 tests (41 api + 12 rag), 20/20 evals, recall@5 10/10, live-verified.
- ✅ kb/ corpus pipeline: kb/sources/*.yaml → `make -C rag kb` → rag/data/best_practice.jsonl
  (validates ids/tiers/urls/text; official/expert tiers REQUIRE real http(s) urls; seed
  chunks pin hashes so sync stays no-op). Then `make -C rag index eval sync`.
- ▶ Runs in MOCK_LLM=1. Servers: `bash run.sh` (starts rag :8788 + devrag :8787 + api :8600) · web :3100 (`npm run dev`)
- ▶ Loop.MD status: offered (awaiting user's answer)

## Pending points
- 🧑 USER: copy the ANTHROPIC_API_KEY line into plotline-api/.env yourself; set MOCK_LLM=0 to go live, then run one real plan + the golden set
- 🧑 USER: answer the Loop offer; edit Loop.MD goal/evals to make them yours
- 🧑 USER: PRD open calls — trademark check on "Plotline"; pricing currency INR vs USD
- 🤖 AGENT: author the real curated corpus in kb/sources/ (target ≥60 chunks; official/expert
  tiers need real URLs), retire seed.yaml, set PLOTLINE_ALLOW_SEED_EVIDENCE=0, re-run evals
- 🤖 AGENT: port asset:/stat:/trend: corpora into rag/ (multi-corpus), drop devrag +
  PLOTLINE_AUX_RAG_URL (routing collapses to one backend)
- 🤖 AGENT: platform vocab drift — corpus uses "twitter", app uses "x"; normalize when
  seed.yaml retires (alias noted in kb/build_corpus.py)
- 🤖 AGENT: exit-gate instrumentation — approval-within-≤2-regens report from concept_state.regen_count

## Decisions
- 2026-08-21 — SQLite + local files for Phase-1 dev (no RDS/AWS in Claude Code lane) — PRD §7 env override; Codex owns plotline_* AWS
- 2026-08-21 — Dev RAG stub inside plotline-api serving the frozen :8787 contract with sample data — Codex's plotline-rag not landed; swap is a no-op
- 2026-08-21 — Models: sonnet-4-6 (plan/critique), haiku-4-5 (intake), env-overridable — PRD §7 table
- 2026-08-21 — MOCK_LLM mode: deterministic agents that still hit real retrieval + validators — full-pipeline dev without key
- 2026-08-21 — Ports: api 8600, web 3100, RAG 8787 (frozen) — avoid clashes with other local projects
- 2026-08-21 — DS: cobalt #2946E5 + Avenir Next system stack; Inter/#6933fa banned per PRD §12
- 2026-08-21 — Git checkpoints: AUTO (user chose in power-coding setup, supersedes earlier propose-only rule for checkpoints)
- 2026-08-22 — Ownership takeover: rag+kb absorbed into plotline-api, Claude owns full stack, Codex delegate-only — user decision; AWS fence carried over binding
