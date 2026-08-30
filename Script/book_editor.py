"""The “Convert Inputted Text into PDF Signatures” view: text boxes in, a book out.

This is the second half of the app. The first half takes a PDF somebody else
made and imposes it; this one lets the user type the book themselves — title,
author, dedication, chapters, appendix — and then hands the result to exactly
the same imposition. The bridge between the two is deliberately narrow: the
editor builds an ordinary input PDF, and from that moment on a typed book is
indistinguishable from any other.

Three rules shape everything below.

**The manuscript is the truth.** Every widget is keyed and reads its starting
value from the `Manuscript` in session state, and every widget writes straight
back into it on the same run. Nothing is stored only in a widget, so saving,
loading, reordering and rebuilding all work off one object.

**Every widget key starts with `bk-`.** Streamlit ignores a new `value=` once a
keyed widget has state of its own, so loading a different draft has to wipe that
state or the old draft's words would sit on top of the new one's. One prefix
means one line does it (`forget_fields`). Wiping is only half of it: the value
that replaces it has to arrive through session state as well, or the browser
never hears about it. See `_seed`. Preferences that should *survive* a draft
change — the autosave tick, the display unit, and the paper a build goes on —
use `bkpref-` instead and are never wiped. Those are also the keys that have to
survive a trip to the other tab, where nothing draws them; see `Editor.sticky`.

**Nothing here does slow work.** Building a book is claimed as a job and run by
the runner at the foot of `app.py`, under the same lock as a conversion, so the
page is painted with every control disabled before a single page is typeset.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from Script.manuscript import (
    BACK_KEYS,
    BODY_KEYS,
    CHAPTER_LABELS,
    CHAPTER_START_NEW_PAGE,
    CHAPTER_START_RECTO,
    DESIGN_LIMITS,
    DRAFT_SUFFIX,
    FRONT_KEYS,
    KINDS,
    LABEL_CHAPTER_NUMBER,
    LABEL_CHAPTER_WORD,
    LABEL_NONE,
    LABEL_NUMBER,
    Manuscript,
    Section,
    blank_manuscript,
    clamp_design,
    clean_draft_name,
    delete_draft,
    draft_name_of,
    example_manuscript,
    kind_of,
    list_drafts,
    load_draft,
    numbered_sections,
    page_sized,
    save_draft,
    unique_draft_name,
)
from Script.paper_sizes import (
    BOOK_PAGE_SIZES,
    PT_PER_INCH,
    SHEET_SIZES,
    describe_size,
    find_paper_size,
    smallest_sheet_for,
)
from Script.print_formatting import (
    FIT,
    SheetFit,
    plan_signatures,
    sheet_problems,
    sheet_warnings,
)
from Script.typesetting import FONTS, estimate_book_pages

# Session keys. The `bk-` ones belong to widgets and are wiped whenever a
# different draft is opened; `bkpref-` ones are preferences and are not.
BOOK = "book_manuscript"
DRAFT_PATH = "book_draft_path"
DRAFT_NAME = "book_draft_name"
SAVED_JSON = "book_saved_json"
REMOVED = "book_removed_section"
ARMED_ACTION = "book_armed_action"
# Two boxes name things after the book's title until the user overrules them.
# See `_auto_box`; these hold "still following" for each.
FILE_NAME_AUTO = "book_file_name_follows"
DRAFT_NAME_AUTO = "book_draft_name_follows"
# A book the AI wrote, waiting for the next run to put it on screen. See
# `hand_over` and `collect`.
GENERATED = "book_generated"

# Set by `app.py` on the run a written book is collected, so this view can say
# once where the book on screen came from. Popped when it is said.
HANDOFF = "ai_handoff"

FIELD_PREFIX = "bk-"
LENGTH_PREFIX = "bk-len-"
PREF_PREFIX = "bkpref-"

CUSTOM_PAGE_SIZE = "Custom size…"

# How big the book is, asked from whichever end the writer is thinking in. Both
# say the same thing — a sheet is folded across its width, so a page is half a
# sheet wide and a full sheet tall — and only the one that was picked is on
# screen, so there is never a second control quietly holding a different answer.
SIZE_FROM_PAGE = "page"
SIZE_FROM_SHEET = "sheet"
SIZE_FROM_LABELS = {
    SIZE_FROM_PAGE: "The finished page",
    SIZE_FROM_SHEET: "The paper it prints on",
}

# Preference keys, not draft keys: the paper belongs to a build, not to the book,
# so it survives loading a different draft and is never written into one.
SIZE_FROM_KEY = f"{PREF_PREFIX}size-from"
SHEET_KEY = f"{PREF_PREFIX}sheet"
SHEET_LANDSCAPE_KEY = f"{PREF_PREFIX}sheet-landscape"

PARTS = (
    ("front", "Front matter", FRONT_KEYS,
     "Everything before the first chapter: what the reader sees on the way in."),
    ("body", "The book itself", BODY_KEYS,
     "The chapters, in the order they are read."),
    ("back", "Back matter", BACK_KEYS,
     "Everything after the last chapter."),
)

CHAPTER_LABEL_NAMES = {
    LABEL_CHAPTER_NUMBER: "Chapter 1",
    LABEL_CHAPTER_WORD: "Chapter One",
    LABEL_NUMBER: "1",
    LABEL_NONE: "No number, just the title",
}


@dataclass(frozen=True)
class Editor:
    """What this view needs from `app.py`, and nothing more.

    Passed in rather than imported so the editor cannot reach round the back of
    the app for the lock, the flash slot or the delete confirmation — one job at
    a time and one armed delete at a time are guarantees that only hold if
    everything goes through the same four functions.
    """

    unit: object
    busy: bool
    sheets_per_signature: int
    finish: object
    arm_delete: object
    confirm_delete: object
    # Keep one of this view's own widgets alive while the other view is on
    # screen. Streamlit discards the state of every widget a run did not draw,
    # and a forgotten paper size would silently build the next book at another
    # size. `sticky(key, default)` before the widget, `remember(key)` after it.
    sticky: object
    remember: object
    # Where a build would land, given the name in the box. Handed in rather
    # than worked out here so the path shown on screen is the same one the
    # build will actually use, sanitised in exactly the same way.
    build_path: object
    # The two ways out that have work to do first: `pdf_bytes(book, file_name,
    # page_size_in)` typesets the book, and `signature_bytes(book, file_name,
    # page_size_in, sheet_size_pt)` typesets and imposes it. Both hand back
    # bytes and both are called by Streamlit when a download button is clicked
    # — off the script run, so neither may draw or touch session state. See
    # `Script/book_build.py`, and `_take_away_panel` for why it is worth it.
    pdf_bytes: object = None
    signature_bytes: object = None
    # True when the session has no room left to write anything. Only the drafts
    # go dead on it: a download writes nothing the session keeps, and a full
    # session is exactly when being able to take your book away matters most.
    full: bool = False
    # Draws the two folding settings — sheets per signature, and the printer's
    # duplex setting — into whatever container is open. Handed in rather than
    # imported because they are shared with the conversion screen under one pair
    # of keys, and only one of the two screens is ever drawn: `app.py` owns that
    # arrangement, and this view only has to say where they go.
    printing_options: object = None


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------


def forget_fields():
    """Drop every widget's memory of the draft that was open.

    Called when a different manuscript is put into session state, and nowhere
    else. Without it Streamlit would keep showing — and then write back — the
    previous draft's title and chapters, because a keyed widget's own state wins
    over the `value=` it is handed.
    """
    for key in [k for k in st.session_state if k.startswith(FIELD_PREFIX)]:
        del st.session_state[key]


def manuscript():
    """The book being edited, created empty on first use."""
    if BOOK not in st.session_state:
        adopt(blank_manuscript(), path=None, name="")
    return st.session_state[BOOK]


def adopt(book, path=None, name=""):
    """Make `book` the manuscript on screen, forgetting whatever was there."""
    forget_fields()
    st.session_state[BOOK] = book
    st.session_state[DRAFT_PATH] = str(path) if path else ""
    st.session_state[DRAFT_NAME] = name or (book.title.strip() or "")
    # A book that has never been written to disk has no saved copy to compare
    # against, which is what makes `is_dirty` say "not saved yet".
    st.session_state[SAVED_JSON] = book.to_json() if path else ""
    st.session_state[REMOVED] = None
    st.session_state[ARMED_ACTION] = None
    st.session_state[FILE_NAME_AUTO] = True
    st.session_state[DRAFT_NAME_AUTO] = True


def draft_path():
    return st.session_state.get(DRAFT_PATH) or ""


def is_dirty(book):
    saved = st.session_state.get(SAVED_JSON) or ""
    return bool(saved) and book.to_json() != saved


def is_unsaved():
    """True when there is no file behind this draft at all."""
    return not draft_path()


def mark_saved(book, path, name):
    st.session_state[DRAFT_PATH] = str(path)
    st.session_state[DRAFT_NAME] = name
    st.session_state[SAVED_JSON] = book.to_json()


def keep_current_as_draft(folder):
    """Write whatever is in the editor to a draft of its own. Returns its name.

    Called before something replaces the book on screen — at present, the AI
    writer. Returns `""` only when there was genuinely nothing there: an empty
    book. A book already on disk and unchanged needs no writing, but its name
    comes back anyway, because the caller uses this to tell the user where the
    book they were looking at has gone — and "it is already in the drafts list
    as X" is the answer in that case too.

    It never writes over an existing draft, because `unique_draft_name` walks the
    name on to "My book 2" first, and `save_draft` writes through a temporary
    file and a rename — so this is safe to do immediately before an operation
    that might fail half way. A saved book with unsaved edits therefore lands
    beside its saved copy rather than on top of it.

    `mark_saved` is deliberately *not* called: the book being kept is about to
    stop being the book on screen, so recording it as the open draft would leave
    the editor pointing at a file the next thing to arrive has nothing to do with.
    """
    book = manuscript()
    if not book.has_content():
        return ""
    if not is_unsaved() and not is_dirty(book):
        return draft_name_of(draft_path())
    wanted = st.session_state.get(DRAFT_NAME) or book.display_title
    return draft_name_of(save_draft(folder, book, unique_draft_name(folder, wanted)))


def hand_over(book):
    """Leave a freshly written book for the next run to put on screen.

    The job runner cannot call `adopt` itself. By the time it runs, every `bk-`
    widget for the book being replaced has already been drawn this run, and
    `adopt` deletes exactly those keys — changing a widget's state after it has
    been instantiated is the one thing Streamlit will not have. So the runner
    leaves the book here, `finish()` reruns, and `collect()` installs it at the
    top of the next run, before a single box exists.
    """
    st.session_state[GENERATED] = book


def collect():
    """Put a handed-over book on screen. Returns whether there was one.

    Must be called before anything draws a `bk-` widget.
    """
    book = st.session_state.pop(GENERATED, None)
    if book is None:
        return False
    # `path=None`: the book arrives having never been saved, so autosave cannot
    # write it over the draft that was just kept for the user.
    adopt(book, path=None, name=book.title.strip())
    return True


# --------------------------------------------------------------------------
# Small widget helpers
# --------------------------------------------------------------------------


def _write_back(holder, attribute, field, convert=None):
    """The callback that puts a box's new words into the manuscript.

    Streamlit runs a widget's `on_change` *before* the script body, so this is
    what makes "the manuscript is the truth" true from the first line of a run
    rather than from the moment the box happens to be drawn.

    Without it, every button drawn above a box — 💾 Save, Open, ➕ Add — reads
    the manuscript as it was *before* the words that arrived in the very same
    message. A click on Save carries the text typed just before it, in one
    request, and `_draft_panel` runs long before `_title_panel` writes that text
    into the model. Worse, those handlers all end in a rerun, and Streamlit
    discards the widget state of every box the interrupted run never reached —
    so the words were gone from the model *and* from the box. That is how a
    title typed and saved in one movement reached the draft file as `""` while
    every chapter, typed and then left alone, arrived intact.
    """
    def apply():
        if field not in st.session_state:
            return
        value = st.session_state[field]
        setattr(holder, attribute, convert(value) if convert else value)

    return apply


def _seed(field, value):
    """Put a value into a box's own state, before the box is drawn.

    Handing a widget a fresh `value=` is not enough to change what the *screen*
    shows. A keyed widget is identified by its key alone, so the box survives
    the rerun as the same box, and Streamlit only tells it to take a new value
    when that value arrived through session state. Everything else is a default
    for a box that does not exist yet, and a box that already exists ignores it.

    That is what lost the title page. Opening a draft wiped every `bk-` key and
    redrew each box with the new book's words: the manuscript, and so the PDF,
    were right, while the boxes on screen still showed the book before it. Only
    the title page was noticed because a chapter's key carries its section id,
    so a different draft's chapters are different boxes and are drawn from
    scratch anyway.

    Seeded only when the key is missing, which is exactly the case it is for:
    `forget_fields` has just removed them, so this is the first run of the new
    book. On every later run the box owns its value and is left alone — writing
    to it on every keystroke would take the cursor with it.
    """
    if field not in st.session_state:
        st.session_state[field] = value


def _seed_choice(field, value, options):
    """`_seed` for a menu, repairing a stored choice this build cannot offer.

    A menu raises rather than falling back when its state names something that
    is not in the list, so a draft carrying a typeface or a page size that has
    since been renamed would take the whole editor down with it.
    """
    if field not in st.session_state or st.session_state[field] not in options:
        st.session_state[field] = value


def _field(widget, label, holder, attribute, key, editor, **kwargs):
    """One widget bound to one attribute of the manuscript.

    The value goes back into the model on the same run it was read, so anything
    that happens later in the run — the word count, an autosave, a build — sees
    what the user just typed rather than what they typed before it. And it goes
    back *again*, ahead of the next run, through `_write_back`, so that anything
    drawn earlier on the page sees it too.
    """
    field = f"{FIELD_PREFIX}{key}"
    # No `value=`: the box takes its value from session state, which is the only
    # route that also reaches the screen. See `_seed`.
    _seed(field, getattr(holder, attribute))
    value = widget(
        label, key=field,
        on_change=_write_back(holder, attribute, field),
        disabled=editor.busy, **kwargs
    )
    setattr(holder, attribute, value)
    return value


def _text(label, holder, attribute, key, editor, **kwargs):
    return _field(st.text_input, label, holder, attribute, key, editor, **kwargs)


def _area(label, holder, attribute, key, editor, **kwargs):
    return _field(st.text_area, label, holder, attribute, key, editor, **kwargs)


def _check(label, holder, attribute, key, editor, **kwargs):
    field = f"{FIELD_PREFIX}{key}"
    _seed(field, bool(getattr(holder, attribute)))
    value = st.checkbox(
        label, key=field,
        on_change=_write_back(holder, attribute, field),
        disabled=editor.busy, **kwargs
    )
    setattr(holder, attribute, value)
    return value


def _length(label, holder, attribute, key, editor, help_text=""):
    """A measurement, shown in the sidebar's unit and stored in inches.

    Keyed per unit, exactly like the conversion tab's own measurements, so
    switching units re-creates the widget with the stored inches converted
    rather than re-reading the old number as if it had always been millimetres.
    `render` wipes the stale per-unit keys when the unit changes.
    """
    unit = editor.unit
    low, high = DESIGN_LIMITS[attribute]
    field = f"{LENGTH_PREFIX}{key}-{unit.name}"
    smallest = round(unit.from_inches(low), unit.places)
    largest = round(unit.from_inches(high), unit.places)
    # Brought inside the box's own range on the way in. A hand-edited draft can
    # hold anything, and a number outside the range is the one thing a spin box
    # refuses to be shown at all.
    _seed(field, min(max(
        round(unit.from_inches(getattr(holder, attribute)), unit.places),
        smallest,
    ), largest))
    value = st.number_input(
        f"{label} ({unit.name})",
        min_value=smallest,
        max_value=largest,
        step=unit.step,
        format=f"%.{unit.places}f",
        key=field,
        on_change=_write_back(holder, attribute, field, convert=unit.to_inches),
        disabled=editor.busy,
        help=help_text,
    )
    setattr(holder, attribute, unit.to_inches(value))
    return getattr(holder, attribute)


def _points(label, holder, attribute, key, editor, step=0.5, help_text=""):
    low, high = DESIGN_LIMITS[attribute]
    field = f"{FIELD_PREFIX}{key}"
    _seed(field, min(max(float(getattr(holder, attribute)), float(low)), float(high)))
    value = st.number_input(
        label, min_value=float(low), max_value=float(high),
        step=step, format="%.2f",
        key=field, on_change=_write_back(holder, attribute, field, convert=float),
        disabled=editor.busy, help=help_text,
    )
    setattr(holder, attribute, float(value))
    return value


def _auto_box(label, key, auto_key, suggestion, editor, **kwargs):
    """A text box that follows `suggestion` until somebody types in it.

    Both name boxes in this view — what to save the draft as, what to build the
    PDF as — start out as the book's title, and a title typed after the box was
    first drawn has to reach them. A keyed widget ignores a new `value=` once it
    has state, so the value is written into session state *before* the widget is
    created, which is the one way to move a widget from code. Emptying the box
    puts it back to following, so there is a way out that needs no explaining.

    What the user typed is kept in a second key of its own, because the widget's
    own state does not survive a rerun that never reaches this box: any button
    above it — 💾 Save, Open, ➕ Add — ends the run early, and Streamlit then
    discards the state of every widget that run did not draw. Reading the name
    back from `kept` rather than falling back to the suggestion is what stops a
    hand-picked build name quietly turning back into the book's title the first
    time the draft is saved.
    """
    field = f"{FIELD_PREFIX}{key}"
    kept = f"{FIELD_PREFIX}typed-{key}"
    if st.session_state.get(auto_key, True):
        st.session_state[field] = suggestion
    else:
        st.session_state[field] = st.session_state.get(kept, suggestion)

    def typed_in():
        typed = st.session_state.get(field, "")
        st.session_state[kept] = typed
        st.session_state[auto_key] = not typed.strip()

    # No `value=`: passing one as well as seeding session state makes Streamlit
    # print a complaint about it on the page.
    return st.text_input(
        label, key=field, on_change=typed_in, disabled=editor.busy, **kwargs
    )


def _plural(count, singular, plural=None):
    return singular if count == 1 else (plural or f"{singular}s")


# --------------------------------------------------------------------------
# Actions that would throw away unsaved words
# --------------------------------------------------------------------------


def _guarded(label, key, actions, editor, book, container=st, help_text="",
             **button_kwargs):
    """A button that asks first if there are unsaved changes to lose.

    Opening another draft, starting a new one and loading the example all
    replace what is on screen. Everything else in this editor can be undone by
    doing the opposite; these cannot, so they get the same second click the
    delete buttons elsewhere in the app get — but only when there is something
    to lose.
    """
    if container.button(
        label, key=f"{FIELD_PREFIX}act-{key}", disabled=editor.busy,
        use_container_width=True, help=help_text, **button_kwargs
    ):
        if is_dirty(book) or (is_unsaved() and book.has_content()):
            st.session_state[ARMED_ACTION] = key
            st.rerun()
        else:
            actions[key]()


def _armed_action_strip(actions, editor, book):
    armed = st.session_state.get(ARMED_ACTION)
    if armed not in actions:
        return
    st.warning(
        "This draft has changes that are not saved. Save it first, or continue "
        "and lose them.",
        icon="⚠️",
    )
    keep, discard = st.columns(2)
    if keep.button("Cancel", key=f"{FIELD_PREFIX}armed-cancel",
                   use_container_width=True, disabled=editor.busy):
        st.session_state[ARMED_ACTION] = None
        st.rerun()
    if discard.button("Continue without saving", key=f"{FIELD_PREFIX}armed-go",
                      type="primary", use_container_width=True,
                      disabled=editor.busy):
        st.session_state[ARMED_ACTION] = None
        actions[armed]()


# --------------------------------------------------------------------------
# Which book is on screen, and the drafts behind it
# --------------------------------------------------------------------------
# These two used to be one panel — "📚 Draft" — at the top of the screen, and
# that put the app's session bookkeeping above the book. A first visit opened on
# a draft-name box, a 💾 Save, a Save a copy and an autosave tick, none of which
# a visitor needs or can be expected to understand: a draft lives in the browser
# session and is gone when the tab closes, so saving one is a convenience for
# somebody coming back to the same tab later, not a way of keeping a book.
#
# So they are split by who they are for. `_start_panel` is the one question a new
# arrival might have — which book am I looking at, and can I see an example? —
# and stays near the top. `_draft_panel` is everything about the session's own
# store, and sits folded away at the foot, below the writing.
#
# Both are still *executed* before any `bk-` field widget, whatever order they
# are drawn in: loading a draft calls `adopt`, which deletes exactly those keys,
# and Streamlit will not have a widget's state changed after the widget exists.
# `render` keeps that straight with containers — see the note there.


def _start_panel(editor, book):
    """New book, the example, and the warning when either would lose words."""
    actions = {
        "new": lambda: _show(editor, blank_manuscript(), None, "",
                             "Started a new, empty book."),
        "example": lambda: _show(editor, example_manuscript(), None,
                                 "Example book",
                                 "Loaded the example book. Type over any of it."),
    }
    new_column, example_column, _spare = st.columns([1, 1, 2])
    _guarded("📄 New book", "new", actions, editor, book, container=new_column,
             help_text="Empties the editor and starts again.")
    _guarded("✨ Load the example", "example", actions, editor, book,
             container=example_column,
             help_text="A complete book that shows what every field does. "
                       "It opens as an unsaved draft, so it cannot "
                       "overwrite anything.")
    # Drawn here rather than with the drafts list because these are the two
    # buttons it can be armed by from this panel. `_armed_action_strip` returns
    # at once when the armed action is not one of the two it is handed, so the
    # drafts panel's own copy stays quiet while this one is showing and the
    # warning always appears beside the button that raised it.
    _armed_action_strip(actions, editor, book)


def _draft_panel(editor, book, folder):
    """Save, load, duplicate and delete drafts: the "keep my progress" half.

    Folded shut, and at the foot of the page. None of it is needed to get a book
    out of this app — the three buttons at the top do that — and a draft is only
    ever worth anything for as long as this browser session lasts.

    One expander, and no expander inside it: Streamlit refuses to nest them, and
    the uploader and the drafts list are plain sections in here now. Nothing is
    hidden behind two clicks that used to be behind one.
    """
    with st.expander(
        "📚 Drafts kept in this browser session — save, reopen, autosave"
    ):
        st.caption(
            "Optional, and only for coming back to this tab: a draft lives on "
            "the server for as long as the session does and goes with it. To "
            "**keep** a book, use one of the download buttons at the top of "
            "the page — **⬇️ Download as JSON** is the one that opens again."
        )

        name_column, save_column, saveas_column = st.columns(
            [3, 1, 1], vertical_alignment="bottom"
        )
        with name_column:
            typed_name = _auto_box(
                "Draft name", "draft-name", DRAFT_NAME_AUTO,
                st.session_state.get(DRAFT_NAME) or book.title.strip(),
                editor,
                placeholder="What to call this draft",
                help="What this draft is called in the list below. It is only "
                     "a name; the book's own title is set in Step 1. Empty it "
                     "to go back to using the title.",
            )
        # An empty box falls back to the book's title, so somebody who types a
        # title and presses Save never has to think about file names at all.
        save_name = clean_draft_name(typed_name or book.display_title)

        # Gated on room like everything else that writes. A draft is a few
        # kilobytes of JSON, so this only ever bites when the session is already
        # full of PDFs — but "full" has to mean full, or the limit is decorative.
        if save_column.button(
            "💾 Save", key=f"{FIELD_PREFIX}save", type="primary",
            use_container_width=True, disabled=editor.busy or editor.full,
            help="Keeps this draft under the name on the left, replacing that "
                 "draft if it is already there.",
        ):
            _save_now(editor, book, folder, save_name)

        if saveas_column.button(
            "Save a copy", key=f"{FIELD_PREFIX}save-as",
            use_container_width=True, disabled=editor.busy or editor.full,
            help="Saves under a free name (“My book 2”, “My book 3”) and "
                 "leaves the draft it came from as it was.",
        ):
            _save_now(editor, book, folder, unique_draft_name(folder, save_name))

        loaded_name = st.session_state.get(DRAFT_NAME, "")
        if draft_path() and loaded_name and save_name != loaded_name:
            st.caption(
                f"Saving now writes a **new** draft called “{save_name}”. "
                f"“{loaded_name}” stays in the list; delete it below if you "
                f"meant to rename."
            )

        # Sticky like the paper settings: a writer who turned autosave off and
        # then went to look at the conversion tab must not come back to find it
        # on again, quietly writing over the draft they were keeping.
        editor.sticky(f"{PREF_PREFIX}autosave", True)
        autosave = st.checkbox(
            "Autosave while I type",
            key=f"{PREF_PREFIX}autosave",
            disabled=editor.busy,
            help="Once a draft has been saved once, every later change is "
                 "written to the same file as you go. Save a copy first if "
                 "you want to keep a version.",
        )
        editor.remember(f"{PREF_PREFIX}autosave")

        status = st.empty()   # filled at the very end of `render`

        _draft_upload(editor)

        drafts = list_drafts(folder)
        st.markdown(f"**📂 This session's drafts ({len(drafts)})**")
        actions = {}
        _draft_list(editor, book, folder, drafts, actions)

        _armed_action_strip(actions, editor, book)
        return status, autosave, save_name


# A draft is kilobytes of JSON. The session's own 100 MB per-file ceiling is
# four orders of magnitude out for this, and a limit that never refuses anything
# is not a limit — so this one is sized for the thing it actually guards.
MAX_DRAFT_BYTES = 8 * 1024 * 1024


def _draft_upload(editor):
    """Bring a downloaded `.book.json` back in.

    The symmetric half of ⬇️ Download as JSON, and for a long time it did not
    exist: a draft could only come back inside a whole-session zip, which meant
    the one file the app hands you as *yours* was the one file it would not take
    back. A test even asserted its absence as a considered decision. It was the
    wrong one.

    Two details are load-bearing.

    **It lives here, in the draft panel**, which `render` *runs* before
    `_title_panel` and before any `bk-` field widget exists — whatever order the
    two are drawn in. Adopting a book deletes exactly those keys, and Streamlit
    will not have a widget's state changed after the widget has been
    instantiated. Moving this into a container filled after the title panel
    would break it in a way that only shows up on the run somebody uses it.

    **It confirms before replacing.** A `file_uploader` fires the moment a file
    is *picked*, not on a button, so it cannot go through `_guarded` — which is
    a button and would never see the click. The inline confirmation below is the
    same shape the workspace zip uses, and it doubles as a preview: the title,
    the author and the length of what is about to land.

    A bordered container rather than an expander of its own: the whole draft
    panel is inside one now, and Streamlit refuses to nest them.
    """
    round_key = "book_draft_upload_round"
    upload_round = st.session_state.setdefault(round_key, 0)
    with st.container(border=True):
        st.markdown("**📂 Open a .book.json**")
        # Keyed on the container so the style block in `app.py` can correct the
        # dropzone's own limit line, and on a round so a picked file is not
        # handed back — and re-loaded — on every rerun this page does.
        with st.container(key="draft-uploader"):
            incoming = st.file_uploader(
                "Open a book saved from this app",
                type=["json"],
                key=f"{PREF_PREFIX}draft-upload-{upload_round}",
                disabled=editor.busy,
                label_visibility="collapsed",
            )
        if incoming is None:
            st.caption(
                "A file this app downloaded, as **⬇️ Download as JSON**. It "
                "opens unsaved, so it cannot write over anything already here."
            )
            return

        book, problem = _read_draft(incoming)
        if problem:
            st.error(problem)
            return

        st.markdown(
            f"**{book.display_title}**"
            + (f" — {book.author}" if book.author else "")
        )
        st.caption(
            f"{book.words:,} words · {len(book.sections)} sections · "
            f"{len(book.chapters)} chapters"
        )
        if st.button(
            "Put this book in the editor",
            key=f"{PREF_PREFIX}draft-upload-go",
            type="primary", use_container_width=True, disabled=editor.busy,
        ):
            st.session_state[round_key] = upload_round + 1
            _show(
                editor, book, None, clean_draft_name(Path(incoming.name).stem),
                f"Opened “{book.display_title}”. The download buttons at the "
                "top of the page take it away again.",
            )


def _read_draft(item):
    """`(Manuscript, problem)` from an uploaded file. One of the two is always None.

    The `isinstance` check is the important line, and it is not paranoia.
    `Manuscript.from_dict` opens with `data = data if isinstance(data, dict)
    else {}`, so a JSON *list*, number or string does not raise — it returns a
    perfectly valid **empty book**, and adopting one of those would silently
    wipe whatever the user had. `load_draft` guards this for a file on disk;
    nothing would guard it here.
    """
    size = item.size if item.size is not None else len(item.getbuffer())
    if size > MAX_DRAFT_BYTES:
        return None, (
            f"That file is {size / (1024 * 1024):.1f} MB. A book saved by this "
            f"app is a few kilobytes of text, so this is not one."
        )
    try:
        text = item.getvalue().decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "That file is not text this app can read."
    try:
        data = json.loads(text)
    except ValueError:
        return None, (
            "That file is not valid JSON. It has to be one this app saved with "
            "**⬇️ Download as JSON**."
        )
    if not isinstance(data, dict):
        return None, (
            "That JSON file is not a book. It has to be one this app saved."
        )
    try:
        book = Manuscript.from_dict(data)
    except Exception as error:
        return None, f"That book could not be read: {error}"
    if not book.has_content() and not book.title.strip():
        return None, (
            "There is nothing in that file — no title, and no text in any "
            "section."
        )
    return book, None


def _draft_list(editor, book, folder, drafts, actions):
    st.caption("Kept for this session only. **📤 Save my data** takes them home.")
    if not drafts:
        st.info("No drafts saved yet. **💾 Save** puts this one here.")
        return

    loaded_path = draft_path()
    for draft in drafts:
        with st.container(border=True):
            current = loaded_path and str(draft.path) == loaded_path
            st.markdown(f"**{draft.name}**" + (" · *loaded*" if current else ""))
            if draft.problem:
                st.error(f"This file will not load: {draft.problem}")
            else:
                st.caption(
                    f"{draft.title}"
                    + (f" · {draft.author}" if draft.author else "")
                    + f" · {draft.words:,} words · "
                    f"{draft.sections} {_plural(draft.sections, 'section')} · "
                    f"{draft.modified_text}"
                )

            load_column, delete_column = st.columns([2, 1])
            key = f"load-{draft.name}"
            if not draft.problem:
                actions[key] = _loader(editor, draft)
                _guarded(
                    "Load" if not current else "Reload (discard changes)", key,
                    actions, editor, book, container=load_column,
                    help_text="Loads this draft into the editor.",
                )
            editor.arm_delete(
                f"draft-{draft.name}",
                "Deletes this draft file for good. The book PDF it built, if "
                "any, is a separate file and is left alone.",
                container=delete_column, disabled=editor.busy,
            )

            def remove(path=draft.path, name=draft.name):
                delete_draft(folder, path)
                if draft_path() == str(path):
                    # The draft on screen was the one deleted. The words stay
                    # in the editor — they are still the user's — but they are
                    # no longer backed by a file, and autosave must not put it
                    # straight back.
                    st.session_state[DRAFT_PATH] = ""
                    st.session_state[SAVED_JSON] = ""
                return f"Deleted the draft “{name}”."

            editor.confirm_delete(f"draft-{draft.name}", remove,
                                  disabled=editor.busy)


def _loader(editor, draft):
    def load_it():
        try:
            book = load_draft(draft.path)
        except Exception as error:
            editor.finish(error=f"Could not load “{draft.name}”: {error}")
            return
        _show(editor, book, draft.path, draft.name, f"Loaded “{draft.name}”.")

    return load_it


def _show(editor, book, path, name, message):
    adopt(book, path=path, name=name)
    editor.finish(message)


def _save_now(editor, book, folder, name):
    try:
        path = save_draft(folder, book, name)
    except Exception as error:
        editor.finish(error=f"Could not save the draft: {error}")
        return
    mark_saved(book, path, draft_name_of(path))
    editor.finish(f"Saved the draft “{draft_name_of(path)}”.")


# --------------------------------------------------------------------------
# The manuscript itself
# --------------------------------------------------------------------------


def _title_panel(editor, book):
    with st.container(border=True):
        st.markdown("#### 🏷️ Title page")
        _text("Title", book, "title", "title", editor,
              placeholder="The name of the book")
        _text("Subtitle", book, "subtitle", "subtitle", editor,
              placeholder="Optional")
        left, right = st.columns(2)
        with left:
            _text("Author", book, "author", "author", editor,
                  placeholder="Whose name goes on the cover")
        with right:
            _text("Series", book, "series", "series", editor,
                  placeholder="e.g. Book Two of the Marches",
                  help="Printed small, above the title.")

        with st.expander("Publication details: the copyright page"):
            st.caption(
                "All optional, and all of it *this* page: fill in any one of "
                "them and a copyright page is printed behind the title page "
                "with whatever is here. Leave them all empty and there is no "
                "such page at all. The author's name above does not count."
            )
            first, second = st.columns(2)
            with first:
                _text("Publisher or imprint", book, "publisher", "publisher",
                      editor, placeholder="e.g. Kitchen Table Press")
                _text("Year", book, "year", "year", editor, placeholder="2026")
            with second:
                _text("Edition", book, "edition", "edition", editor,
                      placeholder="First edition")
                _text("ISBN", book, "isbn", "isbn", editor, placeholder="Optional")
            _text(
                "Copyright line", book, "copyright_notice", "copyright", editor,
                placeholder=book.copyright_line() or "Copyright © 2026 Your Name",
                help="Left empty, this is built from the author and the year.",
            )
            _area(
                "Anything else on that page", book, "rights", "rights", editor,
                height=box_height(book.rights),
                placeholder="Moral rights, a printing history, a note about the "
                            "typeface. One paragraph per blank line.",
            )


# The text boxes size themselves to what is in them, between these two. A short
# note gets a short box instead of a screenful of blank, and a chapter grows
# until it would take over the page, at which point it scrolls inside itself —
# so the panels below a long chapter stay reachable without scrolling past it.
MIN_BOX_HEIGHT_PX = 98      # Streamlit's own floor for a labelled text area
MAX_BOX_HEIGHT_PX = 640
BOX_LINE_HEIGHT_PX = 21     # one line of the default text-area face
BOX_PADDING_PX = 34         # the label, the frame and the resize handle
BOX_CHARACTERS_PER_LINE = 92


def box_height(text):
    """A text box tall enough for `text`, without being taller than the page.

    An estimate rather than a measurement — the browser knows the real width of
    the box and Python never will — so it counts hard line breaks and the wraps
    a long line implies, then clamps. Getting it a line out costs nothing: the
    box scrolls, and it is re-estimated on the next keystroke either way.
    """
    lines = 0
    for line in (text or "").split("\n"):
        lines += max(1, -(-len(line) // BOX_CHARACTERS_PER_LINE))  # ceil
    # One spare line, so there is somewhere to type at the end of the text
    # rather than the box growing only once the line is already full.
    wanted = BOX_PADDING_PX + (lines + 1) * BOX_LINE_HEIGHT_PX
    return min(max(wanted, MIN_BOX_HEIGHT_PX), MAX_BOX_HEIGHT_PX)


def _sections_panel(editor, book, part, heading, keys, blurb, numbers):
    sections = book.list_for(part)
    words = sum(section.words for section in sections)
    with st.container(border=True):
        st.markdown(f"#### {heading}")
        st.caption(
            f"{blurb} · {len(sections)} {_plural(len(sections), 'section')}"
            + (f" · {words:,} words" if words else "")
        )

        for index, section in enumerate(sections):
            _section_card(editor, book, part, index, section, numbers)

        choose, add = st.columns([3, 1])
        _seed_choice(f"{FIELD_PREFIX}add-kind-{part}", keys[0], keys)
        chosen = choose.selectbox(
            "Add to this part",
            keys,
            format_func=lambda key: KINDS[key].label,
            key=f"{FIELD_PREFIX}add-kind-{part}",
            disabled=editor.busy,
            label_visibility="collapsed",
        )
        if add.button(
            "➕ Add", key=f"{FIELD_PREFIX}add-{part}", use_container_width=True,
            disabled=editor.busy,
        ):
            section = Section(kind=chosen)
            sections.append(section)
            # Opened by default, because the reason anyone clicks Add is to
            # start typing into the thing they just added.
            st.session_state[f"{FIELD_PREFIX}open-{section.id}"] = True
            st.rerun()

        _undo_strip(editor, book, part)


def _section_title(section, numbers):
    """What one section's card is called when it is closed.

    Chapters lead with their number, and everything else with the kind it was
    added as — but only when that is not simply the heading again. A card
    reading "Interlude · Interlude · 45 words" is the same word twice and the
    list of them is what the shape of the book is read off.
    """
    spec = kind_of(section.kind)
    heading = section.heading.strip() or spec.heading
    words = section.words
    tail = f" · {words:,} words" if words else " · empty"

    number = numbers.get(section.id)
    if number is not None:
        return f"{number}. {heading or 'Untitled'}{tail}"
    if heading and not heading.casefold().startswith(spec.label.casefold()):
        return f"{spec.label} · {heading}{tail}"
    return f"{heading or spec.label or 'Untitled'}{tail}"


def _section_card(editor, book, part, index, section, numbers):
    sections = book.list_for(part)
    spec = kind_of(section.kind)
    # Stored once, per section, and never changed afterwards: an `expanded`
    # that moved about between runs would fight the user for control of which
    # cards are open.
    open_key = f"{FIELD_PREFIX}open-{section.id}"
    expanded = st.session_state.setdefault(open_key, len(book.sections) <= 2)

    with st.expander(_section_title(section, numbers), expanded=expanded):
        _text(
            "Heading" + ("" if spec.heading else " (printed as typed)"),
            section, "heading", f"head-{section.id}", editor,
            placeholder=spec.heading or "Leave empty for no heading",
            help=None if spec.heading else
            "This kind of section has no standard heading, so whatever you "
            "type here is what gets printed.",
        )
        _area(
            "Text", section, "text", f"text-{section.id}", editor,
            height=box_height(section.text), placeholder=spec.hint,
            help="A blank line starts a new paragraph. See the formatting note "
                 "under the chapters for the rest.",
        )

        up, down, copy, remove = st.columns(4)
        if up.button("↑ Up", key=f"{FIELD_PREFIX}up-{section.id}",
                     use_container_width=True,
                     disabled=editor.busy or index == 0):
            sections[index - 1], sections[index] = sections[index], sections[index - 1]
            st.rerun()
        if down.button("↓ Down", key=f"{FIELD_PREFIX}down-{section.id}",
                       use_container_width=True,
                       disabled=editor.busy or index >= len(sections) - 1):
            sections[index + 1], sections[index] = sections[index], sections[index + 1]
            st.rerun()
        if copy.button("⧉ Duplicate", key=f"{FIELD_PREFIX}copy-{section.id}",
                       use_container_width=True, disabled=editor.busy,
                       help="A second section with the same words, below this one."):
            twin = Section(kind=section.kind, heading=section.heading,
                           text=section.text)
            sections.insert(index + 1, twin)
            st.session_state[f"{FIELD_PREFIX}open-{twin.id}"] = False
            st.rerun()
        if remove.button("🗑 Remove", key=f"{FIELD_PREFIX}del-{section.id}",
                         use_container_width=True, disabled=editor.busy,
                         help="Removes this section. One click of Undo brings "
                              "it back."):
            # No confirmation, because there is a real undo: the section is
            # kept whole and put back where it was if Undo is clicked.
            st.session_state[REMOVED] = (part, index, sections.pop(index))
            for key in (f"{FIELD_PREFIX}head-{section.id}",
                        f"{FIELD_PREFIX}text-{section.id}", open_key):
                st.session_state.pop(key, None)
            st.rerun()


def _undo_strip(editor, book, part):
    removed = st.session_state.get(REMOVED)
    if not removed or removed[0] != part:
        return
    where, index, section = removed
    heading = section.heading.strip() or kind_of(section.kind).label
    left, right = st.columns([3, 1])
    left.caption(f"Removed **{heading}** ({section.words:,} words).")
    if right.button("↩ Undo", key=f"{FIELD_PREFIX}undo-{part}",
                    use_container_width=True, disabled=editor.busy):
        sections = book.list_for(where)
        sections.insert(min(index, len(sections)), section)
        st.session_state[REMOVED] = None
        st.rerun()


def _formatting_note():
    with st.expander("How to type the text"):
        st.markdown(
            """
Type plainly. Titles, headings, text etc are formatted automatically.
For customisation only these things mean anything special:

| What you type | What you get |
| --- | --- |
| a blank line | a new paragraph |
| `*one star*` or `_underscores_` | *italic* |
| `**two stars**` | **bold** |
| `# A line starting with a hash` | a heading inside the section |
| `## Two hashes` | a smaller heading inside the section |
| a line of `***` or `---` | a scene break |
| lines starting with `>` | a quotation, keeping its line breaks |
"""
        )


# --------------------------------------------------------------------------
# Design
# --------------------------------------------------------------------------


def save_name_for(book):
    """What a downloaded copy of `book` is called.

    The name in the draft box when there is one, and the book's own title when
    there is not — so the file that lands in somebody's downloads folder is
    called what the thing on screen is called, whichever of the two ways out
    they took.
    """
    typed = st.session_state.get(f"{FIELD_PREFIX}typed-draft-name")
    return clean_draft_name(typed or st.session_state.get(DRAFT_NAME)
                            or book.display_title)


def _design_summary(book, editor):
    """One line describing the design, for the head of a panel that is all closed.

    Three expanders that start folded are three expanders somebody has to open
    before they know what size book they are about to make. This is the answer
    to that question, written where the question is asked, so opening one is a
    decision rather than a search.

    Deliberately forgiving: it is a caption, and a caption that raised would take
    the whole editor down over a line of description.
    """
    design = book.design
    unit = editor.unit
    try:
        pages = f"{unit.size_label(design.page_width_in, design.page_height_in)} pages"
        if design.page_size_name and design.page_size_name != CUSTOM_PAGE_SIZE:
            pages = f"{design.page_size_name} pages"
        face = FONTS[design.font_key].label if design.font_key in FONTS else "—"
        parts = [
            pages,
            f"{face} {design.font_size_pt:g} pt",
            "justified" if design.justify else "ragged right",
        ]
        if not design.page_numbers:
            parts.append("no page numbers")
        if not design.contents:
            parts.append("no contents")
        return " · ".join(parts)
    except Exception:  # pragma: no cover - a caption must never break the page
        return "Page size, type and page furniture."


def _design_panel(editor, book):
    """The whole look of the book, including how big it is.

    Returns `(page_size_in, sheet_size_pt)` for the build below it: the page
    size to set the type at, or None to use the one in the design, and the paper
    that comes to, or None when the paper is simply two of the book's own pages.
    """
    design = book.design
    unit = editor.unit

    with st.container(border=True):
        st.markdown("#### Step 2 — Design it")

        # A live summary of the three panels below, so the whole of the design
        # can be read at a glance and none of it has to be opened to find out
        # what size book is about to be made. That is what lets all three start
        # closed: the defaults already make a real book, and this says which one.
        st.caption(_design_summary(book, editor))

        with st.expander("📐 Page size and margins"):
            page_size_in, sheet_size_pt = _size_controls(editor, design, unit)

            # Read after the controls above, which may have just changed it.
            built = build_design(design, page_size_in)
            sheet_w, sheet_h = built.source_page_size_in
            if sheet_size_pt is None:
                stock = smallest_sheet_for(sheet_w, sheet_h)
                st.caption(
                    f"Two of these side by side make one sheet of "
                    f"**{describe_size(sheet_w, sheet_h, unit)}**."
                )
            else:
                # The page read off `build_design` rather than off the sheet,
                # because a sheet at the very edge of what paper can be — under
                # two inches wide — leaves a page smaller than the typesetter
                # will set, and this has to be the size the book really gets.
                st.caption(
                    f"One page of the finished book is "
                    f"**{unit.size_label(built.page_width_in, built.page_height_in)}**, "
                    f"half of that sheet. The book is set at that size, so "
                    f"nothing is scaled."
                )

            first, second = st.columns(2)
            with first:
                _length("Inner margin", design, "margin_inner_in", "m-in", editor,
                        "Against the fold. Give this a little more than the "
                        "outer margin: some of it disappears into the binding.")
                _length("Top margin", design, "margin_top_in", "m-top", editor,
                        "Above the text. The running head sits in here.")
            with second:
                _length("Outer margin", design, "margin_outer_in", "m-out", editor,
                        "The thumb edge. Most printers cannot print within about "
                        "0.25 in of the paper edge.")
                _length("Bottom margin", design, "margin_bottom_in", "m-bot",
                        editor, "Below the text. The page number sits in here.")
            # Measured on the page the build would really use, so this is the
            # strip the type actually gets rather than the one it would get at a
            # page size the paper has overruled. Re-read, because the four
            # margins above have just been edited into the design.
            built = build_design(design, page_size_in)
            st.caption(
                f"Text block "
                f"**{unit.size_label(built.text_width_in, built.text_height_in)}**"
            )

        with st.expander("🔠 Type"):
            keys = list(FONTS)
            font_key = design.font_key if design.font_key in keys else keys[0]
            _seed_choice(f"{FIELD_PREFIX}font", font_key, keys)
            chosen_font = st.selectbox(
                "Typeface", keys,
                format_func=lambda key: FONTS[key].label,
                key=f"{FIELD_PREFIX}font", disabled=editor.busy,
            )
            design.font_key = chosen_font
            st.caption(FONTS[chosen_font].note)

            first, second = st.columns(2)
            with first:
                _points("Type size (pt)", design, "font_size_pt", "font-size",
                        editor, step=0.25,
                        help_text="Book text is usually set at 10 to 11 point.")
            with second:
                # Clamped to the slider's own range, which is narrower than the
                # limit a draft is loaded under: a slider refuses to draw at all
                # for a value outside it.
                _seed(f"{FIELD_PREFIX}leading",
                      min(max(float(design.line_spacing), 1.0), 2.0))
                spacing = st.slider(
                    "Line spacing", min_value=1.0, max_value=2.0, step=0.05,
                    key=f"{FIELD_PREFIX}leading", disabled=editor.busy,
                    help="A multiple of the type size. Book text is usually set "
                         "between 1.2 and 1.4.",
                )
                design.line_spacing = float(spacing)

            _check("Justify the text", design, "justify", "justify", editor,
                   help="Straight down both edges. Untick for a ragged right "
                        "edge, which reads better in a narrow column.")
            first, second = st.columns(2)
            with first:
                _length("Paragraph indent", design, "first_line_indent_in",
                        "indent", editor,
                        "How far the first line of a paragraph is pushed in.")
            with second:
                _points("Space between paragraphs (pt)", design,
                        "paragraph_space_pt", "para-space", editor, step=1.0,
                        help_text="Books normally use an indent *or* a space, "
                                  "not both. Leave this at 0 if you are using "
                                  "an indent.")

        with st.expander("📑 Structure and page furniture"):
            starts = (CHAPTER_START_NEW_PAGE, CHAPTER_START_RECTO)
            _seed_choice(f"{FIELD_PREFIX}chapter-start", design.chapter_start, starts)
            start = st.radio(
                "Start each section",
                starts,
                format_func=lambda value: (
                    "On the next page" if value == CHAPTER_START_NEW_PAGE
                    else "On a right-hand page (like a printed book)"
                ),
                key=f"{FIELD_PREFIX}chapter-start", disabled=editor.busy,
                help="A printed book opens its chapters on right-hand pages and "
                     "leaves the facing page blank. That looks right, and costs "
                     "paper: with short chapters it can leave a lot of the book "
                     "empty.",
            )
            design.chapter_start = start

            labels = list(CHAPTER_LABELS)
            _seed_choice(f"{FIELD_PREFIX}chapter-label", design.chapter_label, labels)
            design.chapter_label = st.selectbox(
                "Number the chapters as",
                labels,
                format_func=lambda value: CHAPTER_LABEL_NAMES[value],
                key=f"{FIELD_PREFIX}chapter-label", disabled=editor.busy,
                help="Only sections added as **Chapter** are numbered. A "
                     "prologue or an appendix never joins the count.",
            )

            _check("Page numbers", design, "page_numbers", "folios", editor,
                   help="Centred at the foot of every page except the display "
                        "pages: the title, the copyright, a dedication.")
            _check("Running heads", design, "running_heads", "heads", editor,
                   help="The book's title along the top of left-hand pages and "
                        "the current section along the top of right-hand ones. "
                        "Not printed on a page where a section opens.")
            _check("Title page", design, "title_page", "title-page", editor)
            _check("Copyright page", design, "copyright_page", "copy-page", editor,
                   help="Only printed once one of the **Publication details** on "
                        "the title page card is filled in. An author's name on "
                        "its own is not enough: that would put a page nobody "
                        "asked for in front of every book.")
            _check("Table of contents", design, "contents", "contents", editor,
                   help="Built from the section headings, with the page each one "
                        "starts on. It goes after any dedication and epigraph, "
                        "as it does in a printed book.")
            _text("Scene break marker", design, "scene_break", "scene-break",
                  editor,
                  help="What a line of `***` in the text is printed as.")

        # The two settings this screen shares with the conversion screen, drawn
        # here under the same keys. They belong to the *printing* rather than to
        # the book, which is why they are apart from the three panels above and
        # why they survive a trip to the other screen unchanged.
        if editor.printing_options is not None:
            with st.expander("⚙️ Advanced paper and printing"):
                st.caption(
                    "How the printed sheets are gathered and folded. The same "
                    "two settings the conversion screen uses."
                )
                editor.printing_options()

    return page_size_in, sheet_size_pt


def _size_controls(editor, design, unit):
    """How big the book is, asked once, from whichever end suits the writer.

    The finished page and the paper it prints on are the same measurement seen
    from two ends, so only one of the two menus is ever on screen and the other
    figure is worked out and printed underneath. Returns the `(page_size_in,
    sheet_size_pt)` a build would use; page size None means "the one in the
    design", which is the case where the design's own menu is the live one.
    """
    editor.sticky(SIZE_FROM_KEY, SIZE_FROM_PAGE)
    size_from = st.radio(
        "Give the size as",
        (SIZE_FROM_PAGE, SIZE_FROM_SHEET),
        format_func=SIZE_FROM_LABELS.get,
        horizontal=True,
        key=SIZE_FROM_KEY,
        disabled=editor.busy,
        help="A sheet is folded across its width, so one book page is half a "
             "sheet wide and a full sheet tall. Say whichever of the two you "
             "actually care about and the other follows.",
    )
    editor.remember(SIZE_FROM_KEY)

    if size_from == SIZE_FROM_SHEET:
        return _sheet_menu(editor, unit)
    _page_size_menu(editor, design, unit)
    return None, None


def _sheet_menu(editor, unit):
    """The paper to print on, when the size is being given from that end.

    Named sheets only. Anything else is a page size nobody sells paper in, and
    that is what **The finished page** and its *Custom size…* entry are for —
    two custom size boxes for one measurement is the doubling this panel exists
    to end.
    """
    # Stacked, not side by side: this panel already lives in the narrow
    # right-hand column of the page, and splitting that in two would leave a
    # menu and a pair of radio buttons too cramped to read.
    names = [size.name for size in SHEET_SIZES]

    editor.sticky(SHEET_KEY, "A4")
    chosen = st.selectbox(
        "Paper to print on",
        names,
        format_func=lambda name:
            f"{name} · {unit.size_label(*find_paper_size(name).size_in(False))}",
        key=SHEET_KEY,
        disabled=editor.busy,
        help="The paper you will load into the printer. The book is set at half "
             "a sheet to a page, so the type is drawn at its finished size and "
             "nothing is ever scaled.",
    )
    editor.remember(SHEET_KEY)

    editor.sticky(SHEET_LANDSCAPE_KEY, True)
    landscape = st.radio(
        "Sheet orientation",
        (True, False),
        format_func=lambda value: "Landscape" if value else "Portrait",
        horizontal=True,
        key=SHEET_LANDSCAPE_KEY,
        disabled=editor.busy,
        help="Landscape puts the long edge across. A sheet is folded across its "
             "width, so that is normally what you want; portrait gives tall, "
             "narrow book pages.",
    )
    editor.remember(SHEET_LANDSCAPE_KEY)

    return paper_from_sheet(chosen, landscape)


def paper_from_sheet(name, landscape=True):
    """`(page_size_in, sheet_size_pt)` for one named sheet.

    The rule this whole screen turns on, written once: a sheet is folded across
    its width, so a book page is half a sheet wide and a full sheet tall. The
    menu above returns this, and it is what the download buttons are handed —
    so anything that has to say what a chosen sheet means asks here rather than
    doing the halving again.
    """
    width_in, height_in = find_paper_size(name).size_in(landscape)
    return (
        (width_in / 2, height_in),
        (width_in * PT_PER_INCH, height_in * PT_PER_INCH),
    )


def _page_size_menu(editor, design, unit):
    """The finished page size, the book's own, kept in the draft it belongs to."""
    names = [size.name for size in BOOK_PAGE_SIZES] + [CUSTOM_PAGE_SIZE]
    current = (design.page_size_name if design.page_size_name in names
               else CUSTOM_PAGE_SIZE)
    _seed_choice(f"{FIELD_PREFIX}page-size", current, names)
    chosen = st.selectbox(
        "Finished page size",
        names,
        format_func=lambda name: (
            name if name == CUSTOM_PAGE_SIZE
            else f"{name} · {unit.size_label(*_book_size(name))}"
        ),
        key=f"{FIELD_PREFIX}page-size",
        disabled=editor.busy,
        help="One page of the finished book, as the reader holds it. The PDF "
             "this builds has pages twice as wide, because two book pages sit "
             "side by side on every sheet.",
    )
    if chosen != CUSTOM_PAGE_SIZE and chosen != design.page_size_name:
        design.page_size_name = chosen
        design.page_width_in, design.page_height_in = _book_size(chosen)
        # The two custom boxes are keyed per unit and would otherwise keep
        # showing the size the user has just replaced.
        _forget_lengths(("page-w", "page-h"))
        st.rerun()
    if chosen == CUSTOM_PAGE_SIZE:
        design.page_size_name = CUSTOM_PAGE_SIZE
        first, second = st.columns(2)
        with first:
            _length("Page width", design, "page_width_in", "page-w", editor,
                    "The width of one page of the finished book.")
        with second:
            _length("Page height", design, "page_height_in", "page-h", editor,
                    "The height of one page of the finished book.")


def _book_size(name):
    for size in BOOK_PAGE_SIZES:
        if size.name == name:
            return (size.width_in, size.height_in)
    return (148 / 25.4, 210 / 25.4)


def _forget_lengths(keys):
    """Drop one measurement's widget state in every unit it might be held in."""
    for key in keys:
        for suffix in ("in", "cm", "mm"):
            st.session_state.pop(f"{LENGTH_PREFIX}{key}-{suffix}", None)


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------


def build_design(design, page_size_in=None):
    """The design a build would really use, clamped the way the typesetter will.

    Always a copy — `clamp_design` works in place, and asking what a build would
    look like must never be the thing that edits the draft on screen. Measuring
    a book is a question, not a change. `page_size_in` None means the design's
    own page size, which is also what keeps this a copy in that case.
    """
    if page_size_in is None:
        page_size_in = (design.page_width_in, design.page_height_in)
    return clamp_design(page_sized(design, page_size_in))


def _paper_for(editor, design, page_size_in, sheet_size_pt):
    """How the book being typed would sit on the paper it is going on.

    Returns `(fit, problems, notes)`. The conversion view asks these three
    questions of every PDF it lists, and there is no reason the answer should
    arrive later here just because the PDF does not exist yet: the size of the
    book is known before a word of it is set.

    Normally the answer is "exactly, at 1:1" — the book is set at the size of
    the sheet, so there is nothing to fit. The arithmetic is done anyway because
    a page size can still be clamped away from the sheet at the extremes (a
    sheet under two inches wide, say), and a book that would run off the paper
    has to be said before it is built, not after.
    """
    source_pt = tuple(
        length * PT_PER_INCH
        for length in build_design(design, page_size_in).source_page_size_in
    )
    try:
        # FIT, and no choice offered anywhere: a book being typed is *set* at
        # the size it will print at, so there is never a difference to resolve.
        # The mode only matters in the one clamped case above.
        fit = SheetFit.compute(source_pt, sheet_size_pt, FIT)
    except ValueError as error:
        return None, [str(error)], []
    return (
        fit,
        sheet_problems(fit, editor.unit),
        # No ColumnLayout: the outer margin this book prints with is its own,
        # and the conversion tab's column measurements describe somebody else's
        # PDF.
        sheet_warnings(fit, None, editor.unit),
    )


def _figures(book, page_size_in, editor):
    """One line: how long the book is and roughly what it comes to on paper.

    Drawn under the three buttons, where it answers "what am I about to get?"
    without anything having to be opened. Deliberately forgiving — it is a
    caption, and the estimate is labelled as one everywhere it appears.
    """
    pages = estimate_book_pages(book, page_size_in)
    parts = [f"**{book.words:,}** words",
             f"**{len(book.chapters)}** {_plural(len(book.chapters), 'chapter')}"]
    if pages:
        parts.append(f"≈ **{pages}** book pages")
        try:
            plans = plan_signatures(pages, editor.sheets_per_signature)
        except ValueError:
            plans = []
        if plans:
            sheets = sum(plan.sheets for plan in plans)
            parts.append(
                f"≈ **{len(plans)} {_plural(len(plans), 'signature')}** on "
                f"{sheets} {_plural(sheets, 'sheet')} of paper"
            )
    return " · ".join(parts)


def _take_away_panel(editor, book, page_size_in, sheet_size_pt):
    """The three ways out, at the top of the screen with nothing above them.

    All three are `st.download_button`s, including the two that have real work
    to do first. Streamlit calls a download button's `data` when the button is
    *clicked*, so "make the PDF" and "download the PDF" are one action: the book
    is typeset — and, for the signatures, imposed — inside that call, and the
    bytes go straight to the browser.

    It used to be two "📄 Create…" buttons at the foot of the right-hand column.
    They wrote files into the session and then offered a *second* button to
    fetch them, which put the one thing a writer came here for behind two clicks
    and a scroll, underneath a drafts panel that is machinery for people who
    already know the app. The order is the other way round now: the three files
    at the top, and the session's own store folded away at the foot.

    Two things are given up for that single click, and both are worth saying.
    The build runs off the script run (see `Script/book_build.py`), so there is
    no progress bar, and a failure arrives as a failed download rather than as a
    banner on the page. Typesetting a book is seconds of work; two clicks and a
    scroll to earn a progress bar was the wrong trade.

    Nothing here is gated on `editor.full`. These downloads write nothing the
    session keeps, and a session with no room left is exactly when being able to
    take your book away matters most.
    """
    unit = editor.unit
    empty = not book.has_content()
    # The same three answers the conversion screen gets for a PDF: what fits,
    # what will not, and what is worth a word of warning.
    fit, problems, notes = _paper_for(
        editor, book.design, page_size_in, sheet_size_pt
    )

    with st.container(border=True, key="takeaway"):
        st.markdown("#### ⬇️ Take your book away")
        st.caption(
            "Nothing is kept on the server. Each button makes the file and "
            "hands it straight to your browser."
        )
        # Filled at the end of this function, so the three buttons sit here —
        # above the details — while still being drawn after the box that names
        # them.
        buttons = st.container()

        with st.expander("What these files are called, and the paper they are for"):
            file_name = _auto_box(
                "Name the files", "file-name", FILE_NAME_AUTO,
                book.title.strip() or "Untitled book", editor,
                help="What the PDF and the zip of signatures are called when "
                     "they reach your computer. Empty it to go back to using "
                     "the book's title.",
            )
            st.caption(f"→ `{editor.build_path(file_name).name}`")

            # The same two sizes, worded the same way, as the card on a book
            # waiting to be converted: what to put in the printer, and what
            # comes out.
            if fit is not None:
                st.markdown(
                    f"**Paper to load in the printer:** "
                    f"{describe_size(*fit.sheet_size_in, unit)}\n\n"
                    f"**Each page of the finished book:** "
                    f"{unit.size_label(*fit.book_page_size_in)}"
                )
                # No talk of scaling on the ordinary path, because there is none
                # to talk about: the type goes into the PDF at its finished
                # size, whatever paper that is. It is said only in the one case
                # where the book cannot be set at the sheet's size — a sheet so
                # small that the page size is clamped — and then it is worth
                # saying.
                if fit.is_resized:
                    st.caption(
                        f"Scaled to **{fit.scale * 100:.1f}%**: this sheet is "
                        f"smaller than the smallest page a book can be set at. "
                        f"Print at 100% / “Actual size”."
                    )
                else:
                    st.caption(
                        "Set at this size, so nothing is scaled and no "
                        "sharpness is lost. Chosen under **Step 2 — Design "
                        "it**, in 📐 Page size and margins."
                    )
            for note in notes:
                st.caption(f"ℹ️ {note}")

        pdf_name = editor.build_path(file_name).name
        stem = Path(pdf_name).stem

        with buttons:
            json_column, pdf_column, signature_column = st.columns(3)

            # The editable copy first: it costs nothing, it needs no paper
            # decision, and it is the only one of the three that can be brought
            # back in. Somebody who has typed for an hour and wants to stop
            # should be able to keep their work with one click.
            json_column.download_button(
                "⬇️ Download as JSON",
                # `data` is the function, not its result, so the text is
                # produced when the button is clicked and not on every keystroke
                # that reruns this page. It hands over the book as it stands on
                # screen rather than the last version written — saved or not,
                # what you see is it.
                data=lambda: book.to_json(),
                file_name=f"{save_name_for(book)}{DRAFT_SUFFIX}",
                mime="application/json",
                key=f"{FIELD_PREFIX}download-json",
                type="primary", use_container_width=True,
                disabled=empty or editor.busy,
                help="The book exactly as it is on screen, as one file. Open it "
                     "again with 📂 Open a .book.json at the foot of this "
                     "screen — this is the only format that comes back in.",
            )

            pdf_column.download_button(
                "⬇️ Download as PDF",
                data=lambda: editor.pdf_bytes(book, file_name, page_size_in),
                file_name=pdf_name,
                mime="application/pdf",
                key=f"{FIELD_PREFIX}download-pdf",
                type="primary", use_container_width=True,
                disabled=empty or editor.busy or editor.pdf_bytes is None,
                help="Typesets the book and downloads it — two book pages to a "
                     "sheet, set at the size in Step 2. Print it as it is, or "
                     "put it through 📄 I have a PDF book to fold it into "
                     "signatures.",
            )

            signature_column.download_button(
                "⬇️ Download as signatures",
                data=lambda: editor.signature_bytes(
                    book, file_name, page_size_in, sheet_size_pt
                ),
                file_name=f"{stem}.zip",
                mime="application/zip",
                key=f"{FIELD_PREFIX}download-signatures",
                type="primary", use_container_width=True,
                # Gated on the paper as well, unlike the two beside it: this one
                # goes on to impose, and a sheet the book will not fit is the
                # one thing that stops an imposition. The PDF is still offered,
                # because that half would have worked.
                disabled=(empty or editor.busy or bool(problems)
                          or editor.signature_bytes is None),
                help="Typesets the book, folds it into signatures and downloads "
                     "them as one zip — every signature file in print order, "
                     "with the note saying how to print and fold them. Uses "
                     "the folding settings in ⚙️ Advanced paper and printing.",
            )

            if empty:
                st.info(
                    "Nothing to take away yet. Give the book a title, or type "
                    "something into one of the sections below."
                )
            else:
                st.caption(_figures(book, page_size_in, editor))
            for problem in problems:
                st.error(problem)


# --------------------------------------------------------------------------
# The view
# --------------------------------------------------------------------------


def render(editor, folder):
    """Draw the whole editor.

    Claims nothing and returns nothing. It used to hand back a long job for the
    runner at the foot of `app.py` to perform — building the PDF, imposing it —
    and there is none to hand back any more: the two buttons that did that are
    download buttons now, and they do their work inside the click. See
    `_take_away_panel`.
    """
    book = manuscript()

    # Measurements are keyed per unit; when the unit changes the old keys have
    # to go, or switching back would resurrect the number that was on screen
    # before rather than converting the one in the manuscript.
    if st.session_state.get(f"{PREF_PREFIX}unit") != editor.unit.name:
        for key in [k for k in st.session_state if k.startswith(LENGTH_PREFIX)]:
            del st.session_state[key]
        st.session_state[f"{PREF_PREFIX}unit"] = editor.unit.name

    # The banner for a book that has just been written by a model. Popped rather
    # than read, so it says so once and then stops — it describes how the book
    # arrived, which stops being news the moment the user starts editing it.
    #
    # It leads with the move, because the move is the surprising part: the
    # reader pressed a button on the AI screen and the whole page changed under
    # them. Saying what the book is before saying where they are leaves them
    # working that out for themselves.
    if st.session_state.pop(HANDOFF, False):
        st.success(
            f"**You have been moved to ✍️ Write your book** — the AI has "
            f"finished, and this is the screen where you read and change it. "
            f"**{book.display_title}**: {len(book.chapters)} chapters, "
            f"{book.words:,} words. The download buttons above take it away.",
            icon="🤖",
        )

    # Four slots, because where a panel is *drawn* and where it is *run* have to
    # be settled separately, and this is the one place that knows both.
    #
    # Drawn, top to bottom: the three downloads, then New book / the example,
    # then the writing and the design, then the drafts store.
    #
    # Run, in this order: `_start_panel` and `_draft_panel` first, because both
    # can adopt a different book — and `adopt` deletes every `bk-` key, which
    # Streamlit will not have done after those widgets exist. Then the boxes.
    # Then the downloads last, so the name box and the paper they describe are
    # read after every keystroke of this run has reached the manuscript.
    #
    # Creating a container reserves its place on the page; filling it later puts
    # the content there rather than where the code is. Reordering these four
    # lines is therefore a layout change, and moving `_draft_panel` out of the
    # first two `with` blocks is a bug of the kind that only shows up on the run
    # somebody opens a draft.
    away_slot = st.container()
    start_slot = st.container()
    writing_slot = st.container()
    drafts_slot = st.container()

    with start_slot:
        _start_panel(editor, book)

    with drafts_slot:
        status_slot, autosave, save_name = _draft_panel(editor, book, folder)

    with writing_slot:
        st.markdown("#### Step 1 — Write it")
        writing, design = st.columns([2, 1], gap="large")

        with writing:
            _title_panel(editor, book)

            numbers = numbered_sections(book)
            for part, heading, keys, blurb in PARTS:
                _sections_panel(editor, book, part, heading, keys, blurb, numbers)
            _formatting_note()

        with design:
            page_size_in, sheet_size_pt = _design_panel(editor, book)

    with away_slot:
        _take_away_panel(editor, book, page_size_in, sheet_size_pt)

    _finish_status(status_slot, editor, book, folder, autosave, save_name)


def _finish_status(slot, editor, book, folder, autosave, save_name):
    """The one line saying whether this draft is safe, and the autosave itself.

    Both belong at the end of the run. The status has to describe the
    manuscript *after* every text box has written into it, and an autosave that
    ran any earlier would write the previous run's words.
    """
    if is_unsaved():
        with slot.container():
            st.caption(
                "⚠️ Not saved yet. **💾 Save** keeps this book in the drafts "
                "list, and autosave takes over from there."
            )
        return

    saved_name = st.session_state.get(DRAFT_NAME) or save_name
    if is_dirty(book) and autosave and not editor.busy and not editor.full:
        try:
            path = save_draft(folder, book, saved_name)
        except Exception as error:
            with slot.container():
                st.caption(f"⚠️ Autosave failed: {error}")
            return
        mark_saved(book, path, draft_name_of(path))

    with slot.container():
        if is_dirty(book):
            st.caption(
                f"● Unsaved changes to **{saved_name}**. Autosave is off, so "
                f"click **💾 Save**."
            )
        else:
            st.caption(f"✓ Saved as **{saved_name}** · {book.words:,} words")
