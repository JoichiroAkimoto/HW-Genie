# Userscript リリース・自動更新改善 RR

**作成日:** 2026-08-03
**ステータス:** 提案（実装前）
**対象:** `src/userscripts`、`.github/workflows/release.yml`、userscript のリリース手順

## 1. 背景

HW-Genie の userscript は、`src/userscripts/index.ts` の `@version` を更新して Git tag を push すると、GitHub Actions が bundle を作成し GitHub Release に `.user.js` を公開する運用になっている。

Tampermonkey の自動更新を成立させるため、配布物の metadata は次の構成にする。

- `@downloadURL`: 現在のバージョンを指す固定 URL
- `@updateURL`: 常に安定版の最新 Release asset を指す `releases/latest/download/...` URL

今回の改善では、タグ起点の運用を維持しながら、PR時の事前検証、Release実行時の整合性検証、失敗時の復旧、文書の同期を整備する。

## 2. 現状調査

### 2.1 確認できた障害

- `v1.0.5` tag は存在するが、対応する GitHub Release と `.user.js` asset が存在しない。
- 公開済み `v1.0.4` asset の `@updateURL` は `v1.0.4` 固定 URL のままで、`releases/latest` ではない。自動更新修正のソース変更は、公開済み成果物に反映されていない。
- GitHub Actions の直近の `release.yml` 実行は、job が作成されず `workflow file issue` で失敗している。
- ローカルの `actionlint` で、`release.yml` の run block 内コメントに空の GitHub Actions 式を意味する文字列があり、式のパースエラーになることを確認した。コメント内であっても workflow 式の構文検査対象になるため、コメントが workflow 全体を壊している。
- `actionlint` は上記以外にも、`$GITHUB_OUTPUT` の未引用展開と未使用の `TAG_NAME` を検出している。これらは直ちに構文エラーではないが、同じ workflow を保守する際のノイズと誤動作要因になる。

### 2.2 現状の品質状態

- userscript の単体・統合テストは `49 pass / 0 fail`。
- `build.sh` には型チェック、IIFE 構造検証、metadata 必須キー検証、URL placeholder 検証がある。
- しかし、userscript のテストと build は既存の Python 用 workflow の対象外で、PR時に自動実行されない。
- Release workflow は tag push 時にしか動かないため、通常のPRでは workflow 自体の構文エラーや、Release時に初めて分かる metadata・URL不整合を検出できない。
- リリース手順は `git push --tags` を案内しており、対象 tag を限定して push する手順、失敗時の復旧、既存 tag の再公開方法が定義されていない。
- 同一 tag の workflow が重複実行された場合の排他制御がない。
- 既存の手順書は手動移行対象を「v1.0.3以前」と説明しているが、公開済み v1.0.4 asset も `@updateURL` が固定 URL のため、実際の移行対象と一致していない。

## 3. 目的と非目的

### 目的

1. `vX.Y.Z` tag の push を起点に、対象 tag のソースから userscript を build し、対応する GitHub Release asset を公開する。
2. 公開された配布物が、version・tag・download URL・update URL の整合性を満たすことを自動検証する。
3. PR段階で userscript のテスト、型チェック、bundle build、workflow 構文を検証する。
4. Release workflow が失敗しても、tag を書き換えず再実行・手動復旧できるようにする。
5. 実際の運用とドキュメントを一致させる。

### 非目的

- `main` への merge を自動的に Release とする完全自動リリースへの移行
- userscript 本体の XHR interception、認証送信、session state の仕様変更
- GitHub Release 以外の配布基盤（GitHub Pages、外部 CDN 等）の導入
- 過去の tag や既存ユーザーの installed script を強制的に書き換えること

## 4. 採用方針と代替案

### 採用: tag 起点の Release を堅牢化する

運用は次のまま維持する。

1. PRで `@version` を更新する。
2. PRを merge する。
3. 最新の対象 commit に `vX.Y.Z` tag を作成し、その tag だけを push する。
4. GitHub Actions が build、Release asset 公開、公開後検証を実行する。

この方式は、リリースの意思決定を人間が明示でき、Actions に tag 作成権限を与えずに済む。既存の GitHub Release と Tampermonkey の仕組みも維持できる。

### 代替案A: 固定配布 URL を GitHub Pages 等で提供する

自動更新 URL を常に同じファイルへ向けるため、`releases/latest` が作成日時や prerelease/draft の扱いに依存する問題を避けられる。一方で、Release asset と別の公開経路を運用する必要があり、今回の障害修正としては範囲が広い。将来、複数配布チャネルや強いキャッシュ制御が必要になった場合に再評価する。

### 代替案B: main merge から tag・Release まで自動化する

操作は簡単になるが、Actions の contents write 権限、tag 作成ループの防止、バージョン変更検出、誤リリース防止が必要になる。現在の userscript のリリース頻度と問題の性質に対しては過剰であるため、今回は採用しない。

## 5. 提案する構成

### 5.1 PR検証 workflow

userscript関連の変更を対象に、専用の検証 workflow を追加する。対象パスは少なくとも次を含める。

- `src/userscripts/**`
- `.github/workflows/release.yml`
- userscript 検証 workflow 自身
- `RELEASE_PROCESS.md`

検証内容は以下とする。

1. 現在の検証環境に合わせた Bun `1.3.12` をセットアップする。`latest` は使わない。
2. `bun install --frozen-lockfile` を実行する。
3. `bun test tests/` を実行する。
4. `bash build.sh` を実行し、型チェックと bundle 構造検証を通す。
5. download URL と update URL をテスト用の値で注入し、生成物の metadata を検証する。
6. release workflow と userscript PR検証 workflow に対して、actionlint `1.7.7` を実行する。actionlint 自体の取得元と checksum または固定版指定も実装時に固定する。

PR検証 workflow には `permissions: contents: read` を明示し、GitHub Release の作成などの書き込み権限を与えない。これにより、PRでの安全な検証と、tag push時の公開処理を分離する。

### 5.2 Release workflow

既存の `.github/workflows/release.yml` を次の責務に限定する。

1. `v*` tag push で起動する。
2. 必要に応じて `workflow_dispatch` で指定 tag を復旧できる。
3. 対象 tag の commit を checkout する。
4. metadata block から `@version` を抽出する。
5. `@version` が一意で `X.Y.Z` 形式であることを確認する。
6. tag 名が `v${VERSION}` と完全一致することを確認する。
7. Bun の依存関係を lockfile 固定でインストールする。
8. `@downloadURL` に version 固定 URL、`@updateURL` に `releases/latest` URL を注入して build する。
9. Release がなければ作成し、存在すれば同一 tag の asset を安全に更新する。
10. Release の状態、asset 名、asset の存在を確認する。
11. version 固定 URL と latest URL から配布物を取得し、metadata の version と URL が期待値と一致することを確認する。version 固定 URL は常に dispatch 対象の version と一致させる。latest URL は通常の新規安定版リリースでは対象 version と一致することを確認し、過去 tag の復旧では「現在の安定版 latest の metadata が正しい」ことだけを確認する。取得処理は接続 timeout 10秒、全体 timeout 30秒、初回試行に加えて3回 retry（最大4試行）を持ち、上限超過時は失敗とする。
12. Release notes に既存ユーザー向けの移行案内を含める。

実行モードは明示的に次の2つへ分類する。

- `normal`: tag push、または `workflow_dispatch` の通常公開。Release作成後に対象 tag が安定版 latest になっていることを要求する。
- `recovery`: 既存tagの欠落・破損した Release/asset を復旧するための手動実行。fixed URL は対象tagと一致させるが、latest URL は復旧時点での現在の安定版 Releaseを検証し、対象tagとの一致は要求しない。

tag push は常に `normal` として扱う。`workflow_dispatch` は `tag` と `release_mode` を必須入力とし、`tag` は `vX.Y.Z` 形式で、実際に存在する tag でなければならない。checkout、version検証、Release操作、concurrencyの対象にはこの正規化済みtagを使う。`release_mode=recovery` は過去commitを新規リリースするためではなく、既に作成済みのtagに対応するReleaseを復旧するためだけに使う。

`normal` では、既存の安定版 latest がある場合、対象 version が current latest version 以上であることを事前に確認する。対象 version がより古い場合は normal として公開せず、既存Releaseの欠落・破損なら recovery modeを要求する。同じ version の再実行は、同じtagのReleaseを更新する場合に限り許可する。

workflow の run block 内には、GitHub Actions 式に見える空文字列や説明用の `${{ }}` を置かない。動的な値は `env` 経由で shell に渡し、shell 展開と GitHub Actions 式展開を分離する。

### 5.3 再実行と排他

- `releases/latest` がリポジトリ全体で共有されるため、tag単位ではなくRelease workflow全体を1つの concurrency groupとして直列化する。
- `cancel-in-progress` は false とし、先行する別tagまたは同じtagの公開処理を途中でキャンセルして中途半端な asset を残さない。
- 同一 tag の再実行は冪等にする。既存 Release の asset を clobber しても、tag と version が一致する場合だけ許可する。
- `workflow_dispatch` の入力は `tag`（必須文字列）、`release_mode`（必須 choice: `normal` / `recovery`）、`expected_latest_tag`（任意文字列。ただし recovery時は必須）とする。push時は `github.ref_name`、dispatch時は `inputs.tag` を正規化済みtagとして使用する。
- main の workflow 定義で dispatch を受け、指定された過去 tag のソースを checkout して復旧できるようにする。
- `recovery` では `expected_latest_tag`（必須文字列）も入力させ、workflow開始時点でそのtagが安定版 latestであることを確認する。対象tagが `expected_latest_tag` と異なる場合、対象Releaseを latest に変更しない。
- recovery対象のReleaseが存在しない場合は `--latest=false` で安定版Releaseを作成する。recovery開始前の `expected_latest_tag` が処理後も latestであることを検証し、latestが変化した場合は成功扱いにしない。
- normalで新規Releaseを作成する場合は `--latest` を明示し、recoveryでは `--latest=false` を明示する。既存Releaseのasset更新では、latest状態を暗黙に変更する操作を行わない。
- 既存 tag を force push して内容を差し替える復旧方法は採用しない。
- 対象 tag の Release が draft または prerelease の場合は、workflow が自動で公開状態へ変更せず、asset の上書きも行わずに失敗する。運用者が Release の状態を確認・修正した後、workflow を再実行する。
- 対象 tag に既存の安定版 Release があるが asset が欠落または metadata 不一致の場合は、tag/version 一致を再確認したうえで asset を再生成する。

### 5.4 バージョンと配布 URL

Release 成果物の metadata は次を受入条件にする。

```text
@version      X.Y.Z
@downloadURL  https://github.com/<owner>/<repo>/releases/download/vX.Y.Z/hw-genie-auth-capture.user.js
@updateURL    https://github.com/<owner>/<repo>/releases/latest/download/hw-genie-auth-capture.user.js
```

`releases/latest` は安定版の最新 Release を指すため、Release は draft や prerelease として公開しない。Release作成・更新時には `draft=false` と `prerelease=false` を明示し、公開後にその状態を検証する。`normal` では latest URL が対象 version を返すことを確認する。`recovery` では fixed URL が復旧対象 version を返すことを必須とし、latest URLから実際に返された asset の metadata version が、入力された `expected_latest_tag` と一致することだけを確認する。これにより、v1.0.6 公開後に v1.0.5 の asset を再生成するケースでも、古い assetの検証が誤って失敗せず、latestが過去バージョンへ戻ることも防止する。

## 6. 失敗時・移行時の扱い

### v1.0.5 の復旧

`v1.0.5` tag は既に存在するため、tag を作り直さず、修正後の `workflow_dispatch` で `tag=v1.0.5`、`release_mode=recovery`、`expected_latest_tag=v1.0.4` を指定して Release と asset を作成する。復旧時も、tag の `@version` と生成物の `@version` は `1.0.5` で一致させる。将来、より新しい安定版が存在する状態で過去 tag を復旧する場合も同じ recovery modeを使い、fixed URLの確認と expected latestの維持を必須とする。

### 既存ユーザーの移行

`v1.0.4` 以前の配布物は `@updateURL` が version 固定のため、最新 Release を自動検知できない。次の安定版を公開した後、既存ユーザーには `releases/latest/download/hw-genie-auth-capture.user.js` から一度手動で再インストールしてもらう。

移行案内は Release notes と `RELEASE_PROCESS.md` に記載する。新しい配布物をインストールした後は、`@updateURL` が `releases/latest` になっていることを確認できるようにする。

## 7. ドキュメント更新

### `RELEASE_PROCESS.md`

- `@version` 更新から tag push、Release 完了確認までの手順を明記する。
- `git push --tags` ではなく、対象 tag だけを push するコマンドを案内する。
- tag と `@version` の一致が必須であることを明記する。
- `workflow_dispatch` による再実行・復旧手順を追加する。
- v1.0.4以前のユーザーの手動移行を明記する。
- normal modeでは draft、prerelease、過去 commitへのtagを安定版リリースに使わないことを明記する。既存tagの欠落・破損Releaseを直す場合だけ、recovery modeを使える例外として説明する。

### `src/userscripts/README.md`

- ローカル test/build の手順を維持する。
- Release 用 URL 注入の期待値を明記する。
- placeholder が残ったローカル build は配布物として使わないことを明記する。

## 8. 受入条件

### 必須

- release workflow と userscript PR検証 workflow に対する `actionlint` がエラー・warningなしで終了する。既存の他用途 workflow は今回の受入対象に含めない。
- userscript PR検証で `bun test tests/` が成功する。
- userscript PR検証で型チェック・bundle build・metadata 検証が成功する。
- tag push で対象 tag 以外の通常 pushから Release job が実行されない。
- `@version` と tag が不一致の場合、Release作成前に失敗する。
- 不正な version 形式の場合、Release作成前に失敗する。
- 成果物の `@downloadURL` が version 固定 URL になる。
- 成果物の `@updateURL` が `releases/latest` URL になる。
- Release notes に「v1.0.4以前の利用者は自動更新のため一度手動再インストールが必要」という移行案内が含まれる。
- Release asset が存在しない、空、または metadata 不一致の場合、workflow が成功扱いにならない。
- 同一 tag の workflow 再実行で asset が壊れず、最終的に1つの正しい assetになる。
- `workflow_dispatch` で `v1.0.5` の Releaseを復旧できる。
- 不正な dispatch tag、存在しない tag、draft/prerelease の既存 Release は、公開処理を行わず明確なエラーで停止する。
- 新しい安定版公開後に recovery modeで過去tagを再実行しても、fixed URL の検証は通り、latest URL の検証は現在の安定版を基準に判定される。
- `workflow_dispatch` の `tag` が未指定、不正形式、存在しないtagの場合はbuild・Release公開を行わず停止する。`release_mode` が `normal` の場合は対象tagが公開後にlatestにならなければ失敗し、`recovery` の場合は対象tagとlatestが異なっていても成功できる。
- `recovery` の `expected_latest_tag` が未指定、存在しない、または開始時点のlatestと一致しない場合は、Release作成・asset上書きを行わず停止する。
- `recovery` で対象Releaseを新規作成しても、`expected_latest_tag` のReleaseがlatestとして維持される。
- 複数のtagのRelease実行が同時に要求されても、Release workflowは直列に処理され、latest検証が別のReleaseによって不確定にならない。
- URL検証は一時的なGitHubのリダイレクト・asset反映遅延を考慮し、接続 timeout と有限回数の retry を持つ。retry 上限を超えた場合は失敗扱いにする。
- リリース手順書が実際のworkflowの起動条件・復旧方法と一致する。

### 確認項目

- 公開済みの version 固定 URL が HTTP 200 で配布物を返す。
- `releases/latest/download/...` が新しい安定版の asset を返す。
- Tampermonkey にインストールした配布物の metadata が期待値になっている。
- v1.0.4以前からの手動再インストール後に、`@updateURL` が `releases/latest` へ移行している。

### 検証シナリオ

- 新規tagの normal 公開
- 同一tagの再実行と欠落assetの再生成
- tag/version不一致、不正な dispatch tag、存在しないtag
- 既存 Release が draft または prerelease の場合の安全な停止
- より新しい安定版が存在する状態での recovery 公開（expected latestの維持を含む）
- 複数tagの同時実行が直列化されること
- version固定 URLのmetadata不一致、latest URLのmetadata不一致、HTTP/redirect失敗

## 9. 実装タスク

1. `release.yml` の空の式、未引用 shell 展開、未使用変数を修正し、対象workflowの actionlintを通す。
2. userscript PR検証 workflow を追加する。
3. Release workflow の tag checkout、dispatch復旧、concurrency、公開後検証を実装する。
4. version・tag・metadata・配布 URL の検証をテスト可能な形に整理する。
5. `v1.0.5` を recovery modeで手動復旧し、version固定URL、latest URL、expected latestの維持、移行案内を実際に確認する。
6. `RELEASE_PROCESS.md` と `src/userscripts/README.md` を更新する。
7. 対象workflowの actionlint、userscript test、build、公開後の URL 検証を実行して完了判定する。

## 10. 完了の定義

次回以降、開発者が `@version` を更新して対応する `vX.Y.Z` tag を pushするだけで、GitHub Actions が対象 version の userscript を Release に公開し、公開後の配布物が正しい metadata を持つことを自動確認できる。PR段階では、Releaseを作成せずに同じ build・test・workflow lintの問題を検出できる。
