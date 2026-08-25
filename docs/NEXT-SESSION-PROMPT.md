# Next-session prompt — Plotline Marketing Studio

Paste everything below the line into a fresh Claude Code session.

---

Continue the **Plotline Marketing Studio** build.

Read in this order before touching code:

1. `~/Documents/plotline-api/Handoff.MD` — the project brain for both repos. It carries
   `last-synced` shas; run `git log --oneline <sha>..HEAD` in **both**
   `~/Documents/plotline-api` and `~/Documents/plotline-web` and reconcile any drift
   before trusting it.
2. `~/Documents/plotline-api/Learning.MD` — root causes already found. Check it before
   debugging anything; the answer may be there.
3. `~/Documents/plotline-api/docs/PRD-addendum-03-ui.html` (behavioral spec) and
   `docs/marketing-studio-demo.html` (**the design contract**) — highest precedence,
   above `docs/SYSTEM-PROMPT-v2-marketing-studio.md`.

## Ground rules

- **LOCAL ONLY.** Render autoDeploy is off. Do not push or deploy without asking.
- **83 tests are the floor** (`tests/test_marketing.py` holds the acceptance checks).
  Never weaken a test or an invariant to make a change easy — surface the conflict instead.
- **Verify in a clean environment, not just the dev venv.** Three bugs this session were
  green locally and broken where they ran (unpinned `langgraph`, unisolated `LOG_DIR`,
  `npm run build` clobbering the dev server's `.next`). Use `npm run typecheck` while the
  dev server is up — never `npm run build`.
- **Drive the app like a user.** The refused-brief, dropped-attachment and half-block bugs
  all passed the tests, because the tests call `save_block()` instead of typing.
- Ops: `bash run.sh` in plotline-api (api :8600 + rag :8788 + devrag :8787),
  `npm run dev` in plotline-web (:3100). Real models + real fal are ON locally.
- Observability: `http://localhost:3100/observability` — every agent node's structured
  input/output, filterable, "only retried/failed" is the useful filter. Use it first when
  something stalls; it will tell you which node died and what it was asked.

## Start here — in this order

**1. Two open P0s from the FMEA (both one-liners, do these first).**
   - `PLOTLINE_ALLOW_SEED_EVIDENCE=1` lets the 10 synthetic `seed-*` chunks be cited as
     real evidence and the UI renders them like genuine sources (RPN 336). Set it to `0`;
     the agents already degrade gracefully to "no evidence in DB".
   - Render is still live with real `ANTHROPIC_API_KEY` + `FAL_KEY`, public, **no auth**
     (RPN 224). Ask the user: suspend, add auth, or flip to mock. Warn them that flipping
     `MOCK_LLM=1` would ALSO open `/api/agent-runs` publicly — that gate couples two
     unrelated concerns and should be the explicit env var only.

**2. The video-flow reshape — ASK BEFORE CODING.**
   Handoff's "VIDEO FLOW — proposal" section has five items studied (architecture only,
   nothing copied) from `gofynd/pixelbin@marketing-studio-service`, path
   `services/marketing-studio`. Items 1–3 reshape `CampaignDetail` and the planner prompt,
   which is architecture-shaping — present the delta in plain language and get approval
   before writing code. Item 4 (stitching) and item 5 (checkpoints) are additive and safe.
   Item 5 is the biggest reliability win: today a transient error discards a full paid
   rumination (~25 min + real money).

**3. Addendum-03 leftovers**, if the user prefers product over plumbing:
   `brand_profile` storage + routes + the **My Brand** tab (it is deliberately absent from
   the nav until its page exists — a link that 404s is worse than a missing one);
   `reference_media[]` with user-reference-beats-template; campaign archive/rename/delete;
   and My Campaigns as a folder-card **grid** (§02) instead of the current row list.

## Invariants that must survive every change

Cost shown **before** any generation plus an explicit user event; editable prompts used
**verbatim**; per-asset re-roll only, never the whole deliverable; evidence cited with
retrieved `source_id`s or an honest "no evidence in DB"; confirmed claims are the
compliance source of truth and an unmapped claim raises a kill flag (note: confirming
claims GRANTS permission — it does **not** gate the start); honesty surfaces everywhere
(PROVISIONAL, insufficient-data, empty states that say what is missing); labeled working
steps, never a bare spinner; ≤1 clarifying question per agent turn; `AgentMessage.text`
≤ 2 sentences with content in artifacts; single-vs-variants always asked before
generation; the `generation_log` audit stays reachable.

## Where things live

- Orchestration: `app/campaign.py` (stage machine, steps 0–8) and `app/graph.py` (the
  rumination StateGraph: plan → 3 blind seats in parallel → chair → one-pass refine).
  The linear stages stay plain code **on purpose** — see the 2026-08-25 decision.
- Every LLM call: `app/agents/runner.py::run_agent` (streaming, retries, truncation guard,
  raw-body capture to `data/runs/raw/`).
- Council: `app/agents/council.py`. Retrieval: `app/tools.py` + `app/rag_client.py`.
- Prompts (all live): `prompts/shared_policy.md` (prepended to every agent),
  `campaign_intake.md`, `campaign_planner.md`, `council/{seat_performance,seat_brand,
  seat_platform,chair}.md`. `campaign_orchestrator.md` exists but is not wired yet.
- Diagram: `docs/mermaid/10-agentic-flow.mmd` → `docs/architecture-flow.html`.

Update `Handoff.MD` before each checkpoint commit and at phase end, and re-stamp
`last-synced`. Log root causes in `Learning.MD`.
