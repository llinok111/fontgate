from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from helpers import (
    add_liga,
    build_font,
    set_advance,
    set_family,
    subset_font,
    to_woff2,
)

from fontgate.verify import verify

CPS = (0x41, 0x42, 0x43, 0x4E2D, 0x6587)
TEXT = "ABC中文"


class VerifyParties:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.source = build_font(os.path.join(self.dir, "source.ttf"), CPS)

    def tearDown(self):
        self.tmp.cleanup()

    def make_subset(self, text: str = TEXT, name: str = "sub.woff2", **kwargs):
        target = os.path.join(self.dir, name)
        return subset_font(self.source, target, text, **kwargs)

    @staticmethod
    def codes(result):
        return [f.code for f in result.findings]


class HappyPathTest(VerifyParties, unittest.TestCase):
    def test_faithful_subset_passes(self):
        subset = self.make_subset()
        result = verify((self.source, 0), (subset, 0), expect_text=TEXT)
        self.assertEqual(result.status, "pass", [f.message for f in result.findings])
        self.assertEqual(result.expected, len(set(TEXT)))
        self.assertEqual(result.covered, len(set(TEXT)))
        self.assertEqual(result.subset.container, "woff2")

    def test_ratio_is_measured_not_assumed(self):
        subset = self.make_subset()
        result = verify((self.source, 0), (subset, 0), expect_text=TEXT)
        data = result.to_dict()
        self.assertLess(data["size"]["subset_bytes"], data["size"]["source_bytes"])
        self.assertGreater(data["size"]["ratio"], 0)


class CoverageTest(VerifyParties, unittest.TestCase):
    def test_missing_characters_fail(self):
        subset = self.make_subset(text="AB")  # C and CJK dropped
        result = verify((self.source, 0), (subset, 0), expect_text=TEXT)
        self.assertEqual(result.status, "fail")
        self.assertIn("coverage-missing", self.codes(result))
        self.assertEqual(result.covered, 2)

    def test_missing_expectation_downgrades_to_skip(self):
        subset = self.make_subset()
        result = verify((self.source, 0), (subset, 0), expect_text=None)
        coverage = [c for c in result.checks if c.name == "coverage"][0]
        self.assertEqual(coverage.status, "skip")

    def test_empty_glyph_in_subset_fails(self):
        source = build_font(
            os.path.join(self.dir, "hollow.ttf"), CPS, empty=(0x4E2D,)
        )
        subset = subset_font(source, os.path.join(self.dir, "hollow.woff2"), TEXT)
        result = verify((source, 0), (subset, 0), expect_text=TEXT)
        self.assertIn("coverage-empty", self.codes(result))


class FormatTest(VerifyParties, unittest.TestCase):
    def test_woff2_name_with_truetype_body_fails(self):
        liar = os.path.join(self.dir, "liar.woff2")
        shutil.copyfile(self.source, liar)
        result = verify((self.source, 0), (liar, 0), expect_text=TEXT)
        self.assertIn("format-mismatch", self.codes(result))
        mismatch = [f for f in result.findings if f.code == "format-mismatch"][0]
        self.assertEqual(mismatch.severity, "error")

    def test_real_woff2_passes_format(self):
        subset = self.make_subset()
        result = verify((self.source, 0), (subset, 0), expect_text=TEXT)
        fmt = [c for c in result.checks if c.name == "format"][0]
        self.assertEqual(fmt.status, "pass")

    def test_declared_format_checked(self):
        subset = self.make_subset()
        result = verify(
            (self.source, 0), (subset, 0), expect_text=TEXT, declared_format="truetype"
        )
        self.assertIn("format-mismatch", self.codes(result))

    def test_declared_format_matching_passes(self):
        subset = self.make_subset()
        result = verify(
            (self.source, 0), (subset, 0), expect_text=TEXT, declared_format="woff2"
        )
        self.assertNotIn("format-mismatch", self.codes(result))


class MetricsTest(VerifyParties, unittest.TestCase):
    def test_changed_advance_is_error(self):
        subset = self.make_subset()
        set_advance(subset, 0x41, 777)
        result = verify((self.source, 0), (subset, 0), expect_text=TEXT)
        self.assertIn("metrics-advance", self.codes(result))
        check = [c for c in result.checks if c.name == "metrics"][0]
        self.assertEqual(check.status, "fail")
        self.assertEqual(check.details["advance_diffs"], 1)

    def test_untouched_metrics_pass(self):
        subset = self.make_subset()
        result = verify((self.source, 0), (subset, 0), expect_text=TEXT)
        check = [c for c in result.checks if c.name == "metrics"][0]
        self.assertEqual(check.status, "pass")
        self.assertEqual(check.details["advance_diffs"], 0)
        self.assertEqual(check.details["bounds_diffs"], 0)


class BudgetTest(VerifyParties, unittest.TestCase):
    def test_budget_exceeded(self):
        subset = self.make_subset()
        result = verify((self.source, 0), (subset, 0), expect_text=TEXT, budget=10)
        self.assertIn("budget-exceeded", self.codes(result))

    def test_budget_respected(self):
        subset = self.make_subset()
        size = os.path.getsize(subset)
        result = verify((self.source, 0), (subset, 0), expect_text=TEXT, budget=size + 1)
        self.assertNotIn("budget-exceeded", self.codes(result))

    def test_no_budget_is_skip(self):
        subset = self.make_subset()
        result = verify((self.source, 0), (subset, 0), expect_text=TEXT)
        check = [c for c in result.checks if c.name == "budget"][0]
        self.assertEqual(check.status, "skip")


class IdentityTest(VerifyParties, unittest.TestCase):
    def test_renamed_family_is_error(self):
        subset = self.make_subset()
        set_family(subset, "Something Else")
        result = verify((self.source, 0), (subset, 0), expect_text=TEXT)
        self.assertIn("identity-drift", self.codes(result))
        drift = [f for f in result.findings if f.code == "identity-drift"][0]
        self.assertEqual(drift.severity, "error")


class LayoutTest(VerifyParties, unittest.TestCase):
    def test_relevant_feature_loss_is_error(self):
        source = build_font(os.path.join(self.dir, "liga.ttf"), CPS)
        add_liga(source, 0x41, 0x42)
        subset = subset_font(
            source,
            os.path.join(self.dir, "liga.woff2"),
            "AB",
            options_hook=lambda options: setattr(options, "layout_features", ["kern"]),
        )
        result = verify((source, 0), (subset, 0), expect_text="AB")
        self.assertIn("layout-feature-lost", self.codes(result))

    def test_irrelevant_feature_loss_is_not_an_error(self):
        source = build_font(os.path.join(self.dir, "liga2.ttf"), CPS)
        add_liga(source, 0x41, 0x42)
        # Target text never contains A, so liga cannot apply to it.
        subset = subset_font(
            source,
            os.path.join(self.dir, "liga2.woff2"),
            "中文",
            options_hook=lambda options: setattr(options, "layout_features", ["kern"]),
        )
        result = verify((source, 0), (subset, 0), expect_text="中文")
        self.assertNotIn("layout-feature-lost", self.codes(result))

    def test_dangling_reference_is_detected(self):
        """fontTools refuses to serialise a broken reference, so the detector is
        exercised against an in-memory table that was never written out."""
        from fontTools.ttLib import TTFont

        from fontgate.verify import _dangling_references, _glyph_references

        source = build_font(os.path.join(self.dir, "dang.ttf"), CPS)
        add_liga(source, 0x41, 0x42)
        font = TTFont(source)
        try:
            table = font["GSUB"].table
            replaced = 0
            for lookup in table.LookupList.Lookup:
                for subtable in lookup.SubTable:
                    mapping = getattr(subtable, "mapping", None)
                    if isinstance(mapping, dict):
                        for key in list(mapping):
                            mapping[key] = "ghostGlyph"
                            replaced += 1
            self.assertGreater(replaced, 0, "fixture produced no substitution to break")
            dangling = _dangling_references(font, _glyph_references(font))
        finally:
            font.close()
        self.assertEqual([name for _, name in dangling], ["ghostGlyph"])


if __name__ == "__main__":
    unittest.main()
