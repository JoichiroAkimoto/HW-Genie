---
name: arena
description: HW-Genie を使用して Arena の状態・ランキングを確認し、チームプリセットを管理して、自分より上位の指定プレイヤーを攻撃します。
---
# Arena (HW-Genie 版)
## 概要
Arena の順位・攻撃回数・保存チームを確認し、名前付きチームプリセットを作成して、**自分より上位** のプレイヤーを `--rank` または `--user-id` で指定して攻撃します。

- **対象は上位のみ**: 同順位・下位・順位不明のプレイヤーは攻撃しません（安全側で拒否）。
- **攻撃可能性の条件（通常攻撃）**: 攻撃には「最新の `arenaFindEnemies` 候補にいること」と「`arenaCheckTargetRange` が true」の **両方** が必要です。範囲チェックが true でも候補外なら `arenaAttack` は `NotAvailable` になります（実機確認済み）。
- **範囲チェックのウィンドウ**: 順位/リーグ依存です。トップ帯（22位/34位）では ±21 でしたが、**Grand Arena 51位では5〜50位まで true**（46位上）でした。±21 を固定ルールにせず、常に `arenaCheckTargetRange` の実レスポンスに従います。
- **候補の範囲**: トップ帯（Arena 16〜19位）では1〜13位上が中心でしたが、**Grand Arena 51位では18〜24位（27〜33位上）** が候補に現れました（読み取り確認）。CLI は「-13位まで」のような固定制限を設けず、候補への登場と範囲チェックで判定します。候補は呼び出しごとに変わるため、`--candidate-retries N`（デフォルト10）で `arenaFindEnemies` を追加リクエストして対象の登場を待てます（`0` で初回のみ）。
- **ランキング**: `topGet` はトップ50のみ（ページングなし）。`--user-id` は対象が `topGet` または現在の候補に存在し、順位を解決できる場合のみ使用できます（順位不明は拒否。候補解決にも `--candidate-retries` が効きます）。
- **チーム**: `--heroes`（ちょうど5体・重複なし・ID < 6000）/ `--pet`（>= 6000）/ `--banner` / `--favor HERO=PATRON`（繰り返し可）。未指定フィールドは **フィールド単位で** プリセット → ゲーム保存チームの順に補完されます（例: プリセットが heroes だけでも保存チームの pet/banner を引き継ぎます）。`--banner 0` は「バナーなし」として扱い、`banners` を送信しません。
- **favor**: 明示 `--favor` が最優先。なければプリセット → 同モードの保存 favor（`teamGetFavor.arena`）から選択ヒーローに一致する分だけを使い、欠落ヒーローは警告されます。他モードの favor は流用しません。favor が空の場合は payload から省略されます（部分マップは可）。
- **`--dry-run`**: 読み取り API・`arenaFindEnemies`（必要ならリトライ）・`arenaCheckTargetRange` を実行し、`arenaAttack` は **絶対に送りません**。候補外なら実攻撃でも失敗するため、dry-run の時点で exit 1 になります。
- **実攻撃がデフォルト**: `--dry-run` を付けない場合は攻撃します（`--yes` はありません）。`arenaAttack` は **1回だけ** 送信し、自動リトライしません。タイムアウト等で結果不明の場合は exit 1 で「ゲーム側で確認してから再実行」を促します（攻撃回数を二重消費しないため）。
- **`arena plan`（読み取り専用）**: 取り込み済みの戦闘コーパス（`hw-genie battle import`）から、対象の現在の防衛編成に対する攻撃編成の勝率を返します。`arenaAttack` は送りません。同一防衛者（`defender_player_id`）の履歴を優先し、サンプル不足なら同一編成の他防衛者・他アカウントの履歴にフォールバックします。順位付けは Wilson 下限で行い、`--min-samples` / `--min-win-rate` を満たす候補だけ `REC` になります。

## ワークフロー
> **Tip**: 複数アカウントでは `uv run hw-genie auth --list` で正しいアカウント名を確認してください。

1. 状態確認（読み取りのみ）:
   ```bash
   uv run hw-genie arena status -a Alice
   ```
2. ランキング確認:
   ```bash
   uv run hw-genie arena top -a Alice --limit 20
   uv run hw-genie arena top -a Alice --raw
   ```
3. チームプリセット作成:
   ```bash
   # 現在のゲーム保存チームを取り込む
   uv run hw-genie arena team set atk1 -a Alice --from-saved

   # 直接指定（favor は必要な分だけ）
   uv run hw-genie arena team set atk1 -a Alice \
     --heroes 29 13 73 71 35 --pet 6008 --banner 2 \
     --favor 13=6003 --favor 29=6011

   uv run hw-genie arena team list -a Alice
   uv run hw-genie arena team remove atk1 -a Alice
   ```
4. 攻撃前の予行確認（攻撃しません。候補外ならこの時点で失敗します）:
   ```bash
   uv run hw-genie arena attack -a Alice --rank 10 --team atk1 --dry-run
   uv run hw-genie arena attack -a Alice --user-id 11111111 --dry-run

   # 候補の更新回数を増やす（対象が候補に現れにくい場合）
   uv run hw-genie arena attack -a Alice --rank 10 --team atk1 --dry-run --candidate-retries 20
   ```
5. 実攻撃（攻撃回数を消費します）:
   ```bash
   uv run hw-genie arena attack -a Alice --rank 10 --team atk1
   uv run hw-genie arena attack -a Alice --rank 10 \
     --heroes 29 13 73 71 35 --pet 6008 --banner 2 --favor 13=6003
   ```
6. 手動検証（実験用。スナップショットを保持したまま順位変動を確認）:
   ```bash
   # 候補を1回取得して account_configs.arena_snapshot に保存
   uv run hw-genie arena find-enemies -a Alice

   # 保存した候補と現在の自分/相手の順位・CheckTargetRange を表示
   # （arenaFindEnemies は呼ばない）
   uv run hw-genie arena snapshot -a Alice

   # スナップショットの相手を、候補を更新せずに攻撃
   uv run hw-genie arena attack -a Alice --from-snapshot --rank 10 --team atk1 --dry-run
   uv run hw-genie arena attack -a Alice --from-snapshot --rank 10 --team atk1
   ```

## 事前計算（読み取り）とメンテナンス（書き込み）

> 注意: `battle simulate` は sim/comparison 行を書き込み、`battle prune` は古い行を削除します。まず `--dry-run` で確認してください。

過去の戦闘ログから「この防衛編成にはこの攻撃編成が勝っている」を引けます。`arenaAttack` は送りません。

```bash
# 1) 戦闘リプレイを取り込む（重複は自動スキップ）
uv run hw-genie battle import -a Alice --type arena --limit 50

# 1b) HWA の *.json.gz スナップショットから一括取り込み（既定は直近90日）
uv run hw-genie battle import-hwa -a Alice \
  --source /path/to/hwa-db-2026-10-30.json.gz --types arena,grand --days 90 --no-sync

# 1c) 5分ごとに新規リプレイを自動取り込み（Ctrl+C で終了）
uv run hw-genie battle watch -a Alice --interval 300

# 2) 対象の現在の防衛編成に対する攻撃編成ランキング
uv run hw-genie arena plan -a Alice --rank 10 --min-samples 3 --min-win-rate 1.0

# 3) REC が出たら、表示されたコマンドで攻撃（まず --dry-run）
uv run hw-genie arena attack -a Alice --user-id 11111111 \
  --heroes 1 2 3 4 5 --pet 6001 --banner 2 --dry-run

# 統計的勝率（ToE は既知の getter。Arena は getter 特定後に --config-getter を指定）
uv run hw-genie battle simulate -a Alice --type titan_arena --limit 5 --seeds 10

# 90日より古いデータを削除（まず dry-run）
uv run hw-genie battle prune --days 90 --dry-run
```

- 同一防衛者（`defender_player_id`）の履歴を優先し、サンプル不足なら同一編成の他防衛者・他アカウントの履歴にフォールバックします。
- 順位付けは Wilson 下限です（1勝0敗の「100%」が3勝0敗より上に来ないようにするため）。
- ゲートは `--min-samples`（既定3）と `--min-win-rate`（既定1.0）。満たさない候補は `REC` になりません。
- `--game-version` で特定のサーバーバージョンの戦闘だけに絞り込めます（既定は全バージョン）。
- 防衛編成は `topGet` を優先し、見つからなければ `arenaFindEnemies`（候補）にフォールバックして取得します（`--json` を汚さないよう候補更新の進捗表示は抑制）。どちらにも編成が無い場合のみエラーになります。
- コーパスが空の場合は `battle import` を先に実行してください。

## 注意
- **まず `--dry-run`**: ターゲット解決・候補確認・範囲チェック・チーム内容を確認してから実攻撃してください。
- **上位のみ**: `--rank` / `--user-id` が自分以上、または順位を解決できない場合は exit 1 で攻撃しません。
- **候補必須（通常攻撃）**: `--rank` / `--user-id` とも、対象が `topGet`（トップ50）または `arenaFindEnemies` の候補に存在する必要があります。実攻撃は最新の候補にいる相手しか受理されません。`--candidate-retries` を増やしても見つからない場合は exit 1 で攻撃しません。
- **13位・±21という固定上限はありません**: トップ帯では候補は1〜13位上が中心ですが、GA 51位では27〜33位上も候補になりました。CLI は候補と `arenaCheckTargetRange` の実レスポンスだけで判定します。
- **`--from-snapshot` は実験用**: `arenaFindEnemies` を呼ばず、保存済み候補に `arenaAttack` を送ります。`--rank N` / `--user-id ID` は**保存済みスナップショットの順位・候補**に対して解決されるため、現在の `arena top` と一致するとは限りません（攻撃前に `arena snapshot` で確認してください）。上位のみは常に現在の `topGet` で再チェックし、対象が現在のトップ50にいない場合は順位を検証できないため攻撃しません。`arenaCheckTargetRange` が false の場合は既定で停止します。検証で必要な時だけ `--force` を付けてください（`--from-snapshot` との併用時のみ有効）。`--candidate-retries` は使いません。
- **スナップショットの保存先**: アカウントごとに `account_configs` の `config_key="arena_snapshot"`。`arena snapshot` は `topGet`（トップ50）で現在順位を表示するだけで、候補リストは更新しません。`>13` 列は診断用（固定ルールではない）。
- **攻撃回数**: `NotEnough` / `Refillable` は攻撃せず警告して exit 0 です（`Refillable 5` はクールダウン、`Refillable 6` は回数切れとして扱い、クールダウン時は残り時間も表示）。`arena status` で残回数とクールダウンを確認できます。
- **キャンペーン用途ではありません**: 自動周回・自動ターゲット選択・`multi arena` は未対応です。1回ずつ明示的に指定してください。
- API 仕様の詳細は `docs/api/ARENA_API.md` を参照してください。
