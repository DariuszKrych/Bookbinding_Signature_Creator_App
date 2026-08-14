"""Tests for the book editor: the manuscript, the drafts folder, the typesetting.

Run with:  python -m unittest Script.test_manuscript -v

Same two rules as `test_imposition`, for the same reason:

  * The typesetting tests do not ask the typesetter where it put anything. They
    build a real PDF and then read the finished file — including the table of
    contents, whose printed page numbers are checked against where the chapters
    *actually* start, found by hunting for a marker word in the text.
  * The reading is done with `test_imposition`'s own small PDF interpreter,
    which owes nothing to the code under test and is itself checked against
    pypdf over there.
"""

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pypdf  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402

import main  # noqa: E402

from Script.manuscript import (  # noqa: E402
    BACK_KEYS,
    BODY_KEYS,
    CHAPTER_START_NEW_PAGE,
    CHAPTER_START_RECTO,
    DRAFT_SUFFIX,
    FRONT_KEYS,
    KINDS,
    LABEL_CHAPTER_WORD,
    LABEL_NONE,
    LABEL_NUMBER,
    MIN_TEXT_IN,
    Design,
    Manuscript,
    Section,
    blank_manuscript,
    chapter_label,
    clamp_design,
    clean_draft_name,
    count_words,
    delete_draft,
    draft_name_of,
    example_manuscript,
    kind_of,
    list_drafts,
    load_draft,
    number_word,
    numbered_sections,
    rename_draft,
    save_draft,
    unique_draft_name,
)
from Script.paper_sizes import PT_PER_INCH  # noqa: E402
from Script.print_formatting import plan_signatures, split_to_signatures  # noqa: E402
from Script.test_imposition import read_text_runs  # noqa: E402
from Script.typesetting import (  # noqa: E402
    FONTS,
    PARAGRAPH,
    QUOTE,
    SCENE_BREAK,
    SUBHEADING,
    SUBSUBHEADING,
    EmptyBookError,
    escape,
    estimate_book_pages,
    inline_markup,
    is_generated_book,
    parse_blocks,
    prepare_text,
    render_manuscript,
)


# --------------------------------------------------------------------------
# Reading a finished book back
# --------------------------------------------------------------------------


def runs_by_book_page(path):
    """{book page number: [(label, x within that page, y)]}.

    A source page holds two book pages side by side, so which half a run falls
    in is what decides its book page. Odd book pages are the left column.
    """
    reader = pypdf.PdfReader(str(path))
    pages = {}
    for index, page in enumerate(reader.pages):
        half = float(page.mediabox.width) / 2
        for label, (x, y), _visible in read_text_runs(page):
            column = 0 if x < half else 1
            book_page = 2 * index + 1 + column
            pages.setdefault(book_page, []).append((label, x - column * half, y))
    return pages


def page_holding(pages, needle):
    """The book page a piece of text appears on, or None."""
    for book_page in sorted(pages):
        if any(needle in label for label, _x, _y in pages[book_page]):
            return book_page
    return None


def folio_on(pages, book_page, design):
    """The page number printed at the foot of one book page, if any."""
    bottom = design.margin_bottom_in * PT_PER_INCH
    for label, _x, y in pages.get(book_page, ()):
        if y < bottom and label.strip().isdigit():
            return label.strip()
    return None


def running_head_on(pages, book_page, design):
    top = (design.page_height_in - design.margin_top_in) * PT_PER_INCH
    for label, _x, y in pages.get(book_page, ()):
        if y > top:
            return label
    return None


def long_text(marker, paragraphs=6):
    """A chapter that reliably runs over one page, starting with `marker`."""
    filler = ("The sentence goes on and on and wraps around the column more "
              "than once, which is the point of it. ")
    return "\n\n".join(
        (f"{marker} " if index == 0 else "") + filler * 8
        for index in range(paragraphs)
    )


class BookTestCase(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp(prefix="manuscript-test-"))
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)

    def build(self, book, name="book.pdf", page_size_in=None):
        path = self.workspace / name
        return render_manuscript(book, path, page_size_in=page_size_in), path


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------


class TestManuscriptModel(unittest.TestCase):
    def test_a_full_book_survives_a_json_round_trip_unchanged(self):
        book = example_manuscript()
        again = Manuscript.from_json(book.to_json())
        self.assertEqual(again.to_dict(), book.to_dict())
        # Section identity survives too, which is what keeps the editor's
        # widgets attached to the right chapter after a reload.
        self.assertEqual([s.id for s in again.sections], [s.id for s in book.sections])

    def test_the_example_shows_off_every_part_of_a_book(self):
        """It is what “✨ Load the example” has to teach with, so check it does.

        Five chapters, something in each of the three parts of the book, and
        every piece of markup the formatting note promises — otherwise the one
        button that exists to show what the editor can do leaves half of it out.
        """
        book = example_manuscript()
        self.assertEqual(len(book.chapters), 5)
        self.assertTrue(book.front and book.body and book.back)
        self.assertTrue(book.has_imprint(), "no copyright page to look at")
        self.assertTrue(book.has_title_page())

        kinds = {section.kind for section in book.sections}
        for expected in ("dedication", "epigraph", "preface", "part",
                         "interlude", "epilogue", "appendix", "glossary",
                         "about_the_author", "colophon"):
            self.assertIn(expected, kinds)

        text = "\n".join(section.text for section in book.sections)
        self.assertIn("Lorem ipsum", text)
        for markup in ("\n\n", "*one star*", "**two stars**", "\n# ", "* * *",
                       "\n> "):
            self.assertIn(markup, text, markup)

    def test_every_catalogued_kind_is_real_and_in_exactly_one_menu(self):
        menus = FRONT_KEYS + BODY_KEYS + BACK_KEYS
        self.assertEqual(sorted(menus), sorted(KINDS))
        self.assertEqual(len(menus), len(set(menus)))
        for key, kind in KINDS.items():
            self.assertEqual(kind.key, key)
            self.assertIn(kind.style, ("display", "part", "chapter", "section"))
            self.assertTrue(kind.label)

    def test_a_draft_from_a_stranger_cannot_break_the_model(self):
        for junk in (
            {}, {"title": 5}, {"front": "not a list"}, {"body": [None, 7]},
            {"design": "nope"}, {"body": [{"kind": "no-such-kind", "text": "x"}]},
        ):
            book = Manuscript.from_dict(junk)
            self.assertIsInstance(book.to_json(), str)
        # An unknown kind keeps the words rather than dropping the section.
        book = Manuscript.from_dict({"body": [{"kind": "from-the-future",
                                               "text": "Please keep me."}]})
        self.assertEqual(book.body[0].text, "Please keep me.")
        self.assertEqual(kind_of(book.body[0].kind).style, "section")

    def test_a_section_with_no_id_is_given_one_that_is_its_own(self):
        book = Manuscript.from_dict({"body": [{"kind": "chapter"},
                                              {"kind": "chapter"}]})
        first, second = book.body
        self.assertTrue(first.id and second.id)
        self.assertNotEqual(first.id, second.id)

    def test_only_chapters_are_numbered(self):
        book = Manuscript(
            front=[Section(kind="prologue", text="x")],
            body=[Section(kind="chapter", text="a"), Section(kind="part", text="b"),
                  Section(kind="chapter", text="c"),
                  Section(kind="interlude", text="d"),
                  Section(kind="chapter", text="e")],
            back=[Section(kind="epilogue", text="y")],
        )
        numbers = numbered_sections(book)
        self.assertEqual(list(numbers.values()), [1, 2, 3])
        self.assertEqual(
            [book.body[i].id for i in (0, 2, 4)], list(numbers)
        )

    def test_chapter_labels_read_the_way_they_are_set(self):
        design = Design()
        self.assertEqual(chapter_label(design, 4), "Chapter 4")
        design.chapter_label = LABEL_CHAPTER_WORD
        self.assertEqual(chapter_label(design, 23), "Chapter Twenty-Three")
        design.chapter_label = LABEL_NUMBER
        self.assertEqual(chapter_label(design, 7), "7")
        design.chapter_label = LABEL_NONE
        self.assertEqual(chapter_label(design, 7), "")

    def test_numbers_in_words(self):
        # Checked against English, not against the function's own arithmetic.
        for number, word in ((1, "One"), (11, "Eleven"), (20, "Twenty"),
                             (21, "Twenty-One"), (99, "Ninety-Nine"),
                             (100, "One Hundred"), (101, "One Hundred One"),
                             (342, "Three Hundred Forty-Two")):
            self.assertEqual(number_word(number), word)
        self.assertEqual(number_word(1000), "1000")  # beyond the table

    def test_word_counting_treats_a_hyphenated_word_as_one(self):
        self.assertEqual(count_words(""), 0)
        self.assertEqual(count_words("one two three"), 3)
        self.assertEqual(count_words("It's a well-known fact, isn't it?"), 6)
        self.assertEqual(count_words("1,234 sheep"), 2)

    def test_a_book_has_content_as_soon_as_anything_is_typed(self):
        self.assertFalse(blank_manuscript().has_content())
        self.assertFalse(Manuscript().has_content())
        self.assertTrue(Manuscript(title="T").has_content())
        self.assertTrue(Manuscript(author="A").has_content())
        self.assertTrue(
            Manuscript(body=[Section(kind="chapter", text="hello")]).has_content()
        )
        # A heading with no text still counts: it will be printed.
        self.assertTrue(
            Manuscript(body=[Section(kind="chapter", heading="One")]).has_content()
        )

    def test_the_copyright_line_is_built_from_whatever_is_filled_in(self):
        self.assertEqual(Manuscript().copyright_line(), "")
        self.assertEqual(
            Manuscript(author="A. Binder", year="2026").copyright_line(),
            "Copyright © 2026 A. Binder",
        )
        self.assertEqual(
            Manuscript(author="A", year="2026",
                       copyright_notice="All wrongs reversed").copyright_line(),
            "All wrongs reversed",
        )

    def test_naming_an_author_does_not_ask_for_a_copyright_page(self):
        """The author is a title-page box, and every book fills it in.

        Deriving a copyright page from it printed a whole extra leaf, reading
        "Copyright © A. Binder" and nothing else, in front of every book whose
        author had been named — without anyone asking for one.
        """
        named = Manuscript(title="A Book", author="A. Binder")
        self.assertFalse(named.has_imprint())
        self.assertEqual(named.imprint_lines(), [])

        # One publication detail is enough, and the author then joins the line
        # it belongs on rather than making a page of its own.
        for field, value in (("year", "2026"), ("publisher", "Kitchen Table"),
                             ("edition", "First edition"), ("isbn", "978-0"),
                             ("copyright_notice", "All wrongs reversed"),
                             ("rights", "Moral rights asserted.")):
            book = Manuscript(title="A Book", author="A. Binder",
                              **{field: value})
            with self.subTest(field=field):
                self.assertTrue(book.has_imprint())
                self.assertTrue(book.imprint_lines())
        self.assertIn(
            "Copyright © 2026 A. Binder",
            Manuscript(author="A. Binder", year="2026").imprint_lines(),
        )


class TestDesignLimits(unittest.TestCase):
    def test_out_of_range_numbers_are_pulled_back_rather_than_refused(self):
        design = clamp_design(Design(
            page_width_in=500.0, font_size_pt=-4.0, line_spacing=99.0,
            first_line_indent_in=-1.0,
        ))
        self.assertLessEqual(design.page_width_in, 24.0)
        self.assertGreaterEqual(design.font_size_pt, 5.0)
        self.assertLessEqual(design.line_spacing, 2.5)
        self.assertGreaterEqual(design.first_line_indent_in, 0.0)

    def test_margins_that_swallow_the_page_are_shrunk_in_proportion(self):
        design = clamp_design(Design(
            page_width_in=4.0, margin_inner_in=3.0, margin_outer_in=1.0,
            page_height_in=6.0, margin_top_in=4.0, margin_bottom_in=2.0,
        ))
        self.assertGreaterEqual(design.text_width_in, MIN_TEXT_IN - 1e-9)
        self.assertGreaterEqual(design.text_height_in, MIN_TEXT_IN - 1e-9)
        # In proportion: the inner margin was three times the outer, and stays so.
        self.assertAlmostEqual(
            design.margin_inner_in / design.margin_outer_in, 3.0, places=6
        )

    def test_nonsense_words_fall_back_to_the_shipped_choice(self):
        design = clamp_design(Design(chapter_start="sideways",
                                     chapter_label="roman"))
        self.assertEqual(design.chapter_start, CHAPTER_START_NEW_PAGE)
        self.assertEqual(design.chapter_start, Design().chapter_start)
        self.assertEqual(design.chapter_label, Design().chapter_label)

    def test_a_design_read_from_a_file_is_clamped_on_the_way_in(self):
        book = Manuscript.from_dict({"design": {"font_size_pt": 900,
                                                "margin_top_in": "wide",
                                                "justify": "yes please"}})
        self.assertLessEqual(book.design.font_size_pt, 24.0)
        self.assertEqual(book.design.margin_top_in, Design().margin_top_in)
        self.assertIs(book.design.justify, True)

    def test_the_source_page_is_always_two_book_pages_wide(self):
        design = Design()
        width, height = design.source_page_size_in
        self.assertAlmostEqual(width, 2 * design.page_width_in, places=9)
        self.assertAlmostEqual(height, design.page_height_in, places=9)


# --------------------------------------------------------------------------
# The drafts folder
# --------------------------------------------------------------------------


class TestDrafts(BookTestCase):
    def setUp(self):
        super().setUp()
        self.folder = self.workspace / "Manuscripts"
        self.folder.mkdir()

    def test_a_saved_draft_comes_back_exactly(self):
        book = example_manuscript()
        path = save_draft(self.folder, book, "My book")
        self.assertEqual(path.name, f"My book{DRAFT_SUFFIX}")
        self.assertEqual(load_draft(path).to_dict(), book.to_dict())

    def test_saving_leaves_no_half_written_file_behind(self):
        save_draft(self.folder, example_manuscript(), "My book")
        self.assertEqual(
            sorted(p.name for p in self.folder.iterdir()),
            [f"My book{DRAFT_SUFFIX}"],
        )

    def test_a_name_can_only_ever_land_in_the_folder(self):
        for hostile in ("../escape", r"..\escape", "sub/dir/book", "a:b|c?d*e",
                        "  spaced  ", "trailing...", "CON", "lpt3"):
            path = save_draft(self.folder, Manuscript(title="x"), hostile)
            self.assertEqual(path.parent.resolve(), self.folder.resolve(), hostile)
            self.assertTrue(path.name.endswith(DRAFT_SUFFIX), hostile)

    def test_an_empty_name_still_produces_a_file(self):
        for empty in ("", "   ", "...", "///", None):
            self.assertTrue(clean_draft_name(empty))

    def test_reserved_windows_names_are_moved_out_of_the_way(self):
        for reserved in ("CON", "con", "PRN", "COM1", "lpt9"):
            self.assertNotEqual(clean_draft_name(reserved).upper(), reserved.upper())

    def test_a_free_name_is_found_rather_than_overwriting(self):
        save_draft(self.folder, Manuscript(title="a"), "Book")
        self.assertEqual(unique_draft_name(self.folder, "Book"), "Book 2")
        save_draft(self.folder, Manuscript(title="b"), "Book 2")
        self.assertEqual(unique_draft_name(self.folder, "Book"), "Book 3")
        # Re-saving a draft under its own name is not a clash.
        path = self.folder / f"Book{DRAFT_SUFFIX}"
        self.assertEqual(unique_draft_name(self.folder, "Book", ignore=path), "Book")

    def test_the_listing_is_newest_first_and_describes_each_draft(self):
        import os
        import time

        save_draft(self.folder, Manuscript(title="Older", author="A",
                                           body=[Section(kind="chapter",
                                                         text="one two three")]),
                   "Older")
        time.sleep(0.01)
        save_draft(self.folder, Manuscript(title="Newer"), "Newer")
        # mtimes on Windows can tie; make the order unambiguous.
        older = self.folder / f"Older{DRAFT_SUFFIX}"
        os.utime(older, (0, 0))

        drafts = list_drafts(self.folder)
        self.assertEqual([d.name for d in drafts], ["Newer", "Older"])
        old = drafts[1]
        self.assertEqual(old.title, "Older")
        self.assertEqual(old.author, "A")
        self.assertEqual(old.words, 3)
        self.assertEqual(old.sections, 1)
        self.assertTrue(old.modified_text)

    def test_one_damaged_draft_does_not_take_the_listing_down(self):
        save_draft(self.folder, Manuscript(title="Good"), "Good")
        (self.folder / f"Broken{DRAFT_SUFFIX}").write_text("{ not json",
                                                           encoding="utf-8")
        (self.folder / "notes.txt").write_text("ignore me", encoding="utf-8")

        drafts = {d.name: d for d in list_drafts(self.folder)}
        self.assertEqual(sorted(drafts), ["Broken", "Good"])
        self.assertTrue(drafts["Broken"].problem)
        self.assertFalse(drafts["Good"].problem)

    def test_deleting_refuses_anything_that_is_not_a_draft_in_the_folder(self):
        outside = self.workspace / f"Elsewhere{DRAFT_SUFFIX}"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            delete_draft(self.folder, outside)
        self.assertTrue(outside.is_file())

        not_a_draft = self.folder / "photo.jpg"
        not_a_draft.write_bytes(b"\xff\xd8")
        with self.assertRaises(ValueError):
            delete_draft(self.folder, not_a_draft)
        self.assertTrue(not_a_draft.is_file())

        with self.assertRaises(ValueError):
            delete_draft(self.folder, self.folder)

        good = save_draft(self.folder, Manuscript(title="x"), "Gone")
        self.assertEqual(delete_draft(self.folder, good).name, good.name)
        self.assertFalse(good.exists())
        with self.assertRaises(FileNotFoundError):
            delete_draft(self.folder, good)

    def test_renaming_never_writes_over_another_draft(self):
        first = save_draft(self.folder, Manuscript(title="a"), "First")
        save_draft(self.folder, Manuscript(title="b"), "Second")
        with self.assertRaises(FileExistsError):
            rename_draft(self.folder, first, "Second")
        moved = rename_draft(self.folder, first, "Third")
        self.assertEqual(draft_name_of(moved), "Third")
        self.assertFalse(first.exists())

    def test_a_draft_file_is_readable_json(self):
        path = save_draft(self.folder, example_manuscript(), "Readable")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["title"], "The Folded Sheet")
        self.assertIn("design", data)


# --------------------------------------------------------------------------
# Typed text
# --------------------------------------------------------------------------


class TestTextParsing(unittest.TestCase):
    def styles(self, text):
        return [block.style for block in parse_blocks(text)]

    def test_a_blank_line_starts_a_paragraph_and_a_single_break_does_not(self):
        blocks = parse_blocks("one\ntwo\n\nthree")
        self.assertEqual([b.style for b in blocks], [PARAGRAPH, PARAGRAPH])
        self.assertEqual(blocks[0].text, "one two")
        self.assertEqual(blocks[1].text, "three")

    def test_runs_of_blank_lines_do_not_make_empty_paragraphs(self):
        self.assertEqual(self.styles("a\n\n\n\n\nb"), [PARAGRAPH, PARAGRAPH])
        self.assertEqual(self.styles("\n\n  \n\n"), [])

    def test_headings_need_a_space_so_a_hash_tag_stays_text(self):
        self.assertEqual(self.styles("# Big"), [SUBHEADING])
        self.assertEqual(self.styles("## Small"), [SUBSUBHEADING])
        self.assertEqual(self.styles("#nothashtag"), [PARAGRAPH])
        self.assertEqual(parse_blocks("# Big")[0].text, "Big")

    def test_a_line_of_marks_is_a_scene_break(self):
        for marker in ("***", "* * *", "---", "___", "~~~", "  * * * * "):
            self.assertEqual(self.styles(marker), [SCENE_BREAK], marker)
        # Two is not enough, and a rule with words on it is a paragraph.
        self.assertEqual(self.styles("**"), [PARAGRAPH])
        self.assertEqual(self.styles("--- and then"), [PARAGRAPH])

    def test_quoted_lines_keep_their_line_breaks(self):
        blocks = parse_blocks("> first\n> second\n\nafter")
        self.assertEqual([b.style for b in blocks], [QUOTE, PARAGRAPH])
        self.assertEqual(blocks[0].text, "first<br/>second")

    def test_a_quote_directly_after_prose_is_its_own_block(self):
        blocks = parse_blocks("prose\n> quoted\nprose again")
        self.assertEqual([b.style for b in blocks], [PARAGRAPH, QUOTE, PARAGRAPH])

    def test_markup_is_applied_and_the_text_is_escaped_first(self):
        self.assertEqual(inline_markup("plain"), "plain")
        self.assertEqual(inline_markup("*yes*"), "<i>yes</i>")
        self.assertEqual(inline_markup("_yes_"), "<i>yes</i>")
        self.assertEqual(inline_markup("**yes**"), "<b>yes</b>")
        self.assertEqual(inline_markup("**a** and *b*"), "<b>a</b> and <i>b</i>")
        # A user typing angle brackets or an ampersand must not be able to
        # inject markup into the paragraph parser, which would either change
        # the type or raise part way through a book.
        self.assertEqual(inline_markup("<b>hi</b> & co"),
                         "&lt;b&gt;hi&lt;/b&gt; &amp; co")
        self.assertEqual(escape("a<b>&c"), "a&lt;b&gt;&amp;c")

    def test_stray_stars_are_left_alone(self):
        for text in ("2 * 3 * 4", "a*b*c", "*", "**", "* "):
            self.assertNotIn("<i>", inline_markup(text), text)

    def test_a_paragraph_of_markup_survives_reportlab(self):
        # The real check that escaping is enough: this text goes through the
        # paragraph parser, which raises on markup it cannot read.
        book = Manuscript(
            title="Escapes",
            body=[Section(kind="chapter", heading="Angle < Bracket & Co",
                          text="<not a tag> & <<more>> *ok* **fine** 3 < 4")],
        )
        with tempfile.TemporaryDirectory() as folder:
            result = render_manuscript(book, Path(folder) / "e.pdf")
        self.assertGreaterEqual(result.source_pages, 1)


class TestPreparingText(unittest.TestCase):
    def test_line_endings_tabs_and_invisible_characters_are_normalised(self):
        text, missing = prepare_text("a\r\nb\rc\td​e﻿")
        self.assertEqual(text, "a\nb\nc   de")
        self.assertEqual(missing, set())

    def test_characters_the_font_cannot_set_are_reported_not_dropped(self):
        text, missing = prepare_text("Greek αβ here", FONTS["times"])
        self.assertIn("α", missing)
        self.assertIn("α", text)   # still there, so nothing goes missing

    def test_punctuation_a_font_lacks_is_replaced_with_something_honest(self):
        # Whichever of these a font happens to lack, nothing may be lost: every
        # character either survives or is reported.
        for font in FONTS.values():
            text, missing = prepare_text(
                "‘q’ “Q” — … –", font
            )
            self.assertTrue(text.strip(), font.key)
            for character in missing:
                self.assertIn(character, text, font.key)

    def test_a_font_with_the_glyph_keeps_the_proper_character(self):
        text, missing = prepare_text("don’t — now", FONTS["times"])
        self.assertEqual(text, "don’t — now")
        self.assertEqual(missing, set())

    def test_a_ligature_or_a_symbol_is_spelled_out_when_it_has_to_be(self):
        text, _missing = prepare_text("oｋ", FONTS["times"])  # fullwidth k
        self.assertEqual(text, "ok")


# --------------------------------------------------------------------------
# The typeset book
# --------------------------------------------------------------------------


class TestTypesetting(BookTestCase):
    def a_book(self, **design):
        book = Manuscript(
            title="The Test Book",
            author="A. Tester",
            year="2026",
            front=[Section(kind="preface", text=long_text("MARKPREFACE", 2))],
            body=[
                Section(kind="chapter", heading="Alpha",
                        text=long_text("MARKALPHA", 7)),
                Section(kind="chapter", heading="Beta",
                        text=long_text("MARKBETA", 5)),
                Section(kind="chapter", heading="Gamma",
                        text=long_text("MARKGAMMA", 3)),
            ],
            back=[Section(kind="colophon", text=long_text("MARKCOLOPHON", 1))],
        )
        for name, value in design.items():
            setattr(book.design, name, value)
        return book

    def test_the_pdf_page_is_exactly_two_book_pages_side_by_side(self):
        book = self.a_book()
        result, path = self.build(book)
        reader = pypdf.PdfReader(str(path))
        for page in reader.pages:
            self.assertAlmostEqual(
                float(page.mediabox.width),
                2 * book.design.page_width_in * PT_PER_INCH, places=3,
            )
            self.assertAlmostEqual(
                float(page.mediabox.height),
                book.design.page_height_in * PT_PER_INCH, places=3,
            )
        self.assertEqual(result.source_pages, len(reader.pages))
        self.assertEqual(result.book_pages, 2 * len(reader.pages))

    def test_the_title_page_is_the_first_book_page_and_carries_no_number(self):
        book = self.a_book()
        _result, path = self.build(book)
        pages = runs_by_book_page(path)
        self.assertEqual(page_holding(pages, "The Test Book"), 1)
        self.assertIsNone(folio_on(pages, 1, book.design))
        self.assertIsNone(running_head_on(pages, 1, book.design))
        # And the copyright page, right behind it, is just as bare.
        self.assertEqual(page_holding(pages, "Copyright"), 2)
        self.assertIsNone(folio_on(pages, 2, book.design))

    def test_no_copyright_page_is_printed_until_one_is_asked_for(self):
        """A title and an author must not cost a sheet of paper on their own."""
        book = Manuscript(
            title="The Test Book", author="A. Tester",
            body=[Section(kind="chapter", heading="Alpha", text="Words.")],
        )
        _result, path = self.build(book, "bare.pdf")
        pages = runs_by_book_page(path)
        self.assertEqual(page_holding(pages, "The Test Book"), 1)
        self.assertIsNone(page_holding(pages, "Copyright"))

        # And a year — a publication detail — brings the page back.
        book.year = "2026"
        _result, path = self.build(book, "dated.pdf")
        pages = runs_by_book_page(path)
        self.assertEqual(page_holding(pages, "Copyright"), 2)

    def test_the_page_size_can_be_set_for_one_build_without_touching_the_draft(self):
        """How the writing view prints on any paper without scaling anything.

        The book is *set* at half the sheet rather than set at some other size
        and resized on to it, so the choice belongs to the build and must not
        reach the manuscript the writer has open.
        """
        book = self.a_book()
        _result, path = self.build(book, "letter.pdf", page_size_in=(5.5, 8.5))
        page = pypdf.PdfReader(str(path)).pages[0]
        self.assertAlmostEqual(float(page.mediabox.width), 11.0 * PT_PER_INCH,
                               places=3)
        self.assertAlmostEqual(float(page.mediabox.height), 8.5 * PT_PER_INCH,
                               places=3)
        # The draft still says A5, as it was typed.
        self.assertEqual(book.design.page_size_name, "A5")
        self.assertAlmostEqual(book.design.page_width_in, 148 / 25.4, places=6)

    def test_the_contents_prints_the_page_each_chapter_really_starts_on(self):
        """The strongest check here, and the one worth the machinery.

        The number beside a chapter in the contents is compared against the
        page the chapter's first paragraph actually landed on — found by
        hunting for a marker word, not by asking the typesetter anything.
        """
        book = self.a_book()
        _result, path = self.build(book)
        pages = runs_by_book_page(path)

        contents_page = page_holding(pages, "Contents")
        self.assertIsNotNone(contents_page)
        entries = pages[contents_page]

        for title, marker in (("Alpha", "MARKALPHA"), ("Beta", "MARKBETA"),
                              ("Gamma", "MARKGAMMA"), ("Preface", "MARKPREFACE")):
            really_on = page_holding(pages, marker)
            self.assertIsNotNone(really_on, title)
            # The entry and its page number share a baseline, and the number is
            # drawn on the end of the run of leader dots rather than on its own.
            baselines = [y for label, _x, y in entries if title in label]
            self.assertTrue(baselines, f"{title} missing from the contents")
            printed = [
                int(found.group(1))
                for label, _x, y in entries
                if abs(y - baselines[0]) < 1.0
                and (found := re.search(r"(\d+)\s*$", label))
            ]
            self.assertEqual(len(printed), 1,
                             f"expected one page number beside {title}")
            self.assertEqual(printed[0], really_on, f"contents entry for {title}")

    def test_page_numbers_run_in_order_and_match_the_page_they_are_on(self):
        book = self.a_book()
        _result, path = self.build(book)
        pages = runs_by_book_page(path)
        found = 0
        for book_page in sorted(pages):
            folio = folio_on(pages, book_page, book.design)
            if folio is not None:
                self.assertEqual(int(folio), book_page)
                found += 1
        self.assertGreater(found, 4)

    def test_page_numbers_are_centred_at_the_foot_of_the_book_page(self):
        """The same place a converted PDF gets them; see `draw_folio`.

        Written out by hand: the middle of the book page, and a baseline two
        fifths of the way up the bottom margin. The folio is set 1.5 pt smaller
        than the body type, which is only needed here to know how wide the
        digits are — `x` is where they begin, not where they are centred.
        """
        book = self.a_book()
        _result, path = self.build(book)
        pages = runs_by_book_page(path)

        centre = book.design.page_width_in * PT_PER_INCH / 2
        baseline = 0.40 * book.design.margin_bottom_in * PT_PER_INCH
        checked = 0
        for book_page, runs in runs_by_book_page(path).items():
            folio = folio_on(pages, book_page, book.design)
            if folio is None:
                continue
            x, y = next((x, y) for label, x, y in runs
                        if label.strip() == folio and y < baseline + 1.0)
            width = pdfmetrics.stringWidth(
                folio, "Times-Roman", book.design.font_size_pt - 1.5
            )
            self.assertAlmostEqual(x + width / 2, centre, delta=1.0,
                                   msg=f"page {book_page}")
            self.assertAlmostEqual(y, baseline, delta=0.5, msg=f"page {book_page}")
            checked += 1
        self.assertGreater(checked, 4)

    def test_running_heads_name_the_book_and_the_section(self):
        book = self.a_book()
        _result, path = self.build(book)
        pages = runs_by_book_page(path)

        opening = page_holding(pages, "MARKALPHA")
        # A section's own opening page carries no running head.
        self.assertIsNone(running_head_on(pages, opening, book.design))

        heads = {
            book_page: running_head_on(pages, book_page, book.design)
            for book_page in sorted(pages) if book_page > opening
        }
        heads = {page: text for page, text in heads.items() if text}
        self.assertTrue(heads, "no running heads anywhere")
        for page, text in heads.items():
            if page % 2 == 0:                     # a verso carries the title
                self.assertEqual(text, "The Test Book")
            else:                                 # a recto carries the section
                self.assertIn(text, ("Alpha", "Beta", "Gamma", "Preface",
                                     "Colophon"))

    def test_turning_the_furniture_off_takes_it_off_the_page(self):
        book = self.a_book(page_numbers=False, running_heads=False,
                           contents=False)
        _result, path = self.build(book)
        pages = runs_by_book_page(path)
        self.assertIsNone(page_holding(pages, "Contents"))
        for book_page in pages:
            self.assertIsNone(folio_on(pages, book_page, book.design))
            self.assertIsNone(running_head_on(pages, book_page, book.design))

    def test_starting_on_a_right_hand_page_puts_every_section_on_an_odd_page(self):
        book = self.a_book(chapter_start=CHAPTER_START_RECTO)
        _result, path = self.build(book)
        pages = runs_by_book_page(path)
        for marker in ("MARKPREFACE", "MARKALPHA", "MARKBETA", "MARKGAMMA",
                       "MARKCOLOPHON"):
            page = page_holding(pages, marker)
            self.assertIsNotNone(page, marker)
            self.assertEqual(page % 2, 1, f"{marker} landed on page {page}")

    def test_starting_on_the_next_page_uses_both_sides_of_the_paper(self):
        book = self.a_book(chapter_start=CHAPTER_START_NEW_PAGE)
        recto = self.a_book(chapter_start=CHAPTER_START_RECTO)
        tight, _path = self.build(book, "tight.pdf")
        loose, _path = self.build(recto, "loose.pdf")
        self.assertLessEqual(tight.book_pages, loose.book_pages)
        # And at least one section is on an even page, or nothing was saved.
        pages = runs_by_book_page(self.workspace / "tight.pdf")
        starts = [page_holding(pages, marker) for marker in
                  ("MARKALPHA", "MARKBETA", "MARKGAMMA")]
        self.assertTrue(any(page % 2 == 0 for page in starts), starts)

    def test_a_book_page_is_never_wider_than_its_half_of_the_sheet(self):
        book = self.a_book()
        _result, path = self.build(book)
        page_width_pt = book.design.page_width_in * PT_PER_INCH
        outer = book.design.margin_outer_in * PT_PER_INCH
        for book_page, runs in runs_by_book_page(path).items():
            for label, x, _y in runs:
                self.assertGreaterEqual(x + 1e-6, 0.0, label)
                self.assertLessEqual(x - 1e-6, page_width_pt, label)
            # Nothing wanders into the fold-side margin of a recto.
            if book_page % 2 == 1:
                self.assertTrue(all(x + 1.0 >= outer for _l, x, _y in runs))

    def test_every_typeface_sets_a_book(self):
        for key, font in FONTS.items():
            book = Manuscript(
                title="Faces",
                body=[Section(kind="chapter", heading="Bold and italic",
                              text="Some **bold**, some *italic*, some plain.")],
            )
            book.design.font_key = key
            with self.subTest(font=key):
                result, path = self.build(book, f"font-{key}.pdf")
                self.assertGreaterEqual(result.source_pages, 1)
                self.assertTrue(path.read_bytes().startswith(b"%PDF"))

    def test_an_unknown_typeface_falls_back_instead_of_raising(self):
        book = Manuscript(title="X", body=[Section(kind="chapter", text="y")])
        book.design.font_key = "no-such-font"
        result, _path = self.build(book, "fallback.pdf")
        self.assertGreaterEqual(result.source_pages, 1)

    def test_a_book_with_nothing_in_it_is_the_only_thing_refused(self):
        for empty in (Manuscript(), blank_manuscript(),
                      Manuscript(body=[Section(kind="chapter", heading="  ",
                                               text="  ")])):
            with self.assertRaises(EmptyBookError):
                self.build(empty, "nothing.pdf")
        self.assertFalse((self.workspace / "nothing.pdf").exists())

    def test_a_title_and_nothing_else_still_makes_a_book(self):
        result, path = self.build(Manuscript(title="Just This"), "one.pdf")
        self.assertEqual(result.source_pages, 1)
        self.assertEqual(page_holding(runs_by_book_page(path), "Just This"), 1)

    def test_impossible_margins_are_survived_rather_than_refused(self):
        book = Manuscript(title="Tiny", body=[Section(kind="chapter", text="x " * 50)])
        book.design.page_width_in = 2.0
        book.design.page_height_in = 3.0
        book.design.margin_inner_in = 4.0
        book.design.margin_outer_in = 4.0
        book.design.margin_top_in = 4.0
        book.design.margin_bottom_in = 4.0
        result, _path = self.build(book, "tiny.pdf")
        self.assertGreaterEqual(result.source_pages, 1)

    def test_the_pdf_says_which_program_wrote_it(self):
        _result, path = self.build(Manuscript(title="Stamped"), "stamped.pdf")
        self.assertTrue(is_generated_book(path))
        metadata = pypdf.PdfReader(str(path)).metadata
        self.assertEqual(metadata.get("/Title"), "Stamped")

        stranger = self.workspace / "stranger.pdf"
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with open(stranger, "wb") as stream:
            writer.write(stream)
        self.assertFalse(is_generated_book(stranger))
        self.assertFalse(is_generated_book(self.workspace / "not-there.pdf"))

    def test_the_length_estimate_tracks_the_book(self):
        small = Manuscript(title="S", body=[Section(kind="chapter", text="a b c")])
        big = Manuscript(
            title="B",
            body=[Section(kind="chapter", text=long_text("X", 20))],
        )
        self.assertGreater(estimate_book_pages(big), estimate_book_pages(small))
        self.assertGreaterEqual(estimate_book_pages(small), 1)
        self.assertEqual(estimate_book_pages(Manuscript()), 0)


# --------------------------------------------------------------------------
# All the way through to signatures
# --------------------------------------------------------------------------


class TestTypesetThenImpose(BookTestCase):
    """A typed book has to be an ordinary input PDF, or the point is lost."""

    def setUp(self):
        super().setUp()
        for name, folder in (("INPUT_DIR", self.workspace / "Input"),
                             ("OUTPUT_DIR", self.workspace / "Output"),
                             ("PREVIOUS_DIR", self.workspace / "Prev")):
            folder.mkdir(parents=True, exist_ok=True)
            original = getattr(main, name)
            setattr(main, name, folder)
            self.addCleanup(setattr, main, name, original)

    def test_a_typed_book_imposes_into_signatures_like_any_other_pdf(self):
        book = Manuscript(
            title="Bound At Home",
            author="A. Tester",
            body=[Section(kind="chapter", heading=f"Chapter {n}",
                          text=long_text(f"MARK{n}", 4)) for n in range(1, 5)],
        )
        result = main.typeset_book(book, "Bound At Home")
        self.assertEqual(result.path, main.INPUT_DIR / "Bound At Home.pdf")
        self.assertIn(result.path, main.list_available_books())

        written = main.convert_book(result.path, sheets_per_signature=2,
                                    move_input=True)
        expected = plan_signatures(result.book_pages, 2)
        self.assertEqual(len(written), len(expected))
        self.assertTrue((main.PREVIOUS_DIR / "Bound At Home.pdf").is_file())

        ready = main.list_ready_books()
        self.assertEqual([book.name for book in ready], ["Bound At Home"])

        # Every chapter opening survived the imposition, exactly once.
        seen = []
        for path in written:
            for page in pypdf.PdfReader(str(path)).pages:
                seen += [label for label, _point, visible in read_text_runs(page)
                         if visible and label.startswith("MARK")]
        for n in range(1, 5):
            self.assertEqual(sum(label.startswith(f"MARK{n}") for label in seen), 1)

    def test_the_editor_replaces_its_own_pdf_and_nothing_else(self):
        book = Manuscript(title="Mine", body=[Section(kind="chapter", text="a b c")])
        first = main.typeset_book(book, "Mine")
        before = first.path.read_bytes()
        book.body[0].text = long_text("LATER", 14)
        second = main.typeset_book(book, "Mine")
        self.assertEqual(first.path, second.path)
        self.assertGreater(second.source_pages, first.source_pages)
        self.assertNotEqual(second.path.read_bytes(), before)

        stranger = main.INPUT_DIR / "Theirs.pdf"
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with open(stranger, "wb") as stream:
            writer.write(stream)
        before = stranger.read_bytes()
        with self.assertRaises(FileExistsError):
            main.typeset_book(book, "Theirs")
        self.assertEqual(stranger.read_bytes(), before)

    def test_a_build_name_can_only_ever_land_in_the_input_folder(self):
        for hostile in ("../escape", r"..\escape", "sub/dir/book", "a:b|c?d",
                        "  ", "...", "book.pdf"):
            path = main.book_pdf_path(hostile)
            self.assertEqual(path.parent.resolve(), main.INPUT_DIR.resolve(),
                             hostile)
            self.assertEqual(path.suffix, ".pdf", hostile)

    def test_a_book_typed_in_is_split_at_the_middle_of_its_own_page(self):
        """The fold has to fall between the two columns the typesetter made."""
        book = Manuscript(
            title="Halves",
            body=[Section(kind="chapter", heading="Only", text=long_text("MARK", 6))],
        )
        book.design.contents = False
        result = main.typeset_book(book, "Halves")
        folder = self.workspace / "sigs"
        written = split_to_signatures(result.path, 2, folder)
        self.assertTrue(written)
        # Nothing from the right-hand column of a source page may appear on the
        # left-hand book page of a sheet, and vice versa: if the split were off,
        # a line would be cut in two and show on both.
        for path in written:
            for page in pypdf.PdfReader(str(path)).pages:
                half = float(page.mediabox.width) / 2
                for label, (x, _y), visible in read_text_runs(page):
                    if not visible or not label.strip():
                        continue
                    self.assertNotAlmostEqual(x, half, delta=0.5, msg=label)


if __name__ == "__main__":
    unittest.main(verbosity=2)
