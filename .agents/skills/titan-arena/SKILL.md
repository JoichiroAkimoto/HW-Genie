---
name: titan-arena
description: HW-Genie を使用して Titan Arena (ToE) の状態確認・個別攻撃・Tier 攻略を実行します。rival 自動解決、Raid 優先、負けバンク、dummy 連続停止に対応します。
---
# Titan Arena (ToE) Skill (HW-Genie 版)

## ワークフロー
> **Tip**: ToE は userscript ブリッジを使うため、Dev 版 userscript を入れたゲームタブ（1アカウント1タブ）と `auth-server` が必要です。

1. **状態確認（読み取りのみ）**:
   ```bash
   uv run hw-genie toe status -a <アカウント名>
   ```
2. **単発バトル**:
   ```bash
   uv run hw-genie toe attack -a <アカウント名> [--rival ID] [--titans 5個] [--dry-run]
   ```
3. **Tier 攻略（Raid → 個別バトル）**:
   ```bash
   uv run hw-genie toe run -a <アカウント名> [--titans 5個] [--threshold 250]
   ```
   v302 以降の個別戦・壁は**ライブ実戦**で攻略します（ゲームタブを前面に保つ）:
   ```bash
   uv run hw-genie toe run -a <アカウント名> --engine live [--max-attempts N] [--rounds R]
   ```
   `--rounds R` は rival が閾値未満の間、チームローテーション全体を最大 R 周繰り返します
   （attackScore は max 保持・各戦は seed 再抽選なので追加周回は無害。249 のような
   あと 1 点の壁を粘り強く押し上げる用途）。`--max-attempts` は 1 周あたりのチーム数上限。
4. **全アカウント一括**:
   ```bash
   uv run hw-genie multi toe [--engine hybrid|live] [--parallel N] [--rounds R]
   ```

## 挙動と注意

- **実験的機能・開発途中**: v302 以降の個別戦/壁（`--engine live`）はゲームクライアントの実戦に依存し、ゲーム更新ごとに minify 名の再ピンが必要です。**API 単独では個別戦/壁を攻略できない恒久的な制約**があり、ToE はまだ完成したソリューションではありません（レイド `--engine hybrid` は API で安定）。運用時はこの前提を忘れないでください。
- `--rival` / `--titans` 省略時は `titanArenaGetStatus` / `teamGetAll.titan_arena` から自動解決します。閾値は既定 250。
- 新 Tier は Raid を最初に試し、レイドが検証可能な結果を返せない場合は個別バトルへフォールバックします。
- 負けは既定で毎回バンクします（`attackScore` はバトルスコアの max 保持なので劣る負けは無害）。`--no-end-on-loss` でベスト更新時のみバンク、`--no-bank-best-loss` で改善バンクを無効化できます。
- userscript ブリッジの自動フォールバックは完了シグナルの Haxe ordinal ずれだけを吸収します。battle config / DataStorage getter のずれ（`get_titanPvpManual` → `get_titanClanPvp` 等）は自動吸収されないため、ゲーム更新時は Dev 版 userscript の更新が必要です。それでも戦闘計算が連続 3 回ダミー（空 progress）なら原因を表示して停止します。
- `EndBattle: Invalid battle` が続く場合、ブリッジ未接続（auth-server / ゲームタブ）に加えて **ゲーム更新によるブリッジの設定ずれ**（battle config / ordinal）が原因です。`bash src/userscripts/build.sh --dev` で Dev 版 userscript を最新ビルドへ更新し、**`src/userscripts/dist/hw-genie-auth-capture-dev.user.js` を userscript マネージャへ再登録（上書きインストール）**してから、**ゲームタブをリロード**して再実行してください（Dev 版 userscript はローカルビルドの `@updateURL` が未解決プレースホルダ（`__UPDATE_URL__`）のため自動更新されず、上書きインストールが必要です。またクラスをバンドル起動時にしか捕捉しないため、インストール後のリロードが必須）。2026-11-26 の serverVersion 302 では ToE の config が `get_titanClanPvp` に変わっています。
- **2026-11-26 以降の個別戦・壁（serverVersion 302）**: `effects.ultReseed` の reseed proof（`battleSeed_next` の seeds / `progress[0].proof`）をサーバーが要求するため、オフライン計算の `Invalid battle` は解消しません（レイド戦は `effects: []` のため対象外）。**`--engine live` を使う**と、ゲームタブ内で `PlayerTitanArenaData.cmc`（旧 `bmc`）による実戦を開始し、クライアントが proof 付きで EndBattle（コマンド `DBo`、旧 `kBo`）を送信します（Auto は userscript が `BattleSettingsModel` 経由で有効化。結果ポップアップはゲームに任せ、**Tier 移行時だけ再読込**します）。live は **Dev userscript 1.0.38+** と、**対象アカウントのゲームタブを前面（描画中）に保つ**ことが必要です（sim を含む全 ToE ジョブが対象。可視タブがないと userscript は claim しません）。`canRaid=true` の Tier は live では攻略できません。先に `--engine hybrid` でレイドを消化し、残った壁を live で再実行してください（この停止は想定内で exit code は 0、`action required` として表示されます）。`multi toe --engine live` は CLI からタブを切り替えられないため、基本は `--parallel 1`。**各アカウントを別ウィンドウで同時に表示（最小化しない）できるなら `--parallel N` で並列化**できます（バックグラウンドタブは rAF が止まり実戦が進まないので、同一ウィンドウのタブ切替では並列になりません）。`--rounds R` で全候補の周回を自動化できます。HWH/Goodwin は併用可（早回しに利用可）。なお Titan Arena の **x5 ボタンは購入ポップアップ（Valkyrie's Favor）なので押さない**（Auto のみ）。
- live ジョブが `rival not found in ToE model (want=... have=...)` で失敗した場合、ゲームタブのコンソールで `localStorage.getItem("hwgenie-toe-diag")` を実行すると直近の診断（`want` / `have`（最大12件）/ `commandKey`（`y7` / `x7`）/ `cmc` / `DBo` / `W5b` / `mKl` / `Vge` / `Dtd` の有無、可視状態、クエリを除いた URL）を確認できます。userscript の更新後はタブをリロードしてください。
- `multi toe` は `--dry-run` 非対応です。単発の `toe attack --dry-run` を使います。
- 実在のプレイヤー名・アカウント名は書かないでください（AGENTS.md の「不変条件」）。例は `Alice` / `Bob` を使います。

## 詳細
- API・フラグ・検証結果: [docs/api/GUILD_API.md#titan-arena-toe](../../../docs/api/GUILD_API.md#titan-arena-toe)
- 全体のコマンド概要: [README.md](../../../README.md)
