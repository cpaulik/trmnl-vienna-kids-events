"""Falter.at "4 Kids" event listings for Vienna.

The search page lists one card per occurrence; each event's detail page carries the
description, the age requirement, the venue address and all upcoming dates as
schema.org microdata.
"""
import os
import re

from . import common as c

LABEL = "Falter"
BASE = "https://www.falter.at"
PAGES = int(os.environ.get("FALTER_PAGES", "25"))
RECOMMENDED_FIRST = os.environ.get("FALTER_RECOMMENDED_FIRST", "true").lower() in ("1", "true", "yes")
MIN_RECOMMENDED = int(os.environ.get("FALTER_MIN_RECOMMENDED", "12"))


def _parse_listing(html):
    """Return [{id, url, t, v, g, p}] for one search result page."""
    soup = c.soup_of(html)
    out = []
    for a in soup.select("a.group.block"):
        href = a.get("href", "")
        m = re.search(r"/event/(\d+)/", href)
        if not m:
            continue
        title = c.get_text(a.select_one("h2"))
        if not title:
            continue
        divs = a.select("div.font-display")
        tags = [t for t in (c.get_text(li) for li in a.select("li")) if t]
        tags = [t for t in tags if t.lower() != "empfehlung"]
        poster = ""
        img = a.select_one('img[src*="/events/"]')
        if img:
            poster = re.sub(r"\?width=\d+", "?width=300", img.get("src", ""))
        out.append({
            "id": m.group(1),
            "url": href if href.startswith("http") else BASE + href,
            "t": title,
            "v": c.get_text(divs[0]) if divs else "",
            "g": " / ".join(tags[:2]),
            "p": poster,
        })
    return out


def _scrape_listing(span, recommended):
    seen, events = set(), []
    for page in range(1, PAGES + 1):
        params = {"region": c.REGION, "is_4kids": "true", "span": span, "page": page}
        if recommended:
            params["is_recommended"] = "true"
        resp = c.get(f"{BASE}/events/suche", params=params)
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        cards = _parse_listing(resp.text)
        if not cards:
            break
        new = 0
        for card in cards:
            if card["id"] in seen:
                continue
            seen.add(card["id"])
            events.append(card)
            new += 1
        if new == 0 and page > 1:
            break
        c.polite()
    return events


def _parse_detail(html, now, window_end):
    soup = c.soup_of(html)

    description = c.get_text(soup.select_one("div.prose p"))
    if not description:
        meta = soup.select_one("meta[name=description]")
        description = (meta.get("content") or "").strip() if meta else ""

    # "Details" section: label/value pairs such as Kategorie, Info, Preis
    details = {}
    section = soup.select_one('section[aria-label="Details"]')
    if section:
        for block in section.select("div > div"):
            label_el = block.select_one("div span")
            value_divs = block.select("div")
            if label_el and len(value_divs) >= 2:
                label, value = c.get_text(label_el), c.get_text(value_divs[1])
                if label and value and label != value:
                    details.setdefault(label, value)

    dates, venues, districts = [], [], []
    for table in soup.select("table"):
        th = table.select_one("th")
        venue = c.get_text(th.select_one('[itemprop="name"]')) if th else ""
        venue = venue or c.get_text(th)
        if venue:
            venues.append(venue)
        zip_meta = table.select_one('meta[itemprop="postalCode"]')
        city_meta = table.select_one('meta[itemprop="addressLocality"]')
        code = (zip_meta.get("content") or "").strip() if zip_meta else ""
        city = (city_meta.get("content") or "").strip() if city_meta else ""
        if code:
            # Vienna districts are obvious from the postal code; elsewhere name the town
            districts.append(code if city == "Wien" else f"{code} {city}".strip())
        for row in table.select("tr"):
            for start in row.select('time[itemprop="startDate"]'):
                moment = c.parse_iso(start.get("datetime") or "")
                if moment is None:
                    continue
                clock = re.sub(r"\s*[–—-]\s*", "–", c.get_text(start))
                dates.append(c.occurrence(moment, clock))

    return {
        "desc": description,
        "info": details.get("Info", ""),
        "cat": details.get("Kategorie", ""),
        "price": details.get("Preis", ""),
        "venue": venues[0] if venues else "",
        "district": districts[0] if districts else "",
        "dates": c.sort_dates(dates, now, window_end),
    }


def _collect(cards, now, window_end, recommended, out, seen_ids):
    for card in cards:
        if card["id"] in seen_ids:
            continue
        seen_ids.add(card["id"])
        try:
            resp = c.get(card["url"])
        except Exception as exc:                            # noqa: BLE001 - per-event resilience
            print(f"    {LABEL}: {card['t']}: {exc}")
            continue
        if not resp.ok:
            continue
        detail = _parse_detail(resp.text, now, window_end)
        c.polite()
        if not detail["dates"]:
            continue
        out.append(c.event(
            source=LABEL,
            title=card["t"],
            dates=detail["dates"],
            venue=detail["venue"] or card["v"],
            district=detail["district"],
            category=detail["cat"] or card["g"],
            min_age=c.parse_min_age(detail["info"], detail["desc"]),
            image=card["p"],
            desc=detail["desc"],
            price=detail["price"],
            recommended=recommended,
            url=card["url"],
        ))


def fetch(now, window_end):
    span = f"{now.date()}:{window_end}"
    events, seen_ids = [], set()

    if RECOMMENDED_FIRST:
        cards = _scrape_listing(span, recommended=True)
        print(f"  {LABEL}: {len(cards)} recommended events, fetching details...")
        _collect(cards, now, window_end, True, events, seen_ids)

    # count what survives the age gate, otherwise a week of "ab 6 J." theatre
    # recommendations would leave almost nothing to rotate through
    usable = sum(1 for e in events if c.age_ok(e.get("age")))
    if usable < MIN_RECOMMENDED:
        cards = _scrape_listing(span, recommended=False)
        print(f"  {LABEL}: only {usable} age-appropriate, widening to "
              f"{len(cards)} events in full listing...")
        _collect(cards, now, window_end, False, events, seen_ids)

    return events
