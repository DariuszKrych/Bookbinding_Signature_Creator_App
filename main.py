"""Convert 2-column PDF books in Input/ into printable signatures in Output/.

Usable headless (`python main.py`) or behind the Streamlit GUI (`streamlit run app.py`).
"""

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path

from Script.print_formatting import (
    ColumnLayout,
    inspect_book,
    layout_problems,
    plan_signatures,
    split_to_signatures,
    stamp_book,
)

ROOT_DIR = Path(__file__).resolve().parent
INPUT_DIR = ROOT_DIR / "Input"
OUTPUT_DIR = ROOT_DIR / "Output"
PREVIOUS_DIR = ROOT_DIR / "Previously_Converted"

DEFAULT_SHEETS_PER_SIGNATURE = 5
NUMBERED_SUFFIX = "_Numbered"


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


def list_ready_books():
    """Books already converted, newest first."""
    if not OUTPUT_DIR.is_dir():
        return []
    ready = []
    for folder in OUTPUT_DIR.iterdir():
        if not folder.is_dir():
            continue
        signatures = sorted(
            (folder / "book_signatures").glob("signature_*.pdf"),
            key=lambda p: int(p.stem.split("_")[-1]),
        )
        if not signatures:
            continue
        ready.append(ReadyBook(name=folder.name, folder=folder, signatures=signatures))
    ready.sort(key=lambda b: b.folder.stat().st_mtime, reverse=True)
    return ready


def numbered_copy_path(pdf_path):
    """Where `number_book` would write the numbered copy of `pdf_path`."""
    pdf_path = Path(pdf_path)
    return pdf_path.with_name(f"{pdf_path.stem}{NUMBERED_SUFFIX}.pdf")


def number_book(pdf_path, layout=None, progress=None):
    """Write a page-numbered copy of `pdf_path` beside it, as a new book.

    The original is left untouched, so a wrong layout costs nothing to undo:
    delete the copy and try again.
    """
    pdf_path = Path(pdf_path)
    target = numbered_copy_path(pdf_path)
    if target.exists():
        raise FileExistsError(f"“{target.name}” already exists.")
    return stamp_book(pdf_path, target, layout or ColumnLayout(), progress=progress)


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


def convert_book(
    pdf_path,
    layout=None,
    sheets_per_signature=DEFAULT_SHEETS_PER_SIGNATURE,
    flip_on_long_edge=True,
    move_input=True,
    progress=None,
):
    """Convert one input PDF into per-signature print files.

    Raises ValueError if the column layout does not fit the book, rather than
    quietly printing sheets that fold through a column of text.

    Page numbering is a separate, opt-in step (`number_book`), so what gets
    imposed here is exactly the PDF handed in.
    """
    pdf_path = Path(pdf_path)
    layout = layout or ColumnLayout()
    info = inspect_book(pdf_path)

    problems = layout_problems(layout, info.page_width_pt)
    if problems:
        raise ValueError("; ".join(problems))

    signature_folder = OUTPUT_DIR / pdf_path.stem / "book_signatures"
    signature_folder.mkdir(parents=True, exist_ok=True)

    signatures = split_to_signatures(
        pdf_path,
        sheets_per_signature,
        signature_folder,
        flip_on_long_edge=flip_on_long_edge,
        progress=progress,
    )

    if move_input:
        move_book(pdf_path, PREVIOUS_DIR, overwrite=True)

    if progress:
        progress(1.0, f"Done: {len(signatures)} signatures")
    return signatures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sheets", type=int, default=DEFAULT_SHEETS_PER_SIGNATURE,
        help="sheets of paper per signature (each sheet holds 4 book pages)",
    )
    parser.add_argument("--margin", type=float, default=ColumnLayout.page_margin_in)
    parser.add_argument("--gap", type=float, default=ColumnLayout.column_gap_in)
    parser.add_argument("--column-width", type=float, default=ColumnLayout.column_width_in)
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

    layout = ColumnLayout(args.margin, args.gap, args.column_width)
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
                numbered = number_book(
                    pdf_path, layout,
                    progress=lambda f, msg: print(f"  [{f:>5.0%}] {msg}", end="\r"),
                )
            except FileExistsError as error:
                print(f"  Skipped: {error}")
            else:
                print(f"\n  Wrote {numbered}")
        return

    for pdf_path in books:
        print(f"\nConverting {pdf_path.name} ...")
        info = inspect_book(pdf_path)
        plans = plan_signatures(info.book_pages, args.sheets)
        print(f"  {info.source_pages} source pages -> {info.book_pages} book pages "
              f"-> {len(plans)} signatures")
        signatures = convert_book(
            pdf_path,
            layout=layout,
            sheets_per_signature=args.sheets,
            flip_on_long_edge=not args.short_edge,
            progress=lambda f, msg: print(f"  [{f:>5.0%}] {msg}", end="\r"),
        )
        print(f"\n  Wrote {len(signatures)} files to {signatures[0].parent}")


if __name__ == "__main__":
    main()
