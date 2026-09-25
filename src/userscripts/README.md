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
抽出を自動検証します。`--minify` は ToE 番兵ゲートと非互換のため非対応です
（`build.sh` が明示エラーで拒否します）。

### リリース用 URL 注入

`--inject-download-url URL` / `--inject-update-url URL` で `@downloadURL` /
`@updateURL` を個別に設定します。**両方とも**常に最新リリースを指す
`releases/latest/download/...` を指定してください（更新適用時に `@downloadURL`
から再取得するスクリプトマネージャーがあるため）。

```bash
VERSION=$(bash ./release-metadata.sh version ./index.ts)
bash ./build.sh \
  --inject-download-url "https://github.com/JoichiroAkimoto/HW-Genie/releases/latest/download/hw-genie-auth-capture.user.js" \
  --inject-update-url "https://github.com/JoichiroAkimoto/HW-Genie/releases/latest/download/hw-genie-auth-capture.user.js"
```

> **注意:** `--inject-download-url` や `--inject-update-url` を指定しないローカルビルドでは、`__DOWNLOAD_URL__` や `__UPDATE_URL__` のプレースホルダーが残るため、配布物として使用することはできません。

### DEV版ビルド（ToE自動化時のみONにする開発版）

HW Goodwin 等との干渉を避けるため、ToE自動化を行うときだけDEV版を有効化し、
終わったら無効化して通常版に戻す運用を推奨します。

**通常版では ToE 機能は実行されません**（#134）。通常版のバンドルは ToE の
トラップ設置もポーリングも一切行わず、認証キャプチャ専用です（コード自体は
バンドルに残ります）。ToE の実行に
必要なエンジントラップ（`Object.prototype` への設置）は Goodwin の自動攻略と
干渉するため、DEV 版でのみ有効化されます（ビルド時番兵 `TOE_VARIANT`
（`__TOE_VARIANT_NORMAL__`/`__TOE_VARIANT_DEV__`）。`TOE_ENABLED` は派生フラグ。
通常版ビルドに DEV 番兵が混入していないことは `build.sh` が検証します）。

```bash
bash build.sh --dev
# または
npm run build:dev
```

`dist/hw-genie-auth-capture-dev.user.js` が生成されます。DEV版の識別子:

- `@name` が `HW-Genie Auth Capture (Dev)`、`@namespace` が `.../HW-Genie-dev`
  （通常版と並行インストール可能）
- `@version` に `-dev` サフィックス（例: `1.0.7-dev`）
- 先頭行に `// ⚠️ DEV BUILD ...` バナー、コンソールログは `[HW-Genie/ToE Dev]`
  プレフィックス（通常版の `[HW-Genie/ToE]` と混ざらない）
- `@downloadURL` / `@updateURL` は注入不可（`--inject-*` と `--dev` の併用は
  ビルドエラー）。自動更新で通常版を上書きしないための保護です

運用手順: ToE実行前にDEV版をON → 実行後にOFF → 通常版に戻す。
挙動自体は通常版と同一に保つため、DEV版で動いたものは通常版でも動きます。

## テスト

```bash
npm test
# または
bun test tests/
```

- `tests/release-metadata.test.js`: バージョン抽出、タグ照合、生成された成果物のメタデータ（`@version`, `@downloadURL`, `@updateURL`, プレースホルダー残存）の検証契約をテストします。
- `tests/xhr-interceptor.test.js`: XHR インターセプタの挙動を検証します。他のユーザースクリプト（例: HW Goodwin）と共存できること、同一 XHR の再利用でラッパーが積み重ならないことなどを回帰テストとして固定しています。
- `tests/session.test.js`: セッション送信の状態機械を検証します。
- `tests/auth-recovery.test.js`: セッション失効判定と自動リロード可否を検証します。

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
- セッション失効時（HTTP 401 / `error` の name・文字列値が `auth` /
  `InvalidSession`（完全一致・大文字区別）/ 非 JSON ボディの `Invalid signature`
  テキスト）は XHR レスポンス監視（load イベントのみ。error/abort 時は通知
  しない）で検知して `location.reload()` で自動回復します（DOM 監視なし）。
  無限ループ防止のため `RELOAD_COOLDOWN_MS`（5 分）クールダウン・
  `RELOAD_WINDOW_MS`（10 分）間に `MAX_RELOADS_PER_WINDOW`（3 回）までで、
  超過分はログのみ出力してリロードしません。カウンタは sessionStorage に
  保持するためタブスコープです（複数タブ合算ではありません）。
  sessionStorage が使えない環境（プライベートモード等）では自動リロードは
  動作しません（初回から suppress してリロードしない。メモリ代替で
  リロードすることはありません）。
  無効化手順（kill-switch）: ゲームを開いたタブの開発者コンソールで
  `localStorage.setItem("hw-genie-auto-reload-disabled", "1")` を実行すると、
  検知してもリロードせずログのみにします。再有効化は
  `localStorage.removeItem("hw-genie-auto-reload-disabled")` です。
  kill-switch の localStorage は同一オリジンで全タブ共有のため、1 タブでの
  設定が全タブに適用されます。localStorage が読めない場合も安全側に倒して
  リロードしません（fail-closed）。
- 実機確認（HW Goodwin 併用時）:
  1. HW-Genie のみ有効 → 認証成功ログが出る
  2. HW Goodwin のみ有効 → Goodwin の UI が表示される
  3. 両方有効 → 双方が機能する（Goodwin の UI 表示 + HW-Genie の認証送信）
- 実機確認（自動リロード。DEV 版で先行検証すること）:
  (a) セッション失効の検知 → `location.reload()` による回復 → クールダウン・
  上限超過時の suppress（ログのみ）を確認し、kill-switch
  （`localStorage.setItem("hw-genie-auto-reload-disabled", "1")`）で無効化
  できること、解除（`removeItem`）で再有効化できることを確認する。
  (b) HW Goodwin 併用: DEV 版有効のままダンジョン・コスミック自動攻略が
  動くこと（Goodwin の UI・自動操作が壊れないこと）。
  (c) 複数タブ挙動: カウンタはタブスコープ（合算されない）こと、kill-switch
  は全タブ共有（1 タブの設定が全タブに効く）ことを確認する。

## 構成

- `index.ts` — ユーザースクリプト本体（メタデータ + 起動処理）
- `release-metadata.sh` — メタデータ抽出・タグ検証・成果物バリデーション用 CLI
- `xhr-interceptor.ts` — XHR インターセプタ（共有モジュール。テストからも import）
- `session.ts` — セッション送信の状態機械（共有モジュール。テストからも import）
- `auth-recovery.ts` — セッション失効判定と自動リロード可否（共有モジュール。テストからも import）
- `tests/` — 回帰テスト・メタデータテスト
