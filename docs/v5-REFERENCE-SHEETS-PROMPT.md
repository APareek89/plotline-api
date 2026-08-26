# v5 — Reference sheets, artifact detail view, and stitching

Paste everything below the line into a fresh Claude Code session. Medium effort is fine:
the repo carries power-coding harnessing (Handoff.MD, Learning.MD, 179 tests), so the
context is on disk rather than in the prompt.

---

Continue the **Plotline Marketing Studio** build. Two repos:
`~/Documents/plotline-api` (FastAPI + agents) and `~/Documents/plotline-web` (Next.js).

**Read first, in this order:**
1. `~/Documents/plotline-api/Handoff.MD` — the project brain. It carries `last-synced`
   shas; run `git log --oneline <sha>..HEAD` in BOTH repos and reconcile before trusting it.
2. `~/Documents/plotline-api/Learning.MD` — root causes already found, 5-whys form. The
   last five entries are all the same species and will save you a day.
3. `docs/v3-ENHANCEMENT-BRIEF.md`, `docs/v3-ARTIFACT-SPEC.md` — governing specs.
4. `~/Documents/Reference material/` — the OWNER'S TARGET for this phase. Look at every
   screenshot and both `.md` files before writing code.

**Run it first:** `cd ~/Documents/plotline-web && npm run dev` brings up all four
services (its `predev` runs the API repo's `run-detached.sh`). `npm run stack:status`.
Never `npm run build` — it clobbers dev's `.next`. Use `npm run typecheck`.

---

## Ground rules

- **179 tests are the floor.** `.venv/bin/python -m pytest` with **NO path argument** —
  passing `tests/` overrides `pytest.ini`'s `testpaths` and silently skips 12.
- Never weaken a test to make a change easy. Re-point only when an INVARIANT genuinely
  changed by owner decision, and say so in the commit.
- **Drive the app in the browser.** Every bug that mattered in the last two sessions was
  invisible to a green suite and visible within minutes of clicking. That is now five for five.
- **Ask before spending.** See the QA budget below — it is a hard ceiling, one run.
- `.env` is real: PixelBin token live, `MOCK_MEDIA=0`, `PLOTLINE_MODEL_SCRIPT` and
  `PLOTLINE_MODEL_BOARD` are Sonnet because Haiku burns its retries on the w/s and B1–B4
  arithmetic. Everything else is Haiku. **Put `MOCK_MEDIA=1` back while you build**, and
  flip it only for the single QA run.

---

## What is already true (do not rebuild these)

- Conversation is the only way in; the card path is gone. Naming is the only form.
- Every CTA is answered in the chat. `AgentMessage.question` is an `AgentQuestion` with
  tappable options, LIFTED from each artifact's own `actions` in
  `threadkit._options_from()` — declared once. It covers EVERY actioned artifact in a
  turn and labels carry the artifact id when there is more than one.
- The right panel is a READ-ONLY review surface with five agent-driven tabs
  (Brief · Script · Cast · Keyframes · Creative).
- Media routes PixelBin → fal. `app/pixelbin_client.py` holds the wire contract:
  `Bearer base64(token)`, multipart fields named `input.<key>`, `veo31_generate`, a
  per-model capability table. **Verified by spending.** Do not "tidy" any of it without
  re-reading the comments — each line is a bug that reached production.
- Cost gates, the keyframe hard gate, and `STAGE_REQUIRES` preconditions all hold.

---

## THE WORK

### 1. Reference sheets — one image, many labelled views

Look at `Screenshot 2026-08-26 at 6.20.46 PM.png`. That is a **product reference sheet**:
eight labelled views (lateral profile, medial profile, 3/4 front, 3/4 rear, top-down,
outsole bottom, heel detail, midsole detail) on a white catalog background with thin
labelled dividers — **as ONE generated image**, not eight.

This matters for three reasons and you should keep all three in mind:
- **Cost.** One credit instead of eight.
- **Consistency.** Views generated in a single pass agree with each other by
  construction. Eight separate calls do not.
- **Reusability.** The sheet becomes the reference IMAGE passed into every downstream
  generation. That is the point — see §3.

Today `_canon_turn` renders one image per view (7 renders on the last QA). Change it to
render ONE sheet per canon kind, with the views laid out and labelled inside it.

**Ask the user the angle question at the gate** (owner instruction, verbatim): offer
**1 angle** or **all angles**, with the cost impact stated on each option. Use the
existing question-with-options mechanism — it is already the CTA channel. Do NOT generate
a full sheet without an explicit choice; that is the cost-gate invariant.

The prompt technique to copy is in the reference screenshot's Prompt panel: state the
layout, then negative constraints derived from the user's own reference image
("the pale gray diagonal stripe … is a graphic artifact on the photo and is NOT part of
the shoe. Do NOT include any gray diagonal band"). Product fidelity lives in the negatives.

### 2. Artifact detail view

Look at `Screenshot 2026-08-26 at 6.26.07 PM.png` and `…6.24.32 PM.png`. Clicking an
artifact opens a full-height reading surface with a right rail carrying:
- **Prompt** (with Copy, and Show more for long prompts)
- **Name** (double-click to rename)
- **Settings** as read-only chips (model, aspect ratio, quality, resolution)
- Actions: **Mark as approved / Mark as rejected / Download / Delete**

Build this as the artifact detail view. Two constraints from this codebase:
- Approve/reject must fire the SAME `UserAction` events the chat options fire. Do not
  invent a second approval path — one fact, one representation, and this repo has been
  bitten four times by exactly that.
- The panel stays read-only for anything that SPENDS. Download/rename/delete are safe;
  a re-render is a cost event and belongs in the chat with its price.

**Phase 2, do not build now:** "Select & edit", inpainting, any in-place image editing.

### 3. Consistency — the actual gap, fix it

Current chain:

```
canon sheets (images, approved) --TEXT ONLY--> keyframes (images) --IMAGE SEED--> video
```

- `keyframe → video` IS image-consistent: `media.generate(..., image_url=frame["url"])`
  seeds the clip from the approved still. Verified live — the QA video continued from
  its keyframe.
- `canon → keyframe` is **text only**. `CanonSheet.locks` (strings) and `_locks(context)`
  go into prompts. **The canon IMAGES are never passed as references.** So the product in
  the keyframes need not match the product in the approved product sheet.
- Worse: `media.generate()` takes a **singular** `image_url`, while `config.MEDIA_REF_SLOTS`
  declares 2–6 reference slots per model and the board's B3 lint checks against those
  numbers. The lint polices a capacity the client cannot use.

**Fix all three:**
1. `media.generate()` and `pixelbin_client.generate()` take `image_urls: list[str]`. The
   capability table already names the right field per model (`images` for nanoBanana,
   `image_urls` for veo31) — send up to that model's slot count.
2. The approved canon sheet's asset URL is passed as a reference into every keyframe
   render for shots that bind that canon id.
3. A test asserting a keyframe render for a shot bound to `@product` actually receives
   the product sheet's URL. Without that test this silently regresses to text-only.

### 4. Stitching

Currently N clips are handed over; nothing stitches. Add ffmpeg concat + an audio bed.
The pattern to copy (from the deferred v3 study): **stitching DEGRADES honestly, never
crashes** — if one clip is missing, deliver the rest and say which is absent.

`ffmpeg` is on this machine but NOT on Render; `media.py` already degrades when it is
missing (`_mock_video` falls back to an SVG poster). Follow that precedent.

---

## QA — ONE run, hard ceiling

**Budget: $1–2 Anthropic + 50 PixelBin credits.** Owner's credit model: nanoBanana = 1
credit/image, Veo 3.1 = 20 credits or less per video.

Plan the run before spending:
- 2 shots, **two 3-second videos**, stitched into one 6-second film.
- ~2 videos × 20 = 40 credits, plus a handful of sheet/keyframe images. That is the whole
  budget — there is no second attempt, so dry-run everything with `MOCK_MEDIA=1` first
  and only flip to 0 when the flow completes clean end to end.
- Use `/observability` → **Media** lens to confirm provider and spend per render.

---

## Traps specific to this repo

- **A slot only ever read is a typo with a default.** `approved_option_json` was read by
  two turns and written by nothing for the whole of v3. Grep every new workspace key for
  its WRITE site.
- **Read `node_input` before the validation errors.** An agent that fails its checks is
  often correct about the input it was handed; the errors only describe the OUTPUT's shape.
- **Two lists that must agree need something comparing them**, even across a language
  boundary — there are already Python tests reading TypeScript. Add one rather than
  hand-syncing.
- **`_rehydrate()` must learn every new artifact** whose payload a later stage depends on.
  It has been the cause twice.
- Update `Handoff.MD` before each checkpoint commit and re-stamp `last-synced`. Log root
  causes in `Learning.MD` in the 5-whys format.
