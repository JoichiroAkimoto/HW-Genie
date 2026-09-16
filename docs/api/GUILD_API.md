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

## Titan Arena (ToE)

`titanArena*` 系の RPC はギルドレイド API とは別系統だが、プレイヤー個人の
PvP コンテンツとして同じく Hero Wars 対戦機能を共有する。本リポジトリの
HW-Genie CLI / userscript 連携はこれらを使って壁Rival 攻略や Tier 自動
進行を行う。

### titanArenaGetStatus - 現在の Tier / 壁 / rival 一覧を取得

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `status` | string | `disabled` / `peace_time` / `battle` のいずれか |
| `tier` | int | 現在のティア（1〜maxTier） |
| `maxTier` | int | 到達可能な最大ティア |
| `canRaid` | bool | `true` の場合 `titanArenaStartRaid` でまとめてライバルを一掃できる |
| `defenders` | object | 自分の防衛タイタン（HP / artifact 等） |
| `rivals` | object | ライバル一覧（`rivalId → {attackScore, titans, ...}`）。負の ID（例: `-470711`）は壁 / 練習用ライバル |

### titanArenaStartBattle - 個別ライバル戦を開始

`rivalId` と `titans`（5 体のタイタンID）を指定してバトルを開始する。`titans`
は `teamGetAll.titan_arena` の保存チームに依存せず、任意の 5 体を渡せる
（VitaminD アカウントで `-470711` / `[4003, 4023, 4004, 4001, 4000]`
の組み合わせが受理されることを実機検証済み）。

```json
{
  "calls": [{
    "name": "titanArenaStartBattle",
    "args": {"rivalId": "-470711", "titans": [4003, 4023, 4004, 4001, 4000]},
    "ident": "body"
  }]
}
```

レスポンスの `response.battle` を `titanArenaEndBattle` まで保持する
必要がある（`seed` 等を含めてサーバーが同一バトルとして検証する）。

### titanArenaEndBattle - 結果送信

`progress` と `result`（win / stars）が必要。**これらはクライアント側で
バトルをシミュレーションした正確な値**でないとサーバーが `Invalid battle`
（`serverVersion: 292`）で弾く。HW-Genie 単体では簡易な power ベース見積
のみ可能なため、**完全勝利には userscript 側の `Game.BattlePresets` /
`BattleInstantPlay`（`get_titanPvpManual`）を使うハイブリッドフローが必要**。

`hw-genie toe attack --engine estimate` は power ベースの `progress` を
送るため、サーバ検証で `Invalid battle` になる。`--engine hybrid` を指定
すると、auth server の `/toe/job` キュー経由で userscript が本物の
`BattleCalc` を呼び出し、その結果を `EndBattle` に利用する。

### titanArenaStartRaid / titanArenaEndRaid - 一括レイド（canRaid=true）

`canRaid` が `true` のとき、ライバルを 1 リクエストで全滅させるレイドが
可能。`titanArenaStartRaid` に `titans` だけを渡すとレスポンスの
`attackers` / `rivals` を受け取る。`rivals` の各 `rivalId` ごとに
`progress` / `result` を組み立て、`titanArenaEndRaid(results={rivalId: {progress, result}})`
でまとめて送信する。

### titanArenaCompleteTier - ティアクリア宣言

tier 内ライバルを全滅させた直後に呼ぶ。翌ティアへ進むか、`titanArenaEndRaid`
のレスポンスから自動で発火できる。

### titanArenaFarmDailyReward - 日次報酬受け取り

ティア完了後に 1 日 1 回だけ呼んで `titan_arena_points_reward` を獲得
する。

### HW-Genie での ToE 自動攻略

* `hw-genie toe status -a VitaminD` で現在の Tier / rivals / defenders を
  表示
* `hw-genie toe attack -a VitaminD` で未クリア 1 体を自動攻撃（`--rival`
  省略時は `titanArenaGetStatus` から `attackScore < 250` の rival を
  `(attackScore asc, wall優先, power asc)` で選択、`--titans` 省略時は
  `teamGetAll.titan_arena` から自動解決。`--dry-run` / `--estimate-only`
  で EndBattle を抑止）。明示指定も可能:
  `hw-genie toe attack -a VitaminD --rival -470711 --titans 4003 4023 4004 4001 4000 --dry-run`
* `hw-genie toe run -a VitaminD` で Tier 全体を自動攻略（`--titans`
  省略時は `teamGetAll.titan_arena` を自動解決。`canRaid=true` なら一括
  レイド、なければ `attackScore < threshold`（既定 250）の rivals を
  自動選択して個別バトル → `CompleteTier` → 日次報酬受け取り）。
  明示編成: `hw-genie toe run -a VitaminD --titans 4003 4023 4004 4001 4000`
  - `--threshold 250` で対象閾値を変更可能（`--stop-on-loss` で初回敗北時に中断）
  - `--engine estimate`（既定）: アプリ単体。勝敗は power ベースで
    簡易判定。完全勝利には `--engine hybrid` を指定
  - `--engine hybrid`: auth server 経由で userscript のバトルエンジン
    に依頼。ブラウザで `auth-server` が稼働 + `hw-genie-auth-capture.user.js`
    が Titan Arena 画面に入った状態で動作

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
