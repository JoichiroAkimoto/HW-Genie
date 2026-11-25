---
name: power-tournament
description: HW-Genie を使用して Power Tournament（Hero / Titan）の状態とグループ上位5名（ポイント・パワー・サーバー・最終ログイン・Activity/Titanite/Prestige）をアカウントごとに表示し、受取可能な報酬を受け取ります。
---
# Power Tournament Skill (HW-Genie 版)

## ワークフロー
> **Tip**: 複数アカウントを使用している場合は、事前に `uv run hw-genie auth --list` で正しいアカウント別名を確認してください。`multi` は登録済み全アカウントを対象にします。

1. **確認（読み取りのみ）**:
   ```bash
   uv run hw-genie power-tournament --dry-run -a <アカウント名>
   uv run hw-genie multi power-tournament --dry-run
   ```
   - 開催種別（Titan / Hero）・終了時刻・自分の順位/ポイント
   - グループ上位 N 名（既定 5、`--top N` で最大 50）の名前・ポイント・最終ログイン
   - 受取可能（claimable）な報酬数
2. **受取実行**（ユーザーが明示的に指示した場合、または定期運用として承認済みの場合）:
   ```bash
   uv run hw-genie power-tournament -a <アカウント名>
   uv run hw-genie multi power-tournament
   ```
   - `currentDayRewards` の `isFarmed == 0` かつ当日ポイントが閾値以上の報酬だけを受け取ります。
   - 受取済み（`NotAvailable` / `already farmed`）は received に含め、未知エラーは failed として未受取に残します。
   - `multi` は報酬受取のみで資源を消費しないため `--yes` は不要です。

## 挙動と注意
- Hero / Titan は `powerTournament_getState` の `type` から自動判定します（`titans` → Titan、`heroes` → Hero）。
- ランキングは `powerTournament_getGroupInfo` の `points` を降順ソートして算出します。同点は同じ順位になります（競技順位: 900, 900, 500 → 1, 1, 3）。userId 昇順は同点内の表示順を決めるためだけに使い、順位を連番で割り当てるものではありません。最終ログインは `users[userId].lastLoginTime`、サーバー番号は `serverId` です。
- 総合パワーと Activity/Titanite/Prestige は `users` に含まれていればそれを使います。実測した Titan のレスポンスでは基本情報のみのため、`clanId` から `clanGetInfo {"clanId": ...}` の `membersStat` で Activity/Titanite/Prestige を補完します（累計値は Week 列）。総合パワーは取得できない場合 `-` です。
- ランキング取得に失敗しても、状態表示と報酬受取は実行されます。そのアカウントは失敗として exit 1 になります。クラン統計の取得失敗は警告のみで、ランキングは表示されます。
- イベント未開催（`NotAvailable`）は正常終了（exit 0）です。
- 最終ランキング報酬は自動受取しません（`finalRewardFarmed` は表示のみ）。イベント終了後にゲーム内で受け取ってください。
- イベント終了間際の監視には `--dry-run` を繰り返し使えます。受取は冪等です。

## 出力例
```
🚀 [Alice] Power Tournament
  ℹ️  Type: Titan | Ends: 2026-11-29 10:59
  ℹ️  Alice: rank #2 | points 12.2K (today 6.0K)
  # | Name  | Points | Power | Srv | Act T/W      | Tit T/W   | Pre T/W   | Last login       | Seen
  -------------------------------------------------------------------------------------------------
  1 | Bob   | 104.5K | 1.2M  | 161 | 1.8K/43.6K   | 321/30.2K | 520/44.1K | 2026-11-24 19:05 | 2h
  2 | Alice | 98.7K  | -     | 161 | 25.0K/123.5K | 900/21.0K | 400/39.0K | 2026-11-24 19:05 | 2h   ← you
  ℹ️  Rewards: received 2 / unreceived 1
```

各列の幅は表示内容（ヘッダーと最も長い値）から自動算出されます。`Name` は最大 18 表示幅で切り詰め、名前が短ければ列も狭くなります（上の例は幅が詰まった状態）。`← you` は `Seen` の後ろに付き、列幅には含みません。

`Power` は `users` に `power` / `sumPower` が含まれる場合のみ表示され、無ければ `-`。
`Act/Tit/Pre T/W` は today/week（week は累計値）です。

## 完了後の報告
- 開催種別・自分の順位/ポイント
- 上位5名のポイント・パワー・サーバー・最終ログイン、Activity/Titanite/Prestige（Today/Week）
- 受取済み／未受取件数（received / unreceived）と失敗件数
- `multi` の場合はアカウント別ブロックと失敗一覧

## 注意
- 実在のプレイヤー名・アカウント名をコード・ドキュメント・テストに書かないでください（AGENTS.md の「不変条件」）。例は `Alice` / `Bob` を使います。
- 認証エラー時は新しい `curl` の提供または `auth-server` の起動を促してください。
