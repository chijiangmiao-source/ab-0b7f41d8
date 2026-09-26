"""接口代码测试：回查、裁决、不可行保存、可定位拒绝。"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_unique_fault_roundtrip(client):
    payload = {"channels": ["1", "2", "3"], "checks": [
        {"channels": ["1", "2"], "parity": 1},
        {"channels": ["2", "3"], "parity": 1},
        {"channels": ["1", "3"], "parity": 0}]}
    r = client.post("/api/reviews", json=payload)
    assert r.status_code == 201
    rec = r.json()
    assert rec["review_id"].startswith("R-")
    res = rec["result"]
    assert res["status"] == "optimal"
    assert res["fault_channels"] == ["2"]
    assert res["weight"] == 1
    assert res["selection_vector"] == "010"
    assert all(c["ok"] for c in res["checks"])
    # 刷新后按复核编号可回查故障通道与逐校验复算
    r2 = client.get(f"/api/reviews/{rec['review_id']}")
    assert r2.status_code == 200
    assert r2.json()["result"] == res
    assert r2.json()["input"] == payload


def test_tie_break(client):
    r = client.post("/api/reviews", json={
        "channels": ["1", "2", "3", "4"],
        "checks": [
            {"channels": ["1", "2"], "parity": 1},
            {"channels": ["3", "4"], "parity": 1}]})
    assert r.status_code == 201
    res = r.json()["result"]
    assert res["weight"] == 2
    assert res["fault_channels"] == ["2", "4"]
    assert res["selection_vector"] == "0101"


def test_infeasible_saved(client):
    r = client.post("/api/reviews", json={
        "channels": ["1", "2", "3"],
        "checks": [
            {"channels": ["1", "2"], "parity": 0},
            {"channels": ["1", "3"], "parity": 0},
            {"channels": ["2", "3"], "parity": 1}]})
    assert r.status_code == 201
    rec = r.json()
    assert rec["result"]["status"] == "infeasible"
    assert rec["result"]["fault_channels"] is None
    assert rec["result"]["weight"] is None
    # 不可行结论已保存，可按复核编号回查
    r2 = client.get(f"/api/reviews/{rec['review_id']}")
    assert r2.status_code == 200
    assert r2.json()["result"]["status"] == "infeasible"


def test_parity_accepts_string(client):
    r = client.post("/api/reviews", json={
        "channels": ["1", "2"],
        "checks": [{"channels": ["1", "2"], "parity": "1"}]})
    assert r.status_code == 201
    assert r.json()["result"]["weight"] == 1


def test_duplicate_channels_locatable(client):
    r = client.post("/api/reviews", json={
        "channels": ["1", "1"],
        "checks": [{"channels": ["1"], "parity": 1}]})
    assert r.status_code == 422
    locs = [tuple(e["loc"]) for e in r.json()["detail"]]
    assert ("channels", 1) in locs


def test_duplicate_check_set_locatable(client):
    r = client.post("/api/reviews", json={
        "channels": ["1", "2"],
        "checks": [
            {"channels": ["1", "2"], "parity": 1},
            {"channels": ["2", "1"], "parity": 0}]})
    assert r.status_code == 422
    locs = [tuple(e["loc"]) for e in r.json()["detail"]]
    assert ("checks", 1, "channels") in locs


def test_unknown_channel_in_check_locatable(client):
    r = client.post("/api/reviews", json={
        "channels": ["1", "2"],
        "checks": [{"channels": ["1", "9"], "parity": 1}]})
    assert r.status_code == 422
    locs = [tuple(e["loc"]) for e in r.json()["detail"]]
    assert ("checks", 0, "channels", 1) in locs


def test_empty_check_rejected(client):
    r = client.post("/api/reviews", json={
        "channels": ["1", "2"],
        "checks": [{"channels": [], "parity": 1}]})
    assert r.status_code == 422
    assert any(tuple(e["loc"]) == ("checks", 0, "channels") for e in r.json()["detail"])


def test_channel_count_bounds(client):
    r = client.post("/api/reviews", json={
        "channels": ["1"], "checks": [{"channels": ["1"], "parity": 1}]})
    assert r.status_code == 422
    r = client.post("/api/reviews", json={
        "channels": [f"C{i}" for i in range(37)],
        "checks": [{"channels": ["C0"], "parity": 1}]})
    assert r.status_code == 422
    assert any(tuple(e["loc"]) == ("channels",) for e in r.json()["detail"])


def test_check_count_bound(client):
    checks = [
        {"channels": [f"C{i}", f"C{(i + 1) % 36}"], "parity": 0}
        for i in range(29)
    ]
    r = client.post("/api/reviews", json={
        "channels": [f"C{i}" for i in range(36)], "checks": checks})
    assert r.status_code == 422
    assert any(tuple(e["loc"]) == ("checks",) for e in r.json()["detail"])


def test_parity_invalid_locatable(client):
    r = client.post("/api/reviews", json={
        "channels": ["1", "2"],
        "checks": [{"channels": ["1"], "parity": 2}]})
    assert r.status_code == 422
    assert any(tuple(e["loc"]) == ("checks", 0, "parity") for e in r.json()["detail"])


def test_unknown_review_404(client):
    r = client.get("/api/reviews/R-doesnotexist")
    assert r.status_code == 404
