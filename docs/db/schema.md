---
layout: page
title: Database Schema
description: HW-Genie のデータベース構成と各テーブルの説明
---

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
| `"arena_team_presets"` | `dict[str, dict[str, dict]]` | Arena チームプリセット。`{"arena": {"atk1": {"heroes": [...], "pet": 6008, "favor": {"13": 6003}, "banners": [2]}}}` の形（`pet` / `banners` は任意、Grand Arena 用の `grand` キーは将来拡張） |
| `"arena_snapshot"` | `dict[str, dict]` | 手動検証用の直近 `arenaFindEnemies` スナップショット。`{"arena": {"saved_at": "2026-10-29T16:10:15+09:00", "own_place": 16, "candidates": [{"user_id": 111, "place": 4, "name": "Bob", "power": 5000}]}}` |
| `"player_{key}"` | 任意 | **レガシー**: 旧バージョンの保存ロジックが書き込んだ Player 情報（新規書き込みは行われず、`SessionManager.load` の読み取り互換のためにのみ使用） |

---

### `run_logs`

`multi` 実行（hwda / hwsa / CLI 直実行 / Docker）1 回ごとの結果サマリーと出力全文。

| カラム | 型 | 制約 | 説明 |
|--------|----|------|------|
| `id` | INTEGER | PK, AUTOINCREMENT | 内部ID |
| `started_at` | DATETIME | NOT NULL | 実行開始時刻（UTC） |
| `finished_at` | DATETIME | NOT NULL | 実行終了時刻（UTC） |
| `mode` | VARCHAR | NOT NULL | ルーチン種別（`daily` / `full` / `quests` / `asgard-shop` / `consumable`） |
| `status` | VARCHAR | NOT NULL | `ok` / `failed`（スタミナ切れ等の正常終了は `ok`。クエスト失敗・consumable 失敗・購入エラー・ステータス取得不能・例外・割り込みは `failed`） |
| `exit_code` | INTEGER | | プロセスの終了コード |
| `accounts` | JSON | NOT NULL | アカウント別結果 `[{account, ok, error}]`（`error` は失敗理由） |
| `error_summary` | VARCHAR | | 失敗アカウント一覧とエラー要約（1 件以上失敗した場合のみ） |
| `log_text` | TEXT | | 出力全文（ANSI 除去済み） |
| `log_file` | VARCHAR | | 対応する `data/logs/` のログファイルパス（`HWGENIE_LOG_FILE` 経由。ラッパーが export した場合のみ記録され、CLI 直実行・Docker では `NULL`） |
| `hostname` | VARCHAR | | 実行環境識別子（`user@host` 形式。`HWGENIE_HOST` で明示上書き可能。既存行は `NULL`） |

古い行は `record_run_log`（`core/run_log.py`）が記録のたびに
`HW_LOG_KEEP_DAYS`（デフォルト 7 日、`0` で無効化）より古いものを削除します。
閲覧は `hw-genie log ls` / `log show <id>`。

---

### `team_configs`

`hw-genie battle import` が取り込んだ戦闘のチーム構成（攻撃側・防衛側の両方）。
同じ構成は `config_hash` で 1 行に集約されます。

| カラム | 型 | 制約 | 説明 |
|--------|----|------|------|
| `id` | INTEGER | PK, AUTOINCREMENT | 内部ID |
| `config_hash` | VARCHAR(64) | UNIQUE, NOT NULL | ステータス込みの完全構成ハッシュ（sha256） |
| `lineup_key` | VARCHAR(64) | NOT NULL, INDEX | 編成のみのハッシュ（ヒーロー順＋ペット＋バナー）。パワー/ステータス変化を無視 |
| `format` | VARCHAR(16) | NOT NULL | `team5` など |
| `heroes_summary` | VARCHAR(64) | NOT NULL | 編成順のヒーローID（例 `"13,35,52,61,7"`） |
| `pet_id` | INTEGER | | ペットID（API が「なし」を `0` で返す場合は `NULL`） |
| `banner_id` | INTEGER | | バナーID（API が「なし」を `0` で返す場合は `NULL`） |
| `config_json` | JSON | NOT NULL | 正規化した完全構成（ハッシュの元データ） |
| `completeness` | VARCHAR(16) | NOT NULL | `full`（ステータスあり）/ `ids_only`（ID のみ）/ `partial`（HWA の圧縮取り込みなど一部フィールド欠落） |
| `created_at` | DATETIME | DEFAULT CURRENT_TIMESTAMP | 作成日時 |

### `team_config_heroes`

`team_configs` の検索用スロット行。「ヒーロー X を含む構成」を索引で引くために使います。

| カラム | 型 | 制約 | 説明 |
|--------|----|------|------|
| `id` | INTEGER | PK, AUTOINCREMENT | 内部ID |
| `team_config_id` | INTEGER | FK -> team_configs.id (CASCADE), NOT NULL | 構成参照 |
| `slot` | INTEGER | NOT NULL | リプレイ上の並び順（0 始まり） |
| `hero_id` | INTEGER | NOT NULL, INDEX | ヒーローID |
| `level` / `star` / `power` | INTEGER | | スナップショット値 |

Unique constraint: `(team_config_id, slot)`

### `battle_records`

1 戦闘 = 1 行。`source` は `api_import`（サーバーが生成したリプレイ）/ `sim`（`battle simulate` の再シミュレーション）/ 将来の `attack` を同じ表に載せます。

| カラム | 型 | 制約 | 説明 |
|--------|----|------|------|
| `id` | INTEGER | PK, AUTOINCREMENT | 内部ID |
| `account_id` | INTEGER | FK -> accounts.id (CASCADE), NOT NULL | 取り込み元アカウント |
| `battle_type` | VARCHAR(16) | NOT NULL | `arena` / `grand` / `titan_arena`（`toe` は CLI の別名で、保存値は `titan_arena`） |
| `source` | VARCHAR(16) | NOT NULL | `api_import` / `hwa_import` / `sim`（`battle simulate`）/ 将来: `attack`。`sim` 行は stats / plan の集計から除外 |
| `source_record_id` | VARCHAR(64) | NOT NULL | リプレイID（重複排除キー） |
| `battle_id` | VARCHAR(64) | | 元の battle ID |
| `occurred_at` | DATETIME | NOT NULL | `startTime`（naive UTC） |
| `ended_at` | DATETIME | | `endTime` |
| `game_version` | VARCHAR(32) | | `result.serverVersion` |
| `attacker_config_id` / `defender_config_id` | INTEGER | FK -> team_configs.id | 構成 |
| `attacker_lineup_key` / `defender_lineup_key` | VARCHAR(64) | NOT NULL | 編成ハッシュ |
| `matchup_key` | VARCHAR(64) | NOT NULL | `(type, attacker lineup, defender lineup)` のハッシュ |
| `attacker_player_id` / `defender_player_id` | VARCHAR(32) | | プレイヤーID |
| `viewer_role` | VARCHAR(16) | | 取り込み元アカウントの立場: `attacker` / `defender` / `unknown`（リプレイの `userId` / `typeId` と `accounts.player_id` を比較。既存行は `NULL`） |
| `seed` | BIGINT | | リプレイの seed |
| `win` / `stars` | BOOLEAN / INTEGER | | 結果（`win` はリプレイの `result.win`。攻撃側 `userId` 視点と仮定） |
| `deterministic` | BOOLEAN | | 決定性の検証結果（未検証は NULL） |
| `authoritative` | BOOLEAN | NOT NULL | サーバー生成なら true |
| `engine` | VARCHAR(24) | NOT NULL | `game-server` など |
| `completeness` | VARCHAR(16) | NOT NULL | `full` / `ids_only` / `partial`（`partial` は防衛側が複数編成で先頭のみ保存した場合や HWA の圧縮取り込み） |
| `created_at` | DATETIME | DEFAULT CURRENT_TIMESTAMP | 作成日時 |

Unique constraint: `(source, source_record_id)`（**グローバル**。アカウント単位ではない。
観測ソース（`api_import` / `hwa_import` / `attack`）は replay id で重複排除され、
同一リプレイは最初に取り込んだ 1 行だけが残る。ただし `source='hwa_import'` の
`completeness != 'full'` な行に同一 replay id の**完全な**（`completeness = 'full'`）
`api_import` リプレイが後から届いた場合のみ、その行を `api_import` の完全な構成へ
in-place で upgrade する（新しい行は増やさない）。`ids_only` の API リプレイは
HWA のデータを置き換えず重複として扱い、`account_id` / `viewer_role` は upgrade
後も元の HWA 行の値を保持する。別アカウントでの再取り込みは重複としてスキップ
される。このため `battle stats -a <account>` は取り込み元 `account_id` ではなく、
`attacker_player_id` / `defender_player_id` がそのアカウントの `player_id` と
一致する行（関与ベース）を集計する）

Indexes: `(battle_type, matchup_key, game_version)`, `(defender_player_id, occurred_at)`, `(attacker_lineup_key, defender_lineup_key)`, `(battle_type, defender_lineup_key)`, `(source_record_id)`（`source != 'sim'` の重複判定用）

複数の防衛編成（`defenders` が 2 件以上のリスト）を含むリプレイは、先頭の編成のみを
保存し、`completeness` を `partial` にして警告ログを出します。

### `battle_comparisons`

予測（sim）と実攻撃の突き合わせ。`hw-genie battle simulate` が `source='sim'` 行と取り込み済みリプレイを比較して記録します。

| カラム | 型 | 制約 | 説明 |
|--------|----|------|------|
| `id` | INTEGER | PK, AUTOINCREMENT | 内部ID |
| `account_id` | INTEGER | FK -> accounts.id (CASCADE), NOT NULL | アカウント |
| `predicted_battle_id` / `actual_battle_id` | INTEGER | FK -> battle_records.id, NOT NULL | 比較する 2 行 |
| `expected_win` / `actual_win` | BOOLEAN | | 勝敗 |
| `expected_stars` / `actual_stars` | INTEGER | | スター |
| `mismatch_type` | VARCHAR(16) | NOT NULL | `win_flip` / `star_delta` / `unknown` など |
| `delta_stars` | INTEGER | | スター差 |
| `evaluated_at` | DATETIME | DEFAULT CURRENT_TIMESTAMP | 評価日時 |

Unique constraint: `(predicted_battle_id, actual_battle_id)`

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
    → 各 config_key を data dict にマージ（`player_*` はレガシー行として player_info へ再構成）
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
