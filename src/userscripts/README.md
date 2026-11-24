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

#### 資産一覧（ブランドマークの Source of Truth は 2 つ: マップ + ワードマーク PNG。画面収録 GIF はこの話の外）

ブランドマーク系の資産に限り、`docs/assets/` 全体が正解なのではなく、**意図を書いているのは 2 つの SoT（マップ + ワードマーク PNG）** ＝ `hw-genie-icon.grid.txt`（マーク）と `hw-genie-wordmark.png`（バナーの文字）だけ。マーク系の PNG（マスター 1024 / 透過 512 / 不透明 512 / og:image バナー）と favicon はすべてこの 2 つから機械的に再生成される派生物なので、手編集する必要もしてよいこともない。**この「2 つの SoT ＋その派生物」でカバーするのはブランドマークまで**で、`docs/assets/` の全ファイルが SoT か派生物のどちらかに収まるわけではない——`hw-genie-demo.gif`（画面収録）はどちらでもなく、マップからもワードマークからも再生成できない（表の最終行）。

| ファイル | 用途 |
| --- | --- |
| `hw-genie-icon.grid.txt` | **Source of Truth（その 1）**。64x64 のドットマップ（1 文字 = 1 セル、コメント行に 6 色の宣言 `CH=#RRGGBB`。内訳は地色 X=3,404 / 盾 G=223 / ゴールド V=219 / 煙コア B=115 / 影 L=79 / 煙エッジ A=56 セル＝計 4,096）。下の PNG はすべてここから `render_brand_assets.py` で描画する（例外はバナーの文字だけで、それは Source of Truth その 2 の `hw-genie-wordmark.png` としてコミットされている）。意図の審査は画像ではなくこのテキストで行うので **PNG の画素を手編集しない** |
| `hw-genie-icon-1024.png` | マスター 1024px（マップを scale 16 で描画。6 色・不透明・実測 2,238B） |
| `hw-genie-icon.png` | 配布用 512px **背景透過**（マップを scale 8 で描画し、地色 `X` のセルだけ alpha 0。6 色・実測 1,183B）。使いどころ: `@icon`・Pages の png favicon・README と Pages ヘッダーのタイトル行・`_config.yml` の `logo`（JSON-LD の publisher） |
| `hw-genie-icon-solid.png` | 配布用 512px 不透明（マップを scale 8 で描画。apple-touch-icon 専用。iOS は透過 PNG を黒で塗り潰すため別ファイルにする。6 色・実測 1,165B） |
| `favicon.ico` | 16 / 32 / 48 / 64 レイヤー（マスターから最近傍縮小。各レイヤー 6 色で 8 色以下に収まる。実測 5,414B。indexed BMP 4bpp は 1 ビットの AND マスクで完全透過を表現できる＝部分的な alpha だけが不可で、magick も実際にマスクを書く。現行の favicon.ico は地色を抜いていない＝不透明な紺タイルを意図的に残したものであり、形式上の制限ではない。透過のタブアイコンを望むなら per-size ループに `-transparent "#03104D"` を追加する。**検品 5 の対象外**である点も正直に書いておく（`render_brand_assets.py` は ICO を一切書かず、CI のテストもコミット済み favicon.ico を開かないので、drift ゲートも色数トリップワイヤもここでは効かない。現行手順で ICO に実際に走らせるのは 検品 1（各レイヤー 8 色以下）だけで、検品 4 は対象外（下の注））。ICO 側の来歴は下の per-size magick ループのみで、バイト一致はゲート外で確かめた（現行マスター 1024px から同じループを流すと 5,414B / sha256 `2a41c7ba…` がそのまま再現する） |
| `hw-genie-wordmark.png` | **Source of Truth（その 2）**。バナーの文字ブロック（"HW-Genie" と副題）だけを切り出した 490x410 の実測画（5 色・不透明・メタデータ無し・実測 1,793B）。**来歴は明示的でリポジトリ内から実行可能**: マップ化直前のバナー（commit `0847ae3` の `docs/assets/hw-genie-social-preview.png`。1280x640 / 35,696 色 / 459,954B）を下の「ワードマークの来歴（ブートストラップ）」のコマンド列どおり切り出し → `--palette` で 5 色量子化 → `--min-area 48` で吸収したもの。**この 1 ファイルだけがマップに写らない意図（文字の字形）を持つ**ので、再生成は上記の来歴コマンドに限定される。なお 490x410 の切り出しは文字だけでなく**バナー由来の＋型スパーク 2 個も**運んでいる（ワードマーク x[355,390) y[8,42) と x[320,356) y[371,405)。バナー上は x[995,1030) y[138,172) と x[960,996) y[501,535) で、実測 706px / 718px の＋型。マップのマーク自身が持つ＋型スパーク 3 個（下の行）とは別の意図）。`compose_banner` の入力専用で、`@icon` / favicon / og:image のどれからも参照していない（`docs/assets/` ごと公開する Pages の性質上 `docs/assets/hw-genie-wordmark.png` には到達できてしまうが、配布物としては扱わない）。**ワードマークへの手編集はどのゲートも通らない**: 検品 5 は「マップ + ワードマーク → 描き直した結果」とコミット済み PNG の画素比較なので、ワードマークへ塗り込んだ意図はそのまま再生成に反映されて一致してしまう（実測: 現行ワードマークの x[10,17) y[10,17) に 7x7 = 49px の `#FF0000` を塗り込んで上の通常手順を流し直すと、バナーは 8 色 / 3,188B になり、検品 5 は 4 アセットすべて `match` で exit 0、検品 4 は `smallest_island=49 min_area=48 ok`（わざと `min_area` 以上の島にしているので素通りする。そもそもドキュメント上の 検品 4 の 2 行はワードマークを見ていない）、CI の `pytest .github/scripts/tests` も実測 185 passed / 1 skipped で全部通る）。実際に効く保証は**バナー側**にある。上の箱と同じ 7x7 = 49px を、ワードマークの x[10,17) y[10,17) に対応するバナー上の位置（x[650,657) y[140,147)。バナーの文字ブロックは x[640,1130) y[130,540) に貼られる）へ直接塗り込むと検品 5 が `hw-genie-social-preview.png 1280x640 MISMATCH 49 pixels differ` を出して exit 1 になる（このとき 検品 4 は ok のまま＝49 ≥ 48）。だから文字領域の意図を変えたいときはゲートではなく人の審査で、**文字の意図を決める → 下の「文字だけ差し替えるとき」の 2 手順を流す → 描き上がったバナーを見る**という順で確認する（この節の数値は sandbox の現行資産で実測） |
| `hw-genie-social-preview.png` | 1280x640。og:image / twitter:image（7 色・実測 3,147B）。**マップから描画したマーク（scale 8）＋ コミット済み `hw-genie-wordmark.png`** を `compose_banner` が組み直す（7 色＝マーク 6 色＋文字の影色 `#2F2434`）。＋型スパーク 3 個（5 セルずつ、同一字形。これは**マップのマーク側**の数の話で、ワードマーク由来の＋型スパーク 2 個は上の行どおり文字側に別に入っている）はマーク自身の一部なので、旧運用のようにバナー側から写す必要はない（写すと旧写し箱 x[162,197) y[276,311) がマップ側スパーク x[214,238) y[304,328) の隣に寄って二重スパークになった。実測: 両箱は x 方向に 17 列の隙（最近列間 18px）で開き y は 7 行重なるので、**両箱の最近画素間は 18px（Chebyshev = Manhattan = 18px）**。同じ画像で 2 つの＋型の金の画素同士で見ると **Chebyshev 18px / Manhattan 24px**）。入力はマップとワードマークの 2 ファイルだけなので、何度流しても同じバイト列になる（冪等） |
| `hw-genie-demo.gif` | **SoT でも派生物でもない**もの。README に埋め込む**画面収録**のアニメ GIF（実測 2,432,609B / 論理画面 1100x750 / 136 フレーム。パレットは**グローバルの 128 エントリだけ**で、136 フレームすべて local colour table なし＝局所パレットは 0 個。GIF を構造的にパースして実測: Logical Screen Descriptor の GCT flag=1・size flag=0b110（=128 色）、image descriptor の local flag は全フレーム 0）。来歴は `scripts/demo/demo.tape`（`vhs`）＋ `gifsicle -O3 --lossy=30 --colors 128` で、テープの `Set Width 1100` / `Set Height 750` は GIF の論理画面実測 1100x750 とそのまま一致する（`scripts/demo/README.md` に残っていた 1100×800 は古い記述で、実測値 1100×750 に直した）。**マップからもワードマークからも再生成不可**（手順は `scripts/demo/README.md`）。**検品ゲートの対象外**: 検品 2 は `docs/assets/*.png` のみ、検品 4 は PNG / ICO 専用（GIF は `ERROR: … PNG / ICO 以外の形式は開けません` で exit 2）、検品 5 は `render_brand_assets.py` の描画対象だけを見る。Pages 上の参照実在チェックには `README.md` の `docs/assets/hw-genie-demo.gif` 経由で `/assets/hw-genie-demo.gif` として載っているので、削除すると Pages ビルド前に落ちる（配布ブランド資産ではなく、上の 2 SoT の話には含まれない） |

Pages 側の組み込みは `docs/_includes/head-custom.html`（favicon.ico → 透過 512px png → apple-touch-icon（不透明）。テーマ本体の `head-custom-theme-colors.html` と Google Analytics フックは追加型で残す。`theme-color` はテーマが `#151515` 固定で出すため重複指定しない）、`docs/_layouts/default.html`（テーマの同ファイルをコピーして h1 のタイトル行に透過アイコンを 34px インライン化。**テーマ更新を自動追跡しないので、hacker テーマ側の変更は手動で反映する**）と `docs/_config.yml`（`logo` は JSON-LD の publisher 用、`defaults` の `image` が og:image を生成）。

タイトル行の実描画は `./ [マーク] HW-Genie` になる（左から `./ ` → アイコン → タイトルの順）。先頭の `./ ` は hacker テーマが `header h1::before { content: "./ " }` で出すプレフィックスで、この並びはターミナル風テーマの見ためどおり＝**意図的なもの**。誤記ではないので「修正」（`::before` を消す・アイコンを先頭に寄せる等）はしないこと。`default.html` はコピーして持っているためテーマ更新の自動追跡はないが、このテーマ側の規則自体は反映対象ではなくそのまま受け入れる。

#### 生成と検品

ピクセルアートは既定の補間縮小で境界がにじむ（6 色が 100 色超に膨らむ）ため、縮小は必ず最近傍 (`-filter point`) ＋ 非ディザーの 8 色上限量子化で行う。さらに `-strip` を添えて、生成ツールの来歴メタデータ（`TC260:AIGC` の ContentProducer / ProduceID など）とタイムスタンプを公開資産に残さない。

マーク本体は `docs/assets/hw-genie-icon.grid.txt`（64x64 ドットマップ）から `.github/scripts/render_brand_assets.py` が決定的に描画する。1 セルを `scale x scale` のブロックへ引き伸ばすだけで、補間もノイズ後処理も手作業のかすれも介在しない。生成ツールノイズはマップ化の時点で消えているので、マスターに対する `despeckle` 工程は不要になった。

`.github/scripts/despeckle_assets.py` は **マップ外のラスターを取り込むとき**のクリーナーとして残す。現行の `hw-genie-wordmark.png` はこれでノイズを落としたもので、`compose_banner` はその結果をそのまま写しているだけ。SoT その 2 はマップに写らない意図（字形）を持つ唯一のバイナリなので、**来歴をリポジトリ内の値だけで再実行できるように**しておく。そのために量子化先を参照 PNG ではなく `--palette` の 16 進列で直接指定できる（参照 PNG が自分の成果物になる循環＝鶏と卵を断つ）。

**［ラベル: ワードマークの来歴（ブートストラップ。マップ化直前のバナーから 1 回だけ流す）］**

来歴の源は commit `0847ae3`（マップ化直前の main）の `docs/assets/hw-genie-social-preview.png`。実測 1280x640 / 35,696 色 / 459,954B（sha256 `18e9cf0a…`）で、文字ブロックの箱は現行と同じ x[640,1130) y[130,540)。

```bash
cd "$(git rev-parse --show-toplevel)"   # 以降のパスはすべてリポジトリルート基準

# 0) 来歴のバナーを git 履歴から取り出す（作業ツリーの資産は触らない）
git show 0847ae3:docs/assets/hw-genie-social-preview.png > /tmp/hwgenie-src-banner.png

# 1) 文字ブロックだけ切り出す（先に切ると後段の書き戻しが自前コーダになる。
#    -depth 8 は 16bit 原画に対してのみ必須。この 8bit 源では外しても同じ 1,793B /
#    sha256 6cf15855… がそのまま再現する）
magick /tmp/hwgenie-src-banner.png -crop 490x410+640+130 +repage -strip -depth 8 /tmp/hwgenie-wordmark-draft.png

# 2) ワードマーク実測の 5 色へ量子化 → min_area 48 で吸収
#    ここで colors=19527->5 recoloured=2675 passes=2 / 1,793B に決着する
python3 .github/scripts/despeckle_assets.py --min-area 48 \
  --palette "#03104D,#F1B11D,#AD5914,#2F2434,#A79AE1" /tmp/hwgenie-wordmark-draft.png

# 3) SoT へ据えて、そこからバナーを組み直す
cp /tmp/hwgenie-wordmark-draft.png docs/assets/hw-genie-wordmark.png
python3 .github/scripts/render_brand_assets.py
```

この 3 手順は**この変更が main に入った clone 上で**現行コミットのワードマーク（sha256 `6cf15855…`）を**バイト単位で再現する**（まだ入っていない clone で再現するとは限らない: main の `cea5681` 版には色列表を直接指定する `--palette` が存在せず `--palette-from` だけなので、手順 2 は exit 2 で落ちる（実測: 同じ引数を `cea5681` 版に渡すと argparse の省略形解決が `--palette-from` に化けて `ERROR: [Errno 2] No such file or directory: '#03104D,...'` になる）。吸収も 8 近傍で回る値が違う。源の `0847ae3` バナー自体は main 側にあるので、ブートストラップが流せるのはこのブランチの成果物が main に入った後ということ）。ただし一致するのは **`python3`（本README のコマンドが既定で呼ぶシステム Python / zlib-ng 1.3.1）で流したとき**だけ。別ビルド（例: `direnv` 経由の nix Python、zlib 1.3.2）だと**画素は同一・バイト列だけ変わる**（1,793B → 1,846B）。これはワードマーク固有でなく `render_brand_assets.py` が書く全 PNG に共通の性質で、検品 5 が画素単位しか見ない理由と揃う。`--palette` の色列表の**並び順**は最近傍の同距離打ち壊しにだけ効く入力で（`quantize` は二乗距離の厳密な `<` で先勝ち）、成果物の PLTE 順はそこではなく `encode_png` が画素を走査して最初に見えた順（＝ `colour_table` の順）で書く。現行の切り出し 19,527 色には 5 色の最近傍が同距離になる画素が**一つもない**（全 19,527 色を網羅して実測 0 件。量子化先が決まれば吸収は色列表の順を読まないので、これは「120 通りすべて同じ成果物」の証明でもある。私はそのうち 13 通り——宣言順・完全逆順・等間隔サンプル 11 通り——を実測して全一致: **画素差 0 / sha256 も現行コミットの `6cf15855…` と一致 / 1,793B**。現行 PLTE `#03104D,#F1B11D,#AD5914,#2F2434,#A79AE1` が宣言順と揃っているのは走査順がたまたま一致した結果で、`--palette` の順を写したのではない）。それでも**宣言順どおりに書く**運用は規約として残す（測れる画素効果はない）。色列表の並び是同距離が出た瞬間に優先順位そのものになるので、勝手に並べ替えると「今は同距離が 0 件」という条件が崩れた原画でだけ効く差を、現行資産では検証できないまま抱え込むことになる。

**［ラベル: 文字だけ差し替えるとき（マップ外ラスターの取り込み・通常は流さない）］**

新しい手描き原画を**現行の 5 色パレットに乗せ直す**ので、参照はコミット済みワードマークでよい（ブートストラップと違い、ここで成果物は既に存在する）。`-strip -depth 8` した 1280x640 を `docs/assets/hw-genie-social-preview.png` の位置に置き、次の **2 手順をこの順で**流す。

```bash
# 1) コミット済みワードマークをパレット基準にして、量子化＋吸収で文字のノイズを落とす（--palette-from 必須）
python3 .github/scripts/despeckle_assets.py --min-area 48 \
  --palette-from docs/assets/hw-genie-wordmark.png docs/assets/hw-genie-social-preview.png

# 2) 出来た文字ブロックをワードマーク資産へ切り直し、そこからバナーを組み直す
magick docs/assets/hw-genie-social-preview.png -crop 490x410+640+130 +repage -strip -depth 8 \
  docs/assets/hw-genie-wordmark.png
python3 .github/scripts/render_brand_assets.py
```

`-depth 8` は省略できないお守りです: 本codec は 8bit 専用で、16bit の PNG を渡すと（入力バナーでも `--palette-from` 参照でも）`ERROR: RGB PNG は bit depth 8 のみ対応（現状 16）` で exit 2 になり、取り込みが途中で詰まる（`PNG16:` や 16bit TIFF 経由の原画がこれに当たる。`-depth 8` / `-define png:bit-depth=8` で潰しておけばよい）。

吸収の打ち切りは黙っていません: 既定 64 周（`DEFAULT_MAX_PASSES`）でまだ変化が残ったときは `passes=64` の印字に加えて stderr へ `WARNING: … max_passes=64 で打ち切られました` が出る（＝固定点に達していない）。運用者が「収束したのか打ち切られたのか」を推測する必要はない。残った島は 検品 4 が `FAIL` で拾う。この stderr 印字は CLI 経路そのものを流すテスト `test_cli_warns_on_stderr_when_absorption_hits_the_pass_limit` で固定している（`DEFAULT_MAX_PASSES` を monkeypatch して実ファイルで打ち切りを作り、印字される `max_passes=` が実際の打ち切り周数と揃うところ、既定の周数では警告が出ないところまで見る。`process()` は上限をこの定数から明示的に読むので、印字と挙動がずれることはない）。

この 2 手順を**いつもどおりの再生成と誤って流した場合**の実測結果（現行資産で検証済み）: 1 でバナーは一瞬 5 色に潰れた中間状態になる（`bytes=3147->3019 colors=7->5 recoloured=0 passes=1`＝吸収は 0 画素）が、2 の後段 `render_brand_assets.py` がマークをマップから描き直すので、**最終的な配布 PNG はすべてバイト一致のまま**で、動くのは `hw-genie-wordmark.png` のバイト列だけ（magick の再エンコードで sha256 だけが代わり、画素は元のワードマークと完全一致。実測 0 画素差、1,793B → 1,636B）。壊れないが何も直らないので、通常手順は下の「通常手順」ブロックだけでよい。なお**上のブートストラップ形（先に切る）で現行資産に流すと完全な no-op** に収まる（`bytes=1636->1793 colors=5->5 recoloured=0 passes=1`、sha256 も現行コミットと一致）。

切り出し（2 つ目のコマンド）は量子化＋吸収の直後でなければならない。後段の `render_brand_assets.py` はコミット済みワードマークをバナーへ書き戻すだけなので、先に流すと新しい文字が捨てられる。`--palette-from` は**省略すると最後の検品を通らない**（収束のためだけでなく、成果物がゲート落ちする）。main の 35,696 色バナーを sandbox で入力にすると（sandbox は現行ブランチの作業ツリーに `0847ae3` のバナーを `docs/assets/hw-genie-social-preview.png` として置いたコピー。**`main` 単体では流せない**——後段の `render_brand_assets.py` が読むマップとワードマークは現行ブランチ側の資産なので、両方ないと検品 5 まで届かない）、`--palette-from` 付きは `colors=35696->5 recoloured=5572 passes=3` で決着し、検品 4 は `smallest_island=49 min_area=48 ok`、検品 5 も match。対照実験で **無し**（`--min-area 48` のみ）を同じ 2 手順まで流すと、吸収は `colors=35696->105 recoloured=589004 passes=47`（61,666B）でそれ自体は終わるが、**数値は成果物ごとに別々**に付く（実測: 切り出した**ワードマーク**が `colors=60 smallest_island=1` / 14,522B、それを組み直した**最終バナー**が `colors=66 smallest_island=1` / 19,194B）。検品 4 は両方で `FAIL`（exit 1）で、バナー行にもワードマーク行にも `smallest_island=1` が出る。CI のバナー色数トリップワイヤ（`test_real_banner_colour_count_tripwire` の `<= 16`）が拾うのは **66 色**の方（このゲートが見るのはバナーだけで、ワードマークの 60 は直接見ていない）。±1 揺れの類色 105 色が「形」として残ってしまい、コミットする文字パレットがその場のノイズ依存になる（同じ原画を再投入しても同じ色数になるとは限らない）のが原因側の実情で、ゲートはそれを「ノイズ」として弾く。量子化先を現行ワードマークの実パレット（5 色）に固定するのが収束と再現性の条件で、参照に文字の影色 `#2F2434` が入っていないと影が地色へ吸収される点も同じ理由——`hw-genie-wordmark.png` を参照すれば必ず入っている。閾値 48 は実見で決めた値で、48 なら 3 個の＋型スパークも 2-3 ロジカル px の煙リボンも「形」として残り、256 からは絵が簡略化しすぎる。※ この対照実験の通り方は手順の形で変わる: 上の文書どおり（バナー全体を量子化＋吸収してから切り出す）なら `smallest_island=1` で検品 4 が落ちるが、ブートストラップ形（先に切る）でやると `colors=19527->54 recoloured=172080 passes=47`（切り出し draft は 14,927B）→ ワードマーク `colors=54 smallest_island=58`・最終バナー `colors=60 smallest_island=58` となり、**検品 4 は素通りするが、CI 側は色数トリップワイヤだけでは済まない（2 本落ちる）**。実測: この control 出力を sandbox の `docs/assets/` に置いた状態で `pytest .github/scripts/tests/test_render_brand_assets.py -k "tripwire or wordmark_stroke"` を流すと `2 failed`（`test_real_banner_colour_count_tripwire` は 60 > 16、`test_real_banner_smallest_island_is_the_wordmark_stroke` は記録値 49 に対して 58）。どちらの形でも `--palette` / `--palette-from` なしは不可であることに変わりはない。

**現行ワードマークの来歴（採用判断の実測）**: マップ化移行時に作られた `hw-genie-wordmark.png`（1,892B / sha256 `5cb8512e…`）は、**どのコミットにも入っていない人工物**だった（`git log --all -- docs/assets/hw-genie-wordmark.png` は空になる。現行の 1,793B 側もこのブランチでは未コミットで、インデックスに `A` として入っているだけ）。1,892B は移行作業をしたクローンに **dangling blob `7ac3f8e138c9…`** としてのみ生き残っていて、`git cat-file blob 7ac3f8e138c920d736bf956f56e5705917f1f650 > /tmp/wm-1892.png` でしか取り出せない＝**追跡可能なコミット列から再現できる値ではなく、新規クローンではこの節の数値を再実行できない**（`gc` で消えれば二度と出ない）。それも「ラベルなし人工物」として採用しない理由の一部なので、以下は再実行手順つきで書く。測定の向きは共通で、来歴コマンドの 1〜2（`0847ae3` バナーから 490x410 切り出し → 現行ワードマークの実パレット 5 色へ量子化 → `--min-area N` で吸収）で作った候補を、**1,892B の人工物と画素単位で比べた差分**である。現行 4 近傍実装では **538 画素差**、移行時に近い旧 8 近傍実装（`git show cea5681:.github/scripts/despeckle_assets.py`）では **472 画素差**（2026-11-24 に両実装で再測）。`--min-area` を 8〜256 で掃引すると**両実装とも 47〜49 が最小差の谷**で、現行実装は 46 で 588・50 で 636、旧実装は 46 で 518・50 で 570。移行が `--min-area 48` 由来であることは裏取れる。ただし **472 という数字は「人工物が 8 近傍実装の産物だった」ことの証拠にはならない**（同じ掃引で旧実装の候補は現行コミットとの差が 234 画素＝人工物との 472 より近い。現行実装の候補は現行コミットと 0 画素差）。ここから言えるのは、両実装とも 47〜49 が谷で 46 / 50 に開くという `--min-area 48` の裏取りだけ。**再現できる側を正として採用**した。差は字形を壊していない（上の 538 画素差＝人工物と現行コミットの間で、採用前に実測で確認した数字。1,892B が手元に無いとこの内訳も再実行できない）:

* 差分は 135 個の細片（1〜22px、うち 106 個が 5px 以下）に分散し、200,900 画素の 0.268%。53 行にまたがり、最多はタイトル文字のベースライン境界 y=205 の 151 画素
* 色ごとの島数: 影 `#2F2434` は 19 島・最小 49px のまま**完全一致**、主色 `#F1B11D` も 11 島・最小 125px で一致。淡色 `#A79AE1` は 25 島のまま最小島が 95 → 113px へ**改善**、`#AD5914` だけが 13 → 14 島（最小 55 → 50px）
* 地色 `#03104D` は採用前後とも **4 接続で 1 島**（＝字の内側 counter を塞ぐ吸収は起きていない）
* 検品 4 は採用前後とも `smallest_island=49 min_area=48 ok`。差は閾値未満の島の有無ではなく、吸収が回った順序（移行時コードとの差分ノイズ）

透過版はマップの地色 `X`（`#03104D`）を alpha 0 に抜いて描画する（RGB は地色のままなので `identify` の `colors=` は 6、透過は 217,856px / 不透明 44,288px）。旧運用で外周に必要だった暗色斑点 `#2F2434` → 影色 `#AD5914` の書き換えは、マップに斑点自体が存在しないため無くなった。

**［ラベル: 通常手順（マークをマップから再生成して検品 1〜5 をまとめて回す）］**

```bash
cd "$(git rev-parse --show-toplevel)"   # 以降のパスはすべてリポジトリルート基準

# マーク描画（マスター 1024 / 透過 512 / 不透明 512 / og:image バナー）をマップから再生成
#   入力は docs/assets/hw-genie-icon.grid.txt と docs/assets/hw-genie-wordmark.png（文字）だけ。
#   書き戻すバナーを自分で読む経路はない（自己参照はその領域の検品 5 を盲目にする）
python3 .github/scripts/render_brand_assets.py

# favicon.ico（各サイズを作ってから合成）
for s in 16 32 48 64; do
  magick docs/assets/hw-genie-icon-1024.png \
    -filter point -resize ${s}x${s} -colors 8 -dither none -strip /tmp/ico${s}.png
done
magick /tmp/ico16.png /tmp/ico32.png /tmp/ico48.png /tmp/ico64.png \
  -strip docs/assets/favicon.ico

# 検品 1: favicon の全レイヤーが 8 色以下（現行マークは 6 色なので各レイヤー 6 になる）
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

# 検品 4: ノイズゲート。閾値未満の色島が残っていれば exit 1（各ファイルの最小島も印字される。マップ由来の描画は 1 セル = 256px（マスター）/ 64px（バナーのマーク）なので構造上必ず通過し、実効で効くのはバナーの**文字部分**とマップ外から取り込んだラスター。現行値はマスター smallest_island=256、バナー smallest_island=49（＝文字の最小島））
#   ※ ローカルの 検品 4 は 48、CI のテスト床は 40 に分けてある（実測 49 に対し 48 は余裕が 1px しかないため、CI 側は意図的な字画の簡略化で落とさない）。下の 2 行目（`--min-area 48` を使うバナー行）は従来の 48 のままにすること。1 行目はマスター専用の 128 で、2 行目との役割分担は下の注の通り。
python3 .github/scripts/despeckle_assets.py --check --min-area 128 docs/assets/hw-genie-icon-1024.png
python3 .github/scripts/despeckle_assets.py --check --min-area 48 docs/assets/hw-genie-social-preview.png
# ※ 512px png と favicon.ico は面積ゲートの対象外で、**理由は別々**だ。
#   - 512px png（透過 / 不透明）は最近傍縮小ではなく**マップから scale 8 で描いた直接描画**（`render_brand_assets.py` の ICON_SPECS）。だから迷彩色は構造上入らず、最小島は実測 **64px ちょうど＝マップ 1 セル（8x8）**。対象外なのは縮小の副作用ではなく寸法の性質で、マスター用の 128 閾値を当てはめると 64 で FAIL になる（`--min-area 48` / `64` なら ok、`128` は FAIL）。上の 128 行はマスター専用、48 行はバナー専用、という意味。
#   - favicon.ico だけが**マスター 1024px からの最近傍縮小**（上の per-size ループ）。ここで細線が分解してできる 1-4px の断片が「形」であって迷彩色ではない（吸収すると線が消える）。実測は 4 レイヤーすべて `smallest_island=1`（1px 島は 16px レイヤーで 11 個 / 32px で 19 個 / 48px で 28 個 / 64px で 23 個、**4px 以下の島の数**が 15 / 36 / 46 / 47 個で、**それらに属する画素数**が 20 / 62 / 77 / 77 個。単位を間違えると読めないので両方書く）。`--min-area 48` を当てると全レイヤーが FAIL（exit 1）になるのでゲートに載せられない。
#   迷彩色はマップを通らない取り込み経路でしか入らないので、検品 4 の 2 行が通れば縮小派生も迷彩色を継承しない。色枚数は検品 1、alpha は検品 3 で見る。
# ※ 検品 2 と同じく tRNS はメタデータではなくパレット透過そのもの。ゲートは透過画素を別色として数える（alpha を無視した色判定はしない）。ICO は読み取りのみ対応で、レイヤーごとに #0..#3 と印字される。
# ※ パレット以外（color type 0 / 2）の tRNS カラーキーも透過として解釈する（`magick -size 4x4 xc:red -transparent red PNG24:` のような異種ラスターの取り込みで効く。bit depth 8 に対してサンプル値が 255 を超える等、libpng が警告して無視する入力は黙って不透明にせず exit 2 で拒否する）。

# 検品 5: drift ゲート。マップから描画し直した結果をコミット済み PNG と画素単位で比べ、差があれば exit 1（1 アセット 1 行で一致／差分画素数を印字）
python3 .github/scripts/render_brand_assets.py --check
# ※ 比較はあくまで**画素単位**。「画素を変えない再エンコードやパレット順の入れ替えは検出しない（来歴メタデータは 検品 2、色島と色数は 検品 4 と CI テストが担当）」。実測: `hw-genie-icon-solid.png` の PLTE を逆順に並べ替えたファイル（PLTE を `#AD5914,#F1B11D,#421B97,#A79AE1,#6FBEF6,#03104D` に、インデックスも同じだけ入れ替えて、`encode_png` と同じ決定的フィルタ＋level 9 で描き直す。並べ替え方を決めていないと sha256 は再現できないので construction つき。結果 1,165B → 1,228B / sha256 `e1b6e8bf…` → `5c2e21cf…`、**画素は完全一致**）でも検品 2・検品 4・検品 5 は ok / match のままで、そのコピーを `docs/assets` に置いた砂箱では `pytest .github/scripts/tests`（実測 185 passed / 1 skipped）すら全部通る。つまりバイト一致は CI の保証ではなく、決定的生成側のローカル性質。
# ※ マップを通さない PNG 手編集（1px の塗り潰しや色の差し替え＝画素が変わるもの）は必ずここで落ちる。検品 1〜4 がすべてパスしても検品 5 が落ちる状態は「意図と成果物が食い違っている」ので、マップ本文／色宣言を直すか描画を流し直すどちらかで直す。
# ※ バナーの文字領域もここで実際に検証されている（ワードマークを別ファイルに切り出す前は、文字ブロックの箱 x[640,1130) y[130,540) ＝ 490x410 の**内側**に赤い矩形を塗り込んでも match が出ていた。箱をはみ出す 1200px 幅の矩形は地色部分が変わるので当時のゲートでも落ちていた＝箱の外しか見ていなかった）。手編集を落とすには `hw-genie-wordmark.png` 側を直す／上の 2 手順を流し直す必要がある。
# ※ CI の `pytest .github/scripts/tests`（`docs/assets/**` を触る PR で必ず走る `Python Checks`）が確認しているのも同じ**画素単位の drift**（`test_committed_repo_assets_match_the_map` は `--check` を呼ぶだけ）＋最小色島＋色数。つまり CI は「同じマップから再生成したときの sha256 一致」を保証しない——バイト一致はローカル決定的生成の性質（同じ入力 → 同じエンコード経路）であって、ゲートの対象は画素である。
```

検品は**コミット前のそのファイルに対して**行うもの（`git add` する前に実行し、検品 1〜5 すべてが期待値になったらコミットする）。マークの意図を変えたいときは `docs/assets/hw-genie-icon.grid.txt` を編集して `python3 .github/scripts/render_brand_assets.py` を流し、上の検品をまとめて回す。バナーの文字だけ差し替えるときは `-strip -depth 8` した 1280x640 をバナーの位置に置いてから、上の「文字だけ差し替えるとき（マップ外ラスターの取り込み・通常は流さない）」＝量子化＋吸収 → ワードマークの切り出し → 描画を流し直す（上の通常手順だけでは新しい文字は捨てられる）。ワードマーク自体を初めて作る（＝参照できる既存ワードマークがない）ときは、上の「ワードマークの来歴（ブートストラップ）」を流す。

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
