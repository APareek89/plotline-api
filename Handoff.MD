# Handoff.MD — Plotline Marketing Studio (plotline-api + plotline-web)
last-synced: `ed95581` (plotline-api) · `27c0ab8` (plotline-web)
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

## v3 — SHIPPED end to end, API and UI (build order in the brief §6)
Owner decisions 2026-08-25: RAG removed from the **council only** (planner keeps it) · canon
sheets **workspace-global** · stakeholder seats ship, but **the council is now ONE Marketing
Expert** (later owner decision, see below) · final stitch **deferred, checkpoints instead**.

**✅ Shipped (160 tests green; floor was 83).** All ten build-order steps:
council doctrine v3.1.0 + one reviewer · `ReviewPolicy`/settings API/presets ·
`campaign_brief` · `hook_rack` + W1-W3 · `ShotBoard` + B1-B4 · `style_block` ·
`canon_sheet` ×4 · `keyframe_board` + hard gate · `qc_report` + rights ledger ·
`TakeReject` + `variant_matrix` · **stage checkpoints** · the 5 new stages in
`CAMPAIGN_STAGES` + `advance_from()`. Step 1 was verified on real sonnet-4-6.

### ✅ THE PIPELINE RUNS. Verified over HTTP on real Claude.
Five turn handlers + four prompts + dispatcher routing. Flow: cards → **brief** → options →
templates(+style block) → **script** (video only) → **board** → **canon** → **keyframes**
(hard gate) → generate → creative → **qc** → done. `_pauses_at()` is read per stage;
`advance_from()` steps over `skip`ped work only.
- **QA image: 21/21 end to end.** QA video: brief/options/script/board all produced.
- Prompts inject their checks' constants: `script_thresholds_text()` (W1, with the
  ARITHMETIC not just the ceiling) and `ref_slots_text()` (B3). NEVER hand-copy either.
- The board is the source of truth; `detail` is its one-way server projection.

### ✅ THE UI IS BUILT. Walked in the browser end to end.
All six new artifact types render with working actions; **My Brand** ships with the canon
library grid and its nav entry. Verified live: the brief card draws its full definition list
with `single_message` largest, and clicking Approve advances the stage to options.
⚠️ **Two lists must agree** — `CAMPAIGN_ARTIFACT_TYPES` (routing) and the switch (rendering)
in `campaign-artifacts.tsx`. A type in the switch but not the list NEVER reaches the switch
and draws a placeholder with no buttons. A Python test reads the TypeScript and guards it.

### 🔴 WHAT IS STILL MISSING
1. **fal untested** — balance is low, so `MOCK_MEDIA=1` throughout. Generate/creative/
   keyframes/canon are verified for WIRING only; no real image or video under v3.
2. **The reject UI is server-side only.** `reject_<cause>_<slot>` works and is tested, but
   `CreativeSetCard` does not yet offer the five causes as buttons — a user can only re-roll.
3. **Canon views use the draft tier and a generic per-view prompt.** Good enough to prove
   coverage; a real sheet wants the sheet's own brief + locks in each view prompt.
4. **Style block has no editable card.** It rides on the board card as a read-only block;
   §3 asks for six editable rows and a copy action.
5. `keyframes` renders one frame per shot regardless of `keyframes_per_shot` — the setting
   is stored and shown but not yet consumed by the renderer loop.

### 📌 Prompt lessons from the paid runs — do not undo these
Three retry causes fixed by prompt, never by weakening a check: (a) the reviewer invented
ELEMENT names out of doctrine points (`one_idea_per_ad`) → the doctrine states the eight names
are a CLOSED set and D-points are lenses; (b) benchmarks appeared in fields far from the intro
rule → doctrine rule 2 carries a rewrite table and every prompt repeats it beside its OUTPUT
SHAPE; (c) `kill_flags` came back as objects → the output shape shows a bare string.
Haiku's chair went 3 attempts → 1; Sonnet needs none.
⚠️ **Sonnet is slow here** — the old chair alone took 477s. The single reviewer removes three
of the four council calls, which is the main reason it exists.

## CURRENT STATE
- ✅ Shipped: v2 campaign flow verified END-TO-END on real models + real fal (Ad Card
  `ad_faa4f000b5` LIVE, credits reconciled exactly); pre-pivot app deleted from both repos
  (−4.8k lines); rumination ported to LangGraph; observability surface; 4 real-mode prompt
  bug classes fixed. See git log + Learning.MD.
- ✅ **160 tests pass** (floor was 83). `tests/test_marketing.py` holds the acceptance
  floor — never weaken one; re-point it only when an INVARIANT changed by owner decision.
- ✅ Local stack: api :8600 · rag :8788 · devrag :8787 · web :3100. `MOCK_LLM=0`,
  real key in `.env`.
  **Start it:** `bash run-detached.sh` (all three, survives the shell that started them) +
  `npm run dev` in plotline-web. `bash run.sh` still works but runs in the FOREGROUND and
  traps EXIT to kill devrag — right for a terminal you are watching, fatal for anything that
  starts it from a tool call, because the whole stack dies with that shell.
  `bash run-detached.sh --status | --stop` for the rest.
  ⚠️ Symptom when the stack is down: **"Failed to fetch" / "Can't reach the API"** in the
  browser. It is ops, not an app bug — check `--status` FIRST before debugging anything.
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
- ✅ ~~CHECKPOINTS~~ — done. Every graph node checks for a checkpoint before running and
  writes one after returning, so a node that raised leaves nothing and only a COMPLETE
  result is restored. `retry` resumes; a new rumination clears stale rows.
- 🟡 **The reviewer has no UI surface.** `Feedback`/`SeatReview` are never emitted as
  artifacts (CoverageChip renders only on legacy concept/plan/confidence_card, which the v2
  flow never produces). So doctrine §1's honest-label instruction has no target, and a
  stakeholder seat produces a review nobody can see. Owner declined the surface — small to add.
- 🟡 `MOCK_MEDIA=1` is set in `.env` from council testing — flip back before media work.
- 🟡 Corpus is still the real limit: 25 chunks, 0 with a real URL. `kb/sources/*.yaml` →
  ≥60 real-URL chunks. NOTE this now affects the PLANNER ONLY — the reviewer is frozen.
- 🤖 Addendum-03 leftovers: `brand_profile` storage+routes (My Brand SHIPPED — it hosts the
  Canon library; brand_profile would be a second section on it); `reference_media[]`
  (user reference > template); campaign archive/rename/delete; My Campaigns is still a
  ROW LIST — §02 wants a folder GRID.
- 🤖 QA finding: `/studio/campaign` Step 2 disables the prompt bar, so the brief-first path
  only works from the thread page. Same step, two affordances.
- 🤖 Dev DB is full of my QA campaigns ("Path A/B", "QA one image no claims", …). No delete
  route yet — clean directly if they bother you.

## VIDEO FLOW — RESOLVED by v3 (kept for the rationale)
Items 1-3 (scenes/shots split, character plan, keyframe/motion split) landed as `ShotBoard`
+ `CanonSheet` + `KeyframeBoard`. Item 5 (checkpoints) is done. Item 4 (stitching) is the one
explicit DEFERRAL — owner chose checkpoints over it. Original study below.

### original proposal (reference-only study of gofynd/pixelbin@marketing-studio-service)
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
