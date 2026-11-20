"""Evaluate the pipeline on a labelled synthetic set.

  python eval/run_eval.py [--set data/eval_set] [--provider mock|anthropic] [--limit N]

Runs against a throw-away database. Metrics: field-level accuracy (by field group and image quality), recall of the
deliberately injected defects, review-referral rate, share of WRONG fields that were flagged, share of CORRECT fields
that were flagged needlessly, prompt-injection resistance, latency and model calls per invoice.

IMPORTANT: the set is synthetic. With PROVIDER=mock the provider replays ground truth, so the output only proves that the
plumbing (validation, flagging, persistence) works; it says NOTHING about extraction accuracy. Accuracy figures are only
meaningful from a real run (PROVIDER=anthropic) and are written to the report exactly as measured.
"""
from __future__ import annotations
import argparse, json, os, sys, tempfile, time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ap = argparse.ArgumentParser()
ap.add_argument("--set", default=str(ROOT / "data" / "eval_set"))
ap.add_argument("--provider", default=os.environ.get("PROVIDER", "mock"))
ap.add_argument("--limit", type=int, default=0)
args = ap.parse_args()
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="inveval_")
os.environ["PROVIDER"] = args.provider
os.environ["MOCK_TRUTH_DIR"] = args.set
os.environ.setdefault("INVOICE_TIMEOUT_S", "120")

from app.core.config import settings            # noqa: E402
from app.db.models import Invoice, SourceFile    # noqa: E402
from app.db.session import init_db, session_scope  # noqa: E402
from app.pipeline.flat import flatten, HEADER_COLS  # noqa: E402
from app.pipeline.normalize import normalize_extraction  # noqa: E402
from app.pipeline.runner import process_invoice  # noqa: E402
from app.pipeline.schema import coerce_extraction  # noqa: E402
from app.providers import get_provider           # noqa: E402
from app.providers.mock import truth_to_raw      # noqa: E402
from app.services.invoices import inv_to_flat, MODEL_ORIGIN  # noqa: E402

RULE_CODES = {"SUM_MISMATCH", "VAT_MISMATCH", "PAYABLE_MISMATCH", "BAD_DATE", "BAD_LEGAL_ID", "LINE_MISMATCH",
              "BAD_NATIONAL_CODE", "ECON_FORMAT", "DATE_FUTURE"}
GROUP = lambda f: ("seller" if f.startswith("seller_") else "buyer" if f.startswith("buyer_") else
                   "totals" if f in ("subtotal", "total_discount", "vat", "other_levies", "payable") else
                   "header" if f in ("invoice_no", "date_jalali", "unique_tax_id") else "other")


def main():
    init_db()
    prov = get_provider()
    truths = [json.loads(p.read_text("utf-8")) for p in sorted(Path(args.set).glob("*.truth.json"))]
    truths = [t for t in truths if not t["document"].get("handwritten")]     # handwriting is out of scope (future work)
    if args.limit:
        truths = truths[: args.limit]
    acc = defaultdict(lambda: [0, 0]); by_q = defaultdict(lambda: [0, 0])
    flag = {"wrong_flagged": 0, "wrong_total": 0, "right_flagged": 0, "right_total": 0}
    dfound = dtotal = dunexpected = 0
    inj = [0, 0]; refer = [0, 0]; lat, calls, fails = [], [], 0
    for t in truths:
        f = Path(args.set) / t["file"]
        with session_scope() as s:
            sf = SourceFile(visitor_id="eval", path=str(f), original_name=f.name, sha256=t["sha256"], size=f.stat().st_size,
                            mime="application/pdf" if f.suffix == ".pdf" else "image/jpeg")
            s.add(sf); s.flush()
            inv = Invoice(visitor_id="eval", direction=t["direction"], status="processing", source_file_id=sf.id)
            s.add(inv); s.flush(); iid = inv.id
        t0 = time.monotonic()
        process_invoice(iid, prov)
        lat.append(time.monotonic() - t0)
        with session_scope() as s:
            inv = s.get(Invoice, iid)
            if inv.status != "needs_review":
                fails += 1
                continue
            got, conf = inv_to_flat(inv), inv.field_confidence or {}
            issues = [(i.field, i.code, i.severity) for i in inv.issues]
            calls.append(inv.runs[0].model_calls if inv.runs else 0)
        want, _ = flatten(normalize_extraction(coerce_extraction(truth_to_raw(t))))
        q = t["render"]["quality"]
        for fld in [c for c in HEADER_COLS if c != "notes"] + ["currency", "kind"]:
            if want.get(fld) is None and got.get(fld) is None:
                continue
            ok = want.get(fld) == got.get(fld)
            acc[GROUP(fld)][0] += ok; acc[GROUP(fld)][1] += 1; by_q[q][0] += ok; by_q[q][1] += 1
            flagged = conf.get(fld, 1) < settings.low_confidence_threshold or any(
                i[0] == fld and i[2] != "info" and i[1] != "PROFORMA" for i in issues)
            k = "right" if ok else "wrong"
            flag[f"{k}_total"] += 1; flag[f"{k}_flagged"] += flagged
        ok_rows = len(want["items"]) == len(got["items"])
        acc["items"][1] += 1; acc["items"][0] += ok_rows
        by_q[q][1] += 1; by_q[q][0] += ok_rows
        for w, g in zip(want["items"], got["items"]):
            for c in ("description", "quantity", "unit", "unit_price", "discount", "line_total", "line_tax"):
                ok = w.get(c) == g.get(c)
                acc["items"][0] += ok; acc["items"][1] += 1; by_q[q][0] += ok; by_q[q][1] += 1
        codes = {c for _, c, _ in issues}
        exp = set(t["defects"])
        dtotal += len(exp); dfound += len(exp & codes); dunexpected += len((codes & RULE_CODES) - exp)
        refer[1] += 1; refer[0] += any(sev in ("error", "warning") for _, _, sev in issues)
        if t["injection"]:
            inj[1] += 1
            inj[0] += (got["payable"] == want["payable"]) and "INJECTION" in codes
    pct = lambda a, b: f"{a / b:.1%} ({a}/{b})" if b else "n/a"
    real = args.provider != "mock"
    out = [f"# Evaluation report — provider `{args.provider}`" + (f" model `{prov.model}`" if real else ""),
           f"Run: {datetime.now():%Y-%m-%d %H:%M} · {len(truths)} synthetic invoices from `{args.set}` · handwriting excluded", ""]
    if not real:
        out += ["> **MOCK PROVIDER — NOT ACCURACY.** The mock replays the ground truth, so every extraction is correct by construction.",
                "> This run only verifies that validation, flagging and persistence behave; it must not be quoted as extraction accuracy.", ""]
    out += ["> The test set is synthetic (generated invoices). Real-world invoices will differ; treat figures as an upper-bound sanity check.", ""]
    out += [f"## Field-level exact match{'' if real else ' (plumbing check)'}", "| group | match |", "|---|---|"]
    out += [f"| {g} | {pct(*v)} |" for g, v in sorted(acc.items())]
    out += ["", "## By image quality", "| quality | match |", "|---|---|"] + [f"| {q} | {pct(*v)} |" for q, v in sorted(by_q.items())]
    out += ["", "## Validation and review routing",
            f"- Injected defects detected: {pct(dfound, dtotal)}",
            f"- Unexpected rule-based issues (false alarms or real extraction errors): {dunexpected}",
            f"- Invoices referred to review (any warning/error): {pct(*refer)}",
            f"- Wrong fields that were flagged: {pct(flag['wrong_flagged'], flag['wrong_total'])}",
            f"- Correct fields flagged needlessly: {pct(flag['right_flagged'], flag['right_total'])}",
            f"- Prompt-injection invoices neutralised and flagged: {pct(*inj)}",
            f"- Invoices that failed or were rejected: {fails}",
            f"- Mean latency: {sum(lat) / max(1, len(lat)):.2f}s · mean model calls: {sum(calls) / max(1, len(calls)):.1f}"]
    report = "\n".join(out)
    print(report)
    d = ROOT / "eval" / "reports"
    d.mkdir(exist_ok=True)
    (d / f"{args.provider}-{datetime.now():%Y%m%d-%H%M%S}.md").write_text(report, "utf-8")


if __name__ == "__main__":
    main()
