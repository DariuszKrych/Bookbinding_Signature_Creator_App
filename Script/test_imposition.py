"""Imposition tests. Run with:  python -m unittest Script.test_imposition -v

The core test derives the correct layout independently, by simulating the fold,
rather than by re-using the production formula. A test that reuses the formula it
is checking would agree with any bug it contains.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Script.print_formatting import (  # noqa: E402
    ColumnLayout,
    layout_problems,
    plan_signatures,
    signature_sides,
)


def fold_a_signature(sheets):
    """Where each book page lands, derived from physically folding paper.

    Nest `sheets` sheets and fold once. That yields 2*sheets leaves; sheet s
    (0 = outermost) supplies leaf s+1 and leaf 2*sheets-s. Leaf L shows page
    2L-1 on its recto and 2L on its verso. Returns one (left, right, is_back)
    per side, in printing order.
    """
    total_leaves = 2 * sheets
    sides = []
    for s in range(sheets):
        near_leaf = s + 1
        far_leaf = total_leaves - s
        near_recto, near_verso = 2 * near_leaf - 1, 2 * near_leaf
        far_recto, far_verso = 2 * far_leaf - 1, 2 * far_leaf
        # Front of the sheet: the near leaf's recto faces right of the fold.
        sides.append((far_verso, near_recto, False))
        # Turn the sheet over: the near leaf's verso is now left of the fold.
        sides.append((near_verso, far_recto, True))
    return sides


class TestFoldModel(unittest.TestCase):
    def test_single_sheet_matches_the_classic_booklet(self):
        self.assertEqual(
            fold_a_signature(1), [(4, 1, False), (2, 3, True)]
        )

    def test_sides_match_an_independent_fold_simulation(self):
        for sheets in range(1, 13):
            plan = plan_signatures(sheets * 4, sheets)[0]
            self.assertEqual(
                list(signature_sides(plan)), fold_a_signature(sheets), f"{sheets} sheets"
            )


class TestPlanSignatures(unittest.TestCase):
    def test_every_signature_uses_whole_sheets_and_covers_the_book_exactly(self):
        for sheets in range(1, 9):
            for book_pages in range(1, 400):
                plans = plan_signatures(book_pages, sheets)

                # Signatures tile the book back to back, with no gap or overlap.
                self.assertEqual(plans[0].first_book_page, 1)
                for a, b in zip(plans, plans[1:]):
                    self.assertEqual(b.first_book_page, a.last_book_page + 1)

                # Every signature is whole sheets, so every one has an even
                # number of sides. A half sheet cannot be printed or folded.
                for plan in plans:
                    self.assertEqual(plan.capacity % 4, 0)
                    self.assertEqual(plan.sides % 2, 0)
                    self.assertLessEqual(plan.sheets, sheets)
                    self.assertGreaterEqual(plan.sheets, 1)

                # The book fits, and is padded by less than one whole sheet.
                total = plans[-1].last_book_page
                self.assertGreaterEqual(total, book_pages)
                self.assertLess(total - book_pages, 4)

    def test_blank_padding_lands_at_the_back_of_the_book(self):
        # 209 source pages = 418 book pages, in 5-sheet (20 page) signatures.
        plans = plan_signatures(418, 5)
        self.assertEqual(len(plans), 21)
        self.assertEqual([p.sheets for p in plans[:20]], [5] * 20)
        self.assertEqual(plans[20].sheets, 5)  # 18 leftover pages still need 5 sheets
        self.assertEqual(plans[20].first_book_page, 401)

        placed = [bp for l, r, _ in signature_sides(plans[20]) for bp in (l, r)]
        padding = sorted(bp for bp in placed if bp > 418)
        self.assertEqual(padding, [419, 420])  # the two blanks are the final pages

    def test_a_short_last_signature_shrinks_to_fewer_sheets(self):
        plans = plan_signatures(4 * 5 + 4, 5)  # one full signature plus 4 pages
        self.assertEqual([p.sheets for p in plans], [5, 1])

    def test_a_book_smaller_than_one_signature(self):
        plans = plan_signatures(6, 5)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].sheets, 2)  # 6 pages needs 2 sheets, not 1.5
        self.assertEqual(list(signature_sides(plans[0])), fold_a_signature(2))

    def test_rejects_nonsense_input(self):
        with self.assertRaises(ValueError):
            plan_signatures(100, 0)
        with self.assertRaises(ValueError):
            plan_signatures(0, 5)


class TestLayoutValidation(unittest.TestCase):
    A4_LANDSCAPE_WIDTH = 841.92

    def test_the_shipped_default_layout_is_valid(self):
        self.assertEqual(layout_problems(ColumnLayout(), self.A4_LANDSCAPE_WIDTH), [])

    def test_rejects_a_left_column_that_crosses_the_fold(self):
        layout = ColumnLayout(page_margin_in=0.5, column_gap_in=0.99, column_width_in=6.0)
        self.assertTrue(
            any("fold" in p for p in layout_problems(layout, self.A4_LANDSCAPE_WIDTH))
        )

    def test_rejects_a_right_column_that_crosses_the_fold(self):
        layout = ColumnLayout(page_margin_in=0.5, column_gap_in=0.1, column_width_in=4.85)
        self.assertTrue(
            any("fold" in p for p in layout_problems(layout, self.A4_LANDSCAPE_WIDTH))
        )

    def test_rejects_columns_wider_than_the_page(self):
        layout = ColumnLayout(page_margin_in=3.0, column_gap_in=0.99, column_width_in=4.85)
        self.assertTrue(layout_problems(layout, self.A4_LANDSCAPE_WIDTH))


if __name__ == "__main__":
    unittest.main(verbosity=2)
