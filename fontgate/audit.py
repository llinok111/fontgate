"""Cross-reference a character set against one or more font faces.

The matrix this produces is deliberately asymmetric in the report: full detail
is kept in memory for rendering, but only *failures* are serialised.  A
"5,000 characters OK" table is noise; the 37 that are missing are the work.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .charset import CharsetReport, CharUse
from .errors import InputError
from .fontfile import (
    FontProfile,
    is_blank_character,
    is_variation_selector,
    open_font,
    profile as build_profile,
    unicode_cmap,
    glyph_outline_status,
    face_signature,
)
from .models import Check, Finding, summarize, worst

STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_EMPTY = "empty"
STATUS_BLANK = "blank"  # whitespace: no outline expected, not a defect


@dataclass
class FontAudit:
    profile: FontProfile
    covered: int
    missing: List[CharUse]
    empty: List[CharUse]
    blank: int

    @property
    def total(self) -> int:
        return self.covered + len(self.missing) + len(self.empty) + self.blank

    @property
    def coverage_ratio(self) -> float:
        denom = self.total - self.blank
        if denom <= 0:
            return 1.0
        return round(self.covered / denom, 6)

    def to_dict(self, limit: int = 40) -> Dict[str, Any]:
        return {
            "font": self.profile.to_dict(),
            "covered": self.covered,
            "missing_count": len(self.missing),
            "empty_count": len(self.empty),
            "blank_count": self.blank,
            "coverage_ratio": self.coverage_ratio,
            "missing": [c.to_dict(max_files=3) for c in self.missing[:limit]],
            "empty": [c.to_dict(max_files=3) for c in self.empty[:limit]],
        }


@dataclass
class AuditResult:
    charset: CharsetReport
    fonts: List[FontAudit]
    findings: List[Finding]
    checks: List[Check]

    @property
    def status(self) -> str:
        return worst(self.findings)

    def to_dict(self, limit: int = 40) -> Dict[str, Any]:
        return {
            "kind": "audit",
            "status": self.status,
            "charset": self.charset.to_dict(),
            "fonts": [f.to_dict(limit=limit) for f in self.fonts],
            "findings": [f.to_dict() for f in self.findings],
            "checks": [c.to_dict() for c in self.checks],
            "summary": summarize(self.findings),
        }


def _char_key(use: CharUse) -> Tuple[int, int]:
    return (-use.count, use.codepoint)


def audit_one(
    profile_obj: FontProfile,
    charset: CharsetReport,
    limit: int = 40,
) -> FontAudit:
    """Walk the character set once, recording coverage and empty outlines."""
    font = open_font(profile_obj.path, profile_obj.index)
    try:
        cmap = unicode_cmap(font)
        covered = 0
        missing: List[CharUse] = []
        empty: List[CharUse] = []
        blank = 0
        for cp in sorted(charset.chars):
            use = charset.chars[cp]
            if is_variation_selector(cp):
                # Addressed through cmap format 14, never through the main map.
                blank += 1
                continue
            name = cmap.get(cp)
            if name is None:
                missing.append(use)
                continue
            if is_blank_character(use.char):
                covered += 1
                continue
            status = glyph_outline_status(font, name)
            if status == "empty":
                empty.append(use)
            else:
                covered += 1
    finally:
        try:
            font.close()
        except Exception:  # pragma: no cover
            pass

    missing.sort(key=_char_key)
    empty.sort(key=_char_key)
    return FontAudit(
        profile=profile_obj,
        covered=covered,
        missing=missing,
        empty=empty,
        blank=blank,
    )


def family_consistency(fonts: Sequence[FontProfile]) -> List[Finding]:
    """Compare faces that claim to be the same family.

    Browsers synthesise bold from the nearest real face.  If the bold face
    covers a smaller code point set, those characters render in a *different*
    typeface than the rest of the run — visible as a shape jump mid-sentence.
    """
    groups: Dict[Tuple[str, int], List[FontProfile]] = {}
    for item in fonts:
        groups.setdefault(face_signature(item), []).append(item)

    findings: List[Finding] = []
    for (family, weight), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        digests = {m.cmap_digest for m in members}
        if len(digests) > 1:
            findings.append(
                Finding(
                    code="family-cmap-drift",
                    severity="error",
                    message=(
                        "same family and weight but different cmap: %s"
                        % ", ".join(sorted(m.path for m in members))
                    ),
                    detail={
                        "family": family,
                        "weight": weight,
                        "faces": [
                            {
                                "path": m.path,
                                "index": m.index,
                                "cmap_digest": m.cmap_digest,
                                "cmap_count": m.cmap_count,
                            }
                            for m in members
                        ],
                    },
                    scope="family",
                )
            )

    # Cross-weight drift is the common case and is graded as a warning: a
    # missing glyph in one weight is recoverable via fallback, but it does
    # change the rendered shape, so it must not be silent.
    by_family: Dict[str, List[FontProfile]] = {}
    for item in fonts:
        if item.family:
            by_family.setdefault(item.family.strip(), []).append(item)
    for family, members in sorted(by_family.items()):
        weights = {m.weight_class for m in members}
        if len(weights) < 2:
            continue
        union: Dict[int, List[str]] = {}
        for member in members:
            font = open_font(member.path, member.index)
            try:
                cmap = unicode_cmap(font)
            finally:
                try:
                    font.close()
                except Exception:  # pragma: no cover
                    pass
            for cp in cmap:
                union.setdefault(cp, []).append(str(member.weight_class))
        partial = {
            cp: present
            for cp, present in union.items()
            if len(set(present)) < len(weights)
        }
        if partial:
            sample = sorted(partial)[:24]
            findings.append(
                Finding(
                    code="family-cmap-drift",
                    severity="warn",
                    message=(
                        "%s has %d code points that not every weight covers"
                        % (family, len(partial))
                    ),
                    detail={
                        "family": family,
                        "weights": sorted(weights),
                        "partial_count": len(partial),
                        "sample": [
                            {"hex": "U+%04X" % cp, "char": chr(cp), "weights": partial[cp]}
                            for cp in sample
                            if _printable(cp)
                        ],
                    },
                    scope="family",
                )
            )
    return findings


def _printable(cp: int) -> bool:
    try:
        ch = chr(cp)
    except ValueError:  # pragma: no cover
        return False
    return unicodedata.category(ch)[0] not in ("C",)


def audit(
    font_specs: Sequence[Tuple[str, int]],
    charset: CharsetReport,
    limit: int = 40,
) -> AuditResult:
    """Audit every font against the same character set."""
    if not font_specs:
        raise InputError("at least one font is required")
    if not charset.chars:
        raise InputError("the character set is empty; nothing to audit")

    profiles = [build_profile(path, index) for path, index in font_specs]
    audits = [audit_one(p, charset, limit=limit) for p in profiles]

    findings: List[Finding] = []
    checks: List[Check] = []

    for result in audits:
        label = result.profile.label
        if result.profile.container == "collection":
            findings.append(
                Finding(
                    code="container-risk",
                    severity="warn",
                    message=(
                        "%s is a TrueType Collection; browsers cannot load .ttc "
                        "through @font-face, extract face %d first"
                        % (result.profile.path, result.profile.index)
                    ),
                    detail={"path": result.profile.path, "index": result.profile.index},
                    scope="font",
                )
            )
        if result.profile.missing_required:
            findings.append(
                Finding(
                    code="table-missing",
                    severity="error",
                    message="%s lacks required tables: %s"
                    % (label, ", ".join(result.profile.missing_required)),
                    detail={"path": result.profile.path, "tables": result.profile.missing_required},
                    scope="font",
                )
            )

        needs_supplementary = any(cp > 0xFFFF for cp in charset.chars)
        if (
            needs_supplementary
            and not result.missing
            and not result.profile.supports_supplementary
        ):
            findings.append(
                Finding(
                    code="supplementary-unsupported",
                    severity="error",
                    message=(
                        "%s has no format 12 cmap subtable but the text contains "
                        "code points above U+FFFF" % label
                    ),
                    detail={"path": result.profile.path},
                    scope="font",
                )
            )

        if result.missing:
            findings.append(
                Finding(
                    code="coverage-missing",
                    severity="error",
                    message="%s is missing %d of %d characters (%d occurrences)"
                    % (
                        label,
                        len(result.missing),
                        len(charset.chars),
                        sum(c.count for c in result.missing),
                    ),
                    detail={
                        "path": result.profile.path,
                        "index": result.profile.index,
                        "count": len(result.missing),
                        "chars": [c.to_dict(max_files=3) for c in result.missing[:limit]],
                    },
                    scope="font",
                )
            )

        if result.empty:
            findings.append(
                Finding(
                    code="coverage-empty",
                    severity="error",
                    message="%s maps %d characters to empty glyphs"
                    % (label, len(result.empty)),
                    detail={
                        "path": result.profile.path,
                        "chars": [c.to_dict(max_files=3) for c in result.empty[:limit]],
                    },
                    scope="font",
                )
            )

        checks.append(
            Check(
                name="coverage:%s" % label,
                status="pass" if not result.missing and not result.empty else "fail",
                summary="%d/%d characters renderable"
                % (result.covered, max(result.total - result.blank, 1)),
                details={
                    "path": result.profile.path,
                    "coverage_ratio": result.coverage_ratio,
                    "missing": len(result.missing),
                    "empty": len(result.empty),
                },
            )
        )

    if len(profiles) > 1:
        family_findings = family_consistency(profiles)
        findings.extend(family_findings)

    checks.append(
        Check(
            name="charset",
            status="pass" if charset.files_scanned else "warn",
            summary="%d unique code points from %d files"
            % (len(charset.chars), charset.files_scanned),
            details={
                "files_scanned": charset.files_scanned,
                "files_skipped": charset.files_skipped,
                "bytes_scanned": charset.bytes_scanned,
            },
        )
    )

    return AuditResult(
        charset=charset,
        fonts=audits,
        findings=findings,
        checks=checks,
    )
