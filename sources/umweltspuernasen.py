"""Umweltspürnasen — nature exploration events for kids in and around Vienna.

The site exposes a WordPress AJAX endpoint that needs a nonce first. Events carry
their age group in the category name, e.g. "Kleinchenaktion (3-6 Jährige)".
Day and month come without a year, so the year is inferred from the window.
"""
import os
import re
from datetime import datetime

from . import common as c

LABEL = "Umweltspürnasen"
AJAX = "https://umweltspuernasen.at/wp-admin/admin-ajax.php"
PER_PAGE = int(os.environ.get("USN_PER_PAGE", "50"))
MAX_PAGES = int(os.environ.get("USN_PAGES", "4"))
# School programmes, not open family events
SKIP_CATEGORY_WORDS = {"bildungschancen"}


def _nonce():
    resp = c.post(AJAX, data={"action": "get_event_list_nonce"})
    resp.raise_for_status()
    return resp.json()["data"]["nonce"]


def _page(nonce, page):
    resp = c.post(AJAX, data={
        "action": "wbep_load_event_list",
        "nonce": nonce,
        "wbep_page": page,
        "wbep_events_per_page": PER_PAGE,
        "wbep_show_publish": 1,
        "wbep_show_cancelled": 0,
        "wbep_show_booked": 0,
    })
    resp.raise_for_status()
    return resp.json().get("data", {}).get("events") or []


def _moment(raw, now, window_end):
    """Build a datetime from day/month/time, inferring the year from the window."""
    day, month_name, clock = raw
    month = c.MONTHS.get((month_name or "")[:3].lower())
    if not month or not str(day).isdigit():
        return None
    hour, minute = 0, 0
    m = re.search(r"(\d{1,2})[:.](\d{2})", clock or "")
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
    for year in (now.year, now.year + 1):
        try:
            moment = datetime(year, month, int(day), hour, minute, tzinfo=now.tzinfo)
        except ValueError:
            continue
        if moment.date() >= now.date():
            return moment
    return None


def _location(raw):
    """Split the free-text location into a short venue and a district."""
    text = c.clean(raw).removeprefix("Ort:").strip()
    first = re.split(r";|\bErreichbar\b|\bTreffpunkt\b", text)[0].strip(" ,")
    district = ""
    m = re.search(r"\b(\d{1,2})\.\s*Bez", first)
    if m:
        district = f"{int(m.group(1)):02d}. Bez."
        first = re.sub(r"\b\d{1,2}\.\s*Bez\w*\.?,?\s*", "", first).strip(" ,")
    else:
        m = re.search(r"\b(1\d{3})\b", first)
        if m:
            district = m.group(1)
    return c.trim(first, 48), district


def enrich(ev):
    """Pull the description from the event page (the feed does not carry one).

    Called by the orchestrator only for events that survived filtering.
    """
    if ev.get("desc") or not ev.get("url"):
        return
    resp = c.get(ev["url"])
    if not resp.ok:
        return
    soup = c.soup_of(resp.text)
    for selector in ('meta[property="og:description"]', "meta[name=description]"):
        meta = soup.select_one(selector)
        if meta and (meta.get("content") or "").strip():
            ev["desc"] = c.clean(meta["content"])
            break
    c.polite()


def fetch(now, window_end):
    nonce = _nonce()
    raw_events, page = [], 1
    while page <= MAX_PAGES:
        batch = _page(nonce, page)
        if not batch:
            break
        raw_events.extend(batch)
        if len(batch) < PER_PAGE:
            break
        page += 1
        c.polite()
    print(f"  {LABEL}: {len(raw_events)} events in feed")

    events = []
    for raw in raw_events:
        categories = [cat.get("name", "") for cat in raw.get("categories") or []]
        if any(word in cat.lower() for cat in categories for word in SKIP_CATEGORY_WORDS):
            continue
        moment = _moment((raw.get("date_time_start_day"),
                          raw.get("date_time_start_month"),
                          raw.get("date_time_start_time")), now, window_end)
        dates = c.sort_dates([c.occurrence(moment)], now, window_end)
        if not dates:
            continue
        venue, district = _location(raw.get("location"))
        # the age range lives in the category name, e.g. "Kleinchenaktion (3-6 Jährige)"
        min_age = c.parse_min_age(*categories, raw.get("title", ""))
        category = next((cat.split("(")[0].strip() for cat in categories
                         if "jährige" in cat.lower()), "Natur")
        events.append(c.event(
            source=LABEL,
            title=raw.get("title", ""),
            dates=dates,
            venue=venue,
            district=district,
            category=category,
            min_age=min_age,
            desc="",
            url=raw.get("link", ""),
        ))
    return events
