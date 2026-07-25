---
name: DB インスペクション
description: HW-Genie のデータベース（Turso クラウド / ローカルレプリカ）のテーブル構造やデータを確認します。機能開発時の状態確認やデバッグに使用します。
---

# DB インスペクション (HW-Genie 版)

## 前提条件

- [turso CLI](https://docs.turso.tech/reference/turso-cli) がインストールされ、`turso auth login` で認証済みであること
- 本プロジェクトのルートディレクトリにいること

## DB 構成

```
Turso Cloud (hw-genie-db)  ←→  ローカルレプリカ (data/hw_genie.db)
    　　　↑ 常に最新                     ↑ uv run hw-genie sync で最新化
```

| テーブル | 用途 |
|----------|------|
| `accounts` | プレイヤーアカウント情報（レベル、ゴールド、エネルギー等） |
| `account_configs` | アカウント別の Key-Value 設定（認証ヘッダー、レイド状態等） |
| `sessions` | 認証セッション（レガシー、現在は未使用） |

## 操作方法

### 1. Turso クラウドを直接確認（常に最新）

```bash
# テーブル一覧
turso db shell hw-genie-db ".tables"

# テーブルスキーマ
turso db shell hw-genie-db ".schema accounts"

# データ確認
turso db shell hw-genie-db "SELECT * FROM accounts;"

# 特定のアカウント設定を確認
turso db shell hw-genie-db "SELECT * FROM account_configs WHERE account_id = 1;"
```

`turso db shell` は Turso クラウドの最新データを直接返すため、**同期不要**です。機能開発時の基本的な確認はこちらを使ってください。

### 2. ローカルレプリカを確認

事前に同期してから `sqlite3` で参照します。

```bash
# 同期
uv run hw-genie sync

# 確認
sqlite3 data/hw_genie.db "SELECT * FROM accounts;"
```

ローカルレプリカは Turso クラウドのコピーです。`uv run hw-genie sync` を実行することで最新状態に追従します。

### 3. 同期のみ実行

```bash
uv run hw-genie sync
```

`TURSO_SYNC_URL` が設定されていない環境では何もせず終了します。

## 機能開発時のワークフロー

新しい機能を開発する際、DB の状態を確認しながら進めると効率的です。

```bash
# 1. 現在の DB 構造を確認
turso db shell hw-genie-db ".schema"

# 2. 特定のテーブルのデータを確認
turso db shell hw-genie-db "SELECT * FROM accounts;"

# 3. ローカルでの動作確認後、明示的に同期
uv run hw-genie sync

# 4. ローカルレプリカの状態を確認
sqlite3 data/hw_genie.db "SELECT * FROM account_configs;"
```
