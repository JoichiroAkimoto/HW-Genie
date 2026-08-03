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
抽出を自動検証します。

### リリース用 URL 注入

`--inject-download-url URL` / `--inject-update-url URL` で `@downloadURL` /
`@updateURL` を個別に設定します。**両方とも**常に最新リリースを指す
`releases/latest/download/...` を指定してください。Tampermonkey は更新時に
新しいスクリプトの `@downloadURL` を使いますが、Violetmonkey はインストール済み
スクリプトの `@downloadURL` から再取得するため、`@downloadURL` をバージョン固定に
すると自動更新が同じバージョンに留まる問題があったためです。

```bash
VERSION=$(bash ./release-metadata.sh version ./index.ts)
bash ./build.sh \
  --inject-download-url "https://github.com/JoichiroAkimoto/HW-Genie/releases/latest/download/hw-genie-auth-capture.user.js" \
  --inject-update-url "https://github.com/JoichiroAkimoto/HW-Genie/releases/latest/download/hw-genie-auth-capture.user.js"
```

> **注意:** `--inject-download-url` や `--inject-update-url` を指定しないローカルビルドでは、`__DOWNLOAD_URL__` や `__UPDATE_URL__` のプレースホルダーが残るため、配布物として使用することはできません。

## テスト

```bash
npm test
# または
bun test tests/
```

- `tests/release-metadata.test.js`: バージョン抽出、タグ照合、生成された成果物のメタデータ（`@version`, `@downloadURL`, `@updateURL`, プレースホルダー残存）の検証契約をテストします。
- `tests/xhr-interceptor.test.js`: XHR インターセプタの挙動を検証します。他のユーザースクリプト（例: HW Goodwin）と共存できること、同一 XHR の再利用でラッパーが積み重ならないことなどを回帰テストとして固定しています。
- `tests/session.test.js`: セッション送信の状態機械を検証します。

## 既知の制限

- ゲームが fetch のみで API を呼ぶ環境では認証ヘッダーを捕捉できません
  （XHR のみ対応。v1.0.2 と同じ挙動）。
- API URL の判定はページ URL を基準にしたホスト+パス一致です。ゲームが
  `heroes-wb.nextersglobal.com` 以外のホストや相対パスを API 呼び出しに
  使う場合は捕捉されません。
- dedupe は `x-auth-signature` を除外したシリアライズで判定するため、署名の
  ローテーションでは再送されません。ページロード後に一度だけ送信され、セッション
  値が変わらない限り再送されません（送信ペイロードには署名を含めるため、サーバー
  側の必須ヘッダー検証は維持されます）。
- 再送が発生するのは実セッション値の変化時のみです: session-id / user-id の
  変化（再ログイン）は、バックオフが残っている間のみ同一値の 2 連続観測で
  ガードされ、それ以外は次のポーリングで即送信されます。token 等のその他
  キーの変化は dedupe 不一致として最小 2 秒間隔で再送されます。未送信の
  セッション（初回または値変化後）が残っている場合のみ、サーバー障害時は指数
  バックオフ（最大 30 秒）でリトライして復旧後に再送されます（送信成功済みで
  値が不変の場合は障害中も復旧後も再送されません）。
- 認証サーバーはアップロード時点のゲーム API 検証のみを行い、以降の保存済み
  セッションの署名の鮮度（有効期限）は保証しません。ゲーム側でセッションが
  失効した場合は、curl での再認証または auth-server での再キャプチャが必要です
  （v1.0.2 と同じ前提）。
- 実機確認（HW Goodwin 併用時）:
  1. HW-Genie のみ有効 → 認証成功ログが出る
  2. HW Goodwin のみ有効 → Goodwin の UI が表示される
  3. 両方有効 → 双方が機能する（Goodwin の UI 表示 + HW-Genie の認証送信）

## 構成

- `index.ts` — ユーザースクリプト本体（メタデータ + 起動処理）
- `release-metadata.sh` — メタデータ抽出・タグ検証・成果物バリデーション用 CLI
- `xhr-interceptor.ts` — XHR インターセプタ（共有モジュール。テストからも import）
- `session.ts` — セッション送信の状態機械（共有モジュール。テストからも import）
- `tests/` — 回帰テスト・メタデータテスト
