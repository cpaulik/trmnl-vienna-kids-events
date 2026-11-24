"""Event sources for the Kinder Wien TRMNL plugin.

Each source module exposes:
    LABEL                     short name shown on the TRMNL card
    fetch(now, window_end)    -> list of normalized events (see common.event)
    enrich(event)             optional, called for events that survived filtering

Order matters: when two sources carry the same event, the earlier one wins and
only fills missing fields from the later one.
"""
from . import falter, tmw, umweltspuernasen, wiengv, wienxtra

MODULES = {
    "falter": falter,
    "wienxtra": wienxtra,
    "umweltspuernasen": umweltspuernasen,
    "wiengv": wiengv,
    "tmw": tmw,
}
DEFAULT_ORDER = ["falter", "wienxtra", "umweltspuernasen", "wiengv", "tmw"]


def resolve(names):
    """Map configured source names to modules, ignoring unknown entries."""
    out = []
    for name in names:
        key = name.strip().lower()
        if not key:
            continue
        module = MODULES.get(key)
        if module is None:
            print(f"Unknown source '{name}', skipping. Known: {', '.join(MODULES)}")
            continue
        out.append((key, module))
    return out
