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
     --inject-download-url "https://github.com/example/example/releases/download/v${VERSION}/hw-genie-auth-capture.user.js" \
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

### 仕組み

- タグのプッシュ (`v*`) により `.github/workflows/release.yml` が `normal` モードで起動
- 事前検証 (`release-metadata.sh`) で `@version` とタグ名の一致、およびバージョン昇順チェックを実施
- GitHub Release に `.user.js` をアセットとしてアップロードし、ネットワーク公開状態を検証
- Tampermonkey が `@updateURL` に基づいて自動更新を検知

### 自動更新の仕組み

- `@downloadURL` はバージョン固定の URL（`releases/download/vX.Y.Z/...`）
- `@updateURL` は常に最新リリースを指す URL（`releases/latest/download/...`）
- Tampermonkey は `@updateURL` を定期的に確認し、新しいバージョンがあれば自動更新します。
- リリースは `draft=false` かつ `prerelease=false` の安定版として公開されます。

### 既存ユーザーの移行

- **v1.0.4 以前**にインストールしたユーザーの `@updateURL` はバージョン固定のため、自動更新では新バージョンを検知できません。**手動での再インストールが必要**です。
- 再インストール URL: [https://github.com/JoichiroAkimoto/HW-Genie/releases/latest/download/hw-genie-auth-capture.user.js](https://github.com/JoichiroAkimoto/HW-Genie/releases/latest/download/hw-genie-auth-capture.user.js)
- 再インストール後は `@updateURL` が `releases/latest/download/...` に置き換わり、以降の自動更新が機能します。

### バージョンルール

- セマンティックバージョニングを採用（例: v1.0.0, v1.0.1, v1.1.0）
- メジャーバージョン: 大きな変更や破壊的変更
- マイナーバージョン: 新機能追加（後方互換）
- パッチバージョン: バグ修正