/*
 * CSTR Digital Twin - operator-dashboard (Vue 3, zonder build-stap).
 *
 * Datastromen
 *   WebSocket /ws  -> 'init' (historie + parameters), 'state' (elke tick),
 *                     'measurement' (twin-data), 'parameters' (na wijziging)
 *   REST /api/...  -> alle acties (start/stop, parameters, twin, failsafe, wat-als)
 */
/* global Vue, Chart */
(function () {
  "use strict";

  // ------------------------------------------------------------------ //
  // Parameterdefinities voor het paneel (label, eenheid, sliderbereik).
  // Het sliderbereik is een UI-hulpmiddel; in het invoerveld kun je elke
  // geldige waarde typen. De server valideert alles opnieuw.
  // ------------------------------------------------------------------ //
  const PARAM_DEFS = {
    process: [
      { key: "cooling_mode", label: "Koelmodus", unit: "", type: "select",
        options: [{ value: "jacket", label: "Koelmantel: Q = UA·(Tc − T)" }, { value: "fixed", label: "Vaste Q (vereenvoudigd)" }],
        help: "Mantel = realistisch: de afvoer groeit met T. Vaste Q heeft geen temperatuurterugkoppeling." },
      { key: "UA", label: "UA  warmteoverdracht", unit: "kJ/(min·K)", min: 0, max: 1500, step: 10, showIf: (p) => p.cooling_mode === "jacket",
        help: "U × oppervlak van mantel/spiraal. 60 kJ/(min·K) = 1 kW/K. 0 = koelingsuitval." },
      { key: "Tc", label: "Tc  koelmiddel (hand)", unit: "K", min: 270, max: 370, step: 0.5, showIf: (p) => p.cooling_mode === "jacket",
        help: "Koelmiddeltemperatuur als regelaar TIC-101 in HAND staat. In AUTO bepaalt de regelaar Tc." },
      { key: "Q", label: "Q  vaste warmtestroom", unit: "kJ/min", min: -60000, max: 20000, step: 100, showIf: (p) => p.cooling_mode === "fixed",
        help: "Alleen in modus 'vaste Q'. Negatief = koelen." },
      { key: "F", label: "F  volumestroom", unit: "L/min", min: 0, max: 100, step: 0.5, help: "Voedingsdebiet (= afvoerdebiet). Verblijftijd τ = V/F." },
      { key: "CA_in", label: "CA,in  voedingsconcentratie", unit: "mol/L", min: 0, max: 8, step: 0.05, help: "Concentratie A in de voeding. Bepaalt mee de adiabatische temperatuurstijging." },
      { key: "T_in", label: "T,in  voedingstemperatuur", unit: "K", min: 270, max: 400, step: 1, help: "Temperatuur van de voeding." },
      { key: "V", label: "V  reactorvolume", unit: "L", min: 100, max: 5000, step: 10, help: "Vloeistofvolume in de reactor." },
      { key: "k0", label: "k0  pre-exp. factor", unit: "1/min", min: 1e8, max: 1e13, log: true, help: "Arrhenius pre-exponentiële factor (logaritmische slider)." },
      { key: "E", label: "E  activeringsenergie", unit: "J/mol", min: 40000, max: 150000, step: 500, help: "Hoger = k stijgt sterker met T = gevoeliger voor runaway." },
      { key: "dH", label: "ΔH  reactie-enthalpie", unit: "kJ/mol", min: -400, max: 0, step: 5, help: "Negatief = exotherm." },
      { key: "rho", label: "ρ  dichtheid", unit: "kg/L", min: 0.5, max: 2, step: 0.01, help: "Dichtheid van het reactiemengsel." },
      { key: "Cp", label: "Cp  warmtecapaciteit", unit: "kJ/(kg·K)", min: 1, max: 5, step: 0.05, help: "Water 4,18; organische mengsels ca. 2–3,5." },
      { key: "R", label: "R  gasconstante", unit: "J/(mol·K)", min: 8, max: 8.5, step: 0.001, help: "Normaal 8,314. Moet consistent zijn met E." },
    ],
    control: [
      { key: "auto", label: "TIC-101 in automaat", unit: "", type: "bool", help: "Uit = handbediening: Tc blijft op de handwaarde (tab Proces)." },
      { key: "T_sp", label: "Setpoint T", unit: "K", min: 320, max: 380, step: 0.5, help: "Gewenste reactortemperatuur." },
      { key: "Kc", label: "Versterking Kc", unit: "K/K", min: 0, max: 20, step: 0.5, help: "K koelmiddel per K temperatuurfout. Te laag = runaway niet te stoppen." },
      { key: "tau_I", label: "Integratietijd τI", unit: "min", min: 0.5, max: 60, step: 0.5, help: "Kleiner = sneller wegwerken van blijvende afwijking (maar onrustiger)." },
      { key: "Tc_min", label: "Tc minimum", unit: "K", min: 250, max: 320, step: 1, help: "Koudste beschikbare koelmiddel (maximale koeling)." },
      { key: "Tc_max", label: "Tc maximum", unit: "K", min: 320, max: 400, step: 1, help: "Warmste koelmiddel (voor opwarmen)." },
    ],
    initial: [
      { key: "CA0", label: "CA(0)", unit: "mol/L", min: 0, max: 8, step: 0.05, help: "Beginconcentratie na een reset. Hoog + koud = ophoping (gevaarlijk)." },
      { key: "T0", label: "T(0)", unit: "K", min: 270, max: 450, step: 1, help: "Begintemperatuur na een reset." },
    ],
    safety: [
      { key: "T_warn", label: "Waarschuwingsgrens", unit: "K", min: 330, max: 700, step: 5, help: "Boven deze temperatuur verschijnt een waarschuwing." },
      { key: "T_trip", label: "Trip-grens (runaway)", unit: "K", min: 350, max: 900, step: 5, help: "Boven deze temperatuur grijpt de failsafe in (standaard 600 K)." },
      { key: "stop_feed_on_trip", label: "Voeding stoppen bij trip", unit: "", type: "bool", help: "Zet CA,in op 0 na een trip (quench met inerte voeding)." },
      { key: "UA_emergency", label: "Noodkoeling UA", unit: "kJ/(min·K)", min: 0, max: 5000, step: 50, help: "Extra koeling na een trip: Q = −UA·(T − Tnoodkoel), bovenop de mantel." },
      { key: "T_coolant_emergency", label: "Noodkoelwater T", unit: "K", min: 270, max: 350, step: 1, help: "Temperatuur van het noodkoelwater." },
    ],
    runtime: [
      { key: "speed", label: "Simulatiesnelheid", unit: "min/s", min: 0.05, max: 10, step: 0.05, help: "Gesimuleerde minuten per echte seconde." },
      { key: "dt", label: "Integratiestap dt", unit: "min", min: 0.001, max: 0.05, step: 0.001, help: "Maximale RK4-stap = sampletijd van de regelaar." },
      { key: "tick", label: "Update-interval", unit: "s", min: 0.05, max: 1, step: 0.05, help: "Hoe vaak de server rekent en de grafieken bijwerkt." },
      { key: "twin_realtime", label: "Twin in echte tijd", unit: "", type: "bool", help: "In twin-modus loopt het model gelijk met de klok (1 s = 1/60 min)." },
      { key: "twin_stale_after", label: "Twin-data verouderd na", unit: "s", min: 1, max: 120, step: 1, help: "Waarschuwing als er zo lang geen meting binnenkomt." },
    ],
  };
  const GROUPS = [
    { key: "process", label: "Proces" },
    { key: "control", label: "Regelaar" },
    { key: "initial", label: "Start" },
    { key: "safety", label: "Veiligheid" },
    { key: "runtime", label: "Simulatie" },
  ];
  const MAX_POINTS = 20000; // maximaal aantal punten per reeks in de browser

  // Kleuren komen uit CSS-variabelen, zodat licht/donker-thema klopt.
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // Grafieken buiten Vue's reactiviteit houden (sneller en voorkomt proxies op Chart-objecten).
  const charts = { T: null, CA: null, HB: null };
  const series = { model: [], meas: [], pred: [] }; // {t, CA, T, Tc}

  // Trainingsscenario's: elke knop zet een realistische storing klaar.
  // "reset" = eerst beginvoorwaarden instellen en de simulatie resetten.
  const SCENARIOS = [
    { key: "normal", label: "Normaal bedrijf", kind: "ok",
      help: "Standaardwaarden, regelaar in automaat, start in het werkpunt (350 K, 80 % conversie).",
      defaults: true, reset: true },
    { key: "cooling_fail", label: "Koelingsuitval", kind: "bad",
      help: "Koelwaterpomp valt uit (UA = 0). Opgehoopte reactant reageert steeds sneller: exponentiële runaway.",
      update: { process: { UA: 0 } } },
    { key: "fouling", label: "Verminderde koeling", kind: "warn",
      help: "Vervuilde mantel (UA halveert). De regelaar raakt verzadigd; temperatuurexcursie voordat hij herstelt.",
      update: { process: { UA: 300 } } },
    { key: "manual", label: "Regelaar in hand", kind: "warn",
      help: "TIC-101 naar HAND met een kleine verstoring. Het werkpunt is open-loop instabiel: de reactor drijft weg.",
      update: { control: { auto: false }, process: { T_in: 301 } } },
    { key: "accumulation", label: "Koude opstart met ophoping", kind: "bad",
      help: "Reactor gevuld met verse voeding (5 mol/L) bij 300 K, dan opwarmen naar 350 K. Zodra de reactie 'aanslaat' komt alle opgehoopte energie tegelijk vrij.",
      defaults: true, initial: { CA0: 5.0, T0: 300 }, reset: true },
  ];

  const app = Vue.createApp({
    data() {
      return {
        connected: false,
        state: { time: 0, CA: 0, T: 0, alarm: "normal", twin: { enabled: false, residual: {} }, events: [] },
        params: {},
        draft: {},
        groups: GROUPS,
        activeGroup: "process",
        steadyStates: [],
        windowMin: 10,
        speedOptions: [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10],
        twinBlend: 1.0,
        twinTestJson: '{ "T": 352, "Tc": 314, "Q": -22800 }',
        scenarios: SCENARIOS,
        heatBalance: null,
        prediction: null,
        predictionHorizon: 10,
        toast: null,
      };
    },

    computed: {
      dirtyCount() {
        let n = 0;
        for (const g of Object.keys(PARAM_DEFS)) for (const d of PARAM_DEFS[g]) if (this.isDirty(g, d.key)) n++;
        return n;
      },
      tempPct() {
        const s = this.params.safety; if (!s) return 0;
        const lo = 250;
        return Math.max(0, Math.min(100, ((this.state.T - lo) / (s.T_trip - lo)) * 100));
      },
      warnPct() {
        const s = this.params.safety; if (!s) return 0;
        return ((s.T_warn - 250) / (s.T_trip - 250)) * 100;
      },
      speedChoices() {
        // Vaste keuzes + de actuele waarde (bv. 0.833 ingesteld door de twin-feeder)
        const cur = this.params.runtime && this.params.runtime.speed;
        const list = this.speedOptions.slice();
        if (cur != null && !list.includes(cur)) list.push(cur);
        return list.sort((a, b) => a - b);
      },
      canResetTrip() {
        return this.params.safety && this.state.T < this.params.safety.T_warn;
      },
      tripButtonText() {
        if (this.canResetTrip) return "Trip bevestigen / resetten";
        return "Wachten tot T onder " + this.fmt(this.params.safety && this.params.safety.T_warn, 0) + " K";
      },
      controllerLabel() {
        const c = this.state.controller;
        if (!c || (this.params.process && this.params.process.cooling_mode === "fixed")) return "TIC-101 n.v.t.";
        if (c.overridden) return "TIC-101 volgt meting";
        return "TIC-101 " + (c.auto ? "AUTO" : "HAND") + (c.saturated ? " (verzadigd)" : "");
      },
      controllerPillClass() {
        const c = this.state.controller;
        if (!c) return "pill-idle";
        if (c.saturated || !c.auto) return "pill-warn";
        return "pill-ok";
      },
      controllerTitle() {
        const c = this.state.controller;
        return c ? "Temperatuurregelaar, setpoint " + this.fmt(c.T_sp, 1) + " K" : "";
      },
      twinLabel() {
        const tw = this.state.twin || {};
        if (!tw.enabled) return "Twin uit";
        return tw.stale ? "Twin: geen data" : "Twin live";
      },
      twinPillClass() {
        const tw = this.state.twin || {};
        if (!tw.enabled) return "pill-idle";
        return tw.stale ? "pill-warn" : "pill-twin";
      },
      twinStatusText() {
        const tw = this.state.twin || {};
        if (!tw.enabled) return "Uit (metingen worden alleen geregistreerd)";
        if (tw.seconds_since_last == null) return "Wacht op eerste meting…";
        if (tw.stale) return "Verouderd – laatste meting " + this.fmt(tw.seconds_since_last, 0) + " s geleden";
        return "Live – laatste meting " + this.fmt(tw.seconds_since_last, 1) + " s geleden";
      },
      twinStatusClass() {
        const tw = this.state.twin || {};
        if (!tw.enabled) return "muted";
        return tw.stale ? "warn-text" : "ok-text";
      },
      eventsReversed() {
        return (this.state.events || []).slice().reverse();
      },
    },

    watch: {
      windowMin() { this.redraw(); },
    },

    mounted() {
      this.initCharts();
      this.connect();
      this.refreshSteadyStates();
      // Tc verandert continu door de regelaar: analyse elke 2 s verversen (goedkoop).
      setInterval(() => { if (this.state.running) this.refreshSteadyStates(); }, 2000);
    },

    methods: {
      // ---------------- opmaak ----------------
      fmt(v, d) {
        if (v == null || !isFinite(v)) return "–";
        return Number(v).toLocaleString("nl-NL", { minimumFractionDigits: d, maximumFractionDigits: d });
      },
      signed(v, d) { return (v >= 0 ? "+" : "") + this.fmt(v, d); },
      clockTime(wall) { return new Date(wall * 1000).toLocaleTimeString("nl-NL"); },
      notify(text, kind = "info") {
        this.toast = { text, kind };
        clearTimeout(this._toastTimer);
        this._toastTimer = setTimeout(() => (this.toast = null), 4000);
      },

      // ---------------- REST-hulpfunctie ----------------
      async api(method, url, body) {
        const res = await fetch(url, {
          method,
          headers: body ? { "Content-Type": "application/json" } : {},
          body: body ? JSON.stringify(body) : undefined,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          let msg = data.detail;
          if (Array.isArray(msg)) msg = msg.map((e) => (e.field ? e.field + ": " : "") + (e.error || e.msg)).join("; ");
          throw new Error(msg || res.statusText);
        }
        return data;
      },

      // ---------------- WebSocket ----------------
      connect() {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        const ws = new WebSocket(`${proto}://${location.host}/ws`);
        ws.onopen = () => { this.connected = true; };
        ws.onclose = () => {
          this.connected = false;
          setTimeout(() => this.connect(), 2000); // automatisch opnieuw verbinden
        };
        ws.onmessage = (ev) => this.handleMessage(JSON.parse(ev.data));
      },

      handleMessage(msg) {
        if (msg.type === "init") {
          // Volledige status: historie vervangen (na verbinden of reset)
          series.model = msg.history.map((p) => ({ t: p.time, CA: p.CA, T: p.T, Tc: p.Tc }));
          series.meas = (msg.measurements || []).map((m) => ({ t: m.time, CA: m.CA, T: m.T }));
          // Tijdas kan opnieuw beginnen (reset): oude voorspelling is dan ongeldig.
          series.pred = [];
          this.prediction = null;
          this.setParams(msg.parameters, true);
          this.applyState(msg);
          this.redraw();
          this.refreshSteadyStates();
        } else if (msg.type === "state") {
          const last = series.model[series.model.length - 1];
          if (!last || msg.time > last.t + 1e-9) {
            series.model.push({ t: msg.time, CA: msg.CA, T: msg.T, Tc: msg.Tc });
            if (series.model.length > MAX_POINTS) series.model.splice(0, series.model.length - MAX_POINTS);
            this.redraw();
          }
          this.applyState(msg);
        } else if (msg.type === "measurement") {
          const m = msg.measurement;
          if (m && m.applied) {
            series.meas.push({ t: m.time, CA: m.CA, T: m.T });
            if (series.meas.length > MAX_POINTS) series.meas.shift();
            // Sync-sprong direct zichtbaar maken
            series.model.push({ t: m.time, CA: m.CA != null ? m.CA : this.state.CA, T: m.T != null ? m.T : this.state.T, Tc: this.state.Tc });
            this.redraw();
          }
        } else if (msg.type === "parameters") {
          this.setParams(msg.parameters, false);
          this.refreshSteadyStates();
        }
      },

      applyState(s) {
        const wasTripped = this.state.tripped;
        this.state = s;
        // Blend-slider alleen bijwerken als de serverwaarde verandert (anders "vecht" hij met de operator).
        if (s.twin && s.twin.blend !== this._lastServerBlend) {
          this._lastServerBlend = s.twin.blend;
          this.twinBlend = s.twin.blend;
        }
        if (s.tripped && !wasTripped) this.notify("FAILSAFE TRIP: " + s.trip_reason, "bad");
      },

      // ---------------- parameters ----------------
      setParams(p, force) {
        const hadDirty = this.dirtyCount > 0;
        this.params = p;
        // Concept-waarden alleen overschrijven als de operator niets aan het wijzigen is.
        if (force || !hadDirty) this.draft = JSON.parse(JSON.stringify(p));
        else {
          // Wel niet-gewijzigde velden bijwerken (bv. Q die de twin overneemt).
          for (const g of Object.keys(p)) for (const k of Object.keys(p[g]))
            if (!this.isDirty(g, k)) this.draft[g][k] = p[g][k];
        }
      },
      defsFor(group) {
        // Velden verbergen die in de gekozen koelmodus niet van toepassing zijn
        const proc = this.draft.process || {};
        return PARAM_DEFS[group].filter((d) => !d.showIf || d.showIf(proc));
      },
      isDirty(group, key) {
        if (!this.params[group] || !this.draft[group]) return false;
        return this.draft[group][key] !== this.params[group][key];
      },
      changedValues() {
        const out = {};
        for (const g of Object.keys(PARAM_DEFS)) {
          for (const d of PARAM_DEFS[g]) {
            if (this.isDirty(g, d.key)) (out[g] = out[g] || {})[d.key] = this.draft[g][d.key];
          }
        }
        return out;
      },
      async applyParams() {
        try {
          const p = await this.api("PUT", "/api/parameters", this.changedValues());
          this.setParams(p, true);
          this.notify("Parameters toegepast.", "ok");
          this.refreshSteadyStates();
        } catch (e) { this.notify("Niet toegepast: " + e.message, "bad"); }
      },
      discardParams() { this.draft = JSON.parse(JSON.stringify(this.params)); },
      async restoreDefaults() {
        if (!confirm("Alle parameters terugzetten naar de standaardwaarden?")) return;
        try { this.setParams(await this.api("POST", "/api/parameters/defaults"), true); this.notify("Standaardwaarden hersteld.", "ok"); }
        catch (e) { this.notify(e.message, "bad"); }
      },
      async setSpeed(v) {
        try { this.setParams(await this.api("PUT", "/api/parameters", { runtime: { speed: Number(v) } }), false); }
        catch (e) { this.notify(e.message, "bad"); }
      },

      // Sliders: lineair of logaritmisch (voor k0)
      sliderMin(d) { return d.log ? Math.log10(d.min) : d.min; },
      sliderMax(d) { return d.log ? Math.log10(d.max) : d.max; },
      sliderStep(d) { return d.log ? 0.01 : d.step; },
      toSlider(d, v) { return d.log ? Math.log10(Math.max(v, 1e-300)) : v; },
      fromSlider(d, s) {
        const v = Number(s);
        if (!d.log) return v;
        const raw = Math.pow(10, v);
        return Number(raw.toPrecision(3));
      },

      async refreshSteadyStates() {
        // Stationaire toestanden + Semenov-diagram in één aanroep
        try {
          const hb = await this.api("GET", "/api/heat-balance");
          this.steadyStates = hb.steady_states;
          this.heatBalance = { Tc: hb.Tc, dT_ad: hb.dT_ad, current: hb.current };
          this.drawHeatBalance(hb);
        } catch (e) { /* niet kritisch */ }
      },

      // ---------------- scenario's ----------------
      async runScenario(s) {
        try {
          if (s.defaults) await this.api("POST", "/api/parameters/defaults");
          if (s.initial) await this.api("PUT", "/api/parameters", { initial: s.initial });
          if (s.update) await this.api("PUT", "/api/parameters", s.update);
          if (s.reset) { this.clearPrediction(); await this.api("POST", "/api/control/reset"); }
          if (!this.state.running) await this.api("POST", "/api/control/start");
          this.setParams(await this.api("GET", "/api/parameters"), true);
          this.refreshSteadyStates();
          this.notify("Scenario: " + s.label, s.kind === "ok" ? "ok" : "info");
        } catch (e) { this.notify(e.message, "bad"); }
      },

      // ---------------- besturing ----------------
      async control(action) {
        try { await this.api("POST", "/api/control/" + action); }
        catch (e) { this.notify(e.message, "bad"); }
      },
      confirmReset() {
        if (confirm("Simulatie resetten naar de beginvoorwaarden? De grafiekhistorie wordt gewist.")) {
          this.clearPrediction();
          this.control("reset");
        }
      },
      async resetFailsafe() {
        try { await this.api("POST", "/api/failsafe/reset"); this.notify("Trip gereset.", "ok"); }
        catch (e) { this.notify(e.message, "bad"); }
      },

      // ---------------- digital twin ----------------
      async setTwin(enabled) {
        try { await this.api("POST", "/api/twin/mode", { enabled, blend: this.twinBlend }); }
        catch (e) { this.notify(e.message, "bad"); }
      },
      async sendTestMeasurement() {
        let body;
        try { body = JSON.parse(this.twinTestJson); } catch (e) { this.notify("Ongeldige JSON.", "bad"); return; }
        try {
          const r = await this.api("POST", "/api/twin", body);
          this.notify(r.applied ? "Meting toegepast." : (r.message || "Meting geregistreerd."), r.applied ? "ok" : "info");
        } catch (e) { this.notify(e.message, "bad"); }
      },

      // ---------------- wat-als voorspelling ----------------
      async runPrediction() {
        try {
          const r = await this.api("POST", "/api/simulate", {
            parameters: this.draft.process,
            control: this.draft.control,
            from_current_state: true,
            duration: this.predictionHorizon,
            n_points: 300,
          });
          if (r.error) this.notify(r.error, "bad");
          series.pred = r.time.map((t, i) => ({ t, CA: r.CA[i], T: r.T[i], Tc: r.Tc[i] }));
          this.prediction = r;
          this.redraw();
        } catch (e) { this.notify("Voorspelling mislukt: " + e.message, "bad"); }
      },
      clearPrediction() { series.pred = []; this.prediction = null; this.redraw(); },

      // ---------------- grafieken ----------------
      initCharts() {
        Chart.defaults.color = css("--text-muted");
        Chart.defaults.borderColor = css("--grid");
        Chart.defaults.font.family = css("--font-sans");
        const mk = (canvas, yTitle) => new Chart(canvas, {
          type: "line",
          data: {
            datasets: [
              { label: "Model", data: [], borderColor: css("--series-model"), borderWidth: 2, pointRadius: 0, tension: 0 },
              { label: "Meting", data: [], showLine: false, pointRadius: 3, pointBackgroundColor: css("--series-meas"), borderColor: css("--series-meas") },
              { label: "Voorspelling", data: [], borderColor: css("--series-pred"), borderDash: [6, 4], borderWidth: 2, pointRadius: 0 },
              { label: "Waarschuwing", data: [], borderColor: css("--warn"), borderDash: [3, 3], borderWidth: 1, pointRadius: 0 },
              { label: "Trip", data: [], borderColor: css("--bad"), borderDash: [3, 3], borderWidth: 1.5, pointRadius: 0 },
            ],
          },
          options: {
            animation: false,
            parsing: false,
            normalized: true,
            maintainAspectRatio: false,
            interaction: { mode: "nearest", axis: "x", intersect: false },
            plugins: {
              legend: { display: false },
              tooltip: { callbacks: { title: (items) => "t = " + items[0].parsed.x.toFixed(2) + " min" } },
            },
            scales: {
              x: { type: "linear", title: { display: true, text: "tijd [min]" },
                   ticks: { callback: (v) => Number(v).toLocaleString("nl-NL", { maximumFractionDigits: 2 }) } },
              // Leesbare labels, ook bij zeer kleine waarden (bv. CA -> 0 na een trip)
              y: { title: { display: true, text: yTitle }, grace: "2%",
                   ticks: { callback: (v) => (Math.abs(v) < 1e-6 ? "0"
                     : Number(v).toLocaleString("nl-NL", { maximumSignificantDigits: 4 })) } },
            },
          },
        });
        charts.T = mk(this.$refs.chartT, "T, Tc [K]");
        charts.CA = mk(this.$refs.chartCA, "CA [mol/L]");
        // Extra reeksen in de T-grafiek: koelmiddel (regelaaruitgang) en setpoint
        charts.T.data.datasets.push(
          { label: "Tc koelmiddel", data: [], borderColor: css("--series-coolant"), borderWidth: 1.5, pointRadius: 0 },
          { label: "Tc voorspelling", data: [], borderColor: css("--series-coolant"), borderDash: [6, 4], borderWidth: 1, pointRadius: 0 },
          { label: "Setpoint", data: [], borderColor: css("--text-muted"), borderDash: [2, 4], borderWidth: 1, pointRadius: 0 },
        );

        // Semenov-diagram: warmteproductie en -afvoer als functie van T (stationair)
        charts.HB = new Chart(this.$refs.chartHB, {
          type: "line",
          data: {
            datasets: [
              { label: "Warmteproductie Q_gen", data: [], borderColor: css("--bad"), borderWidth: 2, pointRadius: 0 },
              { label: "Warmteafvoer Q_rem", data: [], borderColor: css("--series-coolant"), borderWidth: 2, pointRadius: 0 },
              { label: "Evenwichten", data: [], showLine: false, pointRadius: 5, pointStyle: "circle",
                pointBackgroundColor: [], pointBorderColor: css("--text"), borderColor: css("--text") },
              { label: "Nu (productie)", data: [], showLine: false, pointRadius: 7, pointStyle: "crossRot",
                borderColor: css("--text"), borderWidth: 2 },
            ],
          },
          options: {
            animation: false, parsing: false, maintainAspectRatio: false,
            plugins: {
              legend: { display: false },
              tooltip: { callbacks: {
                title: (items) => "T = " + items[0].parsed.x.toFixed(1) + " K",
                label: (it) => it.dataset.label + ": " + it.parsed.y.toFixed(0) + " kW",
              } },
            },
            scales: {
              x: { type: "linear", title: { display: true, text: "T [K]" } },
              y: { title: { display: true, text: "warmtestroom [kW]" }, min: 0 },
            },
          },
        });
      },

      drawHeatBalance(hb) {
        if (!charts.HB) return;
        const kW = (v) => v / 60; // kJ/min -> kW
        const d = charts.HB.data.datasets;
        d[0].data = hb.T.map((T, i) => ({ x: T, y: kW(hb.Q_gen[i]) }));
        d[1].data = hb.T.map((T, i) => ({ x: T, y: kW(hb.Q_rem[i]) }));
        const ok = css("--ok"), bad = css("--bad");
        d[2].data = hb.steady_states.map((s) => ({ x: s.T, y: kW((-this.params.process.dH) * this.params.process.F * this.params.process.CA_in * s.conversion) }));
        d[2].pointBackgroundColor = hb.steady_states.map((s) => (s.stable ? ok : bad));
        d[3].data = [{ x: hb.current.T, y: kW(hb.current.Q_gen) }];
        const genMax = Math.max(...hb.Q_gen.map(kW));
        charts.HB.options.scales.y.max = Math.ceil((genMax * 1.25) / 100) * 100;
        charts.HB.options.scales.x.min = hb.T[0];
        charts.HB.options.scales.x.max = hb.T[hb.T.length - 1];
        charts.HB.update("none");
      },

      redraw() {
        // Throttle: maximaal ~20 keer per seconde tekenen.
        if (this._rafPending) return;
        this._rafPending = true;
        requestAnimationFrame(() => { this._rafPending = false; this.drawNow(); });
      },

      drawNow() {
        if (!charts.T) return;
        const last = series.model[series.model.length - 1];
        const tNow = last ? last.t : 0;
        const predEnd = series.pred.length ? series.pred[series.pred.length - 1].t : tNow;
        const tMax = Math.max(tNow, predEnd);
        const tMin = this.windowMin > 0 ? Math.max(0, tNow - this.windowMin) : (series.model.length ? series.model[0].t : 0);
        const inWin = (p) => p.t >= tMin;
        const pick = (arr, key) => arr.filter((p) => inWin(p) && p[key] != null).map((p) => ({ x: p.t, y: p[key] }));

        for (const key of ["T", "CA"]) {
          const c = charts[key];
          c.data.datasets[0].data = pick(series.model, key);
          c.data.datasets[1].data = pick(series.meas, key);
          c.data.datasets[2].data = pick(series.pred, key);
          c.options.scales.x.min = tMin;
          c.options.scales.x.max = Math.max(tMax, tMin + 0.1);
        }
        // Veiligheidsgrenzen als horizontale lijnen in de T-grafiek
        const tr = charts.T.data.datasets;
        const xEnd = Math.max(tMax, tMin + 0.1);
        const jacket = this.params.process && this.params.process.cooling_mode === "jacket";
        tr[5].data = jacket ? pick(series.model, "Tc") : [];
        tr[6].data = jacket ? pick(series.pred, "Tc") : [];
        const c = this.params.control;
        tr[7].data = c && c.auto && jacket ? [{ x: tMin, y: c.T_sp }, { x: xEnd, y: c.T_sp }] : [];
        const s = this.params.safety;
        if (s) {
          tr[3].data = [{ x: tMin, y: s.T_warn }, { x: xEnd, y: s.T_warn }];
          tr[4].data = [{ x: tMin, y: s.T_trip }, { x: xEnd, y: s.T_trip }];
          // Toon de grenzen alleen als de temperatuur er in de buurt komt (anders wordt de schaal onleesbaar)
          const Tvals = charts.T.data.datasets[0].data.concat(charts.T.data.datasets[2].data).map((p) => p.y);
          const Tmax = Tvals.length ? Math.max(...Tvals) : 0;
          tr[3].hidden = Tmax < s.T_warn - 60;
          tr[4].hidden = Tmax < s.T_trip - 60;
        }
        charts.T.update("none");
        charts.CA.update("none");
      },
    },
  });

  app.mount("#app");
})();
