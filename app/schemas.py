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


class Lenses(Strict):
    saturation: SaturationLens
    claims_safety: str
    feasibility: str
    platform_policy: str


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
