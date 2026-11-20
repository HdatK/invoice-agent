"""Fixed pipeline with a bounded repair loop (not a free-roaming agent).

preprocess -> extract -> normalise -> validate (code) -> [repair <= N] -> independent verification
-> confidence -> persist as `needs_review`. Only a human confirmation makes an invoice count in charts.
"""
from __future__ import annotations
import logging, re, time
from datetime import datetime
from ..core.config import settings
from ..core.persian_text import to_en_digits
from ..db.models import Invoice, ExtractionRun, ValidationIssue, ModelUsage
from ..db.session import session_scope
from ..providers.base import ProviderError
from ..security.guards import scan_injection, mask_json
from ..services import invoices as svc
from .flat import flatten
from .normalize import normalize_extraction
from .preprocess import load_document, UnreadableFile
from .schema import coerce_extraction
from .validate import validate, ERROR, WARNING, INFO

log = logging.getLogger("pipeline")
CRITICAL = ["invoice_no", "date_jalali", "subtotal", "vat", "payable", "seller_economic_code", "seller_name"]
NUMERIC_IN_TEXT = ["invoice_no", "subtotal", "vat", "payable", "seller_economic_code"]
FA_LABEL = {"invoice_no": "شماره فاکتور", "date_jalali": "تاریخ", "subtotal": "جمع کل", "vat": "مالیات",
            "payable": "مبلغ قابل پرداخت", "seller_economic_code": "کد اقتصادی فروشنده", "seller_name": "نام فروشنده"}


class PipelineAbort(Exception):
    pass


class _Ctx:
    def __init__(self, deadline: float):
        self.deadline, self.calls, self.attempts = deadline, 0, 0

    def left(self) -> float:
        return self.deadline - time.monotonic()


def _take_call():
    """Global daily cost cap on model calls."""
    day = datetime.utcnow().strftime("%Y-%m-%d")
    with session_scope() as s:
        row = s.get(ModelUsage, day) or ModelUsage(day=day, calls=0)
        if row.calls >= settings.daily_model_call_cap:
            raise PipelineAbort("سقف روزانهٔ پردازش دمو پر شده است؛ فردا دوباره امتحان کنید.")
        row.calls += 1
        s.merge(row)


def _extract(provider, ctx: _Ctx, images, text, *, hints=None, variant=1, sha=None) -> dict:
    last = None
    for attempt in range(1, 4):
        if ctx.left() < 2:
            raise PipelineAbort("زمان پردازش از سقف مجاز گذشت.")
        _take_call()
        ctx.calls += 1
        ctx.attempts += 1
        try:
            raw = provider.extract(images, text, hints=hints, variant=variant,
                                   timeout=max(1.0, min(settings.model_call_timeout_s, ctx.left())), source_sha=sha)
            return coerce_extraction(raw)
        except ProviderError as e:
            last = e
            if not e.retryable:
                raise PipelineAbort(f"خطای مدل: {e}") from e
        except ValueError as e:               # malformed output: one more try, then give up
            last = e
        time.sleep(min(settings.retry_backoff_s * 2 ** (attempt - 1), max(0, ctx.left() - 2)))
    raise PipelineAbort(f"استخراج پس از چند تلاش ناموفق بود ({type(last).__name__}).")


def _prep(x: dict):
    x = normalize_extraction(x)
    flat, conf = flatten(x)
    return x, flat, conf


def _digit_runs(text: str) -> set[str]:
    """Digit runs of a PDF text layer. Arabic-script layers are often stored in *visual* (reversed) order,
    so every run is also kept reversed. Thousands separators are removed first."""
    t = to_en_digits(text)
    t = re.sub(r"(?<=\d)[,٬،](?=\d)", "", t)
    runs = set(re.findall(r"\d+", t))
    return runs | {r[::-1] for r in runs}


def process_invoice(invoice_id: int, provider) -> None:
    t0 = time.monotonic()
    ctx = _Ctx(t0 + settings.invoice_timeout_s)
    status, msg, raw_first = "failed", None, None
    try:
        status, raw_first = _process(invoice_id, provider, ctx)
    except PipelineAbort as e:
        msg = str(e)
    except UnreadableFile as e:
        status, msg = "rejected", str(e)
    except Exception as e:  # never let one bad file take the worker down
        log.exception("pipeline crashed for invoice %s", invoice_id)
        msg = "خطای غیرمنتظره در پردازش؛ دوباره تلاش کنید."
    with session_scope() as s:
        inv = s.get(Invoice, invoice_id)
        if inv is None:
            return
        if status in ("failed", "rejected") and msg:
            inv.status, inv.error_message = status, msg[:290]
        s.add(ExtractionRun(invoice_id=invoice_id, provider=provider.name, model=getattr(provider, "model", None),
                            attempts=ctx.attempts, model_calls=ctx.calls, latency_ms=int((time.monotonic() - t0) * 1000),
                            status=inv.status, raw_json=mask_json(raw_first) if raw_first else None))


def _process(invoice_id: int, provider, ctx: _Ctx):
    with session_scope() as s:
        inv = s.get(Invoice, invoice_id)
        sf = inv.source_file
        path, mime, sha, direction_req, visitor = sf.path, sf.mime, sf.sha256, inv.direction, inv.visitor_id
    doc = load_document(path, mime)

    x = _extract(provider, ctx, doc.processed, doc.text, sha=sha)
    raw_first = x
    if not x["document"]["is_invoice"]:
        with session_scope() as s:
            inv = s.get(Invoice, invoice_id)
            inv.status, inv.error_message = "rejected", "این فایل فاکتور تشخیص داده نشد."
        return "rejected", raw_first

    x, flat, conf = _prep(x)
    issues = validate(flat)
    hard = lambda iss: [i for i in iss if i.severity == ERROR and i.code not in ("MISSING_FIELD", "CURRENCY_MISSING")]
    repairs = 0
    while hard(issues) and repairs < settings.max_repair_attempts and ctx.left() > 10:
        repairs += 1
        try:
            x2 = _extract(provider, ctx, doc.processed, doc.text, hints=[i.message_fa for i in hard(issues)], sha=sha)
        except PipelineAbort:
            break
        x2, flat2, conf2 = _prep(x2)
        iss2 = validate(flat2)
        if len(hard(iss2)) < len(hard(issues)):
            x, flat, conf, issues = x2, flat2, conf2, iss2
        else:
            break

    extra: list[tuple[str, str, str, str]] = []   # (field, code, severity, message)

    # independent second reading on the *unenhanced* image, compared on critical fields only
    if settings.verify_mode == "second_pass" and ctx.left() > 5:
        try:
            xv, flat_v, _ = _prep(_extract(provider, ctx, doc.raw, None, variant=2, sha=sha))
            for f in CRITICAL:
                a, b = flat.get(f), flat_v.get(f)
                if a != b:
                    conf[f] = min(conf.get(f, 1), 0.3)
                    extra.append((f, "VERIFY_DISAGREE", WARNING,
                                  f"دو خوانش مستقل در «{FA_LABEL[f]}» اختلاف دارند (خوانش دوم: {b if b is not None else 'خالی'})."))
        except PipelineAbort:
            extra.append(("invoice_no", "VERIFY_SKIPPED", INFO, "خوانش دوم انجام نشد؛ بازبینی دقیق‌تر توصیه می‌شود."))
            for f in CRITICAL:
                conf[f] = min(conf.get(f, 1), 0.7)

    # deterministic cross-check against the PDF text layer
    if doc.text:
        runs = _digit_runs(doc.text)
        for f in NUMERIC_IN_TEXT:
            v = flat.get(f)
            if v is not None and re.sub(r"\D", "", str(v)) not in runs:
                conf[f] = conf.get(f, 1) * 0.6
                extra.append((f, "TEXT_LAYER_MISMATCH", WARNING, f"مقدار «{FA_LABEL[f]}» در متن داخل PDF پیدا نشد."))

    if "blurry" in doc.notes or "low_light" in doc.notes:
        extra.append(("document", "QUALITY", INFO, "کیفیت تصویر پایین است (تار یا کم‌نور)؛ فیلدها را با دقت بررسی کنید."))

    texts = [v for k, v in flat.items() if isinstance(v, str)] + [it.get("description") for it in flat["items"]]
    texts += (doc.text or "").splitlines() + x.get("injection_flags", [])
    hits = scan_injection(texts)
    if hits:
        extra.append(("notes", "INJECTION", WARNING, "متنی شبیه دستور داخل فاکتور بود؛ نادیده گرفته شد و فقط به‌عنوان داده ثبت شد."))

    for k, c in conf.items():
        v = flat.get(k) if "." not in k else None
        if v is not None and c < settings.low_confidence_threshold and k != "date_gregorian":
            extra.append((k, "LOW_CONFIDENCE", WARNING, "اطمینان استخراج این فیلد کم است؛ با تصویر مقایسه کنید."))
    seen = set()
    extra = [e for e in extra if not (e[:2] in seen or seen.add(e[:2]))]

    with session_scope() as s:
        inv = s.get(Invoice, invoice_id)
        svc.apply_flat(inv, flat)
        direction = direction_req if direction_req in ("purchase", "sale") else svc.infer_direction(s, flat, visitor)
        if direction is None:
            direction = "purchase"
            extra.append(("direction", "DIRECTION", WARNING, "نوع فاکتور (خرید/فروش) قابل تشخیص نبود؛ لطفاً بررسی کنید."))
        inv.direction = direction
        inv.category_id = svc.category_id(s, svc.suggest_category(
            direction, [it.get("description") or "" for it in flat["items"]], x["document"].get("suggested_category")))
        inv.field_confidence = {k: round(float(c), 3) for k, c in conf.items()}
        inv.quality_notes = doc.notes + [f"injection: {h}" for h in hits]
        inv.status, inv.error_message = "needs_review", None
        svc.revalidate(s, inv)
        for f, code, sev, m in extra:
            inv.issues.append(ValidationIssue(field=f, code=code, severity=sev, message_fa=m))
    return "needs_review", raw_first
