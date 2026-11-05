# Django Admin Screenshots

Real screenshots of the running application (Django 6.1 Admin, captured at
1440x900 with browser automation while the system was serving locally with
SQLite). The captured session used:

- the **real OpenRouter LLM** (`nvidia/nemotron-3-ultra-550b-a55b:free`)
  configured via `.env` — the question records shown are `completed` with the
  model-generated, source-cited answers, and
- the deterministic `fake` embedding provider (the demo machine's network
  could not download the ~1.1 GB `fastembed` ONNX model in reasonable time;
  in Docker the real `fastembed` provider is the default and downloads the
  model once into a persistent volume).

Retrieval in these screenshots therefore mixes the real BM25 lexical channel
with the offline `fake` semantic channel. Everything except the embedding
weights is the production code path.

| File | Shows |
| --- | --- |
| `01_admin_dashboard.png` | Admin dashboard with the custom "Document QA System Administration" branding and app groups (Documents, Questions, auth). |
| `02_documents_list.png` | Document management list with the **Source** column (`DOCX` uploads and a `Text` document), status badges, chunk counts and the *By source* filter. |
| `03_document_detail.png` | Document detail page with the **editable Content textarea** (text-centric documents), Source = "Manual text", and the read-only metadata (status, chunk count, hash). |
| `04_chunks_list.png` | Read-only chunk browser with per-document filtering and content previews for both languages. |
| `05_ask_question.png` | The custom **Ask a question** Admin screen that runs the RAG pipeline directly from the Admin, with optional document-scope restriction. |
| `06_questions_history.png` | Question history: 4 completed questions (Persian, English, an insufficient-context case and a prompt-injection test), status badges, model, source counts. |
| `07_question_detail.png` | Question detail: Persian question, **completed** status, Persian answer with a `[Source 1]` citation, the retrieved-sources table (document links, chunk indexes, RRF scores, semantic/lexical ranks), document scope and model name. |

To reproduce the screenshots locally: create a superuser, start the server
(`DJANGO_DB=sqlite python manage.py runserver`), upload the sample documents
(section 23 of the README), ask the questions from
`sample_data/SAMPLE_QUESTIONS.md` and browse the Admin at
<http://localhost:8000/admin/>.
