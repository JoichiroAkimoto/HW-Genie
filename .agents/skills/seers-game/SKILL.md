---
name: seers-game
description: HW-Genie を使用して、Seer's Game（開始→カード4択×4→終了）を Seer's Coin がなくなるまで自動周回します。
---
# Seer's Game (HW-Genie 版)
## 概要
`eventPicker_startGame` → `eventPicker_playRound`（`{"num": pick}`）×4 → `eventPicker_finishGame` の1ゲームを、Seer's Coin 枯渇（または `--max-games` 到達）まで繰り返します。引いたカードの報酬はランダムで何が出ても気にしないため、毎ラウンド固定の `pick`（デフォルト 2）を引き続けます。

- **コスト**: 1ゲーム開始に Seer's Coin を 25 枚消費（`SEERS_COIN_ID` 2824001094、`inventoryGet` の `coin` 枠。実装は `src/python/hw_genie/commands/seers_game.py`）。
- **無料分**: 毎日1回は無料で開始可能（JST 11:00 リセット）。ツールは無料/有料を区別しません — 枯渇まで回すだけです（無料分があってもなくても同じループ）。無料枠の有無は `getState` / `getInfo` に現れないため事前検出不可（枠あり・使用済みで応答同一を実機確認）。不足時は `startGame` を試行し `NotEnough` なら終了します。Live 検証済み: 残高0から無料分1ゲームが完走しコイン残高不変を確認 (2026-10-09)。
- **pick 検証**: `--pick` に `choices` 制限はありません（`event.size` が 3/4 で変動するため）。実行時に live の `event.size` に対して `1 <= pick <= size` を検証し、範囲外なら `ValueError` で失敗します。
- **中断ゲームの再開**: 初回の `eventPicker_getState` で `state == "active"` の中断ゲームがあれば、新規開始せず `playRound` から再開して `finishGame` で閉じます（開始直後の `active (round 1, size 3)` を実機で観測済み）。
- **枯渇時の挙動**: `startGame` の ERROR（コイン不足・回数上限など全種）は正常終了として `last_error` に記録し break します。通信・パース失敗（`UNEXPECTED`）は枯渇と混同せず `SeersGameReadError` を送出します（大声で失敗）。
- **`quests` 同梱**: `playRound` 応答に同梱される `quests`（28244xxxxx 系）は無視します（`questFarm` しません）。

## ワークフロー
> **Tip**: 複数のアカウントを使用している場合は、事前に `uv run hw-genie auth --list` で正しいアカウント別名を確認することを推奨します。

1. 実行コマンド:
   ```bash
   # 単一アカウント（状態と残高の確認のみ、ゲームは開始しない）
   uv run hw-genie seers-game --dry-run
   # 単一アカウント（毎ラウンド pick 2 を引く、デフォルト）
   uv run hw-genie seers-game --pick 2
   # 単一アカウント（最大5ゲームで打ち切り）
   uv run hw-genie seers-game --max-games 5
   # 全アカウント一括実行
   uv run hw-genie multi seers-game
   # 全アカウント一括の予行確認（読み取りのみ）
   uv run hw-genie multi seers-game --dry-run
   ```
   例（アカウント指定）:
   ```bash
   uv run hw-genie seers-game -a Alice --dry-run
   uv run hw-genie seers-game -a Alice --pick 2 --max-games 5
   ```
2. 実行前に状態のみ確認したい場合（ゲームは開始されません）:
   ```bash
   uv run hw-genie seers-game -a Alice --dry-run
   ```
