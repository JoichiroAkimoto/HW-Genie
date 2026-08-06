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
   - 10007（Soul Atrium 召喚）は `enabled: false` のためデフォルトでは実行されません。
   - アカウント固有の操作引数は `account_configs` の `quest_defaults` で上書き可能です。

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