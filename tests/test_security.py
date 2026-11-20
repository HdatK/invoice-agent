from app.security.guards import sniff_type, scan_injection, mask, RateLimiter, mask_json
from generator.data import INJECTION_TEXTS


def test_magic_bytes():
    assert sniff_type(b"\xff\xd8\xff\xe0abc") == "image/jpeg"
    assert sniff_type(b"%PDF-1.7") == "application/pdf"
    assert sniff_type(b"MZ\x90\x00") is None and sniff_type(b"<html>") is None


def test_injection_texts_detected_and_normal_text_not():
    for t in INJECTION_TEXTS:
        assert scan_injection([t]), t
    assert not scan_injection(["حمل بار درون‌شهری", "مبلغ قابل پرداخت ۱٬۲۰۰٬۰۰۰ تومان", "تماس با ما: ۰۲۱۸۸۷۷۶۶۵۵"])


def test_log_masking():
    assert "4111" not in mask("کد اقتصادی ۴۱۱۱۰۰۰۰۰۰۰۱ ثبت شد") and mask("short 123").endswith("123")
    assert "411100000001" not in str(mask_json({"a": ["411100000001"]}))


def test_rate_limiter():
    r = RateLimiter()
    assert all(r.allow("k", 3) for _ in range(3)) and not r.allow("k", 3) and r.allow("other", 3)
    assert not RateLimiter().allow("x", 2, cost=3)
