# Marginalia — a RAG workbench

Upload documents, ask questions, and get streamed answers where every claim carries the
passage it came from.

```
React (Vite :5173) ──HTTP/SSE──▶ FastAPI (:8000) ──▶ Postgres  (documents, chunks, jobs)
                                      │            ──▶ Pinecone (vectors)
                                      │            ──▶ LLM      (streamed answers)
                                      └─enqueue──▶ Redis ──▶ arq worker (parse→chunk→embed→upsert)
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
including the three code changes the app needs before it will run outside a dev proxy.

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
model artefact is a 711 KB tokenizer committed to the repo. `GET /api/health` reports
Postgres, Redis, the embeddings endpoint and Pinecone independently, and never raises — a
failing dependency is reported, not hidden.

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
| `MAX_UPLOAD_MB` | `50` | Enforced while streaming to disk. |
| `SUPABASE_URL` | — | Required. The JWKS endpoint used to verify tokens is derived from it. |
| `MAX_DOCUMENTS_PER_USER` | `50` | Per-tenant ceiling. Pinecone's free tier is 2 GB for the whole org. |

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

`CONTEXT_MIN_SCORE=0.50` sits in that gap. Combined with the ratio test, across a
seven-case eval:

| | chunks sent | precision | unanswerable questions handled |
|---|---|---|---|
| raw top-k | 42 | 12% | 0/2 — returned six chunks each |
| with the floor | 5 | 100% | 2/2 — returned nothing |

End to end that took a real query from 1734 input tokens to 242. Returning nothing is a
feature: the chat endpoint then says the documents don't cover it, instead of answering
from the best of a bad set.

**Re-measure if you change the embedding model.** These numbers are properties of
`bge-base-en-v1.5`, not universal constants:

```bash
.venv/Scripts/python tests/eval_retrieval.py --user <owner-id>
.venv/Scripts/python tests/eval_retrieval.py --user <owner-id> --no-floor
```

`--user` is required because vectors live in a per-user namespace: an evaluation has to
say whose corpus it is measuring. The id is `documents.owner_id` for what you indexed.

Add cases to `CASES` in that file as you add documents, including ones with
`expect=None` — questions the corpus *should not* answer are the ones that catch a floor
set too low.

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
.venv/Scripts/python -m ruff check app tests
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
- **`uvicorn --reload` does not always catch these edits.** If behaviour doesn't match
  the code — stale settings, an old prompt — restart the process before debugging it.
- **No local model at all, and no `torch` / `transformers` / `onnxruntime`.** Embedding
  runs on the provider; the one local artefact is the vendored tokenizer, read through the
  3 MB `tokenizers` wheel rather than `from_huggingface_tokenizer`, which would drag the
  whole torch stack in. If any of those show up in the lockfile, something pulled the
  heavy path back in.
