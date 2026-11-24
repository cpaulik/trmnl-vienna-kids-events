#!/usr/bin/env python3
"""
Scrapes Falter.at kids events ("4 Kids") for Vienna, filters them by the age
requirement found on the detail pages, and POSTs a random selection to the
TRMNL webhook.

Run frequently (e.g. every 30-60 min) for rotation. The event pool — including
detail pages, descriptions and upcoming dates — is scraped once per day and
cached; each run only picks a fresh random selection from that cache.

Required env var: TRMNL_WEBHOOK_URL (not needed with --dry-run)
"""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.falter.at"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; trmnl-kids/1.0)"}

CACHE_FILE = Path(os.environ.get("CACHE_DIR", Path(__file__).parent)) / "events_cache.json"
DISPLAY_COUNT = int(os.environ.get("DISPLAY_COUNT", "4"))      # 2x2 quadrants on TRMNL
REGION = os.environ.get("REGION", "Wien")
DAYS_AHEAD = int(os.environ.get("DAYS_AHEAD", "7"))            # window for the event search
RECOMMENDED_ONLY = os.environ.get("RECOMMENDED_ONLY", "true").lower() in ("1", "true", "yes")
MAX_AGE_REQUIREMENT = int(os.environ.get("MAX_AGE_REQUIREMENT", "4"))  # drop "ab 6 J." events
MAX_DATES = int(os.environ.get("MAX_DATES", "4"))              # upcoming dates shown per event
CACHE_DATES = 8                                                # upcoming dates kept in the cache
MAX_DESC = int(os.environ.get("MAX_DESC", "200"))              # description chars
PAGES_TO_FETCH = int(os.environ.get("PAGES_TO_FETCH", "25"))
MIN_POOL = int(os.environ.get("MIN_POOL", "12"))               # below this, widen beyond recommendations
FETCH_DELAY = float(os.environ.get("FETCH_DELAY", "0.4"))      # politeness delay between detail pages
# Categories that Falter files under "4 Kids" but that are not kid activities
SKIP_CATEGORIES = {c.strip().lower() for c in
                   os.environ.get("SKIP_CATEGORIES", "Vortrag / Diskussion").split(",")
                   if c.strip()}
PAYLOAD_LIMIT = 2000                                           # TRMNL merge_variables soft limit

DAY_SHORT = {
    "Montag": "Mo", "Dienstag": "Di", "Mittwoch": "Mi", "Donnerstag": "Do",
    "Freitag": "Fr", "Samstag": "Sa", "Sonntag": "So",
}


def get_text(el):
    if not el:
        return ""
    return " ".join(el.get_text(" ", strip=True).split())


# --------------------------------------------------------------------------- #
# listing pages
# --------------------------------------------------------------------------- #

def parse_listing(html):
    """Return [{id, url, t, v, g, p}] from one search result page (one card per
    occurrence, so duplicates are expected and de-duplicated by caller)."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select("a.group.block"):
        href = a.get("href", "")
        m = re.search(r"/event/(\d+)/", href)
        if not m:
            continue
        title = get_text(a.select_one("h2"))
        if not title:
            continue
        url = href if href.startswith("http") else BASE + href
        divs = a.select("div.font-display")
        venue = get_text(divs[0]) if divs else ""
        tags = [get_text(li) for li in a.select("li") if get_text(li)]
        tags = [t for t in tags if t.lower() != "empfehlung"]
        poster = ""
        img = a.select_one('img[src*="/events/"]')
        if img:
            poster = re.sub(r"\?width=\d+", "?width=300", img.get("src", ""))
        out.append({
            "id": m.group(1),
            "url": url,
            "t": title,
            "v": venue,
            "g": " / ".join(tags[:2]),
            "p": poster,
        })
    return out


def scrape_listing(span, recommended):
    """Walk the paginated search and return unique events, in page order."""
    seen, events = set(), []
    for page in range(1, PAGES_TO_FETCH + 1):
        params = {"region": REGION, "is_4kids": "true", "span": span, "page": page}
        if recommended:
            params["is_recommended"] = "true"
        resp = requests.get(f"{BASE}/events/suche", params=params, headers=HEADERS, timeout=20)
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        page_events = parse_listing(resp.text)
        if not page_events:
            break
        new_on_page = 0
        for ev in page_events:
            if ev["id"] in seen:
                continue
            seen.add(ev["id"])
            events.append(ev)
            new_on_page += 1
        print(f"  page {page}: {len(page_events)} cards, {new_on_page} new events")
        if new_on_page == 0 and page > 1:
            break
        time.sleep(FETCH_DELAY)
    return events


# --------------------------------------------------------------------------- #
# detail pages
# --------------------------------------------------------------------------- #

AGE_PATTERNS = [
    # "ab 6 J.", "ab 6 Jahren", "ab 3"
    (r"\bab\s+(\d{1,2})\s*(?:J\.?|Jahren?|Jährigen?)", 1),
    # "6 bis 12 J.", "6-12 Jahre", "3 - 6 J."
    (r"\b(\d{1,2})\s*(?:bis|-|–|—)\s*\d{1,2}\s*(?:J\.?|Jahren?|Jährige)", 1),
    # "für 6-Jährige", "für Kinder von 6 Jahren an"
    (r"\bvon\s+(\d{1,2})\s*(?:J\.?|Jahren?)", 1),
    # German spelled-out minimum, e.g. "im Alter von sechs bis zwölf Jahren"
    (r"\bvon\s+(ein|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|elf|zwölf)\s+"
     r"(?:bis\s+\w+\s+)?Jahren", 1),
]
WORD_NUM = {
    "ein": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6,
    "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12,
}


def parse_min_age(*texts):
    """Best-effort minimum age requirement from free text. None if unknown."""
    for text in texts:
        if not text:
            continue
        for pattern, group in AGE_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if not m:
                continue
            raw = m.group(group).lower()
            if raw.isdigit():
                return int(raw)
            if raw in WORD_NUM:
                return WORD_NUM[raw]
    return None


def trim(text, limit):
    if len(text) <= limit:
        return text
    cutoff = text.rfind(" ", 0, limit - 3)
    return (text[:cutoff] if cutoff > limit * 0.6 else text[:limit - 3]) + "..."


def parse_detail(html, now):
    """Extract description, info line, category, district and upcoming dates."""
    soup = BeautifulSoup(html, "html.parser")

    prose = soup.select_one("div.prose p")
    description = get_text(prose)
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
                label = get_text(label_el)
                value = get_text(value_divs[1])
                if label and value and label != value:
                    details.setdefault(label, value)

    # upcoming dates, grouped per venue table
    dates, venues, districts = [], [], []
    for table in soup.select("table"):
        th = table.select_one("th")
        venue = get_text(th.select_one('[itemprop="name"]')) if th else ""
        venue = venue or get_text(th)
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
            day_time = row.select_one("td time[datetime]")
            if not day_time:
                continue
            day_iso = (day_time.get("datetime") or "")[:10]
            day_label = get_text(row.select("td time span")[-1]) if row.select("td time span") else ""
            for start in row.select('time[itemprop="startDate"]'):
                stamp = start.get("datetime") or ""
                clock = re.sub(r"\s*[–—-]\s*", "–", get_text(start))
                if not stamp:
                    continue
                dates.append({
                    "iso": stamp,
                    "day": day_iso,
                    "dow": DAY_SHORT.get(day_label, day_label[:2]),
                    "ti": clock,
                })

    # keep only dates that have not finished yet, sorted and deduplicated
    seen, upcoming = set(), []
    for d in sorted(dates, key=lambda x: x["iso"]):
        key = (d["day"], d["ti"])
        if key in seen:
            continue
        seen.add(key)
        dd, mm = d["day"][8:10], d["day"][5:7]
        occurrence = {"dat": f"{d['dow']} {dd}.{mm}.".strip(), "ti": d["ti"], "i": d["iso"]}
        if not is_over(occurrence, now):
            upcoming.append(occurrence)

    return {
        "desc": description,
        "info": details.get("Info", ""),
        "cat": details.get("Kategorie", ""),
        "price": details.get("Preis", ""),
        "district": districts[0] if districts else "",
        "venue": venues[0] if venues else "",
        "dates": upcoming,
    }


def fetch_detail(event, now):
    resp = requests.get(event["url"], headers=HEADERS, timeout=20)
    if not resp.ok:
        return None
    return parse_detail(resp.text, now)


def is_over(occurrence, now):
    """True if the occurrence has already ended. Ranges count until their end time."""
    stamp = occurrence.get("i")
    if not stamp:
        return False
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    clock = occurrence.get("ti", "")
    if "–" in clock:
        end = clock.split("–")[-1].strip()
        m = re.match(r"(\d{1,2}):(\d{2})$", end)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
            if (hour, minute) >= (moment.hour, moment.minute):
                moment = moment.replace(hour=hour, minute=minute)
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment < now


def prune(pool, now):
    """Drop occurrences that already happened, and events left without any."""
    fresh = []
    for event in pool:
        dates = [d for d in event.get("d", []) if not is_over(d, now)]
        if dates:
            fresh.append({**event, "d": dates})
    return fresh


# --------------------------------------------------------------------------- #
# pool building / caching
# --------------------------------------------------------------------------- #

def collect(listing, now, recommended, pool, seen_ids, window_end=None):
    """Fetch detail pages for `listing` and append age-appropriate events to `pool`."""
    skipped_age = failed = 0
    for i, ev in enumerate(listing, 1):
        if ev["id"] in seen_ids:
            continue
        seen_ids.add(ev["id"])
        try:
            detail = fetch_detail(ev, now)
        except requests.RequestException as exc:
            print(f"  [{i}] {ev['t']}: request failed ({exc})")
            detail = None
        if detail is None:
            failed += 1
            continue
        if window_end:
            detail["dates"] = [d for d in detail["dates"] if d["i"][:10] <= window_end]
        min_age = parse_min_age(detail["info"], detail["desc"])
        if min_age is not None and min_age > MAX_AGE_REQUIREMENT:
            skipped_age += 1
            print(f"  [{i}] skip (ab {min_age} J.): {ev['t']}")
            time.sleep(FETCH_DELAY)
            continue
        if not detail["dates"]:
            print(f"  [{i}] skip (no upcoming dates): {ev['t']}")
            time.sleep(FETCH_DELAY)
            continue
        category = (detail["cat"] or ev["g"]).strip()
        if category.lower() in SKIP_CATEGORIES:
            print(f"  [{i}] skip (category {category}): {ev['t']}")
            time.sleep(FETCH_DELAY)
            continue
        entry = {
            "t": ev["t"],
            "v": detail["venue"] or ev["v"],
            "w": detail["district"],
            "g": category,
            "a": f"ab {min_age} J." if min_age else "",
            "p": ev["p"],
            "desc": detail["desc"],
            "d": detail["dates"][:CACHE_DATES],
            "price": detail["price"],
        }
        if recommended:
            entry["r"] = 1
        pool.append(entry)
        time.sleep(FETCH_DELAY)
    return skipped_age, failed


def build_pool(now):
    today = now.date()
    end = today + timedelta(days=DAYS_AHEAD - 1)
    span = f"{today}:{end}"
    pool, seen_ids = [], set()

    if RECOMMENDED_ONLY:
        print(f"Fetching Falter-recommended kids events for {REGION} {span}...")
        listing = scrape_listing(span, recommended=True)
        print(f"{len(listing)} unique recommended events; fetching detail pages...")
        skipped_age, failed = collect(listing, now, True, pool, seen_ids, str(end))
        print(f"Recommended pool: {len(pool)} events "
              f"(skipped {skipped_age} on age, {failed} failed).")

    if len(pool) < MIN_POOL:
        print(f"Pool below {MIN_POOL}, widening to all kids events...")
        listing = scrape_listing(span, recommended=False)
        print(f"{len(listing)} unique events in wide listing; fetching detail pages...")
        skipped_age, failed = collect(listing, now, False, pool, seen_ids, str(end))
        print(f"After widening: {len(pool)} events "
              f"(skipped {skipped_age} on age, {failed} failed).")

    print(f"Pool: {len(pool)} events "
          f"({sum(1 for e in pool if e.get('r'))} Falter recommendations).")
    return pool


def load_pool(now, force_refresh=False):
    today = now.date()
    if CACHE_FILE.exists() and not force_refresh:
        try:
            cache = json.loads(CACHE_FILE.read_text())
            if cache.get("date") == str(today) and cache.get("events"):
                print(f"Using cache ({len(cache['events'])} events).")
                return cache["events"]
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Cache unreadable ({exc}), refetching.")
    pool = build_pool(now)
    if pool:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({"date": str(today), "events": pool},
                                         ensure_ascii=False))
        print(f"Cached {len(pool)} events at {CACHE_FILE}.")
    elif CACHE_FILE.exists():
        print("Fresh pool empty; falling back to previous cache.")
        try:
            return json.loads(CACHE_FILE.read_text()).get("events", [])
        except (json.JSONDecodeError, OSError):
            return []
    return pool


# --------------------------------------------------------------------------- #
# payload
# --------------------------------------------------------------------------- #

def pick(pool, count):
    """Random selection without replacement, Falter recommendations weighted 3x."""
    candidates = [(e, 3 if e.get("r") else 1) for e in pool]
    chosen = []
    for _ in range(min(count, len(candidates))):
        total = sum(w for _, w in candidates)
        threshold, acc = random.uniform(0, total), 0.0
        for idx, (event, weight) in enumerate(candidates):
            acc += weight
            if threshold <= acc:
                chosen.append(event)
                candidates.pop(idx)
                break
    return chosen


def shape(event, max_dates, desc_limit, with_price=True):
    out = {
        "t": trim(event["t"], 70),
        "v": event["v"],
        "g": event["g"],
        "a": event["a"],
        "p": event["p"],
        "d": [{"dat": d["dat"], "ti": d["ti"]} for d in event["d"][:max_dates]],
    }
    if event.get("w"):
        out["w"] = event["w"]
    if event.get("r"):
        out["r"] = 1
    if with_price and event.get("price"):
        out["pr"] = trim(event["price"], 22)
    if desc_limit and event.get("desc"):
        out["desc"] = trim(event["desc"], desc_limit)
    return {k: v for k, v in out.items() if v}


def build_payload(selection, today, days_ahead=None):
    """Shape the selection, shrinking descriptions/dates until it fits TRMNL."""
    days_ahead = DAYS_AHEAD if days_ahead is None else days_ahead
    tiers = ((MAX_DESC, MAX_DATES, True), (160, 3, True), (120, 2, False), (0, 2, False))
    for desc_limit, max_dates, with_price in tiers:
        payload = {
            "merge_variables": {
                "events": [shape(e, max_dates, desc_limit, with_price) for e in selection],
                "week": f"{today.strftime('%-d.')}–{(today + timedelta(days=days_ahead - 1)).strftime('%-d. %b')}",
            }
        }
        size = len(json.dumps(payload, ensure_ascii=False))
        if size <= PAYLOAD_LIMIT:
            return payload, size
    return payload, size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print the payload instead of posting to TRMNL")
    ap.add_argument("--refresh", action="store_true", help="ignore the daily cache")
    args = ap.parse_args()

    webhook = os.environ.get("TRMNL_WEBHOOK_URL")
    if not webhook and not args.dry_run:
        sys.exit("TRMNL_WEBHOOK_URL is not set (use --dry-run to test without it)")

    now = datetime.now().astimezone()
    cached = load_pool(now, force_refresh=args.refresh)
    pool = prune(cached, now)
    if len(pool) < len(cached):
        print(f"Pruned {len(cached) - len(pool)} events whose dates have passed.")
    if not pool:
        print("No events found, aborting.")
        return 1

    selection = pick(pool, DISPLAY_COUNT)
    payload, size = build_payload(selection, now.date())

    print(f"Selected {len(selection)} of {len(pool)} events ({size} bytes):")
    for e in payload["merge_variables"]["events"]:
        print(f"  - {e['t']} @ {e.get('v', '?')} ({len(e.get('d', []))} dates)")
    if size > PAYLOAD_LIMIT:
        print(f"WARNING: payload over {PAYLOAD_LIMIT} bytes, TRMNL may reject it.")

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    resp = requests.post(webhook, json=payload, timeout=15)
    print(f"TRMNL: {resp.status_code} {resp.text}")
    return 0 if resp.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
