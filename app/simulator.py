"""
LiveSimulator: de "draaiende" CSTR die de web-app aanstuurt.

Verantwoordelijkheden
---------------------
* Houdt de actuele toestand (t, CA, T, regelaar-integraal I) en alle
  parameters bij.
* Integreert proces + temperatuurregelaar TIC-101 in een asynchrone lus
  (asyncio) en stuurt elke tick de toestand via WebSockets naar alle browsers.
* Digital-twin-modus: externe metingen (via POST /api/twin) synchroniseren
  de modeltoestand en werken de ingangen bij (Tc, UA, Q, F, T_in, CA_in).
* Failsafe: detecteert runaway (T >= T_trip, standaard 600 K), vergrendelt
  een TRIP-status en past een noodscenario toe (noodkoeling + voeding dicht).

Alle methodes draaien op de asyncio-eventloop van de webserver. Omdat er
tussen lezen en schrijven van de toestand geen `await` zit, zijn er geen
locks nodig (één thread, coöperatieve multitasking).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set

from .model import (
    ControllerSettings,
    InitialConditions,
    ProcessParameters,
    RuntimeSettings,
    SafetySettings,
    adiabatic_temperature_rise,
    controller_bumpless,
    coolant_temperature,
    find_steady_states,
    heat_balance_curves,
    rate_constant,
    step_closed_loop,
)

log = logging.getLogger("cstr.simulator")

HISTORY_LENGTH = 3000        # aantal punten dat de server bewaart voor nieuwe clients
MEASUREMENT_HISTORY = 1000   # aantal twin-metingen dat bewaard wordt
MAX_SUBSTEPS_PER_TICK = 20000
TC_PLAUSIBLE = (200.0, 800.0)  # grenzen voor een uit Q afgeleide koelmiddeltemperatuur [K]


class LiveSimulator:
    """Eén gedeelde reactorsimulatie voor alle gebruikers van de app."""

    def __init__(self) -> None:
        # --- parametergroepen ---
        self.process = ProcessParameters()
        self.initial = InitialConditions()
        self.safety = SafetySettings()
        self.control = ControllerSettings()
        self.runtime = RuntimeSettings()

        # --- simulatiestatus ---
        self.running: bool = False
        self.t: float = 0.0
        self.CA: float = self.initial.CA0
        self.T: float = self.initial.T0
        self.I: float = controller_bumpless(self.T, self.process.Tc, self.control)  # PI-integraal [K]
        self.Tc: float = self.process.Tc   # koelmiddeltemperatuur van het laatste sample [K]

        # --- failsafe ---
        self.tripped: bool = False
        self.trip_reason: Optional[str] = None
        self.trip_time: Optional[float] = None
        self.events: Deque[Dict[str, Any]] = deque(maxlen=200)  # alarm-/gebeurtenislog

        # --- digital twin ---
        self.twin_enabled: bool = False
        self.twin_blend: float = 1.0                 # 1 = harde sync, <1 = geleidelijk bijsturen
        self.tc_measured: Optional[float] = None     # gemeten/afgeleide Tc (overschrijft de regelaar)
        self.last_measurement: Optional[Dict[str, Any]] = None
        self.last_measurement_wall: Optional[float] = None
        self.last_residual: Dict[str, float] = {}
        self.measurement_count: int = 0
        self.measurements: Deque[Dict[str, Any]] = deque(maxlen=MEASUREMENT_HISTORY)

        # --- uitvoer ---
        self.history: Deque[Dict[str, Any]] = deque(maxlen=HISTORY_LENGTH)
        self.clients: Set[Any] = set()               # verbonden WebSockets
        self.lagging: bool = False                   # rekent trager dan real-time?
        self._task: Optional[asyncio.Task] = None
        self._record_point()

    # ------------------------------------------------------------------ #
    # Afgeleide grootheden
    # ------------------------------------------------------------------ #
    def effective_CA_in(self) -> float:
        """Voedingsconcentratie die het model werkelijk gebruikt (0 na trip)."""
        if self.tripped and self.safety.stop_feed_on_trip:
            return 0.0
        return self.process.CA_in

    def tc_override(self) -> Optional[float]:
        """In twin-modus volgt het model de gemeten koelmiddeltemperatuur."""
        return self.tc_measured if self.twin_enabled else None

    def current_Tc(self) -> float:
        """Koelmiddeltemperatuur die nu op de mantel staat [K]."""
        return coolant_temperature(self.T, self.I, self.process, self.control, self.tc_override())

    def emergency_cooling(self, T: float) -> float:
        """Noodkoeling na een trip: Q_nood = -UA_em * (T - T_noodkoelwater) [kJ/min]."""
        return -self.safety.UA_emergency * (T - self.safety.T_coolant_emergency)

    def effective_Q(self, T: Optional[float] = None) -> float:
        """
        Totale warmtestroom naar de reactor [kJ/min] bij temperatuur T:
          mantel:   UA * (Tc - T)     (Tc van regelaar, handwaarde of meting)
          vast:     Q
          + noodkoeling als de failsafe getript is.
        """
        T = self.T if T is None else T
        if self.process.cooling_mode == "jacket":
            Q = self.process.UA * (self.current_Tc() - T)
        else:
            Q = self.process.Q
        if self.tripped:
            Q += self.emergency_cooling(T)
        return Q

    def alarm_level(self) -> str:
        """'trip', 'warning' of 'normal' voor de UI."""
        if self.tripped:
            return "trip"
        if self.T >= self.safety.T_warn:
            return "warning"
        return "normal"

    def twin_stale(self) -> bool:
        """True als de twin aan staat maar er te lang geen metingen kwamen."""
        if not self.twin_enabled:
            return False
        if self.last_measurement_wall is None:
            return True
        return (time.time() - self.last_measurement_wall) > self.runtime.twin_stale_after

    def sim_speed(self) -> float:
        """Gesimuleerde minuten per echte seconde."""
        if self.twin_enabled and self.runtime.twin_realtime:
            return 1.0 / 60.0  # de echte installatie loopt in echte tijd
        return self.runtime.speed

    def controller_saturated(self) -> bool:
        """Staat de regelaaruitgang tegen een grens (koeling maximaal/minimaal)?"""
        if not self.control.auto or self.process.cooling_mode != "jacket":
            return False
        Tc = self.current_Tc()
        return Tc <= self.control.Tc_min + 1e-6 or Tc >= self.control.Tc_max - 1e-6

    # ------------------------------------------------------------------ #
    # Besturing
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self.running = True
        self._log_event("info", "Simulatie gestart")

    def stop(self) -> None:
        self.running = False
        self._log_event("info", "Simulatie gestopt")

    def reset(self) -> None:
        """Terug naar de beginvoorwaarden; wist historie en trip-status."""
        self.t = 0.0
        self.CA = self.initial.CA0
        self.T = self.initial.T0
        self.I = controller_bumpless(self.T, self.process.Tc, self.control)
        self.Tc = self.current_Tc()
        self.tripped = False
        self.trip_reason = None
        self.trip_time = None
        self.history.clear()
        self.measurements.clear()
        self.last_residual = {}
        self._record_point()
        self._log_event("info", f"Reset naar CA0 = {self.CA:.3f} mol/L, T0 = {self.T:.1f} K")

    def reset_failsafe(self) -> None:
        """
        Operator-bevestiging van een trip. Alleen toegestaan als de
        temperatuur weer onder de waarschuwingsgrens ligt.
        """
        if not self.tripped:
            return
        if self.T >= self.safety.T_warn:
            raise ValueError(
                f"Reset geweigerd: T = {self.T:.1f} K ligt nog boven de "
                f"waarschuwingsgrens ({self.safety.T_warn:.0f} K)."
            )
        self.tripped = False
        self.trip_reason = None
        self.trip_time = None
        self._log_event("info", "Failsafe gereset door operator")

    def set_twin_mode(self, enabled: bool, blend: Optional[float] = None) -> None:
        if blend is not None:
            self.twin_blend = blend
        if enabled and not self.twin_enabled:
            self.running = True  # een twin moet meedraaien met de installatie
            self._log_event("info", f"Digital Twin-modus AAN (blend = {self.twin_blend:.2f})")
        elif not enabled and self.twin_enabled:
            self.tc_measured = None  # eigen regelaar neemt het weer over
            self.I = controller_bumpless(self.T, self.Tc, self.control)
            self._log_event("info", "Digital Twin-modus UIT")
        self.twin_enabled = enabled

    # ------------------------------------------------------------------ #
    # Parameters
    # ------------------------------------------------------------------ #
    def parameters_dict(self) -> Dict[str, Any]:
        return {
            "process": self.process.model_dump(),
            "initial": self.initial.model_dump(),
            "safety": self.safety.model_dump(),
            "control": self.control.model_dump(),
            "runtime": self.runtime.model_dump(),
        }

    def update_parameters(self, update: Dict[str, Dict[str, Any]]) -> None:
        """
        Gedeeltelijke update, bv. {"process": {"UA": 300}, "control": {"auto": False}}.
        Elke groep wordt volledig opnieuw gevalideerd (Pydantic), zodat
        ongeldige combinaties worden geweigerd voordat er iets verandert.
        """
        proc_upd = dict(update.get("process", {}))
        ctrl_upd = dict(update.get("control", {}))
        # Stootvrij naar hand: handwaarde Tc = huidige regelaaruitgang (tenzij expliciet opgegeven)
        if self.control.auto and ctrl_upd.get("auto") is False and "Tc" not in proc_upd:
            proc_upd["Tc"] = round(self.current_Tc(), 2)

        new_process = ProcessParameters(**{**self.process.model_dump(), **proc_upd})
        new_initial = InitialConditions(**{**self.initial.model_dump(), **update.get("initial", {})})
        new_safety = SafetySettings(**{**self.safety.model_dump(), **update.get("safety", {})})
        new_control = ControllerSettings(**{**self.control.model_dump(), **ctrl_upd})
        new_runtime = RuntimeSettings(**{**self.runtime.model_dump(), **update.get("runtime", {})})
        if new_safety.T_trip <= new_safety.T_warn:
            raise ValueError("T_trip moet groter zijn dan T_warn.")
        if new_control.Tc_max <= new_control.Tc_min:
            raise ValueError("Tc_max moet groter zijn dan Tc_min.")

        was_auto = self.control.auto
        changes = [f"{grp}.{k}={v}" for grp, vals in update.items() for k, v in vals.items()]
        self.process, self.initial, self.safety = new_process, new_initial, new_safety
        self.control, self.runtime = new_control, new_runtime
        if not was_auto and new_control.auto:
            # Stootvrij naar automaat: regelaar start vanaf de huidige Tc
            self.I = controller_bumpless(self.T, self.Tc, new_control)
        if changes:
            self._log_event("info", "Parameters gewijzigd: " + ", ".join(changes))

    def restore_defaults(self) -> None:
        self.process = ProcessParameters()
        self.initial = InitialConditions()
        self.safety = SafetySettings()
        self.control = ControllerSettings()
        self.runtime = RuntimeSettings()
        self.I = controller_bumpless(self.T, self.process.Tc, self.control)
        self._log_event("info", "Standaardparameters hersteld")

    def open_loop_parameters(self) -> ProcessParameters:
        """Procesparameters met de huidige Tc als vaste waarde (open-loop analyse)."""
        return self.process.model_copy(update={"Tc": self.current_Tc()})

    def steady_states(self) -> List[Dict[str, Any]]:
        """Open-loop evenwichten bij de huidige koelmiddeltemperatuur."""
        return find_steady_states(self.open_loop_parameters())

    def heat_balance(self) -> Dict[str, Any]:
        p = self.open_loop_parameters()
        return {
            **heat_balance_curves(p),
            "steady_states": find_steady_states(p),
            "Tc": p.Tc,
            "dT_ad": adiabatic_temperature_rise(p),
            "current": {"T": self.T, "Q_gen": self.q_generation(), "Q_rem": self.q_removal()},
        }

    def q_generation(self) -> float:
        """Actuele warmteproductie door reactie [kJ/min]."""
        return (-self.process.dH) * self.process.V * rate_constant(self.T, self.process) * max(self.CA, 0.0)

    def q_removal(self) -> float:
        """Actuele warmteafvoer (doorstroming + koeling) [kJ/min]."""
        p = self.process
        return p.F * p.rho * p.Cp * (self.T - p.T_in) - self.effective_Q()

    # ------------------------------------------------------------------ #
    # Integratie + failsafe
    # ------------------------------------------------------------------ #
    def advance(self, sim_minutes: float) -> None:
        """
        Integreer `sim_minutes` vooruit in samples van max. dt (= sampletijd
        van de regelaar). De failsafe wordt na elk sample gecontroleerd,
        zodat een trip direct ingrijpt (en niet pas aan het einde van een tick).
        """
        if sim_minutes <= 0:
            return
        dt = self.runtime.dt
        n_sub = min(MAX_SUBSTEPS_PER_TICK, max(1, math.ceil(sim_minutes / dt)))
        h = sim_minutes / n_sub
        for _ in range(n_sub):
            prev = (self.CA, self.T, self.I)
            CA, T, I, Tc, _, _, _ = step_closed_loop(
                self.CA, self.T, self.I, h, self.process, self.control,
                CA_in=self.effective_CA_in(),
                extra_cooling=self.emergency_cooling if self.tripped else None,
                tc_override=self.tc_override(),
            )
            if not (math.isfinite(CA) and math.isfinite(T)):
                # Mag met de stijve integrator niet gebeuren, maar vang het af.
                self.CA, self.T, self.I = prev
                self._trip("Numerieke instabiliteit in het model")
                self.running = False
                return
            self.CA, self.T, self.I, self.Tc = CA, T, I, Tc
            self.t += h
            self._check_safety(self.T, "model")

    def _check_safety(self, T: float, source: str) -> None:
        """Runaway-detectie: trip bij T >= T_trip (standaard 600 K)."""
        if not self.tripped and T >= self.safety.T_trip:
            self._trip(f"Runaway: T = {T:.1f} K >= {self.safety.T_trip:.0f} K ({source})")

    def _trip(self, reason: str) -> None:
        self.tripped = True
        self.trip_reason = reason
        self.trip_time = self.t
        actions = ["noodkoeling actief"]
        if self.safety.stop_feed_on_trip:
            actions.append("reactantvoeding gestopt (CA_in = 0)")
        self._log_event("trip", f"FAILSAFE TRIP - {reason}; " + ", ".join(actions))
        log.warning("FAILSAFE TRIP: %s", reason)

    # ------------------------------------------------------------------ #
    # Digital twin
    # ------------------------------------------------------------------ #
    def apply_measurement(self, m: Dict[str, Any]) -> Dict[str, Any]:
        """
        Verwerk één live-meting, bv. {"CA": 0.5, "T": 350, "Q": -5000}.

        1. Residu berekenen: meting - modelvoorspelling (maat voor modelfout).
        2. Als twin-modus aan staat:
             toestand <- toestand + blend * (meting - toestand)
             ingangen (F, T_in, CA_in, UA) <- gemeten waarden
             koeling (mantelmodus):
               Tc gemeten + Q gemeten -> UA online geschat: UA = Q / (Tc - T)
               alleen Q gemeten       -> Tc afgeleid:      Tc = T + Q / UA
               alleen Tc gemeten      -> Tc overgenomen
             koeling (vaste-Q-modus):  Q <- gemeten Q
        3. Failsafe controleren op de GEMETEN temperatuur (altijd, ook als
           twin-modus uit staat - een gemeten runaway is nooit te negeren).
        """
        now = time.time()
        self.last_measurement_wall = now
        self.measurement_count += 1
        m = {k: v for k, v in m.items() if v is not None}

        residual: Dict[str, float] = {}
        if "CA" in m:
            residual["CA"] = m["CA"] - self.CA
        if "T" in m:
            residual["T"] = m["T"] - self.T
        if "Q" in m:
            residual["Q"] = m["Q"] - self.effective_Q()
        self.last_residual = residual

        applied = False
        derived: Dict[str, float] = {}
        if self.twin_enabled:
            a = self.twin_blend
            if "CA" in m:
                self.CA = max(0.0, self.CA + a * (m["CA"] - self.CA))
            if "T" in m:
                self.T = self.T + a * (m["T"] - self.T)
            inputs = {k: m[k] for k in ("F", "T_in", "CA_in", "UA") if k in m}

            if self.process.cooling_mode == "jacket":
                T_ref = m.get("T", self.T)
                if "Tc" in m:
                    self.tc_measured = m["Tc"]
                    if "Q" in m and "UA" not in m and abs(m["Tc"] - T_ref) > 1.0:
                        # Warmteoverdracht online identificeren (vervuiling, pompuitval)
                        inputs["UA"] = max(0.0, m["Q"] / (m["Tc"] - T_ref))
                        derived["UA"] = inputs["UA"]
                elif "Q" in m:
                    UA = inputs.get("UA", self.process.UA)
                    if UA > 0:
                        tc = T_ref + m["Q"] / UA
                        self.tc_measured = min(max(tc, TC_PLAUSIBLE[0]), TC_PLAUSIBLE[1])
                        derived["Tc"] = self.tc_measured
            elif "Q" in m:
                inputs["Q"] = m["Q"]

            if inputs:
                self.process = ProcessParameters(**{**self.process.model_dump(), **inputs})
            self.Tc = self.current_Tc()
            applied = True

        record = {"time": self.t, "wall": now, "applied": applied, **m, "residual": residual, "derived": derived}
        self.last_measurement = record
        if applied:
            self.measurements.append(record)
            self.history.append(self._point())

        if "T" in m:
            self._check_safety(m["T"], "meting")

        return {
            "applied": applied,
            "twin_mode": self.twin_enabled,
            "sim_time": self.t,
            "state": {"CA": self.CA, "T": self.T, "Tc": self.current_Tc()},
            "residual": residual,
            "derived": derived,
            "failsafe": {
                "tripped": self.tripped,
                "reason": self.trip_reason,
                "alarm": self.alarm_level(),
                "recommended_Q": self.effective_Q() if self.tripped else None,
            },
            "message": None if applied else "Digital Twin-modus staat uit: meting geregistreerd maar niet toegepast.",
        }

    # ------------------------------------------------------------------ #
    # Snapshot / broadcasting
    # ------------------------------------------------------------------ #
    def _point(self) -> Dict[str, Any]:
        return {"time": round(self.t, 6), "CA": self.CA, "T": self.T,
                "Tc": self.current_Tc(), "Q": self.effective_Q()}

    def _record_point(self) -> None:
        self.history.append(self._point())

    def _log_event(self, level: str, message: str) -> None:
        self.events.append({"wall": time.time(), "time": round(self.t, 4), "level": level, "message": message})

    def snapshot(self) -> Dict[str, Any]:
        """Volledige status (wordt elke tick via WebSocket verstuurd)."""
        return {
            "type": "state",
            "running": self.running,
            "time": self.t,  # sim-tijd [min]; bewust geen "t" naast "T" (PowerShell is hoofdletterongevoelig)
            "CA": self.CA,
            "T": self.T,
            "Tc": self.current_Tc(),
            "Q_effective": self.effective_Q(),
            "Q_gen": self.q_generation(),
            "CA_in_effective": self.effective_CA_in(),
            "conversion": (1 - self.CA / self.process.CA_in) if self.process.CA_in > 0 else None,
            "alarm": self.alarm_level(),
            "tripped": self.tripped,
            "trip_reason": self.trip_reason,
            "controller": {
                "auto": self.control.auto,
                "T_sp": self.control.T_sp,
                "saturated": self.controller_saturated(),
                "overridden": self.tc_override() is not None,
            },
            "sim_speed": self.sim_speed(),
            "lagging": self.lagging,
            "twin": {
                "enabled": self.twin_enabled,
                "blend": self.twin_blend,
                "stale": self.twin_stale(),
                "count": self.measurement_count,
                "last": self.last_measurement,
                "residual": self.last_residual,
                "seconds_since_last": (time.time() - self.last_measurement_wall)
                if self.last_measurement_wall else None,
            },
            "events": list(self.events)[-8:],
        }

    def full_state(self) -> Dict[str, Any]:
        """Snapshot + historie + parameters (voor nieuwe clients)."""
        return {
            **self.snapshot(),
            "type": "init",
            "history": list(self.history),
            "measurements": [
                {"time": m["time"], "CA": m.get("CA"), "T": m.get("T")} for m in self.measurements
            ],
            "parameters": self.parameters_dict(),
        }

    async def broadcast(self, message: Dict[str, Any]) -> None:
        if not self.clients:
            return
        text = json.dumps(message)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:  # verbinding verbroken
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def notify_full(self) -> None:
        """Stuur de volledige status (na reset/parameterwijziging)."""
        await self.broadcast(self.full_state())

    # ------------------------------------------------------------------ #
    # Hoofdlus
    # ------------------------------------------------------------------ #
    async def run_forever(self) -> None:
        """
        Real-time lus: elke `tick` seconden `speed * tick` minuten simuleren
        en de toestand broadcasten. In stilstand wordt 1x per seconde een
        heartbeat gestuurd (voor alarmen en de twin-watchdog).
        """
        loop = asyncio.get_running_loop()
        last_idle_broadcast = 0.0
        while True:
            tick = self.runtime.tick
            started = loop.time()
            try:
                if self.running:
                    self.advance(self.sim_speed() * tick)
                    self._record_point()
                    await self.broadcast(self.snapshot())
                elif started - last_idle_broadcast >= 1.0:
                    last_idle_broadcast = started
                    await self.broadcast(self.snapshot())
            except Exception:  # de lus mag nooit stoppen
                log.exception("Fout in simulatielus")
            elapsed = loop.time() - started
            self.lagging = elapsed > tick
            await asyncio.sleep(max(0.0, tick - elapsed))

    def start_background(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run_forever())

    async def stop_background(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
