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

A whole book still will not survive one request — models cap their output, small
ones especially, and a reply that runs out mid-chapter is not a short book but
broken JSON. What
makes batching safe is `salvage_chapters`: a truncated reply is mined for the
chapters that did finish, which costs nothing and is worth more than a retry the
budget cannot afford.

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

# Paragraphs asked for per chapter, and the hard ceiling validation applies.
#
# A mini-novel, and sized to the token budget rather than to taste. Five chapters
# of three or four paragraphs at about eighty words comes to roughly fourteen
# hundred words, which is comfortably inside what `_Ledger` can allow for output
# — the point being that the model is asked for less than its cap, so the cap is
# a backstop and not a guillotine.
WANTED_PARAGRAPHS = (3, 4)
WANTED_WORDS = 80
MAX_PARAGRAPHS = 20

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

    # No `provider` block. Several providers serve this model and which of them
    # answers is OpenRouter's decision, not this app's: its own routing already
    # weighs cost heavily, and that is the behaviour wanted here.
    return ChatOpenAI(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=TEMPERATURE,
        timeout=config.timeout,
        max_retries=1,
        default_headers={"X-Title": config.app_title},
    )


def _bind(chat, rung, schema_name, schema, cap):
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
    """
    bound = {"max_tokens": int(cap)}
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


def _read_stream(runnable, messages, live):
    """One request, read a piece at a time, reported as it goes.

    The pieces are joined and handed back as one string, so everything after this
    line — `extract_json`, `salvage_chapters`, the repair — sees precisely what it
    would have seen from `.invoke()`. Streaming is a way of watching the reply
    arrive, not a different kind of reply.
    """
    parts = []
    usage = None
    for chunk in runnable.stream(messages):
        usage = _usage(chunk) or usage
        piece = _content(chunk)
        if not piece:
            continue
        parts.append(piece)
        live.feed("".join(parts))
    return "".join(parts), usage


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


def _invoke(chat, config, messages, schema_name, schema, budget, live, ledger, cap):
    """One request, starting at the strongest format this model has accepted.

    `cap` is the output ceiling the ledger has allowed this request, and it is
    charged for in full before the request goes out. Every rung costs its own
    charge: a downgrade is another request, and the point of the ledger is that
    nothing goes out uncounted.

    Returns `None` when there were no requests left to make one with — which the
    caller turns into chapters written from the outline, not into an error. A
    service that answered badly still raises; only a budget goes quiet.
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
            return None
        charge = ledger.take(cost, allowed)
        try:
            runnable = _bind(chat, rung, schema_name, schema, allowed)
            if live and config.stream:
                reply, usage = _read_stream(runnable, messages, live)
            else:
                answered = runnable.invoke(messages)
                reply, usage = _content(answered), _usage(answered)
        except Exception as error:
            charge.failed()
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
        reply = _content(reply)
        charge.settle(reply, usage)
        _RUNG[config.model] = rung
        return reply
    raise AIError("The model would not answer in JSON.")


# --------------------------------------------------------------------------
# The token budget
# --------------------------------------------------------------------------
# A book may cost 5,000 tokens, input and output, start to finish. Not on
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

# The most the outline may have. It is a page of headings, not prose, and every
# token it does not take is a token of the book itself.
OUTLINE_CAP = 320

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


def _asker(chat, config, budget, live=None, ledger=None):
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
    ):
        messages = [("system", system), ("human", user)]
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
            mended = _invoke(
                chat, config, again, schema_name, schema,
                budget, live, ledger, cap,
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

        reply = _invoke(
            chat, config, messages, schema_name, schema, budget, live, ledger, cap
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
            if data is None:
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
- {low} to {high} paragraphs each, about {words} words a paragraph. Keep to it:
  a reply that runs out mid-sentence loses the chapter it was in.
- Continue the book's voice. No preface, no summary, no explaining, and do not
  repeat the heading inside the paragraphs.

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
        # A plan is a page of headings. Anything more is taken out of the book
        # the plan is for.
        ceiling=OUTLINE_CAP,
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
        "title": clean_line(data.get("title"), "Untitled book"),
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


def write_batch(plan, numbers, ask, config, share=1.0, later=0):
    """Ask for several chapters at once. `numbers` are 1-based, in order.

    Every request carries the whole outline, so the model always knows where in
    the arc it is, but never the text of a chapter already written. Sending the
    story so far would grow every request after the first and spend the token
    budget on repetition — the outline is what holds the book together, and it
    is small.

    `share` is this batch's portion of the tokens left, and `later` how many
    batches follow it. Both go to the ledger, which turns them into the output
    cap this request is allowed. Coming back empty-handed is a legal answer.
    """
    chapters = plan["chapters"]
    outline = "\n".join(
        f"{number}. {chapter['heading']} — {chapter['summary'] or '(follow the book)'}"
        for number, chapter in enumerate(chapters, start=1)
    )

    user = (
        f"{plan['title']}, by {plan['author']}. {plan['style_note']}\n"
        f"About: {shorten(plan['premise'], PREMISE_TOKENS)}\n\n"
        f"The plan, all {len(chapters)} chapters:\n{outline}\n\n"
        f"Write chapters {', '.join(str(number) for number in numbers)} now, "
        "in that order."
    )
    system = _BATCH_SYSTEM.format(
        low=WANTED_PARAGRAPHS[0],
        high=WANTED_PARAGRAPHS[1],
        words=WANTED_WORDS,
        rules=STYLE_RULES,
    )
    data = ask(
        system,
        user,
        "book_chapters",
        batch_schema(len(numbers)),
        salvage_chapters,
        # Chapters stay on screen once they have arrived; the next batch is added
        # under them rather than replacing them.
        keep=True,
        share=share,
        later=later,
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
        prose = _prose(entry, chapters[number - 1]["heading"], number)
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


def _say(progress, fraction, message):
    """Report progress.

    No `try`/`except` around the call, deliberately. The progress callback the
    app passes in is also what enforces the session's disk quota, and it reports
    that by raising. Swallowing it here would quietly turn the quota off.
    """
    if progress:
        progress(fraction, message)


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
    ask = _asker(chat, config, budget, live, ledger)

    total = config.chapters
    _say(progress, 0.0, f"Planning {total} chapters…")
    plan = build_outline(prompt, ask, config)

    # One request for the outline, and the rest shared out between the chapters.
    sizes = split_batches(total, budget.left)
    _say(progress, 0.12, f"{total} chapters planned. Writing them now.")

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
        _say(
            progress,
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
            # still to be paid for after it.
            share=size / max(1, total - done),
            later=len(sizes) - index - 1,
        ):
            written[chapter["number"]] = chapter
        done += size

    chapters = [
        written.get(number) or from_plan(plan, number)
        for number in range(1, total + 1)
    ]

    _say(progress, 0.98, "Putting the book together…")
    book = to_manuscript(plan, chapters, design)
    _say(progress, 1.0, "Done.")
    return book
