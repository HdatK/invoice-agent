from app.core.persian_text import normalize_text, parse_amount, parse_quantity, to_en_digits, fmt_money, digits_only
from app.core.jalali import parse_jalali, is_valid_jalali, to_gregorian
from app.core.ids import valid_national_code, valid_legal_id, economic_code_format_ok


def test_digits_and_letters():
    assert to_en_digits("۱۲۳٤٥٦") == "123456"
    assert normalize_text("كيان\u200c\u200cپور ـــ ") == "کیان\u200cپور"
    assert normalize_text("  شرکت   نمونه ") == "شرکت نمونه"


def test_amounts():
    assert parse_amount("۱٬۲۵۰٬۰۰۰") == 1250000
    assert parse_amount("1,250,000 ریال") == 1250000
    assert parse_amount("abc") is None and parse_amount(None) is None
    assert parse_quantity("۲٫۵") == 2.5
    assert fmt_money(1250000) == "۱٬۲۵۰٬۰۰۰"


def test_jalali():
    assert parse_jalali("۱۴۰۵/۰۳/۱۵") == (1405, 3, 15)
    assert is_valid_jalali(1405, 6, 31) and not is_valid_jalali(1405, 7, 31) and not is_valid_jalali(1405, 13, 1)
    assert is_valid_jalali(1403, 12, 30) and not is_valid_jalali(1404, 12, 30)   # leap year 1403 only
    assert to_gregorian(1405, 1, 1).isoformat() == "2026-03-21"


def _nc(first9):
    s = sum(int(first9[i]) * (10 - i) for i in range(9)) % 11
    return first9 + str(s if s < 2 else 11 - s)


def test_national_code():
    good = _nc("123456789")
    assert valid_national_code(good)
    assert not valid_national_code(good[:-1] + str((int(good[-1]) + 1) % 10))
    assert not valid_national_code("1111111111") and not valid_national_code("123")


def test_economic_code_is_format_only():
    assert economic_code_format_ok("411100000001") and economic_code_format_ok("۴۱۱۱۰۰۰۰۰۰۰۱")
    assert not economic_code_format_ok("12345") and not economic_code_format_ok(None)


def test_legal_id_rejects_garbage():
    assert not valid_legal_id("12345") and not valid_legal_id("00000000000")
