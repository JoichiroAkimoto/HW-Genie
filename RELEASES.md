# Release Notes

## ユーザースクリプト (Tampermonkey)

### リリース方法

```bash
# 1. バージョンを更新（src/userscripts/index.ts の @version を編集）
# 2. タグを作成してプッシュ
git tag v1.0.x
git push --tags
```

### 仕組み

- `git push --tags` で `.github/workflows/release.yml` がトリガー
- `index.ts` の `@version` からバージョンを抽出してビルド
- GitHub Release に `.user.js` をアセットとして自動アップロード
- Tampermonkey が `@updateURL` / `@downloadURL` に基づいて自動更新を検知

### バージョンルール

- セマンティックバージョニングを採用（例: v1.0.0, v1.0.1, v1.1.0）
- メジャーバージョン: 大きな変更や破壊的変更
- マイナーバージョン: 新機能追加（後方互換）
- パッチバージョン: バグ修正