---
name: db-inspect
description: HW-Genie のデータベース（Turso クラウド / ローカルレプリカ）のテーブル構造やデータを確認します。機能開発時の状態確認やデバッグに使用します。
---

# DB インスペクション (HW-Genie 版)

## 前提条件

- 本プロジェクトのルートディレクトリにいること
- `.env` に `TURSO_SYNC_URL` / `TURSO_AUTH_TOKEN` が設定されていること（通常は既に設定済み）
- **worktree / 新規クローンで動かす場合**: `.env` は `.gitignore` 済みのため checkout に含まれない。main worktree の `.env` へのシンボリックリンクを貼るか、環境変数として注入する（下記「worktree での準備」）

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

## worktree での準備

`git worktree` の作業ツリーや新規クローンには `.env` が無いため、そのままでは
Turso 接続（sync / リモート確認）ができません。最初に `.env` を用意してください。

```bash
# worktree のルートディレクトリで実行（main worktree の .env を共有・追従する）
ln -s /path/to/main-worktree/.env .env
```

- シンボリックリンク方式は main の `.env` を共有するため、更新に自動で追従します。
  独立した設定にしたい場合は `cp` でコピーしてください（main の更新に追従しない）。
- `.env` は `.gitignore` 済みなので、リンク/コピーしても git の差分やコミットに
  含まれません。不要になったら worktree 側で `unlink .env`（symlink のみ削除）で
  解除できます。**`rm` は使わないこと**: カレントを間違えると main の `.env`
  実体を削除してしまう恐れがあります。`cp` でコピーした場合は、解除前に
  `ls -la .env` で実ファイル（`-` 始まり）かリンク（`l` 始まり）かを必ず確認してください。
- 注意: リンク経由の書き込みは main の `.env` に反映されます（読み取り用途が基本）。
- なお、`_find_pkg_root` は worktree の `.git` ファイル（`gitdir:` ポインタ）にも
  対応しているため、リンクを貼れば DB パス解決は worktree 内で正しく動きます。

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
