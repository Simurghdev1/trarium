"""Build a Persian copy of a GameMaker 8.0 game from a translation map.

Translation file: JSON list of entries
  {"id": "<literal id from strings.extract>", "fa": "<Persian, codes kept>",
   "mode": "writer" | "draw_text",        # typewriter page or a static draw_text label
   "font": "fnt_maintext",                # GM8 font asset the text is drawn with
   "width": 202, "max_lines": 3,          # writer: wrap width (1x px) and line budget
   "spacing": 8,                          # writer: fixed ASCII advance of that writer
   "align": "center" | "right:<x>"}       # draw_text: where to anchor the label
Only literals whose id (incl. source hash) matches are replaced; everything else
stays byte-identical.  Fonts listed by the entries get the Persian glyph forms.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from .exe import GM8Exe, Script
from .fa_text import CODE_RE, GlyphMap, needed_glyphs, prepare
from .font import GlyphStyle, add_glyphs, original_baseline
from .gml_patch import HELPER_NAME, HELPER_SRC, MARK, draw_text_call, is_typewriter, patch_typewriter
from .strings import code_blocks, scan_literals, short_hash

FONT_TTF = str(Path(__file__).resolve().parents[2] / "fonts" / "Sahel.ttf")
FA_PX = 30     # Persian size at 2x (view-scaled rooms, drawn at 0.5)
FA_PX_1X = 17  # Persian size for 1:1 rooms (drawn unscaled)


class Measure:
    def __init__(self, font, spacing, scale=0.5):
        self.f, self.spacing, self.scale = font, spacing, scale

    def width(self, prepared: str, gm: GlyphMap) -> float:
        w = 0.0
        for c in CODE_RE.sub("", prepared):
            if ord(c) >= 0x80:
                w += self.f.dmap[gm.map[c] * 6 + 4] * self.scale
            elif c == " ":
                w += self.spacing / 2
            else:
                w += self.spacing
        return w


def wrap_page(text: str, meas: Measure, gm: GlyphMap, width: float) -> list[str]:
    """Greedy word wrap of one page (logical order) -> list of lines; keeps manual '&'."""
    lines = []
    for para in text.split("&"):
        bullet = para.lstrip().startswith("*") or re.match(r"^(\\[A-Za-z][0-9A-Za-z]?)*\* ", para)
        indent = "  " if bullet else ""
        words = para.split(" ")
        cur = ""
        for w in words:
            cand = w if cur == "" else cur + " " + w
            if cur and meas.width(prepare(cand), gm) > width:
                lines.append(cur)
                cur = indent + w
            else:
                cur = cand
        lines.append(cur)
    return lines


def _preview(d: str, n: int, font, enc: bytes, ent: dict, scale: float) -> None:
    from PIL import Image
    from .preview import draw_rtl
    Path(d).mkdir(parents=True, exist_ok=True)
    w = ent.get("width", 200)
    sp = ent.get("spacing", 8)
    vs = 20 if ent.get("font") == "fnt_plain" else 18
    dark = ent.get("font") != "fnt_plain"
    img = Image.new("RGB", ((w + 24) * 2, (ent.get("max_lines", 3) * vs + 14) * 2),
                    (0, 0, 0) if dark else (255, 255, 255))
    draw_rtl(img, font, enc, w + 12, 6, sp, vs, (255, 255, 255) if dark else (0, 0, 0), scale=scale)
    img.save(Path(d) / ("%02d.png" % n))


def build(exe_in: str, tr_path: str, exe_out: str, report_path: str | None = None, log=print,
          preview_dir: str | None = None) -> dict:
    g = GM8Exe(Path(exe_in).read_bytes())
    entries = json.loads(Path(tr_path).read_text(encoding="utf-8"))
    by_id = {e["id"]: e for e in entries if e.get("fa")}
    fonts = g.section("fonts")
    font_idx = {f.name.decode(): i for i, f in fonts.items()}

    # 1) shape everything once to collect glyph forms
    gm = GlyphMap()
    for e in by_id.values():
        gm.add(needed_glyphs(prepare(e["fa"].replace("&", " "))))
    log("persian glyph forms: %d / 128 slots" % len(gm.map))
    used_fonts = sorted({e.get("font", "fnt_maintext") for e in by_id.values()})
    scale_of, px_of, crisp_of = {}, {}, {}
    for e in by_id.values():
        fn = e.get("font", "fnt_maintext")
        scale_of[fn] = 0.5 if e.get("glyph_scale") == 0.5 else 1.0
        px_of[fn] = e.get("glyph_px", FA_PX_1X if scale_of[fn] == 1.0 else FA_PX)
        crisp_of[fn] = e.get("crisp", scale_of[fn] == 0.5)
    for name in used_fonts:
        i = font_idx[name]
        f = fonts.get(i)
        base = original_baseline(f)
        thr = 128 if crisp_of[name] else 0
        if scale_of[name] == 1.0:   # glyphs at the font's own pixel size, drawn unscaled
            st = GlyphStyle(FONT_TTF, px_of[name], base, f.glyph(65)[3] + 8, threshold=thr)
        else:                       # 2x glyphs drawn at 0.5 (only for engines that render views at port size)
            st = GlyphStyle(FONT_TTF, px_of[name], 2 * base, 2 * f.glyph(65)[3] + 12, threshold=thr)
        add_glyphs(f, gm.map, st)
        fonts.put(i, f)
        log("font %s: +%d glyphs, atlas %dx%d" % (name, len(gm.map), f.map_w, f.map_h))

    # 2) replace literals
    stats = defaultdict(int)
    problems = []
    applied = {}
    typewriters = 0
    helper_needed = False
    for where, code, setter in code_blocks(g):
        lits = list(scan_literals(code))
        new = bytearray()
        pos = 0
        changed = False
        for n, (s, e_, q, t) in enumerate(lits):
            lid = "%s#%d@%s" % (where, n, short_hash(t))
            ent = by_id.get(lid)
            if not ent:
                continue
            fa = ent["fa"]
            font = fonts.get(font_idx[ent.get("font", "fnt_maintext")])
            mode = ent.get("mode", "writer")
            if mode == "writer":
                meas = Measure(font, ent.get("spacing", 8), scale_of[ent.get("font", "fnt_maintext")])
                lines = wrap_page(fa, meas, gm, ent.get("width", 200))
                if ent.get("center"):
                    lines = [" " * max(0, round((ent.get("width", 200) - meas.width(prepare(l), gm)) / 2
                                                / (meas.spacing / 2))) + l for l in lines]
                worst = max(meas.width(prepare(l), gm) for l in lines)
                if len(lines) > ent.get("max_lines", 3) or worst > ent.get("width", 200) + 0.5:
                    problems.append({"id": lid, "lines": len(lines), "widest": worst})
                enc = MARK + gm.encode(prepare("&".join(lines)))
                if q in enc:
                    raise ValueError("quote char inside translation " + lid)
                new += code[pos:s] + q + enc + q
                pos = e_
                applied[lid] = {"lines": lines}
                if preview_dir:
                    _preview(preview_dir, len(applied), font, enc, ent, meas.scale)
            else:
                call = draw_text_call(code, s, e_)
                if not call:
                    problems.append({"id": lid, "error": "not a draw_text label"})
                    continue
                cs, ce, xe, ye = call
                meas = Measure(font, font.glyph(65)[4])
                prep = prepare(fa)
                fa_w = meas.width(prep, gm)
                en_w = sum(font.glyph(b)[4] for b in t)
                align = ent.get("align", "center")
                if align.startswith("right:"):
                    right = b"%d" % int(align.split(":")[1])
                else:
                    right = b"(%s) + %d" % (xe, round(en_w / 2 + fa_w / 2))
                new += code[pos:cs] + HELPER_NAME + b"(" + right + b", " + ye + b", " + q + gm.encode(prep) + q + b")"
                pos = ce
                helper_needed = True
                applied[lid] = {"x": right.decode(), "w": fa_w}
            changed = True
            stats[mode] += 1
        if changed:
            new += code[pos:]
            code = bytes(new)
        if is_typewriter(code):
            code = patch_typewriter(code, fonts_1x=[n.encode() for n, sc in scale_of.items() if sc == 1.0])
            typewriters += 1
            changed = True
        if changed:
            setter(code)
    if helper_needed:
        sc = g.section("scripts")
        sc.chunks.append(b"")
        sc.put(len(sc.chunks) - 1, Script(HELPER_NAME, 800, HELPER_SRC))
    missing = sorted(set(by_id) - set(applied))
    out = g.build()
    Path(exe_out).write_bytes(out)
    rep = {"translated": len(applied), "requested": len(by_id), "missing_ids": missing,
           "by_mode": dict(stats), "typewriters_patched": typewriters, "glyph_forms": len(gm.map),
           "fonts": used_fonts, "layout_problems": problems, "out_size": len(out),
           "lines": {k: v.get("lines") for k, v in applied.items()}}
    if report_path:
        Path(report_path).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    log("translated %d/%d, typewriters patched %d, layout problems %d, missing %d" %
        (len(applied), len(by_id), typewriters, len(problems), len(missing)))
    return rep
