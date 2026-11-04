---
layout: page
title: Unit API Reference
---

# Unit API Reference

## 概要
所持しているヒーロー、タイタン、ペット、およびそれらの育成データ（ステータス、スキル、スキン等）を取得するメソッドです。

## Endpoint
```
POST https://heroes-wb.nextersglobal.com/api/
Content-Type: application/json; charset=UTF-8
```

---

## heroGetAll - 全ヒーロー情報取得

所持している全ヒーローのステータス、レベル、ランク、スキル、スキン、グリフ、アーティファクト等の詳細を取得します。

エレメントギフトのレベルと消費コインは `titanGiftLevel` /
`titanCoinsSpent.consumable.24`、戦闘力は `power` に入ります（下記
[エレメントギフト](#エレメントギフトherotitangift) 参照）。

### curl 実行例
```bash
# 認証ヘッダー (x-auth-*) はブラウザ DevTools の「Copy as cURL」で取得した curl に含まれる
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"heroGetAll","args":{},"context":{"actionTs":1},"ident":"heroes"}]}'
```

### Response Structure
```json
{
  "results": [{
    "ident": "heroes",
    "result": {
      "response": {
        "2": {
          "id": 2,
          "xp": 3625195,
          "level": 130,
          "color": 16,
          "power": 74216,
          "star": 6,
          "skills": {
            "426": 130,
            "427": 130
          },
          "skins": {
            "2": 21,
            "32": 19
          }
        }
      }
    }
  }]
}
```

---

## heroEnchantRune - グリフ（ルーン）強化

ヒーローのグリフ（ルーン）を強化します。`heroGetAll` の各ヒーローの `runes`
配列が tier 順のグリフ値で、強化すると該当 index の値が増加します
（実測: `tier: 2` で `runes[2]` が 0 → 18590）。

ギルド活動の「グリフ強化で活動ポイント獲得（1 日 1 回）」の操作として
`hw-genie quests` から実行できます（`guild_glyph_defaults`）。実行可否は
`clanGetInfo` の `stat.activityForRuneAvailable` で判定します
（[GUILD_API.md](GUILD_API.md) 参照）。

### Request Args

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `heroId` | number | ○ | 対象ヒーロー ID |
| `tier` | number | ○ | グリフのスロット（0 始まり。`heroGetAll` の `runes` の index に対応） |
| `items` | object | ○ | 消費素材（例: `{"consumable": {"1": 1}}`） |

### curl 実行例
```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"heroEnchantRune","args":{"heroId":46,"tier":2,"items":{"consumable":{"1":1}}},"ident":"body"}]}'
```

### Tips
- 破壊的メソッド（グリフ素材を消費する）。`hw-genie` は
  `guild_glyph_defaults.enabled=true` かつ `activityForRuneAvailable=true` の
  ときだけ実行する。
- レスポンス本文はツールでは使用しない（成功/失敗ステータスのみ参照）。

---

## エレメントギフト（heroTitanGift*）

ヒーローごとの「Gift of the Element」を上げる / 降ろすメソッドです。HW-Genie では
`hw-genie gift status|up|reset` と `multi gift-up` / `multi gift-reset` が使います
（`.agents/skills/element-gift/SKILL.md`）。

### 状態は `heroGetAll` から読む
各ヒーローにギフト関連のフィールドが入っています（実測 2026-11-04）。

| フィールド | 意味 |
|-----------|------|
| `titanGiftLevel` | 現在のギフトレベル（0〜30。30 = MAX） |
| `titanCoinsSpent.consumable.24` | これまでに消費したギフトコイン（consumable libId **24**）。未活性化ヒーローは `{}` |
| `power` | ギフト込みの現在 power（`gift reset` の閾値判定に使う） |

### heroTitanGiftLevelUp - ギフトレベル +1

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `heroId` | number | ○ | 対象ヒーロー ID |

- 1 回のコールでレベル +1。ゲームクライアントは MAX まで（30 回分）を
  `ident: body`, `body_1`, … の 1 リクエスト batch で送るため、HW-Genie も同じ形
  （1 体 = 1 リクエスト）で送ります。
- コストは消費済み込みで **0 → 30 = 65,150 コイン**（MAX ヒーローは全員
  `titanCoinsSpent.consumable.24 = 65150`）。残コストは
  `65,150 - titanCoinsSpent` で見積もれます。
- 応答の `response` は `null`、`quests`（デイリー 10023 等の進捗）のみ返ります。
  実際の消費額・到達レベルは `heroGetAll` / `inventoryGet` を読み直して確認します。
- コイン不足などの失敗はコール単位で `error` を返します。
- HW-Genie は返されたエラー名を `core/client.py` の共有集合で分類します:
  `auth` / `InvalidSession`（`AUTH_ERROR_NAMES`、batch 内 per-call でも認証経路に合流）、
  `NotEnough`（`RESOURCE_EXHAUSTED_ERROR_NAMES`＝コイン枯れ、次の体も買えないので打ち切り）、
  `NotAvailable`（`HERO_SPECIFIC_ERROR_NAMES`＝そのヒーローだけ受理されないので継続）。
  **継続するのは実在を確認したヒーロー個別名のみ**で、未知の名前・エラー名が読めない形・
  transport 失敗（`network_or_parse_error`）は全対象に共通の事故として打ち切ります。

### heroTitanGiftDrop - ギフトを降ろす（全額返却）

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `heroId` | number | ○ | 対象ヒーロー ID |

```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"heroTitanGiftDrop","args":{"heroId":61},"ident":"body"}]}'
```

- 返却額は応答の `response.consumable.24` に載ります（実測: `titanCoinsSpent` と
  同額の 65,150 がそのまま返り、`titanGiftLevel` は 0、`titanCoinsSpent` は空に）。
- ゲームクライアントは前に `stashClient`（ポップアップのウィンドウ記録）を挟みますが、
  操作自体には不要で、HW-Genie も送りません。
- 失敗しても資源は消費されないため、1 体失敗しても他のヒーローの drop は続行します。

### Tips
- 破壊的（コインを消費する）ですが、drop で全額戻るため `up → reset` は実質タダです。
  HW-Genie は `up` / `reset` とも `--dry-run`（計画のみ・読み取りだけ）と y/N 確認
  （`--yes` でスキップ、`multi` は `--yes` 必須）を要求します。
- ヒント: ヒーローの表示名は `heroGetAll` に含まれません（ID のみ）。

---

## titanGetAll - 全タイタン情報取得

所持している全タイタンのステータス、レベル、星ランク等の情報を取得します。

### curl 実行例
```bash
# 認証ヘッダー (x-auth-*) はブラウザ DevTools の「Copy as cURL」で取得した curl に含まれる
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"titanGetAll","args":{},"context":{"actionTs":1},"ident":"titans"}]}'
```

### Response Structure
```json
{
  "results": [{
    "ident": "titans",
    "result": {
      "response": {
        "4000": {
          "id": 4000,
          "level": 130,
          "power": 215197,
          "star": 6
        }
      }
    }
  }]
}
```

---

## pet_getAll - 全ペット情報取得

所持している全ペットのステータス、レベル、星ランク等の情報を取得します。

### curl 実行例
```bash
# 認証ヘッダー (x-auth-*) はブラウザ DevTools の「Copy as cURL」で取得した curl に含まれる
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"pet_getAll","args":{},"context":{"actionTs":1},"ident":"pets"}]}'
```

### Response Structure
```json
{
  "results": [{
    "ident": "pets",
    "result": {
      "response": {
        "pets": [
          {
            "id": 6001,
            "level": 130,
            "power": 163935,
            "star": 6
          }
        ]
      }
    }
  }]
}
```

---

## inventoryGet - インベントリ/所持品

所持している全アイテム（装備、ソウルストーン、ポーション、ルーン等）のリストを取得します。

### curl 実行例
```bash
# 認証ヘッダー (x-auth-*) はブラウザ DevTools の「Copy as cURL」で取得した curl に含まれる
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"inventoryGet","args":{},"context":{"actionTs":1},"ident":"inventory"}]}'
```

### Response Structure
```json
{
  "results": [{
    "ident": "inventory",
    "result": {
      "response": {
        "consumable": { "24": 1247415 },
        "gear": { "4": 866 }
      }
    }
  }]
}
```

`response` の各キーがカテゴリ（`consumable` / `gear` / `scroll` / `fragmentScroll` /
`fragmentGear` / `fragmentHero` / `ascensionGear` 等、実測で 14 種）、その値が
`{libId: 個数}` のマップです。`consumable` の libId は消費アイテムを表し、
事前定義された名前・消費方法は `core/consumables.py` のレジストリを参照してください。

---

## consumableUseLootBox - ルートボックス（Loot Box）消費

consumable（`inventoryGet` の `response.consumable`）のうち、ルートボックス
種別のアイテムをまとめて開封します。レジストリ登録済み libId の一括消費は
`hw-genie consumable run` で行えます。

### curl 実行例
```bash
# libId 215（Equipment Fragment Chest）を 48 個消費
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'accept: */*' \
  -H 'content-type: application/json; charset=UTF-8' \
  --data-raw '{"calls":[{"name":"consumableUseLootBox","args":{"libId":215,"amount":48},"context":{"actionTs":1750526},"ident":"body"}]}'
```

### Response Structure
```json
{
  "results": [{
    "ident": "body",
    "result": {
      "response": {
        "48": {
          "fragmentScroll": { "218": 5, "192": 10, "193": 15, "216": 5 },
          "fragmentGear": { "91": 10, "93": 10, "171": 5, "94": 5 }
        }
      }
    }
  }]
}
```

`response` のキーは**消費した数量**（例: `"48"`）で、その値のカテゴリ
（`fragmentScroll` 等）ごとに `{libId: 報酬量}` として報酬が返ります。
`hw-genie` ではこのカテゴリごとの合計を `ConsumableUseResult.rewards` に集計します。

選択式報酬ボックス（Chest of X Titans / Titan of Your Choice / Artifact 系
Chest 等）は args に `playerRewardChoiceIndex` を追加して報酬を選択します
（例: Titan チェストは `2`、Titan of Your Choice は `0`、Artifact チェストは
`4`）。`hw-genie` では `ConsumableInfo.player_reward_choice_index` で
アイテムごとに固定し、消費時に自動で args へ含めます。

マトリョーシカ系アイテム（開封で同種・下位ボックスが再出現するもの）は
args に `recursive: True` を追加するとサーバー側で再帰開封され、開封
リクエスト数・ラウンド数の削減が見込まれるが、定量比較は未実施
（例: `{"libId":509,"amount":17,"recursive":true}`）。
在庫発生時にラウンド数・リクエスト数を recursive あり/なしで比較する。
`hw-genie` では `ConsumableInfo.recursive=True` で自動付与します（
`consumableUseLootBox` 以外のメソッドには付与しません）。付与対象は本番確認済み
3 件（478/509/513：2026-10-07 の multi consumable 本番実行で recursive 付き消費
SUCCESS を確認）と、既知マトリョーシカ
4 件（149/469/492/497：SKILL に libId ではなく名前で記載された既知マトリョーシカ
（Ancient Titan Artifact Chest=149 / Adventure Chest=469 / Cosmic Titans Battle
Chest=492 / Cosmic Battle Chest=497）に該当する libId。サーバー側再帰の実測は
未実施。ユーザー要求により少なくとも
マトリョーシカ系は True とするため保持。実測後に本番確認済みへ昇格）に限定します
（他は実測待ちのため付与しません。同名のみでは不十分のため、508（492 と同名だが
自体の証拠なし）・493（513 と同名）は対象外。tests の8種行
（149/469/492/497＋Doll 系 176/185/187/190）はクライアント側ラウンドループの
既存定義であり、サーバー側 recursive 対象はそのうち SKILL 名記載に該当する
4 件のみ）。
在庫 0 の libId（215・169・47）でのプローブは NotEnough 返却のため、サーバー側
recursive 受容可否の検証としては判定不能（再現手順：在庫 0 確認後に use_consumable を
method=consumableUseLootBox・amount=1・recursive=True で直接呼び出し。47 は
playerRewardChoiceIndex=2 を併せて付与）。
将来 unmeasured 品で recursive 起因の ERROR が出た場合、該当 libId の recursive を False に戻して再実行する（failed 扱いで取り残されるだけでサイレント消費なし）。

他の consumable 種別（`consumableUseStamina` 等）はアイテムごとにメソッドが
異なるため、実測で判明したものだけ `core/consumables.py` のレジストリに
登録しています。未登録アイテムは `consumable run --method <method>` で
明示指定できます。

---

## その他のメソッド
*   **`heroRating_getInfo`**: ヒーローの評価・ランキング情報。
*   **`titanSpirit_getAll`**: タイタンの精霊（トーテム）の状況。
*   **`titanArtifactGetChest`**: タイタンアーティファクト宝箱の状況。
*   **`pet_getChest`**: ペットの宝箱の状況。
*   **`artifactGetChestLevel`**: アーティファクト宝箱のレベル。
*   **`roleAscension_getAll`**: ロール昇格（アセンション）の状況。
