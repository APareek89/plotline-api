# v3 — ARTIFACT SPEC: what the user reviews at every gate

**Status:** spec of record for the v3 pipeline artifacts. Extends Addendum-03; does not
replace it. Everything here is a **delta on existing schemas in `app/schemas.py`** —
`CampaignContext`, `CampaignOption`, `CampaignDetail`, `ModelConfirm`, `AdCard` and the
`ArtifactType` union already exist and are kept.

**The organising principle.** Cost is not evenly distributed across the pipeline. Text
stages are effectively free, image stages are cheap, video stages dominate. So every
expensive commitment is preceded by a cheap, rejectable artifact. **A gate artifact must
cost less than 5% of the stage it protects, or it is not a gate — it is more work.**

**The invariant that makes settings safe.** Turning a gate off means *the flow does not
pause*. It never means the artifact is not produced. Downstream stages consume upstream
artifacts (keyframes cannot exist without a shot board), and the Activity/`generation_log`
audit trail must stay complete. Only a small, explicitly-marked set of gates may be `skip`ped
as *work*, and those always surface what is being traded away.

---

## 0. Pipeline: current → v3

```
CURRENT  name → paths → cards → options → templates → detail → generate → creative → done

v3       name → paths → cards → brief → options → templates → script → detail → canon
                                 NEW                            NEW      (=board)  NEW
              → keyframes → generate → creative → qc → done
                   NEW                             NEW
```

`detail` keeps its stage id — its artifact is upgraded in place into the shot board. Five
stages are inserted: `brief`, `script`, `canon`, `keyframes`, `qc`.

**Stage-ordering rationale.** The board must exist before canon, because the board is what
names which characters, products and environments the campaign needs. Canon must exist before
keyframes, because keyframes compose approved canon. Keyframes must exist before generate,
because animating an unapproved frame is the single most expensive mistake in the pipeline.

**New `ArtifactType` values** (append to the union in `app/schemas.py`, do not reorder):
`campaign_brief`, `hook_rack`, `style_block`, `canon_sheet`, `keyframe_board`,
`qc_report`, `variant_matrix`.

**Upgraded in place:** `campaign_detail` (becomes the shot board), `creative_set` (gains take
diagnosis), `campaign_option` (gains risk + compliance flag).

---

## 1. `campaign_brief` — Stage `brief`

**Gate question:** "Are we making the right ad?" — and it locks the format constraints
everything downstream inherits.
**Cost:** 0. Derived by the intake agent from the three completed cards; no new user typing
unless a field is genuinely absent.
**Why it's new:** `CampaignContext` already holds objective, audience and platforms, but they
sit in three separate cards and there is no single artifact the user can approve or reject.
Four fields that materially change downstream output have nowhere to live today.

### Schema

```python
class CampaignBrief(Strict):
    # derived from CampaignBlock — echoed, not re-asked
    objective: CampaignObjective
    audience: str
    platforms: list[Platform]
    creative_type: CreativeType

    # NEW — the fields with no home today
    target_metric: Optional[str]        # "2,000 units in 6 weeks at <=Rs420 CAC" | None
    audience_current_belief: str        # D9: what they believe BEFORE this ad
    single_message: str = Field(max_length=160)   # the one thing they should remember
    brand_role: str                     # D4: how the brand functions IN the story
    offer_cta: str

    # format constraints — locked here, propagated everywhere
    aspect_ratios: list[str] = Field(min_length=1)   # validated against AdCard's spec table
    duration_s: Optional[float]         # video only
    languages: list[str] = Field(min_length=1)       # BCP-47; first = primary
    multi_format_policy: Literal["safe_area", "native_regen"] = "safe_area"

    mandatories: list[str] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)

    # appetite — scope is cut to fit this, not the reverse
    budget_credits: Optional[float]
    proof_points: list[str]             # subset of confirmed approved_claims
```

### Rendering (right panel, Context tab + thread card)
A single definition list, two columns on desktop. Order matters — read top to bottom it should
argue for itself:

`Objective` (with target metric inline) → `Audience` (with **current belief** on its own
sub-line, visually secondary) → `Single message` (largest type on the card; this is the
artifact's centre) → `Brand's role` → `Product truth` (proof points as chips) → `Offer / CTA`
→ `Format` (ratio · duration · language chips) → `Mandatories` / `Guardrails` (two-column,
guardrails prefixed with a "never" marker) → `Appetite`.

`single_message` renders at ~1.3× body size with the label **"The one thing they remember."**
If a user reads only one line of this card it must be that one.

### Validation
- `single_message` containing " and " joining two distinct propositions → warn (D2), do not block.
- `aspect_ratios` ⊄ `{"9:16","1:1","16:9","4:5"}` → block. Reuse `AdCard._spec_table`; do not
  duplicate the list.
- `proof_points` ⊄ `brand.approved_claims` → block, naming the unmapped claim. Same rule as
  `_validate_detail`'s `claims_used` check — extend the existing helper rather than writing a
  second one.
- `brand_role` that does not name the product's function in the narrative → warn.

### Actions
`Approve` · `Edit` (opens the relevant card) · `Regenerate brief`

---

## 2. `campaign_option` — Stage `options` *(upgrade)*

Existing schema and council review are correct and stay. Three fields are added.

```python
class CampaignOption(Strict):
    # ... existing: option_id, name_line, description, storyline,
    #     objective_echo, why_it_fits, evidence
    territory: Literal["ritual","problem","heritage","social_proof",
                       "demo","contrarian","aspiration"]   # NEW
    risk: str                                              # NEW — "why this might fail"
    compliance: Literal["clear","constrained","blocked"]    # NEW
    compliance_note: Optional[str] = None                   # NEW
```

**`territory`** exists to enforce spread. The planner must not return three options from the
same territory — that is one idea in three costumes. Validate: `len(set(territories)) == len(options)`.

**`risk`** forces the system to argue against its own suggestion. One sentence, concrete.
An option with no stated risk is an option nobody has stress-tested.

**`compliance`** is checked at birth against `approved_claims` / `banned_words`. An option
whose whole premise needs an unapproved claim is `blocked` and renders struck-through with the
reason — killed at zero cost rather than at the generation stage.

**Rendering:** existing card, plus a territory chip top-right, a `Risk` line in muted type
below `why_it_fits`, and — when not `clear` — a compliance banner. `blocked` options render
but cannot be approved; the Approve action is disabled with the note as its tooltip.

---

## 3. `style_block` — Stage `templates` *(new, emitted alongside `template_picker`)*

**Gate question:** "Does the world look right?"
**Cost:** 0 credits for the block itself; 1 optional look-dev image if the user asks to see it.
**Why it's new:** `TemplateRef.style_descriptors` is a loose list. What downstream prompts need
is a **named, versioned, verbatim string** injected into every visual prompt in the campaign.
This is the cheapest consistency mechanism available and is currently absent.

```python
class StyleBlock(Strict):
    id: str                    # "sb_warm_evening"
    grade: str                 # colour behaviour
    light: str                 # source, direction, quality, time of day
    lens: str                  # focal length, aperture, depth, distortion
    texture: str               # grain + REALISM DIAL (see below)
    motion: str                # handheld drift vs locked-off
    negatives: list[str]       # carried here so they cannot be forgotten per-scene
    realism: Literal["editorial","natural","documentary"] = "natural"
    derived_from: Optional[str] = None   # TemplateRef.id, or None when Skip was chosen
    version: int = 1

    def as_prompt(self) -> str:
        """Verbatim injection. Prepended to every visual_prompt in the campaign."""
```

**The realism dial is load-bearing and counter-intuitive.** Faces read as credible *because of*
pores, asymmetry and ordinary imperfection; prompts asking for flawless skin and idealised
symmetry produce the synthetic look users recognise instantly. The dial maps to `texture` text:
`documentary` → "visible skin pores, natural asymmetry, no beauty smoothing, no retouching".
Ship it as the three-position control, not as a phrase the user must know to type.

**Skip semantics unchanged:** choosing Skip on templates yields `derived_from=None` and a
neutral default block — *not* an absent block. Every campaign has a style block; only its
source varies.

**Rendering:** a monospace card showing the exact injected string, with the six fields as
editable rows and a "copy block" action. The user should be able to see the literal text that
will reach the model — this is an agency tool and that transparency is a selling point.

---

## 4. `hook_rack` — Stage `script`

**Gate question:** "Does a human talk like this, and does it physically fit?"
**Cost:** 0.
**Why it's new:** script content currently lives in `CampaignDetail.copy_primary` and
`shots[].vo_or_copy`, produced once. There is no hook variation and no feasibility check —
so over-stuffed dialogue only surfaces after video has been paid for.

```python
class ScriptLine(Strict):
    slot: str                  # "hook" | "beat_01" | "cta"
    t_in: float
    t_out: float
    text: str                  # native script (Devanagari for hi, Tamil for ta, ...)
    emotion: str               # REQUIRED — see below
    words: int
    wps: float                 # computed
    wps_verdict: Literal["pass","tight","fail"]
    claim_refs: list[str] = Field(default_factory=list)

class HookRack(Strict):
    language: str
    body: list[ScriptLine] = Field(min_length=1)    # the LOCKED body
    hooks: list[ScriptLine] = Field(min_length=1)   # 1..N openers against that body
    selected_hook_slot: str
    loanwords_kept: list[str] = Field(default_factory=list)
    total_duration_s: float
```

### The three lints — all free, all run before any generation

**W1 · Words-per-second.** Pure arithmetic, highest ROI check in the product. Per-language
thresholds (configurable, not hardcoded in the prompt): dialogue exceeding the threshold
produces rushed delivery, truncation, and lip-sync collapse. `fail` blocks the gate and the
agent must propose a trimmed line; `tight` warns. Store thresholds in one place and inject
them into the prompt from that source, exactly as `_RECEIPT_CUES` is injected into the planner
prompt today — *prompt and check must not be able to drift.*

**W2 · Emotion required.** `emotion` is non-optional. Without an explicit emotion the video
model carries the previous neutral read into the new line regardless of what the line says.
Empty or generic ("normal", "good") → block.

**W3 · Loanword preservation.** For non-English languages, terms the audience uses in English
stay in English. Forced translation is the tell that a local-language ad was machine-made. The
agent lists what it kept in `loanwords_kept`; the user can correct it.

### One locked body, many hooks
This structure is what makes variant testing affordable: a hook swap re-renders **one shot**,
not the film. Do not let the agent regenerate the body when producing hook variants — validate
that `body` comes back byte-identical, reusing the existing untouched-options diff check from
the refine loop.

### Rendering
Two stacked panels. **Body** on top: one row per line with timecode gutter, the line in its
native script at reading size, emotion as a muted italic suffix, and a right-aligned
`words/duration = w/s` chip coloured by verdict. A `fail` row shows the proposed fix inline
with a one-tap Apply. **Hook rack** below: radio list, each hook with its own w/s chip and a
one-word territory tag; the selected one is what the board consumes.

---

## 5. `campaign_detail` → the **Shot Board** — Stage `detail` *(major upgrade)*

**Gate question:** "Approve the film before it exists." **The last free gate.**
**Cost:** 0 — and it displays the full cost of everything after it.

`DetailShot` today is `{slot, duration_s, visual_prompt, vo_or_copy}`. That is a script
fragment, not an approvable board. Note that `Shot` and `ConsistencyPlan` already exist in
`app/schemas.py` as Phase-2 contracts with richer vocabulary (`keyframe_prompt`,
`motion_prompt`, `boundary`, `wardrobe_lock`, `lighting_lock`) — **build on those rather than
inventing a third shot vocabulary.**

```python
class BoardShot(Strict):
    slot: str
    duration_s: float = Field(gt=0, le=10)
    beat: str                          # ONE beat — see B1
    dialogue_ref: Optional[str]        # ScriptLine.slot
    action: str
    camera: Literal["static","push_in","pull_out","pan","tilt",
                    "handheld","orbit","macro_slide"]
    shot_size: Literal["ECU","CU","MCU","MS","WS","EWS"]
    emotion: str
    cast_refs: list[str] = Field(default_factory=list)     # canon ids
    product_refs: list[str] = Field(default_factory=list)
    env_refs: list[str] = Field(default_factory=list)
    keyframe_prompt: str               # style_block injected server-side, not by the model
    motion_prompt: str
    model_route: str                   # key into config.MEDIA_MODELS
    route_reason: str                  # the shot's HARDEST requirement
    slots_used: int                    # reference-budget accounting
    est_cost_usd: float

class ShotBoard(Strict):
    creative_type: CreativeType
    shots: list[BoardShot] = Field(min_length=1)
    copy_primary: str
    cta: str
    claims_used: list[str]             # must be subset of confirmed claims (existing rule)
    style_block_id: str
    lints: "BoardLints"
    est_total_usd: float
    version: int = 1
    changes: list[str] = Field(default_factory=list)
```

### The three board lints — the thing that makes this a gate rather than a document

```python
class BoardLints(Strict):
    runtime: LintResult    # B2
    beats: LintResult      # B1
    slots: LintResult      # B3
    motion: LintResult     # B4
```

**B1 · One clear beat per clip.** A clip containing two unrelated actions ("macro insert, then
back to her") degrades reliably. Detect a beat carrying a sequential conjunction or two
distinct subjects; **auto-split into `slot_a` / `slot_b`** and record it in `changes[]`.

**B2 · Runtime budget.** `duration / avg_beat` decides how many shots exist, which decides how
many characters the film can carry. Cast size is an **output** of duration, not an input to it.
When `len(distinct cast_refs) × 2 > len(shots)`, the board cannot give any character enough
screen time — flag and propose which character to cut. This is the check that prevents a
20-second film with four protagonists.

**B3 · Reference-slot budget.** Generators cap how many references one call carries, and a
multi-view canon sheet consumes more than one slot. When a shot over-subscribes, the board must
**force a decision** — drop a reference or split the shot — and record which. It must never
silently truncate the reference list, because a silently dropped product reference is exactly
how label and geometry drift enter a campaign. Cap is per-model and belongs in
`config.MEDIA_REF_SLOTS`, not in the prompt.

**B4 · Motion complexity.** A multi-stage camera path (descend → orbit → push) exceeds what one
short clip holds. Flag compound `camera` descriptions and propose the split.

### Model routing
Route each shot on its **hardest** requirement, not on a global default: legible on-pack text
and fine geometry → the image-pro tier; dialogue to camera → the lip-sync-capable video model;
simple motion → the fast tier. `route_reason` is user-visible and is a large part of why an
agency will trust the tool. Model ids come from `config.MEDIA_MODELS`; costs from
`config.MEDIA_COST_USD`. **Never let the model invent a model id or a price.**

### Rendering
A table, not a card stack — agencies read boards as tables. Columns: `#` · `Dur` ·
`Beat / action` (with emotion as a sub-line) · `Camera / size` · `Refs` (canon chips, with a
slot counter) · `Route` · `Cost`. Below the table, a **lint panel** rendering each lint as
`PASS` or as the specific finding with its applied resolution. Bottom-right, the number the
user is actually approving:

```
BOARD TOTAL   <n> credits  (<takes>/shot)
+ CANON       <n>          (one-time, reusable)
+ KEYFRAMES   <n>
= THIS SPOT   <n> of <budget_credits>
```

### Why this is the artifact that matters most
Everything after it is derived from it. A hook swap edits one row and re-renders one shot. If
variants are derived from the finished video instead of the board, every axis multiplies a full
re-render. **The board, not the MP4, is the source of truth** — that single decision is the
difference between variant testing being a habit and being a quote.

---

## 6. `canon_sheet` — Stage `canon`

**Gate question:** "Is this the right cast, and is this actually our product?"
**Cost:** the largest one-time image spend; amortised across every future campaign.
**Why it's new:** only `ProductBlock.image_upload_ids` exists today. Characters, environments
and voices have no home — so a campaign with a recurring presenter has no way to keep them
consistent, which is the defect users notice first.

One schema, four kinds. Each is a **first-class library entity with its own surface**, reusable
across campaigns — not a modal inside one thread. This is the retention mechanic: campaign two
is far cheaper than campaign one *because the canon already exists.*

```python
class CanonSheet(Strict):
    id: str                    # "@priya", "@tamra-classic"
    kind: Literal["character","product","environment","voice"]
    label: str
    brief: str                 # casting / product / location / voice descriptor
    asset_ids: list[str]       # generated or uploaded views
    coverage: dict[str, bool]  # required views -> present
    locks: list[str]           # what must not change between shots
    slot_cost: int = 1         # how many reference slots this consumes (B3)
    risk_notes: list[str] = Field(default_factory=list)
    rights: Literal["owned","consented","fictional","unverified"] = "unverified"
    consent_ref: Optional[str] = None
    version: int = 1
```

### Required coverage per kind

| Kind | Coverage keys | Locks (typical) |
|---|---|---|
| `character` | `front`, `three_quarter_l`, `three_quarter_r`, `profile_l`, `profile_r`, `full_front`, `full_rear` | base wardrobe, hair, distinguishing marks |
| `product` | `front`, `rear`, `left`, `right`, `top`, `base`, + ≥3 `three_quarter_*` when `risk_notes` is non-empty | proportions, label text, finish/pattern density |
| `environment` | `establishing`, plus ≥3 angles | light direction, time of day, fixed props |
| `voice` | n/a — auditioned, not viewed | pitch, accent, pace |

### Geometry risk — the product-specific check
Product accuracy is harder than face consistency, and packaging text is the hardest part of it.
`risk_notes` names the features that mutate: hinges, threads, lock holes, opening direction,
attachment points, pattern density, engraved marks. A product with a non-empty `risk_notes`
requires extra ¾ views and an explicit "do not change" instruction injected into every prompt
that references it.

### Voice card specifics
- Descriptor fields: pitch · age · energy · accent · pace · emotion.
- **Audition on the real final line**, never on a vendor demo reel. Library metadata says
  nothing about brand fit. The audition player must play `HookRack.body`, not sample text.
- `native_review: {locale: reviewer_name | null}` — a **blocking** field per non-English locale.
  Synthetic fluency is not evidence of correctness, and a non-speaker cannot catch the failure.

### Wiring to the rights ledger
`rights` and `consent_ref` link to the intake rights ledger (§10). `unverified` on a `character`
whose likeness derives from an uploaded real person is a `policy_risk` kill flag at council.

### Skipping is a costed choice, never a silent default
The system may offer "generate without a sheet" — practitioners genuinely do this under time
pressure. The failure is doing it invisibly. Surface it as an explicit choice showing what is
traded: *"Skip the sheet — saves the sheet cost and a few minutes; expect identity drift after
roughly three shots."* Record the choice on the campaign so a later drift complaint is
answerable.

### Rendering
A grid of sheet cards with a coverage meter (`7/7 views`), the locks as chips, a slot-cost
badge (feeding B3), a rights badge, and — for products with `risk_notes` — a red geometry-risk
strip listing the at-risk features. Sheets live in a **My Brand → Canon** library surface as
well as in-thread.

---

## 7. `keyframe_board` — Stage `keyframes`

**Gate question:** "Lock the film as stills."
**Cost:** roughly a fiftieth of the motion it protects.
**Why it's new — and why it is the highest-value single addition in this spec:** the pipeline
currently goes `detail → generate`. There is no still-approval gate. A composition, identity or
label defect therefore surfaces *after* video has been paid for, when it costs the clip plus
its takes plus the time to notice it in motion — where a wrong pattern or a mutated label is far
harder to see than in a still.

**This gate is a hard gate. No video generation without every keyframe approved.** For
`creative_type == "image"` the keyframes *are* the deliverable and the stage terminates the
creative path.

```python
class Keyframe(Strict):
    shot_slot: str
    asset_id: str
    picked_from: int              # generated N, chose 1
    refs_used: list[str]
    checks: dict[str, Literal["pass","fail","na"]]
    # keys: face, hands, product_geometry, label_legibility, composition, safe_area
    repairs: list[str] = Field(default_factory=list)
    cost_usd: float

class KeyframeBoard(Strict):
    frames: list[Keyframe]
    all_approved: bool
    total_cost_usd: float
```

### Region-level repair, not full regeneration
When one element is wrong, replace **that element** and declare the rest unchanged.
Regenerating the whole frame discards everything already correct. `repairs[]` records each
targeted fix so the audit shows what changed and why.

### Constraint-preserving enhancement
If any prompt-enhancement step runs before generation, it **must not touch** canon references,
`claims_used`, or the style block. Show a diff of what the enhancer changed and lock the
protected fields. Enhancers silently rewriting identity and product constraints is a real and
common failure.

### Rendering
A contact sheet: one tile per shot at the campaign's true aspect ratio, with a check strip
along the bottom of each tile (six dots, coloured). A failed check expands to name the defect
and offer `Repair region` / `Regenerate` / `Accept anyway`. Header shows
`n/m approved` and a blue **Approve board → Generate** pill that stays disabled until
`all_approved`.

---

## 8. `creative_set` — Stage `creative` *(upgrade)*

Existing per-asset accept / re-roll / seam QA is correct and stays. One addition: **rejects
must be classified.**

```python
class TakeReject(Strict):
    cause: Literal["wrong_reference","ambiguous_action","too_many_actions",
                   "inconsistent_geometry","unsuitable_model"]
    detail: str
    proposed_fix: str
    variable_changed: str     # exactly ONE per retry
```

**"Make it better" is not a repair instruction.** Ship the taxonomy as the actual reject UI:
the user picks a cause and the system proposes the corresponding prompt or reference patch.
Each cause has a distinct fix — `wrong_reference` re-selects canon, `too_many_actions` splits
the beat back to B1, `unsuitable_model` re-routes per the board's `route_reason`.

**Change one variable at a time.** `variable_changed` is required on every retry and must
differ from the previous retry's. Two simultaneous edits make the next result uninterpretable.

**Review the whole clip, not frame one.** Frame-chained transitions start clean and go
artificial mid-move on hard contrast shifts. The accept control must require scrubbing to the
end, or at minimum show a last-frame thumbnail alongside the first.

---

## 9. `qc_report` — Stage `qc`

**Gate question:** "Is it safe to publish — and under a deadline, which defects may ship?"
**Cost:** 0; mostly automated detectors.
**Why it's new:** seam QA and `AdCard._spec_table` cover fragments. There is no consolidated
report, and — more importantly — no severity model. A flat pass/fail list either blocks a dated
campaign over a background continuity slip, or gets ignored wholesale because it cries wolf.

```python
class QCFinding(Strict):
    tier: Literal["blocking","fix_before_ship","accepted"]
    check: str
    detail: str
    locator: Optional[str]        # asset_id or "asset_id@2.4s" for jump-to-frame
    resolution: Optional[str]
    rationale: Optional[str]      # REQUIRED when tier == "accepted"

class QCReport(Strict):
    findings: list[QCFinding]
    automated: dict[str, Literal["pass","fail","skip"]]
    verdict: Literal["cleared","held"]
    locales: dict[str, Literal["cleared","held"]]
```

### The tiering test
> **Does this defect damage trust, product truth, or meaning?**

Yes → `blocking`. Fixable cheaply → `fix_before_ship`. Neither → `accepted`, **with a written
rationale**. This makes "ship it anyway" an auditable decision rather than a shrug, which is
exactly what an agency needs when a client asks later.

### Blocking, always
- Any uncleared item in the rights ledger (§10). An unlicensed music bed blocks delivery.
- Any claim in final copy not in confirmed `approved_claims` — re-checked at the end, because
  copy drifts during production.
- Any banned word.
- Missing native-speaker sign-off for a non-English locale.

### Automated detectors
Identity drift across shots · hand/finger anomalies · label OCR vs the expected string ·
lip-sync offset · loudness · duration and ratio vs the brief. Report each as pass/fail/skip;
`skip` is honest when the detector is not wired yet and must render as `skip`, never as `pass`.

### Platform policy — the honest limit
The Platform seat flags `policy_check_required` but never asserts what a rule says (see
Doctrine §1). The QC report surfaces that as a `fix_before_ship` finding reading *"needs a
policy check against <platform>'s current ad rules"* — an instruction to a human, not a verdict.

### Rendering
Three collapsible tiers, colour-coded, blocking expanded by default. Every finding has a
jump-to-frame link. `accepted` findings render their rationale inline — an accepted defect
without a visible reason looks like negligence. Footer: per-locale verdict chips.

---

## 10. Rights ledger — extends `BrandBlock`, Stage `cards`

Not a new artifact; a **new block on an existing card**, because it belongs at intake.

```python
# BrandBlock additions
rights_ledger: list[RightsEntry] = Field(default_factory=list)

class RightsEntry(Strict):
    asset_kind: Literal["logo","product_photo","likeness","voice","music","font","stock"]
    ref: str
    status: Literal["owned","consented","licensed","fictional","not_cleared"]
    scope: Optional[str]          # "paid social, 24mo"
    evidence_ref: Optional[str]
```

**Why at Stage 0, not at QC.** Platform terms may grant broad rights over uploaded media, and
no generator promises to indemnify unauthorised likeness use. Discovering at QC that the music
is uncleared means the ad is finished and unshippable. Consent and ownership are settled before
assets enter the pipeline.

`not_cleared` never blocks *planning* — it blocks *delivery*, and the block is visible from the
moment it is recorded.

---

## 11. `variant_matrix` — Stage `creative` → `done` *(new; extends `ModelConfirm`)*

`ModelConfirm.variants_proposed` and `variant_group_id` already exist and are correct. What is
missing is the **reuse map** — the number that decides whether variant testing is affordable.

```python
class VariantCell(Strict):
    variant_id: str
    axis: Literal["hook","language","ratio","cta","duration"]
    delta: str
    hypothesis: str
    shots_rerendered: list[str]   # board slots — often exactly one
    shots_reused: int
    cost_usd: float
    localisation_tier: Optional[Literal["dub","revoice","recast"]] = None

class VariantMatrix(Strict):
    cells: list[VariantCell]
    baseline_cost_usd: float      # same set re-rendered from scratch
    matrix_cost_usd: float        # derived from the board
```

### The three localisation tiers
Named and priced separately because they are genuinely different products:

| Tier | What changes | Honest limitation |
|---|---|---|
| `dub` | audio only | lip-sync drift visible; acceptable for VO-led creative |
| `revoice` | new VO + captions + lip re-sync | — |
| `recast` | new talent, wardrobe, environment | full board re-render; the honest option when a market needs a different face, not just a different language |

Localisation is creative adaptation, not translation. Offering only `dub` and calling it
localisation is the thing agencies will notice first.

### Rendering
A grid, one row per cell: variant id · axis chip · delta · `re-renders n of m` · cost. Footer
shows both totals and the ratio. That ratio is the business case for the shot board and should
be visible.

---

## 12. Cross-cutting rendering rules (apply to every artifact above)

1. **Never restate an artifact's content in the agent's text.** Existing `shared_policy` rule 10.
   The card is the content; the text is one line of framing.
2. **Every artifact is editable in place**, not regenerate-only. If the only response to a wrong
   scene row is "regenerate", the product is a slot machine. Row-level edit, everything else
   untouched — reuse the existing byte-identical diff check from the refine loop.
3. **Every dynamic surface defines empty, loading and error states.** Named explicitly; their
   absence is the most common shipped defect. Labelled agent steps, never a bare spinner —
   already an Addendum-03 rule, extended to every new stage.
4. **Cost is shown before it is spent, every time.** Existing invariant; the new stages inherit it.
5. **Jumping back must ripple, never silently reset.** Editing the brief after keyframes exist
   invalidates downstream artifacts. Show exactly what is invalidated and what re-rendering
   costs, then let the user decide. Never auto-spend on a ripple.
6. **Version every artifact and record the doctrine version** on anything the council touched.
