# Shop & Reward API Reference

## 概要
ショップ、課金オファー、ギフト、広告ボーナス、およびガチャ（ヒーローの宝箱）に関するAPIリファレンスです。

共通エンドポイント:
```
POST https://heroes-wb.nextersglobal.com/api/
```

---

## 主要メソッド

### shopGetAll - 全ショップ陳列情報
タウンショップ、アリーナショップ、ギルドショップなど、ゲーム内の全ショップのラインナップと現在の更新状況を取得します。

### Request Arguments
| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| (なし) | - | - | - |

### Response Structure
```json
{
  "results": [{
    "ident": "shop",
    "result": {
      "response": {
        "1": {
          "id": 1,
          "slots": {
            "1": {
              "id": 1,
              "reward": { "consumable": { "id": amount } },
              "bought": false,
              "cost": { "starmoney": amount }
            }
          }
        }
      }
    }
  }]
}
```

---

### heroesMerchantGet - ヒーロー商人（ソウルストーンショップ）
ヒーローのソウルストーンショップの状態を取得します。

> 注: 現在の API レスポンスでは null が返されます。

---

### specialOffer_getAll - スペシャルオファー
期間限定の課金パッケージやイベントオファーの情報を取得します。

### Response Structure
```json
{
  "results": [{
    "ident": "offer",
    "result": {
      "response": [
        {
          "id": "...",
          "localeIdent": "...",
          "type": "addBilling",
          "offerType": "bundle"
        }
      ]
    }
  }]
}
```

---

### rewardedVideo_boxyGetInfo - 広告ボーナス (Boxy)
動画広告視聴による報酬（Boxy）の取得状況（視聴回数、残り回数等）を取得します。

### Response Structure
```json
{
  "results": [{
    "ident": "boxy",
    "result": {
      "response": {
        "rewards": [...],
        "boxes": [...],
        "generateTime": 1707760802
      }
    }
  }]
}
```

---

## アイテム・報酬 ID について (Library ID)
ゲーム内の各アイテムや報酬は、`libId` (Library ID) と呼ばれる数値 ID で管理されています。

| ID | 名称 (例) | 備考 |
|----|-----------|------|
| **17** | スタミナポーション (120) | `consumableUseStamina` で使用 |
| **1** | ゴールド | リソース ID |
| **5** | ソウルコイン | ショップ通貨 |

> **TODO**: 今後、`libGet` レスポンスをパースして全アイテム名のマスタデータを構築し、ID から名称へ自動変換できるようにする予定です。

---

## 補足メソッド
*   **`billingGetLast`**: 直近の決済情報（購入履歴）。
*   **`zeppelinGiftGet`**: ツェッペリンギフトの取得状況。
*   **`banner_getAll`**: ゲーム内に表示されるバナー広告のリスト。
*   **`gacha_getInfo`**: ヒーローの宝箱（ガチャ）の提供確率や天井カウントなどの状態。
