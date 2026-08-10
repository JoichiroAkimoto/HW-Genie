---
layout: page
title: Unit API Reference
---

# Unit API Reference

## 概要
所持しているヒーロー、タイタン、ペット、およびそれらの育成データ（ステータス、スキル、スキン等）を取得するメソッドです。

## Endpoint
```
POST https://heroes-wb.nextersglobal.com/api/
Content-Type: application/json; charset=UTF-8
```

---

## heroGetAll - 全ヒーロー情報取得

所持している全ヒーローのステータス、レベル、ランク、スキル、スキン、グリフ、アーティファクト等の詳細を取得します。

### curl 実行例
```bash
# 認証ヘッダー (x-auth-*) はブラウザ DevTools の「Copy as cURL」で取得した curl に含まれる
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"heroGetAll","args":{},"context":{"actionTs":1},"ident":"heroes"}]}'
```

### Response Structure
```json
{
  "results": [{
    "ident": "heroes",
    "result": {
      "response": {
        "2": {
          "id": 2,
          "xp": 3625195,
          "level": 130,
          "color": 16,
          "power": 74216,
          "star": 6,
          "skills": {
            "426": 130,
            "427": 130
          },
          "skins": {
            "2": 21,
            "32": 19
          }
        }
      }
    }
  }]
}
```

---

## titanGetAll - 全タイタン情報取得

所持している全タイタンのステータス、レベル、星ランク等の情報を取得します。

### curl 実行例
```bash
# 認証ヘッダー (x-auth-*) はブラウザ DevTools の「Copy as cURL」で取得した curl に含まれる
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"titanGetAll","args":{},"context":{"actionTs":1},"ident":"titans"}]}'
```

### Response Structure
```json
{
  "results": [{
    "ident": "titans",
    "result": {
      "response": {
        "4000": {
          "id": 4000,
          "level": 130,
          "power": 215197,
          "star": 6
        }
      }
    }
  }]
}
```

---

## pet_getAll - 全ペット情報取得

所持している全ペットのステータス、レベル、星ランク等の情報を取得します。

### curl 実行例
```bash
# 認証ヘッダー (x-auth-*) はブラウザ DevTools の「Copy as cURL」で取得した curl に含まれる
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"pet_getAll","args":{},"context":{"actionTs":1},"ident":"pets"}]}'
```

### Response Structure
```json
{
  "results": [{
    "ident": "pets",
    "result": {
      "response": {
        "pets": [
          {
            "id": 6001,
            "level": 130,
            "power": 163935,
            "star": 6
          }
        ]
      }
    }
  }]
}
```

---

## inventoryGet - インベントリ/所持品

所持している全アイテム（装備、ソウルストーン、ポーション、ルーン等）のリストを取得します。

### curl 実行例
```bash
# 認証ヘッダー (x-auth-*) はブラウザ DevTools の「Copy as cURL」で取得した curl に含まれる
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"inventoryGet","args":{},"context":{"actionTs":1},"ident":"inventory"}]}'
```

### Response Structure
```json
{
  "results": [{
    "ident": "inventory",
    "result": {
      "response": {
        "consumable": { "24": 1247415 },
        "gear": { "4": 866 }
      }
    }
  }]
}
```

`response` の各キーがカテゴリ（`consumable` / `gear` / `scroll` / `fragmentScroll` /
`fragmentGear` / `fragmentHero` / `ascensionGear` 等、実測で 14 種）、その値が
`{libId: 個数}` のマップです。`consumable` の libId は消費アイテムを表し、
事前定義された名前・消費方法は `core/consumables.py` のレジストリを参照してください。

---

## consumableUseLootBox - ルートボックス（Loot Box）消費

consumable（`inventoryGet` の `response.consumable`）のうち、ルートボックス
種別のアイテムをまとめて開封します。レジストリ登録済み libId の一括消費は
`hw-genie consumable run` で行えます。

### curl 実行例
```bash
# libId 215（Equipment Fragment Chest）を 48 個消費
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"consumableUseLootBox","args":{"libId":215,"amount":48},"context":{"actionTs":1750526},"ident":"body"}]}'
```

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "48": {
          "fragmentScroll": { "218": 5, "192": 10, "193": 15, "216": 5 },
          "fragmentGear": { "91": 10, "93": 10, "171": 5, "94": 5 }
        }
      }
    }
  }]
}
```

`response` のキーは**消費した数量**（例: `"48"`）で、その値のカテゴリ
（`fragmentScroll` 等）ごとに `{libId: 報酬量}` として報酬が返ります。
`hw-genie` ではこのカテゴリごとの合計を `ConsumableUseResult.rewards` に集計します。

他の consumable 種別（`consumableUseStamina` 等）はアイテムごとにメソッドが
異なるため、実測で判明したものだけ `core/consumables.py` のレジストリに
登録しています。未登録アイテムは `consumable run --method <method>` で
明示指定できます。

---

## その他のメソッド
*   **`heroRating_getInfo`**: ヒーローの評価・ランキング情報。
*   **`titanSpirit_getAll`**: タイタンの精霊（トーテム）の状況。
*   **`titanArtifactGetChest`**: タイタンアーティファクト宝箱の状況。
*   **`pet_getChest`**: ペットの宝箱の状況。
*   **`artifactGetChestLevel`**: アーティファクト宝箱のレベル。
*   **`roleAscension_getAll`**: ロール昇格（アセンション）の状況。
