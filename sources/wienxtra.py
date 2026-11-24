"""WienXtra Kinderinfo — curated kids offers, served as static Bootstrap cards.

Each card carries an image, a <time datetime> for the next date, the venue with its
district in the subtitle, and badges such as "ab 3 Jahre", "Gratis" or
"Anmeldung erforderlich".
"""
import re

from . import common as c

LABEL = "WienXtra"
URL = "https://wienxtra.at/kinder/veranstaltungen/"
BASE = "https://wienxtra.at"


def _district(subtitle):
    """"WIENXTRA-Kinderinfo, 1070" -> ("WIENXTRA-Kinderinfo", "1070")."""
    text = c.clean(subtitle)
    m = re.search(r",\s*(1\d{3})\s*$", text)
    if m:
        return text[:m.start()].strip(" ,"), m.group(1)
    return text, ""


def fetch(now, window_end):
    resp = c.get(URL)
    resp.raise_for_status()
    soup = c.soup_of(resp.text)

    events = []
    for card in soup.select("div.card-body"):
        title = c.get_text(card.select_one("h2.card-title"))
        if not title:
            continue
        venue, district = _district(c.get_text(card.select_one("h3.card-subtitle")))

        time_el = card.select_one("time[datetime]")
        moment = c.parse_iso(time_el.get("datetime")) if time_el else None
        dates = c.sort_dates([c.occurrence(moment)], now, window_end)
        if not dates:
            continue
        # "Do, 26.11. bis Di, 29.12., 9 Termine" -> hint that more dates follow
        schedule = c.get_text(time_el)
        m = re.search(r"(\d+)\s+Termine", schedule)
        series = f"{m.group(1)} Termine" if m else ""

        badges = [c.get_text(b) for b in card.select("span.badge")]
        free = any(b.lower() == "gratis" for b in badges)
        min_age = c.parse_min_age(*badges, title)

        link = card.find_parent("div", class_="card")
        href = ""
        if link:
            anchor = link.select_one("a.stretched-link, a[href]")
            href = anchor.get("href", "") if anchor else ""
        if href.startswith("/"):
            href = BASE + href

        record = c.event(
            source=LABEL,
            title=title,
            dates=dates,
            venue=venue,
            district=district,
            category="Kinder",
            min_age=min_age,
            image=_image(card),
            desc=c.get_text(card.select_one("div.description p")),
            price="Gratis" if free else "",
            url=href,
        )
        if series:
            record["x"] = series
        events.append(record)
    print(f"  {LABEL}: {len(events)} events in window")
    return events


def _image(card):
    figure = card.find_previous("figure")
    if not figure:
        return ""
    img = figure.select_one("img[src]")
    return img.get("src", "") if img else ""
