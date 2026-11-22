---
name: asgard-raid
description: HW-Genie を使用して、Asgard（ギルドレイド）のボス戦（Osh / Maestro 週）でアカウントごとに登録した最大5チームを自動実行します。
---
# Asgard ボス戦 (HW-Genie 版)

## 概要

`clanRaid_startBossBattle` / `clanRaid_endBossBattle` を使って Asgard のボス戦を自動化します。チームはアカウント × ボス（`osh` / `maestro`）× slot（1〜5）ごとに専用テーブル `asgard_raid_teams` へ保存します。週は `clanRaid_getInfo` の `shop` に含まれる buffId シグネチャ（Osh: 61〜81 / Maestro: 112〜133）で自動判定します。shop が空（全 slot 購入済み等）の場合は、同じ週の buffId を持つ `buffs` をフォールバックに使います。

- **チーム登録**: `team set <1-5> --boss osh|maestro --heroes <5体> [--pet N] [--favor HERO=PATRON ...] [--disabled] [--memo TEXT]`。`enabled` はチームごとでデフォルト `true`。`--disabled` を付けたチームは実行時にスキップされます。
- **実行**: `run` は週を自動判定し、`enabled=true` のチームを slot 昇順で実行します。各チームは次の2コールです。
  1. `clanRaid_startBossBattle`（`heroes` / `pet` / `favor`）— この時点で `bossAttempts` が1減ります。
  2. `clanRaid_endBossBattle` に **空の `progress`** を送信（`args: {"result":{"win":false,"stars":0},"progress":[]}`）。サーバーはこれをクライアント側シミュレーションの不備とみなし、**サーバー側オートバトル**で結果を確定します（レスポンスの `result.afterInvalid: true`）。実機検証済み（serverVersion 300, 2026-11-17）。
- **重要**: `start` 後に `end` を送らないと、`clanRaid_getInfo` を何度読んでも `userStats.damage` は増えず、攻撃だけが消費されたままになります。必ず `end`（空 progress）を送ってください。
- **スコア表示**: `end` レスポンスの `result.damage`（defender ごとの damage）を合計し、チーム別 damage / 合計 damage / `bossAttempts` の前後 / `userStats.damage`・`points` の前後を表示します。
- **停止条件**: 残り `bossAttempts` が 0、`start` 失敗、または `end` 失敗で残りチームを停止します（認証切れは `HWAuthError` として上位へ）。
- **`--dry-run`**: 週判定と実行予定チームの表示のみ（攻撃しません）。
- **`multi asgard-raid`**: 全アカウントで実行します。攻撃回数を消費するため **`--yes` 必須**（`--dry-run` は `--yes` 不要）。

## ワークフロー

> **Tip**: 複数アカウントを使う場合は `uv run hw-genie auth --list` でアカウント別名を確認してください。

1. チームを登録（例: Maestro の slot 1）:
   ```bash
   uv run hw-genie asgard-raid team set 1 \
     --boss maestro -a <account> \
     --heroes 50 60 58 51 72 --pet 6005 \
     --favor 50=6005 --favor 51=6001 --favor 58=6008 --favor 60=6006 --favor 72=6002 \
     --memo "template"
   ```
2. 登録内容を確認:
   ```bash
   uv run hw-genie asgard-raid team list -a <account>
   uv run hw-genie asgard-raid team list -a <account> --boss maestro
   ```
3. 実行前に計画だけ確認（攻撃なし）:
   ```bash
   uv run hw-genie asgard-raid run -a <account> --dry-run
   ```
4. 実行:
   ```bash
   uv run hw-genie asgard-raid run -a <account>
   # 週の自動判定が外れる場合のみ明示
   uv run hw-genie asgard-raid run -a <account> --boss osh
   ```
5. 全アカウント一括（攻撃回数を消費するため `--yes` 必須）:
   ```bash
   uv run hw-genie multi asgard-raid --dry-run
   uv run hw-genie multi asgard-raid --yes
   ```
6. チームの削除:
   ```bash
   uv run hw-genie asgard-raid team remove 1 --boss maestro -a <account>
   ```

## 注意

- チームはボスごとに独立です。Osh 週と Maestro 週で同じ slot を使っても上書きされません。
- `team set` は同一 `(account, boss, slot)` を上書きします。登録し直す際は `--disabled` / `--memo` もその回の指定が正になります。
- `--favor` の hero は `--heroes` に含まれる必要があります。hero は5体ちょうど・重複不可です。
- 攻撃回数（`bossAttempts`）は `start` 時点で消費されます。`end` が失敗した場合はその回が未確定のまま残る可能性があるため、次回実行前にゲーム側で状態を確認してください。
