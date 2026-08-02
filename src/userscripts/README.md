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

`dist/hw-genie-auth-capture.user.js` が生成されます。ビルドは `bun build`（IIFE
形式）に加えて `tsc --noEmit` の型チェックを実行し、IIFE ラップとメタデータ
抽出を自動検証します。`--inject-url` を渡すと `@downloadURL` / `@updateURL` に
リリース URL を注入します（CI 用）。

## テスト

```bash
npm test
# または
bun test tests/
```

`tests/xhr-interceptor.test.js` が XHR インターセプタ（本番コード
`xhr-interceptor.ts` を直接 import）の挙動を検証します。他のユーザースクリプト
（例: HW Goodwin）と共存できること、同一 XHR の再利用（open→send→再 open）で
ラッパーが積み重ならないこと、API→非 API→API の遷移で捕捉が正しく解除・
復帰することなどを回帰テストとして固定しています。

## 構成

- `index.ts` — ユーザースクリプト本体（メタデータ + 起動処理）
- `xhr-interceptor.ts` — XHR インターセプタ（共有モジュール。テストからも import）
- `tests/` — 回帰テスト
