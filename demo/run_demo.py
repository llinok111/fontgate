"""End-to-end demo: collect characters, subset a real font, verify, report.

Run it from the repository root:

    python demo/run_demo.py

The script needs one font that covers Chinese text.  It looks for the platform
default and accepts ``--font path@index`` to override.  Everything it produces
lands in ``demo/out/`` so the repository stays clean.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out")
SITE = os.path.join(HERE, "site")
sys.path.insert(0, ROOT)


def default_font_candidates():
    if sys.platform == "win32":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        return [
            (os.path.join(root, "Fonts", "msyh.ttc"), 0),
            (os.path.join(root, "Fonts", "simsun.ttc"), 0),
            (os.path.join(root, "Fonts", "simhei.ttf"), 0),
        ]
    if sys.platform == "darwin":
        return [("/System/Library/Fonts/PingFang.ttc", 0)]
    return [
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ]


def pick_font(explicit):
    if explicit:
        return explicit
    for path, index in default_font_candidates():
        if os.path.exists(path):
            return "%s@%d" % (path, index)
    raise SystemExit(
        "no CJK font found; pass --font /path/to/font.ttf (see README)"
    )


def step(title):
    print("\n== %s" % title)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", help="source font, optionally path@index")
    parser.add_argument("--keep", action="store_true", help="keep previous output")
    args = parser.parse_args()

    from fontgate.cli import main as cli

    font = pick_font(args.font)
    if os.path.isdir(OUT) and not args.keep:
        shutil.rmtree(OUT)
    os.makedirs(OUT, exist_ok=True)

    charset_txt = os.path.join(OUT, "charset.txt")
    subset = os.path.join(OUT, "site-subset.woff2")

    step("1/4 audit the source font against the demo site")
    code = cli(["audit", "--font", font, "--text", SITE, "--no-color"])
    print("   exit=%d (1 means real gaps exist; expected for emoji in the sample)" % code)

    step("2/4 derive a renderable character set")
    code = cli(["charset", "--text", SITE, "--font", font, "-o", charset_txt])
    if code != 0:
        return code

    step("3/4 subset with the upstream CLI")
    cmd = [
        sys.executable,
        "-m",
        "fontTools.subset",
        font.split("@")[0],
        "--text-file=%s" % charset_txt,
        "--flavor=woff2",
        "--output-file=%s" % subset,
    ]
    if "@" in font:
        cmd.insert(4, "--font-number=%s" % font.rsplit("@", 1)[1])
    print("   " + " ".join(cmd[3:]))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return result.returncode
    print("   wrote %s (%d bytes)" % (subset, os.path.getsize(subset)))

    step("4/4 verify the subset")
    code = cli(
        [
            "verify",
            "--source",
            font,
            "--subset",
            subset,
            "--expect-file",
            charset_txt,
            "--budget",
            "400kb",
            "--json",
            os.path.join(OUT, "verify.json"),
            "--html",
            os.path.join(OUT, "verify.html"),
            "--no-color",
        ]
    )
    cli(
        [
            "audit",
            "--font",
            font,
            "--text",
            SITE,
            "--json",
            os.path.join(OUT, "audit.json"),
            "--html",
            os.path.join(OUT, "audit.html"),
            "--no-color",
        ]
    )

    print("\nreports:")
    for name in ("audit.html", "verify.html"):
        print("  %s" % os.path.join(OUT, name))
    print("\nverify exit=%d (0 means every gate passed)" % code)
    return code


if __name__ == "__main__":
    sys.exit(main())
