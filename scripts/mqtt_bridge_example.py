"""
VOORBEELD: koppel een MQTT-datastroom (bv. vanuit een PLC/edge-gateway)
aan de digital twin. Elk MQTT-bericht wordt doorgestuurd naar POST /api/twin.

Vereist:  pip install paho-mqtt==2.1.0

    python scripts/mqtt_bridge_example.py --broker 192.168.1.10 --topic plant/r101/measurements

Verwacht berichtformaat (JSON), bv.:  {"CA": 0.5, "T": 350, "Q": -5000}
Gebruikt je installatie andere tagnamen, pas dan TAG_MAP hieronder aan.
"""

import argparse
import json
import urllib.error
import urllib.request

import paho.mqtt.client as mqtt  # type: ignore

# Tagnaam in de installatie  ->  veldnaam in de twin-API
TAG_MAP = {
    "CA": "CA", "T": "T", "Q": "Q", "F": "F", "T_in": "T_in", "CA_in": "CA_in",
    # voorbeelden van vertalingen:
    "TI101.PV": "T",        # temperatuurtransmitter
    "AI101.PV": "CA",       # online analyser
    "QC101.OUT": "Q",       # koelvermogen
}


def forward(api_url: str, api_key: str, payload: dict) -> None:
    """Stuur één meting door naar de twin-API."""
    body = {TAG_MAP[k]: float(v) for k, v in payload.items() if k in TAG_MAP}
    if not body:
        return
    req = urllib.request.Request(api_url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    if api_key:
        req.add_header("X-API-Key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
            if result.get("failsafe", {}).get("tripped"):
                print("*** FAILSAFE TRIP gemeld door de twin:", result["failsafe"]["reason"])
    except urllib.error.URLError as exc:
        print("Doorsturen mislukt:", exc)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--topic", default="plant/r101/measurements")
    ap.add_argument("--api", default="http://127.0.0.1:8000/api/twin")
    ap.add_argument("--api-key", default="")
    args = ap.parse_args()

    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"Verbonden met {args.broker} ({reason_code}); abonneren op {args.topic}")
        client.subscribe(args.topic)

    def on_message(client, userdata, msg):
        try:
            forward(args.api, args.api_key, json.loads(msg.payload))
        except (ValueError, TypeError) as exc:
            print("Ongeldig bericht genegeerd:", exc)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker, args.port)
    client.loop_forever()


if __name__ == "__main__":
    main()
