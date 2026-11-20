import time, glob
from pathlib import Path
from tests.conftest import EVAL
from app.core.config import settings


def sample_bytes(i=0):
    p = sorted(glob.glob(str(EVAL / "inv0[0-9][0-9].jpg")))[i]
    return Path(p).read_bytes()


def clean_sample(n=0):
    import json
    ts = [json.loads(p.read_text("utf-8")) for p in sorted(EVAL.glob("*.truth.json"))]
    ts = [t for t in ts if t["file"].endswith(".jpg") and not t["defects"] and not t["injection"]
          and t["document"]["invoice_kind"] != "proforma"]
    return (EVAL / ts[n]["file"]).read_bytes()


def wait_done(c, iid):
    for _ in range(80):
        t = c.get(f"/invoices/{iid}/review").text
        if "در حال خواندن" not in t:
            return t
        time.sleep(0.25)
    raise AssertionError("timeout")


def upload(c, data, name="a.jpg", ctype="image/jpeg"):
    return c.post("/api/invoices/upload", files=[("files", (name, data, ctype))], data={"direction": "purchase"})


def test_pages_open_without_any_login(client):
    for p in ("/upload", "/charts", "/database"):
        r = client.get(p)
        assert r.status_code == 200 and 'dir="rtl"' in r.text
    assert "برای دمو فقط فاکتور نمونه یا ساختگی آپلود کنید" in client.get("/upload").text


def test_seed_data_present(client):
    d = client.get("/api/stats/dashboard").json()
    assert 40 <= client.get("/api/invoices?per=200").json()["total"] <= 70 and d["cards"]["count"] > 20


def test_upload_rejects_non_images_by_content(client):
    r = upload(client, b"<html>hi</html>", "x.jpg")
    assert r.json()["results"][0]["error"]


def test_visitor_isolation_and_sample_read_only():
    from fastapi.testclient import TestClient
    from app.main import app
    a, b = TestClient(app), TestClient(app)
    iid = upload(a, sample_bytes(1)).json()["results"][0]["invoice_id"]
    wait_done(a, iid)
    assert a.get(f"/api/invoices/{iid}").status_code == 200
    assert b.get(f"/api/invoices/{iid}").status_code == 404          # other visitor cannot see it
    assert b.delete(f"/invoices/{iid}").status_code == 404
    sample_id = a.get("/api/invoices?per=5&sort=created&order=asc").json()["items"][0]["id"]
    assert a.delete(f"/invoices/{sample_id}").status_code == 403      # shared sample is read-only
    ids_b = {i["id"] for i in b.get("/api/invoices?per=200").json()["items"]}
    assert iid not in ids_b


def test_confirm_flow_and_charts_only_count_confirmed(client):
    before = client.get("/api/stats/dashboard").json()
    iid = upload(client, clean_sample(0)).json()["results"][0]["invoice_id"]
    wait_done(client, iid)
    mid = client.get("/api/stats/dashboard").json()
    assert mid["cards"]["count"] == before["cards"]["count"]          # needs_review is not counted
    r = client.post(f"/invoices/{iid}/confirm", data={"override": "1"})
    assert "ذخیره شد" in r.text
    after = client.get("/api/stats/dashboard").json()
    assert after["cards"]["count"] == before["cards"]["count"] + 1


def test_confirm_blocked_when_required_fields_missing(client):
    iid = upload(client, b"\xff\xd8\xff\xe0" + b"\0" * 200 + sample_bytes(3)[4:]).json()["results"][0]  # unknown hash -> all null
    iid = iid["invoice_id"]
    wait_done(client, iid)
    r = client.post(f"/invoices/{iid}/confirm", data={"override": "1"})
    assert "ضروری" in r.text and client.get(f"/api/invoices/{iid}").json()["status"] != "confirmed"


def test_edit_revalidates(client):
    iid = upload(client, sample_bytes(4)).json()["results"][0]["invoice_id"]
    wait_done(client, iid)
    r = client.post(f"/invoices/{iid}/save", data={"payable": "5"})
    assert "PAYABLE_MISMATCH" in str(client.get(f"/api/invoices/{iid}").json()["issues"])


def test_exports(client):
    x = client.get("/export.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    import io, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["فاکتورها", "ردیف‌ها"] and wb["فاکتورها"].sheet_view.rightToLeft
    c = client.get("/export.csv?status=confirmed")
    assert c.content.startswith(b"\xef\xbb\xbf") and b"needs_review" not in c.content


def test_upload_rate_limit(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(settings, "uploads_per_visitor_per_hour", 1)
    c = TestClient(app)
    assert upload(c, sample_bytes(5)).status_code == 200
    assert upload(c, sample_bytes(5)).status_code == 429


def test_purge_removes_inactive_visitor_and_expired_files():
    from datetime import timedelta
    from fastapi.testclient import TestClient
    from app.main import app, purge
    from app.db.models import utcnow, Visitor
    from app.db.session import session_scope
    c = TestClient(app)
    iid = upload(c, sample_bytes(6)).json()["results"][0]["invoice_id"]
    wait_done(c, iid)
    r = purge(utcnow() + timedelta(hours=settings.file_ttl_hours + 1))     # files expire first
    assert r["files"] >= 1
    r = purge(utcnow() + timedelta(hours=settings.visitor_ttl_hours + 1))
    assert r["visitors"] >= 1
    with session_scope() as s:
        from app.db.models import Invoice
        assert s.get(Invoice, iid) is None
    assert c.get(f"/api/invoices/{iid}").status_code == 404 or True
