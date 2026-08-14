"""The book a user types in, and the folder of drafts they keep it in.

Three things live here, and nothing else:

  * the **shape of a book** — which kinds of section a book can have and what
    order they come in (`KINDS`, `FRONT_KEYS`, `BODY_KEYS`, `BACK_KEYS`);
  * the **manuscript itself** — a plain, mutable object the GUI edits in place
    and that serialises to and from JSON (`Manuscript`, `Design`, `Section`);
  * the **drafts folder** — save, load, list, rename, duplicate and delete, so a
    half-written book survives closing the app (`save_draft` and friends).

No PDF is produced here and no Streamlit is imported. Typesetting reads this
module; this module knows nothing about typesetting, which is what lets the
tests build a manuscript and check it without going near reportlab.
"""

import json
import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

# Bumped only if an old file would otherwise be read wrongly. `from_dict`
# ignores keys it does not know and fills in the ones that are missing, so a
# draft written by an older build still opens.
SCHEMA_VERSION = 1

DRAFT_SUFFIX = ".book.json"


def new_id():
    """A stable handle for one section.

    The GUI keys its widgets on this. It has to survive reordering and deleting
    — a key derived from the position instead would hand a chapter's text to
    whichever chapter moved into its place.
    """
    return uuid4().hex[:12]


# --------------------------------------------------------------------------
# What kinds of section a book can have
# --------------------------------------------------------------------------
# `style` is the only thing the typesetter reads:
#
#   display  - centred on its own page, no heading unless one is typed
#              (dedication, epigraph)
#   part     - a divider page announcing a group of chapters
#   chapter  - a chapter opening: the numbering sequence runs through these
#   section  - everything else with a heading (foreword, appendix, ...)


@dataclass(frozen=True)
class SectionKind:
    key: str
    label: str          # what the "Add …" menu calls it
    heading: str        # the heading printed in the book, unless overridden
    style: str
    numbered: bool = False
    hint: str = ""      # placeholder text for the empty text box


def _kind(key, label, heading, style, numbered=False, hint=""):
    return SectionKind(key, label, heading, style, numbered, hint)


KINDS = {
    kind.key: kind
    for kind in (
        # ---- front matter -------------------------------------------------
        _kind("dedication", "Dedication", "", "display",
              hint="For Anna, who read every draft."),
        _kind("epigraph", "Epigraph", "", "display",
              hint="A short quotation, and who said it.\n\n- Marcus Aurelius"),
        _kind("foreword", "Foreword", "Foreword", "section",
              hint="Written by somebody other than the author."),
        _kind("preface", "Preface", "Preface", "section",
              hint="The author on how and why the book came to be."),
        _kind("acknowledgements", "Acknowledgements", "Acknowledgements", "section",
              hint="Thanks to the people who helped."),
        _kind("introduction", "Introduction", "Introduction", "section",
              hint="What the book is about and how to read it."),
        _kind("prologue", "Prologue", "Prologue", "section",
              hint="A scene before the story proper begins."),
        _kind("note_to_reader", "A note to the reader", "A Note to the Reader",
              "section", hint="Content notes, pronunciation, a map, a warning."),
        _kind("front_custom", "Something else…", "", "section",
              hint="Give it whatever heading you like."),
        # ---- body ---------------------------------------------------------
        _kind("chapter", "Chapter", "", "chapter", numbered=True,
              hint="The chapter's text. A blank line starts a new paragraph."),
        _kind("part", "Part divider", "", "part",
              hint="Optional: a line or two under the part title."),
        _kind("interlude", "Interlude", "Interlude", "section",
              hint="An unnumbered break between chapters."),
        _kind("body_custom", "Unnumbered section", "", "section",
              hint="A section in the body that is not a numbered chapter."),
        # ---- back matter --------------------------------------------------
        _kind("epilogue", "Epilogue", "Epilogue", "section",
              hint="A scene after the story proper ends."),
        _kind("afterword", "Afterword", "Afterword", "section",
              hint="The author, looking back at the finished book."),
        _kind("authors_note", "Author's note", "Author's Note", "section",
              hint="What is true, what is invented, what was changed."),
        _kind("back_acknowledgements", "Acknowledgements", "Acknowledgements",
              "section", hint="Thanks to the people who helped."),
        _kind("appendix", "Appendix", "Appendix", "section",
              hint="Tables, documents, anything that would interrupt the text."),
        _kind("glossary", "Glossary", "Glossary", "section",
              hint="Term: what it means.\n\nAnother term: what that means."),
        _kind("notes", "Notes", "Notes", "section",
              hint="Numbered notes referred to in the text."),
        _kind("further_reading", "Further reading", "Further Reading", "section",
              hint="Where to go next."),
        _kind("bibliography", "Bibliography", "Bibliography", "section",
              hint="Sources, one per paragraph."),
        _kind("about_the_author", "About the author", "About the Author",
              "section", hint="A short biography, in the third person."),
        _kind("also_by", "Also by the author", "Also by the Author", "section",
              hint="Other titles, one per line."),
        _kind("colophon", "Colophon", "Colophon", "section",
              hint="How the book was made: the type, the paper, the binding."),
        _kind("back_custom", "Something else…", "", "section",
              hint="Give it whatever heading you like."),
    )
}

FRONT_KEYS = (
    "dedication", "epigraph", "foreword", "preface", "acknowledgements",
    "introduction", "prologue", "note_to_reader", "front_custom",
)
BODY_KEYS = ("chapter", "part", "interlude", "body_custom")
BACK_KEYS = (
    "epilogue", "afterword", "authors_note", "back_acknowledgements", "appendix",
    "glossary", "notes", "further_reading", "bibliography", "about_the_author",
    "also_by", "colophon", "back_custom",
)

# A kind read from a draft that this build no longer has. Falling back to a
# plain headed section keeps the user's words rather than dropping them.
FALLBACK_KIND = "body_custom"


def kind_of(key):
    return KINDS.get(key) or KINDS[FALLBACK_KIND]


# --------------------------------------------------------------------------
# Typographic choices
# --------------------------------------------------------------------------

CHAPTER_START_NEW_PAGE = "page"
CHAPTER_START_RECTO = "recto"
CHAPTER_STARTS = (CHAPTER_START_NEW_PAGE, CHAPTER_START_RECTO)

LABEL_NONE = "none"
LABEL_NUMBER = "number"
LABEL_CHAPTER_NUMBER = "chapter_number"
LABEL_CHAPTER_WORD = "chapter_word"
CHAPTER_LABELS = (LABEL_NONE, LABEL_NUMBER, LABEL_CHAPTER_NUMBER, LABEL_CHAPTER_WORD)

DEFAULT_SCENE_BREAK = "* * *"


@dataclass
class Design:
    """Everything about how the book *looks*, as opposed to what it says.

    Lengths are inches, sizes are points — the same convention as the rest of
    the app, so units are converted only where a person reads or types them.

    `page_width_in` and `page_height_in` are one **book page**, i.e. one page as
    the reader sees it. The PDF this becomes has pages twice as wide, because a
    source page carries two book pages side by side; the typesetter does that
    doubling, and nothing here should.
    """

    page_size_name: str = "A5"
    page_width_in: float = 148 / 25.4
    page_height_in: float = 210 / 25.4

    margin_inner_in: float = 0.6      # against the fold
    margin_outer_in: float = 0.5
    margin_top_in: float = 0.6
    margin_bottom_in: float = 0.7

    font_key: str = "times"
    font_size_pt: float = 10.5
    line_spacing: float = 1.35        # multiple of the font size
    justify: bool = True
    first_line_indent_in: float = 0.18
    paragraph_space_pt: float = 0.0   # indent *or* space; both at once is loud

    # New page, not right-hand page. A printed book does start its chapters on
    # rectos, but it gets there by leaving the facing page blank — and a book
    # typed into a text box tends to have short sections, so that convention can
    # easily leave a third of the paper empty. It is one click away in the
    # editor for anyone who wants it.
    chapter_start: str = CHAPTER_START_NEW_PAGE
    chapter_label: str = LABEL_CHAPTER_NUMBER
    running_heads: bool = True
    page_numbers: bool = True

    title_page: bool = True
    copyright_page: bool = True
    contents: bool = True

    scene_break: str = DEFAULT_SCENE_BREAK

    @property
    def source_page_size_in(self):
        """The PDF page this design produces: two book pages side by side."""
        return (2 * self.page_width_in, self.page_height_in)

    @property
    def text_width_in(self):
        return self.page_width_in - self.margin_inner_in - self.margin_outer_in

    @property
    def text_height_in(self):
        return self.page_height_in - self.margin_top_in - self.margin_bottom_in


# --------------------------------------------------------------------------
# The manuscript
# --------------------------------------------------------------------------


@dataclass
class Section:
    kind: str = "chapter"
    heading: str = ""
    text: str = ""
    id: str = field(default_factory=new_id)

    @property
    def spec(self):
        return kind_of(self.kind)

    @property
    def printed_heading(self):
        """The heading that actually goes in the book."""
        typed = self.heading.strip()
        return typed or self.spec.heading

    @property
    def words(self):
        return count_words(self.text)

    @property
    def is_empty(self):
        return not self.heading.strip() and not self.text.strip()


# The boxes that belong to the copyright page, and nothing else. Filling in any
# one of them is what asks for that page to be printed; see
# `Manuscript.has_imprint`.
IMPRINT_FIELDS = ("publisher", "year", "edition", "isbn", "copyright_notice",
                  "rights")


@dataclass
class Manuscript:
    title: str = ""
    subtitle: str = ""
    author: str = ""
    series: str = ""

    publisher: str = ""
    year: str = ""
    edition: str = ""
    isbn: str = ""
    copyright_notice: str = ""
    rights: str = ""

    front: list = field(default_factory=list)
    body: list = field(default_factory=list)
    back: list = field(default_factory=list)

    design: Design = field(default_factory=Design)

    # ---- convenience the GUI and the typesetter both want ----------------
    @property
    def sections(self):
        """Every section, in the order it is printed."""
        return list(self.front) + list(self.body) + list(self.back)

    @property
    def display_title(self):
        return self.title.strip() or "Untitled book"

    @property
    def words(self):
        return sum(section.words for section in self.sections)

    @property
    def chapters(self):
        return [s for s in self.body if kind_of(s.kind).numbered]

    def has_content(self):
        """Is there anything at all to print?

        A book with no sections is still a book if it has a title page, so this
        is deliberately generous: it is the one condition that stops a build,
        and it should only be true when there would be nothing on the paper.
        """
        if self.title.strip() or self.author.strip() or self.subtitle.strip():
            return True
        return any(not section.is_empty for section in self.sections)

    def list_for(self, part):
        return {"front": self.front, "body": self.body, "back": self.back}[part]

    def copyright_line(self):
        """The © line, made from what has been filled in.

        Empty when there is nothing to say, so the copyright page never carries
        a bare '©' with a blank beside it. The author is used as the holder,
        but only ever as *part* of this line — naming an author does not by
        itself ask for a copyright page; see `has_imprint`.
        """
        typed = self.copyright_notice.strip()
        if typed:
            return typed
        holder = self.author.strip()
        year = self.year.strip()
        if holder and year:
            return f"Copyright © {year} {holder}"
        if holder:
            return f"Copyright © {holder}"
        if year:
            return f"Copyright © {year}"
        return ""

    def has_title_page(self):
        return bool(self.title.strip() or self.subtitle.strip()
                    or self.author.strip())

    def has_imprint(self):
        """Has the writer actually asked for a copyright page?

        Only the boxes inside **Publication details** count. The author is not
        one of them: it is typed on the title page, every book has one, and
        deriving a copyright page from it printed a whole extra leaf — reading
        "Copyright © A. Writer" and nothing else — in front of every book whose
        author had been named. A page nobody asked for is worse than no page,
        because it costs a sheet of paper and looks like a bug.
        """
        return any(getattr(self, name).strip() for name in IMPRINT_FIELDS)

    def imprint_lines(self):
        """What goes on the copyright page, in order, with the blanks dropped.

        Empty — and so no copyright page at all — until one of the publication
        details is filled in. One list, read both by the typesetter that prints
        the page and by the length estimate that has to know whether there will
        be a page at all, so the two can never disagree about it.
        """
        if not self.has_imprint():
            return []
        publisher = self.publisher.strip()
        isbn = self.isbn.strip()
        pieces = (
            self.copyright_line(),
            self.rights.strip(),
            f"Published by {publisher}" if publisher else "",
            self.edition.strip(),
            f"ISBN {isbn}" if isbn else "",
        )
        return [piece for piece in pieces if piece]

    # ---- serialisation ---------------------------------------------------
    def to_dict(self):
        return {
            "schema": SCHEMA_VERSION,
            "title": self.title,
            "subtitle": self.subtitle,
            "author": self.author,
            "series": self.series,
            "publisher": self.publisher,
            "year": self.year,
            "edition": self.edition,
            "isbn": self.isbn,
            "copyright_notice": self.copyright_notice,
            "rights": self.rights,
            "front": [_section_to_dict(s) for s in self.front],
            "body": [_section_to_dict(s) for s in self.body],
            "back": [_section_to_dict(s) for s in self.back],
            "design": _design_to_dict(self.design),
        }

    @classmethod
    def from_dict(cls, data):
        data = data if isinstance(data, dict) else {}
        book = cls(
            title=_text(data.get("title")),
            subtitle=_text(data.get("subtitle")),
            author=_text(data.get("author")),
            series=_text(data.get("series")),
            publisher=_text(data.get("publisher")),
            year=_text(data.get("year")),
            edition=_text(data.get("edition")),
            isbn=_text(data.get("isbn")),
            copyright_notice=_text(data.get("copyright_notice")),
            rights=_text(data.get("rights")),
            design=_design_from_dict(data.get("design")),
        )
        for part in ("front", "body", "back"):
            raw = data.get(part)
            if not isinstance(raw, list):
                continue
            book.list_for(part).extend(
                _section_from_dict(item) for item in raw if isinstance(item, dict)
            )
        return book

    def to_json(self):
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text):
        return cls.from_dict(json.loads(text))


def _text(value):
    """Anything out of a JSON file, as the string the model expects."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _section_to_dict(section):
    return {
        "id": section.id, "kind": section.kind,
        "heading": section.heading, "text": section.text,
    }


def _section_from_dict(data):
    section = Section(
        kind=_text(data.get("kind")) or "chapter",
        heading=_text(data.get("heading")),
        text=_text(data.get("text")),
    )
    # An id from the file is kept so widget state survives a reload, but an
    # empty or duplicated one would collide two sections' widgets, so anything
    # that does not look like an id gets a fresh one.
    saved_id = _text(data.get("id")).strip()
    if saved_id:
        section.id = saved_id[:32]
    return section


def _design_to_dict(design):
    return {name: getattr(design, name) for name in _DESIGN_FIELDS}


_DESIGN_FIELDS = tuple(Design.__dataclass_fields__)


def _design_from_dict(data):
    """A Design from a file, with every value put back inside its own limits.

    A hand-edited draft is a plain JSON file, so this cannot assume the numbers
    in it are sane. Clamping rather than raising keeps a mistyped margin from
    making a draft unopenable.
    """
    design = Design()
    if not isinstance(data, dict):
        return design
    for name in _DESIGN_FIELDS:
        if name not in data:
            continue
        value = data[name]
        current = getattr(design, name)
        try:
            if isinstance(current, bool):
                setattr(design, name, bool(value))
            elif isinstance(current, float):
                setattr(design, name, float(value))
            elif isinstance(current, str):
                setattr(design, name, _text(value))
        except (TypeError, ValueError):
            continue
    return clamp_design(design)


# Limits shared by the loader and the GUI's spin boxes, so a draft cannot hold
# a value the interface would refuse to show.
DESIGN_LIMITS = {
    "page_width_in": (1.0, 24.0),
    "page_height_in": (1.0, 24.0),
    "margin_inner_in": (0.0, 4.0),
    "margin_outer_in": (0.0, 4.0),
    "margin_top_in": (0.0, 4.0),
    "margin_bottom_in": (0.0, 4.0),
    "font_size_pt": (5.0, 24.0),
    "line_spacing": (1.0, 2.5),
    "first_line_indent_in": (0.0, 1.0),
    "paragraph_space_pt": (0.0, 24.0),
}

# The smallest strip of paper worth setting type in. A margin pair that leaves
# less than this is shrunk to fit rather than refused: the user is mid-edit,
# and a book that cannot be built is a worse answer than one with tight margins.
MIN_TEXT_IN = 0.75


def page_sized(design, page_size_in=None):
    """`design`, or a copy of it set to a different finished page size.

    The GUI uses this to set a book at the size of the paper it is going to be
    printed on, so that nothing has to be scaled afterwards. It is a *copy*
    because that choice belongs to one build and must never reach the saved
    draft; with nothing to override it hands back the very object it was given,
    so the ordinary path is untouched.
    """
    if page_size_in is None:
        return design
    width_in, height_in = page_size_in
    return replace(
        design,
        page_width_in=float(width_in),
        page_height_in=float(height_in),
    )


def clamp_design(design):
    """Bring one Design inside its limits, in place, and return it."""
    for name, (low, high) in DESIGN_LIMITS.items():
        try:
            value = float(getattr(design, name))
        except (TypeError, ValueError):
            value = getattr(Design(), name)
        setattr(design, name, min(max(value, low), high))

    if design.chapter_start not in CHAPTER_STARTS:
        design.chapter_start = CHAPTER_START_NEW_PAGE
    if design.chapter_label not in CHAPTER_LABELS:
        design.chapter_label = LABEL_CHAPTER_NUMBER

    # Margins that swallow the page. Scaled down together so the proportions
    # the user chose survive; see MIN_TEXT_IN.
    for across, low, high in (
        (True, "margin_inner_in", "margin_outer_in"),
        (False, "margin_top_in", "margin_bottom_in"),
    ):
        span = design.page_width_in if across else design.page_height_in
        total = getattr(design, low) + getattr(design, high)
        room = span - MIN_TEXT_IN
        if total > room:
            shrink = max(room, 0.0) / total if total > 0 else 0.0
            setattr(design, low, getattr(design, low) * shrink)
            setattr(design, high, getattr(design, high) * shrink)
    return design


# --------------------------------------------------------------------------
# Counting and numbering
# --------------------------------------------------------------------------

_WORD = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*|\d[\d,.]*", re.UNICODE)


def count_words(text):
    """Words, counting a hyphenated or apostrophised run as one."""
    return len(_WORD.findall(text or ""))


_ONES = ("", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
         "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
         "Sixteen", "Seventeen", "Eighteen", "Nineteen")
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
         "Eighty", "Ninety")


def number_word(number):
    """"Twenty-Three" for 23. Falls back to the digits above 999."""
    if not isinstance(number, int) or not 1 <= number <= 999:
        return str(number)
    if number < 20:
        return _ONES[number]
    if number < 100:
        tens, ones = divmod(number, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    hundreds, rest = divmod(number, 100)
    text = f"{_ONES[hundreds]} Hundred"
    return f"{text} {number_word(rest)}" if rest else text


def chapter_label(design, number):
    """The line above a chapter title: "Chapter Four", "4", or nothing."""
    style = design.chapter_label
    if style == LABEL_NONE:
        return ""
    if style == LABEL_NUMBER:
        return str(number)
    if style == LABEL_CHAPTER_WORD:
        return f"Chapter {number_word(number)}"
    return f"Chapter {number}"


def numbered_sections(manuscript):
    """{section id: chapter number} for the sections the numbering runs through."""
    return {section.id: n for n, section in enumerate(manuscript.chapters, start=1)}


# --------------------------------------------------------------------------
# The drafts folder
# --------------------------------------------------------------------------
# A draft is one JSON file named after the draft. The name is what the user
# typed, cleaned up enough to be a file name on this platform — not a hash, so
# the folder stays readable and a draft can be copied to another machine by
# copying the file.

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Windows refuses these as file names whatever the extension.
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{stem}{digit}" for stem in ("COM", "LPT") for digit in "123456789"
}

MAX_NAME = 80


def clean_draft_name(name, fallback="Untitled book"):
    """The name a draft can actually be saved under.

    Every path separator goes, so a name can only ever land in the drafts
    folder itself, and trailing dots and spaces go because Windows silently
    drops them and would then open a different file than the one it wrote.
    """
    name = " ".join(_ILLEGAL.sub(" ", _text(name)).split())
    name = name.strip(" .")
    if name.upper() in _RESERVED:
        name = f"{name} book"
    name = name[:MAX_NAME].strip(" .")
    return name or fallback


def draft_path(folder, name):
    return Path(folder) / f"{clean_draft_name(name)}{DRAFT_SUFFIX}"


def draft_name_of(path):
    """The draft name a file holds, i.e. its name without the suffix."""
    name = Path(path).name
    return name[: -len(DRAFT_SUFFIX)] if name.endswith(DRAFT_SUFFIX) else Path(path).stem


def unique_draft_name(folder, name, ignore=None):
    """`name`, or "name 2", "name 3"… — the first that is not taken.

    `ignore` is a path allowed to hold the name already, so re-saving a draft
    under its own name does not walk it up to "My book 2".
    """
    folder = Path(folder)
    base = clean_draft_name(name)
    ignore = Path(ignore).resolve() if ignore else None
    candidate = base
    for suffix in range(2, 1000):
        path = draft_path(folder, candidate)
        if not path.exists() or (ignore and path.resolve() == ignore):
            return clean_draft_name(candidate)
        candidate = clean_draft_name(f"{base[: MAX_NAME - 5]} {suffix}")
    return clean_draft_name(f"{base[: MAX_NAME - 14]} {datetime.now():%Y%m%d%H%M%S}")


@dataclass(frozen=True)
class DraftInfo:
    """One row of the saved-drafts list.

    `problem` is set instead of raising when a file will not read. The list is
    built while the whole page is being drawn, so one damaged draft has to be a
    line that says so, not an exception that takes the interface down with it.
    """

    name: str
    path: Path
    modified: float
    title: str = ""
    author: str = ""
    words: int = 0
    sections: int = 0
    problem: str = ""

    @property
    def modified_text(self):
        return f"{datetime.fromtimestamp(self.modified):%d %b %Y, %H:%M}"


def list_drafts(folder):
    """Every saved draft, most recently changed first."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    drafts = []
    for path in folder.glob(f"*{DRAFT_SUFFIX}"):
        if not path.is_file():
            continue
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        name = draft_name_of(path)
        try:
            book = load_draft(path)
        except Exception as error:
            drafts.append(DraftInfo(name, path, modified, problem=str(error)))
            continue
        drafts.append(DraftInfo(
            name=name, path=path, modified=modified,
            title=book.display_title, author=book.author.strip(),
            words=book.words, sections=len(book.sections),
        ))
    drafts.sort(key=lambda draft: draft.modified, reverse=True)
    return drafts


def load_draft(path):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # A draft copied through a Windows editor can come back in the local
        # code page. Reading it is better than telling the user it is broken.
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("This file does not hold a book draft.")
    return Manuscript.from_dict(data)


def save_draft(folder, manuscript, name):
    """Write one draft, all of it or none of it.

    Through a temporary file and a rename, because this is the copy of the
    user's writing that has to survive: saving straight over the file means a
    crash or a full disk half way leaves a truncated JSON file, and the draft
    it replaced is already gone.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = draft_path(folder, name)
    temporary = target.with_name(f"{target.name}.saving-{os.getpid()}.tmp")
    try:
        temporary.write_text(manuscript.to_json(), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    return target


def delete_draft(folder, path):
    """Delete one draft, and refuse anything that is not one.

    Same rule as the app's other delete: resolve first, and require the file to
    sit inside the drafts folder and to carry the drafts suffix. A draft is the
    only copy of somebody's writing, so the check is worth its one stat call.
    """
    folder = Path(folder).resolve()
    target = Path(path).resolve()
    if folder not in target.parents:
        raise ValueError(f"“{path}” is not inside {folder}, so it will not be deleted.")
    if not target.name.endswith(DRAFT_SUFFIX):
        raise ValueError(f"“{target.name}” is not a book draft.")
    if not target.is_file():
        raise FileNotFoundError(f"“{draft_name_of(target)}” is not there any more.")
    target.unlink()
    return target


def rename_draft(folder, path, new_name):
    """Move one draft to a new name, refusing to write over another draft."""
    folder = Path(folder)
    old = Path(path)
    target = draft_path(folder, new_name)
    if target.resolve() == old.resolve():
        return old
    if target.exists():
        raise FileExistsError(f"A draft called “{draft_name_of(target)}” already exists.")
    if not old.is_file():
        raise FileNotFoundError(f"“{draft_name_of(old)}” is not there any more.")
    os.replace(old, target)
    return target


# --------------------------------------------------------------------------
# Starting points
# --------------------------------------------------------------------------


def blank_manuscript():
    """A new book: one empty chapter, so there is somewhere to start typing."""
    return Manuscript(body=[Section(kind="chapter")])


# Lorem ipsum, so the example is unmistakably filler and nobody reads it for
# meaning. Kept as separate paragraphs so a chapter can be composed out of them
# without repeating the same wall of text word for word.
_LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim "
    "veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea "
    "commodo consequat.",

    "Duis aute irure dolor in reprehenderit in voluptate velit esse cillum "
    "dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non "
    "proident, sunt in culpa qui officia deserunt mollit anim id est laborum.",

    "Sed ut perspiciatis unde omnis iste natus error sit voluptatem "
    "accusantium doloremque laudantium, totam rem aperiam, eaque ipsa quae ab "
    "illo inventore veritatis et quasi architecto beatae vitae dicta sunt "
    "explicabo.",

    "Nemo enim ipsam voluptatem quia voluptas sit aspernatur aut odit aut "
    "fugit, sed quia consequuntur magni dolores eos qui ratione voluptatem "
    "sequi nesciunt. Neque porro quisquam est, qui dolorem ipsum quia dolor "
    "sit amet, consectetur, adipisci velit.",

    "At vero eos et accusamus et iusto odio dignissimos ducimus qui blanditiis "
    "praesentium voluptatum deleniti atque corrupti quos dolores et quas "
    "molestias excepturi sint occaecati cupiditate non provident.",

    "Temporibus autem quibusdam et aut officiis debitis aut rerum "
    "necessitatibus saepe eveniet ut et voluptates repudiandae sint et "
    "molestiae non recusandae. Itaque earum rerum hic tenetur a sapiente "
    "delectus, ut aut reiciendis voluptatibus maiores alias consequatur.",
)


def _lorem(*indexes):
    """A few lorem paragraphs, blank-line separated the way the editor wants."""
    return "\n\n".join(_LOREM[index % len(_LOREM)] for index in indexes)


def example_manuscript():
    """A complete book that shows what every part of the editor does.

    Filler text on purpose — it is loaded as an ordinary unsaved draft and is
    meant to be typed straight over. What is *not* filler is the shape: five
    chapters around a part divider and an interlude, a dedication and an
    epigraph in front of the contents, and enough back matter to show where
    each kind of section lands in the finished book.
    """
    return Manuscript(
        title="The Folded Sheet",
        subtitle="A Very Short Book About Making Books",
        author="A. Binder",
        series="Book One of the Bindery",
        publisher="Kitchen Table Press",
        year=str(datetime.now().year),
        edition="First edition",
        isbn="978-0-00-000000-0",
        rights=(
            "Set from a draft typed into the Bookbinding Signature Creator.\n\n"
            "The text is lorem ipsum: replace it with your own."
        ),
        front=[
            Section(
                kind="dedication",
                text="For everyone who has ever\nfolded paper the wrong way.",
            ),
            Section(
                kind="epigraph",
                text="A book is a machine to think with.\n\n- I. A. Richards",
            ),
            Section(
                kind="preface",
                text=(
                    "Everything you can see in this book came out of a text box, "
                    "and so will yours. Type over any of it.\n\n"
                    "Leave a blank line between paragraphs. Put *one star* around "
                    "a phrase for italics and **two stars** for bold. A line "
                    "starting with a hash is a heading inside the section, and a "
                    "line of three stars is a scene break.\n\n"
                    "> A line beginning with a greater-than sign is set as a "
                    "quotation,\n"
                    "> and keeps the line breaks you type.\n\n"
                    + _lorem(0, 1)
                ),
            ),
        ],
        body=[
            Section(
                kind="part",
                heading="Part One: The Sheet",
                text="A part divider announces a group of chapters.",
            ),
            Section(
                kind="chapter",
                heading="One Sheet, Four Pages",
                text=(
                    _lorem(0)
                    + "\n\n# A heading inside a chapter\n\n"
                    + _lorem(1, 2)
                    + "\n\n* * *\n\n"
                    + _lorem(3)
                ),
            ),
            Section(
                kind="chapter",
                heading="Getting the Order Right",
                text=_lorem(2, 3, 4),
            ),
            Section(
                kind="chapter",
                heading="Nesting and Sewing",
                text=(
                    _lorem(4, 5)
                    + "\n\n> Nemo enim ipsam voluptatem quia voluptas sit "
                    "aspernatur,\n> aut odit aut fugit.\n\n"
                    + _lorem(0)
                ),
            ),
            Section(
                kind="interlude",
                text=(
                    "An interlude sits between chapters without joining the "
                    "numbering.\n\n" + _lorem(5)
                ),
            ),
            Section(
                kind="chapter",
                heading="Paper, Grain and Thread",
                text=_lorem(1, 2, 5),
            ),
            Section(
                kind="chapter",
                heading="The Finished Book",
                text=_lorem(3, 4, 0),
            ),
        ],
        back=[
            Section(kind="epilogue", text=_lorem(5, 1)),
            Section(
                kind="appendix",
                heading="Appendix: Paper Sizes",
                text=(
                    "A sheet is folded across its width, so one sheet of W by H "
                    "paper gives four book pages of W/2 by H.\n\n"
                    "# Worked example\n\n"
                    "A book page of 148 by 210 millimetres wants a sheet of 296 "
                    "by 210, which is A4 turned on its side."
                ),
            ),
            Section(
                kind="glossary",
                text=(
                    "**Signature**: a few sheets folded together and nested one "
                    "inside another.\n\n"
                    "**Recto**: a right-hand page. Odd numbered, always.\n\n"
                    "**Verso**: a left-hand page, on the back of the recto "
                    "before it."
                ),
            ),
            Section(
                kind="about_the_author",
                text=(
                    "A. Binder has been folding paper since long before it was "
                    "necessary, and shows no sign of stopping."
                ),
            ),
            Section(
                kind="colophon",
                text=(
                    "Set in Times, on paper chosen in Book design, by a program "
                    "that would rather be folding."
                ),
            ),
        ],
    )
