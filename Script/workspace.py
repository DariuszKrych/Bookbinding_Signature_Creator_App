"""The session's scratch space: made on arrival, destroyed on leaving.

Nothing this app holds is stored. A visitor's books exist on the server only
while they are working on them, in a folder nobody else can reach, and that
folder is deleted within seconds of the browser going away. The only copy that
outlives the session is the zip the visitor downloads themselves.

That is a deliberate position rather than an accident of hosting. Whatever
someone uploads is theirs — including, eventually, something nobody wants
sitting on a server. The way not to be responsible for it is not to keep it.

## Where the files are

The imposition and typesetting code writes real PDFs, so there has to be
somewhere on disk to write them. There is exactly one such place per session:

    <system temp>/bookbinding_sessions/<streamlit session id>/
        Input/  Output/  Previously_Converted/  Manuscripts/

Named by the session, so it cannot be found by guessing and cannot be shared;
under the system temp folder, so it is never anywhere near the app's own source;
and created fresh, so a new visitor starts empty however the last one left.

`open_session` points `main`'s four folder names into it. Everything below
`app.py` goes on writing to the names it always used and never learns that they
move — which is also how the test suite has always redirected them.

## When they go

Three overlapping guarantees, because one is a single point of failure:

* **The sweeper.** A daemon thread wakes every `SWEEP_SECONDS`, asks the
  Streamlit runtime which sessions are still connected, and deletes the folder
  of every session that is not — after `GRACE_SECONDS`, so a momentary network
  blip does not cost somebody their book. Closing the tab therefore erases the
  data within about half a minute, with nothing asked of the visitor.
* **Shutdown.** Everything this process made is removed when it exits, so a
  Space going to sleep takes the files with it.
* **The orphan sweep.** If the runtime cannot be read at all — bare mode, or a
  Streamlit that has moved on — a folder untouched for `ORPHAN_SECONDS` is
  removed anyway, so nothing can survive indefinitely through a bug here.

`discard` is the fourth: the visitor asking for it directly, and not waiting.
"""

import atexit
import io
import os
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

import main

# The whole of a session's state, in the order a person would think of it. Every
# file the visitor could care about is under one of these four; the zip carries
# these and nothing else.
DATA_FOLDERS = ("Input", "Output", "Previously_Converted", "Manuscripts")

# The four names on `main` that the folders above answer to, paired up.
_ATTRIBUTES = {
    "Input": "INPUT_DIR",
    "Output": "OUTPUT_DIR",
    "Previously_Converted": "PREVIOUS_DIR",
    "Manuscripts": "MANUSCRIPT_DIR",
}

# The one place on disk this app ever writes. A module attribute rather than a
# constant so the tests can point it at a folder of their own.
SESSIONS_ROOT = Path(tempfile.gettempdir()) / "bookbinding_sessions"

# How often the sweeper looks, and how long a disconnected session is given
# before its folder goes. Thirty seconds covers a browser reloading or a network
# blip; it is not long enough for anyone to have walked away.
SWEEP_SECONDS = 15
GRACE_SECONDS = 30

# The backstop, used only when the runtime cannot be asked which sessions are
# live. Long enough that it can never cut short somebody who is still working,
# short enough that nothing is forgotten about.
ORPHAN_SECONDS = 60 * 60

# The session-state key holding this session's folder name.
SESSION_KEY = "workspace_id"

# --------------------------------------------------------------------------
# How much one visitor may hold
# --------------------------------------------------------------------------
# One number, counted over everything: uploads, the archive, finished
# signatures, drafts. Not a per-file cap — a per-file cap is no cap at all,
# because nothing stops the next file. `.streamlit/config.toml` sets Streamlit's
# own per-upload limit to the same figure so the browser refuses an oversized
# file before it is sent; this is the one that decides whether it fits.
#
# It is enforced in three places, and it has to be all three:
#
# * before an upload, and before a zip is loaded, where the exact size is known
#   in advance and the answer can be "no" without anything happening;
# * during a conversion, through `watcher`, because the size of what imposition
#   is about to write is not knowable until it is written;
# * on the buttons, which go dead once there is no room left, so the refusal is
#   visible before it is clicked rather than after.
LIMIT_BYTES = 500 * 1024 * 1024

# The most one uploaded PDF may be, and deliberately well under the session
# limit rather than equal to it.
#
# A book that is allowed to fill the whole session is a book that cannot then be
# done anything with: converting it writes its signatures, which come to about
# the size of the book again, and there would be no room for them. The upload
# would succeed and every button on it would be dead — the worst way to hit a
# limit, because nothing said no until it was too late to matter.
#
# A fifth of the session leaves room for the book, its signatures, a numbered
# copy and several more books besides.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

# Below this there is no point offering to start a job: every conversion writes
# at least its signature files and a print_instructions.txt.
ROOM_TO_WORK = 1024 * 1024


class QuotaExceeded(Exception):
    """One session's files would go over `LIMIT_BYTES`."""


# Read a zip entry in blocks rather than whole, so a lying header cannot make
# the app allocate a file it has already decided is too big.
_COPY_BLOCK = 1024 * 1024


def human(size):
    """A byte count as a person would say it."""
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 ** 3):.1f} GB"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.0f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} bytes"


def usage(folders):
    """Bytes currently held by this session, across all four folders."""
    total = 0
    for folder in folders.values():
        for path in Path(folder).rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                # A file the conversion has just replaced out from under us.
                # Missing a few bytes here is not worth failing a size check for.
                continue
    return total


def free(folders):
    """Bytes still available. Never negative, so callers can just compare."""
    return max(0, LIMIT_BYTES - usage(folders))


def guard(folders, extra=0):
    """Raise unless `extra` more bytes would still fit. Returns the free space.

    Written against the total rather than against `free`, which floors at zero:
    a session that is *already* over — a job that wrote past the line between
    two checks — has to raise on `extra=0` as well, and `0 > 0` would not.
    """
    room = LIMIT_BYTES - usage(folders)
    if extra > room:
        raise QuotaExceeded(
            f"that would take this session over its {human(LIMIT_BYTES)} limit "
            f"({human(max(0, room))} free). Save your data and delete "
            f"something, or start again with 🗑 Delete my data now."
        )
    return room


def watcher(folders, every=1.0):
    """A quota check cheap enough to hang off a long job's progress hook.

    Imposition does not know how big its output will be until it has written
    it, so the only honest way to hold a limit over it is to watch while it
    runs and stop it the moment it crosses. Counting the whole session costs a
    directory walk, so it is done at most once a second: a conversion writes one
    page at a time, and a second of writing cannot carry it far past the line.
    """
    last = [0.0]

    def check():
        now = time.monotonic()
        if now - last[0] < every:
            return
        last[0] = now
        guard(folders)

    return check


_lock = threading.Lock()
_sweeper = None
# Every folder this process has made, so shutdown can take them all back.
_ours = set()


# --------------------------------------------------------------------------
# Making one
# --------------------------------------------------------------------------
def _session_id(state):
    """A name for this session's folder, stable across its reruns.

    Streamlit's own session id when there is one, so the sweeper can match a
    folder against the list of connected sessions by name alone. Outside a
    server — a bare script, a test — there is no such id, and a random one is
    kept in session state instead; those folders are swept by age.
    """
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        context = get_script_run_ctx()
    except Exception:
        context = None
    if context is not None and getattr(context, "session_id", None):
        return str(context.session_id)
    name = state.get(SESSION_KEY)
    if not name:
        name = uuid.uuid4().hex
        state[SESSION_KEY] = name
    return name


def open_session(state):
    """This session's four folders, created and ready. Starts the sweeper.

    Called at the top of every run. The folder is touched each time, which is
    what the orphan sweep reads, and `main` is pointed into it, which is what
    every function that writes a file reads.
    """
    root = SESSIONS_ROOT / _session_id(state)
    state[SESSION_KEY] = root.name
    folders = activate(root)
    with _lock:
        _ours.add(root)
    _touch(root)
    start_sweeper()
    return folders


def activate(root):
    """Point `main`'s four folders at `root`, creating them if need be."""
    root = Path(root)
    folders = {}
    for name in DATA_FOLDERS:
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        setattr(main, _ATTRIBUTES[name], folder)
        folders[name] = folder
    return folders


def scratch_root(folders):
    """Somewhere to build a file that is only going to be downloaded.

    A sibling of the four data folders rather than one of them, and that is the
    whole point of it:

    * it is not in `DATA_FOLDERS`, so it counts against nothing in `usage` and
      is never carried in the session zip — nothing here is the visitor's data,
      it is a file on its way to their browser;
    * it is under the same root, so the sweeper, the shutdown hook and
      `discard` all take it away with everything else. A build that dies half
      way therefore leaves nothing behind for longer than the session lasts.
    """
    return Path(folders["Input"]).parent / "_scratch"


def reassert(folders):
    """Say once more, right before any file is written, where it is to go.

    The four names live on `main` — one module shared by every session in the
    process — and a whole page is drawn between the top of a run and the moment
    work starts, during which another session's run can have pointed them at its
    own folder. One call closes all but a sliver of that gap.
    """
    for name, folder in folders.items():
        setattr(main, _ATTRIBUTES[name], folder)


def _touch(root):
    try:
        os.utime(root, None)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Taking it away again
# --------------------------------------------------------------------------
def discard(state):
    """Erase this session's folder now, at the visitor's word.

    The id is dropped as well, so the next run builds a new empty folder under a
    new name rather than reopening the one just emptied.
    """
    name = state.get(SESSION_KEY)
    if name:
        root = SESSIONS_ROOT / name
        with _lock:
            _ours.discard(root)
        shutil.rmtree(root, ignore_errors=True)
        state.pop(SESSION_KEY, None)


def _connected_session_ids():
    """The sessions Streamlit still has a browser on, or None if it cannot say.

    Reaching into the runtime for this is the price of the guarantee: there is
    no public "tell me when a visitor leaves". None is returned rather than an
    empty set on any failure, because an empty set means "delete everything" and
    a missing runtime must never mean that.
    """
    try:
        import streamlit.runtime as runtime
        instance = runtime.get_instance()
        sessions = instance._session_mgr.list_active_sessions()
        return {str(info.session.id) for info in sessions}
    except Exception:
        return None


def sweep(now=None):
    """One pass. Returns the folders it deleted, which is what the tests read."""
    now = time.time() if now is None else now
    connected = _connected_session_ids()
    removed = []
    try:
        candidates = list(SESSIONS_ROOT.iterdir())
    except OSError:
        return removed

    for root in candidates:
        if not root.is_dir():
            continue
        if connected is not None and root.name in connected:
            # Still on screen. Touched here as well as in `open_session`, so a
            # visitor reading the page without clicking anything cannot age out.
            _touch(root)
            continue
        try:
            idle = now - root.stat().st_mtime
        except OSError:
            continue
        # A folder whose session is gone goes after the grace period. One this
        # process never made and cannot ask about — a crashed run, a different
        # Streamlit — waits for the backstop instead.
        limit = GRACE_SECONDS if connected is not None else ORPHAN_SECONDS
        if idle > limit:
            shutil.rmtree(root, ignore_errors=True)
            with _lock:
                _ours.discard(root)
            removed.append(root)
    return removed


def _sweep_forever():
    while True:
        time.sleep(SWEEP_SECONDS)
        try:
            sweep()
        except Exception:
            # A sweeper that dies stops being a guarantee. Nothing here is worth
            # taking the thread down for; the next pass is 15 seconds away.
            pass


def start_sweeper():
    """Start the one sweeper thread, if it is not already running."""
    global _sweeper
    with _lock:
        if _sweeper is not None and _sweeper.is_alive():
            return
        _sweeper = threading.Thread(
            target=_sweep_forever, name="workspace-sweeper", daemon=True
        )
        _sweeper.start()


@atexit.register
def _discard_everything():
    """Nothing this process made outlives it."""
    with _lock:
        roots = list(_ours)
        _ours.clear()
    for root in roots:
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------------------
# The zip, which is the only copy that lasts
# --------------------------------------------------------------------------
def pack(folders):
    """Every file in the four folders, as the bytes of one zip.

    Entries are named by the canonical folder names rather than by wherever
    those folders happen to sit — `Input/book.pdf` — so the zip never carries
    the session's path, and `unpack` can put it back without guessing.

    Deflated rather than stored: a signature PDF is mostly already-compressed
    streams and barely shrinks, but a folder of book drafts is JSON and shrinks
    a lot, and the whole point of the file is that it travels.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in DATA_FOLDERS:
            folder = Path(folders[name])
            if not folder.is_dir():
                continue
            # An empty folder still goes in, as a folder. Without it a workspace
            # with nothing in Output/ would come back from a round trip missing
            # the folder entirely, and the first conversion would be writing
            # into somewhere that had quietly stopped existing.
            archive.writestr(f"{name}/", "")
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    archive.write(path, str(Path(name) / path.relative_to(folder)))
    return buffer.getvalue()


def pack_folder(folder, name=None):
    """One folder — a finished book — as the bytes of a zip.

    For the download on a single book, so taking one home does not mean taking
    everything home. Entries are named under `name`, so the zip opens as a
    folder called after the book rather than as loose signature files.
    """
    folder = Path(folder)
    name = name or folder.name
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, str(Path(name) / path.relative_to(folder)))
    return buffer.getvalue()


def _entry_parts(name):
    """One zip entry's path, cut down to `("Input", "book.pdf")` or None.

    Three things are being decided here, and only one of them is about
    convenience:

    * Anything not under one of the four folder names is dropped. A zip made
      anywhere else has no business writing into the workspace.
    * A zip made by right-clicking the folder itself has everything one level
      deeper — `My Books/Input/x.pdf`. That shape is common enough to be worth
      reading, so a single leading component is stepped over when the one behind
      it is a folder we know.
    * `..` is refused outright. An entry named `../../../etc/passwd` is the
      oldest trick there is against code that unpacks archives; `_entry_target`
      re-checks the finished path as well.
    """
    name = name.replace("\\", "/")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return None
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        return None
    # One wrapper folder is allowed, and only one.
    if parts[0] not in DATA_FOLDERS:
        if len(parts) < 2 or parts[1] not in DATA_FOLDERS:
            return None
        parts = parts[1:]
    return tuple(parts)


def _entry_target(parts, folders):
    """Where `parts` lands, or None if it would land outside its own folder."""
    if len(parts) < 2:
        return None
    folder = Path(folders[parts[0]])
    target = (folder / Path(*parts[1:])).resolve()
    return target if folder.resolve() in target.parents else None


def unpack(data, folders):
    """Replace the four folders with what is in the zip `data`. Returns a count.

    Wipe and replace, not merge: a zip is the whole of a session, and loading
    one is the visitor saying "this is where I was". Merging would leave a book
    they had deleted before saving sitting in the listing beside the ones they
    kept, and no way to tell which zip it came from.

    The zip is read fully before anything is deleted, so a truncated or corrupt
    file is refused with the session still standing.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        # Reads every header and every CRC. This is the check that turns "half
        # a download" into a refusal instead of into a half-emptied session.
        if archive.testzip() is not None:
            raise ValueError("That zip file is damaged.")
        recognised = [
            (entry, parts)
            for entry in archive.infolist()
            if (parts := _entry_parts(entry.filename)) is not None
        ]
        # An empty session saves as four folder entries and no files, and must
        # load back as an empty session rather than as an error — so it is the
        # folders being recognised that says this is one of ours, not the count
        # of files. A zip of holiday photos matches nothing and is refused with
        # nothing touched.
        if not recognised:
            raise ValueError(
                "That zip does not hold this app's folders. Load one saved by "
                "“Save my data”."
            )
        wanted = [
            (entry, target)
            for entry, parts in recognised
            if not entry.is_dir()
            and (target := _entry_target(parts, folders)) is not None
        ]

        # Checked against the whole limit rather than against what is free,
        # because the load empties the session first — so the only question is
        # whether the zip itself fits. Asked from the *declared* sizes, before a
        # byte is written and before anything is deleted: a zip that would not
        # fit is refused with the session exactly as it was. A zip whose header
        # lies about its size is caught on the way in, below.
        incoming = sum(entry.file_size for entry, _target in wanted)
        if incoming > LIMIT_BYTES:
            raise QuotaExceeded(
                f"that zip holds {human(incoming)}, over the "
                f"{human(LIMIT_BYTES)} a session may keep. Nothing was changed."
            )

        for name in DATA_FOLDERS:
            folder = Path(folders[name])
            if folder.is_dir():
                shutil.rmtree(folder)
            folder.mkdir(parents=True, exist_ok=True)

        written = 0
        room = LIMIT_BYTES
        for entry, target in wanted:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Copied a block at a time against a running total, rather than in
            # one call. The listing has already been checked, and `testzip`
            # above catches an entry whose declared length disagrees with its
            # CRC — which is what a zip that lies about its size amounts to. This
            # counts what actually lands anyway: it costs nothing, it does not
            # depend on the central directory agreeing with the local headers,
            # and it means the limit rests on bytes written rather than on
            # anything the file said about itself.
            with archive.open(entry) as source, open(target, "wb") as sink:
                while block := source.read(_COPY_BLOCK):
                    room -= len(block)
                    if room < 0:
                        sink.close()
                        for name in DATA_FOLDERS:
                            shutil.rmtree(folders[name], ignore_errors=True)
                            Path(folders[name]).mkdir(parents=True, exist_ok=True)
                        raise QuotaExceeded(
                            f"that zip unpacks to more than the "
                            f"{human(LIMIT_BYTES)} a session may keep, whatever "
                            f"its listing says. Nothing was kept from it."
                        )
                    sink.write(block)
            written += 1
    return written
