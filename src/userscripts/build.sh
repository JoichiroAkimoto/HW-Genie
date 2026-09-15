#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
VARIANT=""

# 失敗時（tsc エラー等）に一時ファイルを残さない
trap 'rm -f "$DIST_DIR/bundle.tmp.js"' EXIT

INJECT_DOWNLOAD_URL=""
INJECT_UPDATE_URL=""
BUN_MINIFY=""

# エントリ定義: "source:basename:expected-run-at"
# 単一スクリプト構成。@run-at は document-start で ToE トラップを
# バンドル実行前に仕掛け、XHR フック等は DOMContentLoaded まで遅延させて
# HW Goodwin と共存する（詳細は index.ts）。
ENTRIES=(
  "index.ts:hw-genie-auth-capture.user.js:document-start"
)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev)
      VARIANT="dev"
      shift
      ;;
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

# dev+inject の組み合わせは副作用（tsc/bun/dist 書き込み）の前に即失敗させる。
if [[ "$VARIANT" == "dev" ]] && [[ -n "$INJECT_DOWNLOAD_URL" || -n "$INJECT_UPDATE_URL" ]]; then
  echo "ERROR: --inject-* cannot be combined with --dev (dev builds must never auto-update)" >&2
  exit 1
fi

mkdir -p "$DIST_DIR"

# 型チェックを先に実行（bun は型を無視してビルドするため、型エラーを最速で
# 検出する）。カレントディレクトリに依存しないよう tsconfig を明示指定する。
bun x tsc --noEmit -p "$SCRIPT_DIR/tsconfig.json"

# sed の replacement 部で特殊な文字（& はマッチ全体、\ はエスケープ、| は
# 区切り文字）をエスケープしてから注入する。
escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[&\\|]/\\&/g'
}

build_entry() {
  local entry="$1" basename="$2" expected_run_at="$3"
  local output="$DIST_DIR/$basename"
  local dev_name=""

  if [[ "$VARIANT" == "dev" ]]; then
    # 並行インストール用に @name / @namespace を Dev 版に置換
    dev_name="HW-Genie Auth Capture (Dev)"
    output="${output%.user.js}-dev.user.js"
  fi

  # Extract metadata comments from source
  local metadata
  metadata=$(sed -n '/^\/\/ ==UserScript==$/,/^\/\/ ==\/UserScript==$/p' "$SCRIPT_DIR/$entry")

  # Metadata が抽出できていることを検証（空のまま成功させない）
  if [[ "$metadata" != *"==UserScript=="* ]]; then
    echo "ERROR: metadata block not found in $entry" >&2
    exit 1
  fi

  if [[ -n "$dev_name" ]]; then
    metadata=$(printf '%s\n' "$metadata" | sed -E "s|^// @name[[:space:]]+.*|// @name         $dev_name|" | sed -E 's|^// @namespace[[:space:]]+.*|// @namespace    https://github.com/JoichiroAkimoto/HW-Genie-dev|')
    # @version が厳密な X.Y.Z でなければ -dev 付与が無言 no-op になるため先に検証する。
    if ! grep -qE '^// @version[[:space:]]+[0-9]+\.[0-9]+\.[0-9]+$' <<< "$metadata"; then
      echo "ERROR: @version must be strict X.Y.Z for -dev rewrite ($entry)" >&2
      exit 1
    fi
    # 開発版であることが一目で分かるよう @version に -dev を付与する。
    metadata=$(printf '%s\n' "$metadata" | sed -E 's|^(// @version[[:space:]]+[0-9]+\.[0-9]+\.[0-9]+)$|\1-dev|')
    echo "Variant: dev → $basename @name/@namespace/@version rewritten for parallel install" >&2
  fi

  # Build with bun. --format=iife wraps the bundle (including any imported
  # modules) in a single IIFE so no declarations leak onto the page's global
  # scope — important because other userscripts share the page (e.g. HW
  # Goodwin) and the userscript runs with @grant none.
  # --minify を渡すと 1 行圧縮される（IIFE 構造検証はどちらでも通る）。
  bun build "$SCRIPT_DIR/$entry" --outfile "$DIST_DIR/bundle.tmp.js" --format=iife --target=browser $BUN_MINIFY

  # Combine metadata + bundle
  {
    echo "$metadata"
    if [[ -n "$dev_name" ]]; then
      # 開発版バナー: Tampermonkey のプレビューや先頭行で開発版と分かるようにする。
      echo "// ⚠️ DEV BUILD — ToE自動化の都度ON/OFFする開発版。常用時はOFFにすること。"
    fi
    echo ""
    cat "$DIST_DIR/bundle.tmp.js"
  } > "$output"

  if [[ -n "$dev_name" ]]; then
    # 開発版のコンソールprefixを分離し、通常版のログと混ざらないようにする。
    if [[ "$OSTYPE" == "darwin"* ]]; then
      sed -i '' "s|\[HW-Genie/ToE\]|[HW-Genie/ToE Dev]|g" "$output"
    else
      sed -i "s|\[HW-Genie/ToE\]|[HW-Genie/ToE Dev]|g" "$output"
    fi
  fi

  # バンドル部分の先頭が単一 IIFE であることを強制（グローバル漏れの回帰防止）。
  # 構造検証は「先頭が (()=>{ / (() => { で始まり、末尾が }); で終わる」こと。
  # 非 minify（複数行）でも --minify（1 行圧縮）でも動作する。
  local bundle bundle_trimmed
  bundle=$(sed -n '/^\/\/ ==\/UserScript==$/,$p' "$output" | tail -n +2 | sed '/^$/d' | sed '/^\/\/ ⚠️ DEV BUILD/d')
  # 先頭の空白を除去してから判定
  bundle_trimmed=${bundle#"${bundle%%[![:space:]]*}"}
  local iife_open_re='^\(\(\)[[:space:]]*=>[[:space:]]*\{'
  if [[ ! "$bundle_trimmed" =~ $iife_open_re || "$bundle_trimmed" != *"})();" ]]; then
    echo "ERROR: dist bundle is not wrapped in a single IIFE (first='${bundle_trimmed:0:40}…', last='…${bundle_trimmed: -40}')" >&2
    exit 1
  fi

  # 列 0 のトップレベル宣言・グローバル代入の検出は、IIFE 構造検証（先頭
  # (()=>{ / 末尾 });）が通れば構造的に発生しないため不要。
  # （1 行 minify では var が列 0 に現れるため grep ベースは誤検出する。）

  # メタデータの必須キー検証
  local key
  for key in name namespace version run-at match grant; do
    if ! grep -q "^// @$key " <<< "$metadata"; then
      echo "ERROR: metadata missing @$key ($entry)" >&2
      exit 1
    fi
  done

  # エントリごとの @run-at を検証する
  if ! grep -qE "^// @run-at[[:space:]]+$expected_run_at[[:space:]]*$" <<< "$metadata"; then
    echo "ERROR: @run-at must be $expected_run_at ($entry)" >&2
    exit 1
  fi

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
    local download_safe update_safe
    download_safe=$(escape_sed_replacement "$INJECT_DOWNLOAD_URL")
    update_safe=$(escape_sed_replacement "$INJECT_UPDATE_URL")
    if [[ "$OSTYPE" == "darwin"* ]]; then
      sed -i '' "s|__DOWNLOAD_URL__|$download_safe|g" "$output"
      sed -i '' "s|__UPDATE_URL__|$update_safe|g" "$output"
    else
      sed -i "s|__DOWNLOAD_URL__|$download_safe|g" "$output"
      sed -i "s|__UPDATE_URL__|$update_safe|g" "$output"
    fi

    # 未置換のプレースホルダが残っていないことを検証
    if grep -qE '__DOWNLOAD_URL__|__UPDATE_URL__' "$output"; then
      echo "ERROR: __DOWNLOAD_URL__/__UPDATE_URL__ not substituted" >&2
      exit 1
    fi
  else
    # --inject-url 系なしのローカルビルド: プレースホルダが残るため警告
    if grep -qE '__DOWNLOAD_URL__|__UPDATE_URL__' "$output"; then
      echo "WARNING: __DOWNLOAD_URL__/__UPDATE_URL__ placeholders remain (pass --inject-url, or --inject-download-url/--inject-update-url, for release)" >&2
    fi
  fi

  echo "Built: $output"
}

for spec in "${ENTRIES[@]}"; do
  IFS=':' read -r entry basename expected_run_at <<< "$spec"
  build_entry "$entry" "$basename" "$expected_run_at"
done
