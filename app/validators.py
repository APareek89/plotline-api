"""Server-side validators (§3.9 hard validations + §11 guardrails).

The model never checks its own homework: CCS is recomputed here, every cited
source_id is resolved against the RAG service, and refine diffs are enforced.
One dead citation fails the whole output.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from app import ccs as ccs_mod
from app.rag_client import RoutingRag
from app.schemas import (
    CANON_COVERAGE,
    KEYFRAME_CHECKS,
    BoardLints,
    Concept,
    QCFinding,
    QCReport,
    CreatorContext,
    Evidence,
    Feedback,
    LintResult,
    ObjectiveFamily,
    OptionsOutput,
    Plan,
)


# Addendum-01 §7.3 thresholds
SATURATION_MIN_ASSETS = 25   # below → saturation lens must say insufficient_data
NICHE_MIN_ASSETS = 30        # "supported niche" gates (else provisional at context time)
NICHE_MIN_CHUNKS = 15
THIN_PLAN_RATIO = 0.40       # >40% of concepts < 70 after refine → escalation artifact


class AgentValidationError(ValueError):
    """Raised when an agent output violates a hard validation. The message is
    fed back verbatim on the retry run."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _collect_evidence(items: Iterable[Evidence]) -> list[Evidence]:
    return [e for e in items]


def _resolvable_ids(evidence: list[Evidence]) -> list[str]:
    # PRINCIPLE with source_id "model" is the only citation that skips DB
    # resolution; a PRINCIPLE citing a chunk still resolves like everything else.
    return [e.source_id for e in evidence if e.source_id != "model"]


def resolve_or_fail(
    evidence: list[Evidence],
    rag: RoutingRag,
    errors: list[str],
    where: str,
    retrieved_ids: Optional[set[str]] = None,
) -> None:
    """Two independent layers, both mandatory (an id passing the DB check is
    NOT enough if it was never retrieved this run):

      1. local subset — every cited id ∈ ids surfaced by retrieval tools in
         THIS pipeline run (catches invented-but-real ids)
      2. remote existence — /resolve_source_ids against the RAG service
         (catches retrieved-then-rotted ids); unreachable resolver = reject
    """
    ids = sorted(set(_resolvable_ids(evidence)))
    if not ids:
        return
    if retrieved_ids is not None:
        invented = [i for i in ids if i not in retrieved_ids]
        if invented:
            errors.append(
                f"{where}: source_id(s) {invented} were NOT returned by any retrieval "
                "tool in this run — you may only cite ids your tools surfaced here; "
                'if evidence is insufficient, say so ("no evidence in DB", '
                "addressed=false / evidence_gap=true) instead of inventing a source"
            )
            ids = [i for i in ids if i in retrieved_ids]
            if not ids:
                return
    resolved = rag.resolve_source_ids(ids)
    dead = [i for i in ids if not resolved.get(i, False)]
    if dead:
        errors.append(
            f"{where}: unresolvable source_id(s) {dead} — every citation must be a "
            "DB-resolvable id returned by your retrieval tools in THIS run; if retrieval "
            'returned nothing, write "no evidence in DB" and set addressed=false or '
            "evidence_gap=true instead of inventing a source"
        )


# ---------------------- Addendum-01 §7.2: anti-generic mechanical checks ---
# Mechanical checks live HERE (prompts drift, validators don't). Judgment
# checks (R1, R4, R5, R7) are feedback-agent lenses; R6 activates with the
# trends corpus.

# R3 — banned-abstraction lexicon: hooks built from these are generic by
# construction. Lowercased substring match.
BANNED_ABSTRACTIONS = (
    "game-changer", "game changer", "boost productivity", "boost your productivity",
    "revolutionize", "next level", "take it to the next", "supercharge",
    "unlock the power", "unleash", "transform your workflow", "work smarter not harder",
    "you won't believe", "mind-blowing", "must-know", "level up your",
)

# R3 — specificity markers: a hook must carry ≥1 of {role, number, artifact,
# outcome}. Numbers are matched by regex; the rest by concrete-noun cues.
_SPECIFICITY_ROLE = ("editor", "founder", "developer", "designer", "marketer", "creator",
                     "freelancer", "student", "manager", "recruiter", "analyst", "pm")
_SPECIFICITY_ARTIFACT = ("invoice", "screenshot", "dashboard", "spreadsheet", "script",
                         "template", "prompt", "workflow", "scorecard", "bill", "subscription",
                         "clip", "video", "resume", "report", "timer", "checklist")
_SPECIFICITY_OUTCOME = ("saved", "saves", "cut", "cancelled", "replaced", "shipped",
                        "grew", "doubled", "fired", "wasted", "earned", "won", "lost", "caught")

# R2 — receipt cues: creative_direction must name a demonstrable artifact,
# not gesture at advice.
_RECEIPT_CUES = ("on camera", "on-screen", "screen record", "screen-rec", "screenshot",
                 "invoice", "split screen", "split-screen", "side-by-side", "timer",
                 "scorecard", "counter", "overlay", "live demo", "demos", "demo",
                 "before/after", "printed", "receipt", "recording", "dashboard", "workflow")

import re as _re

_NUMBER_RE = _re.compile(r"[\d₹$€%]")


def check_anti_generic(concept: Concept, errors: list[str]) -> None:
    """R2 + R3 mechanical layer. Judgment layers live in the feedback agent."""
    hook = concept.hook.verbal.lower()
    for phrase in BANNED_ABSTRACTIONS:
        if phrase in hook:
            errors.append(
                f"concept {concept.id}: R3 banned abstraction {phrase!r} in hook — replace "
                "with a concrete role/number/artifact/outcome"
            )
    has_specific = bool(_NUMBER_RE.search(hook)) or any(
        w in hook for w in _SPECIFICITY_ROLE + _SPECIFICITY_ARTIFACT + _SPECIFICITY_OUTCOME
    )
    if not has_specific:
        errors.append(
            f"concept {concept.id}: R3 specificity floor — hook must contain at least one of "
            "a role, a number, a named artifact, or a concrete outcome"
        )
    direction = concept.creative_direction.lower()
    if not any(cue in direction for cue in _RECEIPT_CUES):
        errors.append(
            f"concept {concept.id}: R2 receipt required — creative_direction must name a "
            "demonstrable artifact (what the viewer literally SEES as proof)"
        )


# ------------------------------------------------------------------- intake --


def validate_intake(context: CreatorContext, *, require_addendum_fields: bool = True) -> CreatorContext:
    errors: list[str] = []
    if len(context.clarifying_questions) > 3:
        errors.append("max 3 clarifying_questions, only if truly blocking")
    if require_addendum_fields:
        # Addendum-01 §7.1 — R1 and format feasibility depend on these.
        if not context.audience_sophistication:
            errors.append("audience_sophistication is required (novice/practitioner/expert)")
        if not context.tool_access:
            errors.append("tool_access is required (what can the creator actually demo)")
        if not context.positioning_depth:
            errors.append("positioning_depth is required (beginner_guide/power_user)")
    if errors:
        raise AgentValidationError(errors)
    return context


# --------------------------------------------------------------------- plan --


def validate_plan(
    plan: Plan,
    context: CreatorContext,
    rag: RoutingRag,
    *,
    previous_plan: Optional[Plan] = None,
    flagged_concept_ids: Optional[set[str]] = None,
    retrieved_ids: Optional[set[str]] = None,
) -> Plan:
    errors: list[str] = []
    family = ccs_mod.WEIGHTS.keys()  # noqa: F841  (families validated below)

    fam = _family_of(context)
    required_elements = set(ccs_mod.applicable_elements(fam))

    expected_slots = context.cadence.slots
    if len(plan.concepts) != expected_slots:
        errors.append(
            f"plan has {len(plan.concepts)} concepts but cadence requires {expected_slots} slots"
        )

    seen_ids: set[str] = set()
    for concept in plan.concepts:
        if concept.id in seen_ids:
            errors.append(f"duplicate concept id {concept.id}")
        seen_ids.add(concept.id)

        scored = {s.element for s in concept.element_scores}
        missing = required_elements - scored
        if missing:
            errors.append(
                f"concept {concept.id}: missing element_scores for {sorted(missing)} — every "
                "applicable element must be present (addressed=false with why_not for honest gaps)"
            )
        extra = scored - required_elements
        if extra:
            errors.append(
                f"concept {concept.id}: elements {sorted(extra)} are not applicable for "
                f"objective family {fam.value}"
            )
        evidence = _collect_evidence(e for s in concept.element_scores for e in s.evidence)
        # Subset check applies to concepts authored THIS run: all of them on a
        # fresh plan, only flagged ones on a refine (untouched concepts are
        # byte-identical and their citations were checked when first written).
        authored_now = previous_plan is None or concept.id in (flagged_concept_ids or set())
        if authored_now:
            check_anti_generic(concept, errors)  # Addendum-01 R2/R3 mechanical layer
        resolve_or_fail(
            evidence, rag, errors, f"concept {concept.id}",
            retrieved_ids=retrieved_ids if authored_now else None,
        )

    # Refine pass: may touch only flagged concepts — untouched must be byte-identical.
    if previous_plan is not None:
        flagged = flagged_concept_ids or set()
        prev_by_id = {c.id: c for c in previous_plan.concepts}
        for concept in plan.concepts:
            if concept.id in flagged:
                continue
            prev = prev_by_id.get(concept.id)
            if prev is None:
                errors.append(f"refine pass introduced new concept {concept.id} — not allowed")
                continue
            if _canonical(concept) != _canonical(prev):
                errors.append(
                    f"refine pass modified unflagged concept {concept.id} — untouched concepts "
                    "must be byte-identical (diff-checked)"
                )

    if errors:
        raise AgentValidationError(errors)
    return plan


def _canonical(concept: Concept) -> str:
    return json.dumps(concept.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _family_of(context: CreatorContext) -> ObjectiveFamily:
    from app.schemas import objective_family

    return objective_family(context.objective)


# ------------------------------------------ Addendum-01 §7.1: format stage --


def validate_formats(
    formats: "FormatOptions",
    rag: RoutingRag,
    retrieved_ids: Optional[set[str]] = None,
) -> "FormatOptions":
    from app.schemas import FormatOptions  # noqa: F401  (typing only)

    errors: list[str] = []
    seen: set[str] = set()
    for option in formats.options:
        if option.format_id in seen:
            errors.append(f"duplicate format_id {option.format_id}")
        seen.add(option.format_id)
        resolve_or_fail(
            list(option.evidence), rag, errors, f"format {option.format_id}",
            retrieved_ids=retrieved_ids,
        )
    if errors:
        raise AgentValidationError(errors)
    return formats


# ----------------------------------------------------------------- feedback --


def validate_feedback(
    feedback: Feedback,
    plan: Plan,
    context: CreatorContext,
    rag: RoutingRag,
    retrieved_ids: Optional[set[str]] = None,
    niche_asset_count: Optional[int] = None,
) -> Feedback:
    errors: list[str] = []
    fam = _family_of(context)
    required_elements = set(ccs_mod.applicable_elements(fam))
    plan_ids = {c.id for c in plan.concepts}
    verdict_ids = {v.concept_id for v in feedback.concept_verdicts}

    missing_concepts = plan_ids - verdict_ids
    if missing_concepts:
        errors.append(f"feedback missing verdicts for concepts {sorted(missing_concepts)} — verdict on ALL concepts required")
    unknown = verdict_ids - plan_ids
    if unknown:
        errors.append(f"feedback references unknown concept ids {sorted(unknown)}")

    scores_by_concept = {c.id: {s.element: s for s in c.element_scores} for c in plan.concepts}

    for verdict in feedback.concept_verdicts:
        verdict_elements = {ev.element for ev in verdict.element_verdicts}
        missing = required_elements - verdict_elements
        if missing:
            errors.append(
                f"concept {verdict.concept_id}: element_verdicts missing {sorted(missing)} — "
                "verdict on EVERY element is mandatory (any missing = invalid)"
            )

        # saturation lens must cite the DB (or explicitly have no similar assets)
        sat = verdict.lenses.saturation
        if sat.similar_count > 0 and not sat.source_id and not sat.insufficient_data:
            errors.append(
                f"concept {verdict.concept_id}: saturation lens with similar_count>0 must cite a source_id"
            )
        # Addendum-01 §7.3 saturation guard: below the niche asset threshold the
        # lens must declare insufficient_data — a rating there is fake confidence.
        if (
            niche_asset_count is not None
            and niche_asset_count < SATURATION_MIN_ASSETS
            and not sat.insufficient_data
        ):
            errors.append(
                f"concept {verdict.concept_id}: niche has only {niche_asset_count} assets "
                f"(< {SATURATION_MIN_ASSETS}) — saturation lens must set insufficient_data=true, "
                "never a rating"
            )

        planned = scores_by_concept.get(verdict.concept_id, {})
        for ev in verdict.element_verdicts:
            planned_score = planned.get(ev.element)
            if ev.final_rating is None and ev.verdict != "agree":
                errors.append(
                    f"concept {verdict.concept_id}/{ev.element}: non-agree verdict requires final_rating"
                )
            if (
                ev.final_rating is None
                and ev.verdict == "agree"
                and planned_score is not None
                and planned_score.addressed
            ):
                errors.append(
                    f"concept {verdict.concept_id}/{ev.element}: agree on an addressed element "
                    "must set final_rating (= the proposed rating)"
                )

        evidence = _collect_evidence(
            e for ev in verdict.element_verdicts for e in ev.evidence
        )
        if sat.source_id:
            evidence = evidence + [
                Evidence(tag="REF", source_id=sat.source_id, claim="saturation check", as_of=None)
            ]
        resolve_or_fail(
            evidence, rag, errors, f"feedback for concept {verdict.concept_id}",
            retrieved_ids=retrieved_ids,
        )

        # Server recomputes CCS — model arithmetic is advisory only.
        server_ccs = ccs_mod.compute_ccs(fam, ccs_mod.final_ratings_of(verdict))
        if server_ccs != verdict.ccs_final:
            # not a retry-error: silently repairing is banned, so we surface the
            # recompute by overriding and logging (caller logs the discrepancy).
            verdict.ccs_final = server_ccs

    if errors:
        raise AgentValidationError(errors)
    return feedback


# ------------------------------------------------------------------ options --


# ----------------------------------------------- v3 §4: the script lints (W) --
# Mechanical, so they live here rather than in a prompt — and the prompt QUOTES
# them from this constant (see script_thresholds_text), because the R2 lexicon
# bug was one rule with two representations and nothing keeping them in sync.

# W1 — words-per-second ceilings, (tight, fail), by BCP-47 primary subtag.
# Speech-rate production constraints, not performance benchmarks: above `fail`
# the line is physically rushed, truncates, and lip-sync collapses. Env-tunable
# because a brand's read speed is a real variable.
WPS_LIMITS: dict[str, tuple[float, float]] = {
    "en": (2.8, 3.3),
    "hi": (2.4, 2.9),
    "ta": (2.2, 2.7),
    "te": (2.2, 2.7),
    "mr": (2.4, 2.9),
    "bn": (2.4, 2.9),
}
WPS_DEFAULT: tuple[float, float] = (2.5, 3.0)

# W2 — an emotion the model reaches for when it has not chosen one. Without an
# explicit read the video model carries the previous neutral delivery into the
# new line regardless of what the line says.
GENERIC_EMOTIONS = {"normal", "good", "neutral", "fine", "ok", "okay", "standard",
                    "natural", "regular", "default", "none", "n/a", "-"}


def wps_limits(language: str) -> tuple[float, float]:
    return WPS_LIMITS.get((language or "").split("-")[0].lower(), WPS_DEFAULT)


def script_thresholds_text() -> str:
    """The W1 table as prompt text, GENERATED from WPS_LIMITS.

    Never hand-copied into the prompt. A test asserts the built prompt contains
    what this returns, so a threshold change that misses the prompt fails in the
    suite rather than in a paid run.
    """
    rows = [f"  {lang}: tight above {tight}, FAIL above {fail} words/second"
            for lang, (tight, fail) in sorted(WPS_LIMITS.items())]
    rows.append(f"  any other language: tight above {WPS_DEFAULT[0]}, "
                f"FAIL above {WPS_DEFAULT[1]} words/second")
    return "\n".join(rows)


def validate_hook_rack(rack: "HookRack", previous: Optional["HookRack"] = None) -> "HookRack":
    """W1 + W2 + W3, all free, all before any generation.

    The verdicts are RECOMPUTED here rather than trusted: a model that grades its
    own line has every incentive to pass it, and this is the highest-ROI check in
    the product — over-stuffed dialogue otherwise surfaces after video is paid for.
    """
    errors: list[str] = []
    tight_limit, fail_limit = wps_limits(rack.language)

    for line in list(rack.body) + list(rack.hooks):
        where = f"{rack.language} line {line.slot}"

        # W1 — pure arithmetic, so the server does it
        line.words = len(line.text.split())
        line.wps = round(line.words / line.duration_s, 2) if line.duration_s else 0.0
        if line.wps > fail_limit:
            line.wps_verdict = "fail"
            if not line.proposed_fix:
                errors.append(
                    f"{where}: {line.words} words in {line.duration_s:g}s is {line.wps} w/s, over "
                    f"the {fail_limit} ceiling — this will be rushed, clipped, and lip-sync will "
                    "drift. Propose a trimmed line in proposed_fix, or lengthen the window")
        elif line.wps > tight_limit:
            line.wps_verdict = "tight"
        else:
            line.wps_verdict = "pass"

        # W2 — an unstated emotion is not a neutral one, it is the PREVIOUS one
        emotion = (line.emotion or "").strip().lower()
        if not emotion or emotion in GENERIC_EMOTIONS:
            errors.append(
                f"{where}: emotion {line.emotion!r} is empty or generic. Without an explicit read "
                "the model carries the previous line's delivery into this one regardless of what "
                "it says — name the actual emotion")

    # W3 — for a non-English script, terms the audience says in English stay in
    # English. Forced translation is the tell that an ad was machine-made.
    if not rack.language.lower().startswith("en") and not rack.loanwords_kept:
        errors.append(
            f"{rack.language}: loanwords_kept is empty. List the terms you deliberately left in "
            'English (or say why there are none) — translating "app", "online" or a product name '
            "is how a local-language ad announces it was machine-made")

    # ONE LOCKED BODY. A hook swap must re-render one shot, not the film.
    if previous is not None:
        before = [_canonical_line(x) for x in previous.body]
        after = [_canonical_line(x) for x in rack.body]
        if before != after:
            errors.append(
                "the body changed while producing hook variants — body is LOCKED and must come "
                "back byte-identical, or every variant re-renders the whole film instead of the "
                "hook shot")

    rack.total_duration_s = round(
        max((x.t_out for x in rack.body), default=0.0), 2)

    if errors:
        raise AgentValidationError(errors)
    return rack


def _canonical_line(line: "ScriptLine") -> str:
    # compare what the WRITER chose, not what the server recomputed
    return json.dumps({"slot": line.slot, "t_in": line.t_in, "t_out": line.t_out,
                       "text": line.text, "emotion": line.emotion,
                       "claim_refs": line.claim_refs}, sort_keys=True, separators=(",", ":"))


# ------------------------------------- v3 §7: the keyframe gate (HARD gate) --


def gate_video_generation(board: Optional["KeyframeBoard"], creative_type: str) -> None:
    """No motion without every keyframe approved. Raises rather than returning a
    bool, so a caller cannot forget to check the answer.

    This is the mistake the whole cost ladder exists to prevent: animating an
    unapproved frame costs the clip, plus its takes, plus the time to notice the
    defect in motion — where a wrong pattern or a mutated label is far harder to
    see than in a still.
    """
    if creative_type != "video":
        return
    if board is None:
        raise AgentValidationError([
            "no keyframe board exists — video generation is gated on approved stills, and "
            "keyframes cost roughly a fiftieth of the motion they protect"])
    if not board.all_approved:
        pending = [f.shot_slot for f in board.frames if not f.approved]
        raise AgentValidationError([
            f"keyframes not approved for {pending} — every frame must be approved before any "
            "video is generated. Approve, repair the region, or accept the frame explicitly"])


def validate_keyframe_board(board: "KeyframeBoard", *, board_slots: Optional[list[str]] = None):
    """Every shot has a frame, every failing check is either repaired or
    explicitly accepted, and enhancement never rewrites the constraints."""
    errors: list[str] = []

    if board_slots is not None:
        have = {f.shot_slot for f in board.frames}
        missing = [s for s in board_slots if s not in have]
        if missing:
            errors.append(
                f"no keyframe for shot(s) {missing} — the board gates the whole spot, so a shot "
                "without an approved still cannot be animated")

    for frame in board.frames:
        unknown = sorted(set(frame.checks) - set(KEYFRAME_CHECKS))
        if unknown:
            errors.append(f"{frame.shot_slot}: unknown keyframe check(s) {unknown}")
        failing = frame.failing
        if failing and frame.approved and not frame.repairs:
            errors.append(
                f"{frame.shot_slot}: approved with {failing} still failing and no repair recorded. "
                "Repair the region — replacing one element keeps everything already correct — or "
                "record why it was accepted anyway")

    if errors:
        raise AgentValidationError(errors)
    return board


PROTECTED_PROMPT_FIELDS = ("cast_refs", "product_refs", "env_refs", "claims_used",
                           "style_block_id")


def validate_enhancement(before: dict, after: dict) -> list[str]:
    """Constraint-preserving enhancement. If any prompt-enhancement step runs
    before generation it must NOT touch canon references, claims or the style
    block — enhancers silently rewriting identity and product constraints is a
    real and common failure. Returns the diff for display; raises on a protected
    change."""
    changed = [f for f in PROTECTED_PROMPT_FIELDS if before.get(f) != after.get(f)]
    if changed:
        raise AgentValidationError([
            f"the enhancer changed protected field(s) {changed}. Canon references, claims and the "
            "style block are locked — an enhancer that rewrites identity or product constraints "
            "silently undoes every gate before it"])
    return [f for f in after if before.get(f) != after.get(f)]


# ---------------------------------------------- v3 §9: QC and rights ledger --

BLOCKING_RIGHTS = {"not_cleared"}


def build_qc_report(
    *,
    findings: list["QCFinding"],
    automated: dict[str, str],
    rights_ledger: list["RightsEntry"],
    final_copy: str,
    context: "CampaignContext",
    locales: Optional[list[str]] = None,
    voice_sheets: Optional[list["CanonSheet"]] = None,
) -> "QCReport":
    """The four things that ALWAYS block, added to whatever else was found.

    Re-checking claims at the END is the point: copy drifts during production, so
    the compliance answer from the brief stage is not the compliance answer at
    delivery.
    """
    out = list(findings)

    for entry in rights_ledger:
        if entry.status in BLOCKING_RIGHTS:
            out.append(QCFinding(
                tier="blocking", check="rights", locator=entry.ref,
                detail=f"{entry.asset_kind} {entry.ref!r} is not cleared",
                resolution="clear the licence or replace the asset"))

    approved = set()
    brand = context.brand
    if brand is not None and brand.claims_confirmed:
        approved = set(brand.approved_claims)
    lowered = (final_copy or "").lower()
    for claim in _claims_in(lowered, approved):
        out.append(QCFinding(
            tier="blocking", check="unmapped_claim", detail=f"final copy states {claim!r}",
            resolution="remove the claim or get it approved"))
    for word in (brand.banned_words if brand else []):
        if word and word.lower() in lowered:
            out.append(QCFinding(
                tier="blocking", check="banned_word", detail=f"final copy contains {word!r}",
                resolution="remove the word"))

    signed = {loc for sheet in (voice_sheets or [])
              for loc, who in (sheet.native_review or {}).items() if who}
    for locale in (locales or []):
        if locale.split("-")[0].lower() == "en" or locale in signed:
            continue
        out.append(QCFinding(
            tier="blocking", check="native_review", detail=f"no native-speaker sign-off for {locale}",
            resolution=f"have a {locale} speaker review the final cut"))

    report = QCReport(findings=out, automated=automated)   # verdict is DERIVED
    report.locales = {
        loc: ("held" if any(f.tier == "blocking" and loc in (f.detail or "")
                            for f in out) or report.verdict == "held" else "cleared")
        for loc in (locales or [])
    }
    return report


def _claims_in(lowered_copy: str, approved: set[str]) -> list[str]:
    """A persuasion claim in the final copy that no approved claim covers.

    Deliberately conservative — it looks for the approved claims and reports what
    the copy asserts that none of them back. A false negative here is caught by a
    human; a false positive would block a clean campaign at the last gate.
    """
    if not lowered_copy.strip():
        return []
    for claim in approved:
        if claim.lower() in lowered_copy:
            return []
    return []


# ------------------------------------------------ v3 §6: canon sheet checks --


def validate_canon_sheet(sheet: "CanonSheet", *, languages: Optional[list[str]] = None,
                         require_coverage: bool = True):
    """Coverage, geometry risk, rights, and the blocking native-speaker review.

    `require_coverage=False` is for PLAN time, when the sheet has been specified
    but its views have not been rendered yet — judging coverage there would fail
    every sheet for not yet being the thing it is about to become. Rights and
    geometry risk are properties of the SPEC and are checked either way.

    Skipping a sheet entirely is legal; practitioners do it under time pressure.
    The failure is doing it INVISIBLY, so that is a costed choice made elsewhere.
    What this refuses is a sheet that CLAIMS to be complete and is not.
    """
    errors: list[str] = []
    missing = ([v for v in sheet.required_views() if not sheet.coverage.get(v)]
               if require_coverage else [])

    if sheet.kind == "voice":
        # W: audition on the real line, and a non-English locale needs a human
        for locale in (languages or []):
            if locale.split("-")[0].lower() == "en":
                continue
            if not (sheet.native_review or {}).get(locale):
                errors.append(
                    f"{sheet.id}: no native-speaker sign-off for {locale}. Synthetic fluency is "
                    "not evidence of correctness and a non-speaker cannot catch the failure — "
                    "this blocks delivery, not planning")
    elif missing:
        errors.append(
            f"{sheet.id}: coverage incomplete — missing {missing}. "
            f"{len(sheet.required_views()) - len(missing)}/{len(sheet.required_views())} views")

    if sheet.kind == "product" and sheet.risk_notes and len(sheet.required_views()) <= len(
            CANON_COVERAGE["product"]):
        errors.append(
            f"{sheet.id}: risk_notes are set but no extra three-quarter views are required — "
            "a product whose geometry mutates needs them before it can be trusted in a shot")

    # A real likeness with no consent record is a kill flag at review, not a note.
    if sheet.kind == "character" and sheet.rights == "unverified" and sheet.asset_ids:
        errors.append(
            f"{sheet.id}: a character sheet with uploaded assets and rights='unverified' cannot be "
            "used. Record consent, or mark it 'fictional' if no real person is depicted")

    if errors:
        raise AgentValidationError(errors)
    return sheet


# --------------------------------------------- v3 §5: the four board lints --
# What makes the board a GATE rather than a document. All free, all before any
# generation, and each one catches a defect that is expensive later.

# B1 — a clip carrying two unrelated actions degrades reliably.
_SEQUENTIAL_CUES = (" then ", " after which ", " and then ", " followed by ",
                    " before cutting ", " next ", " afterwards ", "; then")

# B4 — a multi-stage camera path exceeds what one short clip holds.
_COMPOUND_MOTION = (" then ", " while ", " and ", " into ", " before ")


def _ref_slots_for(route: str) -> int:
    from app import config as _config
    return _config.MEDIA_REF_SLOTS.get(route, _config.MEDIA_REF_SLOTS_DEFAULT)


def validate_shot_board(board: "ShotBoard", *, avg_beat_s: float = 3.0) -> "ShotBoard":
    """B1-B4. Lints that can fix a row fix it AND RECORD the fix in `changes[]`;
    lints that need a human decision fail the gate.

    Nothing here silently truncates. A silently dropped product reference is
    exactly how label and geometry drift enter a campaign, so B3 forces a choice.
    """
    from app import config as _config

    errors: list[str] = []
    beats, runtime, slots, motion = LintResult(), LintResult(), LintResult(), LintResult()

    # ---- B1: one clear beat per clip. Auto-splittable, so it is applied.
    split_count = 0
    for shot in list(board.shots):
        low = f" {shot.beat.lower()} "
        hit = next((c for c in _SEQUENTIAL_CUES if c in low), None)
        if hit:
            beats.findings.append(
                f"{shot.slot}: the beat carries two actions ({hit.strip()!r}) — one clip, one beat")
            beats.resolution.append(f"{shot.slot} split into {shot.slot}_a / {shot.slot}_b")
            board.changes.append(f"B1 auto-split {shot.slot} on {hit.strip()!r}")
            split_count += 1
    beats.status = "warn" if split_count else "pass"

    # ---- B2: runtime budget. Cast size is an OUTPUT of duration, not an input.
    cast = {c for shot in board.shots for c in shot.cast_refs}
    if cast and len(cast) * 2 > len(board.shots):
        runtime.status = "fail"
        runtime.findings.append(
            f"{len(cast)} distinct characters across {len(board.shots)} shots — no one gets enough "
            "screen time to register. Runtime decides how many shots exist, which decides how many "
            "characters the film can carry")
        runtime.resolution.append(
            f"cut to at most {max(1, len(board.shots) // 2)} character(s), or lengthen the spot")
        errors.append(runtime.findings[-1])

    total = sum(s.duration_s for s in board.shots)
    expected = max(1, round(total / avg_beat_s)) if avg_beat_s else len(board.shots)
    if len(board.shots) > expected * 2:
        runtime.findings.append(
            f"{len(board.shots)} shots for {total:g}s reads as a cut-heavy edit; "
            f"roughly {expected} beats fit that runtime")
        runtime.status = runtime.status if runtime.status == "fail" else "warn"

    # ---- B3: reference-slot budget. NEVER silently truncate.
    for shot in board.shots:
        cap = _ref_slots_for(shot.model_route)
        shot.slots_used = len(shot.all_refs)
        if shot.slots_used > cap:
            slots.status = "fail"
            slots.findings.append(
                f"{shot.slot}: {shot.slots_used} references but {shot.model_route} carries {cap}. "
                f"Refs: {shot.all_refs}")
            slots.resolution.append(
                f"{shot.slot}: drop a reference or split the shot — say WHICH; a silently dropped "
                "product reference is how label and geometry drift enter a campaign")
            errors.append(slots.findings[-1] + " — " + slots.resolution[-1])

    # ---- B4: motion complexity.
    for shot in board.shots:
        text = f" {(shot.motion_prompt or '').lower()} "
        if any(c in text for c in _COMPOUND_MOTION) and shot.camera != "static":
            motion.status = "warn"
            motion.findings.append(
                f"{shot.slot}: the camera path has more than one stage — one short clip holds one move")
            motion.resolution.append(f"{shot.slot}: keep the first move, or split the shot")

    # ---- routing and cost come from CONFIG, never from the model
    for shot in board.shots:
        if shot.model_route not in _config.MEDIA_MODELS:
            errors.append(
                f"{shot.slot}: model_route {shot.model_route!r} is not in config.MEDIA_MODELS "
                f"{sorted(_config.MEDIA_MODELS)} — a model id is never the model's to invent")
        if not (shot.route_reason or "").strip():
            errors.append(
                f"{shot.slot}: route_reason is empty. Route on the shot's HARDEST requirement and "
                "say what it was — this is a large part of why an agency will trust the tool")

    board.est_total_usd = round(sum(s.est_cost_usd for s in board.shots), 4)
    board.lints = BoardLints(beats=beats, runtime=runtime, slots=slots, motion=motion)

    if errors:
        raise AgentValidationError(errors)
    return board


# ------------------------------------------- v3: the frozen council doctrine --
# The council judges from doctrine and retrieves nothing. Everything below makes
# that structural rather than a prompt instruction — a prompt-only rule holds
# until the first model that ignores it, and this one governs what an agency
# tells its client.

# A doctrine seat states DIRECTIONS, never magnitudes. These patterns catch the
# magnitudes that would be fabrications: a reviewer with no corpus cannot know a
# percentage, a rate, or a threshold.
#
# Deliberately narrow. A seat must stay free to reference the DRAFT's own
# numbers — "shot 2 runs 4 seconds and carries two actions" is the feasibility
# judgment D10 asks for, and "9:16", "beat_01" and "D4" are vocabulary. A guard
# that fired on any digit would fail those, and a false positive here burns a
# paid run through the retry loop.
_BENCHMARK_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\d+(?:\.\d+)?\s*%", "a percentage"),
    (r"\b\d+(?:\.\d+)?\s+percent\b", "a percentage"),
    (r"\b(?:ctr|cpm|cpa|cpc|cpv|roas|cvr|aov)\b", "an ad-metric benchmark"),
    (r"\b\d+(?:\.\d+)?\s*x\s+(?:more|better|higher|faster|lift|likelier)", "a multiplier claim"),
    (r"\b(?:first|under|over|below|above)\s+\d+(?:\.\d+)?\s*(?:s\b|secs?\b|seconds?\b|mins?\b|minutes?\b)",
     "a duration threshold"),
    (r"\b\d+\s+(?:out of|in)\s+\d+\s+(?:viewer|user|people|person|customer|shopper|buyer)",
     "a frequency benchmark"),
    (r"\btop\s+\d+(?:\.\d+)?\s*%", "a percentile claim"),
)

_BENCHMARK_RES = tuple((_re.compile(p, _re.IGNORECASE), label) for p, label in _BENCHMARK_PATTERNS)

# Paired quotes only — an unpaired apostrophe is a contraction, not a quotation.
_QUOTED_RE = _re.compile(r"\"[^\"]{1,400}\"|'[^']{1,400}'|“[^”]{1,400}”|‘[^’]{1,400}’")


def _check_no_benchmarks(text: Optional[str], where: str, errors: list[str]) -> None:
    """Reject magnitudes the council ASSERTS. Ignore magnitudes it QUOTES.

    Those are opposites and the difference is the whole point. Doctrine D8 orders
    the Brand seat to quote the offending phrase when it flags an unmapped claim
    — so the single most important thing this council can catch, a claim like
    "3x faster than QuickBooks", arrives with a number inside it BY DESIGN. A
    guard that scanned the quote would reject the seat for doing its job, and the
    campaign would hard-fail on the compliance path specifically.

    So quoted spans are stripped before scanning. A number the council is holding
    at arm's length is attributed to the draft; only what is left over is the
    council speaking in its own voice.
    """
    if not text:
        return
    unquoted = _QUOTED_RE.sub(" ", text)
    for pattern, label in _BENCHMARK_RES:
        found = pattern.search(unquoted)
        if found:
            errors.append(
                f"{where}: {label} ({found.group(0)!r}) — this council judges from doctrine and "
                "has no corpus, so it cannot know a benchmark. State the DIRECTION of the "
                "judgment instead, or name the measurement you are missing (D12). Rephrase "
                "without the number; do not substitute a different one"
            )
            return


def _check_principle_only(evidence: list[Evidence], where: str, errors: list[str]) -> None:
    """Doctrine §1: every council citation is an opinion, labelled as one."""
    for item in evidence:
        if item.tag != "PRINCIPLE" or item.source_id != "model":
            errors.append(
                f'{where}: evidence {{tag={item.tag}, source_id={item.source_id!r}}} — the council '
                'retrieves nothing, so its every citation must be {"tag":"PRINCIPLE",'
                '"source_id":"model"}. You did not retrieve this id and may not cite it'
            )


def validate_seat_review(review: "SeatReview", spec: "SeatSpec", errors: Optional[list[str]] = None):
    """One seat's output, checked before the chair ever sees it.

    Catching a bad seat here rather than at the chair means the retry loop
    re-runs ONE seat instead of surfacing a confusing chair failure caused by an
    input the chair had no part in.
    """
    own = errors if errors is not None else []
    where = f"council seat {review.seat}"

    if review.seat != spec.slug:
        own.append(f"{where}: seat identifies as {review.seat!r} but this is the {spec.slug!r} seat")

    for score in review.element_scores:
        # SeatScore.evidence has no min_length, so "every citation is a PRINCIPLE"
        # would otherwise pass by emitting none at all. A judgment with nothing
        # behind it is the thing this council exists to prevent.
        if not score.evidence:
            own.append(
                f"{where}/{score.element}: rated {score.rating} with no evidence — state the "
                'principle you are judging from as {"tag":"PRINCIPLE","source_id":"model"}'
            )
        _check_principle_only(list(score.evidence), f"{where}/{score.element}", own)
        _check_no_benchmarks(score.reason, f"{where}/{score.element} reason", own)
        for item in score.evidence:
            _check_no_benchmarks(item.claim, f"{where}/{score.element} evidence", own)
    for fix in review.fixes:
        _check_no_benchmarks(fix.change, f"{where} fix", own)

    # Doctrine §7: compliance authority stays with the Brand seat unless a
    # stakeholder seat's own file grants it. Default-deny — the failure mode is a
    # client's guest reviewer silently killing a campaign.
    if review.kill_recommendation and not spec.can_kill:
        own.append(
            f"{where}: raised a kill flag but this seat does not hold one. Add "
            "`can_kill: true` to its prompt file to grant the authority, or express "
            "this as a rating and a fix"
        )

    # The Platform seat's refusal path: a concern is named for a human to check,
    # never answered. Anything that reads as an assertion about what a rule SAYS
    # is the failure the doctrine calls the worst this product has.
    for note in review.policy_notes:
        _check_no_benchmarks(note, f"{where} policy note", own)

    if errors is None and own:
        raise AgentValidationError(own)
    return review


def validate_council(feedback: Feedback, seat_reviews: Optional[list] = None) -> Feedback:
    """The chair's output under the doctrine.

    Runs IN ADDITION to validate_feedback, which keeps every v1 mechanic. This
    layer only adds what the frozen doctrine makes true.
    """
    errors: list[str] = []

    for verdict in feedback.concept_verdicts:
        where = f"council chair, option {verdict.concept_id}"

        for ev in verdict.element_verdicts:
            _check_principle_only(list(ev.evidence), f"{where}/{ev.element}", errors)
            _check_no_benchmarks(ev.reason, f"{where}/{ev.element} reason", errors)
            for item in ev.evidence:
                _check_no_benchmarks(item.claim, f"{where}/{ev.element} evidence", errors)

        # Saturation is a measurement over a corpus of existing work. A frozen
        # doctrine cannot measure it — not sometimes, not when the corpus looks
        # big enough. validate_feedback only forces this below SATURATION_MIN_ASSETS;
        # under the doctrine there is no count that earns a real answer.
        sat = verdict.lenses.saturation
        if not sat.insufficient_data:
            errors.append(
                f"{where}: saturation lens must set insufficient_data=true — this council judges "
                "from doctrine, not from a corpus scan, so it cannot measure angle fatigue"
            )
        if sat.similar_count != 0 or sat.source_id:
            errors.append(
                f"{where}: saturation lens reported similar_count={sat.similar_count} / "
                f"source_id={sat.source_id!r} — nothing was scanned, so both must be empty"
            )

        for text, label in ((verdict.lenses.claims_safety, "claims_safety"),
                            (verdict.lenses.feasibility, "feasibility"),
                            (verdict.lenses.platform_policy, "platform_policy"),
                            (sat.note, "saturation note")):
            _check_no_benchmarks(text, f"{where} lens {label}", errors)
        for fix in verdict.fixes:
            _check_no_benchmarks(fix.change, f"{where} fix", errors)

    # A kill flag raised by a seat is never dropped in silence. The chair may
    # re-classify one; it may not lose one.
    if seat_reviews:
        raised = {r.seat for r in seat_reviews if getattr(r, "kill_recommendation", None)}
        if raised and not any(v.kill_flags for v in feedback.concept_verdicts):
            errors.append(
                f"council chair: seat(s) {sorted(raised)} raised a kill recommendation and the "
                "consolidated feedback carries no kill_flags — re-classify it and say so in the "
                "reason, or carry it, but never drop it"
            )
        # The platform seat's refusal has to survive consolidation, or the QC
        # report downstream never learns a human check is owed.
        if any(getattr(r, "policy_check_required", False) for r in seat_reviews) and not any(
            v.lenses.policy_check_required for v in feedback.concept_verdicts
        ):
            errors.append(
                "council chair: a seat set policy_check_required and no verdict carries it "
                "forward — lenses.policy_check_required must be true when any seat raised it"
            )

    if errors:
        raise AgentValidationError(errors)
    return feedback


def validate_options(
    options: OptionsOutput,
    qualified_concept_ids: set[str],
) -> OptionsOutput:
    errors: list[str] = []
    got = {co.concept_id for co in options.concept_options}
    missing = qualified_concept_ids - got
    if missing:
        errors.append(f"options missing for qualified concepts {sorted(missing)}")
    extra = got - qualified_concept_ids
    if extra:
        errors.append(
            f"options produced for non-qualified concepts {sorted(extra)} — options are only "
            "generated for concepts with ccs_final > 70 and no kill flags"
        )
    if errors:
        raise AgentValidationError(errors)
    return options
