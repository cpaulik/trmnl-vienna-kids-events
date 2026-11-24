# Kinder Wien TRMNL

A [TRMNL](https://usetrmnl.com/) plugin that displays kids events in Vienna. Four events are
picked at random each run and shown in a 2×2 grid with photo, venue and district, category,
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

## Deployment

Three ways to run it, in the order I'd pick them.

### 1. Docker, next to the films plugin (recommended)

Same pattern as `trmnl-kino` on arcane: add the service in `docker-compose.yml` and give it
its **own** webhook UUID — the kids plugin is a separate private plugin, so reusing the films
webhook would overwrite that screen.

```bash
docker compose up -d
```

The scraper runs on start and then every `INTERVAL` seconds, well inside TRMNL's limit of 12
pushes per hour. The daily pool lives in a named volume, so a restart doesn't re-scrape.

### 2. GitHub Actions, no host at all

`.github/workflows/push.yml` does the same job on GitHub's schedule. Set the repository secret
`TRMNL_WEBHOOK_URL` and it runs every two hours, caching the daily pool with `actions/cache`
keyed on the Vienna date, so only the first run of each day scrapes.

What you give up:

- **The schedule is best effort.** Runs get delayed under load and are sometimes dropped, so
  the cadence is "roughly every 30 minutes", not on the clock.
- **Public-repo schedules switch off after 60 days without repository activity**, with only a
  banner to tell you. `keepalive.yml` commits a heartbeat monthly to prevent it.
- **Minutes are only free on a public repository.** 48 runs a day will blow through the free
  tier on a private one.

### 3. GitHub Pages — only via the Polling strategy

Pages cannot run code, so it can't push anything by itself. It works only by inverting the
flow: a scheduled Action writes a static `data.json`, Pages serves it, and the plugin uses
TRMNL's **Polling** strategy to fetch that URL on its own refresh interval. That is more
resilient to a missed Action — the device re-reads slightly stale JSON instead of missing an
update — but the screen only changes when the file does, so rotation still depends on how
often the Action runs, unless the template shuffles a larger pool client-side. Actions is
doing the real work in this setup either way.

## Rotation without frequent pushes

Each payload carries **several screens' worth of events**, and the template decides which one
to show from `trmnl.system.timestamp_utc`, the render clock. The display therefore keeps
changing every `ROTATE_SECONDS` even when nothing has been pushed, so a late, missed or
dropped run costs freshness rather than movement — which is what makes the GitHub Actions
option viable despite its best-effort scheduler.

That is why `INTERVAL` is two hours rather than thirty minutes: the device still shows
something different every half hour.

How many screens fit depends on how wordy the selected events are, so `build_payload` works it
out per run: it keeps the most screens it can **at full description length**, and only shortens
text once it is down to a single screen. Measured against a live pool on the 5 kB tier:

| screens | events | description cap | bytes | fits 5 kB | descriptions truncated |
|---|---|---|---|---|---|
| 2 | 8 | 330 | 3610 | yes | 17% |
| 3 | 12 | 160 | 4782 | barely | 54% |
| 4 | 16 | none | 4126 | yes | 100% |
| 5 | 20 | — | 7822 | no | — |

So two to three screens is the realistic range here. On TRMNL+ (10 kB) raise `PAYLOAD_LIMIT`
and four screens at full length fit without any other change.

If the template ever renders without `trmnl.system.timestamp_utc`, the slot computes to 0 and
the first screen shows — the markup degrades to the old single-screen behaviour rather than
breaking.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `TRMNL_WEBHOOK_URL` | required | Your TRMNL private plugin webhook URL |
| `INTERVAL` | `1800` | Seconds between runs (Docker only; TRMNL allows 12 pushes/hour) |
| `SOURCES` | all five | Comma-separated: `falter,wienxtra,umweltspuernasen,wiengv,tmw` |
| `MAX_AGE_REQUIREMENT` | `4` | Drop events that require a higher minimum age |
| `DISPLAY_COUNT` | `4` | Events per screen |
| `GRID_COLS` | auto | Grid columns; `0` derives them from `DISPLAY_COUNT` |
| `DAYS_AHEAD` | `7` | Length of the event window, starting today |
| `REGION` | `Wien` | Falter region filter |
| `SKIP_CATEGORIES` | `Vortrag / Diskussion` | Comma-separated categories to ignore |
| `RECOMMENDED_WEIGHT` | `3` | Pick weight for Falter recommendations |
| `ROTATION_GROUPS` | `4` | Screens to attempt per payload; trimmed to what fits |
| `ROTATE_SECONDS` | `1800` | How long each screen is shown before the next |
| `MAX_DATES` | `4` | Upcoming dates shown per event |
| `MAX_DESC` | `330` | Description characters before trimming |
| `CACHE_DATES` | `8` | Upcoming dates kept per event in the cache |
| `FETCH_DELAY` | `0.4` | Seconds between requests |
| `PAYLOAD_LIMIT` | `4800` | Byte budget for the webhook payload (TRMNL allows 5 KB, 10 KB with TRMNL+) |
| `CACHE_DIR` | script dir | Where `events_cache.json` lives |
| `FALTER_MIN_RECOMMENDED` | `12` | Below this many age-appropriate recommendations, Falter widens to its full kids listing |
| `WIENGV_MAX_DETAILS` | `140` | Cap on Stadt Wien detail-page fetches per day |

## Layout

The template is **fluid and landscape** and hardcodes no frame.

Device geometry worth knowing: markup is rendered at **CSS pixels**, which is not the panel
resolution. A **TRMNL X** renders at **1040×780** and scales by its `pixel-ratio` of 1.8 onto
the 1872×1404 physical panel (`screen--v2` in the framework carries the same canvas). A
**TRMNL OG** renders at **800×480**. Both are landscape by default. Designing against 1872 or
1404 directly gives you a portrait-shaped layout that does not match any device.

Every size here is a **container query unit** (`cqh`/`cqw`) rather than a viewport unit, as the
TRMNL X guide recommends: viewport units still refer to the whole screen inside mashup slots,
container units follow the card the content actually sits in. Each card is its own query
container, so a tall single-row cell automatically gets a bigger photo and a longer description
than a short cell in a 3×2 grid.

`DISPLAY_COUNT` alone decides the density — the scraper sends a `cols` merge variable
(6 → 3×2, 4 → 2×2, 3 → 1×3), which the grid reads as `--cols`. Override with `GRID_COLS`.

The photo band is deliberately short (23% of the card) so the text, not the picture, gets the
space. Description length is tuned so the server-side trim and the CSS clamp roughly coincide:
at four events per screen a card fits about five lines, and `MAX_DESC` is set so only the
longest ~17% of descriptions end in an ellipsis.

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
.venv/bin/python preview.py --device x,og --events 6,4 --with-nophoto

# serve the preview over HTTP instead of opening the file
.venv/bin/python preview.py --serve 8137
```

`--refresh` ignores the daily cache, `--dry-run` works without a webhook URL.
