---
layout: page
title: Guild API Reference
---

# Guild API Reference

## 概要
所属クラン（ギルド）の情報、メンバー活動状況、ギルド戦（GW）、クロスサーバーギルド戦（CoW）、レイド（Asgard）、およびPvP（アリーナ等）のステータスを取得します。

## 主要メソッド

### clanGetInfo - クラン基本情報
所属クランの基本情報、メンバーリスト、現在のステータス等を取得します。

### Endpoint
```
POST https://heroes-wb.nextersglobal.com/api/
Content-Type: application/json; charset=UTF-8
```

### Request Arguments

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| なし | - | - | - |

### curl 実行例
```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"clanGetInfo","args":{},"ident":"body"}]}'
```

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "clan": {
          "id": "123456",
          "title": "Clan Name",
          "level": "1",
          "members": {
            "12345678": {
              "id": "12345678",
              "name": "PlayerName",
              "level": "130",
              "clanRole": "4"
            }
          }
        }
      }
    }
  }]
}
```

### clan{} フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `id` | string | クランID |
| `title` | string | クラン名 |
| `level` | string | クランレベル |
| `members` | object | メンバー一覧（キーはプレイヤーID） |

---

### clanWarGetBriefInfo - ギルド戦 (GW) の概要
現在のギルド戦（ゴールドリーグ等）の進行状況、対戦相手、スコアなどの概要を取得します。

### Endpoint
```
POST https://heroes-wb.nextersglobal.com/api/
```

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "tries": 0,
        "targets": 0,
        "nextWarTime": 1775811600,
        "hasActiveWar": true
      }
    }
  }]
}
```

### Response フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `tries` | int | 残り攻撃回数 |
| `targets` | int | 攻撃可能なターゲット数 |
| `nextWarTime` | int | 次回の戦争開始予定時間（Unixタイムスタンプ） |
| `hasActiveWar` | bool | 現在戦争中かどうか |

---

### crossClanWar_getBriefInfo - クロスサーバーギルド戦 (CoW) の概要
クロスサーバーギルド戦（CoW）の概要、マッチング状況、順位などを取得します。

### Endpoint
```
POST https://heroes-wb.nextersglobal.com/api/
```

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "status": "active",
        "hasActiveWar": false,
        "heroTries": 0,
        "titanTries": 0
      }
    }
  }]
}
```

### Response フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `status` | string | ステータス（例: "active"） |
| `hasActiveWar` | bool | 現在戦争中かどうか |
| `heroTries` | int | ヒーロー戦残り攻撃回数 |
| `titanTries` | int | タイタン戦残り攻撃回数 |

---

### clanRaid_getInfo - クランレイド情報
アスガルド（クランレイド）の進行状況、ボスのHP、攻撃回数などを取得します。

### Endpoint
```
POST https://heroes-wb.nextersglobal.com/api/
```

### Request Arguments

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| なし | - | - | - |

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "boss": { "teams": [...] },
        "shop": {
          "6": {
            "branch": "",
            "buffId": 66,
            "buffValue": 3,
            "buyLimit": 1,
            "cost": { "coin": { "30": 50 } },
            "rank": 3,
            "requirement": "",
            "boughtCount": 0
          }
        },
        "coins": 1000
      }
    }
  }]
}
```
> ※ 構造が複雑なため、解析の際は `jq` 等で内容を確認してください。

### Response フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `boss` | object | ボスフェーズの情報（`teams` にボスのユニット構成） |
| `nodes` | dict | ミニオンフェーズのノード情報 |
| `shop` | dict | ショップ在庫（キーは slotId → 商品詳細） |
| `coins` | int | 所持コイン数（Osh の Realm Traveler では Valor Emblem） |
| `buffs` | dict | `buffId → {id, value}` のバフ定義 |
| `stats.currentBoss` | string | 現在のボス ID |
| `stats.weekStart` | string | 週の開始タイムスタンプ |

### shop{} フィールド（slot 単位）

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `buffId` | int | バフ ID（Osh の Realm Traveler は 61〜81、Maestro の Phantom Orchestra は 112〜133 で固定） |
| `buffValue` | int | バフ効果量（% 表示用） |
| `buyLimit` | int | 購入上限（Valor Emblem 商品は 1） |
| `cost` | object | 価格。`{"coin": {"30": 価格}}` が Valor Emblem、`{"gold": ...}` がゴールドバフ |
| `rank` | int | レアリティ（1=150 / 2=100 / 3=50 の目安） |
| `boughtCount` | int | 購入済み回数（>= buyLimit で購入済み） |
| `requirement` / `branch` | string | 条件・ブランチ（現在は空文字） |

> **Osh 週のラインナップ**: slot 1〜5 はゴールドバフ（`cost.gold` 100万、buyLimit 5）、
> slot 6〜21 が Valor Emblem 商品（価格は週替わりで 50/100/150 のいずれか）。
> ラインナップ（slot→buffId）は全アカウント共通で固定、`boughtCount` と `coins` はアカウント別。
>
> **Maestro 週のラインナップ**: slot 構成・価格帯は Osh と同じ（slot 1〜5 はゴールドバフ、
> slot 6〜21 が Valor Emblem 商品）だが、buffId の範囲は 112〜133 で、slot→バフの対応は
> 週ごとに変わる場合がある（`hw-genie asgard-shop` は確認済みラインナップに基づく
> slot→順位の固定優先度表 `MAESTRO_PRIORITY` と組み合わせ最適化で購入プランを選定する。
> 週が変わった際は表の更新が必要）。
>
> 2026-08 実測の slot → バフ名（価格, buffId）:
>
> | slot | バフ名 | 価格 | buffId |
> |------|--------|------|--------|
> | 11 | Unbridled Energy | 100 | 118 |
> | 15 | At the Speed of Light | 50 | 125 |
> | 9 | Pillar of Confidence | 50 | 116 |
> | 7 | Effective Tactics | 150 | 114 |
> | 17 | Secret Weapon | 150 | 128 |
> | 16 | Strength in Perseverance | 150 | 127 |
> | 14 | At the Limit | 50 | 122 |
> | 12 | Perfect Storm | 100 | 119 |
> | 10 | Charmer's Skill | 100 | 117 |
> | 19 | Through a Prism | 50 | 133 |
> | 8 | The Tireless | 50 | 115 |
> | 6 / 13 / 18 / 21 | （バフ名不明・購入対象外） | 150 / 100 / 50 / 100 | 112 / 120 / 121 / 132 |
> | 1〜5 | ゴールドバフ（バフ名不明） | 100 万ゴールド | 113 / 123 / 126 / 129 / 130 |

---

### clanRaid_shopBuy - クランレイドショップ購入
Osh の Realm Traveler 等、クランレイドのショップでアイテム（バフ）を購入します。

### Request Arguments

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `slotId` | int | ✓ | 購入するスロット ID（`clanRaid_getInfo` の `shop` キーと対応） |

### curl 実行例
```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"clanRaid_shopBuy","args":{"slotId":17},"context":{"actionTs":3247542},"ident":"body"}]}'
```

---

## 補足メソッド
*(その他のメソッドは必要に応じて追加してください)*
*   **`clan_prestigeGetInfo`**: クランの威信（Prestige）レベルと進行状況。
*   **`clanGetActivityRewardTable`**: クラン活動報酬テーブル。
*   **`clanGetPrevData`**: クランの過去データ。
*   **`clanWarGetWarlordInfo`**: ギルド戦におけるウォーロード（軍師）の配置や設定。
*   **`crossClanWar_getSettings`**: クロスサーバーギルド戦の設定やルール。
*   **`titanArenaGetChestReward`**: タイタンアリーナの宝箱報酬。
