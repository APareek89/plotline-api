# Next-session prompt — Plotline Marketing Studio

Paste everything below the line into a fresh Claude Code session.

---

Continue the **Plotline Marketing Studio** build. Two repos:
`~/Documents/plotline-api` (FastAPI + agents) and `~/Documents/plotline-web` (Next.js).

Read in this order before touching code:

1. `~/Documents/plotline-api/Handoff.MD` — the project brain for both repos. It carries
   `last-synced` shas; run `git log --oneline <sha>..HEAD` in **both** repos and reconcile
   any drift before trusting it.
2. `~/Documents/plotline-api/Learning.MD` — root causes already found, in 5-whys form.
   Check it before debugging anything; the answer may be there. Read at least the last three
   entries — one of them explains why a whole class of bug keeps recurring.
3. `docs/v3-ENHANCEMENT-BRIEF.md`, `docs/v3-ARTIFACT-SPEC.md`, `docs/v3-COUNCIL-DOCTRINE.md`
   — the governing specs, highest precedence. `docs/v3-agentic-flow.html` is a one-page
   visual of the whole pipeline; open it first if you want orientation fast.

## Where the app is

v3 is **shipped end to end, API and UI**. 160 tests green (the floor was 83).
Pipeline: cards → **brief** → options → templates(+style block) → **script** (video only) →
**board** → **canon** → **keyframes** (hard gate) → generate → creative → **qc** → done.
All six new artifact types render with working actions; My Brand hosts the canon library.

Run it: `bash run.sh` in plotline-api (api :8600 + rag :8788 + devrag :8787),
`npm run dev` in plotline-web (:3100). Observability at
`http://localhost:3100/observability` shows every agent node's structured input/output.
Use it FIRST when something stalls — it names which node died and what it was asked.

## Ground rules

- **LOCAL ONLY.** Render autoDeploy is off. Do not push or deploy without asking.
- **160 tests are the floor.** Never weaken a test or an invariant to make a change easy —
  surface the conflict instead. Re-point a test only when an INVARIANT genuinely changed by
  owner decision, and say so in the commit.
- **`.env` is in QA mode**: Haiku on all four model slots, `MOCK_MEDIA=1`. Both are marked in
  the file with how to restore. Restore before judging output quality — Haiku needed several
  retries where Sonnet needed none.
- **Never `npm run build`** — it clobbers the dev server's `.next`. Use `npm run typecheck`.
- **Ask before spending.** A rumination is slow and costs real money.
- **Drive the app like a user.** Every bug that mattered was invisible to the tests: the
  refused brief, the dropped attachment, and a gate that rendered as a placeholder with no
  buttons while 155 tests and two API QA runs passed.

## Start here — pick with the owner

1. **fal is untested under v3.** Balance was low, so `MOCK_MEDIA=1` throughout — generate,
   creative, keyframes and canon are verified for WIRING only. No real image or video has
   rendered under v3. Biggest unknown in the build.
2. **The reject UI is server-side only.** `reject_<cause>_<slot>` works and is tested, but
   `CreativeSetCard` does not offer the five causes as buttons, so a user can only re-roll.
3. **`keyframes_per_shot` is stored and shown but not consumed** — the renderer loop makes
   one frame per shot regardless.
4. **Canon views use the draft tier and a generic per-view prompt.** Enough to prove
   coverage; a real sheet wants its own brief + locks in each view prompt.
5. **Style block has no editable card** — read-only on the board card; §3 asks for six
   editable rows and a copy action.
6. 🔴 **P0-2, still open and costing money:** Render is live and public with a real
   `ANTHROPIC_API_KEY` and `FAL_KEY` and **no auth**. Decide: suspend, add auth, or flip to
   mock. Warn the owner that flipping `MOCK_LLM=1` would ALSO open `/api/agent-runs`
   publicly — that gate couples two unrelated concerns and should be its own env var.

## Invariants that must survive every change

Cost shown **before** any generation plus an explicit user event; **no setting removes a cost
gate** (`generate` is deliberately absent from `GATEABLE_STAGES`, and naming it is an error,
not a no-op); editable prompts used **verbatim**; per-asset re-roll only; **no video without
every keyframe approved**; a gate mode controls whether the flow PAUSES, never whether the
artifact is PRODUCED; confirmed claims are the compliance source of truth and an unmapped
claim is a kill flag; **an unrun check renders as `skip`/`na`, never as `pass`**; honesty
surfaces everywhere; labeled working steps, never a bare spinner; ≤1 clarifying question per
turn; `AgentMessage.text` ≤ 2 sentences with content in artifacts; the `generation_log` audit
stays reachable.

## The bug class that keeps recurring — read before adding any check

Four times now ONE FACT had TWO REPRESENTATIONS and nothing compared them: the R2 receipt
lexicon (prompt vs validator), the W1 words-per-second table and the B3 reference caps
(prompt vs config), and `CAMPAIGN_ARTIFACT_TYPES` vs the renderer switch (list vs switch,
across a language boundary — that one made a gate a dead end in the UI while every API test
passed).

So: **when a check is mechanical, the prompt must QUOTE the mechanism, generated from the
same constant the check reads** — see `validators.script_thresholds_text()` and
`ref_slots_text()`, injected via `prompt_replacements`. Never hand-copy. And when two lists
must agree, something has to compare them in the suite that actually runs, even if that means
a Python test reading TypeScript
(`test_every_artifact_the_server_emits_has_a_renderer_registered`).

The W1 table carries the ARITHMETIC, not just the ceiling ("a 10-word line needs at least
3.0s"), because a model can hold a threshold and still not see its own line breaks it. Do not
simplify that back to a bare number.

## Where things live

- Orchestration: `app/campaign.py` (`CAMPAIGN_STAGES` + `advance_from()`, turn handlers) and
  `app/graph.py` (plan → review → one-pass refine, per-node checkpoints). The linear stages
  stay plain code on purpose — see the 2026-08-25 decision.
- Every LLM call: `app/agents/runner.py::run_agent` (streaming, validation retries,
  truncation guard, transport retry, raw-body capture to `data/runs/raw/`).
- The reviewer: `app/agents/council.py` — ONE Marketing Expert, no retrieval, frozen doctrine.
  Stakeholder seats are an additive opt-in via `app/seats.py`.
- Validators: `app/validators.py` (W1-W3 script lints, B1-B4 board lints, canon, keyframe
  gate, QC, doctrine enforcement). Retrieval: `app/tools.py` + `app/rag_client.py`.
- Prompts (all live): `prompts/shared_policy.md` (prepended to every agent),
  `campaign_intake.md`, `campaign_planner.md`, `campaign_brief.md`, `hook_rack.md`,
  `shot_board.md`, `canon_plan.md`, `council/doctrine.md`, `council/marketing_expert.md`.
- UI: `plotline-web/components/campaign-artifacts.tsx` (all campaign cards — note the TWO
  lists at the top), `app/brand/page.tsx` (canon library),
  `app/studio/thread/[threadId]/page.tsx`.

Update `Handoff.MD` before each checkpoint commit and at phase end, and re-stamp
`last-synced`. Log root causes in `Learning.MD`.
