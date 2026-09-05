"""Score unified-status confidence without averaging contradictory sources."""

from __future__ import annotations

from enum import Enum

from app.models import Confidence


class EvidenceOrigin(str, Enum):
    """Which source actually informed an adopted status field."""

    OFFICIAL = "official"
    SLEEPER = "sleeper"
    NFLVERSE = "nflverse"
    NONE = "none"


def score_status_confidence(
    *origins: EvidenceOrigin | str | None,
    origin_fresh: bool = True,
) -> Confidence:
    """Return one confidence from the strongest informing source.

    Contradictory rows stay in ``source_results``; they are not blended. Official
    NFL inactives and injury reports remain ``OFFICIAL`` when they informed the
    decision. Cached official evidence is reduced to ``HIGH``.
    """

    informing = {_origin(value) for value in origins}
    informing.discard(EvidenceOrigin.NONE)
    if EvidenceOrigin.OFFICIAL in informing:
        base = Confidence.OFFICIAL
    elif EvidenceOrigin.SLEEPER in informing:
        base = Confidence.MEDIUM
    elif EvidenceOrigin.NFLVERSE in informing:
        base = Confidence.LOW
    else:
        base = Confidence.LOW
    return base if origin_fresh else degrade_cached_confidence(base)


def degrade_cached_confidence(confidence: Confidence) -> Confidence:
    """Reduce confidence when official evidence is reused after a failed refresh."""

    if confidence is Confidence.OFFICIAL:
        return Confidence.HIGH
    if confidence is Confidence.HIGH:
        return Confidence.MEDIUM
    if confidence is Confidence.MEDIUM:
        return Confidence.LOW
    return Confidence.LOW


def _origin(value: EvidenceOrigin | str | None) -> EvidenceOrigin:
    if value is None:
        return EvidenceOrigin.NONE
    if isinstance(value, EvidenceOrigin):
        return value
    try:
        return EvidenceOrigin(value)
    except ValueError as exc:
        raise ValueError(f"Unknown evidence origin: {value}") from exc
