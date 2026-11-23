---
layout: page
title: Guild API Reference
---

# Guild API Reference

## 概要
所属クラン（ギルド）の情報、メンバー活動状況、ギルド戦（GW）、クロスサーバーギルド戦（CoW）、レイド（Asgard）、およびPvP（アリーナ等）のステータスを取得します。

## 主要メソッド

### clanGetInfo - クラン基本情報
所属クランの基本情報、メンバーリスト、現在のステータス等を取得します。

### Endpoint
```
POST https://heroes-wb.nextersglobal.com/api/
Content-Type: application/json; charset=UTF-8
```

### Request Arguments

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| なし | - | - | - |

### curl 実行例
```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"clanGetInfo","args":{},"ident":"body"}]}'
```

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "clan": {
          "id": "123456",
          "title": "Clan Name",
          "level": "1",
          "members": {
            "12345678": {
              "id": "12345678",
              "name": "PlayerName",
              "level": "130",
              "clanRole": "4"
            }
          }
        }
      }
    }
  }]
}
```

### clan{} フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `id` | string | クランID |
| `title` | string | クラン名 |
| `level` | string | クランレベル |
| `members` | object | メンバー一覧（キーはプレイヤーID） |

### stat{} フィールド（ギルド活動）

`clanGetInfo` の `stat` には自分の当日・累計の活動ポイントが入る（実測）。

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `todayActivity` | number | 自分の当日ギルド活動ポイント |
| `activitySum` | number | 自分の累計ギルド活動ポイント |
| `todayDungeonActivity` | number | 自分の当日ダンジョン活動ポイント |
| `dungeonActivitySum` | number | 自分の累計ダンジョン活動ポイント |
| `activityForRuneAvailable` | boolean | **今日のグリフ（ルーン）強化による活動ポイントをまだ獲得できるか**。true = 未獲得、false = 獲得済み（グリフ強化の直後に true → false へ変わることを実測確認） |

`hw-genie quests` はギルド活動のグリフ強化（`heroEnchantRune`）の実行可否判定に
`stat.activityForRuneAvailable` を使う（設定は `account_configs` の
`guild_glyph_defaults`）。日次リセットはサーバー時刻（`serverResetTime` /
`userGetInfo` の `nextServerDayTs`）に従う。クエスト `10050`
（Earn 1750 Guild Activity points）はエナジー消費でも進むため、グリフ実施の
判定には使わない。

---

### clanWarGetBriefInfo - ギルド戦 (GW) の概要
現在のギルド戦（ゴールドリーグ等）の進行状況、対戦相手、スコアなどの概要を取得します。

### Endpoint
```
POST https://heroes-wb.nextersglobal.com/api/
```

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "tries": 0,
        "targets": 0,
        "nextWarTime": 1775811600,
        "hasActiveWar": true
      }
    }
  }]
}
```

### Response フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `tries` | int | 残り攻撃回数 |
| `targets` | int | 攻撃可能なターゲット数 |
| `nextWarTime` | int | 次回の戦争開始予定時間（Unixタイムスタンプ） |
| `hasActiveWar` | bool | 現在戦争中かどうか |

---

### crossClanWar_getBriefInfo - クロスサーバーギルド戦 (CoW) の概要
クロスサーバーギルド戦（CoW）の概要、マッチング状況、順位などを取得します。

### Endpoint
```
POST https://heroes-wb.nextersglobal.com/api/
```

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "status": "active",
        "hasActiveWar": false,
        "heroTries": 0,
        "titanTries": 0
      }
    }
  }]
}
```

### Response フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `status` | string | ステータス（例: "active"） |
| `hasActiveWar` | bool | 現在戦争中かどうか |
| `heroTries` | int | ヒーロー戦残り攻撃回数 |
| `titanTries` | int | タイタン戦残り攻撃回数 |

---

### clanRaid_getInfo - クランレイド情報
アスガルド（クランレイド）の進行状況、ボスのHP、攻撃回数などを取得します。

### Endpoint
```
POST https://heroes-wb.nextersglobal.com/api/
```

### Request Arguments

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| なし | - | - | - |

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "boss": { "teams": [...] },
        "shop": {
          "6": {
            "branch": "",
            "buffId": 66,
            "buffValue": 3,
            "buyLimit": 1,
            "cost": { "coin": { "30": 50 } },
            "rank": 3,
            "requirement": "",
            "boughtCount": 0
          }
        },
        "coins": 1000
      }
    }
  }]
}
```
> ※ 構造が複雑なため、解析の際は `jq` 等で内容を確認してください。

### Response フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `boss` | object | ボスフェーズの情報（`teams` にボスのユニット構成） |
| `nodes` | dict | ミニオンフェーズのノード情報 |
| `shop` | dict | ショップ在庫（キーは slotId → 商品詳細） |
| `coins` | int | 所持コイン数（Osh の Realm Traveler では Valor Emblem） |
| `buffs` | dict | `buffId → {id, value}` のバフ定義 |
| `stats.currentBoss` | string | 現在のボス ID |
| `stats.weekStart` | string | 週の開始タイムスタンプ |

### shop{} フィールド（slot 単位）

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `buffId` | int | バフ ID（Osh の Realm Traveler は 61〜81、Maestro の Phantom Orchestra は 112〜133 で固定） |
| `buffValue` | int | バフ効果量（% 表示用） |
| `buyLimit` | int | 購入上限（Valor Emblem 商品は 1） |
| `cost` | object | 価格。`{"coin": {"30": 価格}}` が Valor Emblem、`{"gold": ...}` がゴールドバフ |
| `rank` | int | レアリティ（1=150 / 2=100 / 3=50 の目安） |
| `boughtCount` | int | 購入済み回数（>= buyLimit で購入済み） |
| `requirement` / `branch` | string | 条件・ブランチ（現在は空文字） |

> **Osh 週のラインナップ**: slot 1〜5 はゴールドバフ（`cost.gold` 100万、buyLimit 5）、
> slot 6〜21 が Valor Emblem 商品（価格は週替わりで 50/100/150 のいずれか）。
> ラインナップ（slot→buffId）は全アカウント共通で固定、`boughtCount` と `coins` はアカウント別。
>
> **Maestro 週のラインナップ**: slot 構成・価格帯は Osh と同じ（slot 1〜5 はゴールドバフ、
> slot 6〜21 が Valor Emblem 商品）だが、buffId の範囲は 112〜133 で、slot→バフの対応は
> 週ごとに変わる場合がある（`hw-genie asgard-shop` は確認済みラインナップに基づく
> slot→順位の固定優先度表 `MAESTRO_PRIORITY` と組み合わせ最適化で購入プランを選定する。
> 週が変わった際は表の更新が必要）。
>
> 2026-08 実測の slot → バフ名（価格, buffId）:
>
> | slot | バフ名 | 価格 | buffId |
> |------|--------|------|--------|
> | 11 | Unbridled Energy | 100 | 118 |
> | 15 | At the Speed of Light | 50 | 125 |
> | 9 | Pillar of Confidence | 50 | 116 |
> | 7 | Effective Tactics | 150 | 114 |
> | 17 | Secret Weapon | 150 | 128 |
> | 16 | Strength in Perseverance | 150 | 127 |
> | 14 | At the Limit | 50 | 122 |
> | 12 | Perfect Storm | 100 | 119 |
> | 10 | Charmer's Skill | 100 | 117 |
> | 19 | Through a Prism | 50 | 133 |
> | 8 | The Tireless | 50 | 115 |
> | 6 / 13 / 18 / 21 | （バフ名不明・購入対象外） | 150 / 100 / 50 / 100 | 112 / 120 / 121 / 132 |
> | 1〜5 | ゴールドバフ（バフ名不明） | 100 万ゴールド | 113 / 123 / 126 / 129 / 130 |

---

## Titan Arena (ToE)

`titanArena*` 系の RPC はギルドレイド API とは別系統だが、プレイヤー個人の
PvP コンテンツとして同じく Hero Wars 対戦機能を共有する。本リポジトリの
HW-Genie CLI / userscript 連携はこれらを使って壁Rival 攻略や Tier 自動
進行を行う。

### titanArenaGetStatus - 現在の Tier / 壁 / rival 一覧を取得

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `status` | string | `disabled` / `peace_time` / `battle` のいずれか |
| `tier` | int | 現在のティア（1〜maxTier） |
| `maxTier` | int | 到達可能な最大ティア |
| `canRaid` | bool | `true` の場合 `titanArenaStartRaid` でまとめてライバルを一掃できる |
| `defenders` | object | 自分の防衛タイタン（HP / artifact 等） |
| `rivals` | object | ライバル一覧（`rivalId → {attackScore, titans, ...}`）。負の ID（例: `-470711`）は壁 / 練習用ライバル |

### titanArenaStartBattle - 個別ライバル戦を開始

`rivalId` と `titans`（5 体のタイタンID）を指定してバトルを開始する。`titans`
は `teamGetAll.titan_arena` の保存チームに依存せず、任意の 5 体を渡せる
（Dave アカウントで `-470711` / `[4003, 4023, 4004, 4001, 4000]`
の組み合わせが受理されることを実機検証済み）。

```json
{
  "calls": [{
    "name": "titanArenaStartBattle",
    "args": {"rivalId": "-470711", "titans": [4003, 4023, 4004, 4001, 4000]},
    "ident": "body"
  }]
}
```

レスポンスの `response.battle` を `titanArenaEndBattle` まで保持する
必要がある（`seed` 等を含めてサーバーが同一バトルとして検証する）。

### titanArenaEndBattle - 結果送信

`progress` と `result`（win / stars）が必要。**これらはクライアント側で
バトルをシミュレーションした正確な値**でないとサーバーが `Invalid battle`
（`serverVersion: 292`）で弾く。HW-Genie 単体では簡易な power ベース見積
のみ可能なため、**完全勝利には userscript 側の `Game.BattlePresets` /
`BattleInstantPlay`（`get_titanPvpManual`）を使うハイブリッドフローが必要**。

`hw-genie toe attack --engine estimate` は power ベースの `progress` を
送るため、サーバ検証で `Invalid battle` になる。`--engine hybrid` を指定
すると、auth server の `/toe/job` キュー経由で userscript が本物の
`BattleCalc` を呼び出し、その結果を `EndBattle` に利用する。

EndBattle が失敗した場合（`Invalid battle` に限らず `NotFound` /
`NotAvailable` を含む）は、ローカルの estimate が win でも勝利扱いに
せず `unverified` として記録する。`NotFound` / `NotAvailable` は
サーバー側で既にバトルが閉じられた、または再送が競合した可能性がある
ため WARNING 表示で継続し、次の試行に進む。

### titanArenaStartRaid / titanArenaEndRaid - 一括レイド（canRaid=true）

`canRaid` が `true` のとき、ライバルを 1 リクエストで全滅させるレイドが
可能。`titanArenaStartRaid` に `titans` だけを渡すとレスポンスの
`attackers` / `rivals` を受け取る。`rivals` の各 `rivalId` ごとに
`progress` / `result` を組み立て、`titanArenaEndRaid(results={rivalId: {progress, result}})`
でまとめて送信する。

レイドのシミュレーションに渡す battle は、実際の `titanArenaStartBattle`
battle（`hw-genie toe attack --debug` で取得）と `battleGetReplay` の両方で
形を確認している。`battleGetReplay` は ToE 専用ではなく汎用的な battle RPC
で、replay では `seed` / `startTime` が文字列に再シリアライズされるが、
live の battle payload は数値である:

| フィールド | 値 |
|-----------|-----|
| `attackers` | StartRaid レスポンスの `attackers`（`{titanId: hero}`）。なければ `response["team"]`、さらに `status["defenders"]`。要求した 5 体の titan ID 集合と一致しない場合はレイドを中止する |
| `defenders` | `[{titanId: hero}]`（1 要素の配列）。StartRaid の rival は `rivals[rivalId].team` にロスターを持つ。GetStatus の rival ロスターと titan ID 集合が一致し、かつ要求した 5 体（`len(titans)`）であること |
| `seed` | rival ごとの `seed`（数値。replay では文字列。例 `1991754172`）。`0` や範囲外は欠落扱いで GetStatus にフォールバック |
| `startTime` | 数値 epoch（秒。実測例 `1792726404`、`date +%s` と数秒差）。StartRaid レスポンスに値があればそれを使い、無ければシミュレーション時のクライアント時刻を使う（live の StartBattle battle と同じ慣習） |
| `typeId` | rivalId（文字列） |
| `userId` | StartRaid レスポンスの `userId`。なければ `titanArenaGetStatus` の `userId`、さらに認証ヘッダーの `x-auth-user-id`、どれも無ければ `""` |

`titanArenaGetStatus.rivals[rivalId].seed` と replay の `seed` は一致し、
`progress[].seed` は同じ値の符号付き 32bit 表現になる。rival の
`titans` は replay の `defenders[0]` と `state` キー以外一致するため、
`state`（現在 HP / energy / isDead）は battle から除去する。
ロスターの一貫性も検証する。`attackers` は要求した 5 体の titan ID
集合と一致し、`defenders` は GetStatus の rival ロスターと ID 集合が
一致する（GetStatus 側にロスターが無い場合は 5 体である）必要がある。
食い違い・切り詰められたロスターは推測せずにスキップし、個別バトルで
再試行する。

`seed: 0` や推測形状の defenders を渡すとページ内エンジンの計算が失敗し、
userscript は全滅（dummy loss）を返す。`seed` が欠落・無効、または
ロスターが不整合な rival は battle を組み立てずスキップし、スキップ件数を
警告として 1 行で報告する（該当 rival は個別バトルで再試行される）。
dummy loss は EndRaid に含めない（サーバーがレイド全体を無効化し得るため）。

`hw-genie toe run` はこの形で Raid をステージ開始時に実行し、レイドで
検証済み結果が 1 件も得られなかった場合は同一 Tier 内ではレイド再試行を
打ち切り、Tier が変われば再試行する（抑止は Tier 単位。Tier の同一判定は
数値正規化したキーで行い、API が `8` / `"8"` / `8.0` を揺らしても抑止を
維持する）。`tier` を欠落した status は別 Tier 扱いで抑止をリセットする
（不明な状態で古い抑止を引き継がないため）。残りの rival は個別バトル
（`titanArenaStartBattle`）で継続する。
レイドで負けた rival は `attackScore < threshold` のまま残るため、続く
個別バトルの対象になる。レイドが一部の rival だけを落とした場合は、
個別バトルの前に `titanArenaGetStatus` を再取得し、レイドで既にクリア
した rival を攻撃対象から除外する（古い snapshot による
`beaten up already` の空打ちを避ける）。`--progress line` の進捗行は
Tier 内の rival 位置（例: `T7 3/9 rival ...`）を表示する。

レイドの完了判定は、EndRaid の `results` が全 rival 分そろい、各エントリに
parse 可能な `attackScore` がある場合にそれを使う。欠落・部分エコー・
スコア欠落時は `titanArenaGetStatus` を再取得し、`status == "battle"` かつ
残 rival がすべて閾値以上かを確認する（スキップした rival が既にクリア済み
でも `CompleteTier` に到達できる）。どちらも得られない場合は完了を推測せず
`completed=False` / `no_verified=True` として個別バトルへフォールバックし、
次パスの status で判定する。根拠は raid サマリーの `completed_basis`
（`endraid` / `status` / `unverified`）に残す。EndRaid が成功していない
場合（StartRaid/EndRaid 失敗を含む）は `completed_basis` は `None`。

`StartRaid` / `EndRaid` の API 失敗は `api_error`、payload 不整合は
`payload_error` として区別し、Tier サマリーの `raid_errors` と WARNING で
可視化する（`multi toe` のサマリー表の後に `⚠️ Raid errors` フッター行を
出す）。どちらも個別バトルへフォールバックするため `errors` には入れず、
アカウント失敗にはしない。抑止理由 `raid_fallback` は最新 Tier の状態を
表し、Tier が変わるたびにクリアされる（新しい Tier のレイドも抑止された
場合のみ再設定）。スキップした rival は battle エントリの `skipped` /
`reason` で区別し、件数の警告は実行ログ（stdout）に出す。`raid_fallback` /
`completed_basis` / `skipped` はプログラム・テスト向けの記録で、サマリー
表には表示しない。

### titanArenaCompleteTier - ティアクリア宣言

tier 内ライバルを全滅させた直後に呼ぶ。翌ティアへ進むか、`titanArenaEndRaid`
のレスポンスから自動で発火できる。

### titanArenaFarmDailyReward - 日次報酬受け取り

ティア完了後に 1 日 1 回だけ呼んで `titan_arena_points_reward` を獲得
する。

### HW-Genie での ToE 自動攻略

* `hw-genie toe status -a Dave` で現在の Tier / rivals / defenders を
  表示
* `hw-genie toe attack -a Dave` で未クリア 1 体を自動攻撃（`--rival`
  省略時は `titanArenaGetStatus` から `attackScore < 250` の rival を
  `(attackScore asc, wall優先, power asc)` で選択、`--titans` 省略時は
  `teamGetAll.titan_arena` から自動解決。`--dry-run` / `--estimate-only`
  で EndBattle を抑止）。明示指定も可能:
  `hw-genie toe attack -a Dave --rival -470711 --titans 4003 4023 4004 4001 4000 --dry-run`
* `hw-genie toe run -a Dave` で Tier 全体を自動攻略（`--titans`
  省略時は `teamGetAll.titan_arena` を自動解決。`canRaid=true` なら一括
  レイド、なければ `attackScore < threshold`（既定 250）の rivals を
  自動選択して個別バトル → `CompleteTier` → 日次報酬受け取り）。
  新 Tier に進むと次の周回で改めて `canRaid` を確認するため、
  Raid 可能 Tier は常に Raid から開始される。レイドが検証済み結果を
  1 件も返せなかった場合は **その Tier 内でのみ** レイド再試行を打ち切り、
  残りの rival を個別バトルで継続する（Tier が変われば Raid を再試行。
  レイドで負けた rival は `attackScore < threshold` のまま残るため、
  そのまま個別バトルの対象になる）。
  明示編成: `hw-genie toe run -a Dave --titans 4003 4023 4004 4001 4000`
  - `--threshold 250` で対象閾値を変更可能（`--stop-on-loss` で初回敗北時に中断）
  - 負けのバンク方針（`--seeds` 既定 10）。既定は **負けも毎回バンク**する。
    `attackScore` はバトルスコアの max 保持（`attackScoreEarned` が増分）なので、
    劣る負けを送っても無害（earned 0）で、詰まった rival の加点に最も有効。
    勝ちのないパスでも、バンクでスコアが伸びた間はパスを繰り返し、
    伸びなくなったら停止する。`--no-end-on-loss` で「ベスト更新時のみ」バンク
    （最速掃引）、`--no-bank-best-loss` を併用した場合のみ一切バンクしない。
  - best-loss fallback（+1 の追加 StartBattle + EndBattle）は **廃止** した
    （設計 S5）。最初の検証済み敗北は必ず改善バンクになるため、追加試行は
    到達不能な dead code だった。`--max-attempts` の上限外の試行はない。
    `--no-bank-best-loss` は改善バンクのみを無効化する。
  - レイドのシミュレーションは全 rival 分を auth server にまとめて投入し、
    userscript が 1 tick で連続処理する（250ms 間隔）。`--engine hybrid` の
    実行終了時には `ToE bridge timing (…): post / queue / sim / poll / total` の
    平均が出るので、遅い区間の切り分けに使う。
  - 回転テーブル `STATIC_TEAM_ROTATION` には水3火2の
    `[4003, 4004, 4014, 4001, 4010]` を含む（Tier8 の光壁 `-480913`
    に 15 seeds で WIN の実績）。
  - `--engine estimate`（既定）: アプリ単体。勝敗は power ベースで
    簡易判定。完全勝利には `--engine hybrid` を指定
  - `--engine hybrid`: auth server 経由で userscript のバトルエンジン
    に依頼。ブラウザで `auth-server` が稼働 + `hw-genie-auth-capture.user.js`
    が Titan Arena 画面に入った状態で動作

---

### clanRaid_shopBuy - クランレイドショップ購入
Osh の Realm Traveler 等、クランレイドのショップでアイテム（バフ）を購入します。

### Request Arguments

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `slotId` | int | ✓ | 購入するスロット ID（`clanRaid_getInfo` の `shop` キーと対応） |

### curl 実行例
```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"clanRaid_shopBuy","args":{"slotId":17},"context":{"actionTs":3247542},"ident":"body"}]}'
```

---

### clanRaid_startBossBattle - ボス戦開始
Asgard のボス戦を開始します。チーム（5 ヒーロー + ペット + favor）を指定します。

### Request Arguments

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `heroes` | int[5] | ✓ | 攻撃側のヒーロー ID（5 体） |
| `pet` | int | | ペット ID（例: 6002 / 6005） |
| `favor` | dict | | `heroId -> patronPetId` のマップ（例: `{"12":6003,"13":6002}`） |

### curl 実行例
```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"clanRaid_startBossBattle","args":{"heroes":[50,60,58,51,72],"pet":6005,"favor":{"50":6005,"51":6001,"58":6008,"60":6006,"72":6002}},"context":{"actionTs":15065795},"ident":"body"}]}'
```

### Response フィールド

`response.battle` にバトルオブジェクトが返ります。

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `battle.type` | string | `clan_raid` |
| `battle.seed` | int | バトルシード |
| `battle.typeId` | int | バトル種別 ID（実測: `11002`） |
| `battle.userId` | string | 攻撃側プレイヤー ID |
| `battle.attackers` | object | 攻撃側ユニットの状態（hero ID → 状態） |

> **重要**: `startBossBattle` を送った時点で `clanRaid_getInfo` の `bossAttempts` が 1 減ります。`endBossBattle` を送らないとバトルは未確定のまま残り、`userStats.damage` は増えません（`clanRaid_getInfo` を繰り返しても解決されません）。

---

### clanRaid_endBossBattle - ボス戦終了（オートバトル確定）
ボス戦の結果を送信します。**空の `progress` を送ると、サーバーがクライアント側シミュレーションの不備とみなしてサーバー側オートバトルで結果を確定します**（実測 serverVersion 300, 2026-11-17）。

### Request Arguments

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `result` | object | ✓ | `{"win": bool, "stars": int}` |
| `progress` | array | | クライアント側シミュレーション結果。空配列 `[]` でオートバトル確定 |

### curl 実行例（オートバトル）
```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"clanRaid_endBossBattle","args":{"result":{"win":false,"stars":0},"progress":[]},"context":{"actionTs":0},"ident":"body"}]}'
```

### Response フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `response.result.win` | bool | 勝敗 |
| `response.result.stars` | int | スター数 |
| `response.result.afterInvalid` | bool | `true` = 空/不正 progress によりサーバー側オートバトルで確定 |
| `response.result.damage` | object | defender インデックス → 与ダメージ（例: `{"1":35790884,"2":0}`） |
| `response.result.raidId` | string | レイド ID |
| `response.result.level` | string | ボスレベル |
| `response.result.serverVersion` | int | サーバーバージョン（実測: `300`） |
| `response.damage` | object | `result.damage` と同じ |
| `response.states` | object | バトル後のボス状態（`state.hp` など） |
| `response.replay` | string | リプレイ ID |

> クライアントが通常送る `progress` は `[{"v":300,"b":0,"seed":<int>,"attackers":{"input":["auto",...],"heroes":{...}},"defenders":{"input":[],"heroes":{...}}}]` の形ですが、`hw-genie asgard-raid` はシミュレーションを行わず空配列でオートバトルを利用します。

#### `clanRaid_getInfo` のスコア関連フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `bossAttempts` | int | 残りボス攻撃回数（`startBossBattle` で 1 減る） |
| `attempts` | int | ミニオンフェーズの残り攻撃回数 |
| `userStats.damage` | string | 自分の今週の累計ダメージ（数値文字列） |
| `userStats.points` | string | 自分のクランポイント |
| `stats.points` | string | クラン合計ポイント |

---

### clanRaid_farmRatingReward - 週間ランキング報酬の受け取り

Asgard の週間ランキング報酬を受け取ります。ギルド（`standingClan`）と個人（`standingUser`）の2種類があり、**それぞれ週に1回**受け取れます。

### Request Arguments

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `type` | string | ✓ | `standingClan`（ギルド）または `standingUser`（個人） |

### curl 実行例
```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"clanRaid_farmRatingReward","args":{"type":"standingClan"},"context":{"actionTs":2649805},"ident":"body"}]}'
```

```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"clanRaid_farmRatingReward","args":{"type":"standingUser"},"context":{"actionTs":2649805},"ident":"body"}]}'
```

> `hw-genie asgard-raid run` / `multi asgard-raid` はボス戦の最後にこの2種類を順に呼びます。受取済みなどでエラーになった場合は**スキップ**として扱い、ラン整体を失敗にしません（認証エラーのみ再送出）。`--dry-run` では受け取りません。

---

### clanRaid_logBoss - ボス戦の戦闘履歴

ギルドメンバーのボス戦履歴を取得します。`hw-genie asgard-raid team import-log` はこの履歴からチーム構成（heroes / pet / favor）を復元します。

### Request Arguments

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| なし | - | - | - |

### Response Structure

```json
{
  "response": {
    "<userId>": {
      "<battleId>": {
        "userId": "12345678",
        "typeId": "11002",
        "type": "clan_raid",
        "startTime": 1795371600,
        "endTime": 1795371600,
        "seed": "2094873982",
        "attackers": {
          "50": {"id": 50, "type": "hero", "petId": 6005, "favorPetId": 6005},
          "60": {"id": 60, "type": "hero", "petId": 6006, "favorPetId": 6006},
          "6005": {"id": 6005, "type": "pet", "petId": null, "favorPetId": null}
        },
        "defenders": {"1": {"id": 2025, "type": "hero"}},
        "result": {"win": false, "stars": 0, "damage": {"1": 35790884, "2": 0}}
      }
    }
  }
}
```

### フィールド

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `response[userId][battleId]` | object | プレイヤー別・バトル別の履歴（レスポンスは大きくなり得る） |
| `attackers` | object | 攻撃側ユニット。`type="hero"` がヒーロー、`type="pet"` がチームペット |
| `attackers[].favorPetId` | int | そのヒーローに付けた patron（favor の値） |
| `attackers[].petId` | int | ヒーロー側の patron（通常 `favorPetId` と同値） |
| `startTime` | int | 攻撃時刻（秒）。履歴の攻撃順ソートに使う |
| `id` | int/string | バトル ID |
| `result.damage` | object | defender ごとの与ダメージ |

> `import-log` は `attackers` の `type="hero"` を heroes、`favorPetId`（無ければ `petId`）を favor、`type="pet"` をチーム pet として復元します。5体未満・重複ありの不完全な履歴はスキップし、6件以上ある場合は slot 1〜5 への対応が曖昧になるためエラーにします（`--latest` を付けると直近5件を攻撃順に slot 1〜5 へ割り当てます）。

---

## 補足メソッド
*(その他のメソッドは必要に応じて追加してください)*
*   **`clan_prestigeGetInfo`**: クランの威信（Prestige）レベルと進行状況。
*   **`clanGetActivityRewardTable`**: クラン活動報酬テーブル。
*   **`clanGetPrevData`**: クランの過去データ。
*   **`clanWarGetWarlordInfo`**: ギルド戦におけるウォーロード（軍師）の配置や設定。
*   **`crossClanWar_getSettings`**: クロスサーバーギルド戦の設定やルール。
*   **`titanArenaGetChestReward`**: タイタンアリーナの宝箱報酬。
