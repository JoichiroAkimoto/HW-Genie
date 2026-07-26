---
name: DB インスペクション
description: HW-Genie のデータベース（Turso クラウド / ローカルレプリカ）のテーブル構造やデータを確認します。機能開発時の状態確認やデバッグに使用します。
---

# DB インスペクション (HW-Genie 版)

## 前提条件

- 本プロジェクトのルートディレクトリにいること
- `.env` に `TURSO_SYNC_URL` / `TURSO_AUTH_TOKEN` が設定されていること（通常は既に設定済み）

## DB 構成

```
Turso Cloud (hw-genie-db)  ←→  ローカルレプリカ (data/hw_genie.db)
    　　　| 常に最新                     ↑ uv run hw-genie sync で最新化
    　　　| Turso アカウント所有者のみ
    　　　└ turso db shell で直接アクセス可能
```

| テーブル | 用途 |
|----------|------|
| `accounts` | プレイヤーアカウント情報（レベル、ゴールド、エネルギー等） |
| `account_configs` | アカウント別の Key-Value 設定（認証ヘッダー、レイド状態等） |

## 操作方法

### 1. ローカルレプリカの確認（推奨・全員可能）

`.env` の `TURSO_SYNC_URL` / `TURSO_AUTH_TOKEN` があれば、Turso CLI のセットアップなしで DB を確認できます。

```bash
# 同期してから
uv run hw-genie sync

# sqlite3 で参照
sqlite3 data/hw_genie.db "SELECT * FROM accounts;"
sqlite3 data/hw_genie.db ".tables"
sqlite3 data/hw_genie.db ".schema accounts"
```

### 2. Turso クラウドを直接確認（アカウント所有者のみ）

Turso の無料プランでは、DB 作成者のアカウントでログインした場合のみアクセスできます。

```bash
# turso CLI が必要
turso db shell hw-genie-db ".schema"
turso db shell hw-genie-db "SELECT * FROM accounts;"
turso db shell hw-genie-db "SELECT * FROM account_configs WHERE account_id = 1;"
```

### 3. 同期のみ実行

```bash
uv run hw-genie sync
```

`TURSO_SYNC_URL` が設定されていない環境では何もせず終了します。

## AI エージェント向け

機能開発時の DB 確認は以下の手順で行ってください。

```bash
# 1. 最新の状態に同期
uv run hw-genie sync

# 2. スキーマ確認
sqlite3 data/hw_genie.db ".schema"

# 3. データ確認
sqlite3 data/hw_genie.db "SELECT * FROM accounts;"
sqlite3 data/hw_genie.db "SELECT * FROM account_configs;"
```
