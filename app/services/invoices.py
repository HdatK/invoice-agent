"""DB <-> flat dict mapping, revalidation (shared by the pipeline and manual edits), category/direction helpers."""
from __future__ import annotations
from sqlalchemy import or_
from sqlalchemy.orm import Session
from ..core.config import settings
from ..core.jalali import parse_jalali, is_valid_jalali, to_gregorian
from ..core.persian_text import normalize_text
from ..db.models import Invoice, InvoiceItem, ValidationIssue, Category, BusinessSettings
from ..pipeline.flat import HEADER_COLS
from ..pipeline.validate import validate, Issue, WARNING, ERROR
from ..pipeline.schema import CATEGORIES_EXPENSE, CATEGORIES_INCOME

MODEL_ORIGIN = {"LOW_CONFIDENCE", "VERIFY_DISAGREE", "TEXT_LAYER_MISMATCH", "INJECTION", "VERIFY_SKIPPED", "QUALITY", "DIRECTION"}
ITEM_COLS = ["description", "quantity", "unit", "unit_price", "discount", "line_total", "line_tax"]


def inv_to_flat(inv: Invoice) -> dict:
    d = {c: getattr(inv, c) for c in HEADER_COLS}
    d.update(kind=inv.invoice_kind, currency=inv.currency,
             items=[{"row": it.row_no, **{c: getattr(it, c) for c in ITEM_COLS}} for it in inv.items])
    return d


def apply_flat(inv: Invoice, flat: dict) -> None:
    for c in HEADER_COLS:
        if c in flat:
            setattr(inv, c, flat[c])
    inv.invoice_kind = flat.get("kind") or inv.invoice_kind
    if "currency" in flat:
        inv.currency = flat["currency"]
    p = parse_jalali(inv.date_jalali) if inv.date_jalali else None
    if p and is_valid_jalali(*p):
        inv.jalali_year, inv.jalali_month = p[0], p[1]
        inv.date_gregorian = to_gregorian(*p)
    else:
        inv.jalali_year = inv.jalali_month = inv.date_gregorian = None
    if "items" in flat:
        inv.items = [InvoiceItem(row_no=it["row"], **{c: it.get(c) for c in ITEM_COLS}) for it in flat["items"]]


def find_duplicates(s: Session, inv: Invoice) -> list[Invoice]:
    if not inv.seller_name or not inv.invoice_no:
        return []
    q = s.query(Invoice).filter(Invoice.seller_name == inv.seller_name, Invoice.invoice_no == inv.invoice_no,
                                Invoice.status.in_(["needs_review", "confirmed"]))
    if inv.id:
        q = q.filter(Invoice.id != inv.id)
    q = q.filter(or_(Invoice.visitor_id == inv.visitor_id, Invoice.visitor_id.is_(None)))
    return q.all()


def business_vat_rate(s: Session, visitor_id) -> float:
    b = s.query(BusinessSettings).filter(BusinessSettings.visitor_id == visitor_id).first()
    return b.vat_rate if b and b.vat_rate else settings.vat_rate


def revalidate(s: Session, inv: Invoice, edited: set[str] | None = None) -> list[Issue]:
    """Recompute rule-based issues from the stored values. Model-origin issues stay unless their field was edited by a human."""
    edited = edited or set()
    keep = [i for i in inv.issues if i.code in MODEL_ORIGIN and i.field not in edited]
    issues = validate(inv_to_flat(inv), vat_rate=business_vat_rate(s, inv.visitor_id))
    dups = find_duplicates(s, inv)
    if dups:
        issues.append(Issue("invoice_no", "DUPLICATE", WARNING,
                            f"فاکتور تکراری: فروشنده و شماره فاکتور یکسان با {len(dups)} رکورد دیگر."))
    inv.issues = keep + [ValidationIssue(field=i.field, code=i.code, severity=i.severity, message_fa=i.message_fa) for i in issues]
    return issues


# ---------- categories & direction ----------
_EXP_KW = [("حمل", "حمل‌ونقل"), ("بار", "حمل‌ونقل"), ("باربری", "حمل‌ونقل"), ("اجاره", "اجاره"), ("رهن", "اجاره"),
           ("ورق", "مواد اولیه"), ("پروفیل", "مواد اولیه"), ("مواد", "مواد اولیه"), ("خام", "مواد اولیه"), ("بسته‌بندی", "مواد اولیه"),
           ("دستگاه", "تجهیزات"), ("لپ‌تاپ", "تجهیزات"), ("کامپیوتر", "تجهیزات"), ("تجهیز", "تجهیزات"), ("چاپگر", "تجهیزات"),
           ("خدمات", "خدمات"), ("مشاوره", "خدمات"), ("تعمیر", "خدمات"), ("نگهداری", "خدمات"), ("تبلیغ", "خدمات")]


def suggest_category(direction: str, descriptions: list[str], model_hint: str | None) -> str | None:
    allowed = CATEGORIES_INCOME if direction == "sale" else CATEGORIES_EXPENSE
    if model_hint and normalize_text(model_hint) in [normalize_text(a) for a in allowed]:
        return next(a for a in allowed if normalize_text(a) == normalize_text(model_hint))
    text = " ".join(descriptions)
    if direction == "sale":
        return "فروش خدمات" if any(k in text for k in ("خدمات", "مشاوره", "پروژه", "نگهداری", "طراحی")) else ("فروش کالا" if text else None)
    for kw, cat in _EXP_KW:
        if kw in text:
            return cat
    return "سایر" if text else None


def infer_direction(s: Session, flat: dict, visitor_id) -> str | None:
    b = s.query(BusinessSettings).filter(BusinessSettings.visitor_id == visitor_id).first()
    if not b or not (b.name or b.economic_code):
        return None
    same = lambda name, code: (b.name and name and normalize_text(b.name) == normalize_text(name)) or \
                              (b.economic_code and code and b.economic_code == code)
    if same(flat.get("seller_name"), flat.get("seller_economic_code")):
        return "sale"
    if same(flat.get("buyer_name"), flat.get("buyer_economic_code")):
        return "purchase"
    return None


def category_id(s: Session, name: str | None) -> int | None:
    if not name:
        return None
    c = s.query(Category).filter(Category.name_fa == name).first()
    return c.id if c else None
