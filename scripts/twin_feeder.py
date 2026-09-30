"""
Twin-feeder: speelt een CSV-dataset af als "live" datastroom naar POST /api/twin.
Dit is tevens een referentie-implementatie voor een echte koppeling
(OPC UA / MQTT / historian -> HTTP POST). Alleen standaardbibliotheek.

Voorbeelden
-----------
    # normaal bedrijf, 1 meting per seconde, twin-modus automatisch aan
    python scripts/twin_feeder.py data/testdata_normal.csv --enable-twin

    # koelingsuitval, 10x versneld: twin schat UA online uit Tc en Q
    python scripts/twin_feeder.py data/testdata_cooling_failure.csv --enable-twin --interval 0.1

    # koude opstart met ophoping -> runaway > 600 K (failsafe tript op de meting)
    python scripts/twin_feeder.py data/testdata_runaway.csv --enable-twin --interval 0.1

    # alleen T en Q sturen (geen analyser, geen Tc): twin leidt Tc af uit Q
    python scripts/twin_feeder.py data/testdata_normal.csv --enable-twin --fields T,Q --api-key geheim
"""

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request

PROCESS_FIELDS = ("CA", "T", "Tc", "Q", "UA", "F", "T_in", "CA_in")


def post_json(url, payload, api_key=None, timeout=5.0):
    """POST JSON en geef (statuscode, antwoord-dict) terug."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    if api_key:
        req.add_header("X-API-Key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Speel een CSV af naar de CSTR digital twin.")
    ap.add_argument("file", help="CSV met kolommen zoals CA, T, Tc, Q, F, T_in, CA_in")
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="Basis-URL van de app")
    ap.add_argument("--interval", type=float, default=1.0, help="Seconden tussen metingen (standaard 1.0)")
    ap.add_argument("--fields", default=",".join(PROCESS_FIELDS), help="Welke kolommen versturen (komma-gescheiden)")
    ap.add_argument("--api-key", default=None, help="Waarde voor de X-API-Key header")
    ap.add_argument("--enable-twin", action="store_true",
                    help="Zet eerst de twin-modus aan en laat de modelklok gelijklopen met --interval")
    ap.add_argument("--blend", type=float, default=None, help="Blend-factor bij --enable-twin (0-1]")
    ap.add_argument("--loop", action="store_true", help="Dataset eindeloos herhalen")
    args = ap.parse_args()

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    unknown = set(fields) - set(PROCESS_FIELDS)
    if unknown:
        print(f"Onbekende velden: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    with open(args.file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("Lege dataset.", file=sys.stderr)
        return 1

    base = args.url.rstrip("/")
    if args.enable_twin:
        body = {"enabled": True}
        if args.blend is not None:
            body["blend"] = args.blend
        status, resp = post_json(base + "/api/twin/mode", body)
        print(f"Twin-modus: HTTP {status} {resp}")
        # Modelklok gelijk laten lopen met de dataset (1 rij = 1 s procestijd).
        # Bij --interval 1.0 is dat gewoon echte tijd; bij versneld afspelen
        # rekent het model evenveel sneller.
        if abs(args.interval - 1.0) < 1e-9:
            runtime = {"twin_realtime": True}
        else:
            runtime = {"twin_realtime": False, "speed": round((1.0 / 60.0) / args.interval, 6)}
        req = urllib.request.Request(
            base + "/api/parameters", data=json.dumps({"runtime": runtime}).encode("utf-8"),
            method="PUT", headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5):
                print(f"Modelklok ingesteld: {runtime}")
        except urllib.error.HTTPError as exc:
            print(f"Modelklok niet ingesteld: HTTP {exc.code} {exc.read()!r}")

    print(f"{len(rows)} metingen naar {base}/api/twin, elke {args.interval} s. Ctrl+C om te stoppen.")
    try:
        while True:
            for i, row in enumerate(rows):
                payload = {k: float(row[k]) for k in fields if row.get(k) not in (None, "")}
                t0 = time.time()
                try:
                    status, resp = post_json(base + "/api/twin", payload, args.api_key)
                except urllib.error.URLError as exc:
                    print(f"[{i:4d}] verbinding mislukt: {exc.reason} - nieuwe poging volgt")
                    time.sleep(args.interval)
                    continue
                if status != 200:
                    print(f"[{i:4d}] HTTP {status}: {resp}")
                else:
                    fs = resp.get("failsafe", {})
                    res = resp.get("residual", {})
                    flag = "  *** TRIP ***" if fs.get("tripped") else ""
                    print(f"[{i:4d}] T={payload.get('T', '-'):>8}  CA={payload.get('CA', '-'):>8}  "
                          f"dT={res.get('T', 0):+7.2f}  alarm={fs.get('alarm')}{flag}")
                time.sleep(max(0.0, args.interval - (time.time() - t0)))
            if not args.loop:
                break
    except KeyboardInterrupt:
        print("\nGestopt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
