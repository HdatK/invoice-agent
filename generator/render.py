"""Render a truth record as an image / PDF, with quality degradations."""
from __future__ import annotations
import io
import random
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, features as pil_features

# Pillow needs libraqm for native Persian/Arabic shaping. If it is unavailable,
# use arabic-reshaper + python-bidi as a compatible fallback.
_HAS_RAQM = pil_features.check_feature("raqm")
if not _HAS_RAQM:
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display as _bidi_get_display
    except ImportError:
        arabic_reshaper = None
        _bidi_get_display = None

FONT = Path(__file__).resolve().parents[1] / "app/static/fonts/vazir.ttf"
FONT_B = Path(__file__).resolve().parents[1] / "app/static/fonts/vazir-bold.ttf"
W, H = 1240, 1754
KIND_TITLE = {"official_sale": "صورتحساب فروش کالا و خدمات", "unofficial": "فاکتور فروش", "proforma": "پیش‌فاکتور"}
FA = "۰۱۲۳۴۵۶۷۸۹"
AR = "٠١٢٣٤٥٦٧٨٩"


def num(v, digits="fa") -> str:
    if v is None:
        return ""
    s = f"{v:,}" if isinstance(v, int) else str(v)
    if digits == "en":
        return s
    s = s.replace(",", "٬")
    table = FA if digits == "fa" else AR
    return "".join(table[int(c)] if c.isdigit() else c for c in s)


def _f(size, bold=False):
    return ImageFont.truetype(str(FONT_B if bold else FONT), size)


def _t(d, xy, text, font, fill="#111", anchor="ra"):
    text = str(text)
    if _HAS_RAQM:
        d.text(xy, text, font=font, fill=fill, anchor=anchor,
               direction="rtl", language="fa")
        return

    # Fallback for Windows/Pillow builds compiled without libraqm.
    # Install with: python -m pip install arabic-reshaper python-bidi
    if arabic_reshaper is None or _bidi_get_display is None:
        raise RuntimeError(
            "Persian text rendering requires libraqm or the fallback packages. "
            "Install them with: python -m pip install arabic-reshaper python-bidi"
        )
    shaped_text = arabic_reshaper.reshape(text)
    visual_text = _bidi_get_display(shaped_text)
    d.text(xy, visual_text, font=font, fill=fill, anchor=anchor)


def render_clean(t: dict, digits="fa", style="a") -> Image.Image:
    accent = "#1f4e79" if style == "a" else "#7a2e2e"
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    cur = "ریال" if t["currency"] == "IRR" else "تومان"
    N = lambda v: num(v, digits)
    # header
    if style == "a":
        d.rectangle([0, 0, W, 130], fill=accent)
        _t(d, (W // 2, 65), KIND_TITLE[t["document"]["invoice_kind"]], _f(46, True), "white", "mm")
    else:
        _t(d, (W // 2, 70), KIND_TITLE[t["document"]["invoice_kind"]], _f(46, True), accent, "mm")
        d.line([60, 120, W - 60, 120], fill=accent, width=4)
    h = t["header"]
    _t(d, (W - 60, 160), f"شماره: {N(h['invoice_no'])}", _f(26), anchor="ra")
    _t(d, (W - 60, 200), f"تاریخ: {N(h['date_jalali'])}", _f(26), anchor="ra")
    if h.get("unique_tax_id"):
        _t(d, (400, 160), f"شناسه یکتا: {h['unique_tax_id']}", _f(24), anchor="ra")
    y = 250

    def party(title, p, y, buyer=False):
        d.rectangle([60, y, W - 60, y + 190], outline=accent, width=2)
        d.rectangle([60, y, W - 60, y + 40], fill="#e8eef5")
        _t(d, (W - 75, y + 20), title, _f(24, True), accent, "rm")
        _t(d, (W - 75, y + 55), f"نام: {p['name']}", _f(24))
        _t(d, (W // 2 - 20, y + 55), f"کد اقتصادی: {N(p.get('economic_code') or '')}", _f(24))
        _t(d, (W - 75, y + 95), f"شناسه ملی: {N(p.get('national_id') or '')}", _f(24))
        if not buyer:
            _t(d, (W // 2 - 20, y + 95), f"شماره ثبت: {N(p.get('registration_no') or '')}", _f(24))
        _t(d, (W - 75, y + 135), f"نشانی: {N(p.get('address') or '')}", _f(24))
        _t(d, (330, y + 135), f"تلفن: {N(p.get('phone') or '')}", _f(24))
        return y + 210

    y = party("مشخصات فروشنده", t["seller"], y)
    y = party("مشخصات خریدار", t["buyer"], y, buyer=True)
    # table
    cols = [("ردیف", 60), ("شرح کالا یا خدمات", 300), ("تعداد", 90), ("واحد", 90),
            (f"قیمت واحد ({cur})", 160), ("تخفیف", 120), ("مبلغ کل", 160), ("مالیات", 140)]
    x = W - 60
    rowh = 58
    d.rectangle([60, y, W - 60, y + rowh], fill=accent)
    xs = []
    for name, w in cols:
        xs.append((x, w))
        _t(d, (x - w // 2, y + rowh // 2), name, _f(20, True), "white", "mm")
        x -= w
    y += rowh
    for it in t["items"]:
        vals = [N(it["row"]), it["description"], N(it["quantity"]), it["unit"], N(it["unit_price"]),
                N(it["discount"]) if it["discount"] else N(0), N(it["line_total"]), N(it["line_tax"])]
        d.rectangle([60, y, W - 60, y + rowh], outline="#999", width=1)
        for (cx, w), v in zip(xs, vals):
            _t(d, (cx - w // 2, y + rowh // 2), str(v), _f(21), anchor="mm")
        y += rowh
    y += 30
    # totals
    tt = t["totals"]
    lines = [("جمع کل", tt["subtotal"]), ("تخفیف", tt["total_discount"]), ("مالیات بر ارزش افزوده", tt["vat"]),
             ("عوارض", tt["other_levies"]), (f"مبلغ قابل پرداخت ({cur})", tt["payable"])]
    d.rectangle([60, y, 620, y + 60 * len(lines)], outline=accent, width=2)
    for i, (lab, v) in enumerate(lines):
        yy = y + 30 + i * 60
        last = i == len(lines) - 1
        _t(d, (600, yy), lab, _f(24, last), anchor="rm")
        _t(d, (80, yy), N(v), _f(24, last), anchor="lm")
    _t(d, (W - 60, y + 20), f"روش پرداخت: {t['payment']['method']}", _f(24))
    if t["payment"].get("notes"):
        _t(d, (W - 60, y + 70), f"توضیحات: {t['payment']['notes']}", _f(22))
    _t(d, (W // 2, H - 80), "این سند نمونه و ساختگی است و برای آزمایش تولید شده است", _f(20), "#777", "mm")
    return img


def degrade(img: Image.Image, quality: str, rng: random.Random) -> Image.Image:
    a = np.array(img)[:, :, ::-1].copy()
    if quality in ("skewed", "blurry", "lowlight"):
        ang = rng.uniform(4, 11) * rng.choice([-1, 1]) if quality == "skewed" else rng.uniform(-1.5, 1.5)
        pad = 140
        a = cv2.copyMakeBorder(a, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(70, 78, 85))
        hh, ww = a.shape[:2]
        M = cv2.getRotationMatrix2D((ww / 2, hh / 2), ang, 1.0)
        a = cv2.warpAffine(a, M, (ww, hh), borderValue=(70, 78, 85))
        if quality == "skewed":
            src = np.float32([[0, 0], [ww, 0], [ww, hh], [0, hh]])
            j = lambda: rng.uniform(0, 45)
            dst = np.float32([[j(), j()], [ww - j(), j()], [ww - j(), hh - j()], [j(), hh - j()]])
            a = cv2.warpPerspective(a, cv2.getPerspectiveTransform(src, dst), (ww, hh), borderValue=(70, 78, 85))
    if quality == "blurry":
        a = cv2.GaussianBlur(a, (0, 0), rng.uniform(1.6, 2.4))
        a = cv2.resize(a, None, fx=0.6, fy=0.6, interpolation=cv2.INTER_AREA)
    if quality == "lowlight":
        a = (255 * (a / 255.0) ** 2.4 * 0.55).astype(np.uint8)
        noise = np.random.default_rng(rng.randint(0, 10**6)).normal(0, 7, a.shape)
        a = np.clip(a.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        a[:, :, 0] = np.clip(a[:, :, 0] * 0.85, 0, 255)
    if quality == "pdf_scan":
        a = cv2.GaussianBlur(a, (0, 0), 0.8)
        noise = np.random.default_rng(rng.randint(0, 10**6)).normal(0, 4, a.shape)
        a = np.clip(a.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(a[:, :, ::-1])


def save_image(img: Image.Image, path: Path, quality: int = 88):
    img.save(path, "JPEG", quality=quality)


def image_to_pdf(img: Image.Image, path: Path):
    import fitz
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(str(path))


def html_to_text_pdf(t: dict, path: Path, digits="fa"):
    """Real text-layer PDF (Persian text shaped by MuPDF)."""
    import fitz
    N = lambda v: num(v, digits)
    cur = "ریال" if t["currency"] == "IRR" else "تومان"
    rows = "".join(
        f"<tr><td>{N(i['row'])}</td><td>{i['description']}</td><td>{N(i['quantity'])}</td><td>{i['unit']}</td>"
        f"<td>{N(i['unit_price'])}</td><td>{N(i['discount'])}</td><td>{N(i['line_total'])}</td><td>{N(i['line_tax'])}</td></tr>"
        for i in t["items"])
    tt = t["totals"]
    s, b, h = t["seller"], t["buyer"], t["header"]
    html = f"""<div dir="rtl" style="font-family:Vazir;font-size:10pt">
<h2 style="text-align:center">{KIND_TITLE[t['document']['invoice_kind']]}</h2>
<p>شماره: {N(h['invoice_no'])} &nbsp;&nbsp; تاریخ: {N(h['date_jalali'])} {('&nbsp; شناسه یکتا: ' + h['unique_tax_id']) if h.get('unique_tax_id') else ''}</p>
<p><b>فروشنده:</b> نام: {s['name']} — کد اقتصادی: {N(s.get('economic_code') or '')} — شناسه ملی: {N(s.get('national_id') or '')} — شماره ثبت: {N(s.get('registration_no') or '')}<br>نشانی: {N(s.get('address') or '')} — تلفن: {N(s.get('phone') or '')}</p>
<p><b>خریدار:</b> نام: {b['name']} — کد اقتصادی: {N(b.get('economic_code') or '')} — شناسه ملی: {N(b.get('national_id') or '')}<br>نشانی: {N(b.get('address') or '')}</p>
<table border="1" cellpadding="4" width="100%"><tr><th>ردیف</th><th>شرح کالا یا خدمات</th><th>تعداد</th><th>واحد</th><th>قیمت واحد ({cur})</th><th>تخفیف</th><th>مبلغ کل</th><th>مالیات</th></tr>{rows}</table>
<p>جمع کل: {N(tt['subtotal'])}<br>تخفیف: {N(tt['total_discount'])}<br>مالیات بر ارزش افزوده: {N(tt['vat'])}<br>عوارض: {N(tt['other_levies'])}<br><b>مبلغ قابل پرداخت ({cur}): {N(tt['payable'])}</b></p>
<p>روش پرداخت: {t['payment']['method']} {('<br>توضیحات: ' + t['payment']['notes']) if t['payment'].get('notes') else ''}</p></div>"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    arch = fitz.Archive(str(FONT.parent))
    css = "@font-face{font-family:Vazir;src:url(vazir.ttf);} @font-face{font-family:Vazir;font-weight:bold;src:url(vazir-bold.ttf);}"
    page.insert_htmlbox(fitz.Rect(30, 30, 565, 810), html, css=css, archive=arch)
    doc.save(str(path))
