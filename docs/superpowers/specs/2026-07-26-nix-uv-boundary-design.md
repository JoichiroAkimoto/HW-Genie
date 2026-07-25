# Nix と uv の責務分離 設計

## 目的

Nix 開発シェルへの入場時に Python 依存関係の同期や仮想環境の有効化を行わず、Nix はツールチェーン、uv はプロジェクト依存関係をそれぞれ明示的に管理する。

## 背景

現在の `flake.nix` の `shellHook` は `uv sync --frozen || uv sync` と `.venv/bin/activate` を実行する。このため、`direnv` または `nix develop` でディレクトリへ入るだけで `.venv` への書込みやネットワークアクセスが発生し、Nix 利用時の手順と uv 利用時の手順が README 上でも混在している。

## 設計

### 責務の境界

- Nix (`flake.nix`) は Python 3.13、uv、turso-cli、SQLite のホストツールだけを PATH に提供する。
- uv は `uv.lock` に従い、アプリケーション、テスト、lint 用の Python 依存関係を `.venv` に管理する。
- `flake.nix` に `shellHook` は置かない。Nix シェルへの入場はファイル変更・依存同期・仮想環境 activation を起こさない。
- `ruff` と `pytest` は uv.lock の版を唯一の検証対象とするため、Nix devShell の packages から外す。

### コマンド UX

Nix 利用時の標準コマンドは、lockfile を暗黙更新しない `uv run --locked` とする。

```bash
uv sync --locked                 # 初回、および lockfile 更新後に明示実行
uv run --locked hw-genie --help
uv run --locked pytest
uv run --locked ruff check .
```

`uv run --locked` は環境の同期を許可するが、lockfile が `pyproject.toml` と不整合なら失敗する。これにより、依存解決の変更は `uv lock` を明示的に行う必要がある。

`hw-genie`、`pytest`、`ruff` の直接実行は Nix 利用時のサポート対象から外す。これらが現在直接実行できるのは shellHook が `.venv` を activate するためであり、その暗黙的な挙動を廃止する。

`PATH_add bin` は継続する。したがって direnv を有効にしたセッションでは `hwda` と `hwsa` を直接実行できる。`nix develop` を単独で使う場合は PATH を変更しないため、`bin/hwda` と `bin/hwsa` を明示的に実行する。両ラッパーは既に内部で `uv run hw-genie` を使っているため、`uv run --locked hw-genie` に揃える。

### direnv

`copy.envrc` は `.env` の `dotenv` 読込みと `PATH_add bin` を維持する。

- Nix がある場合: `use flake` のみを実行する。
- Nix がない場合: 既存どおり、存在する `.venv/bin/activate` のみを source する。

このフォールバックは非 Nix 開発者の既存 UX を維持し、Nix 利用時の `.venv` activation とは混在しない。

## ドキュメント範囲

README の Nix セクションを次の内容に変更する。

- 「`.venv` は不要」「ディレクトリ進入時に `uv sync`・activate が走る」という記述を削除する。
- `direnv allow` の後、`uv sync --locked` を明示実行する手順を記載する。
- Python CLI、テスト、lint は `uv run --locked` で実行する例を記載する。
- direnv 利用時は `hwda` と `hwsa`、`nix develop` 単独では `bin/hwda` と `bin/hwsa` を使うこと、および各ラッパーが uv 経由で CLI を実行することを記載する。

AGENTS.md と `.agents/skills/` はすでに `uv run hw-genie` を標準としている。`AGENTS.md` の lint/test コマンドと `bin/hwda`・`bin/hwsa` の実装を `--locked` へ揃える。各スキルに `--locked` を一律追加するかは、ユーザー向けの常用手順を過度に冗長化しないため今回は行わない。

## 受入条件

1. `nix develop` と `direnv` の読み込みが `.venv` を作成・変更せず、依存ネットワークアクセスも行わない。
2. Nix シェル内で `uv sync --locked` 後、`uv run --locked hw-genie --help`、`uv run --locked pytest`、`uv run --locked ruff check .` が動作する。
3. direnv を有効にした Nix セッションでは `hwda` と `hwsa`、`nix develop` 単独の Nix シェルでは `bin/hwda` と `bin/hwsa` が実行でき、いずれのラッパーも内部では `uv run --locked hw-genie` を使用する。
4. Docker の実行経路は変更しない。
5. README の Nix 手順と実際のコマンドが一致する。

## 検証

- Nix: `nix flake check` と `nix develop --command` による PATH／副作用確認。
- uv: 新規または隔離した `.venv` から `uv sync --locked` と各標準コマンドを実行。
- 既存品質ゲート: `uv run --locked ruff check . --fix` と `uv run --locked pytest`。
- ラッパー: `bin/hwda --help` と `bin/hwsa --help` を、実 API 操作を起こさない条件で実行できるよう必要に応じて検証する。
