---
layout: page
title: Chat API Reference
---

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
| `userId` | string | No | 個人チャットの相手プレイヤーID（数値）。省略時は空レスポンス（会話一覧は `chatGetTalks` を使用）。自分自身のIDは指定不可 |

### chatType 早見表

| chatType | 用途 |
|----------|------|
| `clan` | 所属ギルドのメンバー間チャット |
| `training` | トレーニングマッチ（PvP訓練）の相手とのチャット |
| `xgvg` | クロスサーバーギルド戦（XvX）|matching相手とのチャット |
| `server` | サーバー全体向けグローバルチャット（運営公告等） |
| `personal` | 個人チャット（PM）。相手の `userId` を指定するとスレッド、省略すると空（一覧は `chatGetTalks`） |

### curl 実行例

```bash
# 認証ヘッダー (x-auth-*) はブラウザ DevTools の「Copy as cURL」で取得した curl に含まれる
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"chatGetAll","args":{"chatType":"clan"},"context":{"actionTs":1},"ident":"chat"}]}'
```

個人チャット（PM）は `chatType=personal` に相手の `userId` を指定すると、その相手との
スレッドを取得します。`userId` を省略しても会話一覧は返らないため、一覧は
`chatGetTalks`（後述）を使います。

```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"chatGetAll","args":{"chatType":"personal","userId":"12345678","count":50},"context":{"actionTs":1},"ident":"chat"}]}'
```

> **挙動メモ（実機観測）**: `personal` のスレッド取得はサーバー側の未読状態に依存し、
> 既読化済みの会話では 0 件（`{"chat":[],"users":[],"clans":[]}`）が返ることがあります。
> `userId` なし・`0`・自分自身の ID を指定した場合は空またはエラーになります。
> `count` / `lastId` を変えても取得範囲は広がらないため、**相手ごとの最新メッセージ**が
> 必要な場合は `chatGetTalks` の `lastMessage` を使うのが確実です。

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

## chatGetTalks - 個人チャットの会話一覧

直近にやり取りした相手（PM の会話一覧）を取得します。引数は不要です。

### Request Arguments

なし（`args: {}`）

### curl 実行例

```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"chatGetTalks","args":{},"context":{"actionTs":1},"ident":"chat"}]}'
```

### Response Structure

```json
{
  "results": [{
    "ident": "chat",
    "result": {
      "response": {
        "talks": [
          {
            "userId": "12345678",
            "lastMessage": "直近のメッセージ本文",
            "lastMessageType": "text",
            "utime": "1794145281",
            "seen": "1794208957",
            "read": "1794145281"
          }
        ]
      }
    }
  }]
}
```

### talks[] フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `userId` | string | 会話相手のプレイヤーID |
| `lastMessage` | string | 直近メッセージ本文（`challenge` 等では空） |
| `lastMessageType` | string | `text` / `sticker` / `challenge` など |
| `utime` | string | 直近メッセージの Unix タイムスタンプ（秒）。**一覧は utime 降順（最新順）** |
| `seen` | string | 相手が最後に閲覧した時刻（Unix 秒） |
| `read` | string | 自分が最後に既読した時刻（Unix 秒、0 = 未読） |

相手の名前は `userGetInfoByIds` で一括取得できます（`args: {"ids": ["12345678", ...]}`、
最大 300 件、`response.users[]` に `id` / `name` / `level` など）。

---

## chatSendChallenge - フレンドリーチャレンジ送信

個人チャットの相手にフレンドリーバトル（チャレンジ）を送ります。チャレンジは
**相手側**の personal スレッドに `messageType="challenge"` のメッセージとして届きます。

### Request Arguments

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `chatType` | string | Yes | `personal` 固定 |
| `heroes` | int[] | Yes | 自分のチャレンジ用チーム（1〜5 体）。`teamGetAll` の `challenge_titan`（タイタン）/ `challenge`（ヒーロー）が保存チーム |
| `favor` | object | No | ヒーローの favor。タイタンでは `{}` |
| `text` | string | No | 添えるテキスト（通常は空文字） |
| `type` | string | Yes | `titan` または `hero` |
| `manual` | bool | No | 実測: 送信時は `false`、受信したチャレンジには `true` のものもある（意味は未確定。送信は `false` で成功） |
| `maxUpgrade` | bool | No | 実測: `true` で送信成功 |
| `userId` | string | Yes | **受信者のプレイヤーID**（数値）。`x-auth-user-id` ヘッダーとは別物なので注意 |

> **`userId` の注意（実機観測）**: 認証ヘッダーの `x-auth-user-id` を渡すとサーバーは
> `{"result": false, "reason": "notFound"}` を返します。`userGetInfo`（`response.id`）や
> チャットの `userId` で得られるプレイヤーIDを使ってください。

### curl 実行例

```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"chatSendChallenge","args":{"chatType":"personal","heroes":[4003,4002,4004,4001,4000],"favor":{},"text":"","type":"titan","manual":false,"maxUpgrade":true,"userId":"12345678"},"context":{"actionTs":1},"ident":"body"}]}'
```

### Response Structure

成功時:

```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {"result": true},
      "quests": [ ... ]
    }
  }]
}
```

拒否時（例: 対象が存在しない）:

```json
{"results": [{"ident": "body", "result": {"response": {"result": false, "reason": "notFound"}}}]}
```

- `response.result` が `true` なら送信成功。`false` のときは `response.reason` を見る。
- レスポンスには `quests`（クエスト進捗）が同梱されることがある。
- 送信直後は受信者側のスレッドに反映されるまで数秒かかることがある（実測 1〜2 秒）。

---

## chatAcceptChallenge - チャレンジを承諾してバトル

受信したチャレンジを承諾し、バトルを実行します。**チャレンジの受信者側**で呼びます。

### messageId の特定方法（重要）

`messageId` は、受信者側の personal スレッドにある **チャレンジメッセージの `id`** です。
`chatGetAll`（`chatType=personal`, `userId=<送信者>`）を呼び、`chat[]` から
`messageType="challenge"` の `id` を読み取ってください。

```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"chatGetAll","args":{"chatType":"personal","count":50,"userId":"12345678"},"ident":"body"}]}' \
| jq '.results[0].result.response.chat[]
      | select(.messageType=="challenge" or .messageType=="replay")
      | {id, userId, messageType, ctime, challengeId: .data.challengeId}'
```

- チャレンジが未承諾なら、`challenge` メッセージの後に同じ `id` を
  `data.challengeId` に持つ `messageType="replay"` は存在しません。
- 承諾済みなら `replay` メッセージが追加され、`data.challengeId` が
  「どのチャレンジのリプレイか」を示します。**`replay` に id が現れている
  チャレンジは消費済み**なので、自動承諾では未消費の最新チャレンジを選びます。
- メッセージ ID はスレッド内の連番（実測: `1`, `3`, `9` など）です。

### Request Arguments

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `messageId` | int | Yes | 受信者側スレッドの `challenge` メッセージの `id` |
| `heroes` | int[] | Yes | 承諾側のチャレンジ用チーム（1〜5 体）。`teamGetAll.challenge_titan` / `challenge` |
| `favor` | object | No | ヒーローの favor。通常 `{}` |
| `maxUpgrade` | bool | No | 実測: `true` |
| `userId` | string | Yes | **チャレンジを送った相手（送信者）のプレイヤーID** |

### curl 実行例

```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"chatAcceptChallenge","args":{"messageId":9,"heroes":[4003,4002,4004,4001,4000],"favor":{},"maxUpgrade":true,"userId":"12345678"},"context":{"actionTs":1},"ident":"body"}]}'
```

### Response Structure

```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "battle": {
          "userId": "22222222",
          "typeId": 12345678,
          "attackers": {"4003": {"id": 4003, "...": "..."}},
          "defenders": {"4003": {"id": 4003, "...": "..."}},
          "effects": {"battleConfig": "titan_pvp"},
          "reward": [],
          "startTime": 1794728303,
          "seed": 885764156,
          "type": "challenge_titan",
          "id": "1794728303314860997",
          "progress": [ ... ],
          "endTime": 1794728303,
          "result": {"win": true, "stars": 2, "serverVersion": 299}
        }
      }
    }
  }]
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `battle.userId` | string | 承諾した側のプレイヤーID |
| `battle.typeId` | int | チャレンジ送信者のプレイヤーID |
| `battle.type` | string | `challenge_titan`（タイタン）/ `challenge`（ヒーロー） |
| `battle.id` | string | リプレイID。`https://www.hero-wars.com/?replay_id=<id>` で再生 |
| `battle.result.win` | bool | 勝敗 |
| `battle.result.stars` | int | 獲得スター数 |
| `battle.reward` | array | 報酬（フレンドリーバトルでは通常空） |

> **注意（実機観測）**: チャレンジ送信者自身が `chatAcceptChallenge` を呼ぶと、
> サーバーは `battle.typeId` を自分自身としてバトルを成立させます（自分とのバトル）。
> **チャレンジは受信者だけが承諾できます。**

---

## CLI: `hw-genie challenge`

上記 2 API を自動化するコマンドです。詳細は `.agents/skills/challenge/SKILL.md`。

```bash
# チャレンジ送信（保存チームを使用）
uv run hw-genie challenge send -a Alice --user-id 12345678 --type titan

# 自分宛の未承諾チャレンジを一覧（messageId の確認）
uv run hw-genie challenge list -a Bob --user-id 22222222

# 最新の未承諾チャレンジを承諾（messageId は自動特定）
uv run hw-genie challenge accept -a Bob --user-id 22222222

# 送信と承諾を 1 コマンドで（両方とも登録済みアカウント）
uv run hw-genie challenge battle -a Alice --accept-account Bob
```

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
