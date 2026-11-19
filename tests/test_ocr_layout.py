"""Geometry tests for the shared OCR layout layer (no PDF, no network)."""
import unittest

from trarium import ocr_layout as L


def blk(x0, y0, x1, y1, text, **extra):
    data = {"bbox": (x0, y0, x1, y1), "text": text}
    data.update(extra)
    return data


class ReadingOrderTests(unittest.TestCase):
    def test_two_columns_are_read_column_by_column(self):
        blocks = [
            blk(20, 100, 190, 160, "left one"),
            blk(210, 100, 380, 160, "right one"),
            blk(20, 180, 190, 240, "left two"),
            blk(210, 180, 380, 240, "right two"),
        ]
        order = [b["text"] for b in L.reading_order(blocks, 400)]
        self.assertEqual(order, ["left one", "left two", "right one", "right two"])

    def test_single_column_stays_top_to_bottom(self):
        blocks = [
            blk(20, 20, 380, 40, "title"),
            blk(20, 60, 380, 100, "body one"),
            blk(20, 110, 380, 150, "body two"),
        ]
        order = [b["text"] for b in L.reading_order(blocks, 400)]
        self.assertEqual(order, ["title", "body one", "body two"])

    def test_side_by_side_bubbles_are_not_interleaved(self):
        blocks = [blk(20, 20, 180, 90, "left bubble"), blk(220, 30, 380, 100, "right bubble")]
        order = [b["text"] for b in L.reading_order(blocks, 400)]
        self.assertEqual(order, ["left bubble", "right bubble"])


class MergeTests(unittest.TestCase):
    def test_ocr_lines_merge_into_a_paragraph(self):
        blocks = [
            blk(50, 50, 300, 62, "first line"),
            blk(50, 64, 310, 76, "second line"),
            blk(50, 100, 180, 112, "new paragraph"),
        ]
        merged = L.merge_lines(blocks, 400)
        self.assertEqual(len(merged), 2)
        self.assertIn("second line", merged[0]["text"])
        # The merged box keeps the original line height, so the writer does not
        # derive a giant font size from the paragraph box.
        self.assertLess(merged[0]["line_height"], 20)

    def test_two_stacked_bubbles_stay_separate(self):
        blocks = [
            blk(60, 40, 200, 100, "bubble one text", line_count=3),
            blk(60, 108, 200, 168, "bubble two text", line_count=3),
        ]
        merged = L.merge_lines(blocks, 400)
        self.assertEqual(len(merged), 2)

    def test_columns_are_never_merged(self):
        blocks = [
            blk(20, 40, 190, 120, "left column paragraph"),
            blk(210, 40, 380, 120, "right column paragraph"),
        ]
        self.assertEqual(len(L.merge_lines(blocks, 400)), 2)

    def test_title_and_body_are_not_merged(self):
        blocks = [
            blk(20, 40, 380, 70, "A Big Chapter Title"),
            blk(20, 72, 380, 84, "and the much smaller body text"),
        ]
        self.assertEqual(len(L.merge_lines(blocks, 400)), 2)


class FontSizeTests(unittest.TestCase):
    def test_paragraph_box_does_not_become_giant_type(self):
        # Six printed lines inside a 40pt tall box must stay ~7pt, not 10pt+.
        size = L.estimate_font_size((50, 100, 350, 140), "word " * 40)
        self.assertLessEqual(size, 9.0)
        self.assertGreaterEqual(size, 6.0)

    def test_single_line_box_keeps_its_size(self):
        size = L.estimate_font_size((50, 100, 200, 112), "One printed line")
        self.assertTrue(8.0 <= size <= 12.0)

    def test_guess_line_count(self):
        self.assertEqual(L.guess_line_count((0, 0, 10, 10), "ab"), 1)
        self.assertGreater(L.guess_line_count((0, 0, 300, 120), "word " * 60), 1)


class QualityTests(unittest.TestCase):
    def test_page_sized_and_outside_boxes_are_flagged(self):
        page = (0, 0, 400, 600)
        self.assertIn("page-sized", L.block_flags(blk(0, 0, 400, 600, "whole page"), page))
        self.assertIn("outside", L.block_flags(blk(500, 700, 560, 730, "off page"), page))
        self.assertIn("tiny", L.block_flags(blk(10, 10, 10.5, 10.5, "x"), page))

    def test_text_on_text_is_flagged(self):
        page = (0, 0, 400, 600)
        a = blk(50, 50, 300, 100, "one long sentence")
        b = blk(60, 60, 290, 95, "another long sentence")
        self.assertIn("text-on-text", L.block_flags(b, page, [a]))

    def test_needs_second_pass_for_empty_or_page_sized(self):
        page = (0, 0, 400, 600)
        self.assertTrue(L.needs_second_pass([], page))
        self.assertTrue(L.needs_second_pass([blk(0, 0, 400, 600, "all of it")], page))
        self.assertFalse(L.needs_second_pass([blk(50, 50, 300, 90, "a clean paragraph")], page))

    def test_dedupe_keeps_the_longer_copy(self):
        blocks = [
            blk(50, 50, 300, 90, "a repeated paragraph"),
            blk(51, 51, 299, 89, "a repeated"),
        ]
        self.assertEqual(len(L.dedupe_blocks(blocks)), 1)

    def test_merge_passes_recovers_missed_text(self):
        primary = [blk(50, 50, 300, 90, "found text")]
        secondary = [blk(50, 50, 300, 90, "found text"), blk(50, 200, 300, 230, "text the first pass lost")]
        merged = L.merge_passes(primary, secondary, (0, 0, 400, 600))
        self.assertEqual(len(merged), 2)


class FreeSpaceTests(unittest.TestCase):
    page = (0, 0, 400, 600)

    def test_neighbour_blocks_limit_growth(self):
        box = (50, 100, 300, 130)
        below = blk(50, 140, 300, 170, "next paragraph")
        candidates = L.candidate_rects(box, self.page, [below])
        self.assertEqual(candidates[0], (50, 100, 300, 130))
        self.assertTrue(all(rect[3] <= 140 for rect in candidates))

    def test_first_candidate_is_the_original_box(self):
        candidates = L.candidate_rects((40, 40, 200, 60), self.page, [])
        self.assertEqual(candidates[0], (40, 40, 200, 60))

    def test_bubble_does_not_grow_over_neighbour(self):
        bubble = (60, 60, 180, 120)
        beside = blk(200, 60, 320, 120, "other bubble")
        candidates = L.candidate_rects(bubble, self.page, [beside])
        self.assertTrue(all(rect[2] <= 200 for rect in candidates))

    def test_raster_share(self):
        self.assertAlmostEqual(L.raster_share((10, 10, 110, 110), [(0, 0, 400, 600)]), 1.0)
        self.assertAlmostEqual(L.raster_share((10, 10, 110, 110), []), 0.0)

    def test_background_median_ignores_white_assumption(self):
        beige = [(0.96, 0.93, 0.84)] * 5
        self.assertEqual(L.background_from_samples(beige), (0.96, 0.93, 0.84))


if __name__ == "__main__":
    unittest.main()
