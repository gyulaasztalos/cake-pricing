"""Smoke tests for the skeleton app."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readyz():
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_index_is_utf8_hungarian():
    r = client.get("/")
    assert r.status_code == 200
    # Hungarian accented text must survive round-trip (UTF-8 is critical).
    assert "árazó" in r.text
