"""Normalisation of a coerced extraction: digits, Arabic letters, amounts, Jalali -> Gregorian."""
from __future__ import annotations
from ..core.persian_text import normalize_text, digits_only, parse_amount, parse_quantity
from ..core.jalali import parse_jalali, format_jalali, to_gregorian, is_valid_jalali

ID_FIELDS = {"economic_code", "national_id", "national_code", "registration_no", "phone"}
AMOUNT = {"subtotal", "total_discount", "vat", "other_levies", "payable", "unit_price", "discount", "line_total", "line_tax"}


def _norm_field(name: str, f: dict) -> dict:
    v = f["value"]
    if v is None:
        return f
    if name in ID_FIELDS:
        nv = digits_only(v)
    elif name in AMOUNT:
        nv = parse_amount(v)
    elif name == "quantity":
        nv = parse_quantity(v)
    elif name == "date_jalali":
        p = parse_jalali(v)
        nv = format_jalali(*p) if p else normalize_text(str(v))
    elif name == "invoice_no":
        nv = normalize_text(str(v))
    else:
        nv = normalize_text(str(v))
    if nv is None:  # present but unreadable as the expected type: keep nothing, say why. Never guess.
        return {"value": None, "confidence": f["confidence"], "null_reason": "unparsable", "raw": str(v)[:80]}
    return {**f, "value": nv}


def normalize_extraction(x: dict) -> dict:
    for g in ("seller", "buyer", "header", "totals", "payment"):
        x[g] = {k: _norm_field(k, f) for k, f in x[g].items()}
    for it in x["items"]:
        for k in list(it):
            if k != "row":
                it[k] = _norm_field(k, it[k])
    d = x["header"]["date_jalali"]["value"]
    x["header"]["date_gregorian"] = None
    p = parse_jalali(d) if d else None
    if p and is_valid_jalali(*p):
        g = to_gregorian(*p)
        x["header"]["date_gregorian"] = g.isoformat() if g else None
    return x
