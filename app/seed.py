"""First-run demo data: ~50 synthetic invoices (shared, read-only, visitor_id NULL) rendered as real image/PDF files.

Usage: python -m app.seed [--force]
"""
from __future__ import annotations
import json, sys
from datetime import datetime
from pathlib import Path
from .core.config import settings
from .db.models import Invoice, SourceFile, BusinessSettings
from .db.session import init_db, session_scope
from .pipeline.schema import coerce_extraction
from .pipeline.normalize import normalize_extraction
from .pipeline.flat import flatten
from .providers.mock import truth_to_raw
from .services import invoices as svc

N_SAMPLES, SEED = 52, 1405


def seed_if_empty(force: bool = False) -> int:
    init_db()
    with session_scope() as s:
        if not force and s.query(SourceFile).filter(SourceFile.visitor_id.is_(None), SourceFile.original_name.like("smp%")).count():
            return 0
    from generator.make_samples import generate
    out = settings.samples_dir
    truths = generate(out, N_SAMPLES, SEED, injection_every=17, months=range(1, 8), prefix="smp")
    n = 0
    with session_scope() as s:
        for k, t in enumerate(truths):
            f = out / t["file"]
            sf = SourceFile(visitor_id=None, path=str(f), original_name=f.name, size=f.stat().st_size, sha256=t["sha256"],
                            mime="application/pdf" if f.suffix == ".pdf" else "image/jpeg", expires_at=None)
            s.add(sf); s.flush()
            x = normalize_extraction(coerce_extraction(truth_to_raw(t)))
            flat, conf = flatten(x)
            inv = Invoice(visitor_id=None, direction=t["direction"], source_file_id=sf.id, status="needs_review",
                          field_confidence={k2: v for k2, v in conf.items()}, created_at=datetime.utcnow())
            svc.apply_flat(inv, flat)
            inv.category_id = svc.category_id(s, t.get("category"))
            s.add(inv); s.flush()
            issues = svc.revalidate(s, inv)
            bad = [i for i in issues if i.severity == "error"]
            # sample data: clean invoices are confirmed; flawed ones are left for review so the review flow is visible
            if not bad and not t["injection"]:
                inv.status, inv.confirmed_at = "confirmed", datetime.utcnow()
            n += 1
        s.flush()
    return n


if __name__ == "__main__":
    print(f"seeded {seed_if_empty('--force' in sys.argv)} sample invoices")
