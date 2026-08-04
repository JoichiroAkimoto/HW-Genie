---
layout: page
title: Hero Wars RPC API Index
description: Hero Wars RPC API の全体像と機能別ドキュメントへの入り口
---

# Hero Wars RPC API Index (Start Here)

このドキュメントは、Hero Wars API に関する情報の入り口です。
APIの全体像と、機能ごとの詳細ドキュメントへのリンクをまとめています。

---

Hero WarsのAPIは、**「機能ごとに独立したメソッド (`xxxGetAll` や `xxxGetInfo`) を用意し、それらを1つのHTTPリクエストに配列として束ねて送信する」**というRPC（リモートプロシージャコール）スタイルを採用しています。

1つのHTTPリクエスト（`POST /api/`）の中に、`calls`という配列で多数のRPCメソッド呼び出し（APIコール）を詰め込み、サーバーからプレイヤーの全データを一括で取得したり、必要なデータだけをピンポイントで取得したりすることができます。

## 今後の課題 (TODO)
- [ ] **Library ID (アイテムID) のマスタデータ取得**: `libGet` 相当のデータ（アイテム名、ヒーロー名、スキル定義等）を自動取得し、ローカルに保存・参照する仕組みの構築。現状は ID のみの表示となっている箇所が多い。
- [ ] **各 API の詳細な Request Payload 仕様の追記**: 現在は主要なもののみ。

## カテゴリ別API詳細ドキュメント

詳細仕様は以下のファイルを参照してください:

- [CHAT_API.md](./CHAT_API.html) (チャット機能)
- [USER_API.md](./USER_API.html) (ユーザー・アカウント関連)
- [UNIT_API.md](./UNIT_API.html) (ヒーロー・タイタン・ペット)
- [GUILD_API.md](./GUILD_API.html) (ギルド・PvP関連)
- [QUEST_API.md](./QUEST_API.html) (クエスト・ミッション・報酬)
- [SHOP_API.md](./SHOP_API.html) (ショップ・資産関連)
- [ADVENTURE_API.md](./ADVENTURE_API.html) (PVE・アドベンチャー)

## 主なRPCメソッド一覧（概要）

### 1. ユーザー基本情報・設定
プレイヤーの基礎データや設定を取得します。
*   **`userGetInfo`**: プレイヤーID、レベル、経験値、アバターなどの基本情報。
*   **`getTime`**: サーバー時刻の同期。
*   **`settingsGetAll`**: ゲーム設定（音量、通知設定など）。
*   **`tutorialGetInfo`**: チュートリアルの進行状況。
*   **`mechanicAvailability`**: 特定の機能（タイタン、ペットなど）がアンロックされているかどうかの確認。
*   **`registration`**: ギフトID (`giftId`) が含まれており、ギフト付きリンクを踏んだ際の処理や、セッション開始の登録を兼ねています。

### 2. インベントリ・資産・ショップ
所持アイテムや通貨、購入可能な商品に関連する機能です。
*   **`inventoryGet`**: 所持している全アイテム（装備、ポーション、ルーンなど）のリスト。
*   **`billingGetAll` / `billingGetLast`**: 課金通貨（エメラルド等）や購入履歴の状態。
*   **`shopGetAll`**: タウンショップ、アリーナショップ、ギルドショップなど、全ショップのラインナップと更新状況。
*   **`heroesMerchantGet`**: ヒーローのソウルストーンショップの状態（推測）。
*   **`specialOffer_getAll`**: 期間限定の課金オファー（x4セールなど）。

### 3. ユニット管理（ヒーロー・タイタン・ペット）
育成キャラクターたちのステータス情報です。
*   **`heroGetAll`**: 全ヒーローのレベル、ランク、スキル、スキン、グリフ、アーティファクト等の詳細。
*   **`titanGetAll`**: 全タイタンのステータス。
*   **`pet_getAll`**: 全ペットのステータス。
*   **`teamGetAll`**: 保存されている防衛チームや攻撃チームの編成情報。
*   **`titanSpirit_getAll`**: タイタンの「精霊（トーテム）」の状況。
*   **`artifactGetChestLevel` / `titanArtifactGetChest`**: アーティファクトチェストのレベルや開封状況。

### 4. クエスト・ミッション・報酬
デイリー活動や報酬に関連する機能です。
*   **`missionGetAll`**: キャンペーン（ストーリーモード）の各ステージクリア状況（星の数など）。
*   **`questGetAll`**: デイリークエスト、メインクエストの進捗と報酬受け取り状況。
*   **`dailyBonusGetInfo`**: ログインボーナスの状況。
*   **`mailGetAll`**: ゲーム内メール（運営からのお知らせ、報酬配布）。
*   **`battlePass_getInfo`**: バトルパス（シーズンパス）の進捗と報酬。

### 5. ギルド (Clan)・ソーシャル
ギルド活動やチャット機能です。
*   **`clanGetInfo`**: 所属ギルドの情報、メンバーリスト。
*   **`clanWarGetBriefInfo`**: ギルド戦（ゴールドリーグ等）の概要・対戦相手。
*   **`clanRaid_getInfo` / `clanDomination_getInfo`**: ギルドレイド（Asgard）の進捗。
*   **`chatGetAll`**: チャット履歴取得（chatType指定）。
*   **`chatServerSubscribe`**: リアルタイムチャット購読。
*   **`userGetAvailableStickers`**: 利用可能なスタンプ一覧。
*   **`friendsGetInfo`**: フレンドリストとギフト交換状況。

### 6. ゲームモード（PvP / PvE）
各コンテンツの攻略状況です。
*   **`arenaGetAll`**: アリーナの順位、防衛編成、報酬タイム。
*   **`towerGetInfo`**: タワーの登頂状況。
*   **`expeditionGet`**: 飛行船（遠征）の進行状況と回収可能な報酬。
*   **`adventure_getActiveData`**: ペットアドベンチャーの進行状況。
*   **`titanArenaCheckForgotten`**: タイタンダンジョンの状況確認。
*   **`grandArena`** (通常はグランドアリーナ用もあります)

### 7. イベント・その他
*   **`eventPicker_getInfo`**: 現在開催中のスペシャルイベント一覧。
*   **`newYear_getInfo`**: （季節イベント）新年イベント等の特定イベント情報。
*   **`gacha_getInfo`**: 英雄の宝箱（ガチャ）の状態。
*   **`rewardedVideo_boxyGetInfo`**: 広告視聴ボーナス（Boxy）の状態。
