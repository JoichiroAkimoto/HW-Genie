---
name: asgard-shop
description: HW-Genie を使用して、Asgard（ギルドレイド）の Realm Traveler ショップ（Osh 週）で Valor Emblem を使ったバフの自動購入を実行します。
---
# Asgardショッピング (HW-Genie 版)
## 概要
`clanRaid_getInfo` の `response.shop` から Valor Emblem 商品を読み、固定優先度に従って `clanRaid_shopBuy` でバフを購入します。

- **対象**: Osh 週のみ（buffId 61〜81 のラインナップで自動判定）。Maestro 週等は「未対応」としてスキップします。ショップが空（全 slot 買い切り済み）の場合もスキップで正常終了します。
- **優先度**: 優先度1 → 2 → 3 → 残りは価格昇順（同額は slot 昇順）。
  - 優先度1: slot 8, 17, 20, 12, 13, 19
  - 優先度2: slot 6, 10, 21, 18
  - 優先度3: slot 15, 16, 11
  - 優先度の定義・購入順の実装は `src/python/hw_genie/commands/asgard_shop.py` の `OSH_PRIORITY` / `build_buy_queue` がソースです（このファイルを変更した場合はそちらと同期してください）。
- **残高**: `coins`（Valor Emblem 残高）を追跡し、残高不足の商品は購入しません。購入失敗（NotEnough）時も以降をスキップします（両方併用）。残高はこのツールの連続購入内ではローカル減算で追跡します。
- ゴールドバフ（slot 1〜5）と購入済み（boughtCount >= buyLimit）は対象外です。

## ワークフロー
> **Tip**: 複数のアカウントを使用している場合は、事前に `uv run hw-genie auth --list` で正しいアカウント別名を確認することを推奨します。

1. 実行コマンド:
   ```bash
   # 単一アカウント
   uv run hw-genie asgard-shop -a <account>
   # 全アカウント並列実行
   uv run hw-genie multi asgard-shop
   ```
2. 購入前に計画のみ確認したい場合（購入は実行されません）:
   ```bash
   uv run hw-genie asgard-shop -a <account> --dry-run
   ```