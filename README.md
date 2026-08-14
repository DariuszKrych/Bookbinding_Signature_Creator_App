# Bookbinding_Signature_Creator

Takes a 2-column PDF book and shuffles the pages into separate, ready-to-print
signature files, on **A4 or any other paper you can feed a printer**. Books that
are not numbered already can be numbered first, as a separate step.

There is also a **book editor**: type a book into text boxes (title, author,
dedication, chapters, appendix) and it is typeset into exactly that kind of 2-column
PDF and folded into signatures, without ever leaving the app. Drafts are saved as
plain JSON files so a half-written book survives closing the window.

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

Two tabs, chosen at the top of the page: **📚 Convert 2 Column Formatted PDF into PDF
Signatures** and **✍️ Convert Inputted Text into PDF Signatures**.

**The sidebar is only what both tabs share**, and it is four controls: the units (inches,
centimetres or millimetres), the sheets per signature, the printer's duplex setting, and
whether the PDF is moved to the archive once it has been converted. They hold their values
when you switch tabs and mean the same thing on either one.

**Everything about the paper is set on the tab that decides it.** The sheet an existing PDF
is printed on and the size a book being typed is set at are two different questions whose
answers are not interchangeable, so each is asked in one place only:

- The conversion tab has **🖨️ Paper to print on** above the three panels: the sheet size
  (or a custom one), its orientation, and what to do when the book is not that size. Folded
  away underneath is the **column layout**, which only places stamped page numbers on a PDF
  somebody else made and cannot change a conversion at all.
- The writing tab asks it once, in **📐 Book design**, under *Page size and margins*: give
  the size as **the finished page** or as **the paper it prints on**, whichever you actually
  care about, and the other is worked out and printed underneath. Only the menu you chose is
  on screen. See [Writing a book in the app](#writing-a-book-in-the-app).

*If the book is a different size* is asked on the conversion tab only. It has no second
answer while you are typing a book, which is **set** at the size you asked for, so nothing
is ever scaled. Each tab's paper settings are kept while the other tab is up, so a trip
across and back changes nothing.

In the conversion view, three panels show the `Input/` folder (available for conversion),
the `Previously_Converted/` folder (the archive) and the `Output/` folder (ready to print).

Each book waiting to be converted names the two sizes you act on, the **paper to load in
the printer** and **each page of the finished book**, so there is no guessing which number
describes what. A finished book gets one **Open file location** button that opens its
signature folder, rather than a download button per signature: they are printed as a set, in
order, from where they already are.

Only one job runs at a time, and the page is drawn before it starts. Claiming a job reruns
the script immediately, so the whole interface (all three panels, the paper settings above
them *and* the sidebar) is painted locked, and only then does the first page get imposed.
Nothing on screen is clickable while the work happens, and the progress bar takes over the
slot its button was in, so no part of the page moves. Both halves matter: Streamlit restarts
the script whenever a widget changes, which means a click landing mid-conversion does not
just redraw the page and hand the job different numbers than the ones on screen; it stops
the conversion where it stands.

Two of the panels can throw things away. **🗑 Delete** in the archive removes an input PDF
for good, and in Ready to print it removes one book's signature files and its printing notes.
Deleting is the only thing here that cannot be undone, so it always asks first, with two
buttons, **Yes, delete** and **Keep it**, under the card naming the file.

The interface ships a light-green paper-and-foliage theme in `.streamlit/config.toml`, in a
light and a dark version; the **⋮** menu, top right, switches between them.

### The top-right corner

Streamlit's own developer controls are stripped out, because this is a finished local tool
rather than an app someone is building, and two of them are actively dangerous here.
**Stop** and **Rerun** both cut a conversion off mid-write, which is the one thing the
page-drawing order above is arranged to prevent.

`client.toolbarMode = "minimal"` in `.streamlit/config.toml` removes **Deploy**, **Rerun**,
**Auto rerun**, **Clear cache**, **Print** and **Record screen**, and disables the `C`
clear-cache keyboard shortcut. What is left in the **⋮** menu is the System / Light / Dark
switcher, an **About** entry, and the *Made with Streamlit* line. In minimal mode Streamlit
hides the whole header unless the app defines a menu item of its own, so the About entry in
`st.set_page_config` is what keeps the theme switcher reachable; remove it and the corner
goes blank. `server.fileWatcherType = "none"` drops the source-file watcher and with it the
"File change. Rerun / Always rerun" prompt; set it back to `"auto"` when working on the app
itself.

Two things no config option reaches are handled by a style block at the top of `app.py`: the
**Stop** button and the running figure beside it, and the "Is Streamlit still running? …
`streamlit run yourscript.py`" dialog that appears a few seconds after the server goes away.
That dialog is hidden only while the connection is down, so ordinary dialogs still work.
Telling someone who started the app from a shortcut to retype a shell command is worse than
saying nothing.

One Streamlit reflex survives all of this: pressing **R** outside a text field reruns the
script, and doing that during a conversion aborts it. Nothing short of injected JavaScript
turns that off. The staging folder means an aborted run still leaves the previous, complete
set of signatures intact.

**Command line**

```
python main.py --sheets 5 --paper A4
python main.py --sheets 5 --paper Letter
python main.py --list-paper
```

`--margin`, `--gap` and `--column-width` are in inches; `--paper` carries its own unit.

Each converted book lands in `Output/<book name>/`, with one PDF per signature in
`book_signatures/` and a `print_instructions.txt` recording the paper size, the scaling and
the duplex setting it was made with. The input PDF is moved to `Previously_Converted/`
afterwards; the "Move back to Input" button in the archive panel puts it back to reconvert
it with different settings. Reconverting builds the new signatures in a staging folder and
swaps them in only once the whole set exists, so the two runs can never be mixed and a
conversion that fails leaves the previous, complete set exactly as it was. Deleting is
confined to those folders: both delete functions resolve the path first and refuse anything
that is not *inside* `Input/`, `Previously_Converted/` or `Output/`, including the folders
themselves.

## Paper sizes

**A sheet is folded across its width.** One sheet of *W × H* paper gives four book pages of
*W/2 × H*. That single fact is what everything below follows from: A4 landscape folds to A5
pages, Letter landscape folds to Half Letter pages, and a 6 × 9 in book needs 12 × 9 in
paper.

By default the output sheet is **the input PDF's own page size**, so nothing is scaled and a
book laid out for A4 landscape prints exactly as drawn. Choose a sheet size when your PDF
was made for paper you do not have, when you want a smaller or larger book than the PDF was
drawn for, or when you want to print on big paper and trim.

### Sheets you can print on

`--paper <name>`, or the **Sheet size** menu on the conversion tab. 21 sizes:

| Family | Sizes |
| --- | --- |
| ISO A | A2, A3, A4, A5, A6 |
| ISO B | B3, B4, B5, B6 |
| JIS B | JIS B4, JIS B5, JIS B6 |
| North American | Letter, Legal, Half Letter, Executive, Tabloid, ANSI C |
| Oversize | SRA4, SRA3, Super B (A3+) |

Anything else goes in as dimensions: `--paper 12x9in`, `--paper 297x420mm`,
`--paper 30x21cm`, or the **Custom size…** entry in that same menu. Named sizes are used
landscape by default, which is nearly always what you want; `--portrait` (or the **Sheet
orientation** control beside the menu) turns one on its side, and the app warns you that the
result is a tall, narrow book page.

### Book sizes

`python main.py --list-paper` also prints the finished sizes a bound book is normally
trimmed to: mass-market paperback, A6 pocket, A-format, Novella, Digest, B-format, A5, US
trade, Demy and Royal octavo, comic, Crown quarto, Letter and A4, with the sheet each one
needs and the smallest standard stock that sheet fits on. The same tables are in the GUI
under **Paper and book size reference**.

### Fitting

This is about a PDF that already exists, i.e. one you dropped into `Input/`. A book typed
into the editor is set for its paper in the first place, so it never gets here.

When the sheet is not the same size as the input page, each book page is scaled and centred
in its half of the sheet:

- **Fit each book page to the sheet** (default) scales it up or down until it fills the
  paper, keeping its proportions. If the sheet is a different *shape*, what is left over
  appears as extra blank margin, and the app says how much.
- **Keep the original size, centred** never resizes anything, and refuses the job outright
  if the book will not fit the paper. Use it when the margins matter more than filling
  the sheet.

The scale factor is reported before conversion and written into `print_instructions.txt`.
Print at **100% / "Actual size"**: the pages are already the size of your paper, and letting
the printer scale them again compounds the two. The app also warns when shrinking has pushed
the outer margin inside the ~0.25 in that most printers cannot print into.

The fit is worked out per source page, so a PDF whose pages are not all the same size still
comes out on uniform sheets.

### Column layout

The column measurements describe the **input** PDF, not the paper, and they are used for
exactly one thing: placing stamped page numbers. **Imposition never reads them.** The fold
is the middle of the source page by definition, so each page is always split at its own
midpoint: no column measurement can move it, and none is ever a reason to refuse a book.

By default the columns are **fitted to each book's own page**, keeping the margin and gap,
so any page size works with no setup. Untick **Fit the columns to each PDF** under 🖨️ Paper
to print on (or pass `--column-width`) to set the width by hand; each book card then offers
a **Fit the columns to this PDF** button to put it back. Hand-entered measurements that do
not match the book are reported, but only as notes about where the page numbers would land.

Pages that carry a `/Rotate` flag, a crop box smaller than the paper, or a media box that
does not start at the origin are all handled as a PDF reader displays them. So are books
whose pages are not all the same size: each page is split at its own midpoint and fitted to
the sheet separately, so the printed paper stays uniform.

## Writing a book in the app

**✍️ Convert Inputted Text into PDF Signatures** at the top of the page opens an editor
that produces the same kind of 2-column PDF the converter reads. Nothing about the
imposition is special-cased for it: the editor writes an ordinary PDF into `Input/`, and
from that point a typed book is indistinguishable from one that came from anywhere else.

**Nothing is ever scaled here, at any paper size.** Words have no size until the type is
drawn, so the paper is not something to fit a finished book on to afterwards; it is the size
the book is *set* at, half a sheet to a page. Pick any paper you own and the type goes into
the PDF at its final size, at 100%, with no sharpness spent. Changing the size changes how
big the finished book is and how many pages it runs to, and nothing else.

**One menu, either way round.** *Page size and margins* in 📐 Book design starts with **Give
the size as**: *The finished page* offers the book sizes in the reference table plus a custom
one, and *The paper it prints on* offers the sheets instead. Whichever you pick, the other
figure is worked out and printed under the menu, and the menu you did not pick is not on the
page at all. Giving the size as paper belongs to that build and never reaches the saved
draft, which keeps the page size that was typed into it.

### What you can type

| Part | Sections available |
| --- | --- |
| Title page | title, subtitle, author, series, and a copyright page built from publisher, year, edition, ISBN, a copyright line and any other rights text |
| Front matter | dedication, epigraph, foreword, preface, acknowledgements, introduction, prologue, a note to the reader, or a section of your own |
| The book | chapters, part dividers, interludes, unnumbered sections |
| Back matter | epilogue, afterword, author's note, acknowledgements, appendix, glossary, notes, further reading, bibliography, about the author, also by the author, colophon, or your own |

Every section is a heading and a text box, and can be moved up or down, duplicated or
removed; a removal can be undone with one click. Only sections added as **Chapter** join
the numbering, so a prologue or an appendix never becomes "Chapter 4".

### How to type the text

Plainly. Six things mean something: a blank line starts a paragraph, `*stars*` or
`_underscores_` are italic, `**two stars**` are bold, `# a leading hash` is a heading
inside the section (`##` for a smaller one), a line of `***` or `---` is a scene break,
and lines starting with `>` are a quotation that keeps its line breaks. A single line
break inside a paragraph is treated as wrapping, exactly as it is in any other text box.

### The book it builds

You choose the **finished page size** (any of the book sizes in the reference table, or a
custom one), the four margins, the typeface and size, the line spacing, whether the text
is justified and how paragraphs are marked. The PDF it writes has pages **twice as wide**
as the finished page, because two book pages sit side by side on every sheet, so an A5
book comes out as an A4-landscape PDF that prints on A4 with nothing scaled at all.

It sets a title page, a copyright page, a table of contents with the page each section
actually starts on, running heads, and page numbers centred at the foot, all of which can
be switched off. Sections start on a new page by default, or on a right-hand page like a
printed book, which looks right and costs paper.

The copyright page is printed only once one of the **Publication details** (publisher,
year, edition, ISBN, a copyright line, or any other rights text) is filled in. Naming an
author is not enough: that box is on the title page, every book fills it in, and deriving
a copyright page from it put a whole extra leaf, reading "Copyright © A. Binder" and
nothing else, into every book without anyone asking for one.

Five typefaces are available: Times, Helvetica, Courier, Bitstream Vera, and the app's own
Baskervville, which comes in one weight, so bold and italic are set in the regular face.
Characters no typeface here can set (Greek, Cyrillic, CJK) are reported rather than
silently dropped.

### Keeping your progress

Drafts live in `Manuscripts/`, one JSON file each, named after the draft. Save as many as
you like and switch between them; **Autosave** keeps writing to the open draft as you type
once it has been saved once. Saving goes through a temporary file and a rename, so a crash
part way cannot leave a half-written draft on top of the one it replaced. Anything that
would throw away unsaved words asks first.

A draft is a plain JSON file, so carrying a book to another machine is copying a file:
**📂 Open file location** opens the folder they are in. **Load** on any saved draft puts
it in the editor. **✨ Load the example** fills the editor with a complete book (five
chapters, a part divider, an interlude, a dedication, an epigraph and a spread of back
matter, in lorem ipsum) as an unsaved draft that can be typed straight over.

Building writes `Input/<name>.pdf`. Building again replaces the PDF **the editor wrote**;
a PDF you put in that folder yourself is never overwritten, whatever the book is called,
because the editor stamps its own name into the PDF's `/Creator` and checks for it first.
**✂️ Create the signatures** typesets and imposes in one go, using the size set in 📐 Book
design and the folding settings in the sidebar.

**📖 Build the book** names the same two sizes the conversion tab puts on every book
card, the **paper to load in the printer** and **each page of the finished book**, worked
out from *Page size and margins* in 📐 Book design, before anything is built. Every paper
size in the menu can be built on, because the book is set at half of whatever sheet you
pick; on the rare sheet a book could not physically go on, **✂️ Create the signatures** is
disabled and says why, exactly as it would on a card in the other view, while **📄 Create
the book PDF** stays available because that half would have worked. When a run finishes,
the note under the buttons gives the folder the signature files are in, since this view has
no *Ready to print* panel of its own.

## Page numbering

Imposition never touches the book's own content, so a book that already has printed page
numbers just works. For one that does not, **Number the pages** (GUI) or `python main.py
--number` (CLI) stamps a number at the foot of each column and saves the result as a new
book, `<name>_Numbered.pdf`, next to the original. The columns are fitted to each page of
that book, so the numbers land correctly whatever its page size and even if the pages are
not all the same size. The original is left alone, so a wrong column layout costs nothing:
delete the copy and redo it. Convert whichever of the two you want signatures from.

**Both halves of the app number pages the same way**, because both call the same routine
(`print_formatting.draw_folio`): the number is **centred at the foot of the book page**,
on a baseline set as a share of that page's bottom margin. A converted PDF and a book
typed into the editor are therefore indistinguishable on that point, which they were not
when one stamped to the bottom right of a column and the other centred its own.

## How the imposition works

Four terms, because three different things all get called a "page":

| Term | Meaning |
| --- | --- |
| source page | One page of the input PDF. Holds two book pages side by side. |
| book page | One page as the reader sees it. One column of a source page. |
| sheet | One physical piece of paper. Printed on both sides and folded once, it carries **4 book pages**. |
| side | One face of a sheet, i.e. one page of the output PDF. |

A signature of *N* sheets therefore has *2N* sides and *4N* book pages. Print a signature
double-sided, fold every sheet in half, and nest them one inside the other. The first
sheet printed is the outermost.

The fold runs down the middle of the source page, and that is where every page is split,
measured from the page itself, never from the column settings. A conversion is refused only
when it cannot physically be printed as asked: an unreadable or empty PDF, or a book kept at
its original size that runs off the sheet you chose.

Books rarely divide evenly into signatures. The last signature shrinks to the fewest whole
sheets that hold what is left, and any unused pages fall at the **back of the book**, not in
the middle of the final gathering.

### Duplex setting

The sheets are landscape, so the default assumes **long-edge** duplex and rotates the back of
each sheet 180° to compensate. If a test signature comes out with every other page upside
down, switch to short-edge (`--short-edge`, or the sidebar option).

## Tests

```
python -m unittest Script.test_imposition Script.test_manuscript Script.test_editor -v
```

210 tests across the three modules, and they go out of their way not to mark their own
homework:

- The sheet layout is derived by simulating the physical fold, independently of the
  production formula.
- The page-size tests build a real PDF, run the real conversion, and then read back out of
  the finished file where the ink actually landed, including a small PDF interpreter that
  tracks the clipping path, because "did this column reach the paper" is a question text
  extraction cannot answer.
- Rotation, crop boxes and offset media boxes are checked against coordinate formulas
  written out by hand rather than reused from the code under test.
- The claim that the column settings cannot affect a conversion is not asserted, it is
  demonstrated: the same book is imposed under a fitted layout, a nonsense one and none at
  all, and the finished files are compared byte for byte.
- The editor is driven through Streamlit's own `AppTest`: real clicks on the real page,
  and then the draft file is read off the disk. The words a click carries with it are
  typed and clicked in a *single* run, because that is what a browser sends and it is
  where the editor used to lose a book's title page.
- Loading a draft is checked at the *message* the server sends, not at the value it holds.
  A keyed box keeps its identity across a rerun, so a fresh `value=` changes the model
  and nothing on screen; every box therefore has to come back carrying `set_value`, and
  has to stop carrying it on the run after, or typing would fight the cursor.
- Which tab a setting lives on is asserted, not assumed: the sidebar has to be exactly the
  four controls both tabs share, neither tab may show the other one's paper controls, and
  the writing tab may never have both of its size menus on screen at once. What moved still
  has to be *held* — each tab's paper survives a trip through the other, because Streamlit
  discards the state of any widget a run did not draw — and the paper chosen has to reach
  the finished signature, which is read back out of the built PDF's page size rather than
  out of the setting.
- The editor's table of contents is checked against reality rather than against itself: a
  marker word is planted at the start of every chapter, the finished PDF is searched for
  where that word landed, and the number printed in the contents has to agree with it.
  Page numbers, running heads and right-hand-page starts are read out of the built file
  the same way.
- Both halves of the app are asked, separately and in the same terms, where their page
  numbers ended up: centred on the book page, on a baseline derived by hand in the test
  rather than imported from the code that drew it.
- The drafts folder is attacked rather than exercised: names containing `..`, path
  separators, reserved Windows device names and nothing at all must all end up as a file
  inside `Manuscripts/`, and deleting refuses anything that is not a draft in there.
- The command line is driven for real too, `main.main([...])` against a redirected set of
  folders, because it is the half of the app with no interface to notice a break.
