---
layout: page
title: Arena API Reference
---

# Arena API Reference

## 概要
Arena / Grand Arena の順位・防衛チーム・攻撃候補を取得し、指定プレイヤーを攻撃するための RPC 群です。ここに記載したレスポンス形状は 2026-10-27 に実機で確認したものです（読み取り API に加え、`arenaAttack` は 22→11 位の勝利、`grandAttack` は Bo3 勝利の応答を確認済み）。

- 通常 Arena の CLI 実装は `src/python/hw_genie/commands/arena.py` です。
- 攻撃先は **自分より上位のプレイヤーのみ** を許可します（同順位・下位は拒否）。`topGet` の `place`（自分の順位）が取得できた場合は、古い `arenaGetAll` の値より優先します。通常攻撃の直前にも `arenaGetAll` を再取得して上位のみを再確認します（`--from-snapshot` は `topGet` 優先）。
- **実攻撃で確認できた条件**: 対象が **最新の `arenaFindEnemies` レスポンスに含まれる** こと。範囲チェック `true` だけでは不十分です（22位から 2 / 4 / 6 / 8 位は `true` でも候補外のため `NotAvailable`。11位は候補に現れた回に `WIN (22 -> 11)`）。
- **`arenaAttack` が現在の順位ウィンドウを再チェックするかは未検証** です。候補リストはスナップショットとして扱われ、生成後に自分/相手の順位が動くと、現在の順位差と一致しなくなる可能性があります（下記 TODO）。
- `arenaCheckTargetRange` は **1リクエスト最大3件** です。4件以上は `ERROR TooMany`（`"Too many ids to process!"`）になります。
- `arenaCheckTargetRange` のウィンドウは **順位/リーグに依存** します。22位/34位では ±21（22位 → 1〜43位、34位 → 13〜50位）でしたが、**Grand Arena 51位では 5〜50位まで `true`**（46位上まで）でした。±21 を固定ルールとして扱わず、実際の判定は `arenaCheckTargetRange` の結果を優先してください。
- **候補の範囲に固定上限はありません**。トップ帯（Arena 16〜19位）では候補は1〜13位上が中心でしたが、**Grand Arena 51位では候補が18〜24位（27〜33位上）** に現れました（読み取り確認）。候補は `arenaCheckTargetRange` の `true` 範囲内に収まっていました。CLI は「-13位まで」のような固定制限を設けず、候補への登場と範囲チェックで判定します。候補リストを `arenaFindEnemies` で再取得すれば対象が現れる場合があるため、CLI は `--candidate-retries`（既定 10）回まで追加取得します。
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
`topGet` のトップ50 ID をまとめて `arenaCheckTargetRange` に渡した結果です（上位のみ抜粋）。

| モード | 自分の順位 | `true`（上位） | `false`（上位） |
|---|---:|---|---|
| Arena | 22 | 1〜43 | 44〜50 |
| Grand Arena | 34 | 13〜50 | 1〜12 |
| Grand Arena | 51 | **5〜50** | **1〜4** |

- 22位/34位は **自分の順位 ±21** と一致します。
- **51位では ±21 ではなく、5位まで（46位上）が `true`** でした。ウィンドウは順位/リーグに依存し、固定の ±21 ではありません。
- `arenaFindEnemies` の候補も、GA 51位では18〜24位（27〜33位上）に現れ、すべて `true` 範囲内でした。低順位では候補もウィンドウも大きく広がります。

> **注意**: 常に `arenaCheckTargetRange` の実レスポンスを優先してください。CLI の `arena status` は固定の順位帯を表示せず、`--dry-run` で対象ごとに確認するよう案内します。

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

## 手動検証用スナップショット

`arenaFindEnemies` の結果を保存し、候補を更新せずに後から状態確認・攻撃するための実験用機能です。

- `arena find-enemies -a <account>`: `arenaGetAll` + `arenaFindEnemies` を1回ずつ呼び、結果を `account_configs.arena_snapshot` に保存します。攻撃はしません。
- `arena snapshot -a <account>`: 保存済みスナップショットと現在の順位を比較します。`topGet`（自分の順位と候補の現在順位）と `arenaCheckTargetRange`（読み取り）を呼び、**`arenaFindEnemies` は呼びません**。`topGet` に自分の `place` がない場合のみ `arenaGetAll` にフォールバックします。`topGet` はトップ50のみ対応です。
- `arena attack -a <account> --from-snapshot (--rank N | --user-id ID)`: 保存済み候補に `arenaAttack` を送ります。**`arenaFindEnemies` は呼びません**。`--rank N` / `--user-id ID` は**保存済みスナップショットの順位・候補**に対して解決されます（現在の `arena top` の順位とは異なる場合があるため、攻撃前に `arena snapshot` で対象を確認してください）。上位のみは `topGet` の現在順位で再チェックし（`place` がない場合のみ `arenaGetAll` にフォールバック）、対象が**現在のトップ50に存在しない場合は順位を検証できないため攻撃しません**。`arenaCheckTargetRange` が false の場合は既定で停止し、`--force` を付けた時だけ送信します（上位のみの制約は `--force` でも解除されません）。

保存形状:

```json
{
  "arena": {
    "saved_at": "2026-10-29T16:10:15+09:00",
    "own_place": 16,
    "candidates": [
      {"user_id": 11111111, "place": 4, "name": "Bob", "power": 5000}
    ]
  }
}
```

> **注意**: `topGet` が候補リストを更新するかは未検証です。スナップショット検証では、`arena snapshot` / `--from-snapshot` が `arenaFindEnemies` を呼ばないことを前提にしています。`--from-snapshot` は実験用で、通常の `arena attack` は候補プリフライト付きの安全側デフォルトのままです。

---

## エラーと CLI の挙動

| 状況 | CLI の挙動 |
|---|---|
| 対象が `--candidate-retries` 回更新しても `arenaFindEnemies` 候補に現れない | 攻撃せず exit 1。候補外は実攻撃でも `NotAvailable` になる旨と、リトライ回数を増やす案内を表示 |
| `--from-snapshot` で保存済みスナップショットがない | 攻撃せず exit 1。`arena find-enemies` の実行を案内 |
| `--from-snapshot` の対象がスナップショットにない | 攻撃せず exit 1 |
| `--from-snapshot` の対象が現在のトップ50にない | 攻撃せず exit 1。現在順位を検証できず上位のみを保証できない旨を表示（スナップショット順位へのフォールバックはしない） |
| `--from-snapshot` で `arenaCheckTargetRange` が false（`--force` なし） | 攻撃せず exit 1。`--force` の案内を表示 |
| `--force` を `--from-snapshot` なしで指定 | API 呼び出し前に exit 1 |
| `arenaCheckTargetRange` が `false` / 欠落 | 攻撃せず exit 1。順位/リーグ依存のウィンドウである旨を表示（固定の ±21 は表示しない） |
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
- **候補スナップショット + 順位ドリフト**: `arenaFindEnemies` が返した候補リストを保持したまま、自分または相手の順位が外部要因で変動すると、現在の順位差では13位より上（あるいは ±21 の外）なのに候補としては残り、`arenaAttack` が通る可能性がある。検証手順: `arenaFindEnemies` を1回だけ呼んでスナップショットを取り、以降は `arenaFindEnemies` を呼ばずに `arenaGetAll` / `topGet` で順位変動を待ち、候補が14位以上上になった時点で `arenaCheckTargetRange` → その `userId` を1回だけ攻撃する。
- **範囲チェックの動的ウィンドウ**: `arenaCheckTargetRange` の ±21 は22位/34位での実測。Grand Arena 51位では5〜50位まで `true`（46位上）で、候補も27〜33位上に現れた（読み取り確認済み）。順位/リーグ境界の正確な規則は未解明。Arena の21位以下では未計測のため、各順位帯でのマッピングを継続する。
- **`arenaAttack` の範囲再チェック**: `arenaAttack` が候補 membership に加えて現在の順位ウィンドウも再チェックするかは未検証。「候補内だが `arenaCheckTargetRange` が `false`」の相手を1回攻撃して確認する。
- **候補ウィンドウの条件**: `arenaFindEnemies` が 13 位より上を含む条件は未解明。自分の順位変動・サーバー側の候補生成タイミング・防衛編成などが候補か。`--candidate-retries` を増やしても見つからない場合があるため、候補生成の規則を追加調査する。
- **Grand Arena CLI**: `grandAttack` のペイロード/レスポンスは実機確認済み（上記）。CLI 対応時は3チーム・3ペット・3バナー、refillable id=20/21、最後の `battles[].result` からの順位読み取りが必要。

## 関連
- CLI: `.agents/skills/arena/SKILL.md`
