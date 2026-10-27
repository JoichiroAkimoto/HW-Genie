---
layout: page
title: Arena API Reference
---

# Arena API Reference

## 概要
Arena / Grand Arena の順位・防衛チーム・攻撃候補を取得し、指定プレイヤーを攻撃するための RPC 群です。ここに記載したレスポンス形状は 2026-10-27 に実機で確認したものです（読み取り API に加え、`arenaAttack` は 22→11 位の勝利、`grandAttack` は Bo3 勝利の応答を確認済み）。

- 通常 Arena の CLI 実装は `src/python/hw_genie/commands/arena.py` です。
- 攻撃先は **自分より上位のプレイヤーのみ** を許可します（同順位・下位は拒否）。`topGet` の `place`（自分の順位）が取得できた場合は、古い `arenaGetAll` の値より優先します。攻撃直前にも `arenaGetAll` を再取得して上位のみを再確認します。
- **実攻撃の受理条件** は次の両方です。片方だけでは攻撃できません。
  1. 対象が **最新の `arenaFindEnemies` レスポンスに含まれる**（呼び出しごとに変わる候補リスト）。
  2. `arenaCheckTargetRange` が `true`（自分の順位 ±21）。
- 実機では 22位から **2 / 4 / 6 / 8 位**（範囲チェック `true`・候補外）を攻撃するとすべて `NotAvailable`、**11位** が候補に現れた回は `WIN (22 -> 11)` でした。つまり範囲チェック `true` は **必要条件だが十分条件ではありません**。
- `arenaCheckTargetRange` は **1リクエスト最大3件** です。4件以上は `ERROR TooMany`（`"Too many ids to process!"`）になります。
- 攻撃可能な順位範囲は実測で **自分の順位 ±21** です（例: 22位 → 1〜43位、34位 → 13〜50位）。本機能は上位のみを対象とするため、実質 `own_place - 21` 〜 `own_place - 1` です。
- **候補の範囲に固定上限はありません**。実測では候補は自分の 1〜13 位上が中心でしたが、**13位より上を攻撃できた例もあります**（ユーザー観測。どのような条件で候補の幅が広がるかは未解明。下記 TODO 参照）。CLI は「-13位まで」のような固定制限を設けず、候補への登場と範囲チェックで判定します。候補リストを `arenaFindEnemies` で再取得すれば対象が現れる場合があるため、CLI は `--candidate-retries`（既定 10）回まで追加取得します。
- `topGet` はトップ50のみ返し、ページングはありません。50位より下（およびトップ50外のターゲット）は `arenaFindEnemies` の候補に依存します。

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
- `--user-id` を指定しても順位解決の要件は回避できません。対象がこの `topGet`（トップ50）または `arenaFindEnemies` の候補に存在しない場合は exit 1 になります。

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
- 候補は常に **自分より上** で、実測では自分の1〜13位上が中心でした（毎回同じ3件とは限りません）。
- **13位より上を攻撃できた例があります**（固定上限なし）。参考: Grand Arena 11位の `grandAttack` 応答に含まれた候補は1位（10位上）・4位・10位でした。どのような条件で候補の幅が広がるかは未解明です（下記 TODO）。
- 実攻撃は **最新レスポンスの候補** にいる相手しか受理されません。CLI は `arenaFindEnemies` を `--candidate-retries` 回まで追加取得し、対象が現れるまで待ちます（初回 + 追加 N 回 = 最大 N+1 リクエスト）。
- レスポンスのリスト格納キーは環境により `enemies` / `list` / `candidates` 等の可能性があるため、CLI 実装は複数キーを許容します。
- `grandAttack` のレスポンスにも、攻撃後の `state` と `enemies`（候補リスト）が同梱されます（下記 Grand Arena 参照）。

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
- **`true` は攻撃可能性の必要条件にすぎません**。実際の `arenaAttack` は、さらに「最新の `arenaFindEnemies` 候補にいること」を要求します。

### 範囲チェックの順位ウィンドウ（実測）
| モード | 自分の順位 | `true` | `false` |
|---|---:|---|---|
| Arena | 22 | 1〜43 | 44〜50 |
| Grand Arena | 34 | 13〜50 | 1〜12 |

いずれも **自分の順位 ±21** と一致します。したがって 22位のアカウントは 1〜3位も範囲チェック `true` になります。

### 実際の攻撃受理（実機確認）
22位から範囲チェック `true` のターゲットを攻撃した結果:

| ターゲット | 候補リスト | 結果 |
|---|---|---|
| 2位 / 4位 / 6位 / 8位 | 含まれない | `NotAvailable`（攻撃回数・順位とも不変） |
| 11位 | 含まれる（`arenaFindEnemies` の再取得で出現） | `WIN (22 -> 11)` |

範囲チェックが `true` でも候補外は受理されません。CLI は dry-run / 実攻撃の両方で候補確認を行い、候補外なら `arenaAttack` を送りません。候補は再取得で変わるため、CLI は `--candidate-retries` 回まで追加取得します。

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

- `pet` / `banners` は未指定なら省略可能です。`banners: [0]` は送信しません（0 は「バナーなし」のセンチネル）。`--banner 0` もバナーなしとして扱います。
- `favor` は部分マップを送信できます（保存チームでも欠落があるため）。空の場合は payload から省略されます。
- `context.actionTs` は `HWClient.call()` が送信直前に現在時刻へ更新します。
- **受理条件**: `userId` が送信直前の最新 `arenaFindEnemies` 候補に含まれていること。候補外は `ERROR NotAvailable`（攻撃回数は消費されない）。CLI は攻撃前に候補を確認するため、このエラーは通常発生しません。
- **`arenaAttack` はリトライしません**（`HWClient.call(..., max_retries=0)` で1回だけ送信）。タイムアウト等で結果が不明な場合は、攻撃回数を二重に消費しないよう「結果不明・ゲーム側で確認してから再実行」を促すエラーで停止します。

### Response（Arena・実機確認済み）
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "battles": [
          {"result": {"win": true, "oldPlace": 22, "newPlace": 11}}
        ]
      }
    }
  }]
}
```

CLI は `response.battles[0].result` の `win` / `oldPlace` / `newPlace` を表示します。形状が異なる場合は警告を出して exit 0 とし、再試行で攻撃回数を消費しないよう促します。

### Request（Grand Arena・実機確認済み）
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

`heroes` は3チーム分の配列、`pets` / `banners` も3要素です。通常 Arena と異なり `heroes` がネストします。

### Response（Grand Arena・実機確認済み）
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "battles": [
          {"result": {"win": true, "stars": 3, "battleOrder": 0}},
          {"result": {
            "win": true, "stars": 3, "battleOrder": 1,
            "oldPlace": "21", "newPlace": "11", "enemyPlace": "11"
          }}
        ],
        "win": true,
        "state": {"grandPlace": "11", "...": "..."},
        "enemies": [
          {"userId": "11111111", "place": "1"},
          {"userId": "22222222", "place": "4"},
          {"userId": "33333333", "place": "10"}
        ]
      }
    }
  }]
}
```

- Bo3 の各バトルが `battles[]` に入り、順位変動 (`oldPlace` / `newPlace` / `enemyPlace`) は **最後のバトル** の `result` に現れます（`battles[0]` には無い場合がある）。
- `state` は攻撃後の自分の状態、`enemies` は攻撃後の候補リストです。
- 現在の CLI は通常 Arena のみ対応です。GA 対応時は「最後の `battles[].result` から順位を読む」「3チーム分のチーム解決」「GA の refillable id=20/21」が必要です。

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
| 対象が `--candidate-retries` 回更新しても `arenaFindEnemies` 候補に現れない | 攻撃せず exit 1。候補外は実攻撃でも `NotAvailable` になる旨と、リトライ回数を増やす案内を表示 |
| `arenaCheckTargetRange` が `false` / 欠落 | 攻撃せず exit 1。自分の順位と `own_place - 21` の下限を表示 |
| 指定ランクがトップ50・候補にない | 攻撃せず exit 1。`--user-id` は `topGet` / 候補から順位を解決できるプレイヤーのみ指定可能（解決できなければ exit 1） |
| 指定ランク / `userId` が自分以上（解決時または候補の最新順位で判定） | 攻撃せず exit 1。上位のみ対象である旨を表示 |
| 自分自身を指定 | 攻撃せず exit 1 |
| `--heroes` が5個でない / `--candidate-retries` が負数（argparse エラー） | API 呼び出し前に exit 2 |
| ヒーローIDの不正（重複・範囲外）、`--pet` / `--banner` / `--favor` の不正値 | API 呼び出し前に exit 1 |
| `--favor` のヒーローが選択チームに含まれない | `--heroes` 指定時は API 呼び出し前に exit 1。プリセット/保存チームを使う場合はチーム解決後に exit 1 |
| `NotEnough`（攻撃回数切れ、例: `Refillable 6`） | 攻撃せず警告して exit 0 |
| `Refillable`（クールダウン。`Refillable 5` や `NotEnough` + description も含む） | 攻撃せず、`status.cooldown` があれば残り時間を表示して exit 0 |
| 攻撃送信後にタイムアウト / 結果不明 | exit 1。「結果不明・攻撃回数を消費した可能性があるためゲーム側で確認」と表示（自動リトライしない） |
| 攻撃受理後に結果を解析できない | 警告して exit 0（再試行で消費しない） |
| 認証エラー | 既存の `HWAuthError` 共通ハンドラ |

## TODO / 未解明事項
- **候補ウィンドウの条件**: `arenaFindEnemies` が 13 位より上を含む条件は未解明。自分の順位変動・サーバー側の候補生成タイミング・防衛編成などが候補か。`--candidate-retries` を増やしても見つからない場合があるため、候補生成の規則を追加調査する。
- **Grand Arena CLI**: `grandAttack` のペイロード/レスポンスは実機確認済み（上記）。CLI 対応時は3チーム・3ペット・3バナー、refillable id=20/21、最後の `battles[].result` からの順位読み取りが必要。

## 関連
- CLI: `.agents/skills/arena/SKILL.md`
