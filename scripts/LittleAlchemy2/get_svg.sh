#!/usr/bin/env bash

set -euo pipefail

INPUT_JSON="db.json"
OUTPUT_DIR="svgs"

mkdir -p "$OUTPUT_DIR"

jq -r '
  .items
  | to_entries[]
  | "\(.key) \(.value.image_url)"
' "$INPUT_JSON" | while read -r name url; do
  if [[ -n "$url" && "$url" != "null" ]]; then
    echo "Downloading $name from $url"
    curl -sSL "$url" -o "$OUTPUT_DIR/${name}.svg"
  else
    echo "Skipping $name (no image_url)"
  fi
done

echo "Done. Files saved in $OUTPUT_DIR/"
