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
- 30 calls を 1 リクエストに並べる送信形式（`ident`: `body`, `body_1`, …）はゲームクライアントと同じ形で受理されます。

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
   uv run hw-genie gift reset -a Alice --max-power 43342 --limit 1 --yes   # 閾値以内の対象から power 最高の 1 体を降ろす
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
| `multi gift-up` / `multi gift-reset` | `--limit` / `--parallel` / `--dry-run` / `--yes` | | 全アカウント対象。`--yes` なしの実行は exit 2 |
| `multi gift-reset` | `--max-power` | 100000 | `gift-reset` のみ対応。`multi gift-up` に指定すると worker 起動前に exit 2（`gift-up` に power 閾値はない） |

## 出力と終了コード
- `up` の最終行は実測値です: `N hero(es) maxed (MAX 前 -> 後 of 総数), L level(s), C coins spent, balance B[, total power A -> B][, F failed]`。最後の `[, F failed]` は RPC 失敗体が 1 体以上あるときだけ付くサフィックスです（0 体のときは出ない）。消費額と MAX 体数は実行後に `heroGetAll` + `inventoryGet` を 1 回読み直して確定させます（見積では報告しません。唯一の例外が次項）。
- **最終再読自体が失敗したときだけ見積にフォールバックします**。認証切れ（`HWAuthError`）に限らず、API 形変更などの `GiftReadError` でも同じ経路です。warning ログを出したうえで、`up` の消費コインは**「完全に MAX 化できた体の計画額の合計」だけ**（部分適用分は入りません。つまり**実際にはそれ以上消費されており**、表示される残高も「実行前残高 − その合計」という見積もりです）。`MAX 後` は `?`、`total power` の前後は省略。`reset` は残高＝「実行前残高 + drop 応答から読めた返却額」、`gift on 後` は `?`（返却額自体は drop 応答の実測値なので不変）。書き込みは成功しているため、このフォールバックで終了コードは変わりません（実測が要るときは `gift status` を読み直してください）。
- `reset` の最終行: `N hero(es) dropped, C coins recovered (balance B), gift on 前 -> 後[, total power A -> B][, F failed]`。`up` と同じく `[, F failed]` は drop 失敗体があるときだけ付きます。返却額は drop 応答の `response.consumable.24` の合計です。
- 終了コード:
  - **0**: 計画時点で対象 0 体のとき。`up` は「全員 MAX 済み」または「実行前残高で次の 1 体を MAX にできるコインが無い」（`blocked` として表示し実行 nothing）。`reset` は drop 対象 0 体。どちらも**失敗ではありません**。
  - **1**: 実行中のエラー。**`gift up` の 3 分類**: コイン枯れを示すエラー（`NotEnough`）は「同じ残高では次の体も買えない」として**打ち切り**。継続するのは**既知のヒーロー個別業務エラー**（`core.client.HERO_SPECIFIC_ERROR_NAMES`。実在を確認できている名前は `NotAvailable` のみ）のときだけで、その体を失敗と記録して次の体へ進みます（どちらも失敗計上。単体も `multi` のそのアカウントも失敗扱い）。**未知のエラー名・エラー名が読めない形・transport 失敗（`network_or_parse_error` / `ResponseStatus.UNEXPECTED`）は「その体だけ」でなく全アカウント共通の事故なので打ち切る**（資源を消費する書込を積み続けず、読み取り側が同じ状況で止まるのと揃える）。**`gift reset`** は drop が資源を消費しないため、`NotEnough` を含む非認証の失敗はその体を失敗計上して次へ**継続**し、最後に失敗が残れば exit 1（認証切れだけは即中断）。他に RPC 失敗・状態読み取り失敗・y/N 確認での中断（`skipped by user`）も 1。
  - **2**: `--limit < 1` / `--max-power < 0` / `multi gift-up` への `--max-power` 指定（`gift-reset` 専用フラグのため） / `multi gift-up`・`multi gift-reset` を書き込みモードで `--yes` なしに実行（いずれも `run_all_accounts` を呼ぶ前に停止）。
  - 「コイン不足」は**計画時点で出て止まれば 0、実行中に RPC が `NotEnough` を返して止まれば 1**に分かれる点に注意してください（計画は読み取った実残高で立てるため、実行中のコイン不足は手動操作や他タスクが挟まった場合に限られます）。
- 実行の途中で認証切れ（HWAuthError）が出ても、そこまでの部分結果で最終レポートを出してからエラー終了します（`multi` ではそのアカウントだけが失敗し、他アカウントは続行します）。**読み取り batch 内の per-call `auth` / `InvalidSession` も `HWAuthError` に合流する**ので、`multi gift-up` / `gift status` でも単体の認証切れと同じ「Session expired … curl / auth-server で再同期」案内が出て、run_logs も認証事故として記録されます。
- 状態読み取りが厳しいことを忘れないでください: `heroGetAll` / `inventoryGet` の `response` が辞書として読めない形、または `inventoryGet` の `response.consumable` が辞書でない形（欠損 / list / 文字列）は「残高 0」として扱わず `GiftReadError` で失敗（exit 1 / multi ではそのアカウントの失敗）になります。0 円のコイン不足（終了コード 0）として握りつぶしません。個数が無いだけの `{"consumable": {}}` は通常の 0 として正しく扱います（形変更と区別できないため）。

## 制限事項
- **`gift up` の打ち切りは power 降順の先頭で途切れた地点**です。1 体の実コストが見積（65,150 − 消費済み）より安い場合、その実行では打ち切り以降を拾いません。`gift status` を見てから再実行すれば追加で上げられます。
- **`gift up`** でバッチ内にエラーが出たヒーローは**部分成功**として記録します。エラーがコイン枯れ（`NotEnough`）なら同じ残高では次の体も失敗するため**打ち切り**、既知のヒーロー個別業務エラー（`NotAvailable`）なら**その体だけ失敗にして次の体へ継続**、それ以外（未知の名前 / 読めない形 / transport）は**打ち切り**です。部分上昇分も drop で全額回収できます。**`gift reset` は drop が資源を消費しない**ため、`NotEnough` を含む非認証の失敗でもその体を失敗計上して次の体へ継続し（認証切れだけ即中断）、実行後に失敗が残れば exit 1 です。
- **継続した体はコストを消費していないことがあり、打ち切り地点より後の体は試行されません**（計画が「載った体すべてが cost を消費する」前提で境界を決めるため、`blocked` は計画ベースの数字のまま = 実際には買えたのに買わなかったように見える場合があります）。非コイン系で継続して打ち切ったあとは、`gift status` で残りを確認してから再実行してください。
- **`power` が読めないヒーローは reset 対象にしません**（欠損・非数値を 0 に丸めて「閾値以下」に化けると、イベント当日に主力のギフトが落ちるため）。`Infinity` / `NaN`（`requests` の JSON デコードが通る形）も「読めない」扱い。gift が载っていて power が読めない体が 1 体でもあれば `gift reset` 実行時に警告 1 行（`N gifted hero(es) skipped: power unreadable (61, 62). …`）を出します。`up` 側も同じ方針で、読めない未 MAX 体は計画の末尾に回るだけなので警告 1 行（`N unmaxed hero(es) planned last: power unreadable (61, 62).`）を出します。`gift status` は両方の警告行を出します。
- MAX レベル 30 / コスト 65,150 はコード内定数（`GIFT_MAX_LEVEL` / `GIFT_FULL_COST`）です。サーバー側の上限がヒーローレベル等で変わる場合は実測して定数を更新してください。
- ギフトの**表示名・ヒーロー名解決は持っていません**（`heroGetAll` は ID のみ）。出力は `hero 61 power=...` のような ID 表記です。
- `multi gift-up --dry-run` / `multi gift-reset --dry-run` は逐次実行に強制されます（他の dry-run モードと同じ）。アカウント別サマリ表は `Account | ✅ Done | 💰 Coins | 🪙 Left | ⏭️ Blocked | ❌ Failed | ℹ️ Note` で、dry-run では**計画体数（Done 列）と計画コイン（Coins 列）**が載ります（`multi consumable --dry-run` と同じ慣習）。`Failed` 列は失敗体の**計数専用**で、理由文字列は `Note` 列に出します（`summarize_seers_game` の Note 列と同じ列分離。直近の失敗理由、無ければ `-`）。
- 消費は大きめです（例: 75 体中 42 体の MAX 化 = 2,736,300 コイン）。先に `--limit 1` で 1 体検証してから流し込むことをおすすめします。

## 実装
- ロジック: `src/python/hw_genie/commands/element_gift.py`（読み取り・計画・送信・表示）
- 状態読み取り: `ApiAction.HERO_GET_ALL` / `ApiAction.INVENTORY_GET` を `fetch_state` の
  1 リクエスト batch payload で直接読む（単一コール用の `HWClient.hero_get_all()` は
  `HWClient.call` の `detail` の形が変わるため持たない）。操作 RPC は
  `heroTitanGiftLevelUp` / `heroTitanGiftDrop`
- multi: `runner.gift_up_routine` / `gift_reset_routine` / `summarize_gift`（結果は `run_logs` に保存される。
  サマリ表の行は `results` の key がアカウントで、`GiftRunResult` 自体はアカウント名を持たない）
- エラー名の分類（認証／資源枯渇／ヒーロー個別）は `core/client.py` の共有定数だけが正:
  `AUTH_ERROR_NAMES`（`HWClient.call` の自動変換と同一集合）、`RESOURCE_EXHAUSTED_ERROR_NAMES`
  （コイン枯れ＝`NotEnough`。`quests` のフォールバック判定も同じ定数を参照する）、`HERO_SPECIFIC_ERROR_NAMES`
  （継続してよいヒーロー個別名。docs/api か既存コードで実在が確認できた名前だけ）。
  ギフト側で独自集合を持たず、`element_gift` → `quests` の import 依存も持たない。
- API 仕様: `docs/api/UNIT_API.md`（`heroGetAll` のギフト項目と `heroTitanGift*`）。実機の生キャプチャ
  `docs/superpowers/ElementGift.md` は `.gitignore` 済みで追跡外（PR / fresh clone には含まれません）。
  仕様の正は `docs/api/UNIT_API.md` です。

## 完了後の報告
実行体数・消費／返却コイン・実行後の残高・合計 power の前後を報告し、打ち切り（コイン不足）や部分成功があればそのヒーロー ID と理由を明記してください。実在アカウント名は報告に含めず、`Alice` 等のプレースホルダを使ってください。
