---
name: challenge
description: Send and accept Hero Wars friendly personal-chat challenges (chatSendChallenge / chatAcceptChallenge) with automatic messageId discovery, either step-by-step or end-to-end between two registered accounts.
---
# Challenge (Friendly Battle) Skill (HW-Genie)

## ワークフロー

> **Tip**: アカウントは `uv run hw-genie auth --list` で確認してください。`--user-id` は**プレイヤーID**（`userGetInfo.response.id` やチャットの `userId`）で、認証ヘッダーの `x-auth-user-id` とは別物です。間違えると送信が `notFound` で失敗します。

### 1. チャレンジ送信（送信側）

```bash
uv run hw-genie challenge send -a <account> --user-id <相手のプレイヤーID> --type titan
```

- `--type titan` は `teamGetAll.challenge_titan`、`--type hero` は `teamGetAll.challenge` の保存チームを使用（1〜5 体。部分編成可）。
- `--heroes 4003 4002 4004 4001 4000` で保存チームを上書きできます。
- 成功時は `{"result": true}`。失敗時は `reason`（例: `notFound` = 相手ID不正/対象外）を表示して exit 1。
- `--dry-run` は書き込み（`chatSendChallenge`）を行いませんが、チーム解決のため `teamGetAll` などの読み取り API は呼びます。

### 2. messageId の特定（受信側）

`chatAcceptChallenge` に必要な `messageId` は、**受信側**の personal スレッドにある
`messageType="challenge"` メッセージの `id` です。専用 API はありません。

```bash
uv run hw-genie challenge list -a <受信側account> --user-id <送信者のプレイヤーID>
```

内部では `chatGetAll`（`chatType=personal`, `userId=<送信者>`）を呼び、`chat[]` を解析しています。

- `challenge` メッセージの `id` が `messageId`。
- 同じ id を `data.challengeId` に持つ `messageType="replay"` があれば**承諾済み**。
- 未承諾（pending）の最新 1 件が `accept` の自動選択対象。
- `--json` で `message_id` / `sender_id` / `challenge_type` / `power` / `ctime` / `pending` を出力。

### 3. チャレンジ承諾（受信側）

```bash
uv run hw-genie challenge accept -a <受信側account> --user-id <送信者のプレイヤーID>
```

- `--message-id N` を省略すると、未承諾の最新チャレンジを自動選択。
- 承諾チームはチャレンジメッセージの `data.type`（`titan` / `hero`）に応じて
  `challenge_titan` / `challenge` を使用。`--heroes` で上書き可。
- 結果は `WIN` / `LOSS` とスター数、リプレイ URL
  `https://www.hero-wars.com/?replay_id=<battle.id>` を表示。
- `--dry-run` は書き込みを行いませんが、`teamGetAll` などの読み取り API は呼ぶ場合があります（特定した messageId とチームを表示するのみ）。

### 4. 送信 → 承諾を 1 コマンドで（自アカウント同士）

```bash
uv run hw-genie challenge battle -a <送信元account> --accept-account <承諾側account>
```

- 2 つとも登録済みアカウントである必要があります。
- 送信元の `userGetInfo` と承諾側の `userGetInfo` からプレイヤーIDを解決し、
  送信 → 受信側スレッドを `--wait`（既定 10 秒）までポーリング → messageId 自動特定 → 承諾。
- `--heroes` は送信チーム、`--accept-heroes` は承諾チームの上書き。
- `--dry-run` はアカウント・チーム・ID の解決のみ（書き込みはせず、`userGetInfo` / `teamGetAll` などの読み取り API のみ。送信も承諾もしない）。

## 注意

- **チャレンジは受信者だけが承諾できます。** 送信者自身が承諾すると自分自身とのバトル
  （`battle.typeId` が自分の ID）になるため、`battle` は別アカウントを使います。
- 送信直後は受信側スレッドへの反映に 1〜2 秒かかることがあります。
- チャレンジメッセージは personal スレッドの未読状態に依存して見えないことがあります。
  `challenge list` で見つからない場合は時間を置くか、ゲーム内でスレッドを開いてください。
- 認証エラー時はユーザーに新しい `curl` の提供を求めるか `auth-server` の起動を促してください。
- API 仕様: [docs/api/CHAT_API.md](../../../docs/api/CHAT_API.md)

## 完了後の報告

- 送信: 送信元・相手のプレイヤーID・type・使用チームを報告。
- 承諾/バトル: messageId、勝敗、スター数、リプレイ URL を報告。
- 失敗時: `reason`（例: `notFound`）と、`--user-id` がプレイヤーIDかどうかを確認するよう案内。
