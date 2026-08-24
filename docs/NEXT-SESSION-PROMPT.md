# Next-session prompt — Marketing Studio, clean-slate frontend

Paste everything below the line into a fresh Claude Code session.

---

You are continuing the **Marketing Studio** build. Read these in order before touching code:

1. `~/Documents/plotline-api/Handoff.MD` — the project brain for both repos. Run
   `git log --oneline <last-synced>..HEAD` in `~/Documents/plotline-api` and
   `~/Documents/plotline-web` and reconcile any drift before trusting it.
2. `~/Documents/plotline-api/docs/SYSTEM-PROMPT-v2-marketing-studio.md` — the governing brief.
3. **NEW — copy these two out of `~/Downloads` into `~/Documents/plotline-api/docs/` first thing,
   then read both:**
   - `content-engine-prd-addendum-03.html` → `docs/PRD-addendum-03-ui.html` (behavioral spec)
   - `marketing-studio-demo_1.html` → `docs/marketing-studio-demo.html` (**the design contract**)

   NOTE: `docs/PRD-addendum-03.html` already exists — it was generated in the last session from
   v2 and is NOT the same document. Keep it as `docs/PRD-addendum-03-generated.html` for reference,
   but the owner's file is the authority.

**Precedence, highest first:** Addendum-03 (owner's, the two new files) > system-prompt v2 >
Addendum-02 > Addendum-01 > PRD v1. Where the demo HTML and a document disagree on *look*, the
HTML wins; on *behavior*, the documents win.

## THE HEADLINE INSTRUCTION — this is a NEW app

The frontend is a **clean slate**. Nothing from the previous product survives into the UI:

- **Delete, do not adapt:** Avatar Studio, Creative Studio (the DIY page), Content Studio, Plans,
  My Space, the concept/Post-Card screens, the old thread page, and the entire old design system
  (the paper/ink/cobalt palette, Avenir Next stack, `.card`/`.chip`/`.upload-tile` vocabulary,
  `anim-morph`, the `ms-dark` bolt-on scoping — all of it).
- **The app has exactly three global tabs:** Campaign Studio · My Campaigns · My Brand.
  Nothing else at global level. `+ New campaign` lives in the studio sub-bar, not the nav.
- Build the frontend to match `docs/marketing-studio-demo.html` — its tokens, layout, components,
  and micro-interactions are the contract. Do not reinterpret them.

**The backend is leveraged, not rebuilt** — but only the parts that serve this app (see below).

## WHAT IS ALREADY BUILT AND VERIFIED (do not redo)

Backend, `~/Documents/plotline-api`, **97/97 tests green**, running on **real models**
(`MOCK_LLM=0`, `MOCK_MEDIA=0`; both keys already in `.env`):

- `app/campaign.py` — the v2 flow driver, steps 0–8 (paths → cards → rumination → options →
  templates → detail → refine → model-confirm → generate → Ad Card → mark-live), with the
  in-memory workspace + rehydrate-after-restart.
- `app/schemas.py` — CampaignContext (product/campaign/brand blocks incl. confirmed claims),
  CampaignOption(s), TemplateRef, CampaignDetail, VariantSpec, ModelConfirm, AdCard (with a
  ratio spec-table validator), SeatReview, plus the envelope additions
  (`campaign_option`, `template_picker`, `campaign_detail`, `model_confirm`, `creative_set`,
  `ad_card`, `intake_progress`).
- `app/agents/council.py` + `prompts/council/{seat_performance,seat_brand,seat_platform,chair}.md`
  — three blind RAG-grounded seats then a chair, consolidating into the **unchanged** Feedback
  schema. Seats are editable prompt files.
- `app/brand_extract.py` — brand-URL extractor as a **system pipeline, never an agent tool**,
  with an SSRF guard (rejects private/loopback/link-local, re-checked per redirect hop) and
  candidate-claims extraction.
- `app/fal_client.py` — fal queue client (nano-banana / nano-banana-2 / nano-banana-pro,
  veo3.1/fast image-to-video, kokoro, minimax-speech-02-hd), bounded retries, `MediaError(policy=)`.
- `app/rag_client.py` + `rag/` (S3 + DynamoDB, hybrid RRF, :8788) + `devrag/` (:8787 aux sample
  corpora) + `kb/` (corpus pipeline, 25 chunks synced).
- `app/store.py` — series(=campaigns), threads, thread_messages, artifact_activity, assets,
  generation_log, ad_cards, campaign lifecycle + `campaign_spend`.
- `app/main.py` — the campaign HTTP surface (see the contract in the v2 brief; routes exist for
  campaigns CRUD, blocks, brand/fetch, claims/extract, start, templates, thread events,
  ad-cards list/get/bundle/mark-live, assets file, generation-log, prompt edit).
- `tests/test_marketing.py` — **19 acceptance checks**, all passing, no xfail escapes. Treat these
  as the floor: never weaken one to make a change easy.

**Verified against real models this session:** the planner produced real options with 19 retrieval
tool calls and real citations; all three council seats passed first try with independent retrieval
slices (13/8/8 tool calls, own citations); the claims extractor pulled exactly the one checkable
claim from a product description and ignored the marketing fluff.

**Two real-mode prompt bugs were found and fixed** (both only findable by running real models —
expect more of this species): Sonnet emitted `id`/`short_storyline` because the prompt described
fields in prose (prompts now carry the literal key contract), and it wrote slide-by-slide
storylines that blew the 400-char cap (the prompt now states the budget as hard and says the
per-slide breakdown belongs to the *detail* pass).

## WHERE IT STOPPED — start here

A real campaign (`srs_5e7715873a`, thread `thr_42eb12a760`, "Nimbus Desk Lamp — launch") is mid-run.
Planner ✓, all three seats ✓, **the council chair was still in flight when the session ended** and
had not yet produced the Feedback object, so the thread never reached the `options` stage.

**First task:** re-run that campaign's rumination and get the chair through.
`POST /api/campaigns/srs_5e7715873a/start`, then watch `data/runs/agent_runs.jsonl` and
`data/api-server.log`. If the chair burns its two retries, the cause is almost certainly the same
species as the two fixed bugs — its output is large (3 options × 8 elements × reasons + evidence +
four lenses). Fix it the same way: tighten `prompts/council/chair.md` with the exact key contract
(already added) **plus** a per-field length budget, and if that is not enough, have the chair emit
one option's verdict per call and merge server-side rather than loosening the Feedback schema.
Do not weaken `validate_feedback` and do not fabricate a chair result.

Then carry the same campaign through the rest of the flow **on real models and real fal**:
option approve → templates (manifest is empty, so the honest skip path) → campaign detail →
one refine → model-confirm → **single** creative first (cheap) → creative set → Ad Card →
mark-live. Confirm the Ad Card's credits equal `store.campaign_spend()` exactly.

## THE FRONTEND BUILD (the bulk of the work)

Rebuild `~/Documents/plotline-web` as the app the demo HTML describes. Suggested approach: start
from a clean `app/` and `components/` tree, keep only `lib/api.ts`'s Marketing Studio types and
helpers (rewrite the rest), and delete every legacy route and component rather than leaving dead
files behind.

From `docs/marketing-studio-demo.html`, match exactly:

- **Tokens:** `--bg:#0E1116 --sur:#171B23 --elev:#1E242E --line:#2A3140 --tx:#FFFFFF
  --tx2:#A9B3C4 --blue:#4353FF --blueh:#5A6AFF --ok:#39C36A --warn:#E8A13C --dgr:#E5312B`.
  Body `14.5px/1.55` Archivo; Archivo Black headings; Space Mono for labels/mono.
  Where white-on-`--blueh` fails 4.5:1, use a darker solid for the hover fill — keep the palette
  hex in the token block and state the contrast ratios you computed.
- **Shell:** top bar (logo + three nav tabs) → studio sub-bar (breadcrumb left,
  `+ New campaign` dashed pill right, visible only inside Campaign Studio) → stage + persistent
  prompt modal → right panel.
- **Right panel:** `520px` open, `780px` expanded, overlays below `980px`. Tabs **Context | Creative**.
  Activity/audit lives under the ⋮ menu and must show the real `generation_log`
  (prompt, model, seed, cost, re-rolls) — not artifact activity mislabelled as it.
  Panel body 13.5px, meta rows 13px with 8–9px padding, tabs/title 13/14px.
- **Campaign Studio flow:** name step → two path cards → three detail cards, each a modal
  (Product / Campaign / Brand) → rumination with labeled steps → option cards → templates step →
  campaign detail in the panel → refine → model-confirm → creative set → Ad Card.
- **My Campaigns (§02):** folder-card **grid**, not rows. Header + live search; `Current | Archived`
  pill tabs; `Recent | Newest` sort; new-campaign card first; 16:10 thumb that is a **2×2 collage**
  of latest creatives (✦ sparkle placeholder when none); folder icon + name + status chip
  (DRAFT/PLANNED/IN PRODUCTION/READY/LIVE) + kebab (Archive/Restore, Rename, Delete-with-confirm);
  meta line with objective · creative count · credits; inline **Mark Live** on READY;
  results-due note on LIVE opening the CTR/CPC/CPA/ROAS form.
- **My Brand (§03) — new tab and new backend surface.** `brand_profile {id, name, url, palette[4],
  font, logo_url, tone_rules, policy_doc_ref?, approved_claims[{id,text,confirmed}], banned_words[],
  fetched_from_url, updated_at}`. Empty state offers *Pre-fill from URL* (runs the existing
  extractor, then opens the Brand modal to confirm) and *Add manually* — both reuse the **exact**
  Brand modal from the campaign flow with a standalone save. Saved state is a read view with
  Edit/refetch; claims edits require re-confirmation taps. **Bidirectional pre-fill:** saving a
  campaign's Brand card creates/updates the profile; new campaigns pre-fill and are labeled
  "Pre-filled from My Brand ✓"; campaign-level edits ask once "update My Brand too, or this
  campaign only?". `+ Add another brand` renders **disabled with tooltip** (multi-brand is a later
  phase — do not fake it).
- **Reference media (§04):** Campaign Details modal gains *Reference image / video (optional)*,
  up to 3 files, stored as `reference_media[]` on CampaignContext. Passed to planner + orchestrator
  as a style/composition/pacing reference. **Priority: user reference > selected template**, said
  inline on the templates step. Never overrides brand palette or claims. Surfaced honestly: the
  rumination log notes it and the Context tab gets a *Reference media* row.
- **Prompt modal (§05):** `+ upload` with attach counter "Images 0/8" · prompt field ·
  `⚙ Settings` **disabled with tooltip "Model selection coming — using recommended"** · blue send.
  **No LLM or media-model name anywhere in the bar.** The media model appears exactly once — in the
  in-thread model-confirm card, with its reason and cost. LLM identity is never user-facing.

**Replace the demo's simulation with the real thing** (§07): canned agent turns → real envelope
polling/streaming; gradient tiles → real media from `/api/assets/{id}/file`; `Fill sample` buttons →
remove; jump pills, SIMULATION chip, guide bar, Reset → strip; "wired in build" toasts → real
actions; seeded campaigns → real data. **Stays disabled even in prod:** the Settings gear, and
template thumbnails from the local samples folder until a template pipeline exists.

## BACKEND WORK THIS IMPLIES

- **New:** `brand_profile` storage + routes (get/upsert/refetch), the bidirectional pre-fill rule,
  campaign archive/restore/rename/delete, and `reference_media[]` on CampaignContext threaded into
  the planner and orchestrator prompts with the user-reference-beats-template priority.
- **Extract before you delete:** `app/campaign.py` currently imports `CREDIT_USD, _asset_url` from
  `app/creative.py`, `_dispatcher, _flagged_ids, _merge_feedback` from `app/orchestrator.py`, and
  `_actions, _say, _thread_lock, _working` from `app/thread.py`. Move those helpers into a neutral
  module (e.g. `app/threadkit.py`) **first**, repoint `campaign.py`, and only then delete the old
  drivers. Also note `campaign.py` deliberately aliases `creative._pending(tid)["prompts"]` so the
  existing prompt-edit route works — rehome that too.
- **Delete once nothing imports them:** `app/thread.py`, `app/creative.py`, `app/orchestrator.py`,
  `app/agents/mock.py`, `app/commands.py`, `app/ccs.py` *only if* the council no longer needs the
  CCF weights (check — it does today), and the routes `/api/series/*`, `/api/post-cards/*`,
  `/api/diy/*`, `/api/runs/*`, inspiration. Delete their tests with them
  (`test_pipeline_mock.py`, `test_addendum.py`, `test_creative.py`, `test_validators.py`,
  `test_ccs.py` — keep any assertion that still guards a Marketing Studio invariant by moving it
  into `tests/test_marketing.py` first).
- Keep `evals/` only if you repoint it at campaigns; otherwise delete it rather than leave it
  asserting a product that no longer exists.

## NON-NEGOTIABLE INVARIANTS (carry forward)

Cost shown **before** any generation plus an explicit user event; draft-first offered on video;
editable prompts used **verbatim**; per-asset re-roll only, never the whole deliverable; evidence
cited with retrieved `source_id`s or an honest "no evidence in DB"; confirmed claims are the
compliance source of truth and an unmapped persuasion claim raises a kill flag; honesty surfaces
everywhere (PROVISIONAL, insufficient-data, sample-data, empty states that say what is missing);
labeled working steps, never a bare spinner; ≤1 clarifying question per agent turn;
`AgentMessage.text` ≤ 2 sentences with content in artifacts; the single-vs-variants question always
precedes generation and variants share a `variant_group_id`; the `generation_log` audit stays
reachable. **Never weaken an invariant or a test to simplify — surface the conflict instead.**

## HOW TO WORK

- Add acceptance checks for the new rules to `tests/test_marketing.py`: brand pre-fill (both
  directions), reference-media priority over template, grid archive/restore.
- Ops: `bash run.sh` in plotline-api (starts rag :8788 + devrag :8787 + api :8600),
  `npm run dev` in plotline-web (:3100). AWS only inside the `plotline-agent` fence
  (ap-south-1, `plotline-*` buckets, `plotline_*` tables).
- Auto-checkpoint commits are on. Push to `master` auto-deploys both Render services —
  **the api service currently has `FAL_KEY` + `MOCK_MEDIA=0` and no auth, so the public demo spends
  real fal money.** Flag before doing anything that increases that exposure.
- Update `Handoff.MD` before each checkpoint and at phase end; re-stamp `last-synced`.
