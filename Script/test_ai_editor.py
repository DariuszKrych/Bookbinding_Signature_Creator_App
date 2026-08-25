"""Tests for the "Generate book for printing with AI" button.

Run with:  python -m unittest Script.test_ai_editor -v

`Script/test_ai_book.py` checks the writer on its own. This checks the half that
has never been the easy half: what the button does to the page. It drives the
real `app.py` through Streamlit's own `AppTest` and never reaches the network —
`ai_book.write_book` is replaced with something that hands back a book it was
given.

The same rule as `test_editor.py` applies and is tested for explicitly: **a click
and the words typed just before it arrive in the same message**, so the
description box is set and the button clicked with a single `run()` after both.
"""

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Script import ai_book  # noqa: E402
from Script.manuscript import (  # noqa: E402
    Design,
    Manuscript,
    Section,
    list_drafts,
    load_draft,
)
from Script.test_editor import (  # noqa: E402
    CONVERT_VIEW,
    WRITE_VIEW,
    EditorTestCase,
)

# Assembled, not written out, so the literal shape of a key is in no file here.
# See `test_ai_book.TestTheKeyCannotBeCommittedOrShipped`.
FAKE_KEY = "sk-or-" + "v1-" + "0123456789abcdef" * 2


def written_book():
    """What the fake writer hands back — a small but complete book."""
    return Manuscript(
        title="The Paperclip",
        subtitle="A short history",
        author="M. Quire",
        front=[Section(kind="dedication", text="For the filing cabinet.")],
        body=[
            Section(kind="chapter", heading="Bent Wire", text="It begins.\n\nThen more."),
            Section(kind="chapter", heading="The Patent", text="A drawing is filed."),
        ],
    )


class AiEditorTestCase(EditorTestCase):
    """The editor, with a writer that answers instantly and offline."""

    def setUp(self):
        self.asked = []
        # What the fake writer does: a `Manuscript` to hand back, or an
        # exception to raise instead.
        self.result = written_book()

        # Started before the app is built, because `available()` decides whether
        # the button is drawn switched on at all, and that happens on run one.
        for name, replacement in (
            ("available", lambda: True),
            ("why_unavailable", lambda: ""),
            ("write_book", self.fake_write),
        ):
            patcher = mock.patch.object(ai_book, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

        super().setUp()

    def fake_write(self, prompt, *, design=None, progress=None, config=None):
        self.asked.append({"prompt": prompt, "design": design})
        if progress:
            progress(0.5, "Writing chapter 1 of 2: Bent Wire")
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    # ---- driving it ------------------------------------------------------
    def generate(self, description="a book about paperclips", from_view=None):
        """Type a description and press the button, the way a browser would.

        One `run()`, carrying the typed value and the click together, and no
        more. `AppTest.run()` follows the reruns the job goes through, so when it
        returns the book is already on screen — and a second `run()` would be
        worse than useless: the flash banner is consumed on the run that draws
        it, and the `set_value` flag that tells a box to show new words is only
        set on that same run. Running again clears both.
        """
        if from_view is not None:
            self.at.radio(key="view").set_value(from_view).run()
        self.at.text_input(key="ai-prompt").set_value(description)
        self.at.button(key="ai-write").click().run()

    def page_text(self):
        parts = [element.value for element in self.at.markdown]
        parts += [element.value for element in self.at.caption]
        return "\n".join(str(part) for part in parts)


# --------------------------------------------------------------------------
# The controls themselves
# --------------------------------------------------------------------------


class TestTheButton(AiEditorTestCase):
    def test_the_button_and_the_box_are_both_on_the_page(self):
        # `widget_named` does not look at buttons, so this asks for it directly.
        self.assertIn("AI", self.at.button(key="ai-write").label)
        self.assertIsNotNone(self.widget_named("ai-prompt"))

    def test_the_button_does_nothing_without_a_description(self):
        self.assertTrue(self.at.button(key="ai-write").disabled)

    def test_a_description_switches_the_button_on(self):
        self.at.text_input(key="ai-prompt").set_value("a book").run()
        self.assertFalse(self.at.button(key="ai-write").disabled)

    def test_the_box_is_there_on_both_views(self):
        """It lives above the view radio, so neither view can hide it."""
        self.at.text_input(key="ai-prompt").set_value("kept").run()
        self.at.radio(key="view").set_value(CONVERT_VIEW).run()
        self.assertEqual(self.box("ai-prompt"), "kept")
        self.at.radio(key="view").set_value(WRITE_VIEW).run()
        self.assertEqual(self.box("ai-prompt"), "kept")


class TestSwitchedOff(EditorTestCase):
    """No key, no LangChain: the button is drawn off and the app is unharmed."""

    def setUp(self):
        for name, replacement in (
            ("available", lambda: False),
            ("why_unavailable", lambda: "No OPENROUTER_API_KEY is set."),
        ):
            patcher = mock.patch.object(ai_book, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        super().setUp()

    def test_the_button_is_disabled_and_says_why(self):
        self.assertTrue(self.at.button(key="ai-write").disabled)
        captions = "\n".join(str(element.value) for element in self.at.caption)
        self.assertIn("switched off", captions)
        self.assertIn("OPENROUTER_API_KEY", captions)

    def test_both_views_still_work(self):
        self.assertFalse(self.at.exception, self.at.exception)
        self.at.radio(key="view").set_value(CONVERT_VIEW).run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.at.radio(key="view").set_value(WRITE_VIEW).run()
        self.assertFalse(self.at.exception, self.at.exception)

    def test_the_editor_still_saves(self):
        self.type_title_page()
        self.at.button(key="bk-save").click().run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.assertEqual(len(list_drafts(self.drafts)), 1)


# --------------------------------------------------------------------------
# What pressing it does
# --------------------------------------------------------------------------


class TestMovingToTheWritingView(AiEditorTestCase):
    def test_it_moves_you_there_from_the_conversion_view(self):
        """The whole reason the button sits above the view radio."""
        self.generate(from_view=CONVERT_VIEW)
        self.assertEqual(self.state("view"), WRITE_VIEW)
        self.assertFalse(self.at.exception, self.at.exception)

    def test_it_leaves_you_there_when_you_were_already(self):
        self.generate(from_view=WRITE_VIEW)
        self.assertEqual(self.state("view"), WRITE_VIEW)

    def test_the_book_arrives_whichever_view_it_was_started_from(self):
        self.generate(from_view=CONVERT_VIEW)
        self.assertEqual(self.book.title, "The Paperclip")


class TestKeepingWhatWasThere(AiEditorTestCase):
    def test_a_book_already_saved_is_left_where_it_is(self):
        """Nothing to write, so nothing is written — but it is still findable."""
        self.type_title_page()
        self.at.text_area(key=self.first_section_key()).set_value("Once upon a time.")
        self.at.button(key="bk-save").click().run()

        self.generate()

        drafts = list_drafts(self.drafts)
        self.assertEqual(len(drafts), 1, [d.name for d in drafts])
        kept = load_draft(drafts[0].path)
        self.assertEqual(kept.title, "Demon Noble Girl")
        self.assertIn("Once upon a time.", kept.body[0].text)

    def test_edits_made_since_the_last_save_get_a_draft_of_their_own(self):
        """The saved copy must not be written over, and the edits must survive.

        Autosave is off for this one, because with it on there is no such thing
        as an edit that has not reached the disk — which is the happier case, and
        the one the test above covers.
        """
        self.at.checkbox(key="bkpref-autosave").set_value(False).run()
        self.type_title_page()
        self.at.button(key="bk-save").click().run()
        self.at.text_area(key=self.first_section_key()).set_value("Added after saving.")
        self.at.run()

        self.generate()

        drafts = list_drafts(self.drafts)
        self.assertEqual(len(drafts), 2, [d.name for d in drafts])
        texts = [load_draft(draft.path).body[0].text for draft in drafts]
        self.assertIn("Added after saving.", texts)
        self.assertIn("", texts)

    def test_the_kept_draft_is_named_in_the_message(self):
        """The banner has to say where the book that was on screen went."""
        self.type_title_page()
        self.at.button(key="bk-save").click().run()
        self.generate()
        banner = "\n".join(str(element.value) for element in self.at.success)
        self.assertIn("previous book is in the drafts list", banner)
        self.assertIn("Demon Noble Girl", banner)

    def test_an_empty_editor_leaves_no_draft_behind(self):
        self.generate()
        self.assertEqual(list_drafts(self.drafts), [])

    def test_an_unsaved_book_is_kept_too(self):
        """Never saved is exactly when there is most to lose."""
        self.at.text_input(key="bk-title").set_value("Nearly Finished")
        self.at.text_area(key=self.first_section_key()).set_value("Some words.")
        self.at.run()

        self.generate()

        drafts = list_drafts(self.drafts)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(load_draft(drafts[0].path).title, "Nearly Finished")


class TestTheNewBookReachesTheScreen(AiEditorTestCase):
    def test_the_manuscript_is_the_written_one(self):
        self.generate()
        self.assertEqual(self.book.title, "The Paperclip")
        self.assertEqual(len(self.book.chapters), 2)

    def test_the_boxes_are_told_to_show_the_new_words(self):
        """The assertion that catches `session_state[BOOK] = book` on its own.

        Setting the manuscript without going through `adopt` leaves every box
        showing the previous book while the model and the PDF hold the new one.
        """
        self.at.text_input(key="bk-title").set_value("The Old One").run()
        self.generate()
        self.assertTrue(self.told_to_take_a_new_value("bk-title"))
        self.assertEqual(self.box("bk-title"), "The Paperclip")

    def test_the_chapters_are_in_their_boxes(self):
        self.generate()
        first = self.book.body[0]
        self.assertEqual(self.box(f"bk-text-{first.id}"), "It begins.\n\nThen more.")
        self.assertEqual(self.box(f"bk-head-{first.id}"), "Bent Wire")

    def test_the_dedication_arrives_in_the_front_matter(self):
        self.generate()
        self.assertEqual([s.kind for s in self.book.front], ["dedication"])

    def test_it_arrives_unsaved(self):
        """So autosave cannot write it over the draft just kept."""
        self.generate()
        self.assertEqual(self.state("book_draft_path"), "")

    def test_it_can_be_saved_like_any_other_book(self):
        self.generate()
        self.at.button(key="bk-save").click().run()
        self.assertFalse(self.at.exception, self.at.exception)
        titles = [draft.title for draft in list_drafts(self.drafts)]
        self.assertIn("The Paperclip", titles)


class TestTheDesignIsLeftAlone(AiEditorTestCase):
    def test_the_editors_own_design_is_what_is_passed_in(self):
        self.generate()
        self.assertEqual(len(self.asked), 1)
        self.assertIsInstance(self.asked[0]["design"], Design)

    def test_a_chosen_page_size_survives(self):
        self.at.selectbox(key="bk-page-size").set_value("A4").run()
        chosen = self.book.design.page_size_name

        self.result = written_book()
        self.result.design = Design(page_size_name=chosen)
        self.generate()

        self.assertEqual(self.book.design.page_size_name, chosen)


class TestTheWordsTypedWithTheClick(AiEditorTestCase):
    def test_the_description_typed_in_the_same_message_is_the_one_sent(self):
        """The app's signature bug class, in its newest possible home."""
        self.at.text_input(key="ai-prompt").set_value("a history of glue")
        self.at.button(key="ai-write").click().run()
        self.at.run()
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.asked[0]["prompt"], "a history of glue")


class TestWhenItGoesWrong(AiEditorTestCase):
    def test_a_failure_is_reported_and_the_app_unlocks(self):
        self.result = ai_book.AIError("the free models are busy")
        self.generate()

        self.assertTrue(self.at.error)
        self.assertIn("the free models are busy", self.at.error[0].value)
        self.assertIsNone(self.state("busy_job"))
        self.assertFalse(self.at.exception, self.at.exception)

    def test_the_editor_still_works_afterwards(self):
        self.result = ai_book.AIError("nope")
        self.generate()
        self.at.text_input(key="bk-title").set_value("Carrying on").run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.assertEqual(self.book.title, "Carrying on")

    def test_a_failure_does_not_lose_the_book_that_was_there(self):
        self.type_title_page()
        self.at.button(key="bk-save").click().run()
        self.result = ai_book.AIError("nope")
        self.generate()
        self.assertEqual(self.book.title, "Demon Noble Girl")

    def test_an_unexpected_error_is_still_caught(self):
        self.result = RuntimeError("something else entirely")
        self.generate()
        self.assertTrue(self.at.error)
        self.assertIsNone(self.state("busy_job"))
        self.assertFalse(self.at.exception, self.at.exception)


class TestTheKeyNeverReachesThePage(AiEditorTestCase):
    def test_a_key_in_a_failure_does_not_reach_the_screen(self):
        self.result = ai_book.AIError(ai_book.scrub(f"401 from {FAKE_KEY}", FAKE_KEY))
        self.generate()
        for element in self.at.error:
            self.assertNotIn(FAKE_KEY, str(element.value))
        self.assertNotIn(FAKE_KEY, self.page_text())

    def test_no_key_is_anywhere_in_session_state(self):
        self.generate()
        for key, value in self.at.session_state.filtered_state.items():
            self.assertNotIn("sk-or-v1-", str(value), f"in session key {key}")


class TestTheDataPolicySaysSo(AiEditorTestCase):
    """The policy and this feature belong changed together.

    Every other section of that notice promises nothing leaves the server. These
    check that the one thing which now does is disclosed where a reader will
    find it, and that the disclosure did not quietly rot away in a later edit.
    """

    def policy(self):
        for element in self.at.markdown:
            text = str(element.value)
            if "**14. Changes.**" in text:
                return text
        self.fail("the data policy is not on the page")

    def test_the_policy_names_the_service(self):
        self.assertIn("OpenRouter", self.policy())

    def test_the_policy_says_it_only_happens_on_the_button(self):
        policy = self.policy()
        self.assertIn("only if you press", policy)
        self.assertIn("Nothing else goes with it", policy)

    def test_the_policy_admits_free_models_may_train_on_it(self):
        """The part a reader would most resent finding out later."""
        self.assertIn("may train on it", self.policy())

    def test_the_policy_names_the_host_it_actually_runs_on(self):
        policy = self.policy()
        self.assertIn("Render", policy)
        self.assertNotIn("Hugging Face", policy)

    def test_the_policy_warns_that_the_writing_is_invented(self):
        self.assertIn("A model invents", self.policy())

    def test_the_policy_cites_the_licence_the_repository_actually_carries(self):
        """The LICENSE file is MIT. The policy used to lean §10's warranty
        disclaimer on "sections 11 and 12 of the GNU GPL v2", which MIT has no
        equivalent of — a disclaimer resting on a clause that does not exist.
        """
        policy = self.policy()
        licence = (Path(__file__).resolve().parent.parent / "LICENSE").read_text(
            encoding="utf-8"
        )
        self.assertIn("MIT License", licence.splitlines()[0])
        self.assertIn("MIT License", policy)
        self.assertNotIn("GPL", policy)
        self.assertNotIn("General Public License", policy)

    def test_the_policy_says_who_owns_what_the_model_wrote(self):
        policy = self.policy()
        self.assertIn("who owns what the AI wrote", policy)
        self.assertIn("claims nothing", policy)

    def test_the_policy_gives_a_basis_for_sending_it_abroad(self):
        self.assertIn("Article 49(1)(b)", self.policy())

    def test_the_policy_tells_you_not_to_put_personal_data_in_that_box(self):
        self.assertIn("Treat it as public", self.policy())

    def test_the_sections_are_numbered_one_to_fourteen_exactly_once(self):
        """A renumbering slip breaks every cross-reference in the notice."""
        found = [int(n) for n in re.findall(r"\*\*(\d+)\. ", self.policy())]
        self.assertEqual(found, list(range(1, 15)))

    def test_the_button_carries_its_own_notice(self):
        captions = "\n".join(str(element.value) for element in self.at.caption)
        self.assertIn("OpenRouter", captions)


if __name__ == "__main__":
    unittest.main(verbosity=2)
