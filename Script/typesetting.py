"""Set a typed manuscript as a 2-column PDF book.

The output of this module is an ordinary **input PDF** for the rest of the app:
one PDF page carries two book pages side by side, left column first, and that is
exactly what `print_formatting.split_to_signatures` expects to be handed. So a
book typed into the GUI goes through the same imposition, the same paper sizes
and the same signature planning as a PDF that came from somewhere else — there
is no second pipeline to keep in step with the first.

Two consequences of that format are worth stating, because everything here
follows from them:

  * A source page is **twice as wide** as a book page. `Design` stores the book
    page; the doubling happens here and nowhere else.
  * Book page 1 is the **left** column of source page 1, book page 2 the right
    column, and so on. Odd book pages are therefore rectos — right-hand pages in
    the bound book — which is why "start each chapter on a right-hand page"
    means "start it in a left-hand column".
"""

import io
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import reportlab
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    FrameBreak,
    PageTemplate,
    Paragraph,
    Spacer,
)
from reportlab.platypus.doctemplate import ActionFlowable
from reportlab.platypus.tableofcontents import TableOfContents

from Script.manuscript import (
    CHAPTER_START_RECTO,
    Manuscript,
    chapter_label,
    clamp_design,
    kind_of,
    numbered_sections,
    page_sized,
)
from Script.paper_sizes import PT_PER_INCH
# Page numbers are placed by the imposition module, which is also what stamps
# them onto a PDF the app did not set itself. One routine, so a typed book and a
# converted one carry their numbers in the same place.
from Script.print_formatting import draw_folio, folio_fits

ROOT_DIR = Path(__file__).resolve().parent.parent
BASKERVVILLE_PATH = ROOT_DIR / "Script" / "Baskervville-Regular.ttf"
_REPORTLAB_FONTS = Path(reportlab.__file__).resolve().parent / "fonts"

# Written into the PDF's /Creator. The GUI reads it back to tell a book it
# typeset itself from one the user put in the folder by hand, which is what
# lets "build again" overwrite the first without ever overwriting the second.
#
# `PAST_CREATORS` are markers earlier builds stamped. They are still accepted,
# or every book already sitting in Input/ would stop being recognised as this
# editor's the moment the wording changed, and rebuilding it would start
# refusing to replace the file it wrote itself.
GENERATED_BY = "Bookbinding Signature Creator (book editor)"
PAST_CREATORS = ("Bookbinding Signature Creator — book editor",)

LEFT_FRAME = "recto"   # odd book pages: the fold is on their right
RIGHT_FRAME = "verso"  # even book pages: the fold is on their left

# Stands in for the table of contents in the running order of sections, so that
# where it goes is decided in one place alongside everything else.
CONTENTS = "contents-page"


# --------------------------------------------------------------------------
# Typefaces
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BookFont:
    key: str
    # What the typeface is called, on its own. Kept apart from `label` because a
    # note about a character the face cannot set has to name the face and
    # nothing else; picking it back out of the menu entry was a string split
    # waiting to be broken by a reworded menu.
    name: str
    label: str
    regular: str
    bold: str
    italic: str
    bold_italic: str
    note: str = ""
    files: tuple = ()   # (font name, file) pairs to register before first use


_VERA = (
    ("Vera", "Vera.ttf"), ("Vera-Bold", "VeraBd.ttf"),
    ("Vera-Italic", "VeraIt.ttf"), ("Vera-BoldItalic", "VeraBI.ttf"),
)

FONTS = {
    font.key: font
    for font in (
        BookFont(
            "times", "Times", "Times (a book serif)",
            "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
            note="The safe default: a serif face with real bold and italic, and "
                 "the widest character coverage of the built-in fonts.",
        ),
        BookFont(
            "baskervville", "Baskervville", "Baskervville (the app's own serif)",
            "Baskervville", "Baskervville", "Baskervville", "Baskervville",
            note="Shipped with this app and lovely for text, but it comes in one "
                 "weight only: **bold** and *italic* are set in the regular face.",
            files=(("Baskervville", str(BASKERVVILLE_PATH)),),
        ),
        BookFont(
            "helvetica", "Helvetica", "Helvetica (a plain sans)",
            "Helvetica", "Helvetica-Bold", "Helvetica-Oblique",
            "Helvetica-BoldOblique",
            note="Sans serif. Easy to read at small sizes; less bookish.",
        ),
        BookFont(
            "vera", "Bitstream Vera", "Bitstream Vera (a sans with accents)",
            "Vera", "Vera-Bold", "Vera-Italic", "Vera-BoldItalic",
            note="A sans serif with a full set of accented Latin letters.",
            files=tuple((name, str(_REPORTLAB_FONTS / file)) for name, file in _VERA),
        ),
        BookFont(
            "courier", "Courier", "Courier (a typewriter face)",
            "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
            note="Fixed width. Manuscript-ish, and it fits far fewer words on a "
                 "page than the others.",
        ),
    )
}

DEFAULT_FONT_KEY = "times"

_registered = set()


def font_for(key):
    return FONTS.get(key) or FONTS[DEFAULT_FONT_KEY]


def ensure_font(font):
    """Register a TrueType family the first time it is used.

    The built-in Type 1 families need nothing. A one-weight family is registered
    with the same file standing in for bold and italic, so `<b>` and `<i>` in the
    text still resolve to *something* instead of raising mid-build.
    """
    font = font if isinstance(font, BookFont) else font_for(font)
    if font.key in _registered:
        return font
    for name, path in font.files:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, path))
    if font.files:
        pdfmetrics.registerFontFamily(
            font.regular, normal=font.regular, bold=font.bold,
            italic=font.italic, boldItalic=font.bold_italic,
        )
    _registered.add(font.key)
    return font


# --------------------------------------------------------------------------
# Characters a font cannot set
# --------------------------------------------------------------------------
# Nothing bundled with this app covers Greek, Cyrillic or CJK, so a paste from
# elsewhere can contain characters that simply have no glyph. They are replaced
# where an honest replacement exists and reported where none does — never
# dropped silently, which is the one outcome that would let a book go to the
# printer with words missing from it.

# Typographic characters worth rewriting rather than reporting: every one of
# these means the same thing in ASCII.
_FALLBACKS = {
    "‘": "'", "’": "'", "‚": ",", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "--",
    "―": "--", "−": "-",
    "…": "...", "•": "*", "·": "*", "‧": "*",
    "‹": "<", "›": ">", "«": "<<", "»": ">>",
    "′": "'", "″": '"', " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ", " ": " ", " ": " ",
    "⁄": "/", "×": "x", "→": "->", "←": "<-",
}

# Characters that carry no ink and only confuse a line breaker.
_INVISIBLE = "​‌‍⁠﻿­"


def _codepoints_of(font_name):
    """The set of characters a registered font can set, or None for "everything"."""
    try:
        face = pdfmetrics.getFont(font_name).face
    except Exception:
        return None
    mapping = getattr(face, "charToGlyph", None)
    if mapping:
        return set(mapping)
    return None  # a Type 1 face: handled by the encoding test below


def _can_set(char, font_name, codepoints):
    if codepoints is not None:
        return ord(char) in codepoints
    # The built-in faces are used with WinAnsiEncoding, which is cp1252.
    try:
        char.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def prepare_text(text, font=None):
    """Clean one piece of typed text, and say what the font cannot set.

    Returns `(text, missing)`, where `missing` is the set of characters that
    survived and still have no glyph. Line endings are normalised, tabs become
    spaces and zero-width characters go, whatever the font.
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.expandtabs(4)
    for char in _INVISIBLE:
        text = text.replace(char, "")
    if font is None:
        return text, set()

    font = ensure_font(font)
    codepoints = _codepoints_of(font.regular)
    out = []
    missing = set()
    for char in text:
        if char in "\n" or _can_set(char, font.regular, codepoints):
            out.append(char)
            continue
        replacement = _FALLBACKS.get(char)
        if replacement is None:
            # "ﬁ" -> "fi", "№" -> "No", "é" -> "e", but only where the font has
            # no glyph for the character as it was typed.
            folded = unicodedata.normalize("NFKD", char)
            folded = "".join(c for c in folded if not unicodedata.combining(c))
            if folded and folded != char and all(
                _can_set(c, font.regular, codepoints) for c in folded
            ):
                replacement = folded
        if replacement is None:
            missing.add(char)
            out.append(char)
        else:
            out.extend(replacement)
    return "".join(out), missing


# --------------------------------------------------------------------------
# Typed text -> blocks
# --------------------------------------------------------------------------
# The markup is deliberately tiny, and every part of it is something people
# already type into a plain text box without being asked to.

PARAGRAPH = "paragraph"
SUBHEADING = "subheading"
SUBSUBHEADING = "subsubheading"
QUOTE = "quote"
SCENE_BREAK = "scene_break"

_HEADING = re.compile(r"^(#{1,2})\s+(\S.*)$")
_SCENE_BREAK = re.compile(r"^\s*(?:([*\-_~])\s*){3,}$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")

_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S)
_ITALIC_STAR = re.compile(r"(?<![*\w])\*(?=\S)([^*]+?)(?<=\S)\*(?![*\w])", re.S)
_ITALIC_UNDER = re.compile(r"(?<![_\w])_(?=\S)([^_]+?)(?<=\S)_(?![_\w])", re.S)


@dataclass(frozen=True)
class Block:
    style: str
    text: str = ""


def escape(text):
    """XML-escape for reportlab's paragraph parser, which reads `text` as markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline_markup(text):
    """`**bold**`, `*italic*` and `_italic_`, on already-escaped text.

    Bold is converted first so the two stars of `**` are gone before italics
    look for a single one.
    """
    text = escape(text)
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _ITALIC_STAR.sub(r"<i>\1</i>", text)
    text = _ITALIC_UNDER.sub(r"<i>\1</i>", text)
    return text


def parse_blocks(text):
    """Typed text as a list of `Block`s, in order.

    The rules, all of which are in the GUI's formatting note:

      * a blank line starts a new paragraph;
      * `# Heading` and `## Heading` are headings inside a section;
      * a line of three or more `*`, `-`, `_` or `~` is a scene break;
      * lines starting with `>` are a quotation and keep their line breaks;
      * everything else is prose, and single line breaks inside a paragraph are
        soft wrapping — they are joined with a space, because that is what a
        line break inside a paragraph means in every text box ever made.
    """
    blocks = []
    buffer = []
    buffer_style = PARAGRAPH

    def flush():
        nonlocal buffer, buffer_style
        if buffer:
            joiner = "<br/>" if buffer_style == QUOTE else " "
            blocks.append(Block(buffer_style, joiner.join(buffer)))
        buffer = []
        buffer_style = PARAGRAPH

    for line in (text or "").split("\n"):
        if not line.strip():
            flush()
            continue
        if _SCENE_BREAK.match(line):
            flush()
            blocks.append(Block(SCENE_BREAK))
            continue
        heading = _HEADING.match(line.strip())
        if heading:
            flush()
            style = SUBHEADING if len(heading.group(1)) == 1 else SUBSUBHEADING
            blocks.append(Block(style, inline_markup(heading.group(2).strip())))
            continue
        quoted = _QUOTE.match(line)
        style = QUOTE if quoted else PARAGRAPH
        if style != buffer_style:
            flush()
            buffer_style = style
        content = quoted.group(1) if quoted else line.strip()
        buffer.append(inline_markup(content.strip()))
    flush()
    return blocks


# --------------------------------------------------------------------------
# Styles
# --------------------------------------------------------------------------


def build_styles(design, font):
    """Every paragraph style the book uses, derived from one Design."""
    size = design.font_size_pt
    leading = size * design.line_spacing
    align = TA_JUSTIFY if design.justify else TA_LEFT
    indent = design.first_line_indent_in * PT_PER_INCH

    def style(name, **kwargs):
        base = dict(
            fontName=font.regular, fontSize=size, leading=leading,
            alignment=align, allowWidows=0, allowOrphans=0,
        )
        base.update(kwargs)
        return ParagraphStyle(name, **base)

    return {
        "body": style("body", firstLineIndent=indent,
                      spaceAfter=design.paragraph_space_pt),
        # The first paragraph after any heading or break is not indented: the
        # indent exists to show that a paragraph continues the one above it,
        # and there is nothing above this one.
        "body_first": style("body_first", firstLineIndent=0,
                            spaceAfter=design.paragraph_space_pt),
        "quote": style("quote", firstLineIndent=0, alignment=TA_LEFT,
                       fontName=font.italic,
                       leftIndent=indent + 6, rightIndent=6,
                       spaceBefore=leading * 0.5, spaceAfter=leading * 0.5),
        "scene_break": style("scene_break", alignment=TA_CENTER,
                             firstLineIndent=0,
                             spaceBefore=leading * 0.6, spaceAfter=leading * 0.6),
        "subheading": style("subheading", fontName=font.bold, fontSize=size * 1.1,
                            leading=leading * 1.1, alignment=TA_LEFT,
                            firstLineIndent=0, keepWithNext=1,
                            spaceBefore=leading * 1.1, spaceAfter=leading * 0.35),
        "subsubheading": style("subsubheading", fontName=font.italic,
                               alignment=TA_LEFT, firstLineIndent=0,
                               keepWithNext=1, spaceBefore=leading * 0.9,
                               spaceAfter=leading * 0.25),
        "chapter_label": style("chapter_label", alignment=TA_CENTER,
                               fontName=font.italic, fontSize=size,
                               leading=leading, firstLineIndent=0,
                               spaceAfter=leading * 0.5, keepWithNext=1),
        "chapter_title": style("chapter_title", alignment=TA_CENTER,
                               fontName=font.bold, fontSize=size * 1.6,
                               leading=size * 1.9, firstLineIndent=0,
                               spaceAfter=leading * 1.6, keepWithNext=1),
        "part_title": style("part_title", alignment=TA_CENTER, fontName=font.bold,
                            fontSize=size * 2.0, leading=size * 2.4,
                            firstLineIndent=0, spaceAfter=leading),
        "display": style("display", alignment=TA_CENTER, fontName=font.italic,
                         firstLineIndent=0, spaceAfter=leading * 0.6),
        "book_title": style("book_title", alignment=TA_CENTER, fontName=font.bold,
                            fontSize=size * 2.4, leading=size * 2.9,
                            firstLineIndent=0, spaceAfter=leading * 0.9),
        "book_subtitle": style("book_subtitle", alignment=TA_CENTER,
                               fontName=font.italic, fontSize=size * 1.3,
                               leading=size * 1.7, firstLineIndent=0,
                               spaceAfter=leading * 1.4),
        "book_author": style("book_author", alignment=TA_CENTER,
                             fontSize=size * 1.25, leading=size * 1.6,
                             firstLineIndent=0, spaceAfter=leading * 0.6),
        "imprint": style("imprint", alignment=TA_CENTER, fontSize=size * 0.85,
                         leading=size * 1.2, firstLineIndent=0,
                         spaceAfter=leading * 0.35),
        "contents_title": style("contents_title", alignment=TA_CENTER,
                                fontName=font.bold, fontSize=size * 1.6,
                                leading=size * 1.9, firstLineIndent=0,
                                spaceAfter=leading * 1.5),
    }


# --------------------------------------------------------------------------
# Flowables that talk to the page
# --------------------------------------------------------------------------


class _Mark(Flowable):
    """A zero-height note to the page furniture, placed at a section's start.

    It records what should *not* be printed on the book page it lands on — a
    folio on the title page, a running head on a chapter opening — and which
    running head applies from here on. It has to be a flowable rather than a
    calculation because only platypus knows which book page a section ends up
    on, and only after it has put it there.
    """

    width = 0
    height = 0

    def __init__(self, folio=True, running_head=True, head_text=None):
        Flowable.__init__(self)
        self.folio = folio
        self.running_head = running_head
        self.head_text = head_text

    def wrap(self, available_width, available_height):
        return (0, 0)

    def draw(self):
        pass


class _RectoBreak(ActionFlowable):
    """End the current column only if it is a verso, so the next start is a recto.

    Emitted after an ordinary frame break, when the section that follows has
    asked to begin on a right-hand page of the bound book. If the fresh column
    is already a recto this does nothing at all; if it is a verso, ending it
    leaves that book page blank, which is exactly what a printed book does.
    """

    def __init__(self):
        ActionFlowable.__init__(self, ("frameEnd",))

    def apply(self, doc):
        if getattr(doc.frame, "id", None) == RIGHT_FRAME:
            ActionFlowable.apply(self, doc)


# --------------------------------------------------------------------------
# The document
# --------------------------------------------------------------------------


class _BookTemplate(BaseDocTemplate):
    """Two frames to a page: a recto on the left, a verso on the right.

    Everything that has to know *where a thing ended up* lives here, because
    platypus is the only thing that knows: the book page a chapter opened on
    (for the table of contents), which columns got content at all (so a blank
    one is left blank rather than being given a page number), and which running
    head is in force further down the book.
    """

    def __init__(self, target, design, font, styles, running_title, progress=None):
        page_width_pt = design.page_width_in * PT_PER_INCH
        page_height_pt = design.page_height_in * PT_PER_INCH
        BaseDocTemplate.__init__(
            self, target,
            pagesize=(2 * page_width_pt, page_height_pt),
            leftMargin=0, rightMargin=0, topMargin=0, bottomMargin=0,
            allowSplitting=1,
            # Otherwise every page opens by selecting reportlab's default
            # Helvetica and then never draws a glyph with it, which puts a font
            # in the finished file that has nothing to do with the book.
            initialFontName=font.regular,
            initialFontSize=design.font_size_pt,
        )
        self.design = design
        self.font = font
        self.styles = styles
        self.running_title = running_title
        self.progress = progress
        self.book_page_width_pt = page_width_pt
        self.book_page_height_pt = page_height_pt

        inner = design.margin_inner_in * PT_PER_INCH
        outer = design.margin_outer_in * PT_PER_INCH
        top = design.margin_top_in * PT_PER_INCH
        bottom = design.margin_bottom_in * PT_PER_INCH
        self.text_width_pt = max(page_width_pt - inner - outer, 1.0)
        self.text_height_pt = max(page_height_pt - top - bottom, 1.0)
        self.top_margin_pt = top
        self.bottom_margin_pt = bottom

        frames = [
            Frame(outer, bottom, self.text_width_pt, self.text_height_pt,
                  id=LEFT_FRAME, leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0),
            Frame(page_width_pt + inner, bottom, self.text_width_pt,
                  self.text_height_pt, id=RIGHT_FRAME, leftPadding=0,
                  rightPadding=0, topPadding=0, bottomPadding=0),
        ]
        self.addPageTemplates([
            PageTemplate(id="spread", frames=frames, onPageEnd=self._draw_furniture)
        ])
        self._reset_pass()

    # ---- per-pass bookkeeping -------------------------------------------
    def _reset_pass(self):
        self.used_pages = set()      # book pages that received any content
        self.no_folio = set()        # book pages that must not carry a number
        self.no_running_head = set()
        self.head_changes = []       # (book page, running head text)
        self.entries = []            # (level, text, book page) for the contents

    def build(self, flowables, *args, **kwargs):
        # multiBuild runs the whole story more than once; each pass has to start
        # from a clean set of records or the second pass would inherit the
        # first's page numbers.
        self._reset_pass()
        return BaseDocTemplate.build(self, flowables, *args, **kwargs)

    # ---- where things landed --------------------------------------------
    @property
    def _column(self):
        return getattr(self.frame, "id", LEFT_FRAME)

    @property
    def current_book_page(self):
        """The book page currently being filled. Left column is the odd one."""
        return 2 * self.page - (1 if self._column == LEFT_FRAME else 0)

    def afterFlowable(self, flowable):
        if isinstance(flowable, ActionFlowable):
            return
        book_page = self.current_book_page
        if isinstance(flowable, _Mark):
            if not flowable.folio:
                self.no_folio.add(book_page)
            if not flowable.running_head:
                self.no_running_head.add(book_page)
            if flowable.head_text is not None:
                self.head_changes.append((book_page, flowable.head_text))
            return
        self.used_pages.add(book_page)
        entry = getattr(flowable, "_toc_entry", None)
        if entry is not None:
            level, text = entry
            self.entries.append((level, text, book_page))
            self.notify("TOCEntry", (level, text, book_page))

    def _head_for(self, book_page):
        text = self.running_title
        for at, value in self.head_changes:
            if at <= book_page:
                text = value
            else:
                break
        return text

    # ---- page furniture --------------------------------------------------
    def _draw_furniture(self, canvas, document):
        """Folios and running heads, drawn once both columns have been filled.

        At `onPageEnd` — rather than at the start of the page — because whether
        a column has any content on it, and which chapter it belongs to, are
        only known after platypus has finished laying it out.
        """
        design = self.design
        size = max(design.font_size_pt - 1.5, 6.5)
        page_width = self.book_page_width_pt

        room_for_folio = folio_fits(self.bottom_margin_pt, size)
        head_baseline = self.book_page_height_pt - self.top_margin_pt * 0.55
        head_fits = self.top_margin_pt * 0.45 >= size * 0.25 + 2.0

        canvas.saveState()
        canvas.setFont(self.font.regular, size)
        for column, book_page in enumerate(
            (2 * document.page - 1, 2 * document.page)
        ):
            if book_page not in self.used_pages:
                continue  # a blank page carries nothing, not even its number
            centre = column * page_width + page_width / 2
            if design.page_numbers and room_for_folio and book_page not in self.no_folio:
                # The same routine that numbers a converted PDF, so the two
                # halves of the app cannot drift apart about where a number goes.
                draw_folio(
                    canvas, book_page, column * page_width, page_width,
                    self.bottom_margin_pt, self.font.regular, size,
                )
            if (design.running_heads and head_fits
                    and book_page not in self.no_running_head):
                head = (
                    self._head_for(book_page)
                    if column == 0 else self.running_title
                )
                if head:
                    canvas.setFont(self.font.italic, size)
                    canvas.drawCentredString(
                        centre, head_baseline, _shorten(head, 60)
                    )
                    canvas.setFont(self.font.regular, size)
        canvas.restoreState()


def _shorten(text, limit):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------
# Building the story
# --------------------------------------------------------------------------


@dataclass
class TypesetResult:
    path: Path
    source_pages: int
    book_pages: int
    words: int
    sections: int
    notes: list = field(default_factory=list)


def _heading_paragraph(text, style, toc_level=None):
    paragraph = Paragraph(text, style)
    if toc_level is not None:
        # Read back in `_BookTemplate.afterFlowable`, where the book page this
        # heading landed on is finally known.
        paragraph._toc_entry = (toc_level, re.sub(r"<[^>]+>", "", text))
    return paragraph


def _body_flowables(text, styles, design, font, prose="body"):
    """One section's typed text as flowables, and the characters that will not set.

    `prose` names the style ordinary paragraphs get. A dedication or an epigraph
    passes "display" to have its paragraphs centred; everything else takes the
    default, where the first paragraph after a heading or a break is the only
    one not indented.
    """
    cleaned, missing = prepare_text(text, font)
    first_style = styles["body_first"] if prose == "body" else styles[prose]
    later_style = styles[prose]
    flowables = []
    first = True
    for block in parse_blocks(cleaned):
        if block.style == SCENE_BREAK:
            marker, marker_missing = prepare_text(design.scene_break or "* * *", font)
            missing |= marker_missing
            flowables.append(Paragraph(escape(marker.strip()), styles["scene_break"]))
            first = True
        elif block.style == SUBHEADING:
            flowables.append(Paragraph(block.text, styles["subheading"]))
            first = True
        elif block.style == SUBSUBHEADING:
            flowables.append(Paragraph(block.text, styles["subsubheading"]))
            first = True
        elif block.style == QUOTE:
            flowables.append(Paragraph(block.text, styles["quote"]))
            first = True
        else:
            flowables.append(Paragraph(block.text, first_style if first else later_style))
            first = False
    return flowables, missing


def _title_page(manuscript, styles, font):
    """The title page: the one piece of front matter that is not a section."""
    body = []
    for text, key in (
        (manuscript.series, "imprint"),
        (manuscript.title.strip(), "book_title"),
        (manuscript.subtitle, "book_subtitle"),
        (manuscript.author, "book_author"),
    ):
        cleaned, _ = prepare_text(text, font)
        if cleaned.strip():
            body.append(Paragraph(inline_markup(cleaned.strip()), styles[key]))
    imprint = []
    for text in (manuscript.publisher, manuscript.edition):
        cleaned, _ = prepare_text(text, font)
        if cleaned.strip():
            imprint.append(Paragraph(escape(cleaned.strip()), styles["imprint"]))
    return body, imprint


def _copyright_page(manuscript, styles, font):
    lines = []
    for text in manuscript.imprint_lines():
        cleaned, _ = prepare_text(text, font)
        for piece in cleaned.strip().split("\n\n"):
            if piece.strip():
                lines.append(Paragraph(
                    escape(piece.strip()).replace("\n", "<br/>"), styles["imprint"]
                ))
    return lines


def build_story(manuscript, styles, font, doc):
    """Every flowable of the book, in order, plus the notes worth reporting."""
    design = manuscript.design
    story = []
    notes_missing = set()
    text_height = doc.text_height_pt

    def start_page(recto=False):
        """Move to a fresh column, optionally skipping a verso to reach a recto."""
        if story:
            story.append(FrameBreak())
        if recto:
            story.append(_RectoBreak())

    # ---- title page ------------------------------------------------------
    if design.title_page and manuscript.has_title_page():
        body, imprint = _title_page(manuscript, styles, font)
        if body or imprint:
            story.append(_Mark(folio=False, running_head=False))
            story.append(Spacer(1, text_height * 0.20))
            story.extend(body)
            if imprint:
                story.append(Spacer(1, text_height * 0.20))
                story.extend(imprint)

    # ---- copyright page --------------------------------------------------
    if design.copyright_page:
        lines = _copyright_page(manuscript, styles, font)
        if lines:
            start_page()
            story.append(_Mark(folio=False, running_head=False))
            story.append(Spacer(1, text_height * 0.55))
            story.extend(lines)

    # ---- the sections, with the contents page among them -----------------
    # A dedication and an epigraph belong in front of the table of contents and
    # everything else behind it, which is the order every printed book uses. So
    # the contents go after the run of centred display pages that opens the
    # front matter, wherever that run happens to end.
    listed = [
        section for section in manuscript.sections
        if kind_of(section.kind).style in ("chapter", "section", "part")
        and not section.is_empty
    ]
    front = [section for section in manuscript.front if not section.is_empty]
    lead = 0
    for section in front:
        if kind_of(section.kind).style != "display":
            break
        lead += 1

    items = list(front[:lead])
    if design.contents and listed:
        items.append(CONTENTS)
    items += front[lead:]
    items += [s for s in manuscript.body if not s.is_empty]
    items += [s for s in manuscript.back if not s.is_empty]

    numbers = numbered_sections(manuscript)
    for section in items:
        if section is CONTENTS:
            start_page()
            story.append(_Mark(folio=True, running_head=False))
            story.append(Paragraph("Contents", styles["contents_title"]))
            contents = TableOfContents()
            contents.levelStyles = [
                ParagraphStyle(
                    "toc_part", parent=styles["body_first"], fontName=font.bold,
                    leftIndent=0, firstLineIndent=0,
                    spaceBefore=styles["body"].leading * 0.7,
                ),
                ParagraphStyle(
                    "toc_chapter", parent=styles["body_first"], leftIndent=12,
                    firstLineIndent=-12,
                ),
            ]
            contents.dotsMinLevel = 0
            story.append(contents)
            continue

        spec = kind_of(section.kind)
        heading = section.printed_heading.strip()
        cleaned_heading, missing = prepare_text(heading, font)
        notes_missing |= missing
        cleaned_heading = cleaned_heading.strip()

        recto = design.chapter_start == CHAPTER_START_RECTO
        start_page(recto=recto or spec.style == "part")

        if spec.style == "display":
            story.append(_Mark(folio=False, running_head=False))
            story.append(Spacer(1, text_height * 0.25))
            if cleaned_heading:
                story.append(Paragraph(
                    inline_markup(cleaned_heading), styles["chapter_title"]
                ))
            body, missing = _body_flowables(
                section.text, styles, design, font, prose="display"
            )
            notes_missing |= missing
            story.extend(body)
            continue

        if spec.style == "part":
            story.append(_Mark(folio=False, running_head=False,
                               head_text=cleaned_heading or None))
            story.append(Spacer(1, text_height * 0.30))
            title = cleaned_heading or "Part"
            story.append(_heading_paragraph(
                inline_markup(title), styles["part_title"], toc_level=0
            ))
            body, missing = _body_flowables(section.text, styles, design, font)
            notes_missing |= missing
            story.extend(body)
            continue

        # A chapter or any other headed section.
        story.append(_Mark(folio=True, running_head=False,
                           head_text=cleaned_heading or spec.heading or None))
        story.append(Spacer(1, text_height * 0.12))
        label = chapter_label(design, numbers[section.id]) if section.id in numbers else ""
        if label:
            story.append(Paragraph(escape(label), styles["chapter_label"]))
        title = cleaned_heading
        if title:
            story.append(_heading_paragraph(
                inline_markup(title), styles["chapter_title"], toc_level=1
            ))
        elif label:
            # A numbered chapter with no title of its own still belongs in the
            # contents, under the number it was given.
            story[-1] = _heading_paragraph(
                escape(label), styles["chapter_label"], toc_level=1
            )
        body, missing = _body_flowables(section.text, styles, design, font)
        notes_missing |= missing
        story.extend(body)

    return story, notes_missing


# --------------------------------------------------------------------------
# The one function the app calls
# --------------------------------------------------------------------------


class EmptyBookError(ValueError):
    """Raised when there would be nothing at all on the paper."""


def is_generated_book(pdf_path):
    """Did this editor write that PDF?

    Read off the PDF's own /Creator, which `render_manuscript` stamps. Anything
    unreadable answers False, because the only thing this decides is whether a
    file may be silently overwritten, and "no" is the safe answer to that.
    """
    try:
        import pypdf

        metadata = pypdf.PdfReader(str(pdf_path)).metadata or {}
        return metadata.get("/Creator") in (GENERATED_BY, *PAST_CREATORS)
    except Exception:
        return False


def render_manuscript(manuscript, output_path, progress=None, page_size_in=None):
    """Typeset `manuscript` into a 2-column book PDF at `output_path`.

    `page_size_in` overrides the finished page size in the design, in inches.
    The GUI passes half the chosen sheet, so that a book meant for a particular
    paper is *set* at that size rather than set at some other size and scaled on
    to it afterwards — type drawn straight into the PDF at its final size costs
    nothing in sharpness, and scaling it later does. The manuscript is left
    alone: the override is applied to a copy of its design, so it never reaches
    the saved draft.

    The only failure is a manuscript with nothing in it: everything else — a
    chapter with no title, a page size that leaves almost no margin, characters
    the typeface cannot set — is either handled or reported in
    `TypesetResult.notes`, because the alternative is refusing to build a book
    over something the writer can see for themselves on the finished page.
    """
    if not isinstance(manuscript, Manuscript):
        raise TypeError("render_manuscript needs a Manuscript.")
    if not manuscript.has_content():
        raise EmptyBookError(
            "There is nothing to put in the book yet. Give it a title, or type "
            "something into one of the sections."
        )

    design = clamp_design(page_sized(manuscript.design, page_size_in))
    font = ensure_font(design.font_key)
    styles = build_styles(design, font)

    if progress:
        progress(0.1, "Reading the manuscript…")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Built into memory and written in one go, so a failure part way through
    # cannot leave a half-written PDF sitting in Input/ looking like a book.
    buffer = io.BytesIO()
    running_title = manuscript.title.strip() or manuscript.author.strip()
    running_title, _ = prepare_text(running_title, font)
    doc = _BookTemplate(buffer, design, font, styles, running_title.strip(), progress)
    doc.title = manuscript.display_title
    doc.author = manuscript.author.strip()
    doc.subject = "Typeset from a draft in the Bookbinding Signature Creator"
    doc.creator = GENERATED_BY

    story, missing = build_story(manuscript, styles, font, doc)
    if not story:
        raise EmptyBookError(
            "There is nothing to put in the book yet. Give it a title, or type "
            "something into one of the sections."
        )

    if progress:
        progress(0.35, "Setting the type…")
    # multiBuild, not build: the contents page has to be laid out once to learn
    # which book page each chapter starts on, and again to print those numbers.
    doc.multiBuild(story)
    if progress:
        progress(0.9, "Writing the PDF…")

    output_path.write_bytes(buffer.getvalue())

    source_pages = doc.page
    notes = []
    if missing:
        shown = " ".join(sorted(missing)[:12])
        notes.append(
            f"{len(missing)} character(s) in the text have no glyph in "
            f"**{font.name}** and will print as blanks: "
            f"`{shown}`. Try another typeface, or replace those characters."
        )
    if design.chapter_start == CHAPTER_START_RECTO and doc.used_pages:
        # Only the blanks *inside* the book. The last book page is blank
        # whenever the text happens to end on a recto, which has nothing to do
        # with where sections start and is not something to explain away.
        last_used = max(doc.used_pages)
        blanks = [page for page in range(1, last_used) if page not in doc.used_pages]
        if blanks:
            notes.append(
                f"{len(blanks)} page(s) are deliberately blank, because each "
                f"section starts on a right-hand page. Set “Start each section” "
                f"to “On the next page” to close them up."
            )
    if progress:
        progress(1.0, f"Typeset {source_pages} sheet-sides")

    return TypesetResult(
        path=output_path,
        source_pages=source_pages,
        book_pages=2 * source_pages,
        words=manuscript.words,
        sections=len([s for s in manuscript.sections if not s.is_empty]),
        notes=notes,
    )


# --------------------------------------------------------------------------
# An estimate, for before anything is built
# --------------------------------------------------------------------------


def estimate_book_pages(manuscript, page_size_in=None):
    """Roughly how many book pages this manuscript would set to.

    Takes the same `page_size_in` override as `render_manuscript`, so the
    figure on screen is an estimate of the book that would actually be built
    rather than of one at some other page size.

    Deliberately approximate and labelled as such wherever it is shown: it
    exists so the length of a book changes as you type, not to be quoted. The
    exact figures come from the built PDF, which is fast enough to build.
    """
    design = clamp_design(page_sized(manuscript.design, page_size_in))
    font = ensure_font(design.font_key)
    leading = design.font_size_pt * design.line_spacing
    text_width = design.text_width_in * PT_PER_INCH
    text_height = design.text_height_in * PT_PER_INCH
    if leading <= 0 or text_width <= 0 or text_height <= 0:
        return 0

    sample = "Whereas the average English sentence, set in a book face, runs on."
    try:
        width = pdfmetrics.stringWidth(sample, font.regular, design.font_size_pt)
    except Exception:
        width = design.font_size_pt * 0.5 * len(sample)
    per_character = width / len(sample) if width else design.font_size_pt * 0.5
    characters_per_line = max(text_width / per_character, 1.0)
    lines_per_page = max(int(text_height // leading), 1)

    pages = 0
    listed = 0
    for section in manuscript.sections:
        if section.is_empty:
            continue
        listed += kind_of(section.kind).style != "display"
        lines = 4  # the heading and the drop above it
        for block in parse_blocks(prepare_text(section.text)[0]):
            if block.style == SCENE_BREAK:
                lines += 2
                continue
            plain = re.sub(r"<[^>]+>", "", block.text)
            lines += max(math.ceil(len(plain) / characters_per_line), 1)
            if block.style in (SUBHEADING, SUBSUBHEADING):
                lines += 2
        pages += max(math.ceil(lines / lines_per_page), 1)
        if design.chapter_start == CHAPTER_START_RECTO:
            pages += 0.5  # on average, half a blank page per section

    # The same three conditions `build_story` uses, so a book that would come
    # out empty is estimated at nothing rather than at the pages it does not
    # have.
    if design.title_page and manuscript.has_title_page():
        pages += 1
    if design.copyright_page and manuscript.imprint_lines():
        pages += 1
    if design.contents and listed:
        pages += 1
    return int(math.ceil(pages))
