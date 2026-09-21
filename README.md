# Marginalia — a RAG workbench

Upload documents, ask questions, and get streamed answers where every claim carries the
passage it came from.

```
Browser (React / Vite :5173) ──sign in──▶ Supabase Auth
   │                                            │
   │  ◀──────────────── JWT ────────────────────┘
   │
   └── HTTP/SSE + Bearer ──▶ FastAPI (:8000)   verifies the JWT against Supabase's JWKS
                                   │
                                   ├──▶ Postgres    documents, chunks, jobs — scoped by owner_id
                                   ├──▶ Pinecone    vectors — one namespace per user
                                   ├──▶ Embeddings  bge-base over HTTP, no local model
                                   ├──▶ LLM         streamed answers
                                   └──enqueue──▶ Redis ──▶ arq worker (parse→chunk→embed→upsert)
```

| Piece | Choice |
|---|---|
| Auth | Supabase Auth; the API verifies JWTs locally against the project's public JWKS |
| Tenancy | One user, one library — `documents.owner_id` in Postgres, a per-user namespace in Pinecone |
| Ingestion | PDF (PyMuPDF), Word (docx2txt), Excel (openpyxl), plain text / Markdown, URL (trafilatura) |
| Embeddings | `bge-base-en-v1.5`, 768-dim, over any OpenAI-compatible `/embeddings` endpoint |
| Vector store | Pinecone serverless, cosine |
| Generation | Any OpenAI-compatible provider via `ChatOpenAI` + `OPENAI_BASE_URL`, streamed |
| Citations | Numbered `[n]` markers, parsed out of the stream and validated against the retrieved set |

Ingestion never runs in the request path: uploading returns `202` immediately and an arq
worker does the parsing, chunking and embedding while the UI polls for progress.

Every document belongs to exactly one account. Postgres scopes rows by `owner_id` and
Pinecone keeps each user's vectors in their own namespace, so a search cannot reach
another tenant's passages even if a query were built wrongly.

Deploying it: **[DEPLOYMENT.md](DEPLOYMENT.md)** — Vercel, AWS, Supabase and Pinecone,
with the measured memory and throughput figures behind the single-box layout.

## Prerequisites

Python 3.14, Node 22, Docker Desktop, a Pinecone API key, credentials for an
OpenAI-compatible LLM endpoint, and a Supabase project for authentication.

Supabase is used for auth in development too — there is no local emulator. Create a
project, enable the email provider under **Authentication → Sign In / Providers**, turn
**Confirm email off**, and take the project URL and anon key from **Settings → Data API**.
Postgres still runs locally in Docker; only identity comes from Supabase.

Sign-in is email and password, nothing else: creating an account signs you straight in.
That is why *Confirm email* has to be off — with it on, `signUp` returns no session and
nobody gets past the form.

## Setup

```bash
docker compose up -d                  # Postgres + Redis

cd api
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m alembic upgrade head

cd ../frontend
npm install
```

Copy `.env.example` to `.env` and fill in `PINECONE_API_KEY`, `SUPABASE_URL`, plus
`OPENAI_BASE_URL`, `OPENAI_API_KEY` and `OPENAI_MODEL` for your provider. Everything else
has a working default matching `docker-compose.yml`.

Copy `frontend/.env.example` to `frontend/.env.local` and fill in `VITE_SUPABASE_URL` and
`VITE_SUPABASE_ANON_KEY` from the same project. Vite inlines these at build time, so a
change needs a restart of `npm run dev` rather than just a reload.

The Pinecone index is created automatically on first use, with the dimension taken from
`EMBEDDING_DIM`.

## Run

Four processes:

```bash
docker compose up -d                                    # datastores
cd api && .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
cd api && .venv/Scripts/python -m arq app.worker.WorkerSettings
cd frontend && npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to port 8000, so there is no CORS step in
development.

There is nothing to download on first run — embedding is an API call, and the only local
model artefact is a 711 KB tokenizer committed to the repo.

`GET /api/health` reports each dependency separately and never raises; a failing one is
reported, not hidden:

```json
{"auth": "ok (configured)", "redis": "ok", "database": "ok",
 "embeddings": "ok (768 dims)", "pinecone": "ok (190 vectors)",
 "langsmith": "ok (tracing to 'rag')", "status": "ok"}
```

`auth` and `langsmith` are configuration checks rather than liveness ones. Both exist
because their failure mode is silence: without `SUPABASE_URL` every authenticated request
is a 500 with nothing but a traceback to go on, and LangSmith variables set only in `.env`
are ignored with no indication of why.

## Configuration

Everything is read from `.env` through `app/config.py`. The settings worth knowing:

| Variable | Default | Notes |
|---|---|---|
| `OPENAI_BASE_URL` | — | Your provider's endpoint, including `/v1` if it uses one. Blank talks to api.openai.com. |
| `OPENAI_MODEL` | — | Required. The model id exactly as your provider names it. |
| `LLM_REASONING` | `off` | `off` disables the thinking pass on hybrid reasoning models; `auto` keeps the provider default. See below. |
| `LLM_MAX_TOKENS` | `2000` | With reasoning on, thinking spends this budget before any answer is written. |
| `LLM_TEMPERATURE` | `0.2` | |
| `RETRIEVE_K` | `20` | Candidates fetched from Pinecone. |
| `CONTEXT_K` | `6` | **Ceiling**, not a quota — see below. |
| `CONTEXT_MIN_RATIO` | `0.75` | A chunk must score within this fraction of the best match. |
| `CONTEXT_MIN_SCORE` | `0.50` | Absolute floor. Below it, the answer isn't in the corpus. |
| `CHUNK_TOKENS` / `CHUNK_OVERLAP` | `380` / `64` | Measured in BGE tokens, not characters. |
| `EMBEDDING_MODEL` / `EMBEDDING_DIM` | `baai/bge-base-en-v1.5` / `768` | Must agree with the Pinecone index, which is immutable. |
| `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` | — | Blank means the same provider as `OPENAI_*`. |
| `EMBED_BATCH_SIZE` | `128` | Texts per embeddings request. Flat from 32 to 128 on this provider. |
| `MAX_UPLOAD_MB` | `8` | Enforced while streaming to disk; Caddy caps the body at 12 MB so the proxy is never what rejects a valid file. |
| `CHAT_PER_MINUTE` / `CHAT_PER_DAY` | `12` / `300` | Per-user, in-process. `0` disables. |
| `UPLOADS_PER_HOUR` | `30` | Per-user, covers both the file and URL routes. |
| `SUPABASE_URL` | — | Required. The JWKS endpoint used to verify tokens is derived from it. |
| `MAX_DOCUMENTS_PER_USER` | `20` | Per-tenant ceiling. Pinecone's free tier is 2 GB for the whole org. |
| `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` | `false` / — | Both, or neither. `config.py` exports them to the environment, which is where the SDK reads them from. `/api/health` reports whether tracing is live. |

### Why `CONTEXT_K` is a ceiling

Vector search always returns its top k, however weak the matches are. Filling a fixed
number of context slots therefore means shipping whatever ranked highest — measured on a
mixed corpus, a warranty question returned one correct chunk and five from an unrelated
dissertation. That pads every prompt, costs money per query, and invites the model to
answer from material that has nothing to do with the question.

Scores separate cleanly, so a floor fixes it. Measured with `bge-base-en-v1.5`:

| | best chunk's score |
|---|---|
| answerable questions | 0.57 – 0.85 |
| questions the corpus cannot answer | 0.42 – 0.43 |

`CONTEXT_MIN_SCORE=0.50` sits in that gap. The floor does substantial work — measured
across 22 cases on the mixed-type eval corpus, precision per document type:

| | pdf | docx | txt | xlsx | url | negatives |
|---|---|---|---|---|---|---|
| raw top-k | 17% | 17% | 17% | 17% | 17% | 0/4, 24 chunks leaked |
| with the floor | 100% | 75% | 50% | 27% | 100% | 3/4, 1 chunk leaked |

Returning nothing is a feature: the chat endpoint then says the documents don't cover it,
instead of answering from the best of a bad set.

**But one global threshold does not fit every document type.** The same eval reports the
best-chunk score for each group, and the ranges overlap:

| group | | min | median | max |
|---|---|---|---|---|
| pdf | answerable | 0.561 | 0.720 | 0.851 |
| docx | answerable | 0.631 | 0.660 | 0.739 |
| txt | answerable | 0.574 | 0.617 | 0.667 |
| xlsx | answerable | 0.596 | 0.631 | 0.637 |
| url | answerable | **0.466** | 0.641 | 0.723 |
| negative | unanswerable | 0.409 | 0.461 | **0.556** |

A real answer at 0.466 sits below an unanswerable question at 0.556, so no single number
separates them: 0.50 drops the first and admits the second. Both show up as failures in
the eval, and neither is fixable by moving the threshold.

They fail for different reasons. The 0.466 case ranks its correct chunk **first** — the
answer is one sentence in a chunk covering three topics, so it is a weak match in
absolute terms while still being the best one available. The 0.555 case asks which
supplier is in Japan, and the supplier table is genuinely the most similar thing in the
corpus; a bi-encoder measures what a chunk is *about*, not whether it contains the fact.
Separating those needs a reranker or the model itself, not a number. The system prompt
already instructs the model to say when the sources do not answer the question, which is
the cheaper half of that. See **[Retrieval eval](#retrieval-eval)**.

**Re-measure if you change the embedding model.** These numbers are properties of
`bge-base-en-v1.5`, not universal constants.

A local cross-encoder reranker was built and then removed: on this corpus it matched the
score floor exactly — 100% precision either way — while costing an 80 MB model download
and per-query CPU. If questions start legitimately needing several chunks and the floor
begins admitting noise, that is when reranking earns its cost; `eval_retrieval.py` is how
you'd tell.

### Turning reasoning off

Hybrid reasoning models (Qwen3, GLM, o-series) spend most of `LLM_MAX_TOKENS` on a
thinking pass before writing anything. Grounded QA over retrieved text rarely benefits
from it, so `LLM_REASONING` defaults to `off`.

Every stack exposes that switch differently and most gateways silently ignore the ones
they don't implement. Measured against an OpenRouter-style gateway serving Qwen3:

| Attempt | Reasoning tokens |
|---|---|
| baseline | 180 (and truncated mid-thought) |
| `chat_template_kwargs.enable_thinking=false` (vLLM/SGLang) | 151 — ignored |
| `enable_thinking=false` (DashScope) | 157 — ignored |
| `reasoning_effort="minimal"` | 203 — ignored |
| `/no_think` prompt suffix | 187 — ignored |
| **`reasoning: {enabled: false}`** | **0** |
| **`reasoning_effort: "none"`** | **0** |

`app/llm.py` sends the first of those via `extra_body`. On a real query this took the
answer from 353 output tokens to 36. Providers that don't recognise the field ignore it,
so it is safe to send. If your provider is not OpenRouter-style and reasoning tokens stay
above zero, re-run that comparison against your endpoint rather than assuming.

Note the tradeoff: with reasoning off the model is more literal and less able to
reconcile conflicting sources. If multi-document answers start to look thin, try
`LLM_REASONING=auto` before blaming retrieval.

### Why embedding runs on the provider

It used to run locally, as an ONNX model loaded into the API and the worker. That single
choice drove most of the operational pain: ~330 MB resident per process, a 361 MB
download on first boot, `libgomp1` in the image, and a family of tuning dials
(`EMBED_BATCH_SIZE`, `INGEST_BATCH_SIZE`, `LEAN_ONNX`, `ONNX_THREADS`) that existed only
to stop the model eating a 1 GB host. It still got OOM-killed mid-ingest.

The gateway already in use serves the same model. Moving to it was safe because the
vectors are the same ones — measured, not assumed:

| | |
|---|---|
| cosine vs the locally-embedded reference vector | **0.99999423** |
| the existing Pinecone index | unchanged — no re-index |
| `CONTEXT_MIN_SCORE` | still calibrated; it was measured on this model |

What it bought, measured on the current build:

| | before | after |
|---|---|---|
| 60-page PDF, end to end | ~6 min on a t2.micro | **3.75 s** |
| peak RSS embedding 200 chunks at once | OOM-killed a 1 GB host | **206 MB** |
| image size | 879 MB | 678 MB |
| virtualenv + model cache | 454 MB + 361 MB | 327 MB + 0 |

`onnxruntime`, `numpy` and `huggingface-hub` left with it. `tests/test_embeddings.py`
keeps the reference vector and asserts cosine > 0.99999 on every run — if the provider
ever swaps the weights behind that model name, every vector already in Pinecone silently
stops matching, and that test is the only thing that would say so.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/documents` | Multipart upload. `202` when queued, `200` with the existing document if those exact bytes are already indexed. |
| `POST` | `/api/documents/url` | JSON `{url, title?}`. Separate route because FastAPI cannot take a JSON body and a file on one path. Same dedupe behaviour, keyed on the URL. |
| `GET` | `/api/documents` | List with status and indexing progress. |
| `GET` | `/api/documents/{id}` | One document, with its latest job stage and error. |
| `DELETE` | `/api/documents/{id}` | Removes vectors, rows and the stored file. |
| `POST` | `/api/chat/stream` | `{query, history?, document_ids?}` → SSE. |
| `GET` | `/api/health` | Per-dependency status. **The only unauthenticated route.** |

Everything except `/api/health` requires `Authorization: Bearer <supabase-jwt>` and acts
only on the caller's own documents. Another user's document id returns **404, not 403** —
a 403 would confirm the id exists and turn the route into an enumeration oracle.

The chat stream emits these SSE events, in this order:

| Event | Payload | When |
|---|---|---|
| `sources` | retrieved chunks with page and score | before any token, so the UI can render sources while the model works |
| `token` | `{text}` | markers already stripped out |
| `citation` | `{number, source, title, page_from, snippet}` | built from retrieval, never from model output |
| `done` | `{usage}` | |
| `error` | `{message}` | failures arrive here, not as a 500 — the stream is the only channel back |

## Tests

```bash
cd api
.venv/Scripts/python tests/make_fixture.py     # builds the sample PDF once
.venv/Scripts/python -m pytest
.venv/Scripts/python -m ruff check app tests scripts
```

The suite is deliberately aimed at this stack's silent failure modes: embedding shape and
the BGE query prefix, chunk sizing against the 512-token window, header/boilerplate
stripping, Excel header repetition, and citation-marker parsing. `test_parsers.py` skips
its PDF cases until `make_fixture.py` has run. No test calls a live LLM — the generation
tests drive a stubbed stream.

`test_auth.py` generates its own ES256 keypair rather than talking to Supabase, so it
tests our verification policy — including that an HS256 token signed with the public key
is rejected. `test_tenancy.py` runs against the local Postgres inside a transaction that
is rolled back afterwards, because the thing worth testing lives in the WHERE clauses and
a mocked session would only test the mock.

## Limits and abuse

Sign-up is open and email confirmation is off, so an account takes seconds to create.
Everything below assumes the caller is authenticated but otherwise untrusted.

- **URL ingestion validates its destination.** Ingesting a URL makes the *server* fetch
  it, so without a check any user can aim it at addresses only the server can reach —
  the cloud metadata endpoint, other services in the VPC, localhost — and read the reply
  back as a document. `assert_fetchable` restricts the scheme to http/https and rejects
  hostnames resolving to private, loopback, link-local or reserved addresses, checking
  **every** address a name resolves to. Redirects are followed by hand, up to
  `MAX_URL_REDIRECTS`, because a public URL is free to redirect to a private one and
  handing the chain to httpx would check only the first hop. The body is read in chunks
  with a ceiling so a stream that never ends cannot fill the container.
  `tests/test_url_safety.py` covers these.
- **Rate limits are per user and in-process** (`app/ratelimit.py`). That is deliberate:
  the deployed stack is one container with no Redis, so a shared store would add a
  dependency to solve a problem that cannot arise yet. It does mean counters reset on
  restart, and two replicas would each allow the full limit — fix that before scaling
  out, not before.
- **Quotas.** 20 documents and 8 MB per file per user, which also bounds what one
  account can put on the host's disk.
- **Not covered.** Pinecone's Starter plan allows 100 namespaces, so the hundredth user
  is the last one; sign-up 101 fails at upsert with a Pinecone error. Supabase
  rate-limits sign-in and sign-up on its side, so credential stuffing is their
  protection rather than ours.

If you deploy on EC2, also set instance metadata to **IMDSv2 required**. The URL check
above is the fix; that is the belt to its braces.

## Retrieval eval

`tests/eval_retrieval.py` builds a synthetic corpus covering every supported source type,
embeds it into a reserved Pinecone namespace, measures retrieval, and tears the namespace
down:

```bash
cd api
PYTHONPATH=. .venv/Scripts/python tests/eval_retrieval.py              # build, measure, teardown
PYTHONPATH=. .venv/Scripts/python tests/eval_retrieval.py --no-floor   # unfiltered comparison
PYTHONPATH=. .venv/Scripts/python tests/eval_retrieval.py --keep       # leave it up to poke at
```

It owns its corpus rather than measuring your live index. An earlier version measured
whatever happened to be indexed, so its precision figure read 100% one week and 56% the
next — the corpus had changed, not the code, and the number could not be used to judge a
change. It is a script and not a pytest test because a run costs an embedding round trip
for the whole corpus.

The corpus documents deliberately overlap. The spreadsheet carries a `warranty_months`
column so that warranty questions — whose real answer is one prose paragraph in the PDF —
have 150 tabular rows competing for them. Two of the four negatives are structurally
identical to answerable questions for this corpus ("Which supplier is based in Japan?"),
because a floor tuned only against absurd negatives passes those and still admits noise.

Two columns matter most. **recall@k** is whether the chunk holding the answer came back
at all; **floored** is whether it survived `apply_floor`. A gap between them is a
mis-calibrated threshold rather than a retrieval failure, and the two want different
fixes. On the current corpus recall@k is perfect for every type — retrieval finds the
answer every time, and every failure is the floor discarding it.

Add cases to `CASES` in `tests/eval_cases.py`, including ones with `expect=None`:
questions the corpus *should not* answer are what catch a floor set too low.

## Operations

Postgres mirrors every chunk's text, which makes Pinecone reconstructible. `scripts/reindex.py`
re-embeds from those rows and upserts into each document's owner namespace:

```bash
cd api
PYTHONPATH=. .venv/Scripts/python scripts/reindex.py --all
PYTHONPATH=. .venv/Scripts/python scripts/reindex.py --document <uuid>
PYTHONPATH=. .venv/Scripts/python scripts/reindex.py --all --purge-default
```

It is the recovery path for a lost index, and the repair when vectors land in the wrong
namespace — which is what a stale worker does, since `arq` has no `--reload`.
`--purge-default` empties Pinecone's default namespace, where nothing belongs once
documents are owned: no query reaches it and no delete cleans it up, but it still counts
against the storage quota.

## Things worth knowing before you change something

- **`namespace` is a required argument on every `PineconeStore` method, never a
  default.** It is what separates one tenant's vectors from another's, and Pinecone's
  default namespace is the empty string — so an optional parameter someone forgets does
  not raise, it quietly reads and writes a shared space. Required turns that mistake into
  a `TypeError` at the call site instead of a leak nobody notices.
- **Dedupe is scoped to the owner.** `_find_duplicate` matches on `(owner_id,
  content_hash)`. On `content_hash` alone, uploading a file another tenant already
  indexed would hand back *their* row — title included — and attach you to a document you
  cannot see or delete.
- **Vector IDs are `{document_id}#{ordinal}` on purpose.** Pinecone serverless cannot
  delete by metadata filter, so deleting a document walks that ID prefix. Never let
  `add_documents` generate its own IDs. Note `index.list()` yields `ListItem` objects,
  not strings, and `delete()` rejects anything that isn't a `str`.
- **BGE needs a query prefix, and nothing applies it for you.** BGE v1.5 is asymmetric:
  passages go in bare, queries need an instruction prefix. No embeddings API adds it, so
  it lives in `BGEAPIEmbeddings.embed_query` and nowhere else; dropping it costs recall
  silently. `tests/test_embeddings.py` guards this.
- **`check_embedding_ctx_length=False` is load-bearing.** Left at its default,
  `OpenAIEmbeddings` tiktoken-encodes the input and posts arrays of token ids rather than
  strings — meaningless to a BGE backend, whose vocabulary is not tiktoken's.
- **Chunks are sized in tokens, not characters.** `bge-base-en-v1.5` caps at 512 tokens
  and the API rejects anything longer outright, failing the whole document.
- **Citations are the model's assertion here, not the provider's.** Anthropic's
  `search_result` blocks return verified quoted spans; no OpenAI-compatible endpoint has
  an equivalent, so sources are numbered in the prompt and `[n]` markers are parsed back
  out of the stream. `app/llm.py` drops any marker outside the supplied range, so a
  citation can never point at a document that was not retrieved — but nothing guarantees
  the cited passage actually supports the sentence. That is why the margin shows a
  retrieved snippet rather than a quotation.
- **Markers must never reach the visible text.** They can split across streamed chunks
  (`"...[1"` then `"2]..."`), so `stream_answer` holds back a partial tail — including
  the whitespace in front of it, otherwise stripping the marker leaves `"dispatch ."`.
  `tests/test_llm.py` covers the split, grouped (`[1,2]`) and out-of-range cases.
- **Re-uploading is the retry path.** Identical bytes are deduped against
  `content_hash` and return the existing document, so the same file cannot be indexed
  twice. A *failed* document is deliberately excluded from that check — re-uploading it
  is how you retry one.
- **A worker restart fails whatever was mid-flight.** Those documents have no job left
  to finish them, so `fail_stale_documents()` marks them failed on startup rather than
  leaving a progress bar that never moves. They are not auto-retried: a document that
  reliably kills the worker would retry on every restart.
- **Plain text is decoded by sniffing, not by assumption.** A `.txt` arrives as UTF-8,
  as UTF-16 (what Notepad calls "Unicode"), or as cp1252. `decode_text` checks the BOM,
  then detects BOM-less UTF-16 by its interleaved NUL bytes — that check has to run
  *before* the cp1252 attempt, because every byte is valid cp1252, so UTF-16 would
  decode "successfully" into NUL-interleaved mojibake and get embedded that way.
  `.md` and `.markdown` route through the same parser; the splitter already breaks on
  `"\n## "` headings.
- **A URL that extracts under 200 characters is rejected.** Trafilatura returns whatever
  it can find, so a nav-only page yields something like `"Home"` — a one-word document
  that pollutes the index. Login walls and JS-rendered pages fail here too, with a
  message saying so.
- **Pinecone's vector count lags.** `describe_index_stats` is eventually consistent, so
  the number in `/api/health` can still show deleted vectors for a while. A query is the
  reliable check — deleted chunks stop matching immediately.
- **The Pinecone index dimension is immutable.** Changing embedding model means a new
  index; startup asserts the two agree rather than failing later at upsert time.
- **`uvicorn --reload` does not always catch these edits, and `arq` has no reload at
  all.** A worker left running across a change keeps executing the old code with no
  warning. That is not cosmetic: after namespaces were introduced, a stale worker wrote
  187 vectors into the default namespace while queries looked in the user's, and the only
  symptom was an answer that read like a retrieval-quality problem. Restart the worker
  whenever ingestion code changes; `scripts/reindex.py` repairs the damage.
- **`.env` is not a shell file — nothing exports it.** `pydantic-settings` reads it into
  `Settings` *fields*, and `extra="ignore"` silently discards everything else. Any library
  that reads `os.environ` for itself (LangSmith does) therefore sees nothing unless
  `config.py` declares the variable and exports it. Under Docker it works anyway, because
  compose's `env_file` does reach the environment — so this shape of bug passes in
  production and fails on your laptop.
- **Keep `UPLOAD_DIR` absolute, or unset.** A relative path resolves against the working
  directory of whichever process reads it, and `documents.source_uri` stores whatever it
  produces — so relative paths end up in the database, and the worker's cwd is not always
  the API's. `config.py` calls `.resolve()` for this reason; the default is derived from
  the package location rather than the cwd.
- **Pinecone Starter allows 100 namespaces per index, so 100 users.** Sign-up 101
  succeeds and then fails at upsert, landing the document in `failed` with a Pinecone
  error that does not explain itself. It is a cliff, not a slope.
- **No local model at all, and no `torch` / `transformers` / `onnxruntime`.** Embedding
  runs on the provider; the one local artefact is the vendored tokenizer, read through the
  3 MB `tokenizers` wheel rather than `from_huggingface_tokenizer`, which would drag the
  whole torch stack in. If any of those show up in the lockfile, something pulled the
  heavy path back in.
