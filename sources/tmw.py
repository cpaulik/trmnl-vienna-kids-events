"""Technisches Museum Wien programme.

The programme page embeds one schema.org JSON-LD Event per teaser, including the
target audience ("Kinder & Familie"), the address and the start time. The calendar
page itself is rendered client-side, so the static programme page is the source.
"""
import json
import re

from . import common as c

LABEL = "TMW"
URL = "https://www.technischesmuseum.at/veranstaltungen"
BASE = "https://www.technischesmuseum.at"
KIDS_AUDIENCE = ("kinder", "famili")


def _price(raw):
    """"2.5" -> "2,50 €"; empty or unparsable -> ""."""
    text = c.clean(str(raw or "")).replace(",", ".")
    if not text:
        return ""
    try:
        value = float(text)
    except ValueError:
        return ""
    if value == 0:
        return "Gratis"
    return f"{value:.2f} €".replace(".", ",")


def _audience(data):
    audience = data.get("audience") or {}
    if isinstance(audience, list):
        audience = audience[0] if audience else {}
    return c.clean(audience.get("name") or "")


def fetch(now, window_end):
    resp = c.get(URL)
    resp.raise_for_status()
    html = resp.text
    soup = c.soup_of(html)

    # map event name -> teaser link and category, read from the surrounding markup
    links, categories = {}, {}
    for teaser in soup.select("div.veranstaltung.teaser"):
        heading = teaser.select_one("h3 a[href]")
        if not heading:
            continue
        name = c.get_text(heading)
        links[name] = BASE + heading["href"] if heading["href"].startswith("/") else heading["href"]
        categories[name] = c.get_text(teaser.select_one("div.kategorie"))

    seen, events = set(), []
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                            html, re.S):
        try:
            data = json.loads(block.replace("&amp;", "&"))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or data.get("@type") != "Event":
            continue
        audience = _audience(data)
        if audience and not any(word in audience.lower() for word in KIDS_AUDIENCE):
            continue

        name = c.clean(data.get("name") or "")
        moment = c.parse_iso(data.get("startDate") or "")
        dates = c.sort_dates([c.occurrence(moment)], now, window_end)
        if not name or not dates:
            continue
        key = (name, dates[0]["i"])
        if key in seen:
            continue
        seen.add(key)

        place = data.get("location") or {}
        address = place.get("address") or {}
        offer = data.get("offers") or {}
        price = _price(offer.get("price") if isinstance(offer, dict) else "")

        events.append(c.event(
            source=LABEL,
            title=name,
            dates=dates,
            venue="Technisches Museum",
            district=c.clean(address.get("postalCode") or "1140"),
            category=categories.get(name) or "Kinder & Familie",
            min_age=c.parse_min_age(data.get("description")),
            desc=data.get("description") or "",
            price=price,
            url=links.get(name, URL),
        ))
    print(f"  {LABEL}: {len(events)} kids events in window")
    return events
