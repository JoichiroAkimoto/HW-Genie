# Demo GIF 再生成手順

このディレクトリは README に埋め込むデモ GIF (`docs/assets/hw-genie-demo.gif`) の **再現ソース** です。実 API/DB には触れません（`mock_uv.sh` がダミー出力を再生）。

## 生成物

- `docs/assets/hw-genie-demo.gif` — `vhs` で生成された GIF（約 2.4 MB 圧縮後 / 3.8 MB 生、1100×800, 約 33 秒, Dracula テーマ）
- ソース: `scripts/demo/demo.tape`（VHS テープ）+ `scripts/demo/mock_uv.sh`（`uv` シム）

> **Git LFS 見送りの根拠**: GIF は単発・約 2.4 MB（圧縮後）のみ。今後の追加予定はなく、リポジトリ肥大化の牽引にはなりません。LFS 導入の複雑さに見合わないため通常の Git 管理としています（`.gitattributes` で `*.gif binary linguist-generated` を付与）。

## 必要環境

- `vhs` 0.11.0+ (`brew install vhs` — `ffmpeg` `ttyd` も依存で導入)
- `zsh` + `zsh-autosuggestions`（ghost 補完）のみ。`~/.zshrc` は読まず、`if [[ -f $(brew --prefix 2>/dev/null)/share/... ]]; then ...; elif [[ -f /opt/homebrew/... ]]; then ...; elif [[ -f /usr/local/... ]]; then ...; fi` の3分岐で可搬性を確保し、`PS1="%F{green}➜%f %F{cyan}~/dev/HW-Genie%f $ "` で完結（ユーザ環境に依存しない）
- `gifsicle` (`brew install gifsicle`) — 生成後の圧縮に使用
- `sleep` / `mktemp`（coreutils）

フォントは `Menlo, JetBrains Mono, monospace` の順にフォールバックします。Linux で Menlo が無い場合は代替フォントで表の幅が若干変わりますが、崩れません（1100×800 で検証済み、表がギリギリ収まる幅）。

## 再生成

**必ずリポジトリルートから実行してください**（`$PWD` 依存の相対パスを使用）。

```bash
vhs scripts/demo/demo.tape
# 毎回圧縮（約 30-40% 削減、画質はほぼ維持。BSD mktemp は末尾が XXXXXX である必要があるため .gif は付けない）
out=$(mktemp "${TMPDIR:-/tmp}/hw-genie-gif-XXXXXX") && gifsicle -O3 --lossy=30 --colors 128 docs/assets/hw-genie-demo.gif -o "$out" && mv "$out" docs/assets/hw-genie-demo.gif
ls -lh docs/assets/hw-genie-demo.gif
```

テープ内の `Sleep` 値は `mock_uv.sh` の `say` 合計（約 14 秒）+ `TypingSpeed 60ms` を考慮して設定しています。クリーンアップはテープ末尾で `rm -rf "$MOCK_BIN_DIR"` を実行し、`trap` でも EXIT 時に削除します。`auth` と `daily` の間は `clear` せず改行で間を空け、両方の出力が連続して見えるようにしています。

## 収録内容

1. `uv run hw-genie auth --list` — 4 アカウント表（Arthur 436 / Morgana 178 / Elyndra 142 / Kaito 88。Arena 1位を含む黄色ハイライト、Energy over cap は赤、Updated は `2026-08-24 10:27:48` フル形式。Kaito は Arena 15 / GA 18 で色なし）
2. ↓ 改行3回で間を空け → `uv run hw-genie daily --account Arthur` — Hero Raid 3件 Skipping + 4件実行 → Exchanging Soul Stones → Item Raid 2回 → Soul Shop Slot 2-6 購入 5件 → Account Status（212/190 → 42/190 に減少、表の 436 から徐々に減少）→ 10028 Titan Artifact 実行 → 完了。入力時は `zsh-autosuggestions` によるグレー ghost 補完が表示されます

最後は `🏁 Daily Routine Completed.` を約 4 秒静止してから終了します。
