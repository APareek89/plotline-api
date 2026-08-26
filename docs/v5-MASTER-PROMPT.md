# v5 MASTER PROMPT — reference sheets, consistency, stitching

Supersedes `docs/v5-REFERENCE-SHEETS-PROMPT.md`. Paste everything below the line into a
fresh Claude Code session. Medium effort is fine — the context lives on disk
(Handoff.MD, Learning.MD, 179 tests), not in this prompt.

---

You are continuing the **Plotline Marketing Studio** build.
Repos: `~/Documents/plotline-api` (FastAPI + agents) · `~/Documents/plotline-web` (Next.js).

Work through the stages below **strictly in order**. After EVERY stage run the **Code QA
gate** and report to me before starting the next one. Do not batch stages. Do not skip
ahead. **Stage 7 spends real money and is BLOCKED until I say go in the morning** — you
will reach Stage 6, stop, and wait.

---

## READ FIRST (before any code)

1. `~/Documents/plotline-api/Handoff.MD` — the project brain. It carries `last-synced`
   shas: run `git log --oneline <sha>..HEAD` in **both** repos and reconcile before
   trusting anything in it.
2. `~/Documents/plotline-api/Learning.MD` — root causes in 5-whys form. **Read the last
   six entries.** They are all the same species and will save you a day.
3. `~/Documents/Reference material/` — MY TARGET for this phase. Open every screenshot
   and both `.md` files before writing a line.
4. `docs/v3-ENHANCEMENT-BRIEF.md` + `docs/v3-ARTIFACT-SPEC.md` — governing specs.

**Start the stack:** `cd ~/Documents/plotline-web && npm run dev` brings up all four
services (`predev` runs the API repo's `run-detached.sh`). `npm run stack:status` to
check. **Never `npm run build`** — it clobbers dev's `.next`. Use `npm run typecheck`.

---

## THE CODE QA GATE — run after EVERY stage, no exceptions

1. `cd ~/Documents/plotline-api && .venv/bin/python -m pytest` — **no path argument.**
   Passing `tests/` overrides `pytest.ini`'s `testpaths` and silently skips 12 tests.
   The count must be ≥ the floor you started the stage with. It never goes down.
2. `cd ~/Documents/plotline-web && npm run typecheck`.
3. **Write at least one test for what you just built, then verify it BITES** — stash the
   fix, watch the test fail, restore. A test that passes without the fix proves nothing.
   `git stash push -q <file>` / `git stash pop -q`.
4. **Drive the affected surface in the browser.** Five bugs in the last two sessions were
   invisible to a green suite and obvious within minutes of clicking. Five for five.
   Note: synthetic clicks sometimes do not reach React — if a click seems to do nothing,
   dispatch through the element's own handler before concluding the app is broken.
5. Commit with a message that says what broke and why, not what changed.
6. Update `Handoff.MD` (snapshot, re-stamp `last-synced`) and add a `Learning.MD` entry
   in 5-whys form if you found a root cause.
7. **Report to me: what you built, what the test proves, what you did NOT do.** Then stop
   and wait for my go-ahead on the next stage.

**Ground rules that outrank convenience**
- Never weaken a test or an invariant to make a change easy. Surface the conflict. Re-point
  a test only when an INVARIANT genuinely changed by owner decision, and say so in the commit.
- **Keep `MOCK_MEDIA=1` for Stages 1–6.** The full interaction contract — cost lines,
  gates, accept/re-roll — runs identically at zero spend.
- Ask before spending anything, ever.

---

## WHAT IS ALREADY TRUE — do not rebuild

- Conversation is the only way in. The card path is gone; naming is the only form.
- Every CTA is answered in the chat. `AgentMessage.question` is an `AgentQuestion` whose
  options are LIFTED from each artifact's `actions` in `threadkit._options_from()` —
  declared once, covering every actioned artifact in a turn.
- The right panel is READ-ONLY with five agent-driven tabs.
- Media routes PixelBin → fal. `app/pixelbin_client.py` holds a wire contract **verified
  by spending**: `Bearer base64(token)`, multipart fields named `input.<key>`,
  `veo31_generate`, a per-model capability table. Every comment in that file is a bug that
  reached production. Do not tidy it without reading them.
- Cost gates, the keyframe hard gate, and `STAGE_REQUIRES` preconditions hold.
- `.env`: PixelBin token live · `PLOTLINE_MODEL_SCRIPT` and `PLOTLINE_MODEL_BOARD` are
  Sonnet (Haiku burns its retries on the w/s and B1–B4 arithmetic) · everything else Haiku.

---

# STAGE 0 — Orient and pin the floor

No code. Read the four sources above. Then:
- Run the suite and typecheck; record the numbers.
- Confirm `MOCK_MEDIA=1` in `.env`.
- Open `/observability` and confirm both lenses (Agents, Media) load.

**Report:** test count, typecheck status, and — in your own words — what the reference
screenshots tell you the product should look like. If your reading differs from the
stages below, say so before building.

---

# STAGE 1 — Multiple reference images end to end

**The problem.** `media.generate()` takes a singular `image_url`, while
`config.MEDIA_REF_SLOTS` declares 2–6 reference slots per model and the board's B3 lint
polices against those numbers. **The lint enforces a capacity the client cannot use.**

**Build:** `media.generate()` and `pixelbin_client.generate()` take `image_urls: list[str]`.
The capability table already names the right field per model (`images` for nanoBanana,
`image_urls` for veo31) — send up to that model's declared slot count, and if more are
passed than the model accepts, **drop deliberately and say which**, never silently.

**Test:** a render for a model with 4 slots given 6 references sends 4 and reports the 2
it dropped. Verify it bites.

---

# STAGE 2 — Reference sheets: one image, many labelled views

**Look at `Screenshot 2026-08-26 at 6.20.46 PM.png`.** That is a product reference sheet:
eight labelled views (lateral profile, medial profile, 3/4 front, 3/4 rear, top-down,
outsole bottom, heel detail, midsole detail) on a white catalog background with thin
labelled dividers — **as ONE generated image, not eight.**

Three reasons, all of which matter:
- **Cost** — one credit instead of eight.
- **Consistency** — views generated in a single pass agree by construction. Eight separate
  calls do not.
- **Reusability** — the sheet becomes the reference image for everything downstream (Stage 3).

Today `_canon_turn` renders one image per view (7 renders on the last QA run). Change it
to render ONE sheet per canon kind with the views laid out and labelled inside it.

**Ask the angle question at the gate** (my instruction, verbatim): offer **1 angle** or
**all angles**, with the cost impact stated on each option. Use the existing
question-with-options mechanism — it is already the CTA channel. Never generate a sheet
without an explicit choice; that is the cost-gate invariant.

**The prompt technique to copy** is in the reference screenshot's Prompt panel: state the
layout, then negative constraints derived from my own uploaded image — *"the pale gray
diagonal stripe … is a graphic artifact on the photo and is NOT part of the shoe. Do NOT
include any gray diagonal band."* Product fidelity lives in the negatives.

**Test:** the canon turn issues ONE render per kind, not one per view; and choosing
"1 angle" quotes and renders less than "all angles".

---

# STAGE 3 — Close the consistency gap (the important one)

**The current chain:**

```
canon sheets (images, approved) --TEXT ONLY--> keyframes (images) --IMAGE SEED--> video
```

- `keyframe → video` IS image-consistent — `image_url=frame["url"]` seeds the clip from
  the approved still. Verified live.
- `canon → keyframe` is **text only**. `CanonSheet.locks` are strings in a prompt. **The
  canon IMAGES are never passed as references.** So the product in the keyframes need not
  match the product sheet I approved. This is the gap.

**Build:** the approved canon sheet's asset URL is passed as a reference into every
keyframe render for shots that bind that canon id. Uses Stage 1's `image_urls[]`.

**Test — this is the one that matters:** a keyframe render for a shot bound to `@product`
actually receives the product sheet's URL. Without this test it silently regresses to
text-only and nobody notices until the creative looks like four different shoes.

---

# STAGE 4 — Stitching

Today N clips are handed over and nothing stitches. Add ffmpeg concat plus an audio bed.

**The pattern to copy:** stitching **DEGRADES honestly, never crashes** — if one clip is
missing, deliver the rest and say which is absent. `ffmpeg` exists on this machine but NOT
on Render; `media.py` already degrades when it is missing (`_mock_video` falls back to an
SVG poster). Follow that precedent rather than inventing a second one.

**Test:** stitching two clips yields one file of the summed duration; stitching with one
clip missing still produces a file AND names the gap.

---

# STAGE 5 — Artifact detail view

**Look at `Screenshot 2026-08-26 at 6.26.07 PM.png` and `…6.24.32 PM.png`.** Clicking an
artifact opens a full-height reading surface with a right rail carrying:
- **Prompt** (Copy button, Show more for long prompts)
- **Name** (double-click to rename)
- **Settings** as read-only chips (model, aspect ratio, quality, resolution)
- Actions: **Mark as approved · Mark as rejected · Download · Delete**

Two constraints from this codebase:
- Approve/reject must fire the **same `UserAction` events the chat options fire**. Do not
  invent a second approval path — one fact, one representation. This repo has been bitten
  four times by exactly that.
- The panel stays read-only for anything that **spends**. Download, rename and delete are
  safe; a re-render is a cost event and belongs in the chat with its price attached.

**PHASE 2 — do not build now:** "Select & edit", inpainting, any in-place image editing.

---

# STAGE 6 — Full dry run at zero spend, then STOP

With `MOCK_MEDIA=1`, drive the entire flow in the browser as a user:

```
name → intake → claims → brief → options → templates → script → board
     → canon (sheets, angle choice) → keyframes → generate → creative → qc → done
```

Confirm: tabs auto-switch · every CTA is answerable in chat · cost gates quote before
every spend · the keyframe hard gate holds · stitching produces one file · the artifact
detail view opens on every type · `/observability` shows each node and each render.

**Then stop.** Report:
- the full walk, stage by stage, with what you saw
- **the exact paid plan for Stage 7**: how many images, how many videos, at what
  durations, and the credit arithmetic
- anything you are unsure about

**Wait for my go-ahead. Do not proceed to Stage 7 without it.**

---

# STAGE 7 — THE PAID RUN (BLOCKED until I say go)

**Budget — a hard ceiling, one run, no second attempt:**
- **$1–2 Anthropic**
- **50 PixelBin credits.** My credit model: nanoBanana = 1 credit per image; Veo 3.1 = 20
  credits or less per video.

**The run:** 2 shots · **two 3-second videos, stitched seamlessly into one 6-second film**.
Roughly 2 × 20 = 40 credits of video plus a handful of sheet and keyframe images — that is
the entire budget.

Before flipping `MOCK_MEDIA=0`:
- state the plan and the arithmetic, and get my confirmation
- have Stage 6 completed clean end to end

During the run, watch `/observability` → **Media** lens for provider and per-render spend.
If anything fails, **stop and report** rather than retrying — a retry is another 20 credits.

Afterwards: put `MOCK_MEDIA=1` back. Leaving it at 0 means the next accidental generate
costs money.

---

## Traps specific to this repo

- **A slot only ever read is a typo with a default.** `approved_option_json` was read by
  two turns and written by nothing for the whole of v3. Grep every new workspace key for
  its WRITE site before trusting it.
- **Read `node_input` before the validation errors.** An agent that fails its checks is
  often correct about the input it was handed; the errors only describe the OUTPUT's shape.
- **Two lists that must agree need something comparing them**, even across a language
  boundary — there are already Python tests reading TypeScript. Add one rather than
  hand-syncing.
- **`_rehydrate()` must learn every new artifact** whose payload a later stage depends on.
  It has caused two separate outages.
- **Never let a test read `.env`.** One did, and it passed or failed depending on whose
  machine ran it.
