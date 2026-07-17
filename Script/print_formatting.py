"""Turn a 2-column PDF book into printable, foldable signatures.

Terminology used throughout, kept deliberately explicit because the three
different notions of "page" are what make imposition confusing:

  source page  - one page of the input PDF. Holds two book pages side by side.
  book page    - one page as the reader sees it. One column of a source page.
  sheet        - one physical piece of paper. Printed duplex and folded once,
                 it carries 4 book pages (2 per side).
  side         - one face of a sheet, i.e. one page of the output PDF.
                 A signature of N sheets has 2N sides and 4N book pages.
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

ROOT_DIR = Path(__file__).resolve().parent.parent
FONT_PATH = ROOT_DIR / "Script" / "Baskervville-Regular.ttf"
FONT_NAME = "Baskervville"  # https://fonts.google.com/specimen/Baskervville
FONT_SIZE = 10
PAGE_NUMBER_BASELINE_PT = 24
PAGE_NUMBER_INSET_IN = 0.125
PT_PER_INCH = 72.0
CM_PER_INCH = 2.54

_font_registered = False


@dataclass(frozen=True)
class Unit:
    """A length unit for what the user reads and types.

    Every measurement is stored and computed in inches; a Unit only converts at
    the edges, so switching units can never change the geometry of a book.
    """

    name: str
    per_inch: float
    step: float

    def from_inches(self, inches):
        return inches * self.per_inch

    def to_inches(self, value):
        return value / self.per_inch

    def label(self, inches, places=3):
        return f"{self.from_inches(inches):.{places}f} {self.name}"


INCHES = Unit("in", 1.0, 0.05)
CENTIMETRES = Unit("cm", CM_PER_INCH, 0.1)


def _ensure_font():
    global _font_registered
    if not _font_registered:
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH)))
        _font_registered = True


@dataclass(frozen=True)
class ColumnLayout:
    """Where the two columns sit on a source page, in inches."""

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


@dataclass(frozen=True)
class BookInfo:
    source_pages: int
    page_width_pt: float
    page_height_pt: float
    uniform_page_size: bool

    @property
    def book_pages(self):
        return self.source_pages * 2


def inspect_book(pdf_path):
    reader = pypdf.PdfReader(str(pdf_path))
    first = reader.pages[0]
    width = float(first.mediabox.width)
    height = float(first.mediabox.height)
    uniform = all(
        abs(float(p.mediabox.width) - width) < 0.5
        and abs(float(p.mediabox.height) - height) < 0.5
        for p in reader.pages
    )
    return BookInfo(len(reader.pages), width, height, uniform)


def layout_problems(layout, page_width_pt, unit=INCHES):
    """Reasons this layout would produce a physically wrong result.

    Each sheet is folded down the middle, so the split always falls at exactly
    half the source page width. The gutter between the two columns therefore has
    to straddle that midpoint, otherwise the fold cuts through printed text.
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


def layout_warnings(layout, page_width_pt, unit=INCHES):
    """Things that still print, but probably are not what the user meant."""
    warnings = []
    page_width_in = page_width_pt / PT_PER_INCH
    right_margin_in = page_width_in - layout.right_column_end_in
    if abs(right_margin_in - layout.page_margin_in) > 0.01:
        warnings.append(
            f"The layout is not symmetric: the left margin is "
            f"{unit.label(layout.page_margin_in)} but that leaves "
            f"{unit.label(right_margin_in)} on the right. Facing pages will have "
            f"uneven margins."
        )
    return warnings


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


def _page_number_overlay(width_pt, height_pt, layout, left_number, right_number):
    _ensure_font()
    buffer = io.BytesIO()
    canvas = RL_Canvas(buffer, pagesize=(width_pt, height_pt))
    canvas.setFont(FONT_NAME, FONT_SIZE)
    left_x = (layout.left_column_end_in - PAGE_NUMBER_INSET_IN) * PT_PER_INCH
    right_x = (layout.right_column_end_in - PAGE_NUMBER_INSET_IN) * PT_PER_INCH
    canvas.drawRightString(left_x, PAGE_NUMBER_BASELINE_PT, str(left_number))
    canvas.drawRightString(right_x, PAGE_NUMBER_BASELINE_PT, str(right_number))
    canvas.save()
    buffer.seek(0)
    return pypdf.PdfReader(buffer).pages[0]


def stamp_book(book_file, numbered_pdf_output, layout, progress=None):
    """Stamp book page numbers at the bottom of each column.

    Source page i carries book pages 2i-1 and 2i, so the numbers run 1, 2 on the
    first source page, 3, 4 on the second, and so on.
    """
    writer = pypdf.PdfWriter(clone_from=str(book_file))
    total = len(writer.pages)
    for i, page in enumerate(writer.pages):
        overlay = _page_number_overlay(
            float(page.mediabox.width),
            float(page.mediabox.height),
            layout,
            2 * i + 1,
            2 * i + 2,
        )
        page.merge_page(overlay, over=True)
        if progress:
            progress((i + 1) / total, f"Numbering source page {i + 1} of {total}")

    Path(numbered_pdf_output).parent.mkdir(parents=True, exist_ok=True)
    with open(numbered_pdf_output, "wb") as stream:
        writer.write(stream)
    return Path(numbered_pdf_output)


def _merge_column(target, source_page, from_right_column, to_right_column):
    """Copy one column of `source_page` onto one half of `target`.

    pypdf clips a merged page to its cropbox and transforms that clip along with
    the content, so narrowing the cropbox to a single column is what isolates it.
    The source page is read, never mutated destructively: the cropbox is reset on
    every call, and the content stream is left alone. Transforming a page in
    place would corrupt it for the other column, because two pypdf page objects
    cloned from one reader share a single content stream.
    """
    box = source_page.mediabox
    origin_x, origin_y = float(box.left), float(box.bottom)
    half_width = float(box.width) / 2
    height = float(box.height)

    source_x = origin_x + (half_width if from_right_column else 0.0)
    target_x = half_width if to_right_column else 0.0

    source_page.cropbox = RectangleObject(
        (source_x, origin_y, source_x + half_width, origin_y + height)
    )
    target.merge_transformed_page(
        source_page,
        pypdf.Transformation().translate(tx=target_x - source_x, ty=-origin_y),
    )


def _place_book_page(target, reader, book_page, book_pages, to_right_column):
    if book_page > book_pages:
        return  # Padding at the back of the book; leave the half blank.
    source_page = reader.pages[(book_page - 1) // 2]
    _merge_column(
        target,
        source_page,
        from_right_column=(book_page - 1) % 2 == 1,
        to_right_column=to_right_column,
    )


def split_to_signatures(
    book_file,
    sheets_per_signature,
    signature_save_folder,
    flip_on_long_edge=True,
    progress=None,
):
    """Impose a 2-column book into one printable PDF per signature.

    Each output PDF is printed duplex, and every sheet is then folded in half and
    nested inside the previous one to form the signature.

    `flip_on_long_edge` must match the printer's duplex setting. The source pages
    are landscape, so their long edge is the top, and a long-edge duplex flip
    turns the back of the sheet upside down relative to the front. Rotating the
    back sides by 180 degrees cancels that out. Set it False for short-edge
    duplex, which needs no rotation.
    """
    reader = pypdf.PdfReader(str(book_file))
    first = reader.pages[0]
    page_width = float(first.mediabox.width)
    page_height = float(first.mediabox.height)
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
                width=page_width, height=page_height
            )
            _place_book_page(sheet, reader, left, book_pages, to_right_column=False)
            _place_book_page(sheet, reader, right, book_pages, to_right_column=True)
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
