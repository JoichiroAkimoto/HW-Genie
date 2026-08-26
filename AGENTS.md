# AGENTS.md 🧞‍♂️

このファイルは、AI エージェントが **HW-Genie** リポジトリ内のツールやスキルを効果的に使用するためのガイドです。詳細仕様は各リンク先を参照してください。

## リポジトリ概要
Hero Wars の API 自動化ツールキットです。Python CLI (`hw-genie`) を通じてレイドやショッピング等の操作を行います。

## エージェント向け操作ガイド

### 1. スキルの活用
`.agents/skills/` フォルダ内には、エージェントが各機能を実行するための定義（`SKILL.md`）が格納されています。各スキルの実行例・詳細は該当ファイルを参照してください。

*   **daily-raid**: レイド＋ショッピングの連続実行（アイテムのスタミナ限界収集対応）。
*   **hero-raid**: 指定ミッションのヒーローレイド。
*   **hero-shopping**: ヒーローソウルとソウルショップの一括購入。
*   **asgard-shop**: Asgard（ギルドレイド）の Realm Traveler ショップ（Osh / Maestro 週）で Valor Emblem を使ったバフとゴールドバフの自動購入。
*   **consumable**: 所持品（inventory）の在庫確認と、登録済み consumable の一括全消費（`--dry-run` で予行確認。1000 上限アイテムは分割消費、マトリョーシカ系は残りが無くなるまでラウンドを自動繰り返し）。
*   **guild-chat**: ギルドチャット（`chatGetAll` / `chatType=clan`）の履歴取得・表形式と要約表示（`--type` / `--count` / `--last-id` / `--raw` / `--json`）。
*   **hero-wars-auth**: セッション管理・ユーザー情報取得（curl コマンドで認証更新）。
*   **item-raid**: 特定アイテムの繰り返し収集。
*   **quest-status**: クエスト状態の取得・表示と自動完了（`--execute`）。
*   **db-inspect**: データベース（Turso クラウド / ローカルレプリカ）の確認。

### 2. コマンドラインツールの使用法
すべての操作はルートディレクトリから `uv run hw-genie` を通じて行われます。実行前にルートディレクトリにいることを確認してください（`uv run` が依存関係の解決と仮想環境構築を自動で行います）。

*   **ミッションレイド**: `uv run hw-genie raid hero <id1> <id2> --times 3`
*   **アイテムレイド**: `uv run hw-genie raid item --curl '...' --iterations N`（`--times` は `--iterations` のエイリアス。反復回数 = 1 リクエストあたり `times:10` × N。デフォルトはスタミナ限界まで）
*   **ショップ購入**: `uv run hw-genie shop`
*   **Asgard ショップ購入**: `uv run hw-genie asgard-shop`（Osh / Maestro 週を自動判定。`--dry-run` で計画表示のみ、`--gold` / `--no-gold` でゴールドバフ購入を常時 on / off（デフォルトは週依存: Osh 週 off / Maestro 週 on）。詳細は `.agents/skills/asgard-shop/SKILL.md`）
*   **デイリールーチン**: `uv run hw-genie daily`（`--iterations N` でアイテムレイドの反復回数を指定可能 = 1 リクエストあたり `times:10` × N。ヒーローレイドは固定 3 回、デフォルトはスタミナ限界まで）
*   **クエスト状態・自動完了**: `uv run hw-genie quests`（実行可否は `quest_defaults` の `enabled` フラグでアカウントごとに制御。詳細は `.agents/skills/quest-status/SKILL.md`）
*   **在庫確認**: `uv run hw-genie inventory`（consumable 中心の所持品一覧。`--all` で全カテゴリ、`--min N` でフィルタ、`--raw` で生 JSON）
*   **consumable 一括消費**: `uv run hw-genie consumable run`（レジストリ `CONSUMABLE_USE_TARGETS` 登録済みアイテムを在庫全量消費。1000 上限アイテムは分割、マトリョーシカ系は残りが無くなるまで自動繰り返し。`<libId>` 位置引数で指定、未登録アイテムは `--method` でメソッド指定、`--dry-run` で予行。詳細は `.agents/skills/consumable/SKILL.md`）
*   **ギルドチャット**: `uv run hw-genie chat`（`chatGetAll` / `chatType=clan` の履歴を表形式と要約で表示。`--type` / `--count` / `--last-id` / `--raw` / `--json`。詳細は `.agents/skills/guild-chat/SKILL.md`）
*   **アカウント指定**: アカウントは実名（プレイヤー名）で保存されます。`-a`/`--account` 未指定時は、登録が 1 件なら自動選択、複数件なら指定を要求します。`multi` は対象未指定時は全アカウント実行です。
*   **全アカウント一括**: `uv run hw-genie multi daily`（`full` でレイド＋ショップ＋デイリー、`quests` でクエスト自動完了のみ、`asgard-shop` で Asgard ショップ購入のみ（`--gold` / `--no-gold` でゴールドバフ購入を常時 on / off、デフォルトは週依存: Osh 週 off / Maestro 週 on）、`consumable` で consumable 一括消費のみ、`--dry-run` でプラン表示のみ）。`--parallel N` で同時実行数、`account1 account2 ...` で対象限定（dry-run は逐次実行に強制）、`--iterations N` で `daily`/`full` モードのアイテムレイド反復回数を指定。クエスト／consumable 失敗アカウントがあると exit 1。詳細は README.md の「Docker での実行」セクションを参照。
*   **登録アカウント一覧**: `uv run hw-genie auth --list`（`--fresh` で最新ステータス取得）
*   **認証状態確認**: `uv run hw-genie auth --info`
*   **認証サーバー起動**: `uv run hw-genie auth-server`（`--once` で 1 回限り）
*   **同期**: `uv run hw-genie sync`（ローカル Turso レプリカをクラウドと明示的に同期）
*   **実行ログ確認**: `uv run hw-genie log ls` / `log show <id>`（`multi` 実行の結果サマリーと出力全文が DB の `run_logs` に保存される。Turso 同期経由で別ホストからも閲覧可。保持日数は `HW_LOG_KEEP_DAYS`、デフォルト 7 日）
*   **DB 整合性チェック**: `uv run hw-genie db-check`（壊れた config JSON を検出・一覧表示。壊れがあれば exit 1）
*   **デバッグ出力**: 各コマンドに `--debug` を付与（例: `uv run hw-genie --debug daily`）

> **環境変数・表示設定**: `HWGENIE_TZ` / `FORCE_COLOR` / `NO_COLOR` / `HW_LOG_KEEP_DAYS` / `HWGENIE_HOST` / Turso 関連（`TURSO_WRITE_REMOTE` / `TURSO_SYNC_INTERVAL` 等）の詳細は README.md の環境変数表・「libSQL (Turso) の利用」セクションを参照してください。

### 3. API 仕様とメソッドの理解
Hero Wars の RPC API（メソッド一覧やデータ構造）の詳細は、以下のドキュメントを参照してください。

👉 **[docs/api/INDEX.md](docs/api/INDEX.md)**

エージェントが新しいレイド対象を提案したり、特定の API レスポンスを解析したりする際に、このドキュメントが役立ちます。

### 4. 重要事項（エージェント向け）
*   **DB 構成**: データは Turso クラウド (`hw-genie-db`) とローカルレプリカ (`data/hw_genie.db`) で同期されています。クラウドが単一ソースオブトゥルースです。確認方法は `.agents/skills/db-inspect/SKILL.md`、スキーマは **[docs/db/schema.md](docs/db/schema.md)** を参照。
    *   **worktree / 新規クローンの準備**: `.env` は `.gitignore` 済みのため checkout に含まれません。worktree で DB 操作する前に main worktree の `.env` へのシンボリックリンクを貼ってください（例: `ln -s /path/to/main-worktree/.env .env`）。解除は `unlink .env`（`rm` は main の `.env` を誤削除する恐れがあるため使用しないこと）。
*   **認証エラー**: 認証エラーが発生した場合は、ユーザーに新しい `curl` コマンドの提供を求めるか、`auth-server` を起動して Userscript 経由での同期を促してください。
*   **レートリミット**: `HWClient` クラスの `sleep()` メソッドによりリクエスト間に待機時間が設けられていますが、大量の並列実行は避けてください。
*   **タイプ安全なレスポンス**: `src/python/hw_genie/core/client.py` で定義されている `ResponseStatus` や `Emojis` を使用して、実行結果を分かりやすく報告するようにしてください。

## 5. 複数アカウント・複数タスクの実行戦略
大量の処理（例：複数アカウントでのレイド＋ショッピング）を行う際は、タイムアウトを避けるため以下の戦略を徹底してください。

*   **単一アカウントの逐次実行**: 一つのアカウントに対して複数のタスクを順番に行う場合は、サブエージェント (`Task`) を使用せず、メインセッションで直接 `bash` ツールを実行してください。これにより、不要なコンテキスト転送のオーバーヘッドを削減できます。
*   **アカウント単位の並列化**: 複数のアカウントで同様の処理を行う場合は、`Task` ツールを使用してアカウントごとにサブエージェントを起動し、並列に実行してください。これにより、単一プロセスのタイムアウトを回避し、全体の処理時間を短縮できます。
*   **タイムアウトの制御**: 長時間の実行が予想されるコマンド（大量のミッションレイドなど）を実行する場合、`bash` ツールの `timeout` パラメータ（ミリ秒指定）を適切に設定して実行してください（例: 600秒なら `timeout: 600000`）。
*   **タスク単位の逐次実行**: 同一アカウント内での異なるタスク（例：レイド完了後にショッピングを行う）は、一つの `bash` コマンドに `&&` で繋いで詰め込むのではなく、個別のツール呼び出しとして逐次実行してください。

## 6. エージェント向け開発ルール

本リポジトリのスクリプトやスキルを開発・修正する際は、以下のルールを **徹底** してください。

### テストとリンターの実行（必須）
`src/python/hw_genie` 以下のコードを変更した場合は、必ず以下のコマンドを実行し、エラーがないことを確認してください。

*   **リンター**: `uv run --locked ruff check . --fix`
*   **テスト**: `uv run --locked pytest`

新機能の追加やバグ修正の際は、`src/python/tests` に適切なテストケースを追加・更新し、既存のテストを壊していないことを確認してください。

### 依存関係の管理（重要）
本リポジトリでは、依存関係の唯一の正解（Source of Truth）として `pyproject.toml` を使用しています。新しいライブラリを追加する際は、用途に応じて適切なファイルとセクションを更新してください。

*   **`src/python/pyproject.toml` (`dependencies`)**: ツールの実行に必要なライブラリ。
*   **ルートディレクトリの `pyproject.toml` (`dependency-groups`)**: `pytest` や `ruff` など、開発・テストに必要なツール（`dev` グループ）。

**依存更新の運用**: 依存関係の更新 PR は Dependabot（週次、`python-dependencies` グループ）が作成します。マージ後に `git pull` したら、`uv sync --locked`（または `make sync`）を一度実行してください。`bin/hwda` / `bin/hwsa` は `uv run --no-sync` 起動のため、未同期のまま実行すると実行時エラーになります（スクリプトが起動時に未同期を検出し警告します）。新規依存の追加は `uv add --project src/python <pkg>` で行ってください。

> **AI エージェントへのヒント**: `uv sync` を実行するだけで、開発ツールを含む全ての依存関係がインストールされます。

### ドキュメントの同期更新
Python スクリプトの仕様（引数、動作、出力等）を変更した場合は、必要に応じて以下のドキュメントをセットで更新してください。

1.  **.agents/skills/*/SKILL.md**: 各スキルの実行例や説明。
2.  **AGENTS.md**: このファイル自体（スキル一覧や操作ガイド）。
3.  **README.md**: プロジェクト全体の機能概要。

エージェントは「コードを直して終わり」ではなく、常にこれらドキュメントとの整合性を保つ責任があります。
