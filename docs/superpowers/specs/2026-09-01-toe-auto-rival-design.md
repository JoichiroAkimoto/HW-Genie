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
  - `fetch_titan_arena_status(client)` 既存 (`docs/api/GUILD_API.md` Titan Arena章参照: `status`=`battle`/`peace_time`/`disabled` 等)
  - `AUTO_RIVAL_SCORE_THRESHOLD = 250` 定数: `titanArenaGetStatus.rivals[].attackScore` が 250 でクリア済み (HerowarsHelper `attackScore >= 250` をクリア判定、既存 `run_titan_arena_tier` / `_run_rivals` でも `attackScore < 250` で未クリア判定)。マジックナンバーを定数化し `run_titan_arena_tier` 引数 `attack_score_threshold` デフォルトに使用。
  - `_select_auto_rivals(status, threshold=AUTO_RIVAL_SCORE_THRESHOLD) -> list[str]` 新設: `status.rivals: dict[rivalId -> {attackScore, power, titans}]` (スキーマは `docs/api/GUILD_API.md` 参照) から `attackScore < threshold` のみ抽出し `(attackScore asc, isWall asc, power asc)` でソート。`isWall = 0 if rivalId.startswith("-") else 1` で壁 (負ID) を優先、壁同士は `attackScore` が低い (=より未達) ものを先に倒す。`power` は弱い相手から倒すため昇順。空リストは呼び出し側で `No rivals to attack` 扱い。
  - `_resolve_titans(client, titans)` 既存 (None なら `teamGetAll.titan_arena` 5体を返す、失敗時は RuntimeError)
  - `run_titan_arena(client, rival_id: str|int|None=None, titans=None, ...)` 拡張: `rival_id is None` なら `fetch→_select_auto_rivals` 先頭1件を採用、0件なら `{"status": ResponseStatus.SKIPPED, "reason": "no_rivals"}` を返して StartBattle せず終了。`titans is None` なら `_resolve_titans` で自動解決。
  - `run_titan_arena_tier(client, titans=None, ...)` 拡張: `titans is None` なら冒頭で `_resolve_titans` 1回のみ。ループ内 `_run_rivals` は既存通り `status.rivals` を毎回 fetch→フィルタし auto 選定、責務は `_select_auto_rivals` に集約。
  - ログ: auto 選択時に `ℹ️ Auto-selected rival <id> (score=X wall=Y power=Z)  titans=[...]` を1行出力し `run_logs` / 手動検証で追跡可能に。
- `src/python/hw_genie/main.py`
  - `p_toe_attack --rival required=False default=None, --titans nargs=5 required=False default=None` : `nargs=5` は 0個 or 5個のみ受理、1-4個は argparse が自動で `expected 5` エラーを出す
  - `p_toe_run --titans nargs=5 required=False default=None`
  - `cmd_toe_attack` / `cmd_toe_run` で None をそのまま下位に渡す (用語統一: CLI `--rival` / 内部 `rival_id`)
- `src/python/hw_genie/battle/engine.py` 変更なし (既存 Bridge fallback 維持)

## Data Flow
1. `toe attack` (rival省略, titans省略可): cmd → _resolve_titans(if None) → fetch_status → _select_auto_rivals[0] → run_titan_arena(rivalId=auto, titans=resolved) → StartBattle/Engine/EndBattle。rival明示時は fetchをスキップし即 StartBattle (後方互換テスト `test_run_titan_arena_explicit_rival_skips_fetch` で保証)。
2. `toe attack` (rival明示): titans のみ resolve 必要な場合に _resolve_titans、rival は fetch せず即 StartBattle
3. `toe run` (titans省略可): cmd → _resolve_titans(if None, 1回のみ) → run_titan_arena_tier(titans=resolved) → ループ冒頭 fetch_status → status判定 (disabled/peace_time → return) → canRaid? StartRaid/EndRaid : _run_rivals(= fetch→_select_auto_rivals で全件) → CompleteTier → FarmDailyReward → 次ループ先頭で再 fetch。`_run_rivals` の auto 選定責務は `_select_auto_rivals` 集約で Architecture と一致。
4. `toe run` (titans明示): _resolve_titans をスキップし指定編成で Tier 実行 (`test_run_titan_arena_tier_explicit_titans_skips_resolve` で保証)

## Error Handling
- `fetch_titan_arena_status` 失敗: RuntimeError に含まれる `error_name` が `auth`/`InvalidSession` なら `HWAuthError` を再送出し `Messages.AUTH_ERROR` で `auth-server` 更新を促す、以外は ❌ + `threshold=250` 値をログに含めて表示
- `_resolve_titans` 失敗 (teamGetAll 失敗 / 空編成 / 認証切れ): RuntimeError → ❌ `Could not resolve titan team; pass --titans explicitly`、ログに `rival` と共に残す
- `--titans` 不正ID (5件でない / 数値でない / 存在しないタイタンID): `_resolve_titans` 内 `ValueError` → ❌ で即終了、fetchは行わない
- `status in (disabled, peace_time)` → ℹ️ で即 return、`threshold` 値を含めて `status=peace_time (threshold=250)` とログ
- auto対象0件 (`_select_auto_rivals` 空): `toe attack` は SKIPPED、`toe run` は `No rivals to attack (threshold=250)` → `FarmDailyReward` のみ試行して正常終了
- Tier 実行中の部分失敗: `_run_rivals` 内で1体の StartBattle/EndBattle が失敗してもループ継続、結果は `summary.rival_results[].error` と `summary.errors` に蓄積、最後に `failed` カウントで exit 判定。`canRaid` レイドの Bridge 失敗は `_fallback_progress` で loss として EndRaid 継続 (既存)
- `StartBattle NotFound`: 既存の Valid rivals 列挙 + Did you mean サジェストを維持、ログに `threshold` と `valid count` を含める
- `engine=hybrid` Bridge失敗: `BridgeError`/`Exception` を捕捉し Python estimator にフォールバックし estimate_only 返却、EndBattle は呼ばない (前回修正維持)

## Testing
- Unit: `test_select_auto_rivals_sorts` (attackScore 昇順, wall優先, power昇順のタイ), `test_select_auto_rivals_threshold_boundary` (249/250/251), `test_select_auto_rivals_empty_and_no_wall`, `test_run_titan_arena_auto_picks_lowest_score`, `test_run_titan_arena_explicit_rival_skips_fetch`, `test_run_titan_arena_tier_auto_titans`, `test_run_titan_arena_tier_explicit_titans_skips_resolve`, `test_auto_no_rivals_skips_battle`
- Flow: `test_run_titan_arena_dry_run_does_not_call_end_battle` / `test_run_titan_arena_estimate_only*` を optional 化 (required=False) に合わせて更新、mock fixture は `titanArenaGetStatus` (`rivals` with attackScore/power) + `teamGetAll` + `StartBattle` battle payload を conftest に追加
- Regression: 既存 `test_titan_arena_flow.py` の explicit 指定時は fetch/resolve を呼ばないこと、`dry-run`時は fetchは呼ぶが StartBattle/EndBattle は呼ばないことを検証
- Manual: `uv run hw-genie toe status -a VitaminD` → `uv run hw-genie toe attack -a VitaminD --dry-run` (auto 1体) → `uv run hw-genie toe attack -a VitaminD --rival <id> --dry-run` (明示) → `uv run hw-genie toe run -a VitaminD --dry-run` 相当の estimate 検証 (現行 tier は estimate-only で Invalid battle を確認)

## Rollout
- 後方互換: --rival/--titans 明示は従来動作、省略時のみ新 auto パス。`required=False` 変更は breaking ではない
- ロールアウト: フィーチャーフラグなし、失敗時は summary.errors に残り exit 1、ログに auto-selected rivalId を残すため原因特定可能。ロールバックは `git revert` で1コミット
- 監視: `run_logs.log_text` の `Auto-selected rival` 行と `summary.rival_results` の `rivalId` で選択検証
- ドキュメント: `AGENTS.md` / `README.md` の toe 例を `toe run -a <account>` 無引数例に更新、`src/python/hw_genie/main.py --help` 文言を `omitted → auto-select from titanArenaGetStatus / teamGetAll (threshold=250)` に更新、該当 `SKILL.md` があれば同様更新
- 成功指標: `toe run -a VitaminD` で未クリア (attackScore<250) が0になるまで Tier が進むこと、`toe attack` 省略で最優先 rival 1体がクリアされること、明示指定で従来通り動作すること
