"""Upload validation, rate limiting, prompt-injection scanning and log masking."""
from __future__ import annotations
import logging, re, threading, time
from collections import defaultdict, deque
from ..core.persian_text import to_en_digits, normalize_text

# ---------- file type by magic bytes (never trust the extension or client MIME) ----------
def sniff_type(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    return None


# ---------- sliding-window rate limiter (in-process; swap for Redis when scaling out) ----------
class RateLimiter:
    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_s: float = 3600, cost: int = 1) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) + cost > limit:
                return False
            q.extend([now] * cost)
            return True

    def reset(self):
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()

# ---------- prompt-injection scan: invoice text is data, never instructions ----------
_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts?)",
    r"disregard (all |any )?(the )?(previous|prior|above)",
    r"you are now", r"system prompt", r"new instructions?",
    r"دستور(ات|ها)?(ی)? (قبل|پیشین|بالا)(ی)?(.{0,10})(نادیده|فراموش)",
    r"(نادیده|فراموش) ?(بگیر|کن)",
    r"(مبلغ|قیمت|جمع)[^.\n]{0,30}(را|رو)[^.\n]{0,20}(ثبت کن|تغییر بده|بگذار)",
    r"set (the )?(payable|total|amount|price)", r"(?:^|\s)(assistant|ai agent)[:,]",
]
_RX = [re.compile(p, re.I) for p in _PATTERNS]


def scan_injection(texts: list[str | None]) -> list[str]:
    hits = []
    for t in texts:
        if not t:
            continue
        n = normalize_text(str(t)) or ""
        if any(r.search(n) for r in _RX):
            hits.append(n[:160])
    return list(dict.fromkeys(hits))


# ---------- log masking: long digit runs (economic codes, IDs, phones) never reach logs ----------
_DIGITS = re.compile(r"\d{6,}")


def mask(s) -> str:
    t = to_en_digits(str(s))
    return _DIGITS.sub(lambda m: m.group()[:2] + "*" * (len(m.group()) - 2), t)


class MaskingFilter(logging.Filter):
    def filter(self, record):
        record.msg = mask(record.getMessage())
        record.args = ()
        return True


def setup_logging():
    h = logging.StreamHandler()
    h.addFilter(MaskingFilter())
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(logging.INFO)


def mask_json(obj):
    """Mask long digit runs inside a nested structure (used before persisting raw model output)."""
    if isinstance(obj, dict):
        return {k: mask_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_json(v) for v in obj]
    if isinstance(obj, str):
        return mask(obj)
    return obj
