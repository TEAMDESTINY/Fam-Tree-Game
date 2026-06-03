mkdir -p images_file_pngs_compressed

files=(images_file_pngs/*.png)
total=${#files[@]}
count=0
max_jobs=10

process_file() {
  f="$1"
  name=$(basename "$f")

  magick "$f" \
    -resize 100x100 \
    -strip \
    -colors 64 \
    -define png:compression-level=9 \
    PNG8:"images_file_pngs_compressed/$name"
}

for f in "${files[@]}"; do
  process_file "$f" &

  count=$((count + 1))

  # throttle
  while (($(jobs -r | wc -l) >= max_jobs)); do
    sleep 0.1
  done

  echo "Queued [$count/$total]"
done

wait

echo "Done: $count/$total files processed"
