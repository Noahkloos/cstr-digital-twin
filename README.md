# CSTR Digital Twin

Dynamische simulatie + digital twin van een **continu geroerde tankreactor (CSTR)** met een
eerste-orde exotherme reactie A → B. Web-dashboard voor operators, REST-API, real-time
WebSockets, runaway-failsafe en een koppeling voor live procesdata.

---

## 1. Architectuur

```
 ┌──────────────────────── Browser (operator) ────────────────────────┐
 │  static/index.html + app.js  (Vue 3 + Chart.js, geen build-stap)   │
 │  KPI's · T(t)/CA(t)-grafieken · parameters · twin-paneel · alarmen │
 └──────────────▲──────────────────────────────┬───────────────────────┘
                │ WebSocket /ws (10×/s)        │ REST (fetch)
 ┌──────────────┴──────────────────────────────▼───────────────────────┐
 │  FastAPI  (app/main.py)                                              │
 │   /api/simulate  /api/parameters  /api/twin  /api/control  /ws ...   │
 │                                                                      │
 │  LiveSimulator (app/simulator.py)  ── asyncio-lus, elke tick:        │
 │     integreren → failsafe-check → broadcast                          │
 │     twin: meting → residu → sync toestand + ingangen                 │
 │                                                                      │
 │  Model (app/model.py) ── pure functies                               │
 │     ODE's · RK4 · impliciete Euler (stijf) · stationaire toestanden  │
 └──────────────▲───────────────────────────────────────────────────────┘
                │ POST /api/twin  {"CA":0.5,"T":350,"Q":-5000}
 ┌──────────────┴─────────────────┐
 │ Externe datastroom             │  scripts/twin_feeder.py (CSV-replay)
 │ DCS / PLC / historian / MQTT   │  scripts/mqtt_bridge_example.py
 └────────────────────────────────┘
```

**Keuzes**

| Onderdeel | Keuze | Waarom |
|---|---|---|
| Backend | Python 3.9+ · FastAPI · Uvicorn | Wetenschappelijk rekenen hoort in Python; FastAPI geeft validatie, WebSockets en automatische API-docs (`/docs`). |
| Frontend | Vue 3 (global build) + Chart.js | Geen Node/npm/build nodig; bestanden worden direct door de server geserveerd. |
| Real-time | WebSockets | Server pusht elke tick de toestand; alle browsers zien hetzelfde. |
| Solver | RK4, automatisch impliciete Euler bij stijfheid | Bij runaway wordt k(T) ~10⁵/min: expliciete RK4 zou numeriek exploderen. |
| Opslag | Geen database | Eén gedeelde in-memory simulatie; historie (3000 punten) voor nieuwe clients. |

---

## 2. Het model

$$\frac{dC_A}{dt} = \frac{F}{V}(C_{A,in}-C_A) - k_0 e^{-E/RT} C_A$$

$$\frac{dT}{dt} = \frac{F}{V}(T_{in}-T) + \frac{-\Delta H}{\rho C_p} k_0 e^{-E/RT} C_A + \frac{Q}{\rho C_p V}$$

**Koeling** (instelbaar via `cooling_mode`):

$$Q = UA\,(T_c - T) \quad\text{(koelmantel, standaard)} \qquad\text{of}\qquad Q = \text{constant} \quad\text{(vereenvoudigd)}$$

**Temperatuurregelaar TIC-101** (digitale PI, sampletijd = `dt`), stuurt de koelmiddeltemperatuur:

$$T_c = I + K_c\,(T_{sp} - T), \qquad \frac{dI}{dt} = \frac{K_c}{\tau_I}(T_{sp}-T), \qquad T_{c,min} \le T_c \le T_{c,max}$$

met anti-windup en stootvrij omschakelen tussen HAND en AUTO.

**Eenheden (consistent, tijd in minuten) en standaardwaarden**

| Parameter | Eenheid | Standaard | Betekenis |
|---|---|---|---|
| `V` | L | 1000 | reactorvolume (1 m³) |
| `F` | L/min | 25 | volumestroom → verblijftijd τ = V/F = 40 min |
| `CA_in` | mol/L | 5,0 | voedingsconcentratie (geconcentreerd) |
| `T_in` | K | 300 | voedingstemperatuur |
| `k0` | 1/min | 8,66·10¹⁰ | pre-exponentiële factor |
| `E` | J/mol | 80 000 | activeringsenergie (E/R ≈ 9620 K: k verdubbelt per ~9 K bij 350 K) |
| `dH` | kJ/mol | −250 | reactie-enthalpie (sterk exotherm, bv. hydrogenering/oxidatie) |
| `rho` | kg/L | 1,0 | dichtheid |
| `Cp` | kJ/(kg·K) | 3,5 | warmtecapaciteit (water 4,18; organische mengsels 2–3,5) |
| `cooling_mode` | – | `jacket` | `jacket` = Q = UA·(Tc − T); `fixed` = constante Q |
| `UA` | kJ/(min·K) | 600 | warmteoverdracht mantel + spiraal (= 10 kW/K) |
| `Tc` | K | 315,7 | koelmiddeltemperatuur in HAND |
| `Q` | kJ/min | −20 600 | alleen voor `fixed` |
| `R` | J/(mol·K) | 8,314 | gasconstante |
| `T_sp`, `Kc`, `tau_I` | K, K/K, min | 350, 5, 5 | regelaarinstellingen |
| `Tc_min`, `Tc_max` | K | 280, 360 | bereik van het koelmiddel |

**Kengetallen van het standaardproces**
- Werkpunt: T = 350 K, C_A = 1,0 mol/L, **80 % conversie**, reactiewarmte 415 kW, T_c = 315,7 K.
- Adiabatische temperatuurstijging ΔT_ad = (−ΔH)·C_A,in/(ρ·Cp) = 250·5/3,5 = **357 K** (hoge ernstklasse).
- Het werkpunt is **open-loop instabiel** (zie Semenov-diagram): voor een stabiel werkpunt zonder
  regelaar zou UA > ~1400 kJ/(min·K) (≈ 24 kW/K) nodig zijn, wat voor 1 m³ fysiek niet haalbaar is.
  Daarom draait een exotherme CSTR in de praktijk altijd met een temperatuurregelaar.

### Controle van de fysica (versie 1.1)

De modelvergelijkingen waren correct en de eenheden consistent. Twee dingen klopten fysisch niet:

| Probleem in v1.0 | Gevolg | Oplossing in v1.1 |
|---|---|---|
| `Cp = 0,239 kJ/(kg·K)`, een leerboekartefact (omrekenfactor cal→J), ~17× te laag | onrealistische energiebalans | `Cp = 3,5 kJ/(kg·K)` en ΔH, C_A,in en V herschaald naar een realistische reactor |
| Werkpunt met 99 % conversie (τ = 1 min, k = 114 /min) | bijna geen opgehoopte reactant, dus warmteproductie begrensd door de voeding en T stijgt **lineair** in plaats van exponentieel | werkpunt met 80 % conversie (τ = 40 min); bij koelingsuitval reageert ~1 mol/L opgehoopte reactant steeds sneller op, wat een **exponentiële** runaway geeft |
| Constante Q | geen temperatuurterugkoppeling en een onfysische koude toestand (224 K, onder T_in) | mantelkoeling `Q = UA·(Tc − T)` + PI-regelaar; T kan nooit onder min(T_in, T_c) zakken |

**Numerieke solver** (`app/model.py` → `integrate`, `step_closed_loop`)
- Standaard klassieke **RK4** met stap `dt` (0,01 min = sampletijd van de regelaar).
- Per stap wordt de stijfheid geschat (spectraalstraal van de Jacobiaan). Is `dt·ρ > 2`, dan
  wordt een **impliciete Euler-stap met Newton-Raphson** gebruikt (L-stabiel). Bij runaway
  wordt k(T) ~10⁴–10⁵ /min en zou expliciete RK4 numeriek exploderen.
- `method: "euler"` in `/api/simulate` geeft pure expliciete Euler (educatief).
- **Semenov-diagram** (`/api/heat-balance`): warmteproductie Q_gen(T) versus warmteafvoer
  Q_rem(T) = F·ρ·Cp·(T − T_in) + UA·(T − T_c). Snijpunten = evenwichten; waar Q_gen steiler
  loopt dan Q_rem versterkt een temperatuurstijging zichzelf.

---

## 3. Directorystructuur

```
cstr-digital-twin/
├── app/
│   ├── __init__.py
│   ├── config.py             # omgevingsvariabelen / .env
│   ├── model.py              # ODE's, mantelkoeling, PI-regelaar, RK4/impl. Euler, evenwichten, Semenov
│   ├── simulator.py          # LiveSimulator: lus, regelaar, failsafe, digital twin, WebSocket-broadcast
│   ├── schemas.py            # request-validatie (Pydantic)
│   └── main.py               # FastAPI: REST-routes, /ws, statische frontend
├── static/
│   ├── index.html            # operator-dashboard
│   ├── app.js                # Vue-app: WebSocket, grafieken, parameters, twin, alarmen
│   ├── styles.css            # HMI-stijl (licht/donker)
│   └── vendor/               # vue.global.prod.js, chart.umd.js (offline gebruik)
├── data/
│   ├── testdata_normal.csv           # 15 min normaal bedrijf, verstoringen in T_in en F, meetruis
│   ├── testdata_cooling_failure.csv  # 20 min, koelwaterpomp valt uit na 2 min → runaway tot ~507 K
│   └── testdata_runaway.csv          # 12 min koude opstart met ophoping → runaway tot ~655 K (trip)
├── examples/
│   ├── live_update.json          # {"CA":0.5,"T":350,"Q":-5000}
│   ├── live_update_full.json     # alle velden + metadata
│   ├── live_update_batch.json    # lijst met metingen
│   ├── parameters_update.json
│   └── simulate_request.json
├── scripts/
│   ├── twin_feeder.py            # CSV afspelen naar /api/twin
│   ├── generate_testdata.py      # testdatasets (opnieuw) genereren
│   ├── download_vendor.py        # Vue + Chart.js lokaal zetten
│   └── mqtt_bridge_example.py    # voorbeeldkoppeling MQTT → twin
├── tests/                    # 43 pytest-tests (model/fysica, regelaar, simulator, API)
├── docs/OPERATOR_HANDLEIDING.md
├── .claude/launch.json       # preview-configuratie voor Claude Code
├── CLAUDE.md                 # context voor Claude Code
├── run.py                    # startpunt
├── start.ps1 / start.sh      # installeren + starten in één stap
├── requirements.txt
├── .env.example
└── pytest.ini
```

---

## 4. Installatie en starten

**Vereist:** Python 3.9 of nieuwer. (Node.js is niet nodig.)

### Windows (PowerShell)
```powershell
cd "C:\claude code\cstr-digital-twin"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```
Of in één keer: `.\start.ps1`

### Linux / macOS
```bash
cd cstr-digital-twin
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py
```
Of: `./start.sh`

Open daarna **http://127.0.0.1:8000** (dashboard) en **http://127.0.0.1:8000/docs**
(interactieve API-documentatie, hier kun je elk endpoint uitproberen).

### Tests
```powershell
.\.venv\Scripts\python.exe -m pytest
```

### In Claude Code
1. Open de map `cstr-digital-twin` als project (of de bovenliggende map).
2. Vraag bv. *"start de CSTR twin"* — Claude gebruikt `.claude/launch.json` om de server te
   starten en het dashboard in het browserpaneel te openen.
3. `CLAUDE.md` beschrijft de architectuur en conventies, zodat Claude wijzigingen consistent
   doorvoert (bv. *"voeg een dynamische mantelbalans met koelwaterdebiet toe"*).
4. Laat Claude na elke wijziging `python -m pytest` draaien.

### Deployment op een lokale server (LAN)
1. Maak `.env` aan (kopie van `.env.example`) en zet:
   ```
   CSTR_HOST=0.0.0.0
   CSTR_PORT=8000
   CSTR_TWIN_API_KEY=<lange willekeurige sleutel>
   ```
2. Open poort 8000 in de firewall (Windows: *Windows Defender Firewall → Inkomende regel*).
3. Draai `run.py` als service:
   - **Windows:** met [NSSM](https://nssm.cc): `nssm install CSTRTwin "C:\...\.venv\Scripts\python.exe" "C:\...\run.py"`,
     startmap = projectmap; of via Taakplanner "Bij opstarten".
   - **Linux (systemd)** `/etc/systemd/system/cstr-twin.service`:
     ```ini
     [Unit]
     Description=CSTR Digital Twin
     After=network.target
     [Service]
     WorkingDirectory=/opt/cstr-digital-twin
     ExecStart=/opt/cstr-digital-twin/.venv/bin/python run.py
     Restart=always
     [Install]
     WantedBy=multi-user.target
     ```
     `sudo systemctl enable --now cstr-twin`
4. Gebruik **één** worker-proces (standaard): de simulatie leeft in het geheugen van dat proces.
5. Voor HTTPS: zet er een reverse proxy voor (nginx/Caddy/IIS) met WebSocket-doorgifte voor `/ws`.
6. Offline netwerk: `static/vendor/` bevat Vue en Chart.js al; er is geen internet nodig.

---

## 5. API-referentie

| Methode | Pad | Functie |
|---|---|---|
| GET | `/api/health` | levenscheck |
| GET | `/api/state?history=true` | actuele toestand (optioneel met historie) |
| POST | `/api/control/{start\|stop\|reset}` | simulatie besturen |
| GET | `/api/parameters` | alle parameters (5 groepen) |
| PUT/POST | `/api/parameters` | gedeeltelijke update |
| POST | `/api/parameters/defaults` | standaardwaarden herstellen |
| GET | `/api/steady-states` | open-loop evenwichten bij huidige T_c + stabiliteit |
| GET | `/api/heat-balance` | Semenov-diagram (Q_gen, Q_rem), ΔT_ad, actuele toestand |
| POST | `/api/simulate` | offline simulatie / voorspelling (raakt live-sim niet) |
| GET | `/api/twin` | twin-status + recente metingen |
| POST | `/api/twin` | live-meting(en) ontvangen |
| POST | `/api/twin/mode` | `{"enabled": true, "blend": 1.0}` |
| POST | `/api/failsafe/reset` | trip bevestigen (409 als T ≥ T_warn) |
| WS | `/ws` | berichten `init`, `state`, `measurement`, `parameters` |

### Voorbeelden (PowerShell)
```powershell
# Live-meting sturen
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/twin `
  -ContentType "application/json" -Body (Get-Content examples\live_update.json -Raw)

# Parameter aanpassen
Invoke-RestMethod -Method Put http://127.0.0.1:8000/api/parameters `
  -ContentType "application/json" -Body '{"process": {"UA": 300}, "control": {"T_sp": 345}}'

# Wat-als: koude opstart met ophoping offline doorrekenen
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/simulate `
  -ContentType "application/json" -Body (Get-Content examples\simulate_request.json -Raw)
```

### Voorbeelden (curl)
```bash
curl -X POST http://127.0.0.1:8000/api/twin -H "Content-Type: application/json" \
     -d '{"CA": 0.5, "T": 350, "Q": -5000}'
curl -X PUT  http://127.0.0.1:8000/api/parameters -H "Content-Type: application/json" \
     -d '{"process": {"UA": 550, "F": 24}, "control": {"T_sp": 348}, "safety": {"T_warn": 390}}'
curl -X POST http://127.0.0.1:8000/api/simulate -H "Content-Type: application/json" \
     -d @examples/simulate_request.json
```

### Antwoord van `POST /api/twin`
```json
{
  "applied": true,
  "twin_mode": true,
  "sim_time": 12.43,
  "state": {"CA": 0.5, "T": 350.0, "Tc": 341.67},
  "residual": {"CA": -0.50, "T": 0.02, "Q": 15590.0},
  "derived": {"Tc": 341.67},
  "failsafe": {"tripped": false, "reason": null, "alarm": "normal", "recommended_Q": null},
  "message": null
}
```
- `residual` = meting − modelvoorspelling vlak vóór synchronisatie (maat voor modelkwaliteit).
- `derived` = grootheden die de twin uit de meting heeft afgeleid (T_c uit Q, of UA uit T_c + Q).

---

## 6. Parameters aanpassen

Drie manieren — alle drie worden door de server gevalideerd (ongeldige of onbekende velden → HTTP 422):

1. **Dashboard** → paneel *Parameters*: tabbladen Proces / Regelaar / Start / Veiligheid / Simulatie.
   Wijzig met slider of invoerveld; gewijzigde velden worden oranje gemarkeerd.
   *Wat-als* toont eerst een voorspelling (paarse stippellijn, inclusief regelaar); *Toepassen* voert door.
   De knoppen onder **Scenario's** zetten realistische storingen klaar (koelingsuitval,
   vervuiling, regelaar in hand, koude opstart met ophoping).
2. **API**: `PUT /api/parameters` met alleen de velden die veranderen:
   ```json
   {"process": {"UA": 300}, "control": {"auto": false}, "initial": {"T0": 340}, "safety": {"T_trip": 580}, "runtime": {"speed": 2}}
   ```
3. **Standaardwaarden in code**: `app/model.py`, klassen `ProcessParameters`, `ControllerSettings`,
   `InitialConditions`, `SafetySettings`, `RuntimeSettings` (`Field(<standaard>, ...)`).

| Groep | Velden |
|---|---|
| `process` | `V, F, CA_in, T_in, k0, E, dH, rho, Cp, cooling_mode, UA, Tc, Q, R` |
| `control` | `auto`, `T_sp` (350), `Kc` (5), `tau_I` (5 min), `Tc_min` (280), `Tc_max` (360) |
| `initial` | `CA0` (1,0), `T0` (350) — gebruikt bij reset |
| `safety` | `T_warn` (400), `T_trip` (600), `stop_feed_on_trip`, `UA_emergency` (1500), `T_coolant_emergency` (290) |
| `runtime` | `dt` (0,01 min), `speed` (1 sim-min per s), `tick` (s), `twin_realtime`, `twin_stale_after` (s) |

Omschakelen HAND ↔ AUTO is stootvrij: naar HAND blijft T_c staan op de laatste regelaaruitgang,
naar AUTO start de regelaar vanaf de huidige T_c.

---

## 7. Zo werkt de digital twin

1. **Aanzetten**: schakelaar *Digital Twin-modus* in het dashboard of
   `POST /api/twin/mode {"enabled": true, "blend": 1.0}`. De simulatie start dan automatisch
   en rekent in **echte tijd** (1 s = 1/60 min), net als de installatie.
2. **Meting ontvangen** (`POST /api/twin`): voor elke meting
   - berekent de twin het **residu** (meting − model),
   - zet de toestand bij: `x ← x + blend·(x_meting − x)`
     (`blend = 1`: harde synchronisatie; `blend = 0.2`: geleidelijke bijsturing die ruis dempt),
   - neemt gemeten **ingangen** over (`F`, `T_in`, `CA_in`, `UA`),
   - verwerkt de **koeling** (mantelmodus):

     | Gemeten | Wat de twin doet |
     |---|---|
     | `Tc` | model gebruikt de gemeten koelmiddeltemperatuur (de echte regelaar stuurt, niet die van het model) |
     | `Tc` + `Q` | bovendien **UA online geschat**: UA = Q / (T_c − T). Zo ziet de twin vervuiling of een uitgevallen koelwaterpomp |
     | alleen `Q` | T_c afgeleid: T_c = T + Q / UA |
     | `Q` in modus `fixed` | Q wordt direct overgenomen |

   - controleert de **failsafe op de gemeten temperatuur**.
3. **Tussen metingen** voorspelt het model de toestand verder. Zo kun je met *Wat-als* vanuit
   de gesynchroniseerde toestand vooruitkijken (bv. "wat als de koeling uitvalt?").
4. **Watchdog**: komt er langer dan `twin_stale_after` (10 s) geen meting, dan meldt het
   dashboard *"Twin: geen data"*.
5. Staat de twin-modus uit, dan worden metingen wel geregistreerd (en op runaway gecontroleerd)
   maar niet toegepast (`"applied": false`). Bij het uitzetten neemt de eigen regelaar van het
   model stootvrij het stuur weer over.

### Testen met de meegeleverde datasets
Start de server en voer in een tweede terminal uit:
```powershell
# normaal bedrijf, 1 meting/s (echte tijd)
.\.venv\Scripts\python.exe scripts\twin_feeder.py data\testdata_normal.csv --enable-twin

# koelwaterpomp valt uit, 10x versneld: twin schat UA -> 0 en voorspelt de runaway
.\.venv\Scripts\python.exe scripts\twin_feeder.py data\testdata_cooling_failure.csv --enable-twin --interval 0.1

# koude opstart met ophoping, 10x versneld: failsafe tript op de gemeten temperatuur
.\.venv\Scripts\python.exe scripts\twin_feeder.py data\testdata_runaway.csv --enable-twin --interval 0.1 --blend 0.5

# alleen temperatuur en koeling meten (geen online analyser, geen Tc): twin leidt Tc af uit Q
.\.venv\Scripts\python.exe scripts\twin_feeder.py data\testdata_normal.csv --enable-twin --fields T,Q
```
De testdata is gemaakt met een "installatie" (met eigen regelaar) die 4 % snellere kinetiek heeft
dan het model, plus meetruis, zodat je realistische residuen ziet.
Opnieuw genereren: `python scripts/generate_testdata.py`.

### Koppelen aan een echte datastroom
De twin accepteert elke bron die HTTP POST kan doen. Veelgebruikte routes:

| Bron | Aanpak |
|---|---|
| **MQTT** (edge gateway, Node-RED) | `scripts/mqtt_bridge_example.py` (`pip install paho-mqtt`); pas `TAG_MAP` aan op je tagnamen. |
| **OPC UA** (PLC/DCS) | Klein script met `asyncua`: lees tags elke seconde en POST ze (zelfde patroon als `twin_feeder.py`). |
| **Node-RED** | `http request`-node, methode POST, URL `http://<server>:8000/api/twin`, body = JSON. |
| **Historian / PI / SQL** | Periodieke query → lijst metingen als batch posten (`examples/live_update_batch.json`). |
| **Excel/CSV** | `twin_feeder.py` met je eigen CSV (kolommen `CA,T,Tc,Q,F,T_in,CA_in`; ontbrekende kolommen mogen). |

Regels voor het berichtformaat:
- JSON-object of lijst van objecten; minstens één van `CA, T, Tc, Q, UA, F, T_in, CA_in`.
- Q = warmtestroom **naar** de reactor, dus negatief bij koelen. Bij een koelwatermeting:
  Q = −ṁ_koelwater · Cp,water · (T_uit − T_in).
- Eenheden zoals in §2 (T in **kelvin**, Q in **kJ/min**, negatief = koelen). Reken °C om: K = °C + 273,15.
- Extra velden (`timestamp`, `source`, ...) zijn toegestaan en worden genegeerd.
- Beveilig met `CSTR_TWIN_API_KEY` in `.env`; stuur dan header `X-API-Key: <sleutel>`.

---

## 8. Failsafe tegen runaway-reacties

| Situatie | Reactie |
|---|---|
| T ≥ `T_warn` (400 K) | Oranje waarschuwingsbalk met handelingsadvies. |
| Regelaar verzadigd (T_c op T_c,min) | Oranje KPI + melding: de koeling staat maximaal, T is niet meer te regelen. |
| T ≥ `T_trip` (600 K), in model **of** gemeten | **TRIP** (vergrendeld): rode alarmbalk, gebeurtenis in log, en in het model: noodkoeling `Q_nood = −UA_em·(T − T_noodkoel)` **bovenop** de mantelkoeling, en (instelbaar) reactantvoeding dicht (`CA_in = 0`). |
| Trip resetten | Alleen als T < `T_warn` — knop in de alarmbalk of `POST /api/failsafe/reset` (anders HTTP 409). |
| Numerieke fout | Toestand terug naar laatste geldige waarde, trip + simulatie stopt. |

De failsafe wordt ná **elke integratiestap** gecontroleerd (niet alleen per scherm-update),
en `POST /api/twin` retourneert de trip-status en `recommended_Q`, zodat een externe
applicatie kan reageren.

Let op in het scenario *koude opstart met ophoping*: de temperatuur stijgt ná de trip nog door
tot ~655 K, omdat de opgehoopte reactant al aan het reageren is. Een trip kan een runaway die
al versnelt niet direct stoppen. Daarom is het voorkomen van ophoping zo belangrijk.
De trip-grens van 600 K is een eis van deze app; voor een echte vloeistoffasereactor ligt een
realistische grens lager (kookpunt, ontledingstemperatuur, typisch 380–450 K).

> ⚠️ **Veiligheid:** deze app is een simulatie- en adviesinstrument. De failsafe grijpt in op het
> *model*, niet op de installatie. Echte beveiliging tegen runaway hoort in een gecertificeerd
> Safety Instrumented System (SIS, IEC 61511) — nooit in een web-app.

---

## 9. Problemen oplossen

| Symptoom | Oplossing |
|---|---|
| `python` niet gevonden | Installeer Python 3.9+ en vink "Add to PATH" aan. |
| Poort 8000 bezet | `CSTR_PORT=8010` in `.env`. |
| Dashboard leeg / "Geen verbinding" | Draait `run.py`? Controleer de terminal op fouten; proxy moet WebSockets doorgeven. |
| Grafiek beweegt niet | Klik *Start* (of zet twin-modus aan). |
| HTTP 422 bij `/api/twin` | Veldnaam of eenheid fout; het antwoord noemt het veld. |
| HTTP 401 bij `/api/twin` | `X-API-Key` header ontbreekt of klopt niet. |
| "Server rekent trager dan ingesteld" | Stijf regime bij zeer hoge T of hoge snelheid; verlaag `speed` of vergroot `tick`. |

Zie ook **[docs/OPERATOR_HANDLEIDING.md](docs/OPERATOR_HANDLEIDING.md)**.
