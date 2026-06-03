#!/usr/bin/env bash

# -------------------------------------------------
# Dump all images from Fandom wiki
#
# Output structure:
#   data/images_url/images_url_1.json ...
#   data/images_url/images.jsonl   (merged index)
# -------------------------------------------------

mkdir -p data/images_url

aicontinue=""
i=1

# reset merged output file
>data/images_url/images.jsonl

while true; do
  # ---------------------------------------------
  # MediaWiki API: list=allimages
  # - ailimit=500 → batch size
  # ---------------------------------------------
  url="https://little-alchemy.fandom.com/api.php?action=query&list=allimages&ailimit=500&format=json"

  # add continuation token if needed
  if [ -n "$aicontinue" ]; then
    url="$url&aicontinue=$aicontinue"
  fi

  echo "Fetching batch $i..."

  # fetch API response
  res=$(curl -s "$url")

  # ---------------------------------------------
  # Save raw batch (debug / replay)
  # ---------------------------------------------
  echo "$res" >"data/images_url/images_url_${i}.json"

  # ---------------------------------------------
  # Append merged JSONL dataset
  # ---------------------------------------------
  echo "$res" | jq -c '.query.allimages[]' >>data/images_url/images.jsonl

  # get next page token
  aicontinue=$(echo "$res" | jq -r '.continue.aicontinue // empty')

  # stop when done
  [ -z "$aicontinue" ] && break

  i=$((i + 1))
done
