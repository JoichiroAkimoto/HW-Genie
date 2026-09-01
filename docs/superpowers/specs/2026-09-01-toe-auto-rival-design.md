# ToE Auto Rival Design — 2026-09-01

## Overview
Titan Arena (ToE) の手動 `rivalId` 指定を廃し、`titanArenaGetStatus` から未クリア rival を自動選択して連続攻略する。`--rival` / `--titans` を optional にし、省略時はサーバー状態から自動解決する。既存 `toe run` の Tier 全自動フローは維持しつつ、`toe attack` でも rival 省略で単発自動が可能になる。

## Goals
- `uv run hw-genie toe run -a VitaminD` で保存済み編成 + 自動 rival で Tier 全体をクリア
- `uv run hw-genie toe attack -a VitaminD` で最も優先度の高い未クリア1体を自動で攻撃
- 手動指定 (`--rival` / `--titans` 明示) は従来通り即 StartBattle
- ソースは既存 `titan_arena.py` / `main.py` の最小差分

## Non-Goals
- 新コマンド追加 (`toe auto`) は行わない
- レイド可否の手動切替フラグは追加しない (canRaid=true なら raid 優先を維持)

## Architecture
- `src/python/hw_genie/commands/titan_arena.py`
  - `fetch_titan_arena_status(client)` 既存
  - ` _select_auto_rivals(status, threshold=250) -> list[str]` 新設: `status.rivals` から `attackScore<threshold` をフィルタし ` (attackScore, isWall?0:1, power)` でソート
  - `_resolve_titans(client, titans)` 既存 (None なら teamGetAll)
  - `run_titan_arena(client, rival_id=None, titans=None, ...)` 拡張: `rival_id is None` なら fetch→select 先頭1件
  - `run_titan_arena_tier(client, titans=None, ...)` 拡張: `titans is None` なら resolve
- `src/python/hw_genie/main.py`
  - `p_toe_attack --rival required=False, --titans nargs=5 required=False`
  - `p_toe_run --titans nargs=5 required=False`
  - `cmd_toe_attack` / `cmd_toe_run` で None をそのまま下位に渡す
- `src/python/hw_genie/battle/engine.py` 変更なし (既存 fallback 維持)

## Data Flow
1. `toe attack` (rival省略): cmd → _resolve_titans → fetch_status → _select_auto_rivals[0] → run_titan_arena → StartBattle/Engine/EndBattle
2. `toe attack` (rival明示): fetchをスキップし即 StartBattle
3. `toe run` (titans省略): cmd → _resolve_titans → run_titan_arena_tier → canRaid? StartRaid/EndRaid : _run_rivals(auto選定) → CompleteTier → FarmDailyReward
4. `toe run` (titans明示): resolveをスキップし指定編成で Tier 実行

## Error Handling
- `fetch_titan_arena_status` 失敗: RuntimeError → ❌ 表示
- `status in (disabled, peace_time)` または auto対象0件: ℹ️ No rivals to attack で summary 返却、daily_reward のみ試行
- `StartBattle NotFound`: 既存の Valid rivals 列挙 + Did you mean サジェストを維持
- `engine=hybrid` Bridge失敗: Python estimator にフォールバックし estimate_only 返却、EndBattle は呼ばない
- `canRaid` レイド Bridge失敗: _fallback_progress で loss として EndRaid 継続

## Testing
- Unit: test_select_auto_rivals_sorts, test_run_titan_arena_auto_picks_lowest_score, test_run_titan_arena_tier_auto_titans
- Flow: test_run_titan_arena_dry_run_does_not_call_end_battle 等を optional 化に合わせて更新
- Manual: `toe status` → `toe attack --dry-run` (auto) → `toe attack --rival <id> --dry-run` → `toe run --dry-run` 相当の estimate 検証

## Rollout
- 後方互換: --rival/--titans 明示は従来動作、省略時のみ新 auto パス
- ドキュメント: AGENTS.md / README の toe 例を `toe run -a <account>` 無引数例に更新
