"""Mock provider for tests and offline demos.

It cannot read images: it looks the file up by SHA-256 in the sidecar `*.truth.json` files written by the sample
generator and replays that ground truth. Unknown files yield an all-null extraction (honest: nothing was read).
"""
from __future__ import annotations
import json, random
from pathlib import Path
from .base import ProviderError

_CONF = 0.95


def _field(v, conf=_CONF):
    return {"value": v, "confidence": conf if v is not None else 0.9, "null_reason": None if v is not None else "absent"}


def truth_to_raw(t: dict, conf: float = _CONF) -> dict:
    g = lambda d: {k: _field(v, conf) for k, v in d.items()}
    return {
        "document": {**t["document"], "suggested_category": t.get("category"), "quality_notes": []},
        "seller": g(t["seller"]), "buyer": g(t["buyer"]),
        "header": g(t["header"]), "totals": g(t["totals"]), "payment": g(t["payment"]),
        "items": [g({k: v for k, v in it.items() if k != "row"}) for it in t["items"]],
        "meta": {"currency": t["currency"], "currency_evidence": "mock"},
        "injection_flags": [],
    }


class MockProvider:
    name = "mock"
    model = "mock-1"

    def __init__(self, truth_dirs: list[Path] | None = None, *, fail_times: int = 0, corrupt: dict | None = None,
                 delay_s: float = 0.0):
        self.index: dict[str, dict] = {}
        for d in truth_dirs or []:
            for p in Path(d).glob("*.truth.json"):
                t = json.loads(p.read_text("utf-8"))
                self.index[t["sha256"]] = t
        self.fail_times = fail_times
        self.corrupt = corrupt or {}      # {variant: {"totals.payable": 123}} -> deliberate disagreement for tests
        self.delay_s = delay_s
        self.calls = 0

    def extract(self, images, text, *, hints=None, variant=1, timeout=45.0, source_sha=None) -> dict:
        import time
        self.calls += 1
        if self.delay_s:
            time.sleep(min(self.delay_s, timeout))
            if self.delay_s > timeout:
                raise ProviderError("timeout", retryable=False)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ProviderError("simulated transient failure", retryable=True)
        t = self.index.get(source_sha or "")
        if t is None:
            empty = {"document": {"is_invoice": True, "invoice_kind": "unknown", "quality_notes": ["mock: unknown file"]},
                     "seller": {}, "buyer": {}, "header": {}, "totals": {}, "payment": {}, "items": [], "meta": {}}
            return empty
        raw = truth_to_raw(t)
        for path, val in self.corrupt.get(variant, {}).items():
            grp, key = path.split(".")
            raw[grp][key] = _field(val, 0.9)
        return raw
