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
import random
import re
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

# The model the app is set to, read from the settings rather than written out
# again, so changing it in one place does not leave a row of tests asserting the
# old name.
DEFAULT_MODEL = ai_config.DEFAULT_MODEL

# And the token ceiling, for the same reason and more of it: forty assertions
# below are "not one token over", and a literal in every one of them is forty
# places to miss when the ceiling moves.
LIMIT = ai_config.DEFAULT_TOKEN_LIMIT


def settings(**changes):
    base = ai_config.Settings(
        api_key=FAKE_KEY,
        model=DEFAULT_MODEL,
        base_url=ai_config.BASE_URL,
        # Off, as it is in the app: the default model is a paid one. The tests
        # that care about the guard switch it on themselves.
        free_only=False,
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


def cut_off_error(text="", total=0, name="LengthFinishReasonError"):
    """What `openai` raises instead of handing over a reply that filled its cap.

    Built here rather than imported, for the same reason `ai_book` recognises one
    by class name and by message instead of catching it: neither the module nor
    these tests may depend on the client being installed.

    The shape is the real one — the completion it would not parse hangs off the
    exception, with the words in `choices[0].message.content` and the service's
    own count in `usage` — because that shape is the only reason the chapters can
    be got back at all.
    """
    usage = SimpleNamespace(total_tokens=total) if total else None
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=usage,
    )
    error = type(name, (Exception,), {})(
        "Could not parse response content as the length limit was reached"
        + (f" - CompletionUsage(total_tokens={total})" if total else "")
    )
    error.completion = completion
    return error


class CutShort(str):
    """A reply that arrives in full and then runs out at the end of it.

    A plain `str`, so `FakeChat` sends it like any other reply. The marker is
    only there to tell `StreamCutOffChat` to raise once it has been said.
    """

    total = 0


class StreamCutOffChat(FakeChat):
    """A model that streams every word and then refuses to hand the reply over.

    Which is precisely what happens in the app. The client reads the stream to
    the end, parses the finished completion only then, finds `finish_reason` is
    "length", and raises — so the words have already arrived and already been
    shown by the time the exception lands on top of them.
    """

    def stream(self, messages, **kwargs):
        self.streamed += 1
        reply = self._next(messages)
        for start in range(0, len(reply), self.chunk):
            yield SimpleNamespace(content=reply[start : start + self.chunk])
        if isinstance(reply, CutShort):
            raise cut_off_error(reply, total=reply.total)


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
        self.assertEqual(ai_book._RUNG[DEFAULT_MODEL], "json_object")
        self.assertEqual(chat.bindings[2]["response_format"]["type"], "json_object")

    def test_a_model_that_refuses_every_rung_gives_a_book_not_an_error(self):
        """The bottom of the ladder is a shorter book, like every other limit here.

        Three refusals is a stubborn model, not something the reader can act on,
        and the plan can write the chapters for nothing. This used to raise —
        rarely, because a book usually ran out of tokens before it could refuse
        three times, which is exactly the sort of bug a bigger budget uncovers.
        """
        refusal = RuntimeError("400 response_format json_schema is unsupported")
        chat = FakeChat(*[refusal] * 9)
        book = self.run_book(chat, config=settings(max_calls=9))
        self.assertEqual(len(book.chapters), 5)
        self.assertTrue(all(chapter.text.strip() for chapter in book.chapters))

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

    def test_two_bad_replies_move_on_rather_than_loop(self):
        """One repair per question, and then the book carries on without it."""
        chat = FakeChat("nope", "still nope", batch_reply(1))
        book = self.run_book(chat, config=settings(chapters=1))
        # Outline, its one repair, then straight on to the chapter — not a third
        # attempt at the outline.
        self.assertEqual(len(chat.calls), 3)
        self.assertEqual(len(book.chapters), 1)

    def test_a_repair_is_not_attempted_with_no_requests_left(self):
        """The budget outranks the repair: the chapters matter more."""
        chat = FakeChat("nope", "still nope", "and again")
        book = self.run_book(chat, config=settings(max_calls=2, chapters=1))
        # Two requests: the bad outline and the one chapter request. The repair
        # was skipped rather than eating the chapter's turn.
        self.assertEqual(len(chat.calls), 2)
        self.assertEqual(len(book.chapters), 1)


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
    """`OPENROUTER_FREE_ONLY`, which is off by default and still has to work.

    The app's own model is a paid one — a fifth of a penny for ten books, but
    paid — so the guard no longer stands between an ordinary copy and its first
    request. It stands between a copy that has deliberately been told it may not
    spend anything and a model that would.
    """

    def test_a_paid_model_is_refused_when_the_guard_is_on(self):
        made = mock.Mock()
        with mock.patch.object(ai_book, "_make_chat", made):
            with self.assertRaises(ai_book.AIError) as caught:
                ai_book.write_book(
                    "x", config=settings(model="openai/gpt-4o", free_only=True)
                )
        self.assertIn("not a free model", str(caught.exception))
        made.assert_not_called()

    def test_the_message_names_a_model_that_would_be_allowed(self):
        """Not the default: on a free-only copy that is the one thing it cannot
        be set to."""
        with mock.patch.object(ai_book, "_make_chat", mock.Mock()):
            with self.assertRaises(ai_book.AIError) as caught:
                ai_book.write_book(
                    "x", config=settings(model="openai/gpt-4o", free_only=True)
                )
        said = str(caught.exception)
        self.assertIn(ai_config.FREE_MODEL, said)
        self.assertNotIn(DEFAULT_MODEL, said)

    def test_the_app_default_is_a_paid_model_and_says_so(self):
        """The guard would be worthless if the default counted itself free."""
        self.assertFalse(settings().is_free)

    def test_the_default_is_usable_out_of_the_box(self):
        chat = FakeChat(outline_reply(1), batch_reply(1))
        with mock.patch.object(ai_book, "_make_chat", return_value=chat):
            book = ai_book.write_book("x", config=settings(chapters=1))
        self.assertEqual(book.title, "The Folded Sheet")

    def test_the_free_router_is_allowed(self):
        self.assertTrue(settings(model=ai_config.FREE_MODEL).is_free)

    def test_a_free_suffix_is_allowed(self):
        self.assertTrue(settings(model="meta-llama/llama-3.3-70b-instruct:free").is_free)

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

    def test_an_outline_with_no_chapters_still_gives_a_book(self):
        """A plan of nothing is not a failure. It is plain chapter headings."""
        chat = FakeChat(
            json.dumps({"title": "t", "author": "a", "chapters": []}),
            batch_reply(1),
        )
        book = self.run_book(chat, config=settings(chapters=1))
        self.assertEqual(len(book.chapters), 1)
        self.assertEqual(book.chapters[0].heading, "Chapter 1")

    def test_a_batch_with_nothing_usable_in_it_still_gives_a_book(self):
        chat = FakeChat(
            outline_reply(1),
            json.dumps({"chapters": [{"number": 1, "paragraphs": ["", "  "]}]}),
        )
        book = self.run_book(chat, config=settings(chapters=1))
        # Filled from the plan rather than refused: the summary the outline gave
        # that chapter, in an editor, waiting to be written over.
        self.assertEqual(len(book.chapters), 1)
        self.assertEqual(book.chapters[0].text, "What happens in 1.")


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
    def test_a_slow_book_stops_asking_and_keeps_what_it_has(self):
        """Out of time is not out of book: the rest comes from the plan."""
        chat = FakeChat(*whole_book())
        clock = iter([0.0, 0.0, 999.0, 1000.0, 1001.0])
        with mock.patch.object(ai_book.time, "monotonic", lambda: next(clock)):
            book = self.run_book(chat)
        # The clock ran out between the two batches. Chapters 1-3 were written;
        # 4 and 5 come from their own summaries — and there are still five.
        self.assertEqual(len(book.chapters), 5)
        self.assertEqual(book.chapters[0].text.split("\n")[0], "Chapter 1, first.")
        self.assertEqual(book.chapters[4].text, "What happens in 5.")
        self.assertEqual(len(chat.calls), 2)


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
        self.assertIn("Write chapters 1, 2, 3 now", first)
        self.assertIn("Write chapters 4, 5 now", second)

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

    def test_running_out_of_requests_says_no_rather_than_raising(self):
        """A budget that raises is a budget that can break a book."""
        budget = ai_book._Budget(1)
        self.assertTrue(budget.take())
        self.assertFalse(budget.take())
        self.assertFalse(budget.take())

    def test_a_format_probe_that_costs_a_request_still_gives_a_book(self):
        """The failure this used to be an error message about.

        A model that spends a request working out which JSON format it accepts
        leaves one fewer for the chapters. That used to end the book with a
        banner asking the reader to press the button again. It now ends with a
        book, short of the chapters the missing request would have written.
        """
        chat = FakeChat(
            RuntimeError("400 - response_format json_schema is not supported"),
            outline_reply(5),
            batch_reply(1, 2, 3),
        )
        book = self.run_book(chat, config=settings(max_calls=3))
        self.assertEqual(len(book.chapters), 5)
        self.assertEqual(book.chapters[0].text.split("\n")[0], "Chapter 1, first.")
        # The probe cost the second batch, so 4 and 5 come from the plan.
        self.assertEqual(book.chapters[4].text, "What happens in 5.")


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
        # Chapter three was lost with the truncation. Four came back written and
        # the fifth entry is chapter three, filled from its own summary — so the
        # book is still the five the button promised.
        self.assertEqual(len(book.chapters), 5)
        self.assertEqual(book.chapters[2].text, "What happens in 3.")
        self.assertEqual(book.chapters[3].text.split("\n")[0], "Chapter 4, first.")

    def test_objects_are_found_at_every_depth(self):
        found = ai_book._every_object('{"a": {"b": 1}, "c": {"d": 2}}')
        self.assertEqual(len(found), 3)

    def test_a_brace_inside_a_string_does_not_confuse_it(self):
        found = ai_book._every_object('{"a": "not } a brace"}')
        self.assertEqual(found, ['{"a": "not } a brace"}'])


class TestAReplyThatFilledItsCap(AiTestCase):
    """The failure the token limit made ordinary.

    Every request carries a `max_tokens` now, so a model writing right up to it is
    not a rare mishap but the expected end of a batch that had plenty to say. The
    client marks that by raising and throwing the reply away, and a book that had
    been streaming happily for a minute became a red banner reading “The AI
    service could not be reached: Could not parse response content as the length
    limit was reached”. A limit meant to shorten a book was breaking one.

    So a cut-off reply is turned back into the text it is carrying, and every
    test below is about it going on to be treated as what it is: a truncated
    reply, which this app has always known how to read.
    """

    def cut_batch(self):
        """Two whole chapters and the start of a third, as the cut left them."""
        good = json.dumps({"chapters": [one_chapter(1), one_chapter(2)]})
        return good[: good.rindex("]")] + ', {"number": 3, "paragraphs": ["Ha'

    def test_a_cut_off_reply_is_read_rather_than_raised(self):
        chat = FakeChat(
            outline_reply(5), cut_off_error(self.cut_batch()), batch_reply(4, 5)
        )
        book = self.run_book(chat)
        self.assertEqual(len(book.chapters), 5)
        self.assertEqual(book.chapters[0].text.split("\n")[0], "Chapter 1, first.")
        self.assertEqual(book.chapters[1].text.split("\n")[0], "Chapter 2, first.")
        # Three went with the cut, so it comes from its own summary instead.
        self.assertEqual(book.chapters[2].text, "What happens in 3.")
        self.assertEqual(book.chapters[3].text.split("\n")[0], "Chapter 4, first.")

    def test_a_reply_that_was_whole_after_all_is_used_whole(self):
        """The cap can land exactly on the last brace. Nothing is lost then."""
        chat = FakeChat(outline_reply(1), cut_off_error(batch_reply(1)))
        book = self.run_book(chat, config=settings(chapters=1))
        self.assertEqual(book.chapters[0].text.split("\n")[0], "Chapter 1, first.")

    def test_the_words_that_had_already_arrived_are_kept(self):
        """A streamed reply raises after its last piece, not instead of it."""
        chat = StreamCutOffChat(
            outline_reply(5), CutShort(self.cut_batch()), batch_reply(4, 5)
        )
        seen = []
        book = self.run_book(chat, on_text=seen.append)
        self.assertEqual(len(book.chapters), 5)
        self.assertEqual(book.chapters[0].text.split("\n")[0], "Chapter 1, first.")
        # Read while it was written, and still on screen at the end.
        self.assertIn("Chapter 1, first.", seen[-1])

    def test_a_cut_off_reply_does_not_cost_a_repair(self):
        """A repair would carry the same cap and run out in the same place.

        The request it would spend is one the chapters after it need, so this cut
        is deliberately made before the first chapter closes — nothing to
        salvage, and still no repair.
        """
        chat = FakeChat(
            outline_reply(5),
            cut_off_error('{"chapters": [{"number": 1, "paragraphs": ["Ha'),
            batch_reply(4, 5),
        )
        book = self.run_book(chat)
        self.assertEqual(len(chat.calls), 3)
        # A repair is three messages; a batch is two. The third request went on
        # chapters four and five, which is what proves it was not squandered.
        self.assertEqual(len(chat.calls[2]), 2)
        self.assertEqual(book.chapters[3].text.split("\n")[0], "Chapter 4, first.")
        self.assertEqual(len(book.chapters), 5)

    def test_an_unreadable_reply_is_still_repaired(self):
        """Only a reply that ran out skips the repair. Prose still earns one."""
        chat = FakeChat(outline_reply(1), "I am afraid I cannot.", batch_reply(1))
        book = self.run_book(chat, config=settings(chapters=1))
        self.assertEqual(len(chat.calls), 3)
        self.assertEqual(book.chapters[0].text.split("\n")[0], "Chapter 1, first.")

    def test_a_cut_off_outline_still_gives_a_book(self):
        chat = FakeChat(
            cut_off_error(outline_reply(5)[:60]),
            batch_reply(1, 2, 3),
            batch_reply(4, 5),
        )
        book = self.run_book(chat)
        self.assertEqual(len(book.chapters), 5)
        self.assertEqual(len(chat.calls), 3)

    def test_what_the_service_counted_is_what_is_charged(self):
        """The exception carries the usage, and an exact count beats a guess."""
        said, counted = ai_book._cut_off_reply(cut_off_error(batch_reply(1), 1234))
        self.assertEqual(said, batch_reply(1))
        self.assertEqual(counted, 1234)

    def test_nothing_on_the_exception_is_no_words_rather_than_a_crash(self):
        """It is somebody else's object and its shape is theirs to change."""
        self.assertEqual(ai_book._cut_off_reply(RuntimeError("nothing here")), ("", None))

    def test_it_is_recognised_by_its_message_as_well_as_its_class(self):
        self.assertTrue(ai_book._is_cut_off(cut_off_error()))
        self.assertTrue(ai_book._is_cut_off(cut_off_error(name="SomethingElse")))
        self.assertFalse(ai_book._is_cut_off(RuntimeError("429 rate limit exceeded")))

    def test_a_real_failure_still_reaches_the_reader(self):
        """The catch is for one exception, not for every exception."""
        chat = FakeChat(outline_reply(1), RuntimeError("429 rate limit exceeded"))
        with self.assertRaises(ai_book.AIError) as caught:
            self.run_book(chat, config=settings(chapters=1))
        self.assertIn("too much at once", str(caught.exception))


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
        self.assertEqual(len(book.chapters), 5)
        self.assertIn("Chapter 1, first.", seen[-1])


class DutifulChat(FakeChat):
    """A model that does as it is told, and a service that cuts it off.

    The one behaviour no scripted reply can show, and the whole of what went
    wrong: it writes the length the prompt asked for — a little over, as models
    do — and the reply is then chopped wherever `max_tokens` lands.

    That was a loop the old prompt could not win. It asked for the same 320-word
    chapters however small the cap, so the reply always ran past it, the chapters
    after the cut were always lost, and their tokens were always inherited by the
    batch that came next. Every chapter here is what the prompt asked for, so a
    book that comes back lopsided came back lopsided for a reason.
    """

    VOCABULARY = (
        "paper thread linen bone folder wire spine gather sewn fold sheet quire "
        "needle wax board cloth glue press trim head tail band leather rain"
    ).split()

    # Models run long rather than short, and the margin has to survive it.
    OVERRUN = 1.15

    def __init__(self, chapters=5):
        super().__init__()
        self.chapters = chapters
        self.cap = None

    def bind(self, **kwargs):
        self.cap = kwargs.get("max_tokens")
        return super().bind(**kwargs)

    def _prose(self, count):
        words = [self.VOCABULARY[i % len(self.VOCABULARY)] for i in range(count)]
        return " ".join(words).capitalize() + "."

    def _asked(self, system, user):
        """The chapters wanted and the length wanted, read off the prompt."""
        numbers = [
            int(piece)
            for piece in re.search(r"Write chapters ([\d, ]+) now", user)
            .group(1)
            .split(",")
        ]
        found = re.search(r"(?:to|Exactly) (\d+) paragraphs", system)
        most = int(found.group(1)) if found else 1
        return numbers, most, int(re.search(r"about (\d+) words", system).group(1))

    def _next(self, messages):
        self.calls.append(messages)
        system, user = messages[0][1], messages[1][1]
        if "You plan short books" in system:
            body = outline_reply(self.chapters)
        else:
            numbers, most, words = self._asked(system, user)
            body = json.dumps(
                {
                    "chapters": [
                        {
                            "number": number,
                            "heading": f"Chapter {number}",
                            "paragraphs": [
                                self._prose(int(words * self.OVERRUN))
                                for _ in range(most)
                            ],
                        }
                        for number in numbers
                    ]
                }
            )
        # Four bytes to the token, which is about what the service counts, and
        # the cut is where the cap falls rather than where a sentence ends.
        return body[: self.cap * 4] if self.cap else body


class TestEveryChapterGetsTheSameRoom(AiTestCase):
    """The book that came back 28, 27, 22, 322 and 224 words long.

    Two faults that made each other worse. The tokens were shared out a batch at
    a time, so whatever an early batch did not spend was inherited by the batch
    after it; and the prompt asked every batch for the same 320-word chapters
    however little it could afford, so a batch asked for three times its cap ran
    past it, lost every chapter after the cut, and handed all of its tokens on.
    """

    def chapter_words(self, book):
        return [len(chapter.text.split()) for chapter in book.chapters]

    # ---- the ask fits the room ------------------------------------------

    def test_the_length_asked_for_always_fits_the_room_given(self):
        """The fault that lost three chapters: a prompt that ignored the cap."""
        for allowance in (ai_book.FULL_ALLOWANCE, 900, 400, 250, 160, 90, 60):
            with self.subTest(allowance=allowance):
                _fewest, most, words = ai_book.paragraph_plan(allowance)
                asked = most * words * ai_book.TOKENS_PER_WORD + ai_book.CHAPTER_FRAMING
                self.assertLess(asked, allowance)

    def test_the_full_length_is_still_asked_for_when_there_is_room(self):
        """Nothing above is a reason to write a smaller book than the tokens buy."""
        self.assertEqual(
            ai_book.paragraph_plan(ai_book.FULL_ALLOWANCE),
            (ai_book.WANTED_PARAGRAPHS[0], ai_book.WANTED_PARAGRAPHS[1],
             ai_book.WANTED_WORDS),
        )

    def test_less_room_asks_for_less_and_never_for_more(self):
        asked = [
            ai_book.paragraph_plan(allowance)[1] * ai_book.paragraph_plan(allowance)[2]
            for allowance in (80, 150, 250, 400, 600)
        ]
        self.assertEqual(asked, sorted(asked))

    def test_the_prompt_says_the_length_the_cap_actually_allows(self):
        """Planned and granted are not always the same number. The prompt
        follows the one the service was told."""
        chat = FakeChat(*whole_book())
        self.run_book(chat)
        for messages, binding, size in zip(chat.calls[1:], chat.bindings[1:], (3, 2)):
            cap = binding["max_tokens"]
            wanted = ai_book.length_asked(*ai_book.paragraph_plan(cap // size))
            self.assertIn(wanted, messages[0][1])

    # ---- and the room is the same for every chapter -----------------------

    def test_every_batch_is_given_the_same_room_per_chapter(self):
        chat = FakeChat(*whole_book())
        self.run_book(chat)
        each = [
            binding["max_tokens"] / size
            for binding, size in zip(chat.bindings[1:], (3, 2))
        ]
        self.assertAlmostEqual(each[0], each[1], delta=1)

    def test_a_batch_that_came_back_with_nothing_does_not_fatten_the_next(self):
        """The half of the fault that made chapter four three hundred words."""
        whole = FakeChat(*whole_book())
        self.run_book(whole)
        ai_book._RUNG.clear()
        # Valid JSON holding no chapters: nothing to keep, and no repair either,
        # so the second batch is reached with the first batch's tokens unspent.
        lost = FakeChat(
            outline_reply(5), json.dumps({"chapters": []}), batch_reply(4, 5)
        )
        self.run_book(lost)
        self.assertEqual(
            whole.bindings[2]["max_tokens"], lost.bindings[2]["max_tokens"]
        )

    # ---- end to end, against a model that does as it is told ---------------

    def test_a_whole_book_comes_back_evenly_written(self):
        """The report this was fixed for, driven the way it happened."""
        descriptions = {
            "a short one": "a book about paper",
            # Long enough to make the outline expensive, which is what left the
            # first batch too poor to write the chapters it had been given.
            "a long one": "paper, and the people who fold it, and why " * 17,
        }
        for name, description in descriptions.items():
            with self.subTest(name):
                ai_book._RUNG.clear()
                book = self.run_book(DutifulChat(), prompt=description)
                counts = self.chapter_words(book)
                self.assertEqual(len(counts), 5, name)
                # The book that prompted this had a spread of fourteen to one.
                self.assertLessEqual(max(counts), min(counts) * 1.5, counts)

    def test_no_chapter_comes_back_as_its_own_plan_entry(self):
        """A summary in the editor is the fallback, not the ordinary outcome."""
        book = self.run_book(DutifulChat(), prompt="paper, and folding it " * 32)
        summaries = {f"What happens in {n}." for n in range(1, 6)}
        for chapter in book.chapters:
            self.assertNotIn(chapter.text, summaries)

    def test_fewer_requests_are_used_when_they_cannot_all_be_paid_for(self):
        """Nine starved requests are not a book; two full ones are."""
        chat = FakeChat(outline_reply(12), *[batch_reply(1)] * 9)
        book = self.run_book(chat, config=settings(chapters=12, max_calls=10))
        self.assertEqual(len(book.chapters), 12)
        self.assertLess(len(chat.calls), 10)


def _schema_of(binding):
    """The bare schema out of a recorded `response_format`, or `None`.

    The ledger charges for the schema it was given; this digs the same object
    back out of what was sent, so the two are counting the same thing.
    """
    fmt = binding.get("response_format") or {}
    return fmt.get("json_schema", {}).get("schema")


class GreedyChat(FakeChat):
    """A model that always says the most it is allowed to say.

    The worst legal case, and the one the limit has to survive: every reply comes
    back exactly as long as the `max_tokens` it was sent, measured in the same
    over-generous way `estimate_tokens` measures it. A real model answers with
    less. This one never does.

    It answers with real chapters first — padded out to the cap with a long last
    paragraph — so the book is still a book while the arithmetic is at its worst.
    """

    def __init__(self, *replies, **kwargs):
        super().__init__(*replies, **kwargs)
        self.caps = []
        self.said = []

    def bind(self, **kwargs):
        self.caps.append(kwargs.get("max_tokens"))
        return super().bind(**kwargs)

    def _next(self, messages):
        try:
            reply = super()._next(messages)
        except Exception:
            # Recorded as having said nothing, so `said` stays lined up with
            # `calls`. A request that was refused is a request that was sent and
            # produced no output, and the sums below need both facts.
            self.said.append("")
            raise
        reply = self.fatten(reply, self.caps[-1] or 0)
        self.said.append(reply)
        return reply

    @staticmethod
    def fatten(reply, cap):
        """`reply`, grown until it is as long as `cap` allows and no longer."""
        room = cap * 3 - len(reply.encode("utf-8"))  # three bytes to the token
        if room <= 0:
            return reply
        padding = "pad " * (room // 4)
        # Inside the last paragraph when there is one, so the reply stays the
        # JSON it was going to be; on the end when there is not.
        marker = '"]'
        if marker in reply:
            return reply.replace(marker, padding + marker, 1)
        return reply + " " + padding


class TestTheTokenLimit(AiTestCase):
    """`LIMIT` tokens a book, input and output, and never one more.

    The guarantee rests on two things and they are checked separately below,
    because together they are the whole argument:

    1. Output cannot exceed what `max_tokens` allows, because the service
       enforces it. So every request must carry one.
    2. Input cannot exceed `estimate_tokens`, because that number is an
       over-estimate. So it must never read low.

    Given both, `estimated input + max_tokens` is the worst a request can come
    to, and the ledger only sends a request whose worst case still fits.
    """

    def spend(self, chat):
        """What these requests really came to, in tokens.

        Rebuilt from what the model was sent and what it said back, not from the
        ledger's own books — which would only be checking that the ledger agrees
        with itself. Input is the messages plus the schema that went with them;
        output is the reply, which a `GreedyChat` has already grown to fill every
        token its `max_tokens` allowed. So for a `GreedyChat` this is both the
        real cost and the worst possible one.
        """
        total = 0
        for messages, binding, said in zip(chat.calls, chat.bindings, chat.said):
            total += ai_book.estimate_request(messages, _schema_of(binding))
            total += ai_book.estimate_tokens(said)
        return total

    def caps_allowed(self, chat):
        """The other reading: every reply as long as it was *allowed* to be.

        Only meaningful when no request was refused. A refused one produced no
        output at all, so its cap was handed straight back — counting it would
        be adding up tokens that could not all have existed at once.
        """
        return sum(
            ai_book.estimate_request(messages, _schema_of(binding))
            + (binding.get("max_tokens") or 0)
            for messages, binding in zip(chat.calls, chat.bindings)
        )

    # ---- 1. every request is capped ------------------------------------

    def test_every_request_carries_a_maximum(self):
        chat = GreedyChat(*whole_book())
        self.run_book(chat)
        self.assertEqual(len(chat.caps), len(chat.calls))
        self.assertTrue(all(isinstance(cap, int) and cap > 0 for cap in chat.caps))

    def test_the_cap_is_what_the_service_is_told(self):
        chat = GreedyChat(*whole_book())
        self.run_book(chat)
        for binding, cap in zip(chat.bindings, chat.caps):
            self.assertEqual(binding["max_tokens"], cap)

    # ---- 2. the estimate never reads low --------------------------------

    def test_the_estimate_is_never_below_a_real_tokenizer(self):
        """The one assumption the whole limit rests on.

        Checked against a real BPE tokenizer over text of the kinds this app
        actually sends — its own prompts, a description, prose, JSON, and the
        scripts that break a bytes-per-character guess.
        """
        try:
            import tiktoken

            encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:  # pragma: no cover - offline or not installed
            self.skipTest("tiktoken is not available to check against")

        samples = [
            ai_book.STYLE_RULES,
            ai_book._OUTLINE_SYSTEM.format(chapters=5),
            ai_book.batch_system(ai_book.FULL_ALLOWANCE),
            # The shortest ask too: it is a different sentence, and the estimate
            # has to read high for that one as well.
            ai_book.batch_system(ai_book.MIN_OUTPUT_TOKENS),
            ai_book._REPAIR,
            json.dumps(ai_book.OUTLINE_SCHEMA),
            json.dumps(ai_book.BATCH_SCHEMA),
            outline_reply(5),
            batch_reply(1, 2, 3),
            "a book about paperclips" * 40,
            "Здравствуй, мир! " * 30,
            "组装书页的方法。" * 40,
            "🙂🙃" * 200,
            "".join(chr(c) for c in range(32, 127)) * 8,
            "",
            " ",
        ]
        for sample in samples:
            self.assertGreaterEqual(
                ai_book.estimate_untrusted(sample),
                len(encoding.encode(sample)),
                f"under-estimated as untrusted: {sample[:40]!r}",
            )

    def test_the_prompts_this_app_sends_are_not_under_estimated(self):
        """The looser of the two estimators, held to the text it is used on.

        `estimate_tokens` is only ever applied to words written in `ai_book`
        itself, so it is allowed a ratio that suits English prose and JSON. This
        is the check that keeps it honest as those words are edited.
        """
        try:
            import tiktoken

            encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:  # pragma: no cover - offline or not installed
            self.skipTest("tiktoken is not available to check against")

        ours = [
            ai_book.STYLE_RULES,
            ai_book._OUTLINE_SYSTEM.format(chapters=5),
            ai_book.batch_system(ai_book.FULL_ALLOWANCE),
            ai_book.batch_system(ai_book.MIN_OUTPUT_TOKENS),
            ai_book._REPAIR,
            ai_book._REPAIR_SYSTEM,
            json.dumps(ai_book.outline_schema(5)),
            json.dumps(ai_book.batch_schema(3)),
        ]
        for sample in ours:
            self.assertGreaterEqual(
                ai_book.estimate_tokens(sample),
                len(encoding.encode(sample)),
                f"under-estimated: {sample[:60]!r}",
            )

    # ---- the two together ------------------------------------------------

    def test_an_ordinary_book_stays_inside_the_limit(self):
        chat = GreedyChat(*whole_book())
        self.run_book(chat)
        self.assertLessEqual(self.spend(chat), LIMIT)

    def test_a_greedy_model_cannot_push_it_over(self):
        """Every reply as long as it was allowed to be, on every request."""
        chat = GreedyChat(*whole_book())
        book = self.run_book(chat)
        self.assertLessEqual(self.spend(chat), LIMIT)
        self.assertEqual(len(book.chapters), 5)

    def test_the_ledger_agrees_it_never_went_over(self):
        chat = GreedyChat(*whole_book())
        ledger = self.run_watching_the_ledger(chat)
        self.assertLessEqual(ledger.spent, LIMIT)
        self.assertLessEqual(sum(ledger.used), LIMIT)

    def run_watching_the_ledger(self, chat, config=None, **kwargs):
        """Run a book and hand back the `_Ledger` it used."""
        seen = []
        real = ai_book._Ledger

        def remember(limit):
            made = real(limit)
            seen.append(made)
            return made

        with mock.patch.object(ai_book, "_Ledger", remember):
            self.run_book(chat, config=config or settings(), **kwargs)
        self.assertEqual(len(seen), 1)
        return seen[0]

    # ---- the awkward cases ----------------------------------------------

    def test_a_description_at_its_full_length_does_not_break_it(self):
        chat = GreedyChat(*whole_book())
        self.run_book(chat, "paperclips and their discontents " * 40)
        self.assertLessEqual(self.spend(chat), LIMIT)

    def test_a_description_in_another_script_does_not_break_it(self):
        """Bytes, not characters — a thousand characters of Chinese is three
        thousand bytes and the estimate has to know it."""
        chat = GreedyChat(*whole_book())
        self.run_book(chat, "组装书页的方法，" * 120)
        self.assertLessEqual(self.spend(chat), LIMIT)

    def test_a_repair_does_not_break_it(self):
        chat = GreedyChat(outline_reply(5), "not json at all", batch_reply(1, 2, 3))
        self.run_book(chat)
        self.assertLessEqual(self.spend(chat), LIMIT)

    def test_a_format_downgrade_does_not_break_it(self):
        chat = GreedyChat(
            RuntimeError("400 response_format json_schema is not supported"),
            outline_reply(5),
            batch_reply(1, 2, 3),
            batch_reply(4, 5),
        )
        self.run_book(chat, config=settings(max_calls=4))
        self.assertLessEqual(self.spend(chat), LIMIT)

    def test_more_requests_do_not_buy_more_tokens(self):
        """The two budgets are not the same budget. Raising one is not raising
        the other, and the token limit is the one that binds."""
        chat = GreedyChat(outline_reply(5), *[batch_reply(1)] * 9)
        self.run_book(chat, config=settings(max_calls=10))
        self.assertLessEqual(self.spend(chat), LIMIT)

    def test_more_chapters_do_not_buy_more_tokens(self):
        chat = GreedyChat(outline_reply(12), *[batch_reply(1)] * 9)
        self.run_book(chat, config=settings(chapters=12, max_calls=10))
        self.assertLessEqual(self.spend(chat), LIMIT)

    # ---- and it is still a book ------------------------------------------

    def test_five_chapters_arrive_whatever_the_model_does(self):
        """Every way a request can come to nothing, and a book each time."""
        cases = {
            "a whole book": whole_book(),
            "nothing but prose": ["sorry", "still sorry", "no", "no", "no"],
            "an empty outline": [json.dumps({"chapters": []}), batch_reply(1, 2, 3)],
            "one batch missing": [outline_reply(5), batch_reply(1, 2, 3), "rubbish"],
            "chapters with no text": [
                outline_reply(5),
                json.dumps({"chapters": [{"number": 1, "paragraphs": []}]}),
            ],
            "a truncated reply": [
                outline_reply(5),
                '{"chapters": [{"number": 1, "paragraphs": ["Half a sen',
            ],
        }
        for name, replies in cases.items():
            with self.subTest(name):
                ai_book._RUNG.clear()
                chat = GreedyChat(*replies, *["still nothing"] * 6)
                book = self.run_book(chat)
                self.assertEqual(len(book.chapters), 5, name)
                self.assertTrue(all(c.text.strip() for c in book.chapters), name)
                self.assertLessEqual(self.spend(chat), LIMIT, name)

    def test_a_limit_too_small_to_ask_anything_still_gives_a_book(self):
        """The floor of the whole design: no requests at all, still five
        chapters, still no error."""
        chat = GreedyChat(*whole_book())
        book = self.run_book(chat, config=settings(token_limit=50))
        self.assertEqual(len(chat.calls), 0)
        self.assertEqual(len(book.chapters), 5)
        self.assertTrue(all(c.text.strip() for c in book.chapters))

    def test_a_smaller_limit_makes_a_shorter_book_not_a_broken_one(self):
        lengths = {}
        for limit in (1200, 2500, LIMIT):
            ai_book._RUNG.clear()
            chat = GreedyChat(*whole_book())
            book = self.run_book(chat, config=settings(token_limit=limit))
            self.assertEqual(len(book.chapters), 5, limit)
            self.assertLessEqual(self.spend(chat), limit, limit)
            lengths[limit] = book.words
        self.assertLess(lengths[1200], lengths[LIMIT])

    # ---- the ground truth ------------------------------------------------

    def real_spend(self, chat, encoding):
        """What OpenRouter would have billed, counted with a real tokenizer.

        Everything above is this file's arithmetic checking this file's
        arithmetic. This is the number that would appear on the account: every
        byte of every request body that carries tokens, plus every reply.
        """
        total = 0
        for messages, binding, said in zip(chat.calls, chat.bindings, chat.said):
            total += sum(len(encoding.encode(text)) for _role, text in messages)
            fmt = binding.get("response_format")
            if fmt:
                total += len(encoding.encode(json.dumps(fmt)))
            total += len(encoding.encode(said))
        return total

    def test_a_real_tokenizer_agrees_the_limit_held(self):
        """The whole guarantee, measured rather than argued.

        Nine ways a book can go, every reply as long as the service would have
        let it be, counted by a real BPE tokenizer. None may pass the limit.
        """
        runs = {
            "an ordinary book": (whole_book(), "a book about paperclips"),
            "a long description": (
                whole_book(),
                "paperclips, their history, their discontents, and the people "
                "who bent them " * 12,
            ),
            "a description of punctuation": (
                whole_book(),
                "!@#$%^&*()_+-=[]{};':\",./<>?`~ " * 40,
            ),
            "a description in chinese": (whole_book(), "组装书页的方法，" * 120),
            "a description of emoji": (whole_book(), "🙂🙃😀😃" * 200),
            "a book in cyrillic": (
                [
                    outline_reply(5, title="Сложенный лист", author="М. Квайр"),
                    batch_reply(1, 2, 3),
                    batch_reply(4, 5),
                ],
                "книга о бумаге",
            ),
            "prose instead of json": (
                ["sorry", "still sorry", "no", "no", "no"],
                "a book about paper",
            ),
            "a truncated reply": (
                [
                    outline_reply(5),
                    '{"chapters": [{"number": 1, "paragraphs": ["Half a sen',
                    batch_reply(4, 5),
                ],
                "a book about paper",
            ),
            "a format downgrade": (
                [
                    RuntimeError("400 response_format json_schema is not supported"),
                    outline_reply(5),
                    batch_reply(1, 2, 3),
                    batch_reply(4, 5),
                ],
                "a book about paper",
            ),
        }
        try:
            import tiktoken

            encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:  # pragma: no cover - offline or not installed
            self.skipTest("tiktoken is not available to check against")

        for name, (replies, description) in runs.items():
            with self.subTest(name):
                ai_book._RUNG.clear()
                chat = GreedyChat(*replies, *["nothing more"] * 6)
                book = self.run_book(
                    chat, description, config=settings(max_calls=4)
                )
                billed = self.real_spend(chat, encoding)
                self.assertLessEqual(billed, LIMIT, f"{name}: billed {billed}")
                self.assertEqual(len(book.chapters), 5, name)
                self.assertTrue(all(c.text.strip() for c in book.chapters), name)

    def test_two_hundred_random_books_all_stayed_inside_it(self):
        """The cases nobody thought to write down.

        Descriptions built out of every script that priced badly in the table
        the estimators were set from, crossed with every way a reply can go
        wrong, at limits from cramped to generous. Seeded, so a failure here is
        a failure anybody can reproduce.
        """
        rng = random.Random(20260827)
        alphabets = [
            "abcdefghijklmnopqrstuvwxyz ",
            "!@#$%^&*()_+-=[]{};':\",./<>?`~ ",
            "0123456789 .,",
            "组装书页的方法，用亚麻线缝合书脊。",
            "Здравствуй, мир! Книги сшиваются льняной нитью.",
            "שלום עולם, הספרים נתפרים בחוט פשתן.",
            "🙂🙃😀😃😄😁😆😅",
            "áèîõǘ ̈ ̃ ̄",
        ]
        misbehaviours = [
            lambda: outline_reply(5),
            lambda: batch_reply(1, 2, 3),
            lambda: batch_reply(4, 5),
            lambda: "I am sorry, I cannot help with that.",
            lambda: '{"chapters": [{"number": 1, "paragraphs": ["Cut off mid',
            lambda: "```json\n" + batch_reply(1) + "\n```",
            lambda: json.dumps({"chapters": []}),
            lambda: RuntimeError("400 response_format json_schema is unsupported"),
        ]

        for run in range(200):
            ai_book._RUNG.clear()
            alphabet = rng.choice(alphabets)
            description = "".join(
                rng.choice(alphabet) for _ in range(rng.randint(1, 1000))
            )
            if not description.strip():
                continue  # An empty box is refused before any of this, rightly.
            replies = [rng.choice(misbehaviours)() for _ in range(rng.randint(1, 6))]
            limit = rng.choice([400, 900, 1500, 2500, LIMIT])
            chapters = rng.choice([1, 3, 5, 8])
            calls = rng.choice([1, 2, 3, 5])
            chat = GreedyChat(*replies, *["and nothing more"] * 8)
            config = settings(
                token_limit=limit, chapters=chapters, max_calls=calls
            )
            try:
                book = self.run_book(chat, description, config=config)
            except ai_book.AIError as error:  # pragma: no cover - a real failure
                self.fail(f"run {run} raised: {error}")
            note = f"run {run}: limit={limit} chapters={chapters} calls={calls}"
            self.assertEqual(len(book.chapters), chapters, note)
            self.assertTrue(all(c.text.strip() for c in book.chapters), note)
            self.assertLessEqual(self.spend(chat), limit, note)
            if all(chat.said):  # nothing was refused, so the caps all stood
                self.assertLessEqual(self.caps_allowed(chat), limit, note)

    def test_the_worst_case_of_every_request_together_fits(self):
        """Not just what was said — what every reply was *allowed* to say."""
        chat = GreedyChat(*whole_book())
        self.run_book(chat)
        self.assertLessEqual(self.caps_allowed(chat), LIMIT)

    def test_the_limit_can_be_set_from_the_environment(self):
        with mock.patch.dict(
            os.environ, {ai_config.TOKEN_LIMIT_VAR: "2000"}, clear=False
        ):
            self.assertEqual(ai_config.settings().token_limit, 2000)


class TestTheRequestOpenRouterGets(AiTestCase):
    """What `_make_chat` builds, and what it deliberately leaves out."""

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

    def test_the_model_is_the_one_that_was_configured(self):
        self.assertEqual(self.built_with(settings())["model"], DEFAULT_MODEL)

    def test_no_provider_block_is_sent(self):
        """Routing is OpenRouter's to decide, and its default leans on cost."""
        made = self.built_with(settings())
        self.assertNotIn("extra_body", made)
        self.assertNotIn("provider", made.get("model_kwargs") or {})

    def test_nothing_but_the_key_carries_the_key(self):
        made = self.built_with(settings())
        elsewhere = {name: value for name, value in made.items() if name != "api_key"}
        self.assertNotIn(FAKE_KEY, str(elsewhere))


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

    def test_the_default_model_is_one_named_model(self):
        self.assertEqual(
            ai_config.settings().model, "meta-llama/llama-3.1-8b-instruct"
        )

    def test_a_model_can_still_be_named_in_the_environment(self):
        os.environ[ai_config.MODEL_VAR] = ai_config.FREE_MODEL
        self.assertEqual(ai_config.settings().model, ai_config.FREE_MODEL)

    def test_free_only_is_off_unless_it_is_turned_on(self):
        """The default model is paid, so a guard refusing paid models would
        refuse the app itself."""
        self.assertFalse(ai_config.settings().free_only)
        os.environ[ai_config.FREE_ONLY_VAR] = "1"
        self.assertTrue(ai_config.settings().free_only)

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

    def test_streaming_is_on_by_default(self):
        self.assertTrue(ai_config.settings().stream)

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
