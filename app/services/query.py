"""Visibility scoping and the shared invoice filter used by the database page and the exports."""
from __future__ import annotations
from dataclasses import dataclass
from sqlalchemy import or_, func, case
from sqlalchemy.orm import Session
from ..core.jalali import parse_jalali
from ..db.models import Invoice, Category

SORTS = {"date": Invoice.date_gregorian, "no": Invoice.invoice_no, "seller": Invoice.seller_name,
         "payable": Invoice.payable, "created": Invoice.created_at, "status": Invoice.status}


def visible(visitor_id: str):
    """A visitor sees shared sample data plus their own uploads, nothing else."""
    return or_(Invoice.visitor_id.is_(None), Invoice.visitor_id == visitor_id)


def toman(col, cur=Invoice.currency):
    return case((cur == "IRR", col / 10.0), else_=col * 1.0)


@dataclass
class Filters:
    q: str = ""
    date_from: str = ""
    date_to: str = ""
    direction: str = ""
    category: int | None = None
    status: str = ""
    sort: str = "created"
    order: str = "desc"


def _ym(s: str):
    p = parse_jalali(s) if s else None
    return (p[0], p[1], p[2]) if p else None


def apply_filters(query, f: Filters):
    if f.q:
        like = f"%{f.q.strip()}%"
        query = query.filter(or_(Invoice.invoice_no.like(like), Invoice.seller_name.like(like), Invoice.buyer_name.like(like)))
    a, b = _ym(f.date_from), _ym(f.date_to)
    if a:
        query = query.filter(Invoice.date_jalali >= f"{a[0]:04d}/{a[1]:02d}/{a[2]:02d}")
    if b:
        query = query.filter(Invoice.date_jalali <= f"{b[0]:04d}/{b[1]:02d}/{b[2]:02d}")
    if f.direction in ("purchase", "sale"):
        query = query.filter(Invoice.direction == f.direction)
    if f.category:
        query = query.filter(Invoice.category_id == f.category)
    if f.status in ("confirmed", "needs_review", "failed", "rejected", "processing"):
        query = query.filter(Invoice.status == f.status)
    col = SORTS.get(f.sort, Invoice.created_at)
    return query.order_by(col.asc() if f.order == "asc" else col.desc(), Invoice.id.desc())


def list_invoices(s: Session, visitor_id: str, f: Filters, page: int = 1, per: int = 25):
    q = apply_filters(s.query(Invoice).filter(visible(visitor_id)), f)
    total = q.count()
    rows = q.offset((page - 1) * per).limit(per).all()
    return rows, total
