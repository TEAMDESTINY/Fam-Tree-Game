#!/usr/bin/env bash

URL="https://raw.githubusercontent.com/smoak/little-alchemy-two-web/refs/heads/main/src/data/db.json"

curl "$URL" -o db.json
