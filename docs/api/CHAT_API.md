# Chat API Reference

## chatGetAll - チャット履歴取得

指定したchatTypeのチャット履歴を取得します。

### Endpoint
```
POST https://heroes-wb.nextersglobal.com/api/
Content-Type: application/json; charset=UTF-8
```

### Request Arguments

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `chatType` | string | Yes | チャットタイプ（後述） |
| `count` | int | No | 取得件数（デフォルト: 50） |
| `lastId` | string | No | ページネーション用（特定のID以前のメッセージ取得） |

### chatType 早見表

| chatType | 用途 |
|----------|------|
| `clan` | 所属ギルドのメンバー間チャット |
| `training` | トレーニングマッチ（PvP訓練）の相手とのチャット |
| `xgvg` | クロスサーバーギルド戦（XvX）|matching相手とのチャット |
| `server` | サーバー全体向けグローバルチャット（運営公告等） |

### curl 実行例

```bash
# 認証ヘッダー (x-auth-*) はブラウザ DevTools の「Copy as cURL」で取得した curl に含まれる
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"chatGetAll","args":{"chatType":"clan"},"context":{"actionTs":1},"ident":"chat"}]}'
```

### Response Structure

```json
{
  "results": [{
    "ident": "chat",
    "result": {
      "response": {
        "chat": [
          {
            "id": "144249176",
            "userId": "26754937",
            "messageType": "text",
            "ctime": "1775667274",
            "data": {
              "ids": [],
              "text": "メッセージ内容"
            }
          },
          {
            "id": "144192792",
            "userId": "62081929",
            "messageType": "sticker",
            "ctime": "1775459356",
            "data": {
              "ids": [],
              "stickerId": 13
            }
          }
        ],
        "users": {
          "26754937": {
            "id": "26754937",
            "name": "PlayerName",
            "level": "130",
            "clanRole": "4",
            "avatarId": "1617"
          }
        }
      }
    }
  }]
}
```

### chat[] フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `id` | string | メッセージ一意識別子 |
| `userId` | string | 送信者のプレイヤーID |
| `messageType` | string | `text` または `sticker` |
| `ctime` | string | Unixタイムスタンプ（秒） |
| `data.text` | string | メッセージ本文（messageType=text時） |
| `data.stickerId` | int | スタンプID（messageType=sticker時） |

### users{} フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `id` | string | プレイヤーID |
| `name` | string | プレイヤー名 |
| `level` | string | レベル |
| `clanRole` | string | ギルド役職（255=Leader, 4=Member） |
| `avatarId` | string | アバターID |
| `lastLoginTime` | string | 最終ログイン（Unixタイムスタンプ） |

---

## chatServerSubscribe - リアルタイムチャット購読

リアルタイムで新しいチャットメッセージを受信するための購読を開始します。
WebSocketまたはロングポーリング используется。

> 詳細未確認

---

## userGetAvailableStickers - スタンプ一覧

自身が使用可能なスタンプ（絵文字/ステッカー）のリストを取得します。

### Response Structure

```json
{
  "results": [{
    "ident": "stickers",
    "result": {
      "response": {
        "stickers": [
          {
            "id": 1,
            "name": "thumbs_up",
            "url": "..."
          }
        ]
      }
    }
  }]
}
```

---

## Tips: jq で整形表示

### 時刻・ユーザー名・メッセージを表形式にする

```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"chatGetAll","args":{"chatType":"clan"},"context":{"actionTs":1},"ident":"chat"}]}' \
| jq -r '
  .results[0].result.response as $resp |
  $resp.chat | sort_by(.ctime | tonumber) | .[] as $msg |
  ($msg.userId | tostring) as $uid |
  ($resp.users[$uid].name // $uid) as $name |
  "\($msg.ctime | tonumber | strftime("%m/%d %H:%M")) | \($name) | \($msg.data.text // "sticker:\($msg.data.stickerId)")"
'
```

出力例:
```
04/06 07:02 | AAA | about cow, I've been thinking now that everyone can see assignments...
04/08 16:54 | BBB  | That would be fair enough...
```

### メンバー離脱・参加を検出する

```bash
# ユーザーがusers{}に存在しないメッセージを探す（脱落メンバーの可能性）
jq '.results[0].result.response.chat[] | select(.data.text | test("leaving|leave|bye|goodbye"; "i"))'
```

### 最新メッセージ10件を取得

```bash
jq -r '.results[0].result.response.chat | sort_by(.ctime | tonumber) | reverse | .[0:10] | .[]'
```
