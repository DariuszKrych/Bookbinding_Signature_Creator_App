"""Streamlit GUI for the Bookbinding Signature Creator.

Run with:  streamlit run app.py
"""

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
    open_folder,
    typeset_book,
)
from Script import book_editor
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
    menu_items={
        "About": """
            ### 📖 Bookbinding Signature Creator

            Turns a 2-column PDF book, or a book typed straight into the app,
            into printable signature files, on any paper you can feed a
            printer. Print one signature double-sided, fold every sheet in half
            and nest them one inside another; sew the signatures together and
            you have a book.

            Everything happens on this machine: the PDFs never leave the
            `Input/`, `Previously_Converted/` and `Output/` folders beside the
            app.
        """,
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
        height: 2.5rem !important;
        margin-bottom: 0 !important;
    }
    </style>
    """
)

for folder in (INPUT_DIR, OUTPUT_DIR, PREVIOUS_DIR, MANUSCRIPT_DIR):
    folder.mkdir(parents=True, exist_ok=True)

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


def folder_line(label, path):
    st.caption(label)
    st.code(str(path), language=None)


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


def arm_delete(token, help_text, container=st, disabled=False):
    """The 🗑 button. It only *arms* the delete; `confirm_delete` does the work.

    Everything else in this app can be undone by clicking the opposite button, so
    deleting is the one place worth spending a second click on. An archived PDF in
    particular is often the only copy left, the conversion having moved it out of
    Input/.

    One key holds the armed request, so arming a second delete disarms the first
    and there is never more than one confirmation on screen.
    """
    if container.button(
        "🗑 Delete", key=f"arm-delete-{token}", use_container_width=True,
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
st.caption(
    "Turns a 2-column PDF book, or a book typed straight into the app, into "
    "printable signature files, on any paper you can feed a printer. Print one "
    "signature double-sided, fold every sheet in half and nest them one inside "
    "another; sew the signatures together and you have a book."
)

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

# The sidebar holds the settings that mean the same thing on both tabs, and
# only those. Everything about the paper is decided on the tab it belongs to —
# the sheet an existing PDF is printed on is a different question from the size
# a book being typed is set at, and asking both here is what left the writing
# tab with two menus for one measurement, one in the sidebar and one on the
# page.
with st.sidebar:
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
        folder_line("Drop input PDFs in this folder:", INPUT_DIR)

        # The uploader is rebuilt under a fresh key after each save. Without that it
        # keeps handing back the same files on every rerun, which either re-saves a
        # file the user has since moved away, or loops forever on the rerun below.
        upload_round = st.session_state.setdefault("upload_round", 0)
        uploaded = st.file_uploader(
            "…or upload PDFs here", type="pdf", accept_multiple_files=True,
            key=f"uploader-{upload_round}", disabled=busy,
        )
        if uploaded:
            saved = []
            for item in uploaded:
                # The browser supplies this name, so it is taken apart and only its
                # final component kept: a name carrying separators would otherwise
                # place the write somewhere other than Input/.
                name = Path(item.name).name
                if not name or not name.lower().endswith(".pdf"):
                    continue
                target = INPUT_DIR / name
                if not target.exists():
                    target.write_bytes(item.getbuffer())
                    saved.append(name)
            st.session_state.upload_round = upload_round + 1
            finish(
                f"Added {len(saved)} PDF(s) to Input."
                if saved
                else "Those PDFs are already in Input."
            )

        available = list_available_books()
        if not available:
            st.info("No PDFs waiting. Add a 2-column PDF book to the folder above.")
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
                        disabled=bool(problems) or busy,
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
                        disabled=already_numbered or busy,
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
        folder_line("Input PDFs are moved here after conversion:", PREVIOUS_DIR)

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
                        "Move back to Input", key=f"restore-{pdf_path.name}",
                        use_container_width=True, disabled=busy,
                        help="Puts this book back in the conversion list, e.g. to redo it "
                             "on a different paper size.",
                    ):
                        try:
                            move_book(pdf_path, INPUT_DIR)
                        except Exception as error:
                            st.error(f"Could not move it: {error}")
                        else:
                            finish(f"Moved “{pdf_path.name}” back to Input.")

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
        folder_line("Finished files are written here:", OUTPUT_DIR)

        ready = list_ready_books()
        if not ready:
            st.info("Nothing converted yet.")
        else:
            for book in ready:
                with st.container(border=True):
                    folder = book.signatures[0].parent
                    total_kb = sum(s.stat().st_size for s in book.signatures) / 1024
                    st.markdown(f"**{book.name}**")
                    st.caption(
                        f"{len(book.signatures)} signature files · "
                        + (f"{total_kb / 1024:.1f} MB" if total_kb >= 1024
                           else f"{total_kb:.0f} KB")
                    )
                    st.code(str(folder), language=None)

                    # One button for the whole run, rather than one download per
                    # signature. These are printed as a set, in order, from the
                    # folder they already sit in — downloading them one at a time to
                    # a second folder only creates a chance to print them out of
                    # order or miss one.
                    open_col, delete_col = st.columns([2, 1])
                    if open_col.button(
                        "📂 Open file location", key=f"open-{book.name}",
                        type="primary", use_container_width=True, disabled=busy,
                        help="Opens this book's signature folder in your file "
                             "manager, with every signature file for this run in "
                             "print order.",
                    ):
                        try:
                            open_folder(folder)
                        except Exception as error:
                            st.error(f"Could not open the folder: {error}")
                        else:
                            st.toast(f"Opened {folder.name} for “{book.name}”.", icon="📂")

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
            open_folder=open_folder,
        ),
        folder=MANUSCRIPT_DIR,
    )

st.divider()

how_to, choosing, reference = st.columns(3, gap="large")

with how_to:
    with st.expander("How to print and fold"):
        st.markdown(
            f"""
1. Print **one signature file at a time**, double-sided, at **100% / "Actual size"**.
   Never "fit to page": the pages are already the size of your paper, and letting
   the printer scale them again shrinks the margins and moves the fold off centre.
2. Load the paper the book was made for, and check that the print dialog agrees
   about its size; some drivers quietly fall back to their own default.
3. Set your printer's duplex option to **{'long' if flip_on_long_edge else 'short'} edge**,
   matching the setting in the sidebar.
4. Fold **every sheet** of a signature in half, then **nest** them one inside the
   other. The first sheet printed is the outermost.
5. Page numbers should run in order through the folded signature. If every other
   page is upside down, switch the duplex setting and reprint.

Each converted book also gets a `{INSTRUCTIONS_NAME}` file recording the paper
size, the scaling and the duplex setting it was made with.
"""
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
FAILURE_LABELS = {
    "convert": "Conversion failed",
    "number": "Numbering failed",
    "typeset": "Could not build the book",
    "typeset_convert": "Could not build the book",
}

if pending_job is not None:
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

    def report(fraction, message):
        bar.progress(min(max(fraction, 0.0), 1.0), text=message)

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
            book_editor.remember_build(result, result.path.name)
            if whole:
                failure = "The book was built, but the signatures were not"
                written = impose(
                    result.path, None,
                    report=lambda f, m: report(0.45 + f * 0.55, m),
                )
                folder = written[0].parent.parent
                # Recorded again, now that there is a folder to point at: the
                # writing view has no "Ready to print" panel of its own, so
                # without this the finished signatures are only findable by
                # switching views.
                book_editor.remember_build(
                    result, result.path.name, output_folder=folder
                )
                done = (
                    f"Typeset “{result.path.stem}” into {result.book_pages} book "
                    f"pages, and created {signature_count(written)} for it, in "
                    f"“{folder.name}”."
                )
            else:
                done = (
                    f"Typeset “{result.path.name}” into Input: "
                    f"{result.book_pages} book pages. Convert it from "
                    f"**{VIEW_LABELS[CONVERT_VIEW].strip()}**."
                )
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
