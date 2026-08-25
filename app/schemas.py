"""§3.9 agent output contracts — pydantic-enforced.

Invalid agent output → re-run with the validation error (max 2 retries) → hard
fail surfaced to the user. Never silently accepted, never silently repaired.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal, Optional

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


class AgentMessage(Strict):
    """Every agent turn, all studios. Text is a conversational envelope ONLY —
    artifact content is never restated as prose."""

    thread_id: str
    text: str = Field(max_length=280)
    artifacts: list[ArtifactEnvelope] = Field(default_factory=list)
    question: Optional[str] = None  # at most ONE — single field by design

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
        allowed = {"9:16", "1:1", "16:9", "4:5"}
        bad = [r for r in self.ratios if r not in allowed]
        if bad:
            raise ValueError(f"ratio(s) {bad} outside the placement spec table {sorted(allowed)}")
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
