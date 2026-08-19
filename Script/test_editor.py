"""Tests for the “Convert Inputted Text into PDF Signatures” view.

Run with:  python -m unittest Script.test_editor -v

The other two test modules check code that can be called directly. This one
cannot: every bug the editor has ever had lived in the *interaction* — which
widget wrote into the manuscript, and when, relative to the button the user had
just clicked. So these tests drive the real `app.py` through Streamlit's own
`AppTest`, click the real buttons and then read the draft file off the disk.

The rule that shapes them: **a click and the words typed just before it arrive
in the same message.** A browser sends the text box's new value together with
the click that took the focus away from it, so any test that sets a value, runs,
and only then clicks is testing the easy case. The interesting case — and the
one that lost a book's title page — is `set_value(...)` and `click()` with a
single `run()` after both, which is what most of these do.
"""

import io
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pypdf  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

import main  # noqa: E402

from Script import workspace  # noqa: E402
from Script.book_editor import (  # noqa: E402
    MAX_BOX_HEIGHT_PX,
    MIN_BOX_HEIGHT_PX,
    box_height,
)
from Script.manuscript import (  # noqa: E402
    DRAFT_SUFFIX,
    Manuscript,
    Section,
    list_drafts,
    load_draft,
    save_draft,
)

APP = Path(__file__).resolve().parent.parent / "app.py"

WRITE_VIEW = "write"
CONVERT_VIEW = "convert"

# Every box on the title page, and something to type into it.
TITLE_PAGE = {
    "bk-title": "Demon Noble Girl",
    "bk-subtitle": "A Reincarnation Story",
    "bk-author": "A. Writer",
    "bk-series": "Book Two of the Marches",
    "bk-publisher": "Kitchen Table Press",
    "bk-year": "2026",
    "bk-edition": "First edition",
    "bk-isbn": "978-0-00-000000-0",
    "bk-copyright": "All wrongs reversed",
}
TITLE_PAGE_ATTRIBUTES = {
    "bk-title": "title", "bk-subtitle": "subtitle", "bk-author": "author",
    "bk-series": "series", "bk-publisher": "publisher", "bk-year": "year",
    "bk-edition": "edition", "bk-isbn": "isbn",
    "bk-copyright": "copyright_notice",
}


class EditorTestCase(unittest.TestCase):
    """One app, one temporary set of folders, opened on the writing view."""

    def setUp(self):
        # The app makes its own folder per session and points `main` at it, so
        # the test cannot hand it one. What it can do is say where sessions
        # live, which keeps every file this test writes inside a temp folder it
        # owns — and then read back off `main` where the app decided to put
        # them, exactly as every panel in the app does.
        self.workspace = Path(tempfile.mkdtemp(prefix="editor-test-"))
        self.addCleanup(shutil.rmtree, self.workspace, ignore_errors=True)
        self.addCleanup(setattr, workspace, "SESSIONS_ROOT",
                        workspace.SESSIONS_ROOT)
        workspace.SESSIONS_ROOT = self.workspace
        for name in ("INPUT_DIR", "OUTPUT_DIR", "PREVIOUS_DIR", "MANUSCRIPT_DIR"):
            self.addCleanup(setattr, main, name, getattr(main, name))

        self.at = AppTest.from_file(str(APP), default_timeout=180)
        self.at.run()
        self.at.radio(key="view").set_value(WRITE_VIEW).run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.drafts = main.MANUSCRIPT_DIR

    # ---- reading the app -------------------------------------------------
    @property
    def book(self):
        return self.at.session_state["book_manuscript"]

    def state(self, key, default=None):
        try:
            return self.at.session_state[key]
        except KeyError:
            return default

    def box(self, key):
        """The value showing in one text box, by widget key."""
        for element in list(self.at.text_input) + list(self.at.text_area):
            if element.key == key:
                return element.value
        return None

    def widget_named(self, key):
        """One widget on the page, by key, whatever kind it turns out to be."""
        for kind in (self.at.text_input, self.at.text_area, self.at.checkbox,
                     self.at.number_input, self.at.selectbox, self.at.radio,
                     self.at.slider):
            for element in kind:
                if element.key == key:
                    return element
        return None

    def told_to_take_a_new_value(self, key):
        """Is the browser being told to replace what is in this box?

        `set_value` on the message that draws a widget is the only thing that
        changes a box already on screen. A keyed widget keeps its identity
        across a rerun whatever `value=` the script hands it, so without this
        flag the server and the page disagree: the manuscript, and the PDF,
        carry the loaded book while the box still shows the one before it.
        """
        element = self.widget_named(key)
        self.assertIsNotNone(element, f"{key} is not on the page")
        return bool(element.proto.set_value)

    def draft_file(self, name):
        return self.drafts / f"{name}{DRAFT_SUFFIX}"

    def type_title_page(self):
        for key, value in TITLE_PAGE.items():
            self.at.text_input(key=key).set_value(value)

    def assert_title_page(self, book, where):
        for key, attribute in TITLE_PAGE_ATTRIBUTES.items():
            self.assertEqual(getattr(book, attribute), TITLE_PAGE[key],
                             f"{attribute} in {where}")

    def first_section_key(self, prefix="bk-text-"):
        return f"{prefix}{self.book.body[0].id}"

    def sidebar_widget(self, key):
        """One widget in the sidebar, by key. None if it is not in there."""
        for kind in (self.at.sidebar.radio, self.at.sidebar.selectbox,
                     self.at.sidebar.checkbox, self.at.sidebar.number_input,
                     self.at.sidebar.text_input, self.at.sidebar.slider):
            for element in kind:
                if element.key == key:
                    return element
        return None

    def choose_sheet(self, name, landscape=True):
        """Give the book's size as the paper it prints on, in 📐 Book design.

        Two runs, because the sheet menu is not on the page until the radio
        above it says the size is coming from that end — which is the whole
        point of it: only one of the two menus is ever on screen.
        """
        self.at.radio(key="bkpref-size-from").set_value("sheet").run()
        self.at.selectbox(key="bkpref-sheet").set_value(name)
        self.at.radio(key="bkpref-sheet-landscape").set_value(landscape)
        self.at.run()
        self.assertFalse(self.at.exception, self.at.exception)


# --------------------------------------------------------------------------
# Typing, and the click that arrives with it
# --------------------------------------------------------------------------


class TestNothingTypedIsLost(EditorTestCase):
    """The bug this file was written for, and its neighbours.

    A button in the draft panel is drawn near the top of the page and does its
    work there, long before the boxes below have handed their words to the
    manuscript — and it then reruns, which throws away the state of every widget
    the interrupted run never drew. Both halves have to hold for the words to
    survive.
    """

    def test_the_title_page_reaches_the_file_when_save_is_the_next_click(self):
        self.at.checkbox(key="bkpref-autosave").set_value(False).run()

        self.type_title_page()
        self.at.text_area(key=self.first_section_key()).set_value("Once upon a time.")
        self.at.button(key="bk-save").click().run()
        self.assertFalse(self.at.exception, self.at.exception)

        saved = [path.name for path in self.drafts.glob(f"*{DRAFT_SUFFIX}")]
        self.assertEqual(saved, [f"Demon Noble Girl{DRAFT_SUFFIX}"])

        book = load_draft(self.draft_file("Demon Noble Girl"))
        self.assert_title_page(book, "the saved draft")
        self.assertEqual(book.body[0].text, "Once upon a time.")
        # And the copy on screen agrees with the file.
        self.assert_title_page(self.book, "the editor")

    def test_autosave_writes_the_words_that_came_with_the_click(self):
        self.at.text_input(key="bk-title").set_value("Kept").run()
        self.at.button(key="bk-save").click().run()
        self.assertTrue(self.draft_file("Kept").is_file())

        # Autosave is on by default; a later edit lands in the same file.
        self.at.text_input(key="bk-author").set_value("A. Writer")
        self.at.text_area(key=self.first_section_key()).set_value("Body words.")
        self.at.run()
        book = load_draft(self.draft_file("Kept"))
        self.assertEqual(book.author, "A. Writer")
        self.assertEqual(book.body[0].text, "Body words.")

    def test_adding_a_section_does_not_swallow_the_title_typed_with_it(self):
        self.at.text_input(key="bk-title").set_value("Still Here")
        self.at.text_input(key="bk-author").set_value("A. Writer")
        self.at.button(key="bk-add-body").click().run()
        self.assertFalse(self.at.exception, self.at.exception)

        self.assertEqual(self.book.title, "Still Here")
        self.assertEqual(self.book.author, "A. Writer")
        self.assertEqual(len(self.book.body), 2)
        self.assertEqual(self.box("bk-title"), "Still Here")

    def test_a_design_measurement_survives_the_click_that_arrives_with_it(self):
        self.at.number_input(key="bk-len-m-in-in").set_value(0.9)
        self.at.number_input(key="bk-font-size").set_value(12.0)
        self.at.text_input(key="bk-title").set_value("Measured")
        self.at.button(key="bk-save").click().run()
        self.assertFalse(self.at.exception, self.at.exception)

        book = load_draft(self.draft_file("Measured"))
        self.assertAlmostEqual(book.design.margin_inner_in, 0.9, places=6)
        self.assertAlmostEqual(book.design.font_size_pt, 12.0, places=6)

    def test_a_chapter_typed_and_removed_in_one_click_comes_back_whole(self):
        section_id = self.book.body[0].id
        self.at.text_area(key=f"bk-text-{section_id}").set_value("Words worth keeping.")
        self.at.button(key=f"bk-del-{section_id}").click().run()
        self.assertEqual(self.book.body, [])

        self.at.button(key="bk-undo-body").click().run()
        self.assertEqual(len(self.book.body), 1)
        self.assertEqual(self.book.body[0].text, "Words worth keeping.")


# --------------------------------------------------------------------------
# Opening a draft
# --------------------------------------------------------------------------


class TestOpeningADraft(EditorTestCase):
    def saved_book(self, name="Demon Noble Girl"):
        book = Manuscript(
            title="Demon Noble Girl", subtitle="A Reincarnation Story",
            author="A. Writer", series="Book Two of the Marches",
            publisher="Kitchen Table Press", year="2026",
            edition="First edition", isbn="978-0-00-000000-0",
            copyright_notice="All wrongs reversed", rights="Moral rights asserted.",
            front=[Section(kind="front_custom", heading="Description:",
                           text="She had a dream.")],
            body=[Section(kind="chapter", heading="Chapter 1: I Became a Cat",
                          text="The first chapter."),
                  Section(kind="chapter", heading="Chapter 2: I became a Demon",
                          text="The second chapter.")],
        )
        book.design.font_size_pt = 11.5
        book.design.chapter_label = "chapter_word"
        book.design.page_numbers = False
        save_draft(self.drafts, book, name)
        return book

    def load_draft_named(self, name):
        self.at.button(key=f"bk-act-load-{name}").click().run()
        self.assertFalse(self.at.exception, self.at.exception)

    def test_every_box_including_the_title_page_is_filled_again(self):
        original = self.saved_book()
        self.at.run()
        self.load_draft_named("Demon Noble Girl")

        for attribute in ("title", "subtitle", "author", "series", "publisher",
                          "year", "edition", "isbn", "copyright_notice", "rights"):
            self.assertEqual(getattr(self.book, attribute),
                             getattr(original, attribute), attribute)
        # Not just the model: the boxes on screen show it too.
        self.assertEqual(self.box("bk-title"), "Demon Noble Girl")
        self.assertEqual(self.box("bk-author"), "A. Writer")
        self.assertEqual(self.box("bk-rights"), "Moral rights asserted.")

        self.assertEqual([s.heading for s in self.book.front], ["Description:"])
        self.assertEqual([s.heading for s in self.book.body],
                         ["Chapter 1: I Became a Cat", "Chapter 2: I became a Demon"])
        self.assertEqual(self.box(f"bk-text-{self.book.body[0].id}"),
                         "The first chapter.")

        self.assertAlmostEqual(self.book.design.font_size_pt, 11.5, places=6)
        self.assertEqual(self.book.design.chapter_label, "chapter_word")
        self.assertIs(self.book.design.page_numbers, False)

        # Loaded, not merely copied: it is backed by its file and clean.
        self.assertEqual(self.state("book_draft_path"),
                         str(self.draft_file("Demon Noble Girl")))
        self.assertIn("Saved as", "".join(c.value for c in self.at.caption))

    def test_every_box_is_told_to_take_the_loaded_words(self):
        """The half of loading that a value read back from the server hides.

        `self.box(...)` above asks the *server* what a box holds, and that was
        right all along: the manuscript was loaded, and a book built straight
        afterwards printed the loaded title perfectly. What the browser had on
        screen was the book before it, because a keyed widget survives a rerun
        as the same widget and only takes a new value when it is told to.

        The title page was where it showed. A chapter's boxes are keyed on its
        section id, so a different draft's chapters are different boxes and are
        drawn from scratch; the title, the imprint and every design control are
        one fixed key each, and stayed exactly as they were.
        """
        self.saved_book()
        self.at.run()
        self.load_draft_named("Demon Noble Girl")

        for key in ("bk-title", "bk-subtitle", "bk-author", "bk-series",
                    "bk-publisher", "bk-year", "bk-edition", "bk-isbn",
                    "bk-copyright", "bk-rights"):
            self.assertTrue(self.told_to_take_a_new_value(key), key)
        # The design panel is the same fixed keys and the same failure.
        for key in ("bk-font", "bk-font-size", "bk-leading", "bk-justify",
                    "bk-folios", "bk-heads", "bk-contents", "bk-page-size",
                    "bk-chapter-start", "bk-chapter-label", "bk-scene-break",
                    "bk-len-m-in-in", "bk-len-m-out-in", "bk-len-indent-in"):
            self.assertTrue(self.told_to_take_a_new_value(key), key)

        # And what they were told to take is the draft's, not the default's.
        self.assertAlmostEqual(self.widget_named("bk-font-size").value, 11.5,
                               places=6)
        self.assertEqual(self.widget_named("bk-chapter-label").value,
                         "chapter_word")
        self.assertIs(self.widget_named("bk-folios").value, False)

    def test_a_draft_outside_a_control_s_own_range_still_opens(self):
        """A draft is a plain JSON file, so its numbers cannot be trusted.

        Line spacing is loaded up to 2.5 and the slider only goes to 2.0, so a
        hand-edited draft could name a value the control refuses to be drawn
        at, and took the whole editor down with it rather than the draft.
        """
        book = Manuscript(title="Loose", body=[Section(kind="chapter",
                                                       text="Words.")])
        book.design.line_spacing = 2.4
        save_draft(self.drafts, book, "Loose")
        self.at.run()

        self.load_draft_named("Loose")
        self.assertFalse(self.at.exception, self.at.exception)
        self.assertEqual(self.book.title, "Loose")
        self.assertAlmostEqual(self.widget_named("bk-leading").value, 2.0,
                               places=6)

    def test_a_box_is_left_alone_on_every_other_run(self):
        """Only the run that replaces the book may reach into a box.

        Forcing a value on every run would put the cursor back to where the
        model thinks it is on every keystroke, which is worse than the bug.
        """
        self.saved_book()
        self.at.run()
        self.load_draft_named("Demon Noble Girl")
        self.assertTrue(self.told_to_take_a_new_value("bk-title"))

        self.at.run()
        for key in ("bk-title", "bk-author", "bk-font-size", "bk-page-size"):
            self.assertFalse(self.told_to_take_a_new_value(key), key)

        self.at.text_input(key="bk-title").set_value("Typed over").run()
        self.assertEqual(self.book.title, "Typed over")
        self.assertFalse(self.told_to_take_a_new_value("bk-title"))

    def test_the_button_that_puts_a_draft_in_the_editor_says_load(self):
        self.saved_book()
        self.at.run()

        button = self.at.button(key="bk-act-load-Demon Noble Girl")
        self.assertEqual(button.label, "Load")

        # Once it is the draft on screen, the same button offers it again and
        # says what a second click would cost.
        self.load_draft_named("Demon Noble Girl")
        self.assertEqual(self.at.button(key="bk-act-load-Demon Noble Girl").label,
                         "Reload (discard changes)")

    def test_a_second_draft_replaces_the_first_completely(self):
        self.saved_book("First")
        save_draft(self.drafts, Manuscript(title="Second", author="B. Writer",
                                           body=[Section(kind="chapter",
                                                         text="Other words.")]),
                   "Second")
        self.at.run()

        self.load_draft_named("First")
        self.assertEqual(self.book.title, "Demon Noble Girl")
        self.load_draft_named("Second")

        self.assertEqual(self.book.title, "Second")
        self.assertEqual(self.book.author, "B. Writer")
        # Nothing of the first draft is left in a box that the second leaves empty.
        self.assertEqual(self.book.subtitle, "")
        self.assertEqual(self.book.publisher, "")
        self.assertEqual(self.book.front, [])
        self.assertEqual(self.box("bk-title"), "Second")
        self.assertEqual(self.box("bk-subtitle"), "")
        self.assertEqual(self.box("bk-publisher"), "")

    def test_words_typed_in_the_same_click_as_open_are_not_thrown_away_silently(self):
        self.saved_book("First")
        save_draft(self.drafts, Manuscript(title="Second"), "Second")
        self.at.run()
        self.load_draft_named("First")

        # An edit and a click on another draft, together. The edit counts as an
        # unsaved change, so the app has to ask before losing it.
        self.at.checkbox(key="bkpref-autosave").set_value(False).run()
        self.at.text_input(key="bk-subtitle").set_value("Typed but not saved")
        self.at.button(key="bk-act-load-Second").click().run()

        self.assertTrue(any("not saved" in warning.value
                            for warning in self.at.warning), self.at.warning)
        self.assertEqual(self.book.title, "Demon Noble Girl")
        self.assertEqual(self.book.subtitle, "Typed but not saved")

        self.at.button(key="bk-armed-go").click().run()
        self.assertEqual(self.book.title, "Second")

    def test_a_new_book_empties_every_box(self):
        self.saved_book()
        self.at.run()
        self.load_draft_named("Demon Noble Girl")

        self.at.button(key="bk-act-new").click().run()
        self.assertEqual(self.book.title, "")
        self.assertEqual(self.book.author, "")
        self.assertEqual(self.box("bk-title"), "")
        self.assertEqual(self.box("bk-author"), "")
        self.assertEqual(len(self.book.body), 1)
        self.assertTrue(self.book.body[0].is_empty)
        self.assertEqual(self.state("book_draft_path"), "")
        # The draft it came from is untouched.
        self.assertEqual(load_draft(self.draft_file("Demon Noble Girl")).title,
                         "Demon Noble Girl")

    def test_the_example_book_opens_as_an_unsaved_draft(self):
        self.at.button(key="bk-act-example").click().run()
        self.assertEqual(self.book.title, "The Folded Sheet")
        self.assertEqual(self.box("bk-title"), "The Folded Sheet")
        self.assertEqual(self.state("book_draft_path"), "")
        self.assertEqual(list(self.drafts.glob(f"*{DRAFT_SUFFIX}")), [])

    def test_a_draft_deleted_while_open_leaves_the_words_but_not_the_file(self):
        self.saved_book()
        self.at.run()
        self.load_draft_named("Demon Noble Girl")

        self.at.button(key="arm-delete-draft-Demon Noble Girl").click().run()
        self.at.button(key="yes-delete-draft-Demon Noble Girl").click().run()
        self.assertFalse(self.draft_file("Demon Noble Girl").exists())
        self.assertEqual(self.book.title, "Demon Noble Girl")
        self.assertEqual(self.state("book_draft_path"), "")
        # Autosave must not put it straight back.
        self.at.run()
        self.assertFalse(self.draft_file("Demon Noble Girl").exists())


# --------------------------------------------------------------------------
# The round trip, and the two name boxes
# --------------------------------------------------------------------------


class TestSavingAndNaming(EditorTestCase):
    def test_a_book_typed_in_comes_back_identical_after_a_new_book(self):
        self.type_title_page()
        self.at.text_area(key="bk-rights").set_value("Moral rights asserted.")
        section_id = self.book.body[0].id
        self.at.text_input(key=f"bk-head-{section_id}").set_value("Chapter One")
        self.at.text_area(key=f"bk-text-{section_id}").set_value("The first chapter.")
        self.at.button(key="bk-save").click().run()

        before = self.book.to_dict()
        self.at.button(key="bk-act-new").click().run()
        self.assertEqual(self.book.title, "")

        self.at.button(key="bk-act-load-Demon Noble Girl").click().run()
        self.assertEqual(self.book.to_dict(), before)

    def test_the_draft_name_follows_the_title_until_it_is_typed_in(self):
        self.at.text_input(key="bk-title").set_value("Followed").run()
        self.assertEqual(self.box("bk-draft-name"), "Followed")

        self.at.text_input(key="bk-draft-name").set_value("My own name").run()
        self.at.text_input(key="bk-title").set_value("Changed").run()
        self.assertEqual(self.box("bk-draft-name"), "My own name")

        self.at.button(key="bk-save").click().run()
        self.assertTrue(self.draft_file("My own name").is_file())
        self.assertEqual(load_draft(self.draft_file("My own name")).title, "Changed")

    def test_a_hand_picked_build_name_is_not_reset_by_saving(self):
        self.at.text_input(key="bk-title").set_value("The Title").run()
        self.at.text_input(key="bk-file-name").set_value("Print version 2").run()
        self.assertEqual(self.box("bk-file-name"), "Print version 2")

        # Saving reruns from the top of the page, which never redraws this box.
        self.at.button(key="bk-save").click().run()
        self.assertEqual(self.box("bk-file-name"), "Print version 2")

        # Emptying it puts it back to following the title.
        self.at.text_input(key="bk-file-name").set_value("").run()
        self.assertEqual(self.box("bk-file-name"), "The Title")

    def test_save_a_copy_leaves_the_draft_it_came_from_alone(self):
        self.at.text_input(key="bk-title").set_value("Twice").run()
        self.at.button(key="bk-save").click().run()
        self.at.text_input(key="bk-author").set_value("A. Writer")
        self.at.button(key="bk-save-as").click().run()

        self.assertEqual(sorted(d.name for d in list_drafts(self.drafts)),
                         ["Twice", "Twice 2"])
        self.assertEqual(load_draft(self.draft_file("Twice 2")).author, "A. Writer")


class TestTheDraftsPanel(EditorTestCase):
    def test_a_draft_leaves_by_download_because_nothing_here_is_kept(self):
        """No button onto a folder: there is no folder anyone can be shown.

        The app holds a draft only while the tab is open, so the way to keep
        one is to be handed the file. There is no uploader beside it either —
        a draft comes back in through **📥 Load my data** with everything else,
        which is one route rather than two that half overlap.
        """
        keys = [button.key for button in self.at.button]
        self.assertNotIn("bk-open-folder", keys)
        self.assertFalse([key for key in keys
                          if str(key).startswith("bk-upload")])
        self.assertIn("bk-download-draft",
                      [button.key for button in self.at.download_button])

    def test_the_download_hands_over_the_book_as_it_is_on_screen(self):
        """Not the last version written: saved or not, what you see is it."""
        self.at.text_input(key="bk-title").set_value("Typed, never saved").run()
        download = [button for button in self.at.download_button
                    if button.key == "bk-download-draft"][0]
        self.assertIn("Typed, never saved", self.book.to_json())
        self.assertTrue(download.label.startswith("⬇️"))


class TestTextBoxHeight(unittest.TestCase):
    """The text boxes size themselves, so there is no slider to get wrong."""

    def test_a_box_grows_with_its_text_and_then_stops(self):
        self.assertEqual(box_height(""), MIN_BOX_HEIGHT_PX)
        self.assertEqual(box_height("A single short line."), MIN_BOX_HEIGHT_PX)

        dozen = box_height("\n".join(["A line of a chapter."] * 12))
        self.assertGreater(dozen, MIN_BOX_HEIGHT_PX)
        self.assertLess(dozen, MAX_BOX_HEIGHT_PX)
        self.assertGreater(box_height("\n".join(["A line."] * 24)), dozen)

        # A wrapped paragraph counts too, or one long line would sit in a box
        # two lines tall while filling twenty.
        self.assertGreater(box_height("word " * 400), box_height("word " * 20))
        self.assertEqual(box_height("word " * 4000), MAX_BOX_HEIGHT_PX)


# --------------------------------------------------------------------------
# Editing the shape of the book
# --------------------------------------------------------------------------


class TestSectionsPanel(EditorTestCase):
    def add(self, part="body"):
        self.at.button(key=f"bk-add-{part}").click().run()

    def test_sections_can_be_added_reordered_duplicated_and_removed(self):
        first = self.book.body[0].id
        self.at.text_area(key=f"bk-text-{first}").set_value("Alpha").run()
        self.add()
        second = self.book.body[1].id
        self.at.text_area(key=f"bk-text-{second}").set_value("Beta").run()
        self.assertEqual([s.text for s in self.book.body], ["Alpha", "Beta"])

        self.at.button(key=f"bk-up-{second}").click().run()
        self.assertEqual([s.text for s in self.book.body], ["Beta", "Alpha"])
        self.at.button(key=f"bk-down-{second}").click().run()
        self.assertEqual([s.text for s in self.book.body], ["Alpha", "Beta"])

        self.at.button(key=f"bk-copy-{first}").click().run()
        self.assertEqual([s.text for s in self.book.body],
                         ["Alpha", "Alpha", "Beta"])
        # The copy is its own section, with its own widgets.
        self.assertNotEqual(self.book.body[1].id, first)

        self.at.button(key=f"bk-del-{first}").click().run()
        self.assertEqual([s.text for s in self.book.body], ["Alpha", "Beta"])

    def test_each_part_of_the_book_takes_its_own_kind_of_section(self):
        self.at.selectbox(key="bk-add-kind-front").set_value("dedication")
        self.add("front")
        self.at.selectbox(key="bk-add-kind-back").set_value("appendix")
        self.add("back")
        self.assertEqual([s.kind for s in self.book.front], ["dedication"])
        self.assertEqual([s.kind for s in self.book.back], ["appendix"])

    def test_words_follow_the_section_they_were_typed_into_when_it_moves(self):
        first = self.book.body[0].id
        self.at.text_area(key=f"bk-text-{first}").set_value("Mine").run()
        self.add()
        second = self.book.body[1].id
        self.at.button(key=f"bk-up-{second}").click().run()
        self.assertEqual(self.box(f"bk-text-{first}"), "Mine")
        self.assertEqual(self.box(f"bk-text-{second}"), "")


# --------------------------------------------------------------------------
# The design panel and the sidebar it borrows its units from
# --------------------------------------------------------------------------


class TestDesignPanel(EditorTestCase):
    def test_choosing_a_page_size_sets_both_measurements(self):
        self.at.selectbox(key="bk-page-size").set_value("US trade").run()
        self.assertEqual(self.book.design.page_size_name, "US trade")
        self.assertAlmostEqual(self.book.design.page_width_in, 6.0, places=6)
        self.assertAlmostEqual(self.book.design.page_height_in, 9.0, places=6)

    def test_switching_units_converts_rather_than_reinterprets(self):
        self.at.number_input(key="bk-len-m-in-in").set_value(0.60).run()
        # The unit switch is the sidebar's, and it is shared with the other
        # view — the editor only borrows it, which is what these keys record.
        self.sidebar_widget("setting-units").set_value("Millimetres (mm)").run()
        # Converted, not reinterpreted: 0.6 in reads as ~15.2 mm, not as 0.6 mm.
        # The box shows one decimal place and writes back what it shows, so the
        # stored length can move by half a displayed step — 0.05 mm here.
        shown = self.at.number_input(key="bk-len-m-in-mm").value
        self.assertAlmostEqual(shown, 15.2, places=1)
        self.assertAlmostEqual(self.book.design.margin_inner_in, 0.60, delta=0.002)

        self.at.number_input(key="bk-len-m-in-mm").set_value(20.0).run()
        self.assertAlmostEqual(self.book.design.margin_inner_in, 20.0 / 25.4,
                               places=6)

    def test_the_typographic_choices_reach_the_manuscript(self):
        self.at.selectbox(key="bk-font").set_value("baskervville").run()
        self.at.slider(key="bk-leading").set_value(1.5).run()
        self.at.checkbox(key="bk-justify").set_value(False).run()
        self.at.selectbox(key="bk-chapter-label").set_value("chapter_word").run()
        self.at.radio(key="bk-chapter-start").set_value("recto").run()
        self.at.checkbox(key="bk-contents").set_value(False).run()

        design = self.book.design
        self.assertEqual(design.font_key, "baskervville")
        self.assertAlmostEqual(design.line_spacing, 1.5, places=6)
        self.assertIs(design.justify, False)
        self.assertEqual(design.chapter_label, "chapter_word")
        self.assertEqual(design.chapter_start, "recto")
        self.assertIs(design.contents, False)


# --------------------------------------------------------------------------
# All the way out to a PDF
# --------------------------------------------------------------------------


class TestBuilding(EditorTestCase):
    def a_book(self):
        self.at.text_input(key="bk-title").set_value("Bound At Home")
        self.at.text_input(key="bk-author").set_value("A. Writer")
        self.at.text_area(key=self.first_section_key()).set_value(
            "\n\n".join(["A sentence that goes on for a while, and then some more "
                         "of it, so the book has something to set."] * 6)
        )
        self.at.run()

    def test_the_build_button_writes_a_pdf_into_input(self):
        self.a_book()
        self.at.button(key="bk-build").click().run()
        self.assertFalse(self.at.exception, self.at.exception)

        built = main.INPUT_DIR / "Bound At Home.pdf"
        self.assertTrue(built.is_file())
        self.assertTrue(built.read_bytes().startswith(b"%PDF"))
        self.assertIn(built, main.list_available_books())
        # The lock is released and the last-build note is on the page.
        self.assertIsNone(self.state("busy_job"))
        self.assertIsNotNone(self.state("book_last_build"))

    def test_the_signatures_button_goes_all_the_way_to_output(self):
        self.a_book()
        self.at.button(key="bk-build-convert").click().run()
        self.assertFalse(self.at.exception, self.at.exception)

        ready = main.list_ready_books()
        self.assertEqual([book.name for book in ready], ["Bound At Home"])
        self.assertTrue(ready[0].signatures)
        for signature in ready[0].signatures:
            self.assertTrue(signature.read_bytes().startswith(b"%PDF"))
        self.assertIsNone(self.state("busy_job"))

    def test_a_book_with_nothing_in_it_cannot_be_built(self):
        for key in ("bk-build", "bk-build-convert"):
            button = self.at.button(key=key)
            self.assertTrue(button.disabled, key)

    def test_the_title_typed_with_the_click_is_the_book_that_gets_built(self):
        # The build buttons sit below every box, so this has always worked —
        # it is here so that it keeps working if the panels are ever reordered.
        self.at.text_input(key="bk-title").set_value("Named At The Last Moment")
        self.at.button(key="bk-build").click().run()
        self.assertTrue((main.INPUT_DIR / "Named At The Last Moment.pdf").is_file())

    def test_any_paper_size_can_be_chosen_and_nothing_is_ever_scaled(self):
        """Words have no size until the type is drawn, so nothing is resized.

        A6 paper cannot take an A5 book *that already exists* — the conversion
        view says so on the card and refuses. Here there is no book yet: the
        sheet is chosen first and the book is then set at half of it, so every
        paper size in the menu is available and the scale is always exactly 1.
        """
        self.a_book()
        self.choose_sheet("A6")
        for sheet in ("A6", "A3", "Letter", "Legal"):
            with self.subTest(sheet=sheet):
                self.at.selectbox(key="bkpref-sheet").set_value(sheet).run()
                self.assertFalse(self.at.exception, self.at.exception)
                self.assertFalse(self.at.button(key="bk-build-convert").disabled)
                self.assertEqual([error.value for error in self.at.error], [])
                # Nothing anywhere on the page says the book is being resized.
                shown = " ".join(
                    [caption.value for caption in self.at.caption]
                    + [warning.value for warning in self.at.warning]
                    + [info.value for info in self.at.info]
                ).lower()
                self.assertNotIn("scaled to", shown)
                self.assertNotIn("shrunk", shown)
                self.assertNotIn("enlarged", shown)

        # The choice the conversion view needs is not asked anywhere here.
        self.assertIsNone(self.widget_named("setting-scale-mode"))

    def test_the_book_is_set_at_the_size_of_the_paper_it_was_given(self):
        """Chosen in 📐 Book design, read back out of the finished signature.

        Both halves matter: the signature is Letter because that is the paper,
        and the *book PDF* is Letter too — which is what makes the imposition a
        1:1 copy rather than a resize.
        """
        self.a_book()
        self.choose_sheet("Letter")
        self.at.button(key="bk-build-convert").click().run()
        self.assertFalse(self.at.exception, self.at.exception)

        built = pypdf.PdfReader(
            str(main.PREVIOUS_DIR / "Bound At Home.pdf")
        ).pages[0]
        self.assertAlmostEqual(float(built.mediabox.width), 11.0 * 72, places=1)
        self.assertAlmostEqual(float(built.mediabox.height), 8.5 * 72, places=1)

        ready = main.list_ready_books()
        self.assertEqual([book.name for book in ready], ["Bound At Home"])
        page = pypdf.PdfReader(str(ready[0].signatures[0])).pages[0]
        # Letter, landscape: 11 x 8.5 in, and not the A5 book's own 11.65 x 8.27.
        self.assertAlmostEqual(float(page.mediabox.width), 11.0 * 72, places=1)
        self.assertAlmostEqual(float(page.mediabox.height), 8.5 * 72, places=1)

    def test_the_paper_chosen_for_one_build_never_reaches_the_draft(self):
        """Giving the size as paper decides the build, not the saved book."""
        self.a_book()
        self.at.selectbox(key="bk-page-size").set_value("US trade").run()
        self.choose_sheet("A4")
        self.at.button(key="bk-build").click().run()
        self.assertFalse(self.at.exception, self.at.exception)

        # Built at half an A4 landscape sheet — A5 — and not at 6 x 9 in.
        built = pypdf.PdfReader(str(main.INPUT_DIR / "Bound At Home.pdf")).pages[0]
        self.assertAlmostEqual(float(built.mediabox.width), 297 / 25.4 * 72,
                               places=1)
        # The book on screen still says what the writer chose.
        self.assertEqual(self.book.design.page_size_name, "US trade")
        self.assertAlmostEqual(self.book.design.page_width_in, 6.0, places=6)

    def test_a_finished_run_hands_the_signatures_over(self):
        """This view has no “Ready to print” panel, and there is no folder.

        Pointing at a path on the server would be pointing at somewhere nobody
        can reach and that will not exist in a minute. The signatures leave the
        same way everything else does: as a file the browser is given.
        """
        self.a_book()
        self.at.button(key="bk-build-convert").click().run()

        folder = main.OUTPUT_DIR / "Bound At Home"
        self.assertEqual(self.state("book_last_build")["output_folder"], str(folder))
        self.assertIn("bk-download-built",
                      [button.key for button in self.at.download_button])
        # And no path anywhere on the page.
        self.assertFalse([block for block in self.at.code
                          if str(folder) in block.value])

    def test_the_example_book_goes_all_the_way_to_signatures(self):
        """The one button that shows what the editor can do has to work.

        The example is the only book most people will build before typing one
        of their own, and it exercises every kind of section at once — a part
        divider, five chapters, an interlude, display pages, back matter.
        """
        self.at.button(key="bk-act-example").click().run()
        self.assertEqual(len(self.book.chapters), 5)

        self.at.button(key="bk-build-convert").click().run()
        self.assertFalse(self.at.exception, self.at.exception)

        ready = main.list_ready_books()
        self.assertEqual([book.name for book in ready], ["The Folded Sheet"])
        self.assertTrue(ready[0].signatures)

    def test_a_build_name_typed_after_a_save_is_the_one_that_is_used(self):
        self.a_book()
        self.at.text_input(key="bk-file-name").set_value("Print version 2").run()
        # The save reruns from the top of the page and never redraws the name
        # box; the build after it must still use the name that was typed.
        self.at.button(key="bk-save").click().run()
        self.at.button(key="bk-build").click().run()

        self.assertTrue((main.INPUT_DIR / "Print version 2.pdf").is_file())
        self.assertFalse((main.INPUT_DIR / "Bound At Home.pdf").exists())


# --------------------------------------------------------------------------
# The two views
# --------------------------------------------------------------------------


class TestTheTwoViews(EditorTestCase):
    def test_the_tabs_are_named_after_what_they_do(self):
        labels = self.at.radio(key="view").options
        self.assertEqual(labels, [
            "📚  Convert 2 Column Formatted PDF into PDF Signatures",
            "✍️  Convert Inputted Text into PDF Signatures",
        ])

    def test_a_book_survives_a_trip_through_the_other_view(self):
        self.type_title_page()
        self.at.text_area(key=self.first_section_key()).set_value("Words.")
        self.at.run()

        self.at.radio(key="view").set_value(CONVERT_VIEW).run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.assertEqual(self.box("bk-title"), None)  # not drawn at all

        self.at.radio(key="view").set_value(WRITE_VIEW).run()
        self.assert_title_page(self.book, "the editor after a view switch")
        self.assertEqual(self.box("bk-title"), "Demon Noble Girl")
        self.assertEqual(self.box(self.first_section_key()), "Words.")

    def test_a_hand_picked_build_name_survives_a_trip_through_the_other_view(self):
        self.at.text_input(key="bk-title").set_value("The Title").run()
        self.at.text_input(key="bk-file-name").set_value("Print version 2").run()

        self.at.radio(key="view").set_value(CONVERT_VIEW).run()
        self.at.radio(key="view").set_value(WRITE_VIEW).run()
        self.assertEqual(self.box("bk-file-name"), "Print version 2")


# --------------------------------------------------------------------------
# Where each setting lives: the sidebar, or the tab that decides it
# --------------------------------------------------------------------------


class TestSettingsAreNotAskedTwice(EditorTestCase):
    """The sidebar holds what both tabs share, and each tab holds its own.

    It used to hold the paper as well, which meant one measurement had two
    menus: the sheet in the sidebar and the finished page size in 📐 Book
    design, either of which decided how big the book came out. The column
    measurements were worse — they describe a PDF somebody else made, and were
    on screen, in the sidebar, while a book was being typed.

    What moved must still be *held*: Streamlit throws away the state of every
    widget a run did not draw, so a paper size that only exists on one tab has
    to come back with the value it was given after a trip through the other.
    """

    SHARED = {
        "setting-units": "Millimetres (mm)",
        "setting-sheets": 3,
        "setting-duplex": "Flip on short edge",
    }
    # Not one of the shared settings: it says nothing about the book, only about
    # the light the app is read in. It is in the sidebar because that is where
    # the ⋮ menu's switcher moved to when the corner was taken away.
    APPEARANCE = ("setting-theme",)
    CONVERT_ONLY = ("setting-sheet-size", "setting-orientation",
                    "setting-scale-mode", "setting-auto-columns")
    WRITE_ONLY = ("bkpref-size-from", "bkpref-sheet", "bkpref-sheet-landscape")

    def sidebar_keys(self):
        return sorted(
            str(element.key)
            for kind in (self.at.sidebar.radio, self.at.sidebar.selectbox,
                         self.at.sidebar.checkbox, self.at.sidebar.number_input,
                         self.at.sidebar.text_input, self.at.sidebar.slider)
            for element in kind
        )

    def test_the_sidebar_is_the_settings_both_tabs_share_and_no_more(self):
        expected = sorted([*self.SHARED, *self.APPEARANCE])
        self.assertEqual(self.sidebar_keys(), expected, "in the writing view")
        self.at.radio(key="view").set_value(CONVERT_VIEW).run()
        self.assertEqual(self.sidebar_keys(), expected, "in the conversion view")

    def test_a_shared_setting_chosen_in_one_tab_is_still_chosen_in_the_other(self):
        for key, value in self.SHARED.items():
            self.sidebar_widget(key).set_value(value).run()
        chosen = {key: self.sidebar_widget(key).value for key in self.SHARED}
        self.assertEqual(chosen, self.SHARED)

        self.at.radio(key="view").set_value(CONVERT_VIEW).run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.assertEqual({key: self.sidebar_widget(key).value
                          for key in self.SHARED}, self.SHARED,
                         "in the conversion view")

    def test_neither_tab_shows_the_other_tab_s_paper_controls(self):
        for key in self.CONVERT_ONLY:
            self.assertIsNone(self.widget_named(key), f"{key} in the writing view")
        self.assertIsNotNone(self.widget_named("bkpref-size-from"))
        self.assertIsNotNone(self.widget_named("bk-page-size"))

        self.at.radio(key="view").set_value(CONVERT_VIEW).run()
        for key in self.WRITE_ONLY:
            self.assertIsNone(self.widget_named(key), f"{key} in the other view")
        self.assertIsNotNone(self.widget_named("setting-sheet-size"))
        # The column measurements are here, folded away, and only here.
        self.assertIsNotNone(self.widget_named("margin-in"))
        self.assertIsNotNone(self.widget_named("gap-in"))

    def test_only_one_menu_decides_how_big_a_typed_book_is(self):
        """Two ways of saying it, never both on screen at once."""
        self.assertIsNotNone(self.widget_named("bk-page-size"))
        self.assertIsNone(self.widget_named("bkpref-sheet"))

        self.choose_sheet("A4")
        self.assertIsNotNone(self.widget_named("bkpref-sheet"))
        self.assertIsNone(self.widget_named("bk-page-size"))

    def test_the_conversion_paper_survives_a_trip_through_the_writing_tab(self):
        self.at.radio(key="view").set_value(CONVERT_VIEW).run()
        self.at.selectbox(key="setting-sheet-size").set_value("Letter").run()
        self.at.radio(key="setting-orientation").set_value(False).run()
        self.at.radio(key="setting-scale-mode").set_value("actual").run()
        self.at.checkbox(key="setting-auto-columns").set_value(False).run()

        self.at.radio(key="view").set_value(WRITE_VIEW).run()
        self.at.radio(key="view").set_value(CONVERT_VIEW).run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.assertEqual(self.widget_named("setting-sheet-size").value, "Letter")
        self.assertIs(self.widget_named("setting-orientation").value, False)
        self.assertEqual(self.widget_named("setting-scale-mode").value, "actual")
        self.assertIs(self.widget_named("setting-auto-columns").value, False)

    def test_the_writing_paper_survives_a_trip_through_the_conversion_tab(self):
        self.choose_sheet("Letter", landscape=False)
        self.at.checkbox(key="bkpref-autosave").set_value(False).run()

        self.at.radio(key="view").set_value(CONVERT_VIEW).run()
        self.at.radio(key="view").set_value(WRITE_VIEW).run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.assertEqual(self.widget_named("bkpref-size-from").value, "sheet")
        self.assertEqual(self.widget_named("bkpref-sheet").value, "Letter")
        self.assertIs(self.widget_named("bkpref-sheet-landscape").value, False)
        # Autosave too: coming back to find it switched itself on would write
        # over the draft the writer had deliberately stopped saving.
        self.assertIs(self.widget_named("bkpref-autosave").value, False)

    def test_the_page_size_says_what_sheet_it_comes_to(self):
        self.at.selectbox(key="bk-page-size").set_value("US trade").run()
        captions = " ".join(caption.value for caption in self.at.caption)
        # A US trade book page is 6 x 9 in, so its sheet is 12 x 9 in.
        self.assertIn("12.00 × 9.00 in", captions)

    def test_the_column_measurements_say_whose_page_they_describe(self):
        self.at.radio(key="view").set_value(CONVERT_VIEW).run()
        captions = " ".join(caption.value for caption in self.at.caption)
        self.assertIn("Where the two columns sit on a page of the *input* PDF",
                      captions)


class TestCarryingTheWorkspaceInAZip(unittest.TestCase):
    """`Script/workspace.py` on its own, without the app around it.

    The app can be hosted, and a hosted copy has no storage: its disk is wiped
    on every restart, and "📂 Open file location" opens a folder on a server
    nobody can see. So the user carries the whole workspace themselves, as one
    zip. These are the tests for the two ends of that trip.
    """

    def make_workspace(self):
        """A folders mapping of the shape `open_session` hands the app.

        Built by hand rather than through `activate`, which would point the
        real `main` at it and leave it pointed there for whatever ran next.
        """
        root = Path(tempfile.mkdtemp(prefix="zip-test-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        folders = {name: root / name for name in workspace.DATA_FOLDERS}
        for folder in folders.values():
            folder.mkdir(parents=True)
        return folders

    def fill(self, folders):
        """One file in each corner a user would notice losing."""
        (folders["Input"] / "Waiting.pdf").write_bytes(b"%PDF-1.4 waiting")
        signatures = folders["Output"] / "Done" / "book_signatures"
        signatures.mkdir(parents=True)
        (signatures / "signature_1.pdf").write_bytes(b"%PDF-1.4 signature")
        (folders["Previously_Converted"] / "Old.pdf").write_bytes(b"%PDF-1.4 old")
        (folders["Manuscripts"] / f"Draft{DRAFT_SUFFIX}").write_text(
            '{"title": "Draft"}', encoding="utf-8"
        )

    def contents(self, folders):
        return {
            f"{name}/{path.relative_to(folder).as_posix()}": path.read_bytes()
            for name, folder in folders.items()
            for path in sorted(folder.rglob("*")) if path.is_file()
        }

    # ---- the round trip --------------------------------------------------
    def test_every_file_comes_back_byte_for_byte(self):
        saved = self.make_workspace()
        self.fill(saved)
        expected = self.contents(saved)

        loaded = self.make_workspace()
        self.assertEqual(workspace.unpack(workspace.pack(saved), loaded), 4)
        self.assertEqual(self.contents(loaded), expected)

    def test_an_empty_workspace_comes_back_as_four_empty_folders(self):
        """Not as an error, and not as four folders that stopped existing."""
        empty = self.make_workspace()
        loaded = self.make_workspace()
        self.fill(loaded)

        self.assertEqual(workspace.unpack(workspace.pack(empty), loaded), 0)
        for folder in loaded.values():
            self.assertTrue(folder.is_dir(), folder)
            self.assertEqual(list(folder.iterdir()), [], folder)

    def test_loading_replaces_the_workspace_rather_than_adding_to_it(self):
        """A zip is the whole of a workspace, so it is the whole of what stays.

        Merging would leave a book the user had deleted before saving sitting
        in the listing beside the ones they kept, with nothing to say which zip
        it came from.
        """
        saved = self.make_workspace()
        self.fill(saved)
        data = workspace.pack(saved)

        loaded = self.make_workspace()
        (loaded["Input"] / "Leftover.pdf").write_bytes(b"%PDF-1.4 leftover")
        workspace.unpack(data, loaded)

        self.assertFalse((loaded["Input"] / "Leftover.pdf").exists())
        self.assertTrue((loaded["Input"] / "Waiting.pdf").is_file())

    # ---- what a zip is not allowed to do ---------------------------------
    def test_an_entry_cannot_write_outside_the_folder_it_names(self):
        """The oldest trick there is against code that unpacks archives."""
        folders = self.make_workspace()
        root = folders["Input"].parent
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Input/../../escaped.pdf", b"no")
            archive.writestr("../escaped2.pdf", b"no")
            archive.writestr("Input/Fine.pdf", b"yes")

        self.assertEqual(workspace.unpack(buffer.getvalue(), folders), 1)
        self.assertTrue((folders["Input"] / "Fine.pdf").is_file())
        self.assertFalse((root.parent / "escaped.pdf").exists())
        self.assertFalse((root.parent / "escaped2.pdf").exists())

    def test_a_zip_from_somewhere_else_is_refused_with_nothing_deleted(self):
        folders = self.make_workspace()
        self.fill(folders)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("holiday/beach.jpg", b"not a book")

        with self.assertRaises(ValueError):
            workspace.unpack(buffer.getvalue(), folders)
        self.assertTrue((folders["Input"] / "Waiting.pdf").is_file())

    def test_a_folder_zipped_by_hand_is_still_read(self):
        """Right-click → Compress puts everything one level deeper."""
        folders = self.make_workspace()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("My Books/Input/Wrapped.pdf", b"%PDF-1.4 wrapped")
            archive.writestr(f"My Books/Manuscripts/D{DRAFT_SUFFIX}", b"{}")

        self.assertEqual(workspace.unpack(buffer.getvalue(), folders), 2)
        self.assertTrue((folders["Input"] / "Wrapped.pdf").is_file())

    def test_one_book_can_be_taken_home_without_taking_all_of_them(self):
        folders = self.make_workspace()
        self.fill(folders)
        book = folders["Output"] / "Done"

        names = zipfile.ZipFile(
            io.BytesIO(workspace.pack_folder(book, "Done"))
        ).namelist()
        self.assertEqual(names, ["Done/book_signatures/signature_1.pdf"])


class TestNothingIsKept(unittest.TestCase):
    """The retention rule, which is the reason the rest of this exists.

    A visitor's files live in a folder named after their session and are gone
    within seconds of the browser going away. Whatever somebody uploads is
    theirs; the way not to be answerable for it is not to hold it.
    """

    def setUp(self):
        self.sessions = Path(tempfile.mkdtemp(prefix="sessions-test-"))
        self.addCleanup(shutil.rmtree, self.sessions, ignore_errors=True)
        self.addCleanup(setattr, workspace, "SESSIONS_ROOT",
                        workspace.SESSIONS_ROOT)
        workspace.SESSIONS_ROOT = self.sessions
        for name in ("INPUT_DIR", "OUTPUT_DIR", "PREVIOUS_DIR", "MANUSCRIPT_DIR"):
            self.addCleanup(setattr, main, name, getattr(main, name))

    def a_session(self, connected):
        """One session's folder, with a book in it. `connected` is its id."""
        state = {workspace.SESSION_KEY: connected}
        folders = workspace.open_session(state)
        (folders["Input"] / "Theirs.pdf").write_bytes(b"%PDF-1.4 theirs")
        return self.sessions / state[workspace.SESSION_KEY]

    def sweep_seeing(self, connected, now=None):
        """One sweep, with the runtime reporting `connected` as still open."""
        with mock.patch.object(workspace, "_connected_session_ids",
                               return_value=connected):
            return workspace.sweep(now=now)

    def test_a_session_still_on_screen_is_left_alone(self):
        root = self.a_session("live")
        self.assertEqual(self.sweep_seeing({"live"}), [])
        self.assertTrue((root / "Input" / "Theirs.pdf").is_file())

    def test_a_session_whose_browser_has_gone_is_erased(self):
        root = self.a_session("gone")
        # Nothing yet: the grace period is what a network blip survives on.
        self.assertEqual(self.sweep_seeing(set()), [])
        self.assertTrue(root.is_dir())

        later = time.time() + workspace.GRACE_SECONDS + 1
        self.assertEqual(self.sweep_seeing(set(), now=later), [root])
        self.assertFalse(root.exists())

    def test_a_runtime_that_cannot_be_read_never_deletes_a_live_session(self):
        """None means "I do not know", and not knowing must not mean deleting.

        If reading the runtime ever breaks, the failure has to be files kept
        slightly too long, not somebody's book vanishing mid-sentence.
        """
        root = self.a_session("unknown")
        past_the_grace = time.time() + workspace.GRACE_SECONDS + 1
        self.assertEqual(self.sweep_seeing(None, now=past_the_grace), [])
        self.assertTrue(root.is_dir())

        # The backstop still applies, so nothing survives indefinitely.
        much_later = time.time() + workspace.ORPHAN_SECONDS + 1
        self.assertEqual(self.sweep_seeing(None, now=much_later), [root])

    def test_asking_for_it_erases_it_now(self):
        state = {workspace.SESSION_KEY: "impatient"}
        workspace.open_session(state)
        root = self.sessions / state[workspace.SESSION_KEY]
        (root / "Input" / "Regret.pdf").write_bytes(b"%PDF-1.4 regret")

        workspace.discard(state)
        self.assertFalse(root.exists())
        # And the next run starts empty under a new name rather than reopening
        # the folder that was just emptied.
        self.assertNotIn(workspace.SESSION_KEY, state)

    def test_nothing_this_process_made_outlives_it(self):
        root = self.a_session("shutting-down")
        workspace._discard_everything()
        self.assertFalse(root.exists())

    def test_two_sessions_never_share_a_folder(self):
        one = self.a_session("first")
        two = self.a_session("second")
        self.assertNotEqual(one, two)
        self.assertTrue((one / "Input" / "Theirs.pdf").is_file())

    def test_the_folders_are_nowhere_near_the_app(self):
        """Not a subfolder of the repo, under any name."""
        root = self.a_session("elsewhere")
        self.assertNotIn(main.ROOT_DIR.resolve(), root.resolve().parents)


class TestTheSizeLimit(unittest.TestCase):
    """500 MB per session, counted over everything and enforced everywhere.

    A per-file cap is not a cap: nothing stops the next file. What has to hold
    is the total, against uploads, against a loaded zip, and against work whose
    output size is not knowable until it has been written.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="limit-test-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.folders = {name: self.root / name
                        for name in workspace.DATA_FOLDERS}
        for folder in self.folders.values():
            folder.mkdir(parents=True)
        # A limit small enough to reach in a test, restored afterwards.
        self.addCleanup(setattr, workspace, "LIMIT_BYTES",
                        workspace.LIMIT_BYTES)
        workspace.LIMIT_BYTES = 1000

    def fill(self, size, name="Big.pdf"):
        (self.folders["Input"] / name).write_bytes(b"x" * size)

    def test_the_shipped_limits_are_500_mb_a_session_and_100_mb_a_book(self):
        workspace.LIMIT_BYTES = 500 * 1024 * 1024
        self.assertEqual(workspace.human(workspace.LIMIT_BYTES), "500 MB")
        self.assertEqual(workspace.human(workspace.MAX_UPLOAD_BYTES), "100 MB")

    def test_one_book_can_never_fill_the_session_it_is_converted_in(self):
        """The reason the two numbers are not the same.

        A book allowed to fill the session is a book that cannot then be
        converted: imposition writes its signatures, which come to about the
        size of the book again, and there would be nowhere to put them. Room for
        the book, its signatures and a numbered copy is the least that has to
        fit, and the shipped pair leaves far more than that.
        """
        workspace.LIMIT_BYTES = 500 * 1024 * 1024
        book = workspace.MAX_UPLOAD_BYTES
        needed = book + int(book * 1.1) + int(book * 1.1)
        self.assertLess(needed, workspace.LIMIT_BYTES)

    def test_usage_counts_every_folder_not_just_the_uploads(self):
        self.fill(100)
        (self.folders["Output"] / "sig.pdf").write_bytes(b"y" * 250)
        (self.folders["Manuscripts"] / "d.json").write_bytes(b"z" * 50)
        self.assertEqual(workspace.usage(self.folders), 400)
        self.assertEqual(workspace.free(self.folders), 600)

    def test_what_fits_is_allowed_and_what_does_not_is_refused(self):
        self.fill(900)
        self.assertEqual(workspace.guard(self.folders, 100), 100)
        with self.assertRaises(workspace.QuotaExceeded):
            workspace.guard(self.folders, 101)

    def test_a_zip_bigger_than_the_limit_is_refused_before_the_wipe(self):
        self.fill(200, "Precious.pdf")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Input/Huge.pdf", b"x" * 1200)

        with self.assertRaises(workspace.QuotaExceeded):
            workspace.unpack(buffer.getvalue(), self.folders)
        # Refused means refused: the session it would have replaced is intact.
        self.assertTrue((self.folders["Input"] / "Precious.pdf").is_file())

    def test_a_zip_that_lies_about_its_size_gets_nothing_through(self):
        """The header is the attacker's to write, so it is not what decides.

        A zip declaring a small entry and then streaming a large one is the
        standard way past a size check that trusts the listing. Two things stop
        it and either is enough: Python's own reader checks each entry's CRC
        against its declared length and calls the mismatch damage, and the copy
        loop counts the bytes that actually arrive rather than the ones that
        were promised. What this pins is the outcome — refused, with nothing
        kept — not which of the two got there first.
        """
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Input/Bomb.pdf", b"\0" * 20_000)
        data = bytearray(buffer.getvalue())
        # Rewrite every declared uncompressed size to something harmless.
        for header in (b"PK\x03\x04", b"PK\x01\x02"):
            at = data.find(header)
            offset = 22 if header == b"PK\x03\x04" else 24
            data[at + offset:at + offset + 4] = (10).to_bytes(4, "little")

        with self.assertRaises((workspace.QuotaExceeded, ValueError)):
            workspace.unpack(bytes(data), self.folders)
        self.assertEqual(workspace.usage(self.folders), 0)

    def test_a_load_that_would_go_over_leaves_nothing_behind(self):
        """Refused half way is still refused: no partial session survives."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Input/One.pdf", b"x" * 600)
            archive.writestr("Input/Two.pdf", b"y" * 600)

        with self.assertRaises(workspace.QuotaExceeded):
            workspace.unpack(buffer.getvalue(), self.folders)
        self.assertEqual(workspace.usage(self.folders), 0)

    def test_a_zip_that_exactly_fits_is_still_accepted(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Input/Exact.pdf", b"x" * workspace.LIMIT_BYTES)
        self.assertEqual(workspace.unpack(buffer.getvalue(), self.folders), 1)

    def test_a_long_job_is_stopped_the_moment_it_crosses(self):
        """The watcher, which is what makes this a limit and not a hope."""
        check = workspace.watcher(self.folders, every=0)
        self.fill(500)
        check()   # still inside

        self.fill(600, "More.pdf")
        with self.assertRaises(workspace.QuotaExceeded):
            check()

    def test_the_watcher_does_not_walk_the_session_on_every_page(self):
        """Imposition reports per page; a directory walk each time is a cost."""
        check = workspace.watcher(self.folders, every=3600)
        check()
        self.fill(5000)
        # Over the limit, but not yet time to look again.
        check()


class TestAJobStoppedByTheLimit(EditorTestCase):
    """The backstop, driven through the real app against a real conversion.

    Imposition does not know how big its output will be until it has written
    it, so the pre-flight estimate on the button cannot be the whole of the
    limit. This is the half that makes it a limit: a job that starts inside and
    would end outside is stopped part way, and takes its wreckage with it.
    """

    def crossing_watcher(self, after=3):
        """A watcher that lets a job start and then refuses, as running out does."""
        calls = []

        def crossing(_folders, every=1.0):
            def check():
                calls.append(1)
                if len(calls) > after:
                    raise workspace.QuotaExceeded("no room left (test)")
            return check

        return crossing, calls

    def convert_view_with_a_book(self, pages=40):
        from Script.test_imposition import build_source_pdf
        self.at.radio(key="view").set_value(CONVERT_VIEW).run()
        build_source_pdf(main.INPUT_DIR / "Big.pdf", pages)
        self.at.run()
        return "Big.pdf"

    def test_a_conversion_is_stopped_part_way_and_leaves_nothing(self):
        self.convert_view_with_a_book()
        crossing, calls = self.crossing_watcher()

        with mock.patch.object(workspace, "watcher", crossing):
            self.at.button(key="convert-Big.pdf").click().run()
        self.assertFalse(self.at.exception, self.at.exception)

        self.assertGreater(len(calls), 3, "the job never got going")
        self.assertIn("Stopped",
                      " ".join(e.value for e in self.at.error))
        # The staging folder `convert_book` writes into is gone, and the
        # half-made book is not offered as something to print.
        self.assertFalse(list(main.OUTPUT_DIR.rglob("_new_signatures")))
        self.assertNotIn("Big", [b.name for b in main.list_ready_books()])
        # The input is still where it was: an abandoned conversion must not
        # archive the book it failed to convert.
        self.assertTrue((main.INPUT_DIR / "Big.pdf").is_file())

    def test_numbering_stopped_part_way_leaves_no_half_written_pdf(self):
        """This one writes its file directly, with no staging folder to drop."""
        self.convert_view_with_a_book()
        crossing, _calls = self.crossing_watcher()

        with mock.patch.object(workspace, "watcher", crossing):
            self.at.button(key="number-Big.pdf").click().run()
        self.assertFalse(self.at.exception, self.at.exception)

        self.assertFalse((main.INPUT_DIR / "Big_Numbered.pdf").exists())


class TestTheZipControls(EditorTestCase):
    """The two controls at the top of the sidebar, driven through the app."""

    def zip_of(self, entries):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return ("my-books.zip", buffer.getvalue(), "application/zip")

    def test_both_ways_out_are_offered_above_the_settings(self):
        headers = [header.value for header in self.at.sidebar.header]
        self.assertEqual(headers[:2], ["Your data", "Settings"])
        self.assertIn("workspace-save",
                      [button.key for button in self.at.sidebar.download_button])
        self.assertIn("workspace-zip-0",
                      [box.key for box in self.at.sidebar.file_uploader])

    def test_the_paragraph_on_the_page_is_the_one_in_the_source(self):
        """There were two copies of it, and only one was ever displayed.

        The About entry in `set_page_config` is built so that Streamlit builds
        the toolbar the theme switcher lives in, and the toolbar is then hidden —
        so nothing written there is reachable. It held its own slightly different
        wording of the same paragraph, which is a trap for whoever edits it next
        expecting the page to change. Both now read one constant.
        """
        opening = "Turns a 2-column PDF book"
        captions = " ".join(caption.value for caption in self.at.caption)
        self.assertIn(opening, captions)

        # Written once in the file. If this ever counts two again, one of them is
        # the copy nobody can see.
        page = Path(main.__file__).with_name("app.py").read_text(encoding="utf-8")
        self.assertEqual(page.count(opening), 1)
        self.assertIn("TAGLINE", page)

    def test_the_retention_rule_is_on_the_page_not_only_in_the_readme(self):
        captions = " ".join(caption.value for caption in self.at.sidebar.caption)
        self.assertIn("Nothing is stored", captions)
        self.assertIn("erased when it closes", captions)

    def test_erasing_on_demand_takes_two_clicks_and_then_everything(self):
        theirs = main.INPUT_DIR / "Regret.pdf"
        theirs.write_bytes(b"%PDF-1.4 regret")
        root = main.INPUT_DIR.parent

        self.at.button(key="arm-delete-workspace").click().run()
        self.assertTrue(theirs.is_file(), "one click erased it")

        self.at.button(key="yes-delete-workspace").click().run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.assertFalse((root / "Input" / "Regret.pdf").exists())

    def test_nothing_is_deleted_until_the_second_click(self):
        """Picking a file in the dialog is not a decision to delete anything.

        A `file_uploader` fires the moment a file is chosen — no button
        involved — and this load replaces the workspace rather than adding to
        it. So the wrong file in the dialog must cost nothing until it is
        confirmed.
        """
        keeper = main.INPUT_DIR / "Keep me.pdf"
        keeper.write_bytes(b"%PDF-1.4 keep")

        self.assertNotIn("workspace-load-go",
                         [button.key for button in self.at.button])
        self.at.sidebar.file_uploader(key="workspace-zip-0").set_value(
            self.zip_of({"Input/Other.pdf": b"%PDF-1.4 other"})
        ).run()

        self.assertIn("workspace-load-go",
                      [button.key for button in self.at.button])
        self.assertTrue(keeper.is_file(), "the zip was applied without a click")

    def test_a_loaded_zip_replaces_every_folder(self):
        (main.INPUT_DIR / "Gone.pdf").write_bytes(b"%PDF-1.4 gone")

        self.at.sidebar.file_uploader(key="workspace-zip-0").set_value(
            self.zip_of({
                "Input/Arrived.pdf": b"%PDF-1.4 arrived",
                f"Manuscripts/Carried{DRAFT_SUFFIX}": b'{"title": "Carried"}',
            })
        ).run()
        self.at.button(key="workspace-load-go").click().run()
        self.assertFalse(self.at.exception, self.at.exception)

        self.assertTrue((main.INPUT_DIR / "Arrived.pdf").is_file())
        self.assertFalse((main.INPUT_DIR / "Gone.pdf").exists())
        self.assertEqual([draft.name for draft in list_drafts(self.drafts)],
                         ["Carried"])
        # The uploader is rebuilt under a new key, or the same zip would be
        # loaded again on the next thing the user clicked.
        self.assertIn("workspace-zip-1",
                      [box.key for box in self.at.sidebar.file_uploader])

    def test_a_full_session_closes_every_control_that_would_write(self):
        """Refused on the button, not after the click.

        An app that lets you start work it has already decided it will not let
        you finish is worse than one that says so up front.
        """
        self.addCleanup(setattr, workspace, "LIMIT_BYTES", workspace.LIMIT_BYTES)
        workspace.LIMIT_BYTES = 1
        self.at.run()
        self.assertFalse(self.at.exception, self.at.exception)

        self.assertTrue(self.at.button(key="bk-build").disabled)
        self.assertTrue(self.at.button(key="bk-build-convert").disabled)
        self.assertTrue(self.at.button(key="bk-save").disabled)
        warnings = " ".join(w.value for w in self.at.warning)
        self.assertIn("full", warnings)

        # The way out is still open: saving and erasing are never blocked.
        self.assertFalse(self.at.sidebar.download_button(key="workspace-save").disabled)
        self.assertFalse(self.at.button(key="arm-delete-workspace").disabled)

    def test_the_book_uploader_names_its_own_limit_exactly_once(self):
        """Streamlit prints one upload ceiling for the whole app, and it is the
        zip's. The book uploader corrects that line for itself — and says the
        figure nowhere else, so a reader sees one rule rather than three."""
        self.at.radio(key="view").set_value(CONVERT_VIEW).run()

        uploader = self.at.file_uploader(key="uploader-0")
        self.assertNotIn("100", uploader.label)
        self.assertFalse(uploader.help)
        written = " ".join(caption.value for caption in self.at.caption)
        self.assertNotIn("100 MB", written)
        self.assertNotIn("per PDF", written)

        # The one place it is written is the style block that overwrites the
        # dropzone's own line, which carries the figure from the same constant.
        page = Path(main.__file__).with_name("app.py").read_text(encoding="utf-8")
        self.assertIn("MB per file", page)
        self.assertIn("stFileUploaderDropzoneInstructions", page)

    def test_how_much_room_is_left_is_answered_in_one_place(self):
        """The sidebar's bar is the readout. The conversion view had a second
        one under the uploader, saying the same thing a scroll away."""
        self.at.radio(key="view").set_value(CONVERT_VIEW).run()
        captions = " ".join(caption.value for caption in self.at.caption)
        self.assertNotIn("left of", captions)

        # AppTest has no accessor for `st.progress`, so the bar is found in the
        # tree by its proto. There should be exactly one, and it should be the
        # sidebar's.
        def bars(node):
            proto = getattr(node, "proto", None)
            if type(proto).__name__ == "Progress":
                yield proto
            children = getattr(node, "children", None) or {}
            for child in getattr(children, "values", lambda: children)():
                yield from bars(child)

        everywhere = list(bars(self.at._tree))
        self.assertEqual(len(everywhere), 1)
        self.assertIn("500 MB", everywhere[0].text)
        self.assertEqual(list(bars(self.at.sidebar)), everywhere)

    def test_a_book_over_the_per_file_limit_is_refused_and_not_written(self):
        self.addCleanup(setattr, workspace, "MAX_UPLOAD_BYTES",
                        workspace.MAX_UPLOAD_BYTES)
        workspace.MAX_UPLOAD_BYTES = 100
        self.at.radio(key="view").set_value(CONVERT_VIEW).run()

        self.at.file_uploader(key="uploader-0").set_value(
            ("Enormous.pdf", b"%PDF-1.4" + b"x" * 500, "application/pdf")
        ).run()
        self.assertFalse(self.at.exception, self.at.exception)

        self.assertFalse((main.INPUT_DIR / "Enormous.pdf").exists())
        errors = " ".join(e.value for e in self.at.error)
        self.assertIn("Enormous.pdf", errors)
        self.assertIn("over the", errors)

    def test_a_book_that_fits_is_still_taken(self):
        self.at.radio(key="view").set_value(CONVERT_VIEW).run()
        self.at.file_uploader(key="uploader-0").set_value(
            ("Small.pdf", b"%PDF-1.4 small", "application/pdf")
        ).run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.assertTrue((main.INPUT_DIR / "Small.pdf").is_file())

    def test_a_zip_that_is_not_ours_is_reported_and_changes_nothing(self):
        keeper = main.INPUT_DIR / "Keep me.pdf"
        keeper.write_bytes(b"%PDF-1.4 keep")

        self.at.sidebar.file_uploader(key="workspace-zip-0").set_value(
            self.zip_of({"holiday/beach.jpg": b"not a book"})
        ).run()
        self.at.button(key="workspace-load-go").click().run()
        self.assertFalse(self.at.exception, self.at.exception)

        self.assertTrue(keeper.is_file())
        self.assertIn("Could not load that zip",
                      " ".join(error.value for error in self.at.error))


if __name__ == "__main__":
    unittest.main(verbosity=2)
