#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
OUTPUT="$DIST_DIR/hw-genie-auth-capture.user.js"

# 失敗時（tsc エラー等）に一時ファイルを残さない
trap 'rm -f "$DIST_DIR/bundle.tmp.js"' EXIT

INJECT_DOWNLOAD_URL=""
INJECT_UPDATE_URL=""
BUN_MINIFY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --minify)
      BUN_MINIFY="--minify"
      shift
      ;;
    --inject-url)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --inject-url requires a value" >&2
        exit 1
      fi
      # downloadURL / updateURL の両方に同じ値を設定する
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
# --minify を渡すと 1 行圧縮される（IIFE 構造検証はどちらでも通る）。
bun build "$SCRIPT_DIR/index.ts" --outfile "$DIST_DIR/bundle.tmp.js" --format=iife --target=browser $BUN_MINIFY

# Combine metadata + bundle
{
  echo "$METADATA"
  echo ""
  cat "$DIST_DIR/bundle.tmp.js"
} > "$OUTPUT"

# バンドル部分の先頭が単一 IIFE であることを強制（グローバル漏れの回帰防止）。
# 構造検証は「先頭が (()=>{ / (() => { で始まり、末尾が }); で終わる」こと。
# 非 minify（複数行）でも --minify（1 行圧縮）でも動作する。
BUNDLE=$(sed -n '/^\/\/ ==\/UserScript==$/,$p' "$OUTPUT" | tail -n +2 | sed '/^$/d')
# 先頭の空白を除去してから判定
BUNDLE_TRIMMED=${BUNDLE#"${BUNDLE%%[![:space:]]*}"}
IIFE_OPEN_RE='^\(\(\)[[:space:]]*=>[[:space:]]*\{'
if [[ ! "$BUNDLE_TRIMMED" =~ $IIFE_OPEN_RE || "$BUNDLE_TRIMMED" != *"})();" ]]; then
  echo "ERROR: dist bundle is not wrapped in a single IIFE (first='${BUNDLE_TRIMMED:0:40}…', last='…${BUNDLE_TRIMMED: -40}')" >&2
  exit 1
fi

# 列 0 のトップレベル宣言・グローバル代入の検出は、IIFE 構造検証（先頭
# (()=>{ / 末尾 });）が通れば構造的に発生しないため不要。
# （1 行 minify では var が列 0 に現れるため grep ベースは誤検出する。）

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

# sed の replacement 部で特殊な文字（& はマッチ全体、\ はエスケープ、| は
# 区切り文字）をエスケープしてから注入する。
escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[&\\|]/\\&/g'
}

# Inject URLs if provided. downloadURL と updateURL を別々に設定できる
# （自動更新のため updateURL は常に最新リリースを指す URL を使う）。
# 引数解析は「最後に指定した値が有効」（後勝ち）。
if [[ -n "$INJECT_DOWNLOAD_URL" || -n "$INJECT_UPDATE_URL" ]]; then
  # downloadURL / updateURL は対で指定する。片方のみだと未置換プレースホルダが
  # 残り下の検証で失敗するため、ここで明示的に弾く。
  if [[ -z "$INJECT_DOWNLOAD_URL" || -z "$INJECT_UPDATE_URL" ]]; then
    echo "ERROR: --inject-download-url and --inject-update-url must be specified together (or use --inject-url for both)" >&2
    exit 1
  fi
  DOWNLOAD_SAFE=$(escape_sed_replacement "$INJECT_DOWNLOAD_URL")
  UPDATE_SAFE=$(escape_sed_replacement "$INJECT_UPDATE_URL")
  if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s|__DOWNLOAD_URL__|$DOWNLOAD_SAFE|g" "$OUTPUT"
    sed -i '' "s|__UPDATE_URL__|$UPDATE_SAFE|g" "$OUTPUT"
  else
    sed -i "s|__DOWNLOAD_URL__|$DOWNLOAD_SAFE|g" "$OUTPUT"
    sed -i "s|__UPDATE_URL__|$UPDATE_SAFE|g" "$OUTPUT"
  fi

  # 未置換のプレースホルダが残っていないことを検証
  if grep -qE '__DOWNLOAD_URL__|__UPDATE_URL__' "$OUTPUT"; then
    echo "ERROR: __DOWNLOAD_URL__/__UPDATE_URL__ not substituted" >&2
    exit 1
  fi
else
  # --inject-url 系なしのローカルビルド: プレースホルダが残るため警告
  if grep -qE '__DOWNLOAD_URL__|__UPDATE_URL__' "$OUTPUT"; then
    echo "WARNING: __DOWNLOAD_URL__/__UPDATE_URL__ placeholders remain (pass --inject-url, or --inject-download-url/--inject-update-url, for release)" >&2
  fi
fi

echo "Built: $OUTPUT"
