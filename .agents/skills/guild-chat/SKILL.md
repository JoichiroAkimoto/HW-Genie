---
name: guild-chat
description: Fetch guild chat history (chatGetAll / chatType=clan) and display as table with summary. Translates to user's language.
---
# Guild Chat Skill (HW-Genie)

## ワークフロー
> **Tip**: 複数のアカウントを使用している場合は、事前に `uv run hw-genie auth --list` で正しいアカウント別名を確認することを推奨します。

1. **トリガー**: Triggered when user says "show guild chat", "get clan chat", "fetch chat history", etc.
2. **Execution**: Fetch guild chat (`chatGetAll` / `chatType=clan`) and display as table + summary.
   ```bash
   # Latest 50 guild chat messages with table + summary
   uv run hw-genie chat -a <account>
   # Change count (1-200, clamped)
   uv run hw-genie chat -a <account> --count 20
   # Other chat types (training/xgvg/server)
   uv run hw-genie chat -a <account> --type server --count 50
   # Pagination (before ID)
   uv run hw-genie chat -a <account> --last-id 144192792
   # Raw JSON
   uv run hw-genie chat -a <account> --raw
   # Parsed JSON
   uv run hw-genie chat -a <account> --json
   ```
   - `--type` choices: `clan` (guild, default) / `training` / `xgvg` / `server`
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
   - Empty chat: `ℹ️  [account] No chat history found` (translate this line as well).
4. **API spec**: See [docs/api/CHAT_API.md](../../../docs/api/CHAT_API.md). Response is `chat[]` (`id/userId/messageType/ctime/data`) and `users{}` (`name/level`); missing users shown as `"<userId> (left?)"`.

## 注意
- 読み取りのみで破壊的操作はありません。
- 認証エラーが発生した場合は、ユーザーに新しい `curl` コマンドの提供を求めるか、`auth-server` の起動を促してください。
- `chatType` は既定の `clan`（ギルド）以外も取得可能ですが、ギルドチャット以外の用途は限定的です。

## 完了後の報告
取得したチャットを表形式で要約し、特に**期間・参加者・発言数Top・活発時間帯**を中心に、**ユーザーの言語に翻訳して**報告してください。メッセージ本文もユーザーの言語に翻訳すること（`--raw` / `--json` の場合は翻訳せず原文のまま）。

## 使用例
**ユーザーの入力**: 「ギルドチャット最新50件を見せて」

**AI の動作**:
1. `uv run hw-genie chat -a <account> --count 50` を実行し、表＋要約を報告（アカウント未指定なら自動解決、複数なら要指定）。
2. 必要に応じて `uv run hw-genie chat -a <account> --raw` で生レスポンスを提示。
