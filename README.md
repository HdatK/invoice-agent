# Invoice extraction agent (Persian invoices)

A back-office automation agent, not a chatbot: it takes a photo/PDF of a Persian invoice, extracts and validates the data,
flags what it is unsure about for human review, stores confirmed invoices in a database, and draws income/expense charts.
Three public pages, no login: **Upload & review**, **Income/expense charts**, **Database**. UI is Persian (RTL); docs are English.

> **About the numbers.** The evaluation set is *synthetic* (generated invoices). With the default `PROVIDER=mock` the "model"
> replays the ground truth, so results only prove the plumbing works, not extraction accuracy. **This README contains no accuracy
> figures on purpose**: publish accuracy only from a real run (`PROVIDER=anthropic python eval/run_eval.py`) and quote the report as generated.

## Quick start

```bash
pip install -r requirements.txt
python -m generator.make_samples --out data/eval_set --n 70 --seed 7   # synthetic invoices + ground truth
cp .env.example .env                                                  # set PROVIDER / key if you want the real model
uvicorn app.main:app --port 8000                                      # first start seeds 52 demo invoices (~10 s)
```
Open http://localhost:8000. With `PROVIDER=mock` only the sample invoices (download links on the upload page) can be "read".
For real invoices set these in `.env` (loaded automatically; real environment variables override it): `PROVIDER=anthropic`, `ANTHROPIC_API_KEY=...` and, only if you use a gateway/proxy, `ANTHROPIC_BASE_URL=...` (server-side only; restart the server after editing). Docker: `docker compose up --build`
(not tested in the build environment). Behind HTTPS set `COOKIE_SECURE=1`; behind a proxy set `TRUST_PROXY=1`.

Tests: `python -m pytest tests -q` · Evaluation: `python eval/run_eval.py [--provider anthropic] [--limit N]`.

## How it works
`upload -> file checks -> preprocess -> extract (Provider) -> normalise -> validate -> repair (<=2) -> independent 2nd read -> text-layer check -> needs_review -> human confirm -> charts`
Details and the reasons behind each choice are in [`docs/architecture.md`](docs/architecture.md).

* **Honesty rules:** unreadable/absent fields are `null` with a reason (never guessed); every field has a confidence; nothing is
  auto-confirmed; unconfirmed invoices are excluded from charts (their count is shown separately).
* **Validation (plain code):** row sums vs totals, qty x unit price - discount, VAT vs configurable rate, payable total, national code and legal-ID
  check digits, economic code format/length (12 or 14 digits, warning only), Jalali date validity, duplicates (seller + number), missing required fields.
* **Prompt injection:** invoice text is data; the model has no tools; instruction-like text is flagged and ignored.
* **Per-invoice time budget** (`INVOICE_TIMEOUT_S`), retries with backoff, daily global cap on model calls.

## Visitor privacy (no login)
Anonymous random cookie -> each visitor sees the shared read-only sample data plus only their own uploads. Files are deleted after
`FILE_TTL_HOURS` (2 h), visitor data after `VISITOR_TTL_HOURS` (24 h) of inactivity, and "بازنشانی دمو" wipes your uploads immediately.
Upload page says: *for the demo, upload only sample or synthetic invoices*. Logs mask long digit runs. Trade-off: clearing cookies loses your data.

## API (JSON)
`POST /api/invoices/upload` (multipart `files`, `direction=auto|purchase|sale`) · `GET /api/invoices` (filters: `q,date_from,date_to,direction,category,status,sort,order,page,per`)
· `GET /api/invoices/{id}` · `GET /api/stats/dashboard` · `GET /export.xlsx` · `GET /export.csv[?items=1]` · `POST /api/demo/reset` · `GET /healthz` · docs at `/api/docs`.
Currency is stored per invoice (IRR/IRT); charts show Toman (IRR / 10). Revenue and expense exclude VAT; proformas are excluded.

## Layout
`app/` (api, pipeline, providers, services, security, templates, static) · `generator/` (synthetic invoices: clean, skewed, blurry, low-light,
text PDF, scanned PDF; Arabic/Persian/English digits; injected defects and prompt-injection text) · `eval/` · `tests/` · `docs/`.

## Known limitations / next steps
* Handwritten invoices are out of scope for evaluation (future work).
* One real provider (Anthropic) plus Mock; the `ExtractionProvider` interface makes adding an OpenAI-compatible / self-hosted VLM straightforward.
* Buyer legal ID and the unique tax ID format are not validated (no agreed format yet); VAT rate and economic-code lengths are configurable, confirm with an accountant.
* Rows cannot be added/removed in the editor; fields and cell values can.
* In-process worker pool and SQLite: fine for a demo; move to a queue and PostgreSQL (`DATABASE_URL`) to scale. Rate limits are per process.
* The OCR verification mode was evaluated and rejected (see architecture doc); `eval/ocr_probe.py` needs Tesseract with Persian data (`tessdata/`), not required to run the app.
