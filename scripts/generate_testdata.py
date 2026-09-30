"""
Genereert de testdatasets in data/ door een "echte installatie" na te bootsen:
het CSTR-model met eigen temperatuurregelaar, een licht afwijkende kinetiek
(plant-model mismatch: k0 4 % hoger) en meetruis. Zo zie je in de
twin-modus realistische modelafwijkingen.

    python scripts/generate_testdata.py

Resultaat:
    data/testdata_normal.csv           15 min normaal bedrijf, verstoringen in T_in en F
    data/testdata_cooling_failure.csv  20 min, koelwaterpomp valt uit na 2 min (UA -> 0)
    data/testdata_runaway.csv          12 min koude opstart met ophoping -> runaway > 600 K

Kolommen: time_s, CA [mol/L], T [K], Tc [K], Q [kJ/min], F [L/min], T_in [K], CA_in [mol/L]
Eén rij per seconde (1 s = 1/60 min, want de twin rekent in echte tijd).
Q is de gemeten koelwarmtestroom (uit koelwaterdebiet x delta-T), negatief = koelen.
"""

import csv
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.model import (  # noqa: E402
    ControllerSettings,
    ProcessParameters,
    controller_bumpless,
    step_closed_loop,
)

DATA_DIR = ROOT / "data"
DT_ROW_MIN = 1.0 / 60.0   # 1 seconde tussen metingen
DT_SIM = 0.01             # integratiestap / sampletijd regelaar [min]
K0_PLANT = 8.66e10 * 1.04  # de "echte" kinetiek wijkt 4 % af van het model


def generate(name, duration_s, schedule, CA0, T0, seed):
    """
    schedule(t_min) -> dict met procesingangen (F, T_in, CA_in, UA) op tijdstip t.
    Retourneert het pad van het geschreven CSV-bestand.
    """
    rng = random.Random(seed)
    ctrl = ControllerSettings()
    CA, T = CA0, T0
    plant = ProcessParameters(k0=K0_PLANT, **schedule(0.0))
    I = controller_bumpless(T, plant.Tc, ctrl)
    rows = []
    for i in range(duration_s + 1):
        t_min = i * DT_ROW_MIN
        plant = ProcessParameters(k0=K0_PLANT, **schedule(t_min))
        if i > 0:
            remaining = DT_ROW_MIN
            while remaining > 1e-12:
                h = min(DT_SIM, remaining)
                CA, T, I, Tc, Q, _, _ = step_closed_loop(CA, T, I, h, plant, ctrl)
                remaining -= h
        Tc = min(max(I + ctrl.Kc * (ctrl.T_sp - T), ctrl.Tc_min), ctrl.Tc_max)
        Q = plant.UA * (Tc - T)
        rows.append({
            "time_s": i,
            "CA": round(max(0.0, CA * (1 + rng.gauss(0, 0.01))), 5),   # 1 % ruis (analyser)
            "T": round(T + rng.gauss(0, 0.2), 2),                        # 0,2 K ruis
            "Tc": round(Tc + rng.gauss(0, 0.1), 2),
            "Q": round(Q * (1 + rng.gauss(0, 0.01)), 1),                 # 1 % ruis
            "F": round(plant.F, 2),
            "T_in": round(plant.T_in, 2),
            "CA_in": round(plant.CA_in, 4),
        })
    path = DATA_DIR / name
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"{path.name}: {len(rows)} rijen, T {min(r['T'] for r in rows):.1f} .. {max(r['T'] for r in rows):.1f} K")
    return path


def normal_schedule(t):
    """Normaal bedrijf: voeding 5 K warmer na 3 min, debiet +8 % na 9 min."""
    return {"T_in": 300.0 if t < 3 else 305.0, "F": 25.0 if t < 9 else 27.0}


def cooling_failure_schedule(t):
    """Koelwaterpomp valt uit na 2 min: warmteoverdracht UA -> 0."""
    return {"UA": 600.0 if t < 2 else 0.0}


def startup_schedule(t):
    """Normale instellingen; het gevaar zit in de begintoestand (koud + vol reactant)."""
    return {}


if __name__ == "__main__":
    DATA_DIR.mkdir(exist_ok=True)
    generate("testdata_normal.csv", 900, normal_schedule, CA0=1.0, T0=350.0, seed=1)
    generate("testdata_cooling_failure.csv", 1200, cooling_failure_schedule, CA0=1.0, T0=350.0, seed=2)
    generate("testdata_runaway.csv", 720, startup_schedule, CA0=5.0, T0=300.0, seed=3)
