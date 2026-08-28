"""Ask a model for a whole book, and hand back a `Manuscript`.

This is the only file in the project that knows what a language model is. It is
kept apart on purpose: the rest of the app deals in paper sizes and folded
sheets, and none of it should have to care that this exists.

It does not import Streamlit. That keeps it testable with plain `unittest`, and
it means nothing here can reach into session state by accident — including the
API key, which must never end up there.

**Three requests, and a fixed number of chapters.** Both are budgets, not
preferences.

A request is the unit of everything costly: of waiting, of tokens paid for, and
on a copy set to a free model of a daily ration of about fifty shared by everyone
using it. A request per chapter spent six on a five-chapter book, so the chapters
are batched into the requests that are left after the outline — three in all,
counted by `_Budget` and enforced rather than hoped for.

The chapter count is fixed for a different reason. Left to choose, a model reads
"a short novel" as tone and not as a number: one such description came back with
twice the chapters of a request that had asked for a full-length one. So the
count is set in the schema, checked again after the reply, and printed on the
button, and a description that asks for something long gets that story told in
the same five chapters.

**Every chapter gets the same room.** Which is a third budget, and the one that
took a lopsided book to find. The tokens left after the outline are divided by
the chapters that have to share them, once, before any of them is asked for —
`chapter_allowance` — and the length the prompt asks for is cut to what that
buys — `paragraph_plan`. Neither was true before: a batch took a *share of what
was left when it was sent*, so an early batch that came back with nothing handed
its whole allowance to the next one, and the prompt asked every batch for the
same 320-word chapters however little it could afford. A book came back with
chapters of 28, 27, 22, 322 and 224 words. Both of those functions exist to stop
that, and the second is the load-bearing one: a chapter asked for three times
what it can pay for is not a short chapter, it is a chapter chopped mid-word and
salvaged into nothing.

A whole book still will not survive one request — models cap their output, small
ones especially, and a reply that runs out mid-chapter is not a short book but
broken JSON. What
makes batching safe is `salvage_chapters`: a truncated reply is mined for the
chapters that did finish, which costs nothing and is worth more than a retry the
budget cannot afford.

**Every reply is read for what arrived, the outline most of all.** That last part
was missing, and it cost a book its name. The plan has a cap of its own, and a
plan that went one character past it was thrown away whole — title, author,
dedication and every heading — after the reader had watched all of it arrive on
screen. The book that came out was called "Untitled book" with no front page at
all, which is not what a limit meant to shorten a book is for. So a truncated
object is now closed off and read (`close_json`, `salvage_outline`), the plan's
cap has room in it (`OUTLINE_CAP`), and a reply that merely ran out never buys a
repair that would run out in the same place (`ran_out`).

A reply that ran out does not arrive as a reply, though, and that is worth
knowing before reading `_invoke`. The OpenAI client refuses to hand over a
completion whose `finish_reason` is "length" — it raises instead, and throws the
words away with it. Since the ledger sends a `max_tokens` with every request,
filling the cap is the *ordinary* way a full batch ends rather than a rare
mishap. So `_is_cut_off` recognises that exception and `_cut_off_reply` takes the
chapters back out of it, before anything downstream can mistake a short book for
a broken one.

**Why it streams.** A model writing five chapters is not quick, and for most of
that time the old version of this file had nothing to show: one request went out,
and two minutes later a book appeared.

So the reply is *streamed*, and the words arrive as they are written rather than
all at once at the end. `write_book` hands them to its caller through `on_text`
as readable prose — see `_Live` and `stream_prose`, which read the paragraphs out
of a JSON object that is still only half-arrived. Nothing downstream changes: the
same complete text is parsed by the same `extract_json` when the stream ends. It
does not make the model faster, it makes the wait readable, and that was what
"slow" meant here.

Which provider answers is left to OpenRouter. The request carries no `provider`
block, so its own routing decides — see `_make_chat`.

**Why the JSON handling looks paranoid.** A small model is not a reliable one,
and which provider is serving it changes underneath you. Some honour a strict
JSON schema, some only understand "reply with JSON", some will wrap a perfectly
good object in ```json fences or a sentence of preamble. So the request is made
at the strongest rung the model will accept, the reply is parsed by something
that expects to be lied to, and one repair is asked for before giving up. That
ladder matters more with an eight-billion-parameter model than it ever did.
See `_invoke` and `extract_json`.

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
from copy import deepcopy
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

# How much of the description goes into a chapter request, as opposed to the
# outline request which gets all of it.
#
# The outline is written from the whole description; the chapters are written
# from the outline. Sending the description again with every batch cost more
# tokens than the chapters those tokens could have bought, so what goes with a
# batch is the first line or two of it, for flavour, and the plan does the work.
PREMISE_TOKENS = 60

# The longest a chapter is ever asked to be, and the shortest that is still
# prose rather than a note. `paragraph_plan` picks a length between the two, and
# picks it from what the chapter can actually pay for.
#
# The pair used to be the whole story: every chapter was asked for three or four
# paragraphs of eighty words however many tokens its batch had been given. That
# is the bug the floor and `paragraph_plan` exist to fix — see there.
WANTED_PARAGRAPHS = (3, 4)
WANTED_WORDS = 80
FEWEST_PARAGRAPHS = 1
FEWEST_WORDS = 25

# The hard ceiling validation applies, whatever was asked for.
MAX_PARAGRAPHS = 20

# What one word of finished prose costs in output tokens. Measured against a
# real tokenizer over ordinary English: about 1.35, and it barely moves.
TOKENS_PER_WORD = 1.35

# What the JSON around one chapter costs before a word of it is written — the
# number, the heading, the brackets, the quotes and the commas between the
# paragraphs. Subtracted before the words are counted rather than folded into
# the rate, because it is a fixed cost: on a chapter of eighty words it is
# noise, and on a chapter of forty it is a fifth of the bill.
CHAPTER_FRAMING = 25

# How much of a chapter's allowance the prompt actually asks for. The rest is
# the margin that lets a model which runs long still close its braces — and a
# chapter that closes is worth more than a chapter that was nearly longer.
ASK_FOR = 0.75

# The allowance at which a chapter is asked for its full length. There is no use
# giving one more than this: `paragraph_plan` stops growing here, so the tokens
# above it would only be a cap no reply comes near. Capping the allowance is
# also what stops a batch that had a windfall from writing chapters twice the
# length of the ones before it.
FULL_ALLOWANCE = CHAPTER_FRAMING + int(
    WANTED_PARAGRAPHS[1] * WANTED_WORDS * TOKENS_PER_WORD / ASK_FOR
)

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

# A repair does not carry the question that was asked, only the answer that was
# no good. Sending the whole original request again would cost twice over — the
# request and the failed reply both — and buy nothing: reformatting what has
# already been written does not need the instructions that produced it.
_REPAIR_SYSTEM = "You reformat text as JSON. Reply with the JSON object only."


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


def chapter_count():
    """How many chapters a book will have. For the button's own label.

    Read from the settings rather than hard-coded, so the number the button
    promises and the number the schema demands cannot drift apart.
    """
    return ai_config.settings().chapters


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


def _status(error):
    """The HTTP status on whatever the client raised, if there is one."""
    return getattr(error, "status_code", None) or getattr(
        getattr(error, "response", None), "status_code", None
    )


def _is_rate_limited(error):
    """Whether the service refused this request because it is busy.

    On a paid model this is not a limit of OpenRouter's own — those apply to the
    free variants — it is the provider upstream being out of capacity. Which
    matters, because it means waiting works: the request was refused, not
    rejected, and the same request a moment later usually goes through.
    """
    if _status(error) == 429:
        return True
    low = str(error).lower()
    return "rate limit" in low or "too many requests" in low


def _is_busy(error):
    """Whether waiting could plausibly help.

    A rate limit, a gateway that is having a moment, or a connection that did
    not open. Everything else — a bad key, a refused model, a request this app
    got wrong — is answered the same way however long it is left.
    """
    status = _status(error)
    if _is_rate_limited(error):
        return True
    if status and 500 <= int(status) < 600:
        return True
    return status is None and "connect" in str(error).lower()


# The longest a service's own `Retry-After` is believed. Past this it is not a
# busy moment any more, and a reader waiting on a progress bar is owed an answer
# rather than another two minutes of nothing.
MAX_RETRY_WAIT = 30.0

# What to wait when the service asked for nothing in particular. Doubled each
# time, so three attempts come to about twenty seconds in all.
RETRY_WAIT = 3.0


def _retry_after(error, fallback):
    """How long the service asked to be left alone, in seconds.

    Only the plain-seconds form is read. `Retry-After` may also carry an HTTP
    date, and parsing one means trusting a clock this app has no reason to
    trust — the fallback is a better answer than a wrong one.
    """
    headers = getattr(getattr(error, "response", None), "headers", None) or {}
    try:
        asked = float(headers.get("retry-after") or headers.get("Retry-After") or 0)
    except (TypeError, ValueError):
        return fallback
    if asked <= 0:
        return fallback
    return min(asked, MAX_RETRY_WAIT)


def _readable(error, config):
    """Turn whatever the client raised into an `AIError` worth reading.

    The original exception is deliberately not chained (`from None` at the raise
    site). Its traceback and its `__context__` can hold the request that caused
    it, and this app runs at a public URL.
    """
    text = scrub(error, config.api_key)
    status = _status(error)
    low = text.lower()

    if status in (401, 403) or "invalid api key" in low or "no auth" in low:
        return AIError(
            "The AI service would not accept this copy's key. Check "
            f"{ai_config.KEY_VAR}."
        )
    if status == 402 or "credit" in low or "quota" in low:
        return AIError("This copy has no credit left for the AI writer.")
    if _is_rate_limited(error):
        return AIError(
            "The AI writer has been asked for too much at once. Try again in a "
            "few minutes — the rate limit is on this copy as a whole, so a busy "
            "moment passes."
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


def _every_object(text):
    """Every balanced `{...}` in `text`, at any depth, in the order they close.

    Used to rescue a reply that ran out of room. Several chapters now share one
    answer, so a truncated reply is not nonsense — it is three good chapters and
    the beginning of a fourth, and throwing that away would cost a request the
    book does not have to spare.

    The starts are kept on a stack rather than only at depth zero, which matters
    precisely in the case this exists for: when the reply is cut off, the outer
    `{"chapters": [` never closes, so the only objects that ever balance are the
    chapters nested inside it.
    """
    found = []
    starts = []
    in_string = False
    escaped = False
    for index, char in enumerate(text):
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
            starts.append(index)
        elif char == "}" and starts:
            found.append(text[starts.pop() : index + 1])
    return found


def salvage_chapters(text):
    """`{"chapters": [...]}` rebuilt from whatever whole chapters survived.

    Returns `None` when there is nothing to rescue, so the caller can fall back
    to asking again.
    """
    rescued = []
    for chunk in _every_object(text):
        for attempt in (chunk, _TRAILING_COMMA.sub(r"\1", chunk)):
            try:
                data = json.loads(attempt, strict=False)
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and isinstance(data.get("paragraphs"), list):
                rescued.append(data)
            break
    return {"chapters": rescued} if rescued else None


def _closers(stack):
    """The brackets that would balance `stack`, innermost first."""
    return "".join("}" if opener == "{" else "]" for opener in reversed(stack))


# How many places a truncated reply is tried at before giving up. Every one is a
# `json.loads` of the whole fragment, and the useful ones are all at the end —
# the last thing to arrive whole is the last thing that was written. A book-sized
# reply has thousands of them and trying every one would be work with no answer
# in it.
_CUT_ATTEMPTS = 64


def _cut_points(text):
    """Every place a truncated JSON object could be closed off, in order.

    Each is `(index, closers)`: `text[:index] + closers` is balanced. A point is
    recorded wherever a *value* has just finished inside a container — the end of
    a string that is not a key, or the `}`/`]` that closes a nested value — so
    cutting at one always leaves whole entries behind and never half of one.

    The end of a key is deliberately not a cut point. `{"heading"` closed with a
    `}` is not an object with a missing value, it is invalid JSON, and the point
    before it is the one that gives a whole entry.
    """
    points = []
    stack = []
    in_string = False
    escaped = False
    is_key = False
    wants_key = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
                if not is_key and stack:
                    points.append((index + 1, _closers(stack)))
            continue
        if char == '"':
            in_string = True
            escaped = False
            is_key = wants_key
        elif char in "{[":
            stack.append(char)
            wants_key = char == "{"
        elif char in "}]":
            if stack:
                stack.pop()
            wants_key = bool(stack) and stack[-1] == "{"
            # Recorded at the outermost bracket too, where the closers are none
            # at all. That is the point an object which was whole after the cap
            # landed on it closes at, and without it a complete reply would be
            # the one shape this could not read.
            points.append((index + 1, _closers(stack)))
        elif char == ":":
            wants_key = False
        elif char == ",":
            # Back to a key inside an object; still a value inside an array.
            wants_key = bool(stack) and stack[-1] == "{"
    if in_string and not is_key and stack:
        # The reply stopped in the middle of a string. Closing it keeps the half
        # sentence that had arrived, which is worth having in a summary and is
        # the most that was ever written.
        #
        # The tail may be half an escape — a lone `\`, or `\u12` — because the
        # cut landed inside one, and a string ending that way will not parse. So
        # every tail from six characters shorter up to the whole of it is offered
        # and the caller takes the longest that `json` accepts. Six is the length
        # of the longest escape there is, which is why this cannot eat a word.
        closers = '"' + _closers(stack)
        for cut in range(6, -1, -1):
            if len(text) - cut > 0:
                points.append((len(text) - cut, closers))
    return points


def close_json(text):
    """Whatever arrived of a JSON object that stopped part-way, parsed.

    Returns `None` when there is nothing in `text` that can be closed into an
    object. This is what a reply that filled its output cap needs: it is not
    nonsense, it is a good object with its last brace missing, and everything
    before the cut is exactly what the model meant to say.

    The cut points are tried from the last backwards, so the answer is the most
    that survived rather than the first thing that happens to parse. Each attempt
    also gets `strict=False`, for the same reason `extract_json` does — a real
    newline inside a string is the commonest thing a model puts there.
    """
    text = str(text or "")
    start = text.find("{")
    if start < 0:
        return None
    body = text[start:]
    points = _cut_points(body)
    for index, closers in reversed(points[-_CUT_ATTEMPTS:]):
        candidate = body[:index] + closers
        for attempt in (candidate, _TRAILING_COMMA.sub(r"\1", candidate)):
            for strict in (True, False):
                try:
                    data = json.loads(attempt, strict=strict)
                except (ValueError, TypeError):
                    continue
                if isinstance(data, dict):
                    return data
    return None


def ran_out(text):
    """Whether this reply is an object that simply stops, rather than a bad one.

    The difference decides whether a repair is worth a request. A model that sent
    prose can be told to send JSON instead and often will; a reply that reached
    its output cap will reach it again in exactly the same place, and the request
    spent finding that out is one the chapters after it needed.

    The client says so by raising — but only when the request carried a
    `response_format`, which the bottom rung of the ladder does not. There the
    reply just ends, and this is the only thing that can tell. It asks the
    question directly: there is an object in here, it does not parse as it
    stands, and it does parse once its brackets are closed. That is what having
    run out means, and nothing else looks like it.
    """
    sliced = _first_object(str(text or ""))
    if not sliced:
        return False
    try:
        json.loads(sliced, strict=False)
    except (ValueError, TypeError):
        return close_json(text) is not None
    return False


def salvage_outline(text):
    """The plan rebuilt from an outline reply that ran out of room.

    The outline is the one request everything else is written from, and it used
    to be all or nothing: a reply chopped one character before its last brace
    threw away the title, the author, the dedication and every chapter heading,
    and the reader watched all of it arrive on screen and then got a book called
    "Untitled book". That is what this exists to stop.

    It is the same bargain `salvage_chapters` makes for a batch — take what
    arrived whole, cost nothing, ask for nothing again — and the plan is worth
    more of it, because a missing chapter costs the book a chapter while a
    missing plan costs it its name.

    Returns `None` when nothing usable survived, so the caller can still fall
    back to a repair.
    """
    data = close_json(text)
    if not isinstance(data, dict):
        return None
    # Every entry has to be an object with a heading on it. `build_outline`
    # numbers the chapters it is given, so an entry that is half a chapter would
    # take the place of one — and a `"chapters"` that came back as a number
    # rather than a list is a thing a model does about as often.
    raw = data.get("chapters")
    chapters = [
        entry for entry in (raw if isinstance(raw, list) else [])
        if isinstance(entry, dict) and clean_line(entry.get("heading"))
    ]
    if chapters:
        data["chapters"] = chapters
    elif "chapters" in data:
        del data["chapters"]
    named = any(clean_line(data.get(field)) for field in ("title", "author"))
    return data if named or chapters else None


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
# Reading the book out of a reply that has not finished arriving
# --------------------------------------------------------------------------
# The reply is streamed, so at any moment there is a piece of a JSON object in
# hand — `{"chapters": [{"number": 1, "heading": "Bent Wire", "paragraphs":
# ["It begins`. That is not parseable and never will be until the last token, so
# `json.loads` is no use here at all; what follows reads the strings straight out
# of the fragment instead.
#
# This is only ever used to put words on the screen. The finished reply still
# goes through `extract_json` exactly as before, so nothing that reaches a
# `Manuscript` has come through this code.


def _json_strings(fragment):
    """Every string *value* in a JSON fragment, as `(key, text)` pairs.

    `key` is the object key the value was given under — "paragraphs" for the
    prose, "heading" for a chapter title — so the caller can lay them out
    differently. Keys themselves are not returned as values, which is the whole
    difficulty: telling one from the other means knowing whether the fragment is
    inside an object and whether an object is at the point of taking a key. Hence
    the container stack.

    The last string is very often unfinished, because a stream is cut wherever
    the last token landed. It is returned too — a half-written sentence is
    exactly what somebody watching wants to see.
    """
    values = []
    stack = []  # "{" or "[", innermost last
    wants_key = False
    in_string = False
    escaped = False
    is_key = False
    start = 0
    key = ""

    for index, char in enumerate(fragment):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
                if is_key:
                    key = _unescape(fragment[start:index])
                else:
                    values.append((key, _unescape(fragment[start:index])))
            continue
        if char == '"':
            in_string = True
            escaped = False
            start = index + 1
            is_key = wants_key
        elif char in "{[":
            stack.append(char)
            wants_key = char == "{"
        elif char in "}]":
            if stack:
                stack.pop()
            wants_key = bool(stack) and stack[-1] == "{"
        elif char == ":":
            wants_key = False
        elif char == ",":
            # Back to a key inside an object; still a value inside an array.
            wants_key = bool(stack) and stack[-1] == "{"

    if in_string and not is_key:
        values.append((key, _unescape(fragment[start:])))
    return values


def _unescape(raw):
    """One JSON string body, with its escapes undone.

    The end of it may be half an escape — `\\` on its own, or `\\u12` — because
    the stream stopped mid-character. Rather than write an unescaper, the tail is
    shortened a character at a time until `json` will take it: the longest escape
    is six characters, so this gives up long before it could eat a word.
    """
    for cut in range(7):
        piece = raw[: len(raw) - cut] if cut else raw
        try:
            return json.loads(f'"{piece}"')
        except ValueError:
            continue
    return ""


# Sent with the outline and used to write with, never printed in the book. It
# would only be noise on the screen.
_NOT_SHOWN = {"style_note", "number"}


def stream_prose(fragment):
    """The words in a part-arrived reply, laid out to be read.

    Works for both questions without being told which it is looking at, because
    both answer with string values and the interesting ones are the long ones. An
    outline shows as its title and chapter summaries; a batch of chapters shows
    as headings and paragraphs.
    """
    lines = []
    for key, value in _json_strings(fragment):
        if key in _NOT_SHOWN:
            continue
        value = " ".join(str(value).split())
        if not value:
            continue
        lines.append(f"**{value}**" if key in ("title", "heading") else value)
    return "\n\n".join(lines)


class _Live:
    """The book as it is being written, kept for whoever is watching it.

    One request is one reply, but a book is three of them, and a reader should
    not watch chapter four wipe chapters one to three off the screen. So the
    prose of a request that finished is `keep`-ed and everything after it is
    added on the end.

    `sink` is only ever a display. It must not be where a limit is enforced —
    that is `progress`'s job, and `_say` deliberately lets it raise — because a
    display that throws here switches itself off and lets the book carry on
    rather than losing four written chapters to a drawing failure.
    """

    def __init__(self, sink=None):
        self.sink = sink
        self.kept = []
        self.here = ""

    def __bool__(self):
        """Whether anybody is watching, i.e. whether to stream at all."""
        return self.sink is not None

    def feed(self, fragment):
        """What has arrived of the reply in flight.

        It replaces the reply before it rather than being added to it, which is
        what makes a retry, a repair and the move from the outline to the first
        chapter all look the same on screen: the old words stay up until there
        are new ones to put in their place, and then they are gone.
        """
        self.here = stream_prose(fragment)
        self._show()

    def keep(self):
        """That reply is finished and is part of the book. Hold on to it."""
        if self.here:
            self.kept.append(self.here)
        self.here = ""

    def _show(self):
        if self.sink is None:
            return
        try:
            self.sink("\n\n".join(part for part in self.kept + [self.here] if part))
        except Exception:  # pragma: no cover - a broken display is not a broken book
            self.sink = None


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

    # `max_retries=0` on purpose. The client's own retry is invisible: it honours
    # a `Retry-After` for up to two minutes, and it does that inside one call, so
    # nothing here can report the wait, count it against the book's clock, or
    # decide it is not worth having. `_wait_out_rate_limits` does all three.
    return ChatOpenAI(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=TEMPERATURE,
        timeout=config.timeout,
        max_retries=0,
        default_headers={"X-Title": config.app_title},
    )


def _bind(chat, config, rung, schema_name, schema, cap):
    """`chat`, told how firmly to insist on JSON and how long it may go on for.

    Bound onto the runnable rather than built into the client so that one chat
    object serves every rung — and every cap, which is the reason it now has to
    be per request rather than per client: each one gets whatever the ledger can
    still afford it. Passing `response_format` through `model_kwargs` instead
    collides with `ChatOpenAI`'s own handling of it on some releases; `.bind()`
    does not.

    `max_tokens` is the half of the token limit this app does not enforce
    itself. The service enforces it, which is what makes it a limit rather than
    an intention.

    The `provider` block goes through `extra_body` rather than as a keyword,
    because it is OpenRouter's own field and not part of the OpenAI wire format
    the client knows: a keyword would be renamed or dropped, and `extra_body` is
    copied into the request body verbatim. It is routing metadata rather than
    prompt content — OpenRouter reads it and strips it — so it costs no tokens
    and `estimate_request` is right not to count it.
    """
    bound = {"max_tokens": int(cap)}
    if config.provider:
        bound["extra_body"] = {"provider": config.provider}
    if rung == "json_schema":
        bound["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        }
    elif rung == "json_object":
        bound["response_format"] = {"type": "json_object"}
    return chat.bind(**bound)


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


# The sentence the OpenAI client puts on a reply it would not hand over, and the
# class it raises. Either one is enough to recognise it.
_CUT_OFF = "length limit was reached"
_CUT_OFF_CLASS = "LengthFinishReasonError"


def _is_cut_off(error):
    """Whether this is the client throwing away a reply that filled its cap.

    Not a failure, however firmly the client puts it. `openai` raises
    `LengthFinishReasonError` whenever a reply ends because it ran out of
    `max_tokens` *and* the request carried a `response_format` — which is every
    request this app makes at its top two rungs, and every request carries a
    `max_tokens` now that the ledger sets one. So the ordinary end of a batch
    asked for exactly as many tokens as the budget could spare arrives here as an
    exception, carrying the chapters it managed to write.

    That is the whole of the bug this exists for: the app has always treated a
    cut-off reply as a short book (`salvage_chapters`, `from_plan`), and the
    client turned it into a red banner instead.

    Matched by class name and by message rather than caught as `openai.…`,
    because nothing in this module may import the client — see `_make_chat`.
    """
    if type(error).__name__ == _CUT_OFF_CLASS:
        return True
    return _CUT_OFF in str(error).lower()


def _cut_off_reply(error):
    """`(text, usage)` taken back out of a cut-off reply. Either may be missing.

    The exception carries the completion it refused to parse, so the words are
    right there for the asking. Read defensively at every step: this is somebody
    else's object, its shape is theirs to change, and the only thing worse than a
    short book here would be a second exception on top of the first.
    """
    completion = getattr(error, "completion", None)
    parts = []
    for choice in getattr(completion, "choices", None) or []:
        said = getattr(getattr(choice, "message", None), "content", None)
        if isinstance(said, str):
            parts.append(said)
    total = getattr(getattr(completion, "usage", None), "total_tokens", None)
    return "".join(parts), total if isinstance(total, int) and total > 0 else None


def _read_stream(runnable, messages, live):
    """One request, read a piece at a time, reported as it goes.

    Returns `(text, usage, cut_off)`. The pieces are joined and handed back as
    one string, so everything after this line — `extract_json`,
    `salvage_chapters`, the repair — sees precisely what it would have seen from
    `.invoke()`. Streaming is a way of watching the reply arrive, not a different
    kind of reply.

    That holds for a reply that fills its cap too, and it takes this `try` to
    make it hold. Such a reply streams to the very last piece and then raises,
    because the client parses the finished completion once the stream has run
    out — so every word is already in `parts` and on screen by the time the
    exception arrives. Losing them to it would be losing chapters that had been
    written, read, and paid for.

    `cut_off` is the third thing it hands back, and it is not decoration. The
    caller decides whether to spend a request repairing an unreadable reply, and
    a reply that merely ran out of room must never earn one — the repair carries
    the same cap and stops in the same place. Swallowing the exception here
    without saying so made every streamed cut-off look like a model that had
    answered badly, so a truncated outline quietly spent the request that the
    chapters after it needed. Streaming is the default, so that was the ordinary
    path rather than a corner of it.
    """
    parts = []
    usage = None
    cut_off = False
    try:
        for chunk in runnable.stream(messages):
            usage = _usage(chunk) or usage
            piece = _content(chunk)
            if not piece:
                continue
            parts.append(piece)
            live.feed("".join(parts))
    except Exception as error:
        if not _is_cut_off(error):
            raise
        cut_off = True
        # The completion on the exception should say the same as the pieces
        # already gathered. Preferred when it says more, since a stream that
        # failed early is the one case where it can.
        said, counted = _cut_off_reply(error)
        usage = counted or usage
        if len(said) > len("".join(parts)):
            parts = [said]
            live.feed(said)
    return "".join(parts), usage, cut_off


def _usage(reply):
    """What the service said the request cost, if it said anything.

    Present on an ordinary reply and absent from a streamed one, which is why
    the ledger is built to work without it — see `_Charge.settle`. Read here
    because when it is there it is exact, and exact beats generous.
    """
    counted = getattr(reply, "usage_metadata", None)
    if isinstance(counted, dict):
        total = counted.get("total_tokens")
        if isinstance(total, int) and total > 0:
            return total
    return None


class _Pace:
    """The least time between one request and the next.

    Three requests fired back to back is the pattern most likely to trip a
    provider's burst limit, and three back to back is exactly what a book is.
    A second between them costs three seconds on something that takes minutes.

    Held per book rather than on a module global, because two visitors writing
    at once are two separate conversations with the service and neither should
    be made to wait on the other.
    """

    def __init__(self, gap):
        self.gap = max(0.0, float(gap))
        self.last = 0.0

    def wait(self):
        """Hold until this request is allowed to go.

        A pace of nothing reads no clock at all, rather than reading one and
        finding it has nothing to wait for. It is the same answer either way,
        and not asking is the honest way to say "this does not apply".
        """
        if not self.gap:
            return
        if self.last:
            due = self.last + self.gap
            now = time.monotonic()
            if now < due:
                time.sleep(due - now)
        self.last = time.monotonic()


def _wait_out_rate_limits(send, config, note=None, started=None):
    """`send()`, tried again for as long as the service is only busy.

    A 429 is not the request being wrong, it is the provider being full — so the
    same request a moment later usually goes through, and giving up on the first
    one turns a busy minute into a book that could not be written.

    Three things bound the waiting, and all three matter:

    * `config.retries` caps how many times one request is re-sent. Re-sent, not
      re-charged: a refused request generated nothing and was not billed, so the
      caller's `budget.take()` covers every attempt and no second request is
      claimed for it.
    * `config.budget` is the whole book's clock, and a wait that would run past
      it is not started at all. Waiting is only worth it if there is still time
      to use what comes back.
    * `MAX_RETRY_WAIT` caps what a service's own `Retry-After` can ask for.

    `note` is told about the wait. Somebody watching a progress bar deserves to
    know the difference between a slow model and a stopped one — and it is the
    app's quota check as well, which is why nothing here swallows its exception.
    """
    for attempt in range(max(0, config.retries) + 1):
        try:
            return send()
        except Exception as error:
            if attempt >= config.retries or not _is_busy(error):
                raise
            wait = _retry_after(error, RETRY_WAIT * (2**attempt))
            if started is not None and time.monotonic() - started + wait > config.budget:
                raise
            if note:
                note(
                    f"The AI service is busy. Waiting {int(round(wait))}s and "
                    "trying again…"
                )
            time.sleep(wait)
    # Unreachable: the loop either returns or raises on its last pass.
    raise AIError("The AI service is busy.")  # pragma: no cover


def _invoke(chat, config, messages, schema_name, schema, budget, live, ledger, cap,
            pace=None, note=None, started=None, essential=False):
    """One request, starting at the strongest format this model has accepted.

    `cap` is the output ceiling the ledger has allowed this request, and it is
    charged for in full before the request goes out. Every rung costs its own
    charge: a downgrade is another request, and the point of the ledger is that
    nothing goes out uncounted.

    Returns `(reply, cut_off)`, where `cut_off` says the reply stops mid-sentence
    because it reached that cap. The caller wants to know: a cut-off reply is
    worth salvaging and is not worth repairing, since a repair would be sent with
    a cap no bigger and cut off in the same place.

    Returns `(None, False)` when there were no requests left to make one with —
    which the caller turns into chapters written from the outline, not into an
    error. A service that answered badly still raises; only a budget goes quiet.

    `essential` says whether this request is one the book cannot go on without.
    The outline is: there is no plan to write the chapters from if it never
    arrives, so a service that stays busy through every retry has to be reported.
    A batch of chapters is not, and goes quiet the same way a spent budget does —
    those chapters come from the plan and the reader gets a book.
    """
    live = live or _Live()
    start = _RUNG.get(config.model, RUNGS[0])
    rungs = RUNGS[RUNGS.index(start) :]
    for index, rung in enumerate(rungs):
        failure = None
        # A rung below `json_schema` does not send the schema, so it costs less
        # to send. Counted at what this rung actually carries.
        cost = estimate_request(messages, schema if rung == "json_schema" else None)
        # Asked again here, and not only by the caller, because a downgrade is a
        # second request the caller never budgeted for: it worked out what one
        # attempt could afford, and this is the third. Without this line a model
        # that refuses two rungs could walk the total past the limit.
        allowed = min(cap, ledger.left - cost - TOKEN_RESERVE)
        if allowed < MIN_OUTPUT_TOKENS or not budget.take():
            return None, False
        charge = ledger.take(cost, allowed)
        cut_off = False

        def send():
            """This request, once. Retried by the caller while the service is busy.

            `(text, usage, cut_off)` whichever way the reply came back, so the
            two paths cannot disagree about what a cut-off reply is. A streamed
            one says so in its third value; an ordinary one says so by raising,
            and is caught below.
            """
            if pace:
                pace.wait()
            runnable = _bind(chat, config, rung, schema_name, schema, allowed)
            if live and config.stream:
                return _read_stream(runnable, messages, live)
            answered = runnable.invoke(messages)
            return _content(answered), _usage(answered), False

        try:
            reply, usage, cut_off = _wait_out_rate_limits(send, config, note, started)
        except Exception as error:
            if _is_cut_off(error):
                # Not a failure. The reply reached the cap this very request
                # sent with it, the rung it was sent at plainly worked, and the
                # words are on the exception. So this goes on down the ordinary
                # path with a reply that happens to stop mid-sentence — which is
                # the thing `salvage_chapters` was written for.
                cut_off = True
                reply, usage = _cut_off_reply(error)
            else:
                charge.failed()
                if _is_format_problem(error, config):
                    # Down a rung — or, when this was the last one, out of the
                    # loop and back with nothing. A model that will not answer in
                    # JSON at any of the three is not something the reader can
                    # act on, and the chapters it did not write can be written
                    # from the plan for nothing.
                    continue
                if _is_busy(error) and not essential:
                    # Still busy after every retry, on a request the book can do
                    # without. The chapters it would have carried are written
                    # from the plan instead — a shorter book rather than a
                    # banner, which is what every other limit here does too.
                    return None, False
                # A bad key, no credit, a service that is not answering, or a
                # rate limit on the one request there is no plan without. Those
                # the reader has to be told about.
                failure = _readable(error, config)
        # Raised out here rather than inside the `except`, which matters more
        # than it looks. `raise ... from None` only stops Python *printing* the
        # original; the exception object stays reachable as `__context__`, and
        # that object is the one carrying the request — and possibly the key —
        # in its message. Once the except block has ended there is no exception
        # being handled, so this one is raised with no context at all.
        if failure is not None:
            raise failure
        reply = _content(reply)
        charge.settle(reply, usage)
        _RUNG[config.model] = rung
        return reply, cut_off
    # Every rung refused. `None` here means what it means everywhere else in
    # this file: this could not be asked for, so it will be written from the
    # plan instead.
    return None, False


# --------------------------------------------------------------------------
# The token budget
# --------------------------------------------------------------------------
# A book may cost `AI_TOKEN_LIMIT` tokens, input and output, start to finish —
# 8,000 by default, and the number lives in `ai_config` so it can be read in one
# place and changed in one place. Not on
# average — ever. That is a hard number, and the way it is kept is worth stating
# plainly, because "we ask nicely and hope" is the usual way and it is not this.
#
# Two halves, and each is bounded before the request leaves:
#
# * **Output** is bounded by the service. Every request carries `max_tokens`,
#   which is a cap the provider enforces, so a reply cannot come back longer
#   than the number sent with it however talkative the model feels.
# * **Input** is bounded because this file wrote it. It is measured before it is
#   sent by `estimate_tokens`, which deliberately guesses high.
#
# So the worst a request can possibly come to is `estimated input + max_tokens`,
# and a request is only sent when that whole worst case still fits in what is
# left. Charge the worst case, send, then give back what was not used. The
# running total can therefore never pass the limit — not on a slow model, not on
# a retry, not on a repair.
#
# And when there is not enough left for a request, the answer is never an error.
# The chapters that could not be asked for are written from their own outline
# summaries instead, which costs nothing at all. The limit can make a book
# shorter. It cannot make it fail.

# Kept back from the last request rather than spent, so that a small error in
# the estimate on the far side of it still lands inside the limit.
#
# Both estimators read high, so this is not where the safety comes from — it is
# the margin on top of it. Sized to cover the one thing neither estimator can
# promise: a reply whose own script prices worse than anything measured.
TOKEN_RESERVE = 400

# Below this an output cap is not worth sending: the reply would be cut off
# mid-chapter and salvaged into nothing.
MIN_OUTPUT_TOKENS = 160

# What a chapter's allowance actually comes to on the ledger's books, per token
# of that allowance. Two corrections that pull opposite ways:
#
# * A reply is measured by `estimate_tokens` when the service sends no `usage`,
#   which is the ordinary case for a streamed one — and that reads about half
#   again high. So the ledger records a batch spending more than its cap.
# * But the reply is only ever as long as `paragraph_plan` asked for, which is
#   `ASK_FOR` of the allowance rather than all of it.
#
# Planning at the cap ignores the second and starves the last batch; planning at
# the words asked for ignores the first and does the same. Both were ways a book
# came back with three chapters of one sentence and two of three hundred words.
SETTLE_FACTOR = 1.5 * ASK_FOR

# The most the outline may have. It is a page of headings, not prose, and every
# token it does not take is a token of the book itself.
#
# It is a cap and not an allowance, which is the thing to hold on to when reading
# the number: what the outline actually spends is what it writes, because
# `_Charge.settle` hands back the rest before a single chapter is priced. So this
# costs the book nothing until an outline really is this long.
#
# It was 320, and 320 was too near. A perfectly ordinary five-chapter plan — a
# title, a subtitle, an author, a dedication, a note on the voice and five
# headings with a sentence each — measures about 280 tokens against a real
# tokenizer, so a description with named characters in it went over the cap and
# was chopped. Before `salvage_outline` that lost the whole plan; now it loses
# only the tail, and at this number it does not usually lose anything.
OUTLINE_CAP = 640

# Added per message for the role framing the wire format puts around it.
MESSAGE_OVERHEAD = 8


# Two estimators, and which one is used depends on who wrote the text.
#
# There is no getting round this. A token is not a fixed number of bytes: measured
# against a real tokenizer, ordinary English prose runs about 4.5 bytes to the
# token, but a line of pure punctuation runs 1.6 and a string of combining
# accents runs 1.2. So a single ratio is either wasteful for the text this app
# actually sends or unsafe for text somebody could paste into the box.
#
# Hence the split. Prompt text is written in this file, so its ratio can be
# measured and kept — `test_the_estimate_is_never_below_a_real_tokenizer` does
# exactly that against every prompt the app sends. Anything that has been outside
# — a typed description, a model's own reply quoted back to it — gets the ratio
# that nothing realistic beats.
#
# Not `tiktoken`, though it is installed. Its encodings are OpenAI's, not this
# model's, and `get_encoding` fetches the table over the network the first time
# it is asked — a download in the middle of somebody's book, on a container that
# may not be allowed to make it. It is used in the tests, where a network is
# allowed and a wrong answer is a failure rather than a bill.

# Three UTF-8 bytes to the token for ASCII, one and a half for anything else.
_OURS = (3.0, 1.5)
# One and a fifth bytes to the token, whatever the bytes are. Below every ratio
# measured across sixteen scripts, punctuation and emoji included.
_THEIRS = (1.2, 1.2)


def _estimate(text, ratios):
    data = str(text or "").encode("utf-8")
    plain = sum(1 for byte in data if byte < 0x80)
    ascii_ratio, other_ratio = ratios
    return int(plain / ascii_ratio + (len(data) - plain) / other_ratio) + 1


def estimate_tokens(text):
    """An over-estimate of what text this file wrote costs to send."""
    return _estimate(text, _OURS)


def estimate_untrusted(text):
    """An over-estimate of what text from anywhere else costs to send.

    Deliberately about three times what English really costs. That is the price
    of not having to trust it, and it is paid in a slightly shorter book rather
    than in a limit that only usually holds.
    """
    return _estimate(text, _THEIRS)


def estimate_request(messages, schema=None):
    """What one request costs to send, counting everything that goes with it.

    A "system" message is this file's own words. Everything else — the human
    turn carrying the description, an "ai" turn quoting a reply back for repair —
    is charged at the untrusted rate.

    The schema counts. A `response_format` of type `json_schema` is part of the
    request body and is charged for like any other input, which is easy to forget
    and worth several hundred tokens a book.
    """
    total = 0
    for role, text in messages:
        measure = estimate_tokens if role == "system" else estimate_untrusted
        total += measure(text) + MESSAGE_OVERHEAD
    if schema is not None:
        total += estimate_tokens(json.dumps(schema))
    return total


class _Charge:
    """One request's worst case, held against the ledger until it is settled."""

    def __init__(self, ledger, cost, cap):
        self.ledger = ledger
        self.cost = cost
        self.cap = cap

    @property
    def reserved(self):
        return self.cost + self.cap

    def settle(self, reply, usage=None):
        """Hand back whatever the reply did not use.

        `usage` is the service's own count when it sent one, and it is believed
        in both directions: a reply that somehow cost *more* than was reserved
        adds the difference rather than being quietly clamped, so that a provider
        ignoring `max_tokens` shrinks the requests after it instead of going
        unnoticed.

        With no `usage` — the ordinary case, since a streamed reply does not
        carry one unless it is asked for — the input estimate stands and the
        reply itself is measured the same over-generous way.
        """
        if usage:
            actual = int(usage)
        else:
            actual = self.cost + estimate_tokens(reply)
        self.ledger.spent += actual - self.reserved
        self.ledger.used.append(actual)

    def failed(self):
        """Nothing came back. Give up the output half; keep the input charged.

        A request that raised may still have been read by the model before it
        did, and charging for that is the safe direction to be wrong in.
        """
        self.ledger.spent -= self.cap


class _Ledger:
    """Every token one book may cost, counted before it is spent.

    `spent` is never behind the truth. It is put *up* by the worst a request
    could come to before that request is made, and only brought back down once
    the reply is in hand — so at every moment between those two, the number
    already covers a reply that has not arrived yet.
    """

    def __init__(self, limit):
        self.limit = max(0, int(limit))
        self.spent = 0
        # What each finished request actually came to. Only the tests read it,
        # and they read it to prove the total.
        self.used = []

    @property
    def left(self):
        return self.limit - self.spent

    def room_for(self, messages, schema=None, later=0):
        """`(cost, room)` — what this request costs to send, and what is left
        over for it to reply with.

        `later` is how many more requests of about this size are still to come.
        Their input is held back here, so an early batch cannot spend the tokens
        a later one needs to exist at all.
        """
        cost = estimate_request(messages, schema)
        room = self.left - cost - later * cost - TOKEN_RESERVE
        return cost, max(0, room)

    def take(self, cost, cap):
        """Charge the worst this request could possibly come to, and send it."""
        self.spent += cost + cap
        return _Charge(self, cost, cap)


class _Budget:
    """The requests one book is allowed, counted rather than assumed.

    A request is what costs — time on every copy, tokens on a paid one, and a
    share of a rationed fifty a day on a free one. So the limit has to be a
    limit: every attempt costs one, including a rung downgrade and including a
    repair.

    A request the service *refused* does not, and that is the one exception.
    It generated nothing, it was not billed, and it did not come off any daily
    ration — so the retries in `_wait_out_rate_limits` all happen inside the one
    claim the caller already made. What is rationed here is work asked for, not
    packets sent.

    Running out is not an error, and this is the one thing worth being careful
    about. It used to raise, and raising is what turned a budget into a broken
    book: a model that spent a request working out which JSON format it accepts
    left too few for five chapters, and the reader got a red banner instead of
    anything at all. Now `take` simply says no, the chapters that were not asked
    for are written from the outline, and the reader gets a book. Every other
    limit in this file behaves the same way, and for the same reason.
    """

    def __init__(self, allowed):
        self.allowed = max(1, allowed)
        self.spent = 0

    @property
    def left(self):
        return self.allowed - self.spent

    def take(self):
        """Claim a request. `False` when there is not one to claim."""
        if self.left <= 0:
            return False
        self.spent += 1
        return True


def _asker(chat, config, budget, live=None, ledger=None,
           pace=None, note=None, started=None):
    """A function that asks one question and gets one dictionary back.

    A repair is allowed only when both budgets can pay for it. A model that has
    produced prose once will often produce JSON when told so plainly, but not at
    the cost of the chapters that have not been written yet — and a repair is
    expensive in tokens as well as in requests, because the reply being repaired
    is sent back with it.

    `keep` says whether the answer is part of the book being read on screen. The
    chapters are; the outline is shown while it arrives and then makes way for
    them, because a plan is not the book.

    **`None` means "could not be afforded", and it is not a failure.** It is the
    one thing every caller here has to handle, because handling it is what turns
    a token limit into a shorter book instead of an error message.
    """
    live = live or _Live()
    ledger = ledger if ledger is not None else _Ledger(10**9)
    pace = pace or _Pace(0)

    def ask(
        system,
        user,
        schema_name,
        schema,
        salvage=None,
        keep=False,
        share=1.0,
        later=0,
        ceiling=None,
        essential=False,
    ):
        # `system` may be a function of the output cap rather than a string. A
        # chapter request tells the model how long to make a chapter, and a
        # length the cap cannot hold is a reply chopped mid-chapter — so the two
        # have to be settled together. The cap is worked out from the nominal
        # wording, since every wording `length_asked` produces is within a token
        # or two of every other, and the request is then built with the wording
        # that matches the cap actually granted.
        sized = system if callable(system) else None
        messages = [("system", sized(None) if sized else system), ("human", user)]
        # `share` is this request's portion of what is left — two chapters out of
        # the five still to write is two fifths of it — and `later` holds back
        # what the requests after this one will need simply to be sent.
        def afford(for_messages):
            """The output cap these messages may have. `0` for "do not send"."""
            _cost, room = ledger.room_for(for_messages, schema, later=later)
            allowed = int(room * share)
            if ceiling is not None:
                allowed = min(allowed, ceiling)
            return allowed if allowed >= MIN_OUTPUT_TOKENS else 0

        def repair(reply):
            """One more try, if both budgets can still pay for one."""
            if budget.left <= 0:
                return None
            again = [("system", _REPAIR_SYSTEM), ("ai", reply), ("human", _REPAIR)]
            cap = afford(again)
            if not cap:
                return None
            mended, _ = _invoke(
                chat, config, again, schema_name, schema,
                budget, live, ledger, cap,
                pace=pace, note=note, started=started,
            )
            if mended is None:
                return None
            try:
                return extract_json(mended)
            except AIError:
                return None

        if budget.left <= 0:
            return None
        cap = afford(messages)
        if not cap:
            return None
        if sized:
            messages = [("system", sized(cap)), ("human", user)]

        reply, cut_off = _invoke(
            chat, config, messages, schema_name, schema, budget, live, ledger, cap,
            pace=pace, note=note, started=started, essential=essential,
        )
        if reply is None:
            return None
        try:
            data = extract_json(reply)
        except AIError:
            # A truncated reply is not nonsense, it is a reply that ran out.
            # Whatever whole objects are in it are worth more than a retry,
            # and cost nothing.
            data = None
            if salvage is not None:
                data = salvage(reply) or None
            # A reply that ran out of room is not repaired. A repair is another
            # request, sent with a cap no bigger than the one this reply had
            # just filled, so it would run out in the same place — and the
            # request it spent is one the chapters after this one needed. What
            # could not be salvaged is written from the plan instead.
            #
            # Asked two ways because it arrives two ways. `cut_off` is the client
            # having refused the reply, which it only does when the request
            # carried a `response_format`; on the bottom rung there is no
            # exception and the reply simply ends, and `ran_out` is what
            # recognises that one.
            if data is None and not cut_off and not ran_out(reply):
                data = repair(reply)
        if keep and data is not None:
            live.keep()
        return data

    return ask


# --------------------------------------------------------------------------
# What to ask for
# --------------------------------------------------------------------------

# The formatting the typesetter actually implements. Anything outside it is not
# ignored, it is printed literally — a stray "- " becomes a hyphen in the middle
# of a paragraph of a finished book — so the rules are stated to the model, and
# `clean_text` cleans up what comes through anyway.
#
# Shorter than it was, and every word that went was paid for in chapters: a
# system prompt is sent with every request, so a hundred tokens of housekeeping
# here is a hundred tokens of prose the book does not get. What is left is the
# markup the typesetter can print and the ban on everything it cannot.
STYLE_RULES = """\
Plain prose only. One paragraph per string, no line breaks inside one.
*italic*, **bold**, a paragraph of *** for a scene break, "> " to start a quote.
Nothing else: no lists, tables, links, footnotes, code or headings."""

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


def outline_schema(chapters):
    """The outline schema, pinned to exactly `chapters` entries.

    `minItems` and `maxItems` are set to the same number on purpose. Asking in
    the prompt alone does not hold: a description saying "a short novel" came
    back with ten chapters where one asking for a full novel got five, because a
    model reads a word like "short" as tone rather than as a count. The schema is
    the only part of the request the model cannot interpret.
    """
    schema = deepcopy(OUTLINE_SCHEMA)
    schema["properties"]["chapters"]["minItems"] = chapters
    schema["properties"]["chapters"]["maxItems"] = chapters
    return schema


BATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["chapters"],
    "properties": {
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["number", "paragraphs"],
                "properties": {
                    # The number is asked for so a short reply can still be
                    # matched to the chapters it holds rather than guessed at
                    # by position.
                    "number": {"type": "integer"},
                    "heading": {"type": "string"},
                    "paragraphs": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


def batch_schema(count):
    schema = deepcopy(BATCH_SCHEMA)
    schema["properties"]["chapters"]["minItems"] = count
    schema["properties"]["chapters"]["maxItems"] = count
    return schema


# Both of these are written to be short before they are written to be nice. They
# go out with every request, so their length is subtracted from the book — see
# `_Ledger`. What survived the cutting is the part that changes what comes back:
# the shape, the count, and the ban on prefaces.
_OUTLINE_SYSTEM = """\
You plan short books to be printed and sewn by hand. Reply with one JSON object
and nothing else:
{{"title":"","subtitle":"","author":"","dedication":"","style_note":"",
 "chapters":[{{"heading":"","summary":""}}]}}

- "chapters" holds exactly {chapters} entries. Not more, not fewer, whatever the
  description says about length — "short" and "epic" are tone, not counts.
- "summary": one sentence on what happens. The {chapters} together must tell the
  whole story, beginning to end.
- "author": an invented name, never a real person.
- "dedication": one short line, or "".
- "style_note": one sentence on voice and tense. Not printed."""

_BATCH_SYSTEM = """\
You write the chapters of a short book from its plan. Reply with one JSON object
and nothing else:
{{"chapters":[{{"number":1,"heading":"","paragraphs":["",""]}}]}}

- Exactly the chapters asked for, in order, each keeping its "number".
- {length} Keep to it: a reply that runs out mid-sentence loses the chapter it
  was in, and every chapter here has the same room as every other.
- Continue the book's voice. No preface, no summary, no explaining, and do not
  repeat the heading inside the paragraphs.

{rules}"""


def paragraph_plan(allowance):
    """`(fewest, most, words)` — the chapter that `allowance` tokens can buy.

    This is the answer to a book whose chapters came back 28, 27, 22, 322 and 224
    words long, and the reason it happened is worth stating: the prompt used to
    ask for three or four paragraphs of eighty words *whatever* the batch could
    afford. That is about 430 tokens of chapter, against an allowance that is
    often half of it. A model does what the prompt says, so the reply ran past
    its cap and was chopped — and a chapter chopped before its closing brace is
    not a short chapter, it is no chapter at all. The batch holding chapters one
    to three was asked for three times what it could pay for and lost all three;
    the batch after it inherited their tokens and wrote 322 words a chapter.

    So the ask is cut to fit. Words come down first, because a chapter of three
    short paragraphs still reads as a chapter; only when the words reach the
    floor do the paragraphs go, down to a single one at the very bottom.

    Nothing here is ever rounded *up* to a floor. A chapter asked for more than
    it can hold is the failure this exists to prevent, and one short paragraph
    that arrives beats two that are chopped in half.
    """
    words = max(0, int((allowance - CHAPTER_FRAMING) * ASK_FOR / TOKENS_PER_WORD))
    most = WANTED_PARAGRAPHS[1]
    while most > FEWEST_PARAGRAPHS and words // most < FEWEST_WORDS:
        most -= 1
    each = min(WANTED_WORDS, words // most)
    return max(FEWEST_PARAGRAPHS, most - 1), most, max(1, each)


def length_asked(fewest, most, words):
    """The one sentence that tells the model how long a chapter is to be."""
    if most <= 1:
        return f"One paragraph each, of about {words} words."
    count = (
        f"Exactly {most} paragraphs"
        if fewest >= most
        else f"{fewest} to {most} paragraphs"
    )
    return f"{count} each, about {words} words a paragraph."


def batch_system(allowance):
    """The chapter-writing instructions, asking for a length that will fit."""
    return _BATCH_SYSTEM.format(
        length=length_asked(*paragraph_plan(allowance)), rules=STYLE_RULES
    )


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


def shorten(text, tokens):
    """`text` cut down to about `tokens` tokens, at a word boundary.

    Used on the description before it is sent with a batch of chapters, where
    the whole of it is not worth what it costs. Cut at a space so the model is
    given a phrase rather than half a word, and cut by the same over-estimate
    the ledger budgets with, so what is sent is never bigger than what was
    allowed for.
    """
    text = str(text or "")
    if estimate_untrusted(text) <= tokens:
        return text
    # Measured the untrusted way, because this is the description and the whole
    # reason for cutting it is that its cost has to be known rather than guessed.
    # Sliced on characters and re-checked, since a character is not a byte.
    cut = text
    while cut and estimate_untrusted(cut) > tokens:
        cut = cut[: int(len(cut) * 0.9)] if len(cut) > 20 else cut[:-1]
    spaced = cut.rsplit(" ", 1)[0] if " " in cut else cut
    return (spaced or cut).rstrip(" ,;:—-") + "…"


# The longest a title taken from the description may be, in words and then in
# characters. A title is a line on a title page, not a paragraph.
TITLE_WORDS = 8
TITLE_CHARS = 60

# What people put in front of what the book is actually about, and what makes it
# a preamble rather than part of the story. "Write a short book about a lighthouse
# keeper" should give "A lighthouse keeper"; "A quiet novel set on a canal boat
# that says something about grief" should give the boat, not the grief.
#
# So both tests have to pass: the words before it are few, and one of them says
# this is somebody asking for a book. Position alone was not enough — "about"
# turns up in the middle of an ordinary sentence about as often as it introduces
# one.
_ABOUT = (" about ", " concerning ", " telling of ", " on the subject of ")
_ASKING = {
    "book", "novel", "novella", "story", "tale", "mini-novel", "write", "writing",
    "generate", "create", "make", "please", "want", "like",
}
_LEAD_WORDS = 8


def title_from(prompt):
    """A working title taken from the description. `""` when there is none in it.

    Used only when the model's own title never arrived — see `build_outline`.
    Something the writer typed themselves, on the title page of a book about the
    thing they asked for, beats "Untitled book" by a distance: it names the book
    in the drafts list, in the file name of a build, and in the note the app
    prints when the writing is done.

    It does not pretend to be the model's title, and it is not meant to be kept.
    It is the first thing in the box on the writing view, ready to be typed over.
    """
    text = clean_line(prompt)
    if not text:
        return ""
    lowered = text.lower()
    for lead in _ABOUT:
        at = lowered.find(lead)
        if at < 0:
            continue
        before = lowered[:at].split()
        if len(before) <= _LEAD_WORDS and any(
            word.strip(".,;:!?-") in _ASKING for word in before
        ):
            text = text[at + len(lead) :]
            break
    # One clause. A description is often a paragraph of instructions and only
    # the first breath of it is a title.
    text = re.split(r"[.!?;:,\n]", text, maxsplit=1)[0]
    words = text.split()[:TITLE_WORDS]
    text = " ".join(words)[:TITLE_CHARS].strip(" ,;:—-")
    # Cut at a word rather than mid-word when the character limit was the one
    # that bit. Trimming the last word of a two-word title would leave one.
    if len(" ".join(words)) > TITLE_CHARS and " " in text:
        text = text.rsplit(" ", 1)[0]
    return (text[:1].upper() + text[1:]) if text else ""


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
    """Ask for the plan of the book, and make what comes back exactly right.

    The chapter count is forced twice over — once in the schema and once here.
    The schema is what a model that honours it obeys; this is what happens when
    one does not, and it is why the count is a promise the button can make in its
    own label rather than a hope.
    """
    wanted = config.chapters
    system = _OUTLINE_SYSTEM.format(chapters=wanted)
    data = ask(
        system,
        prompt,
        "book_outline",
        outline_schema(wanted),
        # A reply that ran out of room is read for what did arrive rather than
        # thrown away. The plan is the one answer the whole book is written from,
        # so losing all of it over a missing brace cost more than losing any
        # chapter could — see `salvage_outline`.
        salvage_outline,
        # A plan is a page of headings. Anything more is taken out of the book
        # the plan is for.
        ceiling=OUTLINE_CAP,
        # The one request the book cannot be written without. A budget that
        # cannot afford it still goes quiet — headings alone make a thin book
        # but they make one — while a service that stays busy through every
        # retry is a passing condition worth telling the reader to wait out.
        essential=True,
    )
    # `None` is the ledger saying there was not enough left even for this, and an
    # unreadable reply is the model saying the same thing differently. Neither is
    # an error: a plan can be made here, for nothing, and a book with plain
    # chapter headings is a book.
    raw = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raw = []
    data = data if isinstance(data, dict) else {}

    chapters = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        chapters.append(
            {
                "heading": clean_line(entry.get("heading")),
                "summary": clean_line(entry.get("summary")),
            }
        )

    # Too many: keep the first `wanted`, which are the ones the rest of the plan
    # was built around. Too few — or none at all: pad, so the book is the length
    # the button says it is.
    chapters = chapters[:wanted]
    while len(chapters) < wanted:
        chapters.append({"heading": "", "summary": ""})

    for number, chapter in enumerate(chapters, start=1):
        if not chapter["heading"]:
            chapter["heading"] = f"Chapter {number}"

    return {
        # The model's own title, then one taken from the description, and only
        # then the placeholder. A book whose plan never arrived is still a book
        # about something, and the writer said what — "Untitled book" is what
        # the app says when even that is gone.
        "title": (
            clean_line(data.get("title"))
            or title_from(prompt)
            or "Untitled book"
        ),
        "subtitle": clean_line(data.get("subtitle")),
        "author": clean_line(data.get("author")),
        "series": clean_line(data.get("series")),
        "dedication": clean_line(data.get("dedication")),
        "style_note": clean_line(data.get("style_note")),
        # The description as typed. The batches get a trimmed version of it —
        # see `PREMISE_TOKENS` — and the fill-in of last resort gets all of it.
        "premise": prompt,
        "chapters": chapters,
    }


def split_batches(count, batches):
    """`count` chapters shared over `batches` requests, biggest first.

    Five chapters over two requests is [3, 2]. Front-loading rather than
    trailing means the batch most likely to be cut short is the smaller one,
    and the smaller one is the one asked for last.
    """
    batches = max(1, min(batches, count))
    size, extra = divmod(count, batches)
    return [size + (1 if index < extra else 0) for index in range(batches)]


def _prose(entry, fallback_heading, number):
    """One chapter object from a reply, tidied into what the editor stores."""
    raw = entry.get("paragraphs")
    if isinstance(raw, str):
        # Some models send the blob after all. Split it the way the editor would.
        raw = raw.split("\n\n")
    if not isinstance(raw, list):
        return None
    paragraphs = [clean_text(piece) for piece in raw[:MAX_PARAGRAPHS]]
    paragraphs = [piece for piece in paragraphs if piece]
    if not paragraphs:
        return None
    return {
        "number": number,
        "heading": clean_line(entry.get("heading")) or fallback_heading,
        "text": "\n\n".join(paragraphs),
    }


def batch_user(plan, numbers):
    """The half of a chapter request that changes: the plan, and what to write.

    Its own function because the allowance arithmetic has to price a chapter
    request before there is one to price — see `chapter_allowance`. Two ways of
    building the same message would be two ways of costing it, and the ledger's
    sums are only worth anything if what is measured is what is sent.
    """
    chapters = plan["chapters"]
    outline = "\n".join(
        f"{number}. {chapter['heading']} — {chapter['summary'] or '(follow the book)'}"
        for number, chapter in enumerate(chapters, start=1)
    )
    return (
        f"{plan['title']}, by {plan['author']}. {plan['style_note']}\n"
        f"About: {shorten(plan['premise'], PREMISE_TOKENS)}\n\n"
        f"The plan, all {len(chapters)} chapters:\n{outline}\n\n"
        f"Write chapters {', '.join(str(number) for number in numbers)} now, "
        "in that order."
    )


def _batch_cost(plan, size):
    """What one chapter request of `size` chapters costs to send."""
    numbers = list(range(1, size + 1))
    messages = [
        # Priced at the full-length ask. Every sentence `length_asked` can
        # produce is within a token or two of every other, so which one is used
        # here changes nothing `TOKEN_RESERVE` does not already cover.
        ("system", batch_system(FULL_ALLOWANCE)),
        ("human", batch_user(plan, numbers)),
    ]
    return estimate_request(messages, batch_schema(size))


def affordable_batches(ledger, plan, wanted, total):
    """`wanted` chapter requests, cut to the number the tokens left can send.

    Every batch carries the whole plan, so batches cost input whether or not
    they have room to write anything with. Asking for more of them than the
    ledger can pay for used to mean the early ones were priced out one by one
    while the last one — which holds back nothing for anybody after it — got
    whatever was left. Fewer, fuller requests are better than more, starved
    ones, and they are the same book in fewer pieces.
    """
    cost = _batch_cost(plan, total)
    room = ledger.left - TOKEN_RESERVE
    return max(1, min(wanted, room // max(1, cost + MIN_OUTPUT_TOKENS)))


def chapter_allowance(ledger, plan, sizes):
    """The output tokens one chapter may have — the same number for every one.

    Worked out once, before any chapter is asked for, and it is the whole of the
    answer to a book whose chapters were 28, 27, 22, 322 and 224 words long.

    A `share` of what is left cannot give it. A batch is allotted its fraction of
    the tokens remaining *at the moment it is sent*, so everything an earlier
    batch did not spend is inherited by the batch after it — and a batch that
    came back with nothing hands on its whole allowance. The last chapters of the
    book were not written better, they were funded twice as well.

    Dividing what is affordable by the chapters that have to share it fixes both
    ends: an early batch cannot be starved by an expensive outline, and a late
    one cannot be fattened by an early failure.
    """
    total = sum(sizes) or 1
    room = ledger.left - len(sizes) * _batch_cost(plan, max(sizes or [1])) - TOKEN_RESERVE
    # Divided by what the chapters will be *charged*, not by what they will
    # cost: see `SETTLE_FACTOR`. Without it the first batch is allotted a share
    # it then overruns on the ledger's books, and the last batch pays for it.
    return min(FULL_ALLOWANCE, int(max(0, room) / (SETTLE_FACTOR * total)))


def write_batch(plan, numbers, ask, config, share=1.0, later=0,
                allowance=FULL_ALLOWANCE):
    """Ask for several chapters at once. `numbers` are 1-based, in order.

    Every request carries the whole outline, so the model always knows where in
    the arc it is, but never the text of a chapter already written. Sending the
    story so far would grow every request after the first and spend the token
    budget on repetition — the outline is what holds the book together, and it
    is small.

    `allowance` is what one chapter may spend, and it does two things here: it
    sizes the request's cap, and it sizes what the prompt asks for. Both, because
    a cap the prompt ignores is a reply that gets chopped — see `paragraph_plan`.

    `share` is this batch's portion of the tokens left, and `later` how many
    batches follow it. Both go to the ledger, which turns them into a second cap
    this request may not exceed; the allowance is the one that normally binds,
    and the ledger's is what keeps the whole-book limit true whatever else
    changes here. Coming back empty-handed is a legal answer.
    """
    def system(cap):
        """The instructions, asking for what this request's cap can hold.

        `cap` is `None` while the cap is still being worked out — see `ask` —
        and the allowance is the right answer then, being what the cap is
        expected to come to.
        """
        each = allowance if cap is None else cap // len(numbers)
        return batch_system(min(allowance, each))

    data = ask(
        system,
        batch_user(plan, numbers),
        "book_chapters",
        batch_schema(len(numbers)),
        salvage_chapters,
        # Chapters stay on screen once they have arrived; the next batch is added
        # under them rather than replacing them.
        keep=True,
        share=share,
        later=later,
        # The same room per chapter whichever batch it happens to be in.
        ceiling=len(numbers) * allowance,
    )

    raw = data.get("chapters") if isinstance(data, dict) else None
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        # Nothing usable, and nothing raised. These chapters are simply not
        # written yet; `write_book` fills them from the plan.
        return []

    # Matched by the number the model was given, falling back to position for a
    # model that dropped the field. Either way a chapter lands where it belongs
    # rather than wherever it happened to appear.
    written = {}
    for position, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        number = entry.get("number")
        if not isinstance(number, int) or number not in numbers:
            number = numbers[position] if position < len(numbers) else None
        if number is None or number in written:
            continue
        prose = _prose(entry, plan["chapters"][number - 1]["heading"], number)
        if prose:
            written[number] = prose
    return [written[number] for number in numbers if number in written]


def from_plan(plan, number):
    """The chapter the model did not write, written from its own plan entry.

    This is what makes the token limit a limit on the *length* of a book rather
    than on whether there is one. When the ledger cannot afford a request, or a
    reply comes back unreadable, or the clock runs out, the chapters that are
    missing are made here — from the summary the outline already gave them,
    which cost nothing and is on the subject.

    It is not prose and does not pretend to be. It is a chapter with its heading
    and a line saying what happens in it, sitting in an editor, waiting to be
    written over. That is worth more than a red banner and no book at all.
    """
    entry = plan["chapters"][number - 1]
    said = entry.get("summary") or shorten(plan.get("premise", ""), PREMISE_TOKENS)
    return {
        "number": number,
        "heading": entry.get("heading") or f"Chapter {number}",
        "text": clean_line(said) or "…",
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


class _Progress:
    """How far along the book is, and where the bar was left.

    No `try`/`except` anywhere in here, deliberately. The callback the app passes
    in is also what enforces the session's disk quota, and it reports that by
    raising. Swallowing it would quietly turn the quota off — and a wait loop is
    a good place for it to still be able to fire.
    """

    def __init__(self, report=None):
        self.report = report
        self.at = 0.0

    def say(self, fraction, message):
        """The book has got this far."""
        self.at = fraction
        if self.report:
            self.report(fraction, message)

    def note(self, message):
        """Something worth saying that is not progress.

        Waiting out a busy service is the book standing still on purpose, so the
        message goes out at the position the bar already holds rather than
        dragging it backwards or inventing a step it has not taken.
        """
        if self.report:
            self.report(self.at, message)


def _check_allowed(config):
    """Refuse a model this copy is not allowed to use, before anything is sent.

    The default model is a paid one — a very cheap paid one, but paid — so
    `OPENROUTER_FREE_ONLY` is off by default and this check normally passes. The
    account's credit limit is what makes the ceiling real, and it is the only
    control on the far side of a request.

    Switching the flag on is for a copy that must not be able to spend anything
    at all. Then this is the check on *this* side of the request, and a model
    name that could be charged for becomes a sentence on the page rather than a
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
            f"“{ai_config.FREE_MODEL}” or to a model ending in “:free”, or "
            f"switch {ai_config.FREE_ONLY_VAR} off."
        )


def write_book(prompt, *, design=None, progress=None, on_text=None, config=None):
    """Write a whole book from one description.

    `prompt` is the sentence the visitor typed and `design` is the page setup the
    editor already had, which is carried across untouched. Neither the book on
    screen nor anything else from the session is passed in, and that is the point:
    there is no argument here through which somebody's manuscript could be sent
    to a third party.

    `progress` is told how far along the book is; `on_text` is given the book
    itself, in words, over and over as it is written — every few tokens, from the
    first one. It is what turns a two-minute blank wait into something being read
    while it is written, and it is a display only: it is called often, it may be
    called with the same text twice, and it must not be where a limit is
    enforced. Passing nothing asks for the whole book at the end, as before.
    """
    config = config or ai_config.settings()
    _check_allowed(config)
    prompt = clean_prompt(prompt)

    started = time.monotonic()
    budget = _Budget(config.max_calls)
    ledger = _Ledger(config.token_limit)
    chat = _make_chat(config)
    live = _Live(on_text)
    bar = _Progress(progress)
    pace = _Pace(config.gap)
    ask = _asker(
        chat, config, budget, live, ledger,
        pace=pace, note=bar.note, started=started,
    )

    total = config.chapters
    bar.say(0.0, f"Planning {total} chapters…")
    plan = build_outline(prompt, ask, config)

    # One request for the outline, and the rest shared out between the chapters —
    # but no more of them than the tokens left can pay to send, and with the
    # tokens divided evenly per chapter rather than per batch. Both are worked
    # out here, once, while every chapter is still ahead of us: after the first
    # batch has spent or refused its share it is too late to be fair about it.
    sizes = split_batches(total, affordable_batches(ledger, plan, budget.left, total))
    allowance = chapter_allowance(ledger, plan, sizes)
    bar.say(0.12, f"{total} chapters planned. Writing them now.")

    written = {}
    done = 0
    for index, size in enumerate(sizes):
        numbers = list(range(done + 1, done + size + 1))
        if time.monotonic() - started > config.budget:
            # Out of time rather than out of tokens. The chapters already
            # written are kept and the rest are filled in below, so a slow model
            # costs the reader prose and not the book.
            break
        first, last = numbers[0], numbers[-1]
        bar.say(
            0.12 + 0.85 * done / total,
            f"Writing chapter{'s' if size > 1 else ''} {first}"
            + (f"–{last}" if size > 1 else "")
            + f" of {total}…",
        )
        for chapter in write_batch(
            plan,
            numbers,
            ask,
            config,
            # This batch's portion of what is left, and how many batches are
            # still to be paid for after it. Both are the ledger's safety net
            # now; `allowance` is what decides how long these chapters are.
            share=size / max(1, total - done),
            later=len(sizes) - index - 1,
            allowance=allowance,
        ):
            written[chapter["number"]] = chapter
        done += size

    chapters = [
        written.get(number) or from_plan(plan, number)
        for number in range(1, total + 1)
    ]

    bar.say(0.98, "Putting the book together…")
    book = to_manuscript(plan, chapters, design)
    bar.say(1.0, "Done.")
    return book
