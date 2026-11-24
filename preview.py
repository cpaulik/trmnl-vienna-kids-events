#!/usr/bin/env python3
"""
Dev helper: render template.html with real cached data into preview.html, so the
layout can be checked in a browser before pushing anything to TRMNL.

Needs the Liquid renderer that TRMNL uses:  uv pip install python-liquid

Device frames (landscape is the TRMNL default). Sizes are CSS pixels, which is
what the markup renders at; the panel is pixel-ratio times larger:
  og  800x480   TRMNL OG, 1-bit, ratio 1.0
  x   1040x780  TRMNL X, 4-bit, ratio 1.8 -> 1872x1404 physical panel

Usage:
  python preview.py                                  # X frame, DISPLAY_COUNT events
  python preview.py --device x --events 6
  python preview.py --device og,x --events 4,6       # compare frames and densities
  python preview.py --sources TMW --with-nophoto
  python preview.py --serve 8137
"""
import argparse
import json
import random
from datetime import date, datetime, timezone
from pathlib import Path

from liquid import Environment

import scraper

HERE = Path(__file__).parent

DEVICES = {
    "og": (800, 480, "TRMNL OG — 800×480 CSS px, 1-bit"),
    "x": (1040, 780,
          "TRMNL X — 1040×780 CSS px, ×1.8 = 1872×1404 Panel, 4-bit"),
    "v2": (1040, 780, "TRMNL V2 — 1040×780 CSS px, 4-bit"),
}

PAGE = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Kinder Wien TRMNL - Layout-Vorschau</title>
<style>
  body { margin: 0; padding: 24px; background: #2b2b2b; color: #eee;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  h1 { font-size: 18px; font-weight: 600; margin: 0 0 4px; }
  p.note { font-size: 13px; color: #aaa; margin: 0 0 20px; max-width: 900px; }
  h2 { font-size: 14px; font-weight: 600; color: #ddd; margin: 26px 0 10px;
       border-bottom: 1px solid #444; padding-bottom: 6px; }
  .frames { display: flex; flex-wrap: wrap; gap: 20px; align-items: flex-start; }
  .shot { display: flex; flex-direction: column; }
  .frame { overflow: hidden; background: #fff;
           box-shadow: 0 6px 24px rgba(0,0,0,.5); }
  .caption { font-size: 12px; color: #999; margin: 6px 0 0; }
</style>
</head>
<body>
<h1>Kinder Wien &mdash; Layout-Vorschau</h1>
<p class="note">Echte Renders des Plugin-Templates, 1:1 in Gerätegröße. Das Template ist
fluid (kein fixes Format) und misst alles in Container-Einheiten, deshalb füllt
dasselbe Markup jedes Gerät. Der X skaliert diese 1040&times;780 mit Faktor 1,8 auf
sein 1872&times;1404-Panel; auf dem Gerät sind es 16 Graustufen.</p>
GROUPS
</body>
</html>
"""

GROUP = """<h2>{heading}</h2>
<div class="frames">
{shots}
</div>"""

SHOT = """<div class="shot">
  <div class="frame" style="width:{w}px;height:{h}px">{html}</div>
  <p class="caption">{caption}</p>
</div>"""


def render(pool, env, template_src, today, count, timestamp=None):
    selection = scraper.pick(pool, count * scraper.ROTATION_GROUPS)
    payload, size = scraper.build_payload(selection, today, per_screen=count)
    return render_payload(payload, env, template_src, timestamp) + (size,)


def render_payload(payload, env, template_src, timestamp=None):
    """Render one payload, optionally pinned to a given render clock."""
    context = dict(payload["merge_variables"])
    if timestamp is not None:
        context["trmnl"] = {"system": {"timestamp_utc": timestamp}}
    html = env.render(template_src, context)
    events = payload["merge_variables"]["events"]
    shown = html.count('class="cell"')
    photos = sum(1 for e in events if e.get("p"))
    caption = (f"{shown} von {len(events)} Events &middot; {photos} mit Foto &middot; "
               + ", ".join(sorted({e.get("s", "?") for e in events})))
    return html, caption


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--device", default="x", help="x, og, v2 or a comma-separated list")
    ap.add_argument("--events", default=None,
                    help="events per screen; comma-separated list to compare")
    ap.add_argument("--sources", help="only pick events from these sources (LABEL names)")
    ap.add_argument("--with-nophoto", action="store_true",
                    help="append a frame built only from events without a photo")
    ap.add_argument("--slots", type=int, default=0,
                    help="render one payload at N successive rotation slots")
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

    devices = [d.strip().lower() for d in args.device.split(",") if d.strip()]
    counts = ([int(n) for n in args.events.split(",")] if args.events
              else [scraper.DISPLAY_COUNT])

    groups = []
    for device in devices:
        if device not in DEVICES:
            raise SystemExit(f"unknown device '{device}', known: {', '.join(DEVICES)}")
        w, h, label = DEVICES[device]
        for count in counts:
            shots = []
            if args.slots:
                # one payload, rendered at successive render clocks: this is what
                # the device does between pushes
                selection = scraper.pick(pool, count * scraper.ROTATION_GROUPS)
                payload, size = scraper.build_payload(selection, today, per_screen=count)
                mv = payload["merge_variables"]
                screens = max(1, len(mv["events"]) // count)
                print(f"  {device}: one payload, {size} bytes, {len(mv['events'])} events "
                      f"= {screens} screens, rotating every {mv['rotate']}s")
                base = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
                for i in range(args.slots):
                    ts = base + i * mv["rotate"]
                    html, caption = render_payload(payload, env, template_src, ts)
                    label = f"Slot {i + 1} von {screens} &middot; {caption}"
                    shots.append(SHOT.format(w=w, h=h, html=html, caption=label))
            else:
                for i in range(args.samples):
                    html, caption, size = render(pool, env, template_src, today, count)
                    shots.append(SHOT.format(w=w, h=h, html=html,
                                             caption=f"#{i + 1} &middot; {caption}"))
                    print(f"  {device} / {count} events / sample {i + 1}: {size} bytes")
            if args.with_nophoto:
                bare = [e for e in cache["events"] if not e.get("p")]
                if bare:
                    html, caption, size = render(bare, env, template_src, today, count)
                    _ = size
                    shots.append(SHOT.format(w=w, h=h, html=html,
                                             caption=f"ohne Foto &middot; {caption}"))
                    print(f"  {device} / {count} events / no-photo: {size} bytes")
            groups.append(GROUP.format(
                heading=f"{label} &mdash; {count} Events pro Screen",
                shots="\n".join(shots)))

    out = Path(args.out)
    out.write_text(PAGE.replace("GROUPS", "\n".join(groups)))
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
