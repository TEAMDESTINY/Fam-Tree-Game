#!/usr/bin/env python3
"""
build_fanon_db.py
=================
Reads data/source_of_pages.json (produced by fetch_sources.py) and outputs:

  fanon_db.json             – valid elements: combinations, image_url, category flags
  fanon_auto_unlock_db.json – elements that unlock automatically
  fanon_problems.json       – elements with missing / broken recipes

Usage:
  python build_fanon_db.py [--sources PATH] [--nonfanon PATH] [--images PATH] [--out-dir DIR]
"""

import argparse
import json
import re
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RECIPES2_RE = re.compile(r"\{\{Recipes2\s*((?:\|[^|}]*)+)\}\}", re.IGNORECASE)

# Matches {{Recipes|a|b|...}} (the LA1-style template, sometimes wrongly used under LA2)
RECIPES1_RE = re.compile(r"\{\{Recipes\s*((?:\|[^|}]*)+)\}\}", re.IGNORECASE)

CATEGORY_TRIGGERS: dict[str, str] = {
    "programming language": "programming",
    "country": "country",
    "game": "gaming",
    "videogame": "gaming",
}

# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------


def load_sources(path: Path) -> dict[str, Optional[str]]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_non_fanon(path: Path) -> set[str]:
    with path.open() as f:
        raw = json.load(f)
    result: set[str] = set()
    for entry in raw:
        name = entry.strip().strip('"').lower()
        result.add(name)
        result.add(name.replace("-", " "))
        result.add(name.replace(" ", "-"))
    return result


def load_images(path: Path) -> dict[str, str]:
    images: dict[str, str] = {}
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            stem = Path(obj["name"]).stem
            images[stem.lower()] = obj["url"]
    return images


# ---------------------------------------------------------------------------
# Fanon lookup: bare lowercase name → exact wiki title
# e.g. "0.5" → "Fanon:0.5",  "shaun the sheep" → "Fanon:Shaun the sheep"
# ---------------------------------------------------------------------------


def build_fanon_lookup(sources: dict[str, Optional[str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for title in sources:
        bare = title.removeprefix("Fanon:").lower()
        lookup[bare] = title
    return lookup


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_recipes2(wikitext: str) -> list[list[str]]:
    flat = wikitext.replace("\n", "")
    pairs: list[list[str]] = []
    for m in RECIPES2_RE.finditer(flat):
        parts = [p.strip() for p in m.group(1).split("|") if p.strip()]
        for i in range(0, len(parts) - 1, 2):
            a, b = parts[i], parts[i + 1]
            if a and b:
                pairs.append([a, b])
    return pairs


def parse_la2_recipes1_block(wikitext: str) -> list[list[str]]:
    """
    Fallback: some pages wrongly use {{Recipes}} (LA1 template) under the
    Little Alchemy 2 section heading instead of {{Recipes2}}.
    Find the LA2 section, then extract ingredient pairs from the first
    {{Recipes|...}} block found there.
    Returns [] if none found.
    """
    # Find the LA2 heading
    la2_idx = wikitext.lower().find("little alchemy 2")
    if la2_idx == -1:
        return []
    la2_section = wikitext[la2_idx:]
    flat = la2_section.replace("\n", "")
    pairs: list[list[str]] = []
    for m in RECIPES1_RE.finditer(flat):
        parts = [p.strip() for p in m.group(1).split("|") if p.strip()]
        for i in range(0, len(parts) - 1, 2):
            a, b = parts[i], parts[i + 1]
            if a and b:
                pairs.append([a, b])
        break  # only the first Recipes block in the LA2 section
    return pairs


def is_auto_unlock(wikitext: str) -> bool:
    """True when LA2 section has 'Available after N elements' and no Recipes2."""
    if RECIPES2_RE.search(wikitext.replace("\n", "")):
        return False
    la2_idx = wikitext.lower().find("little alchemy 2")
    if la2_idx == -1:
        return False
    return bool(
        re.search(
            r"available after \d+ elements", wikitext[la2_idx:], re.IGNORECASE
        )
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_ingredient(
    raw: str,
    fanon_lookup: dict[str, str],
    non_fanon: set[str],
) -> Optional[str]:
    """
    Resolve a raw recipe ingredient to its canonical element name.
    Fanon elements: returns exact wiki title e.g. 'Fanon:0.5'  (NOT title-cased guessing)
    Non-fanon:      returns title-cased name
    Unknown:        returns None

    Handles:
      - ingredients written with explicit 'Fanon:' prefix e.g. '|Fanon:First|'
      - names starting with "/" like "/999" are valid (fanon elements not yet created)
      - actual wikitext leakage filtered: "#expr:...", "{{...}}", "}}" etc.
    """
    stripped = raw.strip()

    # Filter only real wikitext template leakage artifacts.
    # NOTE: leading "/" is NOT filtered — "Fanon:/999" is a legitimate (if missing) element.
    if (
        not stripped
        or stripped.startswith("#")
        or "{{" in stripped
        or "}}" in stripped
    ):
        return None

    # Strip explicit 'Fanon:' namespace prefix that wiki authors sometimes write directly
    # e.g. 'Fanon:First' -> 'First', 'fanon:0.5' -> '0.5'
    bare = re.sub(r"^fanon:", "", stripped, flags=re.IGNORECASE)
    key = bare.lower()

    # 1. Fanon lookup — uses exact wiki title casing
    if key in fanon_lookup:
        return fanon_lookup[key]

    # 2. Non-fanon
    if key in non_fanon or key.replace(" ", "-") in non_fanon:
        return _title_case(bare)

    return None


def _title_case(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split())


# ---------------------------------------------------------------------------
# Image & category helpers
# ---------------------------------------------------------------------------


def find_image(bare_name: str, images: dict[str, str]) -> Optional[str]:
    stem = bare_name.replace(" ", "_").lower()
    for candidate in [f"{stem}_2", stem]:
        url = images.get(candidate)
        if url:
            return url
    return None


def normalize_category_key(name: str) -> str:
    name = name.lower()

    # remove fanon prefix if present
    name = name.removeprefix("fanon:")

    # normalize spacing/punctuation
    name = name.replace("_", " ").strip()

    return name


def detect_categories(combinations: list[list[str]]) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for pair in combinations:
        for ing in pair:
            flag_key = normalize_category_key(ing)

            if flag_key in CATEGORY_TRIGGERS:
                flags[CATEGORY_TRIGGERS[flag_key]] = True
    return flags


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_combinations(
    raw_combos: list[list[str]],
    fanon_lookup: dict[str, str],
    non_fanon: set[str],
) -> tuple[list[list[str]], list[str]]:
    """
    Resolve every ingredient.  Drop combos where either ingredient is unknown.
    Returns (valid_combos, sorted list of missing ingredient raw names).
    """
    valid: list[list[str]] = []
    missing: set[str] = set()

    for pair in raw_combos:
        resolved = []
        ok = True
        for ing in pair:
            r = resolve_ingredient(ing, fanon_lookup, non_fanon)
            if r is None:
                missing.add(ing)
                ok = False
            else:
                resolved.append(r)
        if ok:
            valid.append(resolved)

    return valid, sorted(missing)


# ---------------------------------------------------------------------------
# Main build pipeline
# ---------------------------------------------------------------------------


def build(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Loading data...")
    sources = load_sources(Path(args.sources))
    non_fanon = load_non_fanon(Path(args.nonfanon))
    images = load_images(Path(args.images))
    fanon_lookup = build_fanon_lookup(sources)

    print(f"  Fanon pages:     {len(sources)}")
    print(f"  Non-fanon items: {len(non_fanon)}")
    print(f"  Images indexed:  {len(images)}")

    # ------------------------------------------------------------------
    # Phase 1 – bucket every page
    # ------------------------------------------------------------------
    unchecked: dict[str, list[list[str]]] = {}
    auto_unlock: dict[str, dict] = {}
    no_recipe: dict[str, dict] = {}
    contribute_to_fix: dict[
        str, list[str]
    ] = {}  # title → list of fixable issues

    for title, wikitext in sources.items():
        if wikitext is None:
            no_recipe[title] = {
                "problem": "Page source unavailable (missing or deleted)"
            }
            continue
        if is_auto_unlock(wikitext):
            m = re.search(
                r"available after \d+ elements[^\n.]*", wikitext, re.IGNORECASE
            )
            msg = (
                m.group(0).strip()
                if m
                else "Available after N elements have been unlocked."
            )
            auto_unlock[title] = {"unlock_condition": msg}
            continue
        raw_combos = parse_recipes2(wikitext)
        if not raw_combos:
            # Fallback: check if {{Recipes}} (LA1 template) was wrongly used under LA2 heading
            fallback = parse_la2_recipes1_block(wikitext)
            if fallback:
                raw_combos = fallback
                contribute_to_fix.setdefault(title, []).append(
                    "Uses {{Recipes}} instead of {{Recipes2}} under Little Alchemy 2 section"
                )
            else:
                no_recipe[title] = {
                    "problem": "No Recipes2 block and no auto-unlock text"
                }
                continue
        unchecked[title] = raw_combos

    print(
        f"\nParsed: auto_unlock={len(auto_unlock)}  no_recipe={len(no_recipe)}  "
        f"to_validate={len(unchecked)}  fixable={len(contribute_to_fix)}"
    )

    # ------------------------------------------------------------------
    # Phase 2 – validate ingredients
    # ------------------------------------------------------------------
    fanon_db: dict[str, dict] = {}
    problems: dict[str, dict] = {}

    for title, raw_combos in unchecked.items():
        valid_combos, missing_ings = validate_combinations(
            raw_combos, fanon_lookup, non_fanon
        )
        bare = title.removeprefix("Fanon:")
        image_url = find_image(bare, images)
        cats = detect_categories(valid_combos)

        if missing_ings:
            # These ingredients are genuinely unknown (not in fanon OR non-fanon)
            problems[title] = {
                "problem": "One or more recipe elements don't exist",
                "do_we_have_working_recipe_added": len(valid_combos) > 0,
                "missing_recipe_elements": missing_ings,
            }

        if valid_combos:
            fanon_db[title] = {
                "fanon": True,
                "combinations": valid_combos,
                "image_url": image_url,
                **cats,
            }
        elif title not in problems:
            problems[title] = {
                "problem": "All recipe elements are unknown – excluded",
                "do_we_have_working_recipe_added": False,
                "missing_recipe_elements": missing_ings,
            }

        # Track missing image as a fixable issue
        bare = title.removeprefix("Fanon:")
        if find_image(bare, images) is None and valid_combos:
            contribute_to_fix.setdefault(title, []).append("Missing image")

    # ------------------------------------------------------------------
    # Phase 3 – cascade: elements whose ingredients got excluded
    #
    # KEY DISTINCTION:
    #   missing_recipe_elements   = ingredient name not found anywhere (unknown)
    #   broken_dependency_elements = ingredient resolved OK but that element
    #                                itself has no valid path to craft it
    # ------------------------------------------------------------------
    excluded = set(problems.keys()) | set(no_recipe.keys())

    for pass_num in range(1, 9999):
        removed: list[str] = []

        for title, entry in list(fanon_db.items()):
            broken_deps = sorted({
                ing
                for combo in entry["combinations"]
                for ing in combo
                if ing in excluded
                # XXX: below condition is added manually. claude don't know about this change.
                # This fix the issue of items appearing as broken dependencies even when they have a working recipe added in the same cascade pass.
                #
                # XXX: And yes, this fix moved almost 800 items to db from problems list, frm 2961 to 3678 (719 items total)
                and not problems.get(ing, {}).get(
                    "do_we_have_working_recipe_added", False
                )
            })
            if not broken_deps:
                continue

            new_combos = [
                combo
                for combo in entry["combinations"]
                if not any(ing in excluded for ing in combo)
            ]

            # Update or create the problem entry (never clobber missing_recipe_elements)
            prob = problems.get(title, {})
            prev_broken = set(prob.get("broken_dependency_elements", []))
            prob["broken_dependency_elements"] = sorted(
                prev_broken | set(broken_deps)
            )

            if new_combos:
                entry["combinations"] = new_combos
                entry.update(detect_categories(new_combos))
                prob.setdefault(
                    "problem", "Some recipes removed due to broken dependencies"
                )
                prob["do_we_have_working_recipe_added"] = True
                problems[title] = prob
            else:
                prob["problem"] = (
                    "All recipes depend on excluded/broken elements"
                )
                prob["do_we_have_working_recipe_added"] = False
                problems[title] = prob
                excluded.add(title)
                del fanon_db[title]
                removed.append(title)

        print(f"  Cascade pass {pass_num}: removed {len(removed)} elements")
        if not removed:
            break

    # ------------------------------------------------------------------
    # Phase 4 – write output
    # ------------------------------------------------------------------
    for title, info in no_recipe.items():
        problems.setdefault(title, info)

    (out_dir / "fanon_db.json").write_text(
        json.dumps({"items": fanon_db}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "fanon_auto_unlock_db.json").write_text(
        json.dumps({"items": auto_unlock}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "fanon_problems.json").write_text(
        json.dumps(problems, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "fanon_contribute_to_fix.json").write_text(
        json.dumps(contribute_to_fix, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print()
    print("=" * 60)
    print(f"fanon_db.json               → {len(fanon_db):>5} elements")
    print(f"fanon_auto_unlock_db.json   → {len(auto_unlock):>5} elements")
    print(f"fanon_problems.json         → {len(problems):>5} entries")
    print(f"fanon_contribute_to_fix.json→ {len(contribute_to_fix):>5} entries")
    print(f"Output: {out_dir.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="data/source_of_pages.json")
    parser.add_argument("--nonfanon", default="data/non_fanon.json")
    parser.add_argument("--images", default="data/images_url/images.jsonl")
    parser.add_argument("--out-dir", default="data/output")
    build(parser.parse_args())


if __name__ == "__main__":
    main()
