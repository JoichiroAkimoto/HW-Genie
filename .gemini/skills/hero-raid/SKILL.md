---
name: ヒーローレイド
description: HW-Genie を使用して指定されたミッションのヒーローレイドを実行します。
---
# ヒーローレイド Skill (HW-Genie 版)
## ワークフロー
1. ユーザーからレイド対象のミッション ID（例：195, 203 など）を指定された場合に起動します。
2. 実行コマンド:
   - 通常実行（保存済みのセッションを使用）:
     ```bash
     .venv/bin/hw-genie raid hero 195 203 --times 3
     ```
   - 認証情報を同時に更新する場合（ブラウザからコピーした curl を使用）:
     ```bash
     .venv/bin/hw-genie raid hero 195 203 --curl 'PASTE_CURL_COMMAND_HERE' --times 3
     ```

## オプション
- `--times`, `-t`: 各ミッションを何回実行するか（デフォルト 3 回）。
- `--curl`, `-c`: 認証情報を抽出してセッションを最新の状態に更新します。
- `--account`, `-a`: アカウント別名を指定して実行します。
