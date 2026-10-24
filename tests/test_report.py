from __future__ import annotations

import json
import os
import re
import tempfile
import unittest

from helpers import build_font, write_text

from fontgate.audit import audit
from fontgate.charset import collect
from fontgate.models import Finding, dumps
from fontgate.report import render_audit, render_verify
from fontgate.verify import verify


class CanonicalJsonTest(unittest.TestCase):
    def test_key_order_does_not_change_the_bytes(self):
        a = {"b": 1, "a": {"d": 2, "c": 3}}
        b = {"a": {"c": 3, "d": 2}, "b": 1}
        self.assertEqual(dumps(a), dumps(b))

    def test_unicode_is_preserved(self):
        self.assertIn("中文", dumps({"k": "中文"}))

    def test_float_integers_become_ints(self):
        self.assertEqual(dumps({"x": 1.0}), '{"x":1}')

    def test_unregistered_finding_code_is_rejected(self):
        with self.assertRaises(ValueError):
            Finding(code="made-up", severity="error", message="x")

    def test_unknown_severity_is_rejected(self):
        with self.assertRaises(ValueError):
            Finding(code="coverage-missing", severity="fatal", message="x")


class AuditReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.font = build_font(os.path.join(self.dir, "a.ttf"), [0x41])
        text = write_text(os.path.join(self.dir, "t.txt"), "A中<&>")
        self.data = audit([(self.font, 0)], collect([text])).to_dict()

    def tearDown(self):
        self.tmp.cleanup()

    def test_report_contains_measured_facts(self):
        html = render_audit(self.data)
        self.assertIn("Font coverage audit", html)
        self.assertIn("Missing in", html)
        self.assertIn("U+4E2D", html)

    def test_html_is_escaped(self):
        html = render_audit(self.data)
        self.assertNotIn("<script>alert", html)
        # The literal characters from the text must appear escaped, not raw.
        self.assertIn("&lt;", html)

    def test_payload_is_parsable_json(self):
        html = render_audit(self.data)
        match = re.search(
            r'<script type="application/json" id="fontgate-payload">(.*?)</script>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        self.assertEqual(payload["kind"], "audit")
        self.assertEqual(payload["status"], self.data["status"])

    def test_no_network_references(self):
        html = render_audit(self.data)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("<link ", html)

    def test_dark_and_light_are_both_declared(self):
        html = render_audit(self.data)
        self.assertIn("prefers-color-scheme: dark", html)
        self.assertIn("color-scheme: light dark", html)


class VerifyReportTest(unittest.TestCase):
    def setUp(self):
        from helpers import subset_font

        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.source = build_font(os.path.join(self.dir, "s.ttf"), [0x41, 0x42])
        self.subset = subset_font(
            self.source, os.path.join(self.dir, "s.woff2"), "AB"
        )
        self.data = verify((self.source, 0), (self.subset, 0), expect_text="AB").to_dict()

    def tearDown(self):
        self.tmp.cleanup()

    def test_report_lists_checks_and_sizes(self):
        html = render_verify(self.data)
        self.assertIn("Subset verification", html)
        self.assertIn("format", html)
        self.assertIn("coverage", html)
        self.assertIn("sha256", html)

    def test_json_round_trip_preserves_status(self):
        reparsed = json.loads(dumps(self.data))
        html = render_verify(reparsed)
        self.assertIn(self.data["status"].upper(), html)


if __name__ == "__main__":
    unittest.main()
