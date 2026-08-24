# Handoff.MD — Plotline (plotline-api + plotline-web)
last-synced: `d640a86` (plotline-api) · `f99ffdc` (plotline-web)
> AGENT: read this fully at session start. FIRST run `git log --oneline <last-synced>..HEAD`
> in BOTH repos (~/Documents/plotline-api and ~/Documents/plotline-web) — reconcile
> anything changed underneath this file before trusting the state below. Then open with
> the pending points. This file is the single project brain for both repos.
>
> UPDATE on concrete events: before every checkpoint commit · end of every phase ·
> low-context signal (secondary). Snapshot, not journal; re-stamp `last-synced`.
> Cap: >~40 lines or ~15 ✅ → collapse ✅ into one "Shipped:" line.

## NEXT SESSION — read docs/NEXT-SESSION-PROMPT.md FIRST
Owner shipped Addendum-03 (2 files in ~/Downloads: content-engine-prd-addendum-03.html +
marketing-studio-demo_1.html) — copy both into docs/ and treat them as HIGHEST precedence.
CLEAN-SLATE FRONTEND: nothing from the old product survives (no Avatar/Creative/Content Studio,
no Plans/My Space, no old design system). Three tabs only: Campaign Studio · My Campaigns ·
My Brand. Backend is leveraged, not rebuilt. Stopped mid-run: council CHAIR was in flight on
campaign srs_5e7715873a / thread thr_42eb12a760 — finish that first.

## PIVOT (declared Fri 22 Aug, work starts next session)
Plotline → MARKETING STUDIO per docs/SYSTEM-PROMPT-v2-marketing-studio.md — the
GOVERNING brief for all new work. Precedence: v2 > Addendum-02 > Addendum-01 > PRD v1.
Re-map the existing machine (envelope, panels, validators, fal stack, RAG, council-
izing the feedback agent); invent nothing architectural. Produce its OUTPUTS in
order (Addendum-03 doc → schemas → agent prompts → UI copy map → acceptance checks)
BEFORE implementing. The Monday items below fold in: do the real-mode retest first
(the council/planner run on real mode), fal-spend decision stands.

## Monday pickup (2026-08-25) — state as of Fri 22 Aug EOD
- Feedback-1 (Google Doc) APPLIED app-wide: full-bleed layout (rail flush left, chat fills
  width, panel = 40%, nav spans window), context form fits one viewport, Plans/DIY densified.
  Doc archived at docs/feedback-01.txt reference; guiding principles: no dead space, clean formatting.
- REAL-MODE BUG FIXED (untested live): planner.formats failed because real Sonnet wrapped
  FormatOptions in the AgentMessage envelope (protocol rule 10 read too literally).
  Fix = runner.unwrap_envelope() + shared_policy rule 15 (v1.3.0) "server owns the envelope".
  → FIRST THING MONDAY: re-run a real-mode plan (MOCK_LLM=0) end-to-end to confirm.
- fal LIVE ON RENDER: FAL_KEY + MOCK_MEDIA=0 set on srv-da4pufqfngtc73col7rg — the PUBLIC
  demo now spends real fal money per generation (no auth on the app). Watch spend / consider
  reverting MOCK_MEDIA=1 or adding an auth gate.
- User has been testing real mode locally (threads "Real-mode smoke — 01" etc. in dev DB).

## Compact summary of previous session
Phase 1 built + RAG/KB integrated + **Addendum-01 applied** (docs/PRD-addendum-01.html —
chat-first Content Studio; supersedes v1 §12 Content Studio row). Browser-verified:
context blocks (+3 required positioning fields) → thread with pinned context summary →
inspiration → PASS 0.5 format options (format-library corpus citations) → concept
batches of 3 with verdicts/coverage → dual-path approvals (typed + buttons, same
events) → plan approve → Plans. Right artifact panel w/ Concept/Activity tabs.

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
- ✅ Addendum-01: AgentMessage/UserEvent envelope (§05), thread driver + command parser,
  R2/R3 validators + saturation guard + platform hard filter + evidence-coverage %/
  PROVISIONAL + niche gate + thin-plan escalation (§7.2/7.3), prompt deltas (§06),
  format-library corpus (15 curated chunks, synced to S3/DDB — 25 total), chat UI
  (§01-§04). 69 tests, 20/20 evals. Old stage UI kept at /studio/[seriesId] as the
  plan board (panel "Expand" target); demo-v2 html stale per §09.
- ✅ Addendum-02 CREATIVE STUDIO (docs/PRD-addendum-02.html = Phase-2 spec of record):
  fal.ai media client (queue API; FAL_KEY in .env from Projects/creative-qc-agent; probe-
  verified model ids NB/NB2/NBPro + veo3.1/fast/i2v + kokoro + minimax-02-hd), MOCK_MEDIA=1
  default (SVG/ffmpeg/WAV placeholders, $0; no-ffmpeg → poster fallback for Render).
  Creative thread driver: confidence card → route → script (per-shot edit) → voice
  (previews) → editable prompts (verbatim, cost-gated) → generate (seam QA + 1 free auto
  re-roll, per-asset reroll) → end-card → Post Card (one id, 3 surfaces) → next-concept
  stitch. Plans lifecycle Planned/In production/Ready/Posted + 72h results nudge. DIY at
  /creative. Real-fal verified: 1 nano-banana image + 1 kokoro TTS (~$0.04). 77 tests.
- ✅ DEPLOYED 2026-08-22: github.com/APareek89/{plotline-api,plotline-web} (private, branch
  master) → Render free/singapore: web https://plotline-web.onrender.com · api
  https://plotline-api-apyq.onrender.com (srv-da4pufqfngtc73col7rg api, srv-da4pujrl550s738l6uag
  web; owner tea-d3af7615pdvs73cqm9f0; key ~/.claude/secrets/render-api-key). Cloud runs
  MOCK_LLM=1 with devrag as primary+aux (sample data, no AWS/Anthropic in cloud);
  render-start.sh boots both. CAVEATS: free tier spins down (~50s cold start); SQLite on
  ephemeral disk — threads/series reset on redeploy; autoDeploy on push to master.
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
- 🤖 AGENT: Addendum leftovers — R6 freshness validator (needs trends corpus / plotline-curator §08); real-mode command parsing for free text beyond the deterministic grammar; context-edit re-run confirm flow; rebuild demo html on request
- 🤖 AGENT: Addendum-02 leftovers — final stitch (shots+VO → single final.mp4, needs ffmpeg concat), carousel/image-post/audio variants (§03 table — reel path built), credits-low banner (no fal balance API wired), consent gate stub, My Space Post-Card rows, real creative-director agent (mock only; prompts ready)

## Decisions
- 2026-08-21 — SQLite + local files for Phase-1 dev (no RDS/AWS in Claude Code lane) — PRD §7 env override; Codex owns plotline_* AWS
- 2026-08-21 — Dev RAG stub inside plotline-api serving the frozen :8787 contract with sample data — Codex's plotline-rag not landed; swap is a no-op
- 2026-08-21 — Models: sonnet-4-6 (plan/critique), haiku-4-5 (intake), env-overridable — PRD §7 table
- 2026-08-21 — MOCK_LLM mode: deterministic agents that still hit real retrieval + validators — full-pipeline dev without key
- 2026-08-21 — Ports: api 8600, web 3100, RAG 8787 (frozen) — avoid clashes with other local projects
- 2026-08-21 — DS: cobalt #2946E5 + Avenir Next system stack; Inter/#6933fa banned per PRD §12
- 2026-08-21 — Git checkpoints: AUTO (user chose in power-coding setup, supersedes earlier propose-only rule for checkpoints)
- 2026-08-22 — Ownership takeover: rag+kb absorbed into plotline-api, Claude owns full stack, Codex delegate-only — user decision; AWS fence carried over binding
