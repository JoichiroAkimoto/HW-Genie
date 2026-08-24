# Demo GIF 再生成手順

このディレクトリは README に埋め込むデモ GIF (`docs/assets/hw-genie-demo.gif`) の **再現ソース** です。実 API/DB には触れません（`mock_uv.sh` がダミー出力を再生）。

## 生成物

- `docs/assets/hw-genie-demo.gif` — `vhs` で生成された GIF（約 3.1 MB, 1200×700, 約 29 秒, Dracula テーマ）
- ソース: `scripts/demo/demo.tape`（VHS テープ）+ `scripts/demo/mock_uv.sh`（`uv` シム）

> **Git LFS 見送りの根拠**: GIF は単発・約 3.1 MB のみ。今後の追加予定はなく、リポジトリ肥大化の牽引にはなりません。LFS 導入の複雑さに見合わないため通常の Git 管理としています（`.gitattributes` で `*.gif binary linguist-generated` を付与）。

## 必要環境

- `vhs` 0.11.0+ (`brew install vhs` — `ffmpeg` `ttyd` も依存で導入)
- `bash` 3.2+（macOS 標準 / Linux `bash`）
- `sleep` / `mktemp`（coreutils）

フォントは `Menlo, JetBrains Mono, monospace` の順にフォールバックします。Linux で Menlo が無い場合は代替フォントで表の幅が若干変わりますが、崩れません（Width 1200 で検証済み）。

## 再生成

**必ずリポジトリルートから実行してください**（`$PWD` 依存の相対パスを使用）。

```bash
vhs scripts/demo/demo.tape
ls -lh docs/assets/hw-genie-demo.gif
# 必要であれば手動で最適化（任意）:
# ffmpeg -i docs/assets/hw-genie-demo.gif -vf "fps=15,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer" docs/assets/hw-genie-demo-optimized.gif
```

テープ内の `Sleep` 値は `mock_uv.sh` の `say` 合計（約 13 秒）+ `TypingSpeed 60ms` を考慮して設定しています。クリーンアップはテープ末尾で `rm -rf "$MOCK_BIN_DIR"` を実行し、`trap` でも EXIT 時に削除します。

## 収録内容

1. `uv run hw-genie auth --list` — 4 アカウント表（Arthur / Morgana / Elyndra / Kaito。Arena 1位を含む黄色ハイライト、Energy over cap は赤）
2. `clear` → `uv run hw-genie daily --account Arthur` — Hero Raid 3件 Skipping + 4件実行 → Exchanging Soul Stones → Item Raid 2回 → Soul Shop Slot 2-6 購入 5件 → Account Status（212/190 over cap 赤）→ 10028 Titan Artifact 実行 → 完了

最後は `🏁 Daily Routine Completed.` を約 3 秒静止してから終了します。
