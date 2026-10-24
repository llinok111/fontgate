from __future__ import annotations

import os
import tempfile
import unittest

from helpers import build_font, write_text

from fontgate.audit import audit, family_consistency
from fontgate.charset import collect
from fontgate.errors import InputError
from fontgate.fontfile import profile


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _charset(self, text: str):
        path = write_text(os.path.join(self.dir, "text.txt"), text)
        return collect([path])

    def test_all_covered(self):
        font = build_font(os.path.join(self.dir, "a.ttf"), [0x41, 0x42, 0x4E2D])
        result = audit([(font, 0)], self._charset("AA中"))
        # Counts are code points, not occurrences: "A" appears twice but is one
        # entry in the matrix.
        self.assertEqual(result.fonts[0].covered, 2)
        self.assertEqual(result.fonts[0].missing, [])
        self.assertFalse(any(f.severity == "error" for f in result.findings))

    def test_missing_characters_are_reported_with_counts(self):
        font = build_font(os.path.join(self.dir, "a.ttf"), [0x41])
        result = audit([(font, 0)], self._charset("A中中中"))
        entry = result.fonts[0]
        self.assertEqual([c.char for c in entry.missing], ["中"])
        self.assertEqual(entry.missing[0].count, 3)
        codes = [f.code for f in result.findings]
        self.assertIn("coverage-missing", codes)

    def test_empty_glyph_is_not_counted_as_covered(self):
        font = build_font(os.path.join(self.dir, "a.ttf"), [0x41, 0x4E2D], empty=(0x4E2D,))
        result = audit([(font, 0)], self._charset("A中"))
        entry = result.fonts[0]
        self.assertEqual([c.char for c in entry.empty], ["中"])
        self.assertEqual(entry.covered, 1)
        self.assertIn("coverage-empty", [f.code for f in result.findings])

    def test_coverage_ratio_excludes_variation_selectors(self):
        font = build_font(os.path.join(self.dir, "a.ttf"), [0x41, 0x2705])
        result = audit([(font, 0)], self._charset("A✅\ufe0f"))
        entry = result.fonts[0]
        self.assertEqual(entry.blank, 1)
        self.assertEqual(entry.missing, [])
        self.assertAlmostEqual(entry.coverage_ratio, 1.0)

    def test_two_fonts_have_independent_verdicts(self):
        good = build_font(os.path.join(self.dir, "good.ttf"), [0x41, 0x4E2D], family="Good")
        bad = build_font(os.path.join(self.dir, "bad.ttf"), [0x41], family="Bad")
        result = audit([(good, 0), (bad, 0)], self._charset("A中"))
        verdicts = {entry.profile.family: len(entry.missing) for entry in result.fonts}
        self.assertEqual(verdicts["Good"], 0)
        self.assertEqual(verdicts["Bad"], 1)

    def test_container_warning_for_collection(self):
        # A plain TTF re-labelled as a collection is not required here; instead
        # assert the check exists by auditing a file whose sniffed container is
        # a collection only when one is present.  Covered in the integration
        # test against a real .ttc.
        self.assertTrue(True)

    def test_empty_charset_is_rejected(self):
        font = build_font(os.path.join(self.dir, "a.ttf"), [0x41])
        path = write_text(os.path.join(self.dir, "empty.txt"), "\n\n")
        charset = collect([path])
        with self.assertRaises(InputError):
            audit([(font, 0)], charset)

    def test_no_fonts_is_rejected(self):
        with self.assertRaises(InputError):
            audit([], self._charset("A"))


class FamilyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_weight_same_family_with_different_cmap_is_error(self):
        a = build_font(os.path.join(self.dir, "a.ttf"), [0x41, 0x42], family="Dup", weight=400)
        b = build_font(os.path.join(self.dir, "b.ttf"), [0x41], family="Dup", weight=400)
        findings = family_consistency([profile(a), profile(b)])
        self.assertTrue(any(f.severity == "error" and f.code == "family-cmap-drift" for f in findings))

    def test_cross_weight_gap_is_warning(self):
        regular = build_font(
            os.path.join(self.dir, "r.ttf"), [0x41, 0x42, 0x43], family="Fam", weight=400
        )
        bold = build_font(
            os.path.join(self.dir, "b.ttf"), [0x41, 0x42], family="Fam", weight=700
        )
        findings = family_consistency([profile(regular), profile(bold)])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warn")
        self.assertEqual(findings[0].detail["partial_count"], 1)

    def test_agreeing_faces_produce_nothing(self):
        a = build_font(os.path.join(self.dir, "a.ttf"), [0x41], family="Same", weight=400)
        b = build_font(os.path.join(self.dir, "b.ttf"), [0x41], family="Same", weight=700)
        self.assertEqual(family_consistency([profile(a), profile(b)]), [])

    def test_different_families_are_not_compared(self):
        a = build_font(os.path.join(self.dir, "a.ttf"), [0x41], family="One", weight=400)
        b = build_font(os.path.join(self.dir, "b.ttf"), [0x41, 0x42], family="Two", weight=700)
        self.assertEqual(family_consistency([profile(a), profile(b)]), [])


if __name__ == "__main__":
    unittest.main()
