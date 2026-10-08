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
  動的に進む）と `2001xxxx`（ギルドアクティビティ到達報酬。ID はローテーションし
  全アカウント共通・日次リセットで再出現）。
- 「Obtain xxx Sparks of Power」等の達成はギルド全体の累積ポイントで進行し、
  `questGetAll` の state=2 になったものを `questFarm` で受領する。
- 報酬は `questGetAll` の各クエストの `reward` に含まれる（実測例:
  `{"id": 20010008, "state": 2, "reward": {"stamina": 200}}`、
  `{"id": 20010011, "reward": {"refillable": {"45": "1"}}}`）。
  実測で確認済みの達成報酬（旧サイクル例。ID はローテーションし新サイクルでは
  +6 シフト等で同順に出現する。例: 20010008 が stamina 200）:
  | クエスト ID（旧例） | 報酬 |
  |---|---|
  | 20010000 | clanActivity 150 |
  | 20010001 | dungeonActivity 75 |
  | 20010002 | stamina 200（エナジー回復） |
  | 20010003 | consumable 81 ×5（オラクルカード） |
  | 20010004 | coin 38 ×1（SOUL クリスタル） |
  | 20010005 | refillable 45 ×1（ポータル） |
  新サイクル実測（ID はローテーションし報酬も変動する。内容判定のため ID 表は目安）:
  | 20010010 | consumable 45 ×3（Artifact Chest Key。`refillable 45` のポータルとは別物） |
  注意: ポータル（`refillable 45`）と Artifact Chest Key（`consumable 45`）は
  ID 45 が衝突しているがカテゴリが異なる。前者は自動受領しないが、後者は取得する。
- `hw-genie` は **スタミナとポータル（`refillable 45`）の報酬を自動受領しない**
  （手動管理のため。オラクルカード・SOUL クリスタル・Artifact Chest Key（`consumable 45`）等は取得する）。
  除外判定は原則報酬内容で行う（ID ローテーション対応）。旧 ID
  `{20010002, 20010005}` のみ互換フォールバックとして ID でも除外するが、
  旧 ID に明確な非対象報酬（例: clanActivity）が載っている場合は取得する。
  旧 ID が将来別報酬に再利用された場合は見直す（サンセット条件）。
  なお除外判定はデイリー/ギルド共通で、デイリーの stamina/ポータル報酬も対象となる（将来 10038/10039 等が登録された場合の安全装置。現状未登録のため実質ギルドのみに作用）。
  除外対象は dry-run で `SKIP (manual-keep: ...)`、
  実行時は `Skipping claim for ... (manual-keep: ...)` として表示され、未受領のまま残る。

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

### eventPicker_getState / startGame / playRound / finishGame - Seer's Game
Seer's Game（開始→カード選択×4→終了）の状態確認・プレイを行います。
1ゲームの開始に Seer's Coin を 25 枚消費します（毎日1回は無料開始可能、JST 11:00 リセット）。
ツールは無料/有料を区別せず、Coin 枯渇まで周回します（`hw-genie seers-game` / `multi seers-game`）。
`stashClient` は不要のため送りません。

*   **Endpoint**: POST /api/
*   **Seer's Coin**: `inventoryGet` の `coin` 枠、ID **2824001094**（`SEERS_COIN_ID`）。
*   **報酬で観測される ID**: coin `14` / `18` / `24`、consumable `11` / `12` / `17` / `51` / `53`
    （`playRound` / `finishGame` 応答の `event.collected_rewards` に
    `{"consumable": {"12": 20}, "coin": {"24": 500}}` 形式で累積される）。
    `playRound` 応答には `2824402359-2367`（coin 18 + consumable 53 系）・
    `2824402326-2334`（consumable 51 系）の `quests` が同梱されることがあるが、
    ツールは `questFarm` せず無視する。

#### eventPicker_getState
*   **Request Args**: `{}`
*   **Response** (`results[0].result.response.event`):
```json
{
  "id": 2824000007,
  "round": 5,
  "size": 4,
  "state": "new_game",
  "win_streak": 14,
  "mark_history": [],
  "collected_rewards": {"consumable": {"12": 40, "17": 3}, "coin": {"24": 500}}
}
```

*   **Tips**: `state` は `new_game`（待機中）/ `active`（round 途中）。
    `state == "active"` の中断ゲームがあればツールは新規開始せず `playRound` から再開する。
    `collected_rewards` は未収集時に `[]`（空リスト）で現れることがある。

#### eventPicker_startGame
*   **Request Args**: `{}`
*   **Response** (`results[0].result.response.event`):
```json
{
  "id": 2824000007,
  "round": 1,
  "size": 3,
  "state": "active",
  "win_streak": 10,
  "mark_history": [],
  "collected_rewards": []
}
```

*   **Tips**: Coin 不足・回数上限など ERROR 応答時はツールは正常終了として停止する
    （`last_error` に記録）。通信・パース失敗（`UNEXPECTED`）は枯渇とみなさず例外送出。

#### eventPicker_playRound
*   **Request Args**: `{"num": 2}`（引くカード番号。ツール既定は 2）
*   **Response** (`results[0].result.response`):
```json
{
  "event": {
    "id": 2824000007,
    "round": 2,
    "size": 3,
    "state": "active",
    "win_streak": 11,
    "mark_history": [],
    "collected_rewards": {"consumable": {"12": 20}}
  },
  "rollResult": {
    "mark": false,
    "cards": {
      "1": {"num": 1, "isUserCard": false, "type": "reward", "reward": {"consumable": {"11": 30}}},
      "2": {"num": 2, "isUserCard": true, "type": "reward", "reward": {"consumable": {"12": 20}}},
      "3": {"num": 3, "isUserCard": false, "type": "reward", "reward": {"consumable": {"53": 1500}}}
    },
    "result": "win"
  },
  "quests": []
}
```

*   **Tips**: `num` は live の `event.size` に対して `1 <= num <= size` で検証する
    （範囲外は `ValueError`）。`size` は 3/4 で変動するため固定 `choices` にしない。
    1ゲームあたり `playRound` は 4 回（round 1→2→3→4→finish）。

#### eventPicker_finishGame
*   **Request Args**: `{}`
*   **Response** (`results[0].result.response.event`):
```json
{
  "id": 2824000007,
  "round": 5,
  "size": 4,
  "state": "new_game",
  "win_streak": 14,
  "mark_history": [],
  "collected_rewards": {"consumable": {"12": 40, "17": 3}, "coin": {"24": 500}}
}
```

*   **Tips**: 成功で `state` が `new_game` に戻る。finish 時の `collected_rewards` がそのゲームの確定報酬。

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
