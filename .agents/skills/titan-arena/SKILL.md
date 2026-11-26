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
   uv run hw-genie toe run -a <アカウント名> --engine live [--max-attempts N]
   ```
4. **全アカウント一括**:
   ```bash
   uv run hw-genie multi toe [--engine hybrid|live] [--parallel 1]
   ```

## 挙動と注意
- `--rival` / `--titans` 省略時は `titanArenaGetStatus` / `teamGetAll.titan_arena` から自動解決します。閾値は既定 250。
- 新 Tier は Raid を最初に試し、レイドが検証可能な結果を返せない場合は個別バトルへフォールバックします。
- 負けは既定で毎回バンクします（`attackScore` はバトルスコアの max 保持なので劣る負けは無害）。`--no-end-on-loss` でベスト更新時のみバンク、`--no-bank-best-loss` で改善バンクを無効化できます。
- userscript ブリッジの自動フォールバックは完了シグナルの Haxe ordinal ずれだけを吸収します。battle config / DataStorage getter のずれ（`get_titanPvpManual` → `get_titanClanPvp` 等）は自動吸収されないため、ゲーム更新時は Dev 版 userscript の更新が必要です。それでも戦闘計算が連続 3 回ダミー（空 progress）なら原因を表示して停止します。
- `EndBattle: Invalid battle` が続く場合、ブリッジ未接続（auth-server / ゲームタブ）に加えて **ゲーム更新によるブリッジの設定ずれ**（battle config / ordinal）が原因です。`bash src/userscripts/build.sh --dev` で Dev 版 userscript を最新ビルドへ更新し、**`src/userscripts/dist/hw-genie-auth-capture-dev.user.js` を userscript マネージャへ再登録（上書きインストール）**してから、**ゲームタブをリロード**して再実行してください（Dev 版 userscript はローカルビルドの `@updateURL` が未解決プレースホルダ（`__UPDATE_URL__`）のため自動更新されず、上書きインストールが必要です。またクラスをバンドル起動時にしか捕捉しないため、インストール後のリロードが必須）。2026-11-26 の serverVersion 302 では ToE の config が `get_titanClanPvp` に変わっています。
- **2026-11-26 以降の個別戦・壁（serverVersion 302）**: `effects.ultReseed` の reseed proof（`battleSeed_next` の seeds / `progress[0].proof`）をサーバーが要求するため、オフライン計算の `Invalid battle` は解消しません（レイド戦は `effects: []` のため対象外）。**`--engine live` を使う**と、ゲームタブ内で `PlayerTitanArenaData.bmc` による実戦を開始し、クライアントが proof 付きで EndBattle を送信します（Auto 有効化・結果ポップアップ回避の再読込は userscript が行う）。live は **Dev userscript 1.0.22+** と、**対象アカウントのゲームタブを前面（描画中）に保つ**ことが必要です。`multi toe --engine live` は `--parallel 1` を推奨（CLI からタブを切り替えられないため、アカウントごとにタブを前面にしてください）。HWH/Goodwin は併用可（早回しに利用可）。
- `multi toe` は `--dry-run` 非対応です。単発の `toe attack --dry-run` を使います。
- 実在のプレイヤー名・アカウント名は書かないでください（AGENTS.md の「不変条件」）。例は `Alice` / `Bob` を使います。

## 詳細
- API・フラグ・検証結果: [docs/api/GUILD_API.md#titan-arena-toe](../../../docs/api/GUILD_API.md#titan-arena-toe)
- 全体のコマンド概要: [README.md](../../../README.md)
