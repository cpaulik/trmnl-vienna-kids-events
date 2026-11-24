#!/usr/bin/env python3
"""
Collects kids events in Vienna from several sources (Falter "4 Kids", WienXtra
Kinderinfo, Umweltspürnasen, Stadt Wien and the Technisches Museum), filters them
by age requirement, and POSTs a random selection to the TRMNL webhook.

Run frequently (e.g. every 30-60 min) for rotation. The whole pool is scraped once
per day and cached; each run only picks a fresh random selection from that cache
and drops dates that have already passed.

Required env var: TRMNL_WEBHOOK_URL (not needed with --dry-run)
"""
import argparse
import json
import os
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

import sources
from sources import common as c

CACHE_FILE = Path(os.environ.get("CACHE_DIR", Path(__file__).parent)) / "events_cache.json"
SOURCE_NAMES = [s for s in os.environ.get("SOURCES", ",".join(sources.DEFAULT_ORDER)).split(",")]
DISPLAY_COUNT = int(os.environ.get("DISPLAY_COUNT", "6"))      # events per screen
GRID_COLS = int(os.environ.get("GRID_COLS", "0"))              # 0 = derive from DISPLAY_COUNT
MAX_DATES = int(os.environ.get("MAX_DATES", "4"))              # dates shown per event
MAX_DESC = int(os.environ.get("MAX_DESC", "200"))              # description chars
RECOMMENDED_WEIGHT = float(os.environ.get("RECOMMENDED_WEIGHT", "3"))
# TRMNL accepts 5kb of merge variables, 10kb for TRMNL+ subscribers
PAYLOAD_LIMIT = int(os.environ.get("PAYLOAD_LIMIT", "4800"))
# Categories that the sources file under "kids" but that are not kid activities
SKIP_CATEGORIES = {s.strip().lower() for s in
                   os.environ.get("SKIP_CATEGORIES", "Vortrag / Diskussion").split(",")
                   if s.strip()}


# --------------------------------------------------------------------------- #
# collecting
# --------------------------------------------------------------------------- #

def gather(now, window_end):
    """Run every configured source. A failing source is reported, not fatal."""
    collected, failures = [], []
    for name, module in sources.resolve(SOURCE_NAMES):
        print(f"[{name}] fetching...")
        try:
            events = module.fetch(now, window_end)
        except Exception as exc:                            # noqa: BLE001 - keep other sources alive
            print(f"[{name}] FAILED: {type(exc).__name__}: {exc}")
            failures.append(name)
            continue
        print(f"[{name}] {len(events)} events")
        collected.extend(events)
    return collected, failures


def keep(event):
    """Age and category gate, applied identically to every source."""
    if not c.age_ok(event.get("age")):
        return False
    if (event.get("g") or "").strip().lower() in SKIP_CATEGORIES:
        return False
    return bool(event.get("d"))


def merge(events):
    """De-duplicate across sources, keeping the first and filling its gaps."""
    by_key, order = {}, []
    for event in events:
        key = c.norm_key(event["t"])
        if key not in by_key:
            by_key[key] = event
            order.append(key)
            continue
        kept = by_key[key]
        for field in ("v", "w", "g", "a", "p", "desc", "price", "x"):
            if not kept.get(field) and event.get(field):
                kept[field] = event[field]
        if kept.get("age") is None and event.get("age") is not None:
            kept["age"] = event["age"]
        kept["r"] = max(kept.get("r", 0), event.get("r", 0))
        if len(event.get("d", [])) > len(kept.get("d", [])):
            kept["d"] = event["d"]
        kept["also"] = sorted(set(kept.get("also", []) + [event["s"]]) - {kept["s"]})
    return [by_key[k] for k in order]


def enrich(pool):
    """Let sources fill in details for events that made it into the pool."""
    modules = dict(sources.resolve(SOURCE_NAMES))
    by_label = {m.LABEL: m for m in modules.values()}
    for event in pool:
        module = by_label.get(event.get("s"))
        hook = getattr(module, "enrich", None)
        if hook is None:
            continue
        try:
            hook(event)
        except Exception as exc:                            # noqa: BLE001
            print(f"  enrich failed for {event['t']}: {exc}")


def build_pool(now, window_end):
    collected, failures = gather(now, window_end)
    print(f"Collected {len(collected)} events from {len(SOURCE_NAMES)} sources.")

    filtered = [e for e in collected if keep(e)]
    dropped_age = sum(1 for e in collected if not c.age_ok(e.get("age")))
    print(f"Filtered to {len(filtered)} (dropped {dropped_age} on age requirement).")

    pool = merge(filtered)
    print(f"Merged to {len(pool)} unique events.")
    enrich(pool)

    for event in pool:
        event["d"] = event["d"][:c.CACHE_DATES]
    counts = {}
    for event in pool:
        counts[event["s"]] = counts.get(event["s"], 0) + 1
    print("Pool by source: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if failures:
        print(f"Sources that failed: {', '.join(failures)}")
    return pool, failures


# --------------------------------------------------------------------------- #
# caching
# --------------------------------------------------------------------------- #

def load_pool(now, window_end, force_refresh=False):
    today = now.date()
    if CACHE_FILE.exists() and not force_refresh:
        try:
            cache = json.loads(CACHE_FILE.read_text())
            if cache.get("date") == str(today) and cache.get("events"):
                print(f"Using cache ({len(cache['events'])} events).")
                return cache["events"]
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Cache unreadable ({exc}), refetching.")

    pool, _ = build_pool(now, window_end)
    if pool:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({"date": str(today), "events": pool},
                                         ensure_ascii=False))
        print(f"Cached {len(pool)} events at {CACHE_FILE}.")
        return pool
    if CACHE_FILE.exists():
        print("Fresh pool empty; falling back to previous cache.")
        try:
            return json.loads(CACHE_FILE.read_text()).get("events", [])
        except (json.JSONDecodeError, OSError):
            return []
    return pool


def prune(pool, now):
    """Drop occurrences that already happened, and events left without any."""
    fresh = []
    for event in pool:
        dates = [d for d in event.get("d", []) if not c.is_over(d, now)]
        if dates:
            fresh.append({**event, "d": dates})
    return fresh


# --------------------------------------------------------------------------- #
# selection and payload
# --------------------------------------------------------------------------- #

def pick(pool, count):
    """Random selection without replacement; recommendations are weighted up."""
    candidates = [(e, RECOMMENDED_WEIGHT if e.get("r") else 1.0) for e in pool]
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
        "t": c.trim(event["t"], 70),
        "v": c.trim(event.get("v", ""), 44),
        "g": event.get("g", ""),
        "a": event.get("a", ""),
        "p": event.get("p", ""),
        "s": event.get("s", ""),
        "d": [{"dat": d["dat"], "ti": d["ti"]} for d in event.get("d", [])[:max_dates]],
    }
    if event.get("w"):
        out["w"] = event["w"]
    if event.get("x"):
        out["x"] = c.trim(event["x"], 24)
    if event.get("r"):
        out["r"] = 1
    if with_price and event.get("price"):
        out["pr"] = c.trim(event["price"], 22)
    if desc_limit and event.get("desc"):
        out["desc"] = c.trim(event["desc"], desc_limit)
    return {k: v for k, v in out.items() if v}


def grid_cols(count):
    """Column count for the template: 6 -> 3x2, 4 -> 2x2, 3 -> 1x3."""
    if GRID_COLS:
        return GRID_COLS
    if count >= 5:
        return 3
    if count == 4:
        return 2
    return max(count, 1)


def build_payload(selection, today, days_ahead=None):
    """Shape the selection, shrinking descriptions/dates until it fits TRMNL."""
    days_ahead = c.DAYS_AHEAD if days_ahead is None else days_ahead
    end = today + timedelta(days=days_ahead - 1)
    tiers = ((MAX_DESC, MAX_DATES, True), (160, 3, True), (120, 2, False), (0, 2, False))
    for desc_limit, max_dates, with_price in tiers:
        payload = {
            "merge_variables": {
                "events": [shape(e, max_dates, desc_limit, with_price) for e in selection],
                "week": f"{today.strftime('%-d.')}–{end.strftime('%-d. %b')}",
                "cols": grid_cols(len(selection)),
            }
        }
        size = len(json.dumps(payload, ensure_ascii=False))
        if size <= PAYLOAD_LIMIT:
            return payload, size
    return payload, size


# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print the payload instead of posting to TRMNL")
    ap.add_argument("--refresh", action="store_true", help="ignore the daily cache")
    ap.add_argument("--sources", help="comma-separated subset of sources for this run")
    args = ap.parse_args()

    if args.sources:
        global SOURCE_NAMES
        SOURCE_NAMES = args.sources.split(",")

    webhook = os.environ.get("TRMNL_WEBHOOK_URL")
    if not webhook and not args.dry_run:
        sys.exit("TRMNL_WEBHOOK_URL is not set (use --dry-run to test without it)")

    now = datetime.now().astimezone()
    window_end = str(now.date() + timedelta(days=c.DAYS_AHEAD - 1))
    print(f"Window {now.date()} .. {window_end}, sources: {', '.join(SOURCE_NAMES)}")

    cached = load_pool(now, window_end, force_refresh=args.refresh)
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
        print(f"  - [{e.get('s', '?')}] {e['t']} @ {e.get('v', '?')} "
              f"({len(e.get('d', []))} dates)")
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
