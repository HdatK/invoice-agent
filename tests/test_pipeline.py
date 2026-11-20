import json
from pathlib import Path
import pytest
from app.core.config import settings
from app.db.models import Invoice, SourceFile, ModelUsage
from app.db.session import init_db, session_scope
from app.pipeline.runner import process_invoice
from app.providers.mock import MockProvider
from tests.conftest import EVAL

init_db()


def make(truth):
    f = EVAL / truth["file"]
    mime = "application/pdf" if f.suffix == ".pdf" else "image/jpeg"
    with session_scope() as s:
        sf = SourceFile(visitor_id="pipeline-test", path=str(f), original_name=f.name, mime=mime, size=f.stat().st_size, sha256=truth["sha256"])
        s.add(sf); s.flush()
        inv = Invoice(visitor_id="pipeline-test", direction="auto", status="processing", source_file_id=sf.id)
        s.add(inv); s.flush()
        return inv.id


def run(truth, **kw):
    prov = MockProvider([EVAL], **kw)
    iid = make(truth)
    process_invoice(iid, prov)
    with session_scope() as s:
        inv = s.get(Invoice, iid)
        _ = inv.issues, inv.items, inv.runs
        return inv, prov


def codes(inv):
    return {i.code for i in inv.issues}


def pick(truths, **want):
    for t in truths:
        if all((t["document"].get(k) == v or t.get(k) == v) for k, v in want.items()) and not t["defects"] and not t["injection"]:
            return t
    raise LookupError(want)


def test_clean_invoice_lands_in_review_never_auto_confirmed(truths):
    inv, _ = run(truths, ) if False else run(pick(truths))
    assert inv.status == "needs_review" and inv.confirmed_at is None
    assert inv.items and inv.jalali_year and inv.date_gregorian


def test_transient_failure_is_retried(truths):
    inv, prov = run(pick(truths), fail_times=2)
    assert inv.status == "needs_review" and inv.runs[0].model_calls >= 3


def test_gives_up_after_three_failures(truths):
    inv, _ = run(pick(truths), fail_times=99)
    assert inv.status == "failed" and "ناموفق" in inv.error_message


def test_per_invoice_timeout(truths, monkeypatch):
    monkeypatch.setattr(settings, "invoice_timeout_s", 3)
    inv, _ = run(pick(truths), delay_s=30)
    assert inv.status == "failed"


def test_second_reading_disagreement_lowers_confidence(truths):
    t = pick(truths)
    inv, _ = run(t, corrupt={2: {"totals.payable": 1}})
    assert "VERIFY_DISAGREE" in codes(inv) and inv.field_confidence["payable"] <= 0.3


def test_injection_is_flagged_and_not_obeyed(truths):
    t = next(t for t in truths if t["injection"])
    inv, _ = run(t)
    assert "INJECTION" in codes(inv)
    assert inv.payable == t["totals"]["payable"]          # the planted "set payable to 1" had no effect
    assert inv.status == "needs_review"


def test_non_invoice_is_rejected(truths):
    t = dict(pick(truths)); t["document"] = {**t["document"], "is_invoice": False}
    prov = MockProvider([EVAL])
    prov.index[t["sha256"]] = t
    iid = make(t)
    process_invoice(iid, prov)
    with session_scope() as s:
        inv = s.get(Invoice, iid)
        assert inv.status == "rejected" and not inv.items


def test_unknown_file_yields_nulls_and_review(truths):
    t = dict(pick(truths)); t["sha256"] = "0" * 64
    iid = make(t)
    process_invoice(iid, MockProvider([EVAL]))
    with session_scope() as s:
        inv = s.get(Invoice, iid)
        assert inv.payable is None and inv.status == "needs_review" and "MISSING_FIELD" in {i.code for i in inv.issues}


def test_daily_cap(truths, monkeypatch):
    monkeypatch.setattr(settings, "daily_model_call_cap", 0)
    inv, _ = run(pick(truths))
    assert inv.status == "failed" and "سقف" in inv.error_message


def test_text_layer_cross_check_handles_visual_order_pdfs(truths):
    """Arabic-digit PDFs store numbers reversed in the text layer; a correct extraction must not be flagged."""
    pdfs = [t for t in truths if t["render"]["quality"] == "pdf_text" and not t["defects"] and not t["injection"]]
    assert {t["render"]["digits"] for t in pdfs} >= {"ar"}
    for t in pdfs:
        inv, _ = run(t)
        assert "TEXT_LAYER_MISMATCH" not in codes(inv), t["id"]


def test_text_layer_cross_check_catches_wrong_number(truths):
    t = next(t for t in truths if t["render"]["quality"] == "pdf_text" and not t["defects"] and not t["injection"])
    inv, _ = run(t, corrupt={1: {"totals.payable": 999_999_999}})
    assert "TEXT_LAYER_MISMATCH" in codes(inv)


def test_raw_json_is_masked(truths):
    inv, _ = run(pick(truths))
    raw = json.dumps(inv.runs[0].raw_json, ensure_ascii=False)
    assert "411100000001" not in raw
