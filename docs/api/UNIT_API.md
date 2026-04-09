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
# session.jsonからヘッダーを自動取得してヒーロー情報を取得
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  $(cat session.json | jq -r '.headers | to_entries[] | "-H \(.key): \(.value)"' 2>/dev/null) \
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
# session.jsonからヘッダーを自動取得してタイタン情報を取得
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  $(cat session.json | jq -r '.headers | to_entries[] | "-H \(.key): \(.value)"' 2>/dev/null) \
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
# session.jsonからヘッダーを自動取得してペット情報を取得
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  $(cat session.json | jq -r '.headers | to_entries[] | "-H \(.key): \(.value)"' 2>/dev/null) \
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
# session.jsonからヘッダーを自動取得してインベントリ情報を取得
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  $(cat session.json | jq -r '.headers | to_entries[] | "-H \(.key): \(.value)"' 2>/dev/null) \
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

---

## その他のメソッド
*   **`heroRating_getInfo`**: ヒーローの評価・ランキング情報。
*   **`titanSpirit_getAll`**: タイタンの精霊（トーテム）の状況。
*   **`titanArtifactGetChest`**: タイタンアーティファクト宝箱の状況。
*   **`pet_getChest`**: ペットの宝箱の状況。
*   **`artifactGetChestLevel`**: アーティファクト宝箱のレベル。
*   **`roleAscension_getAll`**: ロール昇格（アセンション）の状況。
