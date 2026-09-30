"""
FastAPI-applicatie: REST-API + WebSocket + statische frontend.

Endpoints (volledige beschrijving ook op http://localhost:8000/docs)
---------------------------------------------------------------------
GET   /                        operator-dashboard (static/index.html)
GET   /api/health              levenscheck
GET   /api/state               actuele toestand (+ ?history=true)
POST  /api/control/{action}    start | stop | reset
GET   /api/parameters          alle parameters
PUT   /api/parameters          gedeeltelijke update (ook POST toegestaan)
POST  /api/parameters/defaults standaardwaarden herstellen
GET   /api/steady-states       open-loop stationaire toestanden (bij huidige Tc) + stabiliteit
GET   /api/heat-balance        Semenov-diagram: warmteproductie vs. warmteafvoer
POST  /api/simulate            offline simulatie / voorspelling
GET   /api/twin                status van de digital twin
POST  /api/twin                live-meting(en) ontvangen
POST  /api/twin/mode           twin-modus aan/uit + blend-factor
POST  /api/failsafe/reset      trip bevestigen (alleen als T < T_warn)
WS    /ws                      real-time stroom van toestanden
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import List, Optional, Union

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import config
from .model import (
    ControllerSettings,
    ProcessParameters,
    adiabatic_temperature_rise,
    controller_bumpless,
    find_steady_states,
    simulate,
)
from .schemas import ParametersUpdate, SimulateRequest, TwinMeasurement, TwinModeRequest
from .simulator import LiveSimulator

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("cstr.api")

# Eén gedeelde simulator voor het hele proces.
sim = LiveSimulator()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start de simulatielus bij het opstarten en stop hem netjes bij afsluiten."""
    sim.start_background()
    log.info("CSTR Digital Twin gestart - dashboard op http://%s:%s", config.HOST, config.PORT)
    if config.TWIN_API_KEY:
        log.info("POST /api/twin is beveiligd met een API-sleutel (header X-API-Key).")
    yield
    await sim.stop_background()


app = FastAPI(
    title="CSTR Digital Twin",
    description="Dynamische simulatie en digital twin van een continu geroerde tankreactor.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS open zodat externe dashboards/scripts de API vanuit een browser kunnen gebruiken.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# --------------------------------------------------------------------------- #
# Hulpfuncties
# --------------------------------------------------------------------------- #
def _validation_detail(exc: ValidationError) -> list:
    """Pydantic-fouten omzetten naar compacte, leesbare meldingen."""
    return [
        {"field": ".".join(str(x) for x in e["loc"]), "error": e["msg"]}
        for e in exc.errors(include_url=False)
    ]


async def require_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    """Controleer de API-sleutel voor POST /api/twin (alleen als er een is ingesteld)."""
    if config.TWIN_API_KEY and x_api_key != config.TWIN_API_KEY:
        raise HTTPException(status_code=401, detail="Ongeldige of ontbrekende X-API-Key header.")


# --------------------------------------------------------------------------- #
# Algemeen
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health():
    return {"status": "ok", "running": sim.running, "tripped": sim.tripped, "clients": len(sim.clients)}


@app.get("/api/state")
async def get_state(history: bool = False):
    return sim.full_state() if history else sim.snapshot()


@app.post("/api/control/{action}")
async def control(action: str):
    """Start, stop of reset de live-simulatie."""
    if action == "start":
        sim.start()
    elif action == "stop":
        sim.stop()
    elif action == "reset":
        sim.reset()
    else:
        raise HTTPException(status_code=404, detail="Onbekende actie; gebruik start, stop of reset.")
    await sim.notify_full()
    return sim.snapshot()


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #
@app.get("/api/parameters")
async def get_parameters():
    return sim.parameters_dict()


@app.put("/api/parameters")
@app.post("/api/parameters")
async def update_parameters(update: ParametersUpdate):
    """
    Gedeeltelijke update. Voorbeeld:
        {"process": {"UA": 300, "F": 20}, "control": {"T_sp": 345}, "safety": {"T_trip": 580}}
    Wijzigingen werken direct door in de lopende simulatie.
    """
    try:
        sim.update_parameters(update.model_dump())
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await sim.broadcast({"type": "parameters", "parameters": sim.parameters_dict()})
    return sim.parameters_dict()


@app.post("/api/parameters/defaults")
async def restore_defaults():
    sim.restore_defaults()
    await sim.broadcast({"type": "parameters", "parameters": sim.parameters_dict()})
    return sim.parameters_dict()


@app.get("/api/steady-states")
async def steady_states():
    return {"steady_states": sim.steady_states()}


# --------------------------------------------------------------------------- #
# Offline simulatie
# --------------------------------------------------------------------------- #
@app.post("/api/simulate")
async def run_simulation(req: SimulateRequest):
    """
    Draait een offline simulatie (beïnvloedt de live-reactor niet).
    Handig voor "wat-als"-scenario's en voorspellingen vanuit de actuele toestand.
    """
    try:
        params = ProcessParameters(**{**sim.process.model_dump(), **req.parameters})
        control = ControllerSettings(**{**sim.control.model_dump(), **req.control})
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc))

    I0: Optional[float] = None
    tc_override: Optional[float] = None
    if req.from_current_state:
        CA0, T0, t0 = sim.CA, sim.T, sim.t
        tc_override = sim.tc_override()
        if control.auto and not sim.control.auto:
            I0 = controller_bumpless(T0, sim.current_Tc(), control)   # "wat als ik op auto zet?"
        elif not control.auto and sim.control.auto and "Tc" not in req.parameters:
            params = params.model_copy(update={"Tc": sim.current_Tc()})  # hand: Tc blijft staan
        else:
            I0 = sim.I
    elif req.initial is not None:
        CA0, T0, t0 = req.initial.CA0, req.initial.T0, 0.0
    else:
        CA0, T0, t0 = sim.initial.CA0, sim.initial.T0, 0.0

    result = simulate(
        params, CA0, T0, req.duration, dt=req.dt, n_points=req.n_points,
        method=req.method, T_trip=sim.safety.T_trip,
        control=control, I0=I0, tc_override=tc_override,
    )
    result["time"] = [t0 + t for t in result["time"]]  # tijdas laten aansluiten op live-simulatie
    if result["runaway_at"] is not None:
        result["runaway_at"] += t0
    result["parameters"] = params.model_dump()
    result["control"] = control.model_dump()
    result["steady_states"] = find_steady_states(params)
    result["dT_ad"] = adiabatic_temperature_rise(params)
    result["T_trip"] = sim.safety.T_trip
    return result


@app.get("/api/heat-balance")
async def heat_balance():
    """
    Semenov-diagram bij de huidige koelmiddeltemperatuur: warmteproductie
    Q_gen(T) en warmteafvoer Q_rem(T), plus de actuele toestand.
    """
    return sim.heat_balance()


# --------------------------------------------------------------------------- #
# Digital twin
# --------------------------------------------------------------------------- #
@app.get("/api/twin")
async def twin_status(limit: int = 100):
    return {
        "enabled": sim.twin_enabled,
        "blend": sim.twin_blend,
        "stale": sim.twin_stale(),
        "count": sim.measurement_count,
        "last": sim.last_measurement,
        "recent": list(sim.measurements)[-max(0, limit):],
    }


@app.post("/api/twin", dependencies=[Depends(require_api_key)])
async def twin_ingest(payload: Union[TwinMeasurement, List[TwinMeasurement]]):
    """
    Ontvang live-procesdata, als enkel object of als lijst (batch):
        {"CA": 0.5, "T": 350, "Q": -5000}
    Optioneel ook F, T_in, CA_in en timestamp.
    """
    items = payload if isinstance(payload, list) else [payload]
    if not items:
        raise HTTPException(status_code=422, detail="Lege lijst.")
    results = []
    for item in items:
        try:
            results.append(sim.apply_measurement(item.process_values()))
        except ValidationError as exc:  # bv. gemeten F buiten geldig bereik
            raise HTTPException(status_code=422, detail=_validation_detail(exc))
    await sim.broadcast({"type": "measurement", "measurement": sim.last_measurement})
    if sim.twin_enabled:  # ingangen (UA, F, ...) kunnen door de meting veranderd zijn
        await sim.broadcast({"type": "parameters", "parameters": sim.parameters_dict()})
    await sim.broadcast(sim.snapshot())
    return results[0] if len(results) == 1 else {"processed": len(results), "last": results[-1]}


@app.post("/api/twin/mode")
async def twin_mode(req: TwinModeRequest):
    sim.set_twin_mode(req.enabled, req.blend)
    await sim.broadcast(sim.snapshot())
    return {"enabled": sim.twin_enabled, "blend": sim.twin_blend}


# --------------------------------------------------------------------------- #
# Failsafe
# --------------------------------------------------------------------------- #
@app.post("/api/failsafe/reset")
async def failsafe_reset():
    try:
        sim.reset_failsafe()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await sim.broadcast(sim.snapshot())
    return sim.snapshot()


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    Real-time kanaal. Bij verbinden krijgt de client een 'init'-bericht met
    historie + parameters; daarna 'state'-berichten (elke tick),
    'measurement'-berichten (twin-data) en 'parameters'-berichten.
    Berichten van de client worden genegeerd (besturing loopt via REST).
    """
    await ws.accept()
    sim.clients.add(ws)
    try:
        await ws.send_json(sim.full_state())
        while True:
            await ws.receive_text()  # houdt de verbinding open; detecteert disconnect
    except WebSocketDisconnect:
        pass
    finally:
        sim.clients.discard(ws)


# --------------------------------------------------------------------------- #
# Frontend
# --------------------------------------------------------------------------- #
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(config.STATIC_DIR / "index.html")
