"""Turn 2-column PDF books into printable signature files.

Usable headless (`python main.py`) or behind the Streamlit GUI (`streamlit run app.py`).

The four folder names below are where the work happens. Behind the GUI they are
not fixed: `Script/workspace.py` re-points them at a folder made for one browser
session and destroyed when that session ends, so nothing a visitor uploads is
kept. Every function here reads them at call time, and none of them assumes the
folders are anywhere in particular.
"""

import argparse
import shutil
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from Script.paper_sizes import (
    INCHES,
    MILLIMETRES,
    book_page_table,
    describe_size,
    paper_size_table,
    parse_paper_size,
)
from Script.print_formatting import (
    ACTUAL_SIZE,
    FIT,
    PT_PER_INCH,
    SCALE_MODES,
    ColumnLayout,
    SheetFit,
    inspect_book,
    plan_signatures,
    sheet_problems,
    sheet_warnings,
    split_to_signatures,
    stamp_book,
)
from Script.typesetting import is_generated_book, render_manuscript

# Defaults for the headless CLI only, and made on demand rather than shipped:
# `python main.py` is somebody working on their own files on their own machine.
# The GUI never uses these. `Script/workspace.py` replaces all four before the
# first panel is drawn, with folders it will delete again when the browser goes
# away — so nothing anyone uploads through the web app is ever written here.
ROOT_DIR = Path(__file__).resolve().parent
INPUT_DIR = ROOT_DIR / "Input"
OUTPUT_DIR = ROOT_DIR / "Output"
PREVIOUS_DIR = ROOT_DIR / "Previously_Converted"
# Books being written in the GUI's editor, one JSON file per draft. Nothing in
# the conversion path reads this folder: a draft becomes an ordinary input PDF
# the moment it is built, and takes the same route as any other book from there.
MANUSCRIPT_DIR = ROOT_DIR / "Manuscripts"

DEFAULT_SHEETS_PER_SIGNATURE = 5
NUMBERED_SUFFIX = "_Numbered"
INSTRUCTIONS_NAME = "print_instructions.txt"

# What `--paper` accepts to mean "do not resize anything".
SAME_AS_SOURCE = "same"


@dataclass(frozen=True)
class ReadyBook:
    name: str
    folder: Path
    signatures: list


def _list_pdfs(folder):
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.pdf"))


def list_available_books():
    """Input PDFs waiting to be converted."""
    return _list_pdfs(INPUT_DIR)


def list_previous_books():
    """Input PDFs that have already been through a conversion."""
    return _list_pdfs(PREVIOUS_DIR)


def _signature_number(path):
    """The N in signature_N.pdf, or None if this is not one of our files.

    Anything else the user happens to have dropped in the folder is ignored
    rather than sorted. Reading its name as an integer used to raise, and the
    listing is built while the whole GUI is being drawn, so one stray file took
    the entire app down instead of being passed over.
    """
    tail = path.stem[len("signature_"):]
    return int(tail) if tail.isdigit() else None


def _signatures_in(folder):
    numbered = [
        (number, path)
        for path in folder.glob("signature_*.pdf")
        if (number := _signature_number(path)) is not None
    ]
    return [path for _number, path in sorted(numbered)]


def list_ready_books():
    """Books already converted, newest first."""
    if not OUTPUT_DIR.is_dir():
        return []
    ready = []
    for folder in sorted(OUTPUT_DIR.iterdir()):
        if not folder.is_dir():
            continue
        signatures = _signatures_in(folder / "book_signatures")
        if not signatures:
            continue
        ready.append(ReadyBook(name=folder.name, folder=folder, signatures=signatures))
    # Sorted on a stat taken once here, not inside the comparison: a folder that
    # disappears mid-sort would otherwise raise out of a listing whose whole job
    # is to describe what is currently there.
    stamped = []
    for book in ready:
        try:
            stamped.append((book.folder.stat().st_mtime, book))
        except OSError:
            continue
    stamped.sort(key=lambda pair: pair[0], reverse=True)
    return [book for _mtime, book in stamped]


def numbered_copy_path(pdf_path):
    """Where `number_book` would write the numbered copy of `pdf_path`."""
    pdf_path = Path(pdf_path)
    return pdf_path.with_name(f"{pdf_path.stem}{NUMBERED_SUFFIX}.pdf")


def number_book(pdf_path, layout=None, progress=None):
    """Write a page-numbered copy of `pdf_path` beside it, as a new book.

    `layout` None means "fit the columns to each page of this book", which is
    right for any page size. The original is left untouched, so a wrong layout
    costs nothing to undo: delete the copy and try again.
    """
    pdf_path = Path(pdf_path)
    target = numbered_copy_path(pdf_path)
    if target.exists():
        raise FileExistsError(f"“{target.name}” already exists.")
    return stamp_book(pdf_path, target, layout, progress=progress)


def move_book(pdf_path, destination_dir, overwrite=False):
    """Move one PDF between the Input and Previously_Converted folders."""
    pdf_path = Path(pdf_path)
    destination = Path(destination_dir) / pdf_path.name
    if destination.resolve() == pdf_path.resolve():
        return pdf_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not overwrite:
            raise FileExistsError(
                f"“{destination.name}” is already in {destination.parent.name}."
            )
        destination.unlink()
    shutil.move(str(pdf_path), str(destination))
    return destination


def _inside(path, *folders):
    """Resolve `path` and require it to sit *under* one of `folders`.

    Deleting is the one irreversible thing this app does, and `shutil.rmtree`
    aimed at the wrong place cannot be taken back. Both delete functions are
    handed a path that came from a listing of these folders, so this should never
    fire — which is exactly why it is cheap to keep: it costs one stat call and
    it turns "a bug somewhere upstream" into a refusal instead of a lost folder.
    A path equal to the folder itself is rejected too, so no caller can empty
    Output/ or Previously_Converted/ wholesale.
    """
    resolved = Path(path).resolve()
    for folder in folders:
        if Path(folder).resolve() in resolved.parents:
            return resolved
    allowed = ", ".join(str(Path(f)) for f in folders)
    raise ValueError(f"“{path}” is not inside {allowed}, so it will not be deleted.")


def delete_book(pdf_path):
    """Delete one PDF from Input/ or Previously_Converted/."""
    target = _inside(pdf_path, INPUT_DIR, PREVIOUS_DIR)
    if not target.is_file():
        raise FileNotFoundError(f"“{target.name}” is not there any more.")
    target.unlink()
    return target


def delete_ready_book(folder):
    """Delete one converted book's whole Output/ folder.

    The signature files and the print_instructions.txt that explains how to print
    them are only useful together, so they go together. The input PDF this was
    made from is a separate file in another folder and is left alone.
    """
    target = _inside(folder, OUTPUT_DIR)
    if not target.is_dir():
        raise FileNotFoundError(f"“{target.name}” is not there any more.")
    shutil.rmtree(target)
    return target


# --------------------------------------------------------------------------
# Books typed into the editor
# --------------------------------------------------------------------------


def book_pdf_path(file_name):
    """Where a typed book is built: an ordinary input PDF in Input/.

    Only the last component of the name is kept and only a `.pdf` is ever
    written, so whatever the editor's file-name box contains, the build lands
    in Input/ and nowhere else.
    """
    stem = Path(str(file_name)).name
    stem = stem[:-4] if stem.lower().endswith(".pdf") else stem
    stem = "".join(" " if character in '<>:"/\\|?*' else character
                   for character in stem)
    stem = " ".join(stem.split()).strip(" .")[:80].strip(" .")
    return INPUT_DIR / f"{stem or 'Untitled book'}.pdf"


def typeset_book(manuscript, file_name, progress=None, page_size_in=None):
    """Set one typed manuscript as an input PDF, ready to convert.

    `page_size_in` is the finished page size to set the book at, overriding the
    one in its design; the GUI passes half the sheet chosen on the writing tab,
    so that a book destined for particular paper is set at that size instead of
    being scaled on to it afterwards.

    Rebuilding a book after an edit is the normal loop, so a PDF this editor
    wrote is replaced without asking. A PDF that came from anywhere else is
    never replaced: the editor's file name defaults to the book's title, and a
    title that happens to match a book the user uploaded themselves would
    otherwise overwrite it with no warning at all.
    """
    target = book_pdf_path(file_name)
    if target.exists() and not is_generated_book(target):
        raise FileExistsError(
            f"“{target.name}” is already here, and this editor did not write "
            f"it. Choose a different file name so the book already there is "
            f"left alone."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    return render_manuscript(
        manuscript, target, progress=progress, page_size_in=page_size_in
    )


def printing_steps(flip_on_long_edge):
    """How to get a signature off the printer and folded, said once.

    Read twice: the GUI renders these as a numbered list under "How to print and
    fold", and `print_instructions` below strips the emphasis out of them for the
    note that ships beside the signatures. They used to be written out separately
    in each place — five steps in app.py, five differently-divided steps here —
    so a correction to one silently left the other saying something else.

    `**bold**` is the whole of the markup vocabulary, because the plain-text
    rendering removes it with a single replace. Nothing here may refer to the GUI
    by name: the same words go into a file the headless CLI writes.
    """
    edge = "long" if flip_on_long_edge else "short"
    return [
        'Print **one signature file at a time**, double-sided, at '
        '**100% / "Actual size"**. Never "fit to page": the pages are already '
        'the size of your paper, and letting the printer scale them again '
        'shrinks the margins and moves the fold off centre.',

        "Load the paper the book was made for, and check that the print dialog "
        "agrees about its size; some drivers quietly fall back to their own "
        "default.",

        f"Set your printer's duplex option to **flip on the {edge} edge** — the "
        f"setting this book was made for.",

        "Fold **every sheet** of a signature in half, then **nest** them one "
        "inside the other. The first sheet printed is the outermost.",

        "Page numbers should run in order through the folded signature. If "
        "every other page is upside down, switch the duplex setting and "
        "reprint.",
    ]


def print_instructions(book_name, fit, plans, flip_on_long_edge, unit=INCHES):
    """The note dropped next to the signatures, so the settings reach the printer.

    A signature file is useless without knowing what paper it wants and that it
    must not be scaled again on the way out, and that knowledge otherwise lives
    only in whichever GUI session created it.
    """
    sheet_w, sheet_h = fit.sheet_size_in
    page_w, page_h = fit.book_page_size_in
    sheets = sum(plan.sheets for plan in plans)
    lines = [
        f"{book_name}",
        f"Created {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Paper to load        {describe_size(sheet_w, sheet_h, unit)}",
        f"Folded book page     {unit.size_label(page_w, page_h)}",
        f"Scaling applied      {fit.scale * 100:.1f}% of the original",
        f"Signatures           {len(plans)} ({sheets} sheets of paper, "
        f"{sheets * 2} printed sides)",
        f"Sheets per signature {[plan.sheets for plan in plans]}",
        "",
        "How to print",
    ]
    # The same steps the GUI shows, with the emphasis taken out and wrapped to
    # the width of the rest of this note. `textwrap` rather than hand-broken
    # lines, so an edit to the wording in `printing_steps` cannot leave a ragged
    # paragraph behind here.
    for number, step in enumerate(printing_steps(flip_on_long_edge), 1):
        lines.append(textwrap.fill(
            step.replace("**", ""),
            width=76,
            initial_indent=f"  {number}. ",
            subsequent_indent="     ",
        ))
    return "\n".join(lines) + "\n"


def convert_book(
    pdf_path,
    layout=None,
    sheets_per_signature=DEFAULT_SHEETS_PER_SIGNATURE,
    flip_on_long_edge=True,
    sheet_size_pt=None,
    scale_mode=FIT,
    move_input=True,
    progress=None,
):
    """Convert one input PDF into per-signature print files.

    `sheet_size_pt` is the paper to print on, in points; None keeps the source
    page's own size, which is the case where nothing is scaled at all.

    Raises ValueError only when the book genuinely cannot be printed as asked:
    an unreadable or empty PDF, or a book kept at its original size that runs off
    the chosen sheet. Everything else proceeds.

    `layout` is accepted and ignored. Imposition splits every source page at its
    own midpoint, which is where the fold is by definition, so no column
    measurement can change the output — see `ColumnLayout`. It stays in the
    signature because the callers that impose also number, and numbering does
    read it.

    Page numbering is a separate, opt-in step (`number_book`), so what gets
    imposed here is exactly the PDF handed in.
    """
    pdf_path = Path(pdf_path)
    info = inspect_book(pdf_path)

    fit = SheetFit.compute(
        (info.page_width_pt, info.page_height_pt), sheet_size_pt, scale_mode
    )
    problems = sheet_problems(fit)
    if problems:
        raise ValueError("; ".join(problems))

    book_folder = OUTPUT_DIR / pdf_path.stem
    signature_folder = book_folder / "book_signatures"

    # The new signatures are built in a staging folder and swapped in only once
    # the whole set exists.
    #
    # Reconverting the same book on different paper is the normal way to use
    # this, and a smaller sheet or a bigger signature can produce fewer files
    # than last time, so last run's surplus has to go. Deleting it up front and
    # writing over the top is what made that dangerous: a conversion that failed
    # or was interrupted half way left a folder that looks finished but holds a
    # mixture of two different impositions — pages that would be printed, folded
    # and bound before anyone noticed. Staging means a failure leaves the
    # previous, complete set exactly as it was.
    staging = book_folder / "_new_signatures"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        written = split_to_signatures(
            pdf_path,
            sheets_per_signature,
            staging,
            flip_on_long_edge=flip_on_long_edge,
            sheet_size_pt=sheet_size_pt,
            scale_mode=scale_mode,
            progress=progress,
        )
        if signature_folder.exists():
            shutil.rmtree(signature_folder)
        staging.rename(signature_folder)
    except BaseException:
        # BaseException, not Exception: a rerun or a stop raised through this by
        # the GUI must not be the one path that leaves a half-written folder
        # behind, which is the entire point of staging.
        shutil.rmtree(staging, ignore_errors=True)
        raise

    signatures = [signature_folder / path.name for path in written]

    plans = plan_signatures(info.book_pages, sheets_per_signature)
    # utf-8-sig, not plain utf-8: this file is meant to be opened by hand next to
    # a printer, and without a BOM the older Windows text tools read the "×" in a
    # page size as mojibake.
    (book_folder / INSTRUCTIONS_NAME).write_text(
        print_instructions(pdf_path.stem, fit, plans, flip_on_long_edge),
        encoding="utf-8-sig",
    )

    if move_input:
        move_book(pdf_path, PREVIOUS_DIR, overwrite=True)

    if progress:
        progress(1.0, f"Done: {len(signatures)} signatures")
    return signatures


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def _print_size_tables():
    """The reference the GUI shows in an expander, for people who never open it."""
    print("\nSheets you can print on  (--paper <name>, or --paper 12x9in)\n")
    print(f"  {'Name':<14} {'Sheet':<16} {'Folded book page':<22} Notes")
    for _family, name, sheet, folded, note in paper_size_table(unit=MILLIMETRES):
        print(f"  {name:<14} {sheet:<16} {folded:<22} {note}")
    print("\nCommon finished book sizes, and the sheet each one needs\n")
    print(f"  {'Name':<22} {'Book page':<14} {'Sheet needed':<16} Nearest stock")
    for _family, name, trim, needed, stock, _note in book_page_table(unit=MILLIMETRES):
        print(f"  {name:<22} {trim:<14} {needed:<16} {stock}")
    print(
        "\n  A sheet is folded across its width, so a book page is half a sheet wide\n"
        "  and a full sheet tall. 'Nearest stock' is the smallest standard paper the\n"
        "  needed sheet fits on; print on that and trim the book down afterwards.\n"
    )


def _resolve_sheet_size(paper, landscape):
    """Turn the --paper argument into a (width, height) in points, or None."""
    if paper is None or str(paper).strip().casefold() == SAME_AS_SOURCE:
        return None
    width_in, height_in = parse_paper_size(paper, landscape=landscape)
    return (width_in * PT_PER_INCH, height_in * PT_PER_INCH)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sheets", type=int, default=DEFAULT_SHEETS_PER_SIGNATURE,
        help="sheets of paper per signature (each sheet holds 4 book pages)",
    )
    parser.add_argument(
        "--margin", type=float, default=ColumnLayout.page_margin_in,
        help="outer page margin of the input PDF, in inches. Only affects where "
             "--number stamps page numbers",
    )
    parser.add_argument(
        "--gap", type=float, default=ColumnLayout.column_gap_in,
        help="gutter between the two columns of the input PDF, in inches. Only "
             "affects where --number stamps page numbers",
    )
    parser.add_argument(
        "--column-width", type=float, default=None,
        help="column width of the input PDF, in inches. The default is to widen "
             "the columns to fill each book's own page, keeping --margin and "
             "--gap, which suits any page size; give a number to override that",
    )
    parser.add_argument(
        "--fit-columns", action="store_true",
        help="the default, kept for compatibility: ignore --column-width and fit "
             "the columns to each book's own page",
    )
    parser.add_argument(
        "--paper", default=SAME_AS_SOURCE, metavar="SIZE",
        help=f"paper to print on: “{SAME_AS_SOURCE}” (default) keeps the input PDF's "
             f"own page size, or give a name such as “A4”, “Letter”, “Tabloid”, or "
             f"explicit dimensions such as “12x9in” or “420x297mm”. See --list-paper",
    )
    parser.add_argument(
        "--portrait", action="store_true",
        help="use the named --paper size with its short edge horizontal. Rarely what "
             "you want: a sheet is folded across its width, so a portrait sheet gives "
             "tall, narrow book pages",
    )
    parser.add_argument(
        "--scale", choices=SCALE_MODES, default=FIT,
        help=f"how to place the book on a differently sized sheet: “{FIT}” (default) "
             f"scales each book page to fill the sheet, “{ACTUAL_SIZE}” keeps the "
             f"original size and centres it",
    )
    parser.add_argument(
        "--list-paper", action="store_true",
        help="print the paper and book size tables and exit",
    )
    parser.add_argument(
        "--number", action="store_true",
        help="instead of converting, write a page-numbered copy of each input book "
             "into Input/ as <name>_Numbered.pdf, for books that are not numbered yet",
    )
    parser.add_argument(
        "--short-edge", action="store_true",
        help="impose for short-edge duplex instead of long-edge",
    )
    args = parser.parse_args(argv)

    if args.list_paper:
        _print_size_tables()
        return

    try:
        sheet_size_pt = _resolve_sheet_size(args.paper, landscape=not args.portrait)
    except ValueError as error:
        parser.error(str(error))

    books = list_available_books()
    if not books:
        print(f"No PDFs found in {INPUT_DIR}")
        return

    if args.number:
        for pdf_path in books:
            if pdf_path.stem.endswith(NUMBERED_SUFFIX):
                continue
            print(f"\nNumbering {pdf_path.name} ...")
            try:
                # None means "fit the columns to each page", which stays right
                # through a book whose pages are not all the same size; a
                # hand-given --column-width is carried through as it stands.
                layout = (
                    ColumnLayout(args.margin, args.gap, args.column_width)
                    if args.column_width is not None and not args.fit_columns
                    else None
                )
                numbered = number_book(
                    pdf_path, layout,
                    progress=lambda f, msg: print(f"  [{f:>5.0%}] {msg}", end="\r"),
                )
            except (FileExistsError, ValueError) as error:
                print(f"  Skipped: {error}")
            else:
                print(f"\n  Wrote {numbered}")
        return

    for pdf_path in books:
        print(f"\nConverting {pdf_path.name} ...")
        info = inspect_book(pdf_path)
        try:
            layout = _layout_for(info, args)
            fit = SheetFit.compute(
                (info.page_width_pt, info.page_height_pt), sheet_size_pt, args.scale
            )
        except ValueError as error:
            print(f"  Skipped: {error}")
            continue

        print(f"  source page {describe_size(*info.page_size_in, INCHES)}"
              + (" (pages carry a /Rotate)" if info.rotated_pages else ""))

        # Say no before describing a job that will not happen. The column layout
        # is not consulted: imposition never reads it, so it cannot make a
        # conversion wrong.
        refusals = sheet_problems(fit)
        if refusals:
            for refusal in refusals:
                print(f"  Skipped: {refusal}")
            continue

        plans = plan_signatures(info.book_pages, args.sheets)
        print(f"  {info.source_pages} source pages -> {info.book_pages} book pages "
              f"-> {len(plans)} signatures")
        print(f"  printing on {describe_size(*fit.sheet_size_in, INCHES)} at "
              f"{fit.scale * 100:.1f}%, folding to book pages of "
              f"{INCHES.size_label(*fit.book_page_size_in)}")
        for warning in sheet_warnings(fit, layout):
            print(f"  ! {warning}")

        try:
            signatures = convert_book(
                pdf_path,
                layout=layout,
                sheets_per_signature=args.sheets,
                flip_on_long_edge=not args.short_edge,
                sheet_size_pt=sheet_size_pt,
                scale_mode=args.scale,
                progress=lambda f, msg: print(f"  [{f:>5.0%}] {msg}", end="\r"),
            )
        except ValueError as error:
            print(f"  Skipped: {error}")
            continue
        print(f"\n  Wrote {len(signatures)} files to {signatures[0].parent}")
        print(f"  Printing notes in {signatures[0].parent.parent / INSTRUCTIONS_NAME}")


def _layout_for(info, args):
    """The column layout for one book: fitted to its own page unless overridden.

    Fitting is the default because the alternative — one fixed column width for
    every book — is only ever right for the one page size it was measured on.
    """
    if args.column_width is not None and not args.fit_columns:
        return ColumnLayout(args.margin, args.gap, args.column_width)
    return ColumnLayout.fitted(
        info.page_width_pt / PT_PER_INCH, args.margin, args.gap
    )


if __name__ == "__main__":
    main()
