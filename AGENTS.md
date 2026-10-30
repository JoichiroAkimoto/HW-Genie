# AGENTS.md 🧞‍♂️

このファイルは、AI エージェントが **HW-Genie** リポジトリ内のツールやスキルを効果的に使用するためのガイドです。詳細仕様は各リンク先を参照してください。

## リポジトリ概要
Hero Wars の API 自動化ツールキットです。Python CLI (`hw-genie`) を通じてレイドやショッピング等の操作を行います。

## エージェント向け操作ガイド

### 1. スキルの活用
`.agents/skills/` フォルダ内には、エージェントが各機能を実行するための定義（`SKILL.md`）が格納されています。各スキルの実行例・詳細は該当ファイルを参照してください。

*   **daily-raid**: レイド＋ショッピングの連続実行（アイテムのスタミナ限界収集対応）。
*   **hero-raid**: 指定ミッションのヒーローレイド。
*   **hero-shopping**: ヒーローソウルとソウルショップ・タウンショップ（slot 2,4,5,6）の一括購入。
*   **asgard-shop**: Asgard（ギルドレイド）の Realm Traveler ショップ（Osh / Maestro 週）で Valor Emblem を使ったバフとゴールドバフの自動購入。
*   **seers-game**: Seer's Game（開始→カード4択×4→終了）を Seer's Coin 枯渇まで自動周回（`--dry-run` / `--pick` / `--max-games`、`multi seers-game` 対応）。
*   **arena**: Arena の状態・トップ50ランキング確認、名前付きチームプリセット管理、自分より上位の指定プレイヤーへの攻撃（`--rank` / `--user-id`、`--dry-run`、`--candidate-retries`、`--team` / `--heroes` / `--pet` / `--banner` / `--favor`）。上位のみ対象で、通常攻撃は最新の `arenaFindEnemies` 候補にいる相手だけが受理される（13位という固定上限はない）。手動検証用に `arena find-enemies`（候補を1回取得して保存）、`arena snapshot`（保存候補と現在順位の比較）、`arena attack --from-snapshot`（候補を更新せず攻撃、`--force` は `--from-snapshot` 併用時のみ range false を許可）も利用可能。`multi arena` 非対応。詳細は `.agents/skills/arena/SKILL.md`。
*   **consumable**: 所持品（inventory）の在庫確認と、登録済み consumable の一括全消費（`--dry-run` で予行確認。1000 上限アイテムは分割消費、マトリョーシカ系は残りが無くなるまでラウンドを自動繰り返し）。
*   **guild-chat**: ギルドチャット（`chatGetAll` / `chatType=clan`）の履歴取得・表形式と要約表示（`--type` / `--count` / `--last-id` / `--raw` / `--json`）。
*   **hero-wars-auth**: セッション管理・ユーザー情報取得（curl コマンドで認証更新）。
*   **titan-arena**: Titan Arena (ToE) の status / attack / run（`--rival` / `--titans` 省略時は `titanArenaGetStatus` / `teamGetAll.titan_arena` から自動解決、閾値 250。新 Tier は Raid を最初に実行し、レイドが検証可能な結果を返せない場合は個別バトルへフォールバック。負けは既定で毎回バンクし（attackScore はバトルスコアの max 保持なので劣る負けは無害）、勝ちのないパスでもバンクでスコアが伸びている間はパスを継続する。`--no-end-on-loss` で「ベスト更新時のみ」バンクの高速掃引、`--no-bank-best-loss` は改善バンクを無効化（+1 の best-loss fallback は廃止）。詳細は `docs/api/GUILD_API.md#titan-arena-toe`）。
*   **item-raid**: 特定アイテムの繰り返し収集。
*   **quest-status**: クエスト状態の取得・表示と自動完了（`--execute`。デイリー／ギルドクエストに加え、`guild_glyph_defaults.enabled=true` ならギルド活動のグリフ強化 `heroEnchantRune` も実行）。
*   **db-inspect**: データベース（Turso クラウド / ローカルレプリカ）の確認。

### 2. コマンドラインツールの使用法
すべての操作はルートディレクトリから `uv run hw-genie` を通じて行われます。実行前にルートディレクトリにいることを確認してください（`uv run` が依存関係の解決と仮想環境構築を自動で行います）。

*   **ミッションレイド**: `uv run hw-genie raid hero <id1> <id2> --times 3`
*   **アイテムレイド**: `uv run hw-genie raid item --curl '...' --iterations N`（`--times` は `--iterations` のエイリアス。反復回数 = 1 リクエストあたり `times:10` × N。デフォルトはスタミナ限界まで）
*   **ショップ購入**: `uv run hw-genie shop`
*   **Asgard ショップ購入**: `uv run hw-genie asgard-shop`（Osh / Maestro 週を自動判定。`--dry-run` で計画表示のみ、`--gold` / `--no-gold` でゴールドバフ購入を常時 on / off（デフォルトは週依存: Osh 週 off / Maestro 週 on）。詳細は `.agents/skills/asgard-shop/SKILL.md`）
*   **Seer's Game**: `uv run hw-genie seers-game`（開始→カード4択×4→終了を Seer's Coin 枯渇まで自動周回。`--dry-run` で状態・残高表示のみ、`--pick N` で毎ラウンド引くカード指定（デフォルト 2、live の `event.size` に対して検証）、`--max-games N` で打ち切り。`multi seers-game` で全アカウント一括（`--dry-run` 可）。詳細は `.agents/skills/seers-game/SKILL.md`）
*   **Arena**: `uv run hw-genie arena status -a <account>` / `arena top -a <account> [--limit N] [--raw]` / `arena find-enemies -a <account> [--raw]` / `arena snapshot -a <account>` / `arena plan -a <account> (--rank N | --user-id ID) [--min-samples N] [--min-win-rate R] [--limit N] [--candidate-retries N] [--game-version V] [--json]` / `arena team set <name> -a <account> (--from-saved | --heroes 5個) [--pet] [--banner] [--favor HERO=PATRON]` / `arena team list|remove` / `arena attack -a <account> (--rank N | --user-id ID) [--team NAME] [--heroes 5個] [--pet] [--banner] [--favor ...] [--dry-run] [--candidate-retries N] [--from-snapshot] [--force]`（**自分より上位のみ**攻撃。実攻撃は最新の `arenaFindEnemies` 候補にいる相手だけが受理され、候補外は `NotAvailable`。`--candidate-retries`（既定10）で候補リストを更新しながら対象の登場を待てる。`--from-snapshot` は保存済み候補に `arenaFindEnemies` を呼ばず攻撃し、`--force` は `--from-snapshot` 併用時のみ有効で range false は `--force` がない限り停止。`--dry-run` も候補確認と `arenaCheckTargetRange` まで実行し `arenaAttack` は送らない。`topGet` はトップ50のみ。`arena plan` は取り込み済みコーパス（`battle import`）から防衛編成に対する攻撃編成の勝率を返す読み取り専用コマンド（同一防衛者→同一編成の順、Wilson 下限で順位付け、`--min-samples` / `--min-win-rate` でゲート、`--game-version` でバージョン絞り込み）。詳細は `.agents/skills/arena/SKILL.md` / `docs/api/ARENA_API.md`）
*   **Battle Log Corpus**: `uv run hw-genie battle import -a <account> [--type arena|grand|titan_arena] [--limit N] [--offset N] [--dry-run] [--json]`（`toe` は `titan_arena` のエイリアス）で `battleGetByType` から完全な戦闘リプレイ（攻撃/防衛の全編成・ステータス、`seed`、`serverVersion`、勝敗）を取り込み、`uv run hw-genie battle stats [-a <account>] [--type ...] [--defender-id ID] [--game-version V] [--by defender|matchup] [--min-samples N] [--limit N] [--json]` で編成単位の勝率を集計する（`-a` は取り込み元ではなく**関与**ベース: そのアカウントの `player_id` が攻撃側/防衛側のどちらかにある行）。`lineup_key` はヒーロー順＋ペット＋バナー（パワー変化を無視）、`config_hash` はステータス込みの完全一致用。重複は `(source, source_record_id)`（グローバル、先着優先）で自動スキップ。**勝率は攻撃側視点**（防衛側から取り込んだリプレイでも攻撃側の勝敗として記録される）なので、`-a Bob` は「Bob が関与した戦闘」を意味し、Bob 自身の勝率ではない。スキーマは `docs/db/schema.md`、API は `docs/api/ARENA_API.md` の `battleGetByType` を参照。さらに `uv run hw-genie battle simulate -a <account> [--type arena|grand|titan_arena] [--limit N] [--offset N] [--battle-id ID] [--config-getter NAME] [--recycle-every N] [--seeds N] [--dry-run] [--json]`（`--type` の既定は `titan_arena`。`toe` はエイリアス。`--seeds N` は観測 seed を含む合計 N 回（観測分＋N-1 個の決定的追加 seed）で再シミュレーションし、統計的勝率を `sweep_*` として返す。追加 seed は `source='sim'` の別行として保存され stats / plan から除外） で取得済みリプレイを常駐ヘッドレス Oracle で再シミュレーションし、`battle_comparisons` に一致/不一致を記録する（`source='sim'` 行は stats / plan から除外）。未取り込みのリプレイは比較の FK 用に `source='api_import'` として自動取り込みされる。`battle config-getters` で `BattleConfigStorage` の getter 一覧を確認できる（Arena の getter は未検証。ToE は `get_titanPvpManual` が既知）。HWA の `*.json.gz` は `uv run hw-genie battle import-hwa -a <account> --source <path> [--types arena,grand] [--days N|--since YYYY-MM-DD] [--offset N] [--limit N] [--batch-size N] [--dry-run] [--no-sync] [--json]` でストリーミング取り込み（既定は直近90日、`--days 0` で全期間。`arena_replays` / `ga_replays` の Bo3 を展開、id のない GA サブバトルは `{series_key}-{index}-<startTime>-<seed>-<order>` のコンテンツ依存 id を付与、`userId`/`typeId` から登録アカウントへ自動紐付け、観測ソース間は replay id で重複排除し、HWA の partial 行は後からの**完全な** API 取り込みでのみ同一行を完全構成へ upgrade（`account_id` / `viewer_role` は保持））。`--json --no-sync` は stdout を JSON のみに保ち、同期失敗時は元の例外を隠さず `replica sync failed; run \`hw-genie sync\`` を stderr に出す。`uv run hw-genie battle watch -a <account> [--types arena,grand] [--interval N] [--limit N] [--offset N] [--once] [--json]` は `battleGetByType` を定期ポーリングして新規分を自動取り込みする。`uv run hw-genie battle prune [--days N|--before YYYY-MM-DD] [--dry-run] [--json]` は指定日より古い battle 行と comparison を削除する（既定90日。`team_configs` は残す）。
*   **デイリールーチン**: `uv run hw-genie daily`（`--iterations N` でアイテムレイドの反復回数を指定可能 = 1 リクエストあたり `times:10` × N。ヒーローレイドは固定 3 回、デフォルトはスタミナ限界まで）
*   **クエスト状態・自動完了**: `uv run hw-genie quests`（実行可否は `quest_defaults` / `quest_guild_defaults` / `guild_glyph_defaults` の `enabled` フラグでアカウントごとに制御。`--set-default glyph ...` でギルド活動のグリフ強化（`heroEnchantRune`）を設定。詳細は `.agents/skills/quest-status/SKILL.md`）
*   **在庫確認**: `uv run hw-genie inventory`（consumable 中心の所持品一覧。`--all` で全カテゴリ、`--min N` でフィルタ、`--raw` で生 JSON）
*   **consumable 一括消費**: `uv run hw-genie consumable run`（レジストリ `CONSUMABLE_USE_TARGETS` 登録済みアイテムを在庫全量消費。1000 上限アイテムは分割、マトリョーシカ系は残りが無くなるまで自動繰り返し。`<libId>` 位置引数で指定、未登録アイテムは `--method` でメソッド指定、`--dry-run` で予行。詳細は `.agents/skills/consumable/SKILL.md`）
*   **ギルドチャット**: `uv run hw-genie chat`（`chatGetAll` / `chatType=clan` の履歴を表形式と要約で表示。`--type` / `--count` / `--last-id` / `--raw` / `--json`。詳細は `.agents/skills/guild-chat/SKILL.md`）
*   **Titan Arena**: `uv run hw-genie toe status -a <account>` / `toe attack -a <account> [--rival ID] [--titans 5個] [--dry-run]`（省略時は `titanArenaGetStatus` / `teamGetAll.titan_arena` から自動解決、閾値 250）/ `toe run -a <account> [--titans 5個] [--threshold 250]`（詳細は `docs/api/GUILD_API.md#titan-arena-toe`）
*   **アカウント指定**: アカウントは実名（プレイヤー名）で保存されます。`-a`/`--account` 未指定時は、登録が 1 件なら自動選択、複数件なら指定を要求します。`multi` は対象未指定時は全アカウント実行です。
*   **全アカウント一括**: `uv run hw-genie multi daily`（`full` でレイド＋ショップ＋デイリー、`quests` でクエスト自動完了のみ、`asgard-shop` で Asgard ショップ購入のみ（`--gold` / `--no-gold` でゴールドバフ購入を常時 on / off、デフォルトは週依存: Osh 週 off / Maestro 週 on）、`consumable` で consumable 一括消費のみ、`toe` で Titan Arena Tier 自動攻略のみ（`--engine` / `--seeds`（既定 10） / `--threshold` / `--max-attempts` / `--end-on-loss` / `--no-end-on-loss` / `--no-bank-best-loss` 対応）、`seers-game` で Seer's Game 自動周回のみ（`--pick` / `--max-games` 対応）、`--dry-run` でプラン表示のみ（quests / consumable / seers-game のみ。toe を含む他モードではエラー終了する）。`--parallel N` で同時実行数、`account1 account2 ...` で対象限定（dry-run は逐次実行に強制）、`--iterations N` で `daily`/`full` モードのアイテムレイド反復回数を指定。クエスト／consumable 失敗アカウントがあると exit 1。詳細は README.md の「Docker での実行」セクションを参照。
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

### 実在アカウント名の非混入（重要）
コード・コメント・ドキュメント・スキル・テスト・コミットメッセージのいずれにも、実在のプレイヤー名／アカウント名（登録アカウントの実名など）を書かないでください。実行例やテストデータには `Alice` / `Bob` / `Carol` / `Dave` などの一般的なプレースホルダ名を使ってください。既存の混入を見つけた場合は同PRで置換してください。
