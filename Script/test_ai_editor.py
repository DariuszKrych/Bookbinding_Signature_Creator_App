"""Tests for the "Generate 5 chapter mini-novel for printing with AI" button.

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
    AI_ROUTE,
    CONVERT_VIEW,
    HOME_ROUTE,
    WRITE_VIEW,
    EditorTestCase,
)

# Assembled, not written out, so the literal shape of a key is in no file here.
# See `test_ai_book.TestTheKeyCannotBeCommittedOrShipped`.
FAKE_KEY = "sk-or-" + "v1-" + "0123456789abcdef" * 2


# What the fake writer sends to `on_text`, i.e. what a half-written book looks
# like on screen. Deliberately not a phrase from the finished book, so a test can
# tell the live text apart from the book that replaces it.
STREAMED_MARKER = "A sentence still being writ"


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
        # The writer has a screen of its own now, rather than a box floating
        # above every screen. `EditorTestCase.setUp` lands on the writing view,
        # which is where a finished book ends up; these tests start where the
        # asking happens.
        self.go(AI_ROUTE)

    def in_editor(self):
        """Back to the writing screen, where the `bk-` boxes live.

        Called by any test that sets a book up before pressing the button. The
        boxes are on one screen and the button is on another, which is exactly
        what these tests are about.
        """
        return self.go(WRITE_VIEW)

    def fake_write(
        self, prompt, *, design=None, progress=None, on_text=None, config=None
    ):
        self.asked.append({"prompt": prompt, "design": design, "on_text": on_text})
        if progress:
            progress(0.5, "Writing chapter 1 of 2: Bent Wire")
        # The real writer calls this from the first token onwards. Called here
        # too, so the box the words go into is exercised by every test that
        # presses the button rather than by one test that remembers to.
        if on_text:
            on_text(STREAMED_MARKER)
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

        `from_view` is where the *user* was before coming here. The AI screen is
        always where the button is pressed: its progress bar and its streaming
        box both live on it, and a job whose screen stops being drawn is a job
        the runner abandons.
        """
        if from_view is not None:
            self.go(from_view)
        self.go(AI_ROUTE)
        self.at.text_area(key="ai-prompt").set_value(description)
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
        # The button no longer has to say "AI" — the card that opens this screen
        # says it, and the screen is named at the top. It says what it will do.
        self.assertIn("Write my", self.at.button(key="ai-write").label)
        self.assertIsNotNone(self.widget_named("ai-prompt"))

    def test_the_button_says_how_many_chapters_it_will_write(self):
        """The count is fixed, so the button says it rather than leaving the
        reader to find out by counting what arrives."""
        self.assertIn(
            f"{ai_book.chapter_count()} chapter", self.at.button(key="ai-write").label
        )

    def test_the_box_tells_you_length_is_not_yours_to_ask_for(self):
        box = self.widget_named("ai-prompt")
        self.assertIn(f"always {ai_book.chapter_count()} chapters", box.proto.placeholder)

    def test_the_button_offers_a_mini_novel(self):
        self.assertIn("mini-novel", self.at.button(key="ai-write").label)

    def test_the_button_has_no_hover_text(self):
        """It said its piece in a tooltip nobody hovers over. The caption under
        it says the same thing where it can be read."""
        self.assertFalse(self.at.button(key="ai-write").proto.help)

    def test_the_button_is_drawn_on_even_with_an_empty_box(self):
        """Not a slip, and the reason is in `TYPING_SCRIPT`.

        Streamlit's button ignores a click whenever *React* thinks it is
        disabled, whatever the page has since been told — so a button the server
        drew switched off can be made to look switched on and still swallow the
        press. Drawing it on and refusing on the server is what makes the first
        press after typing a real one.
        """
        self.assertFalse(self.at.button(key="ai-write").disabled)

    def test_an_empty_box_asks_for_a_description_on_the_button(self):
        label = self.at.button(key="ai-write").label
        self.assertIn("**(Please type a description of the book to generate)**", label)

    def test_the_asking_goes_away_once_something_is_typed(self):
        self.at.text_area(key="ai-prompt").set_value("a book").run()
        self.assertNotIn("Please type", self.at.button(key="ai-write").label)

    def test_pressing_it_with_an_empty_box_starts_nothing(self):
        """The refusal that the drawn-on button leans on."""
        self.at.button(key="ai-write").click().run()
        self.assertEqual(self.asked, [])
        self.assertFalse(self.at.exception, self.at.exception)

    def test_pressing_it_with_only_spaces_starts_nothing(self):
        self.at.text_area(key="ai-prompt").set_value("    ")
        self.at.button(key="ai-write").click().run()
        self.assertEqual(self.asked, [])
        self.assertFalse(self.at.exception, self.at.exception)

    def test_a_description_and_one_press_is_enough(self):
        """The whole complaint, as a test: type, press once, get a book."""
        self.at.text_area(key="ai-prompt").set_value("a book about paperclips")
        self.at.button(key="ai-write").click().run()
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.asked[0]["prompt"], "a book about paperclips")


class TestTheButtonKeepsUpWithTyping(AiEditorTestCase):
    """Streamlit sends a box's value on blur; the button cannot wait that long.

    The fix is a script, so what can be checked here is that the script is on
    the page, that it carries the two things it needs, and that it is told to
    keep its hands off the button when the button is off for another reason.
    """

    def scripts(self):
        """Every `st.iframe` on the page, as one lump of text.

        `AppTest` has no accessor for an iframe, so it comes back through `get`
        as an unknown element with its proto attached.
        """
        return "\n".join(str(frame.proto.srcdoc) for frame in self.at.get("iframe"))

    def test_the_script_is_on_the_page(self):
        self.assertIn("st-key-ai-prompt", self.scripts())
        self.assertIn("st-key-ai-write", self.scripts())

    def test_the_script_says_the_same_words_as_the_button(self):
        """One string in `app.py`, so the label and the script cannot drift."""
        self.assertIn(
            "Please type a description of the book to generate", self.scripts()
        )

    def test_it_is_free_to_act_when_nothing_else_is_wrong(self):
        self.assertIn("__bindLocked = false", self.scripts())

    def test_typing_alone_never_switches_the_button_on(self):
        """The script may only lift the lock the empty box put there.

        Whether it is allowed to act at all is decided in Python and carried in
        the script's own text, so the browser is never in a position to switch
        on a button that a running job, a full disk or a missing key turned off.
        `TestSwitchedOffLeavesTheScriptLocked` is the other half of this.
        """
        self.assertIn("if (window.parent.__bindLocked) return;", self.scripts())

    def test_a_typed_description_survives_leaving_the_screen(self):
        """It used to live above the view radio, drawn on every run, so nothing
        could lose it. It has a screen of its own now — which means Streamlit
        drops its state the moment another screen is drawn, and only
        `settings.carried` puts it back. A description somebody spent a minute
        writing must not be the price of a look at the front page.
        """
        self.at.text_area(key="ai-prompt").set_value("kept").run()
        self.go(HOME_ROUTE)
        self.assertIsNone(self.box("ai-prompt"), "still drawn off its own screen")
        self.go(CONVERT_VIEW)
        self.go(WRITE_VIEW)
        self.go(AI_ROUTE)
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

    def test_the_card_says_so_before_you_go_in(self):
        """A copy with no key is a perfectly good copy of this app, so the card
        is switched off and says why rather than vanishing."""
        self.go(HOME_ROUTE)
        self.assertTrue(
            next(b for b in self.at.button if b.key == "bookcard-go-ai").disabled
        )
        captions = "\n".join(str(element.value) for element in self.at.caption)
        self.assertIn("no API key", captions)

    def test_the_button_is_disabled_and_says_why(self):
        self.go(AI_ROUTE)
        self.assertTrue(self.at.button(key="ai-write").disabled)
        page = "\n".join(
            str(element.value)
            for element in list(self.at.info) + list(self.at.caption)
        )
        self.assertIn("switched off", page)
        self.assertIn("OPENROUTER_API_KEY", page)

    def test_both_views_still_work(self):
        self.assertFalse(self.at.exception, self.at.exception)
        self.go(CONVERT_VIEW)
        self.assertFalse(self.at.exception, self.at.exception)
        self.go(WRITE_VIEW)
        self.assertFalse(self.at.exception, self.at.exception)

    def test_the_editor_still_saves(self):
        self.type_title_page()
        self.at.button(key="bk-save").click().run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.assertEqual(len(list_drafts(self.drafts)), 1)

    def test_the_typing_script_is_locked_out(self):
        """No key, so no amount of typing may light that button up."""
        scripts = "\n".join(
            str(frame.proto.srcdoc) for frame in self.at.get("iframe")
        )
        self.assertIn("__bindLocked = true", scripts)


# --------------------------------------------------------------------------
# What pressing it does
# --------------------------------------------------------------------------


class TestWatchingItBeWritten(AiEditorTestCase):
    """The book appears while it is being written, not only once it is done.

    A model takes minutes over five chapters. What it does with those
    minutes used to be nothing at all — a progress bar and a blank page — and the
    fix is not a faster model but showing the words as they arrive.
    """

    def test_the_writer_is_given_somewhere_to_put_the_words(self):
        self.generate()
        self.assertIsNotNone(self.asked[-1]["on_text"])

    def test_the_live_text_makes_way_for_the_finished_book(self):
        """It belongs to the run that is writing, and to no run after it."""
        self.generate()
        self.assertNotIn(STREAMED_MARKER, self.page_text())

    def test_drawing_the_words_does_not_upset_the_page(self):
        """The whole book still arrives, through a real `st.empty()` and all."""
        self.generate()
        self.assertFalse(self.at.exception, self.at.exception)
        self.assertIn("The Paperclip", self.state("bk-title"))


class TestMovingToTheWritingView(AiEditorTestCase):
    """The hand-off, which is the most carefully sequenced thing in the app.

    The move happens **when the book is finished**, not when the button is
    pressed — and the difference is not cosmetic. The progress bar goes into the
    slot the button occupied and the words stream into a box beneath it, so both
    live on the AI screen; a job whose screen stops being drawn hands the runner
    no slot, and the runner answers a job it cannot find by releasing the lock
    and rerunning with the work never started and nothing said.

    So the writing keeps this screen for its whole duration, and `collect()`
    finding a finished book is what moves the user — at the top of the next run,
    before a single `bk-` widget exists, which is the other thing that has to be
    true for `adopt` to be allowed to wipe them.
    """

    def test_the_job_was_claimed_on_the_screen_that_draws_its_slot(self):
        """`busy_route` is written once, by `claim_job`, and never cleared — so
        after the book has arrived it still records where the work was pinned.
        Anything other than the AI screen here means the progress bar and the
        streaming box were on a screen the run was not drawing, which is the
        shape of a job the runner silently abandons.
        """
        self.generate()
        self.assertEqual(self.state("busy_route"), AI_ROUTE)

    def test_a_finished_book_moves_you_to_the_editor(self):
        self.generate()
        self.assertEqual(self.state("route"), WRITE_VIEW)
        self.assertFalse(self.at.exception, self.at.exception)

    def test_it_says_where_the_book_on_screen_came_from(self):
        self.generate()
        banner = "\n".join(str(element.value) for element in self.at.success)
        self.assertIn("The Paperclip", banner)
        # And where to go next, which is the row of download buttons the
        # writing screen now opens on rather than a numbered step below.
        self.assertIn("download buttons at the top", banner)

    def test_the_banner_is_said_once_and_then_stops(self):
        """It describes how the book arrived, which stops being news the moment
        the writer starts changing it."""
        self.generate()
        self.at.text_input(key="bk-title").set_value("Mine now").run()
        banner = "\n".join(str(element.value) for element in self.at.success)
        self.assertNotIn("yours now", banner)

    def test_a_failure_leaves_you_where_you_were_with_what_you_typed(self):
        """Nothing arrived, so there is nothing to move to — and the
        description is still worth having."""
        self.result = ai_book.AIError("nope")
        self.generate(description="a book about hinges")
        self.assertEqual(self.state("route"), AI_ROUTE)
        self.assertEqual(self.box("ai-prompt"), "a book about hinges")
        self.assertTrue(self.at.error)

    def test_the_book_arrives_whichever_screen_it_was_started_from(self):
        self.generate(from_view=CONVERT_VIEW)
        self.assertEqual(self.book.title, "The Paperclip")
        self.assertEqual(self.state("route"), WRITE_VIEW)


class TestKeepingWhatWasThere(AiEditorTestCase):
    def test_a_book_already_saved_is_left_where_it_is(self):
        """Nothing to write, so nothing is written — but it is still findable."""
        self.in_editor()
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
        self.in_editor()
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
        self.in_editor()
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
        self.in_editor()
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
        self.in_editor()
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
        self.in_editor()
        self.at.selectbox(key="bk-page-size").set_value("A4").run()
        chosen = self.book.design.page_size_name

        self.result = written_book()
        self.result.design = Design(page_size_name=chosen)
        self.generate()

        self.assertEqual(self.book.design.page_size_name, chosen)


class TestTheWordsTypedWithTheClick(AiEditorTestCase):
    def test_the_description_typed_in_the_same_message_is_the_one_sent(self):
        """The app's signature bug class, in its newest possible home."""
        self.at.text_area(key="ai-prompt").set_value("a history of glue")
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
        # A failure leaves you where you were — on the AI screen, with the
        # description still in the box — so getting back to the editor is a
        # step the user takes, and so is this test.
        self.in_editor()
        self.at.text_input(key="bk-title").set_value("Carrying on").run()
        self.assertFalse(self.at.exception, self.at.exception)
        self.assertEqual(self.book.title, "Carrying on")

    def test_a_failure_does_not_lose_the_book_that_was_there(self):
        self.in_editor()
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

    def test_the_policy_admits_providers_may_train_on_it(self):
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
        """Beside the button, not under the panel.

        This is the one control in the whole app that sends anything anywhere,
        and the moment to say so is the moment before it is pressed — so it is a
        warning next to the button rather than a caption below the box.
        """
        self.go(AI_ROUTE)
        notice = "\n".join(
            str(element.value)
            for element in list(self.at.warning) + list(self.at.caption)
        )
        self.assertIn("OpenRouter", notice)
        self.assertIn("Nothing else from your session", notice)


if __name__ == "__main__":
    unittest.main(verbosity=2)
