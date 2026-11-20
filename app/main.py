"""FastAPI app: 3 server-rendered pages (Jinja + HTMX), a JSON API, anonymous per-visitor isolation."""
from __future__ import annotations
import hashlib, io, logging, re, secrets, shutil, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .core.config import settings, BASE_DIR
from .core.persian_text import to_fa_digits, fmt_money, normalize_text, digits_only, parse_amount, parse_quantity
from .core.jalali import MONTHS, parse_jalali, format_jalali
from .db.models import Visitor, Invoice, SourceFile, Category, BusinessSettings, utcnow
from .db.session import init_db, session_scope
from .pipeline.runner import process_invoice
from .providers import get_provider
from .security.guards import sniff_type, limiter, setup_logging
from .seed import seed_if_empty
from .services import invoices as svc
from .services import stats as stats_svc
from .services.export import to_xlsx, to_csv, STATUS, DIR, KIND
from .services.query import Filters, list_invoices, apply_filters, visible

setup_logging()
log = logging.getLogger("app")
app = FastAPI(title="Invoice Agent demo", docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
templates.env.filters.update(fa=lambda v: to_fa_digits(v) if v is not None else "", money=fmt_money,
                             status=lambda v: STATUS.get(v, v), direction=lambda v: DIR.get(v, v), kind=lambda v: KIND.get(v, v))
templates.env.globals.update(MONTHS=MONTHS, settings=settings)
pool = ThreadPoolExecutor(max_workers=settings.worker_threads)
provider = get_provider()
_VID = re.compile(r"^[0-9a-f]{32}$")


# ------------------------------------------------------------------ startup / cleanup
@app.on_event("startup")
def startup():
    init_db()
    seed_if_empty()
    with session_scope() as s:                       # jobs interrupted by a restart cannot resume
        for inv in s.query(Invoice).filter(Invoice.status == "processing"):
            inv.status, inv.error_message = "failed", "پردازش با راه‌اندازی مجدد سرور متوقف شد؛ دوباره تلاش کنید."
    threading.Thread(target=_janitor, daemon=True).start()


def purge(now: datetime | None = None) -> dict:
    """Delete expired upload files and inactive visitors with everything they own (privacy TTL)."""
    now = now or utcnow()
    n_files = n_vis = 0
    with session_scope() as s:
        for sf in s.query(SourceFile).filter(SourceFile.expires_at.isnot(None), SourceFile.expires_at < now, SourceFile.path != ""):
            Path(sf.path).unlink(missing_ok=True)
            sf.path = ""
            n_files += 1
        cutoff = now - timedelta(hours=settings.visitor_ttl_hours)
        for v in s.query(Visitor).filter(Visitor.last_seen_at < cutoff).all():
            _wipe_visitor(s, v.id)
            s.delete(v)
            n_vis += 1
    return {"files": n_files, "visitors": n_vis}


def _wipe_visitor(s, vid: str):
    for inv in s.query(Invoice).filter(Invoice.visitor_id == vid):
        s.delete(inv)
    for sf in s.query(SourceFile).filter(SourceFile.visitor_id == vid):
        if sf.path:
            Path(sf.path).unlink(missing_ok=True)
        s.delete(sf)
    s.query(BusinessSettings).filter(BusinessSettings.visitor_id == vid).delete()
    shutil.rmtree(settings.upload_dir / vid, ignore_errors=True)


def _janitor():
    while True:
        time.sleep(600)
        try:
            log.info("purge %s", purge())
        except Exception:
            log.exception("purge failed")


# ------------------------------------------------------------------ visitor cookie + headers
@app.middleware("http")
async def visitor_and_headers(request: Request, call_next):
    if request.url.path.startswith("/static/"):
        return await call_next(request)
    vid = request.cookies.get("vid")
    new = False
    with session_scope() as s:
        v = s.get(Visitor, vid) if vid and _VID.match(vid) else None
        if v is None:
            v = Visitor(id=secrets.token_hex(16))
            s.add(v)
            new = True
        v.last_seen_at = utcnow()
        vid = v.id
    request.state.vid = vid
    resp = await call_next(request)
    if new:
        resp.set_cookie("vid", vid, httponly=True, samesite="lax", secure=settings.cookie_secure, max_age=7 * 86400)
    resp.headers.update({
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                                   "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"})
    return resp


def vid_of(request: Request) -> str:
    return request.state.vid


def client_ip(request: Request) -> str:
    if settings.trust_proxy and request.headers.get("x-forwarded-for"):
        return request.headers["x-forwarded-for"].split(",")[0].strip()
    return request.client.host if request.client else "?"


def render(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, ctx)


def wants_json(request: Request) -> bool:
    return request.url.path.startswith("/api/")


def get_inv(s, inv_id: int, vid: str, *, write: bool = False) -> Invoice:
    inv = s.get(Invoice, inv_id)
    if inv is None or not (inv.visitor_id is None or inv.visitor_id == vid):
        raise HTTPException(404, "فاکتور پیدا نشد")
    if write and inv.visitor_id != vid:
        raise HTTPException(403, "داده نمونه فقط‌خواندنی است")
    return inv


# ------------------------------------------------------------------ upload
def save_uploads(request: Request, files: list[UploadFile], direction: str) -> list[dict]:
    vid = vid_of(request)
    if len(files) > settings.max_files_per_upload:
        raise HTTPException(413, f"حداکثر {settings.max_files_per_upload} فایل در هر آپلود")
    if not files:
        raise HTTPException(400, "فایلی انتخاب نشده است")
    if not limiter.allow(f"v:{vid}", settings.uploads_per_visitor_per_hour, cost=len(files)) or \
       not limiter.allow(f"ip:{client_ip(request)}", settings.uploads_per_ip_per_hour, cost=len(files)):
        raise HTTPException(429, "تعداد آپلودها از سقف ساعتی گذشته است؛ بعداً دوباره امتحان کنید.")
    direction = direction if direction in ("purchase", "sale") else "auto"
    udir = settings.upload_dir / vid
    udir.mkdir(parents=True, exist_ok=True)
    results = []
    for f in files:
        data = f.file.read(settings.max_file_bytes + 1)
        name = (f.filename or "file")[:120]
        if len(data) > settings.max_file_bytes:
            results.append({"name": name, "error": "حجم فایل از سقف مجاز بیشتر است"}); continue
        mime = sniff_type(data[:16])
        if mime is None:
            results.append({"name": name, "error": "فقط JPG، PNG و PDF مجاز است"}); continue
        path = udir / f"{uuid.uuid4().hex}{ {'image/jpeg': '.jpg', 'image/png': '.png', 'application/pdf': '.pdf'}[mime] }"
        path.write_bytes(data)
        with session_scope() as s:
            sf = SourceFile(visitor_id=vid, path=str(path), original_name=name, mime=mime, size=len(data),
                            sha256=hashlib.sha256(data).hexdigest(),
                            expires_at=utcnow() + timedelta(hours=settings.file_ttl_hours))
            s.add(sf); s.flush()
            inv = Invoice(visitor_id=vid, direction=direction, status="processing", source_file_id=sf.id)
            s.add(inv); s.flush()
            iid = inv.id
        pool.submit(process_invoice, iid, provider)
        results.append({"name": name, "invoice_id": iid})
    return results


@app.post("/api/invoices/upload")
def api_upload(request: Request, files: list[UploadFile] = File(...), direction: str = Form("auto")):
    return {"results": save_uploads(request, files, direction)}


@app.post("/ui/upload", response_class=HTMLResponse)
def ui_upload(request: Request, files: list[UploadFile] = File(...), direction: str = Form("auto")):
    try:
        results = save_uploads(request, files, direction)
    except HTTPException as e:
        return render(request, "_upload_result.html", rows=[], errors=[e.detail])
    ids = [r["invoice_id"] for r in results if "invoice_id" in r]
    with session_scope() as s:
        rows = [s.get(Invoice, i) for i in ids]
        return render(request, "_upload_result.html", rows=rows, errors=[f"{r['name']}: {r['error']}" for r in results if "error" in r])


# ------------------------------------------------------------------ pages
@app.get("/")
def home():
    return RedirectResponse("/upload")


@app.get("/upload", response_class=HTMLResponse)
def page_upload(request: Request, inv: int | None = None):
    vid = vid_of(request)
    with session_scope() as s:
        mine = s.query(Invoice).filter(Invoice.visitor_id == vid).order_by(Invoice.id.desc()).limit(30).all()
        biz = s.query(BusinessSettings).filter(BusinessSettings.visitor_id == vid).first()
        samples = [f.name for f in sorted(settings.samples_dir.glob("smp*.*")) if f.suffix in (".jpg", ".pdf")][:8]
        return render(request, "upload.html", page="upload", mine=mine, biz=biz, samples=samples, open_id=inv,
                      max_mb=settings.max_file_bytes // 2**20, max_files=settings.max_files_per_upload,
                      ttl=settings.visitor_ttl_hours, provider=provider.name)


@app.get("/charts", response_class=HTMLResponse)
def page_charts(request: Request):
    with session_scope() as s:
        cats = s.query(Category).order_by(Category.id).all()
        return render(request, "charts.html", page="charts", cats=cats)


def _filters(q: str = "", date_from: str = "", date_to: str = "", direction: str = "", category: str = "",
             status: str = "", sort: str = "created", order: str = "desc") -> Filters:
    return Filters(q=q, date_from=date_from, date_to=date_to, direction=direction,
                   category=int(category) if category.isdigit() else None, status=status, sort=sort, order=order)


@app.get("/database", response_class=HTMLResponse)
@app.get("/database/table", response_class=HTMLResponse)
def page_database(request: Request, q: str = "", date_from: str = "", date_to: str = "", direction: str = "",
                  category: str = "", status: str = "", sort: str = "created", order: str = "desc", page: int = 1):
    f = _filters(q, date_from, date_to, direction, category, status, sort, order)
    vid = vid_of(request)
    with session_scope() as s:
        rows, total = list_invoices(s, vid, f, max(1, page))
        cats = s.query(Category).order_by(Category.id).all()
        tpl = "_table.html" if request.url.path.endswith("/table") else "database.html"
        return render(request, tpl, page="database", rows=rows, total=total, cats=cats, f=f, pg=max(1, page), per=25,
                      vid=vid, qs=str(request.url.query))


# ------------------------------------------------------------------ review panel
def _review_ctx(s, inv: Invoice, vid: str, **extra):
    cats = s.query(Category).order_by(Category.id).all()
    by_field: dict[str, list] = {}
    for i in sorted(inv.issues, key=lambda i: {"error": 0, "warning": 1, "info": 2}[i.severity]):
        by_field.setdefault(i.field, []).append(i)
    conf = inv.field_confidence or {}
    return dict(inv=inv, cats=cats, by_field=by_field, conf=conf, low=settings.low_confidence_threshold,
                editable=inv.visitor_id == vid,
                blocking=[i for i in inv.issues if i.code in ("MISSING_FIELD", "CURRENCY_MISSING", "BAD_DATE")],
                errors=[i for i in inv.issues if i.severity == "error"],
                file_ok=bool(inv.source_file and inv.source_file.path and Path(inv.source_file.path).exists()), **extra)


@app.get("/invoices/{inv_id}/review", response_class=HTMLResponse)
def review(request: Request, inv_id: int):
    vid = vid_of(request)
    with session_scope() as s:
        inv = get_inv(s, inv_id, vid)
        return render(request, "_review.html", **_review_ctx(s, inv, vid))


@app.get("/invoices/{inv_id}/row", response_class=HTMLResponse)
def row(request: Request, inv_id: int):
    with session_scope() as s:
        inv = get_inv(s, inv_id, vid_of(request))
        return render(request, "_row.html", r=inv)


@app.get("/invoices/{inv_id}/detail", response_class=HTMLResponse)
def detail(request: Request, inv_id: int):
    with session_scope() as s:
        inv = get_inv(s, inv_id, vid_of(request))
        return render(request, "_detail.html", inv=inv)


_TEXT = ["invoice_no", "seller_name", "seller_address", "seller_phone", "buyer_name", "buyer_address", "payment_method",
         "notes", "unique_tax_id"]
_IDS = ["seller_economic_code", "seller_national_id", "seller_registration_no", "buyer_economic_code", "buyer_national_code"]
_AMT = ["subtotal", "total_discount", "vat", "other_levies", "payable"]


def apply_form(s, inv: Invoice, form) -> set[str]:
    """Apply manual edits; returns the set of fields the human touched (their confidence becomes 1.0)."""
    edited: set[str] = set()
    flat = svc.inv_to_flat(inv)
    new = dict(flat)
    for k in _TEXT:
        if k in form:
            new[k] = normalize_text(form[k])
    for k in _IDS:
        if k in form:
            new[k] = digits_only(form[k])
    for k in _AMT:
        if k in form:
            new[k] = parse_amount(form[k]) if str(form[k]).strip() else None
    if "date_jalali" in form:
        v = str(form["date_jalali"]).strip()
        p = parse_jalali(v) if v else None
        new["date_jalali"] = format_jalali(*p) if p else (normalize_text(v) if v else None)
    for k in ("currency",):
        if k in form:
            new[k] = form[k] if form[k] in ("IRR", "IRT") else None
    if form.get("kind") in ("official_sale", "unofficial", "proforma", "unknown"):
        new["kind"] = form["kind"]
    items = []
    for it in flat["items"]:
        r, row = it["row"], dict(it)
        for c in ("description", "unit"):
            if f"item_{r}_{c}" in form:
                row[c] = normalize_text(form[f"item_{r}_{c}"])
        for c in ("unit_price", "discount", "line_total", "line_tax"):
            if f"item_{r}_{c}" in form:
                row[c] = parse_amount(form[f"item_{r}_{c}"]) if str(form[f"item_{r}_{c}"]).strip() else None
        if f"item_{r}_quantity" in form:
            row["quantity"] = parse_quantity(form[f"item_{r}_quantity"]) if str(form[f"item_{r}_quantity"]).strip() else None
        for c in row:
            if c != "row" and row[c] != it[c]:
                edited.add(f"items.{r}.{c}")
        items.append(row)
    new["items"] = items
    edited |= {k for k in new if k != "items" and new[k] != flat.get(k)}
    svc.apply_flat(inv, new)
    if form.get("direction") in ("purchase", "sale") and form["direction"] != inv.direction:
        inv.direction = form["direction"]; edited.add("direction")
    if "category_id" in form:
        cid = int(form["category_id"]) if str(form["category_id"]).isdigit() else None
        if cid != inv.category_id:
            inv.category_id = cid
    conf = dict(inv.field_confidence or {})
    for k in edited:
        conf[k] = 1.0
    inv.field_confidence = conf
    return edited


@app.post("/invoices/{inv_id}/save", response_class=HTMLResponse)
async def save(request: Request, inv_id: int):
    vid, form = vid_of(request), await request.form()
    with session_scope() as s:
        inv = get_inv(s, inv_id, vid, write=True)
        edited = apply_form(s, inv, form)
        svc.revalidate(s, inv, edited)
        s.flush()
        return render(request, "_review.html", **_review_ctx(s, inv, vid, flash="تغییرات ذخیره شد؛ هنوز تأیید نشده است."))


@app.post("/invoices/{inv_id}/confirm", response_class=HTMLResponse)
async def confirm(request: Request, inv_id: int):
    vid, form = vid_of(request), await request.form()
    with session_scope() as s:
        inv = get_inv(s, inv_id, vid, write=True)
        edited = apply_form(s, inv, form)
        svc.revalidate(s, inv, edited)
        s.flush()
        blocking = [i for i in inv.issues if i.code in ("MISSING_FIELD", "CURRENCY_MISSING", "BAD_DATE")]
        errors = [i for i in inv.issues if i.severity == "error"]
        if blocking:
            return render(request, "_review.html", **_review_ctx(s, inv, vid, flash_err="فیلدهای ضروری (شماره، تاریخ معتبر، مبلغ، فروشنده، واحد پول) کامل نیستند."))
        if errors and not form.get("override"):
            return render(request, "_review.html", **_review_ctx(s, inv, vid, flash_err="خطاهای اعتبارسنجی باقی است. اگر مطمئنید، گزینهٔ «با وجود خطاها تأیید می‌کنم» را بزنید."))
        inv.status, inv.confirmed_at = "confirmed", utcnow()
        return render(request, "_review.html", **_review_ctx(s, inv, vid, flash="ذخیره شد و در نمودارها حساب می‌شود."))


@app.post("/invoices/{inv_id}/retry", response_class=HTMLResponse)
def retry(request: Request, inv_id: int):
    vid = vid_of(request)
    with session_scope() as s:
        inv = get_inv(s, inv_id, vid, write=True)
        if not (inv.source_file and inv.source_file.path and Path(inv.source_file.path).exists()):
            raise HTTPException(410, "فایل اصلی دیگر موجود نیست؛ دوباره آپلود کنید.")
        if inv.status not in ("failed", "rejected", "needs_review"):
            raise HTTPException(409, "این فاکتور قابل پردازش مجدد نیست")
        if not limiter.allow(f"v:{vid}", settings.uploads_per_visitor_per_hour):
            raise HTTPException(429, "سقف ساعتی پر شده است")
        inv.items, inv.issues = [], []
        inv.status, inv.error_message = "processing", None
        s.flush()
        pool.submit(process_invoice, inv.id, provider)
        return render(request, "_review.html", **_review_ctx(s, inv, vid))


@app.delete("/invoices/{inv_id}")
def delete(request: Request, inv_id: int):
    with session_scope() as s:
        inv = get_inv(s, inv_id, vid_of(request), write=True)
        sf = inv.source_file
        s.delete(inv)
        if sf:
            if sf.path:
                Path(sf.path).unlink(missing_ok=True)
            s.delete(sf)
    return Response(status_code=200)


# ------------------------------------------------------------------ files
@app.get("/invoices/{inv_id}/file")
def source_file(request: Request, inv_id: int):
    with session_scope() as s:
        inv = get_inv(s, inv_id, vid_of(request))
        sf = inv.source_file
        if not sf or not sf.path or not Path(sf.path).exists():
            raise HTTPException(410, "فایل اصلی حذف شده است (حذف خودکار بعد از مدت کوتاه)")
        return FileResponse(sf.path, media_type=sf.mime, filename=sf.original_name if inv.visitor_id is None else None,
                            headers={"Content-Disposition": "inline"})


@app.get("/invoices/{inv_id}/preview")
def preview(request: Request, inv_id: int):
    with session_scope() as s:
        inv = get_inv(s, inv_id, vid_of(request))
        sf = inv.source_file
        if not sf or not sf.path or not Path(sf.path).exists():
            raise HTTPException(410, "فایل اصلی حذف شده است")
        path, mime = sf.path, sf.mime
    if mime != "application/pdf":
        return FileResponse(path, media_type=mime)
    import pymupdf
    pix = pymupdf.open(path)[0].get_pixmap(dpi=110)
    return Response(pix.tobytes("jpeg"), media_type="image/jpeg")


@app.get("/samples/{name}")
def sample_file(name: str):
    if not re.fullmatch(r"smp\d{3}\.(jpg|pdf)", name):
        raise HTTPException(404)
    p = settings.samples_dir / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, filename=name)


# ------------------------------------------------------------------ settings & demo reset
@app.post("/settings", response_class=HTMLResponse)
async def save_settings(request: Request):
    vid, form = vid_of(request), await request.form()
    with session_scope() as s:
        b = s.query(BusinessSettings).filter(BusinessSettings.visitor_id == vid).first() or BusinessSettings(visitor_id=vid)
        b.name = normalize_text(form.get("name"))
        b.economic_code = digits_only(form.get("economic_code"))
        try:
            rate = float(form.get("vat_rate") or settings.vat_rate * 100) / 100
            b.vat_rate = rate if 0 <= rate <= 0.5 else settings.vat_rate
        except ValueError:
            b.vat_rate = settings.vat_rate
        s.add(b)
    return HTMLResponse('<span class="ok">ذخیره شد.</span>')


@app.post("/api/demo/reset")
def demo_reset(request: Request):
    vid = vid_of(request)
    if not limiter.allow(f"reset:{vid}", 10):
        raise HTTPException(429, "تعداد بازنشانی‌ها زیاد است")
    with session_scope() as s:
        _wipe_visitor(s, vid)
    return RedirectResponse("/upload", status_code=303) if request.headers.get("hx-request") is None and \
        "text/html" in request.headers.get("accept", "") else {"ok": True}


# ------------------------------------------------------------------ JSON API
def _inv_json(i: Invoice, items: bool = False) -> dict:
    d = {"id": i.id, "direction": i.direction, "kind": i.invoice_kind, "status": i.status, "invoice_no": i.invoice_no,
         "date_jalali": i.date_jalali, "date_gregorian": i.date_gregorian.isoformat() if i.date_gregorian else None,
         "seller": {"name": i.seller_name, "economic_code": i.seller_economic_code, "national_id": i.seller_national_id},
         "buyer": {"name": i.buyer_name, "economic_code": i.buyer_economic_code, "national_code": i.buyer_national_code},
         "totals": {"subtotal": i.subtotal, "total_discount": i.total_discount, "vat": i.vat, "other_levies": i.other_levies,
                    "payable": i.payable}, "currency": i.currency, "category": i.category.name_fa if i.category else None,
         "payment_method": i.payment_method, "is_sample": i.visitor_id is None,
         "issues": [{"field": x.field, "code": x.code, "severity": x.severity, "message": x.message_fa} for x in i.issues],
         "field_confidence": i.field_confidence}
    if items:
        d["items"] = [{c: getattr(it, c) for c in svc.ITEM_COLS} | {"row": it.row_no} for it in i.items]
    return d


@app.get("/api/invoices")
def api_list(request: Request, q: str = "", date_from: str = "", date_to: str = "", direction: str = "", category: str = "",
             status: str = "", sort: str = "created", order: str = "desc", page: int = 1, per: int = 50):
    with session_scope() as s:
        rows, total = list_invoices(s, vid_of(request), _filters(q, date_from, date_to, direction, category, status, sort, order),
                                    max(1, page), min(max(1, per), 200))
        return {"total": total, "page": page, "items": [_inv_json(r) for r in rows]}


@app.get("/api/invoices/{inv_id}")
def api_get(request: Request, inv_id: int):
    with session_scope() as s:
        return _inv_json(get_inv(s, inv_id, vid_of(request)), items=True)


@app.get("/api/stats/dashboard")
def api_stats(request: Request, date_from: str = "", date_to: str = "", category: str = ""):
    with session_scope() as s:
        return stats_svc.dashboard(s, vid_of(request), date_from, date_to, int(category) if category.isdigit() else None)


def _export_rows(request, **kw):
    with session_scope() as s:
        q = apply_filters(s.query(Invoice).filter(visible(vid_of(request))), _filters(**kw))
        rows = q.all()
        for r in rows:
            r.items, r.category          # load before the session closes
        return rows


@app.get("/export.xlsx")
def export_xlsx(request: Request, q: str = "", date_from: str = "", date_to: str = "", direction: str = "", category: str = "",
                status: str = "", sort: str = "created", order: str = "desc"):
    data = to_xlsx(_export_rows(request, q=q, date_from=date_from, date_to=date_to, direction=direction, category=category,
                                status=status, sort=sort, order=order))
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="invoices.xlsx"'})


@app.get("/export.csv")
def export_csv(request: Request, items: int = 0, q: str = "", date_from: str = "", date_to: str = "", direction: str = "",
               category: str = "", status: str = "", sort: str = "created", order: str = "desc"):
    data = to_csv(_export_rows(request, q=q, date_from=date_from, date_to=date_to, direction=direction, category=category,
                               status=status, sort=sort, order=order), items=bool(items))
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{"items" if items else "invoices"}.csv"'})


@app.get("/healthz")
def healthz():
    return {"ok": True, "provider": provider.name}
