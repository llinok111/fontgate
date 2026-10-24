from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

from helpers import build_font, subset_font, write_text

from fontgate.cli import main, parse_font_spec, parse_size

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(argv):
    """Call the CLI in-process, returning (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv + ["--no-color"] if "--no-color" not in argv else argv)
    return code, out.getvalue(), err.getvalue()


class ParseTest(unittest.TestCase):
    def test_size_units(self):
        self.assertEqual(parse_size("10kb"), 10240)
        self.assertEqual(parse_size("1.5MB"), 1572864)
        self.assertEqual(parse_size("512"), 512)

    def test_size_rejects_garbage(self):
        with self.assertRaises(Exception):
            parse_size("banana")

    def test_font_spec_index(self):
        self.assertEqual(parse_font_spec("a.ttc"), ("a.ttc", 0))
        self.assertEqual(parse_font_spec("a.ttc@3"), ("a.ttc", 3))
        self.assertEqual(parse_font_spec("dir@name/x.ttf"), ("dir@name/x.ttf", 0))


class ExitCodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.font = build_font(os.path.join(self.dir, "a.ttf"), [0x41, 0x4E2D])
        self.good = write_text(os.path.join(self.dir, "good.txt"), "A中")
        self.bad = write_text(os.path.join(self.dir, "bad.txt"), "A中Z")

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_audit_exits_zero(self):
        code, out, _ = run(["audit", "--font", self.font, "--text", self.good])
        self.assertEqual(code, 0)
        self.assertIn("PASS", out)

    def test_audit_with_missing_exits_one(self):
        code, out, _ = run(["audit", "--font", self.font, "--text", self.bad])
        self.assertEqual(code, 1)
        self.assertIn("missing", out.lower())

    def test_usage_error_exits_two(self):
        code, _, err = run(["audit", "--text", self.good])
        self.assertEqual(code, 2)  # argparse rejects the missing required flag

    def test_missing_font_exits_three(self):
        code, _, err = run(
            ["audit", "--font", os.path.join(self.dir, "nope.ttf"), "--text", self.good]
        )
        self.assertEqual(code, 3)
        self.assertIn("fontgate:", err)

    def test_not_a_font_exits_three(self):
        junk = write_text(os.path.join(self.dir, "junk.ttf"), "definitely not a font")
        code, _, err = run(["inspect", junk])
        self.assertEqual(code, 3)

    def test_budget_failure_exits_four(self):
        subset = subset_font(self.font, os.path.join(self.dir, "s.woff2"), "A中")
        code, _, _ = run(
            [
                "verify",
                "--source",
                self.font,
                "--subset",
                subset,
                "--expect",
                "A中",
                "--budget",
                "1b",
            ]
        )
        self.assertEqual(code, 4)

    def test_verify_pass_exits_zero(self):
        subset = subset_font(self.font, os.path.join(self.dir, "s.woff2"), "A中")
        code, out, _ = run(
            ["verify", "--source", self.font, "--subset", subset, "--expect", "A中"]
        )
        self.assertEqual(code, 0, out)

    def test_verify_missing_exits_one(self):
        subset = subset_font(self.font, os.path.join(self.dir, "s.woff2"), "A")
        code, _, _ = run(
            ["verify", "--source", self.font, "--subset", subset, "--expect", "A中"]
        )
        self.assertEqual(code, 1)


class CharsetCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.font = build_font(os.path.join(self.dir, "a.ttf"), [0x41, 0x4E2D])
        self.text = write_text(os.path.join(self.dir, "t.txt"), "A中Z")

    def tearDown(self):
        self.tmp.cleanup()

    def test_text_format_is_sorted(self):
        code, out, err = run(["charset", "--text", self.text])
        self.assertEqual(code, 0)
        self.assertEqual([ord(ch) for ch in out.strip()], sorted([ord("A"), ord("中"), ord("Z")]))
        self.assertIn("3 code points", err)

    def test_unicodes_format(self):
        _, out, _ = run(["charset", "--text", self.text, "--format", "unicodes"])
        self.assertIn("U+0041", out)
        self.assertIn("U+4E2D", out)

    def test_css_format(self):
        _, out, _ = run(["charset", "--text", self.text, "--format", "css"])
        self.assertIn("U+", out)

    def test_intersection_with_font_drops_unsupported(self):
        _, out, err = run(["charset", "--text", self.text, "--font", self.font])
        self.assertNotIn("Z", out)
        self.assertIn("1 characters dropped", err)

    def test_output_file(self):
        target = os.path.join(self.dir, "out.txt")
        run(["charset", "--text", self.text, "-o", target])
        with open(target, encoding="utf-8") as handle:
            self.assertIn("中", handle.read())


class InspectCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.font = build_font(os.path.join(self.dir, "a.ttf"), [0x41, 0x4E2D], family="Insp")

    def tearDown(self):
        self.tmp.cleanup()

    def test_human_output(self):
        code, out, _ = run(["inspect", self.font])
        self.assertEqual(code, 0)
        self.assertIn("Insp", out)
        self.assertIn("truetype-glyf", out)

    def test_json_output(self):
        target = os.path.join(self.dir, "p.json")
        run(["inspect", self.font, "--json", target])
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data[0]["family"], "Insp")
        self.assertEqual(len(data[0]["sha256"]), 64)


class ReportCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.font = build_font(os.path.join(self.dir, "a.ttf"), [0x41])
        self.text = write_text(os.path.join(self.dir, "t.txt"), "A中")

    def tearDown(self):
        self.tmp.cleanup()

    def test_audit_json_then_html(self):
        data_path = os.path.join(self.dir, "audit.json")
        html_path = os.path.join(self.dir, "audit.html")
        run(["audit", "--font", self.font, "--text", self.text, "--json", data_path])
        code, _, _ = run(["report", data_path, "--html", html_path])
        self.assertEqual(code, 0)
        with open(html_path, encoding="utf-8") as handle:
            html = handle.read()
        self.assertIn("Font coverage audit", html)
        self.assertIn("U+4E2D", html)

    def test_unknown_json_is_usage_error(self):
        data_path = os.path.join(self.dir, "x.json")
        with open(data_path, "w", encoding="utf-8") as handle:
            json.dump({"kind": "unknown"}, handle)
        code, _, err = run(["report", data_path, "--html", os.path.join(self.dir, "x.html")])
        self.assertEqual(code, 2)
        self.assertIn("cannot tell", err)


class ProcessTest(unittest.TestCase):
    """The in-process tests cannot prove the module is runnable; this one can."""

    def test_module_entry_point(self):
        result = subprocess.run(
            [sys.executable, "-m", "fontgate", "--version"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("fontgate", result.stdout)


if __name__ == "__main__":
    unittest.main()
