# AGENTS.md 🧞‍♂️

Hero Wars API 自動化ツールキット。**このファイルは入口とハードルールだけ**を持ち、機能仕様は README・各 `SKILL.md`・`docs/api` に置きます。重複を増やさず、変更時はリンク先を直してください。

## まず見る場所

| 目的 | 参照 |
|---|---|
| 機能概要・全コマンド・Docker / 環境変数 | [README.md](README.md) |
| RPC 仕様・レスポンス例 | [docs/api/INDEX.md](docs/api/INDEX.md) |
| DB スキーマ | [docs/db/schema.md](docs/db/schema.md) |
| リリース手順 | [RELEASE_PROCESS.md](RELEASE_PROCESS.md) |
| ブランド資産の運用 | [src/userscripts/README.md](src/userscripts/README.md) |
| 設計メモ・実装計画（新規は .gitignore 対象） | `docs/superpowers/specs/`, `docs/superpowers/plans/` |

## クイックスタート

すべてリポジトリルートで `uv run hw-genie ...`（`uv run` が依存を解決）。

```bash
uv run hw-genie daily
uv run hw-genie multi daily
uv run hw-genie power-tournament --dry-run -a <account>
uv run hw-genie consumable run --dry-run -a <account>
uv run hw-genie auth --list
uv run hw-genie log ls
uv run hw-genie db-check
```

- アカウントは実名（プレイヤー名）。`-a` 省略時は登録1件なら自動、複数なら指定が必要。`multi` は未指定で全アカウント。
- 破壊的・資源消費操作はまず `--dry-run`。`--debug` はサブコマンドの後ろに置く（例: `hw-genie daily --debug`）。
- `multi --dry-run` の対応モードは README の multi 節を参照（`daily` / `full` / `asgard-shop` / `toe` は非対応）。
- コマンドの引数・挙動は README と各スキルに書く（このファイルには重複して書かない）。

## スキル（タスク別ルーティング）

依頼が該当したら、実行前に `.agents/skills/<name>/SKILL.md` を読む。実行例・引数・注意点はすべてそこにある。

| スキル | 用途 |
|---|---|
| [daily-raid](.agents/skills/daily-raid/SKILL.md) | レイド＋ショッピングの日次ルーチン |
| [hero-raid](.agents/skills/hero-raid/SKILL.md) | 指定ミッションのヒーローレイド |
| [item-raid](.agents/skills/item-raid/SKILL.md) | 特定アイテムのスタミナ限界まで収集 |
| [hero-shopping](.agents/skills/hero-shopping/SKILL.md) | ヒーローソウル・ソウルショップ・タウンショップ購入 |
| [asgard-shop](.agents/skills/asgard-shop/SKILL.md) | Asgard ショップのバフ購入（Osh / Maestro 週） |
| [asgard-raid](.agents/skills/asgard-raid/SKILL.md) | Asgard ボス戦チーム実行・週次報酬 |
| [quest-status](.agents/skills/quest-status/SKILL.md) | クエスト状態・自動完了 |
| [element-gift](.agents/skills/element-gift/SKILL.md) | エレメントギフトの MAX 化／リセット |
| [arena](.agents/skills/arena/SKILL.md) | Arena 状態・ランキング・指定攻撃 |
| [challenge](.agents/skills/challenge/SKILL.md) | フレンドリーチャレンジ送信・バトル |
| [guild-chat](.agents/skills/guild-chat/SKILL.md) | ギルド／個人チャット取得 |
| [seers-game](.agents/skills/seers-game/SKILL.md) | Seer's Game 周回 |
| [power-tournament](.agents/skills/power-tournament/SKILL.md) | Power Tournament 表示・報酬受取 |
| [consumable](.agents/skills/consumable/SKILL.md) | 在庫確認・一括消費 |
| [hero-wars-auth](.agents/skills/hero-wars-auth/SKILL.md) | セッション管理・認証更新 |
| [db-inspect](.agents/skills/db-inspect/SKILL.md) | DB の確認・デバッグ |

スキル外の主要 API: Titan Arena (ToE) は [docs/api/GUILD_API.md#titan-arena-toe](docs/api/GUILD_API.md#titan-arena-toe)。

## 不変条件（必ず守る）

- **署名**: 送信は `HWClient.call` 経由。署名は `src/python/hw_genie/core/signing.py` が送信バイト列から毎回計算する。キャプチャした `X-Auth-Signature` の再利用や `json=` での送信は `Invalid signature` になる（詳細は同ファイルの docstring）。
- **認証切れ**: ユーザーに新しい `curl` の提供を依頼するか、`auth-server` を案内する（[hero-wars-auth](.agents/skills/hero-wars-auth/SKILL.md)）。
- **レートリミット**: `HWClient.sleep()` に従う。大量の並列実行を避ける。
- **結果表示**: `core/client.py` の `ResponseStatus` / `Emojis` を使う。
- **DB**: クラウド Turso が正、`data/hw_genie.db` はローカルレプリカ。`.env` は未追跡。worktree では main worktree の `.env` にシンボリックリンクを張る（`ln -s /path/to/main/.env .env`。解除は `unlink .env`、`rm` は main の `.env` を誤削除し得るので禁止）。確認は [db-inspect](.agents/skills/db-inspect/SKILL.md)、スキーマは [docs/db/schema.md](docs/db/schema.md)。
- **ブランド資産**: マークは `docs/assets/hw-genie-icon.grid.txt`、文字は `docs/assets/hw-genie-wordmark.png` が正。配布 PNG は `.github/scripts/render_brand_assets.py` で生成し、画素を手編集しない（`--check` が drift を検出）。詳細は [src/userscripts/README.md](src/userscripts/README.md)。
- **実名禁止**: コード・コメント・ドキュメント・テスト・コミットメッセージに実在のプレイヤー名／アカウント名を書かない。例は `Alice` / `Bob` / `Carol` / `Dave`。

## 複数アカウント・複数タスク

- 1アカウント内の複数タスクはメインセッションで逐次実行する（サブエージェント不要）。
- 複数アカウントはアカウント単位でサブエージェントを並列起動する。
- 長時間コマンドは Bash の `timeout` を明示する（例: 600 秒 = `600000`）。
- 同一アカウントの逐次タスクを 1 つの `bash` に `&&` で詰めない（個別呼び出しにする）。

## 開発ルール

- **必須検証**: `uv run --locked ruff check . --fix` と `uv run --locked pytest`。新機能・修正には `src/python/tests` のテスト追加/更新も行う。
- **依存関係**: Source of Truth は実行依存が `src/python/pyproject.toml` の `dependencies`、開発依存がルート `pyproject.toml` の `dependency-groups`（`dev`）。追加は `uv add --project src/python <pkg>`。更新運用（Dependabot / auto-merge / `uv sync --locked`）は README の「開発環境 > 依存関係の更新」を参照。
- **ドキュメント同期**: 仕様変更時は `.agents/skills/*/SKILL.md`、`README.md`、必要なら `AGENTS.md` / `docs/api` を同時更新する。DB 変更なら `docs/db/schema.md`、リリースなら `RELEASE_PROCESS.md`。
- **実名禁止**: 上記の不変条件を参照。既存の混入を見つけたら同じ PR で置換する。
