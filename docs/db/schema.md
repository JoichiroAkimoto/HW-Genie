# Database Schema

HW-Genie のデータベース構成と各テーブルの説明です。

## 概要

| 項目 | 値 |
|------|-----|
| DB エンジン | SQLite (Turso Embedded Replica 同期対応) |
| ファイル | `data/hw_genie.db` |
| クラウド | Turso `hw-genie-db` |
| ORM | SQLAlchemy 2.x |

## テーブル定義

### `accounts`

プレイヤーアカウントの基本情報を保持します。

| カラム | 型 | 制約 | 説明 |
|--------|----|------|------|
| `id` | INTEGER | PK, AUTOINCREMENT | 内部ID |
| `player_id` | VARCHAR | UNIQUE, NOT NULL | プレイヤーID（API由来） |
| `alias` | VARCHAR | | アカウント別名（CLIの `--account` で使用） |
| `player_name` | VARCHAR | | プレイヤー名 |
| `level` | INTEGER | | プレイヤーレベル |
| `gold` | INTEGER | | Gold |
| `gems` | INTEGER | | Gems |
| `energy` | INTEGER | | スタミナ |
| `arena_rank` | INTEGER | | アリーナ順位 |
| `grand_rank` | INTEGER | | グランドアリーナ順位 |
| `last_mission_id` | INTEGER | | 最後にレイドしたミッションID |
| `memo` | VARCHAR | | 任意メモ（`auth --memo` で設定） |
| `last_updated` | DATETIME | DEFAULT CURRENT_TIMESTAMP | 最終更新日時 |

---

### `account_configs`

各アカウントの Key-Value 設定ストア。

| カラム | 型 | 制約 | 説明 |
|--------|----|------|------|
| `id` | INTEGER | PK, AUTOINCREMENT | 内部ID |
| `account_id` | INTEGER | FK -> accounts.id, NOT NULL | アカウント参照 |
| `config_key` | VARCHAR | NOT NULL | Key（下記参照） |
| `config_value` | JSON | | Value（JSON形式） |

Unique constraint: `(account_id, config_key)`

#### Known `config_key` values

| config_key | config_value の型 | 説明 |
|------------|-------------------|------|
| `"headers"` | `dict[str, str]` | 認証ヘッダー（x-auth-*） |
| `"status"` | `str` | `"success"` / `"error"` |
| `"last_updated"` | `str` | ISO-8601 タイムスタンプ |
| `"player_{key}"` | 任意 | Player のその他情報（Account テーブルのカラム以外のもの） |

## 型定義

コード上では以下の TypedDict でデータ構造を型付けしています（`repository.py`）。

```python
class HeadersConfig(TypedDict, total=False):
    x_auth_session_id: str
    x_auth_token: str
    x_auth_user_id: str
    x_request_id: str


class PlayerInfo(TypedDict, total=False):
    id: str
    name: str
    level: int
    gold: int
    gems: int
    energy: int
    arena_rank: int
    grand_rank: int


class AccountData(TypedDict, total=False):
    headers: HeadersConfig
    player: PlayerInfo
    status: str
    last_updated: str
    last_item_raid_mission_id: int
    memo: str
```

## データフロー

### Read

```
SessionManager.load(alias)
  → SessionRepository.get_data(alias)
    → Account (alias で検索)
    → AccountConfig (account_id で一括取得)
    → 各 config_key を data dict にマージ
    → Account カラムを player_info にオーバーレイ
    → AccountData を返す
```

### Write

```
SessionManager.save(alias, data)
  → SessionRepository.update_config(alias, data)
    → Account: 存在確認 / 作成 → カラム更新
    → AccountConfig: 各 key を upsert
    → commit (write_lock で排他制御)
```
