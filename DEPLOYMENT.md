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

- **t3.small (2 GB)** is the practical floor; **t3.medium (4 GB)** is comfortable. The API
  and the worker *each* load the ONNX embedding model — measured at 624 MB and 588 MB
  resident. On a free-tier 1 GB instance this does not fit; see
  [Running on the AWS free tier](#running-on-the-aws-free-tier) for the configuration
  that does.
- Ubuntu 24.04 or 26.04 LTS, 20 GB gp3. The 8 GB default is too small for the image plus the model cache. Docker's apt repo publishes 26.04 (`resolute`), but the distro packages below are fewer steps and fine.
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
| image size | 879 MB |
| build context | 119 KB (815 MB without `.dockerignore`) |
| runs as | `app`, uid 10001, non-root |
| `import app.main` | OK |
| `python -m alembic` | 1.20.0, so migrations run in-image |

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

## Running on the AWS free tier

The free-tier instances (`t2.micro` / `t3.micro`) have **1 GB of RAM**. Measured on the
running app:

| | resident |
|---|---|
| API (uvicorn) | 624 MB |
| Worker (arq) | 588 MB |
| Redis | 5 MB |
| **Total, before Caddy + Docker + OS** | **~1.2 GB** |

So the default layout does **not** fit in 1 GB — it is roughly 50% over before the OS
gets a byte. The reason is specific and fixable: the API and the worker each load their
own full copy of the embedding model.

Measured cost of the model alone, in a bare process:

| model | dimensions | resident |
|---|---|---|
| `BAAI/bge-base-en-v1.5` (current) | 768 | 492 MB |
| `BAAI/bge-small-en-v1.5` | 384 | 226 MB |

### The configuration that fits

Two changes, and **neither requires changing the embedding model**:

**1. Drop the separate worker.** Run ingestion inside the API process with FastAPI
`BackgroundTasks` instead of arq. This removes an entire model copy *and* Redis —
the single biggest saving available. Move the `fail_stale_documents()` call from the
worker's `on_startup` to the API's lifespan so interrupted jobs are still cleared.

**2. Tune the ONNX session.** FastEmbed loads `model_optimized.onnx` and then asks ONNX
Runtime to optimise it *again*, which makes it hold a second copy of the weights. It
also leaves the CPU memory arena on, which pre-allocates and never gives memory back.
Measured, same model file, through FastEmbed:

| | resident |
|---|---|
| as FastEmbed loads it | 494 MB |
| with the session tuned | **327 MB** |

That is **167 MB saved for free** — same weights, same vectors. Verified: embeddings
from the tuned session match the default to a cosine similarity of **0.9999998**
(largest element difference 2.3e-4, ordinary float noise from unfused kernels), so the
existing Pinecone index and the calibrated `CONTEXT_MIN_SCORE` both stay valid.

```python
# api/app/embeddings.py -- apply before constructing TextEmbedding
import onnxruntime as ort

_orig_session = ort.InferenceSession

def _lean_session(path_or_bytes, sess_options=None, providers=None, **kw):
    so = sess_options or ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    so.enable_cpu_mem_arena = False
    so.enable_mem_pattern = False
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    return _orig_session(path_or_bytes, sess_options=so, providers=providers, **kw)

ort.InferenceSession = _lean_session
```

This patches a third-party library, which deserves care: `TextEmbedding` *accepts* an
`extra_session_options` argument but **silently discards it**, so the supported route
does not work. Pin the `fastembed` version, and add a test that embeds a fixed sentence
and asserts cosine ≈ 1 against a stored vector — so a FastEmbed upgrade that changes the
loading path fails loudly instead of quietly re-indexing everything.

| configuration | total | keeps index? |
|---|---|---|
| default (API + worker, bge-base) | ~1.5 GB — **OOM** | — |
| API + worker, tuned | ~1.23 GB — still over | — |
| **single process, tuned, bge-base** | **~780 MB — fits** | **yes** |
| single process, tuned, bge-small | ~630 MB — roomiest | no, needs re-index |

**So the model swap is optional.** Take it only if ~780 MB feels too tight for comfort;
it buys another ~150 MB at the cost of a full re-index and re-calibration.

`docker-compose.free.yml` is this configuration: API and Caddy only, `INLINE_INGESTION`
on, container capped at 768 MB so a runaway cannot take the host down with it.

### Steady state is not the number that matters

Measured on a live t2.micro: the API idles at **247 MB** but peaks near **380 MB** while
embedding. An earlier version of this guide quoted the idle figure as proof it fits,
which was measured with a 4-chunk PDF and missed the ingestion peak entirely. The first
real upload OOM-killed the process twice.

Ingestion peak is governed by two settings, both of which default low for this reason:

- `EMBED_BATCH_SIZE` (4 here) — sequences per ONNX forward pass. Each carries
  512×3072 of intermediate activations, so the old fixed value of 64 allocated hundreds
  of megabytes in one block.
- `INGEST_BATCH_SIZE` (16 here) — chunks embedded and upserted per slice, so peak memory
  is bounded by the *slice*, not the document. A 500-page PDF now costs what a 5-page
  one does.

**Do not raise `EMBED_BATCH_SIZE` to go faster.** Measured on one vCPU, 48 chunks:

| batch | throughput | peak RSS |
|---|---|---|
| 1 | 1.32 chunks/s | 676 MB |
| **4** | **1.22 chunks/s** | **753 MB** |
| 16 | 0.81 chunks/s | 908 MB |
| 64 | 0.67 chunks/s | 1256 MB |

Bigger batches are slower *and* hungrier. Batching amortises cost across parallel
hardware; with one vCPU and `intra_op_num_threads=1` there is nothing to amortise
across, so larger intermediate tensors simply blow the CPU cache. The curve runs the
same direction on both axes, so there is no tradeoff to tune.

Ingestion is CPU-bound, not batch-bound: a t2.micro manages ~0.17 chunks/s against 1.2
on an unthrottled core, so a 60-page PDF takes about six minutes and a 200-page one
around twenty. More cores is the only lever that moves it — on a 2-vCPU host raise
`ONNX_THREADS`, which needs no rebuild.

Then add swap, which is not optional on a 1 GB box — it converts an OOM-kill into
slowness:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

And cap allocator overhead in `.env.production`:

```bash
OMP_NUM_THREADS=1
MALLOC_ARENA_MAX=2
```

### What it costs you

- **Only if you also take the model swap:** a Pinecone index's dimension is immutable,
  so 384 dimensions needs a *new* index and a full re-ingest (set `EMBEDDING_DIM=384`;
  startup asserts the two agree). The retrieval threshold must then be re-calibrated —
  `CONTEXT_MIN_SCORE=0.50` was measured for bge-base. Re-run `tests/eval_retrieval.py`
  and pick the value that separates answerable from unanswerable questions. Do not carry
  the old number over. Retrieval quality drops somewhat; the eval tells you by how much
  instead of leaving you guessing.
- **`intra_op_num_threads = 1` trades throughput for memory.** Embedding a large
  document is single-threaded, so ingestion gets slower. On a 1-vCPU instance there was
  little parallelism to give up.
- **`t3.micro` is burstable.** Sustained embedding will exhaust CPU credits and throttle
  to ~10% baseline, so ingesting a large PDF can take far longer than it does locally.
  Ingestion, not serving, is what will feel slow.
- **Ingestion now competes with request handling** for one vCPU, so a large upload makes
  chat sluggish while it runs.

### Other levers, and why they do not pay

- **Smaller embedding batches** (`batch_size=8`) cut the transient spike during
  ingestion, not the steady-state footprint. Worth doing on 1 GB, but it is not the fix.
- **`lazy_load=True`** defers the model until first use. It helps startup, but once a
  query arrives the memory is resident anyway.
- **Delegating query embedding to the worker** so only one process holds the model is
  *worse*: the worker keeps it loaded permanently and the API still needs its own
  runtime, so two processes beat one at nothing.
- **Pinecone's hosted embeddings** would remove the model from your box entirely
  (API drops to roughly 150 MB). It is a different model, so it means a re-index and
  re-calibration like any model swap, plus a network hop on every query and per-token
  cost — but if memory is the binding constraint, this is the option that removes the
  constraint rather than shrinking it.

### Two alternatives worth weighing

- **`t3.small` (2 GB, ~$15/mo)** runs the architecture as designed — no model swap, no
  re-index, no re-calibration, worker kept. If the app matters, this is the cheaper
  option once your time is counted.
- **Oracle Cloud Always Free** gives 4 ARM cores and 24 GB RAM at no cost, permanently,
  which runs this comfortably with zero compromises. It needs an `arm64` image build
  (`--platform linux/arm64`); onnxruntime and every other dependency here ship aarch64
  wheels.

Check your own account's free-tier terms before planning around them — AWS restructured
the free tier for newer accounts, and I could not confirm the current EC2 allowance. The
binding constraint here is RAM, not hours.

## Costs

| | |
|---|---|
| EC2 t3.small + 20 GB gp3 + Elastic IP | ~$19/mo |
| Supabase free tier | $0 (pauses after 7 days idle — the paid tier does not) |
| Pinecone serverless | ~$0 at this volume |
| Vercel hobby | $0 |
| Domain | ~$12/yr |

Embeddings run on your own CPU, so they cost nothing per document. The LLM endpoint is
billed per token by your provider.

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
`. Fix with `sed -i 's/
$//' .env.production`. |
| Container restarts during ingestion, seemingly at random | Being OOM-killed mid-embed. `docker inspect` reports `OOMKilled: false` because the kernel killed the *python process inside* the container, so Docker sees a clean exit and restarts it. `sudo dmesg -T \| grep -i 'killed process'` is where the truth is. Lower `EMBED_BATCH_SIZE`. |
| Worker OOM-killed | 2 GB is tight with two model copies. Move to t3.medium. |
| Retrieval returns nothing after migrating | Empty Pinecone index — data does not move with the database. Re-index. |

## Scaling past one box

The single constraint is the shared upload volume. To run the API and worker as separate
services (ECS, or several instances behind a load balancer), change ingestion to put
uploads in **S3**: the upload endpoint writes the object and stores its key in
`documents.source_uri`, and `parse()` downloads it to a temp file. Everything else —
Postgres, Redis, Pinecone — is already network-attached and needs no change.

Until then, scale vertically. One t3.medium comfortably serves a small team; embedding
throughput, not request concurrency, is the first thing to run out.
