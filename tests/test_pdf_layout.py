"""Regression tests for the measured, collision-free page layout.

These tests are the contract the layout must keep:

* the text is typographically cleaned *before* it is measured,
* measurements come from the real font, not from a word count,
* multi-column pages are detected, so a list of illustrations cannot grow from
  one column into the other,
* no two writing boxes overlap and nothing is written over an illustration,
* every block survives — a tight page shrinks or moves a block, it never drops
  it.
"""
from __future__ import annotations

import pathlib
import unittest

from trarium import pdf_layout as L
from trarium.fa_style import normalize

FONT_PATH = pathlib.Path(__file__).resolve().parent.parent / "trarium" / "fonts" / "Sahel.ttf"
FONT = L.load_font(str(FONT_PATH)) if FONT_PATH.exists() else None
PAGE = (0.0, 0.0, 420.0, 595.0)


def entry(text, box, size=9.5):
    return L.LayoutItem(text=text, cover=box, base_size=size, lang="fa")


class TypographyBeforeMeasurementTests(unittest.TestCase):
    def test_the_three_samples_are_fixed(self):
        self.assertIn("می‌تواند", normalize("او می تواند بیاید"))
        self.assertIn("کتاب‌ها", normalize("کتاب ها روی میز است"))
        self.assertIn("بزرگ‌تر", normalize("خانهٔ بزرگ تر"))

    def test_prepare_text_runs_the_same_cleanup(self):
        self.assertEqual(L.prepare_text("او می تواند بیاید"), "او می‌تواند بیاید")

    def test_plan_page_normalizes_before_measuring(self):
        item = entry("او می تواند بیاید و کتاب ها را ببرد", (20.0, 100.0, 200.0, 114.0))
        L.plan_page([item], PAGE, FONT)
        self.assertIn("می‌تواند", item.text)
        self.assertIn("کتاب‌ها", item.text)
        self.assertNotIn("می تواند", item.text)


class MeasurementTests(unittest.TestCase):
    def test_longer_text_needs_more_height(self):
        if FONT is None:
            self.skipTest("font missing")
        short = L.measure("سلام", FONT, 10.0, 200.0)
        longer = L.measure("این جملهٔ بلندتر است و باید در چند خط بنشیند", FONT, 10.0, 120.0)
        self.assertEqual(short.lines, 1)
        self.assertGreaterEqual(longer.lines, 2)
        self.assertGreater(longer.height, short.height)

    def test_measured_lines_never_exceed_the_width(self):
        if FONT is None:
            self.skipTest("font missing")
        text = "شوالیه به جنگل رفت و کنار دریاچه ایستاد و مدت زیادی به آب گوش داد"
        for line in L.wrap_lines(text, FONT, 9.0, 150.0):
            self.assertLessEqual(L.text_width(line, FONT, 9.0), 150.0 + 0.5)

    def test_fit_size_shrinks_to_the_available_height(self):
        if FONT is None:
            self.skipTest("font missing")
        text = "یک پاراگراف فارسی که در کادر کوچک جا نمی‌شود و باید کوچک‌تر شود"
        fitted = L.fit_size(text, FONT, 120.0, 30.0, 12.0, min_size=6.0)
        self.assertLess(fitted.size, 12.0)
        self.assertLessEqual(fitted.height, 30.0 + 0.01)


class ColumnTests(unittest.TestCase):
    def test_two_columns_are_found(self):
        boxes = [
            (30.0, 100.0, 190.0, 112.0), (30.0, 116.0, 190.0, 128.0),
            (220.0, 100.0, 390.0, 112.0), (220.0, 116.0, 390.0, 128.0),
        ]
        bands = L.column_bands(boxes)
        self.assertEqual(len(bands), 2)
        self.assertLessEqual(bands[0][1], bands[1][0])

    def test_a_single_column_page_is_one_band(self):
        boxes = [(40.0, 100.0 + i * 20, 380.0, 112.0 + i * 20) for i in range(4)]
        self.assertEqual(len(L.column_bands(boxes)), 1)

    def test_scattered_bubbles_are_not_two_columns(self):
        # A comic page: speech bubbles at unrelated x positions. Splitting this
        # page moved a translation out of its bubble.
        boxes = [
            (60.0, 80.0, 150.0, 110.0), (300.0, 70.0, 380.0, 100.0),
            (80.0, 200.0, 140.0, 240.0), (330.0, 180.0, 400.0, 210.0),
            (90.0, 330.0, 170.0, 360.0), (310.0, 320.0, 395.0, 350.0),
        ]
        self.assertEqual(len(L.column_bands(boxes)), 1)

    def test_blocks_without_a_shared_edge_are_not_a_column(self):
        boxes = [
            (30.0, 100.0, 90.0, 112.0), (44.0, 130.0, 120.0, 142.0),
            (230.0, 100.0, 300.0, 112.0), (250.0, 140.0, 340.0, 152.0),
        ]
        self.assertEqual(len(L.column_bands(boxes)), 1)

    def test_a_spanning_title_belongs_to_no_single_column(self):
        boxes = [
            (30.0, 100.0, 190.0, 112.0), (30.0, 116.0, 190.0, 128.0),
            (220.0, 100.0, 390.0, 112.0), (220.0, 116.0, 390.0, 128.0),
        ]
        bands = L.column_bands(boxes)
        self.assertEqual(L.band_index((40.0, 40.0, 380.0, 60.0), bands), -1)
        self.assertEqual(L.band_index(boxes[0], bands), 0)
        self.assertEqual(L.band_index(boxes[2], bands), 1)


class PlanningTests(unittest.TestCase):
    def test_overlapping_list_entries_end_up_disjoint(self):
        # A list of illustrations: entries crowded together, boxes already
        # touching each other in the source. This is the page that used to show
        # fifteen overlapping lines.
        items = []
        for i in range(12):
            top = 60.0 + i * 18.0
            column = 0 if i % 2 == 0 else 1
            x0 = 30.0 if column == 0 else 220.0
            items.append(entry(
                f"صفحهٔ {12 + i} — تصویر شمارهٔ {i + 1} از کتاب با شرح کوتاه",
                (x0, top, x0 + 170.0, top + 20.0),
                size=10.0,
            ))
        report = L.plan_page(items, PAGE, FONT)
        plans = [item.plan for item in items]
        self.assertEqual(len(plans), 12)
        self.assertTrue(all(plan is not None for plan in plans))
        self.assertEqual(L.overlap_pairs(plans), [])
        self.assertEqual(report.overflow, 0)

    def test_a_paragraph_does_not_grow_over_the_next_one(self):
        items = [
            entry("پاراگراف اول با متن کوتاه", (40.0, 100.0, 380.0, 112.0), size=10.0),
            entry("پاراگراف دوم", (40.0, 130.0, 380.0, 142.0), size=10.0),
        ]
        L.plan_page(items, PAGE, FONT)
        self.assertEqual(L.overlap_pairs([i.plan for i in items]), [])
        self.assertLessEqual(items[0].plan[3], items[1].plan[1] + 0.01)

    def test_boxes_stay_inside_the_page(self):
        items = [
            entry("متن نزدیک لبهٔ صفحه که نباید بیرون بزند", (2.0, 10.0, 418.0, 24.0)),
            entry("متن پایین صفحه", (30.0, 580.0, 390.0, 594.0)),
        ]
        L.plan_page(items, PAGE, FONT)
        for item in items:
            x0, y0, x1, y1 = item.plan
            self.assertGreaterEqual(x0, PAGE[0])
            self.assertGreaterEqual(y0, PAGE[1])
            self.assertLessEqual(x1, PAGE[2])
            self.assertLessEqual(y1, PAGE[3])

    def test_an_illustration_is_never_written_on(self):
        art = (250.0, 60.0, 400.0, 260.0)
        item = entry("شرح تصویر که باید کنار نقاشی بماند", (260.0, 100.0, 390.0, 114.0))
        L.plan_page([item], PAGE, FONT, fixed=[art])
        self.assertEqual(L.overlap_area(item.plan, art), 0.0)

    def test_a_two_column_entry_cannot_enter_the_other_column(self):
        items = [
            entry("مدخل ستون چپ", (30.0, 100.0, 190.0, 112.0)),
            entry("مدخل ستون راست", (220.0, 100.0, 390.0, 112.0)),
        ]
        L.plan_page(items, PAGE, FONT)
        self.assertLessEqual(items[0].plan[2], 210.0)
        self.assertGreaterEqual(items[1].plan[0], 210.0 - 20.0)

    def test_a_long_translation_moves_down_not_up(self):
        # A heading whose source box is two lines but whose translation needs
        # three must not be printed above the running head, and must not be
        # shrunk to six points to squeeze into the gap.
        first = L.LayoutItem(text="یک پاراگراف که رشد می‌کند و فضای زیرش را می‌گیرد",
                             cover=(40.0, 100.0, 380.0, 126.0), base_size=11.0, limit=(40.0, 100.0, 380.0, 200.0))
        heading = L.LayoutItem(text="با همسر زیبایت به خوشی زندگی کن!",
                               cover=(40.0, 130.0, 380.0, 142.0), base_size=11.0,
                               limit=(40.0, 130.0, 380.0, 144.0))
        last = L.LayoutItem(text="پاراگراف پایانی",
                            cover=(40.0, 145.0, 380.0, 240.0), base_size=11.0)
        report = L.plan_page([first, heading, last], PAGE, FONT)
        self.assertEqual(L.overlap_pairs([first.plan, heading.plan, last.plan]), [])
        self.assertGreaterEqual(heading.plan[1], heading.cover[1] - 0.01)
        self.assertGreater(heading.size, 8.0)
        self.assertEqual(report.overflow, 0)

    def test_no_block_is_dropped_on_a_crowded_page(self):
        items = [
            entry(f"سطر شمارهٔ {i} در یک صفحهٔ شلوغ و تنگ", (40.0, 60.0 + i * 12.0, 380.0, 71.0 + i * 12.0), size=10.0)
            for i in range(30)
        ]
        report = L.plan_page(items, PAGE, FONT)
        self.assertEqual(len([i for i in items if i.plan]), 30)
        self.assertEqual(L.overlap_pairs([i.plan for i in items]), [])
        self.assertEqual(report.overflow, 0)

    def test_a_scanned_bubble_stays_inside_its_blank_area(self):
        cover = (100.0, 200.0, 200.0, 214.0)
        limit = (100.0, 186.0, 200.0, 228.0)
        item = L.LayoutItem(text="ترجمهٔ فارسی داخل حباب", cover=cover, base_size=9.0, limit=limit)
        L.plan_page([item], PAGE, FONT)
        self.assertLessEqual(item.plan[1], cover[1] + 0.01)
        self.assertGreaterEqual(item.plan[3], cover[3] - 0.01)
        self.assertLessEqual(item.plan[3], limit[3] + 0.01)

    def test_the_planned_size_is_reported_and_fits(self):
        if FONT is None:
            self.skipTest("font missing")
        item = entry("متن کوتاه", (40.0, 100.0, 120.0, 112.0), size=16.0)
        L.plan_page([item], PAGE, FONT)
        measured = L.measure(item.text, FONT, item.size, L.width_of(item.plan))
        self.assertLessEqual(measured.height, L.height_of(item.plan) + 0.01)
        self.assertGreater(item.size, 0)


if __name__ == "__main__":
    unittest.main()
