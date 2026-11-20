"""Fixed extraction schema (schema_version 1.0). Every field is {value, confidence, null_reason}."""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field, ValidationError

SCHEMA_VERSION = "1.0"
CATEGORIES_EXPENSE = ["مواد اولیه", "حمل‌ونقل", "تجهیزات", "خدمات", "اجاره", "سایر"]
CATEGORIES_INCOME = ["فروش کالا", "فروش خدمات", "سایر"]
KINDS = ["official_sale", "unofficial", "proforma", "unknown"]

GROUPS = {
    "seller": ["name", "economic_code", "national_id", "registration_no", "address", "phone"],
    "buyer": ["name", "economic_code", "national_code", "address"],
    "header": ["invoice_no", "date_jalali", "unique_tax_id"],
    "totals": ["subtotal", "total_discount", "vat", "other_levies", "payable"],
    "payment": ["method", "notes"],
}
ITEM_FIELDS = ["description", "quantity", "unit", "unit_price", "discount", "line_total", "line_tax"]


class F(BaseModel):
    value: Any = None
    confidence: float = Field(0.0, ge=0, le=1)
    null_reason: str | None = None


def _f(raw) -> dict:
    """Lenient coercion of one field. A bare value gets a middling confidence rather than a made-up high one."""
    if isinstance(raw, dict) and "value" in raw:
        try:
            f = F(**{k: raw.get(k) for k in ("value", "confidence", "null_reason") if k in raw})
        except ValidationError:
            f = F(value=raw.get("value"), confidence=0.3)
    else:
        f = F(value=raw, confidence=0.5)
    if f.value in ("", []):
        f.value = None
    if f.value is None and not f.null_reason:
        f.null_reason = "absent"
    return f.model_dump()


def coerce_extraction(raw: Any) -> dict:
    """Return a canonical extraction dict, or raise ValueError if the model output is not usable."""
    if not isinstance(raw, dict):
        raise ValueError("extraction is not a JSON object")
    doc = raw.get("document") or {}
    out: dict = {
        "schema_version": SCHEMA_VERSION,
        "document": {
            "is_invoice": bool(doc.get("is_invoice", True)),
            "invoice_kind": doc.get("invoice_kind") if doc.get("invoice_kind") in KINDS else "unknown",
            "handwritten": bool(doc.get("handwritten", False)),
            "suggested_category": doc.get("suggested_category"),
            "quality_notes": [str(x) for x in (doc.get("quality_notes") or [])][:10],
        },
    }
    for g, names in GROUPS.items():
        src = raw.get(g) or {}
        out[g] = {n: _f(src.get(n)) for n in names}
    items = []
    for i, it in enumerate(raw.get("items") or [], 1):
        if isinstance(it, dict):
            items.append({"row": i, **{n: _f(it.get(n)) for n in ITEM_FIELDS}})
    out["items"] = items
    meta = raw.get("meta") or {}
    cur = meta.get("currency")
    out["meta"] = {"currency": cur if cur in ("IRR", "IRT") else None,
                   "currency_evidence": meta.get("currency_evidence")}
    out["injection_flags"] = [str(x)[:200] for x in (raw.get("injection_flags") or [])][:10]
    return out


def json_schema_for_tool() -> dict:
    """JSON Schema handed to a model's structured-output / tool-use API."""
    fobj = {"type": "object", "required": ["value", "confidence"], "properties": {
        "value": {"type": ["string", "number", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "null_reason": {"type": ["string", "null"], "enum": ["illegible", "absent", None]}}}

    def grp(names):
        return {"type": "object", "properties": {n: fobj for n in names}}
    return {"type": "object", "properties": {
        "document": {"type": "object", "properties": {
            "is_invoice": {"type": "boolean"}, "invoice_kind": {"type": "string", "enum": KINDS},
            "handwritten": {"type": "boolean"}, "suggested_category": {"type": ["string", "null"]},
            "quality_notes": {"type": "array", "items": {"type": "string"}}}, "required": ["is_invoice"]},
        **{g: grp(n) for g, n in GROUPS.items()},
        "items": {"type": "array", "items": grp(ITEM_FIELDS)},
        "meta": {"type": "object", "properties": {"currency": {"type": ["string", "null"], "enum": ["IRR", "IRT", None]},
                                                  "currency_evidence": {"type": ["string", "null"]}}},
        "injection_flags": {"type": "array", "items": {"type": "string"}}},
        "required": ["document", "seller", "buyer", "header", "items", "totals"]}
