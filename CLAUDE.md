# CLAUDE.md — CSTR Digital Twin

Python 3.9+ FastAPI-backend + Vue 3/Chart.js-frontend zonder build-stap. Taal van UI, comments en docs: **Nederlands**.

## Commando's (Windows, vanuit deze map)
- Installeren: `python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt`
- Starten: `.\.venv\Scripts\python.exe run.py` → http://127.0.0.1:8000 (API-docs: /docs)
- Tests: `.\.venv\Scripts\python.exe -m pytest` (moeten altijd groen zijn na een wijziging)
- Testdata opnieuw: `.\.venv\Scripts\python.exe scripts\generate_testdata.py`
- Twin testen: `.\.venv\Scripts\python.exe scripts\twin_feeder.py data\testdata_runaway.csv --enable-twin --interval 0.1`
- Preview in Claude Code: launch-configuratie `cstr-twin` (`.claude/launch.json` in de bovenliggende map)

## Architectuur
- `app/model.py` — pure functies: `derivatives`, `heat_input` (mantel `UA*(Tc-T)` of vaste Q), `rk4_step`, `implicit_euler_step`, `integrate` (RK4, schakelt naar impliciete Euler als `dt·spectraalstraal > 2`), PI-regelaar (`controller_tc`, `controller_update`, `controller_bumpless`), `step_closed_loop` (één regelaar-sample), `simulate`, `find_steady_states`, `heat_balance_curves`. Pydantic-groepen `ProcessParameters`, `ControllerSettings`, `InitialConditions`, `SafetySettings`, `RuntimeSettings` (extra velden verboden).
- `app/simulator.py` — `LiveSimulator`: één gedeelde instantie; toestand `CA, T, I` (regelaar-integraal); asyncio-lus integreert per tick, failsafe na elk sample, broadcast via WebSocket. Twin-logica in `apply_measurement` (Tc-override, UA-schatting uit Tc+Q, Tc uit Q).
- `app/main.py` — REST-routes + `/ws`; alle handlers `async def` (geen locks nodig: één eventloop).
- `static/app.js` — Vue-app; `PARAM_DEFS` (houd in sync met `model.py`) en `SCENARIOS`.

## Fysica / conventies
- Eenheden: tijd in **minuten**, T in K, Q in kJ/min (naar de reactor; negatief = koelen), UA in kJ/(min·K), dH in kJ/mol, E in J/mol, Cp in kJ/(kg·K). Documenteer eenheden bij elk nieuw veld.
- Standaardproces: 1 m³, τ = 40 min, 80 % conversie bij 350 K, ΔT_ad = 357 K; werkpunt is open-loop instabiel en wordt door TIC-101 gestabiliseerd. Wijzig standaardwaarden alleen met herberekening van het werkpunt (tests in `tests/test_model.py` bewaken dit).
- Nieuwe parameter toevoegen = `Field` in `model.py` + entry in `PARAM_DEFS` (app.js) + tabel in README.
- Failsafe-gedrag nooit verzwakken zonder expliciete vraag; tests in `tests/test_simulator.py` dekken het af.
- Draai met één worker (state leeft in het geheugen).
