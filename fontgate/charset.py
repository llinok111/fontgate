"""Collect the characters a project actually renders.

The point of this module is provenance: when a character turns out to be
missing from a font, the report must be able to say *which file* introduced it
and *how often* it appears, otherwise the fix is guesswork.
"""

from __future__ import annotations

import html
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .errors import TextReadError

DEFAULT_EXTENSIONS: Tuple[str, ...] = (
    ".txt",
    ".md",
    ".markdown",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".js",
    ".mjs",
    ".cjs",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".svelte",
    ".json",
    ".xml",
    ".svg",
    ".yml",
    ".yaml",
    ".py",
    ".rb",
    ".php",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
)

MARKUP_EXTENSIONS = frozenset({".html", ".htm", ".svg", ".xml", ".vue", ".svelte"})

SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".workbuddy",
        "node_modules",
        "bower_components",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "dist",
        "build",
        "target",
        "coverage",
        ".next",
        ".nuxt",
        ".cache",
    }
)

_TAG_RE = re.compile(r"<[^>]*>")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

#: `Cc` code points are addressable but never rendered; counting them would
#: drown the report in newlines and would ask the font for glyphs it never needs.
SKIP_CATEGORIES = frozenset({"Cc", "Cs", "Co", "Cn"})


@dataclass
class FileHit:
    path: str
    count: int
    first_line: int

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "count": self.count, "first_line": self.first_line}


@dataclass
class CharUse:
    codepoint: int
    char: str
    category: str
    count: int = 0
    files: List[FileHit] = field(default_factory=list)

    @property
    def label(self) -> str:
        cp = self.codepoint
        if cp < 0x20 or cp == 0x7F:
            return "U+%04X" % cp
        return self.char

    def to_dict(self, max_files: int = 5) -> Dict[str, Any]:
        return {
            "codepoint": self.codepoint,
            "hex": "U+%04X" % self.codepoint,
            "char": self.char,
            "category": self.category,
            "count": self.count,
            "files": [hit.to_dict() for hit in self.files[:max_files]],
            "file_count": len(self.files),
        }


@dataclass
class CharsetReport:
    roots: List[str]
    extensions: List[str]
    files_scanned: int
    files_skipped: int
    bytes_scanned: int
    total_occurrences: int
    chars: Dict[int, CharUse]
    warnings: List[str] = field(default_factory=list)
    encoding_histogram: Dict[str, int] = field(default_factory=dict)

    @property
    def codepoints(self) -> List[int]:
        return sorted(self.chars)

    def text(self) -> str:
        """The collected characters, sorted by code point (stable for hashing)."""
        return "".join(self.chars[cp].char for cp in self.codepoints)

    def to_dict(self, max_chars: Optional[int] = None) -> Dict[str, Any]:
        ordered = sorted(
            self.chars.values(), key=lambda c: (-c.count, c.codepoint)
        )
        if max_chars is not None:
            ordered = ordered[:max_chars]
        return {
            "roots": self.roots,
            "extensions": self.extensions,
            "files_scanned": self.files_scanned,
            "files_skipped": self.files_skipped,
            "bytes_scanned": self.bytes_scanned,
            "total_occurrences": self.total_occurrences,
            "unique_codepoints": len(self.chars),
            "encoding_histogram": self.encoding_histogram,
            "chars": [c.to_dict() for c in ordered],
            "warnings": self.warnings,
        }


def _decode(raw: bytes) -> Tuple[str, str]:
    """Decode bytes, reporting the encoding actually used.

    ``gb18030`` is tried last because it accepts almost any byte sequence; a
    silent win there would hide a mis-encoded UTF-8 file.
    """
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding), "utf-8"
        except UnicodeDecodeError:
            continue
    try:
        return raw.decode("gb18030"), "gb18030"
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace"), "utf-8-lossy"


def strip_markup(text: str) -> str:
    """Remove tags and comments but keep every character that renders.

    A regex stripper is used on purpose: pulling in an HTML parser would add a
    dependency and would still not improve the only thing that matters here,
    which is "will this character end up on screen".
    """
    text = _COMMENT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return html.unescape(text)


def to_ranges(codepoints: Iterable[int]) -> List[Tuple[int, int]]:
    """Collapse sorted code points into inclusive ranges."""
    ranges: List[Tuple[int, int]] = []
    start = previous = None
    for cp in sorted(codepoints):
        if start is None:
            start = previous = cp
        elif cp == previous + 1:
            previous = cp
        else:
            ranges.append((start, previous))
            start = previous = cp
    if start is not None:
        ranges.append((start, previous))
    return ranges


def format_unicode_range(codepoints: Iterable[int]) -> str:
    """CSS ``unicode-range`` value for a code point set."""
    parts = []
    for low, high in to_ranges(codepoints):
        if low == high:
            parts.append("U+%04X" % low)
        else:
            parts.append("U+%04X-%04X" % (low, high))
    return ", ".join(parts)


def _iter_files(root: str, extensions: Sequence[str]):
    if os.path.isfile(root):
        yield root
        return
    if not os.path.isdir(root):
        raise TextReadError("no such file or directory: %s" % root)
    extensions = tuple(e.lower() for e in extensions)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRECTORIES)
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() in extensions:
                yield os.path.join(dirpath, name)


def collect(
    roots: Iterable[str],
    extensions: Optional[Sequence[str]] = None,
    max_chars: Optional[int] = None,
) -> CharsetReport:
    """Walk *roots* and tally every renderable character."""
    roots = [os.path.normpath(r) for r in roots]
    if not roots:
        raise TextReadError("no text inputs given")
    extensions = list(extensions or DEFAULT_EXTENSIONS)

    chars: Dict[int, CharUse] = {}
    warnings: List[str] = []
    encoding_histogram: Dict[str, int] = {}
    files_scanned = 0
    files_skipped = 0
    bytes_scanned = 0
    total = 0

    for root in roots:
        for path in _iter_files(root, extensions):
            try:
                with open(path, "rb") as handle:
                    raw = handle.read()
            except OSError as exc:
                warnings.append("skip %s: %s" % (path, exc))
                files_skipped += 1
                continue
            if b"\x00" in raw[:4096]:
                files_skipped += 1
                continue

            text, encoding = _decode(raw)
            encoding_histogram[encoding] = encoding_histogram.get(encoding, 0) + 1
            if encoding == "utf-8-lossy":
                warnings.append("undecodable bytes replaced in %s" % path)
            if os.path.splitext(path)[1].lower() in MARKUP_EXTENSIONS:
                text = strip_markup(text)

            files_scanned += 1
            bytes_scanned += len(raw)

            per_file: Dict[int, int] = {}
            first_line: Dict[int, int] = {}
            for lineno, line in enumerate(text.splitlines(), 1):
                for ch in line:
                    if unicodedata.category(ch) in SKIP_CATEGORIES:
                        continue
                    cp = ord(ch)
                    per_file[cp] = per_file.get(cp, 0) + 1
                    first_line.setdefault(cp, lineno)

            for cp, count in per_file.items():
                if count == 0:
                    continue
                entry = chars.get(cp)
                if entry is None:
                    ch = chr(cp)
                    entry = CharUse(
                        codepoint=cp,
                        char=ch,
                        category=unicodedata.category(ch),
                    )
                    chars[cp] = entry
                entry.count += count
                total += count
                entry.files.append(
                    FileHit(path=path, count=count, first_line=first_line.get(cp, 1))
                )

    for entry in chars.values():
        entry.files.sort(key=lambda hit: (-hit.count, hit.path))

    report = CharsetReport(
        roots=roots,
        extensions=extensions,
        files_scanned=files_scanned,
        files_skipped=files_skipped,
        bytes_scanned=bytes_scanned,
        total_occurrences=total,
        chars=chars,
        warnings=warnings,
        encoding_histogram=encoding_histogram,
    )
    if max_chars is not None and len(report.chars) > max_chars:
        keep = sorted(report.chars.values(), key=lambda c: (-c.count, c.codepoint))[
            :max_chars
        ]
        report.chars = {c.codepoint: c for c in keep}
        report.warnings.append(
            "character set capped at %d entries (%d seen)" % (max_chars, len(keep))
        )
    return report
