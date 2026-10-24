"""Prove that a subset font is a faithful reduction of its source.

Each check is independent and reports its own verdict.  A missing expectation
set downgrades coverage to ``skip`` rather than to a pass — silence must never
look like success.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .errors import InputError
from .fontfile import (
    FontProfile,
    glyph_outline_status,
    is_blank_character,
    open_font,
    profile as build_profile,
    sniff_format,
    unicode_cmap,
    supports_supplementary,
)
from .models import Check, Finding, has_errors, summarize, worst

#: Layout features whose disappearance changes shaping rather than only
#: typographic taste.  Losing any of these from a subset is an error.
CRITICAL_FEATURES = ("ccmp", "liga", "clig", "kern", "mark", "mkmk", "rlig", "calt")

EXTENSION_FORMAT = {
    ".woff2": "woff2",
    ".woff": "woff",
    ".ttf": "truetype",
    ".otf": "opentype",
    ".ttc": "collection",
    ".otc": "collection",
}


@dataclass
class VerifyResult:
    source: FontProfile
    subset: FontProfile
    checks: List[Check]
    findings: List[Finding]
    expected: int
    covered: int

    @property
    def status(self) -> str:
        return worst(self.findings)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": "verify",
            "status": self.status,
            "source": self.source.to_dict(),
            "subset": self.subset.to_dict(),
            "expected": self.expected,
            "covered": self.covered,
            "checks": [c.to_dict() for c in self.checks],
            "findings": [f.to_dict() for f in self.findings],
            "summary": summarize(self.findings),
            "size": {
                "source_bytes": self.source.file_size,
                "subset_bytes": self.subset.file_size,
                "ratio": round(self.subset.file_size / self.source.file_size, 6)
                if self.source.file_size
                else None,
                "saved_bytes": self.source.file_size - self.subset.file_size,
            },
        }


def _expected_codepoints(text: Optional[str]) -> Optional[Set[int]]:
    if text is None:
        return None
    return {ord(ch) for ch in text if not is_blank_character(ch) or ch == " "}


def check_format(profile_obj: FontProfile, declared: Optional[str] = None) -> Tuple[Check, List[Finding]]:
    """Content, extension and declaration must agree."""
    findings: List[Finding] = []
    ext = os.path.splitext(profile_obj.path)[1].lower()
    implied = EXTENSION_FORMAT.get(ext)
    actual = profile_obj.container

    details = {"extension": ext or None, "implied": implied, "actual": actual}

    if implied and implied != actual:
        findings.append(
            Finding(
                code="format-mismatch",
                severity="error" if implied in ("woff", "woff2") else "warn",
                message=(
                    "%s is named %s but contains %s data; a browser will reject it"
                    % (profile_obj.path, ext, actual)
                ),
                detail=details,
                scope="format",
            )
        )

    if declared:
        normalized = declared.lower().replace("font/", "").replace("application/", "")
        if normalized not in (actual, "sfnt") and not (
            normalized in ("ttf", "truetype") and actual == "truetype"
        ):
            findings.append(
                Finding(
                    code="format-mismatch",
                    severity="error",
                    message="declared format %s does not match content %s"
                    % (declared, actual),
                    detail=dict(details, declared=declared),
                    scope="format",
                )
            )

    status = "fail" if any(f.severity == "error" for f in findings) else (
        "warn" if findings else "pass"
    )
    return (
        Check(
            name="format",
            status=status,
            summary="%s container%s"
            % (actual, "" if ext else ", no extension"),
            details=details,
        ),
        findings,
    )


def check_tables(
    profile_obj: FontProfile, source: Optional[FontProfile] = None
) -> Tuple[Check, List[Finding]]:
    findings: List[Finding] = []
    if profile_obj.missing_required:
        findings.append(
            Finding(
                code="table-missing",
                severity="error",
                message="missing required tables: %s"
                % ", ".join(profile_obj.missing_required),
                detail={"path": profile_obj.path, "tables": profile_obj.missing_required},
                scope="tables",
            )
        )
    # An optional table that the source never had is not a regression; only a
    # table that existed and then disappeared is worth a warning.
    lost = [
        tag
        for tag in profile_obj.missing_rendering
        if source is None or tag in source.tables
    ]
    if lost and source is not None:
        findings.append(
            Finding(
                code="table-missing",
                severity="warn",
                message="these tables existed in the source but not in the subset: %s"
                % ", ".join(lost),
                detail={"path": profile_obj.path, "tables": lost},
                scope="tables",
            )
        )
    status = "fail" if profile_obj.missing_required else ("warn" if findings else "pass")
    return (
        Check(
            name="tables",
            status=status,
            summary="%d tables%s"
            % (
                len(profile_obj.tables),
                ", %d dropped from source" % len(lost) if lost else "",
            ),
            details={
                "tables": profile_obj.tables,
                "missing_required": profile_obj.missing_required,
                "dropped_optional": lost,
            },
        ),
        findings,
    )


def check_identity(source: FontProfile, subset: FontProfile) -> Tuple[Check, List[Finding]]:
    """Subsetting must not rename the face: CSS keeps referencing the old name."""
    diffs = {}
    if source.family and subset.family and source.family != subset.family:
        diffs["family"] = [source.family, subset.family]
    if source.weight_class != subset.weight_class:
        diffs["weight_class"] = [source.weight_class, subset.weight_class]
    if source.outline_kind != subset.outline_kind and subset.outline_kind != "unknown":
        diffs["outline_kind"] = [source.outline_kind, subset.outline_kind]

    findings: List[Finding] = []
    if diffs:
        findings.append(
            Finding(
                code="identity-drift",
                severity="error" if "family" in diffs else "warn",
                message="identity changed during subsetting: %s" % ", ".join(sorted(diffs)),
                detail=diffs,
                scope="identity",
            )
        )
    return (
        Check(
            name="identity",
            status="fail" if "family" in diffs else ("warn" if diffs else "pass"),
            summary="%s %d" % (subset.family or "(no family)", subset.weight_class),
            details=diffs or {"family": source.family, "weight_class": source.weight_class},
        ),
        findings,
    )


def check_coverage(
    subset_profile: FontProfile,
    expected: Optional[Set[int]],
) -> Tuple[Check, List[Finding], int]:
    if expected is None:
        return (
            Check(
                name="coverage",
                status="skip",
                summary="no expectation set supplied",
                details={"reason": "--expect/--expect-file not given"},
            ),
            [],
            0,
        )

    font = open_font(subset_profile.path, subset_profile.index)
    try:
        cmap = unicode_cmap(font)
        missing = sorted(cp for cp in expected if cp not in cmap)
        empty = []
        for cp in sorted(expected):
            name = cmap.get(cp)
            if name is None or is_blank_character(chr(cp)):
                continue
            if glyph_outline_status(font, name) == "empty":
                empty.append(cp)
        supplementary_missing = [cp for cp in missing if cp > 0xFFFF]
        can_supplementary = supports_supplementary(font)
    finally:
        try:
            font.close()
        except Exception:  # pragma: no cover
            pass

    findings: List[Finding] = []
    if missing:
        findings.append(
            Finding(
                code="coverage-missing",
                severity="error",
                message="%d of %d expected characters are absent from the subset"
                % (len(missing), len(expected)),
                detail={
                    "count": len(missing),
                    "chars": [
                        {"hex": "U+%04X" % cp, "char": chr(cp)} for cp in missing[:60]
                    ],
                },
                scope="coverage",
            )
        )
    if supplementary_missing and not can_supplementary:
        # Reported as information inside the same finding detail: the code
        # points are already listed as missing.  A separate error would blame
        # the cmap format even when the source font never had the glyph.
        findings[-1].detail["supplementary_missing"] = len(supplementary_missing)
        findings[-1].detail["cmap_supports_supplementary"] = False
    if empty:
        findings.append(
            Finding(
                code="coverage-empty",
                severity="error",
                message="%d expected characters map to empty glyphs" % len(empty),
                detail={
                    "chars": [
                        {"hex": "U+%04X" % cp, "char": chr(cp)} for cp in empty[:60]
                    ]
                },
                scope="coverage",
            )
        )

    covered = len(expected) - len(missing) - len(empty)
    status = "pass" if not findings else "fail"
    return (
        Check(
            name="coverage",
            status=status,
            summary="%d/%d expected characters present" % (covered, len(expected)),
            details={
                "expected": len(expected),
                "covered": covered,
                "missing": len(missing),
                "empty": len(empty),
            },
        ),
        findings,
        covered,
    )


def check_metrics(
    source: FontProfile,
    subset: FontProfile,
    expected: Optional[Set[int]],
    limit: int = 4000,
) -> Tuple[Check, List[Finding]]:
    """Advance widths and bounding boxes must survive the reduction.

    A changed advance width shifts every subsequent glyph on the line, which is
    the most visible way a "successful" subset breaks a layout.
    """
    src = open_font(source.path, source.index)
    sub = open_font(subset.path, subset.index)
    findings: List[Finding] = []
    try:
        src_cmap, sub_cmap = unicode_cmap(src), unicode_cmap(sub)
        candidates = (
            sorted(cp for cp in expected if cp in src_cmap and cp in sub_cmap)
            if expected is not None
            else sorted(set(src_cmap) & set(sub_cmap))[:limit]
        )
        candidates = candidates[:limit]
        src_hmtx, sub_hmtx = src["hmtx"], sub["hmtx"]
        advance_diffs: List[Dict[str, Any]] = []
        bounds_diffs: List[Dict[str, Any]] = []
        src_glyf, sub_glyf = src.get("glyf"), sub.get("glyf")

        for cp in candidates:
            src_name, sub_name = src_cmap[cp], sub_cmap[cp]
            try:
                src_adv = src_hmtx[src_name][0]
                sub_adv = sub_hmtx[sub_name][0]
            except KeyError:  # pragma: no cover - damaged hmtx
                continue
            if src_adv != sub_adv:
                advance_diffs.append(
                    {"hex": "U+%04X" % cp, "char": chr(cp), "source": src_adv, "subset": sub_adv}
                )
            if src_glyf is not None and sub_glyf is not None:
                try:
                    a, b = src_glyf[src_name], sub_glyf[sub_name]
                    box_a = (getattr(a, "xMin", 0), getattr(a, "yMin", 0), getattr(a, "xMax", 0), getattr(a, "yMax", 0))
                    box_b = (getattr(b, "xMin", 0), getattr(b, "yMin", 0), getattr(b, "xMax", 0), getattr(b, "yMax", 0))
                except KeyError:  # pragma: no cover
                    continue
                if box_a != box_b and a.numberOfContours != 0 and b.numberOfContours != 0:
                    bounds_diffs.append(
                        {
                            "hex": "U+%04X" % cp,
                            "char": chr(cp),
                            "source": list(box_a),
                            "subset": list(box_b),
                        }
                    )
    finally:
        for handle in (src, sub):
            try:
                handle.close()
            except Exception:  # pragma: no cover
                pass

    if advance_diffs:
        findings.append(
            Finding(
                code="metrics-advance",
                severity="error",
                message="%d characters changed advance width" % len(advance_diffs),
                detail={"samples": advance_diffs[:20], "count": len(advance_diffs)},
                scope="metrics",
            )
        )
    if bounds_diffs:
        findings.append(
            Finding(
                code="metrics-bounds",
                severity="error",
                message="%d glyphs changed bounding box" % len(bounds_diffs),
                detail={"samples": bounds_diffs[:20], "count": len(bounds_diffs)},
                scope="metrics",
            )
        )

    status = "fail" if findings else ("skip" if not candidates else "pass")
    return (
        Check(
            name="metrics",
            status=status,
            summary="%d glyphs compared, %d advance diffs, %d bounds diffs"
            % (len(candidates), len(advance_diffs), len(bounds_diffs)),
            details={
                "compared": len(candidates),
                "advance_diffs": len(advance_diffs),
                "bounds_diffs": len(bounds_diffs),
            },
        ),
        findings,
    )


def _glyph_references(font) -> List[Tuple[str, str]]:
    """Best-effort inventory of glyph names referenced by layout tables.

    OpenType lookup types are numerous; this walks the attributes that carry
    glyph names in the common substitution/positioning subtable shapes and
    reports how many references it actually saw, so a thin result is visible
    rather than silently treated as "all clear".
    """
    refs: List[Tuple[str, str]] = []
    order = set(font.getGlyphOrder())
    for tag in ("GSUB", "GPOS"):
        if tag not in font:
            continue
        try:
            table = font[tag].table
            lookups = table.LookupList.Lookup
        except Exception:  # pragma: no cover - broken layout table
            continue
        for lookup in lookups:
            for subtable in getattr(lookup, "SubTable", []):
                for attr in ("mapping", "alternates", "ligatures"):
                    value = getattr(subtable, attr, None)
                    if isinstance(value, dict):
                        for key, target in value.items():
                            refs.append((tag, key))
                            if isinstance(target, list):
                                refs.extend((tag, item) for item in target)
                            else:
                                refs.append((tag, target))
                coverage = getattr(subtable, "Coverage", None)
                if coverage is not None:
                    refs.extend((tag, name) for name in coverage.glyphs)
    return [(tag, name) for tag, name in refs if name]


def _subtable_coverage(subtable) -> Set[str]:
    """Glyph names a subtable actually applies to, including extension wrappers.

    Compiled subtables expose ``Coverage``; freshly parsed ones often only carry
    ``mapping``/``alternates``/``ligatures`` dictionaries, with coverage
    synthesised at compile time.  Both shapes have to be read.
    """
    covered: Set[str] = set()
    coverage = getattr(subtable, "Coverage", None)
    if coverage is not None:
        covered.update(getattr(coverage, "glyphs", []) or [])
    for attr in ("mapping", "alternates", "ligatures"):
        value = getattr(subtable, attr, None)
        if isinstance(value, dict):
            covered.update(value.keys())
    inner = getattr(subtable, "ExtSubTable", None)
    if inner is not None:
        covered |= _subtable_coverage(inner)
    return covered


def _dangling_references(font, refs: Optional[List[Tuple[str, str]]] = None) -> List[Tuple[str, str]]:
    if refs is None:
        refs = _glyph_references(font)
    order = set(font.getGlyphOrder())
    return [(tag, name) for tag, name in refs if name not in order]


def _feature_covers(font, feature_tag: str, target_glyphs: Set[str]) -> bool:
    """Does this feature touch any of the glyphs we ship?

    Dropping ``ccmp`` from a subset is correct when none of the retained
    characters can trigger it.  Only a feature that *would* have applied is a
    real loss, and this is what separates the two cases.
    """
    if not target_glyphs:
        return False
    for table_tag in ("GSUB", "GPOS"):
        if table_tag not in font:
            continue
        try:
            table = font[table_tag].table
            records = table.FeatureList.FeatureRecord
            lookups = table.LookupList.Lookup
        except Exception:  # pragma: no cover - damaged layout table
            continue
        for record in records:
            if record.FeatureTag != feature_tag:
                continue
            for index in getattr(record.Feature, "LookupListIndex", []):
                try:
                    lookup = lookups[index]
                except (IndexError, TypeError):  # pragma: no cover
                    continue
                for subtable in getattr(lookup, "SubTable", []):
                    if _subtable_coverage(subtable) & target_glyphs:
                        return True
    return False


def check_layout(
    source: FontProfile,
    subset: FontProfile,
    expected: Optional[Set[int]] = None,
) -> Tuple[Check, List[Finding]]:
    findings: List[Finding] = []
    src = open_font(source.path, source.index)
    sub = open_font(subset.path, subset.index)
    try:
        refs = _glyph_references(sub)
        dangling = _dangling_references(sub, refs)
        src_features = _feature_tags(src)
        sub_features = _feature_tags(sub)
        lost = sorted(
            tag
            for tag in CRITICAL_FEATURES
            if tag in src_features and tag not in sub_features
        )
        src_cmap = unicode_cmap(src)
        if expected:
            target_glyphs = {src_cmap[cp] for cp in expected if cp in src_cmap}
        else:
            target_glyphs = set(src_cmap.values())
        meaningful = [tag for tag in lost if _feature_covers(src, tag, target_glyphs)]
        benign = [tag for tag in lost if tag not in meaningful]
    finally:
        for handle in (src, sub):
            try:
                handle.close()
            except Exception:  # pragma: no cover
                pass

    if dangling:
        findings.append(
            Finding(
                code="layout-dangling",
                severity="error",
                message="%d layout references point at glyphs absent from the font"
                % len(dangling),
                detail={"samples": [{"table": t, "glyph": n} for t, n in dangling[:20]]},
                scope="layout",
            )
        )
    if meaningful:
        findings.append(
            Finding(
                code="layout-feature-lost",
                severity="error",
                message="features that apply to the shipped glyphs were dropped: %s"
                % ", ".join(meaningful),
                detail={
                    "features": meaningful,
                    "target_glyphs": len(target_glyphs),
                    "source_features": sorted(src_features),
                    "subset_features": sorted(sub_features),
                },
                scope="layout",
            )
        )

    status = "fail" if findings else ("skip" if not refs and not src_features else "pass")
    return (
        Check(
            name="layout",
            status=status,
            summary="%d glyph references, %d features kept, %d dropped (%d relevant)"
            % (len(refs), len(sub_features), len(lost), len(meaningful)),
            details={
                "references_checked": len(refs),
                "dangling": len(dangling),
                "dropped_irrelevant": benign,
                "critical_lost": meaningful,
                "source_features": sorted(src_features),
                "subset_features": sorted(sub_features),
            },
        ),
        findings,
    )


def _feature_tags(font) -> Set[str]:
    tags: Set[str] = set()
    for table_tag in ("GSUB", "GPOS"):
        if table_tag not in font:
            continue
        try:
            for record in font[table_tag].table.FeatureList.FeatureRecord:
                tags.add(record.FeatureTag)
        except Exception:  # pragma: no cover
            continue
    return tags


def check_budget(subset: FontProfile, budget: Optional[int]) -> Tuple[Check, List[Finding]]:
    if budget is None:
        return (
            Check(
                name="budget",
                status="skip",
                summary="no budget configured",
                details={"subset_bytes": subset.file_size},
            ),
            [],
        )
    findings: List[Finding] = []
    if subset.file_size > budget:
        findings.append(
            Finding(
                code="budget-exceeded",
                severity="error",
                message="%s is %d bytes, budget is %d"
                % (subset.path, subset.file_size, budget),
                detail={"bytes": subset.file_size, "budget": budget},
                scope="budget",
            )
        )
    return (
        Check(
            name="budget",
            status="fail" if findings else "pass",
            summary="%d / %d bytes" % (subset.file_size, budget),
            details={"bytes": subset.file_size, "budget": budget},
        ),
        findings,
    )


def verify(
    source_spec: Tuple[str, int],
    subset_spec: Tuple[str, int],
    expect_text: Optional[str] = None,
    budget: Optional[int] = None,
    declared_format: Optional[str] = None,
) -> VerifyResult:
    source = build_profile(*source_spec)
    subset = build_profile(*subset_spec)
    expected = _expected_codepoints(expect_text)

    checks: List[Check] = []
    findings: List[Finding] = []

    for check, found in (
        check_format(subset, declared_format),
        check_tables(subset, source),
        check_identity(source, subset),
    ):
        checks.append(check)
        findings.extend(found)

    coverage_check, coverage_findings, covered = check_coverage(subset, expected)
    checks.append(coverage_check)
    findings.extend(coverage_findings)

    for check, found in (
        check_metrics(source, subset, expected),
        check_layout(source, subset, expected),
        check_budget(subset, budget),
    ):
        checks.append(check)
        findings.extend(found)

    return VerifyResult(
        source=source,
        subset=subset,
        checks=checks,
        findings=findings,
        expected=len(expected or ()),
        covered=covered,
    )
