---
name: quest-status
description: HW-Genie を使用して未完了のデイリー等クエストの状態を取得・表示します。--execute で QUEST_OPERATIONS 登録済みのデイリーを自動完了（操作実行＋questFarm 報酬受領）することもできます。
---
# クエスト状態 Skill (HW-Genie 版)

## ワークフロー
> **Tip**: 複数のアカウントを使用している場合は、事前に `uv run hw-genie auth --list` で正しいアカウント別名を確認することを推奨します。

1. **トリガー**: ユーザーが「今日のデイリーを確認」「未クリアのクエストを見せて」等と指示した場合に起動します。
2. **実行**:
   - デフォルト（未完了のみ表示）で、対象アカウントを指定して実行します。
   ```bash
   uv run hw-genie quests -a <ACCOUNT>
   ```
   - 完了済みも含めて確認したい場合:
   ```bash
   uv run hw-genie quests -a <ACCOUNT> --show-all
   ```
   - カテゴリで絞り込みたい場合:
   ```bash
   uv run hw-genie quests -a <ACCOUNT> --category daily
   uv run hw-genie quests -a <ACCOUNT> --category weekly
   uv run hw-genie quests -a <ACCOUNT> --category guild
   uv run hw-genie quests -a <ACCOUNT> --category main
   uv run hw-genie quests -a <ACCOUNT> --category event
   uv run hw-genie quests -a <ACCOUNT> --category battlepass
   uv run hw-genie quests -a <ACCOUNT> --category one_time
   ```
   - 生 JSON を確認したい場合:
   ```bash
   uv run hw-genie quests -a <ACCOUNT> --raw
   ```
   - **破壊的操作**（デイリークエスト自動完了）: `--execute` で QUEST_OPERATIONS に登録済みの未完了デイリー（10007/10024/10028/10030/10023）を、操作 API（強化・召喚・購入）→ questFarm で自動クリアします。各ステップ実行前に y/n 確認が入るため、自動実行は追加で `--yes` を指定します（例: `uv run hw-genie quests -a <ACCOUNT> --execute --yes`）。
   ```bash
   # 実行プランだけ確認（何も実行しない）
   uv run hw-genie quests -a <ACCOUNT> --dry-run
   # 確認プロンプト付きで実行
   uv run hw-genie quests -a <ACCOUNT> --execute
   # 確認なしで自動実行（要 --execute）
   uv run hw-genie quests -a <ACCOUNT> --execute --yes
   ```
   - **実行可否はアカウントごとに** `account_configs` の `quest_defaults` で制御します。初期状態は全クエスト無効（`enabled: false`）で、`--init-defaults`（または初回 `--execute`）で未初期化アカウントにのみ自動投入されます。**投入時に各操作ステップのデフォルト引数（heroId/titanId 等の共有値）と `note`（操作 RPC 名の連結メモ、例: `"shopBuy → titanArtifactLevelUp"`）も一緒に補完**され、コード側レシピの変更の影響を受けないアカウント設定として固定されます（`note` は DB JSON の可読性用で実行・引数上書きには影響しません）。有効化・引数上書きは **対話的ウィザード `--edit-defaults`**（推奨）か `--set-default` で行います:
   ```bash
   # 初期設定を投入（enabled:false ＋ デフォルト引数・note を追加。既存設定は保持）
   uv run hw-genie quests -a <ACCOUNT> --init-defaults

   # 対話的ウィザード（TTY では rich による全画面リフレッシュ表示）
   uv run hw-genie quests -a <ACCOUNT> --edit-defaults
   #  例: ⚙️  quest_defaults for Joe
   #      ┌─────────────┬───────────────┬───────────────────────┬─────────┐
   #      │ # │ ID   │ Quest                    │ Operations (note) │ Status  │
   #      ├─────────────┼───────────────┼───────────────────────┼─────────┤
   #      │ 1 │ 10007 │ Perform 1 summon in...  │ gacha_open         │ ⏸️ disabled │
   #      │ 3 │ 10024 │ Level up any Hero's...  │ heroArtifactLevelUp│ ⏸️ disabled │ ← 番号選択
   #      │ 4 │ 10028 │ Level up any Titan...   │ shopBuy → titan... │ ⏸️ disabled │
   #      └─────────────┴───────────────┴───────────────────────┴─────────┘
   #        Configure 10024 …:
   #          1. enabled (current: false)   ← 番号選択
   #          2. heroId (current: 61)
   #        （enabled は 1. true / 2. false の番号選択、他キーは値入力）
   #  q: 終了、b: クエスト一覧に戻る。選択のたびに画面が更新され必要な情報だけが残る。
   #  ※ note は一覧に参照表示されるのみで編集対象外。非TTY ではスクロール表示。

   # ID 指定で直接設定（スクリプト向け）
   uv run hw-genie quests -a <ACCOUNT> --set-default 10024 enabled true
   uv run hw-genie quests -a <ACCOUNT> --set-default 10028 titanId 4022
   ```
   - 10007（Soul Atrium 召喚）は消費が大きいため `QUEST_OPERATIONS` 自体が `enabled: false` で、`quest_defaults` でも有効化しない限り実行されません。
   - アカウント固有の操作引数は `account_configs` の `quest_defaults` で上書き可能です。上書きキーはステップの args に**既に存在するキー**にのみ適用され（誤ったキーは警告）、マルチステップ（10028 等）では該当する全ステップに適用されます。
   - 10028（Titan Artifact）の `shopBuy` は実行時に **shopGetAll の実在庫から reward/cost を動的解決**します（取得は実行単位で 1 回にキャッシュされ複数クエスト間で共有）。在庫取得失敗（認証以外）時のみコード既定値へフォールバックし、**指定 shop / slot が在庫に無い、または指定 slot が購入済み（bought）の場合は実行せず失敗報告**します（固定 reward での NotAvailable 送信を防止）。

## 表示内容
- カテゴリ別（Daily / Weekly / Guild / Main / Event / Battle Pass / One-time / Unclassified）に、クエスト ID・名前・進捗（progress/target）・報酬・createTime を表示。
- アイコン: `✅` 完了（state=2）、`⏳` 進行中（progress>0）、`⬜` 未着手。
- デイリー（100xx）の名前は `QUEST_MASTER` テーブルで解決（未確定の ID は「(未命名)」）。

## 制限事項
- `quests`（デフォルト表示）は読み取りのみですが、`--execute` は実際にアイテム・リソースを消費する**破壊的操作**です。実行前に `--dry-run` でプランを確認し、取り消せない消費（フラグメント購入・強化等）を行なうことをユーザーに伝えて同意を得てください。

## GUI アイコンの凡例
- 一般表示: `🎁` 報酬受取可能（state=2）、`⏳` 進行中（progress>0）、`⬜` 未着手。※表示上のマークであり、SKILL ヘッダーの ✅/⏳/⬜ 表記と混同しないこと。

## 完了後の報告
表示されたクエスト一覧から、特に**未完了のデイリークエスト**（100xx）を中心に、残りのタスクと進捗をユーザーに報告してください。