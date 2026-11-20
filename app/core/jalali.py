"""Jalali (Shamsi) date helpers. Both calendars are always stored."""
from __future__ import annotations
import re
from datetime import date
from persiantools.jdatetime import JalaliDate
from .persian_text import to_en_digits

MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
          "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def parse_jalali(s) -> tuple[int, int, int] | None:
    """Parse 1405/03/15, ۱۴۰۵-۰۳-۱۵ ... -> (y, m, d) without validating the calendar."""
    if not s:
        return None
    t = to_en_digits(str(s)).strip()
    m = re.match(r"^(\d{4})\D+(\d{1,2})\D+(\d{1,2})$", t)
    if not m:
        m2 = re.match(r"^(\d{4})(\d{2})(\d{2})$", t)
        if not m2:
            return None
        m = m2
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_valid_jalali(y: int, m: int, d: int) -> bool:
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return False
    try:
        JalaliDate(y, m, d)
        return True
    except ValueError:
        return False


def to_gregorian(y: int, m: int, d: int) -> date | None:
    try:
        return JalaliDate(y, m, d).to_gregorian()
    except ValueError:
        return None


def format_jalali(y: int, m: int, d: int) -> str:
    return f"{y:04d}/{m:02d}/{d:02d}"


def today_jalali() -> JalaliDate:
    return JalaliDate.today()


def month_label(y: int, m: int) -> str:
    return f"{MONTHS[m - 1]} {y}"
