# Handoff.MD — Plotline Marketing Studio (plotline-api + plotline-web)
last-synced: `64c2952` (plotline-api) · `a6bb767` (plotline-web)
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

## v3 — IN PROGRESS (build order in the brief §6; 10 steps)
Owner decisions taken 2026-08-25: RAG removed from the **council only** (planner keeps it) ·
canon sheets **workspace-global** · stakeholder seats **ship fully in v3** · final stitch
**deferred, checkpoints instead**.
- ✅ **Step 1 council doctrine — DONE and verified on the production models. 101 tests green.**
  `prompts/council/doctrine.md` v3.1.0 + `build_system(preludes=)` composite versions
  (`seat_brand@2.0.1+doctrine@3.1.0`); seats/chair → v2.0.1; `dispatcher=None` AND
  `use_tools=False`; `validate_seat_review` + `validate_council`; `app/seats.py` registry
  with `can_kill` default-deny + 5-seat cap; `campaign_settings` table; graph cached per
  seat-roster. Real-mode result (sonnet-4-6, both council rounds): **7 council nodes, zero
  retries, zero retrieval calls, no retrieved id cited**, doctrine version on every one.
- 📌 **Prompt lessons from the paid runs — do not undo these.** Three separate retry causes
  were found and fixed by prompt, not by weakening a check: (a) seats invented ELEMENT names
  from doctrine points (`one_idea_per_ad`) → doctrine now states the eight names are a closed
  set and D-points are lenses; (b) seats/chair invented benchmarks in fields far from the
  intro rule → doctrine rule 2 now carries a rewrite table and every seat repeats it beside
  its OUTPUT SHAPE; (c) chair emitted `kill_flags` as objects → chair.md shows a bare-string
  example. Haiku chair went 3 attempts → 1; Sonnet now needs none.
- ⚠️ **Sonnet is SLOW here**: chair 477s, full rumination + refine >15 min. Not a regression
  (the doctrine removed retrieval, so this is model latency), but it is what makes the
  missing checkpoints expensive.
- 🔜 **Step 2 `ReviewPolicy`** — storage already exists: put gates/counts in the SAME
  `campaign_settings` blob (`store.get/update_campaign_settings`). The settings ROUTE that
  writes `seats[]` belongs there too and must validate slugs on write; `_campaign_seats`
  degrades to the built-ins on a bad roster rather than losing a paid run.
- ⚠️ Brief says "101 tests" — the floor was **83** (71 `tests/` + 12 `rag/tests/`), now **99**.
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
