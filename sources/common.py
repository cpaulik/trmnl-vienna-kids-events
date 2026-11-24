"""Shared configuration and helpers for the event sources."""
import os
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
# shared config
# --------------------------------------------------------------------------- #

REGION = os.environ.get("REGION", "Wien")
DAYS_AHEAD = int(os.environ.get("DAYS_AHEAD", "7"))          # event window, starting today
FETCH_DELAY = float(os.environ.get("FETCH_DELAY", "0.4"))    # politeness delay between requests
CACHE_DATES = int(os.environ.get("CACHE_DATES", "8"))        # upcoming dates kept per event
# Highest minimum age we still want to see, e.g. 4 keeps "ab 3 J." and drops "ab 6 J."
MAX_AGE_REQUIREMENT = int(os.environ.get("MAX_AGE_REQUIREMENT", "4"))
TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "20"))

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; trmnl-kids/2.0)"}

DAY_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
DAY_NAMES = {
    "montag": "Mo", "dienstag": "Di", "mittwoch": "Mi", "donnerstag": "Do",
    "freitag": "Fr", "samstag": "Sa", "sonntag": "So",
}
MONTHS = {
    "jan": 1, "feb": 2, "mär": 3, "mar": 3, "apr": 4, "mai": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dez": 12,
}

_session = requests.Session()
_session.headers.update(HEADERS)


# --------------------------------------------------------------------------- #
# http
# --------------------------------------------------------------------------- #

def get(url, params=None, **kwargs):
    return _session.get(url, params=params, timeout=TIMEOUT, **kwargs)


def post(url, data=None, **kwargs):
    return _session.post(url, data=data, timeout=TIMEOUT, **kwargs)


def soup_of(html):
    return BeautifulSoup(html, "html.parser")


def polite():
    time.sleep(FETCH_DELAY)


# --------------------------------------------------------------------------- #
# text
# --------------------------------------------------------------------------- #

def get_text(el):
    if not el:
        return ""
    return " ".join(el.get_text(" ", strip=True).split())


def clean(text):
    return " ".join((text or "").split())


def trim(text, limit):
    text = clean(text)
    if len(text) <= limit:
        return text
    cutoff = text.rfind(" ", 0, limit - 3)
    return (text[:cutoff] if cutoff > limit * 0.6 else text[:limit - 3]) + "..."


def norm_key(title):
    """Loose key for cross-source de-duplication."""
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())[:40]


# --------------------------------------------------------------------------- #
# ages
# --------------------------------------------------------------------------- #

WORD_NUM = {
    "ein": 1, "eineinhalb": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5,
    "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11,
    "zwölf": 12,
}
AGE_PATTERNS = [
    # "ab 6 J.", "ab 6 Jahren", "ab 3 Jahre"
    r"\bab\s+(\d{1,2})\s*(?:J\.?|Jahren?|Jährigen?)",
    # "6 bis 12 J.", "6-12 Jahre", "3 - 6jährige"
    r"\b(\d{1,2})\s*(?:bis|-|–|—)\s*\d{1,2}\s*(?:J\.?|\s*jährige|Jahren?)",
    # "von 3 bis 6 Jahren", "von 6 Jahren an"
    r"\bvon\s+(\d{1,2})\s*(?:bis\s+\d{1,2}\s*)?(?:J\.?|Jahren?)",
    # "(3-6 Jährige)" without separator spaces
    r"\((\d{1,2})\s*[-–]\s*\d{1,2}\s*[Jj]ährige",
    # spelled out: "im Alter von sechs bis zwölf Jahren"
    r"\bvon\s+(ein|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|elf|zwölf)\s+"
    r"(?:bis\s+\w+\s+)?Jahren",
]


def parse_min_age(*texts):
    """Best-effort minimum age requirement from free text. None if unknown."""
    for text in texts:
        if not text:
            continue
        for pattern in AGE_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if not m:
                continue
            raw = m.group(1).lower()
            if raw.isdigit():
                return int(raw)
            if raw in WORD_NUM:
                return WORD_NUM[raw]
    return None


def age_label(min_age):
    return f"ab {min_age} J." if min_age else ""


def age_ok(min_age):
    """Events without a stated age requirement are kept."""
    return min_age is None or min_age <= MAX_AGE_REQUIREMENT


# --------------------------------------------------------------------------- #
# dates
# --------------------------------------------------------------------------- #

def occurrence(moment, clock=None):
    """Build a display occurrence from a datetime (plus an optional time label)."""
    if moment is None:
        return None
    label = clock or moment.strftime("%H:%M")
    return {
        "dat": f"{DAY_SHORT[moment.weekday()]} {moment.strftime('%d.%m.')}",
        "ti": label,
        "i": moment.isoformat(),
    }


def parse_iso(stamp):
    try:
        return datetime.fromisoformat(clean(stamp))
    except (ValueError, TypeError):
        return None


def is_over(occ, now):
    """True if the occurrence has already ended. Ranges count until their end time."""
    moment = parse_iso(occ.get("i"))
    if moment is None:
        return False
    clock = occ.get("ti", "")
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


def in_window(occ, now, window_end):
    """Keep occurrences that are still ahead and inside the search window."""
    if is_over(occ, now):
        return False
    return occ["i"][:10] <= window_end


def sort_dates(occurrences, now, window_end, limit=None):
    """Filter to the window, drop duplicates, sort chronologically."""
    seen, out = set(), []
    for occ in sorted([o for o in occurrences if o], key=lambda o: o["i"]):
        if not in_window(occ, now, window_end):
            continue
        key = (occ["i"][:10], occ["ti"])
        if key in seen:
            continue
        seen.add(key)
        out.append(occ)
    return out[:limit or CACHE_DATES]


def event(source, title, dates, venue="", district="", category="", min_age=None,
          image="", desc="", price="", recommended=False, url=""):
    """Normalized event record shared by all sources."""
    return {
        "s": source,
        "t": clean(title),
        "v": clean(venue),
        "w": clean(district),
        "g": clean(category),
        "a": age_label(min_age),
        "age": min_age,
        "p": image,
        "desc": clean(desc),
        "price": clean(price),
        "d": dates,
        "r": 1 if recommended else 0,
        "url": url,
    }
