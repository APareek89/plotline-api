# Handoff.MD — Plotline (plotline-api + plotline-web)
last-synced: `f770d06` (plotline-api) · plotline-web: `no commits yet`
> AGENT: read this fully at session start. FIRST run `git log --oneline <last-synced>..HEAD`
> in BOTH repos (~/Documents/plotline-api and ~/Documents/plotline-web) — reconcile
> anything changed underneath this file before trusting the state below. Then open with
> the pending points. This file is the single project brain for both repos.
>
> UPDATE on concrete events: before every checkpoint commit · end of every phase ·
> low-context signal (secondary). Snapshot, not journal; re-stamp `last-synced`.
> Cap: >~40 lines or ~15 ✅ → collapse ✅ into one "Shipped:" line.

## Compact summary of previous session
Built Phase 1 from the PRD (docs/PRD-v1.txt): plotline-api complete (schemas, CCS
engine anchored to PRD worked examples, server-side validators, agent pipeline with
mock mode, dev RAG stub on the frozen :8787 contract, 23 tests green, 20-seed evals
green). plotline-web ~60% (tokens, layout, home, context form, concept cards, SSE).

## Current state
- ✅ plotline-api: full plan pipeline works in MOCK_LLM=1 (`.venv/bin/pytest`, `python -m evals.run_evals`)
- ✅ Dev RAG stub serves sample fixtures via the frozen contract (uvicorn devrag.server:app --port 8787)
- ▶ plotline-web: remaining — studio/[seriesId] shell, plans page, my-space, phase-2 stubs; then e2e browser verify

## Pending points
- 🧑 USER: copy ANTHROPIC_API_KEY line into plotline-api/.env yourself; set MOCK_LLM=0 to go live
- 🧑 USER: edit Loop.MD goal + evals to make them yours
- 🤖 AGENT: finish plotline-web pages → verify e2e in browser → offer the Loop (first working draft)
- 🤖 AGENT: Phase-1 exit gate needs ≥55% of test users approving a plan in ≤2 regenerations — instrument regen counts (data exists in concept_state)

## Decisions
- 2026-08-21 — SQLite + local files for Phase-1 dev (no RDS/AWS in Claude Code lane) — PRD §7 env override; Codex owns plotline_* AWS
- 2026-08-21 — Dev RAG stub inside plotline-api serving the frozen :8787 contract with sample data — Codex's plotline-rag not landed; swap is a no-op
- 2026-08-21 — Models: sonnet-4-6 (plan/critique), haiku-4-5 (intake), env-overridable — PRD §7 table
- 2026-08-21 — MOCK_LLM mode: deterministic agents that still hit real retrieval + validators — full-pipeline dev without key
- 2026-08-21 — Ports: api 8600, web 3100, RAG 8787 (frozen) — avoid clashes with other local projects
- 2026-08-21 — DS: cobalt #2946E5 + Avenir Next system stack; Inter/#6933fa banned per PRD §12
- 2026-08-21 — Git checkpoints: AUTO (user chose in power-coding setup, supersedes earlier propose-only rule for checkpoints)
