"""Persian text and number normalisation."""
from __future__ import annotations
import re

_FA = "۰۱۲۳۴۵۶۷۸۹"
_AR = "٠١٢٣٤٥٦٧٨٩"
_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate(_FA)} | {ord(c): str(i) for i, c in enumerate(_AR)}
_TO_FA = {ord(str(i)): c for i, c in enumerate(_FA)}
_CHAR_MAP = {ord("ي"): "ی", ord("ى"): "ی", ord("ك"): "ک", ord("ە"): "ه", ord("ۀ"): "ه"}
_TATWEEL = "ـ"
_ZWNJ = "\u200c"
_SEPS = "٬,،\u066c' _"  # thousands separators
_DECIMAL = "٫"


def to_en_digits(s: str) -> str:
    return s.translate(_DIGIT_MAP)


def to_fa_digits(s: str | int | float | None) -> str:
    return "" if s is None else str(s).translate(_TO_FA)


def normalize_text(s: str | None) -> str | None:
    """Arabic ي/ك -> Persian ی/ک, strip tatweel, tidy ZWNJ, unify digits, collapse spaces."""
    if s is None:
        return None
    s = str(s).translate(_CHAR_MAP).replace(_TATWEEL, "")
    s = to_en_digits(s)
    s = re.sub(r"[\u200b\u200d\u200e\u200f\u202a-\u202e\ufeff]", "", s)
    s = re.sub(_ZWNJ + r"{2,}", _ZWNJ, s)
    s = re.sub(rf"\s*{_ZWNJ}\s*", _ZWNJ, s)         # ZWNJ never adjacent to spaces
    s = re.sub(r"(^|\s)" + _ZWNJ + "|" + _ZWNJ + r"(\s|$)", " ", s)  # stray ZWNJ at word edges
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def digits_only(s: str | None) -> str | None:
    if s is None:
        return None
    d = re.sub(r"\D", "", to_en_digits(str(s)))
    return d or None


def parse_amount(v) -> int | None:
    """'۱٬۲۵۰٬۰۰۰' / '1,250,000' / 1250000 -> 1250000. Non-numeric -> None (never guesses)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    s = to_en_digits(str(v)).strip()
    for sep in _SEPS:
        s = s.replace(sep, "")
    s = s.replace(_DECIMAL, ".")
    s = re.sub(r"[^\d.\-]", "", s)
    if not s or s in {"-", "."}:
        return None
    try:
        return int(round(float(s)))
    except ValueError:
        return None


def parse_quantity(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = to_en_digits(str(v)).replace(_DECIMAL, ".")
    for sep in _SEPS:
        s = s.replace(sep, "")
    s = re.sub(r"[^\d.\-]", "", s)
    try:
        return float(s) if s else None
    except ValueError:
        return None


def fmt_money(n: int | float | None) -> str:
    """Thousands-separated Persian digits (۱٬۲۵۰٬۰۰۰)."""
    if n is None:
        return "—"
    return to_fa_digits(f"{int(round(n)):,}".replace(",", "٬"))
