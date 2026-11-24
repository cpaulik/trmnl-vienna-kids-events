#!/usr/bin/env python3
"""
Dev helper: render template.html with a real selection from the local cache
into preview.html, so the layout can be checked in a browser before pushing
anything to TRMNL.

Needs the Liquid renderer that TRMNL uses:  uv pip install python-liquid
Usage:  python preview.py [--seed 7]
"""
import argparse
import json
import random
from datetime import date
from pathlib import Path

from liquid import Environment

import scraper

HERE = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", default=str(HERE / "preview.html"))
    args = ap.parse_args()
    if args.seed is not None:
        random.seed(args.seed)

    cache = json.loads((HERE / "events_cache.json").read_text())
    pool = cache["events"]
    selection = scraper.pick(pool, scraper.DISPLAY_COUNT)
    payload, size = scraper.build_payload(selection, date.today())
    print(f"rendering {len(selection)} of {len(pool)} cached events ({size} bytes payload)")

    html = Environment().render((HERE / "template.html").read_text(),
                                payload["merge_variables"])
    Path(args.out).write_text(html)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
