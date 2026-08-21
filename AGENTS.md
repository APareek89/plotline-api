# plotline-api — agent instructions

Repo scope (PRD §7 build handoff): orchestrator + agents + validators + Next.js sibling
(`../plotline-web`). Consumes the plotline-rag HTTP contract on :8787 — NEVER reimplement
retrieval/embeddings/KB ingestion here; never modify Codex's repos (plotline-kb, plotline-rag).
Agents get retrieval tools only — no network tools registered, ever.

Product brief: `docs/PRD-v1.txt` (canonical HTML: `docs/PRD-v1.html`).
Phase-1 env: local-only (SQLite, dev RAG stub) — no AWS in this lane, free tier only.

## Power Coding (auto — do not remove without asking the user)
At session start read Handoff.MD (project brain for BOTH repos); FIRST run
`git log --oneline <its last-synced sha>..HEAD` in both ~/Documents/plotline-api and
~/Documents/plotline-web and reconcile anything changed underneath it; then open with its
pending points. Update Handoff.MD before every git checkpoint commit and at the end of
every phase (low context is a secondary trigger) — snapshot not journal, re-stamp
`last-synced` with HEAD; if context was the trigger, tell the user to start fresh
("Refer to Handoff.MD in ~/Documents/plotline-api and begin"). When Handoff exceeds
~40 lines or ~15 ✅ items, collapse ✅ into one "Shipped:" line, detail to Learning.MD.
Log flow changes / user-reported bugs in Learning.MD (5-whys entry format).
Read Loop.MD every session and obey its `status:` machine — when the first working
draft is done (plan flow end-to-end in browser), ASK the user whether to turn the loop
on (disclosing the free/paid eval split); while `status: on`, run the FREE Loop.MD evals
after every meaningful change and report per-eval pass/fail. The golden set (MOCK_LLM=0,
paid) runs ONLY per consent.paid_evals (ask — offer at milestones, never auto).
Keep docs/mermaid/*.mmd current when the flow changes; staleness is mechanical via
`%% files:` headers (`node ~/.claude/skills/power-coding/scripts/sentinel.mjs scan`).
Obey .power-coding/config.json FMEA triggers: smart_suggest — offer a scan at a natural
pause when signals fire (big diffs, new API integration, auth/async/error-handling),
never twice for an unchanged HEAD. Per user preference: report all findings, FIX P0 only
unless asked. Sentinel enabled: silent four-lens sweep after major completions, one-line
flags only. Session Pulse enabled: 2-line effort split after major milestones.
Commit a git checkpoint at every working state and before any risky change
(consent.git_checkpoints: auto — commit + one-line announce).
Before starting a feature, build the smallest version that proves it works, checkpoint,
then extend. Any architecture-shaping change gets a plain-language delta proposal against
docs/mermaid/ and user approval BEFORE code. Log decisions in Handoff.MD's Decisions;
never silently reverse one.
