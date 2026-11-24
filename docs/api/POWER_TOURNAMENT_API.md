---
layout: page
title: Power Tournament API Reference
description: Power Tournament（Hero / Titan）の状態・グループランキング・報酬受取 API
---

# Power Tournament API Reference

Power Tournament は Hero / Titan のどちらか一方の戦力を伸ばしてポイントを稼ぐイベントです。
開催中の種別は `powerTournament_getState` の `type` で判定します（実測: `"titans"`。
Hero 開催時は `"heroes"` と推定。`hw-genie` は両方を `Titan` / `Hero` に正規化します）。

- グループ順位は `powerTournament_getGroupInfo` の `points` を降順ソートして求めます
  （明示的な順位配列は返りません）。同点は **同じ順位** になります（競技順位:
  900, 900, 500 → 1, 1, 3）。userId 昇順は同点内の表示順を決めるためだけに使い、
  1, 2, 3 のような連番の順位を割り当てるものではありません。
- デイリー報酬は `currentDayRewards` に列挙された `id` を
  `powerTournament_dailyFarm` に渡して受け取ります。**ID を推測してはいけません。**
- 最終ランキング報酬は `finalRewardFarmed` で受取済みかを表示するだけで、`hw-genie` は
  自動受取しません。イベント終了後にゲーム内で受け取ってください。

## Endpoint

```
POST https://heroes-wb.nextersglobal.com/api/
Content-Type: application/json; charset=UTF-8
```

---

## powerTournament_getState - 開催状態

### Request

```json
{"calls":[{"name":"powerTournament_getState","args":{},"context":{"actionTs":0},"ident":"body"}]}
```

### Response Structure

```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "id": 1000000000,
        "startTime": 1700000000,
        "endTime": 1700100000,
        "state": 1,
        "type": "titans",
        "currentDayRewards": [
          {"id": 5, "points": 300, "reward": {"consumable": {"149": 2}}, "isFarmed": 0},
          {"id": 6, "points": 800, "reward": {"consumable": {"20": 2500}}, "isFarmed": 0},
          {"id": 7, "points": 1500, "reward": {"coin": {"24": 2000}}, "isFarmed": 1},
          {"id": 8, "points": 3000, "reward": {"powerTournamentCoins": 3000}, "isFarmed": 0}
        ],
        "currentDailyPoints": 900,
        "currentTournamentPoints": 1234,
        "statueLevel": 1,
        "finalRewardFarmed": 0
      }
    }
  }]
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| `id` | int | イベント ID |
| `startTime` / `endTime` | int | Unix 秒 |
| `state` | int | 開催状態（実測値 `1`） |
| `type` | string | `"titans"` / `"heroes"` |
| `currentDayRewards[]` | array | 当日のポイント報酬。`id` / `points`（閾値）/ `reward` / `isFarmed`（0=未受取、1=受取済み） |
| `currentDailyPoints` | int | 当日ポイント。報酬閾値の判定に使う |
| `currentTournamentPoints` | int | イベント累計ポイント |
| `finalRewardFarmed` | int | 最終ランキング報酬の受取済みフラグ（0/1） |

`hw-genie` は次のすべてを満たす報酬だけを受取対象にします。

- `isFarmed == 0`
- `points`（閾値）が既知
- 当日ポイント（`currentDailyPoints`、無い場合は `currentTournamentPoints`）が既知
- ポイント >= 閾値

ポイントが不明な場合は受取りません（推測しない）。

イベント未開催時の応答は未確認です。`hw-genie` は `NotAvailable` を「未開催」として正常終了し、
それ以外のエラーは失敗として表示します。

---

## powerTournament_getGroupInfo - グループランキング

### Request

```json
{"calls":[{"name":"powerTournament_getGroupInfo","args":{},"context":{"actionTs":0},"ident":"body"}]}
```

引数は不要です。

### Response Structure

```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "users": {
          "1": {"id": "1", "name": "Alice", "lastLoginTime": "1700000000", "level": "130"},
          "2": {"id": "2", "name": "Bob", "lastLoginTime": "1700000100", "level": "130"}
        },
        "points": {"1": 500, "2": 900},
        "rewards": [
          {"place": 1, "reward": {"coin": {"1000000000": 200}}},
          {"place": 2, "reward": {"coin": {"1000000000": 160}}}
        ]
      }
    }
  }]
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| `users` | object | userId → プロフィール。`name` と `lastLoginTime`（Unix 秒）を含む |
| `points` | object | userId → イベントポイント |
| `rewards` | array | 順位ごとの最終ランキング報酬表（`place` → `reward`） |

順位は `points` の降順で決まります。同点は同じ順位を共有します（競技順位:
900, 900, 500 → 1, 1, 3）。userId 昇順は同点内の表示順を決めるためだけに使い、
連番の順位を割り当てるものではありません。`users` はグループ全員分が返るため、
上位 N 名の抽出と自分の順位計算はクライアント側で行います。

`points` が無い / object でない `getGroupInfo` の成功応答は、空ランキングではなく
異常応答として扱い、エラー表示（失敗計上）にします（`points: {}` は正当な空ランキング）。

---

## powerTournament_dailyFarm - 報酬受取

### Request

```json
{"calls":[{"name":"powerTournament_dailyFarm","args":{"id":6},"context":{"actionTs":0},"ident":"body"}]}
```

`id` は `currentDayRewards[].id` をそのまま渡します。

### 受取済みの場合

```json
{
  "results": [{
    "ident": "body",
    "error": {
      "name": "NotAvailable",
      "description": "already farmed",
      "call": {"name": "powerTournament_dailyFarm", "args": {"id": 5}}
    }
  }]
}
```

`hw-genie` は `NotAvailable` / `limitReached` / `NotEnough` を「スキップ」として扱い、
それ以外の非認証エラーを失敗として集計します。受取呼び出しは `max_retries=0` で送ります
（二重受取を避けるため）。

---

## 最終ランキング報酬

- 開催中は `finalRewardFarmed: 0` で、受取用の ID は `getState` に現れません。
- `hw-genie` は最終ランキング報酬を自動受取しません。`finalRewardFarmed` は表示専用です。
- イベント終了後にゲーム内で受け取ってください。`hw-genie` は推測 ID を送りません。

TODO: もし将来 API が最終報酬の受取経路（`dailyFarm` の ID か別 RPC か、自動配布か）を
公開したら、実応答を確認して対応する。それまでは上記の安全側動作を維持する。

---

## 関連コマンド

```bash
# 状態 + グループ上位5名 + 受取可能報酬の表示のみ
uv run hw-genie power-tournament --dry-run -a <account>

# 受取可能報酬を受け取ってから表示
uv run hw-genie power-tournament -a <account>

# 全アカウント（dry-run は逐次、通常は並列）
uv run hw-genie multi power-tournament --dry-run
uv run hw-genie multi power-tournament
```
