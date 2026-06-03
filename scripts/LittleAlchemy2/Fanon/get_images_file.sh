#!/usr/bin/env bash

# ------------------------------------------------------------
# Download SVG images from fanon DB JSON
#
# INPUT:
#   data/output/fanon_db.json
#
# OUTPUT:
#   images_file/
#
# FEATURES:
#   - Silent curl (no progress bar noise)
#   - Safe TSV parsing (handles spaces, colons, special chars)
#   - Accurate progress counter (no subshell bug)
#   - Retry logic for reliability
#   - Skips missing URLs
#   - Skips already downloaded files
#   - Clean filename sanitization
# ------------------------------------------------------------

set -euo pipefail

# ------------------------------------------------------------
# Input JSON file
# ------------------------------------------------------------
INPUT_JSON="data/output/fanon_db.json"

# ------------------------------------------------------------
# Output directory for downloaded SVG files
# ------------------------------------------------------------
OUTPUT_DIR="images_file"

# Ensure output directory exists
mkdir -p "$OUTPUT_DIR"

# ------------------------------------------------------------
# Get total number of items for progress display
# ------------------------------------------------------------
TOTAL=$(jq '.items | length' "$INPUT_JSON")

# Counter for progress tracking
COUNT=0

echo "Total files to process: $TOTAL"

# ------------------------------------------------------------
# Read JSON safely using TSV format + process substitution
#
# Why TSV?
# - avoids breaking on spaces or special characters like ":"
# - safer than "read name url" with space splitting
# ------------------------------------------------------------
while IFS=$'\t' read -r name url; do

  # Increase progress counter
  COUNT=$((COUNT + 1))

  echo "[$COUNT / $TOTAL] Processing: $name"

  # ----------------------------------------------------------
  # Clean URL:
  # - remove carriage returns
  # - trim whitespace
  # ----------------------------------------------------------
  url=$(echo "$url" | tr -d '\r' | xargs)

  # ----------------------------------------------------------
  # Skip invalid or empty URLs
  # ----------------------------------------------------------
  if [[ -z "$url" || "$url" == "null" ]]; then
    echo "[$COUNT / $TOTAL] Skipping (no image_url)"
    continue
  fi

  # ----------------------------------------------------------
  # Sanitize filename for filesystem safety
  # Replace:
  #   / : space → _
  # ----------------------------------------------------------
  safe_name=$(echo "$name" | tr '/: ' '___')

  # Final output file path
  output_file="$OUTPUT_DIR/${safe_name}.svg"

  # ----------------------------------------------------------
  # Skip download if file already exists
  # ----------------------------------------------------------
  if [[ -f "$output_file" ]]; then
    echo "[$COUNT / $TOTAL] Already exists: $safe_name"
    continue
  fi

  echo "[$COUNT / $TOTAL] Downloading: $safe_name"

  # ----------------------------------------------------------
  # curl options:
  #
  # -f  → fail silently on HTTP errors (404, 500, etc.)
  # -s  → silent mode (no progress meter)
  # -S  → still show errors if they happen
  # -L  → follow redirects
  #
  # Retry:
  #   --retry 3 → retry up to 3 times
  #   --retry-delay 1 → wait 1 second between retries
  # ----------------------------------------------------------
  if ! curl -fsSL --retry 3 --retry-delay 1 "$url" -o "$output_file"; then

    # If download fails, clean up partial file
    echo "[$COUNT / $TOTAL] FAILED: $url"
    rm -f "$output_file"

    continue
  fi

done < <(
  # ------------------------------------------------------------
  # Convert JSON → TSV:
  #   column 1 = key (name)
  #   column 2 = image_url
  # ------------------------------------------------------------
  jq -r '
    .items
    | to_entries[]
    | [.key, .value.image_url]
    | @tsv
  ' "$INPUT_JSON"
)

echo "Done. Files saved in $OUTPUT_DIR/"
