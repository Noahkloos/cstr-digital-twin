"""Tests voor het wiskundige model, de regelaar en de integrators (app/model.py)."""

import math

import pytest

from app.model import (
    ControllerSettings,
    ProcessParameters,
    adiabatic_temperature_rise,
    derivatives,
    euler_step,
    find_steady_states,
    heat_balance_curves,
    heat_input,
    integrate,
    rk4_step,
    simulate,
)

P = ProcessParameters()
C = ControllerSettings()


def test_physical_property_values_are_realistic():
    """Cp in het bereik van vloeistoffen (niet de oude 0,239) en een realistische dT_ad."""
    assert 1.5 <= P.Cp <= 4.5
    assert adiabatic_temperature_rise(P) == pytest.approx(250 * 5 / 3.5)


def test_jacket_cooling_scales_with_temperature():
    """Q = UA*(Tc - T): meer afvoer bij hogere T, nul bij T = Tc."""
    assert heat_input(P.Tc, P) == pytest.approx(0.0)
    assert heat_input(400, P) < heat_input(350, P) < 0
    fixed = P.model_copy(update={"cooling_mode": "fixed"})
    assert heat_input(400, fixed) == heat_input(350, fixed) == fixed.Q


def test_no_unphysical_cold_state_with_jacket():
    """Met mantelkoeling kan T nooit onder min(T_in, Tc) zakken (de oude 224 K-toestand bestaat niet meer)."""
    for s in find_steady_states(P):
        assert s["T"] >= min(P.T_in, P.Tc) - 1e-6


def test_operating_point_is_open_loop_unstable():
    """Bij realistische UA is het werkpunt (350 K, ~80 % conversie) open-loop instabiel."""
    ss = find_steady_states(P)
    op = min(ss, key=lambda s: abs(s["T"] - 350))
    assert op["T"] == pytest.approx(350, abs=0.5)
    assert op["conversion"] == pytest.approx(0.80, abs=0.01)
    assert op["stable"] is False


def test_closed_loop_holds_setpoint():
    """De PI-regelaar stabiliseert het instabiele werkpunt na een verstoring van +5 K."""
    r = simulate(P, 1.0, 355, 60, control=C, n_points=61)
    assert r["T"][-1] == pytest.approx(C.T_sp, abs=0.2)
    assert r["T_max"] < 360


def test_open_loop_drifts_away_from_operating_point():
    """In handbediening (Tc vast) loopt de reactor weg van het werkpunt."""
    hand = C.model_copy(update={"auto": False})
    r = simulate(P, 1.0, 350.5, 60, control=hand, n_points=61)
    assert abs(r["T"][-1] - 350) > 20


def test_cooling_failure_gives_accelerating_runaway():
    """
    UA = 0: de temperatuurstijging versnelt (exponentiële fase) zolang er
    opgehoopte reactant is. Controle: de stijging per minuut neemt toe.
    """
    p = P.model_copy(update={"UA": 0.0})
    r = simulate(p, 1.0, 350, 4, control=C, n_points=5)
    rises = [r["T"][i + 1] - r["T"][i] for i in range(3)]
    assert rises[0] < rises[1] < rises[2]
    assert r["T"][3] > 420


def test_accumulation_startup_triggers_runaway_above_600K():
    """Koude opstart met CA0 = 5 mol/L: alle opgehoopte energie komt vrij -> > 600 K."""
    r = simulate(P, 5.0, 300, 20, control=C, n_points=201, T_trip=600)
    assert r["T_max"] > 600
    assert r["runaway_at"] is not None


def test_adiabatic_limit_not_exceeded():
    """Zonder koeling en zonder ophoping nooit boven T_in + dT_ad."""
    p = P.model_copy(update={"UA": 0.0})
    r = simulate(p, 1.0, 350, 200, control=C, n_points=201)
    assert r["error"] is None
    assert r["T_max"] < P.T_in + adiabatic_temperature_rise(P) + 1
    assert r["steps"]["implicit"] > 0  # stijve stappen gebruikt


def test_steady_states_have_zero_derivatives():
    for s in find_steady_states(P):
        dCA, dT = derivatives(s["CA"], s["T"], P)
        assert abs(dCA) < 1e-6
        assert abs(dT) < 1e-4


def test_heat_balance_curves_cross_at_steady_states():
    hb = heat_balance_curves(P)
    diff = [g - r for g, r in zip(hb["Q_gen"], hb["Q_rem"])]
    crossings = sum(1 for a, b in zip(diff, diff[1:]) if a * b < 0)
    assert crossings == len(find_steady_states(P, T_low=hb["T"][0], T_high=hb["T"][-1]))


def test_isothermal_first_order_matches_analytic_solution():
    """Met E = 0, dH = 0, UA = 0 is k constant en is er een exacte oplossing."""
    p = ProcessParameters(k0=0.5, E=0.0, dH=0.0, UA=0.0, T_in=350)
    CA0, t_end = 0.0, 3.0
    tau_inv = p.F / p.V
    CA_ss = tau_inv * p.CA_in / (tau_inv + p.k0)
    exact = CA_ss + (CA0 - CA_ss) * math.exp(-(tau_inv + p.k0) * t_end)
    CA, T, _, _ = integrate(CA0, 350.0, t_end, p, dt=0.01)
    assert CA == pytest.approx(exact, rel=1e-8)
    assert T == pytest.approx(350.0)


def test_rk4_is_fourth_order_and_beats_euler():
    """Halveren van de stap moet de RK4-fout ~16x verkleinen, Euler ~2x."""
    p = ProcessParameters(k0=0.5, E=0.0, dH=0.0, UA=0.0)
    tau_inv = p.F / p.V
    lam = tau_inv + p.k0
    CA_ss = tau_inv * p.CA_in / lam
    exact = CA_ss + (0.0 - CA_ss) * math.exp(-lam * 1.0)

    def run(step, h):
        CA, T = 0.0, 350.0
        for _ in range(int(round(1.0 / h))):
            CA, T = step(CA, T, h, p)
        return abs(CA - exact)

    rk_ratio = run(rk4_step, 0.1) / run(rk4_step, 0.05)
    eu_ratio = run(euler_step, 0.1) / run(euler_step, 0.05)
    assert 12 < rk_ratio < 20
    assert 1.7 < eu_ratio < 2.6
    assert run(rk4_step, 0.05) < run(euler_step, 0.05) / 1000


def test_concentration_never_negative():
    p = ProcessParameters(CA_in=0.0)
    CA, T, _, _ = integrate(0.001, 700.0, 1.0, p, dt=0.005)
    assert CA >= 0.0


def test_parameter_validation_rejects_invalid_values():
    with pytest.raises(ValueError):
        ProcessParameters(V=0)
    with pytest.raises(ValueError):
        ProcessParameters(Cp=-1)
    with pytest.raises(ValueError):
        ProcessParameters(cooling_mode="ijs")
    with pytest.raises(ValueError):
        ProcessParameters(q=-3000)  # onbekend veld (tikfout)
