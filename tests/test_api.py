"""End-to-end tests van de REST-API en WebSocket (FastAPI TestClient)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app, sim

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def client():
    # Schone toestand per test (de simulator is een gedeeld object)
    sim.restore_defaults()
    sim.set_twin_mode(False, blend=1.0)
    sim.running = False
    sim.reset()
    with TestClient(app) as c:
        yield c


def test_health_and_dashboard(client):
    assert client.get("/api/health").json()["status"] == "ok"
    r = client.get("/")
    assert r.status_code == 200 and "CSTR Digital Twin" in r.text


def test_control_start_stop_reset(client):
    assert client.post("/api/control/start").json()["running"] is True
    assert client.post("/api/control/stop").json()["running"] is False
    assert client.post("/api/control/reset").json()["time"] == 0
    assert client.post("/api/control/explode").status_code == 404


def test_get_and_update_parameters(client):
    p = client.get("/api/parameters").json()
    assert p["process"]["UA"] == 600 and p["control"]["auto"] is True
    body = json.loads((EXAMPLES / "parameters_update.json").read_text())
    r = client.put("/api/parameters", json=body)
    assert r.status_code == 200
    assert r.json()["process"]["UA"] == 550
    assert r.json()["control"]["T_sp"] == 348
    assert r.json()["safety"]["T_warn"] == 390


def test_invalid_parameters_rejected(client):
    assert client.put("/api/parameters", json={"process": {"V": -1}}).status_code == 422
    assert client.put("/api/parameters", json={"process": {"q": 1}}).status_code == 422
    assert client.put("/api/parameters", json={"procss": {"Q": 1}}).status_code == 422


def test_simulate_example_request_detects_runaway(client):
    body = json.loads((EXAMPLES / "simulate_request.json").read_text())
    r = client.post("/api/simulate", json=body).json()
    assert len(r["time"]) == 200
    assert r["runaway_at"] is not None
    assert sim.t == 0  # live-simulatie niet aangeraakt


def test_twin_example_payload(client):
    body = json.loads((EXAMPLES / "live_update.json").read_text())
    r = client.post("/api/twin", json=body).json()
    assert r["applied"] is False  # twin-modus staat uit

    assert client.post("/api/twin/mode", json={"enabled": True}).json()["enabled"] is True
    r = client.post("/api/twin", json=body).json()
    assert r["applied"] is True
    assert r["state"]["CA"] == 0.5 and r["state"]["T"] == 350
    # Mantelmodus: Q wordt omgerekend naar koelmiddeltemperatuur Tc = T + Q/UA
    assert r["derived"]["Tc"] == pytest.approx(350 - 5000 / 600)


def test_twin_batch_and_extra_fields(client):
    client.post("/api/twin/mode", json={"enabled": True})
    batch = json.loads((EXAMPLES / "live_update_batch.json").read_text())
    assert client.post("/api/twin", json=batch).json()["processed"] == 3
    full = json.loads((EXAMPLES / "live_update_full.json").read_text())
    assert client.post("/api/twin", json=full).status_code == 200


def test_twin_rejects_invalid_measurements(client):
    assert client.post("/api/twin", json={}).status_code == 422
    assert client.post("/api/twin", json={"T": -5}).status_code == 422
    assert client.post("/api/twin", json={"CA": "veel"}).status_code == 422


def test_twin_api_key(client, monkeypatch):
    monkeypatch.setattr(config, "TWIN_API_KEY", "geheim")
    assert client.post("/api/twin", json={"T": 400}).status_code == 401
    ok = client.post("/api/twin", json={"T": 400}, headers={"X-API-Key": "geheim"})
    assert ok.status_code == 200


def test_failsafe_via_measurement_and_reset_rules(client):
    client.post("/api/twin/mode", json={"enabled": True})
    r = client.post("/api/twin", json={"T": 620}).json()
    assert r["failsafe"]["tripped"] is True
    assert r["failsafe"]["recommended_Q"] < 0
    assert client.post("/api/failsafe/reset").status_code == 409  # nog te heet
    client.post("/api/twin", json={"T": 380})
    assert client.post("/api/failsafe/reset").status_code == 200


def test_steady_states_endpoint(client):
    ss = client.get("/api/steady-states").json()["steady_states"]
    assert any(not s["stable"] and abs(s["T"] - 350) < 0.5 for s in ss)  # werkpunt open-loop instabiel


def test_heat_balance_endpoint(client):
    hb = client.get("/api/heat-balance").json()
    assert len(hb["T"]) == len(hb["Q_gen"]) == len(hb["Q_rem"])
    assert hb["dT_ad"] == pytest.approx(357.14, abs=0.01)
    assert hb["current"]["T"] == pytest.approx(350, abs=0.5)


def test_simulate_what_if_manual_mode_from_current_state(client):
    r = client.post("/api/simulate", json={"control": {"auto": False}, "parameters": {"T_in": 302},
                                           "from_current_state": True, "duration": 60, "n_points": 61}).json()
    assert abs(r["T"][-1] - 350) > 20  # open-loop instabiel: loopt weg


def _case_collisions(obj):
    """Zoek sleutels die alleen in hoofdletters verschillen (bv. 't' en 'T'), recursief."""
    found = []
    if isinstance(obj, dict):
        lower = {}
        for k in obj:
            if k.lower() in lower:
                found.append((lower[k.lower()], k))
            lower[k.lower()] = k
        for v in obj.values():
            found += _case_collisions(v)
    elif isinstance(obj, list):
        for v in obj[:3]:
            found += _case_collisions(v)
    return found


def test_json_keys_are_case_insensitively_unique(client):
    """PowerShell 5.1 (Invoke-RestMethod) kan JSON met 't' en 'T' niet parsen."""
    client.post("/api/twin/mode", json={"enabled": True})
    client.post("/api/twin", json={"T": 350, "CA": 1.0})
    for url in ("/api/state?history=true", "/api/twin", "/api/parameters", "/api/heat-balance"):
        assert _case_collisions(client.get(url).json()) == [], url
    sim_result = client.post("/api/simulate", json={"duration": 5, "n_points": 10}).json()
    assert _case_collisions(sim_result) == []


def test_websocket_sends_init_with_history(client):
    with client.websocket_connect("/ws") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "init"
        assert "history" in msg and "parameters" in msg
