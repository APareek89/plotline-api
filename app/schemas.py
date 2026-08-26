"""§3.9 agent output contracts — pydantic-enforced.

Invalid agent output → re-run with the validation error (max 2 retries) → hard
fail surfaced to the user. Never silently accepted, never silently repaired.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- Evidence ---

EvidenceTag = Literal["REF", "STAT", "TREND", "PRINCIPLE"]


class Evidence(Strict):
    """The atom everything reuses."""

    tag: EvidenceTag
    source_id: str  # asset:A118 | chunk:C0421 | stat:S88 | trend:T12 | "model" (PRINCIPLE only)
    claim: str  # one sentence, numbers only if present in source
    as_of: Optional[date] = None

    @model_validator(mode="after")
    def _model_source_only_for_principle(self) -> "Evidence":
        if self.source_id == "model" and self.tag != "PRINCIPLE":
            raise ValueError('source_id "model" is only allowed with tag PRINCIPLE')
        return self


# ---------------------------------------------------------- CreatorContext ---

Mode = Literal["series", "one_time"]
Objective = Literal["followers", "impressions", "engagement", "conversions"]
ContentType = Literal["text", "text_image", "text_video"]
Platform = Literal["instagram_reels", "youtube_shorts", "tiktok", "instagram_feed", "linkedin", "x"]


class Cadence(Strict):
    type: Mode
    posts_per_week: Optional[int] = Field(default=None, ge=1, le=14)
    weeks: Optional[int] = Field(default=None, ge=1, le=12)
    concept_count: Optional[int] = Field(default=None, ge=1, le=20)  # one_time only

    @model_validator(mode="after")
    def _shape(self) -> "Cadence":
        if self.type == "series":
            if not (self.posts_per_week and self.weeks):
                raise ValueError("series cadence needs posts_per_week and weeks")
        else:
            if not self.concept_count:
                raise ValueError("one_time cadence needs concept_count")
        return self

    @property
    def slots(self) -> int:
        if self.type == "series":
            return int(self.posts_per_week) * int(self.weeks)
        return int(self.concept_count)


class UploadExtraction(Strict):
    """What intake saw in one upload. Describe, never guess metrics."""

    filename: str
    kind: Literal["past_post", "brief", "screenshot", "brand_asset", "other"]
    observations: list[str] = Field(default_factory=list)


AudienceSophistication = Literal["novice", "practitioner", "expert"]
PositioningDepth = Literal["beginner_guide", "power_user"]


class CreatorContext(Strict):
    """Intake output. A parser with eyes — no advice fields allowed."""

    name: str  # user-given series/post name (its handle in Plans)
    mode: Mode
    content_area: str
    description: Optional[str] = None
    objective: Objective
    target_audience: Optional[str] = None
    platforms: list[Platform] = Field(default_factory=list)
    cadence: Cadence
    content_type: ContentType
    # Addendum-01 §7.1 — required for NEW intakes (enforced in validate_intake;
    # Optional here so pre-addendum stored series still load):
    audience_sophistication: Optional[AudienceSophistication] = None
    tool_access: Optional[str] = None  # "what can you actually demo"
    positioning_depth: Optional[PositioningDepth] = None
    style_notes: list[str] = Field(default_factory=list)  # extracted from uploads: visible style/tone/format only
    upload_extractions: list[UploadExtraction] = Field(default_factory=list)
    brand_rules: list[str] = Field(default_factory=list)  # claims/tone rules/banned words from briefs
    notes: list[str] = Field(default_factory=list)  # anything unmappable — never forced into enums
    clarifying_questions: list[str] = Field(default_factory=list, max_length=3)


# -------------------------------------------------------------------- Plan ---

ElementName = Literal[
    "hook_strength",
    "audience_alignment",
    "retention_structure",
    "differentiation",
    "distribution_triggers",
    "platform_format_fit",
    "creator_fit_feasibility",
    "persuasion_proof",
]

Rating = Literal["H", "M", "L"]
Effort = Literal["S", "M", "L"]


class Hook(Strict):
    verbal: str
    first_frame: str


class ElementScore(Strict):
    element: ElementName
    addressed: bool
    proposed_rating: Optional[Rating] = None
    how_addressed: Optional[str] = None
    why_not: Optional[str] = None
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _honesty_rules(self) -> "ElementScore":
        if self.addressed:
            if self.proposed_rating is None:
                raise ValueError(f"{self.element}: addressed=true requires proposed_rating")
            if not self.how_addressed:
                raise ValueError(f"{self.element}: addressed=true requires how_addressed")
            if not self.evidence:
                raise ValueError(f"{self.element}: addressed=true requires >=1 evidence")
        else:
            if self.proposed_rating is not None:
                raise ValueError(f"{self.element}: addressed=false must have rating null (no forced Med)")
            if not self.why_not:
                raise ValueError(f"{self.element}: addressed=false requires why_not")
        return self


class Concept(Strict):
    id: str
    slot_date: Optional[date] = None
    title: str
    description: str = Field(max_length=300)
    creative_direction: str = Field(max_length=240)
    hook: Hook
    format: str
    platform: Platform
    cta: str
    effort: Effort
    asset_needs: list[str] = Field(default_factory=list)
    element_scores: list[ElementScore]


class SeriesLevel(Strict):
    objective: Objective
    north_star_metric: str
    pillar_mix: Optional[str] = None
    cadence: Cadence
    checkpoint_date: Optional[date] = None


class Plan(Strict):
    series: SeriesLevel
    concepts: list[Concept]
    changes: list[str] = Field(default_factory=list)  # refine pass only — log of what changed


# ---------------------------------------------------------------- Feedback ---

Verdict = Literal["agree", "downgrade", "upgrade"]


class ElementVerdict(Strict):
    element: ElementName
    verdict: Verdict
    final_rating: Optional[Rating] = None  # null only when the element was not addressed
    reason: Optional[str] = None
    evidence: list[Evidence] = Field(default_factory=list)
    evidence_gap: bool = False

    @model_validator(mode="after")
    def _non_agree_needs_backing(self) -> "ElementVerdict":
        if self.verdict != "agree":
            if not self.reason:
                raise ValueError(f"{self.element}: {self.verdict} requires a reason")
            if not self.evidence and not self.evidence_gap:
                raise ValueError(f"{self.element}: {self.verdict} requires evidence or evidence_gap=true")
        return self


class SaturationLens(Strict):
    similar_count: int = Field(ge=0)
    source_id: Optional[str] = None
    note: Optional[str] = None
    # Addendum-01 §7.3: below the niche asset threshold the lens must declare
    # insufficient data instead of pretending to a saturation rating.
    insufficient_data: bool = False


class Lenses(Strict):
    saturation: SaturationLens
    claims_safety: str
    feasibility: str
    platform_policy: str
    # v3: carries the Platform seat's refusal through the chair. `platform_policy`
    # is prose and prose cannot be acted on mechanically; this flag is what the
    # QC report reads to raise "needs a policy check against <platform>'s current
    # ad rules" as an instruction to a human rather than a verdict.
    policy_check_required: bool = False


class Fix(Strict):
    priority: int = Field(ge=1)
    change: str


KillFlag = Literal["hook_low", "unsubstantiated_claim", "policy_risk"]


class ConceptVerdict(Strict):
    concept_id: str
    element_verdicts: list[ElementVerdict]
    lenses: Lenses
    kill_flags: list[KillFlag] = Field(default_factory=list)
    fixes: list[Fix] = Field(default_factory=list)
    ccs_final: int = Field(ge=0, le=100)


class Feedback(Strict):
    concept_verdicts: list[ConceptVerdict]
    # Server-stamped, as on SeatReview. A doctrine change invalidates cached
    # council output, so a Feedback that cannot name its doctrine cannot be
    # safely reused.
    doctrine_version: Optional[str] = None


# ----------------------------------------------------------------- Options ---


class Option(Strict):
    option_id: Literal["A", "B", "C"]
    angle_label: str
    hook: Hook
    creative_direction_delta: str
    ccs: int = Field(ge=0, le=100)


class ConceptOptions(Strict):
    concept_id: str
    options: list[Option] = Field(min_length=3, max_length=3)

    @field_validator("options")
    @classmethod
    def _distinct(cls, v: list[Option]) -> list[Option]:
        ids = [o.option_id for o in v]
        if sorted(ids) != ["A", "B", "C"]:
            raise ValueError("options must be exactly A, B, C")
        labels = {o.angle_label.strip().lower() for o in v}
        if len(labels) != 3:
            raise ValueError("angle_labels must be distinct — different approaches, not reworded copies")
        return v


class OptionsOutput(Strict):
    concept_options: list[ConceptOptions]


# ------------------------------------- Addendum-01 §7.1: format stage (0.5) ---


class FormatOption(Strict):
    """A repeatable series format the planner proposes before any concepts.
    Concepts become episodes of the chosen format(s)."""

    format_id: str  # f1, f2, f3
    name: str
    vehicle: str  # the repeatable mechanic, e.g. "same task: human vs AI, scorecard"
    why_fits: str
    evidence: list[Evidence] = Field(default_factory=list)  # evidence rules apply
    effort: Effort
    cadence_fit: str


class FormatOptions(Strict):
    options: list[FormatOption] = Field(min_length=2, max_length=3)


# --------------------------------- Addendum-01 §7.3: thin-plan escalation ---


EscalationChoice = Literal["seed_inspiration", "broaden_niche", "accept_provisional"]


class Escalation(Strict):
    reason: str
    below_threshold_count: int = Field(ge=0)
    total_concepts: int = Field(ge=1)
    choices: list[EscalationChoice] = Field(min_length=1)


# ---------------------------------- Addendum-01 §05: interaction envelope ---

ArtifactType = Literal[
    "context_summary",
    "inspiration_set",
    "format_options",
    "concept",
    "plan",
    "options",
    "escalation",
    "confidence_card",
    "script_package",
    "brand_kit",
    "final_delivery",
    # Addendum-02 §08
    "asset_prompt",
    "asset_set",
    "voice_options",
    "post_card",
    # Addendum-03 (Marketing Studio v2)
    "campaign_option",
    "template_picker",
    "campaign_detail",
    "model_confirm",
    "creative_set",
    "ad_card",
    "intake_progress",
    # v3 — appended, never reordered
    "campaign_brief",
    "hook_rack",
    "style_block",
    "canon_sheet",
    "keyframe_board",
    "qc_report",
    "variant_matrix",
]

ActionStyle = Literal["primary", "secondary", "danger"]


class ArtifactAction(Strict):
    id: str
    label: str
    style: ActionStyle
    event: str  # e.g. approve | feedback | regenerate | pick_format


class ArtifactEnvelope(Strict):
    """Card wrapper. payload carries the §3.9 schema for the type — validated
    upstream by that schema, transported here as plain JSON."""

    type: ArtifactType
    id: str
    title: str
    payload: dict
    actions: list[ArtifactAction] = Field(default_factory=list)


class QuestionOption(Strict):
    """One tappable answer.

    `event` is the UserAction this option fires, which is the whole point: the
    option IS the call to action. Since 2026-08-26 the artifact panel is a
    READ-ONLY review surface and every approve / regenerate / feedback lives
    here instead, so a user never has to hunt a card for a button. An option
    with no `event` is answer-only — it just posts its label as the reply.
    """

    label: str
    event: Optional[str] = None
    artifact_id: Optional[str] = None
    primary: bool = False


class AgentQuestion(Strict):
    """The ONE question an agent turn may ask, plus how to answer it.

    Still one question per turn — options are alternative answers to the same
    question, not a queue of questions. `free_text` stays true by default
    because a fixed option list that cannot be escaped is how an agent traps a
    user who wants to say something the designer did not predict.
    """

    text: str
    options: list[QuestionOption] = Field(default_factory=list)
    multi: bool = False
    free_text: bool = True
    note: Optional[str] = None


class AgentMessage(Strict):
    """Every agent turn, all studios. Text is a conversational envelope ONLY —
    artifact content is never restated as prose."""

    thread_id: str
    text: str = Field(max_length=280)
    artifacts: list[ArtifactEnvelope] = Field(default_factory=list)
    question: Optional[AgentQuestion] = None  # at most ONE — single field by design

    @field_validator("text")
    @classmethod
    def _two_short_sentences(cls, v: str) -> str:
        enders = sum(v.count(c) for c in ".!?")
        if enders > 2:
            raise ValueError("envelope text must be <=2 short sentences — content belongs in artifacts")
        return v


class UserAction(Strict):
    artifact_id: str
    event: str


class UserEvent(Strict):
    """Both input paths (typed text, button tap) normalize to this."""

    thread_id: str
    type: Literal["text", "action"]
    text: Optional[str] = None
    action: Optional[UserAction] = None
    panel_focus: Optional[str] = None   # artifact id while a detail panel is open
    # Attachments are DATA. They used to be stringified into `text` as
    # "[attached images: upl_x]", which meant the intake agent received an
    # upload id as prose and had to guess what to do with it — so an attached
    # product shot silently never reached product.image_upload_ids.
    upload_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> "UserEvent":
        if self.type == "text" and not self.text:
            raise ValueError("text event requires text")
        if self.type == "action" and self.action is None:
            raise ValueError("action event requires action")
        return self


# --------------------------- Addendum-02 §04/§08: Creative Studio contracts ---


class AssetPrompt(Strict):
    """§08 rule 7: every generation prompt is an artifact BEFORE running.
    User-edited prompt_text is used VERBATIM — never 'improved'."""

    asset_slot: str  # frame_01 | shot_02 | slide_03 | vo_track | cover
    model: str
    prompt_text: str
    cost: float  # estimate shown before any generate (§08 rule 8)
    ratio: str = "9:16"
    duration_s: Optional[float] = None
    locks: list[str] = Field(default_factory=list)  # identity/wardrobe/lighting, verbatim


class AssetItem(Strict):
    asset_id: str
    slot: str
    kind: Literal["image", "video", "audio"]
    preview_url: str
    status: Literal["rendering", "ready", "accepted", "rerolling", "failed"]
    cost: float = 0.0
    note: Optional[str] = None  # e.g. seam-QA flag


class AssetSet(Strict):
    slot: str
    items: list[AssetItem]


class VoiceOption(Strict):
    id: str
    label: str
    preview_url: Optional[str] = None
    tier: Literal["draft", "final"]


class VoiceOptions(Strict):
    voices: list[VoiceOption] = Field(min_length=2)


class PostMedia(Strict):
    kind: Literal["video", "image", "audio"]
    ratio: str
    duration_s: Optional[float] = None
    url: str
    cover_url: Optional[str] = None
    params: dict = Field(default_factory=dict)  # model, prompt_id, seed?, cost


class PostContent(Strict):
    hook_line: str
    caption_variants: dict[str, str]  # platform → text
    hashtags: list[str] = Field(default_factory=list)
    cta: str = ""
    alt_text: str = ""


class PostCard(Strict):
    """§04: the deliverable is an object, not a chat message. One id, three
    surfaces (thread final artifact · Plans slot chip · My Space row)."""

    id: str
    series_id: str
    thread_id: str
    concept_id: str
    option: Literal["A", "B", "C"]
    format: str
    platforms: list[str]
    post_content: PostContent
    media: list[PostMedia] = Field(default_factory=list)
    total_cost_credits: float = 0.0  # 1 credit = $0.10
    status: Literal["draft", "ready", "posted"] = "ready"
    posted_at: Optional[float] = None
    results_pasted: bool = False
    created_at: float = 0.0
    generation_log_ref: str = ""


# -------------------- Addendum-03 (Marketing Studio v2): campaign contracts ---

CampaignObjective = Literal["awareness", "traffic", "conversions"]
CreativeType = Literal["video", "image"]


# ------------------------------------------------- v3 §4: the settings model --

GateMode = Literal["review", "auto", "skip"]

# Every stage whose PAUSE can be configured. Deliberately not the same list as
# CAMPAIGN_STAGES: `name`, `paths`, `cards`, `generate` and `done` are not here,
# and `generate` is the important absence — see _hard_rules below.
GATEABLE_STAGES = ["brief", "options", "templates", "script", "detail",
                   "canon", "keyframes", "creative", "qc"]

# §4.3 rule 5. `skip` means the WORK is not done, so it is legal in exactly two
# places, and both surface what is being traded away.
SKIPPABLE_STAGES = {"templates", "canon"}


class ReviewPolicy(Strict):
    """Per-campaign. How deep the pipeline runs and where it stops for you.

    THE SEMANTIC THAT MAKES THIS SAFE: a gate mode controls whether the flow
    PAUSES. It never controls whether the artifact is PRODUCED. Downstream
    stages consume upstream artifacts — keyframes cannot exist without a board —
    and the Activity/generation_log audit trail has to stay complete however
    fast the user wants to move. An agency that turns gates off still needs to
    show a client the board afterwards.

    Defaults reproduce today's behaviour exactly: every gate `review`, every
    media count 1.
    """

    gates: dict[str, GateMode] = Field(
        default_factory=lambda: {g: "review" for g in GATEABLE_STAGES})

    # counts apply from IMAGE GENERATION ONWARDS — each one multiplies real spend
    keyframes_per_shot: int = Field(default=1, ge=1, le=4)
    takes_per_shot: int = Field(default=1, ge=1, le=4)
    variants: int = Field(default=1, ge=1, le=3)
    voice_candidates: int = Field(default=1, ge=1, le=12)

    # free/text stages — separate, because they cost nothing and more is better
    options_count: int = Field(default=3, ge=2, le=3)
    hooks_count: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def _hard_rules(self) -> "ReviewPolicy":
        """§4.3 — the rules no setting may configure away."""
        unknown = sorted(set(self.gates) - set(GATEABLE_STAGES))
        if unknown:
            # `generate` lands here on purpose. The model_confirm card and the
            # single-vs-variants question ALWAYS precede generation; there is no
            # setting that removes a cost gate, so `generate` is not gateable and
            # naming it is an error rather than a no-op.
            raise ValueError(
                f"{unknown} are not gateable stages. Cost gates (model_confirm, "
                "single-vs-variants) and claims_confirmed always fire and cannot be "
                f"configured away; gateable stages are {GATEABLE_STAGES}")
        for stage, mode in self.gates.items():
            if mode != "skip":
                continue
            if stage == "qc":
                raise ValueError(
                    "qc cannot be skipped. `auto` means 'do not stop for me to read the "
                    "accepted-tier items' — it never means 'do not check'; blocking "
                    "findings halt delivery regardless of gate mode")
            if stage == "keyframes":
                raise ValueError(
                    "keyframes cannot be skipped. Animating an unapproved frame is the "
                    "mistake the whole cost ladder exists to prevent; for an image "
                    "campaign the keyframes ARE the deliverable, so skip is meaningless")
            if stage not in SKIPPABLE_STAGES:
                raise ValueError(
                    f"{stage} cannot be skipped — skip means the work is not done, and it "
                    f"is only legal on {sorted(SKIPPABLE_STAGES)}, both of which surface "
                    "what is traded away")
        return self

    def mode(self, stage: str) -> GateMode:
        """Non-gateable stages always `review`: absent config can never mean an
        absent gate."""
        if stage not in GATEABLE_STAGES:
            return "review"
        return self.gates.get(stage, "review")

    def pauses_at(self, stage: str) -> bool:
        return self.mode(stage) == "review"


# §4.4 — presets write the SAME ReviewPolicy; they are not a second model.
POLICY_PRESETS: dict[str, dict[str, Any]] = {
    "full_craft": {},  # every default — brand work, new client, client review
    "fast": {  # known brand, repeat campaign
        "gates": {**{g: "review" for g in GATEABLE_STAGES},
                  "brief": "auto", "templates": "auto", "script": "auto", "canon": "auto"},
    },
    "volume": {  # performance testing, hook fan-out
        "gates": {**{g: "auto" for g in GATEABLE_STAGES},
                  "keyframes": "review", "qc": "review"},
        "takes_per_shot": 2,
        "variants": 3,
    },
}


def policy_from_preset(name: str) -> "ReviewPolicy":
    if name not in POLICY_PRESETS:
        raise ValueError(f"unknown preset {name!r} — one of {sorted(POLICY_PRESETS)}")
    return ReviewPolicy.model_validate(POLICY_PRESETS[name])


class ProductBlock(Strict):
    name: str
    description: str
    # ONE image is enough to lock product consistency; more is better, not
    # required. The old copy asked for 3-8 and the UI enforced it, which blocked
    # anyone launching with a single pack shot.
    image_upload_ids: list[str] = Field(default_factory=list, max_length=8)  # 1-8 → product pack / consistency lock


class CampaignBlock(Strict):
    objective: CampaignObjective  # drives CCF weights (awareness→followers_reach, traffic→engagement, conversions→conversions)
    target_audience: str
    platforms: list[Platform] = Field(min_length=1)
    description: Optional[str] = None
    creative_type: CreativeType = "image"


class BrandBlock(Strict):
    url: Optional[str] = None
    palette: list[str] = Field(default_factory=list)  # hex
    font: Optional[str] = None
    logo_upload_id: Optional[str] = None
    tagline: Optional[str] = None
    policy_upload_id: Optional[str] = None
    # ✚ compliance without a new form field: extracted from policy doc +
    # product description, then ONE-TAP CONFIRMED by the user. Confirmed list
    # = claims source of truth (kill-flag lens unchanged).
    approved_claims: list[str] = Field(default_factory=list)
    banned_words: list[str] = Field(default_factory=list)
    claims_confirmed: bool = False
    # v3 §10 — the rights ledger lives at INTAKE, not at QC. Platform terms may
    # grant broad rights over uploaded media and no generator indemnifies
    # unauthorised likeness use, so discovering at QC that the music is uncleared
    # means the ad is finished and unshippable. `not_cleared` never blocks
    # PLANNING; it blocks DELIVERY, and it is visible from the moment it is recorded.
    rights_ledger: list["RightsEntry"] = Field(default_factory=list)


class CampaignContext(Strict):
    """Paths a and b write THIS identical schema — path b is elicitation UX,
    not a different data model."""

    name: str
    product: Optional[ProductBlock] = None
    campaign: Optional[CampaignBlock] = None
    brand: Optional[BrandBlock] = None

    @property
    def complete(self) -> bool:
        return bool(
            self.product and self.campaign and self.brand and self.brand.claims_confirmed
        )


# The placement spec table. ONE source — AdCard._spec_table and CampaignBrief
# both read it, because two lists drift and the second one is always the wrong
# one. Anything rendered has to be a ratio we actually produce.
PLACEMENT_RATIOS = {"9:16", "1:1", "16:9", "4:5"}


class CampaignBrief(Strict):
    """v3 §1 — "are we making the right ad?", and the format constraints
    everything downstream inherits.

    Costs nothing: the intake agent derives it from the three completed cards.
    Four of these fields materially change downstream output and had nowhere to
    live before — objective, audience and platforms already existed but sat in
    three separate cards with no single artifact anyone could approve or reject.
    """

    # derived from CampaignBlock — echoed, never re-asked
    objective: CampaignObjective
    audience: str
    platforms: list[Platform] = Field(min_length=1)
    creative_type: CreativeType

    # the fields with no home before v3
    target_metric: Optional[str] = None      # "2,000 units in 6 weeks at <=Rs420 CAC"
    audience_current_belief: str             # D9: what they believe BEFORE this ad
    single_message: str = Field(max_length=160)   # the one thing they should remember
    brand_role: str                          # D4: how the brand functions IN the story
    offer_cta: str

    # format constraints — locked here, propagated everywhere
    aspect_ratios: list[str] = Field(min_length=1)
    duration_s: Optional[float] = None       # video only
    languages: list[str] = Field(min_length=1)    # BCP-47; first = primary
    multi_format_policy: Literal["safe_area", "native_regen"] = "safe_area"

    mandatories: list[str] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)

    # appetite — scope is cut to fit this, not the reverse
    budget_credits: Optional[float] = None
    proof_points: list[str] = Field(default_factory=list)   # ⊆ confirmed approved_claims

    version: int = 1
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _format_table(self) -> "CampaignBrief":
        bad = [r for r in self.aspect_ratios if r not in PLACEMENT_RATIOS]
        if bad:
            raise ValueError(
                f"ratio(s) {bad} outside the placement spec table "
                f"{sorted(PLACEMENT_RATIOS)} — every ratio on a brief is one we render")
        if self.creative_type == "video" and self.duration_s is not None and self.duration_s <= 0:
            raise ValueError("duration_s must be positive for a video brief")
        return self


class CampaignOption(Strict):
    option_id: str  # o1, o2, o3
    name_line: str
    description: str = Field(max_length=400)
    storyline: str = Field(max_length=400)
    objective_echo: str
    why_it_fits: str
    evidence: list[Evidence] = Field(default_factory=list)  # source_ids or honest gap


class CampaignOptions(Strict):
    options: list[CampaignOption] = Field(min_length=2, max_length=3)


CanonKind = Literal["character", "product", "environment", "voice"]
RightsStatus = Literal["owned", "consented", "fictional", "unverified"]

# Required coverage per kind. In ONE place because the sheet card's coverage
# meter, the validator and the generation plan all have to agree on "complete".
CANON_COVERAGE: dict[str, tuple[str, ...]] = {
    "character": ("front", "three_quarter_l", "three_quarter_r", "profile_l",
                  "profile_r", "full_front", "full_rear"),
    "product": ("front", "rear", "left", "right", "top", "base"),
    "environment": ("establishing", "angle_a", "angle_b", "angle_c"),
    "voice": (),          # auditioned, not viewed
}
# A product whose geometry mutates needs extra three-quarter views before it can
# be trusted in a shot.
PRODUCT_RISK_VIEWS = ("three_quarter_fl", "three_quarter_fr", "three_quarter_rl")


class CanonSheet(Strict):
    """v3 §6 — "is this the right cast, and is this actually our product?"

    A first-class, workspace-global library entity (owner decision 2026-08-25),
    not a modal inside one thread. This is the retention mechanic: campaign two
    is far cheaper than campaign one BECAUSE the canon already exists.
    """

    id: str                     # "@priya", "@tamra-classic"
    kind: CanonKind
    label: str
    brief: str                  # casting / product / location / voice descriptor
    asset_ids: list[str] = Field(default_factory=list)
    coverage: dict[str, bool] = Field(default_factory=dict)
    locks: list[str] = Field(default_factory=list)
    slot_cost: int = Field(default=1, ge=1)     # how many B3 reference slots it eats
    risk_notes: list[str] = Field(default_factory=list)
    rights: RightsStatus = "unverified"
    consent_ref: Optional[str] = None
    # voice only — a blocking field per non-English locale. Synthetic fluency is
    # not evidence of correctness and a non-speaker cannot catch the failure.
    native_review: dict[str, Optional[str]] = Field(default_factory=dict)
    version: int = 1

    def required_views(self) -> tuple[str, ...]:
        base = CANON_COVERAGE[self.kind]
        if self.kind == "product" and self.risk_notes:
            return base + PRODUCT_RISK_VIEWS
        return base

    @property
    def complete(self) -> bool:
        return all(self.coverage.get(v) for v in self.required_views())


class CanonPlan(Strict):
    """What the canon agent returns: the sheets this board needs, and no others."""

    sheets: list[CanonSheet] = Field(default_factory=list)


class ScriptLine(Strict):
    """One spoken or on-screen line. `words`, `wps` and `wps_verdict` are
    RECOMPUTED server-side — the model never checks its own homework, same rule
    as CCS."""

    slot: str                  # "hook" | "beat_01" | "cta"
    t_in: float = Field(ge=0)
    t_out: float = Field(gt=0)
    text: str                  # native script (Devanagari for hi, Tamil for ta…)
    emotion: str               # REQUIRED — W2
    words: int = 0
    wps: float = 0.0
    wps_verdict: Literal["pass", "tight", "fail"] = "pass"
    proposed_fix: Optional[str] = None    # set when the verdict is `fail`
    claim_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _window(self) -> "ScriptLine":
        if self.t_out <= self.t_in:
            raise ValueError(f"{self.slot}: t_out must be after t_in")
        return self

    @property
    def duration_s(self) -> float:
        return self.t_out - self.t_in


class HookRack(Strict):
    """v3 §4 — "does a human talk like this, and does it physically fit?"

    ONE LOCKED BODY, MANY HOOKS. This is what makes variant testing affordable:
    a hook swap re-renders one shot, not the film. The body must come back
    byte-identical when hooks are regenerated.
    """

    language: str
    body: list[ScriptLine] = Field(min_length=1)     # the LOCKED body
    hooks: list[ScriptLine] = Field(min_length=1)    # 1..N openers against it
    selected_hook_slot: str
    loanwords_kept: list[str] = Field(default_factory=list)
    total_duration_s: float = 0.0
    version: int = 1

    @model_validator(mode="after")
    def _selection_exists(self) -> "HookRack":
        slots = {h.slot for h in self.hooks}
        if self.selected_hook_slot not in slots:
            raise ValueError(
                f"selected_hook_slot {self.selected_hook_slot!r} is not one of the hooks "
                f"{sorted(slots)} — the board consumes the selected hook, so it has to exist")
        return self


class TemplateRef(Strict):
    """Selected template = style/composition reference injected into
    downstream prompts — constrains look, never copy. Skip = None upstream."""

    id: str
    type: Literal["image", "video"]
    style_descriptors: list[str] = Field(default_factory=list)


class DetailShot(Strict):
    slot: str  # shot_01 | slide_01
    duration_s: Optional[float] = None
    visual_prompt: str
    vo_or_copy: Optional[str] = None


Realism = Literal["editorial", "natural", "documentary"]

# The realism dial is LOAD-BEARING and counter-intuitive. Faces read as credible
# BECAUSE of pores, asymmetry and ordinary imperfection; prompts asking for
# flawless skin and idealised symmetry produce the synthetic look people
# recognise instantly. Shipped as a three-position control, never as a phrase the
# user has to know to type.
REALISM_TEXTURE = {
    "editorial": "clean retouch, controlled highlight rolloff, fine even grain",
    "natural": "true skin texture, light unretouched grain, natural asymmetry",
    "documentary": ("visible skin pores, natural asymmetry, no beauty smoothing, "
                    "no retouching, available-light grain"),
}


class StyleBlock(Strict):
    """v3 §3 — "does the world look right?"

    `TemplateRef.style_descriptors` was a loose list. What downstream prompts
    need is a NAMED, VERSIONED, VERBATIM string injected into every visual prompt
    in the campaign — the cheapest consistency mechanism available.
    """

    id: str
    grade: str                  # colour behaviour
    light: str                  # source, direction, quality, time of day
    lens: str                   # focal length, aperture, depth, distortion
    texture: str                # grain + the realism dial
    motion: str                 # handheld drift vs locked-off
    negatives: list[str] = Field(default_factory=list)
    realism: Realism = "natural"
    derived_from: Optional[str] = None    # TemplateRef.id, or None when Skip was chosen
    version: int = 1

    def as_prompt(self) -> str:
        """Verbatim injection, prepended to every visual_prompt in the campaign.

        The user can see this exact string on the card. This is an agency tool
        and that transparency is a selling point, not a debug affordance.
        """
        parts = [f"grade: {self.grade}", f"light: {self.light}", f"lens: {self.lens}",
                 f"texture: {self.texture}", f"motion: {self.motion}"]
        if self.negatives:
            parts.append("avoid: " + ", ".join(self.negatives))
        return " · ".join(parts)


def neutral_style_block(block_id: str = "sb_neutral") -> StyleBlock:
    """Skip on templates yields a NEUTRAL DEFAULT block — never an absent one.

    Every campaign has a style block; only its source varies. A downstream prompt
    that has to ask "is there a style?" is a prompt with two code paths, and the
    second one is always the untested one.
    """
    return StyleBlock(
        id=block_id,
        grade="neutral colour, no strong cast",
        light="soft even key, no hard shadow",
        lens="50mm equivalent, mid aperture, no distortion",
        texture=REALISM_TEXTURE["natural"],
        motion="locked off",
        negatives=["text overlay", "watermark", "logo"],
        realism="natural",
        derived_from=None,
    )


CameraMove = Literal["static", "push_in", "pull_out", "pan", "tilt",
                     "handheld", "orbit", "macro_slide"]
ShotSize = Literal["ECU", "CU", "MCU", "MS", "WS", "EWS"]


class LintResult(Strict):
    """A lint that says only PASS is a lint nobody reads. `findings` names what
    was wrong and `resolution` names what was DONE about it, so the board records
    its own edits rather than quietly applying them."""

    status: Literal["pass", "warn", "fail"] = "pass"
    findings: list[str] = Field(default_factory=list)
    resolution: list[str] = Field(default_factory=list)


class BoardLints(Strict):
    beats: LintResult = Field(default_factory=LintResult)      # B1
    runtime: LintResult = Field(default_factory=LintResult)    # B2
    slots: LintResult = Field(default_factory=LintResult)      # B3
    motion: LintResult = Field(default_factory=LintResult)     # B4


class BoardShot(Strict):
    """One row of the board. Built on the Phase-2 `Shot` vocabulary
    (keyframe_prompt / motion_prompt) rather than a third shot language.

    NOTE the one spec conflict, resolved here: `Shot.duration_s` caps at 8 and
    the v3 artifact spec caps BoardShot at 10. v3 is the spec of record, so 10
    wins — and it is recorded rather than silently differing.
    """

    slot: str
    duration_s: float = Field(gt=0, le=10)
    beat: str                                    # ONE beat — B1
    dialogue_ref: Optional[str] = None           # ScriptLine.slot
    action: str
    camera: CameraMove
    shot_size: ShotSize
    emotion: str
    cast_refs: list[str] = Field(default_factory=list)
    product_refs: list[str] = Field(default_factory=list)
    env_refs: list[str] = Field(default_factory=list)
    keyframe_prompt: str        # style_block injected SERVER-side, not by the model
    motion_prompt: str = ""
    model_route: str            # key into config.MEDIA_MODELS
    route_reason: str           # the shot's HARDEST requirement, user-visible
    slots_used: int = 0
    est_cost_usd: float = 0.0

    @property
    def all_refs(self) -> list[str]:
        return [*self.cast_refs, *self.product_refs, *self.env_refs]


class ShotBoard(Strict):
    """v3 §5 — "approve the film before it exists". THE LAST FREE GATE.

    Everything after this derives from it. A hook swap edits one row and
    re-renders one shot; if variants were derived from the finished video
    instead, every axis would multiply a full re-render. The board, not the MP4,
    is the source of truth — that single decision is the difference between
    variant testing being a habit and being a quote.
    """

    creative_type: CreativeType
    shots: list[BoardShot] = Field(min_length=1)
    copy_primary: str
    cta: str
    claims_used: list[str] = Field(default_factory=list)
    style_block_id: Optional[str] = None
    lints: BoardLints = Field(default_factory=BoardLints)
    est_total_usd: float = 0.0
    version: int = 1
    changes: list[str] = Field(default_factory=list)


class CampaignDetail(Strict):
    """Step 5 artifact → right-panel Context tab. Script (video) or image
    prompt set (statics) + structure, copy, CTA, claims used."""

    creative_type: CreativeType
    shots: list[DetailShot] = Field(min_length=1)
    copy_primary: str
    cta: str
    claims_used: list[str] = Field(default_factory=list)  # must ⊆ confirmed claims
    style_ref: Optional[TemplateRef] = None
    version: int = 1
    changes: list[str] = Field(default_factory=list)  # refine-loop diff log


KeyframeCheck = Literal["face", "hands", "product_geometry", "label_legibility",
                        "composition", "safe_area"]
KEYFRAME_CHECKS: tuple[str, ...] = ("face", "hands", "product_geometry",
                                    "label_legibility", "composition", "safe_area")


class Keyframe(Strict):
    shot_slot: str
    asset_id: str
    picked_from: int = Field(default=1, ge=1)     # generated N, chose 1
    refs_used: list[str] = Field(default_factory=list)
    checks: dict[str, Literal["pass", "fail", "na"]] = Field(default_factory=dict)
    repairs: list[str] = Field(default_factory=list)
    approved: bool = False
    cost_usd: float = 0.0

    @property
    def failing(self) -> list[str]:
        return [k for k, v in self.checks.items() if v == "fail"]


class KeyframeBoard(Strict):
    """v3 §7 — "lock the film as stills". THE HARD GATE.

    The highest-value single addition in the spec. The pipeline went detail →
    generate, so a composition, identity or label defect surfaced only AFTER
    video was paid for — where a wrong pattern or a mutated label is far harder
    to see in motion than in a still. Costs roughly a fiftieth of the motion it
    protects.
    """

    frames: list[Keyframe] = Field(min_length=1)
    all_approved: bool = False
    total_cost_usd: float = 0.0
    version: int = 1

    @model_validator(mode="after")
    def _approval_is_derived(self) -> "KeyframeBoard":
        # Derived, never asserted. all_approved is the thing that unlocks paid
        # video generation, so it may not be a field a model can simply set.
        self.all_approved = bool(self.frames) and all(f.approved for f in self.frames)
        self.total_cost_usd = round(sum(f.cost_usd for f in self.frames), 4)
        return self


TakeCause = Literal["wrong_reference", "ambiguous_action", "too_many_actions",
                    "inconsistent_geometry", "unsuitable_model"]

# Each cause has a DISTINCT fix. "Make it better" is not a repair instruction,
# so the reject UI picks a cause and the system proposes the matching patch.
TAKE_FIXES: dict[str, str] = {
    "wrong_reference": "re-select the canon reference for this shot",
    "ambiguous_action": "state the action as one concrete verb with a subject",
    "too_many_actions": "split the beat back to one action (board lint B1)",
    "inconsistent_geometry": "add the product's at-risk views and re-lock geometry",
    "unsuitable_model": "re-route the shot per its route_reason",
}


class TakeReject(Strict):
    cause: TakeCause
    detail: str
    proposed_fix: str = ""
    variable_changed: str      # exactly ONE per retry

    @model_validator(mode="after")
    def _fix_follows_cause(self) -> "TakeReject":
        if not self.proposed_fix:
            self.proposed_fix = TAKE_FIXES[self.cause]
        if not self.variable_changed.strip():
            raise ValueError(
                "variable_changed is required — two simultaneous edits make the next result "
                "uninterpretable, so a retry changes exactly one thing and says which")
        return self


VariantAxis = Literal["hook", "language", "ratio", "cta", "duration"]
LocalisationTier = Literal["dub", "revoice", "recast"]

# Named and priced separately because they are genuinely different products.
# Offering only `dub` and calling it localisation is the thing agencies notice
# first — localisation is creative adaptation, not translation.
LOCALISATION_LIMITS: dict[str, str] = {
    "dub": "audio only; lip-sync drift is visible — acceptable for VO-led creative",
    "revoice": "new VO, captions and lip re-sync",
    "recast": ("new talent, wardrobe and environment; a full board re-render — the honest "
               "option when a market needs a different face, not just a different language"),
}


class VariantCell(Strict):
    variant_id: str
    axis: VariantAxis
    delta: str
    hypothesis: str
    shots_rerendered: list[str] = Field(default_factory=list)   # board slots
    shots_reused: int = 0
    cost_usd: float = 0.0
    localisation_tier: Optional[LocalisationTier] = None


class VariantMatrix(Strict):
    """v3 §11 — the REUSE MAP. ModelConfirm.variants_proposed already existed;
    what was missing is the number that decides whether variant testing is
    affordable at all."""

    cells: list[VariantCell] = Field(min_length=1)
    baseline_cost_usd: float = 0.0   # the same set re-rendered from scratch
    matrix_cost_usd: float = 0.0     # derived from the board


class RightsEntry(Strict):
    asset_kind: Literal["logo", "product_photo", "likeness", "voice", "music", "font", "stock"]
    ref: str
    status: Literal["owned", "consented", "licensed", "fictional", "not_cleared"]
    scope: Optional[str] = None       # "paid social, 24mo"
    evidence_ref: Optional[str] = None


QCTier = Literal["blocking", "fix_before_ship", "accepted"]


class QCFinding(Strict):
    tier: QCTier
    check: str
    detail: str
    locator: Optional[str] = None     # asset_id, or "asset_id@2.4s" for jump-to-frame
    resolution: Optional[str] = None
    rationale: Optional[str] = None   # REQUIRED when tier == "accepted"

    @model_validator(mode="after")
    def _accepted_needs_a_reason(self) -> "QCFinding":
        if self.tier == "accepted" and not (self.rationale or "").strip():
            raise ValueError(
                f"accepted finding {self.check!r} has no rationale — 'ship it anyway' has to be an "
                "auditable decision rather than a shrug, and an accepted defect with no visible "
                "reason looks like negligence when a client asks later")
        return self


class QCReport(Strict):
    """v3 §9 — "is it safe to publish, and under a deadline which defects may
    ship?"

    Seam QA and AdCard._spec_table covered fragments. What was missing is a
    SEVERITY MODEL: a flat pass/fail list either blocks a dated campaign over a
    background continuity slip, or gets ignored wholesale because it cries wolf.
    """

    findings: list[QCFinding] = Field(default_factory=list)
    automated: dict[str, Literal["pass", "fail", "skip"]] = Field(default_factory=dict)
    verdict: Literal["cleared", "held"] = "cleared"
    locales: dict[str, Literal["cleared", "held"]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _verdict_is_derived(self) -> "QCReport":
        # Derived, never asserted: a blocking finding halts delivery regardless
        # of what the model would like the verdict to say.
        self.verdict = "held" if any(f.tier == "blocking" for f in self.findings) else "cleared"
        return self


class VariantSpec(Strict):
    variant_id: str  # A, B, C
    delta: str  # named delta — different hook / visual treatment / copy angle, never rewordings
    hypothesis: str  # "B tests hook vs A"
    cost_usd: float


class ModelConfirm(Strict):
    """Step 7 card — always precedes generation."""

    recommended_model: str
    reason: str
    cost_usd: float
    settings_note: str = "Model selection coming — using recommended models"
    variants_proposed: list[VariantSpec] = Field(default_factory=list)


class AdCard(Strict):
    """The deliverable (v1 Ad Card spec): per-placement copy, ratios, naming
    string, export bundle. Lands in thread + My Campaigns."""

    id: str
    campaign_id: str  # series id
    thread_id: str
    option_id: str
    variant_group_id: Optional[str] = None
    variant_id: Optional[str] = None
    creative_type: CreativeType
    placements: dict[str, str]  # platform → copy
    ratios: list[str] = Field(min_length=1)
    naming: str  # e.g. brand_campaign_option_variant_ratio
    media: list[PostMedia] = Field(default_factory=list)
    total_cost_credits: float = 0.0
    status: Literal["draft", "ready", "live"] = "ready"
    created_at: float = 0.0

    @model_validator(mode="after")
    def _spec_table(self) -> "AdCard":
        # PLACEMENT_RATIOS, not a local literal: the brief validates against the
        # same table, and two copies of a spec drift apart with the second one
        # always being the stale one.
        bad = [r for r in self.ratios if r not in PLACEMENT_RATIOS]
        if bad:
            raise ValueError(
                f"ratio(s) {bad} outside the placement spec table {sorted(PLACEMENT_RATIOS)}")
        return self


class SeatScore(Strict):
    element: ElementName
    rating: Rating
    reason: str
    evidence: list[Evidence] = Field(default_factory=list)


class SeatReview(Strict):
    """One blind council seat's output (Addendum-03 evaluator).

    `seat` is a free string rather than the original three-value Literal so a
    user-added stakeholder seat ("my_cmo") can validate. The slug is checked
    against the live seat registry in validators.validate_council — shape here,
    policy there, matching how every other semantic rule in this codebase is
    split. A Literal cannot express "whatever this campaign configured".
    """

    seat: str = Field(min_length=1)
    element_scores: list[SeatScore] = Field(min_length=1)
    kill_recommendation: Optional[str] = None
    fixes: list[Fix] = Field(default_factory=list)
    # v3 doctrine: the Platform seat may judge format fit but may NEVER state
    # what a platform's ad policy says. It raises this instead and names what a
    # human has to go and check.
    policy_check_required: bool = False
    policy_notes: list[str] = Field(default_factory=list)
    # Stamped by the SERVER after validation (never emitted by the model — a
    # model-reported version answers "what did it think it was" and the audit
    # question is "which doctrine actually ran").
    doctrine_version: Optional[str] = None


# --------------------------------------------- Phase-2 contracts (defined) ---


class Shot(Strict):
    shot_id: str
    duration_s: float = Field(gt=0, le=8)
    keyframe_prompt: str
    motion_prompt: str
    vo_segment: Optional[str] = None
    boundary: Literal["cut", "interpolate"]


class ConsistencyPlan(Strict):
    identity_pack_ids: list[str]
    wardrobe_lock: str
    lighting_lock: str


class ScriptPackage(Strict):
    beats: list[str]
    vo_script: str
    shots: list[Shot]
    consistency_plan: ConsistencyPlan
    route: Literal["one_take", "keyframe_cuts", "interpolated"]
    closing_shot_option_id: Optional[str] = None
    cost_estimate_credits: float


class ClosingShotOption(Strict):
    layout: str
    message: str
    image_prompt: str
    duration_s: float = Field(ge=1.5, le=2.5)

    @field_validator("message")
    @classmethod
    def _seven_words(cls, v: str) -> str:
        if len(v.split()) > 7:
            raise ValueError("closing-shot message must be <=7 words")
        return v


class Brand(Strict):
    name: str
    logo_asset_id: Optional[str] = None
    colors: list[str] = Field(default_factory=list)
    tagline: Optional[str] = None


class BrandKit(Strict):
    source: Literal["assets", "url_extract"]
    brand: Brand
    closing_shot_options: list[ClosingShotOption] = Field(min_length=2, max_length=3)


# -------------------------------------------------------------- Objectives ---


class ObjectiveFamily(str, Enum):
    followers_reach = "followers_reach"
    engagement = "engagement"
    conversions = "conversions"


def objective_family(objective: Objective) -> ObjectiveFamily:
    if objective in ("followers", "impressions"):
        return ObjectiveFamily.followers_reach
    if objective == "engagement":
        return ObjectiveFamily.engagement
    return ObjectiveFamily.conversions
