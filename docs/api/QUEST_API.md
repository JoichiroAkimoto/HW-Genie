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

*   **Tips**: `state` 1 = active/progressing, 2 = 条件達成済み・**報酬受取可能**（`questFarm` で受領できる。受領前は questGetAll に残る）, 3 = 報酬受領済み（questGetAll からは**消える**。questFarm 応答の `quests` 配列でのみ見られる）。
*   **Tips**: UI の「未完了」表示には state=2（受取待ち）も含まれる。UI の表示値と `progress` は一致しない場合がある（例: 10050 は UI 上 1750/1750 だが API は progress=1858）。
*   **Tips**: レスポンスにクエスト名・カテゴリは含まれない。カテゴリと名前は ID から特定する（下記参照）。
*   **Tips**: `id` ・`progress` ・`reward` の数値は int / str が混在する（例: `"id": "2609007064"`、`"coin": {"24": "500"}`）。
*   **Tips**: クエスト ID はアカウント共通。`createTime` がデイリーリセット時刻（アカウント設定に依存）。

#### カテゴリ判定（ID ファミリー）
| ID 範囲 | カテゴリ |
| --- | --- |
| `100xx` | Daily（デイリー） |
| `110xx` | Weekly（週次） |
| `20000000` 台 | Guild（ギルド） |
| `232xxx` | Main（メイン） |
| `398xxx` | Event（イベント） |
| `26xxxxxx` / `27xxxxxx` | Battle Pass / Season |
| `784`〜`874`, `xxx-4桁〜5桁` | One-time |

`hw-genie quests` コマンドでは 100xx の主要デイリーについて `QUEST_MASTER` テーブルで名前を解決する
（`src/python/hw_genie/commands/quests.py`）。確定済みデイリー:
`10004`=Arena/GA 3回、`10006`=Use emerald exchange、`10007`=Perform 1 summon in the Soul Atrium（召喚・交換の対応は gacha_open / 交換操作のレスポンスで確定）、
`10024`=Hero's Artifact 1回、`10028`=Titan Artifact、`10030`=Skin 1回、
`10050`=Earn 1750 Guild Activity points（報酬: consumable 3×10 + gold 10000。target 1750、questGetAll では progress 1858 と超過が含まれる）。
`10033` は dungeonActivity 報酬だが Daily タブには表示されない未分類。

#### ギルドクエスト（2000xxxx/2001xxxx = Sparks of Power）
- ID ファミリは 2 種: `2000xxxx`（日次のギルドクエスト。ID はアカウント・日次で
  動的に進む）と `2001xxxx`（ギルドアクティビティ到達報酬。固定 ID・全アカウント
  共通・日次リセットで再出現）。
- 「Obtain xxx Sparks of Power」等の達成はギルド全体の累積ポイントで進行し、
  `questGetAll` の state=2 になったものを `questFarm` で受領する。
- 報酬は questGetAll では見えず、**questFarm 応答の `quests` 配列でのみ判明する**。
  実測で確認済みの達成報酬（ギルドアクティビティ 838 到達時）:
  | クエスト ID | 報酬 |
  |---|---|
  | 20010000 | clanActivity 150 |
  | 20010001 | dungeonActivity 75 |
  | 20010002 | stamina 200（エナジー回復） |
  | 20010003 | consumable 81 ×5（オラクルカード） |
  | 20010004 | coin 38 ×1（SOUL クリスタル） |
  | 20010005 | refillable 45 ×1（ポータル） |
- `hw-genie` は **`GUILD_QUEST_CLAIM_EXCLUDE`（20010002 / 20010003 / 20010004 / 20010005）の
  報酬を自動受領しない**（エナジー 200 回復・オラクルカード・SOUL クリスタル・ポータルは
  自動取得しない方針）。除外対象は dry-run で `SKIP (GUILD_QUEST_CLAIM_EXCLUDE)`、
  実行時は `Skipping claim for ...` として表示され、未受領のまま残る。

---

### questFarm - クエスト報酬確定（自動化用、破壊的）
指定クエストの達成報酬を受け取り、進捗を確定させます。

> **警告**: 破壊的メソッド。クエストが達成済み（state=1 で progress 到達）の場合、報酬が消費されます。
> 未達成のクエストに対して呼び出すとエラーになります。

*   **Endpoint**: POST /api/
*   **Request Args**: `{"questId": <id>}`
*   **Context**: `{"actionTs": <連番>}` — actionTs はゲームサーバー固有の連番（Unix epoch ではなく `7540651` のような値）。
  HW-Genie の `HWClient.call()` は独自に actionTs を書き込むため、自動化実装時は既存フレームワークとの整合が必要。

#### Response Structure
```json
{
  "reward": { "consumable": { "56": 1 }, "gold": 6400 },
  "quests": [
    { "id": 10004, "state": 3 }
  ]
}
```

*   **Tips**: 応答に含まれる `quests` 配列でバトルパス・ギルド報酬の進捗が更新される（state=3）。
*   **実装状況**: `quests --execute`（アカウント指定）と `multi quests`（全アカウント一括）で自動完了を実装済み。`multi daily` / `multi full`（bin/hwda / bin/hwsa）は実行後に各アカウントの `quest_defaults.enabled` クエストを自動完了する。

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
