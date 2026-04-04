---
name: Hero Wars 認証・ユーザー情報取得
description: HW-Genie を使用してセッション情報を管理し、ユーザー情報を取得します。ブラウザからコピーした curl コマンドを使用して認証情報を更新できます。
---
# 認証・ユーザー情報取得 (HW-Genie 版)
## ワークフロー
1. ブラウザのネットワークタブから `api/` へのリクエストを `Copy as cURL` でコピーします。
2. 実行コマンド（コピーした curl コマンドを引数に渡します）:
   ```bash
   .venv/bin/hw-genie auth --curl 'PASTE_CURL_COMMAND_HERE'
   ```

## 便利なオプション
- `--info`, `-i`: 現在のセッション情報を再取得して最新の状態に更新します。
- `--account`, `-a`: アカウントに別名を付けて保存します（例: `--account sub1`）。
- `--update`, `-u`: JSON 形式のヘッダーを直接渡して更新します。
