---
name: consumable
description: HW-Genie を使用して所持している consumable（消費アイテム）の在庫を確認し、登録済みアイテム（Equipment Fragment Chest 等）を一括消費します。
---
# Consumable 在庫確認・一括消費 Skill (HW-Genie 版)

## ワークフロー
> **Tip**: 複数のアカウントを使用している場合は、事前に `uv run hw-genie auth --list` で正しいアカウント別名を確認することを推奨します。

1. **トリガー**: ユーザーが「在庫を確認」「consumable を消費」「宝箱を開ける」等を指示した場合に起動します。
2. **在庫確認**（指定があれば）:
   ```bash
   uv run hw-genie inventory -a <アカウント名>
   ```
   - `--all` で全カテゴリ、`--min N` で指定数以上のアイテムのみ表示、`--raw` で生 JSON。
3. **消費実行**（ユーザーが明示的に消費を指示した場合のみ）:
   ```bash
   # 登録済み対象（CONSUMABLE_USE_TARGETS）を全消費
   uv run hw-genie consumable run -a <アカウント名>
   # 特定アイテムのみ（レジストリ未登録は --method 必須）
   uv run hw-genie consumable run <libId> --method <method> -a <アカウント名>
   # 複数アカウント一括
   uv run hw-genie multi consumable <アカウント1> <アカウント2>
   # プラン確認のみ
   uv run hw-genie consumable run --dry-run -a <アカウント名>
   ```
4. **消費量**: 引数指定は不要。実行直前に `inventoryGet` で取得した実在庫数を全量消費します。在庫が無いアイテムはスキップされます。

## 登録管理（開発者向け）
- アイテムの名前・消費 RPC メソッドは `src/python/hw_genie/core/consumables.py` の `CONSUMABLE_REGISTRY` に登録します（例: `215: ConsumableInfo("Equipment Fragment Chest", "consumableUseLootBox")`）。
- 一括消費の対象は `CONSUMABLE_USE_TARGETS` リスト（hero_raid の `DEFAULT_HERO_MISSION_IDS` と同じ固定管理の流儀）。
- 新しいアイテムはまず `--method` 上書きで実績を確認してからレジストリへ追加してください。

## 注意
- 確認プロンプトなしで即座に全消費するため、対象リストの確認（`--dry-run` 推奨）を必ず行ってください。
- 1 回の API 上限（`limitReached` 等）を超えるとエラー報告のみで、自動分割はしません（実績が揃えば対応予定）。
- 認証エラーが発生した場合は、ユーザーに新しい `curl` コマンドの提供を求めるか、`auth-server` の起動を促してください。

## 完了後の報告
消費結果（消費数・報酬のカテゴリ別合計・スキップや失敗の有無）をユーザーに報告してください。`multi` 実行時はアカウント別サマリテーブル（✅ Consumed / ⏭️ Skipped / ❌ Failed）をそのまま提示できます。

## 使用例
**ユーザーの入力**: 「The Best と Champion の Equipment Fragment Chest を全部開けて」

**AI の動作**:
1. `uv run hw-genie multi consumable --dry-run "The Best" Champion` でプラン確認（在庫 48 x2 を表示）。
2. ユーザーに実行確認。
3. `uv run hw-genie multi consumable "The Best" Champion` を実行し、サマリを報告。