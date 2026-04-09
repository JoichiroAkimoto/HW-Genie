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
  $(cat session.json | jq -r '.headers | to_entries[] | "-H \(.key): \(.value)"') \
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

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "boss": {
          "teams": [...]
        }
      }
    }
  }]
}
```
> ※ 構造が複雑なため、解析の際は `jq` 等で内容を確認してください。

---

## 補足メソッド
*(その他のメソッドは必要に応じて追加してください)*
*   **`clan_prestigeGetInfo`**: クランの威信（Prestige）レベルと進行状況。
*   **`clanGetActivityRewardTable`**: クラン活動報酬テーブル。
*   **`clanGetPrevData`**: クランの過去データ。
*   **`clanWarGetWarlordInfo`**: ギルド戦におけるウォーロード（軍師）の配置や設定。
*   **`crossClanWar_getSettings`**: クロスサーバーギルド戦の設定やルール。
*   **`titanArenaGetChestReward`**: タイタンアリーナの宝箱報酬。
