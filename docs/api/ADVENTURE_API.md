# Adventure & Story API Reference

## 概要
ストーリーモード（キャンペーン）の進行状況、ペットアドベンチャー（マルチ/ソロ）、および放置系コンテンツ（遠征）の状況を取得します。

## Endpoint
```
POST https://heroes-wb.nextersglobal.com/api/
Content-Type: application/json; charset=UTF-8
```

---

## 主要メソッド

### campaignStoryGetList - キャンペーンストーリー
キャンペーンの進行状況リストを取得します。

### expeditionGet - 遠征状況
飛行船（遠征）に送り出したキャラクターの帰還時間や報酬取得状況を取得します。

### curl 実行例 (expeditionGet)
```bash
curl -s 'https://heroes-wb.nextersglobal.com/api/' \
  -H 'content-type: application/json; charset=UTF-8' \
  $(cat session.json | jq -r '.headers | to_entries[] | "-H \(.key): \(.value)"') \
  --data-raw '{"calls":[{"name":"expeditionGet","args":{},"context":{"actionTs":1},"ident":"expedition"}]}'
```

### Response Structure (expeditionGet)
```json
{
  "results": [{
    "ident": "expedition",
    "result": {
      "response": {
        "expeditions": [
          {
            "id": "1",
            "finishTime": 1775745000,
            "status": "active"
          }
        ]
      }
    }
  }]
}
```

---

## その他のメソッド
*   **`adventure_getActiveData`**: 現在実行中のマルチプレイヤーアドベンチャーのデータ。
*   **`adventureSolo_getActiveData`**: 現在実行中のソロアドベンチャーのデータ。
*   **`adventure_getPassed`**: これまでにクリア済みのアドベンチャーマップのデータ。
