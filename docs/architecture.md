# Architecture and decisions

```
upload -> file checks (magic bytes, size, count, rate limit) -> temp file + job
 -> preprocess (render PDF, EXIF, deskew, CLAHE, quality notes; PDF text layer kept)
 -> extract (Provider: mock | anthropic; structured output, per-field confidence)
 -> normalise (digits, ي/ك, half-space, amounts, Jalali -> Gregorian)
 -> validate (plain code) -> bounded repair loop (<= 2) with the failed checks as hints
 -> independent verification: 2nd read on the un-enhanced image; critical fields must agree
 -> PDF text-layer cross-check (deterministic)
 -> confidence + issues -> stored as `needs_review`
 -> human edits / confirms -> `confirmed` -> charts
```

## Decisions (and why, for interviews)

| Decision | Reason |
|---|---|
| **Fixed pipeline with one bounded repair loop, not a free agent** | Deterministic, cheap, fully traceable; the model has no tools, so text inside an invoice cannot steer it anywhere. "Agentic" only where it pays: re-reading when code-validation fails. |
| **Vision-language model as the extractor** | Iranian invoices are photographed at angles with table layouts; OCR flattens tables and mis-reads digits. |
| **Independent check = two extractions that must agree (not OCR)** | Measured, not assumed: Tesseract (Persian) found only **38 %** of key numbers on the synthetic set (blurry 58 %, clean 40 %, skewed 23 %, low-light 21 %; `eval/ocr_probe.py`), too weak to arbitrate. The second read uses the *un-enhanced* image and a different prompt so errors are less correlated. PDF text layers are used when present. |
| **Visual-order PDF text** | Arabic-script PDFs often store runs reversed. The text-layer check accepts both orders (found by a failing test on Arabic-digit PDFs). |
| **Confidence is built from three signals** | Model self-confidence is uncalibrated: (model, second-read agreement / text-layer match, rule validation). |
| **Nothing is auto-confirmed** | Every invoice lands in `needs_review`; only a human click counts it in charts. |
| **Validation tolerance is absolute (1 unit per row)** | A percentage tolerance hid real errors on large invoices (found during testing). |
| **Economic code: format and length only** | By decision. National code and legal ID use their check digits (warnings). |
| **Charts: revenue/expense exclude VAT; VAT shown separately; proformas excluded; display in Toman** | Accounting semantics; each invoice keeps its own `currency`, converted at query time (IRR / 10). |
| **Anonymous visitor isolation = cookie id + `visitor_id` column** | One DB, shared read-only sample data (`visitor_id` NULL), per-visitor uploads, TTL purge, reset button. Trade-off: clearing the cookie loses your data. |
| **SQLAlchemy 2.0 without Alembic, in-process thread pool, no queue** | Phase-1 scope. Swap `DATABASE_URL`; replace the pool with RQ/Celery behind `process_invoice(id)`. |
| **Jinja + HTMX, ECharts** | No build step; ECharts gives full control over formatters (Persian digits, Jalali month names) and RTL via inverted axes. |

## Data model
`visitors` · `business_settings` · `categories` · `invoices` (header, totals, `currency`, Jalali + Gregorian date, `field_confidence`) ·
`invoice_items` · `validation_issues` (code, severity, Persian message) · `source_files` (TTL) · `extraction_runs` (provider, calls, latency, masked raw JSON) · `model_usage` (daily cap).

## Security
Magic-byte type check, size/count limits, per-visitor and per-IP rate limits, global daily model-call cap, per-invoice time budget,
server-side API key, UUID file names, CSP and security headers, no inline script, log masking of long digit runs,
prompt-injection scanner (text is data; hits are flagged and never obeyed), shared sample data is read-only.
