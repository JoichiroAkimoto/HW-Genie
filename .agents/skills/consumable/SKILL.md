---
name: consumable
description: HW-Genie を使用して所持している consumable（消費アイテム）の在庫を確認し、登録済みアイテム（Equipment Fragment Chest、各種宝箱・クリスタル等）を一括消費します。
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
5. **再帰消費（自動）**: 全対象の消費後、再度 `inventoryGet` で残りを確認し、マトリョーシカ系アイテム（Ancient Titan Artifact Chest / Adventure Chest / Cosmic Titans Battle Chest / Cosmic Battle Chest 等、開封で同種が再出現するもの）や取り残しが無くなるまでラウンドを繰り返します。1000 上限アイテム（Random Crystal 等）は 1 リクエスト 1000 個ずつに分割して消費します。

## 登録管理（開発者向け）
- アイテムの名前・消費 RPC メソッドは `src/python/hw_genie/core/consumables.py` の `CONSUMABLE_REGISTRY` に登録します（例: `215: ConsumableInfo("Equipment Fragment Chest", "consumableUseLootBox")`）。レジストリと `CONSUMABLE_USE_TARGETS` はカテゴリ（Stamina / Titan・Artifact Chests / Crystals / Equipment Fragment Boxes / Other Chests）ごとにセクション分けされており、追加時は両方に同じセクション順で追記します。
- 1 リクエストあたりの消費上限（例: 1000 個）は `ConsumableInfo` の `max_amount` で指定します（0 = 制限なし）。
- 選択式報酬ボックス（Chest of X Titans / Titan of Your Choice / Artifact 系 Chest 等）は `ConsumableInfo` の `player_reward_choice_index` で報酬選択インデックスを指定します（例: Titan チェストは 2、Titan of Your Choice は 0、Artifact チェストは 4。`consumableUseLootBox` の args に `playerRewardChoiceIndex` として渡されます）。
- 一括消費の対象は `CONSUMABLE_USE_TARGETS` リスト（hero_raid の `DEFAULT_HERO_MISSION_IDS` と同じ固定管理の流儀）。
- 新しいアイテムはまず `--method` 上書きで実績を確認してからレジストリへ追加してください。

## 注意
- 確認プロンプトなしで即座に全消費するため、対象リストの確認（`--dry-run` 推奨）を必ず行ってください。
- 消費は「在庫取得 → 全消費 → 残り確認」のラウンドを残りが無くなるまで繰り返します（最大 30 ラウンドの安全弁付き。打ち切った場合は残り在庫を警告表示）。
- 1 回の API 上限（`limitReached` 等）による失敗はエラー報告のみで、そのアイテムは以降のラウンドで再試行しません（日次上限のため再試行しても成功しないため）。
- 認証エラーが発生した場合は、ユーザーに新しい `curl` コマンドの提供を求めるか、`auth-server` の起動を促してください。

## 完了後の報告
消費結果（消費数・報酬のカテゴリ別合計・スキップや失敗の有無）をユーザーに報告してください。`multi` 実行時はアカウント別サマリテーブル（✅ Consumed / ⏭️ Skipped / ❌ Failed）をそのまま提示できます。

## 使用例
**ユーザーの入力**: 「The Best と Champion の Equipment Fragment Chest を全部開けて」

**AI の動作**:
1. `uv run hw-genie multi consumable --dry-run "The Best" Champion` でプラン確認（在庫 48 x2 を表示）。
2. ユーザーに実行確認。
3. `uv run hw-genie multi consumable "The Best" Champion` を実行し、サマリを報告。