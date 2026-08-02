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

# 型チェック（bun は型を無視してビルドするため、CI で型エラーを検出する）。
# カレントディレクトリに依存しないよう tsconfig を明示指定する。
bun x tsc --noEmit -p "$SCRIPT_DIR/tsconfig.json"

# Combine metadata + bundle
{
  echo "$METADATA"
  echo ""
  cat "$DIST_DIR/bundle.tmp.js"
} > "$OUTPUT"

rm -f "$DIST_DIR/bundle.tmp.js"

# バンドル部分の先頭が単一 IIFE であることを強制（グローバル漏れの回帰防止）
BUNDLE_FIRST=$(sed -n '/^\/\/ ==\/UserScript==$/,$p' "$OUTPUT" | tail -n +2 | sed '/^$/d' | head -1)
BUNDLE_LAST=$(sed -n '/^\/\/ ==\/UserScript==$/,$p' "$OUTPUT" | tail -n +2 | sed '/^$/d' | tail -1)
if [[ "$BUNDLE_FIRST" != "(() => {" || "$BUNDLE_LAST" != "})();" ]]; then
  echo "ERROR: dist bundle is not wrapped in a single IIFE (first='$BUNDLE_FIRST', last='$BUNDLE_LAST')" >&2
  exit 1
fi

# 列 0 のトップレベル宣言・グローバル代入を検出（IIFE の外への漏れ。metadata は
# // 始まりなので誤検出なし）
if grep -nE '^(var|let|const|function|class|async function|export|window\.|globalThis\.)' "$OUTPUT"; then
  echo "ERROR: top-level declaration/global write leaks outside the IIFE (see lines above)" >&2
  exit 1
fi

# メタデータの必須キー検証
for key in name namespace version run-at match grant; do
  if ! grep -q "^// @$key " <<< "$METADATA"; then
    echo "ERROR: metadata missing @$key" >&2
    exit 1
  fi
done

# @run-at は document-idle でなければならない（document-start は他ユーザー
# スクリプトと競合し、HW Goodwin の UI を壊す回帰を防ぐ）
if ! grep -q '^// @run-at       document-idle$' <<< "$METADATA"; then
  echo "ERROR: @run-at must be document-idle (document-start conflicts with other userscripts)" >&2
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

  # 未置換のプレースホルダが残っていないことを検証
  if grep -q '__DOWNLOAD_URL__\|__UPDATE_URL__' "$OUTPUT"; then
    echo "ERROR: __DOWNLOAD_URL__/__UPDATE_URL__ not substituted" >&2
    exit 1
  fi
fi

echo "Built: $OUTPUT"
