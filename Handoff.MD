# Handoff.MD — Plotline Marketing Studio (plotline-api + plotline-web)
last-synced: `14159f1` (plotline-api) · `a6bb767` (plotline-web)
> AGENT: read this fully at session start. FIRST run `git log --oneline <last-synced>..HEAD`
> in BOTH repos (~/Documents/plotline-api, ~/Documents/plotline-web) and reconcile before
> trusting anything below. Then open with the pending points.
> UPDATE before every checkpoint commit and at end of phase. Snapshot, not journal.
> Cap: >~40 lines or ~15 ✅ → collapse ✅ into one "Shipped:" line.

## LOCAL ONLY (decided 2026-08-25)
Render autoDeploy is **off** on both services; nothing is pushed. `origin/master` is
behind local. Both Render services are still RUNNING with real ANTHROPIC_API_KEY +
FAL_KEY, publicly, **no auth** — see P0-2.

## SPEC PRECEDENCE
**v3** (`docs/v3-ENHANCEMENT-BRIEF.md` + `v3-ARTIFACT-SPEC.md` + `v3-COUNCIL-DOCTRINE.md`) >
`docs/PRD-addendum-03-ui.html` (owner, behavioral) + `docs/marketing-studio-demo.html`
(owner, THE DESIGN CONTRACT) > `docs/SYSTEM-PROMPT-v2-marketing-studio.md` > Addendum-02 >
Addendum-01 > PRD-v1. `PRD-addendum-03-generated.html` is reference only, NOT authoritative.

## v3 — CONTRACTS DONE, RUNTIME NOT WIRED (build order in the brief §6)
Owner decisions 2026-08-25: RAG removed from the **council only** (planner keeps it) · canon
sheets **workspace-global** · stakeholder seats ship, but **the council is now ONE Marketing
Expert** (later owner decision, see below) · final stitch **deferred, checkpoints instead**.

**✅ Shipped (147 tests green; floor was 83).** Steps 1-10 of the brief as CONTRACTS:
council doctrine v3.1.0 + one reviewer · `ReviewPolicy`/settings API/presets ·
`campaign_brief` · `hook_rack` + W1-W3 · `ShotBoard` + B1-B4 · `style_block` ·
`canon_sheet` ×4 · `keyframe_board` + hard gate · `qc_report` + rights ledger ·
`TakeReject` + `variant_matrix` · **stage checkpoints** · the 5 new stages in
`CAMPAIGN_STAGES` + `advance_from()`. Step 1 was verified on real sonnet-4-6.

### 🔴 WHAT IS NOT DONE — the app does NOT run the new pipeline yet
The five new stages have schemas, validators, lints and gates, and `advance_from()` knows
their order — but **nothing produces their artifacts at runtime**. Specifically:
1. **No turn handlers.** `_dispatch` routes none of `brief|script|canon|keyframes|qc`.
   Existing handlers to copy the shape from: `_detail_turn`, `_templates_turn`.
2. **No prompts.** Need `campaign_brief.md`, `hook_rack.md`, `shot_board.md` (upgrade
   `campaign_planner.md`'s detail section), `canon_sheet.md`, `qc_report.md`.
   The W1 table MUST be injected via `validators.script_thresholds_text()`, never hand-copied.
3. **No web renderers.** `campaign-artifacts.tsx` renders 0 of the 7 new `ArtifactType`s.
   §12 rendering rules + per-artifact layouts are in `docs/v3-ARTIFACT-SPEC.md`.
4. **No My Brand tab / canon library page** (still deliberately absent from nav until it exists).
5. `ReviewPolicy` gates are enforced by `advance_from()` but **no stage handler reads
   `_pauses_at()` yet** — wire it as each handler lands.

### 📌 Prompt lessons from the paid runs — do not undo these
Three retry causes fixed by prompt, never by weakening a check: (a) the reviewer invented
ELEMENT names out of doctrine points (`one_idea_per_ad`) → the doctrine states the eight names
are a CLOSED set and D-points are lenses; (b) benchmarks appeared in fields far from the intro
rule → doctrine rule 2 carries a rewrite table and every prompt repeats it beside its OUTPUT
SHAPE; (c) `kill_flags` came back as objects → the output shape shows a bare string.
Haiku's chair went 3 attempts → 1; Sonnet needs none.
⚠️ **Sonnet is slow here** — the old chair alone took 477s. The single reviewer removes three
of the four council calls, which is the main reason it exists.
- ⚠️ Council output has **no UI surface at all** (Feedback/SeatReview are never emitted as
  artifacts; CoverageChip renders only on legacy concept/plan/confidence_card). So doctrine §1's
  "replace the coverage chip with an honest label" has no target, and a user-added stakeholder
  seat produces a review nobody can see. Owner declined a council surface for now.
- ⚠️ v3 silently drops the Handoff's stage-level **checkpoints** — and adds five stages, so the
  "transient error discards a full paid rumination" exposure got worse, not better.

## CURRENT STATE
- ✅ Shipped: v2 campaign flow verified END-TO-END on real models + real fal (Ad Card
  `ad_faa4f000b5` LIVE, credits reconciled exactly); pre-pivot app deleted from both repos
  (−4.8k lines); rumination ported to LangGraph; observability surface; 4 real-mode prompt
  bug classes fixed. See git log + Learning.MD.
- ✅ **83 tests pass.** `tests/test_marketing.py` holds the acceptance floor — never weaken one.
- ✅ Local stack: api :8600 · rag :8788 · devrag :8787 · web :3100. `MOCK_LLM=0`,
  `MOCK_MEDIA=0`, real key in `.env`. Start: `bash run.sh` (api+rag+devrag), `npm run dev` (web).
- ✅ Observability = its own nav tab `/observability` (NOT per-thread). Gated on
  `PLOTLINE_DEBUG_OBSERVABILITY=1` (already in `.env`) + `NODE_ENV !== production`.
- ▶ Loop.MD status: offered (awaiting your answer)

## PENDING — start here
- ✅ ~~P0-1 seed evidence~~ — `PLOTLINE_ALLOW_SEED_EVIDENCE=0` in `.env` (2026-08-25);
  `/health` reports `allow_seed_evidence: false`. Note `.env` is gitignored, so this is a
  LOCAL fix — Render still needs the same change whenever it is next touched.
- 🔴 **P0-2 Render live, real keys, no auth** — anyone with the URL spends real money.
  RPN 224. Decide: suspend, add auth, or flip to mock. NOTE: flipping `MOCK_LLM=1` would
  ALSO open `/api/agent-runs` publicly (P1-3) — the gate couples two unrelated concerns.
- 🔴 **CHECKPOINTS — the biggest reliability gap, owner-prioritised over stitching.**
  A rumination is planner → 3 seats → chair (→ refine repeats the council). Intermediate
  results live ONLY in memory, so one failure anywhere discards every completed node and
  the user pays again from zero. Observed live 2026-08-25: planner 209s + seats 120/137/139s
  all SUCCEEDED, then the chair's connection dropped and all four were binned. The
  `AgentTransport` retry fixes one node; it does not stop a later failure destroying
  earlier ones. v3 makes this worse by adding five stages. Fix = persist each stage's
  output as it completes and resume from the last good one.
- 🟡 **The council has no UI surface at all.** `Feedback`/`SeatReview` are never emitted as
  artifacts (CoverageChip renders only on legacy concept/plan/confidence_card, which the v2
  flow never produces). So doctrine §1's honest-label instruction has no target, and the
  shipped stakeholder-seat feature produces reviews nobody can see. Backend complete; owner
  declined the surface for now — small to add.
- 🟡 `MOCK_MEDIA=1` is set in `.env` from council testing — flip back before media work.
- 🟡 `/studio/campaign` Step 2 disables the prompt bar, so the brief-first path only works
  from the thread page. Same step, two affordances.
- 🟡 Corpus is still the real limit: 25 chunks, 0 with a real URL. `kb/sources/*.yaml` →
  ≥60 real-URL chunks. NOTE this now affects the PLANNER ONLY — the council is frozen.
- 🧑 **DECISION NEEDED — video flow reshape.** Proposed from the gofynd reference (below);
  items 1–3 reshape `CampaignDetail` + planner prompt = architecture-shaping, so it needs
  your approval BEFORE code. Item 4 is additive/safe.
- 🤖 Addendum-03 leftovers: `brand_profile` storage+routes + **My Brand tab** (deliberately
  NOT in nav until its page exists); `reference_media[]` (user reference > template);
  campaign archive/rename/delete; My Campaigns is still a ROW LIST — §02 wants a folder GRID.
- 🤖 QA finding: `/studio/campaign` Step 2 disables the prompt bar, so the brief-first path
  only works from the thread page. Same step, two affordances.
- 🤖 Dev DB is full of my QA campaigns ("Path A/B", "QA one image no claims", …). No delete
  route yet — clean directly if they bother you.
- 🤖 Corpus is the real limit: 25 chunks, **0 with a real URL**. `kb/sources/*.yaml` → ≥60
  real-URL chunks is what turns this from a working pipeline into grounded expertise.

## VIDEO FLOW — proposal (reference-only study of gofynd/pixelbin@marketing-studio-service)
Read for ARCHITECTURE ONLY; nothing copied, nothing of theirs lands here (their stack is
Node/Remotion, ours Python/fal). Their pipeline: inputs → ad-vector → strategy → assets →
scenes → shots → keyframes → video. Ours: options → templates → detail → generate → creative.
1. **Split scenes from shots** — plan narrative beats, then decompose to camera units. Gives
   a cheap place to critique the story before paying for frames.
2. **Character plan + safety pass** — `characters[]{character_id, description, role,
   outfits[]}`; every shot binds exactly ONE role (character+outfit OR product). We have no
   character primitive today; this is what "character shots" needs.
3. **Split keyframe/motion prompts with negatives + provider length pre-check** — and a
   motion-verb lexicon (same trick as our R2 receipt cues).
4. **Stitching** — we don't stitch at all today; we hand over N clips. ffmpeg concat + audio
   bed is our equivalent. Pattern worth copying: stitching DEGRADES honestly, never crashes.
5. **Checkpoints (biggest reliability win, not video-specific)** — stage-level resume. Today
   a transient error discards a full paid rumination (~25 min, real money).

## Decisions
- 2026-08-21 — SQLite + local files for dev; ports api 8600 / web 3100 / rag 8788 / devrag 8787
- 2026-08-21 — Models: sonnet-4-6 (plan/critique), haiku-4-5 (intake), env-overridable
- 2026-08-21 — Git checkpoints AUTO
- 2026-08-22 — Claude owns the full stack here (api + rag/ + kb/ + devrag); AWS fence binding:
  profile `plotline-agent`, ap-south-1, `plotline-*` buckets / `plotline_*` tables only
- 2026-08-25 — Rumination runs as a LangGraph StateGraph (`app/graph.py`); the LINEAR stages
  stay plain code on purpose — they are a state machine over user events, and a graph there
  adds ceremony without adding truth
- 2026-08-25 — Confirming claims GRANTS permission to make them; it does NOT gate the start.
  Brand has no required field and is defaulted at `begin_rumination`
- 2026-08-25 — Local only: Render autoDeploy off

**Session efficiency (2026-08-25):** 🎯 ~55% feature · 🔧 ~25% support · 🔁 ~20% rework
(rework was self-inflicted: unpinned langgraph broke the Render build; unisolated LOG_DIR
leaked test failures into observability; `npm run build` clobbered dev's `.next` three times)
