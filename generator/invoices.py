"""Build ground-truth invoice records (what is printed on the page)."""
from __future__ import annotations
import random
from .data import *

VAT = 0.10


def build_truth(rng: random.Random, seed_id: str, *, direction: str, jy: int, jm: int,
                kind: str | None = None, currency: str | None = None, defect: str | None = None,
                injection: bool = False, our_business: dict | None = None, party_cache: dict | None = None) -> dict:
    party_cache = party_cache if party_cache is not None else {}
    me = our_business or {**OUR_BUSINESS, "economic_code": "411100000001", "national_id": "10380284790",
                          "registration_no": "445566", "national_code": None}
    if direction == "purchase":
        name, cat = rng.choice(SUPPLIERS)
        seller = party_cache.setdefault(name, make_party(rng, name))
        buyer = {**me}
        catalog = EXPENSE_ITEMS[cat]
    else:
        seller = {**me}
        cname = rng.choice(CUSTOMERS)
        buyer = party_cache.setdefault(cname, make_party(rng, cname))
        cat = None
        catalog = SALE_ITEMS
    kind = kind or rng.choices(["official_sale", "unofficial", "proforma"], [0.72, 0.18, 0.10])[0]
    currency = currency or rng.choice(["IRT", "IRR"])
    mult = 10 if currency == "IRR" else 1
    items = []
    for i in range(1, rng.randint(1, 5) + 1):
        desc, unit, (lo, hi) = rng.choice(catalog)
        qty = rng.randint(1, 12) if unit not in ("ماه", "پروژه") else 1
        price = round(rng.randint(lo, hi), -3) * mult
        disc = round(qty * price * rng.choice([0, 0, 0, 0.05, 0.1]), -3 if mult == 1 else -4)
        total = qty * price - disc
        items.append({"row": i, "description": desc, "quantity": qty, "unit": unit, "unit_price": price,
                      "discount": int(disc), "line_total": int(total),
                      "line_tax": 0 if kind == "unofficial" else int(round(total * VAT))})
    if direction == "sale":
        cat = INCOME_CATEGORY.get(items[0]["description"].split()[0], "فروش کالا")
    subtotal = sum(it["quantity"] * it["unit_price"] for it in items)
    discount = sum(it["discount"] for it in items)
    vat = sum(it["line_tax"] for it in items)
    levies = 0
    payable = subtotal - discount + vat + levies
    totals = {"subtotal": subtotal, "total_discount": discount, "vat": vat, "other_levies": levies, "payable": payable}
    date = jalali_date(rng, jy, jm)
    expected = []
    if defect == "SUM_MISMATCH":
        totals["subtotal"] += rng.choice([-1, 1]) * rng.randint(2, 9) * 10_000 * mult
        totals["payable"] = totals["subtotal"] - discount + vat + levies  # internally consistent, rows are not
        expected = ["SUM_MISMATCH"]
    elif defect == "VAT_MISMATCH":
        totals["vat"] = int(totals["vat"] * 1.5) + 1000 * mult
        totals["payable"] = subtotal - discount + totals["vat"]
        expected = ["VAT_MISMATCH"]
    elif defect == "PAYABLE_MISMATCH":
        totals["payable"] += 50_000 * mult
        expected = ["PAYABLE_MISMATCH"]
    elif defect == "BAD_DATE":
        date = f"{jy}/{jm:02d}/31" if jm >= 7 else f"{jy}/13/05"
        expected = ["BAD_DATE"]
    elif defect == "BAD_LEGAL_ID":
        # corrupt a COPY of the seller (parties are cached and shared across invoices) and always the seller:
        # the buyer's legal ID is not an extracted field
        seller = {**seller, "national_id": seller["national_id"][:-1] + str((int(seller["national_id"][-1]) + 3) % 10)}
        expected = ["BAD_LEGAL_ID"]
    truth = {
        "id": seed_id, "direction": direction, "category": cat, "currency": currency,
        "document": {"is_invoice": True, "invoice_kind": kind, "handwritten": False},
        "seller": seller, "buyer": buyer,
        "header": {"invoice_no": str(rng.randint(1000, 99999)), "date_jalali": date,
                   "unique_tax_id": ("A" + str(rng.randint(10**9, 10**10 - 1))) if kind == "official_sale" else None},
        "items": items, "totals": totals,
        "payment": {"method": rng.choice(PAYMENT_METHODS), "notes": rng.choice(INJECTION_TEXTS) if injection else None},
        "defects": expected, "injection": injection,
    }
    return truth
