# Kinder Wien TRMNL

A [TRMNL](https://usetrmnl.com/) plugin that displays kids events in Vienna. Three events are
picked at random each run and shown side by side with photo, venue and district, category,
age requirement, price, short description, upcoming dates and the source they came from.

Sibling plugin to [Kino-Wien-TRMNL](https://github.com/cpaulik/Kino-Wien-TRMNL), same shape:
scrape → cache → random selection → webhook.

## Sources

| Source | What it gives | How |
|---|---|---|
| **Falter** "4 Kids" | the bulk of the pool, recommendations marked ★ | search pages + detail pages with schema.org microdata |
| **WienXtra** Kinderinfo | curated kids offers, free/registration badges, recurring series | static Bootstrap cards |
| **Umweltspürnasen** | nature exploration events ("Kleinchenaktion 3-6") | WordPress AJAX API (nonce + POST), description from the event page |
| **Stadt Wien** "Wien erleben" | city events flagged *Für Kinder* | `veranstaltungen.json` + JSON-LD on the detail pages |
| **Technisches Museum** | museum programme for *Kinder & Familie* | JSON-LD on the programme page |

Each source is a module in `sources/` exposing `fetch(now, window_end)` and an optional
`enrich(event)`. A source that fails is reported and skipped — the run continues with the rest.

## How it works

- Events from all sources are normalized to one record shape, then filtered:
  - minimum age above `MAX_AGE_REQUIREMENT` is dropped (events with no stated age are kept)
  - categories in `SKIP_CATEGORIES` are dropped (adult panel talks filed under "kids")
  - dates outside the window or already past are dropped
- Duplicates across sources are merged by title; the first source wins and the others only
  fill its empty fields (so a Falter event can inherit a WienXtra "Gratis" badge)
- The whole pool is cached once per day; every run only picks a new random selection,
  so the display rotates without re-scraping
- Falter recommendations are weighted `RECOMMENDED_WEIGHT`× when picking
- The payload shrinks descriptions and date lists as needed to stay under TRMNL's 2 KB limit

## Running with Docker

Create a `.env` file next to the compose file:

```
TRMNL_WEBHOOK_URL=https://usetrmnl.com/api/custom_plugins/YOUR_PLUGIN_ID
```

Then start the container:

```bash
docker compose up -d
```

The scraper runs immediately on start and then every 30 minutes. The daily event pool is
cached in a named Docker volume so restarts don't re-fetch unnecessarily. A full scrape takes
a few minutes (roughly 250 requests, paced by `FETCH_DELAY`); the other 47 runs of the day are
instant.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `TRMNL_WEBHOOK_URL` | required | Your TRMNL private plugin webhook URL |
| `INTERVAL` | `1800` | Seconds between runs |
| `SOURCES` | all five | Comma-separated: `falter,wienxtra,umweltspuernasen,wiengv,tmw` |
| `MAX_AGE_REQUIREMENT` | `4` | Drop events that require a higher minimum age |
| `DISPLAY_COUNT` | `3` | Events per screen (one column each) |
| `DAYS_AHEAD` | `7` | Length of the event window, starting today |
| `REGION` | `Wien` | Falter region filter |
| `SKIP_CATEGORIES` | `Vortrag / Diskussion` | Comma-separated categories to ignore |
| `RECOMMENDED_WEIGHT` | `3` | Pick weight for Falter recommendations |
| `MAX_DATES` | `4` | Upcoming dates shown per event |
| `MAX_DESC` | `200` | Description characters before trimming |
| `CACHE_DATES` | `8` | Upcoming dates kept per event in the cache |
| `FETCH_DELAY` | `0.4` | Seconds between requests |
| `PAYLOAD_LIMIT` | `2000` | Byte budget for the webhook payload |
| `CACHE_DIR` | script dir | Where `events_cache.json` lives |
| `FALTER_MIN_RECOMMENDED` | `12` | Below this many age-appropriate recommendations, Falter widens to its full kids listing |
| `WIENGV_MAX_DETAILS` | `140` | Cap on Stadt Wien detail-page fetches per day |

## Layout

The template is **fluid and landscape** — it hardcodes no frame, so the same markup fills
TRMNL OG (800×480, 1-bit) and TRMNL V2 (1040×780, 4-bit). Type sizes and the photo band scale
with viewport height, and panels taller than 600px get a bigger photo and a longer description.

The grid puts **one column per event**, so `DISPLAY_COUNT` alone decides the density — 3 is
comfortable on an OG, 4 fits if you prefer breadth over prose.

Event photos are landscape (1.25–1.78 aspect), unlike film posters, so each card carries the
photo as a band across the top rather than in a portrait poster slot. Roughly 80% of events
have a photo; the Technisches Museum and Umweltspürnasen never provide one, and those cards
give their text the whole cell instead.

## TRMNL plugin setup

1. Create a new **Private Plugin** in the TRMNL dashboard
2. Copy the webhook URL into `TRMNL_WEBHOOK_URL`
3. Paste the contents of `template.html` as the plugin template

## Local development

```bash
uv venv .venv && uv pip install -p .venv/bin/python requests beautifulsoup4 python-liquid

# scrape everything and print the payload without posting
.venv/bin/python scraper.py --dry-run --refresh

# work on one source at a time
.venv/bin/python scraper.py --dry-run --refresh --sources wienxtra

# render template.html with cached data into preview.html for a browser check
.venv/bin/python preview.py --device og,v2 --events 3,4 --with-nophoto

# serve the preview over HTTP instead of opening the file
.venv/bin/python preview.py --serve 8137
```

`--refresh` ignores the daily cache, `--dry-run` works without a webhook URL.
