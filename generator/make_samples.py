"""Generate synthetic invoice files with ground truth sidecars.

Usage: python -m generator.make_samples --out data/eval_set --n 70 --seed 7
Every file gets <file>.truth.json containing the printed values and the file's sha256.
"""
from __future__ import annotations
import argparse
import json
import random
from pathlib import Path
from .invoices import build_truth
from .data import sha256_file
from . import render as R

QUALITIES = ["clean"] * 3 + ["skewed"] * 2 + ["blurry", "lowlight", "pdf_text", "pdf_scan"]
DEFECTS = [None] * 7 + ["SUM_MISMATCH", "VAT_MISMATCH", "PAYABLE_MISMATCH", "BAD_DATE", "BAD_LEGAL_ID"]


def write_one(t: dict, out: Path, quality: str, rng: random.Random, digits: str, style: str) -> Path:
    base = out / t["id"]
    t["render"] = {"quality": quality, "digits": digits, "style": style}
    if quality == "pdf_text":
        path = base.with_suffix(".pdf")
        R.html_to_text_pdf(t, path, digits)
    else:
        img = R.render_clean(t, digits, style)
        if quality == "pdf_scan":
            path = base.with_suffix(".pdf")
            R.image_to_pdf(R.degrade(img, "pdf_scan", rng), path)
        else:
            path = base.with_suffix(".jpg")
            R.save_image(R.degrade(img, quality, rng) if quality != "clean" else img, path,
                         quality=70 if quality in ("blurry", "lowlight") else 90)
    t["file"] = path.name
    t["sha256"] = sha256_file(path)
    path.with_name(path.name + ".truth.json").write_text(json.dumps(t, ensure_ascii=False, indent=1), "utf-8")
    return path


def generate(out: Path, n: int, seed: int, *, injection_every: int = 15, months=range(1, 7), jy: int = 1405,
             prefix: str = "inv") -> list[dict]:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    cache: dict = {}
    res = []
    for i in range(n):
        direction = "sale" if rng.random() < 0.4 else "purchase"
        defect = rng.choice(DEFECTS)
        t = build_truth(rng, f"{prefix}{i:03d}", direction=direction, jy=jy, jm=rng.choice(list(months)),
                        defect=defect, injection=(i % injection_every == injection_every - 1), party_cache=cache)
        quality = QUALITIES[i % len(QUALITIES)]
        write_one(t, out, quality, rng, rng.choice(["fa", "fa", "ar", "en"]), rng.choice(["a", "b"]))
        res.append(t)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/eval_set")
    ap.add_argument("--n", type=int, default=70)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    ts = generate(Path(a.out), a.n, a.seed)
    print(f"generated {len(ts)} synthetic invoices in {a.out}")
