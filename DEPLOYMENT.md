# Deployment guide

Target: **Vercel** (frontend) · **AWS EC2** (API + worker) · **Supabase** (Postgres) ·
**Pinecone** (vectors) · your existing OpenAI-compatible LLM endpoint.

```
Browser ──▶ Vercel (static React)
   │
   └──HTTPS/SSE──▶ api.yourdomain.com  ──▶  EC2
                                             ├─ caddy    (TLS termination)
                                             ├─ api      (uvicorn)
                                             ├─ worker   (arq: parse→chunk→embed)
                                             └─ redis    (job queue)
                                                  │
                          Supabase (Postgres) ◀───┤
                          Pinecone (vectors)  ◀───┤
                          LLM endpoint        ◀───┘
```

## Read this first — four things that will bite you

1. **The API and the worker must share a filesystem.** Upload writes the file to
   `storage/uploads/`, then the *worker* parses it. Split them across separate hosts or
   separate Fargate tasks and ingestion breaks silently: uploads sit at `queued`
   forever. The single-box layout below keeps them on one shared volume. Splitting them
   requires moving uploads to S3 first — see [Scaling past one box](#scaling-past-one-box).
2. **Supabase direct connections are IPv6-only** unless you buy the IPv4 add-on. A
   default EC2 instance is IPv4, so `db.<ref>.supabase.co` will simply fail to connect.
   Use the **session-mode pooler** (IPv4, and unlike transaction mode it keeps prepared
   statements working, so asyncpg needs no special handling).
3. **The API must be served over HTTPS.** Vercel is HTTPS-only, and browsers block
   mixed content, so a plain-HTTP API cannot be called from the deployed frontend. That
   means the API needs a hostname — a bare EC2 IP cannot get a certificate.
4. **Redis is not in your service list but the app needs it.** arq uses it as the job
   queue. The compose file below runs it on the same box, which costs nothing. If the
   box restarts mid-ingest, queued jobs are lost — the worker's `fail_stale_documents()`
   already marks those documents failed on startup so they don't hang at "embedding"
   forever, and re-uploading retries them.

---

## Step 1 — Supabase (Postgres)

1. Create a project. Save the database password.
2. **Connect → Session pooler** and copy that string. It looks like:

   ```
   postgresql://postgres.<project-ref>:<password>@aws-1-<region>.pooler.supabase.com:5432/postgres
   ```

   Note the username is `postgres.<project-ref>`, not `postgres`.

3. Convert it for SQLAlchemy + asyncpg by swapping the scheme and requiring TLS:

   ```
   DATABASE_URL=postgresql+asyncpg://postgres.<ref>:<password>@aws-1-<region>.pooler.supabase.com:5432/postgres?ssl=require
   ```

   URL-encode the password if it contains `@ : / ? # [ ] %`.

4. Run migrations from your laptop against the same URL:

   ```bash
   cd api
   DATABASE_URL="postgresql+asyncpg://..." .venv/Scripts/python -m alembic upgrade head
   ```

   Verify in the Supabase table editor that `documents`, `chunks` and `ingest_jobs` exist.

**Which pooler mode:** session mode (`:5432`). Transaction mode (`:6543`) is for
serverless and does not support prepared statements — asyncpg would then need
`connect_args={"statement_cache_size": 0}` or it throws
`DuplicatePreparedStatementError` under load. A long-lived uvicorn process has no reason
to take that trade.

## Step 2 — Pinecone

No change from local. Reuse the existing index, or create one per environment so staging
cannot pollute production:

- dimension **768**, metric **cosine**, serverless
- keep `EMBEDDING_DIM=768` in sync — startup asserts they match rather than failing later at upsert

## Step 3 — Code changes required before deploying

The app currently assumes the Vite dev proxy and a permissive origin list. Three small
edits make it deployable.

**a. Frontend: point at the API by env var** (`frontend/src/api/client.js`)

```diff
-const BASE = '/api'
+// Dev uses the Vite proxy; production points at the API host.
+const BASE = import.meta.env.VITE_API_BASE ?? '/api'
```

**b. Backend: allow Vercel preview origins** (`api/app/config.py`, `api/app/main.py`)

Preview deployments get a new hostname per commit, so a fixed list will not cover them.

```diff
  cors_origins: str = "http://localhost:5173"
+ # Vercel previews get a fresh hostname per deploy; match them by pattern.
+ cors_origin_regex: str = ""
```

```diff
  app.add_middleware(
      CORSMiddleware,
      allow_origins=settings.cors_origin_list,
+     allow_origin_regex=settings.cors_origin_regex or None,
      allow_credentials=True,
```

Then set `CORS_ORIGIN_REGEX=https://<your-project>-.*\.vercel\.app` in production.

**c. Backend: a connection pool sized for the pooler** (`api/app/db.py`)

```diff
  engine = create_async_engine(
      settings.database_url,
      pool_pre_ping=True,
+     pool_size=5,
+     max_overflow=5,
+     pool_recycle=1800,   # Supavisor drops idle connections; recycle before it does
      echo=False,
  )
```

API and worker are separate processes, so the real connection count is roughly double
`pool_size + max_overflow`. Keep it well under the Supabase plan's limit.

## Step 4 — Backend on AWS (EC2)

### 4.1 Launch the instance

- **t3.micro (1 GB)** is enough since embedding moved to the provider's API — there is no
  model in either process. Anything larger is headroom, not a requirement. See
  [Running on a small or free-tier host](#running-on-a-small-or-free-tier-host) for the two-container
  configuration that drops Redis as well.
- Ubuntu 24.04 or 26.04 LTS, 20 GB gp3 is plenty. Docker's apt repo publishes 26.04 (`resolute`), but the distro packages below are fewer steps and fine.
- Allocate an **Elastic IP** so the address survives a stop/start.
- Security group: `443` and `80` from anywhere (80 is needed for the ACME challenge),
  `22` from your IP only.

Install Docker:

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER && newgrp docker
sudo systemctl enable --now docker
```

### 4.2 Point DNS at it

Create an `A` record: `api.yourdomain.com → <elastic-ip>`. Caddy needs this resolving
*before* it can issue a certificate.

### 4.3 The image

`api/Dockerfile` and `api/.dockerignore` are in the repo — one image serves both
services, with the worker overriding the command. Verified locally:

| | |
|---|---|
| image size | was 879 MB with the local embedding model; **re-measure** — `onnxruntime`, `numpy` and `fastembed` are gone |
| build context | ~830 KB, almost all of it the vendored tokenizer |
| runs as | `app`, uid 10001, non-root |
| `import app.main` | OK |
| `python -m alembic` | 1.20.0, so migrations run in-image |

There is no longer an `apt` layer: every dependency ships a manylinux wheel, so nothing
needs compiling and nothing needs linking. `libgomp1` left with `onnxruntime`.

Two details worth knowing before you edit it:

- The project is installed **editable**. `app.config` derives its default storage paths
  from `__file__`, so a copy in `site-packages` would resolve them to the wrong tree and
  silently write uploads somewhere the volume is not mounted.
- The healthcheck hits `/openapi.json`, **not** `/api/health`. Health probes Pinecone,
  and a 30-second healthcheck would mean a constant trickle of billable third-party
  calls just to prove the process is alive.

```bash
docker build -t rag-api ./api
```

### 4.4 Stack, TLS and secrets

Three files are in the repo, validated with `docker compose config`:

| file | what it does |
|---|---|
| `docker-compose.prod.yml` | api · worker · redis · caddy on one host |
| `Caddyfile` | TLS termination and the SSE-safe proxy |
| `.env.production.example` | the template to copy and fill in |

Points worth knowing before you change them:

- **The API and worker share the `storage` volume.** That is the dependency described in
  gotcha 1 — it is what makes ingestion work.
- **The worker disables the image's healthcheck.** That healthcheck probes HTTP, and the
  worker serves none, so without the override it sits permanently "unhealthy" while
  working perfectly.
- **Redis runs with persistence off.** A lost queue only means a document stuck
  mid-ingest, and `fail_stale_documents()` clears those on the next worker start.
- **`flush_interval -1` in the Caddyfile is not optional.** Without it Caddy buffers the
  response and the streamed answer arrives in one lump at the end.
- **The `caddydata` volume holds issued certificates.** Deleting it makes Caddy
  re-request them, which will hit Let's Encrypt rate limits if you do it repeatedly.

Fill in the secrets on the server:

```bash
cp .env.production.example .env.production
nano .env.production          # API_DOMAIN, DATABASE_URL, the three API keys, CORS
```

`.env.production` is gitignored; only the `.example` is tracked.

### 4.5 Ship it

The compose file and Caddyfile are in the repo, so the clone brings them with it — only
`.env.production` is created by hand, since it is the one file with secrets in it.

```bash
git clone https://github.com/dead-shadow-7/Research-RAG.git && cd Research-RAG
cp .env.production.example .env.production && nano .env.production
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f worker
```

First worker start downloads the embedding model (a few hundred MB) into the `storage`
volume. It is cached from then on, including across rebuilds, because the volume
outlives the container.

```bash
curl https://api.yourdomain.com/api/health
# {"database":"ok","redis":"ok","pinecone":"ok (0 vectors)","status":"ok"}
```

## Step 5 — Frontend on Vercel

1. Import the GitHub repo.
2. **Root directory:** `frontend` · **Framework:** Vite · Build `npm run build` · Output `dist`
3. Environment variable, for all environments:

   ```
   VITE_API_BASE = https://api.yourdomain.com/api
   ```

   Vite inlines this at *build* time, so changing it requires a redeploy, not just a restart.

4. Deploy, then add the resulting domain to `CORS_ORIGINS` on the server and restart the
   API:

   ```bash
   docker compose -f docker-compose.prod.yml up -d api
   ```

**Why not a Vercel rewrite instead of CORS?** A `vercel.json` rewrite to the API would
keep everything same-origin, but it puts Vercel's proxy in the middle of the SSE stream,
which risks buffering the very thing that needs to stream. Calling the API directly with
CORS keeps the token stream between the browser and the API.

## Step 6 — Verify the deployment

Work through this in order; each step depends on the one before.

1. `curl https://api.yourdomain.com/api/health` → all three `ok`
2. Open the Vercel URL. The library loads (empty, not "can't reach the server")
3. Upload a small PDF → status moves `queued → parsing → embedding → ready`
4. Pinecone console shows the vector count rise
5. Ask a question → tokens appear **progressively**, not in one lump
   *(if they arrive all at once, `flush_interval -1` is missing from the Caddyfile)*
6. A citation marker opens the source drawer with the correct passage
7. Delete the document → rows and vectors both go

## Operating it

```bash
cd Research-RAG
docker compose -f docker-compose.prod.yml logs -f api worker   # tail logs
docker compose -f docker-compose.prod.yml restart api          # restart one service
git pull && docker compose -f docker-compose.prod.yml up -d --build   # deploy an update
```

**Migrations on update.** Run them before the new code starts:

```bash
docker compose -f docker-compose.prod.yml run --rm api python -m alembic upgrade head
```

**Cap the logs before they cap you.** Docker's default `json-file` driver has no size
limit, so container logs grow until the disk is full — and that failure looks nothing
like "disk full": Postgres writes fail and Caddy cannot renew.

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
EOF
sudo systemctl restart docker
```

**Backups.** Supabase handles Postgres. Pinecone is *not* backed up by that — but it is
rebuildable, because Postgres holds every chunk's text. A re-index script that reads
`chunks` and re-upserts is the recovery path; the uploaded files in the `storage` volume
are the only thing with no second copy, so snapshot the EBS volume if they matter.

## Running on a small or free-tier host

The free-tier instances (`t2.micro` / `t3.micro`) have **1 GB of RAM**, and the app now
fits in it with room to spare. That was not always true: embedding used to run locally,
and the API and worker each held their own ~330 MB copy of the model, putting the stack
at ~1.2 GB before the OS got a byte. Moving embedding to the provider's `/v1/embeddings`
endpoint removed the model, `onnxruntime`, `numpy`, `huggingface-hub` and the 361 MB
weight download in one step, and with them every memory dial that existed to keep the
model in check. On disk that is 488 MB less — a 454 MB virtualenv down to 327 MB, and no
model cache at all. The only local model artefact left is a 711 KB tokenizer committed to
the repo, kept because chunk sizing must be exact.

### The configuration

`docker-compose.free.yml` is API and Caddy only, with `INLINE_INGESTION=true`:

- **No worker.** Ingestion runs in the API process via FastAPI `BackgroundTasks`. The
  `fail_stale_documents()` call moves from the worker's `on_startup` to the API's
  lifespan, so jobs interrupted by a restart are still cleared.
- **No Redis.** With nothing to queue, the dependency goes away entirely.
- `mem_limit: 512m` as a ceiling, so a runaway cannot take the host's OS with it.

Measured on the current build:

| | |
|---|---|
| after importing the app | 188 MB |
| after the first embedding call | 202 MB |
| after embedding 200 chunks in one call | **205 MB** (peak 208 MB) |

That last row is the one that matters. Embedding a long document used to be the thing
that killed a 1 GB host — it peaked near 380 MB at `EMBED_BATCH_SIZE=4` and over 1.2 GB
at the old default of 64. It now costs about 3 MB, because the vectors come back over
HTTP instead of being computed in-process, and peak memory no longer scales with how you
batch. (Figures are a Windows working set; a Linux container is normally lower.)

Ingestion is network-bound rather than CPU-bound for the same reason, which is why this
layout costs so much less than it used to: the process waits on HTTP instead of competing
for the single vCPU. End to end on a 60-page PDF:

| | |
|---|---|
| parse | 0.11 s (60 pages) |
| clean + chunk | 0.30 s (60 chunks) |
| embed | 3.34 s |
| **total** | **3.75 s** — against roughly six minutes on a t2.micro before |

Batching is flat from 32 to 128 texts per request (~3.3 s either way) and starts to cost
at 256, which is why `EMBED_BATCH_SIZE` defaults to 128.

### What it costs you

- **Embedding is now a network dependency.** A provider outage stops ingestion and
  queries, where before only the LLM call could fail that way. `EMBED_MAX_RETRIES`
  (default 4) covers transient 429s and 5xxs; past that the document is marked `failed`
  with the error text and re-uploading is the retry. `GET /api/health` probes the
  endpoint so you can tell this apart from a bug.
- **Chunks over 512 tokens now fail loudly.** The local model truncated them silently and
  the chunk lost its tail; the API returns a 400 and the whole document fails. Chunking
  already sizes in BGE tokens and `tests/test_chunking.py` asserts the cap, so this is a
  guard rather than a risk — but do not loosen `CHUNK_TOKENS` past 512.
- **Per-token cost, though negligible here.** A 60-page PDF is roughly 23k embedding
  tokens, one time. Queries embed a single sentence each.
- **Ingestion still competes with request handling** on one vCPU for parsing and
  chunking, so a very large upload can make chat briefly sluggish.

### If the provider ever stops serving this model

The Pinecone index is built from `bge-base-en-v1.5` vectors, and an index's dimension is
immutable. `tests/test_embeddings.py` holds a reference vector captured from the original
local model and asserts cosine > 0.99999 against the live endpoint — it scored
**0.99999423** on the day of the switch. If that test starts failing, the provider has
changed the weights under you and every vector already stored has quietly stopped
matching. Postgres holds every chunk's text, so re-indexing from `chunks` is the recovery
path, and `CONTEXT_MIN_SCORE` must be re-calibrated with `tests/eval_retrieval.py` for
whatever model replaces it. Do not carry the old number across models.

### Other hosts worth weighing

- **Oracle Cloud Always Free** — 4 ARM cores and 24 GB RAM, permanently free, always on.
  Needs an `arm64` image build (`--platform linux/arm64`); every dependency here ships
  aarch64 wheels, and with onnxruntime gone there is nothing left that might not.
- **Free PaaS tiers** (Render, Fly, Railway) cap at 512 MB, which this now fits inside
  comfortably. They sleep when idle, so the first request after a pause is slow.

Check your own account's free-tier terms before planning around them — AWS restructured
the free tier for newer accounts. The binding constraint used to be RAM; it no longer is.

## Costs

| | |
|---|---|
| EC2 t3.micro + 20 GB gp3 + Elastic IP | ~$10/mo (or $0 on Oracle Always Free) |
| Supabase free tier | $0 (pauses after 7 days idle — the paid tier does not) |
| Pinecone serverless | ~$0 at this volume |
| Vercel hobby | $0 |
| Domain | ~$12/yr |

Embeddings are billed per token by your provider alongside the LLM, but at a different
order of magnitude: a 60-page PDF is ~23k embedding tokens once, and a query embeds one
sentence. Generation is where the cost is.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Uploads stick at `queued` | Worker is down, or `REDIS_URL` is wrong. `docker compose logs worker`. |
| Uploads reach `parsing` then fail | API and worker are not sharing the `storage` volume — gotcha 1. |
| `connection refused` / timeout to Supabase | Using the direct IPv6 host from an IPv4 box. Switch to the session pooler. |
| `DuplicatePreparedStatementError` | Connected to transaction mode (`:6543`). Use `:5432`, or set `statement_cache_size=0`. |
| Answer arrives all at once | `flush_interval -1` missing from the Caddyfile. |
| Browser: blocked by CORS | The Vercel domain is not in `CORS_ORIGINS`; previews need `CORS_ORIGIN_REGEX`. |
| Browser: blocked mixed content | The frontend is calling `http://`. The API must be HTTPS. |
| `invalid sslmode value: "require"`, or auth failing with a password you know is right | `.env.production` has CRLF line endings, from being written or edited on Windows. Every value then carries a trailing carriage return, the password included. `file .env.production` will say "CRLF line terminators"; fix it with `dos2unix .env.production`. |
| Uploads fail at `embedding` with an HTTP error | The embeddings endpoint. `GET /api/health` reports it separately from Pinecone. A 400 mentioning 512 tokens means a chunk is oversized — check `CHUNK_TOKENS`. |
| Answers cite the wrong things after a working period | The provider may have changed the model behind `EMBEDDING_MODEL`. Run `pytest tests/test_embeddings.py`; the reference-vector test is there to catch exactly this. |
| Retrieval returns nothing after migrating | Empty Pinecone index — data does not move with the database. Re-index. |

## Scaling past one box

The single constraint is the shared upload volume. To run the API and worker as separate
services (ECS, or several instances behind a load balancer), change ingestion to put
uploads in **S3**: the upload endpoint writes the object and stores its key in
`documents.source_uri`, and `parse()` downloads it to a temp file. Everything else —
Postgres, Redis, Pinecone — is already network-attached and needs no change.

Until then, scale vertically — though there is much less to scale than there used to be.
Embedding throughput used to be the first thing to run out; now it belongs to the
provider, and what is left on the box is parsing, chunking and streaming. The embedding
provider's rate limit is the new ceiling, and `max_jobs` in `app/worker.py` is the dial
for it.
