"""Streamlit GUI for the Bookbinding Signature Creator.

Run with:  streamlit run app.py
"""

import streamlit as st

from main import (
    DEFAULT_SHEETS_PER_SIGNATURE,
    INPUT_DIR,
    OUTPUT_DIR,
    PREVIOUS_DIR,
    convert_book,
    list_available_books,
    list_previous_books,
    list_ready_books,
    move_book,
    number_book,
    numbered_copy_path,
)
from Script.print_formatting import (
    CENTIMETRES,
    INCHES,
    ColumnLayout,
    inspect_book,
    layout_problems,
    layout_warnings,
    plan_signatures,
)

st.set_page_config(page_title="Bookbinding Signature Creator", page_icon="📖", layout="wide")

for folder in (INPUT_DIR, OUTPUT_DIR, PREVIOUS_DIR):
    folder.mkdir(parents=True, exist_ok=True)

DEFAULT_LAYOUT_IN = {
    "margin": ColumnLayout.page_margin_in,
    "gap": ColumnLayout.column_gap_in,
    "width": ColumnLayout.column_width_in,
}
UNITS = {"Inches (in)": INCHES, "Centimetres (cm)": CENTIMETRES}


def human_size(path):
    kb = path.stat().st_size / 1024
    return f"{kb / 1024:.1f} MB" if kb >= 1024 else f"{kb:.0f} KB"


def folder_line(label, path):
    st.caption(label)
    st.code(str(path), language=None)


def finish(message):
    """Report an action and refresh every panel it could have changed."""
    st.session_state.flash = message
    st.rerun()


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
st.title("📖 Bookbinding Signature Creator")
st.caption(
    "Turns a 2-column PDF book into printable signatures. Print each signature "
    "double-sided, fold every sheet in half, and nest them to form the signature."
)

with st.sidebar:
    st.header("Settings")

    unit = UNITS[
        st.radio(
            "Units",
            list(UNITS),
            horizontal=True,
            help="Applies to every measurement shown below. Switching converts the "
                 "current values rather than reinterpreting them, so the book is "
                 "unaffected.",
        )
    ]
    st.caption("The colour theme lives in the **⋮** menu, top right → *Settings*.")

    # Measurements are stored in inches and only converted for display, so a
    # unit switch cannot drift the layout. The widgets are keyed per unit, so
    # the stale keys have to go or the old unit's number would come back.
    layout_in = st.session_state.setdefault("layout_in", dict(DEFAULT_LAYOUT_IN))
    if st.session_state.get("unit_name") != unit.name:
        for field in DEFAULT_LAYOUT_IN:
            for known in UNITS.values():
                st.session_state.pop(f"{field}-{known.name}", None)
        st.session_state.unit_name = unit.name

    st.divider()

    sheets_per_signature = st.number_input(
        "Sheets of paper per signature",
        min_value=1,
        max_value=25,
        value=DEFAULT_SHEETS_PER_SIGNATURE,
        step=1,
        help="Physical sheets in one folded gathering. Each sheet is printed on "
             "both sides and folded once, so it carries 4 book pages.",
    )
    st.caption(
        f"= {sheets_per_signature * 2} printed sides, "
        f"**{sheets_per_signature * 4} book pages** per signature"
    )

    st.subheader("Column layout")
    st.caption(f"Where the two columns sit on a page of the *input* PDF, in {unit.name}.")

    def length_input(label, field, max_in, help_text, min_in=0.0):
        value = st.number_input(
            f"{label} ({unit.name})",
            min_value=round(unit.from_inches(min_in), 3),
            max_value=round(unit.from_inches(max_in), 3),
            value=round(unit.from_inches(layout_in[field]), 3),
            step=unit.step,
            format="%.3f",
            key=f"{field}-{unit.name}",
            help=help_text,
        )
        layout_in[field] = unit.to_inches(value)
        return layout_in[field]

    page_margin_in = length_input(
        "Outer page margin", "margin", 5.0,
        "From the paper edge to the outer edge of the first column.",
    )
    column_width_in = length_input(
        "Column width", "width", 12.0,
        "Width of each of the two columns. Both are the same width.",
        min_in=0.1,
    )
    column_gap_in = length_input(
        "Gap between the two columns", "gap", 6.0,
        "The gutter. The fold runs down the middle of it.",
    )

    layout = ColumnLayout(page_margin_in, column_gap_in, column_width_in)

    with st.expander("Printing options"):
        duplex = st.radio(
            "Printer duplex setting",
            ["Flip on long edge", "Flip on short edge"],
            help="Must match your printer. The wrong one prints every other side "
                 "upside down. If a test signature comes out that way, switch this.",
        )
        flip_on_long_edge = duplex == "Flip on long edge"
        move_input = st.checkbox(
            "Move the input PDF to Previously_Converted when done", value=True
        )

if flash := st.session_state.pop("flash", None):
    st.success(flash)

# --------------------------------------------------------------------------
# Available for conversion
# --------------------------------------------------------------------------
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
        key=f"uploader-{upload_round}",
    )
    if uploaded:
        saved = []
        for item in uploaded:
            target = INPUT_DIR / item.name
            if not target.exists():
                target.write_bytes(item.getbuffer())
                saved.append(item.name)
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

                problems = layout_problems(layout, info.page_width_pt, unit)
                warnings = layout_warnings(layout, info.page_width_pt, unit)
                plans = plan_signatures(info.book_pages, sheets_per_signature)

                a, b, c = st.columns(3)
                a.metric("Source pages", info.source_pages)
                b.metric("Book pages", info.book_pages)
                c.metric("Signatures", len(plans))
                st.caption(
                    f"{human_size(pdf_path)} · page size "
                    f"{unit.label(info.page_width_pt / 72, 2)} × "
                    f"{unit.label(info.page_height_pt / 72, 2)} · "
                    f"{sum(p.sheets for p in plans)} sheets of paper"
                    + (f" · last signature is {plans[-1].sheets} sheet(s)"
                       if plans[-1].sheets != sheets_per_signature else "")
                )

                if not info.uniform_page_size:
                    st.warning(
                        "The pages of this PDF are not all the same size. The layout is "
                        "measured from the first page, so other pages may be cut wrong."
                    )
                for warning in warnings:
                    st.warning(warning)
                for problem in problems:
                    st.error(problem)

                if st.button(
                    "Create signatures", key=f"convert-{pdf_path.name}",
                    type="primary", disabled=bool(problems), use_container_width=True,
                ):
                    bar = st.progress(0.0, text="Starting…")
                    try:
                        signatures = convert_book(
                            pdf_path,
                            layout=layout,
                            sheets_per_signature=sheets_per_signature,
                            flip_on_long_edge=flip_on_long_edge,
                            move_input=move_input,
                            progress=lambda f, msg: bar.progress(min(f, 1.0), text=msg),
                        )
                    except Exception as error:
                        bar.empty()
                        st.error(f"Conversion failed: {error}")
                    else:
                        finish(
                            f"Created {len(signatures)} signatures for "
                            f"“{pdf_path.stem}”."
                        )

                number_button, move_button = st.columns(2)
                already_numbered = numbered_copy_path(pdf_path).exists()
                if number_button.button(
                    "Number the pages", key=f"number-{pdf_path.name}",
                    disabled=already_numbered or bool(problems),
                    use_container_width=True,
                    help="Only needed if this book's pages are not numbered already. "
                         "Stamps a page number at the bottom of each column and saves "
                         "it as a separate new book — this one is left untouched.",
                ):
                    bar = st.progress(0.0, text="Starting…")
                    try:
                        numbered = number_book(
                            pdf_path, layout,
                            progress=lambda f, msg: bar.progress(min(f, 1.0), text=msg),
                        )
                    except Exception as error:
                        bar.empty()
                        st.error(f"Numbering failed: {error}")
                    else:
                        finish(f"Wrote “{numbered.name}”. The original is still here.")

                if move_button.button(
                    "Move to Previously_Converted", key=f"archive-{pdf_path.name}",
                    use_container_width=True,
                ):
                    try:
                        move_book(pdf_path, PREVIOUS_DIR)
                    except Exception as error:
                        st.error(f"Could not move it: {error}")
                    else:
                        finish(f"Moved “{pdf_path.name}” to Previously_Converted.")

                if already_numbered:
                    st.caption(
                        f"Already numbered — see `{numbered_copy_path(pdf_path).name}`."
                    )

# --------------------------------------------------------------------------
# Previously converted
# --------------------------------------------------------------------------
with middle:
    st.header("Previously converted")
    folder_line("Input PDFs are moved here after conversion:", PREVIOUS_DIR)

    previous = list_previous_books()
    if not previous:
        st.info("Nothing here yet. Converted input PDFs are moved here automatically.")
    else:
        for pdf_path in previous:
            with st.container(border=True):
                st.markdown(f"**{pdf_path.name}**")
                st.caption(human_size(pdf_path))
                if st.button(
                    "Move back to Input", key=f"restore-{pdf_path.name}",
                    use_container_width=True,
                    help="Puts this book back in the conversion list, e.g. to redo it "
                         "with a different signature size.",
                ):
                    try:
                        move_book(pdf_path, INPUT_DIR)
                    except Exception as error:
                        st.error(f"Could not move it: {error}")
                    else:
                        finish(f"Moved “{pdf_path.name}” back to Input.")

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
                st.markdown(f"**{book.name}**")
                st.caption(f"{len(book.signatures)} signatures")
                st.code(str(book.signatures[0].parent), language=None)

                for signature in book.signatures:
                    row, button = st.columns([3, 1], vertical_alignment="center")
                    row.markdown(
                        f"`{signature.name}` · {human_size(signature)}"
                    )
                    button.download_button(
                        "Download",
                        data=signature.read_bytes(),
                        file_name=f"{book.name}_{signature.name}",
                        mime="application/pdf",
                        key=f"dl-{book.name}-{signature.name}",
                        use_container_width=True,
                    )

st.divider()
with st.expander("How to print and fold"):
    st.markdown(
        f"""
1. Print **one signature file at a time**, double-sided, at 100% scale
   (no "fit to page" — scaling breaks the margins).
2. Set your printer's duplex option to **{'long' if flip_on_long_edge else 'short'} edge**,
   matching the setting in the sidebar.
3. Fold **every sheet** of a signature in half, then **nest** them one inside the
   other. The first sheet printed is the outermost.
4. Page numbers should run in order through the folded signature. If every other
   page is upside down, switch the duplex setting and reprint.
"""
    )
