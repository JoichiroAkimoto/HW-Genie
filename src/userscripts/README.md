# userscripts

HW-Genie のブラウザ用ユーザースクリプト（Tampermonkey 等）の開発ディレクトリ。

## セットアップ

```bash
bun install
```

## ビルド

```bash
bash build.sh
# または
npm run build
```

`dist/hw-genie-auth-capture.user.js` が生成されます。`--inject-url` を渡すと
`@downloadURL` / `@updateURL` にリリース URL を注入します（CI 用）。

## テスト

```bash
npm test
# または
bun test tests/
```

`tests/xhr-interceptor.test.js` が XHR インターセプタ（本番コード
`xhr-interceptor.ts` を直接 import）の挙動を検証します。他のユーザースクリプト
（例: HW Goodwin）と共存できること、再オープンでラッパーが積み重ならないこと
などを回帰テストとして固定しています。

## 構成

- `index.ts` — ユーザースクリプト本体（メタデータ + 起動処理）
- `xhr-interceptor.ts` — XHR インターセプタ（共有モジュール。テストからも import）
- `tests/` — 回帰テスト
