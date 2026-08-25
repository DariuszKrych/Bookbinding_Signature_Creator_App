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
TIMEOUT_VAR = "AI_CALL_TIMEOUT_SECONDS"
BUDGET_VAR = "AI_TOTAL_BUDGET_SECONDS"
MAX_CHAPTERS_VAR = "AI_MAX_CHAPTERS"

DEFAULT_TITLE = "Bookbinding Signature Creator"
DEFAULT_TIMEOUT = 90.0
DEFAULT_BUDGET = 420.0
DEFAULT_MAX_CHAPTERS = 10

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
    max_chapters: int

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
        max_chapters=int(_number(MAX_CHAPTERS_VAR, DEFAULT_MAX_CHAPTERS, int)),
    )


def configured():
    """Whether a key is present at all.

    Checked before the button is drawn, so a copy of the app with no key shows a
    switched-off button and an explanation rather than failing when pressed.
    """
    return bool(_text(KEY_VAR))
