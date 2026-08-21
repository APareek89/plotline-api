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
Phase 1 built from the PRD (docs/PRD-v1.txt) and verified end-to-end in the browser:
context form → intake → inspiration cards (sample-data label) → plan pipeline (plan →
red-team → refine → options) → concept cards with CCF scoresheets → approve/regenerate/
reorder → Plans tab → My Space (profile + performance paste). 23 tests, 20/20 evals.

## Current state
- ✅ Shipped: full Phase-1 planning product, both repos (see git log & Learning.MD)
- ▶ Runs in MOCK_LLM=1. Servers: devrag :8787 + api :8600 (`bash run.sh` or launch.json) · web :3100 (`npm run dev`)
- ▶ Loop.MD status: offered (awaiting user's answer)

## Pending points
- 🧑 USER: copy the ANTHROPIC_API_KEY line into plotline-api/.env yourself; set MOCK_LLM=0 to go live, then run one real plan + the golden set
- 🧑 USER: answer the Loop offer; edit Loop.MD goal/evals to make them yours
- 🧑 USER: PRD open calls — trademark check on "Plotline"; pricing currency INR vs USD
- 🤖 AGENT: when Codex's plotline-rag lands, stop devrag and point PLOTLINE_RAG_URL at it (contract-compatible, no code change)
- 🤖 AGENT: exit-gate instrumentation — approval-within-≤2-regens report from concept_state.regen_count

## Decisions
- 2026-08-21 — SQLite + local files for Phase-1 dev (no RDS/AWS in Claude Code lane) — PRD §7 env override; Codex owns plotline_* AWS
- 2026-08-21 — Dev RAG stub inside plotline-api serving the frozen :8787 contract with sample data — Codex's plotline-rag not landed; swap is a no-op
- 2026-08-21 — Models: sonnet-4-6 (plan/critique), haiku-4-5 (intake), env-overridable — PRD §7 table
- 2026-08-21 — MOCK_LLM mode: deterministic agents that still hit real retrieval + validators — full-pipeline dev without key
- 2026-08-21 — Ports: api 8600, web 3100, RAG 8787 (frozen) — avoid clashes with other local projects
- 2026-08-21 — DS: cobalt #2946E5 + Avenir Next system stack; Inter/#6933fa banned per PRD §12
- 2026-08-21 — Git checkpoints: AUTO (user chose in power-coding setup, supersedes earlier propose-only rule for checkpoints)
