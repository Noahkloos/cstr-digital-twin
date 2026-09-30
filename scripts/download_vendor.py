"""
Download de frontend-bibliotheken naar static/vendor/ zodat het dashboard
zonder internet werkt (bv. op een afgeschermd procesnetwerk).

    python scripts/download_vendor.py

Zonder deze bestanden valt index.html automatisch terug op de CDN.
"""

import sys
import urllib.request
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent.parent / "static" / "vendor"

FILES = {
    "vue.global.prod.js": "https://cdn.jsdelivr.net/npm/vue@3.4.38/dist/vue.global.prod.js",
    "chart.umd.js": "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.js",
}


def main() -> int:
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        target = VENDOR_DIR / name
        print(f"Downloaden {url} -> {target}")
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        target.write_bytes(data)
        print(f"  OK ({len(data) / 1024:.0f} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
