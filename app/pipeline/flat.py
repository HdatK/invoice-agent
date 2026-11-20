"""Flat invoice dict <-> extraction dict. One flat shape is shared by validation, DB and UI."""
from __future__ import annotations

HEADER_COLS = (["seller_" + n for n in ["name", "economic_code", "national_id", "registration_no", "address", "phone"]]
               + ["buyer_" + n for n in ["name", "economic_code", "national_code", "address"]]
               + ["invoice_no", "date_jalali", "unique_tax_id", "subtotal", "total_discount", "vat", "other_levies",
                  "payable", "payment_method", "notes"])
_PAY = {"method": "payment_method", "notes": "notes"}


def flatten(x: dict) -> tuple[dict, dict]:
    """Return (flat values, flat confidences). Items live under flat['items']."""
    vals, conf = {}, {}
    for g in ("seller", "buyer", "header", "totals", "payment"):
        for k, f in x[g].items():
            if not isinstance(f, dict):
                continue
            name = _PAY.get(k, k) if g == "payment" else (f"{g}_{k}" if g in ("seller", "buyer") else k)
            vals[name], conf[name] = f["value"], f["confidence"]
    vals["date_gregorian"] = x["header"].get("date_gregorian")
    vals["kind"] = x["document"]["invoice_kind"]
    vals["currency"] = x["meta"]["currency"]
    items = []
    for it in x["items"]:
        row = {"row": it["row"]}
        for k, f in it.items():
            if k != "row":
                row[k] = f["value"]
                conf[f"items.{it['row']}.{k}"] = f["confidence"]
        items.append(row)
    vals["items"] = items
    return vals, conf
