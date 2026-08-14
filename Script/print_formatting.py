"""Turn a 2-column PDF book into printable, foldable signatures.

Terminology used throughout, kept deliberately explicit because the three
different notions of "page" are what make imposition confusing:

  source page  - one page of the input PDF. Holds two book pages side by side.
  book page    - one page as the reader sees it. One column of a source page.
  sheet        - one physical piece of paper. Printed duplex and folded once,
                 it carries 4 book pages (2 per side).
  side         - one face of a sheet, i.e. one page of the output PDF.
                 A signature of N sheets has 2N sides and 4N book pages.

The source page size and the sheet size are independent. By default the sheet
matches the source page, which is what an A4-in/A4-out book wants, but any sheet
size from `paper_sizes.SHEET_SIZES` (or any custom size) can be printed on
instead: each column is then scaled and centred into its half of the sheet.
"""

import io
import math
from dataclasses import dataclass
from pathlib import Path

import pypdf
from pypdf.generic import RectangleObject
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas as RL_Canvas

# Units live in paper_sizes, next to the sizes they measure, but they were part
# of this module's interface first, so they stay importable from here too.
from Script.paper_sizes import (  # noqa: F401  (re-exported)
    CENTIMETRES,
    CM_PER_INCH,
    INCHES,
    MILLIMETRES,
    MM_PER_INCH,
    PT_PER_INCH,
    Unit,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
FONT_PATH = ROOT_DIR / "Script" / "Baskervville-Regular.ttf"
FONT_NAME = "Baskervville"  # https://fonts.google.com/specimen/Baskervville
FONT_SIZE = 10

# How a page number is placed, for both halves of the app. See `draw_folio`.
FOLIO_MARGIN_SHARE = 0.40      # how far up the bottom margin the baseline sits
FOLIO_MIN_BASELINE_PT = 9.0    # but never closer than this to the paper edge

# Scale modes for putting a source page onto a sheet of a different size.
FIT = "fit"
ACTUAL_SIZE = "actual"
SCALE_MODES = (FIT, ACTUAL_SIZE)

SCALE_MODE_LABELS = {
    FIT: "Fit each book page to the sheet",
    ACTUAL_SIZE: "Keep the original size, centred",
}

# Almost no desktop printer can put ink closer than about this to the paper
# edge, so content inside this band is a warning, not an error.
UNPRINTABLE_EDGE_IN = 0.25

_font_registered = False


def _ensure_font():
    global _font_registered
    if not _font_registered:
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))
        _font_registered = True


# --------------------------------------------------------------------------
# Affine helpers
# --------------------------------------------------------------------------
# PDF matrices are the 6-tuple (a, b, c, d, e, f), meaning
#     x' = a*x + c*y + e        y' = b*x + d*y + f
# Placing a column involves two of them - source page to displayed page, then
# displayed page to sheet - so they are composed explicitly rather than by
# chaining pypdf's Transformation builder, whose ordering is easy to get
# backwards and impossible to see in a diff.

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def compose(first, then):
    """The single matrix equivalent to applying `first` and then `then`."""
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = then
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def invert(matrix):
    a, b, c, d, e, f = matrix
    determinant = a * d - b * c
    if abs(determinant) < 1e-12:
        raise ValueError("This page transform cannot be inverted.")
    return (
        d / determinant,
        -b / determinant,
        -c / determinant,
        a / determinant,
        (c * f - d * e) / determinant,
        (b * e - a * f) / determinant,
    )


def apply_matrix(matrix, point):
    a, b, c, d, e, f = matrix
    x, y = point
    return (a * x + c * y + e, b * x + d * y + f)


def _bounding_box(matrix, rect):
    """The axis-aligned box that `rect` maps to. Exact for 90-degree rotations."""
    left, bottom, right, top = rect
    corners = [
        apply_matrix(matrix, point)
        for point in ((left, bottom), (right, bottom), (right, top), (left, top))
    ]
    xs = [x for x, _ in corners]
    ys = [y for _, y in corners]
    return (min(xs), min(ys), max(xs), max(ys))


# --------------------------------------------------------------------------
# Reading a source page
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PageGeometry:
    """Where a source page's *visible* rectangle is, and how big it looks.

    Three things stand between a PDF page and what a reader sees, and all three
    have to be undone before the column split makes sense:

      * the mediabox need not start at (0, 0);
      * a cropbox may hide part of the mediabox, and it is the cropbox that a
        viewer shows and therefore the one the user measured their margins
        against;
      * /Rotate turns the page for display without touching its content, so a
        landscape scan is often a portrait page rotated 90 degrees.

    `to_visible` maps source coordinates onto a displayed page whose lower-left
    corner is the origin and whose size is (width_pt, height_pt).
    """

    left: float
    bottom: float
    right: float
    top: float
    rotation: int
    is_cropped: bool

    @classmethod
    def of(cls, page):
        media = RectangleObject(page.mediabox)
        crop = RectangleObject(page.cropbox)
        box = (
            max(float(media.left), float(crop.left)),
            max(float(media.bottom), float(crop.bottom)),
            min(float(media.right), float(crop.right)),
            min(float(media.top), float(crop.top)),
        )
        # A cropbox outside the mediabox is invalid but does occur; fall back to
        # the mediabox rather than producing an empty page.
        if box[2] - box[0] <= 0 or box[3] - box[1] <= 0:
            box = (
                float(media.left), float(media.bottom),
                float(media.right), float(media.top),
            )
        cropped = any(
            abs(a - b) > 0.5
            for a, b in zip(box, (float(media.left), float(media.bottom),
                                  float(media.right), float(media.top)))
        )
        # /Rotate is required to be a multiple of 90; round rather than trust it.
        rotation = (round(page.rotation / 90) * 90) % 360
        return cls(box[0], box[1], box[2], box[3], rotation, cropped)

    @property
    def box_width_pt(self):
        return self.right - self.left

    @property
    def box_height_pt(self):
        return self.top - self.bottom

    @property
    def is_turned(self):
        return self.rotation in (90, 270)

    @property
    def width_pt(self):
        """Width as displayed, i.e. after /Rotate."""
        return self.box_height_pt if self.is_turned else self.box_width_pt

    @property
    def height_pt(self):
        return self.box_width_pt if self.is_turned else self.box_height_pt

    @property
    def to_visible(self):
        """Source coordinates -> displayed coordinates with the origin at the
        bottom-left of the displayed page."""
        left, bottom, right, top = self.left, self.bottom, self.right, self.top
        if self.rotation == 90:
            # Displayed 90 degrees clockwise: the source's +y becomes screen +x.
            return (0.0, -1.0, 1.0, 0.0, -bottom, right)
        if self.rotation == 180:
            return (-1.0, 0.0, 0.0, -1.0, right, top)
        if self.rotation == 270:
            return (0.0, 1.0, -1.0, 0.0, top, -left)
        return (1.0, 0.0, 0.0, 1.0, -left, -bottom)

    def column_box(self, right_column):
        """The source-coordinate rectangle holding one displayed half.

        Returned as a cropbox, because narrowing the cropbox is how pypdf is
        told to clip a merged page to part of itself.
        """
        half = self.width_pt / 2
        start = half if right_column else 0.0
        visible_rect = (start, 0.0, start + half, self.height_pt)
        left, bottom, right, top = _bounding_box(invert(self.to_visible), visible_rect)
        # Clamp away floating-point overshoot so the clip never reaches outside
        # the page it came from.
        return RectangleObject((
            max(left, self.left),
            max(bottom, self.bottom),
            min(right, self.right),
            min(top, self.top),
        ))


@dataclass(frozen=True)
class BookInfo:
    source_pages: int
    page_width_pt: float
    page_height_pt: float
    uniform_page_size: bool
    rotated_pages: bool = False
    cropped_pages: bool = False

    @property
    def book_pages(self):
        return self.source_pages * 2

    @property
    def page_size_in(self):
        return (self.page_width_pt / PT_PER_INCH, self.page_height_pt / PT_PER_INCH)

    @property
    def book_page_size_in(self):
        """What one book page measures on the source page, before any resizing."""
        width_in, height_in = self.page_size_in
        return (width_in / 2, height_in)


def inspect_book(pdf_path):
    reader = pypdf.PdfReader(str(pdf_path))
    if not reader.pages:
        raise ValueError("This PDF has no pages.")
    geometries = [PageGeometry.of(page) for page in reader.pages]
    first = geometries[0]
    uniform = all(
        abs(g.width_pt - first.width_pt) < 0.5 and abs(g.height_pt - first.height_pt) < 0.5
        for g in geometries
    )
    return BookInfo(
        source_pages=len(geometries),
        page_width_pt=first.width_pt,
        page_height_pt=first.height_pt,
        uniform_page_size=uniform,
        rotated_pages=any(g.rotation for g in geometries),
        cropped_pages=any(g.is_cropped for g in geometries),
    )


# --------------------------------------------------------------------------
# Column layout on the source page
# --------------------------------------------------------------------------


# When a page is too narrow for the margin and gap asked for, this is the share
# of it the columns keep; the margin and gap are shrunk in proportion to free it.
MIN_COLUMN_SHARE = 0.5


@dataclass(frozen=True)
class ColumnLayout:
    """Where the two columns sit on a source page, in inches.

    Only page *numbering* reads these measurements. Imposition does not: the fold
    is the middle of the source page by definition, so `PageGeometry.column_box`
    always splits at exactly half the page width and never consults a layout. A
    layout that does not match the book therefore stamps numbers in the wrong
    place, and can do nothing else — in particular it cannot move the fold or
    mis-cut a column, so it is never a reason to refuse a conversion.
    """

    page_margin_in: float = 0.5
    column_gap_in: float = 0.99
    column_width_in: float = 4.85

    @property
    def left_column_end_in(self):
        return self.page_margin_in + self.column_width_in

    @property
    def right_column_start_in(self):
        return self.left_column_end_in + self.column_gap_in

    @property
    def right_column_end_in(self):
        return self.right_column_start_in + self.column_width_in

    @classmethod
    def fitted(cls, page_width_in, page_margin_in=None, column_gap_in=None):
        """The widest symmetric two-column layout that fits `page_width_in`.

        The class defaults are the numbers that fill an A4 landscape page exactly,
        so any other page size needs its column width recomputed — this is what
        the app uses for every book it opens. Margin and gap are kept as given and
        only the column width moves, because those two are the measurements a user
        actually has an opinion about.

        Total by construction: a page too narrow for the requested margin and gap
        shrinks both in proportion instead of raising. This is a fallback that has
        to produce an answer for whatever PDF is handed in, and a page merely
        being small is a fact about the book, not an error to report at the user.
        """
        margin = max(float(cls.page_margin_in if page_margin_in is None
                           else page_margin_in), 0.0)
        gap = max(float(cls.column_gap_in if column_gap_in is None
                        else column_gap_in), 0.0)
        page_width_in = max(float(page_width_in), 0.0)

        furniture = 2 * margin + gap
        budget = page_width_in * (1.0 - MIN_COLUMN_SHARE)
        if furniture > budget:
            shrink = budget / furniture if furniture > 0 else 0.0
            margin *= shrink
            gap *= shrink
        return cls(margin, gap, (page_width_in - 2 * margin - gap) / 2)


def layout_problems(layout, page_width_pt, unit=INCHES):
    """Ways this layout disagrees with the page it claims to describe.

    Each sheet is folded down the middle, so the split always falls at exactly
    half the source page width, whatever this layout says. A layout that puts a
    column across that midpoint is therefore describing the *book* badly, not
    breaking the imposition — the fold lands in the same place either way.

    So these are reasons the stamped page numbers would land somewhere silly, and
    nothing more. `stamp_book` is the only consumer that acts on them; conversion
    deliberately ignores them, because refusing a book over a measurement the
    imposition never reads is a false alarm the user cannot act on.
    """
    problems = []
    page_width_in = page_width_pt / PT_PER_INCH
    fold_in = page_width_in / 2
    eps = 1e-6

    if layout.column_width_in <= 0:
        problems.append(f"Column width must be greater than 0 {unit.name}.")
    if layout.page_margin_in < 0:
        problems.append("Page margin cannot be negative.")
    if layout.column_gap_in < 0:
        problems.append("Column gap cannot be negative.")
    if problems:
        return problems

    if layout.right_column_end_in > page_width_in + eps:
        problems.append(
            f"The columns need {unit.label(layout.right_column_end_in)} of width but the "
            f"source page is only {unit.label(page_width_in)} wide. Reduce the margin, "
            f"the gap, or the column width."
        )
    if layout.left_column_end_in > fold_in + eps:
        problems.append(
            f"The left column ends at {unit.label(layout.left_column_end_in)}, past the "
            f"fold at {unit.label(fold_in)}. The fold would cut through it."
        )
    if layout.right_column_start_in < fold_in - eps:
        problems.append(
            f"The right column starts at {unit.label(layout.right_column_start_in)}, before "
            f"the fold at {unit.label(fold_in)}. The fold would cut through it."
        )
    return problems


# Round-tripping a length through a millimetre spin box costs a few thousandths
# of an inch, twice over for two columns. Anything under this is that, not a
# layout the user should hear about.
SYMMETRY_TOLERANCE_IN = 0.02


def layout_warnings(layout, page_width_pt, unit=INCHES):
    """Things that still print, but probably are not what the user meant.

    Like `layout_problems`, this is about where page numbers would be stamped.
    An asymmetric layout has no effect at all on a conversion.
    """
    warnings = []
    page_width_in = page_width_pt / PT_PER_INCH
    right_margin_in = page_width_in - layout.right_column_end_in
    if abs(right_margin_in - layout.page_margin_in) > SYMMETRY_TOLERANCE_IN:
        warnings.append(
            f"These column measurements are not symmetric on this book's "
            f"{unit.label(page_width_in)} page: a left margin of "
            f"{unit.label(layout.page_margin_in)} leaves "
            f"{unit.label(right_margin_in)} on the right. Page numbers would sit "
            f"off-centre; the fold and the imposition are unaffected."
        )
    return warnings


# --------------------------------------------------------------------------
# Fitting a source page onto a sheet
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SheetFit:
    """How one source page is placed on one sheet of paper.

    The fold is always the vertical centre line of the sheet, so the source
    page's left half goes on the sheet's left half and its right half on the
    right. Each half is scaled by the same factor and centred in its own half of
    the sheet, which keeps the fold exactly in the middle and gives the two
    facing pages identical treatment - centring the whole source page instead
    would move both book pages towards one edge.
    """

    source_width_pt: float
    source_height_pt: float
    sheet_width_pt: float
    sheet_height_pt: float
    scale: float

    @classmethod
    def compute(cls, source_size_pt, sheet_size_pt=None, scale_mode=FIT):
        source_width_pt, source_height_pt = source_size_pt
        if source_width_pt <= 0 or source_height_pt <= 0:
            raise ValueError("The source page has no area.")
        sheet_width_pt, sheet_height_pt = sheet_size_pt or source_size_pt
        if sheet_width_pt <= 0 or sheet_height_pt <= 0:
            raise ValueError("The sheet size has to be larger than zero.")

        if scale_mode == ACTUAL_SIZE:
            scale = 1.0
        elif scale_mode == FIT:
            # Halving both widths cancels, so this is the same factor whether it
            # is derived from the whole page or from one column.
            scale = min(sheet_width_pt / source_width_pt, sheet_height_pt / source_height_pt)
        else:
            raise ValueError(
                f"Unknown scale mode “{scale_mode}”. Use one of: {', '.join(SCALE_MODES)}."
            )
        return cls(source_width_pt, source_height_pt, sheet_width_pt, sheet_height_pt, scale)

    @property
    def source_half_width_pt(self):
        return self.source_width_pt / 2

    @property
    def sheet_half_width_pt(self):
        return self.sheet_width_pt / 2

    @property
    def content_width_pt(self):
        return self.source_half_width_pt * self.scale

    @property
    def content_height_pt(self):
        return self.source_height_pt * self.scale

    @property
    def margin_x_pt(self):
        """Blank paper added left and right of one book page. Negative = clipped."""
        return (self.sheet_half_width_pt - self.content_width_pt) / 2

    @property
    def margin_y_pt(self):
        return (self.sheet_height_pt - self.content_height_pt) / 2

    @property
    def overflows(self):
        return self.margin_x_pt < -1e-6 or self.margin_y_pt < -1e-6

    @property
    def is_resized(self):
        return abs(self.scale - 1.0) > 1e-6

    @property
    def sheet_size_in(self):
        return (self.sheet_width_pt / PT_PER_INCH, self.sheet_height_pt / PT_PER_INCH)

    @property
    def book_page_size_in(self):
        """The untrimmed book page: half a sheet."""
        return (self.sheet_half_width_pt / PT_PER_INCH, self.sheet_height_pt / PT_PER_INCH)

    @property
    def content_size_in(self):
        return (self.content_width_pt / PT_PER_INCH, self.content_height_pt / PT_PER_INCH)

    def placement(self, from_right_column, to_right_column):
        """Displayed source coordinates -> sheet coordinates, for one column."""
        column_start = self.source_half_width_pt if from_right_column else 0.0
        sheet_start = self.sheet_half_width_pt if to_right_column else 0.0
        return (
            self.scale, 0.0, 0.0, self.scale,
            sheet_start + self.margin_x_pt - self.scale * column_start,
            self.margin_y_pt,
        )


def sheet_problems(fit, unit=INCHES):
    """Reasons this sheet size cannot hold the book at all."""
    problems = []
    if not fit.overflows:
        return problems
    page_w, page_h = fit.book_page_size_in
    content_w, content_h = fit.content_size_in
    problems.append(
        f"At its original size each book page is {unit.size_label(content_w, content_h)}, "
        f"which does not fit the {unit.size_label(page_w, page_h)} half-sheet it has to "
        f"go on. Switch to “{SCALE_MODE_LABELS[FIT]}” or choose a larger sheet."
    )
    return problems


def sheet_warnings(fit, layout=None, unit=INCHES):
    """Things that print, but that the user should know before wasting paper."""
    warnings = []
    if fit.overflows:
        return warnings  # sheet_problems already said the real thing

    page_w, page_h = fit.book_page_size_in

    if fit.is_resized:
        percent = fit.scale * 100
        direction = "enlarged" if fit.scale > 1 else "shrunk"
        extra = (
            " Enlarging a scan magnifies its blur and speckle as well as its text."
            if fit.scale > 1.02
            else ""
        )
        warnings.append(
            f"Every book page is {direction} to {percent:.1f}% to fit this sheet, so the "
            f"printed type changes size too. Print at 100% / “Actual size”: letting the "
            f"printer scale as well would compound the two.{extra}"
        )

    margin_x_in = fit.margin_x_pt / PT_PER_INCH
    margin_y_in = fit.margin_y_pt / PT_PER_INCH
    if margin_x_in > 0.02 and margin_y_in > 0.02:
        warnings.append(
            f"The sheet is not the same shape as the source page, so each book page "
            f"gains {unit.label(margin_x_in)} of blank paper on each side and "
            f"{unit.label(margin_y_in)} above and below."
        )
    elif margin_y_in > 0.02:
        warnings.append(
            f"The sheet is proportionally taller than the source page, so each book page "
            f"gains {unit.label(margin_y_in)} of blank paper above and below."
        )
    elif margin_x_in > 0.02:
        warnings.append(
            f"The sheet is proportionally wider than the source page, so each book page "
            f"gains {unit.label(margin_x_in)} of blank paper on each side."
        )

    if layout is not None:
        # Distance from the paper's outer edge to the outermost ink, which is
        # where the printer's unprintable border bites first.
        outer_ink_in = layout.page_margin_in * fit.scale + margin_x_in
        if outer_ink_in < UNPRINTABLE_EDGE_IN:
            warnings.append(
                f"Only {unit.label(outer_ink_in)} separates the outer edge of the text "
                f"from the edge of the paper. Most printers cannot print within "
                f"{unit.label(UNPRINTABLE_EDGE_IN)} of an edge, so the outer margin may "
                f"be clipped. Widen the outer page margin, or use a larger sheet."
            )

    if page_h > 0 and page_w / page_h < 0.55:
        warnings.append(
            f"Folded, this sheet gives book pages of {unit.size_label(page_w, page_h)}, "
            f"which is unusually tall and narrow. If that is not what you meant, the sheet is "
            f"probably the wrong way round: a sheet is folded across its width, so it "
            f"wants its long edge horizontal."
        )

    if page_w < 2.5 or page_h < 3.5:
        warnings.append(
            f"A finished page of {unit.size_label(page_w, page_h)} is very small; text "
            f"laid out for a larger page will be hard to read at this size."
        )

    sheet_w, sheet_h = fit.sheet_size_in
    if max(sheet_w, sheet_h) > 19.0 or min(sheet_w, sheet_h) > 13.0:
        warnings.append(
            f"A {unit.size_label(sheet_w, sheet_h)} sheet is larger than Super B / A3+, "
            f"so it needs a wide-format printer or a print shop."
        )
    return warnings


# --------------------------------------------------------------------------
# Planning the signatures
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SignaturePlan:
    number: int
    first_book_page: int
    sheets: int

    @property
    def capacity(self):
        """Book pages this signature can hold; always a multiple of 4."""
        return self.sheets * 4

    @property
    def sides(self):
        return self.sheets * 2

    @property
    def last_book_page(self):
        return self.first_book_page + self.capacity - 1


def plan_signatures(book_pages, sheets_per_signature):
    """Split a book into signatures of whole sheets.

    Full signatures take `sheets_per_signature` sheets each. Whatever is left
    over becomes one final, smaller signature, sized to the fewest whole sheets
    that hold it. Any unused book pages land at the very end of that last
    signature, so blanks fall at the back of the book instead of in the middle
    of the final gathering.
    """
    if sheets_per_signature < 1:
        raise ValueError("A signature needs at least 1 sheet of paper.")
    if book_pages < 1:
        raise ValueError("The book has no pages.")

    plans = []
    first_page = 1
    remaining = book_pages
    while remaining > 0:
        sheets = (
            sheets_per_signature
            if remaining >= sheets_per_signature * 4
            else math.ceil(remaining / 4)
        )
        plan = SignaturePlan(len(plans) + 1, first_page, sheets)
        plans.append(plan)
        first_page += plan.capacity
        remaining -= plan.capacity
    return plans


def signature_sides(plan):
    """The (left_book_page, right_book_page, is_back_of_sheet) for each side.

    Side order is front of sheet 1, back of sheet 1, front of sheet 2, ... which
    is the order a duplex printer consumes. Within a folded signature the
    outermost sheet carries the first and last pages and the innermost sheet
    carries the centre spread, so side j pairs local page j with local page
    capacity - j + 1.
    """
    for j in range(1, plan.sides + 1):
        near = plan.first_book_page + j - 1
        far = plan.first_book_page + plan.capacity - j
        is_back = j % 2 == 0
        # Front sides read [far | near]; the sheet flips for the back.
        left, right = (near, far) if is_back else (far, near)
        yield left, right, is_back


# --------------------------------------------------------------------------
# Page numbering
# --------------------------------------------------------------------------
# Both halves of the app number pages, and they must do it the same way: a
# reader flicking between a converted PDF and a book typed into the editor
# should not be able to tell which is which. So there is exactly one routine
# that places a number — `draw_folio` — and both call it.
#
# The rule it implements: **centred at the foot of the book page it belongs
# to**, on a baseline set as a share of that page's bottom margin, so a book
# with generous margins carries its numbers a little further up the page. Each
# caller supplies the rectangle it knows about — the typesetter one book page of
# the spread it is setting, `stamp_book` one column of a PDF somebody else made.


def folio_baseline_pt(bottom_margin_pt):
    """How far above the foot of a book page its number's baseline sits."""
    return max(bottom_margin_pt * FOLIO_MARGIN_SHARE, FOLIO_MIN_BASELINE_PT)


def folio_fits(bottom_margin_pt, font_size_pt):
    """Is there room for a number in this bottom margin without touching text?"""
    return (folio_baseline_pt(bottom_margin_pt) + font_size_pt * 0.75
            <= bottom_margin_pt - 1.0)


def draw_folio(canvas, number, page_left_pt, page_width_pt, bottom_margin_pt,
               font_name=FONT_NAME, font_size_pt=FONT_SIZE):
    """Draw one page number, centred at the foot of one book page.

    `page_left_pt` and `page_width_pt` describe that book page in the canvas's
    own coordinates; `bottom_margin_pt` is the blank strip under its text.
    """
    canvas.setFont(font_name, font_size_pt)
    canvas.drawCentredString(
        page_left_pt + page_width_pt / 2,
        folio_baseline_pt(bottom_margin_pt),
        str(number),
    )


def _page_number_overlay(width_pt, height_pt, layout, left_number, right_number):
    """A transparent page carrying two numbers, one under each column.

    The book page here is a column of the source page, so the layout is what
    says where it is — that is the one thing column measurements are for.
    Vertically there is nothing else to go on, so the outer page margin stands
    in for the bottom margin: a book set with wide margins gets its numbers
    further up, which is the same rule the typesetter follows.
    """
    _ensure_font()
    buffer = io.BytesIO()
    canvas = RL_Canvas(buffer, pagesize=(width_pt, height_pt))
    bottom_margin_pt = max(layout.page_margin_in, 0.0) * PT_PER_INCH
    column_width_pt = layout.column_width_in * PT_PER_INCH
    for number, left_in in (
        (left_number, layout.page_margin_in),
        (right_number, layout.right_column_start_in),
    ):
        draw_folio(
            canvas, number, left_in * PT_PER_INCH, column_width_pt,
            bottom_margin_pt,
        )
    canvas.save()
    buffer.seek(0)
    return pypdf.PdfReader(buffer).pages[0]


def stamp_book(book_file, numbered_pdf_output, layout=None, progress=None):
    """Stamp book page numbers centred at the foot of each column.

    Source page i carries book pages 2i-1 and 2i, so the numbers run 1, 2 on the
    first source page, 3, 4 on the second, and so on. They are placed by
    `draw_folio`, which is also what the editor's typesetter uses, so a
    converted book and a typed one carry their numbers in the same place.

    `layout` says where the columns are. Leave it None — the normal case — and
    each page gets a layout fitted to its own width, which is right for any page
    size and stays right through a book whose pages are not all the same size.
    Pass one only to override where the numbers sit.

    The numbers are drawn on a sheet the size of the *displayed* page and then
    mapped back into the page's own coordinates, so they land in the right place
    on pages whose mediabox does not start at (0, 0), whose cropbox hides part of
    the mediabox, or which carry a /Rotate.
    """
    writer = pypdf.PdfWriter(clone_from=str(book_file))
    total = len(writer.pages)
    if not total:
        raise ValueError("This PDF has no pages.")
    for i, page in enumerate(writer.pages):
        geometry = PageGeometry.of(page)
        overlay = _page_number_overlay(
            geometry.width_pt,
            geometry.height_pt,
            layout or ColumnLayout.fitted(geometry.width_pt / PT_PER_INCH),
            2 * i + 1,
            2 * i + 2,
        )
        page.merge_transformed_page(
            overlay,
            pypdf.Transformation(invert(geometry.to_visible)),
            over=True,
        )
        if progress:
            progress((i + 1) / total, f"Numbering source page {i + 1} of {total}")

    Path(numbered_pdf_output).parent.mkdir(parents=True, exist_ok=True)
    with open(numbered_pdf_output, "wb") as stream:
        writer.write(stream)
    return Path(numbered_pdf_output)


# --------------------------------------------------------------------------
# Imposition
# --------------------------------------------------------------------------


def _merge_column(target, source_page, geometry, fit, from_right_column, to_right_column):
    """Copy one column of `source_page` onto one half of `target`.

    pypdf clips a merged page to its cropbox and transforms that clip along with
    the content, so narrowing the cropbox to a single column is what isolates it.
    The source page is read, never mutated destructively: the cropbox is reset on
    every call and the content stream is left alone. Transforming a page in place
    would corrupt it for the other column, because two pypdf page objects cloned
    from one reader share a single content stream.
    """
    source_page.cropbox = geometry.column_box(from_right_column)
    matrix = compose(
        geometry.to_visible,
        fit.placement(from_right_column, to_right_column),
    )
    target.merge_transformed_page(source_page, pypdf.Transformation(matrix))


def _place_book_page(
    target, reader, geometries, book_page, book_pages, to_right_column,
    sheet_size_pt, scale_mode,
):
    if book_page > book_pages:
        return  # Padding at the back of the book; leave the half blank.
    index = (book_page - 1) // 2
    geometry = geometries[index]
    fit = SheetFit.compute(
        (geometry.width_pt, geometry.height_pt), sheet_size_pt, scale_mode
    )
    _merge_column(
        target,
        reader.pages[index],
        geometry,
        fit,
        from_right_column=(book_page - 1) % 2 == 1,
        to_right_column=to_right_column,
    )


def split_to_signatures(
    book_file,
    sheets_per_signature,
    signature_save_folder,
    flip_on_long_edge=True,
    sheet_size_pt=None,
    scale_mode=FIT,
    progress=None,
):
    """Impose a 2-column book into one printable PDF per signature.

    Each output PDF is printed duplex, and every sheet is then folded in half and
    nested inside the previous one to form the signature.

    `sheet_size_pt` is the (width, height) of the paper to print on, in points.
    Leave it None to keep the source page's own size, which is the no-op case:
    the fit is then exactly 1:1 and nothing is scaled or moved. Give it any other
    size and each column is scaled by `scale_mode` and centred in its half of the
    sheet. The fit is recomputed per source page, so a book whose pages are not
    all the same size still lands squarely on uniform sheets.

    `flip_on_long_edge` must match the printer's duplex setting. The sheets are
    landscape, so their long edge is the top, and a long-edge duplex flip turns
    the back of the sheet upside down relative to the front. Rotating the back
    sides by 180 degrees cancels that out. Set it False for short-edge duplex,
    which needs no rotation.
    """
    reader = pypdf.PdfReader(str(book_file))
    if not reader.pages:
        raise ValueError("This PDF has no pages.")
    # Snapshot every page's geometry before any merging: _merge_column rewrites
    # cropboxes, and the original cropbox is part of what defines a page.
    geometries = [PageGeometry.of(page) for page in reader.pages]
    sheet_width_pt, sheet_height_pt = sheet_size_pt or (
        geometries[0].width_pt, geometries[0].height_pt
    )
    book_pages = len(reader.pages) * 2

    folder = Path(signature_save_folder)
    folder.mkdir(parents=True, exist_ok=True)

    plans = plan_signatures(book_pages, sheets_per_signature)
    total_sides = sum(plan.sides for plan in plans)
    written = []
    sides_done = 0

    for plan in plans:
        writer = pypdf.PdfWriter()
        for left, right, is_back in signature_sides(plan):
            sheet = pypdf.PageObject.create_blank_page(
                width=sheet_width_pt, height=sheet_height_pt
            )
            for book_page, to_right in ((left, False), (right, True)):
                _place_book_page(
                    sheet, reader, geometries, book_page, book_pages, to_right,
                    (sheet_width_pt, sheet_height_pt), scale_mode,
                )
            if is_back and flip_on_long_edge:
                sheet.rotate(180)
            writer.add_page(sheet)

            sides_done += 1
            if progress:
                progress(
                    sides_done / total_sides,
                    f"Signature {plan.number}: imposed pages {left} and {right}",
                )

        output_path = folder / f"signature_{plan.number}.pdf"
        with open(output_path, "wb") as stream:
            writer.write(stream)
        written.append(output_path)

    return written
