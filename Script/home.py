"""The front page: three cards, and a way back to work already in progress.

The app does three genuinely different jobs, and for a long time it presented
them as two tabs named in its own vocabulary — "Convert 2 Column Formatted PDF
into PDF Signatures" — with the AI writer floating above both as the loudest
thing on the page. A visitor had to know what a signature was, and which of the
two tabs their book was, before anything could happen.

So: three cards, each naming a thing a person might arrive wanting, and each
saying what they will get out of it. Nothing else is on this screen. The
settings are on the screens they affect, and the front page asks one question.

**The whole card is the click target, and that is a progressive enhancement.**
Each card holds an ordinary `st.button`; the style block in `app.py` stretches it
over its container, transparent, with its label left showing at the foot. If a
Streamlit release moves the DOM under that selector, the rule stops matching and
the card degrades to an ordinary bordered card with an ordinary button in it —
which is worth saying out loud, because this file already carries four style
blocks pinned to Streamlit internals and none of them may be allowed to break
the app outright.
"""

import streamlit as st

# Tall enough for the longest of the three texts at the narrowest column width
# the app is used at, plus the room the call to action takes at the foot.
# Streamlit does not equalise the heights of columns, and three cards of
# different heights read as three things of different importance.
CARD_HEIGHT = 205


def render(cards, go, *, busy, full, ai_ready, resume=()):
    """Draw the three cards. `resume` is the strip of work already in progress."""
    st.markdown("#### What would you like to do?")

    columns = st.columns(len(cards), gap="large")
    for column, card in zip(columns, cards):
        with column:
            _card(card, go, busy=busy, full=full, ai_ready=ai_ready)

    if resume:
        st.markdown("")
        with st.container(border=True):
            st.markdown("**Already in progress**")
            for line, label, route, key in resume:
                text_column, button_column = st.columns([3, 1], vertical_alignment="center")
                text_column.markdown(line)
                if button_column.button(
                    label, key=key, use_container_width=True, disabled=busy
                ):
                    go(route)

    with st.expander("New here? What a signature is, and why this app makes them"):
        st.markdown(SIGNATURE_NOTE)


def _card(card, go, *, busy, full, ai_ready):
    route = card["route"]
    # The AI card is the only one that can be switched off by configuration
    # rather than by the session being full: a copy with no key can still do
    # everything else, so the card says so rather than disappearing.
    off = not ai_ready and route == "ai"
    with st.container(border=True, height=CARD_HEIGHT, key=f"bookcard-{route}"):
        st.markdown(
            f"<div class='bookcard-body'>"
            f"<div class='bookcard-icon'>{card['icon']}</div>"
            f"<div class='bookcard-title'>{card['title']}</div>"
            f"<div class='bookcard-text'>{card['text']}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if off:
            st.caption("Switched off on this copy — no API key is set.")
        if st.button(
            "Start →",
            key=f"bookcard-go-{route}",
            use_container_width=True,
            # `full` closes the two cards that would write something. The
            # conversion card is closed too: it opens on an uploader.
            disabled=busy or off or full,
        ):
            go(route)


SIGNATURE_NOTE = """
A **signature** is a small stack of sheets, printed on both sides, folded once
down the middle and nested one inside another. A book is several of them, sewn
together through the fold.

That is why this app exists: printing a book's pages in order gives you a stack
of paper, not a book. The pages have to be **imposed** — shuffled so that when
the sheets are folded and nested, the pages come out in reading order. Page 1
ends up beside the last page on the same side of the same sheet.

**One sheet gives four book pages.** A sheet of *W × H* paper folds to book pages
of *W/2 × H*, so A4 landscape folds to A5, and Letter landscape folds to Half
Letter.

Whichever card you pick, what you get out is a set of PDFs — one per signature —
in print order, with a note telling you how to print and fold them.
"""
