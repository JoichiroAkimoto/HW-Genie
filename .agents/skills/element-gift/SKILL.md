---
name: element-gift
description: HW-Genie を使用してエレメントギフト（heroTitanGiftLevelUp / heroTitanGiftDrop）を一括操作します。全ヒーローの MAX アクティベート（コインが続くところまで）と、低 power ヒーローのギフトを降ろしてコインを全額回収するリセットを実行できます。
---
# Element Gift (HW-Genie 版)

## 概要
月 1 回の「所持パワーを競う」イベント用に、エレメントギフトをまとめて上げ／まとめます。

- **`gift up`**: 未 MAX のヒーローを **power 降順に 1 体ずつ MAX（レベル 30）まで**上げます。次の 1 体を MAX にできるコインが残っていなければそこで打ち切り（全員に薄く分配しません）。1 体 = レベル分だけの calls を並べた **1 リクエスト**で送ります。
- **`gift reset`**: **実行時点の power（ギフト込み）が `--max-power`（既定 100,000）以下**で、かつギフトが载っているヒーローを drop します。drop は `titanCoinsSpent` を**全額返却**するので、イベント後の回収は実質タダです。
- **状態は DB に持ちません**。`heroGetAll` の `titanGiftLevel` / `titanCoinsSpent.consumable.24` / `power` がサーバー側の正で、実行のたびに読み直します（手動でギフトを触った後でも計画が自動追随）。だから `enabled` ゲートも実行時刻記録もありません。

### 実測で確定している数値（2026-11-04 live 検証）
- ギフトのコイン = consumable **libId 24**。
- **0 → MAX（レベル 30）= 65,150 コイン**。MAX ヒーローは全員 `titanCoinsSpent.consumable.24 = 65150` で一致し、実行的にも見積 65,150 と実消費が同額でした。
- **drop の返却 = 消費額と同一**（65,150 を消費 → 65,150 が返り、残高が実行前に復旧することを確認）。
- **MAX で power +約 4,000**（39,382 → 43,342、+10% 前後）。弱いヒーローは MAX でも 10 万を超えないため、既定の `--max-power 100000` で回収対象になります。
- 30  calls を 1 リクエストに並べる送信形式（`ident`: `body`, `body_1`, …）はゲームクライアントと同じ形で受理されます。

## ワークフロー
> **Tip**: 先に `uv run hw-genie auth --list` でアカウント別名を確認してください。

1. 現況確認（読み取りのみ。コイン残高・MAX 体数・up 可能体数・reset 対象体数と返却額を出す）:
   ```bash
   uv run hw-genie gift status -a Alice
   uv run hw-genie gift status -a Alice --raw   # パース済み JSON を出力
   ```
2. イベント前の MAX 化（予行 → 本実行）:
   ```bash
   uv run hw-genie gift up -a Alice --dry-run
   uv run hw-genie gift up -a Alice --limit 1 --yes   # まず 1 体だけ試す
   uv run hw-genie gift up -a Alice --yes             # コインが続く限り全ヒーロー
   ```
   `--dry-run` は状態読み取り（`heroGetAll` + `inventoryGet` 1 リクエスト）のみで、操作 RPC は送りません。`--yes` なしでは実行前に y/N 確認を求め、標準入力が読めない環境（パイプ・cron）では中断します。
3. イベント後の回収:
   ```bash
   uv run hw-genie gift reset -a Alice --dry-run              # 回収対象を確認
   uv run hw-genie gift reset -a Alice --yes                  # power 10 万以下を drop
   uv run hw-genie gift reset -a Alice --max-power 43342 --limit 1 --yes   # 特定体だけを返す
   ```
4. 全アカウント一括（`multi` は非対話なので **`--yes` 必須**。無いと exit 2 で説明を出します）:
   ```bash
   uv run hw-genie multi gift-up --dry-run          # 読み取りのみ・逐次実行
   uv run hw-genie multi gift-up --yes --parallel 2
   uv run hw-genie multi gift-reset --yes --max-power 100000
   ```

## 引数
| コマンド | 引数 | 既定 | 説明 |
|---|---|---|---|
| `gift status` | `--raw` | off | `heroGetAll` から抽出したギフト状態を JSON で出力 |
| `gift up` | `--limit N` | 無制限 | この実行で MAX にする体数を N 体に制限 |
| `gift up` | `--dry-run` | off | 計画表示のみ（操作 RPC は送らない） |
| `gift up` | `--yes` | off | y/N 確認をスキップ |
| `gift reset` | `--max-power N` | 100000 | この power **以下**（ギフト込みの現在値）を drop 対象にする |
| `gift reset` | `--limit N` | 無制限 | drop する体数を N 体に制限（power 降順で先頭 N 体） |
| `gift reset` | `--dry-run` / `--yes` | off | 上と同じ |
| `multi gift-up` / `multi gift-reset` | `--limit` / `--max-power` / `--parallel` / `--dry-run` / `--yes` | | 全アカウント対象。`--yes` なしの実行は exit 2 |

## 出力と終了コード
- `up` の最終行は実測値です: `N hero(es) maxed (MAX 前 -> 後 of 総数), L level(s), C coins spent, balance B, total power A -> B`。消費額と MAX 体数は実行後に `heroGetAll` + `inventoryGet` を 1 回読み直して確定させます（見積で報告しない）。
- `reset` の最終行: `N hero(es) dropped, C coins recovered (balance B), gift on 前 -> 後, total power A -> B`。返却額は drop 応答の `response.consumable.24` の合計です。
- 終了コード: 対象 0 体・コイン不足で打ち切り（実行自体は成功）は **0**。RPC 失敗・状態読み取り失敗・確認で中断は **1**。`--limit < 1` / `--max-power < 0` / multi で `--yes` なしは **2**。

## 制限事項
- **打ち切りはpower降順の先頭で途切れた地点**です。1 体の実コストが見積（65,150 − 消費済み）より安い場合、その実行では打ち切り以降を拾いません。`gift status` を見てから再実行すれば追加で上げられます。
- バッチ内で 1 つでもエラー（ほぼコイン不足）が出たヒーローは**部分成功**として記録し、同じ残高では次の体も失敗するため打ち切ります。部分上昇分も drop で全額回収できます。
- MAX レベル 30 / コスト 65,150 はコード内定数（`GIFT_MAX_LEVEL` / `GIFT_FULL_COST`）です。サーバー側の上限がヒーローレベル等で変わる場合は実測して定数を更新してください。
- ギフトの**表示名・ヒーロー名解決は持っていません**（`heroGetAll` は ID のみ）。出力は `hero 61 power=...` のような ID 表記です。
- `multi gift-up --dry-run` / `multi gift-reset --dry-run` は逐次実行に強制されます（他の dry-run モードと同じ）。
- 消費は大きめです（例: 75 体中 42 体の MAX 化 = 2,736,300 コイン）。先に `--limit 1` で 1 体検証してから流し込むことをおすすめします。

## 実装
- ロジック: `src/python/hw_genie/commands/element_gift.py`（読み取り・計画・送信・表示）
- クライアント: `HWClient.hero_get_all()` / `ApiAction.HERO_GET_ALL`（`core/client.py`）。操作 RPC は `heroTitanGiftLevelUp` / `heroTitanGiftDrop`
- multi: `runner.gift_up_routine` / `gift_reset_routine` / `summarize_gift`（結果は `run_logs` に保存される）
- API 仕様: `docs/api/UNIT_API.md`（`heroGetAll` のギフト項目と `heroTitanGift*`）、生キャプチャは `docs/superpowers/ElementGift.md`

## 完了後の報告
実行体数・消費／返却コイン・実行後の残高・合計 power の前後を報告し、打ち切り（コイン不足）や部分成功があればそのヒーロー ID と理由を明記してください。実在アカウント名は報告に含めず、`Alice` 等のプレースホルダを使ってください。
