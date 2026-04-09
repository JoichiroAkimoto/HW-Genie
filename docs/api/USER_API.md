# User API Reference

ユーザーアカウント、基本設定、サーバー時刻、およびアバターなどの装飾アイテムに関するAPIリファレンスです。

## Endpoint
```
POST https://heroes-wb.nextersglobal.com/api/
Content-Type: application/json; charset=UTF-8
```

---

## userGetInfo - ユーザー基本情報

プレイヤーの名前、レベル、所持リソース、スタミナ等の基本情報を取得します。

### Request Arguments
なし (`{}`)

### curl 実行例
```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'content-type: application/json; charset=UTF-8' \
  $(cat session.json | jq -r '.headers | to_entries[] | "-H \(.key): \(.value)"') \
  --data-raw '{"calls":[{"name":"userGetInfo","args":{},"ident":"user"}]}'
```

### Response Structure (主要フィールド)
```json
{
  "results": [{
    "ident": "user",
    "result": {
      "response": {
        "id": "61405391",
        "name": "xxx",
        "level": "130",
        "gold": 332083406,
        "starMoney": 384649,
        "refillable": [
          {
            "id": 1,
            "amount": 38,
            "boughtToday": 7
          }
        ],
        "avatarId": "1159"
      }
    }
  }]
}
```

### 主要フィールド説明
| フィールド | 型 | 説明 |
|-----------|-----|------|
| `id` | string | プレイヤーID |
| `name` | string | プレイヤー名 |
| `level` | string | プレイヤーレベル |
| `gold` | int | 所持ゴールド |
| `starMoney` | int | 所持エメラルド |
| `refillable[0]` | object | スタミナ情報 (id: 1) |
| `avatarId` | string | 現在使用中のアバターID |

---

## getTime - サーバー時刻取得

サーバーの現在時刻（Unixタイムスタンプ）を取得します。

### Request Arguments
なし (`{}`)

### Response Structure
```json
{
  "results": [{
    "ident": "time",
    "result": {
      "response": 1775726926
    }
  }]
}
```

---

## settingsGetAll - 設定全般

ゲーム内の各種設定を取得します。

### Request Arguments
なし (`{}`)

---

## subscriptionGetInfo - サブスクリプション状況

サブスクリプション（「ヴァルキリーの寵愛」等）のステータスを取得します。

### Request Arguments
なし (`{}`)

---

## tutorialGetInfo - チュートリアル進行度

チュートリアルの完了状況を取得します。

### Request Arguments
なし (`{}`)

---

## userGetAvailableAvatars - 利用可能なアバター

プレイヤーが所持しており、変更可能なアバターのリストを取得します。

### Request Arguments
なし (`{}`)

### Response Structure
```json
{
  "results": [{
    "ident": "avatars",
    "result": {
      "response": [1, 10, 100]
    }
  }]
}
```

---

## userGetAvailableAvatarFrames - 利用可能なフレーム

プレイヤーが所持しているアバターフレームのリストを取得します。

### Request Arguments
なし (`{}`)

---

## userGetAvailableStickers - 利用可能なスタンプ

チャットで使用可能なスタンプのリストを取得します。

### Request Arguments
なし (`{}`)

### Response Structure
```json
{
  "results": [{
    "ident": "stickers",
    "result": {
      "response": [1, 2, 3]
    }
  }]
}
```
