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

# One model, named, and the same one every time.
#
# Small and quick is the whole point: at eight billion parameters this answers in
# a fraction of the time a large model takes, and the app's problem was never the
# quality of the prose but the length of the wait in front of it.
#
# Which provider serves it is left to OpenRouter. Five of them offer this model
# and OpenRouter's own routing already leans hard on cost; there is no `provider`
# block in the request telling it otherwise.
#
# It is not free. It is about as close as a paid model gets — $0.02 per million
# tokens in and $0.04 out, so a whole book costs a small fraction of a penny —
# but "not free" is why `DEFAULT_FREE_ONLY` below is off. The credit limit on the
# key is what makes the ceiling real.
DEFAULT_MODEL = "meta-llama/llama-3.1-8b-instruct"

# OpenRouter's free-model router, kept as a name because two things still point
# at it: the message a free-only copy prints when it refuses a paid model, and
# `is_free`. It picks from whatever free models exist at that moment, which is
# what makes it the one model name that never goes stale.
FREE_MODEL = "openrouter/free"

KEY_VAR = "OPENROUTER_API_KEY"
MODEL_VAR = "OPENROUTER_MODEL"
FREE_ONLY_VAR = "OPENROUTER_FREE_ONLY"
TITLE_VAR = "OPENROUTER_APP_TITLE"
TIMEOUT_VAR = "AI_CALL_TIMEOUT_SECONDS"
BUDGET_VAR = "AI_TOTAL_BUDGET_SECONDS"
CHAPTERS_VAR = "AI_CHAPTERS"
MAX_CALLS_VAR = "AI_MAX_CALLS_PER_BOOK"
STREAM_VAR = "AI_STREAM"
TOKEN_LIMIT_VAR = "AI_TOKEN_LIMIT"

DEFAULT_TITLE = "Bookbinding Signature Creator"
DEFAULT_TIMEOUT = 90.0
DEFAULT_BUDGET = 420.0

# Off, because the model above is a paid one and a guard set to refuse every paid
# model would refuse this app's own default — the button would be switched on,
# and every press would come back with a refusal.
#
# The guard itself is untouched and still worth having. Set `OPENROUTER_FREE_ONLY`
# to 1 on a copy that must not be able to spend anything, and set
# `OPENROUTER_MODEL` to `openrouter/free` or a `:free` model to go with it.
DEFAULT_FREE_ONLY = False

# Exactly this many chapters, every time, whatever the description asks for.
#
# Not a maximum. Letting the model choose meant a description saying "a short
# novel" came back with twice the chapters of one that asked for a full novel —
# models read length words as flavour, not as instructions. A fixed number is
# also what makes the request budget below predictable.
DEFAULT_CHAPTERS = 5

# The most requests one book may cost, counting every retry and repair.
#
# Three, because a request is the unit of everything expensive here: of time
# waited, of tokens paid for, and — on a copy configured with a free model — of a
# daily ration of about fifty shared by everyone using it. A chapter-per-request
# book spent one plus one per chapter; batching the chapters into the calls left
# over brings that to three, and the cap is enforced rather than hoped for.
DEFAULT_MAX_CALLS = 3

# Every token one book may cost, input and output, from the first request to the
# last. A ceiling, not a target: most books come in well under it.
#
# It is kept rather than hoped for. `ai_book._Ledger` measures what a request
# will cost to send, sends `max_tokens` so the reply cannot exceed what is left,
# and charges the worst case before the request goes out. A request that will
# not fit is not made at all — the chapters it would have written are filled in
# from the outline instead, which costs nothing.
#
# So lowering this shortens the book. It cannot break it.
DEFAULT_TOKEN_LIMIT = 5000

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
    # Defaulted so that these could be added without touching every place a
    # `Settings` is built by hand.
    stream: bool = True
    token_limit: int = DEFAULT_TOKEN_LIMIT

    @property
    def batches(self):
        """How many requests are left for chapters once the outline has had one."""
        return max(1, self.max_calls - 1)

    @property
    def is_free(self):
        """Whether this model is one that cannot cost anything.

        `openrouter/free` is the free-model router; a `:free` suffix is one
        specific free model. Everything else has a price per token, the app's own
        default included — which is exactly why this is not written in terms of
        `DEFAULT_MODEL`. A guard that counted the default as free by definition
        would be no guard at all.
        """
        return self.model == FREE_MODEL or self.model.endswith(":free")


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


def settings():
    """The current settings, read fresh from the environment."""
    return Settings(
        api_key=_text(KEY_VAR),
        model=_text(MODEL_VAR, DEFAULT_MODEL),
        base_url=_text("OPENROUTER_BASE_URL", BASE_URL),
        free_only=_flag(FREE_ONLY_VAR, DEFAULT_FREE_ONLY),
        app_title=_text(TITLE_VAR, DEFAULT_TITLE),
        timeout=_number(TIMEOUT_VAR, DEFAULT_TIMEOUT, float),
        budget=_number(BUDGET_VAR, DEFAULT_BUDGET, float),
        chapters=int(_number(CHAPTERS_VAR, DEFAULT_CHAPTERS, int)),
        max_calls=int(_number(MAX_CALLS_VAR, DEFAULT_MAX_CALLS, int)),
        stream=_flag(STREAM_VAR, True),
        token_limit=int(_number(TOKEN_LIMIT_VAR, DEFAULT_TOKEN_LIMIT, int)),
    )


def configured():
    """Whether a key is present at all.

    Checked before the button is drawn, so a copy of the app with no key shows a
    switched-off button and an explanation rather than failing when pressed.
    """
    return bool(_text(KEY_VAR))
