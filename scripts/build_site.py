"""Assemble the project website into _site/ (published by the "Website" GitHub Actions workflow).

    python scripts/build_site.py            # then open _site/index.html, or:
    python -m http.server -d _site 8000     # and visit http://localhost:8000

The page itself lives in site/. This adds the README screenshots, the app icon, the bundled
Leaflet map library, and the list of famous places for the demo.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from places import PLACES  # noqa: E402


def main() -> None:
    out = os.path.join(ROOT, "_site")
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "site"), out)
    shutil.copytree(os.path.join(ROOT, "docs", "screenshots"), os.path.join(out, "screenshots"))
    shutil.copytree(os.path.join(ROOT, "static", "vendor", "leaflet"), os.path.join(out, "vendor", "leaflet"))
    shutil.copy(os.path.join(ROOT, "static", "icon.png"), os.path.join(out, "icon.png"))
    with open(os.path.join(out, "places.json"), "w", encoding="utf-8") as f:
        json.dump(PLACES, f, ensure_ascii=False)
    open(os.path.join(out, ".nojekyll"), "w").close()  # serve files as-is
    print(f"Built {out}")


if __name__ == "__main__":
    main()
