# Handoff.MD — Plotline Marketing Studio (plotline-api + plotline-web)
last-synced: `90abec5` (plotline-api) · `2797e3d` (plotline-web)
> AGENT: read this fully at session start. FIRST run `git log --oneline <last-synced>..HEAD`
> in BOTH repos (~/Documents/plotline-api, ~/Documents/plotline-web) and reconcile before
> trusting anything below. Then open with the pending points.
> UPDATE before every checkpoint commit and at end of phase. Snapshot, not journal.
> Cap: >~40 lines or ~15 ✅ → collapse ✅ into one "Shipped:" line.

## LOCAL ONLY (decided 2026-08-25)
Render autoDeploy is **off** on both services; nothing is pushed. `origin/master` is
behind local. Both Render services are still RUNNING with real ANTHROPIC_API_KEY +
FAL_KEY, publicly, **no auth** — see P0-2.
Service ids (verified 2026-08-26, both `not_suspended`):
`plotline-api` `srv-da4pufqfngtc73col7rg` → https://plotline-api-apyq.onrender.com ·
`plotline-web` `srv-da4pujrl550s738l6uag` → https://plotline-web.onrender.com

## SPEC PRECEDENCE
**v3** (`docs/v3-ENHANCEMENT-BRIEF.md` + `v3-ARTIFACT-SPEC.md` + `v3-COUNCIL-DOCTRINE.md`) >
`docs/PRD-addendum-03-ui.html` (owner, behavioral) + `docs/marketing-studio-demo.html`
(owner, THE DESIGN CONTRACT) > `docs/SYSTEM-PROMPT-v2-marketing-studio.md` > Addendum-02 >
Addendum-01 > PRD-v1. `PRD-addendum-03-generated.html` is reference only, NOT authoritative.

## ✅ SHIPPED — v3 and v4 (see git log + Learning.MD for the detail)
**v3** (2026-08-25): all ten build-order steps — council doctrine v3.1.0 + ONE Marketing
Expert · `ReviewPolicy`/settings/presets · `campaign_brief` · `hook_rack` + W1-W3 ·
`ShotBoard` + B1-B4 · `style_block` · `canon_sheet` ×4 · `keyframe_board` + hard gate ·
`qc_report` + rights ledger · `TakeReject` + `variant_matrix` · stage checkpoints.
Owner decisions: RAG removed from the council only · canon workspace-global · final
stitch deferred.

**v4 interaction pivot** (2026-08-26): the card path is GONE — conversation is the only
way in. Media routes through PixelBin with fal as fallback. Every CTA answered in the
chat. Two panes, five agent-driven tabs. Blue and white only. See below.

## v4 — CURRENT SHAPE
Flow: name → **intake** (conversation) → brief → options → templates(+style) → script
(video only) → board → canon → keyframes (hard gate) → generate → creative → qc → done.
`"paths"` and `"cards"` no longer exist as stages.

- **Naming is the only form in the product.** `/studio/campaign` lands on it;
  `/campaigns` is the browse GRID (Current/Archived, search, sort, delete).
- **Every CTA lives in the composer.** `AgentMessage.question` is an `AgentQuestion`
  with tappable options. Options are LIFTED from each artifact's own `actions` in
  `threadkit._options_from()` — declared once, rendered twice, so they cannot drift.
  ⚠️ It lifts from EVERY actioned artifact in the turn and labels carry the artifact id
  when more than one is in play. Taking only the last one made options 1 and 2
  unapprovable — see Learning.MD 2026-08-26.
- **The right panel is READ-ONLY.** `readOnly` on `CampaignArtifactCard` sets a context
  AND a pointer-events wrapper; bespoke button rows exist in several cards and gating
  them by hand is how one stays live. ActionRow prints "waiting on you: …" so a blocked
  card never looks finished.
- **Claims confirmation moved to a question** (multi-select, consequence stated inline,
  confirming none still confirms). The Brand card was its only home before.
- **Media:** `app/media.py` routes PixelBin → fal. Never falls back on a POLICY refusal —
  a declined prompt is a real answer. Audio goes straight to fal (PixelBin has no TTS).
  REST contract in `app/pixelbin_client.py`, read off `@pixelbin/admin`, not guessed.
- **Web search:** Anthropic's NATIVE tool, on the INTAKE agent only. Never the planner or
  council — a web result must not become the evidence behind a claim. Queries captured
  into `RunLog.searched` and shown, so "searched the web" is never an empty claim.
- **Palette:** ok/warn/danger keep their token NAMES but resolve to accent / secondary /
  INVERTED white. Status reads by weight, not hue.

### ✅ QA — walked in the browser on real Haiku, 2026-08-26
name → intake (one message filled product+campaign+brand) → web search corrected an
invented spec → claims gate → brief → options → templates(skipped) → **script**. 8 agent
calls, ~$0.30, well under the $2 budget. Tabs auto-switched; brief and script both render
in full detail. **167 tests green** (floor was 83).

## v5 — IN FLIGHT (`docs/v5-MASTER-PROMPT.md` + the owner's addendum)
Seven stages, a Code QA gate after each, Stage 7 (paid) gated on a morning go-ahead.
Owner's visual target is `~/Documents/Reference material/`. **Floor: 190 tests.**

- ✅ **Stage 0** — 183 tests green at `7a806ce`, typecheck clean, `MOCK_MEDIA=1`,
  `/observability` renders both lenses (agents 300 · media 51 · $1.52 lifetime).
- ✅ **Stage 1 — plural references** (`e588592`). `media.generate()` and
  `pixelbin_client.generate()` take `image_urls: list[str]`; over-supply is trimmed
  ONCE in the router against `config.MEDIA_REF_SLOTS` — the same table B3 lints —
  and every dropped ref comes back in `dropped_refs` with its reason. B3 had been
  policing a capacity the client could not reach since v3. `config.media_key()` /
  `config.ref_slots()` are now the one resolver both sides read; do not add a second
  cap in the client. 187 tests.
- ✅ **Stage 2 — reference sheets** (`90abec5` + web `2797e3d`). ONE labelled sheet per
  canon kind, not one render per view. Layout first, negatives second; `locks` and
  `risk_notes` injected from campaign data, never restated. Owner decision: sheets
  render at **1K** (`config.IMAGE_RESOLUTION_DEFAULT`) on the FINAL tier — draft is
  nanoBanana v1, which declares no output_resolution and cannot make legible panel
  labels. **No angle question** (owner addendum); the gate states the resolution and
  the saving, and offers a priced 2K `resheet_canon` that drops the library copy first
  so an upgrade cannot re-bill the same sheet. Coverage now means COMPOSED, not
  verified — the chip reads "N views in one sheet" and the card shows the image.
  190 tests.
- ⏭ **Stage 3 next** — bind the approved sheet's `sheet_url` into every keyframe render
  for shots that reference its canon id. Both halves it needs now exist.
- 🔴 **MOCK_LLM=1 cannot complete an intake** — found driving Stage 2. `mock_campaign_intake`
  never learned `assume_mode`, and the assume pass sends `message: ""`, so the mock
  returns the context unchanged; `_assume_and_proceed` then offers **Start**
  unconditionally and Start refuses ("Not ready yet"). The free rung of the loop
  therefore reaches no stage past intake, and every zero-spend QA becomes a paid one.
  Point two bites in REAL mode too: a model that under-fills in assume mode gives the
  same dead-end CTA. NOT fixed — the fix is the owner's call. See Learning.MD.
- ⚠️ Stage 6's walk line still says "canon (sheets, angle choice)" — that choice is
  gone; the line is wrong.
- ⚠️ QA gate now also requires: **type like a person** — 3+ short fragments through any
  conversational stage before calling it done, never one well-formed sentence.

### 🔴 WHAT IS STILL MISSING
0. **fal cannot carry image references on the IMAGE path** — found wiring Stage 1.
   Its video model seeds from ONE `image_url`; its image path here is text-to-image
   with no verified reference field. Both now REPORT what they could not carry, so a
   PixelBin→fal failover no longer silently renders something else — but a failover
   mid-campaign still means weaker consistency than the primary. Wire fal's reference
   field from its real schema, or accept the degradation knowingly.
1. **fal AND PixelBin are both untested for real.** `MOCK_MEDIA=1` throughout; no real
   image or video has rendered under v3 or v4. Owner is adding `PIXELBIN_API_TOKEN`.
2. **QA stopped at script by design.** board → canon → keyframes → generate → creative →
   qc are unexercised in v4; they passed under v3 and the stage machine is unchanged.
3. **The reject UI is server-side only.** `reject_<cause>_<slot>` works and is tested, but
   the five causes are not offered as options yet.
4. **Canon views use the draft tier and a generic per-view prompt.**
5. `keyframes_per_shot` is stored and shown but not consumed by the renderer loop.
6. **31 QA campaigns are still in the dev DB.** The grid can delete them now — not done
   for you, since that is your data.

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
- ✅ Shipped earlier: v2 verified end-to-end on real models + real fal (Ad Card
  `ad_faa4f000b5` LIVE, credits reconciled); pre-pivot app deleted (−4.8k lines);
  rumination on LangGraph; observability surface. See git log + Learning.MD.
- ✅ **190 tests pass** (floor was 83; 183 before v5). Run `.venv/bin/python -m pytest` with **NO path
  argument** — passing `tests/` overrides `pytest.ini`'s `testpaths = tests rag/tests`
  and silently skips 12. `tests/test_marketing.py` holds the acceptance floor: never
  weaken one; re-point only when an INVARIANT changed by owner decision, and say so.
- ✅ Local stack: api :8600 · rag :8788 · devrag :8787 · web :3100. `MOCK_LLM=0`,
  real key in `.env`.
  **Start it — ONE command:** `cd ~/Documents/plotline-web && npm run dev`. Its `predev`
  runs the API repo's `run-detached.sh`, which is idempotent, so this brings up all four
  from cold. `npm run stack:status` / `stack:stop` for the rest.
  `bash run.sh` still works but runs in the FOREGROUND and
  traps EXIT to kill devrag — right for a terminal you are watching, fatal for anything that
  starts it from a tool call, because the whole stack dies with that shell.
  `bash run-detached.sh --status | --stop` for the rest.
  ⚠️ Symptom when the stack is down: **"Failed to fetch" / "Can't reach the API"** in the
  browser. It is ops, not an app bug — check `--status` FIRST before debugging anything.
- ✅ Observability = its own nav tab `/observability` (NOT per-thread). Gated on
  `PLOTLINE_DEBUG_OBSERVABILITY=1` (already in `.env`) + `NODE_ENV !== production`.
- ▶ Loop.MD status: offered (awaiting your answer)

## PENDING — start here
- ▶ **Stage 2 of `docs/v5-MASTER-PROMPT.md`**, read with the owner's addendum. Awaiting
  the resolution/credit answer above before the cost line can be honest.
- 🔴 **Consistency gap, the important one — Stage 3, still open.** canon → keyframe is
  TEXT ONLY: `CanonSheet.locks` are strings in a prompt and the canon IMAGES are never
  passed as references, so the product in the keyframes need not match the sheet the
  user approved. `_render_keyframe()` calls `generate()` with no references at all.
  keyframe → video IS image-consistent (verified live). The client half is now done —
  `image_urls[]` exists and the slot budget is honoured — so Stage 3 is purely a matter
  of binding the approved sheet's URL to the shots that reference its canon id.
- 🟠 **Reference-material questions the master prompt does not settle** (raised before
  Stage 2, unanswered):
  1. `CANON_COVERAGE` names product views `front/rear/left/right/top/base`; the owner's
     sheet names them lateral/medial profile, 3/4 front/rear, top-down, outsole, heel
     detail, midsole detail. `coverage`, `complete` and `validate_canon_sheet` all key
     off the former. Whose vocabulary wins is an invariant call, not a cosmetic one.
  2. The 3×3 storyboard sheet in the reference shots cannot be the video seed — Stage 3
     needs per-frame URLs. Treating it as a review artifact ALONGSIDE per-shot stills
     unless the owner says otherwise.
  3. The artifact detail rail is type-dependent in the reference: images get Prompt +
     Settings + approve/reject; text artifacts get only Name + Download + Delete.
     Approve is a TOGGLE ("Mark as approved" ⇄ "Remove approved status"). There is also
     a version chip and prev/next paging the master prompt does not mention.
- ✅ ~~P0-1 seed evidence~~ — `PLOTLINE_ALLOW_SEED_EVIDENCE=0` in `.env` (2026-08-25);
  `/health` reports `allow_seed_evidence: false`. Note `.env` is gitignored, so this is a
  LOCAL fix — **Render still has `allow_seed_evidence: true`, confirmed live 2026-08-26.**
- 🔴 **P0-2 Render live, real keys, no auth** — anyone with the URL spends real money.
  RPN 224. **Owner decided 2026-08-26: leave as-is for now.** Do not re-litigate; raise
  only if it changes. Verified live that day against the public `/health`, unauthenticated,
  HTTP 200 — the exposure is WORSE than "real keys": `mock_llm: false` with
  **sonnet-4-6 on BOTH planner and feedback** (the expensive slots, not Haiku),
  `media_mock: false` (**real fal media generation**), and `allow_seed_evidence: true`.
  `plotline-web` is public too, so there is a working UI pointed at it — clickable, not
  just curl-able. Options if revisited: suspend (seconds, reversible), add auth, or mock.
  NOTE: flipping `MOCK_LLM=1` would ALSO open `/api/agent-runs` publicly (P1-3) — verified
  in `app/main.py::_debug_enabled()`, which is `MOCK_LLM or PLOTLINE_DEBUG_OBSERVABILITY`;
  that endpoint leaks full prompts and payloads, and mocking would not fix seed evidence.
- ✅ ~~CHECKPOINTS~~ — done. Every graph node checks for a checkpoint before running and
  writes one after returning, so a node that raised leaves nothing and only a COMPLETE
  result is restored. `retry` resumes; a new rumination clears stale rows.
- 🟡 **The reviewer has no UI surface.** `Feedback`/`SeatReview` are never emitted as
  artifacts (CoverageChip renders only on legacy concept/plan/confidence_card, which the v2
  flow never produces). So doctrine §1's honest-label instruction has no target, and a
  stakeholder seat produces a review nobody can see. Owner declined the surface — small to add.
- 🟡 `MOCK_MEDIA=1` in `.env`. Flip to 0 **and** add `PIXELBIN_API_TOKEN` before media
  work — PixelBin is primary now, fal is the fallback. Neither is tested for real.
- 🟡 Corpus is still the real limit: 25 chunks, 0 with a real URL. `kb/sources/*.yaml` →
  ≥60 real-URL chunks. NOTE this now affects the PLANNER ONLY — the reviewer is frozen.
- ✅ ~~My Campaigns is a row list~~ — it is the folder GRID now, with delete. ~~Step 2's
  disabled prompt bar~~ — that whole screen is gone.
- 🤖 Addendum-03 leftovers: `brand_profile` storage+routes (My Brand hosts the Canon
  library; brand_profile would be a second section on it); `reference_media[]` (user
  reference > template); campaign rename, and a REAL archive — the Archived tab currently
  derives from status rather than a stored flag, and says so.
- 🤖 31 QA campaigns still in the dev DB. The grid deletes them now; left for you.
- ✅ ~~Loop.MD Rung 1 said `pytest tests/`~~ — fixed 2026-08-26; it now says no path argument.


## VIDEO FLOW — RESOLVED by v3
Scenes/shots split, character plan and keyframe/motion split all landed as `ShotBoard` +
`CanonSheet` + `KeyframeBoard`; checkpoints are done. **Stitching is the one explicit
DEFERRAL** — the owner chose checkpoints over it, so today we hand over N clips rather
than one film. ffmpeg concat + an audio bed is our equivalent when it is picked up, and
the pattern worth copying is that stitching DEGRADES honestly rather than crashing. The
full reference-only study of gofynd/pixelbin@marketing-studio-service is in git history
(nothing of theirs landed here — their stack is Node/Remotion, ours Python/fal).

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
- 2026-08-26 — P0-2 deferred by owner: Render stays live, public, real keys. Logged so a
  future session does not re-raise it as new; it is a known accepted risk, not an oversight
- 2026-08-26 — **v4 interaction pivot (owner) — SHIPPED.** The campaign
  flow becomes conversation-first: (a) enrich the brief with Anthropic's NATIVE
  `web_search` tool — no new vendor or key, citations arrive in a shape the existing
  evidence validators can consume; (b) the structured 3-card detail path is removed
  ENTIRELY, cards and block routes included — the agent becomes the only way to populate a
  campaign, and this re-points ~30 tests by owner decision, not by weakening them;
  (c) the left campaign rail is deleted outright. The STAGES did not change — only how
  input is gathered. `claims_confirmed` was orphaned by (b) and now lives in a chat
  question, since intake may not self-confirm it. All of this is shipped and QA'd.

**Session efficiency (2026-08-26):** 🎯 ~70% feature · 🔧 ~15% support · 🔁 ~15% rework
(rework was two dead ends the suite could not see — both found within minutes of driving
the browser, both now pinned by tests that bite)
