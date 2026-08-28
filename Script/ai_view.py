"""The screen that asks a model for a book.

One question, one button, and the book appearing as it is written. Everything
about *what* is written lives in `Script/ai_book.py`, which does not import
Streamlit; this module is the screen in front of it and knows nothing about
tokens, requests or JSON.

**It does not move the user.** That is the one thing worth reading before
changing anything here. The progress bar goes into the slot the button occupied
and the book streams into a box beneath it, so both of them are on *this* screen
— and a job whose screen stops being drawn hands the runner no slot, which the
runner answers by releasing the lock and rerunning with the work never started
and nothing said. So the writing keeps this screen for its whole duration, and
the move into the editor happens afterwards, in `app.py`, on the strength of
`book_editor.collect()` finding a finished book.

The old arrangement — a box above the view radio, on every screen, with the
route assigned on the click — was forced by a Streamlit rule: `st.session_state`
may not be assigned for a widget that has already been instantiated, and the
route was a radio's key. It is a plain key now, so none of that applies, and the
button that was the loudest thing on the page for every visitor is now on the
screen of the people who came for it.
"""

import streamlit as st

from Script import ai_book, settings

# The description box. Carried, because it is no longer drawn on every run: it
# used to sit above the view radio and so survived anything, and moving it onto
# a screen of its own means a trip to the front page would otherwise throw away
# a description somebody had just typed.
AI_PROMPT_KEY = "ai-prompt"

# Said on the button itself while the box is empty, and taken off it again the
# moment a character is typed. One string, used by the label below and by the
# script at the foot of `app.py` that keeps the label right between reruns — so
# the two cannot drift.
AI_HINT = "(Please type a description of the book to generate)"

settings.register(AI_PROMPT_KEY, "")


def render(*, busy, job, full, claim_job, handed_over=False):
    """Draw the AI screen. Returns the job to run, or `None`.

    `handed_over` is only ever true on the run a finished book arrives, and this
    screen is not on the page then — it is passed for symmetry with the other
    flows and to keep the signature honest about what `app.py` knows.
    """
    ready = ai_book.available()
    writing = job == ("ai", "write")
    chapters = ai_book.chapter_count()
    # Every reason the button is off that has nothing to do with what is in the
    # box. Kept apart because `TYPING_SCRIPT` may switch the button on when
    # somebody types, and must never do it while a job is running, the disk is
    # full, or this copy has no key.
    locked = busy or full or not ready

    st.markdown("### Describe the book you want")

    settings.hold(AI_PROMPT_KEY)
    st.text_area(
        "Describe the book you want",
        key=AI_PROMPT_KEY,
        max_chars=ai_book.MAX_PROMPT_CHARS,
        height=120,
        placeholder=(
            "What it is about, who it is for, and the voice. "
            f"It is always {chapters} chapters, so say what should happen in it "
            "rather than how long it should be."
        ),
        disabled=busy or not ready,
        label_visibility="collapsed",
    )
    settings.keep(AI_PROMPT_KEY)
    prompt = st.session_state.get(AI_PROMPT_KEY, "")

    if not ready:
        st.info(
            f"Writing with AI is switched off on this copy. "
            f"{ai_book.why_unavailable()} Everything else works as usual.",
            icon="🔌",
        )

    # The disclosure sits with the button rather than under the panel, because
    # this is the one control in the whole app that sends anything anywhere and
    # the moment to say so is the moment before it is pressed.
    st.warning(
        "**What leaves this app:** only the description above, sent to "
        "OpenRouter to be written. Nothing else from your session is ever sent "
        "anywhere. Treat that box as public.",
        icon="⚠️",
    )

    pending = None
    slot = st.empty()
    if slot.button(
        "✍️ Writing…" if writing
        else f"🤖 Write my {chapters} chapter mini-novel"
        + ("" if prompt.strip() else f"  **{AI_HINT}**"),
        key="ai-write",
        type="primary",
        use_container_width=True,
        # Not switched off for an empty box, and that is deliberate. Streamlit's
        # button ignores a click whenever *React* thinks it is disabled, whatever
        # the browser has been told, so a button the server drew switched off
        # cannot be switched on from the page. It is drawn on, made to look and
        # behave off while the box is empty by `TYPING_SCRIPT`, and the check
        # below is what actually refuses.
        disabled=locked,
    ):
        # Asked again, because the button can be pressed with the box empty: the
        # script that unlocks it while somebody types works from what the browser
        # can see, and the server has the last word on what it was actually sent.
        if st.session_state.get(AI_PROMPT_KEY, "").strip():
            claim_job(("ai", "write"))

    if writing:
        # Where the book appears while it is being written. Drawn only while the
        # job is running, and directly under the button it belongs to, so nothing
        # below it moves when the words start arriving.
        stream = st.empty()
        pending = {
            "kind": "ai_write",
            "slot": slot,
            "stream": stream,
            "prompt": st.session_state.get(AI_PROMPT_KEY, ""),
        }

    st.caption(
        "It writes the words only — your page size, type and margins are left "
        "exactly as you set them. When it finishes you land in the editor with "
        "the book in it, to read, change and export however you like."
    )

    with st.expander("What to expect"):
        st.markdown(
            f"""
- **It is always {chapters} chapters**, whatever the description says. Describe
  *what happens*, not how long it should be — "a short novel" and "an epic"
  change the voice and the pacing, not the count.
- **What comes back is a draft, not a finished book.** A model invents: it can be
  confidently wrong, and it can land close to something it was trained on. Read
  it before you print it, and read it properly before you publish it.
- **Whatever is in the editor is kept first.** If it was already saved it stays
  where it is; if it had unsaved words they go to a draft of their own beside it,
  and the message afterwards names it. Nothing you typed is the price of
  pressing this button.
- **Nothing is saved anywhere afterwards.** The book arrives in the editor
  unsaved, with the three download buttons at the top of that screen.
"""
        )

    return pending
