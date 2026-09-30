"""Tests voor de live-simulator: regelaar, failsafe en digital-twin-synchronisatie."""

import pytest

from app.simulator import LiveSimulator


@pytest.fixture
def sim():
    return LiveSimulator()


def test_starts_in_operating_point_and_holds_it(sim):
    sim.advance(30.0)
    assert sim.t == pytest.approx(30.0)
    assert sim.T == pytest.approx(350.0, abs=0.2)
    assert sim.Tc == pytest.approx(315.7, abs=0.5)
    assert sim.effective_Q() < 0  # koelen


def test_cooling_failure_trips_failsafe_and_emergency_cools(sim):
    sim.update_parameters({"process": {"UA": 0.0}})
    sim.advance(100.0)
    assert sim.tripped
    assert "Runaway" in sim.trip_reason
    assert sim.effective_CA_in() == 0.0            # voeding dicht
    assert sim.emergency_cooling(600.0) < -100000   # noodkoeling actief
    assert sim.T < sim.safety.T_warn                # daarna afgekoeld
    assert any(e["level"] == "trip" for e in sim.events)


def test_accumulation_startup_trips(sim):
    sim.update_parameters({"initial": {"CA0": 5.0, "T0": 300.0}})
    sim.reset()
    sim.advance(15.0)
    assert sim.tripped


def test_failsafe_reset_refused_while_hot_and_allowed_when_cool(sim):
    sim.apply_measurement({"T": 610.0})
    assert sim.tripped
    sim.T = 450.0  # nog boven T_warn (400 K)
    with pytest.raises(ValueError):
        sim.reset_failsafe()
    sim.advance(10.0)  # noodkoeling koelt af
    assert sim.T < sim.safety.T_warn
    sim.reset_failsafe()
    assert not sim.tripped


def test_switch_to_manual_is_bumpless(sim):
    sim.advance(5.0)
    Tc_before = sim.current_Tc()
    sim.update_parameters({"control": {"auto": False}})
    assert sim.process.Tc == pytest.approx(Tc_before, abs=0.01)
    assert sim.current_Tc() == pytest.approx(Tc_before, abs=0.01)


def test_switch_to_auto_is_bumpless(sim):
    sim.update_parameters({"control": {"auto": False}, "process": {"Tc": 320.0}})
    sim.advance(1.0)
    sim.update_parameters({"control": {"auto": True}})
    assert sim.current_Tc() == pytest.approx(320.0, abs=0.01)


def test_measurement_ignored_when_twin_disabled(sim):
    T_before = sim.T
    r = sim.apply_measurement({"CA": 0.5, "T": 360.0, "Q": -5000.0})
    assert r["applied"] is False
    assert sim.T == T_before
    assert r["residual"]["T"] == pytest.approx(360.0 - T_before)


def test_twin_hard_sync_and_q_infers_coolant_temperature(sim):
    sim.set_twin_mode(True, blend=1.0)
    r = sim.apply_measurement({"CA": 0.5, "T": 350.0, "Q": -6000.0})
    assert r["applied"] is True
    assert sim.CA == 0.5 and sim.T == 350.0
    # Tc = T + Q/UA = 350 - 6000/600 = 340 K
    assert r["derived"]["Tc"] == pytest.approx(340.0)
    assert sim.current_Tc() == pytest.approx(340.0)
    assert sim.effective_Q() == pytest.approx(-6000.0)
    assert sim.running


def test_twin_estimates_ua_from_tc_and_q(sim):
    sim.set_twin_mode(True)
    r = sim.apply_measurement({"T": 350.0, "Tc": 330.0, "Q": -4000.0})
    assert r["derived"]["UA"] == pytest.approx(200.0)
    assert sim.process.UA == pytest.approx(200.0)


def test_twin_fixed_mode_takes_q(sim):
    sim.update_parameters({"process": {"cooling_mode": "fixed"}})
    sim.set_twin_mode(True)
    sim.apply_measurement({"T": 350.0, "Q": -5000.0})
    assert sim.process.Q == -5000.0


def test_twin_soft_sync_blend(sim):
    sim.set_twin_mode(True, blend=0.5)
    sim.T = 400.0
    sim.apply_measurement({"T": 420.0})
    assert sim.T == pytest.approx(410.0)


def test_disabling_twin_returns_control_to_own_controller(sim):
    sim.set_twin_mode(True)
    sim.apply_measurement({"T": 350.0, "Tc": 330.0})
    assert sim.current_Tc() == 330.0
    sim.set_twin_mode(False)
    assert sim.tc_measured is None
    assert sim.current_Tc() == pytest.approx(330.0, abs=0.01)  # stootvrij


def test_parameter_update_validation(sim):
    with pytest.raises(ValueError):
        sim.update_parameters({"safety": {"T_trip": 350, "T_warn": 400}})
    with pytest.raises(ValueError):
        sim.update_parameters({"control": {"Tc_min": 350, "Tc_max": 300}})
    assert sim.safety.T_trip == 600.0  # ongewijzigd


def test_twin_realtime_speed(sim):
    assert sim.sim_speed() == sim.runtime.speed
    sim.set_twin_mode(True)
    assert sim.sim_speed() == pytest.approx(1 / 60)
