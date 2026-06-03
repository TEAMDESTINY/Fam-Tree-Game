#!/usr/bin/env bash

# --------------------------------------------
# Dump ONLY Category:Fanon pages (clean list)
# Uses MediaWiki categorymembers API
#
# WHY? WHY only category not all page with prefix "Fanon:"?
# Because there are many (atm like 50) Fanon elements pages that are deleted(marked to be deleted) and they are returned
# --------------------------------------------

mkdir -p data/fanon_category # output folder

cmcontinue="" # pagination token
i=1           # batch counter

while true; do
  # Base request: category members (NOT allpages)
  url="https://little-alchemy.fandom.com/api.php?action=query&list=categorymembers&cmtitle=Category:Fanon&cmlimit=500&format=json"

  # Add continuation token if present
  if [ -n "$cmcontinue" ]; then
    url="$url&cmcontinue=$cmcontinue"
  fi

  echo "Fetching batch $i..."

  # Fetch API response
  res=$(curl -s "$url")

  # Save raw batch (for debugging / replay)
  echo "$res" >"data/fanon_category/batch_$i.json"

  # Append clean structured entries (JSONL format)
  echo "$res" | jq -c '.query.categorymembers[]' >>"data/fanon_category/pages.jsonl"

  # Get next continuation token
  cmcontinue=$(echo "$res" | jq -r '.continue.cmcontinue // empty')

  # Stop if no more pages
  [ -z "$cmcontinue" ] && break

  i=$((i + 1))
done
