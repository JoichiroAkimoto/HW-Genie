#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
OUTPUT="$DIST_DIR/hw-genie-auth-capture.user.js"

# 失敗時（tsc エラー等）に一時ファイルを残さない
trap 'rm -f "$DIST_DIR/bundle.tmp.js"' EXIT

INJECT_DOWNLOAD_URL=""
INJECT_UPDATE_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --inject-url)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --inject-url requires a value" >&2
        exit 1
      fi
      # 後方互換: downloadURL / updateURL の両方に同じ値を設定する
      INJECT_DOWNLOAD_URL="$2"
      INJECT_UPDATE_URL="$2"
      shift 2
      ;;
    --inject-download-url)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --inject-download-url requires a value" >&2
        exit 1
      fi
      INJECT_DOWNLOAD_URL="$2"
      shift 2
      ;;
    --inject-update-url)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --inject-update-url requires a value" >&2
        exit 1
      fi
      INJECT_UPDATE_URL="$2"
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

# 型チェックを先に実行（bun は型を無視してビルドするため、型エラーを最速で
# 検出する）。カレントディレクトリに依存しないよう tsconfig を明示指定する。
bun x tsc --noEmit -p "$SCRIPT_DIR/tsconfig.json"

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
if ! grep -qE '^// @run-at[[:space:]]+document-idle[[:space:]]*$' <<< "$METADATA"; then
  echo "ERROR: @run-at must be document-idle (document-start conflicts with other userscripts)" >&2
  exit 1
fi

# Inject URLs if provided. downloadURL と updateURL を別々に設定できる
# （自動更新のため updateURL は常に最新リリースを指す URL を使う）。
if [[ -n "$INJECT_DOWNLOAD_URL" || -n "$INJECT_UPDATE_URL" ]]; then
  if [[ "$OSTYPE" == "darwin"* ]]; then
    [[ -n "$INJECT_DOWNLOAD_URL" ]] && sed -i '' "s|__DOWNLOAD_URL__|$INJECT_DOWNLOAD_URL|g" "$OUTPUT"
    [[ -n "$INJECT_UPDATE_URL" ]] && sed -i '' "s|__UPDATE_URL__|$INJECT_UPDATE_URL|g" "$OUTPUT"
  else
    [[ -n "$INJECT_DOWNLOAD_URL" ]] && sed -i "s|__DOWNLOAD_URL__|$INJECT_DOWNLOAD_URL|g" "$OUTPUT"
    [[ -n "$INJECT_UPDATE_URL" ]] && sed -i "s|__UPDATE_URL__|$INJECT_UPDATE_URL|g" "$OUTPUT"
  fi

  # 未置換のプレースホルダが残っていないことを検証
  if grep -q '__DOWNLOAD_URL__\|__UPDATE_URL__' "$OUTPUT"; then
    echo "ERROR: __DOWNLOAD_URL__/__UPDATE_URL__ not substituted" >&2
    exit 1
  fi
else
  # --inject-url 系なしのローカルビルド: プレースホルダが残るため警告
  if grep -q '__DOWNLOAD_URL__\|__UPDATE_URL__' "$OUTPUT"; then
    echo "WARNING: __DOWNLOAD_URL__/__UPDATE_URL__ placeholders remain (pass --inject-url for release)" >&2
  fi
fi

echo "Built: $OUTPUT"
