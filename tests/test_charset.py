from __future__ import annotations

import os
import tempfile
import unittest

from helpers import write_text

from fontgate.charset import (
    CharUse,
    collect,
    format_unicode_range,
    strip_markup,
    to_ranges,
)
from fontgate.errors import TextReadError


class StripMarkupTest(unittest.TestCase):
    def test_tags_and_entities(self):
        text = strip_markup("<p>中文 &amp; English</p>")
        self.assertIn("中文", text)
        self.assertIn("&", text)
        self.assertNotIn("<p>", text)

    def test_comments_are_removed(self):
        text = strip_markup("visible<!-- hidden 隐藏 -->tail")
        self.assertNotIn("隐藏", text)
        self.assertIn("visible", text)
        self.assertIn("tail", text)


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_and_provenance(self):
        path = write_text(os.path.join(self.dir, "a.txt"), "字体字体\n交付")
        report = collect([path])
        self.assertEqual(report.files_scanned, 1)
        self.assertEqual(report.chars[ord("字")].count, 2)
        self.assertEqual(report.chars[ord("体")].count, 2)
        hit = report.chars[ord("交")].files[0]
        self.assertEqual(hit.path, path)
        self.assertEqual(hit.first_line, 2)

    def test_control_characters_are_excluded(self):
        path = write_text(os.path.join(self.dir, "a.txt"), "a\tb\nc\r\nd")
        report = collect([path])
        for cp in (9, 10, 13):
            self.assertNotIn(cp, report.chars)

    def test_html_tags_do_not_leak_into_charset(self):
        html = "<html><body><h1>标题</h1><p>正文</p></body></html>"
        path = write_text(os.path.join(self.dir, "a.html"), html)
        report = collect([path])
        self.assertIn(ord("标"), report.chars)
        self.assertNotIn(ord("<"), report.chars)
        self.assertNotIn(ord("h"), report.chars)

    def test_gb18030_fallback(self):
        raw = "中文编码".encode("gb18030")
        path = os.path.join(self.dir, "b.txt")
        with open(path, "wb") as handle:
            handle.write(raw)
        report = collect([path])
        self.assertEqual(report.encoding_histogram.get("gb18030"), 1)
        self.assertIn(ord("中"), report.chars)

    def test_binary_files_are_skipped(self):
        path = os.path.join(self.dir, "c.txt")
        with open(path, "wb") as handle:
            handle.write(b"\x00\x01\x02binary")
        report = collect([path])
        self.assertEqual(report.files_scanned, 0)
        self.assertEqual(report.files_skipped, 1)

    def test_extension_filter_excludes_other_suffixes(self):
        write_text(os.path.join(self.dir, "keep.md"), "甲")
        write_text(os.path.join(self.dir, "drop.bin"), "乙")
        report = collect([self.dir])
        self.assertIn(ord("甲"), report.chars)
        self.assertNotIn(ord("乙"), report.chars)

    def test_vendor_directories_are_ignored(self):
        write_text(os.path.join(self.dir, "src", "a.md"), "源码")
        write_text(os.path.join(self.dir, "node_modules", "x.md"), "依赖")
        report = collect([self.dir])
        self.assertIn(ord("源"), report.chars)
        self.assertNotIn(ord("赖"), report.chars)

    def test_missing_path_raises(self):
        with self.assertRaises(TextReadError):
            collect([os.path.join(self.dir, "nope")])

    def test_empty_roots_raise(self):
        with self.assertRaises(TextReadError):
            collect([])

    def test_text_is_sorted_by_codepoint(self):
        path = write_text(os.path.join(self.dir, "a.txt"), "乙甲丙")
        report = collect([path])
        cps = [ord(ch) for ch in report.text()]
        self.assertEqual(cps, sorted(cps))

    def test_max_chars_keeps_the_frequent_ones(self):
        path = write_text(os.path.join(self.dir, "a.txt"), "甲" * 5 + "乙" * 2 + "丙")
        report = collect([path], max_chars=2)
        self.assertEqual(len(report.chars), 2)
        self.assertIn(ord("甲"), report.chars)
        self.assertTrue(any("capped" in w for w in report.warnings))


class RangeTest(unittest.TestCase):
    def test_ranges_merge_consecutive(self):
        self.assertEqual(to_ranges([1, 2, 3, 7, 9, 10]), [(1, 3), (7, 7), (9, 10)])

    def test_unicode_range_format(self):
        self.assertEqual(
            format_unicode_range([0x4E00, 0x4E01, 0x4E03]),
            "U+4E00-4E01, U+4E03",
        )

    def test_unicode_range_empty(self):
        self.assertEqual(format_unicode_range([]), "")


if __name__ == "__main__":
    unittest.main()
