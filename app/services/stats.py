"""Chart data. Every number comes from one SQL query on confirmed, non-proforma invoices (amounts in Toman)."""
from __future__ import annotations
from sqlalchemy import func
from sqlalchemy.orm import Session
from ..core.jalali import MONTHS, parse_jalali
from ..core.persian_text import to_fa_digits
from ..db.models import Invoice, Category
from .query import visible, toman

NET = toman(Invoice.payable - func.coalesce(Invoice.vat, 0))   # revenue/expense exclude VAT; VAT is reported separately
VAT = toman(func.coalesce(Invoice.vat, 0))


def _base(s: Session, visitor_id: str, date_from: str, date_to: str, category: int | None):
    q = s.query(Invoice).filter(visible(visitor_id), Invoice.status == "confirmed", Invoice.invoice_kind != "proforma",
                                Invoice.jalali_year.isnot(None), Invoice.payable.isnot(None), Invoice.currency.isnot(None))
    a, b = (parse_jalali(date_from) if date_from else None), (parse_jalali(date_to) if date_to else None)
    if a:
        q = q.filter(Invoice.date_jalali >= f"{a[0]:04d}/{a[1]:02d}/{a[2]:02d}")
    if b:
        q = q.filter(Invoice.date_jalali <= f"{b[0]:04d}/{b[1]:02d}/{b[2]:02d}")
    if category:
        q = q.filter(Invoice.category_id == category)
    return q


def _sum(q, expr, direction):
    return q.filter(Invoice.direction == direction).with_entities(func.coalesce(func.sum(expr), 0)).scalar() or 0


def dashboard(s: Session, visitor_id: str, date_from: str = "", date_to: str = "", category: int | None = None) -> dict:
    q = _base(s, visitor_id, date_from, date_to, category)
    income, expense = _sum(q, NET, "sale"), _sum(q, NET, "purchase")
    # monthly series
    rows = q.with_entities(Invoice.jalali_year, Invoice.jalali_month, Invoice.direction, func.sum(NET)) \
            .group_by(Invoice.jalali_year, Invoice.jalali_month, Invoice.direction).all()
    by = {}
    for y, m, d, v in rows:
        by.setdefault((y, m), {"sale": 0, "purchase": 0})[d] = v
    months = []
    if by:
        (y0, m0), (y1, m1) = min(by), max(by)
        y, m = y0, m0
        while (y, m) <= (y1, m1):
            months.append((y, m))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    inc = [round(by.get(k, {}).get("sale", 0)) for k in months]
    exp = [round(by.get(k, {}).get("purchase", 0)) for k in months]
    cat_rows = q.filter(Invoice.direction == "purchase").join(Category, Invoice.category_id == Category.id, isouter=True) \
        .with_entities(func.coalesce(Category.name_fa, "بدون دسته"), func.sum(NET)).group_by(Category.name_fa) \
        .order_by(func.sum(NET).desc()).all()
    top_s = q.filter(Invoice.direction == "purchase").with_entities(Invoice.seller_name, func.sum(NET)) \
        .group_by(Invoice.seller_name).order_by(func.sum(NET).desc()).limit(10).all()
    top_b = q.filter(Invoice.direction == "sale").with_entities(Invoice.buyer_name, func.sum(NET)) \
        .group_by(Invoice.buyer_name).order_by(func.sum(NET).desc()).limit(10).all()
    pending = s.query(Invoice).filter(visible(visitor_id), Invoice.status == "needs_review").count()
    proformas = s.query(Invoice).filter(visible(visitor_id), Invoice.status == "confirmed", Invoice.invoice_kind == "proforma").count()
    return {
        "cards": {"income": round(income), "expense": round(expense), "profit": round(income - expense), "count": q.count()},
        "monthly": {"labels": [f"{MONTHS[m - 1]} {to_fa_digits(str(y))}" for y, m in months], "months": [[y, m] for y, m in months], "income": inc, "expense": exp,
                    "profit": [a - b for a, b in zip(inc, exp)]},
        "categories": [{"name": n, "value": round(v)} for n, v in cat_rows],
        "top_sellers": [{"name": n or "—", "value": round(v)} for n, v in top_s],
        "top_buyers": [{"name": n or "—", "value": round(v)} for n, v in top_b],
        "vat": {"paid": round(_sum(q, VAT, "purchase")), "received": round(_sum(q, VAT, "sale"))},
        "pending": pending, "proformas_excluded": proformas,
    }
