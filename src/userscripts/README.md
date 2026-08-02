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

## 既知の制限

- ゲームが fetch のみで API を呼ぶ環境では認証ヘッダーを捕捉できません
  （XHR のみ対応。v1.0.2 と同じ挙動）。
- API URL の判定はページ URL を基準にしたホスト+パス一致です。ゲームが
  `heroes-wb.nextersglobal.com` 以外のホストや相対パスを API 呼び出しに
  使う場合は捕捉されません。
- 実機確認（HW Goodwin 併用時）:
  1. HW-Genie のみ有効 → 認証成功ログが出る
  2. HW Goodwin のみ有効 → Goodwin の UI が表示される
  3. 両方有効 → 双方が機能する（Goodwin の UI 表示 + HW-Genie の認証送信）
  - 既知の懸念: HW Goodwin が open 内でインスタンス own setRequestHeader を
    毎回張る実装の場合、document-idle 構成では genie が open チェーン最外側に
    なり捕捉が失われる可能性がある（tests の「genie が外側」ケース）。
    現行の HW Goodwin 実装では発生しないことを実機で確認済みであること。

## 構成

- `index.ts` — ユーザースクリプト本体（メタデータ + 起動処理）
- `xhr-interceptor.ts` — XHR インターセプタ（共有モジュール。テストからも import）
- `tests/` — 回帰テスト
