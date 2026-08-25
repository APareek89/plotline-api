# Handoff.MD — Plotline Marketing Studio (plotline-api + plotline-web)
last-synced: `8bc17ee` (plotline-api) · `a6bb767` (plotline-web)
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
`docs/PRD-addendum-03-ui.html` (owner, behavioral) + `docs/marketing-studio-demo.html`
(owner, THE DESIGN CONTRACT) > `docs/SYSTEM-PROMPT-v2-marketing-studio.md` > Addendum-02 >
Addendum-01 > PRD-v1. `PRD-addendum-03-generated.html` is reference only, NOT authoritative.

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
- 🔴 **P0-1 `PLOTLINE_ALLOW_SEED_EVIDENCE=1`** lets the 10 synthetic `seed-*` chunks be
  cited as real evidence; the UI renders them like genuine sources. RPN 336. One-line fix:
  set `0` and let agents say "no evidence in DB" (they already do this gracefully).
- 🔴 **P0-2 Render live, real keys, no auth** — anyone with the URL spends real money.
  RPN 224. Decide: suspend, add auth, or flip to mock. NOTE: flipping `MOCK_LLM=1` would
  ALSO open `/api/agent-runs` publicly (P1-3) — the gate couples two unrelated concerns.
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
