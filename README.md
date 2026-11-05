# Document QA System

Upload your documents, ask questions in **English or Persian**, and get answers
that are grounded **strictly in what your documents say** — with references back
to the exact passages that were used.

Built with **Django, Django REST Framework, LangChain, OpenRouter, ChromaDB and
PostgreSQL**, fully dockerized. The Django Admin is the whole user interface —
no separate frontend needed.

---

## What it does

- **Add documents** — upload `.docx` files (text, headings and tables are
  extracted) or paste text directly. You can edit and delete them anytime.
- **Ask questions** — type a question in the Admin ("Ask a question" page) or
  send it to the REST API.
- **Get grounded answers** — the system finds the most relevant passages
  (hybrid search: semantic + keyword, fused with Reciprocal Rank Fusion) and
  an LLM hosted on OpenRouter writes the answer using *only* those passages,
  with `[Source 1]`, `[Source 2]`… citations.
- **Persian-friendly** — Persian questions get Persian answers; documents typed
  with different keyboards (ی/ي, ک/ك, نیم‌فاصله, Persian digits) all match.
- **Full history** — every question, answer, sources and model used is stored
  and browsable.

---

## Requirements

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/macOS)
  or Docker Engine + Compose v2 (Linux)
- A free [OpenRouter](https://openrouter.ai/) API key — only needed for asking
  questions (sign up at [openrouter.ai](https://openrouter.ai/), create a key at
  [openrouter.ai/keys](https://openrouter.ai/keys))

---

## Quick start

```bash
# 1. (recommended) create your configuration
cp .env.example .env
#    → put your OpenRouter key in OPENROUTER_API_KEY
#    → change POSTGRES_PASSWORD to something strong
```

```bash
# 2. start everything
docker compose up --build -d
```

The first start downloads the embedding model once (~1.1 GB) into a persistent
volume — **before** the web app starts, so uploads never wait for it. Watch the
progress (and confirm it finished) with:

```bash
docker compose logs -f embedding-init
```

Then create your admin account and load the built-in sample documents:

```bash
# 3. create your login (follow the prompts)
docker compose exec web python manage.py createsuperuser

# 4. load two ready-made sample documents (English + Persian)
docker compose exec web python manage.py load_sample_data
```

Open **<http://localhost:8000/admin/>**, open a document, then try the
**"Ask a question"** page — for example:

> بودجه آموزشی سالانه هر کارمند چقدر است؟

…or the English document:

> What is the code review SLA for pull requests?

> The web app listens on port **8000**. If that port is busy on your machine,
> start on another one: `WEB_PORT=18000 docker compose up -d`
> (PowerShell: `$env:WEB_PORT="18000"` then `docker compose up -d`).

---

## Everyday commands

| What | Command |
| --- | --- |
| Start | `docker compose up -d` |
| Stop (keeps all data) | `docker compose down` |
| Stop and **wipe everything** (database, vector index, downloaded model) | `docker compose down -v` |
| See the logs | `docker compose logs -f web` |
| Model initialization logs | `docker compose logs -f embedding-init` |
| Run the test suite | `docker compose exec web python manage.py test` |

> ⚠️ `docker compose down -v` deletes the named volumes too — including the
> downloaded model and your documents. Use plain `down` for everyday stops.

---

## How to use it

### Through the Admin (no coding needed)

1. **Documents** section → *Add*: upload a `.docx` file **or** paste text.
   The status goes `pending → processing → completed` and shows how many
   chunks were created. A colored badge shows the state; failed documents
   show the reason and can be reprocessed.
2. **Questions** section → **"Ask a question"**: type your question
   (optionally restrict it to specific documents) and read the answer with
   its sources table — each source links back to the document and shows the
   retrieval ranks.
3. Every question and answer stays in the history with its timestamp and model.

### Through the REST API

Interactive documentation (Swagger UI) is at **<http://localhost:8000/api/docs/>**.
All `/api/v1/` endpoints need a token:

```bash
# get a token
curl -X POST http://localhost:8000/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "YOUR_USER", "password": "YOUR_PASS"}'

# upload a DOCX
curl -X POST http://localhost:8000/api/v1/documents/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -F "file=@sample_data/persian_sample.docx"

# ask a question (Persian is fully supported)
curl -X POST http://localhost:8000/api/v1/questions/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question_text": "مرخصی استحقاقی چند روز است؟"}'

# browse the history
curl "http://localhost:8000/api/v1/questions/" -H "Authorization: Token YOUR_TOKEN"
```

> Tip for Windows terminals: pass non-ASCII text (Persian) through a UTF-8
> file instead of the command line, e.g. write the JSON body to `q.json` and
> use `curl --data-binary @q.json`.

The full endpoint reference lives in [`docs/API.md`](docs/API.md).

---

## Configuration

Everything is configured through environment variables — copy
[`.env.example`](.env.example) to `.env` and edit. The most useful ones:

| Variable | Meaning | Default |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | your key for asking questions (leave empty to use everything except answering) | — |
| `OPENROUTER_MODEL` | any model from the [free models list](https://openrouter.ai/collections/free-models) | `meta-llama/llama-3.3-70b-instruct:free` |
| `POSTGRES_PASSWORD` | database password | change it |
| `WEB_PORT` | host port for the app | `8000` |
| `EMBEDDING_MODEL` | embedding model (see below) | `Qwen/Qwen3-Embedding-0.6B-Q` |
| `WEB_WORKERS` | Gunicorn workers (each holds the model in RAM, ~1 GB) | `2` (use `1` on small machines) |

Restart the stack after changing `.env`: `docker compose up -d`.

---

## About the embedding model

The system embeds your documents locally (nothing leaves your machine) with a
multilingual model that understands Persian well. You don't need to do anything
special about it:

- it is downloaded **once** by the `embedding-init` service before the app
  starts, and stored in the `model_cache` Docker volume,
- every later start reuses it in seconds — restarting or recreating containers
  never re-downloads it,
- the web app runs in local-cache mode: uploads and questions can never trigger
  a download,
- if you ever want a clean re-download: `docker compose down` →
  `docker volume rm <project>_model_cache` → `docker compose up -d`.

If you change `EMBEDDING_MODEL`, also rebuild the search index once:

```bash
docker compose exec web python manage.py rebuild_index
```

---

## Sample data

`sample_data/` contains two ready-made documents (an English backend handbook
and a Persian HR guide) plus [`sample_data/SAMPLE_QUESTIONS.md`](sample_data/SAMPLE_QUESTIONS.md)
with questions you can ask to see the system in action — including
cross-lingual ones (English question → answer found in the Persian document).

Re-load them anytime with:

```bash
docker compose exec web python manage.py load_sample_data
```

---

## Running the tests

The suite (175 tests) is fully offline — it never calls OpenRouter and never
downloads models:

```bash
docker compose exec web python manage.py test
```

---

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| "port is already allocated" when starting | Something else uses port 8000 — start on another port with `WEB_PORT=<other>` |
| `web` restarts with database errors | Your `POSTGRES_PASSWORD` changed after the database volume was created — either restore the old value in `.env`, or wipe and start fresh with `docker compose down -v` |
| Questions fail with "The LLM is not configured" | Put your key in `OPENROUTER_API_KEY` and `docker compose up -d` again |
| A document stays `failed` after an interrupted start | Reprocess it from the Admin (or `POST /api/v1/documents/{id}/reprocess/`) |
| `web` cannot resolve the `db` host after recreating | Rare Docker Desktop hiccup — `docker compose down && docker compose up -d` |

---

## Project layout

```
config/       Django settings and URLs
core/         health check, token auth, Persian-aware text utilities
documents/    document model, REST API, admin, DOCX extraction,
              chunking, embeddings, vector store
questions/    question history, REST API, admin, hybrid retrieval,
              prompts, OpenRouter integration, RAG pipeline
sample_data/  reproducible English/Persian sample documents
docs/API.md   full API reference
screenshots/  what the Admin looks like
```
