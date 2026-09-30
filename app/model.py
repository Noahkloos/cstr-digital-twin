"""
CSTR-model: materiaalbalans + warmtebalans voor een eerste-orde exotherme reactie A -> B.

    dCA/dt = F/V * (CA_in - CA) - k0 * exp(-E / (R*T)) * CA
    dT/dt  = F/V * (T_in - T) + (-dH)/(rho*Cp) * k0 * exp(-E/(R*T)) * CA + Q/(rho*Cp*V)

Warmtestroom Q naar de reactor (twee koelmodi):
    "jacket" (standaard):  Q = UA * (Tc - T)     koelmantel/-spiraal; afvoer groeit met T
    "fixed":               Q = constante waarde   (vereenvoudiging, geen T-terugkoppeling)

Eenhedenstelsel (consistent, tijd in MINUTEN):
    V      [L]            reactorvolume
    F      [L/min]        volumestroom
    CA_in  [mol/L]        voedingsconcentratie A
    T_in   [K]            voedingstemperatuur
    k0     [1/min]        pre-exponentiële factor
    E      [J/mol]        activeringsenergie
    R      [J/(mol*K)]    gasconstante (E/R moet in K uitkomen)
    dH     [kJ/mol]       reactie-enthalpie (negatief = exotherm)
    rho    [kg/L]         dichtheid
    Cp     [kJ/(kg*K)]    warmtecapaciteit (water ~4,18; organische mengsels ~2-3,5)
    UA     [kJ/(min*K)]   warmteoverdrachtscoëfficiënt x oppervlak (60 kJ/(min*K) = 1 kW/K)
    Tc     [K]            koelmiddeltemperatuur
    Q      [kJ/min]       warmtestroom naar de reactor (negatief = koelen)

Standaardwaarden: een geconcentreerde, sterk exotherme vloeistoffasereactie
in een reactor van 1 m3 (bv. een hydrogenering/oxidatie):
    dT_ad = (-dH)*CA_in/(rho*Cp) = 250*5/3.5 = 357 K   (hoge ernstklasse)
    werkpunt ~350 K, ~80 % conversie, verblijftijd 40 min
Bij koelingsverlies reageert de opgehoopte reactant (~1 mol/L) steeds
sneller op: k verdubbelt per ~9 K -> klassieke exponentiële thermal runaway.

Dit bestand bevat uitsluitend pure functies (geen I/O, geen globale staat),
zodat het model los te testen en te hergebruiken is.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    """Basisklasse: onbekende velden (tikfouten zoals "q" i.p.v. "Q") worden geweigerd."""

    model_config = ConfigDict(extra="forbid")


# Ondergrens voor de temperatuur in de kinetiek. Voorkomt deling door nul of
# een negatieve T in exp(-E/RT) als een gebruiker extreme koeling instelt.
T_MIN_KINETICS = 1.0


# --------------------------------------------------------------------------- #
# Parametergroepen (Pydantic => automatische validatie in de API)
# --------------------------------------------------------------------------- #
class ProcessParameters(_Strict):
    """Fysische en kinetische parameters van de reactor."""

    V: float = Field(1000.0, gt=0, description="Reactorvolume [L]")
    F: float = Field(25.0, ge=0, description="Volumestroom [L/min]")
    CA_in: float = Field(5.0, ge=0, description="Voedingsconcentratie A [mol/L]")
    T_in: float = Field(300.0, gt=0, description="Voedingstemperatuur [K]")
    k0: float = Field(8.66e10, gt=0, description="Pre-exponentiële factor [1/min]")
    E: float = Field(80000.0, ge=0, description="Activeringsenergie [J/mol]")
    dH: float = Field(-250.0, description="Reactie-enthalpie [kJ/mol] (negatief = exotherm)")
    rho: float = Field(1.0, gt=0, description="Dichtheid [kg/L]")
    Cp: float = Field(3.5, gt=0, description="Warmtecapaciteit [kJ/(kg*K)]")
    cooling_mode: Literal["jacket", "fixed"] = Field(
        "jacket", description="'jacket': Q = UA*(Tc - T); 'fixed': Q is constant")
    UA: float = Field(600.0, ge=0, description="Koeling UA [kJ/(min*K)] (60 = 1 kW/K)")
    Tc: float = Field(315.7, gt=0, description="Koelmiddeltemperatuur in handbediening [K]")
    Q: float = Field(-20600.0, description="Vaste warmtestroom [kJ/min] (alleen modus 'fixed'; negatief = koelen)")
    R: float = Field(8.314, gt=0, description="Gasconstante [J/(mol*K)]")


class InitialConditions(_Strict):
    """Begintoestand die bij een reset wordt gebruikt."""

    CA0: float = Field(1.0, ge=0, description="Begin-concentratie A [mol/L]")
    T0: float = Field(350.0, gt=0, description="Begintemperatuur [K]")


class SafetySettings(_Strict):
    """Instellingen van de failsafe tegen runaway-reacties."""

    T_warn: float = Field(400.0, gt=0, description="Waarschuwingsgrens [K]")
    T_trip: float = Field(600.0, gt=0, description="Trip-grens (noodstop) [K]")
    stop_feed_on_trip: bool = Field(True, description="Reactantvoeding (CA_in) naar 0 bij trip")
    UA_emergency: float = Field(1500.0, ge=0, description="Noodkoeling UA [kJ/(min*K)] (bovenop de mantel)")
    T_coolant_emergency: float = Field(290.0, gt=0, description="Noodkoelwatertemperatuur [K]")


class ControllerSettings(_Strict):
    """
    Temperatuurregelaar TIC-101: digitale PI-regelaar die de koelmiddel-
    temperatuur Tc stuurt (alleen bij cooling_mode = 'jacket').

        Tc = I + Kc * (T_sp - T)          begrensd tot [Tc_min, Tc_max]
        dI/dt = Kc / tau_I * (T_sp - T)   met anti-windup bij verzadiging

    Omgekeerd werkend: T boven setpoint -> kouder koelmiddel.
    In 'hand' (auto = False) blijft Tc op de handwaarde ProcessParameters.Tc.
    """

    auto: bool = Field(True, description="Regelaar in automaat (False = hand, Tc vast)")
    T_sp: float = Field(350.0, gt=0, description="Setpoint reactortemperatuur [K]")
    Kc: float = Field(5.0, ge=0, description="Versterking [K koelmiddel per K fout]")
    tau_I: float = Field(5.0, gt=0, description="Integratietijd [min]")
    Tc_min: float = Field(280.0, gt=0, description="Minimale koelmiddeltemperatuur [K]")
    Tc_max: float = Field(360.0, gt=0, description="Maximale koelmiddeltemperatuur [K]")


class RuntimeSettings(_Strict):
    """Instellingen van de live-simulatielus."""

    dt: float = Field(0.01, gt=0, le=0.1, description="Integratiestap RK4 = sampletijd regelaar [min]")
    speed: float = Field(1.0, gt=0, le=60, description="Gesimuleerde minuten per echte seconde")
    tick: float = Field(0.1, ge=0.02, le=2.0, description="Update-interval van de lus [s]")
    twin_realtime: bool = Field(True, description="In twin-modus in echte tijd rekenen (1 s = 1/60 min)")
    twin_stale_after: float = Field(10.0, gt=0, description="Twin-data verouderd na [s]")


# --------------------------------------------------------------------------- #
# Model-vergelijkingen
# --------------------------------------------------------------------------- #
CoolingFn = Callable[[float], float]  # functie T -> Q [kJ/min]


def heat_input(T: float, p: ProcessParameters) -> float:
    """
    Warmtestroom naar de reactor Q [kJ/min] volgens de gekozen koelmodus.
    Mantelkoeling: Q = UA*(Tc - T) -> negatief (koelen) zolang T > Tc.
    """
    if p.cooling_mode == "jacket":
        return p.UA * (p.Tc - T)
    return p.Q


def rate_constant(T: float, p: ProcessParameters) -> float:
    """Arrhenius: k(T) = k0 * exp(-E / (R*T))  [1/min]."""
    T_safe = max(T, T_MIN_KINETICS)
    return p.k0 * math.exp(-p.E / (p.R * T_safe))


def derivatives(
    CA: float,
    T: float,
    p: ProcessParameters,
    cooling: Optional[CoolingFn] = None,
    CA_in: Optional[float] = None,
) -> tuple:
    """
    Rechterleden van de ODE's.

    cooling : optionele functie Q(T); standaard heat_input(T, p) (mantel of vaste Q).
              (De failsafe gebruikt dit om noodkoeling te modelleren.)
    CA_in   : optionele overschrijving van de voeding (failsafe: voeding dicht).
    """
    Q = heat_input(T, p) if cooling is None else cooling(T)
    feed = p.CA_in if CA_in is None else CA_in
    CA_eff = max(CA, 0.0)                    # concentratie kan fysisch niet < 0
    r = rate_constant(T, p) * CA_eff         # reactiesnelheid [mol/(L*min)]
    tau_inv = p.F / p.V                      # 1/verblijftijd [1/min]
    rhoCp = p.rho * p.Cp                     # [kJ/(L*K)]

    dCA = tau_inv * (feed - CA) - r
    dT = tau_inv * (p.T_in - T) + (-p.dH) / rhoCp * r + Q / (rhoCp * p.V)
    return dCA, dT


def rk4_step(
    CA: float,
    T: float,
    dt: float,
    p: ProcessParameters,
    cooling: Optional[CoolingFn] = None,
    CA_in: Optional[float] = None,
) -> tuple:
    """Eén klassieke Runge-Kutta 4e-orde stap met stapgrootte dt [min]."""
    k1 = derivatives(CA, T, p, cooling, CA_in)
    k2 = derivatives(CA + 0.5 * dt * k1[0], T + 0.5 * dt * k1[1], p, cooling, CA_in)
    k3 = derivatives(CA + 0.5 * dt * k2[0], T + 0.5 * dt * k2[1], p, cooling, CA_in)
    k4 = derivatives(CA + dt * k3[0], T + dt * k3[1], p, cooling, CA_in)
    CA_new = CA + dt / 6.0 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
    T_new = T + dt / 6.0 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
    return max(CA_new, 0.0), T_new


def euler_step(
    CA: float,
    T: float,
    dt: float,
    p: ProcessParameters,
    cooling: Optional[CoolingFn] = None,
    CA_in: Optional[float] = None,
) -> tuple:
    """Expliciete Euler-stap (alleen ter vergelijking; RK4 is nauwkeuriger)."""
    dCA, dT = derivatives(CA, T, p, cooling, CA_in)
    return max(CA + dt * dCA, 0.0), T + dt * dT


def _cooling_slope(T: float, p: ProcessParameters, cooling: Optional[CoolingFn]) -> float:
    """dQ/dT [kJ/(min*K)]: -UA bij mantelkoeling, 0 bij vaste Q, anders numeriek."""
    if cooling is None:
        return -p.UA if p.cooling_mode == "jacket" else 0.0
    eps = 1e-3
    return (cooling(T + eps) - cooling(T - eps)) / (2 * eps)


def spectral_radius(CA: float, T: float, p: ProcessParameters, cooling: Optional[CoolingFn] = None) -> float:
    """
    Grootste |eigenwaarde| van de Jacobiaan = maat voor stijfheid [1/min].
    Expliciete RK4 is alleen stabiel als dt * rho <~ 2.78.
    """
    (a11, a12), (a21, a22) = jacobian(CA, T, p, cooling)
    tr, det = a11 + a22, a11 * a22 - a12 * a21
    disc = tr * tr / 4.0 - det
    if disc >= 0:
        s = math.sqrt(disc)
        return max(abs(tr / 2 + s), abs(tr / 2 - s))
    return math.sqrt(max(det, 0.0))  # complexe eigenwaarden: |lambda| = sqrt(det)


def implicit_euler_step(
    CA: float,
    T: float,
    dt: float,
    p: ProcessParameters,
    cooling: Optional[CoolingFn] = None,
    CA_in: Optional[float] = None,
    _depth: int = 0,
) -> tuple:
    """
    Impliciete (backward) Euler-stap, opgelost met Newton-Raphson (2x2).
    L-stabiel: blijft stabiel voor willekeurig stijve systemen, zoals een
    runaway-reactie bij hoge T. Convergeert Newton niet, dan wordt de stap
    gehalveerd (recursief).
    """
    x_ca, x_T = CA, T
    for _ in range(30):
        f_ca, f_T = derivatives(x_ca, x_T, p, cooling, CA_in)
        g1 = x_ca - CA - dt * f_ca          # residu G(x) = x - x_n - dt*f(x)
        g2 = x_T - T - dt * f_T
        (a11, a12), (a21, a22) = jacobian(x_ca, x_T, p, cooling)
        m11, m12 = 1 - dt * a11, -dt * a12  # M = I - dt*J
        m21, m22 = -dt * a21, 1 - dt * a22
        det = m11 * m22 - m12 * m21
        if det == 0 or not math.isfinite(det):
            break
        d_ca = (-g1 * m22 + g2 * m12) / det  # Cramer: M * d = -G
        d_T = (-g2 * m11 + g1 * m21) / det
        x_ca = max(x_ca + d_ca, 0.0)
        x_T = max(x_T + d_T, T_MIN_KINETICS)
        if abs(d_T) < 1e-9 * (1 + abs(x_T)) and abs(d_ca) < 1e-12 + 1e-9 * abs(x_ca):
            return x_ca, x_T
    if _depth >= 12:
        return x_ca, x_T  # beste benadering; komt in de praktijk niet voor
    half = dt / 2
    ca_m, T_m = implicit_euler_step(CA, T, half, p, cooling, CA_in, _depth + 1)
    return implicit_euler_step(ca_m, T_m, half, p, cooling, CA_in, _depth + 1)


# RK4 is stabiel op de reële as tot |dt*lambda| ~ 2.78; we houden marge.
RK4_STABILITY_LIMIT = 2.0


def integrate(
    CA: float,
    T: float,
    duration: float,
    p: ProcessParameters,
    dt: float = 0.005,
    cooling: Optional[CoolingFn] = None,
    CA_in: Optional[float] = None,
    method: str = "rk4",
) -> tuple:
    """
    Integreer de toestand over `duration` minuten met maximale stap `dt`.

    method = "rk4"   : RK4 (standaard). Wordt het systeem stijf
                       (dt * spectraalstraal > 2), dan wordt automatisch een
                       impliciete Euler-stap gebruikt, zodat een runaway
                       nooit tot numerieke explosie leidt.
    method = "euler" : pure expliciete Euler (educatief; kan instabiel worden).

    Retourneert (CA, T, n_rk4, n_implicit).
    """
    remaining = duration
    n_rk4 = n_imp = 0
    while remaining > 1e-12:
        h = min(dt, remaining)
        if method == "euler":
            CA, T = euler_step(CA, T, h, p, cooling, CA_in)
            n_rk4 += 1
        elif h * spectral_radius(CA, T, p, cooling) <= RK4_STABILITY_LIMIT:
            CA, T = rk4_step(CA, T, h, p, cooling, CA_in)
            n_rk4 += 1
        else:
            CA, T = implicit_euler_step(CA, T, h, p, cooling, CA_in)
            n_imp += 1
        remaining -= h
        if not (math.isfinite(CA) and math.isfinite(T)):
            break
    return CA, T, n_rk4, n_imp


# --------------------------------------------------------------------------- #
# Temperatuurregelaar (PI) + gesloten-lus integratie
# --------------------------------------------------------------------------- #
def controller_tc(T: float, I: float, c: ControllerSettings) -> float:
    """Regelaaruitgang: koelmiddeltemperatuur Tc [K], begrensd."""
    u = I + c.Kc * (c.T_sp - T)
    return min(max(u, c.Tc_min), c.Tc_max)


def controller_bumpless(T: float, Tc: float, c: ControllerSettings) -> float:
    """Integraaltoestand zo kiezen dat de uitgang precies Tc is (stootvrij omschakelen)."""
    return Tc - c.Kc * (c.T_sp - T)


def controller_update(T: float, I: float, h: float, c: ControllerSettings) -> float:
    """Integraalactie over één sample h, met anti-windup bij een verzadigde uitgang."""
    e = c.T_sp - T
    u = I + c.Kc * e
    dI = c.Kc / c.tau_I * e * h
    if (u >= c.Tc_max and dI > 0) or (u <= c.Tc_min and dI < 0):
        dI = 0.0  # niet verder oplopen als de klep al volledig open/dicht staat
    return I + dI


def coolant_temperature(
    T: float, I: float, p: ProcessParameters,
    c: Optional[ControllerSettings] = None, tc_override: Optional[float] = None,
) -> float:
    """Actuele koelmiddeltemperatuur: gemeten (twin) > regelaar (auto) > handwaarde."""
    if tc_override is not None:
        return tc_override
    if c is not None and c.auto:
        return controller_tc(T, I, c)
    return p.Tc


def step_closed_loop(
    CA: float,
    T: float,
    I: float,
    h: float,
    p: ProcessParameters,
    c: Optional[ControllerSettings] = None,
    CA_in: Optional[float] = None,
    extra_cooling: Optional[CoolingFn] = None,
    tc_override: Optional[float] = None,
    method: str = "rk4",
) -> tuple:
    """
    Eén regelaar-sample van lengte h [min] (h <= dt):
      1. regelaar bepaalt Tc (vastgehouden gedurende het sample, zoals een DCS),
      2. proces integreren met Q(T) = UA*(Tc - T) (+ eventuele noodkoeling),
      3. integraalactie bijwerken (of stootvrij meelopen in hand/twin).

    extra_cooling : extra warmtestroom Q_extra(T) [kJ/min] (failsafe-noodkoeling).
    Retourneert (CA, T, I, Tc, Q_bij_nieuwe_T, n_rk4, n_implicit).
    """
    Tc = coolant_temperature(T, I, p, c, tc_override)
    if p.cooling_mode == "jacket":
        def base(T_: float) -> float:
            return p.UA * (Tc - T_)
    else:
        def base(T_: float) -> float:
            return p.Q
    cooling = base if extra_cooling is None else (lambda T_: base(T_) + extra_cooling(T_))

    CA, T_new, a, b = integrate(CA, T, h, p, dt=h, cooling=cooling, CA_in=CA_in, method=method)

    if c is not None:
        if c.auto and tc_override is None and p.cooling_mode == "jacket":
            I = controller_update(T_new, I, h, c)
        else:
            I = controller_bumpless(T_new, Tc, c)  # volgen, zodat 'auto' stootvrij inschakelt
    return CA, T_new, I, Tc, cooling(T_new), a, b


def simulate(
    p: ProcessParameters,
    CA0: float,
    T0: float,
    duration: float,
    dt: float = 0.01,
    n_points: int = 500,
    method: str = "rk4",
    T_trip: Optional[float] = None,
    control: Optional[ControllerSettings] = None,
    I0: Optional[float] = None,
    tc_override: Optional[float] = None,
) -> Dict[str, object]:
    """
    Offline (batch) simulatie van t = 0 .. duration [min], inclusief de
    temperatuurregelaar als `control` is opgegeven.

    Geeft tijdreeksen terug met `n_points` gelijk verdeelde punten. Als
    T_trip is opgegeven wordt het eerste uitvoertijdstip gerapporteerd
    waarop T >= T_trip (runaway-detectie). De failsafe grijpt hier NIET in:
    dit is een "wat-als"-analyse die het ongecontroleerde verloop laat zien.
    """
    n_points = max(2, n_points)
    interval = duration / (n_points - 1)
    CA, T = CA0, T0
    if I0 is None:
        I0 = controller_bumpless(T0, p.Tc, control) if control is not None else 0.0
    I = I0
    Tc0 = coolant_temperature(T0, I, p, control, tc_override)
    Q0 = p.UA * (Tc0 - T0) if p.cooling_mode == "jacket" else p.Q

    t_list: List[float] = [0.0]
    ca_list: List[float] = [CA0]
    T_list: List[float] = [T0]
    tc_list: List[float] = [Tc0]
    q_list: List[float] = [Q0]
    runaway_at: Optional[float] = None
    T_max = T0
    n_rk4 = n_imp = 0
    error: Optional[str] = None

    for i in range(1, n_points):
        remaining = interval
        Tc, Q = Tc0, Q0
        while remaining > 1e-12:
            h = min(dt, remaining)
            CA, T, I, Tc, Q, a, b = step_closed_loop(CA, T, I, h, p, control, tc_override=tc_override, method=method)
            n_rk4, n_imp = n_rk4 + a, n_imp + b
            remaining -= h
            if not (math.isfinite(CA) and math.isfinite(T)):
                break
        t = i * interval
        if not (math.isfinite(CA) and math.isfinite(T)):
            error = f"Numerieke instabiliteit bij t = {t:.4f} min; verklein dt of gebruik rk4."
            break
        T_max = max(T_max, T)
        if T_trip is not None and runaway_at is None and T >= T_trip:
            runaway_at = t
        t_list.append(round(t, 10))
        ca_list.append(CA)
        T_list.append(T)
        tc_list.append(Tc)
        q_list.append(Q)

    return {
        "time": t_list, "CA": ca_list, "T": T_list, "Tc": tc_list, "Q": q_list,
        "runaway_at": runaway_at, "T_max": T_max, "error": error,
        "steps": {"rk4": n_rk4, "implicit": n_imp},
    }


def adiabatic_temperature_rise(p: ProcessParameters) -> float:
    """dT_ad = (-dH) * CA_in / (rho * Cp) [K]: maat voor de ernst van een runaway."""
    return (-p.dH) * p.CA_in / (p.rho * p.Cp)


def heat_balance_curves(
    p: ProcessParameters, T_low: float = 280.0, T_high: float = 700.0, n: int = 211
) -> Dict[str, List[float]]:
    """
    Semenov/Van Heerden-diagram (stationair, per temperatuur):
        Q_gen(T) = (-dH) * V * k(T) * CA_ss(T)                warmteproductie [kJ/min]
        Q_rem(T) = F*rho*Cp*(T - T_in) + UA*(T - Tc)            warmteafvoer    [kJ/min]
    (bij vaste Q:  Q_rem = F*rho*Cp*(T - T_in) - Q)
    Snijpunten = stationaire toestanden. Is de helling van Q_gen groter dan
    die van Q_rem, dan versterkt een temperatuurstijging zichzelf: runaway.
    """
    tau_inv = p.F / p.V
    Ts, gen, rem = [], [], []
    for i in range(n):
        T = T_low + (T_high - T_low) * i / (n - 1)
        k = rate_constant(T, p)
        CA_ss = tau_inv * p.CA_in / (tau_inv + k) if (tau_inv + k) > 0 else 0.0
        Ts.append(T)
        gen.append((-p.dH) * p.V * k * CA_ss)
        rem.append(p.F * p.rho * p.Cp * (T - p.T_in) - heat_input(T, p))
    return {"T": Ts, "Q_gen": gen, "Q_rem": rem}


# --------------------------------------------------------------------------- #
# Stationaire toestanden + stabiliteit (handig voor de operator)
# --------------------------------------------------------------------------- #
def steady_state_residual(T: float, p: ProcessParameters) -> float:
    """
    dT/dt in stationaire toestand, na eliminatie van CA via de massabalans:
        CA_ss(T) = (F/V) * CA_in / (F/V + k(T))
    Nulpunten van deze functie zijn de stationaire temperaturen.
    """
    tau_inv = p.F / p.V
    k = rate_constant(T, p)
    CA_ss = tau_inv * p.CA_in / (tau_inv + k) if (tau_inv + k) > 0 else 0.0
    return derivatives(CA_ss, T, p)[1]


def jacobian(
    CA: float, T: float, p: ProcessParameters, cooling: Optional[CoolingFn] = None
) -> List[List[float]]:
    """Analytische Jacobiaan van (dCA/dt, dT/dt) naar (CA, T)."""
    tau_inv = p.F / p.V
    T_safe = max(T, T_MIN_KINETICS)
    k = rate_constant(T_safe, p)
    dk_dT = k * p.E / (p.R * T_safe * T_safe)
    CA_eff = max(CA, 0.0)
    J = (-p.dH) / (p.rho * p.Cp)
    dQ_dT = _cooling_slope(T, p, cooling) / (p.rho * p.Cp * p.V)
    return [
        [-tau_inv - k, -dk_dT * CA_eff],
        [J * k, -tau_inv + J * dk_dT * CA_eff + dQ_dT],
    ]


def find_steady_states(
    p: ProcessParameters, T_low: float = 100.0, T_high: float = 1200.0, n_grid: int = 2000
) -> List[Dict[str, object]]:
    """
    Zoek alle stationaire toestanden in [T_low, T_high] via een raster +
    bisectie, en classificeer de stabiliteit via spoor/determinant van de
    Jacobiaan (stabiel <=> spoor < 0 en det > 0).
    """
    results: List[Dict[str, object]] = []
    grid = [T_low + (T_high - T_low) * i / n_grid for i in range(n_grid + 1)]
    f_prev = steady_state_residual(grid[0], p)
    for i in range(1, len(grid)):
        f_cur = steady_state_residual(grid[i], p)
        if f_prev == 0 or f_prev * f_cur < 0:
            a, b, fa = grid[i - 1], grid[i], f_prev
            for _ in range(60):  # bisectie
                m = 0.5 * (a + b)
                fm = steady_state_residual(m, p)
                if fa * fm <= 0:
                    b = m
                else:
                    a, fa = m, fm
            T_ss = 0.5 * (a + b)
            tau_inv = p.F / p.V
            k = rate_constant(T_ss, p)
            CA_ss = tau_inv * p.CA_in / (tau_inv + k) if (tau_inv + k) > 0 else 0.0
            (a11, a12), (a21, a22) = jacobian(CA_ss, T_ss, p)
            trace, det = a11 + a22, a11 * a22 - a12 * a21
            results.append({
                "T": T_ss,
                "CA": CA_ss,
                "conversion": (1 - CA_ss / p.CA_in) if p.CA_in > 0 else 0.0,
                "stable": trace < 0 and det > 0,
            })
        f_prev = f_cur
    return results
