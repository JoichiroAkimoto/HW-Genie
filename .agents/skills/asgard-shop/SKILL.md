---
name: asgard-shop
description: HW-Genie を使用して、Asgard（ギルドレイド）の Realm Traveler ショップ（Osh / Maestro 週）で Valor Emblem を使ったバフとゴールドバフの自動購入を実行します。
---
# Asgardショッピング (HW-Genie 版)
## 概要
`clanRaid_getInfo` の `response.shop` から Valor Emblem 商品を読み、週のラインナップに応じた購入ルールで `clanRaid_shopBuy` を実行します。ゴールドバフ（slot 1〜5）もデフォルトで購入します。

- **対象**: Osh 週（buffId 61〜81）と Maestro 週（buffId 112〜133）を自動判定。それ以外のラインナップやショップが空（全 slot 買い切り済み）の場合はスキップで正常終了します。
- **Osh 週の優先度**: 優先度1 → 2 → 3 → 残りは価格昇順（同額は slot 昇順）。
  - 優先度1: slot 8, 17, 20, 12, 13, 19
  - 優先度2: slot 6, 10, 21, 18
  - 優先度3: slot 15, 16, 11
  - 優先度の定義・購入順の実装は `src/python/hw_genie/commands/asgard_shop.py` の `OSH_PRIORITY` / `build_buy_queue` がソースです（このファイルを変更した場合はそちらと同期してください）。
- **Maestro 週の優先度**: バフは週替わりでローテーションするため、slot → 順位の固定優先度表（`MAESTRO_PRIORITY`）を使い、**組み合わせ最適化**（`select_maestro_plan`）で購入プランを選定します。
  - 順位 1〜6 が S（Unbridled Energy / At the Speed of Light / Pillar of Confidence / Effective Tactics / Secret Weapon / Strength in Perseverance）、7〜9 が A（At the Limit / Perfect Storm / Charmer's Skill）、10〜11 が B（Through a Prism / The Tireless）。C（それ以外）は購入対象外。
  - ルール: 残高を上限として、S → A → B の優先度を崩さず、同一優先度では高順位を優先し、コイン内で最も優先度の高いバフ構成になる組み合わせを購入（同構成なら安い方を優先して残コインを多くする）。「順位の高いバフ 1 個の確保」は「下位バフ複数」より優先。
  - 詳細仕様は `docs/superpowers/Maestro-buff.md` を参照。
- **ゴールドバフ**: slot 1〜5（100 万ゴールド、buyLimit 5）を残り購入回数分、Valor 商品より先に購入します。購入前に `fetch_player_status` で最新のゴールド残高を取得し、不足時はスキップします。`--no-gold` でオフにできます。
- **残高**: `coins`（Valor Emblem 残高）を追跡し、残高不足の商品は購入しません。購入失敗（NotEnough）時も以降をスキップします（両方併用）。残高はこのツールの連続購入内ではローカル減算で追跡します。
- 購入済み（boughtCount >= buyLimit）は対象外です。

## ワークフロー
> **Tip**: 複数のアカウントを使用している場合は、事前に `uv run hw-genie auth --list` で正しいアカウント別名を確認することを推奨します。

1. 実行コマンド:
   ```bash
   # 単一アカウント
   uv run hw-genie asgard-shop -a <account>
   # 全アカウント並列実行
   uv run hw-genie multi asgard-shop
   # ゴールドバフを購入しない場合
   uv run hw-genie asgard-shop -a <account> --no-gold
   ```
2. 購入前に計画のみ確認したい場合（購入は実行されません）:
   ```bash
   uv run hw-genie asgard-shop -a <account> --dry-run
   ```