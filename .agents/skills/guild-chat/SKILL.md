---
name: guild-chat
description: Fetch guild chat (chatGetAll / chatType=clan) and personal chat (PM) — recent conversation list (chatGetTalks) or a single thread (chatType=personal + peer userId) — and display as a table with summary. Translates to user's language.
---
# Guild / Personal Chat Skill (HW-Genie)

## ワークフロー
> **Tip**: 複数のアカウントを使用している場合は、事前に `uv run hw-genie auth --list` で正しいアカウント別名を確認することを推奨します。

1. **トリガー**: Triggered when user says "show guild chat", "get clan chat", "fetch chat history", "read PM / personal chat", etc.
2. **Execution**: Fetch chat (`chatGetAll`; personal talk list uses `chatGetTalks`) and display as table (+ summary for chat history).
   ```bash
   # Latest 50 guild chat messages with table + summary
   uv run hw-genie chat -a <account>
   # Change count (1-200, clamped)
   uv run hw-genie chat -a <account> --count 20
   # Other chat types (training/xgvg/server)
   uv run hw-genie chat -a <account> --type server --count 50
   # Personal chat (PM): recent conversations, newest first (names resolved via userGetInfoByIds)
   uv run hw-genie chat -a <account> --type personal
   # Personal chat (PM): one thread with a specific peer
   uv run hw-genie chat -a <account> --type personal --user-id <peerId>
   # Pagination (before ID)
   uv run hw-genie chat -a <account> --last-id 144192792
   # Raw JSON
   uv run hw-genie chat -a <account> --raw
   # Parsed JSON
   uv run hw-genie chat -a <account> --json
   ```
   - `--type` choices: `clan` (guild, default) / `training` / `xgvg` / `server` / `personal`
   - For `--type personal`: **omit `--user-id` to list recent conversations** (`chatGetTalks`, newest first, with the last message per partner). Specify `--user-id` to fetch that thread. `--user-id` is rejected for other types; `--last-id` is not supported for the talk list.
   - `--count` is clamped to 1-200 (default 50).
   - `--raw` and `--json` are mutually exclusive.
   - In `docs/superpowers/chat.md` the 2nd element (`[1]` / `ident: group_1_body`) is `chatType=clan` (this command uses a single `chatGetAll` call).
3. **Display**:
   - **Table**: `rich` table with headers `Time | Sender | Message`. Time respects `HWGENIE_TZ` (default UTC, e.g. `Asia/Tokyo`), stickers as `sticker:ID`, long messages truncated at 200 chars.
     **Language handling (important)**: The CLI outputs messages in their original language (as stored). The agent **must translate** the table and summary to the user's requested language.
     - If the user writes in Japanese, translate `Time`→`日時`, `Sender`→`送信者`, `Message`→`メッセージ`, `Chat`→`ギルドチャット`, `Period`→`期間`, `Count`→`件数`, `Participants`→`参加者`, `Top speakers`→`発言数Top`, `Peak hour`→`最も活発な時間帯`, `Longest gap`→`最長の無言期間`, `Summary`→`要約` **and translate every Message body** from its original language to Japanese (keep names/times as-is, translate only the message content).
     - If the user requests another language (e.g. English, Korean), translate headers/labels/summary and message bodies to that language accordingly.
     - If no language is specified, default to the user's current conversation language; keep original if translation is not needed. `--raw` / `--json` are always left untranslated (raw data).
   - **Summary**: period (`oldest ~ newest`), count (text/sticker), participants, top 3 speakers, peak hour (in display TZ), longest gap (if >=24h), and summary text (2-4 sentences) — all translated to the user's language.
   - **Personal talk list mode** (`--type personal` without `--user-id`): different columns `Time | Partner | Type | Message` and no statistical summary. Partner is shown as `Name (userId)` when the `userGetInfoByIds` name lookup succeeds (otherwise just the userId). `--json` returns talk objects (`user_id/utime/message_type/last_message/seen/read/name`).
   - Empty chat: `ℹ️  [account] No chat history found` / `No personal talks found` (translate these lines as well).
4. **API spec**: See [docs/api/CHAT_API.md](../../../docs/api/CHAT_API.md). `chatGetAll` returns `chat[]` (`id/userId/messageType/ctime/data`) and `users{}` (`name/level`); missing users shown as `"<userId> (left?)"`. The personal talk list uses `chatGetTalks` → `talks[]` (`userId/lastMessage/lastMessageType/utime/seen/read`).

## 注意
- 読み取りのみで破壊的操作はありません。
- 認証エラーが発生した場合は、ユーザーに新しい `curl` コマンドの提供を求めるか、`auth-server` の起動を促してください。
- `chatType` は既定の `clan`（ギルド）以外も取得可能です（`training` / `xgvg` / `server`）。
- 個人チャットの会話一覧は `chatGetTalks`（`--type personal` で `--user-id` 省略）で取得できます。相手ごとの直近メッセージ・時刻・既読状態が新しい順に返り、相手名は自動で `userGetInfoByIds` により解決されます。
- 個別スレッド（`--user-id` 指定）は実機観測として未読分のみ返ることがあり、既読化済みの会話は 0 件になります。0 件のときは「未読がない」可能性を伝えてください。**相手ごとの最新1件**が必要ならスレッド取得ではなく会話一覧（`chatGetTalks`）の `lastMessage` を使ってください。
- 正確な時刻が必要な場合は `--json` / `--raw` の epoch 秒（スレッドは `ctime`、会話一覧は `utime`）を使い、`HWGENIE_TZ`（既定 UTC）または JST へ換算して報告してください。

## 完了後の報告
**チャット履歴**（`clan` / `training` / `xgvg` / `server` および個人スレッド `--type personal --user-id`）の場合:
取得したチャットを表形式で要約し、特に**期間・参加者・発言数Top・活発時間帯**を中心に、**ユーザーの言語に翻訳して**報告してください。メッセージ本文もユーザーの言語に翻訳すること（`--raw` / `--json` の場合は翻訳せず原文のまま）。

**個人チャットの会話一覧**（`--type personal` で `--user-id` なし）の場合:
統計項目（参加者・発言数Top・活発時間帯・最長の無言期間）は報告せず、相手ごとに **相手（`Name (userId)`）・最新メッセージ・種別・日時** を最新順に報告してください（表の `Time | Partner | Type | Message` をそのまま使う）。メッセージ本文はユーザーの言語に翻訳すること（`--raw` / `--json` の場合は翻訳せず原文のまま）。

## 使用例
**ユーザーの入力**: 「ギルドチャット最新50件を見せて」

**AI の動作**:
1. `uv run hw-genie chat -a <account> --count 50` を実行し、表＋要約を報告（アカウント未指定なら自動解決、複数なら要指定）。
2. 必要に応じて `uv run hw-genie chat -a <account> --raw` で生レスポンスを提示。

**ユーザーの入力**: 「<account> のPMに <name> から来てるチャット、日本時間だと何時？」

**AI の動作**:
1. `HWGENIE_TZ=Asia/Tokyo uv run hw-genie chat -a <account> --type personal` で会話一覧を取得し、相手と直近メッセージを確認する（相手名から userId も分かる）。
2. スレッド本文が必要なら `uv run hw-genie chat -a <account> --type personal --user-id <peerId>` を実行。
3. 0 件なら未読でない可能性を伝える。正確な時刻は `--json` の epoch 秒（会話一覧は `utime`、スレッドは `ctime`）を JST に換算して報告する。

**ユーザーの入力**: 「最新順に3人分の直近メッセージを1件ずつ <account> で取れる？」

**AI の動作**:
1. `HWGENIE_TZ=Asia/Tokyo uv run hw-genie chat -a <account> --type personal --count 3`（または `--json`）を1回実行する。会話一覧は既に utime 降順で、各相手の `lastMessage` を含むため相手ごとの追加取得は不要。
