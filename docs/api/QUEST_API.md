---
layout: page
title: Quest API Reference
---

# Quest API Reference

## 概要
デイリーミッション、メインクエスト、イベントクエスト、バトルパス、およびPVEキャンペーンの進行状況を取得します。

## 主要メソッド

### questGetAll - 全クエスト
デイリー、メイン、その他各種クエストの進捗状況を取得します。

*   **Endpoint**: POST /api/
*   **Request Args**: `{}`

#### Response Structure (`results[0].result.response`)
```json
[
  {
    "id": 784,
    "state": 1,
    "progress": 0,
    "reward": { "starmoney": 50 },
    "createTime": 1705763533,
    "farmCount": 0
  }
]
```

*   **Tips**: `state` 1 = active/progressing, 2 = finished.

---

### missionGetAll - キャンペーンミッション
ストーリーモード（キャンペーン）の各ステージクリア状況を取得します。

*   **Endpoint**: POST /api/
*   **Request Args**: `{}`

#### Response Structure (`results[0].result.response`)
```json
[
  {
    "id": 1,
    "stars": 3,
    "triesSpent": 3,
    "resetToday": 0,
    "attempts": 457,
    "wins": 457
  }
]
```

---

### battlePass_getInfo - バトルパス (Season Pass)
現在のバトルパスの進捗、報酬、有効期限等を取得します。

*   **Endpoint**: POST /api/
*   **Request Args**: `{}`

#### Response Structure (`results[0].result.response`)
```json
{
  "id": 1963000109,
  "clientData": { ... },
  "battlePass": {
    "exp": 15350,
    "rewards": { ... }
  }
}
```

---

### eventPicker_getInfo - イベント一覧
現在開催中の全スペシャルイベントの情報を取得します。

*   **Endpoint**: POST /api/
*   **Request Args**: `{}`

#### Response Structure (`results[0].result.response`)
```json
{
  "available": false,
  "timeStart": null,
  "timeEnd": null
}
```

*   **Tips**: イベント開催状況により `available` が true になる。

---

## 補足メソッド
(現時点で未実装またはテスト不可能なメソッド)
*   `dailyBonusGetInfo`
*   `missionGetReplace`
*   `battlePass_getSpecial`
*   `seasonAdventure_getInfo`
*   `newYear_getInfo`
*   `socialQuestGetInfo`
*   `telegramQuestGetInfo`
