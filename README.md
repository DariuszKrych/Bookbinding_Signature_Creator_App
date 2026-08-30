<div align="center">

# 📖 Bookbinding Signature Creator

**Turn any 2-column PDF — or a book you type in the browser — into ready-to-print, foldable signatures.**

[![Live app](https://img.shields.io/badge/live-app-2ea44f?style=for-the-badge&logo=streamlit&logoColor=white)](https://bookbinding-signature-creator.onrender.com)
&nbsp;
[![Uptime](https://img.shields.io/badge/uptime-24%2F7-2ea44f?style=for-the-badge)](#-deployment--architecture)

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.59-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![Docker](https://img.shields.io/badge/Docker-micromamba-2496ED?logo=docker&logoColor=white)](./Dockerfile)
[![Render](https://img.shields.io/badge/Render-deployed-46E3B7?logo=render&logoColor=black)](https://render.com/)
[![Tests](https://img.shields.io/badge/tests-489-brightgreen)](#-testing)
[![License](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)

**[▶ Open the live app](https://bookbinding-signature-creator.onrender.com)** · [What it does](#-what-it-does) · [Architecture](#-deployment--architecture) · [Engineering notes](#-engineering-highlights) · [Run it locally](#-run-it-yourself)

</div>

---

> **The short version.** Hand-binding a book means printing it as *signatures* — small stacks of sheets, each printed double-sided, folded once and nested inside one another. Getting the page order right by hand is tedious and easy to ruin. This app does the imposition for you, on any paper a printer will accept, and hands back one zip per book in print order.
>
> **Nothing is stored.** Your books exist on the server only while the tab is open, so every screen ends in a download and that copy is the one that lasts. The single thing that ever leaves the server is the description you type on the AI screen, and only when you press that button — [what that involves](#-the-ai-writer-and-where-its-key-lives).

I built it because I wanted to print signatures for a series of nine books for a bookbinding hobby, and writing the tool is more enjoyable than manually setting up the signature format for the mountain of pages which those nine books come to. It was fun and ended up saving me some time too.

---

## ✨ What it does

**Three cards on the front page, and one linear flow behind each.** You arrive at a
question in your own words — *I have a PDF book*, *I want to start or continue writing my
own book*, *I want AI to generate a 5 chapter mini-novel book* — and each card says what
you get out of it before you commit to anything. Every screen carries a **← Home**, three
numbered steps, and a download at the end of them.

<table>
<tr>
<td width="50%" valign="top">

### 📄 I have a PDF book

Add a 2-column book PDF. Pick the paper you'll actually load in the printer — or leave it on *Same as the PDF*, which is right whenever the book was already made for your paper. Get back one PDF per signature plus a `print_instructions.txt` recording the paper size, scaling and duplex setting used, all as a single zip in print order.

Books that aren't numbered yet can be **page-numbered first**, as a separate step that leaves the original untouched.

</td>
<td width="50%" valign="top">

### ✍️ I want to write my own book

Type a book straight into the browser — title, author, dedication, chapters, appendix — and take it away three ways: **as JSON** to keep editing, **as a PDF book**, or **as signatures** ready to print. JSON comes back in, so a book can be put down and picked up again.

**Nothing is ever scaled here.** The paper isn't something to fit a finished book onto afterwards; it's the size the book is *set* at.

</td>
</tr>
</table>

### 🤖 I want AI to generate a 5 chapter mini-novel book

A one-line description — *"a warm, plain-English beginner's guide to hand bookbinding"* — becomes a five-chapter mini-novel of about 1,800 words, and hands you into the writing screen with it in the editor, to change and export like anything you typed. The words appear **as they are written** rather than after a silent wait, and every book is held to a hard ceiling of 8,000 tokens. Optional, off unless a key is configured, and it writes the **words only**: your page size, type and margins are left exactly as you set them. [How it works, and where its key lives.](#-the-ai-writer-and-where-its-key-lives)

```mermaid
flowchart LR
    H["🗺 Front page<br/><i>three cards</i>"] --> A
    H --> B
    H --> G
    B["✍️ Type a book<br/>in the editor"] --> T["typesetting.py<br/><i>sets the type at final size</i>"]
    G["🤖 Describe a book"] --> AI["ai_book.py<br/><i>outline, then chapters</i>"]
    AI --> B
    A["📄 Add a<br/>2-column PDF"] --> C
    T --> C["print_formatting.py<br/><b>imposition</b>"]
    B -.-> J["⬇️ JSON"]
    T -.-> P["⬇️ PDF book"]
    C --> D["📄 One PDF per signature<br/>+ print_instructions.txt"]
    D --> E["⬇️ One zip,<br/>in print order"]
    J -.-> B
```

**The two writing cards converge.** The AI writer joins at the *editor*, not at the pipeline: it produces a `Manuscript`, the same object the editor holds, so everything downstream of it is the one code path a typed book takes. There is one editor, one design panel and one set of export buttons, whichever card you came in through.

The bridge between the two halves is deliberately narrow: the editor writes an **ordinary input PDF**, and from that moment a typed book is indistinguishable from one that came from anywhere else. There is no second pipeline to keep in step with the first.

---

## 🚀 Deployment & Architecture

The app is containerized with Docker and runs **24/7 on Render's free tier** — with no cold starts.

```mermaid
flowchart LR
    subgraph CI["Reproducible build"]
        L["conda-lock.yml<br/><i>pinned, linux-64</i>"] --> M["micromamba<br/>base env"]
    end
    subgraph R["Render — Docker web service (512 MB)"]
        M --> S["Streamlit :8501"]
        S --> W["Per-session temp workspace<br/><i>swept when the tab closes</i>"]
    end
    K["⏱ cron-job.org<br/>every 10 min"] -->|"GET /_stcore/health"| S
    V["🌐 Visitor"] -->|HTTPS| S
```

### Tech stack & environment

| Layer | Choice | Why |
| --- | --- | --- |
| **Framework** | Streamlit (Python 3.12) | The whole GUI is Python; no separate front end to keep in sync. |
| **Environment** | micromamba | Small, fast, no full conda install inside the image. |
| **Dependency locking** | [`conda-lock.yml`](./conda-lock.yml) | Targeted at **`linux-64`** for exact cross-platform reproducibility — and to pin Streamlit, so an unpinned upgrade cannot break the custom GUI CSS classes the interface relies on. |
| **Container** | [`mambaorg/micromamba:1.5.8`](./Dockerfile) | A specific, locked base image rather than a floating tag. |
| **Host** | Render (free tier, Docker) | 24/7 public URL from a container built straight out of the repo. |
| **AI writing** | LangChain → OpenRouter (`meta-llama/llama-3.1-8b-instruct`) | Optional and off without a key. One small, quick model, streamed to the page as it writes. [Details below.](#-the-ai-writer-and-where-its-key-lives) |
| **Secrets** | Render environment variables | The key is never in the repo and never in the image; `.env` is for local use only. |

### Docker configuration

The container is built **strictly from the unified `conda-lock.yml`**, which sidesteps the OS-specific binary conflicts (Windows C++ runtimes, for one) that surface when a Windows-authored environment is resolved inside a Linux image at deploy time.

```dockerfile
FROM mambaorg/micromamba:1.5.8
COPY --chown=$MAMBA_USER:$MAMBA_USER conda-lock.yml .
RUN micromamba install --name base --yes --file conda-lock.yml && \
    micromamba clean --all --yes
COPY --chown=$MAMBA_USER:$MAMBA_USER . .
EXPOSE 8501
CMD ["micromamba", "run", "-n", "base", "streamlit", "run", "app.py", \
     "--server.port=8501", "--server.address=0.0.0.0"]
```

Streamlit defaults to port **8501**, and the Render environment variable `PORT=8501` is exposed so traffic routes to the containerized app.

### Continuous uptime on a free tier

Render spins free Docker instances down after **15 minutes** of inactivity, which means the next visitor waits through a cold start. To keep the app instantly responsive:

- An automated **cron job (cron-job.org)** sends an HTTP `GET` every **10 minutes**.
- **The optimization:** the ping targets Streamlit's hidden diagnostic endpoint **`/_stcore/health`** rather than the main URL. That registers as active web traffic to Render *without* forcing Streamlit to execute the Python script or render the UI — which conserves the container's **512 MB RAM** limit.
- **Quota management:** the strategy consumes **~744 instance-hours per month**, fitting inside Render's **750 free monthly hours** with deliberate headroom.

> The app behaves identically wherever it runs. There is no "local mode" and no "hosted mode", because a rule that only applies when deployed is a rule nobody has tested — `streamlit run app.py` on your own machine gives you the same ephemeral session folder that the container does.

### 🤖 The AI writer, and where its key lives

The third card, **🤖 I want AI to generate a 5 chapter mini-novel book**, takes a one-line description and hands you into the writing screen with a whole book in the editor: title, author, dedication and every chapter.

All of it is in [`Script/ai_book.py`](./Script/ai_book.py), which imports LangChain and nothing from Streamlit, and [`Script/ai_config.py`](./Script/ai_config.py).

**Five chapters, three requests, eight thousand tokens.** Three budgets, each enforced rather than hoped for.

*The chapter count* is fixed rather than suggested, because a model reads a length word as tone rather than as a number. It goes into the JSON schema as `minItems` and `maxItems`, is trimmed or padded again after the reply, and is printed on the button — so what the button promises is what arrives.

*The request count* is fixed because a request is the unit of everything costly here — waiting, tokens paid for, and on a copy set to a free model a ration of roughly **50 a day** shared by everyone using it. The chapters are batched into whatever calls are left after the outline, which brings a five-chapter book to three.

```text
request 1   outline      title, author, dedication, 5 headings + summaries
request 2   chapters 1-3
request 3   chapters 4-5
```

Batching is only safe because of `salvage_chapters`. A reply that runs past its output limit is truncated JSON — but the chapters that *did* finish are complete objects inside it, so they are mined out and kept. Recovering three good chapters costs nothing; asking again costs a request the book does not have.

**The outline is read the same way**, and it matters more there than anywhere: the plan is the one reply everything else is written from, so losing it costs the book its title, its author, its dedication and every heading at once. `close_json` closes off the brackets a truncated object never reached, and `salvage_outline` keeps the fields and the whole chapter entries that arrived. The cap has room in it too — an ordinary five-chapter plan measures about 280 tokens against a real tokenizer, against an `OUTLINE_CAP` of 640 — and that headroom costs the book nothing, because the ledger refunds whatever a reply did not use before a single chapter is priced.

**A cut-off reply does not arrive as a reply.** The OpenAI client will not hand over a completion whose `finish_reason` is `length` — it raises `LengthFinishReasonError` and discards the words. Since every request carries a `max_tokens`, filling the cap is the *ordinary* way a full batch ends rather than a rare mishap, so `_is_cut_off` recognises that exception and `_cut_off_reply` takes the chapters back out of it before anything downstream can mistake a short book for a broken one. Such a reply is never *repaired*: a repair carries a cap no bigger, runs out in the same place, and spends a request the remaining chapters need.

A reply that ran out is recognised in both the shapes it comes in. A **streamed** one raises only after the last piece has arrived, so `_read_stream` keeps the words *and* reports the fact; on the bottom rung of the JSON ladder there is no `response_format` at all, nothing raises, and the reply simply stops — which is what `ran_out` is for. Streaming is the default, so both paths carry the same guarantee: a reply that merely ran out never buys a repair that would run out in the same place.

**And a book always has a name.** When no title arrives at all, `title_from` takes one from the description the writer typed — a book about a lighthouse keeper is a better thing to find in the drafts list than "Untitled book". It is a working title, sitting in the first box on the writing screen, waiting to be typed over.

#### The 8,000-token ceiling

**A book costs at most 8,000 tokens, input and output, first request to last.** Not on average. Ever. `_Ledger` in [`ai_book.py`](./Script/ai_book.py) makes that a fact rather than an intention, and the argument has two halves:

| Half | Bounded by | How |
| --- | --- | --- |
| **Output** | the service | every request carries `max_tokens`, which the provider enforces |
| **Input** | this repo | it is text this repo composed, measured by `estimate_tokens` before it is sent |

So the worst a request can come to is *estimated input + `max_tokens`*, and one is only sent when that whole worst case still fits. The ledger charges the worst case up front, sends, then refunds whatever the reply did not use.

**Measuring the input is the hard half, and it is why there are two estimators.** Against a real tokenizer, English prose runs about 4.5 bytes to the token, pure punctuation 1.6, and combining accents 1.2. One ratio is therefore either wasteful for the text the app sends or unsafe for text somebody can paste into the box. So prompt text — written here, and held to its ratio by a test against a real tokenizer — is measured at 3 bytes to the token; anything that has been outside at 1.2, which nothing realistic beats.

**Running out is never an error.** When the ledger cannot afford a request, `from_plan` writes those chapters from their own outline summaries, for nothing. The same when the clock runs out, when the request budget runs out, when a reply is unreadable, and when a model refuses every rung of the JSON ladder. Every budget here says "no" rather than raising. **A shorter book, never a broken one.** A bad key, no credit, a service that is not answering at all — those still raise, because the reader has to be told about them.

Lowering `AI_TOKEN_LIMIT` shortens the book; a test walks it down to nothing and checks exactly that. Raising `AI_MAX_CALLS_PER_BOOK` gives more, smaller batches — not more tokens.

#### When the provider is busy

A **429** on a paid model is not a limit of OpenRouter's own — those apply to the free variants — it is the provider upstream being out of capacity. Which matters, because it means waiting works: the request was refused rather than rejected, and the same one a moment later usually goes through.

So `_wait_out_rate_limits` waits and sends it again, for as long as three things allow. `AI_RATE_LIMIT_RETRIES` caps the attempts; `AI_TOTAL_BUDGET_SECONDS` refuses to start a wait that would outlast the book; and the service's own `Retry-After` is honoured up to half a minute. **The retries do not come out of `AI_MAX_CALLS_PER_BOOK`** — a refused request generated nothing and was not billed, so what that budget rations is work asked for rather than packets sent. The wait is reported on the progress bar, at the position it already holds, so a busy service looks different from a stopped one.

The client's own retry is switched off (`max_retries=0`) so that all of this is the app's to decide: a retry inside the client is a wait nothing here can see, count against the clock, or judge not worth having.

`AI_REQUEST_GAP_SECONDS` puts a second between one request and the next, because three fired back to back is the pattern most likely to trip a burst limit in the first place.

If it is still busy when the retries run out, a **chapter batch goes quiet** like any other budget here and those chapters come from the plan. The **outline** is the one request that is reported instead, because there is no plan to write anything from if it never arrives.

#### Every chapter gets the same room

Chapter one and chapter five are written to the same length, and that takes two decisions made together, once, before any chapter is asked for:

| | What decides it |
| --- | --- |
| **How much each chapter may spend** | `chapter_allowance` — what is left after the outline, divided by the chapters sharing it. One number for the whole book. |
| **How long the prompt asks for** | `paragraph_plan` — cut to what that allowance buys, words first and paragraphs after, down to a single one. Never rounded *up* to a floor. |

Both halves matter, and the second is the load-bearing one. A model writes what the prompt asks for, so a chapter asked for three times what its cap can hold is not a short chapter — it is a reply chopped mid-word, salvaged into nothing, whose tokens are then left to whichever batch comes next. Sizing the ask to the allowance is what keeps `max_tokens` a backstop rather than a guillotine.

The ask is built against the cap the request was *granted* rather than the one planned, since it is the granted one the service enforces. `affordable_batches` trims the batch count to what the tokens can pay to send, because every batch carries the whole plan and costs input whether or not it has room to write with. `share` and the ledger still bind underneath — they are what keeps the 8,000 true whatever the allowance says — but in the ordinary case the allowance decides, and it decides the same for every chapter.

At the default ceiling that comes to the full three or four paragraphs of eighty words per chapter, whatever the length of the description: five even chapters, around 1,800 words, for roughly 3,500 real tokens of the 8,000.

**The book is streamed.** The reply arrives token by token and the words go onto the page as they are written, into a box under the button. It costs nothing and changes no request — the same complete reply is parsed by the same code when the stream ends. It does not make the model faster; it makes the wait readable.

Reading a book out of a reply that has not arrived yet is the interesting part, because a part-arrived JSON object is not JSON and `json.loads` will never take it. `_json_strings` reads the strings straight out of the fragment, telling a key from a value by tracking the containers it is inside, and returns the final unfinished string too — a half-written sentence is exactly what somebody watching wants to see. `stream_prose` lays those out; `_Live` keeps the finished chapters so the next batch appears *under* them rather than over them.

**It is optional.** With no key set the button is drawn switched off with a line explaining why, and the rest of the app is untouched. The LangChain import happens *inside* the call that needs it, so a machine without `langchain-openai` still runs the whole app — the test suite is proof, since it never installs it.

**The model is `meta-llama/llama-3.1-8b-instruct`.** One model, named, the same every time. Small is the point: at eight billion parameters it answers in a fraction of the time a large model takes. Which of the providers serving it answers is OpenRouter's decision, steered by a `provider` block the request carries — `sort: throughput` by default, so routing follows tokens per second rather than price. `OPENROUTER_PROVIDER_SORT`, `_ORDER` and `_IGNORE` change or clear it; fallbacks stay on either way.

*It is a paid model,* at $0.02 per million tokens in and $0.04 out, so a whole book costs a small fraction of a penny. `OPENROUTER_FREE_ONLY` therefore defaults to **0**, since a guard set to refuse every paid model would refuse the app's own default. **The credit limit on the key is the ceiling**, and the only control OpenRouter enforces for you. A copy that must not be able to spend anything sets `OPENROUTER_FREE_ONLY=1` *and* `OPENROUTER_MODEL=openrouter/free`.

*The JSON ladder earns its keep here.* An 8B model is less reliable about strict schemas than a large one, and support varies by provider. `RUNGS` starts at `json_schema`, drops to `json_object`, then to asking in the prompt, and remembers which rung worked.

The outline and style note go with *every* chapter request, because there is no conversation to remember them — each request stands alone, and the outline is what holds the voice together. The description is not sent again in full: the outline was written from it, and a trimmed line goes with each batch for flavour, which costs fewer tokens than the chapters a full repeat would displace.

#### The button knows you are typing

Streamlit only tells the server what is in a text box once the box loses focus, so a button gated on the server having a description cannot light up on the first keystroke. There is no Python for a keystroke either, so `TYPING_SCRIPT` at the foot of [`app.py`](./app.py) asks the browser directly, finding the widgets through the `st-key-…` classes their keys put on them. On every keystroke it switches the button and takes the bracketed **(Please type a description of the book to generate)** half of the label off or puts it back.

**Which way round it is switched is the whole trick.** Drawing the button off on the server and switching it on in the page gives a button that lights up and then eats the press: Streamlit's button ignores a click whenever *React* thinks it is disabled, and React believes what the server told it. Clearing the attribute changes the colour and lets the browser fire its events, and the component drops them anyway.

So it goes the other way round. **The server draws the button on**, the script makes it look and behave off while the box is empty — `disabled` greys it, blocks the pointer and takes it out of the tab order — and the click handler refuses an empty description on the server. React is never told the button is off, so the first press after typing is a real one, and it carries the box's value with it because losing focus to the button is what flushes it.

**The server still decides.** The script only changes what is drawn between reruns, and a test pins the words it puts on the button to the same string the label is built from. It is also told when the button is off for a reason unrelated to typing — a job running, a full disk, no key — and then keeps its hands off entirely.

**Two things about it broke silently once, and both are now pinned by tests.** The first was a selector: it looked for an `input`, and stayed looking for an `input` when the description grew into a text area. `querySelector` found nothing, the script returned at its first line, and the button sat there enabled with an empty box. Nothing errored. The selectors are now built from the widget keys in [`Script/ai_view.py`](./Script/ai_view.py), and a test asserts the box the app draws is the tag the script looks for.

The second is subtler and is why the script no longer *contains* the watcher. It runs in a one-pixel iframe, and Streamlit rebuilds that iframe whenever the script's text changes — which is exactly when the lock flag flips, which is exactly when somebody walks onto this screen. A listener registered from inside the frame dies with the frame, while an "already bound" flag on the parent survives it, so every later frame skipped the registration it needed. The frame now appends the watcher to the page as a `<script>`, so the code and the flag guarding it live in the same realm and last as long as the tab; the frame only hands over this run's facts. The watcher also coalesces its work into one pass per animation frame — its own DOM writes wake the `MutationObserver` watching them, and a mutation made from a microtask can queue the next one forever without a frame going by, which freezes the tab rather than slowing it.

#### Setting the key on Render

**The key is not part of the deploy.** Render pulls the code from GitHub but reads the key from its *own* settings, which never touch the repository — two separate paths into the container. Set it once, by hand, and every future `git push` picks it up.

```mermaid
flowchart LR
    D["💻 Your machine<br/><code>.env</code><br/><i>git-ignored</i>"] -.->|"never pushed"| G
    G["📦 GitHub<br/><i>public repo</i>"] -->|"auto-deploy on push"| R
    K["🔑 Render → Environment<br/><code>OPENROUTER_API_KEY</code><br/><i>set once, by hand</i>"] -->|"injected at run time"| R
    R["🐳 Render container<br/><code>os.environ</code>"]
```

**Do this once:**

1. Get a key at **[openrouter.ai/keys](https://openrouter.ai/keys)**. Make it a key used by nothing else, and **set a credit limit on it** while you are there.
2. Open your service in the **[Render dashboard](https://dashboard.render.com)** → **Environment** in the left-hand menu.
3. **Add Environment Variable**:
   - **Key:** `OPENROUTER_API_KEY`
   - **Value:** your key
4. **Save Changes.** Render redeploys on its own — the variable change *is* a deploy trigger, so you do not need to push anything to activate it.

That is the whole setup. `OPENROUTER_MODEL` and the rest have working defaults; add them only to override. Environment variables persist across deploys, and `[skip render]` has no bearing on the key either — it only stops a rebuild.

> **Use "Environment Variables", never "Secret Files" or a Docker build argument.** A build argument is recorded in the image history and can be read straight back out of the image. `.env` is for your own machine only, and is both git-ignored and Docker-ignored.

#### Checking it worked

Open the app after the deploy finishes. The third card, and the screen behind it, tell you which state you are in without needing the logs:

| What you see | What it means |
| --- | --- |
| The **🤖** card is live, and **🤖 Write my 5 chapter mini-novel** is clickable once you type a description | The key arrived. Done. |
| The card is greyed out and reads *"Switched off on this copy — no API key is set"*; the screen behind it says *"…No `OPENROUTER_API_KEY` is set…"* | The variable is missing or misnamed. Check the spelling in Render — it is case-sensitive. |
| Card greyed out, the screen behind it mentions **langchain-openai** | The key is fine but the image is stale. Redeploy with **Clear build cache**, since `conda-lock.yml` changed. |
| *"…is not a free model, and this copy is set to free models only"* | `OPENROUTER_FREE_ONLY` is at 1 while the model is a paid one — the app's own default included. Switch the flag off, or set `OPENROUTER_MODEL` to `openrouter/free`. |

#### Every setting

All optional except the key, all read from the environment, all documented in [`.env.example`](./.env.example). A bad number falls back to its default rather than taking the app down.

| Variable | Default | What it does |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | *(none)* | The key. Empty or unset switches the button off; everything else still works. |
| `OPENROUTER_MODEL` | `meta-llama/llama-3.1-8b-instruct` | The model that writes the book. `openrouter/free` (the free-model router) and any `:free` model work too. |
| `OPENROUTER_FREE_ONLY` | `0` | Refuse anything that could be charged for, before any request is made. Off, because the default model is paid. Set it to 1 *and* pick a free model together. |
| `OPENROUTER_APP_TITLE` | `Bookbinding Signature Creator` | The `X-Title` header — how OpenRouter's dashboard labels this app's traffic. Identifies the app, never the visitor. |
| `AI_STREAM` | `1` | Show the book as it is written instead of all at once at the end. `0` waits for the whole reply. |
| `AI_TOKEN_LIMIT` | `8000` | Every token one book may cost, input and output. A ceiling that is kept, not a target. Lower it for a shorter book. |
| `AI_CALL_TIMEOUT_SECONDS` | `120` | How long one request may take. A batch of three full-length chapters is the long one. |
| `AI_TOTAL_BUDGET_SECONDS` | `420` | How long a whole book may take before it stops asking for more. |
| `AI_CHAPTERS` | `5` | How many chapters every book has. Exactly this many — see below. |
| `AI_MAX_CALLS_PER_BOOK` | `3` | The most requests one book may cost. Not more tokens — see the ceiling above. |
| `AI_RATE_LIMIT_RETRIES` | `2` | Extra sends of one request while the service is busy. They cost time, not requests. |
| `AI_REQUEST_GAP_SECONDS` | `1.0` | The least time between one request and the next, so a book is not a burst. |
| `OPENROUTER_PROVIDER_SORT` | `throughput` | How OpenRouter picks between the providers serving the model: `throughput`, `latency`, `price`, or `none` to let it balance as it likes. |
| `OPENROUTER_PROVIDER_ORDER` | *(none)* | Comma list of providers to try in order. |
| `OPENROUTER_PROVIDER_IGNORE` | *(none)* | Comma list of providers to skip — for routing around one that is reliably busy. |

#### Why it cannot leak

The repository is public and so is the deployed URL, so the key is never in either. `load_dotenv(..., override=False)` means that even if a `.env` somehow reached a server, it could not shadow the key that server was configured with — there is a test for exactly that.

What stops it leaking, in order of how much each one matters:

1. **[`.dockerignore`](./.dockerignore)** — the `Dockerfile` ends in `COPY . .`, so without it a local `.env` would be baked into a layer of a public image, and a layer survives being deleted in a later one.
2. **[`.gitignore`](./.gitignore)** covers `.env`, `.env.*` and `*.env`, not just the one name the GitHub template ships with — a routine `cp .env .env.local` while debugging is otherwise a committed key.
3. The key is **read from the environment, used and dropped**. Never in `st.session_state`, never on a module global, never on a `Manuscript` — that last one because **📤 Save my data** zips the drafts folder and hands it to the browser.
4. Every error is re-raised **scrubbed**, matching both the configured key and the `sk-or-v1-…` shape, outside the `except` block so the original is not even reachable as `__context__`. `showErrorDetails = "none"` means no traceback is ever drawn on a public page.
5. **The request budget.** Three requests a book, enforced by `_Budget`, so a book cannot cost an unbounded number of calls however badly the model behaves.
6. **Free models only, on request.** `OPENROUTER_FREE_ONLY=1` refuses any model that is not `openrouter/free` or `:free` *before the client is constructed*. Off by default now the default model is paid, and there for a copy that must not be able to spend anything.

**Do these two things on openrouter.ai, because no code here can:** use a key dedicated to this app, and **set a credit limit on it**. That limit is what makes the worst case a refused request instead of a bill, and it is the only control OpenRouter enforces for you.

To confirm the key was never committed, and that it is not in the image:

```bash
git check-ignore -v .env          # must print the .gitignore rule that catches it
git log --all --oneline -- .env   # must print nothing at all
docker build -t bsc . && docker run --rm bsc ls -a /app   # must not list .env
```

If it ever *did* reach a commit, rotate the key rather than trying to rewrite history — a pushed secret should be treated as burned.

---

## 🧠 Engineering highlights

The seven design decisions that shape the rest of the codebase.

<table>
<tr><td>

**🗺 One question on the front page, one flow behind each answer**

Three cards, each naming a thing somebody might arrive wanting and what they get out of it. Behind each is a single screen of three numbered steps ending in a download, and a **← Home** to leave by. Every setting lives on the screen it affects, in one of three tiers: the input that defines the job, one collapsed expander named for what it changes, and the rare things nested inside that. `Script/settings.py` is what makes that possible — it reads a setting's value *without drawing it*, so nothing has to sit in the sidebar merely to be readable.

</td></tr>
<tr><td>

**🔒 Privacy by construction, not by policy**

Nothing uploaded is retained. Each visitor gets one folder under the system temp directory, named after their Streamlit session ID, and **three overlapping guarantees** remove it: a sweeper thread that checks every 15 seconds which sessions still have a browser attached, a shutdown hook, and an age-based orphan sweep for when the runtime can't be read at all. Three, because one would be a single point of failure — and the fallback is *age*, never "delete everything", since guessing wrong would erase somebody mid-sentence.

</td></tr>
<tr><td>

**📐 The imposition is derived from physics, not from a lookup table**

A sheet is folded across its width, so one sheet of *W × H* gives four book pages of *W/2 × H*. Every paper decision in the app follows from that one fact. The fold is the middle of the source page *by definition*, so no column setting can move it — and the tests **prove** that rather than asserting it (see below).

</td></tr>
<tr><td>

**🧪 Tests that refuse to mark their own homework**

551 tests that read ink positions back out of finished PDFs, simulate the physical fold independently of the production formula, derive expected coordinates by hand rather than importing them from the code under test, drive the real interface through Streamlit's own `AppTest`, and feed the AI writer the malformed JSON small models really send. [Details below.](#-testing)

</td></tr>
<tr><td>

**⚙️ Concurrency-safe UI in a framework that reruns your script on every click**

Streamlit restarts the script whenever a widget changes. A click landing mid-conversion doesn't just redraw the page with stale numbers — it *kills the job*. So a job is claimed, the script reruns immediately to paint the entire interface locked, and only then does the first page get imposed. The progress bar takes over the exact slot its button was in, so nothing on the page moves. A job also **pins the screen it was claimed on**: it reports into that slot, so the route cannot change out from under it.

</td></tr>
<tr><td>

**📊 A quota enforced in five places, because four of them aren't enough**

A per-file cap isn't a cap (nothing stops the next file); a pre-flight check isn't a cap either (nothing knows how big a set of signatures will be until it has written them). So the limit is enforced before upload, before a zip loads, *during* the conversion via a progress-hook watcher, at Streamlit's own `maxUploadSize`, and in the app code the config option cannot reach. [The reasoning.](#-your-data-in-and-out-as-one-zip)

</td></tr>
<tr><td>

**♻️ Atomic writes everywhere something could be lost**

Reconversion builds into a staging folder and swaps it in only once the whole set exists, so a failed run leaves the previous complete set exactly as it was. Draft saves go through a temp file and a rename, so a crash part way can't leave a half-written draft on top of the one it replaced.

</td></tr>
</table>

---

## 🏃 Run it yourself

### Use the hosted app

**[bookbinding-signature-creator.onrender.com](https://bookbinding-signature-creator.onrender.com)** — nothing to install.

### Docker

```bash
docker build -t bookbinding .
docker run -p 8501:8501 bookbinding
```

### Local Python

```bash
conda env create -f environment.yml     # or: micromamba create -f environment.yml
conda activate BookBinding
streamlit run app.py
```

To use the AI writer locally, add a key. Skip this and everything else still works — the button is simply drawn switched off.

```bash
cp .env.example .env      # then put your OpenRouter key in it
```

<details>
<summary><b>Pinned alternative:</b> <code>pip install -r requirements.txt</code></summary>

`requirements.txt` carries the fully resolved pin set (Streamlit 1.59.2, pypdf 6.14.2, reportlab 5.0.0, pandas 3.0.3, pillow 12.3.0). Use `conda-lock.yml` for anything that has to be byte-reproducible; use `requirements.txt` for a quick venv.

</details>

### Headless CLI

`python main.py` works on your own files on your own machine, and is the one place the four folders exist as ordinary folders beside the app — it creates them on demand. Nothing the web app does ever writes there.

```bash
python main.py --sheets 5 --paper A4
python main.py --sheets 5 --paper Letter
python main.py --paper 12x9in --scale actual-size --short-edge
python main.py --number          # stamp page numbers instead of converting
python main.py --list-paper      # print the paper and book size tables
```

`--margin`, `--gap` and `--column-width` are in inches; `--paper` carries its own unit.

---

## 🖨️ Using the app

<details open>
<summary><b>The interface at a glance</b></summary>

<br/>

**Three cards, and one flow behind each.** The front page asks *what would you like to
do?* and answers in the reader's own words rather than the app's: **📄 I have a PDF book**,
**✍️ I want to start/continue writing my own book**, **🤖 I want AI to generate a 5 chapter
mini-novel book**. Each card names what comes out of it, and the whole card is the click
target. A card this copy cannot offer — the AI one, on a copy with no key — is drawn
switched off and says why, rather than disappearing. Underneath, **New here?** explains what
a signature is, which is the one word the whole app is named after.

**Hovering a card moves nothing**, and that is a fix rather than a preference. The hover
style used to lift each card two pixels with a CSS `transform`, which moves the element's
hit area with it: a cursor near the bottom edge hovered the card, the card slid out from
under the cursor, the hover ended, the card slid back — several times a second. What that
looked like was the pointer flickering between a hand and an arrow with *Start →* strobing
underneath it, and cards that were very hard to actually click. Hover is colour, shadow and
outline only now, and the pointer stays a hand across the whole card, border included.

Come back to the front page with work in progress and it offers a way back into it:
*You have a book in progress*, *2 books ready to print*.

Every screen behind a card carries **← Home** and numbered steps:

| Screen | Step 1 | Step 2 | Step 3 |
| --- | --- | --- | --- |
| 📄 Convert a PDF | Add your PDF | Choose your paper | Create and download |
| ✍️ Write your book | Write it | Design it | — |
| 🤖 Mini-novel | Describe the book — then it hands you into the writing screen with the book in the editor | | |

The writing screen has no third step, because the thing a third step would lead to is at
the *top*: **⬇️ Take your book away**, three download buttons on one row above everything
else. Writing and design are the two steps; taking the book away is the outcome, and it is
the first thing on the screen rather than the last.

**Everything is on the screen it belongs to, in one of three tiers.** Tier one is the one
input that defines the job and the one button that produces the output, and nothing else.
Tier two is a single collapsed expander named for *what it affects* — never "advanced
settings". Tier three nests inside that.

So the conversion screen shows **Sheet size** and nothing else about paper, with the
orientation, the fitting mode, the sheets per signature, the duplex setting and the stamped
page-number positioning one click down in **⚙️ Advanced paper and printing** — and a live
caption under it saying what the choice came to. The writing screen shows a one-line
summary — *A5 pages · Times 10.5 pt · justified* — above three closed panels, so nothing
has to be opened to see what size book is about to be made.

**The sidebar is preferences only**: the theme and the display units, neither of which
changes any book. Everything that shapes one — *sheets per signature*, *printer duplex* —
sits beside the thing it affects, on whichever screen is up, under one shared pair of keys.
`Script/settings.py` is what allows that: `resolve()` reads a setting without drawing it,
so a control can live three expanders deep on one screen and still be read by the job
runner on another.

**Everything ends in a download.** A converted book gets **⬇️ Download this book's
signatures** — every signature in print order with its printing notes, as one zip, because
they are printed as a set and fetching them one at a time only creates a chance to print
them out of order. A typed book gets all three of the things its card offers, side by side
at the top of the screen: **⬇️ Download as JSON**, **⬇️ Download as PDF** and **⬇️ Download
as signatures**. JSON is the format that comes back in, through **📂 Open a .book.json** at
the foot of the same screen, so a book can be put down and picked up again.

**All three are one click, and none of them keeps anything.** The PDF and the signatures
used to be "📄 Create the book PDF" and "✂️ Create the signatures": buttons that wrote files
into the session and then offered a *second* button to fetch them, at the foot of the
right-hand column, underneath the drafts panel. They are `st.download_button`s now, and
Streamlit calls a download button's `data` when it is clicked — so the book is typeset, and
imposed, inside the click, and the bytes go straight to the browser. The build happens in a
scratch folder that is deleted as soon as the download is served, so nothing counts against
the session's limit and there is nothing left to find afterwards. `Script/book_build.py` is
that code, and its docstring says what the single click costs: no progress bar, and a
failure that arrives as a failed download rather than as a banner.

No paths are shown anywhere. Each book waiting to be converted names the two sizes you act
on — the **paper to load in the printer** and **each page of the finished book** — so there
is no guessing which number describes what.

**The archive and the finished-signature list are folded away** into **📂 This session's
files**, and the drafts list into **📂 This session's drafts** — there for a batch, out of
the way otherwise. Nothing here is kept past the tab, so the download is the point and the
lists are the footnote.

**The data policy sits behind a 🔒 expander at the foot of every screen**, home included.
Fourteen sections of notice printed under three cards would be a page of legal text with
the work at the bottom of it; folded, the page stays clean and the notice is still one
click away wherever you happen to be standing.

**🗑 Delete** in the archive removes an input PDF for good; in Ready to print it removes one
book's signatures and notes. Deleting is the only thing here that cannot be undone, so it
always asks first — **Yes, delete** and **Keep it**, under the card naming the file. Walking
away from a screen clears anything armed on it, since the two buttons that answer a delete
are only drawn under the card that armed it.

**Nothing can be walked away from mid-job.** ← Home and the three cards go dead while a job
runs, and the job additionally *pins* the screen it was claimed on — because a job reports
into the slot its button occupied, and one whose screen is not drawn would be released
silently with the work never started and nothing said.

The interface ships a light-green paper-and-foliage theme in `.streamlit/config.toml`, in a
light and a dark version; **Theme**, at the top of the sidebar, switches between them.

</details>

<details>
<summary><b>Paper sizes and fitting</b></summary>

<br/>

**A sheet is folded across its width.** One sheet of *W × H* paper gives four book pages of *W/2 × H*. That single fact is what everything below follows from: A4 landscape folds to A5 pages, Letter landscape folds to Half Letter pages, and a 6 × 9 in book needs 12 × 9 in paper.

By default the output sheet is **the input PDF's own page size**, so nothing is scaled and a book laid out for A4 landscape prints exactly as drawn. Choose a sheet size when your PDF was made for paper you do not have, when you want a smaller or larger book than the PDF was drawn for, or when you want to print on big paper and trim.

#### Sheets you can print on

`--paper <name>`, or the **Sheet size** menu in Step 2 of the conversion screen. 21 sizes:

| Family | Sizes |
| --- | --- |
| ISO A | A2, A3, A4, A5, A6 |
| ISO B | B3, B4, B5, B6 |
| JIS B | JIS B4, JIS B5, JIS B6 |
| North American | Letter, Legal, Half Letter, Executive, Tabloid, ANSI C |
| Oversize | SRA4, SRA3, Super B (A3+) |

Anything else goes in as dimensions: `--paper 12x9in`, `--paper 297x420mm`, `--paper 30x21cm`, or the **Custom size…** entry in that same menu. Named sizes are used landscape by default, which is nearly always what you want; `--portrait` (or the **Sheet orientation** control beside the menu) turns one on its side, and the app warns you that the result is a tall, narrow book page.

#### Book sizes

`python main.py --list-paper` also prints the finished sizes a bound book is normally trimmed to: mass-market paperback, A6 pocket, A-format, Novella, Digest, B-format, A5, US trade, Demy and Royal octavo, comic, Crown quarto, Letter and A4 — with the sheet each one needs and the smallest standard stock that sheet fits on. The same tables are in the GUI under **Paper and book size reference**.

#### Fitting

This concerns an uploaded PDF only — a book typed into the editor is set for its paper in the first place. When the sheet is not the size of the input page, each book page is scaled and centred in its half of the sheet:

- **Fit each book page to the sheet** (default) scales it until it fills the paper, keeping its proportions. If the sheet is a different *shape*, the leftover appears as extra blank margin and the app says how much.
- **Keep the original size, centred** never resizes anything, and refuses the job outright if the book will not fit. Use it when the margins matter more than filling the sheet.

The scale factor is reported before conversion and written into `print_instructions.txt`. Print at **100% / "Actual size"**: the pages are already the size of your paper, and letting the printer scale them again compounds the two. The app also warns when shrinking has pushed the outer margin inside the ~0.25 in most printers cannot reach. The fit is worked out per source page, so a PDF whose pages differ in size still comes out on uniform sheets.

#### Column layout

The column measurements describe the **input** PDF, not the paper, and do exactly one thing: place stamped page numbers. **Imposition never reads them.** The fold is the middle of the source page by definition, so no column measurement can move it and none is ever a reason to refuse a book.

By default the columns are **fitted to each book's own page**, keeping the margin and gap, so any page size works with no setup. Untick **Fit the columns to each PDF** under 🖨️ Paper to print on (or pass `--column-width`) to set the width by hand; each book card then offers a **Fit the columns to this PDF** button to put it back. Hand-entered measurements that do not match the book are reported only as notes about where the page numbers would land.

Pages carrying a `/Rotate` flag, a crop box smaller than the paper, or a media box that does not start at the origin are handled as a PDF reader displays them.

</details>

<details>
<summary><b>Writing a book in the app</b></summary>

<br/>

**✍️ I want to start/continue writing my own book** opens an editor that produces the same kind of 2-column PDF the converter reads. Nothing about the imposition is special-cased for it.

**Nothing is ever scaled here, at any paper size.** Words have no size until the type is drawn, so the paper is not something to fit a finished book onto afterwards; it is the size the book is *set* at, half a sheet to a page. Pick any paper you own and the type goes into the PDF at its final size, at 100%. Changing the size changes how big the book is and how many pages it runs to, and nothing else.

**One menu, either way round.** *📐 Page size and margins*, in Step 2, starts with **Give the size as**: *The finished page* offers the book sizes in the reference table plus a custom one, *The paper it prints on* offers the sheets. Whichever you pick, the other figure is worked out and printed under the menu, and the menu you did not pick is not on the page at all. Giving the size as paper belongs to that build and never reaches the saved draft.

#### What you can type

| Part | Sections available |
| --- | --- |
| Title page | title, subtitle, author, series, and a copyright page built from publisher, year, edition, ISBN, a copyright line and any other rights text |
| Front matter | dedication, epigraph, foreword, preface, acknowledgements, introduction, prologue, a note to the reader, or a section of your own |
| The book | chapters, part dividers, interludes, unnumbered sections |
| Back matter | epilogue, afterword, author's note, acknowledgements, appendix, glossary, notes, further reading, bibliography, about the author, also by the author, colophon, or your own |

Every section is a heading and a text box, and can be moved up or down, duplicated or removed; a removal can be undone with one click. Only sections added as **Chapter** join the numbering, so a prologue or an appendix never becomes "Chapter 4".

#### How to type the text

Plainly. Six things mean something: a blank line starts a paragraph, `*stars*` or `_underscores_` are italic, `**two stars**` are bold, `# a leading hash` is a heading inside the section (`##` for a smaller one), a line of `***` or `---` is a scene break, and lines starting with `>` are a quotation that keeps its line breaks. A single line break inside a paragraph is treated as wrapping, exactly as it is in any other text box.

#### The book it builds

You choose the **finished page size** (any of the book sizes in the reference table, or a custom one), the four margins, the typeface and size, the line spacing, whether the text is justified and how paragraphs are marked. The PDF it writes has pages **twice as wide** as the finished page, because two book pages sit side by side on every sheet — so an A5 book comes out as an A4-landscape PDF that prints on A4 with nothing scaled at all.

It sets a title page, a copyright page, a table of contents with the page each section actually starts on, running heads, and page numbers centred at the foot, all of which can be switched off. Sections start on a new page by default, or on a right-hand page like a printed book, which looks right and costs paper.

The copyright page is printed only once one of the **Publication details** (publisher, year, edition, ISBN, a copyright line, or any other rights text) is filled in. Naming an author is deliberately not enough — that box lives on the title page and every book fills it in, so deriving a copyright page from it would put an extra leaf into every book unasked.

Five typefaces are available: Times, Helvetica, Courier, Bitstream Vera, and the app's own Baskervville, which comes in one weight, so bold and italic are set in the regular face. Characters no typeface here can set (Greek, Cyrillic, CJK) are reported rather than silently dropped.

#### Having one written for you

The third card, **🤖 I want AI to generate a 5 chapter mini-novel book**, opens a screen with
one question on it. Until you have typed something the button is greyed out and says
**(Please type a description of the book to generate)** on itself; type the first character
and it goes live there and then, without your having to click away first. The one thing in
this app that sends anything anywhere says so beside that button, not in a caption
underneath it.

Describe the book — subject, voice, who it is for, what should happen — and press it. **The
book appears under the button as it is written**, a sentence at a time. The screen stays put
while it writes, and that is deliberate: the progress bar and the streaming box both live
here, and a job whose screen stops being drawn is a job the runner abandons.

When it finishes you are moved to **✍️ Write your book**, with the title, author, dedication
and five chapters already in the editor. The banner there leads with the move — you pressed
a button on one screen and the whole page changed, so being told *where you are and why* is
worth more than being told what the book is. It does not tell you to save it: a draft lives
in this browser session and dies with it, and the three download buttons at the top of that
screen are the ones that actually keep a book. The AI screen says the same thing before you
press, so the move is expected rather than surprising.

**It is always five chapters**, whatever the description says, so describe *what happens* rather than how long it should be. "A short novel" or "an epic" changes the voice and the pacing, not the count.

Four more things worth knowing:

- **Whatever was in the editor is kept first.** If it was already saved it stays where it is; if it had unsaved words they go to a draft of their own beside it, and the banner afterwards names it. Nothing you typed is the price of pressing the button.
- **It writes the words only.** Page size, typeface, margins, spacing and every other design choice are left exactly as you set them.
- **A mini-novel is what it says.** Each book is capped at 8,000 tokens all in, and the five chapters share what is left once the plan is written — about 350 words each, and **always the same length as one another**. If a chapter arrives as a single line describing what happens in it, that is the floor of the design: there were not tokens enough to ask for it, so its plan entry went into the editor for you to write over. You always get five chapters and never an error.
- **Treat the description box as public**, because it is the one thing on the page that leaves the server. It goes to whichever provider OpenRouter routes it to, and some providers keep what they are sent and may train on it. Describe a book in it and nothing else — [the data policy](#-your-data-in-and-out-as-one-zip) is explicit about this.

What comes back is a **draft, not a finished book**. A model invents: it can be confidently wrong, and it can land close to something it was trained on. Read it before you print it, and read it properly before you publish it.

If the button is greyed out, this copy has no key configured — see [the AI writer](#-the-ai-writer-and-where-its-key-lives). Everything else works exactly the same without it.

#### Keeping your progress

To keep a book past the session, **⬇️ Download as JSON** at the top of the screen hands over
the book as it is on screen — saved or not — and **📂 Open a .book.json** takes it back.
JSON is the only format that comes back in, which is the round trip the second card
promises. **✨ Load the example** fills the editor with a complete lorem-ipsum book (five
chapters, a part divider, an interlude, a dedication, an epigraph and a spread of back
matter) to type straight over.

Drafts are the *other* thing, and they are deliberately further down the page now, folded
into **📚 Drafts kept in this browser session** below the writing. They are one JSON file
each, kept for the session only: save as many as you like and switch between them, and
**Autosave** keeps writing to the open draft once it has been saved once. Saving goes
through a temp file and a rename, so a crash part way cannot leave a half-written draft on
top of the one it replaced, and anything that would throw away unsaved words asks first.
**📤 Save my data** takes every draft at once, for carrying a whole session somewhere else.

None of that is needed to get a book out, which is why it is no longer the first thing on
the screen. It used to open on a draft-name box, **💾 Save**, **Save a copy** and an
autosave tick — session bookkeeping, above the book, for a visitor who has not typed a word
yet and whose drafts will be gone when the tab closes.

**What these files are called, and the paper they are for** — the expander under the three
buttons — names the same two sizes the conversion screen puts on every card, worked out from
*Page size and margins* before anything is built. Every paper size can be used, since the
book is set at half of whatever sheet you pick; on the rare sheet a book could not physically
go on, **⬇️ Download as signatures** is disabled and says why, while **⬇️ Download as PDF**
stays available because that half would have worked.

</details>

<details>
<summary><b>Page numbering</b></summary>

<br/>

Imposition never touches the book's own content, so a book that already has printed page numbers just works. For one that does not, **Number the pages** (GUI) or `python main.py --number` (CLI) stamps a number at the foot of each column and saves the result as `<name>_Numbered.pdf` next to the original. The columns are fitted to each page, so the numbers land correctly whatever its page size and even if the pages differ. The original is left alone, so a wrong column layout costs nothing.

**Both halves of the app number pages the same way**, through the same routine (`print_formatting.draw_folio`): **centred at the foot of the book page**, on a baseline set as a share of that page's bottom margin. A converted PDF and a typed book are indistinguishable on that point.

</details>

<details>
<summary><b>The top-right corner (and why it's empty)</b></summary>

<br/>

Streamlit's own developer controls are stripped out, because this is a finished tool rather than an app someone is building — and two of them are actively dangerous here. **Stop** and **Rerun** both cut a conversion off mid-write, which is the one thing the page-drawing order is arranged to prevent.

`client.toolbarMode = "minimal"` in `.streamlit/config.toml` removes **Deploy**, **Rerun**, **Auto rerun**, **Clear cache**, **Print** and **Record screen**, and disables the `C` clear-cache keyboard shortcut. That leaves the System / Light / Dark switcher, an **About** entry, and the *Made with Streamlit* line, and no config option reaches any of them.

They cannot simply be left unbuilt. In minimal mode Streamlit builds the toolbar only for an app that has defined a menu item of its own, so dropping the About entry takes the whole corner with it — switcher included, and the switcher is the only way into the theme that does not throw the session away. So the About entry stays, a style block in `app.py` hides the corner, and **Theme** at the top of the sidebar drives the switcher inside it: a one-pixel `st.iframe` at the foot of the page opens the hidden menu, clicks the mode you chose and shuts it again. The same block puts the header strip back to transparent and click-through, leaving only the **⟩⟩** that appears when the sidebar is folded away.

`server.fileWatcherType = "none"` drops the source-file watcher and its "File change. Rerun / Always rerun" prompt; set it back to `"auto"` when working on the app itself.

That style block also handles two things no config option reaches: the **Stop** button with its running figure, and the "Is Streamlit still running? … `streamlit run yourscript.py`" dialog that appears once the server goes away — hidden only while the connection is down, so ordinary dialogs still work. Telling someone who started the app from a shortcut to retype a shell command is worse than saying nothing.

One Streamlit reflex survives all of this: pressing **R** outside a text field reruns the script, and doing that during a conversion aborts it. Nothing short of injected JavaScript turns that off — and the staging folder means an aborted run still leaves the previous, complete set of signatures intact.

</details>

---

## 🔒 Your data, in and out as one zip

**Nothing this app holds is stored.** Whatever you upload or write exists on the server only while you are working on it, and is erased when you close the tab. The one copy that lasts is the zip you download yourself. That is a position, not an accident of hosting: whatever somebody uploads is theirs, and the way not to be answerable for it is not to keep it.

**The one exception, stated plainly:** pressing **🤖 Write my 5 chapter mini-novel** sends the sentence you typed in its box — and nothing else from your session — to OpenRouter. Never press it and nothing you do here ever leaves the server. The screen says so beside the button, and the app's own data policy says the same in more detail.

**The policy is behind a 🔒 expander at the foot of every screen**, the front page included. It is fourteen sections long, and unfolded under three cards it would be a page of legal text with the work underneath it — so it is folded, the page stays clean, and the notice is one click away from wherever you are standing rather than somewhere else entirely.

**Every screen ends in a download**, and that is the ordinary way work leaves: **⬇️ Download this book's signatures** on a finished conversion, and **⬇️ Download as JSON**, **⬇️ Download as PDF** and **⬇️ Download as signatures** at the top of the editor. Under **💾 This session's data** in the sidebar are the three controls for a whole session at once:

| Control | What it does |
| --- | --- |
| **📤 Save my data (.zip)** | Hands you everything in the session — input PDFs, the archive, finished signatures and drafts — as one zip. Save it before you close the tab, or it is gone. |
| **📥 Load my data (.zip)** | Puts one of those zips back at the start of your next visit. It *replaces* the session rather than adding to it, so it asks for a second click first. A zip holding none of the app's folders is refused before anything is touched, and the zip is checked end to end first, so a half-finished download cannot leave you half-emptied. A folder you zipped by hand is read too. |
| **🗑 Delete my data now** | Erases the session immediately, without waiting for the tab to close. Two clicks, like every other delete here. |

A single book comes back the same way it left: **📂 Open a .book.json** in the editor takes a file **⬇️ Download as JSON** handed you.

<details>
<summary><b>How the erasure actually works</b></summary>

<br/>

The imposition and typesetting code writes real PDFs, so there has to be somewhere on disk. There is exactly one such place per visitor, and [`Script/workspace.py`](./Script/workspace.py) is the whole of it:

```text
<system temp>/bookbinding_sessions/<streamlit session id>/
    Input/  Output/  Previously_Converted/  Manuscripts/
```

Named after the session, so it cannot be found by guessing or shared between visitors; under the system temp folder, so it is never anywhere near the app's own source; created fresh, so a new visitor starts empty however the last one left. `open_session` points `main`'s four folder names into it, and everything below `app.py` goes on writing to the names it always used without ever learning that they move.

Three overlapping guarantees take it away again, because one would be a single point of failure:

- **The sweeper.** A daemon thread wakes every 15 seconds, asks the Streamlit runtime which sessions still have a browser attached, and deletes the folder of every session that does not — after a 30-second grace, so a momentary network blip does not cost somebody their book. Closing the tab therefore erases everything within about half a minute, with nothing asked of the visitor.
- **Shutdown.** Everything the process created is removed when it exits, so an instance going to sleep takes the files with it.
- **The orphan sweep.** If the runtime cannot be read at all, a folder untouched for an hour is removed anyway. Not being able to tell which sessions are live never means "delete everything" — that would erase somebody mid-sentence — so it falls back to age instead.

Uploaded bytes live in Streamlit's in-memory uploaded-file manager and go with the session. Nothing is logged or copied elsewhere, and Streamlit's anonymous usage statistics are off (`gatherUsageStats = false`), so no telemetry leaves either.

The one thing that is sent anywhere is opt-in by being a button: the description typed into the AI box. **No file, no draft, no manuscript and no file name is ever part of that request** — `ai_book.write_book` takes a string and a `Design`, so there is no argument through which a book could be passed to it. A property of the signature rather than a promise in a comment.

</details>

<details>
<summary><b>How much one session may hold</b></summary>

<br/>

Two numbers, and they are deliberately not the same one:

| Limit | Meaning |
|---|---|
| **500 MB** | everything one session may hold, together — uploads, the archive, finished signatures and drafts (`LIMIT_BYTES`) |
| **100 MB** | the most any single uploaded book PDF may be (`MAX_UPLOAD_BYTES`) |
| **500 MB** | the most a loaded data zip may unpack to, which is the session limit by definition |

Both live in `Script/workspace.py`, and a bar at the top of the sidebar shows how much of the session is gone.

**A book is capped at a fifth of the session on purpose.** If one PDF were allowed to fill the session, uploading it would succeed and then nothing could be done with it: converting writes its signatures, which come to about the size of the book again, and there would be nowhere to put them. Every button on that book would be dead, which is the worst way to meet a limit — nothing said no until it was too late for it to help. At 100 MB the worst case is a 100 MB book, ~110 MB of signatures and ~110 MB numbered copy: 320 MB of 500 MB, with room to spare.

A per-file cap is not a cap either, because nothing stops the next file, so the total is enforced too — five places in all:

- **Streamlit's own `maxUploadSize`** is 512 MB in `.streamlit/config.toml` — one number for the whole app, a ceiling on one *file* rather than a quota, set for the largest file the app must accept: a data zip carrying a whole session. It sits 12 MB above the session limit on purpose, because the bulk of a full session is PDF and therefore already compressed, so a zip of one comes back *larger* than the session: 500 MB of incompressible content measures 500.2 MB zipped in a few large files and 505.7 MB spread over four thousand small ones. At a ceiling of exactly 500 MB the browser could refuse the zip the app had just written.
- **The 100 MB book cap** is therefore enforced in `app.py`, on the bytes as they arrive, since no config option can scope Streamlit's ceiling to one uploader. For the same reason the dropzone's printed "500MB per file" is wrong under *both* uploaders, so each sits in a keyed container whose style block replaces that line with the rule it actually enforces: `100MB per file • PDF`, and `Must fit the 500 MB session • ZIP`.
- **Before an upload**, where the sizes are known: free space is counted down file by file as they are written, so three files that would each have fitted the space free before any of them was written do not all get written. What is refused is named, and "over the 100 MB a book may be" and "would fit if you deleted something" are said as two different things.
- **Before a zip is loaded**, from the declared sizes, before anything is deleted — a zip that would not fit is refused with the session exactly as it was. The copy loop counts the bytes that actually arrive too, so the limit rests on what landed rather than on what the listing claimed.
- **During a conversion**, through `workspace.watcher` on the progress hook the imposition already reports through. Nothing can know how big a set of signatures will be until it has written them, so a job that starts inside the limit and would end outside it is stopped part way. A conversion builds into a staging folder and drops it on failure; numbering and typesetting write directly, so the partial file is removed by hand. A book typeset before only its imposition was stopped is a real result and is kept.

Every control that would write — convert, number, build, save a draft, autosave, upload — goes dead when there is no room, with the reason on screen. **📤 Save my data** and **🗑 Delete my data now** never do: the way out and the way to make room have to stay open when everything else is shut.

Changing the session figure means changing `LIMIT_BYTES` in `Script/workspace.py` **and** `server.maxUploadSize` in `.streamlit/config.toml`, which must stay comfortably *above* it. The per-book figure is `MAX_UPLOAD_BYTES` alone.

</details>

---

## 📐 How the imposition works

Four terms, because three different things all get called a "page":

| Term | Meaning |
| --- | --- |
| **source page** | One page of the input PDF. Holds two book pages side by side. |
| **book page** | One page as the reader sees it. One column of a source page. |
| **sheet** | One physical piece of paper. Printed on both sides and folded once, it carries **4 book pages**. |
| **side** | One face of a sheet, i.e. one page of the output PDF. |

A signature of *N* sheets therefore has *2N* sides and *4N* book pages. Print a signature double-sided, fold every sheet in half, and nest them one inside the other. **The first sheet printed is the outermost.**

The fold runs down the middle of the source page, and that is where every page is split — measured from the page itself, never from the column settings. A conversion is refused only when it cannot physically be printed as asked: an unreadable or empty PDF, or a book kept at its original size that runs off the sheet you chose.

Books rarely divide evenly into signatures. The last signature shrinks to the fewest whole sheets that hold what is left, and any unused pages fall at the **back of the book**, not in the middle of the final gathering.

**Duplex setting.** The sheets are landscape, so the default assumes **long-edge** duplex and rotates the back of each sheet 180° to compensate. If a test signature comes out with every other page upside down, switch to short-edge (`--short-edge`, or the sidebar option).

---

## 🧪 Testing

```bash
python -m unittest Script.test_imposition Script.test_manuscript Script.test_editor \
                   Script.test_ai_book Script.test_ai_editor -v
```

**551 tests across five modules**, and they go out of their way not to mark their own homework:

**The imposition**

- The sheet layout is derived by **simulating the physical fold**, independently of the production formula. Rotation, crop boxes and offset media boxes are checked against coordinate formulas **written out by hand** rather than reused from the code under test.
- The page-size tests build a real PDF, run the real conversion, and **read back out of the finished file where the ink actually landed** — including a small PDF interpreter that tracks the clipping path, because "did this column reach the paper" is a question text extraction cannot answer.
- That column settings cannot affect a conversion is not asserted but **demonstrated**: the same book is imposed under a fitted layout, a nonsense one and none at all, and the finished files compared byte for byte.
- The command line is driven for real, `main.main([...])` against redirected folders, because it is the half of the app with no interface to notice a break.

**The editor**

- Driven through Streamlit's own `AppTest`: real clicks on the real page, then the draft file read off the disk. The words a click carries are typed and clicked in a *single* run, because that is what a browser sends and it is the case a two-run test cannot see.
- Loading a draft is checked at the **message the server sends**, not the value it holds. A keyed box keeps its identity across a rerun, so a fresh `value=` changes the model and nothing on screen; every box must come back carrying `set_value`, and must stop carrying it on the run after, or typing would fight the cursor.
- **Where every setting lives is asserted**, on all four screens: the sidebar is preferences and the session zip and nothing else, neither working screen shows the other's paper controls, and the writing screen never has both size menus up at once. A shared setting has to survive a trip out through the front page *and* the AI screen — Streamlit discards the state of any widget a run did not draw, and a setting is undrawn on three screens out of four — and has to be readable on a screen that never draws it. The paper chosen must reach the finished signature, read back out of the built PDF's page size.
- **A job owns its screen.** ← Home goes dead while one runs, and the job pins the route it was claimed on, since a job whose slot is not drawn would be released with the work never started. Walking away disarms anything armed, because the buttons that answer a delete are drawn only under the card that armed it.
- **Nothing stale is left on the page.** Finishing a job must not duplicate a button, and the half-written book must be gone the moment the finished one arrives — both are the same hazard, Streamlit matching elements by position when a container's contents change.
- **The uploaded-draft parser is tested against what it is handed**, not against what it hopes for: a JSON list, a bare number, a string and `null` are each refused. `Manuscript.from_dict` opens with `data = data if isinstance(data, dict) else {}`, so any of them would otherwise return a valid *empty* book, and adopting one would wipe the writer's work with no error anywhere.
- **The three cards are tested as the front door**: each says what the reader wants and what comes out of it, each opens its screen, the AI one is drawn switched off with its reason when no key is set, and the front page offers a way back into work already in progress.
- The table of contents is checked against reality: a marker word is planted at the start of every chapter, the finished PDF searched for where it landed, and the printed number has to agree. Page numbers, running heads and right-hand-page starts are read out the same way — and both halves of the app are asked, in the same terms, where their folios ended up.

**Attack, not exercise**

- The drafts folder: names containing `..`, path separators, reserved Windows device names and nothing at all must all land as a file *inside* the drafts folder, and deleting refuses anything that is not a draft there. The zip likewise — an entry named `../../escaped.pdf` must not write outside its folder, a zip from elsewhere is refused before anything is deleted, and a round trip comes back byte for byte.
- The erasure rules are tested **as rules, not timings**: a session on screen survives a sweep, one whose browser has gone does not, and a runtime that cannot be read must never delete a live session — not knowing falls back to age, or a bug there would take somebody's book mid-sentence.
- The size limit runs against a real conversion: a job starts and is refused part way, and afterwards the staging folder must be gone, the half-made book not offered for printing, and the input PDF not archived as if it had converted.

**The AI writer**

- Tested **against a model that lies**, and never against the network: `ai_book._make_chat` is the one place a client is built, so replacing it replaces the outside world. The replies are the ones small models really send — JSON in a code fence, prefaced with "Certainly!", a `}` inside a sentence, a real newline mid-paragraph — each with a test named after it.
- **Budgets asserted as budgets.** A five-chapter book costs **exactly three requests**, split 3 then 2; a repair is *not* attempted when the allowance is gone. The chapter count is pinned from both ends — twelve planned is trimmed to five, two is padded to five, and the button's label must equal the schema's `minItems`.
- **The token ceiling is measured, not argued.** A `GreedyChat` fills every reply to exactly the `max_tokens` it was sent — the worst legal case, which no real model reaches — and the cost is rebuilt from what was actually sent and said. Three ways: a real tokenizer (nine runs counted with `tiktoken` the way the account is billed, including descriptions of pure punctuation, Chinese, emoji and Cyrillic); the assumption underneath (the estimators held against that tokenizer over sixteen scripts plus every prompt the app sends, since the limit rests on the input estimate never reading low); and fuzzing (200 seeded books crossing eight alphabets with eight ways a reply can go wrong, at five limits and four chapter counts). The limit is read from `ai_config` rather than written out, so moving it cannot leave forty assertions checking a stale number.
- **The floor too**: a limit of 50 tokens makes *no requests at all* and still returns five chapters and no error, and walking the limit down gives a shorter book rather than a broken one.
- **A busy provider is tested without waiting for one.** `time.sleep` is replaced with a list, so the waits are recorded rather than taken and the assertions are about their length: a `Retry-After: 17` is waited for seventeen seconds, an hour-long one is capped, and a wait that would outlast `AI_TOTAL_BUDGET_SECONDS` is never started. A 429 then a good reply has to give a whole book on **three** requests, not four — the refused one does not come out of the budget — and a 429 on every attempt has to give five chapters from the plan on a batch and the readable banner on the outline.
- **A plan cut at every character it has.** The outline is truncated at all 400-odd positions in turn, and two things have to hold at every one: nothing raises, and what comes back is a plan rather than half of one — no chapter entry without a heading, since the book is numbered from what it is given. Once the title has finished arriving it must survive every later cut. `close_json` is pinned on its own against the shapes a cut lands in: after a value, inside a string, after a key, inside a `\u` escape, and on a brace that was inside a sentence all along.
- **Truncation, three ways.** By actually truncating — a two-chapter reply cut mid-string, both chapters surviving without another request, the lost one filled from its outline summary. By the exception the OpenAI client raises *instead* of the reply, faked in the test file so nothing imports the client, and fed both ways a real one lands: before a word arrives, and after a whole reply has streamed onto the page. And by a `DutifulChat` that reads the length off the prompt, writes it fifteen percent over as models do, and is chopped wherever `max_tokens` falls — the assertion being that no chapter exceeds half again the shortest. What `paragraph_plan` asks for is pinned separately: it must cost less than the allowance it was given, at every allowance from a full one down to sixty tokens.

**The key, and the words about it**

- Treated as the thing most worth losing. It must not survive an error message, must not appear in any session-state value, and must not be reachable as an exception's `__context__` — `raise ... from None` only stops Python *printing* the original, so the scrubbed error is raised outside the `except` block. A final test sweeps every committable file for anything key-shaped, which is why the fake keys are assembled at runtime.
- Refusing to spend money is asserted before it could be spent: a paid model raises **and the client is never even constructed**.
- The data policy is tested like code — it must name OpenRouter, say the sending happens only on that button, admit providers may train on what is sent, warn that the writing is invented, and name the host the app actually runs on, so a stale hosting reference cannot survive a deploy.
- The typing script is checked where Python can see it: that it is on the page, that its words are the *same string* the label is built from, and that a copy with no key sends it locked.

---

## 🗂 Project layout

```text
Bookbinding_Signature_Creator_App/
├── app.py                       Routing, the conversion screen, job locking, theming
├── main.py                      Headless CLI and the shared folder contract
├── Dockerfile                   micromamba base, env built from the lock file
├── conda-lock.yml               Pinned linux-64 environment (the deploy source of truth)
├── environment.yml              Human-facing env spec
├── requirements.txt             Fully resolved pip pins
├── .env.example                 AI settings, with no key in it — copy to .env locally
├── .dockerignore                Keeps .env and .git out of the image
├── .streamlit/config.toml       Theme, toolbar mode, upload ceiling, telemetry off, no tracebacks
└── Script/
    ├── print_formatting.py      Imposition: source pages → signatures, folios
    ├── typesetting.py           Manuscript → 2-column PDF, at final size
    ├── manuscript.py            Book model, section kinds, drafts (JSON)
    ├── home.py                  The front page: three cards, and a way back to work
    ├── book_editor.py           The writing screen
    ├── book_build.py            A typed book → the bytes of a download, inside the click
    ├── ai_view.py               The screen that asks a model for a book
    ├── ai_book.py               The only file that knows what a language model is
    ├── ai_config.py             Where the AI settings and the key come from
    ├── settings.py              Settings that outlive the screen they are set on
    ├── paper_sizes.py           Sheet and book-page catalogues, unit parsing
    ├── workspace.py             Per-session temp workspace, sweeper, quotas
    ├── Baskervville-Regular.ttf The app's own typeface
    ├── test_imposition.py       ~1,660 lines
    ├── test_manuscript.py       ~980 lines
    ├── test_editor.py           ~1,610 lines
    ├── test_ai_book.py          ~820 lines
    └── test_ai_editor.py        ~450 lines
```

Roughly **9,300 lines of application code** and **5,500 lines of tests**.

---

## 📄 License

[MIT](./LICENSE) © 2026 Dariusz Krych

<div align="center">

**[▶ Open the live app](https://bookbinding-signature-creator.onrender.com)**

</div>
