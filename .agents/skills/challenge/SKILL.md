---
name: challenge
description: Send and battle Hero Wars friendly personal-chat challenges (chatSendChallenge / chatAcceptChallenge) with automatic messageId discovery — the recipient or the sender can battle, either step-by-step or end-to-end.
---
# Challenge (Friendly Battle) Skill (HW-Genie)

## ワークフロー

> **Tip**: アカウントは `uv run hw-genie auth --list` で確認してください。`--user-id` は**プレイヤーID**（`userGetInfo.response.id` やチャットの `userId`）で、認証ヘッダーの `x-auth-user-id` とは別物です。間違えると送信が `notFound` で失敗します。

### 0. 対象 UserID の保存（任意・Multi 向け）

対象が決まっている場合は、アカウント設定 `challenge_defaults` に保存できます（`account_configs` に保存され、リポジトリには実IDを含めません）。

```bash
uv run hw-genie challenge config -a <account> --user-id <相手のプレイヤーID> --type titan
uv run hw-genie challenge config -a <account>          # 現在値を表示
uv run hw-genie challenge config -a <account> --clear  # 削除
```

解決順は `--user-id` > `challenge_defaults.user_id` > エラー、type は `--type` > `challenge_defaults.type` > `titan` です。

### 1. チャレンジ送信

```bash
uv run hw-genie challenge send -a <account> --user-id <相手のプレイヤーID> --type titan
uv run hw-genie challenge send -a <account>  # challenge_defaults を使用
```

- 送信する `heroes` は**バトル対象（防御側）チーム**です。既定では `teamGetAll.challenge_titan`（`--type titan`）または `challenge`（`--type hero`）の**先頭1体**を使用（実機のゲーム操作と同じ1体）。
- `--heroes 4013` のように指定すると対象チームを明示できます（1〜5 体）。
- 成功時は CLI に `✅ Challenge sent to <peer_id>.` と表示されます（API 応答は `{"result": true}`）。失敗時は `reason`（例: `notFound` = 相手ID不正/対象外）を表示して exit 1。
- `--dry-run` は書き込み（`chatSendChallenge`）を行いませんが、チーム解決のため `teamGetAll` などの読み取り API は呼びます。

### 2. messageId の特定

`chatAcceptChallenge` に必要な `messageId` は、personal スレッドにある
`messageType="challenge"` メッセージの `id` です。受信者・送信者のどちらから見ても同じ ID です。専用 API はありません。

```bash
uv run hw-genie challenge list -a <account> --user-id <相手のプレイヤーID>
```

内部では `chatGetAll`（`chatType=personal`, `userId=<相手>`）を呼び、`chat[]` を解析しています。

- `challenge` メッセージの `id` が `messageId`。
- 同じ id を `data.challengeId` に持つ `messageType="replay"` があれば**バトル済み**。
- 未処理（pending）の最新 1 件が `accept` の自動選択対象。
- 受信した pending は `accept`、自分が送った pending は `accept --sent` / `battle --user-id` でバトル。
- `--json` で `message_id` / `sender_id` / `challenge_type` / `power` / `ctime` / `pending` を出力。

### 3. チャレンジをバトル

**受信したチャレンジ（受信者側）**:

```bash
uv run hw-genie challenge accept -a <受信側account> --user-id <送信者のプレイヤーID>
```

**自分が送ったチャレンジ（送信者側）**:

```bash
uv run hw-genie challenge accept -a <送信側account> --user-id <受信者のプレイヤーID> --sent
```

- `--message-id N` を省略すると、対象方向の未処理最新チャレンジを自動選択。
- バトルチームは保存チーム全員（`challenge_titan` / `challenge`）。`--heroes` で上書き可。
- 攻撃側 = `heroes`、防御側 = チャレンジメッセージの `data.heroes`（送信時に指定した対象チーム）。
  送信者側のバトルでは `battle.userId` と `battle.typeId` がどちらも自分になる。
- 結果は `WIN` / `LOSS` とスター数、リプレイ URL
  `https://www.hero-wars.com/?replay_id=<battle.id>` を表示。
- `--dry-run` は書き込みを行いませんが、`teamGetAll` などの読み取り API は呼ぶ場合があります（特定した messageId とチームを表示するのみ）。

### 4. 送信 → バトルを 1 コマンドで

**ドキュメントと同じ（同一アカウントで送信 → バトル）**:

```bash
uv run hw-genie challenge battle -a <account> --user-id <相手のプレイヤーID>
uv run hw-genie challenge battle -a <account>  # challenge_defaults を使用
```

**受信側の別アカウントにバトルさせる**:

```bash
uv run hw-genie challenge battle -a <送信元account> --accept-account <受信側account>
```

- `--user-id` モード: 送信（対象1体）→ 自分のスレッドを `--wait`（既定 10 秒）までポーリング → messageId 特定 → 同じアカウントでバトル。
- `--accept-account` モード: 2 つとも登録済みアカウントが必要。送信元の `userGetInfo` と受信側の `userGetInfo` からプレイヤーIDを解決し、受信側スレッドで messageId を特定してバトル。
- `--heroes` は対象チーム、`--accept-heroes` はバトルチームの上書き。
- `--dry-run` はアカウント・チーム・ID の解決のみ（書き込みはせず、`userGetInfo` / `teamGetAll` などの読み取り API のみ。送信もバトルもしない）。

### 5. Multi（複数アカウント）で一括実行

```bash
# 各アカウントの challenge_defaults を使って「1体送信→同一アカウントでバトル」
uv run hw-genie multi challenge --dry-run
uv run hw-genie multi challenge --yes

# 全アカウント共通の対象で上書き
uv run hw-genie multi challenge --user-id <相手のプレイヤーID> --yes
```

- `multi challenge` は各アカウントから実メッセージを送るため、`--yes` がない限り実行しません（`--dry-run` は読み取りのみ、逐次表示）。
- `--user-id` / `--type` を省略すると、各アカウントの `challenge_defaults` を使用。既定値がないアカウントは失敗として集計されます。
- 途中で失敗したアカウントがあれば exit 1。バトルの LOSS は実行済みとして成功扱いです。

## 注意

- **チャレンジは受信者・送信者のどちらもバトルできます。** 送信者側は「自分の保存チーム全員 vs 送信時に指定した対象チーム」のバトルになります。
- 送信直後は personal スレッドへの反映に 1〜2 秒かかることがあります。
- チャレンジメッセージは personal スレッドの未読状態に依存して見えないことがあります。
  `challenge list` で見つからない場合は時間を置くか、ゲーム内でスレッドを開いてください。
- 認証エラー時はユーザーに新しい `curl` の提供を求めるか `auth-server` の起動を促してください。
- API 仕様: [docs/api/CHAT_API.md](../../../docs/api/CHAT_API.md)

## 完了後の報告

- 送信: 送信元・相手のプレイヤーID・type・使用した**対象チーム**（既定1体）を報告。
- バトル: messageId、どちら側のバトルか（受信者/送信者）、勝敗、スター数、リプレイ URL を報告。
- 失敗時: `reason`（例: `notFound`）と、`--user-id` がプレイヤーIDかどうかを確認するよう案内。
