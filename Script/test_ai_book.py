"""Tests for the AI book writer.

Run with:  python -m unittest Script.test_ai_book -v

Nothing here touches the network, and nothing here needs `langchain_openai` to be
installed. `ai_book._make_chat` is the only place a real client is built, so
replacing it with `FakeChat` replaces the whole outside world — which is the
reason it is a function of its own.

The model is treated throughout as something that lies: it sends prose instead of
JSON, wraps objects in code fences, puts real newlines inside strings and reaches
for bullet points it was told not to use. Every one of those is a real failure
from a real free model, and every one has a test.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Script import ai_book, ai_config  # noqa: E402
from Script.manuscript import Design, Manuscript  # noqa: E402

# Assembled rather than written out, so that the literal shape of a key appears
# nowhere in this repository — which is what lets
# `test_no_key_is_sitting_in_the_repository` sweep every file with no exceptions.
FAKE_KEY = "sk-or-" + "v1-" + "0123456789abcdef" * 2


def settings(**changes):
    base = ai_config.Settings(
        api_key=FAKE_KEY,
        model="openrouter/free",
        base_url=ai_config.BASE_URL,
        free_only=True,
        app_title="tests",
        timeout=5.0,
        budget=60.0,
        chapters=5,
        max_calls=3,
    )
    return replace(base, **changes) if changes else base


class FakeChat:
    """A chat model that says exactly what it is told to say.

    `bind` records what it was asked to insist on and returns itself, so one
    object can serve every rung of the ladder and the test can see which rungs
    were tried, in order.

    `stream` says the same thing as `invoke`, in pieces, because that is the only
    difference a streamed reply makes: the same words arrive in more messages. It
    is used exactly when the real one is — when the caller passed an `on_text` —
    and `streamed` counts how often, so a test can prove which way a book went.
    """

    def __init__(self, *replies, chunk=7):
        self.replies = list(replies)
        self.calls = []
        self.bindings = []
        self.chunk = chunk
        self.streamed = 0

    def bind(self, **kwargs):
        self.bindings.append(kwargs)
        return self

    def _next(self, messages):
        self.calls.append(messages)
        if not self.replies:
            raise AssertionError("the model was asked more times than expected")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def invoke(self, messages, **kwargs):
        return SimpleNamespace(content=self._next(messages))

    def stream(self, messages, **kwargs):
        self.streamed += 1
        reply = self._next(messages)
        for start in range(0, len(reply), self.chunk):
            yield SimpleNamespace(content=reply[start : start + self.chunk])


def outline_reply(count=5, **extra):
    data = {
        "title": "The Folded Sheet",
        "subtitle": "A short history",
        "author": "M. Quire",
        "series": "",
        "dedication": "For everyone who ruined their first signature.",
        "style_note": "Warm, plain, present tense.",
        "chapters": [
            {"heading": f"Chapter {n}", "summary": f"What happens in {n}."}
            for n in range(1, count + 1)
        ],
    }
    data.update(extra)
    return json.dumps(data)


def one_chapter(number, paragraphs=None):
    return {
        "number": number,
        "heading": f"Chapter {number}",
        "paragraphs": paragraphs or [f"Chapter {number}, first.", "Second.", "Third."],
    }


def batch_reply(*numbers, **extra):
    """A reply holding the chapters `numbers`, as `write_batch` expects them."""
    return json.dumps({"chapters": [one_chapter(n) for n in numbers], **extra})


def whole_book(chapters=5, batches=(3, 2)):
    """The scripted replies for one complete book: outline, then each batch."""
    replies = [outline_reply(chapters)]
    seen = 0
    for size in batches:
        replies.append(batch_reply(*range(seen + 1, seen + size + 1)))
        seen += size
    return replies


class AiTestCase(unittest.TestCase):
    def setUp(self):
        # The remembered rung is a process-wide cache. Left alone it would leak
        # one test's downgrade into the next test's first request.
        ai_book._RUNG.clear()
        self.addCleanup(ai_book._RUNG.clear)

    def run_book(self, chat, prompt="a book about paper", config=None, **kwargs):
        with mock.patch.object(ai_book, "_make_chat", return_value=chat):
            return ai_book.write_book(prompt, config=config or settings(), **kwargs)

    def tiny(self, *extra_replies, **changes):
        """A one-chapter book: outline, one batch. Two requests, so a test can
        spend the third on a repair or a downgrade without special pleading."""
        chat = FakeChat(outline_reply(1), batch_reply(1), *extra_replies)
        return chat, settings(chapters=1, **changes)


# --------------------------------------------------------------------------


class TestReadingJson(AiTestCase):
    """`extract_json` against every shape a model has actually sent back."""

    def test_a_plain_object(self):
        self.assertEqual(ai_book.extract_json('{"a": 1}'), {"a": 1})

    def test_a_fenced_object(self):
        self.assertEqual(ai_book.extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_a_bare_fence(self):
        self.assertEqual(ai_book.extract_json('```\n{"a": 1}\n```'), {"a": 1})

    def test_prose_before_it(self):
        text = 'Certainly! Here is the JSON you asked for:\n{"a": 1}'
        self.assertEqual(ai_book.extract_json(text), {"a": 1})

    def test_prose_after_it(self):
        self.assertEqual(ai_book.extract_json('{"a": 1}\nHope that helps!'), {"a": 1})

    def test_a_closing_brace_inside_a_string(self):
        """The slice must not stop at a `}` that is part of the prose."""
        text = 'Here: {"a": "a brace } in a sentence", "b": 2} done'
        self.assertEqual(
            ai_book.extract_json(text), {"a": "a brace } in a sentence", "b": 2}
        )

    def test_an_escaped_quote_inside_a_string(self):
        text = 'x {"a": "she said \\"no\\" firmly"} y'
        self.assertEqual(ai_book.extract_json(text), {"a": 'she said "no" firmly'})

    def test_a_real_newline_inside_a_string(self):
        """The commonest failure of all: a literal newline mid-paragraph."""
        text = '{"a": "line one\nline two"}'
        with self.assertRaises(ValueError):
            json.loads(text)
        self.assertEqual(ai_book.extract_json(text), {"a": "line one\nline two"})

    def test_a_trailing_comma(self):
        self.assertEqual(ai_book.extract_json('{"a": 1,}'), {"a": 1})

    def test_two_objects_takes_the_first(self):
        self.assertEqual(ai_book.extract_json('{"a": 1} {"b": 2}'), {"a": 1})

    def test_prose_and_nothing_else(self):
        with self.assertRaises(ai_book.AIError) as caught:
            ai_book.extract_json("I'm sorry, I can't help with that.")
        self.assertIn("did not answer with JSON", str(caught.exception))

    def test_an_empty_reply(self):
        with self.assertRaises(ai_book.AIError):
            ai_book.extract_json("   ")

    def test_a_list_is_not_an_object(self):
        with self.assertRaises(ai_book.AIError):
            ai_book.extract_json("[1, 2, 3]")


class TestKeepingTheKeyOut(AiTestCase):
    """The key must not survive a trip through an error message."""

    def test_the_configured_key_is_removed(self):
        text = ai_book.scrub(f"Authorization: Bearer {FAKE_KEY}", FAKE_KEY)
        self.assertNotIn(FAKE_KEY, text)
        self.assertIn("«api key»", text)

    def test_a_key_shape_is_removed_even_when_it_is_not_ours(self):
        other = "sk-or-" + "v1-" + "f" * 32
        self.assertNotIn(other, ai_book.scrub(f"bad key {other}", FAKE_KEY))

    def test_an_error_carrying_the_key_reaches_the_caller_scrubbed(self):
        chat = FakeChat(RuntimeError(f"401 for {FAKE_KEY}"))
        with self.assertRaises(ai_book.AIError) as caught:
            self.run_book(chat)
        self.assertNotIn(FAKE_KEY, str(caught.exception))

    def test_an_http_failure_is_not_chained_to_the_original(self):
        """`from None`: the original traceback can hold the request."""
        chat = FakeChat(RuntimeError(f"boom {FAKE_KEY}"))
        with self.assertRaises(ai_book.AIError) as caught:
            self.run_book(chat)
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn(FAKE_KEY, repr(caught.exception.__context__ or ""))


class TestReadableFailures(AiTestCase):
    def test_a_bad_key_says_so(self):
        chat = FakeChat(RuntimeError("Error code: 401 - invalid api key"))
        with self.assertRaises(ai_book.AIError) as caught:
            self.run_book(chat)
        self.assertIn("OPENROUTER_API_KEY", str(caught.exception))

    def test_a_rate_limit_says_to_come_back_later(self):
        chat = FakeChat(RuntimeError("429 rate limit exceeded"))
        with self.assertRaises(ai_book.AIError) as caught:
            self.run_book(chat)
        self.assertIn("few minutes", str(caught.exception))

    def test_a_timeout_suggests_a_shorter_book(self):
        chat = FakeChat(RuntimeError("Request timed out."))
        with self.assertRaises(ai_book.AIError) as caught:
            self.run_book(chat)
        self.assertIn("fewer chapters", str(caught.exception))


class TestTheFormatLadder(AiTestCase):
    def test_it_starts_by_asking_for_a_strict_schema(self):
        chat, config = self.tiny()
        self.run_book(chat, config=config)
        self.assertEqual(
            chat.bindings[0]["response_format"]["type"], "json_schema"
        )

    def test_the_schema_pins_the_chapter_count(self):
        """The prompt asks; only this makes the model obey."""
        chat, config = self.tiny()
        self.run_book(chat, config=config)
        chapters = chat.bindings[0]["response_format"]["json_schema"]["schema"][
            "properties"
        ]["chapters"]
        self.assertEqual(chapters["minItems"], 1)
        self.assertEqual(chapters["maxItems"], 1)

    def test_a_model_that_refuses_the_schema_drops_one_rung(self):
        chat, config = self.tiny()
        chat.replies.insert(
            0, RuntimeError("400 - response_format json_schema is not supported")
        )
        self.run_book(chat, config=config)
        self.assertEqual(chat.bindings[0]["response_format"]["type"], "json_schema")
        self.assertEqual(chat.bindings[1]["response_format"]["type"], "json_object")

    def test_the_rung_that_worked_is_remembered(self):
        chat, config = self.tiny()
        chat.replies.insert(0, RuntimeError("response_format not supported"))
        self.run_book(chat, config=config)
        # The chapter request did not repeat the probe.
        self.assertEqual(ai_book._RUNG["openrouter/free"], "json_object")
        self.assertEqual(chat.bindings[2]["response_format"]["type"], "json_object")

    def test_a_rate_limit_does_not_drop_a_rung(self):
        """Only a format complaint downgrades; anything else is reported at once."""
        chat = FakeChat(RuntimeError("429 rate limit"), outline_reply(1))
        with self.assertRaises(ai_book.AIError):
            self.run_book(chat)
        self.assertEqual(len(chat.calls), 1)


class TestRepairingOneBadReply(AiTestCase):
    def test_prose_is_followed_by_one_repair_request(self):
        chat, config = self.tiny()
        chat.replies.insert(0, "Sure, I'd love to help!")
        book = self.run_book(chat, config=config)
        self.assertEqual(book.title, "The Folded Sheet")
        self.assertEqual(len(chat.calls), 3)

    def test_two_bad_replies_give_up_rather_than_loop(self):
        chat = FakeChat("nope", "still nope")
        with self.assertRaises(ai_book.AIError):
            self.run_book(chat)
        self.assertEqual(len(chat.calls), 2)

    def test_a_repair_is_not_attempted_with_no_requests_left(self):
        """The budget outranks the repair: the chapters matter more."""
        chat = FakeChat("nope", "still nope", "and again")
        with self.assertRaises(ai_book.AIError):
            self.run_book(chat, config=settings(max_calls=2))
        self.assertEqual(len(chat.calls), 2)


class TestTidyingTheProse(AiTestCase):
    def test_bullet_points_lose_their_markers(self):
        self.assertEqual(ai_book.clean_text("- a point"), "a point")
        self.assertEqual(ai_book.clean_text("1. a point"), "a point")
        self.assertEqual(ai_book.clean_text("• a point"), "a point")

    def test_links_keep_their_words(self):
        self.assertEqual(
            ai_book.clean_text("See [the guide](http://x.example) now."),
            "See the guide now.",
        )

    def test_backticks_and_headings_go(self):
        self.assertEqual(ai_book.clean_text("## A heading"), "A heading")
        self.assertEqual(ai_book.clean_text("a `word` here"), "a word here")

    def test_italic_and_bold_survive(self):
        self.assertEqual(ai_book.clean_text("*this* and **that**"), "*this* and **that**")

    def test_a_scene_break_survives(self):
        self.assertEqual(ai_book.clean_text("***"), "***")
        self.assertEqual(ai_book.clean_text("* * *"), "***")

    def test_a_quotation_keeps_its_marker(self):
        self.assertEqual(ai_book.clean_text("> a quote"), "> a quote")

    def test_line_breaks_inside_a_paragraph_become_spaces(self):
        self.assertEqual(ai_book.clean_text("one\ntwo"), "one two")


class TestTheDescription(AiTestCase):
    def test_an_empty_description_is_refused(self):
        with self.assertRaises(ai_book.AIError):
            ai_book.clean_prompt("   \n  ")

    def test_a_long_description_is_cut_down(self):
        text = ai_book.clean_prompt("word " * 5000)
        self.assertLessEqual(len(text), ai_book.MAX_PROMPT_CHARS)


class TestRefusingToSpendMoney(AiTestCase):
    def test_a_paid_model_is_refused_before_anything_is_sent(self):
        made = mock.Mock()
        with mock.patch.object(ai_book, "_make_chat", made):
            with self.assertRaises(ai_book.AIError) as caught:
                ai_book.write_book("x", config=settings(model="openai/gpt-4o"))
        self.assertIn("not a free model", str(caught.exception))
        made.assert_not_called()

    def test_the_free_router_is_allowed(self):
        self.assertTrue(settings(model="openrouter/free").is_free)

    def test_a_free_suffix_is_allowed(self):
        self.assertTrue(settings(model="meta-llama/llama-3.3-70b-instruct:free").is_free)

    def test_a_paid_model_is_allowed_when_that_was_asked_for(self):
        chat = FakeChat(outline_reply(1), batch_reply(1))
        with mock.patch.object(ai_book, "_make_chat", return_value=chat):
            book = ai_book.write_book(
                "x",
                config=settings(
                    model="openai/gpt-4o", free_only=False, chapters=1
                ),
            )
        self.assertEqual(book.title, "The Folded Sheet")

    def test_no_key_is_refused(self):
        made = mock.Mock()
        with mock.patch.object(ai_book, "_make_chat", made):
            with self.assertRaises(ai_book.AIError):
                ai_book.write_book("x", config=settings(api_key=""))
        made.assert_not_called()


class TestBuildingTheBook(AiTestCase):
    def test_a_whole_book_arrives_in_three_requests(self):
        chat = FakeChat(*whole_book())
        book = self.run_book(chat)
        self.assertIsInstance(book, Manuscript)
        self.assertEqual(book.title, "The Folded Sheet")
        self.assertEqual(book.author, "M. Quire")
        self.assertEqual(len(book.chapters), 5)
        self.assertEqual(len(chat.calls), 3)

    def test_the_chapters_are_in_the_order_they_were_planned(self):
        chat = FakeChat(*whole_book())
        book = self.run_book(chat)
        self.assertEqual(
            [section.heading for section in book.body],
            [f"Chapter {n}" for n in range(1, 6)],
        )

    def test_a_batch_that_comes_back_out_of_order_is_put_right(self):
        """Chapters are placed by the number they carry, not by position."""
        chat = FakeChat(
            outline_reply(5),
            json.dumps({"chapters": [one_chapter(3), one_chapter(1), one_chapter(2)]}),
            batch_reply(4, 5),
        )
        book = self.run_book(chat)
        self.assertEqual(
            [section.heading for section in book.body],
            [f"Chapter {n}" for n in range(1, 6)],
        )

    def test_a_batch_with_no_numbers_falls_back_to_position(self):
        without = [
            {k: v for k, v in one_chapter(n).items() if k != "number"}
            for n in (1, 2, 3)
        ]
        chat = FakeChat(
            outline_reply(5), json.dumps({"chapters": without}), batch_reply(4, 5)
        )
        book = self.run_book(chat)
        self.assertEqual(len(book.chapters), 5)

    def test_the_dedication_lands_in_the_front_matter(self):
        chat, config = self.tiny()
        book = self.run_book(chat, config=config)
        self.assertEqual([s.kind for s in book.front], ["dedication"])

    def test_no_dedication_means_no_front_matter(self):
        chat = FakeChat(outline_reply(1, dedication=""), batch_reply(1))
        book = self.run_book(chat, config=settings(chapters=1))
        self.assertEqual(book.front, [])

    def test_paragraphs_are_joined_the_way_the_editor_stores_them(self):
        chat = FakeChat(
            outline_reply(1),
            json.dumps({"chapters": [one_chapter(1, ["One.", "Two.", "Three."])]}),
        )
        book = self.run_book(chat, config=settings(chapters=1))
        self.assertEqual(book.body[0].text, "One.\n\nTwo.\n\nThree.")

    def test_every_section_gets_its_own_id(self):
        chat = FakeChat(*whole_book())
        book = self.run_book(chat)
        ids = [s.id for s in book.sections]
        self.assertTrue(all(ids))
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_book_survives_being_saved_and_reopened(self):
        chat = FakeChat(*whole_book())
        book = self.run_book(chat)
        again = Manuscript.from_json(book.to_json())
        self.assertEqual(again.to_dict(), book.to_dict())

    def test_a_chapter_sent_as_one_blob_is_still_split(self):
        chat = FakeChat(
            outline_reply(1),
            json.dumps(
                {"chapters": [{"number": 1, "paragraphs": "First.\n\nSecond."}]}
            ),
        )
        book = self.run_book(chat, config=settings(chapters=1))
        self.assertEqual(book.body[0].text, "First.\n\nSecond.")

    def test_a_chapter_with_no_heading_gets_a_numbered_one(self):
        chat = FakeChat(
            outline_reply(1, chapters=[{"heading": "", "summary": "s"}]),
            json.dumps({"chapters": [{"number": 1, "paragraphs": ["Text."]}]}),
        )
        book = self.run_book(chat, config=settings(chapters=1))
        self.assertEqual(book.body[0].heading, "Chapter 1")

    def test_an_outline_with_no_chapters_is_refused(self):
        chat = FakeChat(json.dumps({"title": "t", "author": "a", "chapters": []}))
        with self.assertRaises(ai_book.AIError) as caught:
            self.run_book(chat)
        self.assertIn("did not plan any chapters", str(caught.exception))

    def test_a_book_with_no_usable_chapters_is_refused(self):
        chat = FakeChat(
            outline_reply(1),
            json.dumps({"chapters": [{"number": 1, "paragraphs": ["", "  "]}]}),
        )
        with self.assertRaises(ai_book.AIError) as caught:
            self.run_book(chat, config=settings(chapters=1))
        self.assertIn("no usable chapters", str(caught.exception))


class TestTheDesignIsLeftAlone(AiTestCase):
    def test_the_editors_design_is_carried_across(self):
        mine = Design(page_size_name="A4", font_key="helvetica", font_size_pt=12.0)
        chat, config = self.tiny()
        with mock.patch.object(ai_book, "_make_chat", return_value=chat):
            book = ai_book.write_book("x", design=mine, config=config)
        self.assertEqual(book.design.page_size_name, "A4")
        self.assertEqual(book.design.font_key, "helvetica")
        self.assertEqual(book.design.font_size_pt, 12.0)

    def test_the_design_is_copied_not_shared(self):
        """Editing the new book's design must not reach the old one."""
        mine = Design(page_size_name="A4")
        chat, config = self.tiny()
        with mock.patch.object(ai_book, "_make_chat", return_value=chat):
            book = ai_book.write_book("x", design=mine, config=config)
        book.design.page_size_name = "A6"
        self.assertEqual(mine.page_size_name, "A4")

    def test_without_a_design_the_defaults_are_used(self):
        chat, config = self.tiny()
        book = self.run_book(chat, config=config)
        self.assertEqual(book.design.page_size_name, Design().page_size_name)


class TestProgress(AiTestCase):
    def test_it_counts_up_and_finishes(self):
        seen = []
        chat = FakeChat(*whole_book())
        self.run_book(chat, progress=lambda f, m: seen.append((f, m)))
        fractions = [f for f, _ in seen]
        self.assertEqual(fractions, sorted(fractions))
        self.assertGreaterEqual(fractions[0], 0.0)
        self.assertEqual(fractions[-1], 1.0)
        self.assertTrue(any("chapters 1–3 of 5" in m for _, m in seen))
        self.assertTrue(any("chapters 4–5 of 5" in m for _, m in seen))

    def test_a_single_chapter_batch_is_not_called_a_range(self):
        seen = []
        chat, config = self.tiny()
        self.run_book(chat, config=config, progress=lambda f, m: seen.append((f, m)))
        self.assertTrue(any("chapter 1 of 1…" in m for _, m in seen))

    def test_a_progress_callback_that_raises_is_not_swallowed(self):
        """The app's callback enforces the session disk quota by raising."""

        class Quota(Exception):
            pass

        def boom(fraction, message):
            raise Quota()

        chat, config = self.tiny()
        with self.assertRaises(Quota):
            self.run_book(chat, config=config, progress=boom)


class TestTheTimeBudget(AiTestCase):
    def test_a_slow_book_is_stopped_and_says_where(self):
        chat = FakeChat(*whole_book())
        clock = iter([0.0, 0.0, 999.0, 1000.0, 1001.0])
        with mock.patch.object(ai_book.time, "monotonic", lambda: next(clock)):
            with self.assertRaises(ai_book.AIError) as caught:
                self.run_book(chat)
        self.assertIn("of 5 chapters", str(caught.exception))


class TestTheRequestBudget(AiTestCase):
    """Three requests a book, because the free tier allows about fifty a day."""

    def test_five_chapters_cost_exactly_three_requests(self):
        chat = FakeChat(*whole_book())
        self.run_book(chat)
        self.assertEqual(len(chat.calls), 3)

    def test_the_chapters_are_split_over_the_requests_that_are_left(self):
        chat = FakeChat(*whole_book())
        self.run_book(chat)
        # Batch one asks for chapters 1-3, batch two for 4-5.
        first, second = chat.calls[1][1][1], chat.calls[2][1][1]
        self.assertIn("Write these 3 of them now", first)
        self.assertIn("Write these 2 of them now", second)

    def test_a_bigger_allowance_is_used_as_more_batches(self):
        chat = FakeChat(outline_reply(5), *[batch_reply(1, 2)] * 3)
        self.run_book(chat, config=settings(max_calls=4))
        self.assertEqual(len(chat.calls), 4)

    def test_the_split_is_even_and_front_loaded(self):
        self.assertEqual(ai_book.split_batches(5, 2), [3, 2])
        self.assertEqual(ai_book.split_batches(5, 3), [2, 2, 1])
        self.assertEqual(ai_book.split_batches(4, 2), [2, 2])
        self.assertEqual(ai_book.split_batches(1, 2), [1])
        self.assertEqual(ai_book.split_batches(5, 1), [5])

    def test_running_out_of_requests_says_so_plainly(self):
        budget = ai_book._Budget(1)
        budget.take()
        with self.assertRaises(ai_book.AIError) as caught:
            budget.take()
        self.assertIn("rationed by the request", str(caught.exception))

    def test_a_format_probe_that_costs_the_book_says_to_press_again(self):
        """Self-healing, and worth saying rather than looking simply broken."""
        budget = ai_book._Budget(1)
        budget.probed = True
        budget.take()
        with self.assertRaises(ai_book.AIError) as caught:
            budget.take()
        self.assertIn("press the button again", str(caught.exception))


class TestExactlyFiveChapters(AiTestCase):
    """The count is fixed, and fixed twice: in the schema and again here.

    The bug this guards: a description saying "a short novel" came back with ten
    chapters, where one asking for a full novel had given five. A model reads a
    length word as tone, so the number cannot be left to the prompt.
    """

    def test_a_model_that_plans_too_many_is_trimmed(self):
        chat = FakeChat(outline_reply(12), batch_reply(1, 2, 3), batch_reply(4, 5))
        book = self.run_book(chat, "a short novel")
        self.assertEqual(len(book.chapters), 5)

    def test_a_model_that_plans_too_few_is_padded(self):
        chat = FakeChat(outline_reply(2), batch_reply(1, 2, 3), batch_reply(4, 5))
        book = self.run_book(chat, "an epic")
        self.assertEqual(len(book.chapters), 5)

    def test_the_padded_chapters_still_get_headings(self):
        chat = FakeChat(outline_reply(2), batch_reply(1, 2, 3), batch_reply(4, 5))
        book = self.run_book(chat)
        self.assertTrue(all(section.heading for section in book.body))

    def test_the_outline_request_says_the_number_in_words_too(self):
        chat = FakeChat(*whole_book())
        self.run_book(chat)
        system = chat.calls[0][0][1]
        self.assertIn("exactly 5 entries", system)
        self.assertIn("Not more, not fewer", system)

    def test_the_number_follows_the_setting(self):
        chat = FakeChat(outline_reply(3), batch_reply(1, 2), batch_reply(3))
        book = self.run_book(chat, config=settings(chapters=3))
        self.assertEqual(len(book.chapters), 3)

    def test_the_button_label_and_the_schema_agree(self):
        with mock.patch.dict(
            os.environ, {ai_config.CHAPTERS_VAR: "7"}, clear=False
        ):
            self.assertEqual(ai_book.chapter_count(), 7)
            schema = ai_book.outline_schema(ai_book.chapter_count())
        self.assertEqual(schema["properties"]["chapters"]["minItems"], 7)


class TestRescuingATruncatedBatch(AiTestCase):
    """A reply that ran out is three good chapters, not a failure."""

    def test_whole_chapters_are_recovered_from_a_cut_off_reply(self):
        good = json.dumps({"chapters": [one_chapter(1), one_chapter(2)]})
        truncated = good[: good.rindex("]")] + ', {"number": 3, "paragraphs": ["Half'
        rescued = ai_book.salvage_chapters(truncated)
        self.assertEqual(len(rescued["chapters"]), 2)

    def test_nothing_to_rescue_returns_none(self):
        self.assertIsNone(ai_book.salvage_chapters("I'm sorry, I can't."))
        self.assertIsNone(ai_book.salvage_chapters('{"chapters": ['))

    def test_a_truncated_batch_does_not_cost_another_request(self):
        good = json.dumps({"chapters": [one_chapter(1), one_chapter(2)]})
        truncated = good[: good.rindex("]")] + ', {"number": 3, "paragraphs": ["Ha'
        chat = FakeChat(outline_reply(5), truncated, batch_reply(4, 5))
        book = self.run_book(chat)
        self.assertEqual(len(chat.calls), 3)
        # Chapter three was lost with the truncation; the rest survived.
        self.assertEqual(len(book.chapters), 4)

    def test_objects_are_found_at_every_depth(self):
        found = ai_book._every_object('{"a": {"b": 1}, "c": {"d": 2}}')
        self.assertEqual(len(found), 3)

    def test_a_brace_inside_a_string_does_not_confuse_it(self):
        found = ai_book._every_object('{"a": "not } a brace"}')
        self.assertEqual(found, ['{"a": "not } a brace"}'])


class TestReadingAHalfArrivedReply(AiTestCase):
    """`stream_prose`, which has to read JSON that is not JSON yet.

    Every fragment below is a real prefix of a real reply — the string a token
    stream is holding part way through — so none of them can be parsed, and that
    is the point of the code being tested.
    """

    def test_the_words_are_read_out_of_a_finished_object(self):
        prose = ai_book.stream_prose(batch_reply(1))
        self.assertIn("Chapter 1, first.", prose)
        self.assertIn("Second.", prose)

    def test_a_sentence_that_is_still_arriving_is_shown(self):
        fragment = '{"chapters": [{"number": 1, "paragraphs": ["The wire bends sl'
        self.assertIn("The wire bends sl", ai_book.stream_prose(fragment))

    def test_keys_are_not_mistaken_for_words(self):
        prose = ai_book.stream_prose('{"heading": "Bent Wire", "paragraphs": ["It')
        self.assertNotIn("paragraphs", prose)
        self.assertNotIn("heading", prose)

    def test_a_heading_is_marked_and_a_paragraph_is_not(self):
        prose = ai_book.stream_prose('{"heading": "Bent Wire", "paragraphs": ["It"]}')
        self.assertIn("**Bent Wire**", prose)
        self.assertIn("\n\nIt", prose)

    def test_an_unfinished_key_shows_nothing(self):
        # Half of `"heading"` is not half of a word of the book.
        self.assertEqual(ai_book.stream_prose('{"chapters": [{"head'), "")

    def test_escapes_are_undone(self):
        fragment = '{"paragraphs": ["She said \\"no\\" \\u2014 twice'
        prose = ai_book.stream_prose(fragment)
        self.assertIn('She said "no" — twice', prose)

    def test_an_escape_cut_in_half_does_not_break_it(self):
        # The stream stopped in the middle of `—`. The rest of the sentence
        # still has to appear.
        prose = ai_book.stream_prose('{"paragraphs": ["Folded and sewn \\u20')
        self.assertIn("Folded and sewn", prose)

    def test_a_brace_inside_a_sentence_is_just_a_brace(self):
        prose = ai_book.stream_prose('{"paragraphs": ["A } and an { in the prose"]}')
        self.assertIn("A } and an { in the prose", prose)

    def test_the_style_note_is_not_shown(self):
        prose = ai_book.stream_prose(outline_reply(1))
        self.assertIn("The Folded Sheet", prose)
        self.assertNotIn("present tense", prose)

    def test_nothing_yet_is_no_words_rather_than_a_failure(self):
        for fragment in ("", "{", '{"chapters": ['):
            self.assertEqual(ai_book.stream_prose(fragment), "")


class TestWatchingTheBookBeingWritten(AiTestCase):
    """The whole reason for streaming: words on the page before the book is done."""

    def run_watched(self, chat, **kwargs):
        seen = []
        book = self.run_book(chat, on_text=seen.append, **kwargs)
        return book, seen

    def test_the_words_arrive_before_the_book_does(self):
        chat = FakeChat(*whole_book())
        book, seen = self.run_watched(chat)
        self.assertEqual(len(book.chapters), 5)
        # Many updates, not one at the end, and the last of them holds the book.
        self.assertGreater(len(seen), 10)
        self.assertIn("Chapter 5, first.", seen[-1])

    def test_it_only_streams_when_somebody_is_watching(self):
        chat = FakeChat(*whole_book())
        self.run_book(chat)
        self.assertEqual(chat.streamed, 0)

    def test_every_request_of_a_watched_book_is_streamed(self):
        chat = FakeChat(*whole_book())
        self.run_watched(chat)
        self.assertEqual(chat.streamed, 3)

    def test_a_streamed_book_is_the_same_book(self):
        """Streaming is how the reply arrives, not a different reply."""

        def prose(book):
            # Ids are minted fresh for every book and are the one thing that
            # cannot match.
            return [(chapter.heading, chapter.text) for chapter in book.chapters]

        plain = self.run_book(FakeChat(*whole_book()))
        streamed, _ = self.run_watched(FakeChat(*whole_book()))
        self.assertEqual(prose(plain), prose(streamed))

    def test_chapters_already_written_stay_on_screen(self):
        """The second batch is added under the first, not over it."""
        chat = FakeChat(*whole_book())
        _, seen = self.run_watched(chat)
        self.assertIn("Chapter 1, first.", seen[-1])
        self.assertIn("Chapter 5, first.", seen[-1])

    def test_the_outline_is_shown_and_then_makes_way_for_the_book(self):
        chat = FakeChat(*whole_book())
        _, seen = self.run_watched(chat)
        self.assertTrue(any("The Folded Sheet" in text for text in seen))
        # The plan is not part of the book, so it is gone by the end.
        self.assertNotIn("The Folded Sheet", seen[-1])

    def test_a_reply_that_had_to_be_repaired_leaves_no_wreckage(self):
        # Outline, prose instead of chapters, then the repaired chapters.
        chat = FakeChat(outline_reply(1), "I am afraid I cannot.", batch_reply(1))
        _, seen = self.run_watched(chat, config=settings(chapters=1))
        self.assertNotIn("I am afraid", seen[-1])
        self.assertIn("Chapter 1, first.", seen[-1])

    def test_a_display_that_breaks_does_not_lose_the_book(self):
        """A drawing failure costs the words on screen, never the chapters."""

        def broken(_text):
            raise RuntimeError("the browser went away")

        book = self.run_book(FakeChat(*whole_book()), on_text=broken)
        self.assertEqual(len(book.chapters), 5)

    def test_streaming_can_be_switched_off_without_switching_off_the_book(self):
        chat = FakeChat(*whole_book())
        book = self.run_book(chat, config=settings(stream=False), on_text=lambda _: None)
        self.assertEqual(chat.streamed, 0)
        self.assertEqual(len(book.chapters), 5)

    def test_a_truncated_batch_is_kept_on_screen_as_it_is_kept_in_the_book(self):
        good = json.dumps({"chapters": [one_chapter(1), one_chapter(2)]})
        truncated = good[: good.rindex("]")] + ', {"number": 3, "paragraphs": ["Ha'
        chat = FakeChat(outline_reply(5), truncated, batch_reply(4, 5))
        book, seen = self.run_watched(chat)
        self.assertEqual(len(book.chapters), 4)
        self.assertIn("Chapter 1, first.", seen[-1])


class TestAskingForTheFastestProvider(AiTestCase):
    """The `provider` block, which is the other half of not waiting."""

    def built_with(self, config):
        made = {}

        class Recorder:
            def __init__(self, **kwargs):
                made.update(kwargs)

        with mock.patch.dict(
            "sys.modules",
            {"langchain_openai": SimpleNamespace(ChatOpenAI=Recorder)},
        ):
            ai_book._make_chat(config)
        return made

    def test_throughput_is_asked_for_by_default(self):
        made = self.built_with(settings())
        self.assertEqual(
            made["extra_body"], {"provider": {"sort": "throughput"}}
        )

    def test_it_goes_in_extra_body_and_not_in_the_model_parameters(self):
        """`model_kwargs` sets OpenAI's own fields; `provider` is not one."""
        made = self.built_with(settings())
        self.assertNotIn("provider", made.get("model_kwargs") or {})

    def test_another_sort_is_sent_as_asked(self):
        made = self.built_with(settings(sort="latency"))
        self.assertEqual(made["extra_body"], {"provider": {"sort": "latency"}})

    def test_switching_it_off_sends_no_provider_block_at_all(self):
        made = self.built_with(settings(sort=""))
        self.assertIsNone(made["extra_body"])

    def test_the_key_is_still_the_only_thing_that_carries_the_key(self):
        made = self.built_with(settings())
        self.assertNotIn(FAKE_KEY, str(made["extra_body"]))


class TestSwitchedOff(AiTestCase):
    def test_no_key_means_not_available(self):
        with mock.patch.object(ai_config, "configured", return_value=False):
            self.assertFalse(ai_book.available())
            self.assertIn("OPENROUTER_API_KEY", ai_book.why_unavailable())

    def test_no_langchain_means_not_available(self):
        with mock.patch.object(ai_config, "configured", return_value=True):
            with mock.patch.object(ai_book, "_has_langchain", return_value=False):
                self.assertFalse(ai_book.available())
                self.assertIn("langchain-openai", ai_book.why_unavailable())

    def test_available_does_not_import_langchain_or_make_a_request(self):
        made = mock.Mock()
        with mock.patch.object(ai_book, "_make_chat", made):
            ai_book.available()
        made.assert_not_called()


class TestWhereTheKeyComesFrom(AiTestCase):
    """`ai_config`, and the one rule the hosted copy depends on."""

    def setUp(self):
        super().setUp()
        self.folder = Path(tempfile.mkdtemp(prefix="ai-config-test-"))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        for name in (
            ai_config.KEY_VAR,
            ai_config.MODEL_VAR,
            ai_config.FREE_ONLY_VAR,
            ai_config.CHAPTERS_VAR,
            ai_config.MAX_CALLS_VAR,
            ai_config.TIMEOUT_VAR,
            ai_config.SORT_VAR,
            ai_config.STREAM_VAR,
        ):
            self.addCleanup(os.environ.pop, name, None)
            os.environ.pop(name, None)

    def env_file(self, text):
        path = self.folder / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_dotenv_file_is_read(self):
        ai_config.load_env(self.env_file(f"{ai_config.KEY_VAR}=from-the-file\n"))
        self.assertEqual(ai_config.settings().api_key, "from-the-file")

    def test_a_real_environment_variable_beats_the_file(self):
        """The whole reason a stray `.env` cannot endanger the hosted copy.

        On Render the key arrives as a genuine environment variable. If a `.env`
        ever reached that image it must not be able to replace it, so `load_env`
        passes `override=False`.
        """
        os.environ[ai_config.KEY_VAR] = "from-the-environment"
        ai_config.load_env(self.env_file(f"{ai_config.KEY_VAR}=from-the-file\n"))
        self.assertEqual(ai_config.settings().api_key, "from-the-environment")

    def test_no_file_is_not_an_error(self):
        self.assertFalse(ai_config.load_env(self.folder / "nothing-here"))

    def test_the_default_model_is_the_free_router(self):
        self.assertEqual(ai_config.settings().model, "openrouter/free")
        self.assertTrue(ai_config.settings().is_free)

    def test_free_only_is_on_unless_it_is_turned_off(self):
        self.assertTrue(ai_config.settings().free_only)
        os.environ[ai_config.FREE_ONLY_VAR] = "0"
        self.assertFalse(ai_config.settings().free_only)

    def test_a_nonsense_number_falls_back_instead_of_crashing(self):
        """A typo in a hosted environment variable must not take the app down."""
        os.environ[ai_config.CHAPTERS_VAR] = "lots"
        os.environ[ai_config.TIMEOUT_VAR] = "-4"
        settings_now = ai_config.settings()
        self.assertEqual(settings_now.chapters, ai_config.DEFAULT_CHAPTERS)
        self.assertEqual(settings_now.timeout, ai_config.DEFAULT_TIMEOUT)

    def test_the_defaults_are_five_chapters_in_three_requests(self):
        now = ai_config.settings()
        self.assertEqual(now.chapters, 5)
        self.assertEqual(now.max_calls, 3)
        self.assertEqual(now.batches, 2)

    def test_the_defaults_are_the_fast_ones(self):
        now = ai_config.settings()
        self.assertEqual(now.sort, "throughput")
        self.assertEqual(now.provider, {"sort": "throughput"})
        self.assertTrue(now.stream)

    def test_another_ordering_can_be_asked_for(self):
        os.environ[ai_config.SORT_VAR] = "price"
        self.assertEqual(ai_config.settings().provider, {"sort": "price"})

    def test_a_sort_openrouter_would_refuse_falls_back_to_the_default(self):
        """A typo here would otherwise turn every request into a 400."""
        os.environ[ai_config.SORT_VAR] = "fastest"
        self.assertEqual(ai_config.settings().sort, ai_config.DEFAULT_SORT)

    def test_the_routing_can_be_left_entirely_to_openrouter(self):
        os.environ[ai_config.SORT_VAR] = "off"
        self.assertIsNone(ai_config.settings().provider)

    def test_streaming_can_be_switched_off(self):
        os.environ[ai_config.STREAM_VAR] = "0"
        self.assertFalse(ai_config.settings().stream)

    def test_configured_follows_the_key(self):
        self.assertFalse(ai_config.configured())
        os.environ[ai_config.KEY_VAR] = "something"
        self.assertTrue(ai_config.configured())

    def test_whitespace_is_not_a_key(self):
        os.environ[ai_config.KEY_VAR] = "   "
        self.assertFalse(ai_config.configured())


class TestTheKeyCannotBeCommittedOrShipped(unittest.TestCase):
    """The two ignore files, guarded so a later edit cannot quietly drop them.

    Neither `git` nor `docker` can be run from the test suite, so these check the
    rules are written rather than that the tools obey them — which they do. The
    commands that prove the rest are in the README.
    """

    def rules(self, name):
        path = ai_config.ROOT_DIR / name
        self.assertTrue(path.is_file(), f"{name} is missing")
        return [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def test_git_ignores_every_shape_of_env_file(self):
        rules = self.rules(".gitignore")
        for pattern in (".env", ".env.*", "!.env.example"):
            self.assertIn(pattern, rules)

    def test_docker_ignores_every_shape_of_env_file(self):
        rules = self.rules(".dockerignore")
        for pattern in (".env", ".env.*", ".git"):
            self.assertIn(pattern, rules)

    def test_the_example_holds_no_key(self):
        """`.env.example` is the one env file that is committed on purpose.

        It names the setting and leaves it empty. It may show the *shape* of a
        key in a comment, which is why this looks for a real one rather than for
        the prefix.
        """
        text = (ai_config.ROOT_DIR / ".env.example").read_text(encoding="utf-8")
        self.assertIn(f"{ai_config.KEY_VAR}=\n", text)
        self.assertIsNone(ai_book._KEY_SHAPE.search(text))

    def test_no_key_is_sitting_in_the_repository(self):
        """A last sweep of everything that would be committed."""
        for path in ai_config.ROOT_DIR.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            if path.suffix.lower() not in {".py", ".toml", ".yml", ".yaml", ".md", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            found = ai_book._KEY_SHAPE.search(text)
            self.assertIsNone(found, f"something key-shaped in {path}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
