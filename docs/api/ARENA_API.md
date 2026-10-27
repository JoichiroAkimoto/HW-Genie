---
layout: page
title: Arena API Reference
---

# Arena API Reference

## 概要
Arena / Grand Arena の順位・防衛チーム・攻撃候補を取得し、指定プレイヤーを攻撃するための RPC 群です。ここに記載したレスポンス形状は、2026-10-27 に読み取り専用 API（`arenaAttack` / `grandAttack` は未実行）で実機確認したものです。

- 通常 Arena の CLI 実装は `src/python/hw_genie/commands/arena.py`（`feat/arena-target-attack`）。
- 攻撃先は **自分より上位のプレイヤーのみ** を許可します（同順位・下位は拒否）。
- `arenaCheckTargetRange` は **1リクエスト最大3件** です。4件以上は `ERROR TooMany`（`"Too many ids to process!"`）になります。
- 攻撃可能な順位範囲は実測で **自分の順位 ±21** です（例: 22位 → 1〜43位、34位 → 13〜50位）。本機能は上位のみを対象とするため、実質 `own_place - 21` 〜 `own_place - 1` です。
- `topGet` はトップ50のみ返し、ページングはありません。50位より下は `arenaFindEnemies` の候補（自分の約13位上まで）に依存します。

### Endpoint
```
POST https://heroes-wb.nextersglobal.com/api/
Content-Type: application/json; charset=UTF-8
```

---

## arenaGetAll - 自分の Arena 状態

### Request
```json
{"calls":[{"name":"arenaGetAll","args":{},"ident":"body"}]}
```

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "arenaPlace": "22",
        "grandPlace": "34",
        "arenaPower": "1176007",
        "grandPower": "980000",
        "arenaHeroes": [{"id": 46}, {"id": 62}, {"id": 71}, {"id": 35}, {"id": 70}],
        "grandHeroes": [[{"id": 46}, {"id": 62}, {"id": 71}, {"id": 35}, {"id": 70}]],
        "battles": 10,
        "wins": 7,
        "rewardFlag": 0,
        "rewardTime": 0
      }
    }
  }]
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| `arenaPlace` / `grandPlace` | string | 自分の順位 |
| `arenaHeroes` / `grandHeroes` | array | 防衛編成 |
| `arenaPower` / `grandPower` | string | 防衛戦力 |
| `battles` / `wins` | int | 直近の戦績 |

`arenaGetAll` には相手一覧・ランキングは含まれません。

### 攻撃回数・クールダウン
`userGetInfo` の `refillable` を参照します。

| `refillable.id` | 意味 | 最大 |
|---|---|---|
| 5 | Arena クールダウン | - |
| 6 | Arena 攻撃回数 | 5 |
| 20 | Grand Arena クールダウン | - |
| 21 | Grand Arena 攻撃回数 | 5 |

```json
{"refillable": [{"id": 5, "amount": 0}, {"id": 6, "amount": 5}]}
```

---

## topGet - ランキング上位50件

### Request
```json
{"calls":[{"name":"topGet","args":{"type":"arena","extraId":0},"ident":"body"}]}
```

- `type`: `"arena"` / `"grand"`
- `extraId`: 0 / 1 / 2 を検証したが、いずれも同じトップ50を返した（ページングなし）

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "top": [
          {
            "place": "1",
            "userId": "12345678",
            "power": "1176007",
            "heroes": [
              {"id": 46}, {"id": 62}, {"id": 71}, {"id": 35}, {"id": 70},
              {"id": 6008, "type": "pet"}
            ],
            "banners": [{"id": 4, "slots": {"0": 35, "1": 119, "2": 12}}]
          }
        ],
        "place": 22,
        "users": {
          "12345678": {"id": "12345678", "name": "Bob", "clanId": "9", "leagueId": 2}
        }
      }
    }
  }]
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| `top` | array | 上位最大50件。`place` / `userId` / `power` / `heroes` / `banners` |
| `place` | int | 自分の順位 |
| `users` | object | `userId` → 名前・クラン・リーグ。`top` 要素には `user` がないため結合する |

`heroes` には防衛ヒーローとペット（`type: "pet"`、ID >= 6000）が混在します。ペットは `type` または ID で判別してください。

---

## arenaFindEnemies / grandFindEnemies - 攻撃候補

### Request
```json
{"calls":[{"name":"arenaFindEnemies","args":{},"ident":"body"}]}
```

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "enemies": [
          {
            "userId": "11111111",
            "place": "20",
            "power": "900000",
            "heroes": [{"id": 46}, {"id": 62}, {"id": 6008, "type": "pet"}],
            "banners": [{"id": 4}],
            "user": {"name": "Alice", "clanId": "9", "leagueId": 2}
          }
        ]
      }
    }
  }]
}
```

- 1回の呼び出しで最大3件の候補が返ります。
- 候補は常に **自分より上の順位** で、実測では自分の1〜13位上でした（毎回同じ3件とは限りません）。
- レスポンスのリスト格納キーは環境により `enemies` / `list` / `candidates` 等の可能性があるため、CLI 実装は複数キーを許容します。

---

## arenaCheckTargetRange / grandCheckTargetRange - 攻撃可否チェック

### Request
```json
{"calls":[{"name":"arenaCheckTargetRange","args":{"ids":[11111111,22222222,33333333]},"ident":"body"}]}
```

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {"11111111": true, "22222222": false, "33333333": true}
    }
  }]
}
```

- 戻り値は `{userId: true|false}` のマップです。
- **1リクエスト最大3件**。4件以上は `ERROR TooMany` になります。
- 自分自身の `userId` も `true` になるため、CLI は攻撃前に自分を明示的に除外します。
- 攻撃は行わず、攻撃回数も消費しません。`--dry-run` はこの API まで実行します。

### 攻撃可能な順位範囲（実測）
| モード | 自分の順位 | `true` | `false` |
|---|---:|---|---|
| Arena | 22 | 1〜43 | 44〜50 |
| Grand Arena | 34 | 13〜50 | 1〜12 |

いずれも **自分の順位 ±21** と一致します。したがって 22位のアカウントは 1〜3位も範囲チェック `true` になります。上位を狙う場合は `arenaFindEnemies` ではなく `topGet` で `userId` を解決してください。

> **注意**: 範囲内の任意ターゲットを `arenaAttack` が受理するかは、実攻撃を伴うため未確認です。初回の実攻撃は範囲内の1件で確認してください。

---

## arenaAttack / grandAttack - 攻撃

### Request（Arena）
```json
{
  "calls": [{
    "name": "arenaAttack",
    "args": {
      "userId": 12345678,
      "heroes": [29, 13, 73, 71, 35],
      "pet": 6008,
      "favor": {"13": 6002, "29": 6011, "35": 6001, "71": 6006, "73": 6004},
      "banners": [2]
    },
    "context": {"actionTs": 0},
    "ident": "body"
  }]
}
```

- `pet` / `banners` は未指定なら省略可能です。
- `favor` は部分マップ・空マップでも送信できます（保存チームでも欠落があるため）。
- `context.actionTs` は `HWClient.call()` が送信直前に現在時刻へ更新します。

### Request（Grand Arena）
```json
{
  "calls": [{
    "name": "grandAttack",
    "args": {
      "userId": 12345678,
      "heroes": [[29, 13, 73, 71, 35], [46, 62, 71, 35, 70], [1, 2, 3, 4, 5]],
      "pets": [6008, 6008, 6008],
      "favor": {"13": 6008, "64": 6005},
      "banners": [1, 3, 2]
    },
    "context": {"actionTs": 0},
    "ident": "body"
  }]
}
```

### Response（未実機確認）
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "battles": [
          {"result": {"win": true, "oldPlace": 22, "newPlace": 10}}
        ]
      }
    }
  }]
}
```

CLI は `response.battles[0].result` の `win` / `oldPlace` / `newPlace` を表示します。形状が異なる場合は警告を出して exit 0 とし、再試行で攻撃回数を消費しないよう促します。

---

## チーム取得

### teamGetAll
```json
{"calls":[{"name":"teamGetAll","args":{},"ident":"body"}]}
```
```json
{"response": {"arena": [29, 13, 73, 71, 35, 6008], "grand": [[...], [...], [...]]}}
```

- `arena`: `[heroId ×5, petId]` のフラット配列（ペットなしは5要素）。
- `grand`: 3チーム分の配列。

### teamGetFavor
```json
{"calls":[{"name":"teamGetFavor","args":{},"ident":"body"}]}
```
```json
{"response": {"arena": {"13": 6002, "29": 6011}, "grand": {"13": 6008}}}
```

- `{heroId: patronPetId}`。**全ヒーロー分が揃っているとは限りません**（実データでも保存チームの1体が欠落）。
- CLI は選択ヒーローに一致するエントリのみを使い、欠落ヒーローは警告に列挙します。

### team_getBanners
```json
{"calls":[{"name":"team_getBanners","args":{},"ident":"body"}]}
```
```json
{"response": {"arena": 2, "grand": [1, 3, 2]}}
```

- `arena` は単一の bannerId、`grand` は3つです。

---

## エラーと CLI の挙動

| 状況 | CLI の挙動 |
|---|---|
| `arenaCheckTargetRange` が `false` / 欠落 | 攻撃せず exit 1。自分の順位と `own_place - 21` の下限を表示 |
| 指定ランクがトップ50・候補にない | 攻撃せず exit 1。`--user-id` を案内 |
| 指定ランク / `userId` が自分以上 | 攻撃せず exit 1。上位のみ対象である旨を表示 |
| 自分自身を指定 | 攻撃せず exit 1 |
| チームが5体でない / 重複 / 不正ID | API 呼び出し前に exit 1 |
| `NotEnough`（攻撃回数切れ） | 攻撃せず警告して exit 0 |
| `Refillable`（クールダウン） | 攻撃せず警告して exit 0 |
| 攻撃受理後に結果を解析できない | 警告して exit 0（再試行で消費しない） |
| 認証エラー | 既存の `HWAuthError` 共通ハンドラ |

## 関連
- CLI: `.agents/skills/arena/SKILL.md`
- 生ペイロード例: `docs/superpowers/Arena.md`
