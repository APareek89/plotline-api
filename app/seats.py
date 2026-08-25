"""The council's seat registry.

Seats used to be a hardcoded three-value list. v3 makes them a registry so an
agency can define a seat for *their client's* reviewer ("my_cmo") — the feature
the doctrine calls the agency sale. A seat is a prompt file plus two facts:
whether it is built in, and whether it may hold a kill flag.

DEPENDENCY NOTE. This module reads prompt headers itself instead of calling
`runner.load_prompt`, because `runner` imports `validators` and `validators`
imports this registry — routing through runner would close that loop. The parse
here is deliberately tiny: a header comment, not a format.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from app import config

# The three seats the doctrine defines. Always present: they are the only
# holders of the three kill flags, and a council that can lose its Brand seat
# is a council that can lose compliance authority by configuration.
BUILTIN_SEATS: tuple[str, ...] = ("performance", "brand", "platform")

# Doctrine §7. Past this the chair's consolidation degrades and the extra cost
# is not repaid — so it is a cap, not a suggestion.
MAX_SEATS = 5

_PROMPT_PREFIX = "seat_"
_CAN_KILL_RE = re.compile(r"can_kill:\s*(true|yes|1)", re.IGNORECASE)


class SeatConfigError(ValueError):
    """A campaign asked for a seat that cannot be honoured. Surfaced rather than
    silently dropped — a review the user thinks happened and did not is worse
    than a refusal."""


@dataclass(frozen=True)
class SeatSpec:
    slug: str
    prompt_name: str
    can_kill: bool
    builtin: bool

    @property
    def agent_name(self) -> str:
        return f"council.{self.slug}"


def _prompt_path(slug: str):
    return config.PROMPTS_DIR / "council" / f"{_PROMPT_PREFIX}{slug}.md"


def _spec(slug: str) -> Optional[SeatSpec]:
    path = _prompt_path(slug)
    if not path.exists():
        return None
    builtin = slug in BUILTIN_SEATS
    # A built-in seat owns a kill flag by doctrine §5. A stakeholder seat gets
    # one ONLY by saying so in its own file — default-deny, because the failure
    # mode is a client's guest reviewer silently killing a campaign.
    can_kill = builtin or bool(_CAN_KILL_RE.search(path.read_text()))
    return SeatSpec(slug=slug, prompt_name=f"council/{_PROMPT_PREFIX}{slug}",
                    can_kill=can_kill, builtin=builtin)


def available() -> dict[str, SeatSpec]:
    """Every seat with a prompt file on disk, built-in or stakeholder."""
    found: dict[str, SeatSpec] = {}
    for slug in BUILTIN_SEATS:
        spec = _spec(slug)
        if spec is not None:
            found[slug] = spec
    council_dir = config.PROMPTS_DIR / "council"
    if council_dir.exists():
        for path in sorted(council_dir.glob(f"{_PROMPT_PREFIX}*.md")):
            slug = path.stem[len(_PROMPT_PREFIX):]
            if slug and slug not in found:
                spec = _spec(slug)
                if spec is not None:
                    found[slug] = spec
    return found


def resolve(extra_slugs: Optional[Iterable[str]] = None) -> list[SeatSpec]:
    """The seat list for one campaign: the three built-ins, then any configured
    stakeholder seats in the order given.

    Stakeholder seats are ADDITIVE. There is no way to configure a built-in seat
    away — see BUILTIN_SEATS. Order is canonical and deterministic, which is what
    lets the chair see the same input twice for the same campaign.
    """
    registry = available()
    missing_builtin = [s for s in BUILTIN_SEATS if s not in registry]
    if missing_builtin:
        raise SeatConfigError(
            f"built-in council seat prompt(s) missing from disk: {missing_builtin}"
        )

    seats: list[SeatSpec] = [registry[s] for s in BUILTIN_SEATS]
    seen = set(BUILTIN_SEATS)
    for slug in extra_slugs or []:
        slug = (slug or "").strip()
        if not slug or slug in seen:
            continue
        spec = registry.get(slug)
        if spec is None:
            raise SeatConfigError(
                f"stakeholder seat {slug!r} has no prompt at "
                f"prompts/council/{_PROMPT_PREFIX}{slug}.md — add the file or remove the seat"
            )
        seats.append(spec)
        seen.add(slug)

    if len(seats) > MAX_SEATS:
        raise SeatConfigError(
            f"{len(seats)} seats configured but the cap is {MAX_SEATS} — beyond that the "
            "chair's consolidation degrades and the extra council cost is not repaid"
        )
    return seats


def slugs(seats: Iterable[SeatSpec]) -> list[str]:
    return [s.slug for s in seats]
