#!/usr/bin/env python3
"""
Dev helper: render template.html with real cached data into preview.html, so the
layout can be checked in a browser before pushing anything to TRMNL.

Needs the Liquid renderer that TRMNL uses:  uv pip install python-liquid

Usage:
  python preview.py                       # 3 random samples
  python preview.py --samples 4 --seed 7
  python preview.py --sources TMW,Umweltspürnasen   # force the no-photo case
  python preview.py --serve 8080          # serve the result over HTTP
"""
import argparse
import json
import random
from datetime import date
from pathlib import Path

from liquid import Environment

import scraper

HERE = Path(__file__).parent

PAGE = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Kinder Wien TRMNL - Layout-Vorschau</title>
<style>
  body {{ margin: 0; padding: 24px; background: #2b2b2b; color: #eee;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  h1 {{ font-size: 18px; font-weight: 600; margin: 0 0 4px; }}
  p.note {{ font-size: 13px; color: #aaa; margin: 0 0 20px; }}
  .frames {{ display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start; }}
  .frame {{ width: {fw}px; height: {fh}px; overflow: hidden;
            box-shadow: 0 6px 24px rgba(0,0,0,.5); background: #fff; }}
  .frame > .scaler {{ transform: scale({scale}); transform-origin: top left; }}
  .caption {{ font-size: 12px; color: #999; margin: 6px 0 0; }}
  .shot {{ display: flex; flex-direction: column; }}
</style>
</head>
<body>
<h1>Kinder Wien &mdash; TRMNL 1030&times;1404, {n} Varianten</h1>
<p class="note">Jeder Rahmen ist ein echter Render des Plugin-Templates, auf {pct}% skaliert.
Auf dem Gerät erscheint das Ganze in Graustufen-E-Ink.</p>
<div class="frames">
{shots}
</div>
</body>
</html>
"""

SHOT = """<div class="shot">
  <div class="frame"><div class="scaler">{html}</div></div>
  <p class="caption">{caption}</p>
</div>"""


def render(pool, env, template_src, today):
    selection = scraper.pick(pool, scraper.DISPLAY_COUNT)
    payload, size = scraper.build_payload(selection, today)
    html = env.render(template_src, payload["merge_variables"])
    photos = sum(1 for e in payload["merge_variables"]["events"] if e.get("p"))
    caption = (f"{size} Bytes &middot; {photos}/{len(selection)} mit Foto &middot; "
               + ", ".join(sorted({e.get("s", "?") for e in payload["merge_variables"]["events"]})))
    return html, caption, size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--sources", help="only pick events from these sources (LABEL names)")
    ap.add_argument("--with-nophoto", action="store_true",
                    help="append a frame built only from events without a photo")
    ap.add_argument("--serve", type=int, metavar="PORT", help="serve the preview over HTTP")
    ap.add_argument("--out", default=str(HERE / "preview.html"))
    args = ap.parse_args()
    if args.seed is not None:
        random.seed(args.seed)

    cache = json.loads((HERE / "events_cache.json").read_text())
    pool = cache["events"]
    if args.sources:
        wanted = {s.strip().lower() for s in args.sources.split(",")}
        pool = [e for e in pool if e.get("s", "").lower() in wanted]
        if not pool:
            raise SystemExit(f"no cached events from {args.sources}")
    print(f"pool: {len(pool)} events (cache date {cache['date']})")

    env = Environment()
    template_src = (HERE / "template.html").read_text()
    today = date.today()

    shots = []
    for i in range(args.samples):
        html, caption, size = render(pool, env, template_src, today)
        shots.append(SHOT.format(html=html, caption=f"#{i + 1} &middot; {caption}"))
        print(f"  sample {i + 1}: {size} bytes")

    if args.with_nophoto:
        bare = [e for e in cache["events"] if not e.get("p")]
        if bare:
            html, caption, size = render(bare, env, template_src, today)
            shots.append(SHOT.format(html=html, caption=f"ohne Foto &middot; {caption}"))
            print(f"  no-photo sample: {size} bytes")

    page = PAGE.format(
        fw=int(1030 * args.scale), fh=int(1404 * args.scale), scale=args.scale,
        pct=int(args.scale * 100), n=len(shots), shots="\n".join(shots),
    )
    out = Path(args.out)
    out.write_text(page)
    print(f"wrote {out}")

    if args.serve:
        import functools
        import http.server
        import socketserver

        handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                    directory=str(out.parent))
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("0.0.0.0", args.serve), handler) as httpd:
            print(f"serving {out.name} on port {args.serve} (ctrl-c to stop)")
            httpd.serve_forever()


if __name__ == "__main__":
    main()
