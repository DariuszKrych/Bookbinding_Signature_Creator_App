# Bookbinding_Signature_Creator

Takes an input of a 2 column PDF book file and shuffles the pages into separate,
ready-to-print signature files. Books that are not numbered already can be numbered
first, as a separate step.

I wanted to create signatures for 12 separate books for some bookbinding hobby stuff and it
seemed like more fun to write this than to do it manually and it saved me time. XD

## Requirements

Python 3.12 with `pypdf`, `reportlab` and `streamlit`.

```
conda activate bookbinding
```

## Usage

Put a 2-column PDF into `Input/`, then either:

**GUI**

```
streamlit run app.py
```

The sidebar sets the units (inches or centimetres), the sheets per signature, the outer page
margin, the column width and the gap between the columns. Three panels show the `Input/`
folder (available for conversion), the `Previously_Converted/` folder and the `Output/`
folder (ready to print).

**Command line**

```
python main.py --sheets 5 --margin 0.5 --column-width 4.85 --gap 0.99
```

CLI measurements are always in inches.

Each converted book lands in `Output/<book name>/`, with one PDF per signature in
`book_signatures/`. The input PDF is moved to `Previously_Converted/` afterwards; the
"Move back to Input" button in the GUI puts it back to reconvert it with different
settings.

## Page numbering

Imposition never touches the book's own content, so a book that already has printed page
numbers just works. For one that does not, **Number the pages** (GUI) or `python main.py
--number` (CLI) stamps a number at the bottom of each column and saves the result as a new
book, `<name>_Numbered.pdf`, next to the original. The original is left alone, so a wrong
column layout costs nothing — delete the copy and redo it. Convert whichever of the two you
want signatures from.

## How the imposition works

Three different things get called a "page", so to be precise:

| Term | Meaning |
| --- | --- |
| source page | One page of the input PDF. Holds two book pages side by side. |
| book page | One page as the reader sees it. One column of a source page. |
| sheet | One physical piece of paper. Printed on both sides and folded once, it carries **4 book pages**. |
| side | One face of a sheet, i.e. one page of the output PDF. |

A signature of *N* sheets therefore has *2N* sides and *4N* book pages. Print a signature
double-sided, fold every sheet in half, and nest them one inside the other — the first
sheet printed is the outermost.

The fold runs down the middle of the source page, so the gap between the two columns has to
straddle that midpoint. The app checks this against the actual page size and refuses to
convert if a column would be folded through.

Books rarely divide evenly into signatures. The last signature shrinks to the fewest whole
sheets that hold what is left, and any unused pages fall at the **back of the book**, not in
the middle of the final gathering.

### Duplex setting

The source pages are landscape, so the default assumes **long-edge** duplex and rotates the
back of each sheet 180° to compensate. If a test signature comes out with every other page
upside down, switch to short-edge (`--short-edge`, or the sidebar option).

## Tests

```
python -m unittest Script.test_imposition -v
```

The imposition tests derive the correct sheet layout by simulating the physical fold
independently, rather than reusing the production formula.
