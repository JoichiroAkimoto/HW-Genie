"""Tests for hw_genie.commands.titan_arena.

These tests use a FakeClient that mimics the HWClient interface and the
real server's *battle-result verification* semantics, so the full control
flow runs without any network/API calls.

Server semantics (derived from HWH / real client analysis):
- The server recomputes the battle from (seed + initial placement) and
  verifies that the submitted `progress[0].attackers.heroes[id].hp` (and
  isDead) exactly matches the recomputed surviving HP. If it does not
  match, it returns "Invalid battle".
- For the test fake we approximate this: a win is accepted only when the
  `battle_sim` callback produced a progress whose surviving attacker HP is
  below the defenders' total (i.e. the sim says we won). Without a real
  battle_sim the payload uses full-HP and the server rejects it.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from hw_genie.commands import titan_arena as ta
from hw_genie.core.client import PlayerStatus, HWResponse, ResponseStatus, ErrorName


# ---------------------------------------------------------------------------
# Fake client: simulates the real server's battle-result verification
# ---------------------------------------------------------------------------

class FakeClient:
    """Mimics HWClient.call / fetch_player_status / sleep.

    Win rule (from real server): EndBattle win=True is accepted only when the
    submitted progress matches a "winning" computation. In the fake we accept a
    win when a battle_sim produced a result with win=True; otherwise we return
    "Invalid battle" (just like the real server when the HP mismatch).
    """

    def __init__(self, seed_sequence, winnable_seeds, tier_sequence=None, rival_sequence=None):
        self.seed_sequence = list(seed_sequence)
        self.winnable_seeds = set(winnable_seeds)
        self.tier_sequence = list(tier_sequence or [])
        self.rival_sequence = list(rival_sequence or [])
        self.calls = []
        self._start_idx = 0
        self._complete_idx = 0
        self._tier_idx = 0
        self._rival_idx = 0
        self.end_results = []
        self._win_count = 0

    def call(self, payload):
        name = payload["calls"][0]["name"]
        self.calls.append(name)
        if name == "titanArenaEndBattle":
            self.end_results.append(payload["calls"][0]["args"]["result"]["win"])
        if name == "titanArenaStartBattle":
            seed = self.seed_sequence[self._start_idx % len(self.seed_sequence)]
            self._start_idx += 1
            battle = {
                "seed": str(seed),
                "attackers": {"4003": {"hp": 8508860, "energy": 1000}, "4023": {"hp": 1, "energy": 1000}},
                "defenders": [{"4000": {}}],
            }
            return HWResponse(status=ResponseStatus.SUCCESS, detail={"response": {"battle": battle}})
        if name == "titanArenaEndBattle":
            signed_seed = payload["calls"][0]["args"]["progress"][0]["seed"]
            # server stores unsigned; reconstruct the seed it checks
            raw_seed = signed_seed + 0x100000000 if signed_seed < 0 else signed_seed
            win = payload["calls"][0]["args"]["result"]["win"]
            # The real server recomputes HP from seed+placement. The fake accepts
            # a win when the seed is "winnable" (approximation of a matching
            # battle_sim result). Without a real battle_sim the payload's HP will
            # not match and the real server returns Invalid battle.
            if win and raw_seed in self.winnable_seeds:
                self._win_count += 1
                return HWResponse(
                    status=ResponseStatus.SUCCESS,
                    detail={"response": {"battle": {"result": {"win": True, "scoreAttack": 250, "scoreDefence": 50}}}},
                )
            return HWResponse(status=ResponseStatus.SUCCESS, detail={"response": {"error": "Invalid battle"}})
        if name == "titanArenaCompleteTier":
            info = self.tier_sequence[self._complete_idx % len(self.tier_sequence)] if self.tier_sequence else {"tier": 1, "maxTier": 8, "canRaid": True, "status": "battle", "rivals": {"-480906": {"isBot": True}}}
            self._complete_idx += 1
            return HWResponse(status=ResponseStatus.SUCCESS, detail={"response": info})
        if name == "titanArenaGetStatus":
            # 新 auto は毎 tier 開始時と各勝利後に GetStatus を呼ぶ。
            # 実サーバでは 1敵倒すとその敵は dead になる。Fake では
            # win_count > complete_idx の間は「現在の tier の全敵を倒した」
            # 状態をエミュレートして rivals を空で返す。
            if self._win_count > self._complete_idx:
                # 現在 tier の全敵を倒した直後（CompleteTier 前）
                base = self.tier_sequence[self._complete_idx % len(self.tier_sequence)] if self.tier_sequence else {"tier": 1, "maxTier": 8, "canRaid": True, "status": "battle", "rivals": {"-480906": {"isBot": True}}}
                empty = dict(base)
                empty["rivals"] = {}
                return HWResponse(status=ResponseStatus.SUCCESS, detail={"response": empty})
            # 通常: 現在 tier の情報
            info = self.tier_sequence[self._complete_idx % len(self.tier_sequence)] if self.tier_sequence else {"tier": 1, "maxTier": 8, "canRaid": True, "status": "battle", "rivals": {"-480906": {"isBot": True}}}
            return HWResponse(status=ResponseStatus.SUCCESS, detail={"response": info})
        return HWResponse(status=ResponseStatus.ERROR, error_name="unknown", detail={})

    def fetch_player_status(self):
        return PlayerStatus(name="T", level=130)

    def sleep(self):
        pass


# ---------------------------------------------------------------------------
# Unit tests: pure helpers
# ---------------------------------------------------------------------------

def test_to_signed32_unsigned():
    assert ta._to_signed32("3443036521") == -851930775
    assert ta._to_signed32(3443036521) == -851930775


def test_to_signed32_small_positive_unchanged():
    assert ta._to_signed32("52649907") == 52649907
    assert ta._to_signed32(100) == 100


def test_build_start_payload():
    p = ta._build_start_payload("-480906", [4003, 4023])
    c = p["calls"][0]
    assert c["name"] == "titanArenaStartBattle"
    assert c["args"] == {"rivalId": "-480906", "titans": [4003, 4023]}
    assert c["ident"] == "body"


def test_build_complete_tier_payload():
    p = ta._build_complete_tier_payload()
    c = p["calls"][0]
    assert c["name"] == "titanArenaCompleteTier"
    assert c["args"] == {}
    assert c["ident"] == "body"


def test_build_end_payload_shape_and_signed_seed():
    battle = {"attackers": {"4003": {"hp": 8508860}, "4023": {"hp": 1}}, "defenders": [{"4000": {}}]}
    p = ta._build_end_payload("-470706", "3443036521", win=True, battle=battle)
    prog = p["calls"][0]["args"]["progress"][0]
    assert prog["seed"] == -851930775  # signed 32-bit
    assert prog["attackers"]["heroes"]["4003"] == {"hp": 8508860, "energy": 1000, "isDead": False}
    assert prog["defenders"] == {"input": [], "heroes": {}}
    assert p["calls"][0]["args"]["result"] == {"win": True, "stars": 1}
    assert p["calls"][0]["ident"] == "body"


def test_build_end_payload_uses_battle_sim():
    """When a battle_sim result is supplied, its surviving HP is used instead
    of the full-HP fallback (which would be rejected by the server)."""
    battle = {"attackers": {"4003": {"hp": 8508860}, "4023": {"hp": 1}}, "defenders": [{"4000": {}}]}
    sim = {
        "attackers": {"heroes": {"4003": {"hp": 12345, "isDead": False}, "4023": {"hp": 0, "isDead": True}}},
        "defenders": {"heroes": {"4000": {"hp": 0, "isDead": True}}},
    }
    p = ta._build_end_payload("-470706", "3443036521", win=True, battle=battle, battle_sim=sim)
    prog = p["calls"][0]["args"]["progress"][0]
    # battle_sim's surviving HP overrides the full-HP fallback
    assert prog["attackers"]["heroes"]["4003"]["hp"] == 12345
    assert prog["attackers"]["heroes"]["4003"]["isDead"] is False
    assert prog["attackers"]["heroes"]["4023"]["hp"] == 0
    assert prog["attackers"]["heroes"]["4023"]["isDead"] is True
    # defenders come from the sim when provided
    assert prog["defenders"]["heroes"]["4000"]["hp"] == 0
    assert prog["defenders"]["heroes"]["4000"]["isDead"] is True


def test_build_end_payload_list_defenders():
    """defenders may be a list; _build_end_payload should still parse ids."""
    battle = {"attackers": {"4003": {"hp": 8508860}}, "defenders": [{"4000": {}}, {"4001": {}}]}
    p = ta._build_end_payload("-470706", "3443036521", win=True, battle=battle)
    prog = p["calls"][0]["args"]["progress"][0]
    # fallback defenders stay empty when a sim is not provided
    assert prog["defenders"] == {"input": [], "heroes": {}}


def test_build_end_payload_matches_real_request():
    """Replay the exact battle block from the user's captured manual win."""
    battle = {"attackers": {"4003": {"hp": 8508860, "energy": 1000}}, "defenders": [{"4000": {}, "4001": {}, "4002": {}, "4003": {}, "4013": {}}]}
    p = ta._build_end_payload("-470706", "3443036521", win=True, battle=battle)
    prog = p["calls"][0]["args"]["progress"][0]
    assert prog["seed"] == -851930775
    assert prog["defenders"] == {"input": [], "heroes": {}}
    assert prog["attackers"]["heroes"].get("4003") == {"hp": 8508860, "energy": 1000, "isDead": False}
    assert p["calls"][0]["args"]["result"] == {"win": True, "stars": 1}


def test_extract_seed():
    assert ta._extract_seed({"response": {"battle": {"seed": "3443036521"}}}) == "3443036521"
    assert ta._extract_seed({}) == ""


def test_is_invalid_battle():
    assert ta._is_invalid_battle({"response": {"error": "Invalid battle"}}) is True
    assert ta._is_invalid_battle({"response": {"battle": {"result": {"win": True}}}}) is False


def test_extract_win():
    assert ta._extract_win({"response": {"battle": {"result": {"win": True}}}}) is True
    assert ta._extract_win({"response": {"battle": {"result": {"win": False}}}}) is False
    assert ta._extract_win({}) is False


def test_extract_tier_info():
    resp = {"tier": 8, "maxTier": 8, "canRaid": True, "status": "battle", "rivals": {"-480906": {"isBot": True}}}
    info = ta._extract_tier_info({"response": resp})
    assert info["tier"] == 8
    assert info["max_tier"] == 8
    assert info["can_raid"] is True
    assert info["rivals"] == {"-480906": {"isBot": True}}


def test_pick_next_rival_prefers_human_over_bot():
    # humans first, lowest attackScore
    info = {"rivals": {"-480906": {"isBot": True}, "123": {"attackScore": 10}, "456": {"attackScore": 5}}}
    assert ta._pick_next_rival(info) == "456"
    # only bots -> lowest attackScore bot
    assert ta._pick_next_rival({"rivals": {"-480906": {"isBot": True, "attackScore": 100}}}) == "-480906"
    assert ta._pick_next_rival({"rivals": {}}) is None


# ---------------------------------------------------------------------------
# Flow tests: run_titan_arena (single stage)
# ---------------------------------------------------------------------------

def test_run_titan_arena_wins_on_first_seed():
    fc = FakeClient(seed_sequence=["3443036521"], winnable_seeds={3443036521})
    res = ta.run_titan_arena(fc, rival_id="-470706", team_rotation=[[4003]], max_attempts_per_team=10)
    assert res.win is True
    assert "titanArenaStartBattle" in fc.calls
    assert "titanArenaEndBattle" in fc.calls
    # critical: only win=True is ever submitted (never win=False, which caused false defeats)
    assert all(w is True for w in fc.end_results)
    assert fc.end_results == [True]


def test_run_titan_arena_retries_then_wins():
    # first seed is a loss, second is a win
    fc = FakeClient(seed_sequence=["100", "3443036521"], winnable_seeds={3443036521})
    res = ta.run_titan_arena(fc, rival_id="-470706", team_rotation=[[4003]], max_attempts_per_team=10)
    assert res.win is True
    # 2 starts (retry on invalid), 2 ends
    assert fc.calls.count("titanArenaStartBattle") == 2
    assert fc.calls.count("titanArenaEndBattle") == 2


def test_run_titan_arena_exhausts_and_fails():
    fc = FakeClient(seed_sequence=["100", "200"], winnable_seeds={999})  # never winnable
    res = ta.run_titan_arena(fc, rival_id="-470706", team_rotation=[[4003]], max_attempts_per_team=3)
    assert res.win is False
    assert fc.calls.count("titanArenaStartBattle") == 3


def test_run_titan_arena_rival_not_found_stops():

    class NFClient(FakeClient):
        def call(self, payload):
            name = payload["calls"][0]["name"]
            self.calls.append(name)
            if name == "titanArenaStartBattle":
                return HWResponse(status=ResponseStatus.ERROR, error_name=ErrorName.NOT_FOUND, detail={})
            return super().call(payload)

    fc = NFClient(seed_sequence=[], winnable_seeds=set())
    res = ta.run_titan_arena(fc, rival_id="-X", team_rotation=[[4003], [4023]], max_attempts_per_team=5)
    assert res.win is False
    # should not loop through teams after NotFound
    assert fc.calls.count("titanArenaStartBattle") == 1


def test_run_titan_arena_with_battle_sim_passes_sim_to_payload():
    """When a battle_sim callback is provided, its result must reach the
    EndBattle payload (so the server verifies matching HP)."""

    captured = {}

    def sim(rival_id, seed, battle):
        # pretend the sim says 4003 survived at 50% HP, 4023 died
        return {
            "attackers": {"heroes": {"4003": {"hp": 4254430, "isDead": False}, "4023": {"hp": 0, "isDead": True}}},
            "defenders": {"heroes": {"4000": {"hp": 0, "isDead": True}}},
        }

    class CaptureClient(FakeClient):
        def call(self, payload):
            name = payload["calls"][0]["name"]
            if name == "titanArenaEndBattle":
                captured["payload"] = payload
            return super().call(payload)

    fc = CaptureClient(seed_sequence=["3443036521"], winnable_seeds={3443036521})
    res = ta.run_titan_arena(
        fc, rival_id="-470706", team_rotation=[[4003, 4023]], max_attempts_per_team=10, battle_sim=sim
    )
    assert res.win is True
    prog = captured["payload"]["calls"][0]["args"]["progress"][0]
    assert prog["attackers"]["heroes"]["4003"]["hp"] == 4254430
    assert prog["attackers"]["heroes"]["4023"]["hp"] == 0


# ---------------------------------------------------------------------------
# Flow tests: run_titan_arena_auto (multi-stage)
# ---------------------------------------------------------------------------

def _tier(tier, max_tier, rival):
    return {"tier": tier, "maxTier": max_tier, "canRaid": True, "status": "battle", "rivals": {rival: {"isBot": True}}}


def test_auto_clears_two_stages_then_final_stop():
    """stage1 win -> completeTier tier1->2, stage2 win (final tier) -> stop without extra CompleteTier."""
    fc = FakeClient(
        seed_sequence=["3443036521", "52649907"],
        winnable_seeds={3443036521, 52649907},
        tier_sequence=[_tier(1, 2, "-480706"), _tier(2, 2, "-480806")],
    )
    cleared = ta.run_titan_arena_auto(fc, initial_rival_id="-480706", team_rotation=[[4003]], max_attempts_per_team=10, max_stages=20)
    assert cleared == 2
    # tier1 (non-final) calls CompleteTier once; final tier (2/2) stops without CompleteTier
    assert fc.calls.count("titanArenaCompleteTier") == 1


def test_auto_stops_at_final_stage_no_complete():
    """If first win's CompleteTier already reports tier==maxTier, no further CompleteTier."""
    fc = FakeClient(
        seed_sequence=["3443036521"],
        winnable_seeds={3443036521},
        tier_sequence=[_tier(8, 8, "-480906")],
    )
    cleared = ta.run_titan_arena_auto(fc, initial_rival_id="-480906", team_rotation=[[4003]], max_attempts_per_team=10, max_stages=20)
    assert cleared == 1
    # final tier (8/8) is cleared without calling CompleteTier per spec
    assert fc.calls.count("titanArenaCompleteTier") == 0


def test_auto_stops_on_stage_failure():
    fc = FakeClient(
        seed_sequence=["100", "200"],
        winnable_seeds={999},  # stage1 never winnable
        tier_sequence=[_tier(1, 8, "-480706")],
    )
    cleared = ta.run_titan_arena_auto(fc, initial_rival_id="-480706", team_rotation=[[4003]], max_attempts_per_team=2, max_stages=20)
    assert cleared == 0
    assert fc.calls.count("titanArenaCompleteTier") == 0


def test_auto_respects_max_stages_cap():
    # every stage winnable and never final -> cap at max_stages
    fc = FakeClient(
        seed_sequence=["3443036521"],
        winnable_seeds={3443036521},
        tier_sequence=[_tier(1, 99, "-480706")],
    )
    cleared = ta.run_titan_arena_auto(fc, initial_rival_id="-480706", team_rotation=[[4003]], max_attempts_per_team=10, max_stages=3)
    assert cleared == 3
    assert fc.calls.count("titanArenaCompleteTier") == 3
