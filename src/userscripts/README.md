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

### アイコン（@icon）

`index.ts` の `@icon` は GitHub Pages 上の 512px アイコン（**背景透過**版）を指す。Tampermonkey /
Violentmonkey がインストール・更新時に取得し、一覧や詳細画面に表示する（取得のタイミングと既存インストールへの反映の関係は「反映タイミング」節）。DEV版も同じアイコンを使う（DEV版は `@name` と `-dev` サフィックスで区別される）。

```
https://joichiroakimoto.github.io/HW-Genie/assets/hw-genie-icon.png
```

絶対 URL を**ドキュメント本文**に明記するのは本セクションだけにする（コード内の `@icon` は仕様上 絶対 URL が必須。それ他のドキュメントは `docs/assets/` への相対参照に留める）。

- 参照の実在性は `github-pages.yml` の `Verify /assets references exist` が **Pages ビルド前**に検証する（走査対象は `src/userscripts/index.ts` / `docs/_includes/head-custom.html` / `docs/_layouts/default.html` / `docs/_config.yml` / `docs/index.md` / `README.md` / `src/userscripts/README.md`）。このステップは `build` ジョブにあるため、誤参照はアイコン限りでなく Pages デプロイ全体を止める。
- `@icon` URL の到達性は **リリース後**に `release.yml` が確認し、未達でも warning にとどめてスクリプト配布は止めない（Pages デプロイ遅れの 404 でリリースを止めない）。

#### 資産一覧（`docs/assets/` が Source of Truth）

| ファイル | 用途 |
| --- | --- |
| `hw-genie-icon-1024.png` | マスター（1024px / フラット 8 色 / 不透明） |
| `hw-genie-icon.png` | 配布用 512px **背景透過**（`@icon`・Pages の png favicon・README と Pages ヘッダーのタイトル行・`_config.yml` の `logo`（JSON-LD の publisher）） |
| `hw-genie-icon-solid.png` | 配布用 512px 不透明（apple-touch-icon 専用。iOS は透過 PNG を黒で塗り潰すため別ファイルにする） |
| `favicon.ico` | 16 / 32 / 48 / 64 レイヤー（各レイヤー 8 色以下、16px は 7 色。indexed BMP 4bpp は 1 ビットの AND マスクで完全透過を表現できる＝部分的な alpha だけが不可で、magick も実際にマスクを書く。現行の favicon.ico は地色を抜いていない＝不透明な紺タイルを意図的に残したものであり、形式上の制限ではない。透過のタブアイコンを望むなら per-size ループに `-transparent "#03104D"` を追加する） |
| `hw-genie-social-preview.png` | 1280x640。og:image / twitter:image。原画はリポジトリ外のためリポジトリ内容からの再生成は不可 |

Pages 側の組み込みは `docs/_includes/head-custom.html`（favicon.ico → 透過 512px png → apple-touch-icon（不透明）。テーマ本体の `head-custom-theme-colors.html` と Google Analytics フックは追加型で残す。`theme-color` はテーマが `#151515` 固定で出すため重複指定しない）、`docs/_layouts/default.html`（テーマの同ファイルをコピーして h1 のタイトル行に透過アイコンを 34px インライン化。**テーマ更新を自動追跡しないので、hacker テーマ側の変更は手動で反映する**）と `docs/_config.yml`（`logo` は JSON-LD の publisher 用、`defaults` の `image` が og:image を生成）。

タイトル行の実描画は `./ [マーク] HW-Genie` になる（左から `./ ` → アイコン → タイトルの順）。先頭の `./ ` は hacker テーマが `header h1::before { content: "./ " }` で出すプレフィックスで、この並びはターミナル風テーマの見ためどおり＝**意図的なもの**。誤記ではないので「修正」（`::before` を消す・アイコンを先頭に寄せる等）はしないこと。`default.html` はコピーして持っているためテーマ更新の自動追跡はないが、このテーマ側の規則自体は反映対象ではなくそのまま受け入れる。

#### 生成と検品

ピクセルアートは既定の補間縮小で境界がにじむ（8 色が 100 色超に膨らむ）ため、縮小は必ず最近傍 (`-filter point`) ＋ 非ディザーの 8 色量子化で行う。さらに `-strip` を添えて、生成ツールの来歴メタデータ（`TC260:AIGC` の ContentProducer / ProduceID など）とタイムスタンプを公開資産に残さない。

透過版は、地色を抜いたあと外周に残る暗色斑点 `#2F2434`（旧 609px、うち 571px がシルエット外周）をランプの影色 `#AD5914` に書き換えてから量子化する。そのまま残すと淡い背景の上で縁が黒く崩れて見えるためで、結果は 7 色（斑点 0 / 影色 6570px、透過画素数は差し替え前と同一）になる。

```bash
cd "$(git rev-parse --show-toplevel)"   # 以降のパスはすべてリポジトリルート基準

# 配布用 512px png（透過版: マスターの地色 #03104D を alpha 0 に抜き、外周に残る暗色斑点 #2F2434 をランプの影色 #AD5914 に寄せる）
magick docs/assets/hw-genie-icon-1024.png -transparent "#03104D" \
  -fill "#AD5914" -opaque "#2F2434" \
  -filter point -resize 512x512 -colors 8 -dither none -strip docs/assets/hw-genie-icon.png
# 注意: `-opaque` に `+opaque` は使えない（選択が反転して斑点以外が塗り替わり、alpha も落ちて透過版が壊れる）

# 配布用 512px png（不透明版: apple-touch-icon 用）
magick docs/assets/hw-genie-icon-1024.png \
  -filter point -resize 512x512 -colors 8 -dither none -strip docs/assets/hw-genie-icon-solid.png

# favicon.ico（各サイズを作ってから合成）
for s in 16 32 48 64; do
  magick docs/assets/hw-genie-icon-1024.png \
    -filter point -resize ${s}x${s} -colors 8 -dither none -strip /tmp/ico${s}.png
done
magick /tmp/ico16.png /tmp/ico32.png /tmp/ico48.png /tmp/ico64.png \
  -strip docs/assets/favicon.ico

# 検品 1: favicon の全レイヤーが 8 色以下（16px は 7 色）
identify -format "%s: %wx%h colors=%k\n" docs/assets/favicon.ico

# 検品 2: コミット対象の PNG にメタデータチャンクが無いこと # ※ tRNS はパレット透過そのもの（検品 3 の alpha=Blend の根拠）なので対象外。blacklist は来歴/時刻チャンクのみ。
python3 - docs/assets/*.png <<'PY'
import struct, sys
for path in sys.argv[1:]:
    data, off, bad = open(path, "rb").read(), 8, []
    while off + 8 <= len(data):
        ln = struct.unpack(">I", data[off:off + 4])[0]
        ty = data[off + 4:off + 8].decode("latin-1")
        if ty in ("tEXt", "zTXt", "iTXt", "tIME"):
            bad.append(ty)
        off += 12 + ln
    print(path, "meta chunks:", bad or "none")
PY

# 検品 3: 透過版は alpha=Blend、不透明版は alpha=Undefined（地色を抜いていないなら Blend にならない）
identify -format "%f alpha=%A\n" docs/assets/hw-genie-icon.png docs/assets/hw-genie-icon-solid.png
```

検品は**コミット前のそのファイルに対して**行うもの（`git add` する前に実行し、検品 1〜3 すべてが期待値になったらコミットする）。マスター／原画の差し替え時も同じく `-strip` した原画を取り込んでから上の生成コマンドを流し直す。

#### 反映タイミング

`@icon` の URL を変えずに `docs/assets/hw-genie-icon.png` の中身だけ差し替えた場合、効くのは**新規インストールだけ**（インストール時にその時点のバイト列が取得されるため）。既存インストールは、マネージャーが URL をキーに保存した旧いアイコンバイトを使い続け、**スクリプト更新（`@version` 上昇）が起きた時点で初めて**アイコンも再取得される。Violentmonkey は `cac:<iconURL>` の形でアイコンバイトを保存し、実際のスクリプト更新（`src.update` がある場合）か項目が無い場合のみ再取得する。Tampermonkey もスクリプト更新時に外部資産を同様に更新するため結論は同じ。Pages 側が `cache-control: max-age=600` を返すことはボトルネックではない（10分で切れる HTTP キャッシュより、マネージャー自身の保存領域が長生きするため）。

したがって**アイコンの変更を既存インストールに届けるには `@version` のバンプとタグリリースが必要**。手順は RELEASE_PROCESS.md（Source of Truth）に記載どおりであり、本READMEでは繰り返さない。

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
- `@downloadURL` / `@updateURL` は DEV 専用アセット（`-dev.user.js`）を指す
  URL のみ注入可。通常版 latest を指す URL はビルドエラー。
  自動更新で通常版を上書きしないための保護です

運用手順: ToE実行前にDEV版をON → 実行後にOFF → 通常版に戻す。
挙動自体は通常版と同一に保つため、DEV版で動いたものは通常版でも動きます。

### DEV版の pre-release 配布

`dev-*` タグは自動リリースの対象外（`release.yml` は `v*` のみ）のため、
手動で pre-release を切ります。pre-release は `latest` に影響しないため、
通常版の自動更新を乱しません（前例: `dev-20260916-bee72ee`）。

```bash
cd src/userscripts
TAG="dev-$(date +%Y%m%d)-$(git rev-parse --short HEAD)"
BASE="https://github.com/JoichiroAkimoto/HW-Genie/releases/download/${TAG}/hw-genie-auth-capture-dev.user.js"
bash ./build.sh --dev \
  --inject-download-url "$BASE" \
  --inject-update-url "$BASE"
gh release create "$TAG" --prerelease \
  --title "DEV build ($TAG)" \
  --notes "開発版（ToE 自動化用）。通常版と併用時は ToE 実行時のみ ON にすること。" \
  ./dist/hw-genie-auth-capture-dev.user.js
```

> 注意: バージョン固定 URL のため DEV 版の自動更新はかかりません。
> 新しい DEV 版が出たら入れ直してください。

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
  値が変わらない限り再送されません（署名はサーバー側が `x-auth-token` /
  `x-auth-session-id` / `x-request-id` とボディから再計算するため、捕捉した
  署名値の中身はサーバーでは使われません。ただしキャプチャ経路では
  `x-auth-signature` が必須ヘッダーとして存在チェックされます）。
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
- 送信失敗時はコンソールに `Failed to send auth headers to server (http-500): <理由>`
  の形式でサーバー側の失敗理由（例: `Session expired or invalid signature.`）も
  表示します（v1.0.14 以降）。同じ理由は auth-server のログにも記録されます
  （`auth header capture failed for x-auth-user-id=...`）。
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
