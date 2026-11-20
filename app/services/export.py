from __future__ import annotations
import csv, io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from ..core.persian_text import to_fa_digits
from ..db.models import Invoice

STATUS = {"confirmed": "تأییدشده", "needs_review": "نیازمند بازبینی", "failed": "ناموفق", "rejected": "ردشده", "processing": "در حال پردازش"}
DIR = {"purchase": "خرید (هزینه)", "sale": "فروش (درآمد)"}
KIND = {"official_sale": "رسمی", "unofficial": "غیررسمی", "proforma": "پیش‌فاکتور", "unknown": "نامشخص"}
CUR = {"IRR": "ریال", "IRT": "تومان", None: ""}
HEAD = ["شناسه", "نوع", "نوع سند", "وضعیت", "شماره فاکتور", "تاریخ شمسی", "تاریخ میلادی", "فروشنده", "کد اقتصادی فروشنده",
        "شناسه ملی فروشنده", "خریدار", "کد اقتصادی خریدار", "دسته‌بندی", "جمع کل", "تخفیف", "مالیات", "عوارض",
        "مبلغ قابل پرداخت", "واحد پول", "روش پرداخت"]
ITEM_HEAD = ["شناسه فاکتور", "شماره فاکتور", "ردیف", "شرح", "مقدار", "واحد", "قیمت واحد", "تخفیف", "مبلغ ردیف", "مالیات ردیف", "واحد پول"]


def _head_row(i: Invoice):
    return [i.id, DIR.get(i.direction), KIND.get(i.invoice_kind), STATUS.get(i.status), i.invoice_no, i.date_jalali,
            i.date_gregorian.isoformat() if i.date_gregorian else None, i.seller_name, i.seller_economic_code, i.seller_national_id,
            i.buyer_name, i.buyer_economic_code, i.category.name_fa if i.category else None, i.subtotal, i.total_discount,
            i.vat, i.other_levies, i.payable, CUR.get(i.currency), i.payment_method]


def _item_rows(i: Invoice):
    return [[i.id, i.invoice_no, it.row_no, it.description, it.quantity, it.unit, it.unit_price, it.discount, it.line_total,
             it.line_tax, CUR.get(i.currency)] for it in i.items]


def to_xlsx(invoices: list[Invoice]) -> bytes:
    wb = Workbook()
    for title, head, rows in [("فاکتورها", HEAD, [_head_row(i) for i in invoices]),
                              ("ردیف‌ها", ITEM_HEAD, [r for i in invoices for r in _item_rows(i)])]:
        ws = wb.active if title == "فاکتورها" else wb.create_sheet()
        ws.title = title
        ws.sheet_view.rightToLeft = True
        ws.append(head)
        for c in ws[1]:
            c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="0F5C52")
            c.alignment = Alignment(horizontal="center", vertical="center")
        for r in rows:
            ws.append(r)
        for col in ws.columns:
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col[:60])
            ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 3), 38)
            for c in col[1:]:
                if isinstance(c.value, (int, float)):
                    c.number_format = "#,##0"
        ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def to_csv(invoices: list[Invoice], items: bool = False) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(ITEM_HEAD if items else HEAD)
    for i in invoices:
        for r in (_item_rows(i) if items else [_head_row(i)]):
            w.writerow(["" if v is None else v for v in r])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")   # BOM so Excel opens Persian text correctly
