# ToE Auto Rival Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `toe attack` / `toe run` で `--rival` / `--titans` を省略可能にし、`titanArenaGetStatus` から未クリア rival を自動選択して Tier を連続攻略する

**Architecture:** `titan_arena.py` に `_select_auto_rivals` と `AUTO_RIVAL_SCORE_THRESHOLD=250` を追加し `run_titan_arena(rival_id=None)` と `run_titan_arena_tier(titans=None)` で自動解決。CLI は `required=False` に緩和し None を下位に透過。既存 `fetch_titan_arena_status` / `_resolve_titans` を流用し最小差分。

**Tech Stack:** Python 3.13, HWClient (requests), pytest, FastAPI auth-server (toe job queue), Haxe battle engine bridge

**Spec:** `docs/superpowers/specs/2026-09-01-toe-auto-rival-design.md`

## Global Constraints
- Python 3.13 / uv sync --locked
- Threshold 250 は `titanArenaGetStatus.rivals[].attackScore >=250` でクリア判定 (HerowarsHelper準拠)
- isWall = rivalId.startswith("-") で壁判定、ソートは (attackScore asc, isWall asc, power asc)
- 後方互換: --rival/--titans 明示は従来通り即 StartBattle
- ruff check --fix / pytest 622 passed を維持

---

### Task 1: Auto rival selector + run_titan_arena optional化

**Files:**
- Modify: `src/python/hw_genie/commands/titan_arena.py:19-80`
- Test: `src/python/tests/test_titan_arena.py`

**Interfaces:**
- Consumes: `fetch_titan_arena_status(client) -> dict`, `_resolve_titans(client, titans) -> list[int]`
- Produces: `AUTO_RIVAL_SCORE_THRESHOLD = 250`, `_select_auto_rivals(status, threshold) -> list[str]`, `run_titan_arena(client, rival_id=None, titans=None, ...)` auto path

- [ ] **Step 1: Write failing test for _select_auto_rivals**

```python
# src/python/tests/test_titan_arena.py
def test_select_auto_rivals_sorts():
    from hw_genie.commands.titan_arena import _select_auto_rivals
    status = {"rivals": {
        "-480511": {"attackScore": 167, "power": "1800000"},
        "-480711": {"attackScore": 120, "power": "1800000"},
        "49301300": {"attackScore": 0, "power": "495427"},
        "19913600": {"attackScore": 250, "power": "545611"},
    }}
    res = _select_auto_rivals(status)
    assert res == ["49301300", "-480711", "-480511"]  # 0 < 120 < 167, wall優先は同score時

def test_select_auto_rivals_threshold_boundary():
    from hw_genie.commands.titan_arena import _select_auto_rivals
    status = {"rivals": {
        "a": {"attackScore": 249, "power": "1"},
        "b": {"attackScore": 250, "power": "1"},
        "c": {"attackScore": 251, "power": "1"},
    }}
    assert _select_auto_rivals(status) == ["a"]
    assert _select_auto_rivals(status, threshold=251) == ["a", "b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --locked pytest src/python/tests/test_titan_arena.py::test_select_auto_rivals_sorts -v`
Expected: FAIL with "cannot import name '_select_auto_rivals'"

- [ ] **Step 3: Implement AUTO_RIVAL_SCORE_THRESHOLD + _select_auto_rivals + run_titan_arena auto**

```python
# src/python/hw_genie/commands/titan_arena.py
AUTO_RIVAL_SCORE_THRESHOLD = 250

def _select_auto_rivals(status: dict, threshold: int = AUTO_RIVAL_SCORE_THRESHOLD) -> list[str]:
    rivals = status.get("rivals") or {}
    if not isinstance(rivals, dict):
        return []
    candidates = []
    for rid, info in rivals.items():
        if not isinstance(info, dict):
            continue
        score = info.get("attackScore")
        try:
            s = int(score) if score is not None else 0
        except: s = 0
        if s < threshold:
            try: p = int(str(info.get("power","0")))
            except: p = 0
            is_wall = 0 if str(rid).startswith("-") else 1
            candidates.append((s, is_wall, p, str(rid)))
    candidates.sort()
    return [rid for _,_,_,rid in candidates]

def run_titan_arena(client_or_headers, rival_id=None, titans=None, ...):
    ...
    if rival_id is None:
        status = fetch_titan_arena_status(client)
        auto = _select_auto_rivals(status)
        if not auto:
            print(f"{Emojis.INFO}No rivals to attack (threshold={AUTO_RIVAL_SCORE_THRESHOLD})", flush=True)
            return {"status": ResponseStatus.SKIPPED, "reason": "no_rivals"}
        rival_id_str = auto[0]
        print(f"{Emojis.INFO}Auto-selected rival {rival_id_str} (score ...)", flush=True)
    else:
        rival_id_str = str(rival_id)
    titans = _resolve_titans(client, titans)  # Noneなら teamGetAll
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --locked pytest src/python/tests/test_titan_arena.py -k select_auto -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/python/hw_genie/commands/titan_arena.py src/python/tests/test_titan_arena.py
git commit -m "feat(toe): auto rival selector and optional rival in attack"
```

---

### Task 2: CLI optional化 + run_titan_arena_tier titans optional

**Files:**
- Modify: `src/python/hw_genie/main.py:1131-1174`
- Modify: `src/python/hw_genie/commands/titan_arena.py:224-260` (run_titan_arena_tier)
- Test: `src/python/tests/test_titan_arena_flow.py`

**Interfaces:**
- Consumes: Task1's `_select_auto_rivals`, `_resolve_titans`
- Produces: `cmd_toe_attack` / `cmd_toe_run` handles None

- [ ] **Step 1: Write failing test for CLI optional**

```python
# test that run_titan_arena_tier resolves titans when None
def test_run_titan_arena_tier_auto_titans(mock_client):
    client, mock_call = mock_client
    # mock teamGetAll then getStatus peace_time
    mock_call.side_effect = [
        _ok({"response": {"titan_arena": [1,2,3,4,5]}}), # _resolve
        _ok({"response": {"status": "peace_time", "tier": 1, "rivals": {}}}),
    ]
    summary = run_titan_arena_tier(client, titans=None, engine=PythonBattleEngine())
    assert summary["titans"] == [1,2,3,4,5]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --locked pytest src/python/tests/test_titan_arena_flow.py::test_run_titan_arena_tier_auto_titans -v`
Expected: FAIL (titans None raises)

- [ ] **Step 3: Make CLI args optional + wire**

```python
# main.py
p_toe_attack.add_argument("--rival", required=False, default=None, help="Rival ID (omitted → auto-select lowest score wall/player via titanArenaGetStatus, threshold=250)")
p_toe_attack.add_argument("--titans", nargs=5, type=int, required=False, default=None, ...)
# cmd_toe_attack:
rival = getattr(args, "rival", None)
titans = list(args.titans) if args.titans else None
run_titan_arena(client, rival_id=rival, titans=titans, ...)

p_toe_run.add_argument("--titans", nargs=5, type=int, required=False, default=None, help="5 titan IDs (omitted → auto-resolve via teamGetAll.titan_arena)")
# cmd_toe_run similar

# titan_arena.py run_titan_arena_tier:
engine = engine or PythonBattleEngine()
titans = _resolve_titans(client, titans)  # move to top, handles None
```

- [ ] **Step 4: Run tests**

Run: `uv run --locked pytest src/python/tests/test_titan_arena_flow.py -v`
Expected: PASS + 622

- [ ] **Step 5: Commit**

```bash
git add src/python/hw_genie/main.py src/python/hw_genie/commands/titan_arena.py src/python/tests/test_titan_arena_flow.py
git commit -m "feat(toe): make --rival/--titans optional with auto-resolve"
```

---

### Task 3: Docs, help text, and regression

**Files:**
- Modify: `docs/api/GUILD_API.md` (ensure example clarifies auto)
- Modify: `README.md` / `AGENTS.md` if toe section exists
- Test: full `pytest` + `ruff check`

- [ ] **Step 1: Update help text to mention auto**

```python
# main.py help strings already in Task2, ensure they contain "(omitted → auto-select ... threshold=250)" and "(omitted → auto-resolve via teamGetAll.titan_arena)"
```

- [ ] **Step 2: Run ruff + full pytest**

Run: `uv run --locked ruff check . --fix`
Run: `uv run --locked pytest -q`
Expected: All checks passed, 622+ passed

- [ ] **Step 3: Manual verification**

```bash
uv run hw-genie toe status -a VitaminD | head -n 50
uv run hw-genie toe attack -a VitaminD --dry-run  # auto 1体
uv run hw-genie toe attack -a VitaminD --rival -480711 --titans 4003 4023 4004 4001 4000 --dry-run # explicit
uv run hw-genie toe run -a VitaminD --dry-run # expect via tier loop but dry-run? use estimate
```

- [ ] **Step 4: Commit**

```bash
git add docs/api/GUILD_API.md README.md AGENTS.md
git commit -m "docs(toe): mention auto rival/titans for toe commands"
```

