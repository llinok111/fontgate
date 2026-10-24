"""Command line interface.

Exit codes follow :mod:`fontgate.errors`; the contract is exercised in
``tests/test_cli.py`` because CI depends on it.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import __version__
from .audit import audit, family_consistency
from .charset import collect, format_unicode_range, to_ranges
from .errors import (
    EXIT_BUDGET,
    EXIT_FINDINGS,
    EXIT_INPUT,
    EXIT_OK,
    EXIT_USAGE,
    FontgateError,
    UsageError,
)
from .fontfile import collection_size, open_font, profile as build_profile
from .models import dumps, has_errors
from .report import render_audit, render_verify
from .verify import verify as run_verify

UNITS = {"b": 1, "kb": 1024, "kib": 1024, "mb": 1024 ** 2, "mib": 1024 ** 2}


def parse_size(text: str) -> int:
    raw = text.strip().lower().replace("_", "")
    for suffix in ("kib", "mib", "kb", "mb", "b"):
        if raw.endswith(suffix):
            number = raw[: -len(suffix)].strip()
            try:
                return int(float(number) * UNITS[suffix])
            except ValueError:
                raise UsageError("cannot read size %r" % text)
    try:
        return int(raw)
    except ValueError:
        raise UsageError("cannot read size %r; use e.g. 120kb or 200000" % text)


def parse_font_spec(text: str) -> Tuple[str, int]:
    """Accept ``path`` or ``path@INDEX`` for collections."""
    if "@" in text:
        head, _, tail = text.rpartition("@")
        if tail.isdigit():
            return head, int(tail)
    return text, 0


class Palette:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return "\033[%sm%s\033[0m" % (code, text)

    def ok(self, text: str) -> str:
        return self._wrap("32", text)

    def warn(self, text: str) -> str:
        return self._wrap("33", text)

    def fail(self, text: str) -> str:
        return self._wrap("31", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def status(self, status: str, text: str) -> str:
        return {"pass": self.ok, "fail": self.fail, "warn": self.warn}.get(
            status, self.dim
        )(text)


def _use_color(args) -> bool:
    if getattr(args, "no_color", False):
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _write(path: Optional[str], content: str) -> None:
    if not path:
        return
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def _fmt(value: int) -> str:
    return "{:,}".format(value)


def cmd_inspect(args) -> int:
    palette = Palette(_use_color(args))
    rows = []
    for spec in args.font:
        path, index = parse_font_spec(spec)
        faces = collection_size(path) if index == 0 else None
        item = build_profile(path, index)
        rows.append(item)
        print(palette.bold(item.full_name or item.family))
        print("  %-16s %s" % ("file", item.path))
        print("  %-16s %s (face %d)" % ("container", item.container, item.index))
        if faces and faces > 1:
            print("  %-16s %d faces; use path@N to select" % ("collection", faces))
        print("  %-16s %s (weight %d)" % ("family", item.family or "(none)", item.weight_class))
        print("  %-16s %s" % ("outlines", item.outline_kind))
        print("  %-16s %s glyphs, %s code points" % ("size", _fmt(item.glyph_count), _fmt(item.cmap_count)))
        print("  %-16s %s" % ("file size", "%d bytes" % item.file_size))
        print("  %-16s %s" % ("sha256", item.sha256[:32] + "…"))
        print("  %-16s %s" % ("variable", "yes" if item.is_variable else "no"))
        if item.variable_axes:
            print("  %-16s %s" % ("axes", ", ".join(a["tag"] for a in item.variable_axes)))
        print("  %-16s %s" % ("supplementary", "yes" if item.supports_supplementary else "no"))
        print("  %-16s %s" % ("cmap subtables", ", ".join(
            "%d/%d f%d" % (s["platform"], s["encoding"], s["format"]) for s in item.cmap_subtables
        ) or "none"))
        if item.missing_required:
            print("  %-16s %s" % ("MISSING", palette.fail(", ".join(item.missing_required))))
        for warning in item.warnings:
            print("  %-16s %s" % ("warning", palette.warn(warning)))
        print()

    if args.json:
        _write(args.json, dumps([item.to_dict() for item in rows], indent=2) + "\n")
        print(palette.dim("json → %s" % args.json))
    return EXIT_OK


def _collect_charset(args):
    if getattr(args, "expect", None):
        # Direct string mode: build a synthetic one-entry char set so audit and
        # verify share the same downstream shape.
        from .charset import CharUse, CharsetReport

        chars = {}
        for ch in args.expect:
            cp = ord(ch)
            if cp in chars:
                chars[cp].count += 1
            else:
                chars[cp] = CharUse(
                    codepoint=cp, char=ch, category=__import__("unicodedata").category(ch), count=1
                )
        return CharsetReport(
            roots=["<argument>"],
            extensions=[],
            files_scanned=1,
            files_skipped=0,
            bytes_scanned=len(args.expect.encode("utf-8")),
            total_occurrences=len(args.expect),
            chars=chars,
        )
    extensions = args.ext or None
    return collect(args.text or [], extensions=extensions)


def cmd_charset(args) -> int:
    """Emit the collected characters in the form the next tool expects."""
    palette = Palette(_use_color(args))
    if not args.text:
        raise UsageError("charset needs at least one --text")
    charset = collect(args.text, extensions=args.ext or None)
    dropped: List[Any] = []

    if getattr(args, "font", None):
        from .fontfile import is_variation_selector, open_font, unicode_cmap

        font_path, font_index = parse_font_spec(args.font)
        handle = open_font(font_path, font_index)
        try:
            cmap = unicode_cmap(handle)
        finally:
            try:
                handle.close()
            except Exception:  # pragma: no cover
                pass
        keep = {}
        for cp, use in charset.chars.items():
            if cp in cmap or is_variation_selector(cp):
                keep[cp] = use
            else:
                dropped.append(use)
        charset.chars = keep
        dropped.sort(key=lambda c: (-c.count, c.codepoint))
        codepoints = charset.codepoints
    else:
        codepoints = charset.codepoints

    if args.format == "text":
        payload = charset.text()
    elif args.format == "unicodes":
        payload = ",".join("U+%04X" % cp for cp in codepoints)
    elif args.format == "css":
        payload = format_unicode_range(codepoints)
    elif args.format == "json":
        payload = dumps(charset.to_dict(), indent=2)
    else:  # pragma: no cover - argparse restricts choices
        raise UsageError("unknown format %r" % args.format)

    if args.output:
        _write(args.output, payload + ("\n" if not payload.endswith("\n") else ""))
    else:
        sys.stdout.write(payload + "\n")

    sys.stderr.write(
        "fontgate charset: %d code points in %d ranges from %d files\n"
        % (len(codepoints), len(to_ranges(codepoints)), charset.files_scanned)
    )
    if dropped:
        sys.stderr.write(
            "  %d characters dropped because the font cannot render them: %s\n"
            % (
                len(dropped),
                " ".join("%s %s" % (c.char, "U+%04X" % c.codepoint) for c in dropped[:12]),
            )
        )
    if charset.files_skipped:
        sys.stderr.write("  %d files skipped\n" % charset.files_skipped)
    return EXIT_OK


def cmd_audit(args) -> int:
    palette = Palette(_use_color(args))
    if not args.font:
        raise UsageError("audit needs at least one --font")
    if not args.text and not getattr(args, "expect", None):
        raise UsageError("audit needs --text (file or directory) or --expect")

    charset = _collect_charset(args)
    specs = [parse_font_spec(spec) for spec in args.font]
    result = audit(specs, charset, limit=args.limit)
    data = result.to_dict(limit=args.limit)

    print(palette.bold("fontgate audit"))
    print("  %-10s %s" % ("fonts", len(result.fonts)))
    print("  %-10s %s code points, %s occurrences, %d files"
          % ("charset", _fmt(len(charset.chars)), _fmt(charset.total_occurrences), charset.files_scanned))
    if charset.files_skipped:
        print("  %-10s %d" % ("skipped", charset.files_skipped))
    print()

    for entry in result.fonts:
        status = "pass" if not entry.missing and not entry.empty else "fail"
        print(
            "  %s  %-28s coverage %6.2f%%  missing %d  empty %d"
            % (
                palette.status(status, status.upper().ljust(4)),
                (entry.profile.label or "")[:28],
                entry.coverage_ratio * 100,
                len(entry.missing),
                len(entry.empty),
            )
        )
    print()

    if result.findings:
        for finding in result.findings:
            print("  %s %s" % (palette.status(
                "fail" if finding.severity == "error" else "warn",
                finding.severity.upper().ljust(5),
            ), finding.message))
            chars = finding.detail.get("chars")
            if chars:
                preview = " ".join(
                    "%s %s" % (c.get("char", "?"), c.get("hex", "")) for c in chars[:12]
                )
                print("        %s" % palette.dim(preview))
    else:
        print("  %s every character is renderable in every face" % palette.ok("PASS"))

    if args.json:
        _write(args.json, dumps(data, indent=2) + "\n")
        print("\n  %s" % palette.dim("json → %s" % args.json))
    if args.html:
        _write(args.html, render_audit(data))
        print("  %s" % palette.dim("html → %s" % args.html))

    return EXIT_FINDINGS if has_errors(result.findings) else EXIT_OK


def cmd_verify(args) -> int:
    palette = Palette(_use_color(args))
    source_spec = parse_font_spec(args.source)
    subset_spec = parse_font_spec(args.subset)

    expect_text: Optional[str] = None
    if getattr(args, "expect", None):
        expect_text = args.expect
    elif args.expect_file:
        charset = collect(args.expect_file, extensions=args.ext or None)
        expect_text = charset.text()

    budget = parse_size(args.budget) if args.budget else None
    result = run_verify(
        source_spec,
        subset_spec,
        expect_text=expect_text,
        budget=budget,
        declared_format=args.declared_format,
    )
    data = result.to_dict()

    print(palette.bold("fontgate verify"))
    print("  %-9s %s" % ("source", "%s (%s)" % (source_spec[0], result.source.container)))
    print("  %-9s %s (%s)" % ("subset", "%s" % subset_spec[0], result.subset.container))
    print("  %-9s %d bytes → %d bytes (%.3f%%)"
          % ("size", result.source.file_size, result.subset.file_size,
             data["size"]["ratio"] * 100 if data["size"]["ratio"] is not None else 0))
    print()

    for check in result.checks:
        print(
            "  %s  %-11s %s"
            % (
                palette.status(
                    "fail" if check.status == "fail" else ("warn" if check.status in ("warn", "skip") else "pass"),
                    check.status.upper().ljust(4),
                ),
                check.name,
                check.summary,
            )
        )

    if result.findings:
        print()
        for finding in result.findings:
            print("  %s %s" % (
                palette.status("fail" if finding.severity == "error" else "warn",
                               finding.severity.upper().ljust(5)),
                finding.message,
            ))

    if args.json:
        _write(args.json, dumps(data, indent=2) + "\n")
        print("\n  %s" % palette.dim("json → %s" % args.json))
    if args.html:
        _write(args.html, render_verify(data))
        print("  %s" % palette.dim("html → %s" % args.html))

    if has_errors(result.findings):
        budget_only = all(
            f.code == "budget-exceeded" for f in result.findings if f.severity == "error"
        )
        return EXIT_BUDGET if budget_only else EXIT_FINDINGS
    return EXIT_OK


def cmd_family(args) -> int:
    palette = Palette(_use_color(args))
    if len(args.font) < 2:
        raise UsageError("family needs at least two --font arguments")
    profiles = [build_profile(*parse_font_spec(spec)) for spec in args.font]
    findings = family_consistency(profiles)

    print(palette.bold("fontgate family"))
    groups: Dict[str, List[Any]] = {}
    for item in profiles:
        groups.setdefault(item.family or "(unnamed)", []).append(item)
    for family, members in groups.items():
        print("  %s" % family)
        for member in sorted(members, key=lambda m: m.weight_class):
            print(
                "    %-14s weight %3d  cmap %6s  digest %s"
                % (
                    member.subfamily or "-",
                    member.weight_class,
                    _fmt(member.cmap_count),
                    member.cmap_digest[:12],
                )
            )
    print()
    if findings:
        for finding in findings:
            print("  %s %s" % (
                palette.status("fail" if finding.severity == "error" else "warn",
                               finding.severity.upper().ljust(5)),
                finding.message,
            ))
    else:
        print("  %s faces agree on coverage" % palette.ok("PASS"))

    if args.json:
        _write(args.json, dumps({"kind": "family", "findings": [f.to_dict() for f in findings]}, indent=2) + "\n")
        print("\n  %s" % palette.dim("json → %s" % args.json))
    return EXIT_FINDINGS if has_errors(findings) else EXIT_OK


def cmd_report(args) -> int:
    palette = Palette(_use_color(args))
    with open(args.input, encoding="utf-8") as handle:
        import json

        data = json.load(handle)
    kind = data.get("kind")
    if kind == "audit":
        html_text = render_audit(data)
    elif kind == "verify":
        html_text = render_verify(data)
    else:
        raise UsageError("cannot tell what kind of result %s is" % args.input)
    _write(args.html, html_text)
    print(palette.dim("html → %s" % args.html))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fontgate",
        description="Verification gates for web font delivery.",
    )
    parser.add_argument("--version", action="version", version="fontgate %s" % __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--json", help="write the machine-readable result to this path")
        p.add_argument("--no-color", action="store_true", help="disable ANSI colour")

    p_inspect = sub.add_parser("inspect", help="dump what a font binary actually contains")
    p_inspect.add_argument("font", nargs="+", help="font path, or path@INDEX for collections")
    p_inspect.add_argument("--json", help="write JSON to this path")
    p_inspect.add_argument("--no-color", action="store_true")
    p_inspect.set_defaults(func=cmd_inspect)

    p_charset = sub.add_parser(
        "charset", help="emit collected characters for pyftsubset or @font-face"
    )
    p_charset.add_argument("--text", action="append", required=True, help="file or directory")
    p_charset.add_argument("--ext", action="append", help="override the scanned extensions")
    p_charset.add_argument(
        "--font",
        help="intersect with this font's cmap so the output is guaranteed renderable",
    )
    p_charset.add_argument("-o", "--output", help="write here instead of stdout")
    p_charset.add_argument(
        "--format",
        choices=("text", "unicodes", "css", "json"),
        default="text",
        help="text for --text-file, unicodes for --unicodes, css for unicode-range",
    )
    p_charset.add_argument("--no-color", action="store_true")
    p_charset.set_defaults(func=cmd_charset)

    p_audit = sub.add_parser("audit", help="cross-reference text against one or more faces")
    p_audit.add_argument("--font", action="append", required=True, help="font path, repeatable")
    p_audit.add_argument("--text", action="append", help="file or directory, repeatable")
    p_audit.add_argument("--ext", action="append", help="override the scanned extensions")
    p_audit.add_argument("--expect", help="use this literal string instead of --text")
    p_audit.add_argument("--limit", type=int, default=40, help="characters listed per face")
    common(p_audit)
    p_audit.add_argument("--html", help="write a self-contained HTML report to this path")
    p_audit.set_defaults(func=cmd_audit)

    p_verify = sub.add_parser("verify", help="prove a subset is a faithful reduction")
    p_verify.add_argument("--source", required=True, help="original font")
    p_verify.add_argument("--subset", required=True, help="candidate font")
    p_verify.add_argument("--expect", help="literal text the subset must render")
    p_verify.add_argument("--expect-file", action="append", help="file or directory of expected text")
    p_verify.add_argument("--ext", action="append", help="override the scanned extensions")
    p_verify.add_argument("--budget", help="maximum size, e.g. 120kb")
    p_verify.add_argument("--declared-format", help="format declared in @font-face, e.g. woff2")
    common(p_verify)
    p_verify.add_argument("--html", help="write a self-contained HTML report to this path")
    p_verify.set_defaults(func=cmd_verify)

    p_family = sub.add_parser("family", help="compare faces that share a family name")
    p_family.add_argument("--font", action="append", required=True, help="font path, repeatable")
    common(p_family)
    p_family.set_defaults(func=cmd_family)

    p_report = sub.add_parser("report", help="re-render a saved JSON result as HTML")
    p_report.add_argument("input", help="JSON written by audit or verify")
    p_report.add_argument("--html", required=True, help="output HTML path")
    p_report.add_argument("--no-color", action="store_true")
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits the process for --help and for usage errors.  Turn
        # that into a return value so the CLI is callable from tests and from
        # other programs without hijacking their exit path.
        if isinstance(exc.code, int):
            return exc.code
        return EXIT_USAGE
    try:
        return args.func(args)
    except FontgateError as exc:
        sys.stderr.write("fontgate: %s (%s)\n" % (exc, exc.code))
        return exc.exit_code
    except FileNotFoundError as exc:
        sys.stderr.write("fontgate: %s\n" % exc)
        return EXIT_INPUT
    except BrokenPipeError:  # pragma: no cover
        return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
