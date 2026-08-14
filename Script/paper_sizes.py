"""Units and paper sizes, so the app is not tied to A4.

Two catalogues, because they answer two different questions:

  SHEET_SIZES     - paper you can actually feed into a printer. One sheet is
                    printed on both sides and folded once, so it yields four
                    book pages, each **half the sheet's width** and its full
                    height.
  BOOK_PAGE_SIZES - sizes a finished book is normally trimmed to. Read the
                    relationship backwards: a book page of w x h needs a sheet
                    of 2w x h.

That doubling is the single fact that trips people up, so both catalogues can
report the other side of it (`book_page_in`, `sheet_needed_in`).

Every length is stored in inches. Units only convert at the edges - what the
user reads and types - so changing units can never change a book's geometry.
"""

import re
from dataclasses import dataclass

PT_PER_INCH = 72.0
CM_PER_INCH = 2.54
MM_PER_INCH = 25.4


@dataclass(frozen=True)
class Unit:
    """A length unit for what the user reads and types.

    `step` is the increment for a spin box, `places` the precision for a typed
    field, and `size_places` the (coarser) precision for quoting a paper size,
    where trailing noise like "210.000 mm" only obscures a round number.
    """

    name: str
    per_inch: float
    step: float
    places: int = 3
    size_places: int = 2

    def from_inches(self, inches):
        return inches * self.per_inch

    def to_inches(self, value):
        return value / self.per_inch

    def label(self, inches, places=None):
        places = self.places if places is None else places
        return f"{self.from_inches(inches):.{places}f} {self.name}"

    def size_label(self, width_in, height_in, places=None):
        """'11.69 × 8.27 in' - a page size, without repeating the unit twice."""
        places = self.size_places if places is None else places
        return (
            f"{self.from_inches(width_in):.{places}f} × "
            f"{self.from_inches(height_in):.{places}f} {self.name}"
        )


INCHES = Unit("in", 1.0, 0.05, places=3, size_places=2)
CENTIMETRES = Unit("cm", CM_PER_INCH, 0.1, places=2, size_places=1)
MILLIMETRES = Unit("mm", MM_PER_INCH, 1.0, places=1, size_places=0)

UNITS = (INCHES, CENTIMETRES, MILLIMETRES)


@dataclass(frozen=True)
class PaperSize:
    """A named size, always stored portrait: `width_in` is the short edge.

    Orientation is applied on the way out (`size_in`), so one entry serves both
    "A4 portrait" and "A4 landscape" and the catalogue cannot disagree with
    itself about which is which.
    """

    name: str
    width_in: float
    height_in: float
    family: str
    note: str = ""

    @classmethod
    def from_mm(cls, name, width_mm, height_mm, family, note=""):
        return cls(
            name, width_mm / MM_PER_INCH, height_mm / MM_PER_INCH, family, note
        )

    @property
    def area_in2(self):
        return self.width_in * self.height_in

    def size_in(self, landscape=True):
        """(width, height) with the long edge horizontal unless told otherwise."""
        if landscape:
            return (self.height_in, self.width_in)
        return (self.width_in, self.height_in)

    def size_pt(self, landscape=True):
        width_in, height_in = self.size_in(landscape)
        return (width_in * PT_PER_INCH, height_in * PT_PER_INCH)

    def book_page_in(self, landscape=True):
        """One finished book page once this sheet is folded down the middle."""
        width_in, height_in = self.size_in(landscape)
        return (width_in / 2, height_in)

    def sheet_needed_in(self):
        """The sheet a book page of this size needs: twice as wide, same height."""
        return (2 * self.width_in, self.height_in)


# --------------------------------------------------------------------------
# Sheets you can print on
# --------------------------------------------------------------------------
# Folding halves the width, which is why the ISO series is so tidy here: A3
# landscape folds to A4 portrait, A4 to A5, and so on down. The notes call out
# only what the numbers cannot say by themselves.

SHEET_SIZES = (
    PaperSize.from_mm("A2", 420, 594, "ISO A", "Needs a wide-format printer."),
    PaperSize.from_mm("A3", 297, 420, "ISO A", "The usual choice for an A5 book."),
    PaperSize.from_mm("A4", 210, 297, "ISO A", "Any home printer; folds to A5."),
    PaperSize.from_mm("A5", 148, 210, "ISO A", "Small booklets and zines."),
    PaperSize.from_mm("A6", 105, 148, "ISO A", "Miniature books; very little text fits."),
    PaperSize.from_mm("B3", 353, 500, "ISO B", "Wide-format only."),
    PaperSize.from_mm("B4", 250, 353, "ISO B", "Folds to B5, a common novel size."),
    PaperSize.from_mm("B5", 176, 250, "ISO B", "Folds to B6."),
    PaperSize.from_mm("B6", 125, 176, "ISO B", "Pocket booklets."),
    PaperSize.from_mm("JIS B4", 257, 364, "JIS B", "Japanese B series; slightly larger than ISO B4."),
    PaperSize.from_mm("JIS B5", 182, 257, "JIS B", "Standard Japanese book stock."),
    PaperSize.from_mm("JIS B6", 128, 182, "JIS B", "Japanese pocket paperback."),
    PaperSize("Letter", 8.5, 11.0, "North American", "Any home printer; folds to Half Letter."),
    PaperSize("Legal", 8.5, 14.0, "North American", "Extra width when folded, same height as Letter."),
    PaperSize("Half Letter", 5.5, 8.5, "North American", "Also called Statement; folds to a small booklet."),
    PaperSize("Executive", 7.25, 10.5, "North American", "Uncommon, but stocked by some offices."),
    PaperSize("Tabloid", 11.0, 17.0, "North American", "Also ANSI B or Ledger; folds to Letter."),
    PaperSize("ANSI C", 17.0, 22.0, "North American", "Wide-format only; folds to Tabloid."),
    PaperSize.from_mm("SRA4", 225, 320, "Oversize", "Press stock: A4 with room to trim."),
    PaperSize.from_mm("SRA3", 320, 450, "Oversize", "Press stock: A3 with room to trim."),
    PaperSize("Super B", 13.0, 19.0, "Oversize", "Also A3+; the largest many photo inkjets take."),
)

SHEET_FAMILIES = ("ISO A", "ISO B", "JIS B", "North American", "Oversize")


# --------------------------------------------------------------------------
# Sizes finished books are trimmed to
# --------------------------------------------------------------------------
# These are *not* things to print on. Each one is here so the app can answer
# "what paper do I need for a 6 x 9 book?" - the answer is always a sheet twice
# as wide, used landscape.

BOOK_PAGE_SIZES = (
    PaperSize("Mass-market paperback", 4.25, 6.87, "Pocket", "The supermarket paperback."),
    PaperSize.from_mm("A6 pocket", 105, 148, "Pocket", "Folds out of an A5 sheet."),
    PaperSize.from_mm("A-format", 110, 178, "Pocket", "UK mass-market paperback."),
    PaperSize("Novella", 5.0, 8.0, "Paperback", "Short fiction and chapbooks."),
    PaperSize("Digest", 5.5, 8.5, "Paperback", "Half a Letter sheet; no trimming needed."),
    PaperSize.from_mm("B-format", 129, 198, "Paperback", "UK trade paperback."),
    PaperSize.from_mm("A5", 148, 210, "Paperback", "Half an A4 sheet; no trimming needed."),
    PaperSize("US trade", 6.0, 9.0, "Trade", "The default for print-on-demand novels."),
    PaperSize.from_mm("Demy octavo", 138, 216, "Trade", "Traditional UK hardback."),
    PaperSize.from_mm("Royal octavo", 156, 234, "Trade", "Larger UK hardback and academic books."),
    PaperSize("Comic book", 6.625, 10.25, "Trade", "US floppy comic trim."),
    PaperSize.from_mm("Crown quarto", 189, 246, "Large", "Textbooks and illustrated books."),
    PaperSize("US Letter", 8.5, 11.0, "Large", "Manuals, scores, workbooks."),
    PaperSize.from_mm("A4", 210, 297, "Large", "Manuals and scores, metric side."),
)

BOOK_PAGE_FAMILIES = ("Pocket", "Paperback", "Trade", "Large")


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------
# A tolerance of 1/20 inch (~1.3 mm) is wide enough to absorb rounding in a PDF
# that was built in millimetres, and far tighter than the gap between any two
# entries in either catalogue, so a match is never ambiguous.
NAME_TOLERANCE_IN = 0.05

# "11x17in", "297 x 420 mm", "30*21cm", '8.5x11"'. The unit is optional and
# defaults to inches.
DIMENSIONS_PATTERN = re.compile(
    r"""\s*(?P<width>\d+(?:\.\d+)?)\s*[x*×]\s*(?P<height>\d+(?:\.\d+)?)\s*
        (?P<unit>in|inch|inches|"|cm|mm)?\s*""",
    re.IGNORECASE | re.VERBOSE,
)


def find_paper_size(name, catalogue=SHEET_SIZES):
    """Look a size up by name, case- and space-insensitively. None if unknown."""
    wanted = " ".join(str(name).split()).casefold()
    for size in catalogue:
        if size.name.casefold() == wanted:
            return size
    return None


def sizes_by_family(catalogue=SHEET_SIZES, families=SHEET_FAMILIES):
    """The catalogue grouped for a menu, in the order the families are listed."""
    return [
        (family, [size for size in catalogue if size.family == family])
        for family in families
    ]


def identify_size(width_in, height_in, catalogue=SHEET_SIZES,
                  tolerance_in=NAME_TOLERANCE_IN):
    """Name a measured page: (PaperSize, is_landscape), or None if unrecognised.

    Both orientations of every entry are tried and the closest match wins, so a
    near-square size cannot be reported under the wrong orientation.
    """
    best = None
    best_error = tolerance_in
    for size in catalogue:
        for landscape in (False, True):
            expected_w, expected_h = size.size_in(landscape)
            error = max(abs(width_in - expected_w), abs(height_in - expected_h))
            if error <= best_error:
                best, best_error = (size, landscape), error
    return best


def describe_size(width_in, height_in, unit=INCHES, catalogue=SHEET_SIZES):
    """'11.69 × 8.27 in (A4, landscape)', or just the dimensions if unrecognised."""
    text = unit.size_label(width_in, height_in)
    match = identify_size(width_in, height_in, catalogue)
    if match is None:
        return text
    size, landscape = match
    return f"{text} ({size.name}, {'landscape' if landscape else 'portrait'})"


def smallest_sheet_for(width_in, height_in, catalogue=SHEET_SIZES, slack_in=1e-6):
    """The smallest stock sheet that a sheet of width x height fits on.

    Either orientation counts, since feeding the paper the other way round is
    free. Used to answer "I want 6 x 9 pages, what do I buy?" - where the honest
    answer is often "nothing standard, print larger and trim", i.e. None.
    """
    def fits(size):
        return any(
            stock_w + slack_in >= width_in and stock_h + slack_in >= height_in
            for stock_w, stock_h in (size.size_in(True), size.size_in(False))
        )

    candidates = [size for size in catalogue if fits(size)]
    return min(candidates, key=lambda size: size.area_in2) if candidates else None


def parse_paper_size(text, landscape=True):
    """Turn a command-line paper argument into (width_in, height_in).

    Accepts a sheet name ("A4", "half letter") which is oriented by the
    `landscape` flag, or explicit dimensions ("11x17in", "297x420mm", "30x21cm")
    which are taken exactly as written, because the user already said which way
    round they want them.

    Only SHEET_SIZES names are accepted. Book page names are deliberately not,
    because "A5" as a sheet and "A5" as a finished page mean sheets of different
    sizes, and silently guessing which one was meant would halve or double the
    book.
    """
    text = " ".join(str(text).split())
    if not text:
        raise ValueError("No paper size given.")

    size = find_paper_size(text, SHEET_SIZES)
    if size is not None:
        return size.size_in(landscape)

    # Matched as a whole, not split on the first "x" found: plenty of words
    # contain an x, and quietly reading "nonexistent" as "none by istent" is a
    # worse failure than saying the name is not known.
    match = DIMENSIONS_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError(
            f"Could not read the paper size “{text}”. Use one of the known sheet "
            f"names ({', '.join(size.name for size in SHEET_SIZES)}), or dimensions "
            f"such as “11x17in”, “297x420mm” or “30x21cm”."
        )

    suffix = (match.group("unit") or "in").lower()
    unit = {"in": INCHES, "inch": INCHES, "inches": INCHES, '"': INCHES,
            "cm": CENTIMETRES, "mm": MILLIMETRES}[suffix]
    width, height = float(match.group("width")), float(match.group("height"))
    if width <= 0 or height <= 0:
        raise ValueError(f"Paper size “{text}” must be larger than zero.")
    return (unit.to_inches(width), unit.to_inches(height))


def paper_size_table(catalogue=SHEET_SIZES, unit=MILLIMETRES, families=SHEET_FAMILIES):
    """Rows of (family, name, size, folded book page, note) for a reference table."""
    rows = []
    for family, sizes in sizes_by_family(catalogue, families):
        for size in sizes:
            page_w, page_h = size.book_page_in(landscape=True)
            folded = unit.size_label(page_w, page_h)
            match = identify_size(page_w, page_h, SHEET_SIZES)
            if match is not None:
                folded += f" ({match[0].name})"
            rows.append((family, size.name, unit.size_label(*size.size_in(False)),
                         folded, size.note))
    return rows


def book_page_table(unit=MILLIMETRES):
    """Rows of (family, name, trim size, sheet needed, nearest stock, note)."""
    rows = []
    for family, sizes in sizes_by_family(BOOK_PAGE_SIZES, BOOK_PAGE_FAMILIES):
        for size in sizes:
            sheet_w, sheet_h = size.sheet_needed_in()
            stock = smallest_sheet_for(sheet_w, sheet_h)
            rows.append((
                family,
                size.name,
                unit.size_label(size.width_in, size.height_in),
                unit.size_label(sheet_w, sheet_h),
                stock.name if stock is not None else "none",
                size.note,
            ))
    return rows
