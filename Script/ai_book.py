"""Ask a model for a whole book, and hand back a `Manuscript`.

This is the only file in the project that knows what a language model is. It is
kept apart on purpose: the rest of the app deals in paper sizes and folded
sheets, and none of it should have to care that this exists.

It does not import Streamlit. That keeps it testable with plain `unittest`, and
it means nothing here can reach into session state by accident — including the
API key, which must never end up there.

**Two calls, not one.** A whole book will not survive a single request. Free
models cap their output, and a reply that runs out half way through chapter nine
is not a short book, it is broken JSON. So: one request for an outline, then one
request per chapter. Each is small enough to finish, a failure loses one chapter
rather than the book, and there is something honest to put in the progress bar.

**Why the JSON handling looks paranoid.** Free models are a moving target. Some
honour a strict JSON schema, some only understand "reply with JSON", some will
wrap a perfectly good object in ```json fences or a sentence of preamble. So the
request is made at the strongest rung the model will accept, the reply is parsed
by something that expects to be lied to, and one repair is asked for before
giving up. See `_invoke` and `extract_json`.

**Why chapters arrive as a list of strings.** The editor stores a chapter as one
blob of text where a blank line starts a paragraph. Asking a model to put that
blob in a JSON string invites real newlines inside the string, which is exactly
the thing `json.loads` refuses. Asking for one array element per paragraph makes
that impossible, and `"\\n\\n".join(...)` rebuilds precisely what the typesetter
wants. This is the single most load-bearing decision in the file.

**What leaves this machine.** The description the visitor typed, and the app's
own instructions. `write_book` takes a string and a `Design`, not a `Manuscript`,
so there is no route by which somebody's book, drafts or uploads could be sent
anywhere — the type signature is the guarantee, not a promise in a comment.
"""

import importlib.util
import json
import re
import time
from dataclasses import replace

from Script import ai_config
from Script.manuscript import Manuscript

# A `.env` on a development machine, read once. Absent in production, where the
# key arrives as a real environment variable; harmless either way, and it cannot
# override one. See `ai_config.load_env`.
try:
    ai_config.load_env()
except Exception:  # pragma: no cover - a broken .env must not stop the app
    pass


class AIError(RuntimeError):
    """Something gone wrong, in words a person can act on.

    Every message that leaves this module is one of these, and every one has been
    through `scrub`. Nothing else escapes: the exceptions the HTTP client raises
    can carry request context, and on Render the maintainer's log is where that
    would land.
    """


# How much description is accepted. Long enough to say what the book should be,
# short enough that the box cannot be used as a way to push a large prompt
# through somebody else's account.
MAX_PROMPT_CHARS = 1000

MIN_CHAPTERS = 3
MAX_PARAGRAPHS = 40

TEMPERATURE = 0.8

# Tried in this order, strongest first. Every rung also describes the shape it
# wants in the prompt itself, which is what makes the last one work at all.
RUNGS = ("json_schema", "json_object", "prompt")

# Which rung a given model turned out to accept. Paying the probe once per
# process rather than once per chapter is the whole point. Model names only —
# there is never a key in here.
_RUNG = {}

_REPAIR = (
    "That reply was not valid JSON. Send the JSON object again, on its own: "
    "no explanation before it, no code fence around it, nothing after it."
)


# --------------------------------------------------------------------------
# Is this switched on at all
# --------------------------------------------------------------------------


def _has_langchain():
    try:
        return importlib.util.find_spec("langchain_openai") is not None
    except (ImportError, ValueError):
        return False


def available():
    """Whether the AI button can do anything.

    Called on every rerun, so it stays cheap: no import of LangChain, and no
    request. The button is drawn switched off rather than left to fail when
    pressed, because a copy of this app with no key is a perfectly good copy of
    this app — everything else works.
    """
    return ai_config.configured() and _has_langchain()


def why_unavailable():
    """One sentence explaining the switched-off button, for the page."""
    if not ai_config.configured():
        return (
            f"No {ai_config.KEY_VAR} is set, so there is nothing to write with."
        )
    if not _has_langchain():
        return "The langchain-openai package is not installed on this copy."
    return ""


# --------------------------------------------------------------------------
# Keeping the key out of everything
# --------------------------------------------------------------------------

# OpenRouter keys have a recognisable shape. Matching it as well as the exact
# string means a key that is not the configured one — an old one quoted back in
# an error, say — is caught too.
_KEY_SHAPE = re.compile(r"sk-or-v1-[A-Za-z0-9_\-]{8,}")


def scrub(text, key=""):
    """`text` with anything that looks like a key taken out of it."""
    text = str(text)
    if key:
        text = text.replace(key, "«api key»")
    return _KEY_SHAPE.sub("«api key»", text)


def _readable(error, config):
    """Turn whatever the client raised into an `AIError` worth reading.

    The original exception is deliberately not chained (`from None` at the raise
    site). Its traceback and its `__context__` can hold the request that caused
    it, and this app runs at a public URL.
    """
    text = scrub(error, config.api_key)
    status = getattr(error, "status_code", None) or getattr(
        getattr(error, "response", None), "status_code", None
    )
    low = text.lower()

    if status in (401, 403) or "invalid api key" in low or "no auth" in low:
        return AIError(
            "The AI service would not accept this copy's key. Check "
            f"{ai_config.KEY_VAR}."
        )
    if status == 402 or "credit" in low or "quota" in low:
        return AIError("This copy has no credit left for the AI writer.")
    if status == 429 or "rate limit" in low or "too many requests" in low:
        return AIError(
            "The free models are out of requests for the moment. Try again in "
            "a few minutes, or later today — free models are shared and they "
            "do run out."
        )
    if "timeout" in low or "timed out" in low:
        return AIError(
            "The model took too long to answer. Try a shorter description, or "
            "ask for fewer chapters."
        )
    if status and 500 <= int(status) < 600:
        return AIError("The AI service is having trouble. Try again shortly.")
    return AIError(f"The AI service could not be reached: {text}")


# --------------------------------------------------------------------------
# Reading JSON out of whatever came back
# --------------------------------------------------------------------------


_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.S)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _first_object(text):
    """The first balanced `{...}`, or as much of one as there is.

    Tracks whether it is inside a string, and whether the last character was a
    backslash, so a `}` inside a chapter does not end the slice early. A reply
    that was cut off mid-object comes back unbalanced on purpose: the parser
    below still gets a chance at it, and a truncated book is worth one attempt.
    """
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text[start:]


def extract_json(text):
    """The JSON object in `text`, however it has been wrapped up.

    Each step is a real failure that has been seen from a real model, in roughly
    the order they turn up. `strict=False` is the quiet hero: it allows literal
    control characters inside strings, which is what a model produces when it
    puts a real newline in the middle of a paragraph.
    """
    text = (text or "").strip()
    if not text:
        raise AIError("The model sent an empty reply.")

    candidates = [text]
    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    sliced = _first_object(candidates[-1]) or _first_object(text)
    if sliced:
        candidates.append(sliced)

    for candidate in candidates:
        for attempt in (candidate, _TRAILING_COMMA.sub(r"\1", candidate)):
            for strict in (True, False):
                try:
                    data = json.loads(attempt, strict=strict)
                except (ValueError, TypeError):
                    continue
                if isinstance(data, dict):
                    return data
    raise AIError(
        "The model did not answer with JSON. It said: "
        f"{scrub(text[:200])}"
    )


# --------------------------------------------------------------------------
# Making the request
# --------------------------------------------------------------------------


def _make_chat(config):
    """The chat model. The one place LangChain is imported, and the test seam.

    The import is in here rather than at the top of the file for a reason that
    matters more than tidiness: `app.py` imports this module at start-up, and a
    missing `langchain_openai` at module level would take down the whole app —
    the PDF conversion half included — on any machine that had not installed it.
    In here, a missing package is a switched-off button.
    """
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=TEMPERATURE,
        timeout=config.timeout,
        max_retries=1,
        default_headers={"X-Title": config.app_title},
    )


def _bind(chat, rung, schema_name, schema):
    """`chat`, told how firmly to insist on JSON.

    Bound onto the runnable rather than built into the client so that one chat
    object serves every rung. Passing `response_format` through `model_kwargs`
    instead collides with `ChatOpenAI`'s own handling of it on some releases;
    `.bind()` does not.
    """
    if rung == "json_schema":
        return chat.bind(
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            }
        )
    if rung == "json_object":
        return chat.bind(response_format={"type": "json_object"})
    return chat


_FORMAT_TROUBLE = (
    "response_format",
    "json_schema",
    "structured output",
    "structured_output",
    "not supported",
    "unsupported",
    "does not support",
)


def _is_format_problem(error, config):
    """Whether this failure is the model refusing the format, not the request.

    Only these come back down a rung. A rate limit or a bad key must be reported
    at once rather than provoking two more doomed requests.
    """
    text = scrub(error, config.api_key).lower()
    return any(marker in text for marker in _FORMAT_TROUBLE)


def _content(reply):
    """The text of a reply, whichever shape the model returned it in."""
    content = getattr(reply, "content", reply)
    if isinstance(content, list):
        parts = []
        for piece in content:
            if isinstance(piece, str):
                parts.append(piece)
            elif isinstance(piece, dict) and "text" in piece:
                parts.append(str(piece["text"]))
        return "".join(parts)
    return content if isinstance(content, str) else str(content)


def _invoke(chat, config, messages, schema_name, schema):
    """One request, starting at the strongest format this model has accepted."""
    start = _RUNG.get(config.model, RUNGS[0])
    rungs = RUNGS[RUNGS.index(start) :]
    for index, rung in enumerate(rungs):
        failure = None
        try:
            reply = _bind(chat, rung, schema_name, schema).invoke(messages)
        except Exception as error:
            if index < len(rungs) - 1 and _is_format_problem(error, config):
                continue
            failure = _readable(error, config)
        # Raised out here rather than inside the `except`, which matters more
        # than it looks. `raise ... from None` only stops Python *printing* the
        # original; the exception object stays reachable as `__context__`, and
        # that object is the one carrying the request — and possibly the key —
        # in its message. Once the except block has ended there is no exception
        # being handled, so this one is raised with no context at all.
        if failure is not None:
            raise failure
        _RUNG[config.model] = rung
        return _content(reply)
    raise AIError("The model would not answer in JSON.")


def _asker(chat, config):
    """A function that asks one question and gets one dictionary back.

    One repair is allowed, and exactly one. A model that has produced prose twice
    is not going to produce JSON on the third attempt, and every extra attempt is
    another request against a free tier that counts them.
    """

    def ask(system, user, schema_name, schema):
        messages = [("system", system), ("human", user)]
        reply = _invoke(chat, config, messages, schema_name, schema)
        try:
            return extract_json(reply)
        except AIError:
            repaired = _invoke(
                chat,
                config,
                messages + [("ai", reply), ("human", _REPAIR)],
                schema_name,
                schema,
            )
            return extract_json(repaired)

    return ask


# --------------------------------------------------------------------------
# What to ask for
# --------------------------------------------------------------------------

# The formatting the typesetter actually implements. Anything outside it is not
# ignored, it is printed literally — a stray "- " becomes a hyphen in the middle
# of a paragraph of a finished book — so the rules are stated to the model, and
# `clean_text` cleans up what comes through anyway.
STYLE_RULES = """\
Formatting rules, which are strict:
- Plain prose. Each paragraph is one string with no line breaks inside it.
- *one star* for italic, **two stars** for bold.
- A paragraph of exactly *** is a scene break.
- A paragraph beginning with "> " is a quotation.
- No lists, no bullet points, no numbered points, no tables, no links, no
  images, no footnotes, no code, and no headings inside the chapter text.
"""

OUTLINE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "author", "chapters"],
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "author": {"type": "string"},
        "series": {"type": "string"},
        "dedication": {"type": "string"},
        "style_note": {"type": "string"},
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["heading", "summary"],
                "properties": {
                    "heading": {"type": "string"},
                    "summary": {"type": "string"},
                },
            },
        },
    },
}

CHAPTER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["paragraphs"],
    "properties": {
        "heading": {"type": "string"},
        "paragraphs": {"type": "array", "items": {"type": "string"}},
    },
}

_OUTLINE_SYSTEM = """\
You plan books that will be printed and sewn by hand. You are given a short
description and you return a plan for the whole book.

Reply with one JSON object and nothing else, in this shape:
{{"title": "", "subtitle": "", "author": "", "series": "", "dedication": "",
  "style_note": "", "chapters": [{{"heading": "", "summary": ""}}]}}

- "author" is an invented author's name suited to the book. Never a real person.
- "dedication" is one short line, or "" for none.
- "style_note" is one sentence describing the voice, tense and register, so that
  every chapter is written the same way. It is not printed.
- "chapters" holds between {low} and {high} entries. "summary" is one or two
  sentences saying what happens in that chapter.
"""

_CHAPTER_SYSTEM = """\
You are writing one chapter of a book that will be printed and sewn by hand.

Reply with one JSON object and nothing else, in this shape:
{{"heading": "", "paragraphs": ["", ""]}}

Write between 6 and {most} paragraphs of real prose. Continue the voice of the
book exactly; do not summarise, do not explain what you are doing, and do not
write a preface to the chapter. Do not repeat the chapter heading in the
paragraphs.

{rules}"""


# --------------------------------------------------------------------------
# Tidying what comes back
# --------------------------------------------------------------------------

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_LEADING_HASH = re.compile(r"^\s*#{1,6}\s*")
_BACKTICKS = re.compile(r"`+")
_SCENE_BREAK = re.compile(r"^\s*(?:([*\-_~])\s*){3,}$")


def clean_prompt(text):
    """The description, as it will be sent. Capped here, not only in the widget.

    The `max_chars` on the text box is a courtesy to the person typing. This is
    the one that counts, because it is the one a request cannot get past.
    """
    text = _CONTROL.sub(" ", str(text or ""))
    text = " ".join(text.split())
    if not text:
        raise AIError("Say what the book should be about first.")
    return text[:MAX_PROMPT_CHARS]


def clean_line(text, fallback=""):
    text = _CONTROL.sub(" ", str(text or ""))
    return " ".join(text.split()) or fallback


def clean_text(paragraph):
    """One paragraph, with the markup the typesetter cannot print removed.

    Italic, bold and the scene break survive untouched — those are real, and the
    model was asked for them. Everything else that models reach for out of habit
    goes, because it would otherwise be printed as the literal characters.
    """
    text = str(paragraph or "")
    if _SCENE_BREAK.match(text.strip()):
        return "***"
    text = _CONTROL.sub(" ", text)
    text = " ".join(text.split())
    if not text:
        return ""
    quoted = text.startswith("> ")
    if quoted:
        text = text[2:]
    text = _LEADING_HASH.sub("", text)
    text = _LIST_MARKER.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _BACKTICKS.sub("", text)
    text = text.strip()
    return f"> {text}" if quoted and text else text


# --------------------------------------------------------------------------
# The two questions
# --------------------------------------------------------------------------


def build_outline(prompt, ask, config):
    """Ask for the plan of the book, and check what comes back is usable."""
    system = _OUTLINE_SYSTEM.format(low=MIN_CHAPTERS, high=config.max_chapters)
    data = ask(system, prompt, "book_outline", OUTLINE_SCHEMA)

    raw = data.get("chapters")
    if not isinstance(raw, list) or not raw:
        raise AIError("The model did not plan any chapters. Try describing the book differently.")

    chapters = []
    for entry in raw[: config.max_chapters]:
        if not isinstance(entry, dict):
            continue
        chapters.append(
            {
                "heading": clean_line(entry.get("heading")),
                "summary": clean_line(entry.get("summary")),
            }
        )
    if not chapters:
        raise AIError("The model's chapter list did not hold any chapters.")

    for number, chapter in enumerate(chapters, start=1):
        if not chapter["heading"]:
            chapter["heading"] = f"Chapter {number}"

    return {
        "title": clean_line(data.get("title"), "Untitled book"),
        "subtitle": clean_line(data.get("subtitle")),
        "author": clean_line(data.get("author")),
        "series": clean_line(data.get("series")),
        "dedication": clean_line(data.get("dedication")),
        "style_note": clean_line(data.get("style_note")),
        "chapters": chapters,
    }


def write_chapter(plan, index, ask, config):
    """Ask for one chapter's prose. `index` is 1-based.

    The request carries the whole outline so the model knows where in the arc it
    is, and the previous chapter's *summary* — never its text. That keeps every
    chapter roughly the same size to ask for; sending the story so far would make
    a ten-chapter book cost several times what a three-chapter one does, on a
    tier that is rationed by request and by token.
    """
    chapters = plan["chapters"]
    this = chapters[index - 1]
    outline = "\n".join(
        f"{number}. {chapter['heading']} — {chapter['summary']}"
        for number, chapter in enumerate(chapters, start=1)
    )
    previous = chapters[index - 2]["summary"] if index > 1 else ""

    user = (
        f"Book: {plan['title']}\n"
        f"By: {plan['author']}\n"
        f"Voice: {plan['style_note']}\n\n"
        f"The whole book:\n{outline}\n\n"
        + (f"What just happened: {previous}\n\n" if previous else "")
        + f"Write chapter {index} of {len(chapters)}: {this['heading']}\n"
        f"It should cover: {this['summary']}"
    )
    system = _CHAPTER_SYSTEM.format(most=MAX_PARAGRAPHS, rules=STYLE_RULES)
    data = ask(system, user, "book_chapter", CHAPTER_SCHEMA)

    raw = data.get("paragraphs")
    if isinstance(raw, str):
        # Some models send the blob after all. Split it the way the editor would.
        raw = [piece for piece in raw.split("\n\n")]
    if not isinstance(raw, list):
        raise AIError(f"Chapter {index} came back in a shape that could not be read.")

    paragraphs = [clean_text(piece) for piece in raw[:MAX_PARAGRAPHS]]
    paragraphs = [piece for piece in paragraphs if piece]
    if not paragraphs:
        raise AIError(f"Chapter {index} came back empty.")

    return {
        "heading": clean_line(data.get("heading")) or this["heading"],
        "text": "\n\n".join(paragraphs),
    }


def to_manuscript(plan, chapters, design=None):
    """The plan and its chapters, as a book the editor can open.

    Built as a dictionary and handed to `Manuscript.from_dict` rather than
    assembled out of `Section` objects: that route already mints ids, coerces
    what is not a string and drops what makes no sense, and it is the same code
    path a saved draft comes back through.

    `design` is the design the editor already had. It is copied rather than
    shared, so that changing the new book's margins cannot reach back into the
    book this one replaced.
    """
    front = []
    if plan["dedication"]:
        front.append(
            {"kind": "dedication", "heading": "", "text": plan["dedication"]}
        )

    book = Manuscript.from_dict(
        {
            "title": plan["title"],
            "subtitle": plan["subtitle"],
            "author": plan["author"],
            "series": plan["series"],
            "front": front,
            "body": [
                {"kind": "chapter", "heading": chapter["heading"], "text": chapter["text"]}
                for chapter in chapters
            ],
            "back": [],
        }
    )
    if design is not None:
        book.design = replace(design)
    return book


# --------------------------------------------------------------------------
# The whole book
# --------------------------------------------------------------------------


def _say(progress, fraction, message):
    """Report progress.

    No `try`/`except` around the call, deliberately. The progress callback the
    app passes in is also what enforces the session's disk quota, and it reports
    that by raising. Swallowing it here would quietly turn the quota off.
    """
    if progress:
        progress(fraction, message)


def _check_allowed(config):
    """Refuse a model that could be charged for, before anything is sent.

    The account's own credit limit is the real control, but it is on the far side
    of a request. This one is on this side of it, so a mistyped model name in a
    Render environment variable becomes a sentence on the page rather than a
    charge on the account.
    """
    # Asked of the settings in hand rather than of the environment, so the
    # message is about the key this call is actually using.
    if not config.api_key:
        raise AIError(
            f"No {ai_config.KEY_VAR} is set on this copy, so there is nothing "
            "to write with."
        )
    if config.free_only and not config.is_free:
        raise AIError(
            f"“{config.model}” is not a free model, and this copy is set to "
            f"free models only. Set {ai_config.MODEL_VAR} to "
            f"“{ai_config.DEFAULT_MODEL}” or to a model ending in “:free”."
        )


def write_book(prompt, *, design=None, progress=None, config=None):
    """Write a whole book from one description.

    `prompt` is the sentence the visitor typed and `design` is the page setup the
    editor already had, which is carried across untouched. Neither the book on
    screen nor anything else from the session is passed in, and that is the point:
    there is no argument here through which somebody's manuscript could be sent
    to a third party.
    """
    config = config or ai_config.settings()
    _check_allowed(config)
    prompt = clean_prompt(prompt)

    started = time.monotonic()
    chat = _make_chat(config)
    ask = _asker(chat, config)

    _say(progress, 0.0, "Planning the book…")
    plan = build_outline(prompt, ask, config)
    total = len(plan["chapters"])
    _say(progress, 0.12, f"{total} chapters planned. Writing them now.")

    chapters = []
    for number, chapter in enumerate(plan["chapters"], start=1):
        if time.monotonic() - started > config.budget:
            raise AIError(
                f"This took too long and was stopped after {number - 1} of "
                f"{total} chapters. Free models can be slow when they are busy — "
                "try again, or ask for a shorter book."
            )
        _say(
            progress,
            0.12 + 0.85 * (number - 1) / total,
            f"Writing chapter {number} of {total}: {chapter['heading']}",
        )
        chapters.append(write_chapter(plan, number, ask, config))

    _say(progress, 0.98, "Putting the book together…")
    book = to_manuscript(plan, chapters, design)
    _say(progress, 1.0, "Done.")
    return book
