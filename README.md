# HW-Genie 🧞‍♂️

[![Buy Me a Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg)](https://buymeacoffee.com/joichiroakimoto)

**HW-Genie** は、Hero Wars のプレイを強力にサポートする AI エージェント対応の自動化ツールキットです。
Python による高速な API 自動化 (CLI) と、ブラウザ画面での利便性を高めるユーザースクリプト (Userscript) を統合したハイブリッドな構成を採用しています。

## 主な機能
- **Daily Routine**: ヒーローレイドとショッピングをワンコマンドで連続実行（アイテムのスタミナ限界の場合は中断）。
- **Hero Raid**: 指定したミッションのヒーローレイドを実行。
- **Item Raid**: 特定のアイテムを目的とした繰り返しレイドの自動化（スタミナ不足または指定回数に達するまで）。
- **Hero Shopping**: ターゲットショップでのヒーローソウル購入と、ソウルショップでの全アイテムの一括購入（余剰ソウルの自動換金対応）。
- **DB Sync**: `hw-genie sync` でローカル Turso レプリカをクラウドと明示的に同期。
- **Auth & Session Sync**: `curl` コマンドを利用したセッション情報の管理・更新。ユーザースクリプトを使用した自動同期機能に対応。

## クイックスタート

### Python CLI (hw-genie)
Python 3.13+ と [uv](https://github.com/astral-sh/uv) の使用を推奨しています。

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

有効化後は `uv run` を付けずに直接 `hw-genie` や `pytest`, `ruff` を実行できるほか、並列処理スクリプト（`hwda` や `hwsa` など）も直接コマンドとして実行可能です。

### Nix を使用する場合（推奨）
[Nix](https://nixos.org/) がインストール済みの環境では、`direnv` が Python 3.13 や uv、turso-cli 等のツールを Nix 経由で自動的に提供します。従来の `.venv` による環境構築は不要です。

```bash
# 1. Nix のインストール（未インストールの場合）
# Determinate Nix Installer 推奨
curl --proto '=https' --tlsv1.2 -sSf -L https://install.determinate.systems/nix | sh

# 2. テンプレートから .envrc を作成して許可
cp copy.envrc .envrc
direnv allow

# 3. 以降、ディレクトリに入るだけで環境が自動構築される
#   - Nix が Python 3.13, uv, turso-cli, ruff, pytest 等を提供
#   - .env の環境変数を自動読み込み
#   - uv sync + .venv の有効化を自動実行
hw-genie --help
```

> Nix がインストールされていない環境では、従来の `.venv` ベースの環境に自動フォールバックします。

### Docker での実行 (推奨)
環境構築なしでコンテナを使用して認証サーバーや一括実行を起動できます。

```bash
# 1. 認証サーバーのビルドと起動
docker-compose up --build -d auth-server

# 2. ログの確認
docker-compose logs -f
```

> **Security Note**: 認証サーバーを Docker 経由で起動する場合、コンテナ外部からのアクセスを許可するために `0.0.0.0` にバインドされます。公開サーバーで実行する場合は、ファイアウォール等で適切にアクセス制限を行ってください。

データベース (`hw_genie.db`) は `./data` ディレクトリに保存・永続化されます。

#### コンテナ内での全アカウント一括実行（並列）
`hwda` / `hwsa` 相当の処理は、1 つのコンテナプロセス内で全アカウントを並列（スレッドプール）実行する `hw-genie multi` コマンドで行います。プロセス内並列のため、libSQL の Embedded Replica（ローカルファイル + Turso Syncs）をそのまま共有でき、`wal_insert_begin failed` の WAL 競合を回避できます（issue #47 の「案 E」）。

```bash
# 認証サーバーとは別に、全アカウントのデイリーを並列実行するサービスを起動
docker compose --profile bulk up --build -d hwda

# あるいは都度実行（指定アカウントのみ / 同時実行数を制限）
# サービスの command に既に `multi daily`/`multi full` が含まれるため、
# 渡すのはアカウント名と --parallel のみ（multi daily は重複指定しない）
docker-compose run --rm hwda --parallel 4 account1 account2
docker-compose run --rm hwsa
# ホスト上で直接実行する場合は bin/hwda・bin/hwsa を使う
bin/hwda --parallel 4 account1 account2
bin/hwsa
```

- `hwda` サービス: `hw-genie multi daily`（全アカウントのデイリールーチン）
- `hwsa` サービス: `hw-genie multi full`（ヒーローレイド + ショップ + デイリー）
- 同時実行数は環境変数 `HWDA_MAX_PARALLEL` で制限（0 / 未設定 = アカウント数 = 事実上無制限）

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
export TURSO_SYNC_INTERVAL="30"   # 同期間隔（秒、省略可)
# 接続ごとに明示的に sync() する (デフォルト true)。短時間のCLIコマンド
# (auth --list 等) は接続直後にクエリを投げるため、バックグラウンド同期が
# 完了する前に古いデータを読むのを防ぎます。常駐コンテナでは false にして
# sync_interval 任せにする方が効率的です。
export TURSO_SYNC_ON_CONNECT="true"
# 複数端末から書き込む場合は、write をリモートプライマリに直接行うよう推奨。
# 各端末のローカルレプリカ同士で書き込み競合するのを防ぐ。
export TURSO_WRITE_REMOTE="true"
```

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
> **パス指定**: `DATABASE_URL` のローカルファイルパスは以下の通り解決されます。
> - `sqlite+libsql:///./data/hw_genie.db` → `PKG_ROOT/data/hw_genie.db`（相対・推奨）
> - `sqlite+libsql:////abs/path.db` （4スラッシュ）→ そのまま絶対パス
> - `sqlite+libsql:///data/hw_genie.db` （3スラッシュ, `./` なし）→ リテラル絶対パス `/data/...`

### 表示・運用の環境変数

| 変数 | 既定値 | 説明 |
| --- | --- | --- |
| `HWGENIE_TZ` | `UTC` | `auth --list` の `Updated` 列の表示タイムゾーン（IANA 名、例: `Asia/Tokyo`）。DB には常に UTC で保存され、表示時のみ変換されます。 |
| `HWDA_LOG_KEEP_DAYS` | `7` | `bin/hwda` / `bin/hwsa` が `data/logs/` の古いログ（`hwda_*.log` / `hwsa_*.log`）を自動削除する保持日数。`0` で削除を無効化。 |

### 認証方法

#### 方法1: 手動 (curl コピー)
1. ブラウザの DevTools → Network タブから `api/` へのリクエストを右クリック → `Copy as cURL`
2. ターミナルで実行:
```bash
hw-genie auth --curl 'PASTE_CURL_COMMAND_HERE'
```

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

### Gemini CLI 連携
本リポジトリの `.agents/skills/` を読み込ませることで、Gemini CLI等のツールから自然言語でレイド等を指示できます。

## 開発環境
- **Backend**: Python 3.14 (Ruff, pytest)
- **Frontend**: TypeScript (Bun, Vite)

## ライセンス
[MIT License](LICENSE)
