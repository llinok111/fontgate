"""Regenerate the static demo pages under docs/.

The JSON fixtures in docs/data were produced by running the CLI against a real
font (see README §6) and then rewriting the absolute source path to a relative
one.  Keeping the fixtures in the repository means the pages can be rebuilt
without the original font installed.

Usage:
    python docs/build_docs.py
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from fontgate.report import render_audit, render_verify  # noqa: E402

NAV = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>fontgate — reports</title>
<style>
:root{color-scheme:light dark;--bg:#FAFAF8;--panel:#FFF;--border:#E1E1DA;--fg:#1A1A18;
--muted:#6B6B63;--accent:#3A63B8;--mono:ui-monospace,SFMono-Regular,monospace;
--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI","Noto Sans SC",sans-serif}
@media (prefers-color-scheme:dark){:root{--bg:#0F1115;--panel:#161A21;--border:#2A303B;
--fg:#E7EAF0;--muted:#98A2B3;--accent:#7FA6E8}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font-family:var(--sans);line-height:1.7;-webkit-font-smoothing:antialiased}
.wrap{max-width:760px;margin:0 auto;padding:72px 24px}
h1{font-size:26px;font-weight:600;margin:0 0 6px;letter-spacing:-.01em}
.lead{color:var(--muted);margin:0 0 36px}
a.card{display:block;background:var(--panel);border:1px solid var(--border);border-radius:10px;
padding:16px 18px;margin-bottom:10px;text-decoration:none;color:inherit}
a.card:hover{border-color:var(--accent)}
a.card b{font-weight:600;display:block}
a.card span{color:var(--muted);font-size:13.5px}
code{font-family:var(--mono);font-size:13px;background:var(--panel);border:1px solid var(--border);
border-radius:5px;padding:1px 6px}
footer{margin-top:40px;padding-top:16px;border-top:1px solid var(--border);color:var(--muted);
font-size:12.5px;font-family:var(--mono)}
</style></head><body><div class="wrap">
<h1>fontgate — sample reports</h1>
<p class="lead">Two reports produced by the CLI from a real baseline run:
Microsoft YaHei Regular (19.7&nbsp;MB TTC) subset to WOFF2 against a three-file
demo site. Paths were rewritten to relative form; every number is measured.</p>

<a class="card" href="audit.html"><b>Coverage audit →</b>
<span>361 code points × 1 face. Six characters in the sample text have no glyph
in the font; each is listed with its code point, occurrence count and source line.</span></a>

<a class="card" href="verify.html"><b>Subset verification →</b>
<span>19.7&nbsp;MB → 104.7&nbsp;KiB (0.544%). Seven checks: format, tables,
identity, coverage, metrics, layout, budget.</span></a>

<p style="margin-top:28px;color:var(--muted);font-size:14px">
Reproduce locally:</p>
<p><code>python demo/run_demo.py</code></p>

<footer>fontgate 1.0.0 · MIT · reports are single-file, zero network requests</footer>
</div></body></html>
"""


def rewrite(node, old, new):
    """Replace the source font path everywhere in a serialised result."""
    if isinstance(node, dict):
        return {key: rewrite(value, old, new) for key, value in node.items()}
    if isinstance(node, list):
        return [rewrite(item, old, new) for item in node]
    if isinstance(node, str):
        return node.replace(old, new)
    return node


def main() -> int:
    data_dir = os.path.join(HERE, "data")
    replacements = [
        ("C:/Windows/Fonts/msyh.ttc", "fonts/msyh.ttc"),
        ("C:\\Windows\\Fonts\\msyh.ttc", "fonts/msyh.ttc"),
    ]

    with open(os.path.join(data_dir, "audit.json"), encoding="utf-8") as handle:
        audit_data = json.load(handle)
    with open(os.path.join(data_dir, "verify.json"), encoding="utf-8") as handle:
        verify_data = json.load(handle)

    for old, new in replacements:
        audit_data = rewrite(audit_data, old, new)
        verify_data = rewrite(verify_data, old, new)

    with open(os.path.join(HERE, "audit.html"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_audit(audit_data))
    with open(os.path.join(HERE, "verify.html"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_verify(verify_data))
    with open(os.path.join(HERE, "index.html"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(NAV)
    with open(os.path.join(HERE, ".nojekyll"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("")

    for name in ("index.html", "audit.html", "verify.html"):
        size = os.path.getsize(os.path.join(HERE, name))
        print("%-12s %7d bytes" % (name, size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
