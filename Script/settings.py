"""Settings that outlive the screen they are set on.

Streamlit throws away the state of every widget a run did not draw. That is the
right default for a one-page app and the wrong one for this app, where a setting
chosen on one screen is read on another: the sheets-per-signature the writing
flow builds with is set on a panel the conversion flow never draws, and the
printer's duplex setting is read by the job runner on every route.

Left alone, such a setting is back at its default the first time the user comes
back to it — quietly printing the next book on different paper than the one they
chose. That is the bug this module exists for, and it got worse rather than
better when the app grew from two views to four routes: a setting is now undrawn
on three screens out of four instead of one out of two.

**How it works.** Every carried value has a second key, `kept-<key>`, that no
widget owns and so nothing discards. `keep` writes it after the widget is drawn;
`hold` puts it back before the widget is drawn on some later run. `resolve` reads
it without drawing anything at all.

**Why `resolve` is the important one.** It is what separates *reading* a setting
from *drawing* it. The job runner needs `sheets_per_signature` on a run where the
number box lives inside a collapsed expander on a flow that is not on screen;
before this, the only way to know a setting's value was to have just drawn it,
which is precisely why every shared setting had to live in the sidebar. `resolve`
is what lets a setting move next to the thing it affects.

**Why `carried` rather than a pair of calls.** The old `sticky`/`remember` pair
had a rule that lived only in a docstring: it is for widgets drawn *without*
`value=` or `index=`, because Streamlit prints a complaint on the page when a
widget is given both a starting value and a session one. A rule stated in prose
is a rule that gets broken — `setting-sheets` passed `value=` for months while
sitting in the sidebar, where it happened not to matter because the sidebar is
drawn on every run. `carried` asserts it instead.

It also repairs a stored choice this build cannot offer, which `sticky` did not.
A `selectbox` *raises* rather than falling back when its state names something
absent from its options, so a kept paper size that a later release renamed would
take the whole page down. `_seed_choice` in `book_editor` has always done this
for the editor's menus; `carried` brings it to every carried menu.
"""

import streamlit as st

# What a key falls back to when neither the widget nor the kept copy has an
# answer, and — for a menu — what it is allowed to be. Registration is not
# required to use `hold`/`keep`/`carried` with an explicit default, but it is
# what lets `resolve` answer for a widget that has never been drawn in this
# session at all, which is the whole point on a first visit.
_DEFAULTS = {}
_OPTIONS = {}


def register(key, default, options=None):
    """Record what `key` means, so `resolve` can answer before it is ever drawn.

    Called once per setting, at import time, next to the constant it defaults
    to. `options` is only for menus, and only so a stored choice this build
    cannot offer can be repaired rather than raised on.
    """
    _DEFAULTS[key] = default
    if options is not None:
        _OPTIONS[key] = tuple(options)
    return default


def kept_key(key):
    """Where the surviving copy of `key` is kept."""
    return f"kept-{key}"


def resolve(key, default=None):
    """What `key` is set to, without drawing anything.

    The widget's own state first, because that is the value the user is looking
    at; then the kept copy, which is what survives the runs its widget was not
    drawn on; then the registered default.

    A menu's answer is checked against its options on the way out, so a stored
    choice this build no longer offers resolves to the default rather than to
    something no widget could show.
    """
    if key in st.session_state:
        value = st.session_state[key]
    elif kept_key(key) in st.session_state:
        value = st.session_state[kept_key(key)]
    elif default is not None:
        return default
    else:
        return _DEFAULTS.get(key)

    options = _OPTIONS.get(key)
    if options is not None and value not in options:
        return default if default is not None else _DEFAULTS.get(key)
    return value


def hold(key, default=None):
    """Put the kept copy back into a widget's state, before the widget is drawn.

    Seeded only when the widget has no state of its own, which is exactly the
    case it is for: either this is the first run that draws it, or the run
    before dropped it for not being on screen. On every other run the widget
    owns its value and is left alone — writing to it on every run would fight
    the user for the cursor.
    """
    if key not in st.session_state:
        st.session_state[key] = resolve(key, default)
    else:
        # Already has state, but it may be a choice this build cannot offer —
        # a `selectbox` raises on one of those rather than falling back.
        options = _OPTIONS.get(key)
        if options is not None and st.session_state[key] not in options:
            st.session_state[key] = resolve(key, default)


def keep(key):
    """Keep this widget's value for the runs its screen is not on."""
    if key in st.session_state:
        st.session_state[kept_key(key)] = st.session_state[key]


def carried(key, widget, *args, **kwargs):
    """Draw a widget whose value survives the screens that do not draw it.

    `hold` before, `keep` after, in one call, so the two can never drift apart —
    a `remember` that a later edit forgets to add is a setting that silently
    reverts, and that failure is invisible until somebody prints a book on the
    wrong paper.

    The `value=`/`index=` rule is asserted rather than described. Streamlit
    prints a complaint on the page when a widget is handed both a starting value
    and a session one, and a complaint on the page is not something this app
    should ever show a visitor.
    """
    assert "value" not in kwargs, (
        f"“{key}” is carried, so its value comes from session state. "
        "Register a default instead of passing value=."
    )
    assert "index" not in kwargs, (
        f"“{key}” is carried, so its value comes from session state. "
        "Register a default instead of passing index=."
    )
    if "options" in kwargs and key not in _OPTIONS:
        _OPTIONS[key] = tuple(kwargs["options"])
    elif len(args) >= 2 and key not in _OPTIONS and widget in (st.selectbox, st.radio):
        # `st.selectbox(label, options)` — the positional form.
        try:
            _OPTIONS[key] = tuple(args[1])
        except TypeError:  # pragma: no cover - not every second argument is a list
            pass

    hold(key)
    value = widget(*args, key=key, **kwargs)
    keep(key)
    return value


def forget(key):
    """Drop a setting and its kept copy. For tests, and for a session reset."""
    st.session_state.pop(key, None)
    st.session_state.pop(kept_key(key), None)
