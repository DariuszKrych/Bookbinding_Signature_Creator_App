"""Turning the book on screen into the file the writer walks away with.

The writing screen offers three ways out — as JSON, as a PDF, as a zip of
signatures — and all three are `st.download_button`s. Streamlit calls a download
button's `data` when the button is *clicked*, so the two that have real work to
do can do it there: one click typesets the book, or typesets and imposes it, and
hands the bytes straight to the browser. Nothing is created and then left
somewhere for the user to go and find.

That callable runs **outside a script run**, on whichever thread served the
request, and everything in this module is shaped by it:

* **No path is read off `main`.** Its four folder names are process-wide, and
  every session's run re-points them at its own workspace, so a build reading
  them from another thread could write into somebody else's session. Every path
  used here is passed in.
* **Nothing touches session state and nothing draws.** There is no script run to
  draw into, so no progress bar and no `st.` anything. A failure comes back to
  the browser as a failed download rather than as a banner on the page, which is
  the price of the single click.
* **Nothing is left behind.** Each build works in a folder of its own under the
  session's scratch space and removes it again, so the only copy that outlives
  the click is the one in the visitor's downloads folder.

The imposition and typesetting themselves are not re-implemented here: this is
`main.convert_book` and `Script.typesetting.render_manuscript`, given somewhere
private to write.
"""

import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

import main
from Script.print_formatting import FIT
from Script.typesetting import render_manuscript
from Script.workspace import pack_folder


@contextmanager
def _workroom(root):
    """A private folder under `root`, removed however this ends.

    Named with a fresh uuid rather than after the book, so two clicks on the
    same button — or the same book downloaded twice at once — cannot be building
    over each other's files. Removed in a `finally`, so a build that raises
    leaves nothing; and `root` is under the session's own folder, so even a
    process that dies mid-build has the sweeper behind it.
    """
    room = Path(root) / uuid.uuid4().hex
    room.mkdir(parents=True, exist_ok=True)
    try:
        yield room
    finally:
        shutil.rmtree(room, ignore_errors=True)


def pdf_bytes(manuscript, file_name, page_size_in, root):
    """Typeset `manuscript` and hand back the PDF, as bytes.

    `page_size_in` is the finished page size to set the book at — half the sheet
    the writing screen chose — so the type is drawn at the size it will print
    at and nothing is scaled afterwards.
    """
    with _workroom(root) as room:
        target = room / f"{main.safe_stem(file_name)}.pdf"
        render_manuscript(manuscript, target, page_size_in=page_size_in)
        return target.read_bytes()


def signature_bytes(
    manuscript,
    file_name,
    page_size_in,
    root,
    sheet_size_pt=None,
    sheets_per_signature=main.DEFAULT_SHEETS_PER_SIGNATURE,
    flip_on_long_edge=True,
):
    """Typeset `manuscript`, impose it, and hand back one zip of signatures.

    The zip holds what the conversion screen's own download holds: the numbered
    signature files in print order under `book_signatures/`, and beside them the
    `print_instructions.txt` recording the paper, the scaling and the duplex
    setting they were made for. A signature file is not much use without that
    note, and it is the only place those settings are written down once the tab
    is closed.

    `move_input=False`: there is no archive here to move the PDF into, and it is
    about to be deleted with the rest of the workroom either way.
    """
    stem = main.safe_stem(file_name)
    with _workroom(root) as room:
        source = room / f"{stem}.pdf"
        render_manuscript(manuscript, source, page_size_in=page_size_in)
        output = room / "Output"
        main.convert_book(
            source,
            sheets_per_signature=sheets_per_signature,
            flip_on_long_edge=flip_on_long_edge,
            sheet_size_pt=sheet_size_pt,
            # The book was set at the size of the sheet, so there is nothing to
            # fit and the mode only shows at the extremes. Said explicitly
            # rather than defaulted, because it is the one the writing screen
            # offers no choice about.
            scale_mode=FIT,
            move_input=False,
            output_dir=output,
        )
        return pack_folder(output / stem, stem)
