# Kinder Wien TRMNL

A [TRMNL](https://usetrmnl.com/) plugin that displays kids events in Vienna from Falter.at's
"4 Kids" listings. Four events are picked at random each run and shown in a 2×2 grid with
photo, venue and district, category, age requirement, short description and upcoming dates.

Sibling plugin to [Kino-Wien-TRMNL](https://github.com/cpaulik/Kino-Wien-TRMNL), same shape:
scrape → cache → random selection → webhook.

## How it works

- Scrapes `falter.at/events/suche?is_4kids=true&region=Wien` for the next 7 days
- Falter recommendations first; if fewer than `MIN_POOL` survive filtering, the full kids
  listing is added as well (recommendations keep a 3× chance of being picked and are marked ★)
- Each event's detail page provides the description, the age requirement (e.g. "ab 6 J."),
  the venue address and all upcoming dates
- Events requiring an age above `MAX_AGE_REQUIREMENT` are dropped — events with no stated
  age requirement are kept
- The whole pool is cached once per day; every run only picks a new random selection,
  skipping dates that have already passed
- POSTs the selection to your TRMNL webhook, shrinking descriptions and date lists if
  needed to stay under TRMNL's 2 KB payload limit

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
cached in a named Docker volume so restarts don't re-fetch unnecessarily.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `TRMNL_WEBHOOK_URL` | required | Your TRMNL private plugin webhook URL |
| `INTERVAL` | `1800` | Seconds between runs |
| `MAX_AGE_REQUIREMENT` | `4` | Drop events that require a higher minimum age |
| `DISPLAY_COUNT` | `4` | Events per screen (2×2 grid) |
| `DAYS_AHEAD` | `7` | Length of the event window, starting today |
| `REGION` | `Wien` | Falter region filter |
| `RECOMMENDED_ONLY` | `true` | Start from Falter recommendations |
| `MIN_POOL` | `12` | Below this, widen to all kids events |
| `MAX_DATES` | `4` | Upcoming dates shown per event |
| `MAX_DESC` | `200` | Description characters before trimming |
| `SKIP_CATEGORIES` | `Vortrag / Diskussion` | Comma-separated categories to ignore |
| `FETCH_DELAY` | `0.4` | Seconds between detail-page requests |
| `CACHE_DIR` | script dir | Where `events_cache.json` lives |

## TRMNL plugin setup

1. Create a new **Private Plugin** in the TRMNL dashboard
2. Copy the webhook URL into `TRMNL_WEBHOOK_URL`
3. Paste the contents of `template.html` as the plugin template

## Local development

```bash
uv venv .venv && uv pip install -p .venv/bin/python requests beautifulsoup4 python-liquid

# scrape and print the payload without posting anything
.venv/bin/python scraper.py --dry-run --refresh

# render template.html with cached data into preview.html for a browser check
.venv/bin/python preview.py --seed 3
```

`--refresh` ignores the daily cache, `--dry-run` works without a webhook URL.
