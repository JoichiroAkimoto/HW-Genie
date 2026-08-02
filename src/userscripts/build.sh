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

# Metadata が抽出できていることを検証（空のまま成功させない）
if [[ "$METADATA" != *"==UserScript=="* ]]; then
  echo "ERROR: metadata block not found in index.ts" >&2
  exit 1
fi

# Build with bun. --format=iife wraps the bundle (including any imported
# modules) in a single IIFE so no declarations leak onto the page's global
# scope — important because other userscripts share the page (e.g. HW
# Goodwin) and the userscript runs with @grant none.
bun build "$SCRIPT_DIR/index.ts" --outfile "$DIST_DIR/bundle.tmp.js" --format=iife --target=browser

# Combine metadata + bundle
{
  echo "$METADATA"
  echo ""
  cat "$DIST_DIR/bundle.tmp.js"
} > "$OUTPUT"

rm -f "$DIST_DIR/bundle.tmp.js"

# バンドル部分の先頭が単一 IIFE であることを強制（グローバル漏れの回帰防止）
BUNDLE_FIRST=$(sed -n '/^\/\/ ==\/UserScript==$/,$p' "$OUTPUT" | tail -n +2 | sed '/^$/d' | head -1)
if [[ "$BUNDLE_FIRST" != "(() => {" ]]; then
  echo "ERROR: dist bundle is not IIFE-wrapped (global leak risk): $BUNDLE_FIRST" >&2
  exit 1
fi

# Inject URL if provided
if [[ -n "$INJECT_URL" ]]; then
  if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s@__DOWNLOAD_URL__@$INJECT_URL@g" "$OUTPUT"
    sed -i '' "s@__UPDATE_URL__@$INJECT_URL@g" "$OUTPUT"
  else
    sed -i "s@__DOWNLOAD_URL__@$INJECT_URL@g" "$OUTPUT"
    sed -i "s@__UPDATE_URL__@$INJECT_URL@g" "$OUTPUT"
  fi
fi

echo "Built: $OUTPUT"
