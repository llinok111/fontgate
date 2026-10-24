"""Synthetic font fixtures.

Every test builds its own font from scratch so the suite does not depend on
which system fonts happen to be installed.  That matters on CI and on machines
where Microsoft fonts are absent.
"""

from __future__ import annotations

import os
import sys
from typing import Iterable, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

GLYPH_BOX = ((60, 0), (60, 700), (540, 700), (540, 0))


def _draw(pen: TTGlyphPen) -> None:
    pen.moveTo(GLYPH_BOX[0])
    for point in GLYPH_BOX[1:]:
        pen.lineTo(point)
    pen.closePath()


def glyph_name(cp: int) -> str:
    return "uni%04X" % cp


def build_font(
    path: str,
    codepoints: Sequence[int],
    family: str = "Test Sans",
    style: str = "Regular",
    weight: int = 400,
    empty: Iterable[int] = (),
    width: int = 600,
    upem: int = 1000,
) -> str:
    """Write a minimal TrueType font covering *codepoints*.

    Code points listed in *empty* get a glyph with zero contours: present in
    cmap, invisible when rendered.  That is the state a cmap-only coverage
    check cannot see.
    """
    order = [".notdef"]
    cmap = {}
    for cp in sorted(set(codepoints)):
        name = glyph_name(cp)
        order.append(name)
        cmap[cp] = name

    builder = FontBuilder(upem, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(cmap)

    empty_names = {glyph_name(cp) for cp in empty}
    glyphs = {}
    for name in order:
        pen = TTGlyphPen(None)
        if name != ".notdef" and name not in empty_names:
            _draw(pen)
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (width, 60) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": family, "styleName": style})
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
        usWeightClass=weight,
    )
    builder.setupPost()
    builder.save(path)
    return path


def add_liga(font_path: str, source_cp: int = 0x41, target_cp: int = 0x42) -> None:
    """Install a single substitution so layout tables exist to be checked."""
    from fontTools.feaLib.builder import addOpenTypeFeaturesFromString

    font = TTFont(font_path)
    try:
        addOpenTypeFeaturesFromString(
            font,
            "languagesystem DFLT dflt;\n"
            "feature liga { sub %s by %s; } liga;\n"
            % (glyph_name(source_cp), glyph_name(target_cp)),
        )
        font.save(font_path)
    finally:
        font.close()


def to_woff2(src: str, dst: str) -> str:
    font = TTFont(src)
    try:
        font.flavor = "woff2"
        font.save(dst)
    finally:
        font.close()
    return dst


def subset_font(
    src: str,
    dst: str,
    text: str,
    flavor: Optional[str] = "woff2",
    options_hook=None,
) -> str:
    """Produce a subset with the same library the upstream CLI uses."""
    from fontTools import subset

    options = subset.Options()
    options.flavor = flavor
    options.ignore_missing_unicodes = True
    if options_hook is not None:
        options_hook(options)
    font = subset.load_font(src, options)
    try:
        subsetter = subset.Subsetter(options=options)
        subsetter.populate(text=text)
        subsetter.subset(font)
        subset.save_font(font, dst, options)
    finally:
        font.close()
    return dst


def set_advance(font_path: str, cp: int, width: int) -> None:
    """Tamper with one advance width, leaving cmap untouched."""
    font = TTFont(font_path)
    try:
        name = font.getBestCmap()[cp]
        _, lsb = font["hmtx"][name]
        font["hmtx"][name] = (width, lsb)
        font.save(font_path)
    finally:
        font.close()


def set_family(font_path: str, family: str, style: Optional[str] = None) -> None:
    font = TTFont(font_path)
    try:
        name_table = font["name"]
        name_table.setName(family, 1, 3, 1, 0x409)
        name_table.setName(family, 4, 3, 1, 0x409)
        if style is not None:
            name_table.setName(style, 2, 3, 1, 0x409)
        font.save(font_path)
    finally:
        font.close()


def write_text(path: str, text: str, encoding: str = "utf-8") -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding=encoding, newline="\n") as handle:
        handle.write(text)
    return path


def font_paths(directory: str) -> list:
    return sorted(
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if name.endswith((".ttf", ".otf", ".woff", ".woff2"))
    )
