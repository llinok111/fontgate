"""Reading font binaries and turning them into comparable facts.

Everything downstream (audit, verify, family) consumes :class:`FontProfile`
and plain dictionaries produced here.  No module below this one touches a
binary directly, which keeps the checks testable against synthetic fonts.
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .errors import InputError, MissingDependencyError, UnsupportedFormatError

#: Container formats recognised by magic bytes.  Extensions are never trusted;
#: ``.woff2`` files that contain bare sfnt data are a real-world failure mode.
MAGIC: Dict[bytes, str] = {
    b"wOFF": "woff",
    b"wOF2": "woff2",
    b"\x00\x01\x00\x00": "truetype",
    b"true": "truetype",
    b"OTTO": "opentype",
    b"ttcf": "collection",
    b"typ1": "opentype",
}

#: Tables without which a browser or shaping engine will reject the file.
REQUIRED_TABLES: Tuple[str, ...] = (
    "cmap",
    "head",
    "hhea",
    "hmtx",
    "maxp",
    "name",
)

#: Optional tables whose absence changes rendering rather than validity.
RENDERING_TABLES: Tuple[str, ...] = ("OS/2", "post", "GDEF", "GSUB", "GPOS", "kern")

WHITESPACE_CATEGORIES = frozenset({"Zs", "Zl", "Zp", "Cc"})


def sniff_format(path: str) -> str:
    """Return the container format implied by the first four bytes."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(4)
    except OSError as exc:  # pragma: no cover - exercised via CLI
        raise InputError("cannot read %s: %s" % (path, exc)) from exc
    if len(head) < 4:
        raise UnsupportedFormatError("%s is shorter than an sfnt header" % path)
    return MAGIC.get(head, "unknown")


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_fonttools():
    try:
        from fontTools.ttLib import TTFont  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise MissingDependencyError(
            "fontTools is required; install it with: pip install fonttools"
        ) from exc
    return True


def _validate_directory(path: str) -> None:
    """Reject files whose table directory cannot fit in the file.

    ``fontTools`` opens a reader before it parses, so a truncated file leaves a
    handle behind when it raises.  Checking the header first keeps that path
    clean and turns the failure into a typed error.
    """
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        head = handle.read(12)
        if len(head) < 12:
            raise UnsupportedFormatError("%s is shorter than an sfnt header" % path)
        if head[:4] == b"ttcf":
            count = struct.unpack(">I", head[8:12])[0]
            if count == 0:
                raise UnsupportedFormatError("%s declares zero faces" % path)
            if 12 + count * 4 > size:
                raise UnsupportedFormatError(
                    "%s declares %d faces but the header does not fit" % (path, count)
                )
            return
        num_tables = struct.unpack(">H", head[4:6])[0]
        if num_tables == 0:
            raise UnsupportedFormatError("%s declares zero tables" % path)
        if 12 + num_tables * 16 > size:
            raise UnsupportedFormatError(
                "%s declares %d tables (%d bytes needed) but is only %d bytes"
                % (path, num_tables, 12 + num_tables * 16, size)
            )


def open_font(path: str, index: int = 0):
    """Open a single face, lazily.

    ``index`` selects a face inside a TrueType/OpenType collection.  Passing a
    non-zero index for a non-collection is an error rather than a silent
    fallback: picking the wrong face out of a ``.ttc`` is otherwise invisible.
    """
    _require_fonttools()
    from fontTools.ttLib import TTFont

    fmt = sniff_format(path)
    if fmt == "unknown":
        raise UnsupportedFormatError(
            "%s is not a recognised font container (magic bytes %r)"
            % (path, _peek(path))
        )
    _validate_directory(path)
    if fmt == "collection":
        if index < 0:
            raise InputError("font index must be >= 0, got %d" % index)
    elif index not in (0, None):
        raise InputError(
            "%s is a single-face %s file; index %d is out of range"
            % (path, fmt, index)
        )

    try:
        if fmt == "collection":
            return TTFont(path, fontNumber=index, lazy=True)
        return TTFont(path, lazy=True)
    except IndexError as exc:
        raise InputError("%s has no face at index %d" % (path, index)) from exc
    except Exception as exc:
        raise InputError("cannot parse %s: %s" % (path, exc)) from exc


def _peek(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read(4)
    except OSError:  # pragma: no cover
        return b""


def collection_size(path: str) -> int:
    """Number of faces in a collection, or 1 for a single-face file."""
    if sniff_format(path) != "collection":
        return 1
    with open(path, "rb") as handle:
        header = handle.read(12)
    if len(header) < 12:
        raise UnsupportedFormatError("%s has a truncated TTC header" % path)
    return struct.unpack(">I", header[8:12])[0]


def unicode_cmap(font) -> Dict[int, str]:
    """Union of every Unicode cmap subtable.

    ``font.getBestCmap()`` returns one subtable; this returns all of them, so a
    character exposed only through a legacy subtable still counts as present.
    """
    out: Dict[int, str] = {}
    cmap = font.get("cmap")
    if cmap is None:
        return out
    for table in cmap.tables:
        try:
            is_unicode = table.isUnicode()
        except AttributeError:  # pragma: no cover - very old fontTools
            is_unicode = table.platformID == 0 or (
                table.platformID == 3 and table.platEncID in (1, 10)
            )
        if is_unicode:
            out.update(table.cmap)
    return out


def cmap_subtables(font) -> List[Dict[str, Any]]:
    """Describe each cmap subtable in a serialisable way."""
    cmap = font.get("cmap")
    if cmap is None:
        return []
    rows = []
    for table in cmap.tables:
        rows.append(
            {
                "platform": table.platformID,
                "encoding": table.platEncID,
                "format": table.format,
                "mappings": len(table.cmap),
                "supplementary": sum(1 for cp in table.cmap if cp > 0xFFFF),
                "unicode": bool(table.isUnicode()) if hasattr(table, "isUnicode") else False,
            }
        )
    return sorted(rows, key=lambda r: (r["platform"], r["encoding"], r["format"]))


def supports_supplementary(font) -> bool:
    """True when some subtable can address code points above U+FFFF."""
    return any(
        row["supplementary"] > 0 or row["format"] in (12, 13)
        for row in cmap_subtables(font)
    )


def cmap_digest(cmap: Dict[int, str]) -> str:
    """Order-independent fingerprint of a code point set.

    Only the code points take part: glyph names differ between faces of the
    same family and are not part of "does this face cover the same text".
    """
    digest = hashlib.sha256()
    for cp in sorted(cmap):
        digest.update(cp.to_bytes(4, "big"))
    return digest.hexdigest()


def _glyph_set(font):
    cached = getattr(font, "_fontgate_glyphset", None)
    if cached is None:
        cached = font.getGlyphSet()
        setattr(font, "_fontgate_glyphset", cached)
    return cached


def glyph_outline_status(font, glyph_name: str) -> str:
    """Classify a glyph as ``outline``, ``empty`` or ``composite``.

    An empty glyph still occupies a cmap slot: the character "exists" for the
    shaper but draws nothing.  This is the failure a coverage check that only
    looks at cmap cannot see.
    """
    glyf = font.get("glyf")
    if glyf is not None:
        try:
            glyph = glyf[glyph_name]
        except KeyError:
            return "missing"
        if glyph.numberOfContours == 0:
            return "empty"
        if glyph.numberOfContours > 0:
            return "outline"
        return "composite"

    if font.get("CFF ") is not None or font.get("CFF2") is not None:
        from fontTools.pens.recordingPen import RecordingPen

        pen = RecordingPen()
        try:
            _glyph_set(font)[glyph_name].draw(pen)
        except KeyError:
            return "missing"
        return "outline" if pen.value else "empty"

    return "unknown"


def outlines_for(font, codepoints: Iterable[int], cmap: Optional[Dict[int, str]] = None):
    """Map each code point to its outline status, walking cmap once."""
    cmap = cmap if cmap is not None else unicode_cmap(font)
    out: Dict[int, str] = {}
    for cp in codepoints:
        name = cmap.get(cp)
        out[cp] = "missing" if name is None else glyph_outline_status(font, name)
    return out


#: Variation selectors are addressed through cmap format 14 (UVS), not through
#: the ordinary code point map.  Treating them as "characters that need a
#: glyph" produces a false positive on every emoji sequence.
VARIATION_SELECTORS = frozenset(
    list(range(0xFE00, 0xFE10))
    + list(range(0xE0100, 0xE01F0))
    + list(range(0x180B, 0x180E))
)


def is_variation_selector(cp: int) -> bool:
    return cp in VARIATION_SELECTORS


def is_blank_character(char: str) -> bool:
    """Characters that legitimately have no outline of their own."""
    import unicodedata

    if char in "\u200b\u200c\u200d\ufeff":
        return True
    if is_variation_selector(ord(char)):
        return True
    return unicodedata.category(char) in WHITESPACE_CATEGORIES


@dataclass
class FontProfile:
    """Everything fontgate knows about one face, without loading glyph data."""

    path: str
    index: int
    container: str
    face_format: str
    family: str
    subfamily: str
    full_name: str
    postscript_name: str
    weight_class: int
    width_class: int
    glyph_count: int
    cmap_count: int
    cmap_digest: str
    outline_kind: str
    is_variable: bool
    variable_axes: List[Dict[str, Any]]
    tables: List[str]
    missing_required: List[str]
    missing_rendering: List[str]
    supports_supplementary: bool
    cmap_subtables: List[Dict[str, Any]]
    file_size: int
    sha256: str
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "index": self.index,
            "container": self.container,
            "face_format": self.face_format,
            "family": self.family,
            "subfamily": self.subfamily,
            "full_name": self.full_name,
            "postscript_name": self.postscript_name,
            "weight_class": self.weight_class,
            "width_class": self.width_class,
            "glyph_count": self.glyph_count,
            "cmap_count": self.cmap_count,
            "cmap_digest": self.cmap_digest,
            "outline_kind": self.outline_kind,
            "is_variable": self.is_variable,
            "variable_axes": self.variable_axes,
            "tables": self.tables,
            "missing_required": self.missing_required,
            "missing_rendering": self.missing_rendering,
            "supports_supplementary": self.supports_supplementary,
            "cmap_subtables": self.cmap_subtables,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "warnings": self.warnings,
        }

    @property
    def label(self) -> str:
        """Human-facing identity used in reports and error messages."""
        if self.subfamily and self.subfamily.lower() not in ("regular", "normal"):
            return "%s %s" % (self.family, self.subfamily)
        return self.family or os.path.basename(self.path)


def _name(font, name_id: int) -> str:
    try:
        value = font["name"].getDebugName(name_id)
    except Exception:  # pragma: no cover - damaged name tables
        return ""
    return value or ""


def _outline_kind(font) -> str:
    if font.get("glyf") is not None:
        return "truetype-glyf"
    if font.get("CFF2") is not None:
        return "cff2"
    if font.get("CFF ") is not None:
        return "cff"
    return "unknown"


def profile(path: str, index: int = 0) -> FontProfile:
    """Build a profile.  Raises :class:`InputError` for unreadable files."""
    container = sniff_format(path)
    if container == "unknown":
        raise UnsupportedFormatError(
            "%s is not a font: first bytes are %r" % (path, _peek(path))
        )
    font = open_font(path, index)
    warnings: List[str] = []
    try:
        tables = sorted(font.keys())
        if not tables:
            raise InputError(
                "%s parses to an empty table directory; it is not a usable font"
                % path
            )
        missing_required = [t for t in REQUIRED_TABLES if t not in tables]
        missing_rendering = [t for t in RENDERING_TABLES if t not in tables]
        if missing_required:
            warnings.append(
                "missing required tables: %s" % ", ".join(missing_required)
            )
        cmap = unicode_cmap(font)
        if not cmap:
            warnings.append("no Unicode cmap subtable found")

        axes: List[Dict[str, Any]] = []
        if "fvar" in tables:
            for axis in font["fvar"].axes:
                axes.append(
                    {
                        "tag": axis.axisTag,
                        "min": axis.minValue,
                        "default": axis.defaultValue,
                        "max": axis.maxValue,
                    }
                )

        weight = 400
        width = 5
        if "OS/2" in tables:
            try:
                weight = int(font["OS/2"].usWeightClass)
                width = int(font["OS/2"].usWidthClass)
            except Exception:  # pragma: no cover
                warnings.append("OS/2 present but weight/width unreadable")

        face_format = container
        if container == "collection":
            # The container is a collection; the face inside keeps its own
            # outline flavour, which is what a browser cares about.
            face_format = _outline_kind(font)

        profile_obj = FontProfile(
            path=path,
            index=index,
            container=container,
            face_format=face_format,
            family=_name(font, 1) or _name(font, 16),
            subfamily=_name(font, 2) or _name(font, 17),
            full_name=_name(font, 4),
            postscript_name=_name(font, 6),
            weight_class=weight,
            width_class=width,
            glyph_count=len(font.getGlyphOrder()),
            cmap_count=len(cmap),
            cmap_digest=cmap_digest(cmap),
            outline_kind=_outline_kind(font),
            is_variable="fvar" in tables,
            variable_axes=axes,
            tables=tables,
            missing_required=missing_required,
            missing_rendering=missing_rendering,
            supports_supplementary=supports_supplementary(font),
            cmap_subtables=cmap_subtables(font),
            file_size=os.path.getsize(path),
            sha256=sha256_file(path),
            warnings=warnings,
        )
    finally:
        try:
            font.close()
        except Exception:  # pragma: no cover
            pass
    return profile_obj


def face_signature(profile_obj: FontProfile) -> Tuple[str, int]:
    """Stable (family, weight) key used for cross-weight comparisons."""
    return (profile_obj.family.lower().strip(), profile_obj.weight_class)
