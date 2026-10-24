"""Render audit and verify results as one self-contained HTML file.

The report is generated in Python rather than hydrated from JSON in the
browser, so the facts are present in the served markup.  That makes the output
verifiable with a DOM dump and readable with scripting disabled.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import __version__
from .models import dumps

CSS = """
:root{
  color-scheme: light dark;
  --bg:#FAFAF8; --panel:#FFFFFF; --panel-2:#F2F2EE; --border:#E1E1DA;
  --fg:#1A1A18; --muted:#6B6B63; --accent:#3A63B8;
  --ok:#2E7D53; --warn:#9A6B00; --fail:#A32B25; --chip:#EDEDE7;
  --mono:ui-monospace,SFMono-Regular,"Cascadia Mono","Liberation Mono",monospace;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI","Noto Sans SC",sans-serif;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#0F1115; --panel:#161A21; --panel-2:#1C2129; --border:#2A303B;
    --fg:#E7EAF0; --muted:#98A2B3; --accent:#7FA6E8;
    --ok:#5FB98A; --warn:#D9A441; --fail:#E0736B; --chip:#222834;
  }
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);font-family:var(--sans);
  font-size:15px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:40px 24px 80px}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:12px 20px;
  padding-bottom:18px;border-bottom:1px solid var(--border);margin-bottom:28px}
h1{font-size:22px;font-weight:600;margin:0;letter-spacing:-0.01em}
.sub{color:var(--muted);font-size:13px;font-family:var(--mono)}
.badge{display:inline-flex;align-items:center;gap:7px;padding:5px 12px;border-radius:999px;
  font-size:12.5px;font-weight:600;letter-spacing:.02em;border:1px solid currentColor}
.badge i{width:7px;height:7px;border-radius:50%;background:currentColor;display:block}
.b-pass{color:var(--ok)} .b-warn{color:var(--warn)} .b-fail{color:var(--fail)}
h2{font-size:14px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);margin:38px 0 14px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.card .k{font-size:11.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
.card .v{font-size:24px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:2px}
.card .u{font-size:12px;color:var(--muted);font-family:var(--mono)}
table{width:100%;border-collapse:collapse;background:var(--panel);
  border:1px solid var(--border);border-radius:10px;overflow:hidden;font-size:13.5px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--border);vertical-align:top}
tr:last-child td{border-bottom:none}
th{font-size:11.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);
  font-weight:600;background:var(--panel-2)}
td.mono,th.mono{font-family:var(--mono);font-size:12.5px}
.t-pass{color:var(--ok);font-weight:600} .t-warn{color:var(--warn);font-weight:600}
.t-fail{color:var(--fail);font-weight:600} .t-skip{color:var(--muted);font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:8px}
.glyph{background:var(--panel);border:1px solid var(--border);border-radius:8px;
  padding:8px;text-align:center;overflow:hidden}
.glyph .g{font-size:26px;line-height:1.25;min-height:34px;word-break:break-all}
.glyph .c{font-family:var(--mono);font-size:10.5px;color:var(--muted)}
.glyph .n{font-size:10.5px;color:var(--muted)}
.glyph .f{font-family:var(--mono);font-size:9.5px;color:var(--muted);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.glyph.bad{border-color:color-mix(in srgb,var(--fail) 45%,var(--border))}
.glyph.empty{border-color:color-mix(in srgb,var(--warn) 45%,var(--border))}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{font-family:var(--mono);font-size:11px;color:var(--muted);background:var(--chip);
  border-radius:5px;padding:2px 7px;white-space:nowrap}
.finding{background:var(--panel);border:1px solid var(--border);border-left:3px solid var(--muted);
  border-radius:8px;padding:11px 14px;margin-bottom:8px}
.finding.error{border-left-color:var(--fail)} .finding.warn{border-left-color:var(--warn)}
.finding.info{border-left-color:var(--accent)}
.finding .code{font-family:var(--mono);font-size:11px;color:var(--muted)}
.finding .msg{margin-top:2px}
details{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:10px 14px}
summary{cursor:pointer;font-size:13px;color:var(--muted)}
pre{font-family:var(--mono);font-size:12px;overflow:auto;max-height:420px;margin:12px 0 0}
.muted{color:var(--muted)}
footer{margin-top:44px;padding-top:16px;border-top:1px solid var(--border);
  color:var(--muted);font-size:12px;font-family:var(--mono)}
"""

STATUS_CLASS = {"pass": "b-pass", "warn": "b-warn", "fail": "b-fail"}
CHECK_CLASS = {"pass": "t-pass", "warn": "t-warn", "fail": "t-fail", "skip": "t-skip"}


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _bytes(n: Optional[int]) -> str:
    if n is None:
        return "—"
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f KiB" % (n / 1024)
    return "%.2f MiB" % (n / (1024 * 1024))


def _badge(status: str) -> str:
    label = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}.get(status, status.upper())
    return '<span class="badge %s"><i></i>%s</span>' % (
        STATUS_CLASS.get(status, "b-warn"),
        _e(label),
    )


def _cards(items: List[Dict[str, str]]) -> str:
    cells = []
    for item in items:
        unit = '<span class="u">%s</span>' % _e(item["unit"]) if item.get("unit") else ""
        cells.append(
            '<div class="card"><div class="k">%s</div><div class="v">%s%s</div></div>'
            % (_e(item["label"]), _e(item["value"]), unit)
        )
    return '<div class="cards">%s</div>' % "".join(cells)


def _checks_table(checks: List[Dict[str, Any]]) -> str:
    rows = []
    for check in checks:
        rows.append(
            "<tr><td class=\"mono\">%s</td><td class=\"%s\">%s</td><td>%s</td></tr>"
            % (
                _e(check["name"]),
                CHECK_CLASS.get(check["status"], "t-skip"),
                _e(check["status"].upper()),
                _e(check["summary"]),
            )
        )
    return (
        "<table><thead><tr><th>Check</th><th>Verdict</th><th>Measured</th></tr></thead>"
        "<tbody>%s</tbody></table>" % "".join(rows)
    )


def _detail_chips(detail: Dict[str, Any]) -> str:
    """Render finding detail as chips instead of a raw JSON blob."""
    chips: List[str] = []

    def add(text: str, title: str = "") -> None:
        chips.append(
            '<span class="chip" title="%s">%s</span>' % (_e(title or text), _e(text))
        )

    for item in detail.get("chars", []) or []:
        if isinstance(item, dict):
            add("%s %s" % (item.get("char", "?"), item.get("hex", "")), item.get("hex", ""))
    for item in detail.get("samples", []) or []:
        if isinstance(item, dict):
            label = item.get("char") or item.get("glyph") or item.get("code") or ""
            suffix = item.get("hex") or item.get("table") or ""
            add("%s %s" % (label, suffix), json.dumps(item, ensure_ascii=False))
        else:
            add(str(item))
    for key in ("features", "critical_lost", "dropped_irrelevant", "tables"):
        value = detail.get(key)
        if isinstance(value, list):
            for entry in value:
                add(str(entry), key)
    if not chips:
        return ""
    return '<div class="chips">%s</div>' % "".join(chips)


def _findings_block(findings: List[Dict[str, Any]]) -> str:
    if not findings:
        return '<p class="muted">No findings. Every requested check passed.</p>'
    blocks = []
    for finding in findings:
        blocks.append(
            '<div class="finding %s"><div class="code">%s · %s</div>'
            '<div class="msg">%s</div>%s</div>'
            % (
                _e(finding["severity"]),
                _e(finding["code"]),
                _e(finding["scope"]),
                _e(finding["message"]),
                _detail_chips(finding.get("detail", {})),
            )
        )
    return "".join(blocks)


def _glyph_grid(chars: List[Dict[str, Any]], kind: str) -> str:
    if not chars:
        return '<p class="muted">None.</p>'
    cells = []
    for item in chars:
        source = ""
        if item.get("files"):
            first = item["files"][0]
            source = "%s:%s" % (
                first["path"].replace("\\", "/").split("/")[-1],
                first["first_line"],
            )
        cells.append(
            '<div class="glyph %s" title="%s">'
            '<div class="g">%s</div>'
            '<div class="c">%s</div>'
            '<div class="n">%sx</div>'
            '<div class="f">%s</div></div>'
            % (
                _e(kind),
                _e("%s %s" % (item["hex"], item["char"])),
                _e(item["char"]),
                _e(item["hex"]),
                _e(item["count"]),
                _e(source),
            )
        )
    return '<div class="grid">%s</div>' % "".join(cells)


def _shell(title: str, subtitle: str, status: str, body: str, payload: Any) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s — fontgate</title>
<style>%(css)s</style></head>
<body><div class="wrap">
<header><h1>%(title)s</h1>%(badge)s<span class="sub">%(subtitle)s</span></header>
%(body)s
<footer>fontgate %(version)s · generated %(stamp)s · all measurements read from the font binary</footer>
</div>
<details style="margin-top:18px"><summary>Raw result JSON</summary>
<pre>%(payload)s</pre></details>
<script type="application/json" id="fontgate-payload">%(inline)s</script>
</body></html>
""" % {
        "title": _e(title),
        "css": CSS,
        "badge": _badge(status),
        "subtitle": _e(subtitle),
        "body": body,
        "version": _e(__version__),
        "stamp": _e(stamp),
        "payload": _e(json.dumps(json.loads(dumps(payload)), ensure_ascii=False, indent=1)),
        "inline": dumps(payload).replace("</", "<\\/"),
    }


def render_audit(data: Dict[str, Any]) -> str:
    """Render the serialised form, so a saved JSON can be re-rendered later."""
    charset = data["charset"]
    parts: List[str] = []

    totals = [
        {"label": "Fonts", "value": str(len(data["fonts"]))},
        {
            "label": "Characters",
            "value": str(charset["unique_codepoints"]),
            "unit": "unique",
        },
        {"label": "Occurrences", "value": "%d" % charset["total_occurrences"]},
        {"label": "Files", "value": str(charset["files_scanned"])},
        {"label": "Errors", "value": str(data["summary"]["error"])},
        {"label": "Warnings", "value": str(data["summary"]["warn"])},
    ]
    parts.append(_cards(totals))

    parts.append("<h2>Coverage per face</h2>")
    rows = []
    for entry in data["fonts"]:
        font = entry["font"]
        rows.append(
            "<tr><td>%s<div class=\"mono muted\" style=\"font-size:11px\">%s</div></td>"
            "<td class=\"mono\">%s</td><td class=\"mono\">%s</td>"
            "<td class=\"mono\">%d</td><td class=\"mono\">%d</td>"
            "<td class=\"mono\">%d</td><td class=\"mono\">%.2f%%</td></tr>"
            % (
                _e(font["full_name"] or font["family"]),
                _e(font["path"]),
                _e(font["container"]),
                _e(font["face_format"]),
                entry["missing_count"],
                entry["empty_count"],
                entry["blank_count"],
                entry["coverage_ratio"] * 100,
            )
        )
    parts.append(
        "<table><thead><tr><th>Face</th><th>Container</th><th>Outlines</th>"
        "<th>Missing</th><th>Empty</th><th>Skipped</th><th>Coverage</th></tr></thead>"
        "<tbody>%s</tbody></table>" % "".join(rows)
    )
    parts.append(
        '<p class="muted" style="font-size:12px;margin-top:8px">Skipped counts code '
        "points that need no glyph of their own (variation selectors); coverage is "
        "measured over the remainder.</p>"
    )

    parts.append("<h2>Checks</h2>")
    parts.append(_checks_table(data["checks"]))

    for entry in data["fonts"]:
        font = entry["font"]
        if entry["missing"]:
            parts.append(
                "<h2>Missing in %s <span class=\"muted\" style=\"text-transform:none;letter-spacing:0\">(%d)</span></h2>"
                % (_e(font["full_name"] or font["family"]), entry["missing_count"])
            )
            parts.append(_glyph_grid(entry["missing"], "bad"))
        if entry["empty"]:
            parts.append(
                "<h2>Empty glyphs in %s <span class=\"muted\" style=\"text-transform:none;letter-spacing:0\">(%d)</span></h2>"
                % (_e(font["full_name"] or font["family"]), entry["empty_count"])
            )
            parts.append(_glyph_grid(entry["empty"], "empty"))

    parts.append("<h2>Findings</h2>")
    parts.append(_findings_block(data["findings"]))
    return _shell(
        "Font coverage audit",
        "%d code points × %d faces" % (charset["unique_codepoints"], len(data["fonts"])),
        data["status"],
        "".join(parts),
        data,
    )


def render_verify(data: Dict[str, Any]) -> str:
    """Render the serialised form of a verify result."""
    size = data["size"]
    parts: List[str] = []

    ratio = size["ratio"]
    parts.append(
        _cards(
            [
                {"label": "Status", "value": data["status"].upper()},
                {"label": "Expected", "value": str(data["expected"]), "unit": "characters"},
                {"label": "Covered", "value": str(data["covered"])},
                {"label": "Source", "value": _bytes(size["source_bytes"])},
                {"label": "Subset", "value": _bytes(size["subset_bytes"])},
                {
                    "label": "Ratio",
                    "value": "%.3f%%" % (ratio * 100) if ratio is not None else "—",
                },
            ]
        )
    )

    parts.append("<h2>Checks</h2>")
    parts.append(_checks_table(data["checks"]))

    parts.append("<h2>Face comparison</h2>")
    src, sub = data["source"], data["subset"]
    rows = [
        ("family", src["family"], sub["family"]),
        ("subfamily", src["subfamily"], sub["subfamily"]),
        ("weight", src["weight_class"], sub["weight_class"]),
        ("container", src["container"], sub["container"]),
        ("outlines", src["outline_kind"], sub["outline_kind"]),
        ("glyphs", src["glyph_count"], sub["glyph_count"]),
        ("code points", src["cmap_count"], sub["cmap_count"]),
        ("bytes", _bytes(src["file_size"]), _bytes(sub["file_size"])),
        ("sha256", src["sha256"][:16], sub["sha256"][:16]),
    ]
    body = "".join(
        "<tr><td class=\"mono\">%s</td><td class=\"mono\">%s</td><td class=\"mono\">%s</td></tr>"
        % (_e(a), _e(b), _e(c))
        for a, b, c in rows
    )
    parts.append(
        "<table><thead><tr><th>Property</th><th>Source</th><th>Subset</th></tr></thead>"
        "<tbody>%s</tbody></table>" % body
    )

    parts.append("<h2>Findings</h2>")
    parts.append(_findings_block(data["findings"]))
    return _shell(
        "Subset verification",
        "%s → %s" % (src["path"], sub["path"]),
        data["status"],
        "".join(parts),
        data,
    )
