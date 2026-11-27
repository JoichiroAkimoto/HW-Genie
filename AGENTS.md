# AGENTS.md 🧞‍♂️

Hero Wars API 自動化ツールキット。**このファイルは入口とハードルールだけ**を持ち、機能仕様は README・各 `SKILL.md`・`docs/api` に置きます。重複を増やさず、変更時はリンク先を直してください。

## ドキュメントの役割

- `docs/api/` は **ゲーム側（Hero Wars）の RPC 仕様**。このツールの CLI 仕様ではない。
- **HW-Genie のプロダクト仕様**（コマンド・引数・挙動・運用手順）は [README.md](README.md) と `.agents/skills/*/SKILL.md`。
- **HW-Genie の DB** は [docs/db/schema.md](docs/db/schema.md)。

| 知りたいこと | 参照 |
|---|---|
| ゲームの RPC 仕様・レスポンス例（外部仕様） | [docs/api/INDEX.md](docs/api/INDEX.md) |
| HW-Genie の CLI・機能・運用（プロダクト仕様） | [README.md](README.md), `.agents/skills/*/SKILL.md` |
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
uv run hw-genie auth --list        # 登録名の確認（最新ステータスは --fresh）
uv run hw-genie log ls
uv run hw-genie db-check
```

- アカウントは実名（プレイヤー名）。`-a` 省略時は登録1件なら自動、複数なら指定が必要。`multi` は未指定で全アカウント。
- 破壊的・資源消費操作はまず `--dry-run`。`--debug` はサブコマンドの後ろに置く（例: `hw-genie daily --debug`）。
- `multi --dry-run` の対応モードは README の multi 節を参照（`daily` / `full` / `asgard-shop` / `toe` は非対応）。
- コマンドの引数・挙動は README と各スキルに書く（このファイルには重複して書かない）。

## スキル

- スキルは `.agents/skills/*/SKILL.md` の frontmatter（`name` / `description`）で discover する。ここに一覧を複製しない（メタデータを正とする）。
- 依頼に該当するスキルがあれば、実行前にその `SKILL.md` を読む。実行例・引数・注意点はすべてそこにある。
- 一覧確認: `grep -H '^description:' .agents/skills/*/SKILL.md`

## 不変条件（必ず守る）

- **署名**: 送信は `HWClient.call` 経由。署名は `src/python/hw_genie/core/signing.py` が送信バイト列から毎回計算する。キャプチャした `X-Auth-Signature` の再利用や `json=` での送信は `Invalid signature` になる（詳細は同ファイルの docstring）。
- **認証切れ**: ユーザーに新しい `curl` の提供を依頼するか、`auth-server` を案内する（[hero-wars-auth](.agents/skills/hero-wars-auth/SKILL.md)）。
- **レートリミット**: `HWClient.sleep()` に従う。大量の並列実行を避ける。
- **結果表示**: `core/client.py` の `ResponseStatus` / `Emojis` を使う。
- **DB / worktree**: クラウド Turso が正、`data/hw_genie.db` はローカルレプリカ。`.env` は未追跡。worktree では main worktree の `.env` にシンボリックリンクを張る（`ln -s /path/to/main/.env .env`。解除は `unlink .env`、`rm` は main の `.env` を誤削除し得るので禁止）。独立した設定が必要なら symlink ではなく `cp` を使う（`core/database.py` が `.env` 記載キーで `os.environ` を上書きするため、記載キーのシェル上書きは効かない）。確認は [db-inspect](.agents/skills/db-inspect/SKILL.md)、スキーマは [docs/db/schema.md](docs/db/schema.md)。
- **ブランド資産**: マークは `docs/assets/hw-genie-icon.grid.txt`、文字は `docs/assets/hw-genie-wordmark.png` が正。配布 PNG は `.github/scripts/render_brand_assets.py` で生成し、画素を手編集しない（`--check` が drift を検出）。詳細は [src/userscripts/README.md](src/userscripts/README.md)。
- **実名禁止**: コード・コメント・ドキュメント・テスト・コミットメッセージに実在のプレイヤー名／アカウント名を書かない。例は `Alice` / `Bob` / `Carol` / `Dave`。

## 複数アカウント・複数タスク

- 1アカウント内の複数タスクはメインセッションで逐次実行する（サブエージェント不要）。
- 複数アカウントはアカウント単位でサブエージェントを並列起動する。
- 長時間コマンドは Bash の `timeout` を明示する（例: 600 秒 = `600000`）。
- 同一アカウントの逐次タスクを 1 つの `bash` に `&&` で詰めない（個別呼び出しにする）。
- worktree と main を並行確認する場合、実行先は `session_move` または `workdir` 明示で分ける。pytest / ruff は worktree ごとに同時実行してよい（テストは一時 DB とモックで本番 DB・ネットワークに触れない）。auth-server はポート 8765 を 1 つだけ起動する（Userscript が固定）。同じアカウントへの CLI 実行（`--dry-run` 含む）は 1 箇所で逐次にし、書き込みを伴う操作と Turso 同期も 1 箇所だけで実行する。

## 開発ルール

- **必須検証**: `uv run --locked ruff check . --fix` と `uv run --locked pytest`。新機能・修正には `src/python/tests` のテスト追加/更新も行う。
- **依存関係**: Source of Truth は実行依存が `src/python/pyproject.toml` の `dependencies`、開発依存がルート `pyproject.toml` の `dependency-groups`（`dev`）。追加は `uv add --project src/python <pkg>`。更新運用（Dependabot / auto-merge / `uv sync --locked`）は README の「開発環境 > 依存関係の更新」を参照。
- **ドキュメント同期**: 仕様変更時は `.agents/skills/*/SKILL.md`、`README.md`、必要なら `AGENTS.md` / `docs/api` を同時更新する。DB 変更なら `docs/db/schema.md`、リリースなら `RELEASE_PROCESS.md`。
- **コメントは現行仕様のみ**: 新規・更新するコード／ワークフロー／ドキュメントのコメントには変更履歴（「以前は…」「〜をやめた」）を書かない。履歴は PR 本文・コミットメッセージに書く。互換性の説明も「今どう扱うか」という現行の挙動として書く。設定値は情報源を一箇所に集約し、コメントに重複して書かない。既存コメントの一括書き換えはPRの目的範囲でのみ行う。
- **実名禁止**: 上記の不変条件を参照。既存の混入を見つけたら同じ PR で置換する。
