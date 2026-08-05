---
name: quest-status
description: HW-Genie を使用して未完了のデイリー等クエストの状態を取得・表示します。questGetAll の取得のみ（questFarm による自動完了は未実装）。
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

## 表示内容
- カテゴリ別（Daily / Weekly / Guild / Main / Event / Battle Pass / One-time / Unclassified）に、クエスト ID・名前・進捗（progress/target）・報酬・createTime を表示。
- アイコン: `✅` 完了（state=2）、`⏳` 進行中（progress>0）、`⬜` 未着手。
- デイリー（100xx）の名前は `QUEST_MASTER` テーブルで解決（未確定の ID は「(未命名)」）。

## 制限事項
- これは **読み取りのみ** のコマンドです。`questFarm` によるクエスト報酬の自動確定・未完了クエストの自動完了は未実装です。

## 完了後の報告
表示されたクエスト一覧から、特に**未完了のデイリークエスト**（100xx）を中心に、残りのタスクと進捗をユーザーに報告してください。