"""Flask web interface: login, RBAC, CSRF, forms, PDFs and duplicate prevention."""
import pytest

import reefwatch_app as rw
from flask_app import create_app


@pytest.fixture()
def client(seeded):
    app = create_app(seeded)
    app.config["TESTING"] = True
    return app.test_client()


def token(client):
    with client.session_transaction() as sess:
        return sess.setdefault("_csrf", "test-token")


def post(client, url, data=None, **kw):
    return client.post(url, data={**(data or {}), "_csrf": token(client)}, **kw)


def login(client, user, password):
    return post(client, "/login", {"username": user, "password": password}, follow_redirects=True)


def test_requires_login(client):
    assert client.get("/").status_code == 302
    assert client.get("/surveys").status_code == 302


def test_bad_login_and_csrf(client):
    assert b"Invalid" in login(client, "staff", "wrong").data
    assert client.post("/login", data={"username": "admin", "password": "admin123"}).status_code == 400


def test_field_staff_permissions(client):
    login(client, "staff", "staff123")
    for url in ["/", "/surveys", "/surveys/new", "/equipment"]:
        assert client.get(url).status_code == 200, url
    for url in ["/partners", "/summaries", "/charts"]:
        assert client.get(url).status_code == 403, url
    assert post(client, "/summaries/generate", {"date": rw.today_str()}).status_code == 403
    assert post(client, "/equipment/new", {}).status_code == 403


def test_staff_can_add_survey_and_get_pdf(client, seeded):
    login(client, "staff", "staff123")
    before = seeded.cosmos.get_equipment("eq-001")["available_quantity"]
    r = post(client, "/surveys/new", {"r_name": "X", "r_email": "", "r_inst": "", "site": "S",
                                      "date": rw.today_str(), "cost": "10", "species": "Clownfish:2",
                                      "notes": "", "qty_eq-001": "2"}, follow_redirects=True)
    assert b"Survey saved" in r.data
    assert seeded.cosmos.get_equipment("eq-001")["available_quantity"] == before - 2
    sid = seeded.cosmos.list_surveys(1)[0]["_id"]
    assert client.get(f"/surveys/{sid}/pdf").data[:4] == b"%PDF"


def test_validation_errors_are_flashed(client):
    login(client, "staff", "staff123")
    r = post(client, "/surveys/new", {"r_name": "X", "r_email": "", "r_inst": "", "site": "S",
                                      "date": rw.today_str(), "cost": "10", "species": "bad:x",
                                      "notes": ""}, follow_redirects=True)
    assert b"not a whole number" in r.data


def test_admin_pages_pdfs_and_duplicates(client, seeded):
    login(client, "admin", "admin123")
    for url in ["/", "/surveys", "/equipment", "/partners", "/summaries", "/charts"]:
        assert client.get(url).status_code == 200, url
    today = rw.today_str()
    assert b"generated" in post(client, "/summaries/generate", {"date": today}, follow_redirects=True).data
    assert b"already exists" in post(client, "/summaries/generate", {"date": today}, follow_redirects=True).data
    assert client.get(f"/summaries/{today}/pdf").data[:4] == b"%PDF"
    assert b"not a valid date" in post(client, "/summaries/generate", {"date": "nope"}, follow_redirects=True).data


def test_admin_restock_and_add_partner(client, seeded):
    login(client, "admin", "admin123")
    before = seeded.cosmos.get_equipment("eq-011")["available_quantity"]
    post(client, "/equipment/eq-011/restock", {"qty": "5"})
    assert seeded.cosmos.get_equipment("eq-011")["available_quantity"] == before + 5
    post(client, "/partners/new", {"org_name": "New Org", "org_type": "NGO", "c_name": "A",
                                   "c_email": "a@x.com", "c_phone": "1", "areas": "Reef A, Reef B"})
    assert any(p["org_name"] == "New Org" for p in seeded.cosmos.list_partners())


def test_cron_endpoint_disabled_without_token(client):
    assert client.post("/cron/daily-summary").status_code == 404


def test_cron_endpoint_with_token(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "CRON_TOKEN", "secret")
    assert client.post("/cron/daily-summary", headers={"X-Cron-Token": "bad"}).status_code == 403
    r = client.post("/cron/daily-summary?date=2026-10-01", headers={"X-Cron-Token": "secret"})
    assert r.json == {"date": "2026-10-01", "created": True}
    assert client.post("/cron/daily-summary?date=2026-10-01",
                       headers={"X-Cron-Token": "secret"}).json["created"] is False
