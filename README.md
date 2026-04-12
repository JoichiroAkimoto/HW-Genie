# HW-Genie 🧞‍♂️

[![Buy Me a Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg)](https://buymeacoffee.com/joichiroakimoto)

**HW-Genie** は、Hero Wars のプレイを強力にサポートする AI エージェント対応の自動化ツールキットです。
Python による高速な API 自動化 (CLI) と、ブラウザ画面での利便性を高めるユーザースクリプト (Userscript) を統合したハイブリッドな構成を採用しています。

## 主な機能
- **Daily Routine**: ヒーローレイドとショッピングをワンコマンドで連続実行（アイテムのスタミナ限界の場合は中断）。
- **Hero Raid**: 指定したミッションのヒーローレイドを実行。
- **Item Raid**: 特定のアイテムを目的とした繰り返しレイドの自動化（スタミナ不足または指定回数に達するまで）。
- **Hero Shopping**: ターゲットショップでのヒーローソウル購入と、ソウルショップでの全アイテムの一括購入（余剰ソウルの自動換金対応）。
- **Auth & Session Sync**: `curl` コマンドを利用したセッション情報の管理・更新。ユーザースクリプトを使用した自動同期機能に対応。

## クイックスタート

### Python CLI (hw-genie)
Python 3.13+ と [uv](https://github.com/astral-sh/uv) の使用を推奨しています。

```bash
# 1. 依存関係のインストール (初回のみ)
uv sync

# 2. 実行 (ルートディレクトリから直接可能)
uv run hw-genie --help
```

### Docker での実行 (推奨)
環境構築なしでコンテナを使用して認証サーバーを起動できます。

```bash
# 1. ビルドと起動
docker-compose up --build -d

# 2. ログの確認
docker-compose logs -f
```

> **Security Note**: 認証サーバーを Docker 経由で起動する場合、コンテナ外部からのアクセスを許可するために `0.0.0.0` にバインドされます。公開サーバーで実行する場合は、ファイアウォール等で適切にアクセス制限を行ってください。

データベース (`hw_genie.db`) は `./data` ディレクトリに保存・永続化されます。

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
