"""Iranian identifier checks (pure functions, no model involved)."""
from __future__ import annotations
import re
from .persian_text import digits_only


def valid_national_code(code: str | None) -> bool:
    """10-digit Iranian national code with check digit."""
    c = digits_only(code)
    if not c or len(c) != 10 or len(set(c)) == 1:
        return False
    s = sum(int(c[i]) * (10 - i) for i in range(9))
    r = s % 11
    return int(c[9]) == (r if r < 2 else 11 - r)


def valid_legal_id(code: str | None) -> bool:
    """11-digit legal-entity national ID (شناسه ملی) with check digit."""
    c = digits_only(code)
    if not c or len(c) != 11 or len(set(c)) == 1:
        return False
    d = [int(x) for x in c]
    coef = [29, 27, 23, 19, 17, 29, 27, 23, 19, 17]
    s = sum((d[i] + d[9] + 2) * coef[i] for i in range(10))
    r = s % 11
    return d[10] == (0 if r == 10 else r)


def economic_code_format_ok(code: str | None, lengths=(12, 14)) -> bool:
    """Format and length only (by decision): digits only, 12 or 14 long."""
    c = digits_only(code)
    return bool(c) and len(c) in lengths and bool(re.fullmatch(r"\d+", c))
