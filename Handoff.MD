# Handoff.MD — Plotline (plotline-api + plotline-web)
last-synced: `e854b31` (plotline-api) · `f99ffdc` (plotline-web)
> AGENT: read this fully at session start. FIRST run `git log --oneline <last-synced>..HEAD`
> in BOTH repos (~/Documents/plotline-api and ~/Documents/plotline-web) — reconcile
> anything changed underneath this file before trusting the state below. Then open with
> the pending points. This file is the single project brain for both repos.
>
> UPDATE on concrete events: before every checkpoint commit · end of every phase ·
> low-context signal (secondary). Snapshot, not journal; re-stamp `last-synced`.
> Cap: >~40 lines or ~15 ✅ → collapse ✅ into one "Shipped:" line.

## NEXT SESSION — read docs/NEXT-SESSION-PROMPT.md FIRST
Addendum-03 landed in docs/ (owner's files, HIGHEST precedence): `PRD-addendum-03-ui.html`
(behavioral spec) + `marketing-studio-demo.html` (the DESIGN CONTRACT). The old generated
one is kept as `PRD-addendum-03-generated.html` — reference only, NOT authoritative.
CLEAN-SLATE FRONTEND: nothing from the old product survives (no Avatar/Creative/Content Studio,
no Plans/My Space, no old design system). Three tabs only: Campaign Studio · My Campaigns ·
My Brand. Backend is leveraged, not rebuilt.

## WHERE IT STOPPED — start here (2026-08-24)
Chair + full flow verified, AND the pre-pivot app is DELETED from both repos (-3037 lines
api, -2600 web). Remaining: brand_profile storage+routes w/ bidirectional pre-fill,
reference_media[] (user reference > template), campaign archive/restore/rename/delete,
then the FRONTEND REBUILD to docs/marketing-studio-demo.html (My Campaigns is still a ROW
LIST — §02 wants a folder-card GRID; My Brand tab does not exist yet and is deliberately
NOT in the nav, since a link that 404s is worse than a missing one). New acceptance checks
still to write: brand pre-fill both directions, reference-media priority, grid
archive/restore.

## WHAT WAS DELETED (2026-08-24) — do not resurrect
api: app/{thread,creative,orchestrator,commands}.py, app/agents/mock.py, evals/,
tests/test_{addendum,creative,pipeline_mock}.py, and 19 routes (/api/series/*,
/api/post-cards/*, /api/diy/*, /api/runs/*, inspiration). web: /avatar /creative /plans
/space /studio (Content Studio) /studio/[seriesId], 5 components, the LEGACY nav
dropdown, api.series.*. `/` now redirects to /studio/campaign.
KEPT ON PURPOSE: **app/ccs.py + app/validators.py are LIVE** (validators imports ccs; the
council needs the CCF weights) — so tests/test_ccs.py and tests/test_validators.py were
KEPT too, against the old plan to delete them: deleting a test whose module survives just
removes coverage from live code.
NEW: **app/threadkit.py** — neutral thread plumbing (_say/_actions/_thread_lock/_working/
_pending/_dispatcher/_flagged_ids/_merge_feedback/_niche_of/CREDIT_USD/_asset_url). This
existed so campaign.py stopped importing from the drivers it replaces. `_working` and
`_pending` are shared MUTABLE state — always import them, never redefine, or drivers stop
seeing the same object.

## REAL-MODE BUG CLASS #3 — silent truncation (fixed 2026-08-24)
The chair was NOT failing on prompt size as predicted. Root cause found by probe:
`thinking` tokens share the `max_tokens` budget, the response came back
`stop_reason=max_tokens`, and `runner._llm_call` returned the truncated text WITHOUT
checking stop_reason — so `json.loads` blamed syntax ("Expecting ',' delimiter") and hid
a pure overflow. Fixes: `AgentTruncated` raised on stop_reason=max_tokens (retryable, and
the retry now tells the model it was CUT OFF, not invalid); all text blocks joined (was
first-only); `config.MAX_OUTPUT_TOKENS` 16000→32000; **streaming is now mandatory** (the
SDK refuses non-streamed calls whose budget implies >10min); raw failing body persisted to
`data/runs/raw/` — that capture is what made the next bug findable in one run, not three.
Then bug #4, same species as #1/#2: R2 receipt is a LITERAL substring check but the prompt
described it in prose, so the planner wrote "camera holds on the screen" (fails) instead of
"on-screen" (passes). Prompt now has the lexicon INJECTED from `validators._RECEIPT_CUES`
via prompt_replacements, so prompt and check cannot drift. planner 1.2.0→1.3.0.

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
- ✅ 101/101 tests (23 in test_marketing.py — 19 acceptance floor + 4 new: truncation never
  parsed as complete, multi-block joins, prompt/validator lexicon drift guard, R2 floor)
- ✅ **FULL v2 FLOW VERIFIED END-TO-END ON REAL MODELS + REAL FAL** (campaign srs_5e7715873a,
  thread thr_42eb12a760, ad card `ad_faa4f000b5`, campaign status LIVE). Every agent passed
  first try except one legit self-correction: planner 93s/1 attempt (R2 fix held) → 3 seats
  1 attempt each → **chair 378s/1 attempt, 3 citations** → refine (2 flagged; options_refine
  needed 2 attempts, caught by "untouched options must come back byte-identical") → 2nd
  council → options (CCS 83/70/62, 0 killed) → approve o1 → templates HONEST SKIP ("No
  templates in the library yet") → detail v1 (claims_used ⊆ confirmed claims, style_ref null)
  → refine → v2 (CTA only, changes[] documents it, rest byte-identical) → model-confirm
  ($0.24 shown BEFORE generate, model named once w/ reason, single-vs-variants asked) →
  single → 3 real 928×1152 PNGs on nano-banana-2 → accept → Ad Card → mark-live.
  **Credits reconcile exactly: total_cost_credits 2.4 == campaign_spend() 0.24 USD / 0.1.**
  generation_log: 3 rows (model, cost, prompt, seed, asset_id) — audit reachable.
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
