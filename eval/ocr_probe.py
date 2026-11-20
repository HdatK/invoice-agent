"""Probe: can local Tesseract (fas+eng) confirm key numbers on our samples?

For every sample image we check whether each key printed number appears as a digit run in the OCR text.
This tells us whether OCR is usable as the independent verification signal (decision 3).
Usage: python eval/ocr_probe.py data/eval_set
"""
from __future__ import annotations
import json, os, re, subprocess, sys, tempfile
from collections import defaultdict
from pathlib import Path
import cv2, numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core.persian_text import to_en_digits  # noqa: E402

os.environ.setdefault("TESSDATA_PREFIX", str(ROOT / "tessdata"))


def ocr(img_bgr) -> str:
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    if w < 1800:
        g = cv2.resize(g, None, fx=1800 / w, fy=1800 / w, interpolation=cv2.INTER_CUBIC)
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        cv2.imwrite(f.name, g)
        r = subprocess.run(["tesseract", f.name, "-", "-l", "fas+eng", "--psm", "6"], capture_output=True, text=True, timeout=60)
    return r.stdout


def digit_runs(text: str) -> set[str]:
    t = to_en_digits(text)
    t = re.sub(r"(?<=\d)[,٬،.\s](?=\d{3}\b)", "", t)
    return set(re.findall(r"\d+", t))


def key_numbers(t) -> dict:
    tt = t["totals"]
    return {"invoice_no": t["header"]["invoice_no"], "payable": tt["payable"], "subtotal": tt["subtotal"],
            "vat": tt["vat"], "seller_econ": t["seller"]["economic_code"],
            "date": re.sub(r"\D", "", t["header"]["date_jalali"])}


def main(folder: str):
    import fitz
    stats = defaultdict(lambda: [0, 0])
    for tp in sorted(Path(folder).glob("*.truth.json")):
        t = json.loads(tp.read_text("utf-8"))
        f = tp.with_name(t["file"])
        if f.suffix == ".pdf":
            doc = fitz.open(str(f)); pix = doc[0].get_pixmap(dpi=200)
            img = cv2.cvtColor(np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n), cv2.COLOR_RGB2BGR)
        else:
            img = cv2.imread(str(f))
        runs = digit_runs(ocr(img))
        q = t["render"]["quality"]
        for k, v in key_numbers(t).items():
            if v in (None, ""):
                continue
            hit = re.sub(r"\D", "", str(v)) in runs or any(re.sub(r"\D", "", str(v)) in r for r in runs if len(r) >= len(str(v)))
            stats[q][0] += int(hit); stats[q][1] += 1
    print("OCR probe — share of key numbers found in OCR text, per image quality (synthetic samples)")
    tot = [0, 0]
    for q, (h, n) in sorted(stats.items()):
        print(f"  {q:9s} {h:3d}/{n:3d}  {h / n:5.0%}"); tot[0] += h; tot[1] += n
    print(f"  {'ALL':9s} {tot[0]:3d}/{tot[1]:3d}  {tot[0] / tot[1]:5.0%}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/eval_set")
