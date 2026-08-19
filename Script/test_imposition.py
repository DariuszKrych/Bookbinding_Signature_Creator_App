"""Imposition tests. Run with:  python -m unittest Script.test_imposition -v

Two rules keep these tests honest:

  * The core fold test derives the correct layout independently, by simulating
    the fold, rather than by re-using the production formula. A test that reuses
    the formula it is checking would agree with any bug it contains.
  * The page-size tests do not inspect the imposition code's own arithmetic.
    They build a real PDF, run the real conversion, and then ask *pypdf* where
    the text ended up, by reading the transformation matrices out of the finished
    file. Nothing in `print_formatting` gets a say in the answer.
"""

import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pypdf  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfgen.canvas import Canvas  # noqa: E402

import main  # noqa: E402

from Script.paper_sizes import (  # noqa: E402
    BOOK_PAGE_FAMILIES,
    BOOK_PAGE_SIZES,
    CENTIMETRES,
    INCHES,
    MILLIMETRES,
    MM_PER_INCH,
    PT_PER_INCH,
    SHEET_FAMILIES,
    SHEET_SIZES,
    book_page_table,
    describe_size,
    find_paper_size,
    identify_size,
    paper_size_table,
    parse_paper_size,
    sizes_by_family,
    smallest_sheet_for,
)
from Script.print_formatting import (  # noqa: E402
    ACTUAL_SIZE,
    FIT,
    FONT_NAME,
    FONT_SIZE,
    ColumnLayout,
    PageGeometry,
    SheetFit,
    apply_matrix,
    compose,
    inspect_book,
    invert,
    layout_problems,
    layout_warnings,
    plan_signatures,
    sheet_problems,
    sheet_warnings,
    signature_sides,
    split_to_signatures,
    stamp_book,
)

A4_LANDSCAPE_PT = (841.89, 595.28)
LETTER_LANDSCAPE_PT = (11.0 * PT_PER_INCH, 8.5 * PT_PER_INCH)


# --------------------------------------------------------------------------
# Building and reading test PDFs
# --------------------------------------------------------------------------


MARK_IN_COLUMN_PT = (20.0, 30.0)  # where each mark sits inside its own column


def source_coordinates(visible_point, box_width_pt, box_height_pt, rotation):
    """Where to draw so that a mark shows up at `visible_point` on screen.

    Derived from what /Rotate means - "rotate the page clockwise by this many
    degrees when displaying it" - and deliberately *not* from
    `PageGeometry.to_visible`, so the two can disagree and be caught.
    """
    visible_x, visible_y = visible_point
    if rotation == 90:
        return (box_width_pt - visible_y, visible_x)
    if rotation == 180:
        return (box_width_pt - visible_x, box_height_pt - visible_y)
    if rotation == 270:
        return (visible_y, box_height_pt - visible_x)
    return (visible_x, visible_y)


def visible_coordinates(source_point, box_width_pt, box_height_pt, rotation):
    """The inverse of `source_coordinates`, written out rather than computed."""
    x, y = source_point
    if rotation == 90:
        return (y, box_width_pt - x)
    if rotation == 180:
        return (box_width_pt - x, box_height_pt - y)
    if rotation == 270:
        return (box_height_pt - y, x)
    return (x, y)


def build_source_pdf(
    path, source_pages, page_size_pt=A4_LANDSCAPE_PT, rotation=0, origin_pt=(0.0, 0.0),
    first_book_page=1,
):
    """A 2-column book whose every book page carries a mark at a known place.

    Book page N is marked "BPn" drawn at (20, 30) points from the bottom-left of
    its own column, as the page is *displayed*. That single anchor is enough to
    pin down the scale and both offsets of whatever the imposition did to it.

    `rotation` and `origin_pt` reproduce the two things real PDFs do that a
    freshly generated one never does: a /Rotate that only a reader applies, and a
    mediabox that does not start at (0, 0).
    """
    visible_width_pt, visible_height_pt = page_size_pt
    turned = rotation in (90, 270)
    box_width_pt = visible_height_pt if turned else visible_width_pt
    box_height_pt = visible_width_pt if turned else visible_height_pt
    offset_x, offset_y = origin_pt
    mark_x, mark_y = MARK_IN_COLUMN_PT

    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(box_width_pt, box_height_pt))
    for index in range(source_pages):
        canvas.setFont("Helvetica", 9)
        book_pages = (first_book_page + 2 * index, first_book_page + 2 * index + 1)
        for column, book_page in enumerate(book_pages):
            visible_point = (column * visible_width_pt / 2 + mark_x, mark_y)
            x, y = source_coordinates(visible_point, box_width_pt, box_height_pt, rotation)
            canvas.saveState()
            # The content moves with the mediabox, exactly as it would in a file
            # that was authored on offset paper.
            canvas.translate(x + offset_x, y + offset_y)
            canvas.rotate(-rotation)
            canvas.drawString(0, 0, f"BP{book_page}")
            canvas.restoreState()
        canvas.showPage()
    canvas.save()
    buffer.seek(0)

    writer = pypdf.PdfWriter(clone_from=buffer)
    for page in writer.pages:
        if rotation:
            page.rotation = rotation
        if offset_x or offset_y:
            page.mediabox = pypdf.generic.RectangleObject(
                (offset_x, offset_y, offset_x + box_width_pt, offset_y + box_height_pt)
            )
    with open(path, "wb") as stream:
        writer.write(stream)
    return Path(path)


def bounding_box(matrix, rect):
    left, bottom, right, top = rect
    corners = [
        apply_matrix(matrix, point)
        for point in ((left, bottom), (right, bottom), (right, top), (left, top))
    ]
    xs = [x for x, _ in corners]
    ys = [y for _, y in corners]
    return (min(xs), min(ys), max(xs), max(ys))


UNCLIPPED = (-1e9, -1e9, 1e9, 1e9)
PAINT_OPERATORS = (b"n", b"f", b"F", b"f*", b"S", b"s", b"B", b"B*", b"b", b"b*")


def _intersect(first, second):
    return (
        max(first[0], second[0]), max(first[1], second[1]),
        min(first[2], second[2]), min(first[3], second[3]),
    )


def _text_of(operand):
    if isinstance(operand, (bytes, bytearray)):
        return operand.decode("latin-1", "replace")
    return str(operand)


def read_text_runs(page):
    """[(label, (x, y), visible)] for every piece of text a page draws.

    A tiny PDF interpreter, because the question "does this text reach the paper"
    needs the clipping path, and pypdf's text extraction ignores clipping - it
    would happily report the column that was masked off. Clips nest, so they are
    intersected down the graphics state stack rather than merely collected.

    Only the operators these files actually contain are handled, which is the
    point: it is short enough to read and owes nothing to the code under test.
    """
    stream = page.get_contents()
    if stream is None:
        return []

    matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    clip = UNCLIPPED
    saved = []
    rectangle = None
    pending_clip = False
    text_matrix = line_matrix = matrix
    runs = []

    for operands, operator in stream.operations:
        if operator == b"q":
            saved.append((matrix, clip))
        elif operator == b"Q":
            matrix, clip = saved.pop() if saved else ((1.0, 0.0, 0.0, 1.0, 0.0, 0.0), UNCLIPPED)
        elif operator == b"cm":
            # A `cm` premultiplies: the new matrix applies before the old one.
            matrix = compose(tuple(float(value) for value in operands), matrix)
        elif operator == b"re":
            x, y, width, height = (float(value) for value in operands)
            rectangle = (min(x, x + width), min(y, y + height),
                         max(x, x + width), max(y, y + height))
        elif operator in (b"W", b"W*"):
            pending_clip = True
        elif operator in PAINT_OPERATORS:
            if pending_clip and rectangle is not None:
                clip = _intersect(clip, bounding_box(matrix, rectangle))
            rectangle, pending_clip = None, False
        elif operator == b"BT":
            text_matrix = line_matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        elif operator == b"Tm":
            text_matrix = line_matrix = tuple(float(value) for value in operands)
        elif operator in (b"Td", b"TD"):
            shift = (1.0, 0.0, 0.0, 1.0, float(operands[0]), float(operands[1]))
            text_matrix = line_matrix = compose(shift, line_matrix)
        elif operator in (b"Tj", b"TJ", b"'", b'"'):
            if operator == b"TJ":
                label = "".join(
                    _text_of(item) for item in operands[0]
                    if isinstance(item, (bytes, bytearray, str))
                ).strip()
            else:
                label = _text_of(operands[-1]).strip()
            if label:
                point = apply_matrix(compose(text_matrix, matrix), (0.0, 0.0))
                visible = (
                    clip[0] - 1e-6 <= point[0] <= clip[2] + 1e-6
                    and clip[1] - 1e-6 <= point[1] <= clip[3] + 1e-6
                )
                runs.append((label, point, visible))
    return runs


def clip_rectangles(page):
    """Every clipping rectangle set on a page, in that page's own coordinates."""
    stream = page.get_contents()
    if stream is None:
        return []
    matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    saved = []
    rectangle = None
    pending_clip = False
    found = []
    for operands, operator in stream.operations:
        if operator == b"q":
            saved.append(matrix)
        elif operator == b"Q":
            matrix = saved.pop() if saved else (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        elif operator == b"cm":
            matrix = compose(tuple(float(value) for value in operands), matrix)
        elif operator == b"re":
            x, y, width, height = (float(value) for value in operands)
            rectangle = (x, y, x + width, y + height)
        elif operator in (b"W", b"W*"):
            pending_clip = True
        elif operator in PAINT_OPERATORS:
            if pending_clip and rectangle is not None:
                found.append(bounding_box(matrix, rectangle))
            rectangle, pending_clip = None, False
    return found


def labels_on(page):
    """{label: (x, y)} for all text drawn, including text that is clipped away."""
    return {label: point for label, point, _visible in read_text_runs(page)}


def visible_labels_on(page):
    """{label: (x, y)} for the text that survives clipping and reaches the paper."""
    return {label: point for label, point, visible in read_text_runs(page) if visible}


def marks_on(page):
    return {k: v for k, v in labels_on(page).items() if k.startswith("BP")}


def visible_marks_on(page):
    return {k: v for k, v in visible_labels_on(page).items() if k.startswith("BP")}


class PdfTestCase(unittest.TestCase):
    """A scratch directory per test, cleaned up whatever happens."""

    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp(prefix="signature-test-"))
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)

    def impose(self, source, sheets_per_signature=1, **kwargs):
        folder = self.workspace / "out"
        written = split_to_signatures(source, sheets_per_signature, folder, **kwargs)
        return [pypdf.PdfReader(str(path)) for path in written]

    def assert_marks_where_expected(self, page, expected_pages, fit):
        """`expected_pages` is (left book page or None, right book page or None).

        `fit` is the SheetFit for the source page, or a pair of them when the two
        halves come from source pages of different sizes.
        """
        fits = fit if isinstance(fit, tuple) else (fit, fit)
        marks = visible_marks_on(page)
        wanted = {f"BP{n}" for n in expected_pages if n is not None}
        self.assertEqual(set(marks), wanted)

        mark_x, mark_y = MARK_IN_COLUMN_PT
        for column, book_page in enumerate(expected_pages):
            if book_page is None:
                continue
            half = fits[column]
            expected_x = (
                (half.sheet_width_pt / 2) * column
                + half.margin_x_pt
                + half.scale * mark_x
            )
            expected_y = half.margin_y_pt + half.scale * mark_y
            got_x, got_y = marks[f"BP{book_page}"]
            self.assertAlmostEqual(got_x, expected_x, places=3, msg=f"BP{book_page} x")
            self.assertAlmostEqual(got_y, expected_y, places=3, msg=f"BP{book_page} y")


# --------------------------------------------------------------------------
# The fold model
# --------------------------------------------------------------------------


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


# --------------------------------------------------------------------------
# Affine algebra
# --------------------------------------------------------------------------


class TestMatrices(unittest.TestCase):
    SAMPLES = (
        (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
        (0.5, 0.0, 0.0, 0.5, 30.0, -12.0),
        (0.0, -1.0, 1.0, 0.0, -7.0, 595.0),
        (0.0, 1.0, -1.0, 0.0, 842.0, -3.0),
        (-1.0, 0.0, 0.0, -1.0, 841.89, 595.28),
        (2.5, 0.0, 0.0, 1.25, -100.0, 4.0),
    )
    POINTS = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (123.4, -56.7), (841.89, 595.28))

    def test_composing_matches_applying_them_one_after_the_other(self):
        for first in self.SAMPLES:
            for then in self.SAMPLES:
                combined = compose(first, then)
                for point in self.POINTS:
                    step_by_step = apply_matrix(then, apply_matrix(first, point))
                    for a, b in zip(apply_matrix(combined, point), step_by_step):
                        self.assertAlmostEqual(a, b, places=9)

    def test_inverting_undoes_a_matrix(self):
        for matrix in self.SAMPLES:
            for point in self.POINTS:
                back = apply_matrix(invert(matrix), apply_matrix(matrix, point))
                for a, b in zip(back, point):
                    self.assertAlmostEqual(a, b, places=9)

    def test_a_flattened_matrix_cannot_be_inverted(self):
        with self.assertRaises(ValueError):
            invert((0.0, 0.0, 0.0, 0.0, 5.0, 5.0))


# --------------------------------------------------------------------------
# Reading a page
# --------------------------------------------------------------------------


class TestPageGeometry(PdfTestCase):
    def geometry_for(self, rotation, origin_pt=(0.0, 0.0), page_size_pt=A4_LANDSCAPE_PT):
        source = build_source_pdf(
            self.workspace / "src.pdf", 1, page_size_pt, rotation, origin_pt
        )
        return PageGeometry.of(pypdf.PdfReader(str(source)).pages[0])

    def test_the_displayed_size_survives_rotation_and_an_offset_mediabox(self):
        for rotation in (0, 90, 180, 270):
            for origin in ((0.0, 0.0), (17.0, -23.0)):
                geometry = self.geometry_for(rotation, origin)
                self.assertAlmostEqual(geometry.width_pt, A4_LANDSCAPE_PT[0], places=6)
                self.assertAlmostEqual(geometry.height_pt, A4_LANDSCAPE_PT[1], places=6)

    def test_to_visible_puts_the_page_in_the_first_quadrant(self):
        for rotation in (0, 90, 180, 270):
            geometry = self.geometry_for(rotation, origin_pt=(17.0, -23.0))
            corners = [
                apply_matrix(geometry.to_visible, point)
                for point in (
                    (geometry.left, geometry.bottom), (geometry.right, geometry.bottom),
                    (geometry.right, geometry.top), (geometry.left, geometry.top),
                )
            ]
            xs = sorted(round(x, 6) for x, _ in corners)
            ys = sorted(round(y, 6) for _, y in corners)
            self.assertEqual(xs[:2], [0.0, 0.0], f"rotation {rotation}")
            self.assertEqual(ys[:2], [0.0, 0.0], f"rotation {rotation}")
            self.assertAlmostEqual(xs[-1], geometry.width_pt, places=6)
            self.assertAlmostEqual(ys[-1], geometry.height_pt, places=6)

    def test_the_two_column_boxes_tile_the_page_without_overlapping(self):
        for rotation in (0, 90, 180, 270):
            geometry = self.geometry_for(rotation, origin_pt=(17.0, -23.0))
            left_box = geometry.column_box(False)
            right_box = geometry.column_box(True)
            areas = [
                float(box.width) * float(box.height) for box in (left_box, right_box)
            ]
            whole = geometry.box_width_pt * geometry.box_height_pt
            self.assertAlmostEqual(sum(areas), whole, places=3, msg=f"rotation {rotation}")
            self.assertAlmostEqual(areas[0], areas[1], places=6)
            # Together they cover the page, and neither reaches outside it.
            for box in (left_box, right_box):
                self.assertGreaterEqual(float(box.left) + 1e-6, geometry.left)
                self.assertGreaterEqual(float(box.bottom) + 1e-6, geometry.bottom)
                self.assertLessEqual(float(box.right) - 1e-6, geometry.right)
                self.assertLessEqual(float(box.top) - 1e-6, geometry.top)

    def test_a_crop_box_is_what_gets_measured(self):
        source = build_source_pdf(self.workspace / "src.pdf", 1)
        writer = pypdf.PdfWriter(clone_from=str(source))
        writer.pages[0].cropbox = pypdf.generic.RectangleObject((36, 18, 500, 400))
        cropped = self.workspace / "cropped.pdf"
        with open(cropped, "wb") as stream:
            writer.write(stream)

        geometry = PageGeometry.of(pypdf.PdfReader(str(cropped)).pages[0])
        self.assertTrue(geometry.is_cropped)
        self.assertAlmostEqual(geometry.width_pt, 500 - 36, places=6)
        self.assertAlmostEqual(geometry.height_pt, 400 - 18, places=6)
        self.assertAlmostEqual(inspect_book(cropped).page_width_pt, 464, places=6)

    def test_a_crop_box_outside_the_media_box_falls_back_to_the_media_box(self):
        source = build_source_pdf(self.workspace / "src.pdf", 1)
        writer = pypdf.PdfWriter(clone_from=str(source))
        writer.pages[0].cropbox = pypdf.generic.RectangleObject((5000, 5000, 5100, 5100))
        broken = self.workspace / "broken.pdf"
        with open(broken, "wb") as stream:
            writer.write(stream)

        geometry = PageGeometry.of(pypdf.PdfReader(str(broken)).pages[0])
        self.assertAlmostEqual(geometry.width_pt, A4_LANDSCAPE_PT[0], places=6)
        self.assertAlmostEqual(geometry.height_pt, A4_LANDSCAPE_PT[1], places=6)

    def test_inspect_book_reports_rotation_and_uniformity(self):
        plain = build_source_pdf(self.workspace / "plain.pdf", 3)
        info = inspect_book(plain)
        self.assertEqual(info.source_pages, 3)
        self.assertEqual(info.book_pages, 6)
        self.assertTrue(info.uniform_page_size)
        self.assertFalse(info.rotated_pages)
        self.assertFalse(info.cropped_pages)

        turned = build_source_pdf(self.workspace / "turned.pdf", 2, rotation=90)
        self.assertTrue(inspect_book(turned).rotated_pages)
        self.assertAlmostEqual(
            inspect_book(turned).page_width_pt, A4_LANDSCAPE_PT[0], places=6
        )

    def test_a_pdf_with_no_pages_is_rejected(self):
        empty = self.workspace / "empty.pdf"
        writer = pypdf.PdfWriter()
        with open(empty, "wb") as stream:
            writer.write(stream)
        with self.assertRaises(ValueError):
            inspect_book(empty)


# --------------------------------------------------------------------------
# The paper catalogue
# --------------------------------------------------------------------------


class TestPaperCatalogue(unittest.TestCase):
    def test_every_entry_is_stored_portrait_with_a_known_family(self):
        for catalogue, families in (
            (SHEET_SIZES, SHEET_FAMILIES), (BOOK_PAGE_SIZES, BOOK_PAGE_FAMILIES)
        ):
            for size in catalogue:
                self.assertLess(size.width_in, size.height_in, size.name)
                self.assertGreater(size.width_in, 0, size.name)
                self.assertIn(size.family, families, size.name)
                self.assertTrue(size.note, f"{size.name} has no note")

    def test_names_are_unique_within_a_catalogue(self):
        for catalogue in (SHEET_SIZES, BOOK_PAGE_SIZES):
            names = [size.name.casefold() for size in catalogue]
            self.assertEqual(len(names), len(set(names)))

    def test_grouping_by_family_loses_nothing(self):
        for catalogue, families in (
            (SHEET_SIZES, SHEET_FAMILIES), (BOOK_PAGE_SIZES, BOOK_PAGE_FAMILIES)
        ):
            grouped = [size for _f, sizes in sizes_by_family(catalogue, families)
                       for size in sizes]
            self.assertEqual(sorted(s.name for s in grouped),
                             sorted(s.name for s in catalogue))

    def test_the_headline_sizes_are_right(self):
        # Checked against the ISO 216 / ANSI definitions, not against the module.
        self.assertAlmostEqual(find_paper_size("A4").width_in, 210 / MM_PER_INCH, places=9)
        self.assertAlmostEqual(find_paper_size("A4").height_in, 297 / MM_PER_INCH, places=9)
        self.assertEqual(find_paper_size("Letter").size_in(False), (8.5, 11.0))
        self.assertEqual(find_paper_size("Tabloid").size_in(True), (17.0, 11.0))
        self.assertAlmostEqual(
            find_paper_size("A4").size_pt(True)[0], 841.8897637795277, places=6
        )

    def test_folding_an_iso_sheet_gives_the_next_iso_size(self):
        # A3 landscape folds to A4 portrait, A4 to A5, and so on down the series.
        for bigger, smaller in (("A2", "A3"), ("A3", "A4"), ("A4", "A5"), ("A5", "A6"),
                                ("B3", "B4"), ("B4", "B5"), ("B5", "B6"),
                                ("JIS B4", "JIS B5"), ("JIS B5", "JIS B6"),
                                ("Letter", "Half Letter"), ("Tabloid", "Letter"),
                                ("ANSI C", "Tabloid")):
            folded = find_paper_size(bigger).book_page_in(landscape=True)
            expected = find_paper_size(smaller).size_in(landscape=False)
            for got, want in zip(folded, expected):
                self.assertAlmostEqual(got, want, delta=0.05, msg=f"{bigger} -> {smaller}")

    def test_every_size_identifies_itself_in_both_orientations(self):
        for catalogue in (SHEET_SIZES, BOOK_PAGE_SIZES):
            for size in catalogue:
                for landscape in (False, True):
                    match = identify_size(*size.size_in(landscape), catalogue)
                    self.assertIsNotNone(match, size.name)
                    self.assertEqual(match[0].name, size.name)
                    self.assertEqual(match[1], landscape)

    def test_no_two_sheet_sizes_are_close_enough_to_be_confused(self):
        # The identify tolerance is only meaningful if the catalogue is spread
        # wider than it.
        for i, first in enumerate(SHEET_SIZES):
            for second in SHEET_SIZES[i + 1:]:
                for landscape in (False, True):
                    error = max(
                        abs(a - b) for a, b in
                        zip(first.size_in(False), second.size_in(landscape))
                    )
                    self.assertGreater(error, 0.05, f"{first.name} vs {second.name}")

    def test_an_unknown_size_is_reported_as_plain_dimensions(self):
        self.assertIsNone(identify_size(9.0, 12.0))
        self.assertEqual(describe_size(9.0, 12.0, INCHES), "9.00 × 12.00 in")
        self.assertIn("(A4, landscape)", describe_size(11.6929, 8.2677, INCHES))

    def test_parsing_paper_arguments(self):
        self.assertEqual(parse_paper_size("Letter"), (11.0, 8.5))
        self.assertEqual(parse_paper_size("half letter", landscape=False), (5.5, 8.5))
        self.assertEqual(parse_paper_size("  TABLOID  "), (17.0, 11.0))
        self.assertEqual(parse_paper_size("12x9in"), (12.0, 9.0))
        self.assertEqual(parse_paper_size("12x9"), (12.0, 9.0))
        width_in, height_in = parse_paper_size("420x297mm")
        self.assertAlmostEqual(width_in, 420 / MM_PER_INCH, places=9)
        self.assertAlmostEqual(height_in, 297 / MM_PER_INCH, places=9)
        self.assertAlmostEqual(parse_paper_size("30x21cm")[0], 30 / 2.54, places=9)

    def test_a_book_page_name_is_not_accepted_as_a_sheet(self):
        # "US trade" is a finished page size; taking it as a sheet would halve
        # the book. It has to be spelled out as dimensions instead.
        with self.assertRaises(ValueError):
            parse_paper_size("US trade")

    def test_bad_paper_arguments_are_rejected(self):
        for text in ("", "   ", "nonsense", "11by17", "0x5in", "axbmm", "1x2x3in",
                     "-4x5", "A9", "x", "11x", "x17", "11 17"):
            with self.assertRaises(ValueError, msg=text):
                parse_paper_size(text)

    def test_a_word_that_merely_contains_an_x_is_not_a_size(self):
        # "nonexistent" splits into "none" and "istent" if the parser goes
        # looking for an "x" instead of matching the whole thing.
        for text in ("nonexistent", "extra large", "boxy", "A4x"):
            with self.assertRaises(ValueError, msg=text) as caught:
                parse_paper_size(text)
            self.assertIn("known sheet names", str(caught.exception))

    def test_the_sheet_a_book_size_needs_is_twice_as_wide(self):
        for size in BOOK_PAGE_SIZES:
            sheet_width_in, sheet_height_in = size.sheet_needed_in()
            self.assertAlmostEqual(sheet_width_in, 2 * size.width_in, places=9)
            self.assertAlmostEqual(sheet_height_in, size.height_in, places=9)
            stock = smallest_sheet_for(sheet_width_in, sheet_height_in)
            self.assertIsNotNone(stock, f"nothing holds {size.name}")
            self.assertGreaterEqual(
                max(stock.size_in(True)) + 1e-6, sheet_width_in, size.name
            )

    def test_the_smallest_stock_really_is_the_smallest_that_fits(self):
        for size in BOOK_PAGE_SIZES:
            needed = size.sheet_needed_in()
            chosen = smallest_sheet_for(*needed)
            for other in SHEET_SIZES:
                if other.area_in2 < chosen.area_in2:
                    self.assertIsNone(
                        smallest_sheet_for(*needed, catalogue=(other,)),
                        f"{other.name} is smaller than {chosen.name} and also fits "
                        f"{size.name}",
                    )

    def test_the_reference_tables_cover_every_entry(self):
        self.assertEqual(len(paper_size_table()), len(SHEET_SIZES))
        self.assertEqual(len(book_page_table()), len(BOOK_PAGE_SIZES))
        self.assertTrue(all(len(row) == 5 for row in paper_size_table()))
        self.assertTrue(all(len(row) == 6 for row in book_page_table()))


class TestUnits(unittest.TestCase):
    def test_a_round_trip_through_any_unit_changes_nothing(self):
        for unit in (INCHES, CENTIMETRES, MILLIMETRES):
            for inches in (0.0, 0.5, 4.85, 11.6929):
                self.assertAlmostEqual(
                    unit.to_inches(unit.from_inches(inches)), inches, places=12
                )

    def test_labels_use_the_unit_they_claim(self):
        self.assertEqual(INCHES.label(0.5), "0.500 in")
        self.assertEqual(CENTIMETRES.label(1.0), "2.54 cm")
        self.assertEqual(MILLIMETRES.label(1.0), "25.4 mm")
        self.assertEqual(MILLIMETRES.size_label(210 / MM_PER_INCH, 297 / MM_PER_INCH),
                         "210 × 297 mm")


# --------------------------------------------------------------------------
# Fitting a page onto a sheet
# --------------------------------------------------------------------------


class TestSheetFit(unittest.TestCase):
    def test_the_same_sheet_as_the_source_changes_nothing(self):
        for size in (A4_LANDSCAPE_PT, LETTER_LANDSCAPE_PT, (500.0, 700.0)):
            for sheet in (None, size):
                fit = SheetFit.compute(size, sheet, FIT)
                self.assertEqual(fit.scale, 1.0)
                self.assertEqual(fit.margin_x_pt, 0.0)
                self.assertEqual(fit.margin_y_pt, 0.0)
                self.assertFalse(fit.is_resized)
                self.assertFalse(fit.overflows)
                # Left column to left half, right to right half: nothing moves.
                self.assertEqual(fit.placement(False, False), (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
                self.assertEqual(fit.placement(True, True), (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
                # Right column to the left half: shifted by exactly one column.
                self.assertAlmostEqual(fit.placement(True, False)[4], -size[0] / 2)

    def test_a_book_page_is_always_half_a_sheet(self):
        fit = SheetFit.compute(A4_LANDSCAPE_PT, LETTER_LANDSCAPE_PT, FIT)
        width_in, height_in = fit.book_page_size_in
        self.assertAlmostEqual(width_in, 5.5, places=9)
        self.assertAlmostEqual(height_in, 8.5, places=9)

    def test_fitting_uses_the_tighter_of_the_two_axes(self):
        # A4 landscape onto Letter landscape: limited by width (11 / 11.69).
        fit = SheetFit.compute(A4_LANDSCAPE_PT, LETTER_LANDSCAPE_PT, FIT)
        self.assertAlmostEqual(fit.scale, 792.0 / 841.89, places=9)
        self.assertAlmostEqual(fit.margin_x_pt, 0.0, places=9)
        self.assertGreater(fit.margin_y_pt, 0.0)

        # Letter landscape onto A4 landscape: limited by height (595.28 / 612).
        fit = SheetFit.compute(LETTER_LANDSCAPE_PT, A4_LANDSCAPE_PT, FIT)
        self.assertAlmostEqual(fit.scale, 595.28 / 612.0, places=9)
        self.assertAlmostEqual(fit.margin_y_pt, 0.0, places=9)
        self.assertGreater(fit.margin_x_pt, 0.0)

    def test_fitting_never_overflows_and_always_touches_one_edge(self):
        sizes = [size.size_pt(True) for size in SHEET_SIZES]
        sizes += [size.size_pt(False) for size in SHEET_SIZES]
        for source in sizes:
            for sheet in sizes:
                fit = SheetFit.compute(source, sheet, FIT)
                self.assertFalse(fit.overflows, f"{source} -> {sheet}")
                self.assertGreaterEqual(fit.margin_x_pt, -1e-9)
                self.assertGreaterEqual(fit.margin_y_pt, -1e-9)
                self.assertLess(min(fit.margin_x_pt, fit.margin_y_pt), 1e-6)
                # The content lands inside the half it was given, exactly.
                for to_right in (False, True):
                    matrix = fit.placement(False, to_right)
                    x0, y0 = apply_matrix(matrix, (0.0, 0.0))
                    x1, y1 = apply_matrix(
                        matrix, (fit.source_half_width_pt, fit.source_height_pt)
                    )
                    half_start = fit.sheet_half_width_pt if to_right else 0.0
                    self.assertGreaterEqual(x0 + 1e-6, half_start)
                    self.assertLessEqual(x1 - 1e-6, half_start + fit.sheet_half_width_pt)
                    self.assertGreaterEqual(y0 + 1e-6, 0.0)
                    self.assertLessEqual(y1 - 1e-6, fit.sheet_height_pt)

    def test_actual_size_never_scales_and_reports_when_it_will_not_fit(self):
        fit = SheetFit.compute(A4_LANDSCAPE_PT, LETTER_LANDSCAPE_PT, ACTUAL_SIZE)
        self.assertEqual(fit.scale, 1.0)
        self.assertTrue(fit.overflows)
        self.assertTrue(sheet_problems(fit))
        self.assertEqual(sheet_warnings(fit), [])  # the problem said it already

        roomy = SheetFit.compute(A4_LANDSCAPE_PT, (1000.0, 800.0), ACTUAL_SIZE)
        self.assertFalse(roomy.overflows)
        self.assertEqual(sheet_problems(roomy), [])
        self.assertGreater(roomy.margin_x_pt, 0)

    def test_nonsense_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            SheetFit.compute((0.0, 100.0))
        with self.assertRaises(ValueError):
            SheetFit.compute(A4_LANDSCAPE_PT, (0.0, 100.0))
        with self.assertRaises(ValueError):
            SheetFit.compute(A4_LANDSCAPE_PT, LETTER_LANDSCAPE_PT, "stretch")


class TestSheetAdvice(unittest.TestCase):
    def test_scaling_is_always_called_out(self):
        fit = SheetFit.compute(A4_LANDSCAPE_PT, find_paper_size("A5").size_pt(True), FIT)
        text = " ".join(sheet_warnings(fit))
        self.assertIn("shrunk", text)
        self.assertIn("100%", text)

    def test_enlarging_warns_about_the_scan_as_well(self):
        fit = SheetFit.compute(find_paper_size("A5").size_pt(True), A4_LANDSCAPE_PT, FIT)
        text = " ".join(sheet_warnings(fit))
        self.assertIn("enlarged", text)
        self.assertIn("scan", text)

    def test_an_unchanged_sheet_says_nothing(self):
        fit = SheetFit.compute(A4_LANDSCAPE_PT, A4_LANDSCAPE_PT, FIT)
        self.assertEqual(sheet_warnings(fit, ColumnLayout()), [])

    def test_a_shape_change_explains_where_the_blank_paper_goes(self):
        fit = SheetFit.compute(A4_LANDSCAPE_PT, LETTER_LANDSCAPE_PT, FIT)
        self.assertTrue(any("above and below" in w for w in sheet_warnings(fit)))

    def test_shrinking_the_margin_below_the_printable_edge_warns(self):
        # A4 landscape squeezed onto A6 landscape: a half inch margin becomes 0.18.
        fit = SheetFit.compute(A4_LANDSCAPE_PT, find_paper_size("A6").size_pt(True), FIT)
        text = " ".join(sheet_warnings(fit, ColumnLayout()))
        self.assertIn("edge of the paper", text)

    def test_a_portrait_sheet_is_flagged_as_probably_backwards(self):
        fit = SheetFit.compute(A4_LANDSCAPE_PT, find_paper_size("A4").size_pt(False), FIT)
        self.assertTrue(any("tall and narrow" in w for w in sheet_warnings(fit)))

    def test_paper_no_home_printer_takes_is_flagged(self):
        fit = SheetFit.compute(A4_LANDSCAPE_PT, find_paper_size("ANSI C").size_pt(True), FIT)
        self.assertTrue(any("wide-format" in w for w in sheet_warnings(fit)))


# --------------------------------------------------------------------------
# Column layout
# --------------------------------------------------------------------------


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

    def test_the_shipped_default_only_suits_a4_landscape(self):
        # This is the whole reason `fitted` exists: the defaults are A4 numbers.
        self.assertTrue(
            layout_problems(ColumnLayout(), find_paper_size("Letter").size_pt(True)[0])
        )

    def test_a_fitted_layout_is_valid_on_the_page_it_was_fitted_to(self):
        for size in SHEET_SIZES:
            for landscape in (True, False):
                width_in = size.size_in(landscape)[0]
                if width_in <= 2 * 0.5 + 0.99:
                    continue  # too narrow for the default margin and gap at all
                layout = ColumnLayout.fitted(width_in)
                self.assertEqual(
                    layout_problems(layout, width_in * PT_PER_INCH), [],
                    f"{size.name} {'landscape' if landscape else 'portrait'}",
                )
                # It fills the page exactly, and is symmetric about the fold.
                self.assertAlmostEqual(layout.right_column_end_in, width_in - 0.5, places=9)
                self.assertAlmostEqual(
                    layout.left_column_end_in + layout.column_gap_in / 2,
                    width_in / 2, places=9,
                )

    def test_fitting_a_page_too_narrow_shrinks_the_margin_and_gap(self):
        # 2 * 0.5 + 0.99 leaves nothing of a 1.9 in page. Rather than refuse a
        # book for being small, the margin and gap give way to the columns.
        layout = ColumnLayout.fitted(1.9, page_margin_in=0.5, column_gap_in=0.99)
        self.assertGreater(layout.column_width_in, 0)
        self.assertLess(layout.page_margin_in, 0.5)
        self.assertLess(layout.column_gap_in, 0.99)
        # Still symmetric, still exactly filling the page, still fold-safe.
        self.assertAlmostEqual(layout.right_column_end_in, 1.9 - layout.page_margin_in,
                               places=9)
        self.assertEqual(layout_problems(layout, 1.9 * PT_PER_INCH), [])

    def test_fitting_is_total_over_every_sheet_size_and_orientation(self):
        # Nothing skipped: `fitted` is the app's fallback for every book it opens,
        # so there must be no page size at all for which it fails to answer.
        for size in SHEET_SIZES:
            for landscape in (True, False):
                width_in = size.size_in(landscape)[0]
                layout = ColumnLayout.fitted(width_in)
                self.assertEqual(
                    layout_problems(layout, width_in * PT_PER_INCH), [],
                    f"{size.name} {'landscape' if landscape else 'portrait'}",
                )

    def test_fitting_a_letter_landscape_page_is_clean(self):
        # The regression: 11 in wide input against A4-shaped default numbers
        # (0.5 + 4.85 + 0.99 + 4.85 = 11.19 in) used to be reported as two
        # problems on a perfectly ordinary PDF.
        layout = ColumnLayout.fitted(11.0)
        self.assertEqual(layout_problems(layout, 11.0 * PT_PER_INCH), [])
        self.assertEqual(layout_warnings(layout, 11.0 * PT_PER_INCH), [])

    def test_fitting_reproduces_the_shipped_defaults_on_a4(self):
        layout = ColumnLayout.fitted(841.89 / PT_PER_INCH)
        self.assertAlmostEqual(layout.column_width_in, ColumnLayout.column_width_in, places=2)


# --------------------------------------------------------------------------
# End to end: build a book, impose it, read back where the ink went
# --------------------------------------------------------------------------


class TestImposedOutput(PdfTestCase):
    """Every check here reads the finished PDF, not the code that made it."""

    def test_a_single_sheet_booklet_lands_exactly_where_the_fold_says(self):
        source = build_source_pdf(self.workspace / "src.pdf", 2)  # 4 book pages
        reader, = self.impose(source, 1)
        self.assertEqual(len(reader.pages), 2)

        fit = SheetFit.compute(A4_LANDSCAPE_PT, None, FIT)
        # fold_a_signature(1) says: front is [4 | 1], back is [2 | 3].
        self.assert_marks_where_expected(reader.pages[0], (4, 1), fit)
        self.assert_marks_where_expected(reader.pages[1], (2, 3), fit)

    def test_the_output_sheet_is_the_paper_that_was_asked_for(self):
        source = build_source_pdf(self.workspace / "src.pdf", 2)
        for name in ("A4", "A3", "Letter", "Tabloid", "B5", "JIS B5", "Super B"):
            sheet_size_pt = find_paper_size(name).size_pt(True)
            reader, = self.impose(source, 1, sheet_size_pt=sheet_size_pt)
            for page in reader.pages:
                self.assertAlmostEqual(float(page.mediabox.width), sheet_size_pt[0], places=3)
                self.assertAlmostEqual(float(page.mediabox.height), sheet_size_pt[1], places=3)

    def test_every_sheet_size_places_both_columns_correctly(self):
        source = build_source_pdf(self.workspace / "src.pdf", 2)
        for size in SHEET_SIZES:
            for landscape in (True, False):
                sheet_size_pt = size.size_pt(landscape)
                fit = SheetFit.compute(A4_LANDSCAPE_PT, sheet_size_pt, FIT)
                reader, = self.impose(source, 1, sheet_size_pt=sheet_size_pt)
                with self.subTest(sheet=size.name, landscape=landscape):
                    self.assert_marks_where_expected(reader.pages[0], (4, 1), fit)
                    self.assert_marks_where_expected(reader.pages[1], (2, 3), fit)

    def test_a_custom_sheet_size_works_just_as_well(self):
        source = build_source_pdf(self.workspace / "src.pdf", 2)
        for sheet_size_pt in ((12 * 72, 9 * 72), (400.0, 900.0), (2000.0, 300.0)):
            fit = SheetFit.compute(A4_LANDSCAPE_PT, sheet_size_pt, FIT)
            reader, = self.impose(source, 1, sheet_size_pt=sheet_size_pt)
            with self.subTest(sheet=sheet_size_pt):
                self.assert_marks_where_expected(reader.pages[0], (4, 1), fit)

    def test_actual_size_leaves_the_book_untouched_on_bigger_paper(self):
        source = build_source_pdf(self.workspace / "src.pdf", 2)
        sheet_size_pt = find_paper_size("A3").size_pt(True)
        fit = SheetFit.compute(A4_LANDSCAPE_PT, sheet_size_pt, ACTUAL_SIZE)
        self.assertEqual(fit.scale, 1.0)
        reader, = self.impose(
            source, 1, sheet_size_pt=sheet_size_pt, scale_mode=ACTUAL_SIZE
        )
        self.assert_marks_where_expected(reader.pages[0], (4, 1), fit)

    def test_a_source_page_of_any_size_imposes_onto_any_sheet(self):
        for source_name in ("A4", "Letter", "B5", "Legal"):
            source_size_pt = find_paper_size(source_name).size_pt(True)
            source = build_source_pdf(
                self.workspace / f"{source_name}.pdf", 2, source_size_pt
            )
            for sheet_name in ("A4", "A5", "Letter", "Tabloid"):
                sheet_size_pt = find_paper_size(sheet_name).size_pt(True)
                fit = SheetFit.compute(source_size_pt, sheet_size_pt, FIT)
                reader, = self.impose(source, 1, sheet_size_pt=sheet_size_pt)
                with self.subTest(source=source_name, sheet=sheet_name):
                    self.assert_marks_where_expected(reader.pages[0], (4, 1), fit)

    def test_rotated_source_pages_are_imposed_as_a_reader_displays_them(self):
        for rotation in (0, 90, 180, 270):
            source = build_source_pdf(
                self.workspace / f"rot{rotation}.pdf", 2, rotation=rotation
            )
            self.assertAlmostEqual(
                inspect_book(source).page_width_pt, A4_LANDSCAPE_PT[0], places=6
            )
            for sheet_name in (None, "Letter"):
                sheet_size_pt = (
                    None if sheet_name is None
                    else find_paper_size(sheet_name).size_pt(True)
                )
                fit = SheetFit.compute(A4_LANDSCAPE_PT, sheet_size_pt, FIT)
                reader, = self.impose(source, 1, sheet_size_pt=sheet_size_pt)
                with self.subTest(rotation=rotation, sheet=sheet_name):
                    self.assert_marks_where_expected(reader.pages[0], (4, 1), fit)
                    self.assert_marks_where_expected(reader.pages[1], (2, 3), fit)

    def test_a_mediabox_that_does_not_start_at_the_origin_still_lines_up(self):
        for origin in ((0.0, 0.0), (36.0, 12.0), (-25.0, -40.0)):
            source = build_source_pdf(
                self.workspace / "offset.pdf", 2, origin_pt=origin
            )
            fit = SheetFit.compute(A4_LANDSCAPE_PT, LETTER_LANDSCAPE_PT, FIT)
            reader, = self.impose(source, 1, sheet_size_pt=LETTER_LANDSCAPE_PT)
            with self.subTest(origin=origin):
                self.assert_marks_where_expected(reader.pages[0], (4, 1), fit)

    def test_pages_of_mixed_sizes_all_land_on_the_same_sheet(self):
        # Two separately built books stitched together, so source page 1 is A4
        # landscape (BP1, BP2) and source page 2 is Letter landscape (BP3, BP4).
        # Each is fitted on its own terms onto one common sheet size.
        first = build_source_pdf(self.workspace / "a.pdf", 1, A4_LANDSCAPE_PT)
        second = build_source_pdf(
            self.workspace / "b.pdf", 1, LETTER_LANDSCAPE_PT, first_book_page=3
        )
        writer = pypdf.PdfWriter(clone_from=str(first))
        writer.append(str(second))
        mixed = self.workspace / "mixed.pdf"
        with open(mixed, "wb") as stream:
            writer.write(stream)

        info = inspect_book(mixed)
        self.assertEqual(info.source_pages, 2)
        self.assertFalse(info.uniform_page_size)

        sheet_size_pt = find_paper_size("A3").size_pt(True)
        a4_fit = SheetFit.compute(A4_LANDSCAPE_PT, sheet_size_pt, FIT)
        letter_fit = SheetFit.compute(LETTER_LANDSCAPE_PT, sheet_size_pt, FIT)
        self.assertNotAlmostEqual(a4_fit.scale, letter_fit.scale, places=3)

        reader, = self.impose(mixed, 1, sheet_size_pt=sheet_size_pt)
        for page in reader.pages:
            self.assertAlmostEqual(float(page.mediabox.width), sheet_size_pt[0], places=3)
            self.assertAlmostEqual(float(page.mediabox.height), sheet_size_pt[1], places=3)

        # Front is [BP4 | BP1]: BP4 comes off the Letter page, BP1 off the A4 one.
        self.assert_marks_where_expected(reader.pages[0], (4, 1), (letter_fit, a4_fit))
        self.assert_marks_where_expected(reader.pages[1], (2, 3), (a4_fit, letter_fit))

    def test_the_back_of_each_sheet_is_turned_over_for_long_edge_duplex(self):
        source = build_source_pdf(self.workspace / "src.pdf", 2)
        turned, = self.impose(source, 1, flip_on_long_edge=True)
        straight, = self.impose(source, 1, flip_on_long_edge=False)
        self.assertEqual([page.rotation for page in turned.pages], [0, 180])
        self.assertEqual([page.rotation for page in straight.pages], [0, 0])

    def test_blank_padding_leaves_the_paper_empty(self):
        # 3 source pages = 6 book pages, in 2-sheet (8 page) signatures: two of
        # the eight halves have nothing to put on them.
        source = build_source_pdf(self.workspace / "src.pdf", 3)
        reader, = self.impose(source, 2)
        self.assertEqual(len(reader.pages), 4)
        placed = set()
        for page in reader.pages:
            placed |= set(visible_marks_on(page))
        self.assertEqual(placed, {f"BP{n}" for n in range(1, 7)})

        fit = SheetFit.compute(A4_LANDSCAPE_PT, None, FIT)
        # fold_a_signature(2): sides are [8|1], [2|7], [6|3], [4|5]; 7 and 8 are
        # padding and simply do not appear.
        self.assert_marks_where_expected(reader.pages[0], (None, 1), fit)
        self.assert_marks_where_expected(reader.pages[1], (2, None), fit)
        self.assert_marks_where_expected(reader.pages[2], (6, 3), fit)
        self.assert_marks_where_expected(reader.pages[3], (4, 5), fit)

    def test_a_multi_signature_book_splits_into_the_planned_files(self):
        source = build_source_pdf(self.workspace / "src.pdf", 10)  # 20 book pages
        readers = self.impose(source, 2, sheet_size_pt=LETTER_LANDSCAPE_PT)
        self.assertEqual(len(readers), 3)  # 8 + 8 + 4 book pages
        self.assertEqual([len(r.pages) for r in readers], [4, 4, 2])

        seen = []
        for reader in readers:
            for page in reader.pages:
                seen.extend(visible_marks_on(page))
        self.assertEqual(sorted(seen), sorted(f"BP{n}" for n in range(1, 21)))

    def test_columns_are_clipped_to_exactly_their_own_half_of_the_sheet(self):
        # Only the marks belonging to a side may appear on it; if the clip failed,
        # the neighbouring column's mark would be dragged along too.
        source = build_source_pdf(self.workspace / "src.pdf", 4)
        sheet_width_pt, sheet_height_pt = LETTER_LANDSCAPE_PT
        reader, = self.impose(source, 2, sheet_size_pt=LETTER_LANDSCAPE_PT)
        for page, expected in zip(reader.pages, fold_a_signature(2)):
            self.assertEqual(
                set(visible_marks_on(page)), {f"BP{expected[0]}", f"BP{expected[1]}"}
            )
            # The other column of each source page is present in the stream but
            # clipped away; that is what keeps it off the paper.
            self.assertEqual(len(marks_on(page)), 4)

            # Each clip is exactly the placed column: it starts at that column's
            # half of the paper, is the width and height of the scaled book page,
            # and never reaches into the other half or off the sheet.
            fit = SheetFit.compute(A4_LANDSCAPE_PT, LETTER_LANDSCAPE_PT, FIT)
            clips = sorted(clip_rectangles(page))
            self.assertEqual(len(clips), 2)
            for column, (left, bottom, right, top) in enumerate(clips):
                half_start = column * sheet_width_pt / 2
                self.assertAlmostEqual(left, half_start + fit.margin_x_pt, places=3)
                self.assertAlmostEqual(right, left + fit.content_width_pt, places=3)
                self.assertAlmostEqual(bottom, fit.margin_y_pt, places=3)
                self.assertAlmostEqual(top, bottom + fit.content_height_pt, places=3)
                # A hair of slack: the matrices are rounded on their way into the
                # file, so an exact edge can land a millionth of a point over it.
                self.assertGreaterEqual(left + 1e-3, half_start)
                self.assertLessEqual(right - 1e-3, half_start + sheet_width_pt / 2)
                self.assertGreaterEqual(bottom + 1e-3, 0.0)
                self.assertLessEqual(top - 1e-3, sheet_height_pt)

    def test_the_test_interpreter_agrees_with_pypdf_about_where_text_is(self):
        # The clip tracking above is only worth anything if the rest of the tiny
        # interpreter reads positions the same way a real PDF library does.
        source = build_source_pdf(self.workspace / "src.pdf", 2, rotation=90)
        reader, = self.impose(source, 1, sheet_size_pt=LETTER_LANDSCAPE_PT)
        for page in reader.pages:
            from_pypdf = {}

            def visit(text, cm, tm, _font, _size, found=from_pypdf):
                label = text.strip()
                if label:
                    found[label] = apply_matrix(compose(tuple(tm), tuple(cm)), (0.0, 0.0))

            page.extract_text(visitor_text=visit)
            mine = labels_on(page)
            self.assertEqual(set(mine), set(from_pypdf))
            self.assertTrue(from_pypdf)
            for label, point in from_pypdf.items():
                for a, b in zip(mine[label], point):
                    self.assertAlmostEqual(a, b, places=6, msg=label)

    def test_the_source_pdf_is_never_modified(self):
        source = build_source_pdf(self.workspace / "src.pdf", 4)
        before = source.read_bytes()
        self.impose(source, 2, sheet_size_pt=LETTER_LANDSCAPE_PT)
        self.assertEqual(source.read_bytes(), before)

    def test_an_empty_pdf_is_refused(self):
        empty = self.workspace / "empty.pdf"
        with open(empty, "wb") as stream:
            pypdf.PdfWriter().write(stream)
        with self.assertRaises(ValueError):
            self.impose(empty, 1)


# --------------------------------------------------------------------------
# Page numbering
# --------------------------------------------------------------------------


class TestNumbering(PdfTestCase):
    def numbers_on(self, path):
        """Where each stamped number sits on the page *as displayed*.

        The page's own mediabox and /Rotate are read straight from the file and
        undone with the test's own `visible_coordinates`, so the answer never
        passes through the code that put the numbers there.
        """
        reader = pypdf.PdfReader(str(path))
        pages = []
        for page in reader.pages:
            box = page.mediabox
            origin_x, origin_y = float(box.left), float(box.bottom)
            box_width_pt, box_height_pt = float(box.width), float(box.height)
            found = {}

            def visit(text, cm, tm, _font, _size, found=found):
                label = text.strip()
                if label.isdigit():
                    found[label] = apply_matrix(compose(tuple(tm), tuple(cm)), (0.0, 0.0))

            page.extract_text(visitor_text=visit)
            pages.append({
                label: visible_coordinates(
                    (x - origin_x, y - origin_y), box_width_pt, box_height_pt,
                    page.rotation % 360,
                )
                for label, (x, y) in found.items()
            })
        return pages

    def test_numbers_are_stamped_centred_at_the_foot_of_each_column(self):
        """Centred, because that is where the editor's typesetter puts them.

        Both halves of the app go through `draw_folio`, so a converted book and
        a book typed into the editor carry their numbers in the same place. The
        two figures below are written out by hand rather than imported: the
        middle of each column, and a baseline two fifths of the way up the page
        margin.
        """
        layout = ColumnLayout()
        source = build_source_pdf(self.workspace / "src.pdf", 3)
        numbered = stamp_book(source, self.workspace / "numbered.pdf", layout)
        pages = self.numbers_on(numbered)
        self.assertEqual(len(pages), 3)

        centres = (
            (layout.page_margin_in + layout.column_width_in / 2) * PT_PER_INCH,
            (layout.right_column_start_in + layout.column_width_in / 2) * PT_PER_INCH,
        )
        baseline = 0.40 * layout.page_margin_in * PT_PER_INCH
        for index, found in enumerate(pages):
            self.assertEqual(set(found), {str(2 * index + 1), str(2 * index + 2)})
            for column, number in enumerate((2 * index + 1, 2 * index + 2)):
                x, y = found[str(number)]
                # `x` is where the digits begin, so half the width of the number
                # sits to the left of the middle it was centred on.
                width = pdfmetrics.stringWidth(str(number), FONT_NAME, FONT_SIZE)
                self.assertAlmostEqual(x + width / 2, centres[column], delta=0.5,
                                       msg=f"page {number}")
                self.assertAlmostEqual(y, baseline, places=6)

    def test_numbering_survives_rotation_and_an_offset_mediabox(self):
        layout = ColumnLayout()
        plain = self.numbers_on(
            stamp_book(
                build_source_pdf(self.workspace / "plain.pdf", 2),
                self.workspace / "plain-n.pdf", layout,
            )
        )
        for rotation in (90, 180, 270):
            source = build_source_pdf(
                self.workspace / f"r{rotation}.pdf", 2, rotation=rotation,
                origin_pt=(11.0, -7.0),
            )
            got = self.numbers_on(
                stamp_book(source, self.workspace / f"r{rotation}-n.pdf", layout)
            )
            self.assertEqual(len(got), len(plain))
            for expected_page, got_page in zip(plain, got):
                self.assertEqual(set(got_page), set(expected_page))
                for label, point in expected_page.items():
                    for a, b in zip(got_page[label], point):
                        self.assertAlmostEqual(a, b, places=4, msg=f"{rotation} {label}")

    def test_numbering_with_no_layout_fits_the_columns_to_the_page(self):
        """Numbering is the only thing that reads a layout, so it works one out.

        A Letter-landscape book stamped against the A4 default column width would
        put the right-hand number 0.19 in past the edge of its own page. Fitting
        per page puts both numbers inside their columns for any page size.
        """
        for page_size_pt in (A4_LANDSCAPE_PT, LETTER_LANDSCAPE_PT,
                             find_paper_size("B5").size_pt(True)):
            with self.subTest(page_size_pt=page_size_pt):
                name = f"n{int(page_size_pt[0])}"
                source = build_source_pdf(
                    self.workspace / f"{name}.pdf", 2, page_size_pt=page_size_pt
                )
                numbered = stamp_book(source, self.workspace / f"{name}-out.pdf")
                expected = ColumnLayout.fitted(page_size_pt[0] / PT_PER_INCH)
                for index, found in enumerate(self.numbers_on(numbered)):
                    left = found[str(2 * index + 1)]
                    right = found[str(2 * index + 2)]
                    self.assertGreater(left[0], expected.page_margin_in * PT_PER_INCH)
                    self.assertLess(left[0], expected.left_column_end_in * PT_PER_INCH)
                    self.assertGreater(
                        right[0], expected.right_column_start_in * PT_PER_INCH
                    )
                    # Inside the column, and therefore inside the page.
                    self.assertLess(right[0], expected.right_column_end_in * PT_PER_INCH)
                    self.assertLess(right[0], page_size_pt[0])

    def test_numbering_does_not_disturb_the_book_itself(self):
        source = build_source_pdf(self.workspace / "src.pdf", 2)
        numbered = stamp_book(source, self.workspace / "numbered.pdf", ColumnLayout())
        original = pypdf.PdfReader(str(source))
        stamped = pypdf.PdfReader(str(numbered))
        for before, after in zip(original.pages, stamped.pages):
            self.assertEqual(float(before.mediabox.width), float(after.mediabox.width))
            self.assertEqual(float(before.mediabox.height), float(after.mediabox.height))
            self.assertEqual(before.rotation, after.rotation)
        # The book's own marks are all still there, alongside the new numbers.
        self.assertTrue({"BP1", "BP2"} <= set(marks_on(stamped.pages[0])))

    def test_a_numbered_book_still_imposes_correctly(self):
        source = build_source_pdf(self.workspace / "src.pdf", 2)
        numbered = stamp_book(source, self.workspace / "numbered.pdf", ColumnLayout())
        reader, = self.impose(numbered, 1, sheet_size_pt=LETTER_LANDSCAPE_PT)
        fit = SheetFit.compute(A4_LANDSCAPE_PT, LETTER_LANDSCAPE_PT, FIT)
        self.assert_marks_where_expected(reader.pages[0], (4, 1), fit)
        # One stamped page number per column, alongside the book's own content.
        numbers = {
            label for label in visible_labels_on(reader.pages[0]) if label.isdigit()
        }
        self.assertEqual(numbers, {"1", "4"})


# --------------------------------------------------------------------------
# The conversion the app actually calls
# --------------------------------------------------------------------------


class TestConvertBook(PdfTestCase):
    """`main.convert_book` end to end, with the app's folders redirected."""

    def setUp(self):
        super().setUp()
        self.output_dir = self.workspace / "Output"
        self.previous_dir = self.workspace / "Previously_Converted"
        for name, value in (("OUTPUT_DIR", self.output_dir),
                            ("PREVIOUS_DIR", self.previous_dir)):
            original = getattr(main, name)
            setattr(main, name, value)
            self.addCleanup(setattr, main, name, original)
        self.source = build_source_pdf(self.workspace / "Book.pdf", 5)

    def signatures_of(self, name="Book"):
        folder = self.output_dir / name / "book_signatures"
        return sorted(folder.glob("signature_*.pdf"),
                      key=lambda p: int(p.stem.split("_")[-1]))

    def test_a_conversion_writes_signatures_and_a_note_about_the_paper(self):
        sheet_size_pt = find_paper_size("Letter").size_pt(True)
        written = main.convert_book(
            self.source, sheets_per_signature=2, sheet_size_pt=sheet_size_pt,
            move_input=False,
        )
        self.assertEqual([path.name for path in written],
                         ["signature_1.pdf", "signature_2.pdf"])
        for path in written:
            for page in pypdf.PdfReader(str(path)).pages:
                self.assertAlmostEqual(float(page.mediabox.width), 792.0, places=3)

        notes = (self.output_dir / "Book" / main.INSTRUCTIONS_NAME).read_text(
            encoding="utf-8-sig"
        )
        # Read with the wrapping taken out. Where the steps break across lines
        # is a matter of how long the words are, and a phrase landing on a line
        # end is not a change in what the note says.
        said = " ".join(notes.split())
        self.assertIn("Letter", said)
        self.assertIn("94.1%", said)           # A4 landscape shrunk onto Letter
        self.assertIn("long edge", said)
        self.assertIn("Actual size", said)

    def test_the_note_and_the_page_give_the_same_instructions(self):
        """The five steps are one list, rendered twice.

        They were written out separately in `main.print_instructions` and in
        app.py's "How to print and fold" expander, so a correction to either one
        left the other saying something else — which is worse than saying it
        once, because both look authoritative.
        """
        # `print_instructions` reads four things off these, and none of them is
        # what this test is about, so they are stood in for rather than measured.
        class Fit:
            sheet_size_in = (11.0, 8.5)
            book_page_size_in = (5.5, 8.5)
            scale = 0.941

        class Plan:
            sheets = 2

        for flip, edge in ((True, "long"), (False, "short")):
            steps = main.printing_steps(flip)
            self.assertEqual(len(steps), 5)

            note = main.print_instructions("Book", Fit(), [Plan()], flip)
            said = " ".join(note.split())
            for number, step in enumerate(steps, 1):
                # The note carries every step, in order, without the markdown.
                self.assertIn(f"{number}. {step.replace('**', '')}", said)
            self.assertNotIn("**", note)
            self.assertIn(f"flip on the {edge} edge", said)

    def test_reconverting_on_smaller_paper_leaves_no_stale_signatures(self):
        main.convert_book(self.source, sheets_per_signature=1, move_input=False)
        self.assertEqual(len(self.signatures_of()), 3)  # 10 book pages, 1 sheet each

        main.convert_book(self.source, sheets_per_signature=5, move_input=False)
        # One big signature now. The three files from last time must be gone, or
        # they would be printed as part of this book.
        self.assertEqual(len(self.signatures_of()), 1)

    def test_a_conversion_that_fails_leaves_the_previous_run_intact(self):
        """The worst thing this app could do: half a book that looks like a book.

        Signatures used to be written straight over the previous run's files,
        after deleting whatever the new run would not overwrite. A conversion
        that failed — or that the GUI stopped part way, which it did every time
        the user clicked anything while one was running — left a folder holding
        some signatures from this book and some from the last, with nothing on
        screen or on disk to say so. It would be printed, folded and bound before
        anyone noticed.
        """
        main.convert_book(self.source, sheets_per_signature=1, move_input=False)
        good = {path.name: path.read_bytes() for path in self.signatures_of()}
        self.assertEqual(len(good), 3)

        with mock.patch.object(
            main, "split_to_signatures", side_effect=RuntimeError("printer on fire")
        ):
            with self.assertRaises(RuntimeError):
                main.convert_book(self.source, sheets_per_signature=5,
                                  move_input=False)

        # Byte for byte what was there before, and no staging folder left over.
        self.assertEqual(
            {path.name: path.read_bytes() for path in self.signatures_of()}, good
        )
        self.assertEqual(list((self.output_dir / "Book").glob("_new_signatures")), [])
        # The input is still where it was, so the failure can simply be retried.
        self.assertTrue(self.source.is_file())

    def test_an_interrupted_conversion_leaves_the_previous_run_intact(self):
        # The GUI stops a running script by raising through it, and that raise is
        # a BaseException — the one kind an `except Exception` would let past.
        main.convert_book(self.source, sheets_per_signature=1, move_input=False)
        good = {path.name: path.read_bytes() for path in self.signatures_of()}

        class Stopped(BaseException):
            pass

        with mock.patch.object(main, "split_to_signatures", side_effect=Stopped):
            with self.assertRaises(Stopped):
                main.convert_book(self.source, sheets_per_signature=5,
                                  move_input=False)

        self.assertEqual(
            {path.name: path.read_bytes() for path in self.signatures_of()}, good
        )
        self.assertEqual(list((self.output_dir / "Book").glob("_new_signatures")), [])

    def test_a_stray_file_does_not_take_the_whole_listing_down(self):
        """`list_ready_books` runs while the GUI is being drawn.

        Reading the tail of every signature_*.pdf name as an integer meant one
        file the user had dropped in the folder raised out of the listing, and
        with it the entire page — not just the book it was in.
        """
        main.convert_book(self.source, sheets_per_signature=2, move_input=False)
        folder = self.output_dir / "Book" / "book_signatures"
        (folder / "signature_notes.pdf").write_bytes(b"%PDF-1.4\n")
        (folder / "signature_2_reprint.pdf").write_bytes(b"%PDF-1.4\n")

        books = main.list_ready_books()
        self.assertEqual([book.name for book in books], ["Book"])
        # Only the app's own files are listed, and still in print order.
        self.assertEqual(
            [path.name for path in books[0].signatures],
            ["signature_1.pdf", "signature_2.pdf"],
        )

    def test_signatures_are_listed_in_print_order_past_nine(self):
        # Sorted as numbers, not as text: "signature_10" sorts before
        # "signature_2" alphabetically, which would misorder a bound book.
        main.convert_book(self.source, sheets_per_signature=1, move_input=False)
        folder = self.output_dir / "Book" / "book_signatures"
        for number in (10, 2):
            shutil.copyfile(folder / "signature_1.pdf", folder / f"signature_{number}.pdf")

        book, = main.list_ready_books()
        self.assertEqual(
            [path.name for path in book.signatures],
            ["signature_1.pdf", "signature_2.pdf", "signature_3.pdf",
             "signature_10.pdf"],
        )

    def test_a_book_that_will_not_fit_the_paper_is_refused_before_writing(self):
        sheet_size_pt = find_paper_size("A6").size_pt(True)
        with self.assertRaises(ValueError) as caught:
            main.convert_book(
                self.source, sheet_size_pt=sheet_size_pt, scale_mode=ACTUAL_SIZE,
                move_input=False,
            )
        self.assertIn("does not fit", str(caught.exception))
        self.assertEqual(self.signatures_of(), [])

    def test_the_column_layout_cannot_change_a_conversion(self):
        """The reason conversion no longer inspects the layout at all.

        A layout that is nonsense for this book, one fitted to it, and none at
        all must all impose to byte-identical signatures — because the fold is
        the midpoint of the source page by definition and no column measurement
        is ever consulted. Refusing a book over one was a false alarm: the user
        could neither act on it nor observe any difference from obeying it.
        """
        def signature_bytes(**kwargs):
            main.convert_book(self.source, move_input=False, **kwargs)
            return [path.read_bytes() for path in self.signatures_of()]

        baseline = signature_bytes()
        self.assertTrue(baseline)
        # Columns wider than the page, crossing the fold, and margin-less.
        for layout in (
            ColumnLayout(column_width_in=8.0),
            ColumnLayout(page_margin_in=0.0, column_gap_in=0.0, column_width_in=0.1),
            ColumnLayout.fitted(A4_LANDSCAPE_PT[0] / PT_PER_INCH),
        ):
            self.assertEqual(signature_bytes(layout=layout), baseline)

    def test_a_letter_landscape_book_converts_with_the_defaults(self):
        """The reported regression, end to end.

        An 11 x 8.5 in (Letter landscape) book against the shipped A4 column
        numbers was refused outright, with the button greyed out and two red
        errors, despite the imposition never reading those numbers.
        """
        letter_book = build_source_pdf(
            self.workspace / "Letter.pdf", 5, page_size_pt=LETTER_LANDSCAPE_PT
        )
        written = main.convert_book(letter_book, sheets_per_signature=2,
                                    move_input=False)
        self.assertEqual(len(written), 2)
        for path in written:
            for page in pypdf.PdfReader(str(path)).pages:
                self.assertAlmostEqual(float(page.mediabox.width), 792.0, places=3)
                self.assertAlmostEqual(float(page.mediabox.height), 612.0, places=3)

    def test_the_input_is_archived_only_when_asked(self):
        main.convert_book(self.source, move_input=False)
        self.assertTrue(self.source.exists())
        main.convert_book(self.source, move_input=True)
        self.assertFalse(self.source.exists())
        self.assertTrue((self.previous_dir / "Book.pdf").exists())

    def test_resolving_the_paper_argument(self):
        self.assertIsNone(main._resolve_sheet_size("same", True))
        self.assertIsNone(main._resolve_sheet_size(None, True))
        width_pt, height_pt = main._resolve_sheet_size("Letter", True)
        self.assertAlmostEqual(width_pt, 792.0, places=6)
        self.assertAlmostEqual(height_pt, 612.0, places=6)
        width_pt, height_pt = main._resolve_sheet_size("Letter", False)
        self.assertAlmostEqual(width_pt, 612.0, places=6)


class TestCommandLine(PdfTestCase):
    """`python main.py` itself — the half of the app with no GUI to check it."""

    def setUp(self):
        super().setUp()
        for name in ("INPUT_DIR", "OUTPUT_DIR", "PREVIOUS_DIR"):
            folder = self.workspace / name
            folder.mkdir(parents=True, exist_ok=True)
            self.addCleanup(setattr, main, name, getattr(main, name))
            setattr(main, name, folder)
        self.input_dir = main.INPUT_DIR

    def test_a_run_converts_every_book_waiting_in_input(self):
        build_source_pdf(self.input_dir / "Book.pdf", 5)
        main.main(["--sheets", "2", "--paper", "Letter"])

        folder = main.OUTPUT_DIR / "Book" / "book_signatures"
        self.assertEqual(
            sorted(path.name for path in folder.glob("*.pdf")),
            ["signature_1.pdf", "signature_2.pdf"],
        )
        for path in folder.glob("*.pdf"):
            for page in pypdf.PdfReader(str(path)).pages:
                self.assertAlmostEqual(float(page.mediabox.width), 792.0, places=3)
        # And the book it came from has been archived.
        self.assertTrue((main.PREVIOUS_DIR / "Book.pdf").is_file())

    def test_numbering_from_the_command_line_fits_each_book_to_its_own_page(self):
        """The default has to suit whatever page size is in the folder.

        Both books are numbered in one run, and a Letter book measured against
        the shipped A4 column width would put its right-hand number off the
        edge of its own page.
        """
        build_source_pdf(self.input_dir / "A4.pdf", 2)
        build_source_pdf(self.input_dir / "Letter.pdf", 2,
                         page_size_pt=LETTER_LANDSCAPE_PT)
        main.main(["--number"])

        for name, page_size_pt in (("A4", A4_LANDSCAPE_PT),
                                   ("Letter", LETTER_LANDSCAPE_PT)):
            with self.subTest(book=name):
                numbered = self.input_dir / f"{name}{main.NUMBERED_SUFFIX}.pdf"
                self.assertTrue(numbered.is_file())
                labels = {
                    label for page in pypdf.PdfReader(str(numbered)).pages
                    for label, _point, _visible in read_text_runs(page)
                }
                self.assertTrue({"1", "2", "3", "4"} <= labels)
                for page in pypdf.PdfReader(str(numbered)).pages:
                    for label, (x, _y), _visible in read_text_runs(page):
                        if label.isdigit():
                            self.assertLess(x, page_size_pt[0])

    def test_an_already_numbered_book_is_not_numbered_again(self):
        build_source_pdf(self.input_dir / f"Book{main.NUMBERED_SUFFIX}.pdf", 2)
        main.main(["--number"])
        self.assertEqual(
            sorted(path.name for path in self.input_dir.glob("*.pdf")),
            [f"Book{main.NUMBERED_SUFFIX}.pdf"],
        )

    def test_an_empty_input_folder_is_said_rather_than_crashed_on(self):
        main.main([])
        self.assertEqual(list(main.OUTPUT_DIR.iterdir()), [])


class TestOneRunIsOneSetOfFiles(unittest.TestCase):
    """What the download on a finished book is handed.

    There is no "open the folder" any more: the app holds a book only while
    the tab is open, so the way out is a file the browser is given. What that
    download must contain is every signature of the run and nothing else.
    """

    def test_every_signature_of_a_run_lands_in_the_one_folder(self):
        with tempfile.TemporaryDirectory() as workspace:
            workspace = Path(workspace)
            original = main.OUTPUT_DIR
            main.OUTPUT_DIR = workspace / "Output"
            self.addCleanup(setattr, main, "OUTPUT_DIR", original)

            # 6 source pages -> 12 book pages -> a full 2-sheet signature and a
            # 1-sheet remainder, so the folder holds more than one file.
            source = build_source_pdf(workspace / "Book.pdf", 6)
            written = main.convert_book(source, sheets_per_signature=2,
                                        move_input=False)
            self.assertEqual(len(written), 2)
            folder = written[0].parent
            self.assertEqual(
                sorted(p.name for p in folder.glob("signature_*.pdf")),
                sorted(p.name for p in written),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
