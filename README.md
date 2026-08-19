# HW-Genie 🧞‍♂️

[![Sponsor](https://img.shields.io/badge/Sponsor-HW--Genie-ea4aaa.svg?style=for-the-badge&logo=github)](https://github.com/sponsors/JoichiroAkimoto)
[![Python Checks](https://github.com/JoichiroAkimoto/HW-Genie/actions/workflows/python-tests.yml/badge.svg)](https://github.com/JoichiroAkimoto/HW-Genie/actions/workflows/python-tests.yml)
[![Userscript CI](https://github.com/JoichiroAkimoto/HW-Genie/actions/workflows/userscript-ci.yml/badge.svg)](https://github.com/JoichiroAkimoto/HW-Genie/actions/workflows/userscript-ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**HW-Genie** は、Hero Wars のプレイを強力にサポートする AI エージェント対応の自動化ツールキットです。
Python による高速な API 自動化 (CLI) と、ブラウザ画面での利便性を高めるユーザースクリプト (Userscript) を統合したハイブリッドな構成を採用しています。

📚 プロジェクトサイト (GitHub Pages): [https://joichiroakimoto.github.io/HW-Genie/](https://joichiroakimoto.github.io/HW-Genie/)

## 目次

- [主な機能](#主な機能)
- [クイックスタート](#クイックスタート)
  - [認証方法](#認証方法)
  - [AI エージェント連携](#ai-エージェント連携)
- [開発環境](#開発環境)
- [サポート](#サポート)
- [FAQ](#faq)
- [免責事項](#免責事項)
- [ライセンス](#ライセンス)

## 主な機能

- **Daily Routine**: ヒーローレイドとショッピングをワンコマンドで連続実行（アイテムのスタミナ限界の場合は中断）。
- **Hero Raid**: 指定したミッションのヒーローレイドを実行。
- **Item Raid**: 特定のアイテムを目的とした繰り返しレイドの自動化（スタミナ不足または指定回数に達するまで）。
- **Quest Status**: 現在のクエスト（デイリー・週次・ギルド・メイン・イベント etc.）の状態をカテゴリ別に取得・表示。`--execute` でデイリーの自動完了も可能（実行可否はアカウントごとの `quest_defaults` enabled フラグで制御、`--edit-defaults` の対話的ウィザードで設定、`hw-genie quests`）。`multi quests` で全アカウント一括自動完了（`--dry-run` 予行可）、daily / full ルーチンにも統合済み。
- **Hero Shopping**: ターゲットショップでのヒーローソウル購入と、ソウルショップでの全アイテムの一括購入（余剰ソウルの自動換金対応）。
- **Asgard Shop**: Asgard（ギルドレイド）の Realm Traveler ショップで Valor Emblem を使ったバフとゴールドバフを自動購入（`hw-genie asgard-shop` / `multi asgard-shop`。Osh 週は固定優先度、Maestro 週は優先度 S→A→B の組み合わせ最適化で購入。判定不能な週はスキップ、`--dry-run` で計画表示のみ、`--gold` / `--no-gold` でゴールドバフ購入を常時 on / off（デフォルトは週依存: Osh 週 off / Maestro 週 on））。
- **Consumable**: 所持 consumable の在庫確認（`hw-genie inventory`。名前付き表示・`--all`/`--min`/`--raw`）と、レジストリ登録済みアイテムの一括全消費（`hw-genie consumable run` / `multi consumable`。対象は `CONSUMABLE_USE_TARGETS` に固定登録、在庫は実行時に `inventoryGet` で自動取得して全量消費、在庫 0 はスキップ。`--dry-run` で予行確認可）。
- **Multi Account**: 複数アカウントのレイド・ショッピング・デイリークエスト・consumable 消費を単一プロセス内で並列実行（`hw-genie multi`）。
- **DB Sync**: `hw-genie sync` でローカル Turso レプリカをクラウドと明示的に同期。
- **DB Check**: `hw-genie db-check` で全アカウントの `account_configs` を走査し、壊れた config JSON（手動編集ミス等）を検出・一覧表示（壊れがあれば exit 1）。壊れた行は読み取り時に警告付きでスキップされるため日常操作は続行可能。
- **Run Log**: `multi` 実行（hwda / hwsa / Docker / 別ホストの CLI 直実行）の結果サマリーと出力全文を DB（`run_logs` テーブル）に保存し、Turso 同期経由で全環境から閲覧可能（`hw-genie log ls` / `log show <id>`。実行環境識別子 `ユーザー名@ホスト名` を自動記録。保持日数は `HW_LOG_KEEP_DAYS`、デフォルト 7 日）。
- **Auth & Session Sync**: `curl` コマンドを利用したセッション情報の管理・更新。ユーザースクリプトを使用した自動同期機能に対応。

## クイックスタート

### Python CLI (hw-genie)

Python 3.13 と [uv](https://github.com/astral-sh/uv) の使用を推奨しています。

> **開発者向け**: DB の状態確認には [turso CLI](https://docs.turso.tech/reference/turso-cli) が便利です。`turso auth login` で認証後、`turso db shell hw-genie-db "SELECT ..."` で Turso クラウド上の最新データを直接参照できます。

```bash
# 1. 依存関係のインストール (初回のみ)
uv sync

# 2. 実行 (ルートディレクトリから直接可能)
uv run hw-genie --help

# デバッグログを表示する場合 (--debug はサブコマンドの前に配置)
uv run hw-genie --debug auth --list
```

### Tips: direnv による自動有効化

[direnv](https://direnv.net/) を使用すると、ディレクトリに移動するだけで自動的に仮想環境が有効化され、`bin/` 内の便利スクリプトへパスが通ります。

```bash
# テンプレートをコピーして .envrc を作成し、実行を許可
cp copy.envrc .envrc
direnv allow
```

`copy.envrc` は `.env` を自動で読み込むため、Turso 接続設定等の環境変数が
コマンド実行時に正しく展開されます。既存の `.envrc` を手動で作成している場合は、
`copy.envrc` の最新版を `.envrc` に反映してください（`.env` の `dotenv` 読み込みが
含まれていないと `TURSO_*` 等が未設定となります）。

有効化後は `uv run` を付けずに直接 `hw-genie` や `pytest`, `ruff` を実行できるほか、並列処理スクリプト（`hwda` や `hwsa` など）も直接コマンドとして実行可能です（Nix 環境の場合は代わりに `uv run --locked` を使用します。詳細は「Nix を使用する場合」を参照）。`hwda` / `hwsa` は起動時に仮想環境を再同期しない `uv run --no-sync` で実行されるため、依存関係（`pyproject.toml` / `uv.lock`）を更新した場合や初回実行前は、事前に `uv sync` を一度実行してください（未同期だと `.venv` 未作成時は起動失敗、依存更新後は実行時エラーになります）。

### Nix を使用する場合 (任意・Nix ユーザー向け)

[Nix](https://nixos.org/) がインストール済みの環境では、`direnv` が Python 3.13 や uv、turso-cli 等のツールを Nix 経由で自動的に提供します。

```bash
# 1. Nix のインストール（未インストールの場合）
curl --proto '=https' --tlsv1.2 -sSf -L https://install.determinate.systems/nix | sh -s -- install

# 2. テンプレートから .envrc を作成して許可
cp copy.envrc .envrc
direnv allow

# 3. 依存関係の同期（初回のみ、または pyproject.toml/uv.lock 更新後）
uv sync --locked

# 4. コマンドは uv run --locked 経由で実行
uv run --locked hw-genie --help
uv run --locked pytest
uv run --locked ruff check .
```

> Nix 環境で `hwda` / `hwsa` を使用する場合も、事前に **ステップ3 の `uv sync --locked`** を済ませておいてください（両スクリプトは `uv run --no-sync` で起動するため）。

> Nix がインストールされていない環境では、従来の `.venv` ベースの環境に自動フォールバックします（`.venv` が存在すれば `source .venv/bin/activate`、なければ何もしません）。

### Docker での実行 (推奨)

環境構築なしでコンテナを使用して認証サーバーや一括実行を起動できます。

> **イメージの取得**: Docker イメージは GitHub Actions でビルドされ GHCR
> （`ghcr.io/joichiroakimoto/hw-genie`）に公開されています（main 更新 → `latest`、
> バージョンタグ → `vX.Y.Z`）。ローカルに無い場合は自動 pull され、
> ローカルビルドは原則不要です（pull 失敗時のみフォールバックでローカルビルド。
> フォールバック後は `docker compose pull` を実行するまで最新化されません）。
> 最新イメージへ更新するには:
>
>     docker compose pull
>     docker compose up -d
>
> hwda / hwsa（bulk プロファイル）も更新する場合は `--profile bulk` を付けます
> （例: `docker compose --profile bulk pull`）。

```bash
# 1. 認証サーバーの起動（イメージは初回に自動取得される）
docker compose up -d auth-server

# 2. ログの確認
docker compose logs -f
```

> **Security Note**: 認証サーバーを Docker 経由で起動する場合、コンテナ外部からのアクセスを許可するために `0.0.0.0` にバインドされます。公開サーバーで実行する場合は、ファイアウォール等で適切にアクセス制限を行ってください。

データベース (`hw_genie.db`) は `./data` ディレクトリに保存・永続化されます。

#### コンテナ内での全アカウント一括実行（並列）

`hwda` / `hwsa` 相当の処理は、1 つのコンテナプロセス内で全アカウントを並列（スレッドプール）実行する `hw-genie multi` コマンドで行います。プロセス内並列のため、libSQL の Embedded Replica（ローカルファイル + Turso Syncs）をそのまま共有でき、`wal_insert_begin failed` の WAL 競合を回避できます（issue #47 の「案 E」）。

```bash
# 認証サーバーとは別に、全アカウントのデイリーを並列実行するサービスを起動
docker compose --profile bulk up -d hwda

# あるいは都度実行（指定アカウントのみ / 同時実行数を制限）
# サービスの command に既に `multi daily`/`multi full` が含まれるため、
# 渡すのはアカウント名と --parallel のみ（multi daily は重複指定しない）
docker compose run --rm hwda --parallel 4 account1 account2
docker compose run --rm hwsa
# ホスト上で直接実行する場合は bin/hwda・bin/hwsa を使う
bin/hwda --parallel 4 account1 account2
bin/hwsa
```

- `hwda` サービス: `hw-genie multi daily`（全アカウントのデイリールーチン）
- `hwsa` サービス: `hw-genie multi full`（ヒーローレイド + ショップ + デイリー）
- `multi quests`: 全アカウントのデイリークエスト自動完了のみ（`--dry-run` で予行確認可）。daily / full は実行後に各アカウントの `quest_defaults.enabled` クエストを自動完了します（enabled のみ、初期状態は無効）
- `multi asgard-shop`: 全アカウントの Asgard ショップ自動購入のみ（Osh / Maestro 週を自動判定、`--gold` / `--no-gold` でゴールドバフ購入を常時 on / off）
- `multi consumable`: 全アカウントの登録済み consumable 一括消費のみ（`--dry-run` で予行確認可、`--lib` / `--method` で対象・メソッド上書き）
- 同時実行数は環境変数 `HW_MAX_PARALLEL` で制限（0 / 未設定 = アカウント数 = 事実上無制限）

### libSQL (Turso) の利用

libSQL (Turso) を使用する場合は、環境変数 `DATABASE_URL` を指定します。

```bash
export DATABASE_URL="sqlite+libsql://[your-db].turso.io?auth_token=[your-token]"
```

#### ローカルでの libSQL サーバーの起動

Docker を使用してローカルに libSQL サーバー (`sqld`) を起動し、接続テストを行うことができます。

```bash
# 1. サーバーの起動
docker run -d -p 8080:8080 -p 5001:5001 ghcr.io/tursodatabase/libsql-server:latest

# 2. 接続先の設定
export DATABASE_URL="libsql://localhost:8080"

# 3. 実行
uv run hw-genie --help
```

#### 明示的同期

`uv run hw-genie sync` で、ローカルレプリカを Turso クラウドと明示的に同期できます。`TURSO_SYNC_URL` 未設定時は何もせず終了します。

```bash
uv run hw-genie sync
```

#### 実行ログの確認

`multi` 実行（hwda / hwsa / CLI 直実行 / Docker）は、終了時に結果サマリーと出力全文を
DB の `run_logs` テーブルへ保存します（best-effort: DB 書き込みが失敗しても実行自体は
中断されません）。Turso 同期を利用している場合、別ホストからも同じログを確認できます。

```bash
uv run hw-genie log ls          # 直近の実行一覧（--limit N で件数指定）
uv run hw-genie log show 42     # ID 指定で詳細（メタデータ + 出力全文）
```

保持日数は `HW_LOG_KEEP_DAYS`（デフォルト 7 日、`0` で無効化）。フルログのファイル
（`data/logs/`）は従来どおり `bin/hwda` / `bin/hwsa` が保存します。

各レコードには実行環境識別子（`ユーザー名@ホスト名`、例: `ak@ak-mac`）が自動記録され、
Turso 同期で混在する複数環境の実行を切り分けられます（`log ls` の最終列・`log show` の
`Host:` 行で確認）。識別子は次の優先順位で決まります:

1. `HWGENIE_HOST`（明示指定）→ 最優先（例: `HWGENIE_HOST=win-pc uv run hw-genie multi daily`）
2. Docker 実行時はホストのシェル環境変数が Compose 補間で自動反映されるため、`.env` の設定は不要
   - WSL・Mac・Linux（シェル実行）: `USER` / `HOSTNAME` が自動反映
     （ホスト名は bash 実行時のみ。zsh 等では `HOSTNAME` が環境変数にならないため、
     `.env` の `HWGENIE_HOST` 指定を推奨）
   - ネイティブ Windows（PowerShell / cmd）: `USERNAME` / `COMPUTERNAME` が自動反映
3. 環境変数: ユーザーは `HWGENIE_USER` → `HWGENIE_USER_UNIX` → `USER` → `USERNAME` → `getpass.getuser()`、
   ホストは `HWGENIE_MACHINE` → `HWGENIE_MACHINE_UNIX` → `COMPUTERNAME` → `HOSTNAME` → `socket.gethostname()`
   （取得できない場合は `unknown` にフォールバック）

また `log_file` 列には、ラッパーが `HWGENIE_LOG_FILE` を export した場合のみファイル
パスが記録されます（現行は `bin/hwda` / `bin/hwsa`。CLI 直実行・Docker では `NULL`）。

#### Turso Embedded Replicas (Syncs) の利用

`TURSO_SYNC_URL` を設定すると、ローカルの SQLite ファイルをリモート DB の
**Embedded Replica (同期レプリカ)** として動作させられます。ローカルファイルへの
読み書きは高速で、バックグラウンドでリモートと自動同期されます。

```bash
# ローカルレプリカの保存先。"./" 付きの相対パスはプロジェクトルート(PKG_ROOT)基準で
# 解決されるため、コンテナ(/app/data)とホスト(リポジトリの data/)で同じ .env が使える。
export DATABASE_URL="sqlite+libsql:///./data/hw_genie.db"
export TURSO_SYNC_URL="libsql://[your-db].turso.io"
export TURSO_AUTH_TOKEN="[your-token]"
# バックグラウンド同期間隔（秒）。注意: 設定すると接続オープン直後に libSQL が
# バックグラウンド sync を走らせ、複数接続を並列に使うコマンド (auth --list
# --fresh 等) で WAL 競合を頻発させるため、短命なホスト CLI では設定しないこと。
# 長時間稼働の hwda/hwsa コンテナ用は docker-compose.yml で個別指定する。
# export TURSO_SYNC_INTERVAL="30"
# 接続ごとに明示的に sync() する (デフォルト true)。短時間のCLIコマンド
# (auth --list 等) は接続直後にクエリを投げるため、バックグラウンド同期が
# 完了する前に古いデータを読むのを防ぎます。常駐コンテナでは false にして
# sync_interval 任せにする方が効率的です。
export TURSO_SYNC_ON_CONNECT="true"
# 複数端末から書き込む場合は、write をリモートプライマリに直接行うよう推奨。
# 各端末のローカルレプリカ同士で書き込み競合するのを防ぐ。
export TURSO_WRITE_REMOTE="true"
```

<details>
<summary><strong>動作の詳細・実装メモ</strong>（クリックで展開）</summary>

> **実装メモ**: `sqlalchemy-libsql` 0.2.0 の標準ダイアレクトはローカルファイル時に
> `sync_url` 等を破棄してしまうため、`hw_genie/core/database.py` の
> `TursoReplicaDialect` がこれらを `libsql_experimental.connect` へ転送します。
> `sqlite+libsql://` スキームをそのまま利用できます。
>
> **接続時同期の重要性**: バックグラウンド同期 (`sync_interval`) はプロセスが
> 生存している間しか動きません。`auth --list` のような短時間コマンドは接続直後に
> クエリを投げるため、同期完了前に古いローカルレプリカを読む可能性があります。
> `TURSO_SYNC_ON_CONNECT=true` (既定) にすると、レプリカ接続のたびに
> `conn.sync()` をブロック実行し、必ず最新状態を読み込みます。他端末で書いた
> 変更を即座に反映したい場合はこの設定を有効にしてください。
>
> **書き込みはリモート直接 (`TURSO_WRITE_REMOTE=true`)**: 複数端末から書き込む構成では、
> 各端末のローカルレプリカがそれぞれリモートへ push し競合する恐れがあります。
> `TURSO_WRITE_REMOTE=true` にすると、`SessionRepository` の書き込み系メソッド
> (`update_config` 等) はローカルレプリカではなく**リモートプライマリへ直接**書き込みます。
> 読み取りは引き続きローカルレプリカ (`TURSO_SYNC_ON_CONNECT` で最新化) を使用するため、
> 端末間で一貫性が保たれます。未設定時は読み書きとも従来のレプリカ経由で動作します。
>
> **WAL 競合の自動リトライ**: ローカルレプリカは同一マシン上の複数プロセス
> （並列起動した CLI・常駐 auth-server・`multi` 等）で共有されるため、SQLite WAL の
> 単一ライター制約により稀に `wal_insert_begin failed` が発生します。同一プロセス内では
> 接続時 `sync()` と書き込みトランザクションが共有ロック（RLock）で直列化され、
> プロセス間の競合のみ指数バックオフ付きで自動リトライされます（非競合エラーは即時失敗）。
> 頻発する場合は `TURSO_READ_REMOTE=true` と `TURSO_WRITE_REMOTE=true` を併用した
> 完全リモート構成（ローカルファイル不使用）に切り替えることで構造的に回避できます。
> **`TURSO_SYNC_INTERVAL` は設定しないこと**: 設定すると libSQL が接続オープン直後に
> バックグラウンド sync を走らせ、複数接続を並列に使うコマンド（`auth --list --fresh` 等）で
> WAL 競合を毎回招きます（新鮮さは接続時 sync が担保）。長時間稼働の hwda/hwsa コンテナ用にのみ
> docker-compose.yml で個別指定しています。
>
> **パス指定**: `DATABASE_URL` のローカルファイルパスは以下の通り解決されます。
> - `sqlite+libsql:///./data/hw_genie.db` → `PKG_ROOT/data/hw_genie.db`（相対・推奨）
> - `sqlite+libsql:////abs/path.db` （4スラッシュ）→ そのまま絶対パス
> - `sqlite+libsql:///data/hw_genie.db` （3スラッシュ, `./` なし）→ リテラル絶対パス `/data/...`

</details>

### 表示・運用の環境変数

| 変数 | 既定値 | 説明 |
| --- | --- | --- |
| `HWGENIE_TZ` | `UTC` | `auth --list` の `Updated` 列の表示タイムゾーン（IANA 名、例: `Asia/Tokyo`）。DB には常に UTC で保存され、表示時のみ変換されます。 |
| `HW_LOG_KEEP_DAYS` | `7` | `bin/hwda` / `bin/hwsa` が `data/logs/` の古いログ（`hwda_*.log` / `hwsa_*.log`）を自動削除する保持日数。DB 上の実行ログ（`run_logs` テーブル）の保持日数も兼ねる（`hw-genie multi` が記録のたびに古い行を削除）。`0` で削除を無効化。 |

### 認証方法

#### 方法1: 手動 (curl コピー)

1. ブラウザの DevTools → Network タブから `api/` へのリクエストを右クリック → `Copy as cURL`
2. ターミナルで実行:
```bash
hw-genie auth --curl 'PASTE_CURL_COMMAND_HERE'
```

**最新ステータスの一覧取得**: `auth --list` は DB のキャッシュ値を表示します。
ゲームサーバーから最新ステータスを取得して DB を更新してから表示するには `--fresh` を併用します
（全アカウント並列、`-a` で特定アカウントのみ指定可能）:
```bash
hw-genie auth --list --fresh
```
`Memo` 列は切り捨てられず、端末幅に応じて複数行に折り返して全文表示されます
（改行入りメモはそのまま継続行として表示）。幅は `COLUMNS` 環境変数で固定することもできます。

#### 方法2: 自動キャプチャ (推奨)

1. 認証サーバーを起動:
```bash
hw-genie auth-server
```
2. ブラウザに Userscript (`src/userscripts/index.ts`) を Tampermonkey 等にインストール
3. Hero Wars を開くと自動的に認証情報がキャプチャされ、セッションが更新されます

**ポート変更**: 環境変数 `HW_GENIE_AUTH_PORT` で変更可能（デフォルト: 8765）
```bash
HW_GENIE_AUTH_PORT=9000 hw-genie auth-server
```

**起動方法の使い分け**: 認証サーバーはホスト直接実行（上記 `hw-genie auth-server`）と Docker 実行（`docker compose up --build -d auth-server`）のどちらでも起動できます。

| 方法 | 推奨ケース | 備考 |
| --- | --- | --- |
| ホスト直接実行 (`hw-genie auth-server`) | ローカルでの一時利用・開発時 | ローカルレプリカをそのまま使用 |
| Docker 実行 (`docker compose up -d auth-server`) | 常駐・長時間運用 | コンテナ内では完全リモート構成（`TURSO_READ_REMOTE` / `TURSO_WRITE_REMOTE=true`）になり、ホスト CLI との WAL 競合を構造的に回避 |

どちらも認証キャプチャ機能に違いはありません。常駐させる場合は Docker 実行がおすすめです。

### AI エージェント連携

本リポジトリの `.agents/skills/` を読み込ませることで、Gemini CLI や Claude Code 等の AI エージェントツールから自然言語でレイド等を指示できます。各スキル（`daily-raid`・`hero-raid`・`hero-shopping` 等）の説明は [AGENTS.md](AGENTS.md) を参照してください。

## 開発環境

- **Backend**: Python 3.13 (Ruff, pytest)
- **Frontend**: TypeScript (Bun, Vite)

### 依存関係の更新

依存関係は Dependabot が週次で更新 PR を出します。マージ後に `git pull` したら `uv sync --locked`（または `make sync`）を 1 回実行してください。

```bash
git pull
make sync   # = uv sync --locked（依存のダウンロードはこの時だけ）
```

`hwda` / `hwsa` は `uv run --no-sync` で起動するため、未同期のまま実行すると実行時エラーになります（起動時に未同期を検出して警告を表示します）。新規依存の追加は `uv add --project src/python <pkg>` で行ってください。

## サポート

HW-Genie の開発を継続・改善するために、[GitHub Sponsors](https://github.com/sponsors/JoichiroAkimoto) でのご支援を受け付けています（README 冒頭の **[Sponsor]** バッジから）。
支援金は主に**開発時間の確保・インフラ費用（Turso DB 等）・ライブラリ更新の継続**に充てられます。ご無理のない範囲で応援していただければ幸いです。

## FAQ

<details>
<summary><strong>Q. 認証情報が古くなってエラーが出ます</strong></summary>

`hw-genie auth --curl '...'` で新しい curl コマンドを渡すか、認証サーバー + Userscript の自動キャプチャを利用してください。

</details>

<details>
<summary><strong>Q. アカウントはどうやって指定しますか</strong></summary>

`-a` / `--account` オプションでプレイヤー名を指定します。登録アカウントが 1 件のみの場合は自動選択されます。複数アカウントを扱う場合は `hw-genie multi` を使います。

</details>

<details>
<summary><strong>Q. 複数端末から同じ DB を使いたい</strong></summary>

各端末で同じ Turso リモート URL を `DATABASE_URL`（例: `sqlite+libsql://[your-db].turso.io?auth_token=...`）に指定すれば、そのまま共有できます（デフォルト構成が Turso リモート直結のため追加設定は不要）。ローカルファイルをキャッシュとして高速化したい場合のみ Embedded Replicas（`TURSO_SYNC_URL`）を設定してください。詳細は「libSQL (Turso) の利用」を参照してください。

</details>

## 免責事項

- 本ツールは **非公式** の自動化ツールであり、Hero Wars の開発・運営元（Nexters 等）とは一切関係ありません。
- ゲームの利用規約に反する可能性があるため、**自己責任** で使用してください。
- 本ツールの使用により生じたいかなる損害・不利益についても、作者は責任を負いません。

## ライセンス

[MIT License](LICENSE)
