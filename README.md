---
title: Bookbinding Signature Creator
emoji: 📖
colorFrom: green
colorTo: gray
sdk: docker
app_port: 8501
---

# Bookbinding_Signature_Creator

Takes a 2-column PDF book and shuffles the pages into separate, ready-to-print
signature files, on **A4 or any other paper you can feed a printer**. Books that
are not numbered already can be numbered first, as a separate step.

There is also a **book editor**: type a book into text boxes (title, author,
dedication, chapters, appendix) and it is typeset into exactly that kind of 2-column
PDF and folded into signatures, without ever leaving the app.

**Nothing is stored.** Your books are held only while the tab is open and are erased
when it closes. The one copy that lasts is the zip you download yourself — see
[Your data](#your-data-in-and-out-as-one-zip).

I wanted to create signatures for 12 separate books for some bookbinding hobby stuff and it
seemed like more fun to write this than to do it manually and it saved me time. XD

## Requirements

Python 3.12 with `pypdf`, `reportlab` and `streamlit`.

```
conda activate bookbinding
```

## Usage

**GUI**

```
streamlit run app.py
```

Upload a 2-column PDF in **Available for conversion**, or type a book in the writing
tab. There is nothing to put in a folder first, and nothing left in one afterwards.

Two tabs, chosen at the top of the page: **📚 Convert 2 Column Formatted PDF into PDF
Signatures** and **✍️ Convert Inputted Text into PDF Signatures**.

**The sidebar's Settings are only what both tabs share**, and they are four controls: the
units (inches, centimetres or millimetres), the sheets per signature, the printer's duplex
setting, and whether the PDF is moved to the archive once it has been converted. They hold
their values when you switch tabs and mean the same thing on either one. Above them sits
**Your data**, the zip in and zip out described in
[Your data, in and out as one zip](#your-data-in-and-out-as-one-zip).

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

The conversion view has three panels: **Available for conversion**, **Archive of previously
converted** and **Ready to print**. No paths are shown anywhere, because there is nothing
worth showing you — see [Your data](#your-data-in-and-out-as-one-zip).

Each book waiting to be converted names the two sizes you act on, the **paper to load in
the printer** and **each page of the finished book**, so there is no guessing which number
describes what. A finished book gets one **⬇️ Download this book** button that hands over
every signature file in print order with its printing notes, as one zip, rather than a
download per signature: they are printed as a set, in order, and fetching them one at a
time only creates a chance to print them out of order or miss one.

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
light and a dark version; **Theme**, at the top of the sidebar, switches between them.

### Your data, in and out as one zip

**Nothing this app holds is stored.** Whatever you upload or write exists on the server only
while you are working on it, and is erased when you close the tab. The one copy that lasts is
the zip you download yourself.

That is a position, not an accident of hosting. Whatever somebody uploads is theirs, and the
way not to be answerable for it is not to keep it.

At the very top of the sidebar, above **Settings**, are the controls that make that workable:

- **📤 Save my data (.zip)** hands you everything in the session — input PDFs, the archive,
  finished signatures and drafts — as one zip. Save it before you close the tab, or it is
  gone.
- **📥 Load my data (.zip)** puts one of those zips back at the start of your next visit. It
  *replaces* everything currently in the session rather than adding to it, so it asks for a
  second click before deleting anything, the same way **🗑 Delete** does. A zip that holds
  none of the app's folders is refused before anything is touched, and the zip is checked end
  to end first, so a half-finished download cannot leave you half-emptied. A folder you
  zipped by hand (everything one level deeper, the shape right-click → *Compress* makes) is
  read too.
- **🗑 Delete my data now** erases the session immediately, without waiting for the tab to
  close. Two clicks, like every other delete here.

Single files leave the same way: **⬇️ Download this book** on a finished conversion, and
**⬇️ Download this draft** in the editor, which hands over the book as it is on screen
whether or not it has been saved.

### How the erasure actually works

The imposition and typesetting code writes real PDFs, so there has to be somewhere on disk to
write them. There is exactly one such place per visitor, and `Script/workspace.py` is the
whole of it:

    <system temp>/bookbinding_sessions/<streamlit session id>/
        Input/  Output/  Previously_Converted/  Manuscripts/

Named after the session, so it cannot be found by guessing or shared between visitors; under
the system temp folder, so it is never anywhere near the app's own source; created fresh, so
a new visitor starts empty however the last one left. `open_session` points `main`'s four
folder names into it, and everything below `app.py` goes on writing to the names it always
used without ever learning that they move.

Three overlapping guarantees take it away again, because one would be a single point of
failure:

- **The sweeper.** A daemon thread wakes every 15 seconds, asks the Streamlit runtime which
  sessions still have a browser attached, and deletes the folder of every session that does
  not — after a 30-second grace, so a momentary network blip does not cost somebody their
  book. Closing the tab therefore erases everything within about half a minute, with nothing
  asked of the visitor.
- **Shutdown.** Everything the process created is removed when it exits, so a Space going to
  sleep takes the files with it.
- **The orphan sweep.** If the runtime cannot be read at all, a folder untouched for an hour
  is removed anyway. Not being able to tell which sessions are live never means "delete
  everything" — that would erase somebody mid-sentence — so it falls back to age instead.

Uploaded bytes themselves live in Streamlit's in-memory uploaded-file manager and go with the
session. Nothing is logged, copied elsewhere, or sent anywhere.

### How much one session may hold

Two numbers, and they are deliberately not the same one:

| | |
|---|---|
| **500 MB** | everything one session may hold, together — uploads, the archive, finished signatures and drafts (`LIMIT_BYTES`) |
| **100 MB** | the most any single uploaded book PDF may be (`MAX_UPLOAD_BYTES`) |
| **500 MB** | the most a loaded data zip may unpack to, which is the session limit by definition |

Both live in `Script/workspace.py`, and a bar at the top of the sidebar shows how much of the
session is gone.

**A book is capped at a fifth of the session on purpose.** If one PDF were allowed to fill
the session, uploading it would succeed and then nothing could be done with it: converting
writes its signatures, which come to about the size of the book again, and there would be
nowhere to put them. Every button on that book would be dead, which is the worst way to meet
a limit — nothing said no until it was too late for it to help. At 100 MB the worst case is a
100 MB book, ~110 MB of signatures and ~110 MB numbered copy: 320 MB of 500 MB, with room to
spare.

A per-file cap is not a cap either, because nothing stops the next file, so the total is
enforced too — five places in all:

- **Streamlit's own `maxUploadSize`** is 512 MB in `.streamlit/config.toml`. It is one number
  for the whole app, it is a ceiling on one *file* rather than a quota, and it is set for the
  largest file the app must accept: a data zip carrying a whole session. It sits 12 MB above
  the session limit on purpose. Drafts are JSON and shrink to nothing, which is why a zip of a
  lightly used session looks tiny beside the usage bar — but the bulk of a full session is PDF,
  which is already compressed, so a zip of one comes back *larger* than the session, not
  smaller: 500 MB of incompressible content measures 500.2 MB zipped in a few large files and
  505.7 MB spread over four thousand small ones. At a ceiling of exactly 500 MB the browser
  could refuse the zip the app had just written.
- **The 100 MB book cap** is therefore enforced in `app.py`, on the bytes as they arrive,
  since no config option can scope Streamlit's ceiling to one uploader. For the same reason
  the figure the dropzone prints ("500MB per file") is wrong under *both* uploaders — not the
  limit at all under the book one, and a true statement about the wrong quantity under the zip
  one, whose real rule is what the zip unpacks to. Each uploader sits in a keyed container and
  a style block replaces its line with the rule that uploader actually enforces: `100MB per
  file • PDF`, and `Must fit the 500 MB session • ZIP`.
- **Before an upload**, where the sizes are known: the free space is counted down file by
  file as they are written, so three files that would each have fitted the space that was
  free before any of them was written do not all get written. What is refused is named in the
  error rather than silently dropped, and "over the 100 MB a book may be" and "would fit if
  you deleted something" are said as two different things, because they are.
- **Before a zip is loaded**, from the declared sizes, before anything is deleted — a zip
  that would not fit is refused with the session exactly as it was. The copy loop then
  counts the bytes that actually arrive as well, so the limit rests on what landed rather
  than on what the listing claimed.
- **During a conversion**, through `workspace.watcher`, hung off the progress hook the
  imposition already reports through. Nothing can know how big a set of signatures will be
  until it has written them, so a job that starts inside the limit and would end outside it
  is stopped part way. A conversion writes into a staging folder and drops it on any failure;
  numbering and typesetting write directly, so the partial file is removed by hand. A book
  that was typeset and then had only its imposition stopped is a real result and is kept.

Every control that would write — convert, number, build, save a draft, autosave, upload —
goes dead when there is no room, with the reason on screen. **📤 Save my data** and
**🗑 Delete my data now** never do: the way out and the way to make room have to stay open
when everything else is shut.

### Running it on a server

The app behaves the same way wherever it runs — there is no local mode and no hosted mode,
because a rule that only applies when deployed is a rule nobody has tested. `streamlit run
app.py` on your own machine gives you a session folder in temp that is erased when you close
the tab, exactly as a Space does.

The frontmatter at the top of this file is the HuggingFace Spaces config (`sdk: streamlit`,
`app_file: app.py`); `requirements.txt` pins everything else.

One caveat worth knowing before deploying. A Space is one process shared by everyone who
opens the page, and the four folder names live on `main` as module attributes, so the
isolation is re-asserted at the top of every script run and again immediately before any file
is written. Two people converting at the very same second could still see one another's
listing for a frame. Making that impossible would mean passing a folder down through every
function that writes a file.

One session may hold 500 MB and one book PDF may be 100 MB; see
[How much one session may hold](#how-much-one-session-may-hold). Changing the session figure
means changing `LIMIT_BYTES` in `Script/workspace.py` **and** `server.maxUploadSize` in
`.streamlit/config.toml`, which must stay comfortably *above* it — the first is the quota,
the second is what lets a full data zip back through the browser, and a zip of a full session
is not smaller than the session. The per-book figure is `MAX_UPLOAD_BYTES` alone.

### The headless CLI

`python main.py` still works on your own files on your own machine, and is the one place the
four folders exist as ordinary folders beside the app — it creates them on demand. Nothing
the web app does ever writes there.

### The top-right corner

Streamlit's own developer controls are stripped out, because this is a finished tool rather
than an app someone is building, and two of them are actively dangerous here.
**Stop** and **Rerun** both cut a conversion off mid-write, which is the one thing the
page-drawing order above is arranged to prevent.

`client.toolbarMode = "minimal"` in `.streamlit/config.toml` removes **Deploy**, **Rerun**,
**Auto rerun**, **Clear cache**, **Print** and **Record screen**, and disables the `C`
clear-cache keyboard shortcut. That leaves the System / Light / Dark switcher, an **About**
entry, and the *Made with Streamlit* line, and no config option reaches any of them.

They cannot simply be left unbuilt either. In minimal mode Streamlit builds the top-right
toolbar only for an app that has defined a menu item of its own, so dropping the About entry
from `st.set_page_config` takes the whole corner with it — switcher included, and the
switcher is the only way into the theme that does not throw the session away. So the About
entry stays, the style block in `app.py` hides the corner, and **Theme** at the top of the
sidebar drives the switcher inside it: a one-pixel `st.iframe` at the foot of the page opens
the hidden menu, clicks the mode you chose and shuts it again. The same style block puts the
header strip back to transparent and click-through, which is what it was while it held
nothing, leaving only the **⟩⟩** that appears there when the sidebar is folded away.

`server.fileWatcherType = "none"` drops the source-file watcher and with it the "File change.
Rerun / Always rerun" prompt; set it back to `"auto"` when working on the app itself.

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

Each converted book becomes one PDF per signature plus a `print_instructions.txt` recording
the paper size, the scaling and the duplex setting it was made with — which is exactly what
**⬇️ Download this book** hands over. The input PDF moves to the archive afterwards; **Move
back to the list** puts it back to reconvert with different settings. Reconverting builds the
new signatures in a staging folder and swaps them in only once the whole set exists, so the
two runs can never be mixed and a conversion that fails leaves the previous, complete set
exactly as it was. Deleting is confined to the session's own folders: both delete functions
resolve the path first and refuse anything that is not *inside* one of them, including the
folders themselves.

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

This is about a PDF that already exists, i.e. one you uploaded. A book typed into the editor
is set for its paper in the first place, so it never gets here.

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
imposition is special-cased for it: the editor writes an ordinary input PDF, and from that
point a typed book is indistinguishable from one that came from anywhere else.

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

Drafts are one JSON file each, named after the draft, and kept for the session only. Save as
many as you like and switch between them; **Autosave** keeps writing to the open draft as you
type once it has been saved once. Saving goes through a temporary file and a rename, so a
crash part way cannot leave a half-written draft on top of the one it replaced. Anything that
would throw away unsaved words asks first.

To keep a book past the session, **⬇️ Download this draft** hands over the JSON as it is on
screen — saved or not, what you see is what you get — and **📤 Save my data** takes all of
them at once. **Load** on any saved draft puts it in the editor. **✨ Load the example** fills
the editor with a complete book (five chapters, a part divider, an interlude, a dedication,
an epigraph and a spread of back matter, in lorem ipsum) as an unsaved draft that can be
typed straight over.

Building writes `<name>.pdf` into the session. Building again replaces the PDF **the editor
wrote**; a PDF you uploaded yourself is never overwritten, whatever the book is called,
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
  inside the drafts folder, and deleting refuses anything that is not a draft in there.
- The zip is attacked the same way: an entry named `../../escaped.pdf` must not write
  outside the folder it names, a zip from somewhere else is refused before anything is
  deleted, and a round trip has to come back byte for byte.
- The erasure rules are tested as rules, not as timings: a session still on screen survives
  a sweep, one whose browser has gone does not, and a runtime that cannot be read must never
  delete a live session — not knowing has to fall back to age, or a bug there would take
  somebody's book mid-sentence.
- The size limit is driven through the real app against a real conversion, not just unit
  tested: a job is allowed to start and then refused part way, and what has to be true
  afterwards is that the staging folder is gone, the half-made book is not offered as
  something to print, and the input PDF was not archived as if it had converted.
- The command line is driven for real too, `main.main([...])` against a redirected set of
  folders, because it is the half of the app with no interface to notice a break.
