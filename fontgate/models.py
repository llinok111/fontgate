"""Result shapes shared by every check, plus canonical serialisation.

Canonical form matters here: reports are meant to be committed and diffed
between builds.  ``json.dumps`` alone is key-order sensitive, so the same
logical result written twice could hash differently and look like a change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

SEVERITY_RANK = {"info": 1, "warn": 2, "error": 3}

#: Finding codes are stable identifiers; tests and downstream CI match on them
#: rather than on message text.
FINDING_CODES: Dict[str, str] = {
    "coverage-missing": "a requested character has no cmap entry in the subset",
    "coverage-empty": "a requested character maps to a glyph with no outline",
    "coverage-unexpected": "a requested character is absent from the source font",
    "metrics-advance": "advance width changed between source and subset",
    "metrics-bounds": "glyph bounding box changed between source and subset",
    "layout-dangling": "a layout table references a glyph that is not in the font",
    "layout-feature-lost": "the subset dropped a layout feature the source had",
    "table-missing": "a table required for rendering is absent",
    "format-mismatch": "file content does not match its extension or declaration",
    "identity-drift": "family or weight identity changed during subsetting",
    "family-cmap-drift": "faces of one family cover different character sets",
    "family-metrics-drift": "faces of one family disagree on advance width",
    "supplementary-unsupported": "the font cannot address code points above U+FFFF",
    "budget-exceeded": "the file is larger than the configured budget",
    "container-risk": "the file uses a container a browser cannot load directly",
}


def canonical(value: Any) -> Any:
    """Recursively sort mapping keys so equal data always serialises equal."""
    if isinstance(value, dict):
        return {str(k): canonical(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def dumps(value: Any, indent: Optional[int] = None) -> str:
    """Deterministic JSON.  ``sort_keys`` is redundant with :func:`canonical`
    but keeps the guarantee if a caller passes a raw mapping."""
    if indent is None:
        return json.dumps(
            canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    return json.dumps(canonical(value), ensure_ascii=False, sort_keys=True, indent=indent)


@dataclass
class Finding:
    code: str
    severity: str
    message: str
    detail: Dict[str, Any] = field(default_factory=dict)
    scope: str = "general"

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY_RANK:
            raise ValueError("unknown severity %r" % (self.severity,))
        if self.code not in FINDING_CODES:
            raise ValueError(
                "unregistered finding code %r; add it to FINDING_CODES" % (self.code,)
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail,
            "scope": self.scope,
        }


@dataclass
class Check:
    """One named assertion with a machine-readable verdict."""

    name: str
    status: str  # pass | warn | fail | skip
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
        }


def worst(findings: Iterable[Finding]) -> str:
    rank = 0
    label = "pass"
    for finding in findings:
        value = SEVERITY_RANK[finding.severity]
        if value > rank:
            rank = value
            label = "fail" if finding.severity == "error" else "warn"
    return label


def has_errors(findings: Iterable[Finding]) -> bool:
    return any(f.severity == "error" for f in findings)


def summarize(findings: List[Finding]) -> Dict[str, int]:
    counts = {"error": 0, "warn": 0, "info": 0}
    for finding in findings:
        counts[finding.severity] += 1
    return counts
