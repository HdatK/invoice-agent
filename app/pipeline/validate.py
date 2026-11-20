"""Deterministic validation. Plain code only; no model is involved in any rule below."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
from ..core.ids import valid_national_code, valid_legal_id, economic_code_format_ok
from ..core.jalali import parse_jalali, is_valid_jalali, to_gregorian
from ..core.config import settings

ERROR, WARNING, INFO = "error", "warning", "info"


@dataclass
class Issue:
    field: str
    code: str
    severity: str
    message_fa: str

    def asdict(self):
        return self.__dict__.copy()


def _num(v):
    return v if isinstance(v, (int, float)) else None


def validate(inv: dict, *, vat_rate: float | None = None, today: date | None = None) -> list[Issue]:
    rate = settings.vat_rate if vat_rate is None else vat_rate
    out: list[Issue] = []
    add = lambda f, c, s, m: out.append(Issue(f, c, s, m))
    items = inv.get("items") or []

    for f, label in [("invoice_no", "شماره فاکتور"), ("date_jalali", "تاریخ"), ("payable", "مبلغ قابل پرداخت"),
                     ("seller_name", "نام فروشنده")]:
        if inv.get(f) is None:
            add(f, "MISSING_FIELD", ERROR, f"«{label}» خوانده نشد؛ لطفاً دستی وارد کنید.")
    if not items:
        add("items", "NO_ITEMS", WARNING, "هیچ ردیف کالا یا خدمتی استخراج نشد.")
    if inv.get("currency") is None:
        add("currency", "CURRENCY_MISSING", ERROR, "واحد پول (ریال یا تومان) مشخص نیست؛ باید انتخاب شود.")

    # --- dates
    p = parse_jalali(inv.get("date_jalali")) if inv.get("date_jalali") else None
    if inv.get("date_jalali") and (not p or not is_valid_jalali(*p)):
        add("date_jalali", "BAD_DATE", ERROR, "تاریخ شمسی معتبر نیست (ماه یا روز نادرست).")
    elif p:
        g = to_gregorian(*p)
        if g and g > (today or date.today()) + timedelta(days=settings.max_issue_future_days):
            add("date_jalali", "DATE_FUTURE", WARNING, "تاریخ فاکتور در آینده است.")

    # --- identifiers (format only for economic code, by decision)
    for side in ("seller", "buyer"):
        ec = inv.get(f"{side}_economic_code")
        if ec and not economic_code_format_ok(ec, settings.economic_code_lengths):
            add(f"{side}_economic_code", "ECON_FORMAT", WARNING, "کد اقتصادی باید ۱۲ یا ۱۴ رقم باشد.")
        nid = inv.get(f"{side}_national_id")
        if nid and not valid_legal_id(nid):
            add(f"{side}_national_id", "BAD_LEGAL_ID", WARNING, "شناسه ملی (۱۱ رقم) رقم کنترل را قبول نمی‌کند.")
    nc = inv.get("buyer_national_code")
    if nc and not valid_national_code(nc):
        add("buyer_national_code", "BAD_NATIONAL_CODE", WARNING, "کد ملی (۱۰ رقم) رقم کنترل را قبول نمی‌کند.")

    sub, disc, vat = _num(inv.get("subtotal")), _num(inv.get("total_discount")) or 0, _num(inv.get("vat"))
    levies, pay = _num(inv.get("other_levies")) or 0, _num(inv.get("payable"))
    tol = max(1, len(items)) * settings.rounding_tol_per_row  # absolute rounding slack, never a percentage

    # --- row level: quantity x unit price - discount = line total
    for it in items:
        q, up, d, lt = _num(it.get("quantity")), _num(it.get("unit_price")), _num(it.get("discount")) or 0, _num(it.get("line_total"))
        if None not in (q, up, lt) and abs(q * up - d - lt) > settings.rounding_tol_per_row:
            add(f"items.{it['row']}.line_total", "LINE_MISMATCH", WARNING,
                f"ردیف {it['row']}: تعداد × قیمت واحد − تخفیف با مبلغ ردیف نمی‌خواند.")

    # --- rows vs totals
    lts = [_num(i.get("line_total")) for i in items]
    if items and sub is not None and all(v is not None for v in lts):
        if abs(sum(lts) - (sub - disc)) > tol:
            add("subtotal", "SUM_MISMATCH", ERROR, "جمع ردیف‌ها با جمع کل فاکتور نمی‌خواند.")

    # --- VAT: configurable rate. Unofficial invoices may legitimately carry no VAT.
    if vat is not None and sub is not None:
        expected = rate * (sub - disc)
        lt_tax = [_num(i.get("line_tax")) for i in items]
        by_rows = sum(lt_tax) if items and all(v is not None for v in lt_tax) else None
        ok = abs(vat - expected) <= max(len(items), 0.001 * expected, 1) or (by_rows is not None and abs(vat - by_rows) <= 1 and abs(vat - expected) <= 0.02 * expected + len(items))
        if not ok and not (vat == 0 and inv.get("kind") in ("unofficial", "unknown")):
            add("vat", "VAT_MISMATCH", WARNING, f"مالیات با نرخ {rate:.0%} سازگار نیست.")

    if None not in (sub, vat, pay):
        if abs(sub - disc + vat + levies - pay) > tol:
            add("payable", "PAYABLE_MISMATCH", ERROR, "مبلغ قابل پرداخت = جمع − تخفیف + مالیات + عوارض نمی‌خواند.")

    if inv.get("kind") == "proforma":
        add("kind", "PROFORMA", INFO, "پیش‌فاکتور است و در نمودارها حساب نمی‌شود.")
    return out
