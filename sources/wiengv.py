"""Stadt Wien event calendar ("Wien erleben").

`/veranstaltungen.json` is a GeoJSON collection of every city event (title,
description, url, image) but without dates. Candidates are pre-selected by
keyword and then confirmed on their detail page, whose schema.org JSON-LD carries
the "Für Kinder" flag, the address, prices and every occurrence in `subEvent`.
"""
import json
import os
import re

from . import common as c

LABEL = "wien.gv.at"
LIST_URL = "https://www.wien.gv.at/veranstaltungen.json"
MAX_DETAILS = int(os.environ.get("WIENGV_MAX_DETAILS", "140"))

# Recall-oriented pre-filter; the "Für Kinder" flag on the detail page decides.
KEYWORDS = (
    "kind", "famili", "kasperl", "puppen", "märchen", "baby", "kleinkind",
    "bastel", "mitmach", "spielfest", "ferienspiel", "nikolo", "advent",
    "weihnachtsmarkt", "christkindlmarkt", "zirkus", "clown",
)


def _candidates():
    resp = c.get(LIST_URL)
    resp.raise_for_status()
    features = resp.json().get("features") or []
    out = []
    for feature in features:
        props = feature.get("properties") or {}
        url = props.get("url") or ""
        if not url:
            continue
        blob = f"{props.get('title', '')} {props.get('description', '')} {url}".lower()
        if any(word in blob for word in KEYWORDS):
            out.append(props)
    print(f"  {LABEL}: {len(features)} city events, {len(out)} kids candidates")
    return out[:MAX_DETAILS]


def _event_ld(html):
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                            html, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Event":
            return data
    return None


def _is_for_kids(data):
    for prop in data.get("additionalProperty") or []:
        name = (prop.get("name") or "").lower()
        if "für kinder" in name and prop.get("value"):
            return True
    return False


def _category(data):
    """Pick a human category from the additionalProperty flags."""
    skip = {"für kinder", "barrierefrei", "eintritt frei"}
    for prop in data.get("additionalProperty") or []:
        name = c.clean(prop.get("name") or "")
        if not prop.get("value") or "[" in name or name.lower() in skip:
            continue
        return name
    return "Wien erleben"


def _price(data):
    offers = data.get("offers") or []
    prices = [o.get("price") for o in offers if isinstance(o.get("price"), (int, float))]
    if not prices:
        return ""
    low = min(prices)
    if low == 0:
        return "Gratis"
    return f"ab {low:.2f}".replace(".", ",").replace(",00", "") + " €"


def fetch(now, window_end):
    events = []
    for props in _candidates():
        try:
            resp = c.get(props["url"])
        except Exception as exc:                            # noqa: BLE001
            print(f"    {LABEL}: {props.get('title', '?')}: {exc}")
            continue
        c.polite()
        if not resp.ok:
            continue
        data = _event_ld(resp.text)
        if not data or not _is_for_kids(data):
            continue

        occurrences = [c.occurrence(c.parse_iso(sub.get("startDate")))
                       for sub in data.get("subEvent") or []]
        dates = c.sort_dates(occurrences, now, window_end)
        if not dates:
            continue

        address = (data.get("addresses") or [{}])[0]
        district = c.clean(address.get("postalCode") or "")
        locality = c.clean(address.get("addressLocality") or "")
        if district and locality not in ("Vienna", "Wien", ""):
            district = f"{district} {locality}"
        # markets and open-air events often have no venue name, only a street
        venue = c.clean(address.get("name") or address.get("street") or "") or "Wien"

        events.append(c.event(
            source=LABEL,
            title=data.get("name") or props.get("title", ""),
            dates=dates,
            venue=venue,
            district=district,
            category=_category(data),
            min_age=c.parse_min_age(data.get("description"), props.get("description")),
            image=props.get("image0") or "",
            desc=data.get("description") or props.get("description", ""),
            price=_price(data),
            url=props["url"],
        ))
    print(f"  {LABEL}: {len(events)} kids events in window")
    return events
