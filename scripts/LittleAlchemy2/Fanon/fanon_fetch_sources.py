#!/usr/bin/env python3
"""
fetch_sources.py
================
Fetches raw wikitext for every page in data/fanon_category/pages.jsonl
and saves them all to data/source_of_pages.json.

Format:
  {
    "Fanon:First": "{{Element\\n|fanon = 1\\n...}}",
    "Fanon:Missing": null,   ← page existed in list but had no revisions
    ...
  }

Run once; then edit/rerun build_fanon_db.py without hitting the network.

Usage:
  python fetch_sources.py [--pages PATH] [--out PATH] [--batch-size N] [--delay F]
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

import requests

API_URL = "https://little-alchemy.fandom.com/api.php"
BATCH_SIZE = 50
DELAY = 0.5  # seconds between requests


# ---------------------------------------------------------------------------


def load_titles(path: Path) -> list[str]:
    titles = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            titles.append(obj["title"])
    return titles


def fetch_batch(titles: list[str]) -> dict[str, Optional[str]]:
    """Fetch wikitext for up to 50 titles. Returns {title: wikitext|None}."""
    params = {
        "action": "query",
        "prop": "revisions",
        "rvprop": "content",
        "titles": "|".join(titles),
        "format": "json",
    }
    resp = requests.get(API_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    result: dict[str, Optional[str]] = {}
    for page in data.get("query", {}).get("pages", {}).values():
        title = page["title"]
        revs = page.get("revisions")
        result[title] = revs[0].get("*") if revs else None
    return result


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch fanon page sources from the wiki"
    )
    parser.add_argument("--pages", default="data/fanon_category/pages.jsonl")
    parser.add_argument("--out", default="data/source_of_pages.json")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--delay", type=float, default=DELAY)
    args = parser.parse_args()

    pages_path = Path(args.pages)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    titles = load_titles(pages_path)
    total = len(titles)
    print(f"Loaded {total} titles from {pages_path}")

    # Resume support: load whatever we already saved
    sources: dict[str, Optional[str]] = {}
    if out_path.exists():
        with out_path.open() as f:
            sources = json.load(f)
        already = len(sources)
        print(f"Resuming — {already} pages already fetched, skipping them.")
    else:
        already = 0

    # Filter out already-fetched titles
    remaining = [t for t in titles if t not in sources]
    print(f"Need to fetch: {len(remaining)}")

    errors = 0
    for batch_start in range(0, len(remaining), args.batch_size):
        batch = remaining[batch_start : batch_start + args.batch_size]
        done = already + batch_start + len(batch)
        pct = done / total * 100
        print(
            f"  Batch {batch_start // args.batch_size + 1:>4} "
            f"({done}/{total}  {pct:.1f}%)  ...",
            end=" ",
            flush=True,
        )
        try:
            fetched = fetch_batch(batch)
            sources.update(fetched)
            nones = sum(1 for v in fetched.values() if v is None)
            print(f"ok  (null: {nones})")
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            for t in batch:
                sources[t] = None
            errors += 1

        # Save after every batch so crashes don't lose progress
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(sources, f, ensure_ascii=False, indent=2)

        time.sleep(args.delay)

    print()
    total_null = sum(1 for v in sources.values() if v is None)
    print(f"Done. {len(sources)} pages saved → {out_path}")
    print(f"  Null (missing/deleted): {total_null}")
    if errors:
        print(f"  Network errors:        {errors} batches", file=sys.stderr)


if __name__ == "__main__":
    main()
