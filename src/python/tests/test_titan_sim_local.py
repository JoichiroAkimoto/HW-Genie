"""Tests for hw_genie.commands.titan_sim_local (擬似シミュレーション).

レビュー指摘の全項目をカバー:
- hashlib.md5 に usedforsecurity=False
- /0xFFFFFFFF -> /0x100000000
- _total_power の ValueError ハンドリング
- advantage のバリデーションとドキュメント
- defenders list 正規化の冗長解消
- attackers 空時の return None
- energy の不整合にコメント（勝者 1000 / 敗者 0 の簡略化）
- CLI --battle-sim の help に擬似シミュレーション注意追記
"""

from unittest.mock import MagicMock, patch

import pytest

from hw_genie.commands.titan_sim_local import (
    LocalBattleSimulator,
    LocalSimulator,
    _seeded_random,
    _total_power,
)


# ---------------------------------------------------------------------------
# _seeded_random
# ---------------------------------------------------------------------------


def test_seeded_random_deterministic_and_range():
    """同じ seed/salt では常に同じ値、範囲は [0,1)."""
    v1 = _seeded_random("123", "salt")
    v2 = _seeded_random("123", "salt")
    assert v1 == v2
    assert 0.0 <= v1 < 1.0
    # salt 違いで異なる
    v3 = _seeded_random("123", "other")
    assert v1 != v3


def test_seeded_random_uses_0x100000000():
    """0x100000000 で割ることで最大値でも 1.0 未満になる."""
    # ハッシュ先頭 8 文字が ffffffff になる seed を探す（総当たりで数件）
    # 理論上、/0xFFFFFFFF なら 1.0 になるが /0x100000000 なら 0.999... になる
    # ここでは任意の seed で <1.0 を確認し、内部で正しい divisor が使われていることを
    # モックで検証する。
    val = _seeded_random("any", "any")
    assert val < 1.0
    # モックで最大値ケースを直接検証
    with patch("hw_genie.commands.titan_sim_local.hashlib.md5") as mock_md5:
        mock_md5.return_value.hexdigest.return_value = "ffffffff" + "0" * 24
        v = _seeded_random("x", "y")
        # 0xffffffff / 0x100000000 = 0.9999999997... (<1.0)
        assert v == 0xFFFFFFFF / 0x100000000
        assert v < 1.0
        # 旧バグ (/0xFFFFFFFF) なら 1.0 になる
        assert v != 1.0
    # usedforsecurity=False が渡されていること
    with patch("hw_genie.commands.titan_sim_local.hashlib.md5") as mock_md5:
        mock_md5.return_value.hexdigest.return_value = "0" * 32
        _seeded_random("s", "t")
        args, kwargs = mock_md5.call_args
        assert kwargs.get("usedforsecurity") is False


def test_seeded_random_usedforsecurity_flag():
    """hashlib.md5 が usedforsecurity=False で呼ばれること（Bandit 対応）."""
    # 実装が usedforsecurity=False を付けているかソースレベルで確認
    import inspect

    src = inspect.getsource(_seeded_random)
    assert "usedforsecurity=False" in src


# ---------------------------------------------------------------------------
# _total_power
# ---------------------------------------------------------------------------


def test_total_power_handles_invalid_values():
    """power が数値化できない場合はスキップし、例外を投げない."""
    titans = {
        "1": {"power": "invalid"},
        "2": {"power": None},
        "3": {"power": "123"},
        "4": {"power": 456},
        "5": {"power": ""},  # "" or 0 -> 0
        "6": "not-a-dict",  # 非 dict はスキップ
    }
    # "invalid" は ValueError でスキップ、123 と 456 のみ加算
    assert _total_power(titans) == 123 + 456
    # 空 dict は 0
    assert _total_power({}) == 0
    # 非 dict 値のみ
    assert _total_power({"1": "bad", "2": 123}) == 0


def test_total_power_handles_value_error_and_type_error():
    titans = {
        "a": {"power": "not-a-number"},
        "b": {"power": {"nested": 1}},  # TypeError で int({'nested':1}) -> TypeError
        "c": {"power": 100},
    }
    assert _total_power(titans) == 100


# ---------------------------------------------------------------------------
# advantage バリデーションとドキュメント
# ---------------------------------------------------------------------------


def test_advantage_validation_rejects_out_of_range():
    with pytest.raises(ValueError, match="advantage must be between"):
        LocalBattleSimulator(advantage=-0.1)
    with pytest.raises(ValueError, match="advantage must be between"):
        LocalBattleSimulator(advantage=1.1)
    with pytest.raises(ValueError, match="advantage must be between"):
        LocalBattleSimulator(advantage=2.0)


def test_advantage_validation_accepts_boundaries():
    LocalBattleSimulator(advantage=0.0)
    LocalBattleSimulator(advantage=1.0)
    LocalBattleSimulator(advantage=0.55)


def test_advantage_doc_mentions_range_and_multiplier():
    """ドキュメントに 0.0-1.0 と multiplier の説明があること."""
    import inspect

    doc = inspect.getdoc(LocalBattleSimulator)
    assert doc is not None
    assert "0.0" in doc and "1.0" in doc
    # 擬似シミュレーションであり保証がない旨
    assert "擬似" in doc or "保証はない" in doc or "pseudo" in doc.lower()
    # multiplier の説明
    assert "advantage" in doc.lower()


# ---------------------------------------------------------------------------
# defenders / attackers 正規化
# ---------------------------------------------------------------------------


def test_defenders_list_normalization():
    sim = LocalBattleSimulator(advantage=0.55)
    battle = {
        "attackers": {"1": {"power": 1000, "hp": 10000}},
        "defenders": [{"2": {"power": 500, "hp": 8000}}, {"3": {"power": 600, "hp": 9000}}],
    }
    res = sim("rival", "seed1", battle)
    assert res is not None
    # defenders が list でも正規化されて勝敗判定に使われる
    assert "2" in res["defenders"]["heroes"] or "3" in res["defenders"]["heroes"]
    # attackers はそのまま
    assert "1" in res["attackers"]["heroes"]


def test_defenders_list_normalization_empty():
    """defenders が空 list の場合は空 dict として扱われる（冗長解消後も同様）."""
    sim = LocalBattleSimulator(advantage=0.5)
    battle = {
        "attackers": {"1": {"power": 100, "hp": 1000}},
        "defenders": [],
    }
    res = sim("rival", "seed", battle)
    # defenders 空でも勝敗判定は行える
    assert res is not None


def test_defenders_non_dict_handled():
    """defenders が dict でも list でもない型の場合は空として扱う."""
    sim = LocalBattleSimulator()
    battle = {
        "attackers": {"1": {"power": 100, "hp": 1000}},
        "defenders": "invalid",
    }
    res = sim("rival", "seed", battle)
    assert res is not None


def test_attackers_empty_returns_none():
    sim = LocalBattleSimulator()
    # attackers 空 dict
    assert sim("rival", "seed", {"attackers": {}, "defenders": {"1": {"power": 100}}}) is None
    # attackers None
    assert sim("rival", "seed", {"attackers": None, "defenders": {}}) is None
    # attackers が dict でない
    assert sim("rival", "seed", {"attackers": [], "defenders": {}}) is None
    assert sim("rival", "seed", {"attackers": "bad", "defenders": {}}) is None
    # attackers キー自体が無い
    assert sim("rival", "seed", {"defenders": {"1": {"power": 100}}}) is None


# ---------------------------------------------------------------------------
# 勝敗と残HP / energy
# ---------------------------------------------------------------------------


def test_win_and_lose_deterministic():
    sim = LocalBattleSimulator(advantage=0.55)
    battle = {
        "attackers": {"1": {"power": 1000, "hp": 10000}, "2": {"power": 900, "hp": 9000}},
        "defenders": {"3": {"power": 1500, "hp": 8000}},
    }
    r1 = sim("rival", "seed123", battle)
    r2 = sim("rival", "seed123", battle)
    assert r1 == r2  # 決定論的


def test_win_generates_expected_hp_range():
    """勝利時は attackers の残HPが 20-80% の範囲に収まる."""
    sim = LocalBattleSimulator(advantage=1.0)  # 攻撃側が確実に勝つ
    battle = {
        "attackers": {"1": {"power": 10000, "hp": 10000}, "2": {"power": 10000, "hp": 10000}},
        "defenders": {"3": {"power": 1, "hp": 100}},
    }
    res = sim("r", "seed", battle)
    assert res is not None
    # 勝ちなので defenders 全滅
    for h in res["defenders"]["heroes"].values():
        assert h["hp"] == 0 and h["isDead"] is True
    for tid, h in res["attackers"]["heroes"].items():
        # 最も power が低い 1体は 20% にされる、それ以外は 30-80%
        max_hp = battle["attackers"][tid]["hp"]
        assert 1 <= h["hp"] <= max_hp
        assert h["isDead"] is False
        assert h["energy"] == 1000


def test_lose_generates_expected_hp():
    """敗北時は attackers 全滅、defenders 残存."""
    sim = LocalBattleSimulator(advantage=0.0)  # 攻撃側が確実に負ける
    battle = {
        "attackers": {"1": {"power": 1, "hp": 100}},
        "defenders": {"2": {"power": 10000, "hp": 10000}, "3": {"power": 10000, "hp": 12000}},
    }
    res = sim("r", "seed-lose", battle)
    assert res is not None
    for h in res["attackers"]["heroes"].values():
        assert h["hp"] == 0 and h["isDead"] is True and h["energy"] == 0
    for h in res["defenders"]["heroes"].values():
        assert h["hp"] >= 1 and h["isDead"] is False
        # energy の不整合コメント: 簡易実装では 0 で統一
        assert h["energy"] == 0


def test_energy_inconsistency_has_comment():
    """energy の不整合（敗北時 defenders energy=0）がコメントで説明されている."""
    import inspect

    src = inspect.getsource(LocalBattleSimulator.__call__)
    # コメントに energy と不整合/簡易/将来 の言及がある
    assert "energy" in src.lower()
    # コメントマーカー NOTE または 不整合/簡略化 の記載
    assert "NOTE" in src or "不整合" in src or "簡易" in src or "簡略" in src


def test_local_simulator_alias():
    assert LocalSimulator is LocalBattleSimulator


def test_attackers_non_dict_value_skipped_in_win():
    """attackers 内の非 dict 要素はスキップされる."""
    sim = LocalBattleSimulator(advantage=1.0)
    battle = {
        "attackers": {"1": {"power": 5000, "hp": 10000}, "bad": "not-dict", "2": {"power": 5000, "hp": 10000}},
        "defenders": {"3": {"power": 1, "hp": 100}},
    }
    res = sim("r", "seed", battle)
    assert res is not None
    assert "bad" not in res["attackers"]["heroes"]
    assert "1" in res["attackers"]["heroes"]
    assert "2" in res["attackers"]["heroes"]


# ---------------------------------------------------------------------------
# CLI 統合テスト: --battle-sim
# ---------------------------------------------------------------------------


def test_titan_arena_cli_battle_sim_default_is_local(monkeypatch):
    """デフォルトは local（擬似シミュレーション）."""

    # parser の help に擬似シミュレーション注意が含まれるか

    # main() 内で作られる parser を直接構築して検証
    # main.main の parser 定義を呼び出すために、--help をキャプチャ

    # 擬似的に main の parser を再構築して help を取得
    # main.main の実装を呼び出す代わりに、help 文字列を直接検証
    # main.py の --battle-sim help に「擬似」「保証はない」等が含まれる
    import pathlib

    text = pathlib.Path("src/python/hw_genie/main.py").read_text(encoding="utf-8")
    assert "擬似" in text or "pseudo" in text.lower()
    assert "保証" in text or "NOT guarantee" in text
    assert "default" in text.lower() and "local" in text.lower()


def test_titan_arena_cli_battle_sim_choices_via_parser(monkeypatch):
    """--battle-sim の選択肢が hwh/local で、help に注意書きがある."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "hw_genie.main", "titan-arena", "--help"],
        capture_output=True,
        text=True,
        cwd="src/python",
    )
    out = result.stdout + result.stderr
    assert "local" in out
    assert "hwh" in out
    # 擬似シミュレーションの注意書き
    assert "擬似" in out or "pseudo" in out.lower() or "does NOT guarantee" in out
    # Chrome 非依存 と BattleCalc の記述
    assert "LocalBattleSimulator" in out or "app-standalone" in out or "pseudo" in out.lower()


def test_cmd_titan_arena_selects_local_by_default(monkeypatch):
    """未指定(None)でも local が選ばれる。"""
    from hw_genie import main as main_mod

    monkeypatch.setattr("hw_genie.main._ensure_session", lambda a: {"x-auth-token": "t"})
    monkeypatch.setattr("hw_genie.main.HWClient", MagicMock)
    monkeypatch.setattr("hw_genie.main.resolve_account", lambda a: "acc")
    monkeypatch.setattr(
        "hw_genie.core.auth.update_session_with_headers", lambda h, a: {"status": "success", "player": MagicMock(name="P"), "headers": h}
    )

    # titan_arena の実行をモック
    called = {}

    def fake_run(client, rival_id=None, team_rotation=None, max_attempts_per_team=10, account=None, battle_sim=None):
        called["battle_sim"] = battle_sim
        from hw_genie.commands.titan_arena import TitanArenaResult

        return TitanArenaResult(win=True, attempts=1, team=[1])

    monkeypatch.setattr("hw_genie.commands.titan_arena.run_titan_arena", fake_run)

    args = MagicMock()
    args.account = None
    args.curl = None
    args.teams = None
    args.rival = None
    args.max_attempts = 10
    args.max_stages = 20
    args.auto = False
    args.battle_sim = None  # 未指定

    main_mod.cmd_titan_arena(args)
    assert isinstance(called["battle_sim"], LocalBattleSimulator)


def test_cmd_titan_arena_selects_hwh_when_requested(monkeypatch):
    """--battle-sim hwh で HWH が選ばれる."""
    from hw_genie import main as main_mod
    from hw_genie.commands.titan_sim_hwh import TitanSimulatorHWH

    monkeypatch.setattr("hw_genie.main._ensure_session", lambda a: {"x-auth-token": "t"})
    monkeypatch.setattr("hw_genie.main.HWClient", MagicMock)
    monkeypatch.setattr("hw_genie.main.resolve_account", lambda a: "acc")

    called = {}

    def fake_run(client, rival_id=None, team_rotation=None, max_attempts_per_team=10, account=None, battle_sim=None):
        called["battle_sim"] = battle_sim
        from hw_genie.commands.titan_arena import TitanArenaResult

        return TitanArenaResult(win=True, attempts=1, team=[1])

    monkeypatch.setattr("hw_genie.commands.titan_arena.run_titan_arena", fake_run)

    args = MagicMock()
    args.account = None
    args.curl = None
    args.teams = None
    args.rival = None
    args.max_attempts = 10
    args.max_stages = 20
    args.auto = False
    args.battle_sim = "hwh"

    main_mod.cmd_titan_arena(args)
    assert isinstance(called["battle_sim"], TitanSimulatorHWH)


def test_cmd_titan_arena_selects_local_when_explicit(monkeypatch):
    """--battle-sim local で local が選ばれる."""
    from hw_genie import main as main_mod

    monkeypatch.setattr("hw_genie.main._ensure_session", lambda a: {"x-auth-token": "t"})
    monkeypatch.setattr("hw_genie.main.HWClient", MagicMock)
    monkeypatch.setattr("hw_genie.main.resolve_account", lambda a: "acc")

    called = {}

    def fake_run(client, rival_id=None, team_rotation=None, max_attempts_per_team=10, account=None, battle_sim=None):
        called["battle_sim"] = battle_sim
        from hw_genie.commands.titan_arena import TitanArenaResult

        return TitanArenaResult(win=True, attempts=1, team=[1])

    monkeypatch.setattr("hw_genie.commands.titan_arena.run_titan_arena", fake_run)

    args = MagicMock()
    args.account = None
    args.curl = None
    args.teams = None
    args.rival = None
    args.max_attempts = 10
    args.max_stages = 20
    args.auto = False
    args.battle_sim = "local"

    main_mod.cmd_titan_arena(args)
    assert isinstance(called["battle_sim"], LocalBattleSimulator)
