"""Where the AI writer's settings come from, and where its key does not go.

Everything the AI feature reads from the environment is named here, so that
`ai_book.py` can be about talking to a model and this can be about configuration.
Nothing in this module imports Streamlit or LangChain: it is safe to import from
anywhere, including a test run on a machine that has neither.

The one rule worth stating plainly, because the repository is public and the app
is hosted at a public URL:

    The key is read from the environment, used, and dropped.

It is never written into `st.session_state`, never cached on a module global,
never stored on a `Manuscript`, and never logged. That last one is not
hypothetical — the sidebar's "Save my data" zips the drafts folder and hands it
to the browser, so a key that reached a manuscript would leave the building
inside somebody's download.

Two places supply it, and they do not compete:

* **On Render**, `OPENROUTER_API_KEY` is set in the service's Environment panel.
  It is injected into the container at run time, separately from the git pull,
  and is already in `os.environ` before the app starts. There is no `.env` file
  in the image at all — `.dockerignore` sees to that.
* **On your own machine**, a `.env` file next to `app.py` is read by
  `load_env()`. It is git-ignored, and it is the only place a key sits on disk.

`load_env` passes `override=False`, so if both exist the real environment
variable wins. A `.env` that somehow reached a server therefore cannot shadow
the key that server was given.
"""

import os
from dataclasses import dataclass
from pathlib import Path

# OpenRouter speaks the OpenAI wire format at this address, which is what lets
# `langchain_openai.ChatOpenAI` talk to it without a bespoke client.
BASE_URL = "https://openrouter.ai/api/v1"

# A router, not a model.
#
# `openrouter/free` picks from whatever free models are available at that moment
# and — the part this app depends on — narrows them to the ones that support the
# features the request needs, which here means structured JSON output. Naming a
# single model instead would mean editing this file every time a free model is
# retired or renamed, which is the maintenance this default exists to avoid.
#
# It selects at random per request, so two chapters of one book can be written by
# two different models. `ai_book` sends the outline and the style note with every
# chapter for that reason.
DEFAULT_MODEL = "openrouter/free"

KEY_VAR = "OPENROUTER_API_KEY"
MODEL_VAR = "OPENROUTER_MODEL"
FREE_ONLY_VAR = "OPENROUTER_FREE_ONLY"
TITLE_VAR = "OPENROUTER_APP_TITLE"
SORT_VAR = "OPENROUTER_PROVIDER_SORT"
TIMEOUT_VAR = "AI_CALL_TIMEOUT_SECONDS"
BUDGET_VAR = "AI_TOTAL_BUDGET_SECONDS"
CHAPTERS_VAR = "AI_CHAPTERS"
MAX_CALLS_VAR = "AI_MAX_CALLS_PER_BOOK"
STREAM_VAR = "AI_STREAM"

DEFAULT_TITLE = "Bookbinding Signature Creator"
DEFAULT_TIMEOUT = 90.0
DEFAULT_BUDGET = 420.0

# Which provider OpenRouter should route to, when several of them serve the same
# model.
#
# The same free model is usually hosted by more than one provider, and they are
# not equally quick — the slow one can take several times as long for the same
# reply. Left alone, OpenRouter balances by its own default order; `throughput`
# tells it to pick whichever provider is currently producing the most tokens per
# second, which is the one that finishes a chapter first.
#
# The three OpenRouter accepts. Anything else here would be sent to the API only
# to be refused, so an unrecognised value falls back to the default the way a
# mistyped number does.
DEFAULT_SORT = "throughput"
SORTS = ("throughput", "latency", "price")
# Written in the environment to send no `provider` block at all, i.e. to leave
# the routing entirely to OpenRouter.
SORT_OFF = ("off", "none", "default")

# Exactly this many chapters, every time, whatever the description asks for.
#
# Not a maximum. Letting the model choose meant a description saying "a short
# novel" came back with twice the chapters of one that asked for a full novel —
# models read length words as flavour, not as instructions. A fixed number is
# also what makes the request budget below predictable.
DEFAULT_CHAPTERS = 5

# The most requests one book may cost, counting every retry and repair.
#
# OpenRouter's free tier allows about 50 requests a day, shared across the whole
# app. A chapter-per-request book spent one plus one per chapter — six for five
# chapters, so eight books a day. Batching the chapters into the calls that are
# left brings that to three, and the cap is enforced rather than hoped for.
DEFAULT_MAX_CALLS = 3

# The repository root, i.e. the folder holding `app.py`.
ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT_DIR / ".env"


@dataclass(frozen=True)
class Settings:
    """One reading of the environment, used for one book and then dropped.

    Frozen because nothing should be able to edit a key in flight, and built
    fresh by `settings()` on every call rather than cached, so that changing a
    variable and restarting is enough — there is no stale copy to clear.
    """

    api_key: str
    model: str
    base_url: str
    free_only: bool
    app_title: str
    timeout: float
    budget: float
    chapters: int
    max_calls: int
    # Both have defaults so that the two speed settings could be added without
    # touching every place a `Settings` is built by hand.
    sort: str = DEFAULT_SORT
    stream: bool = True

    @property
    def batches(self):
        """How many requests are left for chapters once the outline has had one."""
        return max(1, self.max_calls - 1)

    @property
    def provider(self):
        """The `provider` block to send with the request, or `None` for none.

        OpenRouter reads this out of the request body and uses it to choose
        between the providers serving the model. `{"sort": "throughput"}` asks
        for the fastest one by tokens per second — the whole reason this exists,
        since a book is three replies and the difference between the quickest
        provider and the slowest is felt on every one of them.
        """
        return {"sort": self.sort} if self.sort else None

    @property
    def is_free(self):
        """Whether this model can cost money.

        `openrouter/free` is the free-model router; a `:free` suffix is one
        specific free model. Anything else has a price per token.
        """
        return self.model == DEFAULT_MODEL or self.model.endswith(":free")


def load_env(path=None):
    """Read a `.env` file if there is one. Returns whether anything was read.

    Deliberately quiet and deliberately optional. `python-dotenv` is not needed
    to run this app — the deployed copy has no `.env` and gets its key from the
    environment — so a missing package is a `False`, not an exception. The same
    goes for a missing file, which is the normal case in production.

    `override=False` is the important argument: a real environment variable
    always beats a file. That is what stops a `.env` that found its way onto a
    server from quietly replacing the key that server was configured with.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    target = Path(path) if path else ENV_FILE
    if not target.is_file():
        return False
    return bool(load_dotenv(target, override=False))


def _text(name, default=""):
    return (os.environ.get(name) or "").strip() or default


def _number(name, default, cast):
    """One numeric setting, falling back rather than failing.

    A typo in an environment variable on a hosted service should degrade to the
    default and leave the app running, not take the whole page down with a
    `ValueError` at import time.
    """
    raw = _text(name)
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _flag(name, default=True):
    raw = _text(name).lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def _sort(name=SORT_VAR):
    """Which provider ordering to ask OpenRouter for. `""` means ask for none.

    Unrecognised values fall back to the default rather than being forwarded:
    OpenRouter refuses a sort rule it does not know, and a typo in a Render
    environment variable should not turn every request into a 400.
    """
    raw = _text(name).lower()
    if not raw:
        return DEFAULT_SORT
    if raw in SORT_OFF:
        return ""
    return raw if raw in SORTS else DEFAULT_SORT


def settings():
    """The current settings, read fresh from the environment."""
    return Settings(
        api_key=_text(KEY_VAR),
        model=_text(MODEL_VAR, DEFAULT_MODEL),
        base_url=_text("OPENROUTER_BASE_URL", BASE_URL),
        free_only=_flag(FREE_ONLY_VAR, True),
        app_title=_text(TITLE_VAR, DEFAULT_TITLE),
        timeout=_number(TIMEOUT_VAR, DEFAULT_TIMEOUT, float),
        budget=_number(BUDGET_VAR, DEFAULT_BUDGET, float),
        chapters=int(_number(CHAPTERS_VAR, DEFAULT_CHAPTERS, int)),
        max_calls=int(_number(MAX_CALLS_VAR, DEFAULT_MAX_CALLS, int)),
        sort=_sort(),
        stream=_flag(STREAM_VAR, True),
    )


def configured():
    """Whether a key is present at all.

    Checked before the button is drawn, so a copy of the app with no key shows a
    switched-off button and an explanation rather than failing when pressed.
    """
    return bool(_text(KEY_VAR))
