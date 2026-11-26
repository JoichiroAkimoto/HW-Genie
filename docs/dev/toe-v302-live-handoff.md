# ToE v302 live 実装 ハンドオフ（2026-11-27）

この文書は、HW-Genie の Titan Arena (ToE) を **v302 のサーバー検証（ultReseed proof）** に対応させる作業の現状と次手順を、別エージェント（Orca/Opencode）へ引き継ぐためのものです。

- ブランチ: `fix/toe-302-clan-pvp`
- PR: https://github.com/JoichiroAkimoto/HW-Genie/pull/212 （**draft**）
- 作業ディレクトリ: `/home/ak/ghq/github.com/JoichiroAkimoto/HW-Genie`
- Dev userscript: `src/userscripts/dist/hw-genie-auth-capture-dev.user.js`（**1.0.23-dev**、ビルド済み）

## 1. 結論（今どこまでできているか）

- v302 の原因を特定し、**ゲームクライアントの実戦で proof を作る live 経路**を実装した。
  - `PlayerTitanArenaData.bmc(rival, team)` → `TitanArenaBattleThread` → `battleSeed_next` → `titanArenaEndBattle`（proof 付き）
  - Orca 上で **EndBattle 受理（ok:true / win:true / stars=2）**、rival 250 点到達を確認済み。
- ハイブリッド RAID（offline 計算）は v302 でも有効で、Tier 5/6 を全クリア済み。
- Firefox + Tampermonkey の live ジョブは **1 戦目の受理まで確認済み**。
- **Auto の確実な有効化**を 1.0.23 で追加（`BattleController` の設定 `Rr.Qya` を ON）。**この版の実機検証が次の最優先**。
- PR #212 は draft。live 実機検証が通れば ready にする。

## 2. v302 の原因（確定事項）

1. **battle config の変更**: ToE は `get_titanPvpManual` ではなく **`get_titanClanPvp`**（`TitanArenaBattleThread` / `BattleThreadFactory` が `titan_arena` → `I.qj.f7a()`）。
2. **完了シグナルの shape**: 実 `signals.Signal1` は `add` + `subscribe` を持ち、`dispatch` はビルドごとに minify される。`add` + `dispatch` 判定はダミー連発になる。
3. **ultReseed proof**: StartBattle の `effects.ultReseed`（battleId / reseedInterval / keyVersion / signature）により、個別戦はクライアント実戦の `battleSeed_next` seeds / `progress[0].proof` が必要。オフライン `BattleInstantPlay` では作れない。
   - レイドは `effects: []` なので proof 不要（offline hybrid で通る）。

## 3. 実装マップ

| 領域 | ファイル | 内容 |
|---|---|---|
| userscript live | `src/userscripts/toe-bridge.ts` | `kind:"live"` ジョブ、`runLiveJob`（`bmc` / `x7.kBo` 捕捉 / EndBattle 結果）、Auto パッチ（`BattleController.start` で `Rr.Qya.ba(true)`）、結果後の `location.reload()`、`readAccount`（`GameModel` / `hwgenie-x-auth-user-id`） |
| auth capture | `src/userscripts/index.ts` | `x-auth-user-id` を `localStorage["hwgenie-x-auth-user-id"]` に記録（live の account 照合）。Dev 1.0.23 |
| auth server | `src/python/hw_genie/commands/auth_server.py` | `kind:"live"` ジョブ（`action:"battle"` / `"complete_tier"`）、account 完全一致、live の in-flight 300s、sim の account-less fallback は従来どおり |
| CLI live | `src/python/hw_genie/commands/toe_live.py` | live ジョブ投入/待機、チームローテーション、status でスコア確認、未所有/無効チームはスキップ、Tier 完了は API `titanArenaCompleteTier` |
| CLI 配線 | `src/python/hw_genie/main.py`, `runner.py` | `toe run --engine live`、`multi toe --engine live`。`--parallel 1` 推奨の警告 |
| テスト | `src/python/tests/test_auth_server_toe.py`, `test_toe_live.py` | live claim/submit、driver の clear/wall |
| Orca 研究用 | `src/userscripts/dist/extension-dev/` | MAIN-world content script の unpacked 拡張（Orca の TM 制約回避。**本番は Firefox TM**） |

## 4. 現在の ToE 状態（2026-11-27 00:5x 時点）

| Account | Tier | 残り | 備考 |
|---|---|---|---|
| VitaminD | 7 | 2 | 壁 `-470814` **220** / `-470914` **181**。Tier 4 は live でクリア、5/6 は RAID でクリア済み |
| Joe | 5 | 1 | rival `35085076` **249**（あと少し） |
| Champion | 8 | 3 | 壁系（`-480714`, `-480914`, `-480514` を確認） |
| The Best | 5 | 1 | rival `35653326` |

※ 実行で状態は変わります。都度 `uv run hw-genie toe status -a <account>` で確認。
※ 認証は一時的に新鮮（23:56/00:00 更新）だが、長時間後は `auth --list` / curl 更新が必要。

## 5. 直近の実機結果

- **Orca（研究）**: live で Tier 4 の rival を 250 点（+200）クリア。`battleSeed_next` が送られ、EndBattle が `ok:true`。
- **Firefox live 1 戦目**: 成功。EndBattle `ok:true`（win=False/stars=0）、壁 `-470914` は 181 のまま。
- **Firefox live 2 戦目**: Auto 未発火で userscript の 180s タイムアウト。→ **1.0.23 で `BattleController.Rr.Qya` を ON にするパッチを追加**（実機検証待ち）。
- 初回の RAID multi toe: RAID 対象でない Tier のため 4 アカウントとも失敗（想定どおり）。

## 6. 次の手順（Orca/Opencode 向け）

### 前提
1. **Firefox** の Tampermonkey に **1.0.23-dev** を上書きインストールし、**全アカウントのゲームタブをリロード**。
2. **Orca のゲームタブ / unpacked 拡張は live 中は閉じるか無効化**（描画停止中に live ジョブを横取りするとタイムアウトする）。
3. auth-server は Podman の `hw-genie-auth-server-1`（ソースをマウント）。auth_server.py を変更したら **`podman restart hw-genie-auth-server-1`**。`curl http://127.0.0.1:8765/health` で確認。
4. live は **対象アカウントのゲームタブを前面（描画中）に保つ**。Firefox のバックグラウンドタブは rAF が止まり、実戦が進まないことがある。

### 最初の検証（VitaminD）
```bash
cd /home/ak/ghq/github.com/JoichiroAkimoto/HW-Genie
uv run hw-genie toe status -a VitaminD   # Tier 7 / 壁 220,181 を確認
# VitaminD の Firefox ゲームタブを前面にしてから:
uv run hw-genie toe run -a VitaminD --engine live \
  --titans 4003 4023 4024 4004 4000 --max-attempts 1 --progress verbose
```
期待:
- ゲーム内で実戦が始まり、Auto が ON になって完走。
- CLI に `EndBattle ok win=... stars=... score=...`。
- 壁のスコアが 220/181 から上昇、またはクリア（250）。

### 壁の追い込み
- スコアは **max 保持**。同じチームで再試行（seed 抽選）や、ローテーションの他チームで加点。
- `--titans` 省略で保存チーム + 静的ローテーションを順に試す（未所有/無効チームはスキップ）。
- `--max-attempts N` は「rival あたりに試すチーム数」の上限。
- 壁がどうしても 250 に届かない場合は、**アカウントの育成/チーム強化**が必要（Tier 8 壁は特に）。

### 全アカウント
```bash
# まずは 1 アカウントずつ確実に（タブを前面に切り替えながら）
uv run hw-genie toe run -a Joe --engine live
uv run hw-genie toe run -a Champion --engine live
uv run hw-genie toe run -a "The Best" --engine live

# 慣れたら（--parallel 1 推奨、アカウントごとにタブを前面に）
uv run hw-genie multi toe --engine live --parallel 1
```
- `multi toe --engine live` はアカウントごとに live ジョブを出しますが、**CLI から Firefox のタブは切り替えられません**。`--parallel 1` で順番に進め、表示中のタブを該当アカウントにしてください。
- Joe は残り 1 体 249、VitaminD は壁 2 体、Champion は壁 3 体、The Best は 1 体。

## 7. Auto が効かない場合の調査ポイント

1.0.23 の Auto パッチ:
- `BattleController.prototype.start` をラップし、`this.Rr.Qya.ba(true)` を呼ぶ（`Rr` は BattleSettingsModel、`Qya` は Auto の BooleanProperty）。
- 形状が変わった場合は `liveAutoApplied=false` のまま 2.5s 後に座標クリックへフォールバック。
- それでもタイムアウトする場合:
  - `BattleController` が capturedClasses に居るか（`__hwGenieToe.capturedNames()`）。
  - `Rr.Qya` のフィールド名が v302 で変わっていないか（バンドル検索 / Orca eval）。
  - `TitanArenaBattleThread` を trap して controller を直接操作する案（現在は未実装）。
  - Auto の代わりに「時間切れまで待つ」は不可（180s で timeout）。

## 8. 既知の落とし穴

- **account の識別**: live ジョブの account は **`x-auth-user-id`**（Python が使う値）。ゲーム内 player id とは別。userscript は `localStorage["hwgenie-x-auth-user-id"]` を最優先で使う。
- **Orca の TM**: Chrome の「Allow User Scripts」が無いため TM は注入不可。Orca は **unpacked `dist/extension-dev`** を Settings → Browser → Extensions → Load unpacked で読み込む（研究用）。本番 live は Firefox TM。
- **結果ポップアップ**: 勝利/敗北の OK は合成クリックに反応しないことがある。live ジョブは結果送信後に **ページ再読込** で回避。
- **x5 ボタン**: 購入ポップアップ（Valkyrie's Favor）なので押さない。Auto のみ。
- **同一アカウントの並行実行**: CLI/実験を同時に走らせない。Turso/WAL・ToE ジョブ・ゲームタブの競合。
- **サーバー側の開始済みバトル**: live タイムアウト後、サーバー側のバトルが残る場合がある。次の StartBattle がエラーになったら（live driver は non-fatal として次チームへスキップ）時間切れを待つ。
- **認証切れ**: `hw-genie auth --list` と各ゲームタブの稼働を確認。必要なら `auth-server` / curl 更新。
- **レート**: `HWClient.sleep()` に従う。大量並列は避ける。

## 9. コード上の契約（次に触る人向け）

- live ジョブ:
  - `POST /toe/job` body: `{account, kind:"live", action:"battle", rivalId, titans:[5 ids]}`
  - `GET /toe/next?account=<x-auth-user-id>` → job（live は account 完全一致のみ）
  - `POST /toe/job/{id}/result` body: `{account, result:{ok, error?, result:{win, stars}, rivalId, action}}`
  - auth-server の live は in-flight 300s、sim は従来の 60s。
- userscript の live 実行:
  - `capturedClasses["GameModel"].F().player.cl`（ToE data）、`player.cl.bmc(rival, {f3b:()=>titans})`、`model.Ka.x7.kBo` をラップして EndBattle 応答を捕捉。
  - `WJl()` で status を再取得してから rival を探す（Python の CompleteTier 後に Tier が変わるため）。
  - 結果送信後 `location.reload()`（1.5s）。
- CLI live:
  - `run_live_tier` は status → 対象 rival → チームローテーション → 各 live job → status でスコア確認 → 全員 250 で CompleteTier。
  - `--max-attempts` = チーム数上限。エラーは fatal（account mismatch/rival not found/timeout/no engine）以外は次チームへ。

## 10. PR / 検証

- PR #212 は draft。live 実機検証後に ready へ。
- 必須検証: `uv run --locked ruff check .` / `uv run --locked pytest` / `cd src/userscripts && bun test tests/` / `bash build.sh --dev`。
- 直近: pytest 1555 passed、bun 154 pass、ruff clean。
- 実機 live は 1.0.23 の Auto パッチ検証が残っています。

## 11. 参考

- `docs/api/GUILD_API.md#titan-arena-toe`（v302 / proof / live）
- `.agents/skills/titan-arena/SKILL.md`（live モードの運用手順）
- `README.md`（Titan Arena のコマンド概要）
- Orca の MAIN-world 拡張: `src/userscripts/dist/extension-dev/manifest.json`（gitignore 対象。必要なら `dist/` を再生成）
- auth-server コンテナ: `hw-genie-auth-server-1`（compose が `./src/python/hw_genie` をマウント。コード変更は restart で反映）

---

**Orca/Opencode への依頼**: まずこの文書の「6. 次の手順」に従い、1.0.23-dev の Auto 検証（VitaminD）から再開してください。live はゲームタブを前面に保ち、Orca のゲームタブ/拡張は live 中は無効にしてください。
