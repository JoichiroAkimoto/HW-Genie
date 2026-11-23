# Release Process

## ユーザースクリプト (Tampermonkey)

### 通常のリリリース手順

1. `src/userscripts/index.ts` の `@version` を更新（例: `1.0.6`）
2. ローカルでビルド・テストの検証を実施:
   ```bash
   cd src/userscripts
   bun install --frozen-lockfile
   bun test tests/
   VERSION=$(bash ./release-metadata.sh version ./index.ts)
bash ./build.sh \
      --inject-download-url "https://github.com/example/example/releases/latest/download/hw-genie-auth-capture.user.js" \
      --inject-update-url "https://github.com/example/example/releases/latest/download/hw-genie-auth-capture.user.js"
   bash ./release-metadata.sh validate-artifact ./dist/hw-genie-auth-capture.user.js "$VERSION" "example/example"
   ```
3. 変更をコミットして `main` へマージ（PR経由）
4. 対象コミットに対して指定のバージョンタグのみを作成し、プッシュ:
   ```bash
   git tag -a v1.0.6 -m "Release userscript v1.0.6"
   git push origin v1.0.6
   ```
   > **注意:** `git push --tags` は意図しないタグを一括送信してしまうため使用しないでください。必ず対象タグ名のみをプッシュしてください。タグ名（`vX.Y.Z`）は `index.ts` の `@version`（`X.Y.Z`）と完全に一致する必要があります。
   >
   > **デプロイ順序:** タグは GitHub Pages のデプロイ確定後にプッシュしてください。`main` にマージ済みの Pages 配信資産（`docs/` 配下）がまだデプロイされていない状態でタグを流すと、公開済み配布物から参照される URL がその間 404 になります。Pages の遅れそのものはリリースをブロックしませんが、`release.yml` が公開後に到達性を確認して警告します。

### リリース復旧手順 (Recovery Mode)

GitHub Release の作成漏れ、アセットの欠落・破損などのトラブルが発生した場合、タグを削除・force-push することなく `workflow_dispatch` で復旧できます。

#### 実行例: v1.0.5 の復旧（現時点の最新安定版が v1.0.4 の場合）

```bash
gh workflow run release.yml \
  -f tag=v1.0.5 \
  -f release_mode=recovery \
  -f expected_latest_tag=v1.0.4
```

- `release_mode=recovery` では、対象の固定 URL 用アセットを再生成・検証します。
- `expected_latest_tag` を指定することで、復旧処理によって過去のバージョンが誤って最新安定版 (`releases/latest`) に指し変わるのを防ぎます。

#### 復旧後に latest へ昇格させる場合

recovery モードは `releases/latest` を変更しません。復旧したバージョンが現在の最新安定版として公開されるべき場合は、復旧完了後に手動で昇格させてください。

```bash
gh release edit v1.0.5 --latest
```

> **補足:** `releases/latest` はリポジトリ全体で1つだけです。上記コマンドで v1.0.5 が latest に昇格すると、それ以前のバージョンは latest でなくなります。

### 仕組み

- タグのプッシュ (`v*`) により `.github/workflows/release.yml` が `normal` モードで起動
- タグのプッシュにより `.github/workflows/docker-image.yml` も起動し、`ghcr.io/joichiroakimoto/hw-genie:vX.Y.Z` として Docker イメージを公開
- 事前検証 (`release-metadata.sh`) で `@version` とタグ名の一致、およびバージョン昇順チェックを実施
- GitHub Release に `.user.js` をアセットとしてアップロードし、ネットワーク公開状態を検証
- Tampermonkey が `@updateURL` に基づいて自動更新を検知

### 自動更新の仕組み

- `@downloadURL` / `@updateURL` はどちらも常に最新リリースを指す URL（`releases/latest/download/...`）
- Tampermonkey は `@updateURL` を定期的に確認し、新しいバージョンがあれば自動更新します。
- 両方を latest に統一する理由: 更新適用時に `@downloadURL` から再取得するスクリプトマネージャーがあるため、`@downloadURL` も最新リリースを指す必要があります。
- リリースは `draft=false` かつ `prerelease=false` の安定版として公開されます。

### バージョンルール

- セマンティックバージョニングを採用（例: v1.0.0, v1.0.1, v1.1.0）
- メジャーバージョン: 大きな変更や破壊的変更
- マイナーバージョン: 新機能追加（後方互換）
- パッチバージョン: バグ修正
- アセット（`@icon` が指す画像等）の差し替えも、既存インストールに届けるにはパッチバージョンのバンプとタグリリースが必要（根拠: `src/userscripts/README.md`「反映タイミング」）