#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
OUTPUT="$DIST_DIR/hw-genie-auth-capture.user.js"

INJECT_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --inject-url)
      INJECT_URL="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

mkdir -p "$DIST_DIR"

# Extract metadata comments from source
METADATA=$(sed -n '/^\/\/ ==UserScript==$/,/^\/\/ ==\/UserScript==$/p' "$SCRIPT_DIR/index.ts")

# Build with bun
bun build "$SCRIPT_DIR/index.ts" --outfile "$DIST_DIR/bundle.tmp.js" 2>/dev/null

# Combine metadata + bundle
{
  echo "$METADATA"
  echo ""
  cat "$DIST_DIR/bundle.tmp.js"
} > "$OUTPUT"

rm -f "$DIST_DIR/bundle.tmp.js"

# Inject URL if provided
if [[ -n "$INJECT_URL" ]]; then
  if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s|__DOWNLOAD_URL__|$INJECT_URL|g" "$OUTPUT"
    sed -i '' "s|__UPDATE_URL__|$INJECT_URL|g" "$OUTPUT"
  else
    sed -i "s|__DOWNLOAD_URL__|$INJECT_URL|g" "$OUTPUT"
    sed -i "s|__UPDATE_URL__|$INJECT_URL|g" "$OUTPUT"
  fi
fi

echo "Built: $OUTPUT"
