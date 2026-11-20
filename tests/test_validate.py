from app.pipeline.schema import coerce_extraction
from app.pipeline.normalize import normalize_extraction
from app.pipeline.flat import flatten
from app.pipeline.validate import validate
from app.providers.mock import truth_to_raw


def flat_of(t):
    return flatten(normalize_extraction(coerce_extraction(truth_to_raw(t))))[0]


def test_injected_defects_are_all_found_and_clean_ones_are_quiet(truths):
    found = missed = false_pos = 0
    for t in truths:
        codes = {i.code for i in validate(flat_of(t))} - {"PROFORMA"}
        exp = set(t["defects"])
        found += len(exp & codes); missed += len(exp - codes); false_pos += len(codes - exp)
    assert found > 0 and missed == 0 and false_pos == 0


def test_unreadable_field_is_null_with_reason():
    raw = truth_to_raw({"document": {"is_invoice": True, "invoice_kind": "official_sale"}, "seller": {"name": "الف"}, "buyer": {},
                        "header": {"invoice_no": "۱۲۳", "date_jalali": "1405/01/01"}, "totals": {"payable": "نامشخص"},
                        "payment": {}, "items": [], "currency": "IRT"})
    x = normalize_extraction(coerce_extraction(raw))
    f = x["totals"]["payable"]
    assert f["value"] is None and f["null_reason"] == "unparsable"          # never guessed
    codes = {i.code for i in validate(flatten(x)[0])}
    assert "MISSING_FIELD" in codes


def test_missing_currency_is_an_error(truths):
    flat = flat_of(truths[0]); flat["currency"] = None
    assert any(i.code == "CURRENCY_MISSING" and i.severity == "error" for i in validate(flat))


def test_vat_rate_is_configurable(truths):
    t = next(t for t in truths if t["document"]["invoice_kind"] == "official_sale" and not t["defects"])
    flat = flat_of(t)
    assert not any(i.code == "VAT_MISMATCH" for i in validate(flat, vat_rate=0.10))
    assert any(i.code == "VAT_MISMATCH" for i in validate(flat, vat_rate=0.09))


def test_unofficial_invoice_without_vat_is_fine(truths):
    t = next(t for t in truths if t["document"]["invoice_kind"] == "unofficial" and not t["defects"])
    assert not any(i.code == "VAT_MISMATCH" for i in validate(flat_of(t)))
