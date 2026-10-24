from __future__ import annotations

import os
import tempfile
import unittest

from helpers import build_font, glyph_name, to_woff2

from fontgate.errors import InputError, UnsupportedFormatError
from fontgate.fontfile import (
    cmap_digest,
    collection_size,
    glyph_outline_status,
    is_blank_character,
    open_font,
    profile,
    sniff_format,
    supports_supplementary,
    unicode_cmap,
)

CPS = (0x41, 0x42, 0x4E2D, 0x6587)


class SniffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.ttf = build_font(os.path.join(self.dir, "a.ttf"), CPS)

    def tearDown(self):
        self.tmp.cleanup()

    def test_truetype(self):
        self.assertEqual(sniff_format(self.ttf), "truetype")

    def test_woff2(self):
        path = to_woff2(self.ttf, os.path.join(self.dir, "a.woff2"))
        self.assertEqual(sniff_format(path), "woff2")

    def test_unknown_binary(self):
        path = os.path.join(self.dir, "x.bin")
        with open(path, "wb") as handle:
            handle.write(b"not a font at all")
        self.assertEqual(sniff_format(path), "unknown")
        with self.assertRaises(UnsupportedFormatError):
            profile(path)

    def test_truncated_file(self):
        path = os.path.join(self.dir, "short.ttf")
        with open(path, "wb") as handle:
            handle.write(b"\x00\x01")
        with self.assertRaises(UnsupportedFormatError):
            sniff_format(path)


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.ttf = build_font(
            os.path.join(self.dir, "a.ttf"), CPS, family="Fixture Sans", weight=400
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_core_fields(self):
        item = profile(self.ttf)
        self.assertEqual(item.family, "Fixture Sans")
        self.assertEqual(item.weight_class, 400)
        self.assertEqual(item.outline_kind, "truetype-glyf")
        self.assertEqual(item.cmap_count, len(CPS))
        self.assertEqual(item.glyph_count, len(CPS) + 1)
        self.assertEqual(len(item.sha256), 64)
        self.assertFalse(item.is_variable)
        self.assertEqual(item.missing_required, [])

    def test_sha_is_content_addressed(self):
        first = profile(self.ttf).sha256
        second = profile(self.ttf).sha256
        self.assertEqual(first, second)
        other = build_font(os.path.join(self.dir, "b.ttf"), CPS, family="Other")
        self.assertNotEqual(first, profile(other).sha256)

    def test_index_on_single_face_is_rejected(self):
        with self.assertRaises(InputError):
            profile(self.ttf, index=1)

    def test_corrupt_body_raises_input_error(self):
        path = os.path.join(self.dir, "broken.ttf")
        with open(self.ttf, "rb") as handle:
            data = bytearray(handle.read())
        # numTables lives at offset 4; claiming 65535 tables runs off the end of
        # the file and must surface as an input error, not a traceback.
        data[4:6] = b"\xff\xff"
        with open(path, "wb") as handle:
            handle.write(data)
        with self.assertRaises(InputError):
            profile(path)


class CmapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.ttf = build_font(os.path.join(self.dir, "a.ttf"), CPS)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unicode_cmap_matches(self):
        font = open_font(self.ttf)
        try:
            cmap = unicode_cmap(font)
        finally:
            font.close()
        self.assertEqual(set(cmap), set(CPS))

    def test_digest_is_order_independent(self):
        a = {0x41: "x", 0x42: "y"}
        b = {0x42: "y", 0x41: "x"}
        self.assertEqual(cmap_digest(a), cmap_digest(b))
        self.assertNotEqual(cmap_digest(a), cmap_digest({0x41: "x"}))

    def test_supplementary_detection(self):
        font = open_font(self.ttf)
        try:
            self.assertFalse(supports_supplementary(font))
        finally:
            font.close()


class OutlineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.ttf = build_font(
            os.path.join(self.dir, "a.ttf"), CPS, empty=(0x6587,)
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_glyph_is_reported(self):
        font = open_font(self.ttf)
        try:
            self.assertEqual(glyph_outline_status(font, glyph_name(0x41)), "outline")
            self.assertEqual(glyph_outline_status(font, glyph_name(0x6587)), "empty")
            self.assertEqual(glyph_outline_status(font, "ghost"), "missing")
        finally:
            font.close()

    def test_blank_character_rules(self):
        self.assertTrue(is_blank_character(" "))
        self.assertTrue(is_blank_character("\u3000"))
        self.assertTrue(is_blank_character("\ufeff"))
        self.assertTrue(is_blank_character("\ufe0f"), "variation selectors need no glyph")
        self.assertFalse(is_blank_character("字"))
        self.assertFalse(is_blank_character("A"))


class CollectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_face_reports_one(self):
        ttf = build_font(os.path.join(self.dir, "a.ttf"), CPS)
        self.assertEqual(collection_size(ttf), 1)


if __name__ == "__main__":
    unittest.main()
