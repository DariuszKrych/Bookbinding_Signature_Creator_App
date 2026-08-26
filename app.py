"""Streamlit GUI for the Bookbinding Signature Creator.

Run with:  streamlit run app.py
"""

import time
from datetime import datetime
from pathlib import Path

import streamlit as st

from main import (
    DEFAULT_SHEETS_PER_SIGNATURE,
    INPUT_DIR,
    INSTRUCTIONS_NAME,
    MANUSCRIPT_DIR,
    NUMBERED_SUFFIX,
    OUTPUT_DIR,
    PREVIOUS_DIR,
    book_pdf_path,
    convert_book,
    delete_book,
    delete_ready_book,
    list_available_books,
    list_previous_books,
    list_ready_books,
    move_book,
    number_book,
    numbered_copy_path,
    printing_steps,
    typeset_book,
)
from Script import ai_book, book_editor, workspace
from Script.paper_sizes import (
    BOOK_PAGE_FAMILIES,
    BOOK_PAGE_SIZES,
    CENTIMETRES,
    INCHES,
    MILLIMETRES,
    SHEET_SIZES,
    book_page_table,
    describe_size,
    find_paper_size,
    paper_size_table,
)
from Script.print_formatting import (
    ACTUAL_SIZE,
    FIT,
    PT_PER_INCH,
    SCALE_MODE_LABELS,
    ColumnLayout,
    SheetFit,
    inspect_book,
    layout_problems,
    layout_warnings,
    plan_signatures,
    sheet_problems,
    sheet_warnings,
)

# What the app is, in one paragraph. Said once here because it is wanted in two
# places — the caption under the title, which is what a visitor reads, and the
# About entry below, which nobody ever reads. Those were two hand-kept copies,
# and they had already drifted apart in three places.
TAGLINE = (
    "Turns a 2-column PDF book, or a book typed straight into the app, "
    "into printable signature files. Works on any paper size you can feed a "
    "printer. Print one signature double-sided, fold every sheet in half "
    "and nest them one inside another. Then sew the signatures together and "
    "you have a book."
)

st.set_page_config(
    page_title="Bookbinding Signature Creator",
    page_icon="📖",
    layout="wide",
    # This About entry is the price of the theme control in the sidebar, and it
    # buys nothing else.
    #
    # In minimal mode Streamlit builds the top-right toolbar only for an app
    # that has defined a menu item of its own — with no `menu_items` there is no
    # toolbar, no ⋮ menu, and so no System / Light / Dark switcher anywhere in
    # the page for the sidebar control to reach. The switcher is the only way
    # into the theme that does not throw the session away, so the menu has to be
    # built. The style block below then hides the whole corner, so this entry,
    # the About dialog it opens and the "Made with Streamlit v…" line under it
    # are all unreachable: built, never shown.
    #
    # ► NOTHING WRITTEN HERE APPEARS ANYWHERE. Editing this text changes nothing
    # ► on the page. The paragraph a visitor actually reads is the `st.caption`
    # ► under `st.title`, further down — both now come from `TAGLINE` above, so
    # ► edit that.
    menu_items={
        "About": f"### 📖 Bookbinding Signature Creator\n\n{TAGLINE}",
    },
)

# The two pieces of Streamlit's own interface that no config option reaches.
#
# `stStatusWidget` is the running figure and the **Stop** button that appear in
# the header a moment into every rerun. Stop aborts the script mid-write —
# exactly the interruption the drawing order at the foot of this file exists to
# make impossible — and every job already shows a progress bar in its own
# button's place, so the widget offers nothing and risks a book. `display: none`
# takes it out of the tab order too, so it cannot be reached by keyboard either.
#
# `stDialog` here is one specific dialog: the "Is Streamlit still running? …
# streamlit run yourscript.py" panel, raised a few seconds after the server goes
# away. Telling someone who started the app from a desktop shortcut to retype a
# shell command is worse than saying nothing. The rule is scoped to the
# disconnected state, so every dialog Streamlit can raise while the app is up,
# About included, still works. Streamlit closes this one itself on reconnect, so
# a brief drop cannot leave it queued to appear the moment the selector stops
# matching.
#
# `st.html` with nothing but a style block is Streamlit's supported way in: it
# goes to the event container rather than into the page, so it occupies no space
# and — see the note on the flash slot below — moves nothing beneath it.
st.html(
    """
    <style>
    [data-testid="stStatusWidget"] { display: none !important; }

    body:not(:has([data-test-connection-state="CONNECTED"])) [data-testid="stDialog"] {
        display: none !important;
    }
    </style>
    """
)

# The top-right corner, taken off the page without taking it out of the page.
#
# `stMainMenu` is the ⋮ button and `stMainMenuPopover` the panel it drops —
# drawn through a portal, so it needs a rule of its own rather than inheriting
# the first. Hidden, not removed: `set_page_config` above explains why the menu
# has to go on being built, and `APPEARANCE_SCRIPT` at the foot of this file is
# what reaches into it. `display: none` takes both out of the tab order too, so
# neither can be reached by keyboard.
#
# The rest of this undoes what building that menu costs. The header is an
# absolutely positioned strip across the top of the page, 3.75rem tall, that
# pushes nothing down — and the moment it has something to hold it stops being
# transparent and starts taking clicks. Over a page whose content now starts at
# 2rem that would be a band of background colour across the title and an
# invisible catcher over the top of the right-hand column. Transparent and
# click-through it is exactly what it was when it held nothing, with the one
# control it still owns — the ⟩⟩ that appears there when the sidebar is folded
# away — put back on its feet by hand.
#
# The gap itself: Streamlit pads the main column by 6rem to clear that strip,
# which is 6rem of nothing for an app whose corner is empty.
#
# The sidebar counts its own top space separately — a 3.75rem strip and a 1rem
# margin under it — so it needs its own rule to start where the page does. The
# strip is not removed: it holds the ⟨⟨ that folds the sidebar away, and 2rem
# is the height of the logo slot beside it, so nothing in there is squeezed.
st.html(
    """
    <style>
    [data-testid="stMainMenu"],
    [data-testid="stMainMenuPopover"] { display: none !important; }

    [data-testid="stHeader"],
    [data-testid="stToolbar"] {
        background: transparent !important;
        pointer-events: none !important;
    }
    [data-testid="stExpandSidebarButton"] { pointer-events: auto !important; }

    [data-testid="stMainBlockContainer"] { padding-top: 2rem !important; }

    [data-testid="stSidebarHeader"] {
        height: 3rem !important;
        margin-bottom: 0 !important;
    }
    </style>
    """
)

# The one upload limit Streamlit will print, corrected on both uploaders, because
# under each of them it answers a question nobody asked.
#
# `server.maxUploadSize` is a single number for the whole app: a ceiling on the
# size of one uploaded *file*, set for the largest file the app must accept,
# which is a data zip carrying a whole session. Under each dropzone Streamlit
# prints it as though it were that uploader's rule, and under each it is wrong in
# a different way:
#
# * Under the book uploader it is simply not the limit. A book may be a fifth of
#   it — see `MAX_UPLOAD_BYTES` — so the printed line contradicts the refusal a
#   200 MB book would actually get.
# * Under the zip uploader it is a true statement about the wrong quantity. What
#   decides whether a zip can be loaded is not how big the file is, it is what it
#   unpacks to, measured against the session's own limit — `unpack` refuses on
#   that and on nothing else. The two are not the same number and cannot be made
#   the same number: a session is stored unpacked, and its bulk is PDF, which is
#   already compressed, so a zip of a full session comes back at very nearly the
#   size it went in at. `maxUploadSize` sits deliberately above the session limit
#   so that such a zip is never refused by the browser (see the note in
#   `.streamlit/config.toml`), which is exactly what makes it the wrong figure to
#   print here: it would promise room the session does not have.
#
# So each line is replaced with the rule that uploader actually enforces, and
# each is the *only* place that rule is written. How full the session is remains
# a question about the session, answered once, by the bar below.
#
# The line is not in any Python file and no config option reaches it: the
# frontend builds it from `maxUploadSize` (`static/js/FileUploader.*.js`,
# `` `${C(t,T.Byte,0)} per file` `` followed by `` ` • ${types}` ``). It is
# reached through the `st-key-…` class `st.container(key=…)` puts on a block, so
# both uploaders sit in one.
#
# The text sits in a *span* inside the instructions element — not a `small`,
# which is what the equivalent element is in some other Streamlit versions. If
# this ever stops matching after an upgrade, that markup is the first thing to
# check; the selector is deliberately loose (`… span`) because the instructions
# element holds nothing else.
#
# ► The replacement has to FLOW, not float. That span carries Streamlit's own
# ► `overflow: hidden; text-overflow: ellipsis; white-space: nowrap`
# ► (`FileUploader.*.js`, styled `span`, target `e3v525e4`), and it is the
# ► element the text lives in. An earlier version of this block hid the original
# ► with `visibility: hidden` and painted the replacement over it with
# ► `position: absolute` — which made the span the pseudo-element's containing
# ► block, so that `overflow: hidden` clipped the replacement to the span's own
# ► width. In the main column nothing showed, because the line put there is the
# ► same length as the line it replaces. In the sidebar, where the dropzone's
# ► text column is about nineteen characters wide, a longer line was silently
# ► cut off mid-sentence with no ellipsis to admit it.
#
# So: the original text node is collapsed with `font-size: 0` rather than hidden,
# and the replacement is ordinary inline content of the same span. It inherits
# the faded colour, it wraps instead of being cut, and the dropzone grows to hold
# it. Collapsing the font is what costs the pseudo-element its size — it would
# inherit the nought — so `fontSizes.sm` is restated on it, which is the one
# number here copied out of Streamlit's theme rather than derived from this app.
st.html(
    f"""
    <style>
    .st-key-pdf-uploader [data-testid="stFileUploaderDropzoneInstructions"] span,
    .st-key-zip-uploader [data-testid="stFileUploaderDropzoneInstructions"] span {{
        font-size: 0 !important;
        overflow: visible !important;
        text-overflow: clip !important;
        white-space: normal !important;
    }}
    .st-key-pdf-uploader [data-testid="stFileUploaderDropzoneInstructions"] span::after,
    .st-key-zip-uploader [data-testid="stFileUploaderDropzoneInstructions"] span::after {{
        font-size: 0.875rem;
    }}
    .st-key-pdf-uploader [data-testid="stFileUploaderDropzoneInstructions"] span::after {{
        content: "{workspace.MAX_UPLOAD_BYTES // (1024 * 1024)}MB per file • PDF";
    }}
    .st-key-zip-uploader [data-testid="stFileUploaderDropzoneInstructions"] span::after {{
        content: "Must fit the {workspace.human(workspace.LIMIT_BYTES)} after decompression";
    }}
    </style>
    """
)

# This session's scratch space, settled before anything reads it.
#
# Nothing here is storage. `open_session` makes a folder named after this
# session under the system temp folder, points `main`'s four names into it, and
# starts the sweeper that deletes it seconds after the browser goes away. A
# visitor's books are never anywhere near the app's own folder, never visible to
# another visitor, and never left behind — see `Script/workspace.py` for the
# three guarantees that make that true.
#
# The names bound here are this run's copy, and are what the panels below draw.
# See the job runner at the foot of this file, which says them again before any
# file is written.
WORKSPACE = workspace.open_session(st.session_state)
INPUT_DIR = WORKSPACE["Input"]
OUTPUT_DIR = WORKSPACE["Output"]
PREVIOUS_DIR = WORKSPACE["Previously_Converted"]
MANUSCRIPT_DIR = WORKSPACE["Manuscripts"]

# What this session is holding, measured once here so every panel agrees about
# it, the same way `job` is read once below. `full` closes every control that
# would write: an app that lets you start a conversion it has already decided it
# will not let you finish is worse than one that says so on the button.
used = workspace.usage(WORKSPACE)
room = max(0, workspace.LIMIT_BYTES - used)
full = room < workspace.ROOM_TO_WORK

# The two halves of the app. One is picked at the top of the page and the other
# is not drawn at all — not hidden, not disabled, simply not there — so a
# half-finished manuscript cannot be reached by a stray click while a
# conversion is running, and the conversion panels are not rebuilt on every
# keystroke of a chapter.
CONVERT_VIEW = "convert"
WRITE_VIEW = "write"
VIEW_LABELS = {
    CONVERT_VIEW: "📚  Convert 2 Column Formatted PDF into PDF Signatures",
    WRITE_VIEW: "✍️  Convert Inputted Text into PDF Signatures",
}

UNITS = {"Inches (in)": INCHES, "Centimetres (cm)": CENTIMETRES, "Millimetres (mm)": MILLIMETRES}

# The three appearance modes, spelled exactly as Streamlit spells them: these
# are the labels on its own switcher, and `APPEARANCE_SCRIPT` finds the control
# to click by building a selector out of the chosen one.
APPEARANCE_MODES = ("System", "Light", "Dark")

# The "do not resize anything" entry in the conversion tab's sheet menu.
SAME_AS_INPUT = "Same as the input PDF"
CUSTOM_SIZE = "Custom size…"

# Every length the conversion tab collects, stored in inches. Keeping one
# dictionary means the unit switch and the "fit to this PDF" buttons have
# exactly one place to write to, and the widgets stay a display layer over it.
DEFAULT_LENGTHS_IN = {
    "margin": ColumnLayout.page_margin_in,
    "gap": ColumnLayout.column_gap_in,
    "width": ColumnLayout.column_width_in,
    "sheet_w": 11.0,   # Letter, landscape - a size every printer has
    "sheet_h": 8.5,
}


def human_size(path):
    kb = path.stat().st_size / 1024
    return f"{kb / 1024:.1f} MB" if kb >= 1024 else f"{kb:.0f} KB"


def finish(message=None, error=None):
    """Report an action and refresh every panel it could have changed.

    Failures go through here too, rather than being printed where they happened.
    A message written mid-page survives only until the next rerun, and the run
    that hits an error is the run that has just drawn every button disabled for a
    job that is no longer running — so leaving the page as it stands strands the
    whole app until the user finds something else to click.
    """
    if message is not None:
        st.session_state.flash = message
    if error is not None:
        st.session_state.flash_error = error
    st.rerun()


def busy_job():
    """The long-running job in flight, if any. See `claim_job`."""
    return st.session_state.get("busy_job")


def claim_job(job):
    """Take the click now, do the work on the next run.

    Streamlit keeps the page live while a script is running, so a slow job leaves
    its own button clickable — and a second click starts a second conversion over
    the top of the first, writing the same signature files from two threads.

    Recording the job and rerunning immediately closes that window: the very next
    run paints every button disabled *before* any work starts, so there is no
    moment when a second click can be registered.
    """
    st.session_state.busy_job = job
    st.rerun()


def release_job():
    st.session_state.busy_job = None


def forget_widget(field):
    """Drop the per-unit widget state for one length.

    Streamlit ignores a new `value=` once a keyed widget has state of its own, so
    setting a length from code means deleting the widget's memory of it first.
    """
    for known in UNITS.values():
        st.session_state.pop(f"{field}-{known.name}", None)


def set_length(field, inches):
    st.session_state.lengths_in[field] = inches
    forget_widget(field)


def sticky(key, default):
    """Seed a widget that lives on one tab only, before it is drawn.

    Streamlit throws away the state of every widget a run did not draw, so a
    setting that sits on one tab would be back at its default the first time the
    user came back to it — quietly printing the next book on different paper
    than the one they chose. `remember` keeps a copy under a second key that no
    widget owns, and so nothing discards; this puts it back.

    Only for widgets drawn without `value=` or `index=`: Streamlit complains on
    the page when a widget is given both a starting value and a session one.
    """
    if key not in st.session_state:
        st.session_state[key] = st.session_state.get(f"kept-{key}", default)


def remember(key):
    """Keep one tab-local widget's value for the runs its tab is not on screen."""
    st.session_state[f"kept-{key}"] = st.session_state[key]


def arm_delete(token, help_text, container=st, disabled=False, label="🗑 Delete"):
    """The 🗑 button. It only *arms* the delete; `confirm_delete` does the work.

    Everything else in this app can be undone by clicking the opposite button, so
    deleting is the one place worth spending a second click on. An archived PDF in
    particular is often the only copy left, the conversion having moved it out of
    Input/.

    One key holds the armed request, so arming a second delete disarms the first
    and there is never more than one confirmation on screen.
    """
    if container.button(
        label, key=f"arm-delete-{token}", use_container_width=True,
        disabled=disabled, help=help_text,
    ):
        st.session_state.armed_delete = token
        st.rerun()


def confirm_delete(token, do_delete, disabled=False):
    """The confirmation for an armed delete, drawn under the card it belongs to.

    Two buttons and nothing else. The banner that used to sit above them
    described what would survive the delete — and got it wrong as soon as the
    user had deleted the other half themselves, telling them their input PDF was
    safe when they had already removed it. The card directly above says which
    file this is, which is the only thing the second click needs to know.

    Both buttons are disabled together while a job runs. "Keep it" changes
    nothing, but it is still a widget: clicking one mid-conversion asks Streamlit
    for a rerun, and Streamlit grants it by stopping the script where it stands.
    """
    if st.session_state.get("armed_delete") != token:
        return
    yes, no = st.columns(2)
    if yes.button(
        "Yes, delete", key=f"yes-delete-{token}", type="primary",
        use_container_width=True, disabled=disabled,
    ):
        # Disarmed first: whether the delete succeeds or raises, the confirmation
        # has been answered and must not come back on the next run.
        st.session_state.armed_delete = None
        try:
            message = do_delete()
        except Exception as error:
            finish(error=f"Could not delete it: {error}")
        else:
            finish(message)
    if no.button(
        "Keep it", key=f"no-delete-{token}", use_container_width=True,
        disabled=disabled,
    ):
        st.session_state.armed_delete = None
        st.rerun()


def named(names):
    """“Book.pdf”, or “Book.pdf” and 2 others — a refusal a person can act on.

    Listing every name of a large batch would push the rest of the message off
    the banner, and naming none of them leaves somebody counting files to work
    out which one it meant. Quoted here rather than by the caller, so a message
    reads the same whether it names one file or twenty.
    """
    if len(names) == 1:
        return f"“{names[0]}”"
    return (f"“{names[0]}” and {len(names) - 1} "
            f"other{'' if len(names) == 2 else 's'}")


def signature_count(written):
    """"3 signatures", or "1 signature" — a book can be one gathering."""
    return f"{len(written)} signature" + ("" if len(written) == 1 else "s")


def markdown_table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
st.title("📖 Bookbinding Signature Creator")
# This is the paragraph on the page. `TAGLINE` is defined at the top of the file
# and the unreachable About entry reads the same one.
st.caption(TAGLINE)

# Nothing on the page may move while a job runs. Streamlit restarts the script
# the instant any widget changes, so a click made during a conversion tears down
# the half-drawn page and redraws it from the top — that is the duplicated header
# and the shifted columns. It is also a correctness problem: the restarted run
# abandons `convert_book` wherever it had got to, and would hand the next one a
# sheet size the book on screen was never measured against. Locking every
# control closes both.
#
# `job` is read once, here, so that every panel below agrees about it. See the
# job runner at the foot of this file for the other half of the guarantee: the
# work itself does not start until the page has been drawn locked.
job = busy_job()
busy = job is not None
pending_job = None
# The AI job is kept apart from `pending_job` until below the two views, and
# that is not tidiness. `pending_job = book_editor.render(...)` further down is
# an unconditional assignment: anything claimed up here would be overwritten and
# silently never run. See where the two are joined again.
ai_job = None

# --------------------------------------------------------------------------
# Writing a book with a model
# --------------------------------------------------------------------------
# Above the view radio for two reasons. It is offered whichever half of the app
# somebody is looking at, and — the load-bearing one — `st.session_state["view"]`
# may only be assigned before the radio below has been instantiated. Pressing the
# button has to move the user to the writing view, so the button has to be here.
#
# A book that has already been written into the editor is kept as a draft before
# anything replaces it. That happens in the job runner at the foot of this file,
# not on the click: a click claims, and only the runner does work.
AI_PROMPT_KEY = "ai-prompt"
ai_ready = ai_book.available()
ai_writing = job == ("ai", "write")
# Said on the button, because it is fixed and a reader should not have to find
# out by counting. It comes from the same setting the request is built from, so
# the promise and the schema cannot drift apart.
ai_chapters = ai_book.chapter_count()

with st.container(border=True):
    ai_button_column, ai_prompt_column = st.columns(
        [1, 2.5], vertical_alignment="center"
    )
    ai_slot = ai_button_column.empty()
    ai_prompt = ai_prompt_column.text_input(
        "Describe the book you want",
        key=AI_PROMPT_KEY,
        max_chars=ai_book.MAX_PROMPT_CHARS,
        placeholder=(
            f"Describe the book to write — what it is about, who it is for and "
            f"the voice. It is always {ai_chapters} chapters, so say what should "
            f"happen in it rather than how long it should be."
        ),
        disabled=busy or not ai_ready,
        label_visibility="collapsed",
    )
    # The slot holds the button, and the runner replaces it with a progress bar,
    # so starting a book does not move anything else on the page.
    if ai_slot.button(
        "✍️ Writing…"
        if ai_writing
        else f"🤖 Generate {ai_chapters} chapter book for printing with AI",
        key="ai-write",
        type="primary",
        use_container_width=True,
        disabled=busy or full or not ai_ready or not ai_prompt.strip(),
        help=(
            f"Writes a {ai_chapters}-chapter book from that description and puts "
            "it straight into the writing view, where you can edit it like "
            "anything you typed. Anything already in the editor is kept as a "
            "draft first. Your description — and nothing else from this session "
            "— is sent to OpenRouter; see the data policy at the foot of the page."
        ),
    ):
        # The one moment this assignment is legal: the radio below has not been
        # instantiated yet on this run, so this is the value it will take when it
        # is. `claim_job` reruns immediately and the next run draws the writing
        # view, locked, with the progress bar in the slot above.
        st.session_state["view"] = WRITE_VIEW
        claim_job(("ai", "write"))
    if ai_writing:
        # Where the book appears while it is being written. Drawn only while the
        # job is running, and inside the same bordered box as the button it
        # belongs to, so nothing below it moves when the words start arriving.
        #
        # It is created here rather than in the runner at the foot of the file for
        # the reason everything else on this page is: elements land where the
        # script produces them, and the runner produces its elements last of all.
        ai_stream = st.empty()
        ai_job = {
            "kind": "ai_write",
            "slot": ai_slot,
            "stream": ai_stream,
            "prompt": st.session_state.get(AI_PROMPT_KEY, ""),
        }
    if not ai_ready:
        st.caption(
            f"Writing with AI is switched off on this copy. "
            f"{ai_book.why_unavailable()} Everything else works as usual."
        )
    else:
        st.caption(
            "Writes the words only — your page size, type and margins are left "
            "exactly as you set them. The description you type is sent to "
            "OpenRouter to be written; nothing else from this session is."
        )

# A book handed over by a finished AI job, put on screen before any box is drawn.
# It cannot be done in the runner: by the time that runs, every box belonging to
# the book being replaced has already been drawn.
book_editor.collect()

# Which half of the app is on screen. A radio rather than tabs or a segmented
# control: it cannot be deselected into showing nothing, it keeps its choice in
# session state across every rerun a text box in the editor causes, and it locks
# like every other control while a job runs — so the view cannot change out from
# under a book that is being written to disk.
view = st.radio(
    "What would you like to do?",
    list(VIEW_LABELS),
    format_func=VIEW_LABELS.get,
    horizontal=True,
    key="view",
    disabled=busy,
    label_visibility="collapsed",
)
writing = view == WRITE_VIEW

with st.sidebar:
    # --------------------------------------------------------------------------
    # Your data — the whole workspace, in and out, as one zip
    # --------------------------------------------------------------------------
    # Above the settings because it is the first thing a visitor does and the
    # last thing they do, and because it is the only way in or out: nothing here
    # is stored, so the zip is the whole of what a person keeps.
    st.header("Your data")

    # A fresh key after each load, for the reason the PDF uploader has one: a
    # `file_uploader` hands back the same file on every rerun, so without this
    # the zip would be loaded again the moment anything else on the page moved.
    load_round = st.session_state.setdefault("workspace_round", 0)
    # Keyed on the container rather than on the uploader, for the same reason the
    # book uploader is: the style block at the top of this file reaches inside to
    # correct the limit line the dropzone prints, and the uploader's own key
    # changes on every load round, so a selector built from it would stop
    # matching the moment a zip was loaded.
    #
    # The help says nothing about size. The rule is on the dropzone line, once,
    # where the wrong one used to be — and it is a rule about what the zip holds
    # rather than about the file, so a figure repeated here would be read as a
    # second, different limit on the upload itself.
    with st.container(key="zip-uploader"):
        incoming = st.file_uploader(
            "📥 Load my data (.zip)",
            type="zip",
            key=f"workspace-zip-{load_round}",
            disabled=busy,
            help="A zip saved by the button below. Puts back every book, draft, "
                 "archived PDF and finished signature it holds.",
        )

    # A second click before anything is deleted. A `file_uploader` fires the
    # moment a file is *picked* — no button involved — and this load replaces
    # the workspace rather than adding to it, so with no confirmation the wrong
    # file in the dialog would take the books with it. Same rule as `arm_delete`
    # below, drawn inline because there is only ever one of these on screen.
    if incoming is not None:
        st.caption(
            "⚠️ Replaces everything now in the app: every input PDF, "
            "archived book, finished signature and draft."
        )
        if st.button(
            "Replace everything with this zip",
            key="workspace-load-go",
            type="primary",
            use_container_width=True,
            disabled=busy,
        ):
            try:
                loaded = workspace.unpack(incoming.getvalue(), WORKSPACE)
            except Exception as error:
                finish(error=f"Could not load that zip: {error}")
            else:
                st.session_state.workspace_round = load_round + 1
                finish(f"Loaded {loaded} file(s) from the zip.")

    # `data` is the function, not its result: Streamlit calls it when the button
    # is actually clicked, so the workspace is not zipped up again on every
    # rerun of the page just to have a file sitting ready.
    st.download_button(
        "📤 Save my data (.zip)",
        data=lambda: workspace.pack(WORKSPACE),
        file_name=f"bookbinding-data-{datetime.now():%Y-%m-%d}.zip",
        mime="application/zip",
        key="workspace-save",
        type="primary",
        use_container_width=True,
        disabled=busy,
        help="Everything in the app — input PDFs, the archive, finished "
             "signatures and book drafts — in one zip on your own machine. "
             "Load it back with the box above.",
    )

    # How full the session is, on screen rather than only in an error message.
    # A limit somebody only meets by being refused is a limit that reads as a
    # fault; a bar that has been filling up all along reads as a rule.
    st.progress(
        min(1.0, used / workspace.LIMIT_BYTES),
        text=f"{workspace.human(used)} of {workspace.human(workspace.LIMIT_BYTES)} used",
    )
    if full:
        st.warning(
            "This session is full. Save your data, then delete a book or two — "
            "or start again with 🗑 below.",
            icon="🚫",
        )

    # The retention rule, in one line, where the person it applies to is
    # standing. Not buried in an About dialog nobody opens.
    st.caption(
        "**Nothing is stored.** Your files are held only while this tab is "
        "open and are erased when it closes. Save your zip first."
    )

    # The same rule, on demand. Someone who has just converted something they
    # would rather not leave sitting anywhere should not have to close the tab
    # and take it on trust.
    arm_delete(
        "workspace",
        "Erases every file in this session right now: input PDFs, the archive, "
        "finished signatures and drafts.",
        disabled=busy,
        label="🗑 Delete my data now",
    )

    def erase_everything():
        workspace.discard(st.session_state)
        return "Erased. Nothing of yours is left on the server."

    confirm_delete("workspace", erase_everything, disabled=busy)

    st.divider()

    # Settings holds what means the same thing on both tabs, and only that.
    # Everything about the paper is decided on the tab it belongs to — the sheet
    # an existing PDF is printed on is a different question from the size a book
    # being typed is set at, and asking both here is what left the writing tab
    # with two menus for one measurement, one in the sidebar and one on the page.
    st.header("Settings")

    # First under the header, and not one of the four the caption below counts:
    # this one says nothing about the book, only about the light the app is read
    # in. It is locked with everything else while a job runs, and for the same
    # reason — a widget that changes asks Streamlit for a rerun, and Streamlit
    # grants it by stopping the running script dead, half way through writing a
    # book's signatures.

    st.divider()

    appearance = st.radio(
        "Theme",
        APPEARANCE_MODES,
        horizontal=True,
        key="setting-theme",
        disabled=busy,
    )

    st.divider()

    if busy:
        st.warning(
            "Working on a book. The settings below are locked until it finishes, "
            "so the file being written matches what you see.",
            icon="⏳",
        )

    unit = UNITS[
        st.radio(
            "Units",
            list(UNITS),
            horizontal=True,
            key="setting-units",
            disabled=busy,
            help="Applies to every measurement in the app, on both tabs. "
                 "Switching converts the current values rather than "
                 "reinterpreting them, so no book changes size.",
        )
    ]

    # Measurements are stored in inches and only converted for display, so a
    # unit switch cannot drift the layout. The widgets are keyed per unit, so
    # the stale keys have to go or the old unit's number would come back.
    lengths_in = st.session_state.setdefault("lengths_in", dict(DEFAULT_LENGTHS_IN))
    if st.session_state.get("unit_name") != unit.name:
        for field in DEFAULT_LENGTHS_IN:
            forget_widget(field)
        st.session_state.unit_name = unit.name

    st.divider()

    sheets_per_signature = st.number_input(
        "Sheets of paper per signature",
        min_value=1,
        max_value=25,
        value=DEFAULT_SHEETS_PER_SIGNATURE,
        step=1,
        key="setting-sheets",
        disabled=busy,
        help="Physical sheets in one folded gathering. Each sheet is printed on "
             "both sides and folded once, so it carries 4 book pages.",
    )
    st.caption(
        f"= {sheets_per_signature * 2} printed sides, "
        f"**{sheets_per_signature * 4} book pages** per signature"
    )

    st.divider()

    duplex = st.radio(
        "Printer duplex setting",
        ["Flip on long edge", "Flip on short edge"],
        key="setting-duplex",
        disabled=busy,
        help="Must match your printer. The wrong one prints every other side "
             "upside down. If a test signature comes out that way, switch this.",
    )
    flip_on_long_edge = duplex == "Flip on long edge"


# Drawn wherever it is called from, which is now the conversion tab rather than
# the sidebar: `unit`, `lengths_in` and `busy` are the same on both tabs, so the
# widget only has to know which container the caller has open.
def length_input(label, field, max_in, help_text, min_in=0.0):
    value = st.number_input(
        f"{label} ({unit.name})",
        min_value=round(unit.from_inches(min_in), unit.places),
        max_value=round(unit.from_inches(max_in), unit.places),
        value=round(unit.from_inches(lengths_in[field]), unit.places),
        step=unit.step,
        format=f"%.{unit.places}f",
        key=f"{field}-{unit.name}",
        disabled=busy,
        help=help_text,
    )
    lengths_in[field] = unit.to_inches(value)
    return lengths_in[field]


def layout_for(info):
    """The column layout for one book, fitted to its own page unless overridden."""
    if column_width_in is None:
        return ColumnLayout.fitted(
            info.page_width_pt / PT_PER_INCH, page_margin_in, column_gap_in
        )
    return ColumnLayout(page_margin_in, column_gap_in, column_width_in)


def book_notes(info):
    """Worth knowing about a book — none of it stops the book converting.

    Each of these used to be a yellow warning telling the user to go and check
    something. They are all cases the imposition already handles exactly, so the
    only honest thing to say is what it does with them.
    """
    notes = []
    if not info.uniform_page_size:
        notes.append(
            "The pages of this PDF are **not all the same size**. Each one is "
            "split down its own middle and fitted to the sheet separately, so the "
            "printed paper stays uniform: book pages from a smaller source page "
            "simply carry a little more blank margin."
        )
    if info.rotated_pages:
        notes.append(
            "Some pages carry a **rotation flag**. They are imposed the way a PDF "
            "reader displays them, which is what you want."
        )
    if info.cropped_pages:
        notes.append(
            "Some pages have a **crop box** smaller than the paper. The fold and "
            "the measurements above follow the cropped page, i.e. exactly what a "
            "PDF reader shows you."
        )
    return notes


# One slot, created on every run whether or not there is a message to put in it.
#
# Streamlit identifies an element by its *position* among its siblings, and it
# only discards the previous run's surplus elements once a run finishes. A banner
# drawn straight into the page therefore shifts everything below it by one place
# for exactly one run: the run that shows it has one more element than the run
# after, so the older, longer page's last element — the row of reference
# expanders at the foot of this file — is left on screen, greyed out as stale,
# beside the new copy one place higher up.
#
# Normally that lasts a few milliseconds. But the run that *starts* a conversion
# is the run that has just dropped the banner, and it then holds the page for as
# long as the book takes to impose, so the duplicate sits there for the whole
# job. Keeping the slot whether or not it is used keeps the element count fixed,
# so nothing below it ever moves. An unfilled `st.empty()` renders nothing and
# occupies no space.
flash_slot = st.empty()
flash = st.session_state.pop("flash", None)
flash_error = st.session_state.pop("flash_error", None)
if flash or flash_error:
    # A container, not the slot itself: `finish` takes a message and an error,
    # and `st.empty` holds only one element.
    with flash_slot.container():
        if flash:
            st.success(flash)
        if flash_error:
            st.error(flash_error)

# --------------------------------------------------------------------------
# Convert 2 column formatted PDF into PDF signatures
# --------------------------------------------------------------------------
if view == CONVERT_VIEW:
    # ----------------------------------------------------------------------
    # Paper to print on — this tab's settings, on this tab
    # ----------------------------------------------------------------------
    # Everything in here is about a PDF somebody else made: which paper to put
    # it on, what to do when it is not that size, and where its two columns sit.
    # None of it means anything while a book is being typed, which is why it now
    # sits above the three panels it describes instead of in the sidebar.
    with st.container(border=True):
        st.markdown("#### 🖨️ Paper to print on")
        size_column, choice_column = st.columns(2)

        def sheet_label(name):
            if name in (SAME_AS_INPUT, CUSTOM_SIZE):
                return name
            return f"{name} · {unit.size_label(*find_paper_size(name).size_in(False))}"

        sticky("setting-sheet-size", SAME_AS_INPUT)
        sheet_choice = size_column.selectbox(
            "Sheet size",
            [SAME_AS_INPUT] + [size.name for size in SHEET_SIZES] + [CUSTOM_SIZE],
            format_func=sheet_label,
            key="setting-sheet-size",
            disabled=busy,
            help=f"The paper you will actually load into the printer. Leave it on "
                 f"“{SAME_AS_INPUT}” to keep each book exactly as it is, which is "
                 f"the right answer whenever the PDF was already made for your "
                 f"paper.",
        )
        remember("setting-sheet-size")

        if sheet_choice == SAME_AS_INPUT:
            sheet_size_pt = None
            scale_mode = FIT
            st.caption(
                "Each book keeps its own page size and nothing is scaled."
            )
        else:
            if sheet_choice == CUSTOM_SIZE:
                with choice_column:
                    sheet_width_in = length_input(
                        "Sheet width", "sheet_w", 48.0,
                        "The full width of the paper, before folding. A book page "
                        "ends up half this wide.",
                        min_in=1.0,
                    )
                    sheet_height_in = length_input(
                        "Sheet height", "sheet_h", 48.0,
                        "The full height of the paper. A book page is this tall.",
                        min_in=1.0,
                    )
            else:
                sticky("setting-orientation", True)
                landscape = choice_column.radio(
                    "Sheet orientation",
                    [True, False],
                    format_func=lambda value: (
                        "Landscape (long edge across)" if value
                        else "Portrait (short edge across)"
                    ),
                    key="setting-orientation",
                    disabled=busy,
                    help="A sheet is folded across its width, so the long edge "
                         "normally goes across. Portrait gives tall, narrow book "
                         "pages.",
                )
                remember("setting-orientation")
                sheet_width_in, sheet_height_in = find_paper_size(
                    sheet_choice
                ).size_in(landscape)

            sheet_size_pt = (
                sheet_width_in * PT_PER_INCH, sheet_height_in * PT_PER_INCH
            )
            sticky("setting-scale-mode", FIT)
            scale_mode = st.radio(
                "If the book is a different size",
                [FIT, ACTUAL_SIZE],
                format_func=lambda mode: SCALE_MODE_LABELS[mode],
                horizontal=True,
                key="setting-scale-mode",
                disabled=busy,
                help="“Fit” scales each book page up or down until it fills the "
                     "sheet, keeping its proportions. “Keep the original size” "
                     "never resizes anything and refuses the job if the book is "
                     "too big for the paper.",
            )
            remember("setting-scale-mode")
            st.caption(
                f"Sheet **{describe_size(sheet_width_in, sheet_height_in, unit)}** "
                f"→ folded book pages of "
                f"**{unit.size_label(sheet_width_in / 2, sheet_height_in)}**"
            )

        # Folded away, because the group reads like a setting that shapes the
        # book and is nothing of the kind: every page is folded down its own
        # middle whatever is in here, and the columns are fitted to each book by
        # default, so most people never need to open it at all.
        with st.expander("Stamped page number positioning"):
            st.caption(
                f"Dictates where **stamped page numbers** are placed."
            )

            sticky("setting-auto-columns", True)
            auto_columns = st.checkbox(
                "Fit the columns to each PDF", key="setting-auto-columns",
                disabled=busy,
                help="Works out the column width from each book's own page size, "
                     "keeping the margin and gap below. Leave this on: a fixed "
                     "column width is only ever right for the one page size it "
                     "was measured on.",
            )
            remember("setting-auto-columns")

            margin_column, gap_column, width_column = st.columns(3)
            with margin_column:
                page_margin_in = length_input(
                    "Outer page margin", "margin", 5.0,
                    "From the paper edge to the outer edge of the first column.",
                )
            with gap_column:
                column_gap_in = length_input(
                    "Gap between the two columns", "gap", 6.0,
                    "The gutter. The fold runs down the middle of it.",
                )
            # None means "work it out per book". Kept out of ColumnLayout
            # entirely rather than defaulted, so nothing downstream can mistake a
            # stale A4 number for a measurement of the book actually in hand.
            column_width_in = None
            if not auto_columns:
                with width_column:
                    column_width_in = length_input(
                        "Column width", "width", 12.0,
                        "Width of each of the two columns. Both are the same "
                        "width. The shipped default fills an A4 landscape page.",
                        min_in=0.1,
                    )

    left, middle, right = st.columns(3, gap="large")

    with left:
        st.header("Available for conversion")

        # The uploader is rebuilt under a fresh key after each save. Without that it
        # keeps handing back the same files on every rerun, which either re-saves a
        # file the user has since moved away, or loops forever on the rerun below.
        upload_round = st.session_state.setdefault("upload_round", 0)
        # Keyed so the style block at the top of this file can reach inside it
        # and correct the dropzone's own limit line: Streamlit has one upload
        # ceiling for the whole app, it has to be the zip's 500 MB, and it would
        # otherwise be printed here as the limit on a book.
        #
        # That corrected line is the *only* place the per-book limit is written.
        # It was in the label, the help and a caption as well, all within an inch
        # of each other and all saying the same thing — which reads as three
        # different rules rather than one. How much of the session is left is a
        # question about the session, and is answered once, in the sidebar.
        with st.container(key="pdf-uploader"):
            uploaded = st.file_uploader(
                "Upload 2-column PDF books",
                type="pdf", accept_multiple_files=True,
                key=f"uploader-{upload_round}", disabled=busy or full,
            )
        if uploaded:
            saved = []
            too_big = []
            no_space = []
            # Counted down as we go rather than checked once: three files that
            # each fit the space that was free before any of them was written
            # would otherwise all be written.
            spare = room
            for item in uploaded:
                # The browser supplies this name, so it is taken apart and only its
                # final component kept: a name carrying separators would otherwise
                # place the write somewhere other than Input/.
                name = Path(item.name).name
                if not name or not name.lower().endswith(".pdf"):
                    continue
                target = INPUT_DIR / name
                if target.exists():
                    continue
                size = item.size if item.size is not None else len(item.getbuffer())
                # Two different refusals, kept apart because they mean different
                # things to the person: one book is too big to work on at all,
                # against one that would fit if something else were deleted.
                if size > workspace.MAX_UPLOAD_BYTES:
                    too_big.append(name)
                    continue
                if size > spare:
                    no_space.append(name)
                    continue
                target.write_bytes(item.getbuffer())
                spare -= size
                saved.append(name)
            st.session_state.upload_round = upload_round + 1
            added = f"Added {len(saved)} PDF(s). " if saved else ""
            if too_big:
                finish(error=(
                    f"{added}{named(too_big)} "
                    f"{'is' if len(too_big) == 1 else 'are'} over the "
                    f"{workspace.human(workspace.MAX_UPLOAD_BYTES)} limit for a "
                    f"single book. Split it, or shrink it in a PDF tool first."
                ))
            if no_space:
                finish(error=(
                    f"{added}No room for {named(no_space)}. A session may hold "
                    f"{workspace.human(workspace.LIMIT_BYTES)} — save your data "
                    f"and delete something to make space."
                ))
            finish(
                f"Added {len(saved)} PDF(s)."
                if saved
                else "Those PDFs are already here."
            )

        available = list_available_books()
        if not available:
            st.info("No PDFs waiting. Upload a 2-column PDF book above.")
        else:
            for pdf_path in available:
                with st.container(border=True):
                    st.markdown(f"**{pdf_path.name}**")
                    try:
                        info = inspect_book(pdf_path)
                    except Exception as error:
                        st.error(f"Could not read this PDF: {error}")
                        continue

                    try:
                        fit = SheetFit.compute(
                            (info.page_width_pt, info.page_height_pt), sheet_size_pt, scale_mode
                        )
                    except ValueError as error:
                        st.error(str(error))
                        continue

                    layout = layout_for(info)

                    # The only thing that can stop a conversion is a sheet the book
                    # physically will not go on. Column measurements never can:
                    # imposition splits each page at its own midpoint and never reads
                    # a layout, so a layout complaint here would be a refusal the user
                    # could neither act on nor see any effect from.
                    problems = sheet_problems(fit, unit)
                    notes = sheet_warnings(fit, layout, unit) + book_notes(info)
                    if column_width_in is not None:
                        # Hand-entered column measurements are the user's own numbers,
                        # so it is worth saying when they do not describe this book —
                        # but only ever about where page numbers would land.
                        notes += layout_problems(layout, info.page_width_pt, unit)
                        notes += layout_warnings(layout, info.page_width_pt, unit)
                    plans = plan_signatures(info.book_pages, sheets_per_signature)

                    a, b, c = st.columns(3)
                    a.metric("Source pages", info.source_pages)
                    b.metric("Book pages", info.book_pages)
                    c.metric("Signatures", len(plans))
                    st.caption(
                        f"{human_size(pdf_path)} · "
                        f"{sum(p.sheets for p in plans)} sheets of paper"
                        + (f" · last signature is {plans[-1].sheets} sheet(s)"
                           if plans[-1].sheets != sheets_per_signature else "")
                    )

                    # The two sizes worth acting on: what to put in the printer, and
                    # what comes out. The input PDF's own page size and the notes on
                    # how a sheet folds were only ever restating what the panel
                    # above and "Choosing a paper size" already explain.
                    st.markdown(
                        f"**Paper to load in the printer:** "
                        f"{describe_size(*fit.sheet_size_in, unit)}\n\n"
                        f"**Each page of the finished book:** "
                        f"{unit.size_label(*fit.book_page_size_in)}"
                    )
                    st.caption(
                        f"Scaled to **{fit.scale * 100:.1f}%**"
                        + ("" if fit.is_resized else ", so nothing is resized")
                        + ". Print at 100% / “Actual size”."
                    )

                    # Folded away, not shouted. None of this blocks anything, and a
                    # panel of yellow banners on a book that converts perfectly reads
                    # as "something is wrong" when nothing is.
                    if notes:
                        with st.expander(f"Notes on this book ({len(notes)})"):
                            for note in notes:
                                st.markdown(f"- {note}")
                    for problem in problems:
                        st.error(problem)

                    # A conversion writes roughly what it reads: imposition
                    # copies each page's own content on to a sheet, so the
                    # signatures for a book come to about the size of the book,
                    # plus the printing notes. A tenth on top covers the sheet
                    # objects imposition adds. This is only an estimate — the
                    # watcher on the job itself is what actually holds the line —
                    # but it is close enough to say "this will not fit" before
                    # the work starts rather than half way through it.
                    needs = int(pdf_path.stat().st_size * 1.1) + 4096
                    no_room = full or needs > room
                    if no_room and not problems:
                        st.warning(
                            f"Not enough room to convert this: it needs about "
                            f"{workspace.human(needs)} and there is "
                            f"{workspace.human(room)} free.",
                            icon="🚫",
                        )

                    # One job at a time, across every book: a conversion moves the
                    # input PDF and rewrites a whole output folder, so two at once
                    # would race over the same files.
                    #
                    # Each action gets an `st.empty()` slot holding either its button
                    # or, once that job is the one running, its progress bar. Swapping
                    # the two inside one slot is what keeps the page still: a progress
                    # bar added *below* a button pushes everything under it down the
                    # moment work starts, and pulls it back up when the bar goes.
                    converting = job == ("convert", pdf_path.name)
                    convert_slot = st.empty()
                    if convert_slot.button(
                        "Creating signatures…" if converting else "Create signatures",
                        key=f"convert-{pdf_path.name}",
                        type="primary",
                        disabled=bool(problems) or busy or no_room,
                        use_container_width=True,
                    ):
                        claim_job(("convert", pdf_path.name))
                    if converting:
                        pending_job = {
                            "kind": "convert", "slot": convert_slot,
                            "path": pdf_path, "layout": layout,
                            # Carried on the job rather than read from the page
                            # by the runner: the paper now belongs to a tab, and
                            # the job is the one thing that knows which tab
                            # started it.
                            "sheet_size_pt": sheet_size_pt,
                            "scale_mode": scale_mode,
                        }

                    # The "fit" button only exists to repair a hand-entered column
                    # width, so it is offered only when there is one to repair.
                    if column_width_in is None:
                        number_button, move_button = st.columns(2)
                    else:
                        fit_button, number_button, move_button = st.columns(3)
                        if fit_button.button(
                            "Fit the columns to this PDF", key=f"fit-{pdf_path.name}",
                            use_container_width=True, disabled=busy,
                            help="Widens or narrows both columns until they exactly fill "
                                 "this book's own page, keeping the margin and gap you set.",
                        ):
                            fitted = ColumnLayout.fitted(
                                info.page_width_pt / PT_PER_INCH,
                                page_margin_in, column_gap_in,
                            )
                            set_length("width", fitted.column_width_in)
                            finish(
                                f"Column width set to "
                                f"{unit.label(fitted.column_width_in)} for a "
                                f"{unit.label(info.page_width_pt / PT_PER_INCH)} wide page."
                            )

                    # A numbered copy is itself a book in this list, and
                    # numbering it again would stamp a second set of numbers
                    # over the first and call the result …_Numbered_Numbered.
                    is_numbered_copy = pdf_path.stem.endswith(NUMBERED_SUFFIX)
                    already_numbered = (
                        is_numbered_copy or numbered_copy_path(pdf_path).exists()
                    )
                    numbering = job == ("number", pdf_path.name)
                    number_slot = number_button.empty()
                    if number_slot.button(
                        "Numbering…" if numbering else "Number the pages",
                        key=f"number-{pdf_path.name}",
                        # Not gated on `problems`: those are about the sheet this book
                        # would be printed on, and numbering only rewrites the book.
                        # It is gated on room, because it writes a second copy of
                        # this whole book beside it.
                        disabled=already_numbered or busy or no_room,
                        use_container_width=True,
                        help="Only needed if this book's pages are not numbered already. "
                             "Stamps a page number centred at the foot of each column "
                             "and saves it as a separate new book, leaving this one "
                             "untouched.",
                    ):
                        claim_job(("number", pdf_path.name))
                    if numbering:
                        pending_job = {
                            "kind": "number", "slot": number_slot,
                            "path": pdf_path,
                            # None means "fit the columns to each page of this
                            # book", which is right even when the pages are not
                            # all the same size — `layout` was measured off the
                            # first page only. A hand-entered width is the one
                            # thing worth carrying through as it stands.
                            "layout": layout if column_width_in is not None else None,
                        }

                    if move_button.button(
                        "Move to archive", key=f"archive-{pdf_path.name}",
                        use_container_width=True, disabled=busy,
                        help="Moves this PDF to the archive without converting it.",
                    ):
                        try:
                            move_book(pdf_path, PREVIOUS_DIR)
                        except Exception as error:
                            st.error(f"Could not move it: {error}")
                        else:
                            finish(f"Moved “{pdf_path.name}” to the archive.")

                    if is_numbered_copy:
                        st.caption(
                            "This is the numbered copy of another book here."
                        )
                    elif already_numbered:
                        st.caption(
                            f"Already numbered: see `{numbered_copy_path(pdf_path).name}`."
                        )

    # --------------------------------------------------------------------------
    # Archive of previously converted
    # --------------------------------------------------------------------------
    with middle:
        st.header("Archive of previously converted")

        previous = list_previous_books()
        if not previous:
            st.info(
                "Nothing archived yet. Converted input PDFs are moved here "
                "automatically, and “Move to archive” puts one here unconverted."
            )
        else:
            for pdf_path in previous:
                with st.container(border=True):
                    st.markdown(f"**{pdf_path.name}**")
                    st.caption(human_size(pdf_path))

                    restore_col, delete_col = st.columns([2, 1])
                    if restore_col.button(
                        "Move back to the list", key=f"restore-{pdf_path.name}",
                        use_container_width=True, disabled=busy,
                        help="Puts this book back in the conversion list, e.g. to redo it "
                             "on a different paper size.",
                    ):
                        try:
                            move_book(pdf_path, INPUT_DIR)
                        except Exception as error:
                            st.error(f"Could not move it: {error}")
                        else:
                            finish(
                                f"Moved “{pdf_path.name}” back to the "
                                f"conversion list."
                            )

                    arm_delete(
                        f"archive-{pdf_path.name}",
                        "Deletes this input PDF from the archive for good.",
                        container=delete_col, disabled=busy,
                    )

                    def remove_archived(path=pdf_path):
                        delete_book(path)
                        return f"Deleted “{path.name}” from the archive."

                    confirm_delete(
                        f"archive-{pdf_path.name}", remove_archived, disabled=busy
                    )

    # --------------------------------------------------------------------------
    # Ready to print
    # --------------------------------------------------------------------------
    with right:
        st.header("Ready to print")

        ready = list_ready_books()
        if not ready:
            st.info("Nothing converted yet.")
        else:
            for book in ready:
                with st.container(border=True):
                    total_kb = sum(s.stat().st_size for s in book.signatures) / 1024
                    st.markdown(f"**{book.name}**")
                    st.caption(
                        f"{len(book.signatures)} signature files · "
                        + (f"{total_kb / 1024:.1f} MB" if total_kb >= 1024
                           else f"{total_kb:.0f} KB")
                    )

                    # One download for the whole book, rather than one per
                    # signature. These are printed as a set, in order, and
                    # fetching them one at a time only creates a chance to print
                    # them out of order or miss one. `data` is the function, not
                    # its result, so the zip is built when the button is clicked
                    # rather than on every rerun of the page.
                    download_col, delete_col = st.columns([2, 1])
                    download_col.download_button(
                        "⬇️ Download this book",
                        data=lambda target=book.folder, name=book.name:
                            workspace.pack_folder(target, name),
                        file_name=f"{book.name}.zip",
                        mime="application/zip",
                        key=f"download-{book.name}",
                        type="primary", use_container_width=True, disabled=busy,
                        help="Every signature file for this book, in print "
                             "order, with its printing notes, as one zip.",
                    )

                    arm_delete(
                        f"ready-{book.name}",
                        "Deletes this book's signature files and its printing notes.",
                        container=delete_col, disabled=busy,
                    )

                    def remove_ready(target=book.folder, name=book.name,
                                     count=len(book.signatures)):
                        delete_ready_book(target)
                        return f"Deleted the {count} signature files for “{name}”."

                    confirm_delete(f"ready-{book.name}", remove_ready, disabled=busy)

                    notes = book.folder / INSTRUCTIONS_NAME
                    if notes.is_file():
                        with st.expander("Paper and printing settings used"):
                            st.code(notes.read_text(encoding="utf-8-sig"), language=None)

                    with st.expander(f"Files in print order ({len(book.signatures)})"):
                        for signature in book.signatures:
                            st.markdown(f"`{signature.name}` · {human_size(signature)}")

# --------------------------------------------------------------------------
# Convert inputted text into PDF signatures — the book editor
# --------------------------------------------------------------------------
# The editor is handed the pieces that have to stay app-wide — the display
# unit, the lock, the flash slot, the armed-delete slot and the two functions
# that keep a tab's own widgets alive while the other tab is up — rather than
# reaching for them itself, so "one job at a time" and "one confirmation on
# screen" still mean what they say now that two views can start work.
#
# The paper is not among them: this view sets a book's size rather than fitting
# an existing book to a sheet, so it asks that question itself, once, in
# 📐 Book design, and hands the answer back on the job it claims.
else:
    pending_job = book_editor.render(
        book_editor.Editor(
            unit=unit,
            busy=busy,
            job=job,
            sheets_per_signature=sheets_per_signature,
            finish=finish,
            claim_job=claim_job,
            arm_delete=arm_delete,
            confirm_delete=confirm_delete,
            sticky=sticky,
            remember=remember,
            build_path=book_pdf_path,
            pack_folder=workspace.pack_folder,
            full=full,
        ),
        folder=MANUSCRIPT_DIR,
    )

# The AI job rejoins the others here, below both views, because the assignment
# just above is unconditional and would otherwise throw it away. Nothing is lost
# the other way either: while an AI job is in flight `busy` is true, so every
# build button in the editor is drawn disabled and `render` cannot have returned
# a job of its own.
if ai_job is not None:
    pending_job = ai_job

st.divider()

how_to, choosing, reference = st.columns(3, gap="large")

with how_to:
    with st.expander("How to print and fold"):
        # The steps themselves live in `main.printing_steps`, because the note
        # written beside every converted book says the same five things and the
        # two used to be kept by hand. Edit them there, not here.
        st.markdown("\n".join(
            f"{number}. {step}"
            for number, step in enumerate(printing_steps(flip_on_long_edge), 1)
        ))
        st.markdown(
            f"Each converted book also gets a `{INSTRUCTIONS_NAME}` file "
            f"recording the paper size, the scaling and the duplex setting it "
            f"was made with."
        )

with choosing:
    with st.expander("Choosing a paper size"):
        st.markdown(
            """
**A sheet is folded across its width.** One sheet of *W × H* paper gives four
book pages of *W/2 × H*. So A4 landscape folds to A5 pages, Letter landscape
folds to Half Letter pages, and a 6 × 9 in book needs 12 × 9 in paper.

**Pick whatever paper you have.** Nothing is scaled here at any size: the book
is *set* at the size you ask for, half a sheet to a page, so the type is drawn
into the PDF at its finished size. Changing the size changes how big the
finished book is, and how many pages it runs to, not how sharp it is.

**One size, said either way.** In **📐 Book design** you can give the finished
page or the paper it prints on, whichever you actually care about, and the app
works out the other. There is no second place to set it.

**Watch the outer margin.** Most printers cannot put ink within about 6 mm
(0.25 in) of the paper edge, so a small page wants small margins to match.
"""
            if writing else
            f"""
**A sheet is folded across its width.** One sheet of *W × H* paper gives four
book pages of *W/2 × H*. So A4 landscape folds to A5 pages, Letter landscape
folds to Half Letter pages, and a 6 × 9 in book needs 12 × 9 in paper.

**Leave it on "{SAME_AS_INPUT}"** unless you have a reason not to. A book laid
out for A4 landscape prints perfectly on A4 with no scaling at all, and scaling
always costs a little sharpness.

**Pick a sheet size when** the book was made for paper you do not have (an
A4 book on a Letter-only printer), when you want a smaller or larger book than
it was drawn for, or when you want to print two-up on big paper and trim.

**Fit vs. keep the original size.** *Fit* scales each book page until it fills
the sheet, keeping its proportions; if the sheet is a different shape, the
leftover appears as extra blank margin. *Keep the original size* never resizes
anything and refuses the job outright if the book will not fit. Use it when the
margins matter more than filling the paper.

**Watch the outer margin.** Most printers cannot put ink within about 6 mm
(0.25 in) of the paper edge. Shrinking a book onto smaller paper shrinks its
margins too, and the app warns when the text gets that close to the edge.
"""
        )

with reference:
    with st.expander("Paper and book size reference"):
        st.markdown("**Sheets you can print on**, and what one folds down to:")
        st.markdown(
            markdown_table(
                ("Sheet", "Family", "Size", "Folded book page", "Notes"),
                [(name, family, size, folded, note)
                 for family, name, size, folded, note in paper_size_table(unit=unit)],
            )
        )
        st.markdown("")
        st.markdown("**Finished book sizes**, and the sheet each one needs:")
        st.markdown(
            markdown_table(
                ("Book page", "Family", "Trim size", "Sheet needed", "Nearest stock"),
                [(name, family, trim, needed, stock)
                 for family, name, trim, needed, stock, _note in book_page_table(unit=unit)],
            )
        )
        st.caption(
            f"“Nearest stock” is the smallest standard paper the needed sheet fits on. "
            f"Print on that with a custom sheet size and trim the finished book down, or "
            f"pick the stock itself and accept slightly larger pages. "
            f"{len(SHEET_SIZES)} sheet sizes, and {len(BOOK_PAGE_SIZES)} book sizes "
            f"across {len(BOOK_PAGE_FAMILIES)} categories."
        )

# --------------------------------------------------------------------------
# Data policy
# --------------------------------------------------------------------------
# Outside the `if view ==` above and last of everything drawn, so it stands under
# both halves of the app: a notice that appears on one tab is a notice half the
# visitors never see. The divider is the line it hangs under, run across the foot
# of the page.
#
# On the page rather than behind an expander, and unfolded rather than in a
# dialog, because the transparency this is written to satisfy is about a person
# actually being told. A notice nobody can find is not a notice; the GDPR's own
# word for what it has to be is "easily accessible".
#
# The figures are read out of `workspace` rather than typed in here, for the same
# reason `TAGLINE` is said once at the top of this file and the folding steps
# live in `main.printing_steps`. Every promise below is a claim about code in
# this repository — a policy still promising a thirty-second erasure after the
# sweeper has been changed to five minutes is worse than no policy at all, and
# hand-kept copies of a number are exactly how that happens.
#
# What section 5 says is a disclosure rather than a promise, and it is there
# because a policy is only worth anything if it describes the app that exists:
# Render logs every visitor to every service, and no line of this repository can
# switch that off. It is named, with its own link, instead of being left to be
# inferred from a sentence about how nothing is stored here.
#
# The AI service is the second such disclosure, and the more serious one, because
# it is the only thing in this app that sends a visitor's words off this machine.
# It is stated three times over — in the opening paragraph, in section 5 and in
# section 9 — because a reader who skims will see the first, and because the
# thing being disclosed is the one thing the rest of the notice spends fourteen
# sections promising does not happen. If `Script/ai_book.py` is ever removed, or
# the button taken off the page, those three passages and section 12's paragraph
# about invented text come out with it.
#
# The framework's own telemetry used to be the second such disclosure. It is now
# a promise instead — `gatherUsageStats = false` in `.streamlit/config.toml`
# turns it off at the source — which is why section 4 states it as a flat "no
# telemetry leaves" rather than section 5 conceding one. If that line is ever
# removed from the config, this notice becomes untrue on the point a reader is
# most likely to care about, so the two belong changed together.
REPOSITORY = "https://github.com/DariuszKrych/Bookbinding_Signature_Creator_App"
POLICY_UPDATED = "24 August 2026"

# The four folder names as the policy says them, built here rather than inside
# the f-string below: nesting a quoted separator inside a triple-quoted f-string
# is legal and unreadable, and this is the one place in the notice where the
# text has to be assembled rather than simply interpolated.
SESSION_FOLDERS = ", ".join(f"`{name}/`" for name in workspace.DATA_FOLDERS)

DATA_POLICY = f"""
**Nothing you upload or type into this app is stored.** Your files exist on the
server only while this tab is open, in a folder no other visitor can reach, and
they are erased within about a minute of the tab closing. Nobody reads them, no
copy is kept, nothing about them is logged, shared, sold, or used to train
anything. The only lasting copy of anything is the zip you download yourself.

**One thing leaves this server, and only if you press the button that does it.**
**🤖 Generate 5 chapter book for printing with AI** sends the sentence you type in
its box to an AI service, which writes a book back. Nothing else from your session goes
with it — not your manuscript, not your drafts, not your uploads, not their
names. Never press it, and nothing you do here ever leaves. Section 5 says
exactly what that involves, and it is worth reading before you use it.

**1. Who runs this.** A free, non-commercial hobby project, maintained by one
private individual and published as a Docker web service on Render. The source is
public under the MIT License at
[github.com/DariuszKrych/Bookbinding_Signature_Creator_App]({REPOSITORY}), so
every claim on this page can be checked against the code that makes it. For
anything here — a question, a request, a copyright complaint — open an issue on
that repository. Where the GDPR or an equivalent law applies, the maintainer is
the controller for the narrow technical processing described in sections 2 and 5,
and for nothing else.

**2. What happens to a file you give it.** An upload is held in the Streamlit
server's memory and written into a folder made for your session alone, named
after your session id, under the host machine's temporary directory
({SESSION_FOLDERS}). Books you type are kept as JSON drafts in the same place.
Everything done to them is mechanical: pages are split down their
own middle, re-ordered into signatures, stamped with page numbers, or typeset
into a PDF. **No file is opened, read, indexed, scanned, analysed, or looked at by
a person, and nothing in your session is sent anywhere — with the single
exception in section 5, which happens only when you press the button that does
it.** There is no database, no object store, no backup, and
no log of file names or of anything inside a file. A session may hold
{workspace.human(workspace.LIMIT_BYTES)} in all, and a single uploaded PDF may be
{workspace.human(workspace.MAX_UPLOAD_BYTES)}.

**3. How long it lasts, and the legal basis for holding it at all.** Four ways it
goes, three of them without you doing anything. A sweeper thread wakes every
{workspace.SWEEP_SECONDS} seconds, asks which sessions still have a browser
attached, and deletes the folder of every session that does not, after a
{workspace.GRACE_SECONDS}-second grace period so a network blip cannot cost you a
book. Everything the process created is removed when the process exits, so the
service going to sleep takes it all with it. A folder untouched for
{workspace.ORPHAN_SECONDS // 60} minutes is removed anyway, in case neither of
the first two can run. And **🗑 Delete my data now**, in the sidebar, erases the
whole session immediately, at your word, with nothing to wait for. The lawful
basis is Article 6(1)(b) — the processing *is* the service you asked for — there
is no secondary purpose, and the retention period is the session and not one
minute longer. The same basis covers the AI request in section 5 — it is the
thing you asked for by pressing the button, it happens on that press and at no
other time, and this app keeps no copy of what was sent. Where that request
leaves the UK or EEA, it does so because you instructed it: Article 49(1)(b)
covers a transfer necessary to perform the service you requested. It is not
relied on for anything else here, and it is a further reason the box asks for a
description of a book and not for anything about a person.

**4. What is never collected.** No account, no sign-up, no name, no email
address, no password, no payment details. No tracking, no advertising, no
profiling, no behavioural analytics, and no automated decision-making of the kind
Article 22 is about — the AI in section 5 writes a chapter of a book when asked
to, and makes no decision about you, produces no assessment of you, and never
sees anything about you to make one from. This app sets no cookie of its own. The Streamlit framework
sets one strictly necessary `_xsrf` cookie, which exists to stop another site
forging requests to this one; a cookie strictly necessary to deliver a service
the visitor has asked for requires no consent under the ePrivacy Directive, which
is why there is no cookie banner and nothing here to consent to. Streamlit would
otherwise send anonymous usage statistics of its own to Snowflake Inc. in the
United States — that is its default, and it is **switched off here**
(`gatherUsageStats = false` in `.streamlit/config.toml`), so no telemetry leaves
this app about you or about anything you do in it.

**5. What the maintainer does not control, and you should know about anyway.**
Three things sit outside this app, and honesty about them is worth more than a
clean-sounding promise:

- **The host.** This runs as a Docker web service on Render, on Render's servers.
  Independently of this app and under its own policy, Render records your IP
  address, request metadata, information about your device and browser, and the
  service's technical logs. That data may be stored and processed in the United
  States or any other country where Render or its infrastructure providers keep
  facilities. It is theirs and not the maintainer's: it cannot be read, corrected
  or deleted from inside this app, and requests about it go to them. See the
  [Render Privacy Policy](https://render.com/privacy) and
  [Terms of Service](https://render.com/terms). The maintainer can see the
  technical runtime logs the platform shows every service owner; they carry
  nothing out of your files.
- **The AI service, if and only if you press the AI button.** Pressing
  **🤖 Generate 5 chapter book for printing with AI** sends the description you typed, plus
  this app's own fixed writing instructions, to
  [OpenRouter](https://openrouter.ai). **Nothing else goes with it** — not your
  manuscript, not your drafts, not your uploaded PDFs, not any file name, and no
  identifier of you: the request is made by this server, under this copy's own
  key, and your IP address is never part of it. OpenRouter passes it on to
  whichever free model provider is serving the request, which may be in the
  United States or anywhere else. **Providers of free models may keep what is
  sent to them, and may train on it.** So treat that box as public: do not type
  anything into it you would not be willing to publish. What comes back is
  written into the editor and stored nowhere but your own session, exactly like a
  book you typed yourself. See the [OpenRouter Privacy
  Policy](https://openrouter.ai/privacy) and
  [Terms](https://openrouter.ai/terms). Leave the button alone and none of this
  applies to you.
- **The network.** The page is served over HTTPS, but the connection between your
  machine and the server crosses networks nobody here operates.

**6. What you must not put into it.** By uploading or typing anything you confirm
that it is yours or that you are otherwise entitled to use it this way. Do not
put into this app: material you have no right to copy; personal data about other
people, and in particular the special categories of Article 9 — health,
biometrics, race, religion, politics, sex life, trade union membership — or data
about criminal offences; anything confidential, classified or export-controlled;
or anything unlawful. If you do put another person's personal data through it,
**you** are the controller of that data and answerable for having a lawful basis
for it. The maintainer acts only as your processor, only mechanically, only for
the seconds the work takes, and keeps none of it.

That goes double for the AI description box, and for a different reason: it is
the only box whose contents leave this server. **Treat it as public.** Put a
description of a book in it — subject, length, voice, who it is for — and nothing
else. No personal data about yourself or anybody else, nothing confidential, and
nothing you would mind a third party keeping. Section 5 explains why: what is
sent may be retained and trained on by whichever provider answers, which is not
something this app can undo or promise against.

**7. Copyright, and who owns what the AI wrote.** Nothing is retained, so there is
never a copy here to take down. If you believe this app has been used against your
rights, tell the maintainer through the routes in section 1, or report the service
to Render under its acceptable use policy.

**The maintainer claims nothing over anything you make here**, typed or generated.
What the AI writes is handed to you and kept nowhere. But note two things honestly,
because neither is this app's to settle: whether text produced by a model attracts
copyright at all, and who would own it if it does, differ between countries and are
still being argued over — in several, work with no human author does not qualify.
And a model can reproduce something close to what it was trained on without either
of you knowing. So the text arrives as a draft, not as a cleared asset. If you mean
to sell or publish it, that is yours to check, and section 12 says so again.

**8. Your rights.** Where the GDPR or an equivalent law applies you have rights
of access, rectification, erasure, restriction, portability and objection. In
practice the maintainer holds no personal data about you whatsoever and has no
way to identify you — Article 11 is explicit that a controller need not acquire
extra information merely in order to identify someone. The two rights that do
bite are on the
page and take effect at once, with nobody to ask: **📤 Save my data** is
portability, and **🗑 Delete my data now** is erasure. You may complain to the
data protection authority of your own country. Rights over the data described in
section 5 are exercised with Render, or with OpenRouter, and not here.

**9. Given to nobody, with one exception you choose.** Nothing you provide is
sold, rented, shared, disclosed or transferred to any third party, for any
purpose, commercial or otherwise. There is no analytics vendor and no advertiser.
The only processors are the host in section 5 and — solely when you press the AI
button, and solely for the sentence you typed into its box — the AI service in
that same section. **Everything else you upload or type stays on this server and
is used to train nothing.** The description you send to the AI service is the one
thing this cannot promise for, because that is not the maintainer's to promise;
section 5 says so plainly rather than hiding it here. Disclosure beyond that
would arise only if a valid legal order compelled it, and by then there would be
nothing left to disclose.

**10. No warranty.** This app is provided free of charge, **as is and as
available, without warranty of any kind**, express or implied, including any
implied warranty of merchantability, fitness for a particular purpose and
non-infringement — in the words of the [MIT License]({REPOSITORY}/blob/main/LICENSE)
it is released under. It is a hobby tool for folding paper. It is not a
professional, certified, archival or backup service, and must not be relied on as
one.

**11. Limitation of liability.** To the fullest extent permitted by law, the
maintainer is not liable for any loss or damage arising out of or connected with
this app, its output, or its unavailability: lost, corrupted or prematurely
erased files, unsaved work, wrongly imposed or misprinted output, wasted paper,
ink, materials or time, lost profits or business, or any indirect, incidental,
special or consequential loss, whether or not the possibility of it was known.
**Treat this as a tool that forgets everything, because it is one.** It may
sleep, restart, be updated or be withdrawn at any moment, taking the session with
it. Save your zip. Print one test signature before you print a whole book.
Nothing in this notice excludes or limits any liability that cannot lawfully be
excluded, including for death or personal injury caused by negligence, or for
fraud.

**12. Your responsibility.** You are responsible for your own use of this app and
for whatever you put into it. To the extent the law allows, you will hold the
maintainer harmless against any claim, demand, loss or cost brought by anyone
else and arising from what you uploaded or typed, or from what you did with what
came out. **This includes anything the AI button writes.** A model invents: it
can be wrong about plain facts, it can produce something that resembles work
somebody else already owns, and it has no idea which it is doing. What it writes
arrives as a draft for you to read, edit and decide about — check it before you
print it, and check it properly before you publish or sell it.

**13. Children.** This app is not directed at children, and nothing is knowingly
collected from anyone, of any age.

**14. Changes.** This notice changes as the app does. Whatever it says on the
page in front of you is the version that governs the session you are in, and
every earlier version is in the repository's history.

**Using this app means accepting this notice.** If any of it does not suit you,
run the app on your own machine instead — it behaves identically there, and the
source is one clone away.
"""

st.divider()

st.markdown("#### 🔒 Data policy")
# Dated, because a policy without one cannot be told from the version it
# replaced — and section 14 leans on the reader being able to see which they are
# looking at. Update it when the words above change.
st.caption(f"Last updated {POLICY_UPDATED}. Applies to both tabs.")
st.markdown(DATA_POLICY)

# --------------------------------------------------------------------------
# Carrying the appearance choice out to the browser
# --------------------------------------------------------------------------
# Which palette the app is drawn in is decided in the browser, not on the
# server: Streamlit sends both versions of the theme once, when the page is
# first opened, and the switcher in its ⋮ menu picks between them client-side
# without a rerun. There is no Python call for that choice, so the sidebar
# radio reaches the same switcher the same way a click would — the menu is
# still built, only hidden, and its System / Light / Dark items are still
# there to be clicked.
#
# Reaching across from an `st.iframe` is what makes that possible: it is drawn
# from `srcdoc`, so it shares the page's origin and can see `window.parent`.
# The rest is the shape of that menu: the radio items only exist while the
# panel is open, so it is opened, the wanted one is clicked, and it is shut
# again — selecting a theme is the one thing in there that does not close it.
#
# Nothing here runs on an ordinary rerun. The script carries the chosen mode,
# so the iframe's content only changes when the choice does, and an unchanged
# `srcdoc` is never reloaded.
APPEARANCE_SCRIPT = """
<style>html, body { margin: 0; background: transparent; }</style>
<script>
(function () {
  var page;
  try {
    page = window.parent.document;
  } catch (error) {
    return;                       // Not the same origin: nothing to reach.
  }

  var TRIGGER = '[data-testid="stMainMenuButton"]';
  var PANEL = '[data-testid="stMainMenuPopover"]';
  var OPTION = '[data-testid="stMainMenuItem-theme-__MODE__"]';

  function again(step, tries) {
    if (tries > 0) window.setTimeout(function () { step(tries - 1); }, 50);
  }

  // Escape first, because it can only ever close. Clicking the button is a
  // toggle, and is kept back as the fallback for the same reason.
  function shut() {
    var panel = page.querySelector(PANEL);
    if (!panel) return;
    panel.dispatchEvent(new window.parent.KeyboardEvent(
      "keydown", { key: "Escape", bubbles: true }
    ));
    window.setTimeout(function () {
      var trigger = page.querySelector(TRIGGER);
      if (page.querySelector(PANEL) && trigger) trigger.click();
    }, 80);
  }

  function choose(tries) {
    var option = page.querySelector(OPTION);
    if (!option) {
      again(choose, tries);
      return;
    }
    if (option.getAttribute("aria-checked") !== "true") option.click();
    window.setTimeout(shut, 0);
  }

  function open(tries) {
    var trigger = page.querySelector(TRIGGER);
    if (!trigger) {
      again(open, tries);
      return;
    }
    if (!page.querySelector(OPTION)) trigger.click();
    choose(20);
  }

  open(100);                      // The header is not drawn on the first frame.
})();
</script>
"""

# One transparent pixel, at the very foot of the page, where nothing is pushed
# about by it. `st.iframe` will not take a height of nought.
st.iframe(
    APPEARANCE_SCRIPT.replace("__MODE__", appearance),
    width=1,
    height=1,
    tab_index=-1,
)

# --------------------------------------------------------------------------
# The job itself, run last of all
# --------------------------------------------------------------------------
# Nothing above this point does any work. The whole page — all three panels, the
# sidebar, the reference tables — is drawn first, with every widget disabled,
# and only then does the conversion start.
#
# That ordering is the fix for the worst failure this app had. Streamlit paints
# elements as the script produces them, so a long job run half way down the page
# left the panels below it still showing the *previous* run's widgets: buttons
# that looked live because they were live. Clicking one asked Streamlit for a
# rerun, and Streamlit grants that by stopping the running script dead — in the
# middle of writing a book's signature files. Drawing everything first means
# there is no live widget left anywhere on the page by the time a single page is
# imposed, so the job cannot be interrupted from the UI at all.
#
# The progress bar goes into the slot the button was occupying, so starting and
# finishing a job moves nothing on the page.
#
# The AI job also fills a box with the book as the model writes it. Two numbers
# govern that, and both are about the browser rather than about the model:
#
# * A reply arrives as hundreds of tiny pieces, and redrawing on every one would
#   put hundreds of messages down the websocket for text nobody could read that
#   fast. Ten times a second still looks like typing.
# * Only the last part of the book is shown. The box is a fixed height so that
#   the page cannot grow under the reader's cursor, and it does not scroll itself,
#   so a window of roughly a box-full keeps the newest words in view.
STREAM_EVERY_SECONDS = 0.1
STREAM_TAIL_CHARS = 900
STREAM_BOX_HEIGHT = 260

FAILURE_LABELS = {
    "convert": "Conversion failed",
    "number": "Numbering failed",
    "typeset": "Could not build the book",
    "typeset_convert": "Could not build the book",
    "ai_write": "The AI could not write the book",
}

if pending_job is not None:
    # Said again, right here, because the four names being written through live
    # on `main` — one module shared by every session in the process. Between the
    # top of this run and this line the whole page was drawn, and on a hosted
    # copy another session's run could have pointed them at its own workspace in
    # the meantime. One call, immediately before the first byte is written.
    workspace.reassert(WORKSPACE)

    kind = pending_job["kind"]
    slot = pending_job["slot"]
    # What to call it if it throws. ✂️ Create the signatures is two jobs behind
    # one button, and it moves on to the second only once the PDF is written and
    # named in the last-build note — so a failure there has to say the book was
    # built. "Could not build the book", printed under a note saying it had just
    # been built, is the app disagreeing with itself about a file the user can go
    # and open.
    failure = FAILURE_LABELS.get(kind, "That did not work")
    bar = slot.progress(0.0, text="Starting…")

    # The limit, held over work whose output size nobody can know in advance.
    # Imposition reports a page at a time and this is checked at most once a
    # second, so a job that starts inside the limit and would end outside it is
    # stopped part way rather than allowed to finish — which is the only way the
    # cap can be a cap rather than a hope.
    over_limit = workspace.watcher(WORKSPACE)

    def report(fraction, message):
        over_limit()
        bar.progress(min(max(fraction, 0.0), 1.0), text=message)

    def live_writer(slot):
        """A function that puts the book on screen as it is being written.

        Returns something that does nothing at all when there is no slot to draw
        into, so the caller has no branch to write. The quota is deliberately not
        checked in here: `report` is called between chapters and is where a job
        gets stopped, and a limit enforced from a display would stop a book for
        being long to look at.
        """
        if slot is None:
            return None
        last_drawn = [0.0]

        def show(text):
            now = time.monotonic()
            if now - last_drawn[0] < STREAM_EVERY_SECONDS:
                return
            last_drawn[0] = now
            tail = text[-STREAM_TAIL_CHARS:]
            if len(tail) < len(text):
                # Cut at a paragraph rather than mid-word, and say that the
                # beginning is missing rather than looking like a book that
                # starts in the middle of a sentence.
                broken = tail.find("\n\n")
                tail = "…\n\n" + (tail[broken + 2 :] if broken >= 0 else tail)
            slot.container(height=STREAM_BOX_HEIGHT, border=True).markdown(tail)

        return show

    def impose(pdf_path, layout, report=report):
        """The conversion, shared by the button on a PDF and the one on a book.

        The paper comes off the job rather than off the page: each tab decides
        it for itself now, and only one of the two is on screen. Everything else
        here is a sidebar setting and means the same thing on both.
        """
        return convert_book(
            pdf_path,
            layout=layout,
            sheets_per_signature=sheets_per_signature,
            flip_on_long_edge=flip_on_long_edge,
            sheet_size_pt=pending_job.get("sheet_size_pt"),
            scale_mode=pending_job.get("scale_mode", FIT),
            # Always, and no longer asked: a converted book belongs in the
            # archive, so the conversion list stays the books not done yet.
            # The archive panel moves any of them back.
            move_input=True,
            progress=report,
        )

    # Set once the typesetting has finished, so the quota handler below can tell
    # a book that was never written from one that was written and then had its
    # imposition stopped. The second is a real result and is kept.
    built = None

    try:
        if kind == "convert":
            written = impose(pending_job["path"], pending_job["layout"])
            done = (
                f"Created {signature_count(written)} for "
                f"“{pending_job['path'].stem}”."
            )
        elif kind == "number":
            numbered = number_book(
                pending_job["path"], pending_job["layout"], progress=report
            )
            done = f"Wrote “{numbered.name}”. The original is still here."
        elif kind == "ai_write":
            # Whatever is in the editor goes to a draft of its own first, so a
            # book somebody typed is never the price of pressing this button.
            # Atomic, and done before a single word is sent anywhere.
            kept = book_editor.keep_current_as_draft(MANUSCRIPT_DIR)
            if kept:
                report(0.02, f"Kept your book as the draft “{kept}”.")
            written = ai_book.write_book(
                pending_job["prompt"],
                # The design the editor already had, carried across untouched:
                # the model writes the words, not the page size or the type.
                design=book_editor.manuscript().design,
                progress=lambda f, m: report(0.03 + f * 0.96, m),
                # The words as they are written, into the box under the button.
                # A book takes minutes; this is what fills them.
                on_text=live_writer(pending_job.get("stream")),
            )
            # Left for the next run rather than adopted here. Every box for the
            # book being replaced was drawn long before this line, and adopting
            # deletes exactly those keys. See `book_editor.hand_over`.
            book_editor.hand_over(written)
            done = (
                f"Wrote “{written.display_title}” — {len(written.chapters)} "
                f"chapters, {written.words:,} words. Nothing is saved yet: press "
                "💾 Save to keep it."
                + (f" Your previous book is in the drafts list as “{kept}”." if kept else "")
            )
        else:
            # Typesetting takes the first part of the bar and, when signatures
            # were asked for, imposition takes the rest — one bar for what the
            # user thinks of as one action.
            whole = kind == "typeset_convert"
            result = typeset_book(
                pending_job["manuscript"], pending_job["file_name"],
                progress=(lambda f, m: report(f * 0.45, m)) if whole else report,
                # Half the chosen sheet, or None when the size was given as the
                # book's own page. Setting the type at the size it will be
                # printed at is what keeps this path free of any scaling.
                page_size_in=pending_job["page_size_in"],
            )
            built = result.path
            book_editor.remember_build(result, result.path.name)
            if whole:
                failure = "The book was built, but the signatures were not"
                written = impose(
                    result.path, None,
                    report=lambda f, m: report(0.45 + f * 0.55, m),
                )
                folder = written[0].parent.parent
                # Recorded again, now that there is something to download: the
                # writing view has no "Ready to print" panel of its own, so
                # without this the finished signatures could only be fetched by
                # switching views.
                book_editor.remember_build(
                    result, result.path.name, output_folder=folder
                )
                done = (
                    f"Typeset “{result.path.stem}” into {result.book_pages} book "
                    f"pages, and created {signature_count(written)} for it."
                )
            else:
                done = (
                    f"Typeset “{result.path.name}”: {result.book_pages} book "
                    f"pages. Convert it from "
                    f"**{VIEW_LABELS[CONVERT_VIEW].strip()}**."
                )
    except workspace.QuotaExceeded as error:
        # A job stopped part way has left a half-written file behind, and the
        # whole point of stopping was to stay under the limit — so the wreckage
        # goes too. A conversion cleans up after itself (`convert_book` writes
        # into a staging folder and removes it on any failure); these are the two
        # that write a file directly.
        wreck = None
        if kind == "number":
            wreck = numbered_copy_path(pending_job["path"])
        elif kind in ("typeset", "typeset_convert") and built is None:
            # `built is None` means the typesetting itself was stopped. A book
            # that was typeset and then had only its imposition stopped is a
            # real result and stays; the writing view offers it as a build.
            wreck = book_pdf_path(pending_job["file_name"])
        if wreck is not None:
            Path(wreck).unlink(missing_ok=True)
        finish(error=f"Stopped: {error}")
    except Exception as error:
        finish(error=f"{failure}: {error}")
    else:
        finish(done)
    finally:
        # Runs even on the rerun `finish` raises, so a crash can never leave
        # every button in the app stuck disabled.
        release_job()
elif busy:
    # A job was claimed for a book that is no longer on the page — it was
    # renamed or removed between the click and this run. Without this the flag
    # would never be cleared by anything, and every control in the app would
    # stay disabled for the rest of the session.
    release_job()
    st.rerun()
