# UI copy map v2 — Plotline → Marketing Studio

Companion to `docs/PRD-addendum-03.html`. Governing brief: `docs/SYSTEM-PROMPT-v2-marketing-studio.md`.
Precedence: **v2 > Addendum-02 > Addendum-01 > PRD v1**.

**Scope.** Old → new user-facing copy for the Marketing Studio mode: nav, buttons, empty states,
agent-facing labels, lifecycle chips — plus the disabled-states list with the exact string for each.

**Rules for this map.**

- Where a string already ships in `plotline-web`, the **shipped string is the authority** and is quoted
  verbatim here. Rows marked *(new)* have no old equivalent.
- Renaming stops at the Marketing Studio mode. Content Studio, Creative Studio (DIY `/creative`),
  Avatar Studio and My Space keep their v1/Addendum-01/02 copy — see §8.
- Em dash `—` (not a hyphen) separates a label from its qualifier. Ellipsis is the single glyph `…`.
- Sentence case for body and hints; Title Case for nav items, tab labels and card titles.

---

## 1 · Navigation and surfaces

| Old | New | Notes |
| --- | --- | --- |
| `Content Studio` | **`Campaign Studio`** | Top-nav item. The mode's working surface. |
| `Plans` | **`My Campaigns`** | Top-nav item. Campaign home, replaces Plans for this mode. |
| `+ New series` | **`+ New campaign`** | Appears in the studio top bar once the user has ≥1 campaign. |
| `Start a new plan` (studio landing h1) | **`Campaign Name`** + hint **`Give a name to your campaign.`** | Step 0 is the name block, not a form. |
| `Series` (left rail heading) | **`Campaigns`** | Rail lists campaigns; thread numbering `<Campaign> — 01` is unchanged. |
| panel tabs `Concept` \| `Activity` | **`Context`** \| **`Creative`** | Activity moves under the panel's `⋮` menu — it does not vanish. |
| — | **`Activity`** (under `⋮`) *(new position)* | Same generation_log audit, new home. |
| `Open plan` | **`Open`** | Row CTA in My Campaigns. |
| `Generate next post` | **`Generate next creative`** | Opens the next thread step; it never itself generates. |

> **Variance to settle:** the My Campaigns page title currently ships as `My campaigns` (sentence case)
> while the nav item is `My Campaigns`. Pick one — recommendation: Title Case in both, since it is a
> proper surface name.

---

## 2 · Domain vocabulary

Rename these everywhere they surface to the user *in this mode* — headings, chips, tooltips, agent text,
error copy, aria-labels.

| Old term | New term | Where it shows |
| --- | --- | --- |
| series | **campaign** | Rail, row labels, thread names, agent references |
| plan | **campaign** | My Campaigns rows; "the plan" → "the campaign" |
| concept | **campaign option** | Step 3 cards; ids `c1/c2/c3` → `o1/o2/o3` |
| format options (PASS 0.5) | **campaign options** | The step-3 artifact is `campaign_option` |
| script package | **campaign detail** | Right panel, Context tab |
| Post Card | **Ad Card** | Deliverable; ships as `Ad card` in the results form heading |
| post | **creative** | "next post" → "next creative"; counts read `Creatives` |
| Posted | **Live** | Terminal lifecycle state |
| `Mark Posted` | **`Mark Live`** | Ad Card action |
| post results | **campaign results** | CTR / CPC / CPA / ROAS |
| creative director | **creative orchestrator** | Agent-facing; §5 |
| feedback agent | **Agent Council** | Three blind seats + a chair |
| context blocks | **campaign details cards** | Product · Campaign · Brand |
| inspiration set | *(not used in this mode)* | Retrieval still runs; it is not a user-facing card here |

---

## 3 · Buttons and actions

| Old | New | Surface |
| --- | --- | --- |
| — | **`Enter`** *(new)* | Step 0, submits the campaign name (Enter key does the same) |
| — | **`Provide Campaign Details`** *(new)* | Step 1, path a |
| — | **`Help me define campaign`** *(new)* | Step 1, path b |
| — | **`Enter Product Details`** / **`Enter Campaign Details`** / **`Enter Brand Details`** *(new)* | Step 2 cards; the modals title as `Product Details` / `Campaign Details` / `Brand Details` |
| — | **`Fetch from URL`** *(new)* | Brand card; runs the system extractor, user confirms |
| — | **`Start campaign`** *(new)* | Enabled only when all three cards are ✓ |
| `Approve` | **`Approve`** | Unchanged — campaign option card |
| `Regenerate` | **`Regenerate`** | Unchanged — campaign option card |
| `Feedback` | *(retired here)* | Refinement is free text in the prompt modal |
| — | **`Skip — no style constraint`** *(new)* | Template picker; `Select a template` is the disabled-primary label |
| — | **`Generate creative`** *(new)* | Campaign detail card |
| — | **`Generate — $X.XX`** *(new)* | Model-confirm card; cost is always in the label |
| — | **`One creative`** / **`2 variants`** / **`3 variants`** *(new)* | Model-confirm choice; count is always explicit |
| — | **`Accept all`** / **`Re-roll`** / **`Use as reference`** *(new)* | Creative tab, per item |
| — | **`Download bundle`** *(new)* | Ad Card export (media + `copy_<platform>.txt` + `meta.json`) |
| `Mark Posted` | **`Mark Live`** | Ad Card |
| `Paste post results` | **`Paste results`** / **`Log results`** | My Campaigns, Live rows only |
| `Close` | **`Close — draft is kept`** | Card modals; Esc does the same |
| `Edit` | **`Edit →`** / **`Open →`** | Card row, saved vs empty |

---

## 4 · Empty, loading and error states

| Old | New | Notes |
| --- | --- | --- |
| `No plans yet` | **`No campaigns yet`** | My Campaigns empty state |
| `Start a plan` | **`+ New campaign`** | Empty-state CTA |
| — | **`Loading campaigns…`** *(new)* | |
| — | **`Can't reach the campaigns API`** *(new)* | Honest failure, not an empty list |
| — | **`Creating the campaign and its first thread`** *(new)* | Labeled working step, never a bare spinner |
| — | **`Starting the campaign`** *(new)* | Labeled working step |
| — | **`Still needed: <cards>.`** *(new)* | Which cards are unfilled, named |
| — | **`All three cards saved — Start campaign hands the brief to the agent chain.`** *(new)* | |
| — | **`Half-filled cards persist — the agent picks up where you left off`** *(new)* | Path a ↔ b switch |
| — | **`Objective not set`** *(new)* | Campaign row before the campaign card is saved |
| `No evidence in DB` | **`no evidence in DB`** | Unchanged honesty surface, now on `why_it_fits` |
| `sample data — pipeline pending` | unchanged | Still applies to sampled corpora |
| `insufficient data for saturation` | unchanged | Performance seat, below the niche asset threshold |
| `PROVISIONAL` | unchanged | Evidence coverage < 40% |
| — | **`no claims used`** *(new)* | Campaign detail uses no approved claim |
| — | **`Claims not confirmed yet`** *(new)* | Brand card ✓ withheld until one-tap confirmation |

**Working-step labels (step 3 rumination).** Verbatim, in order, each a separate labeled step:

```
retrieving evidence  →  drafting options  →  council review
```

---

## 5 · Agent-facing labels

Artifact titles, the agent's own words, and the one question per turn.

| Artifact type | Card title | Actions |
| --- | --- | --- |
| `intake_progress` | **`Brief progress`** | — (display only) |
| `campaign_option` | **`Campaign option — <name_line>`** | `Approve` · `Regenerate` |
| `template_picker` | **`Style reference — optional`** | `Use <id>` · `Skip — no style constraint` |
| `campaign_detail` | **`Campaign detail`** + version chip `v2` | `Generate creative` |
| `model_confirm` | **`Before we generate`** | `Generate — $X.XX` with an explicit count |
| `creative_set` | **`Creative set`** | `Accept all` · `Re-roll` · `Use as reference` |
| `ad_card` | **`Ad Card`** | `Download bundle` · `Mark Live` |

**Progress chips (path b).** Exactly three, in order, glyphs fixed:

```
Product ✓ · Campaign … · Brand ✗
```

`✓` filled · `…` in progress · `✗` not started.

**Agent questions — at most one per turn.**

| Moment | String |
| --- | --- |
| Step 5, after the detail opens | **`Generate creative?`** |
| Step 7, on the model-confirm card | **`One creative, or variants?`** |

**Agent-side renames (prompt files and any text they surface):**

| Old | New |
| --- | --- |
| `intake` | `campaign_intake` — "parser with eyes; no advice" |
| `planner` | `campaign_planner` — passes `options` / `detail` / `refine` |
| `feedback` (single critic) | `council/seat_performance` · `council/seat_brand` · `council/seat_platform` · `council/chair` |
| `creative_director` | `campaign_orchestrator` — inherits rules 7–12, adds 13–15 |
| Seat display names | **`Performance Marketer`** · **`Brand Guardian`** · **`Platform Specialist`** · **`Chair`** |

---

## 6 · Status and lifecycle labels

**Campaign lifecycle** (My Campaigns row chip) — five states, replacing the four-state Plans lifecycle:

| Old (Plans) | New (My Campaigns) | Row hint |
| --- | --- | --- |
| — | **`Draft`** *(new)* | `Intake cards aren't finished — no approved campaign detail yet.` |
| `Planned` | **`Planned`** | `An option is approved and the campaign detail exists — ready to produce.` |
| `In production` | **`In production`** | `Generation is running in this campaign's thread.` |
| `Ready` | **`Ready`** | `Ad Card assembled — export the bundle or mark it live.` |
| `Posted` | **`Live`** | `Running — paste results so performance memory learns.` |

**Ad Card status** is a separate, narrower lifecycle: `draft` · `ready` · `live`. Do not conflate the two
in copy — a campaign can be `In production` while a card is still `draft`.

**Results nudge.**

| Old | New |
| --- | --- |
| `Posted >72h — paste results in My Space to feed the next plan` | **`Live ad card created <age> ago — no results pasted yet.`** |
| — | **`Live — paste results to feed performance memory`** *(new, Ad Card chip)* |

---

## 7 · DISABLED STATES

Every control below is **rendered, visibly inert, and explains itself**. None of them is hidden, and none
of them is a dead click — an enabled-looking control that swallows the tap and does nothing is the
failure mode this list exists to prevent. Strings are user-facing and exact.

Legend: **[shipped]** = quoted verbatim from `plotline-web`. **[spec]** = specified here, not yet in code.

| # | Control | When it is disabled | Exact string shown |
| --- | --- | --- | --- |
| 1 | **Settings (gear) icon** in the prompt modal | **Always, for now.** Model selection is not user-configurable; the stack is fixed (NB2/NB images, Veo 3.1 Fast video, Kokoro/MiniMax voice). | **[shipped]** `Model selection coming — using recommended models` |
| 2 | **Settings (gear) icon** on the model-confirm card | Always — same reason, same note. Mirrors `ModelConfirm.settings_note`. | **[shipped]** `Model selection coming — using recommended models` |
| 3 | **Template picker**, when `samples/templates/manifest.json` is empty or absent | No curated samples supplied yet. **Skip stays enabled** — an empty manifest must never block the flow. | **[shipped]** `No templates available — the samples folder is empty. Skip proceeds with no style constraint.` |
| 4 | **Template primary button**, before a thumbnail is chosen | Nothing picked yet. | **[shipped]** `Select a template` |
| 5 | **Generate next creative** (My Campaigns), before a campaign detail exists | No approved option and no detail — there is nothing to generate from. | **[shipped]** `Generate next creative is off until an option is approved and a campaign detail exists — Open is the action that gets you there.` |
| 6 | **Paste results / Log results**, before the campaign is Live | Results only make sense against a running ad. Current build **hides** the block on non-Live rows; specify the inert form instead, so the loop is discoverable before it is usable. | **[spec]** `Paste results once the campaign is Live.` |
| 7 | **Attach (+ Images 0/8)** in the prompt modal, before the thread exists | The prompt has nowhere to go yet; product images belong in the Product card. | **[shipped]** `Attachments open with the thread — product images go in the Product Details card` |
| 8 | **Prompt field / Send**, before the thread exists | Same reason. The placeholder states it rather than looking live. Two variants by step. | **[shipped]** `The conversation starts once the cards are filled…` · `The conversation starts in the campaign thread…` |
| 9 | **Start campaign**, before all three cards are ✓ | Context is incomplete; `.complete` requires product, campaign, brand **and** `claims_confirmed`. | **[shipped]** `Still needed: <card names>.` |
| 10 | **Brand card ✓**, before claims are confirmed | Extracted claims are candidates until the user taps to confirm. | **[shipped]** `Claims not confirmed yet` |
| 11 | **Generate** on the model-confirm card, before a count is chosen | The single-vs-variants question always precedes generation. | **[shipped]** `Generate` (inert until a choice is made; becomes `Generate — $X.XX`) |
| 12 | **Avatar Studio** nav item | Phase 2, not built. | **[shipped]** `PHASE 2` badge |

**Tooltip mechanics.** A native `[disabled]` button swallows pointer events, so the tooltip never
appears. Controls that must explain themselves use `aria-disabled="true"` with no click handler
instead — focusable, announced as disabled, tooltip reachable. This applies to #1, #2 and #7.

---

## 8 · Terms that do NOT change

Guardrail against over-renaming. Leave these alone:

- **Content Studio · Creative Studio · Avatar Studio · My Space** — other modes, other copy. Their
  `series`, `concept`, `plan` and `Post Card` vocabulary stands.
- **DIY mode** at `/creative` — keeps the Addendum-02 confidence-card → route → script sequence and its labels.
- **Honesty surfaces** — `PROVISIONAL`, `no evidence in DB`, `insufficient data`, `sample data — pipeline
  pending`. Same words, same meaning, now on campaign artifacts.
- **`Approve` / `Regenerate`** — the approval verbs are stable across studios.
- **Thread numbering** — `<Name> — 01`, zero-padded, never renumbered.
- **Credits** — the spend unit stays `credits`; generation costs quoted to the user in-card stay `$`.
- **Model names** — NB2/NB, Veo 3.1 Fast, Kokoro, MiniMax. Never paraphrased into "our image model".
