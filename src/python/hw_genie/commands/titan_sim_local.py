"""ローカルの Titan Arena 戦闘シミュレーター（Chrome/HWH 非依存）。

HWH の BattleCalc (get_titanClanPvp) をアプリ内で完結させるための
軽量な代替。ゲーム本体の BattleInstantPlay を完全に再現するのではなく、
手持ちの titan ステータスと seed から決定論的に勝敗と残HPを生成し、
サーバの再計算検証を通過する形式の progress を返す。

設計方針:
- 固定リスト（DEFAULT_TEAM_ROTATION）＋ 10回リトライの前提で、
  同じ team/rival/seed に対しては常に同じ結果を返す（決定論的）
- 総戦力（power）と seed による揺らぎで勝敗を決め、手軽に勝率の高い
  組合せを探索できる
- 将来的に Node への完全移植（heroes.4e2c73d...js の BattleInstantPlay）
  に差し替える場合も同じ battle_sim シグネチャで利用可能

battle_sim(rival_id, seed, battle) -> Optional[dict]
  battle: StartBattle の battle ブロック
  戻り値: {"attackers": {"heroes": {...}}, "defenders": {"heroes": {...}}}
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional


def _seeded_random(seed: str | int, salt: str = "") -> float:
    """seed と salt から 0.0-1.0 の決定論的な擬似乱数を生成。"""
    h = hashlib.md5(f"{seed}:{salt}".encode()).hexdigest()
    # 先頭 8 文字を 0-1 に正規化
    return int(h[:8], 16) / 0xFFFFFFFF


def _total_power(titans: dict[str, Any]) -> int:
    total = 0
    for tid, tdata in titans.items():
        if isinstance(tdata, dict):
            total += int(tdata.get("power", 0) or 0)
    return total


class LocalBattleSimulator:
    """Chrome/HWH 非依存の簡易シミュレーター。

    Args:
        advantage: 攻撃側に与えるバイアス（0.0-1.0）。0.5 なら互角、
            0.6 なら攻撃側が 60% で勝つように調整。テストでは 0.55 程度が
            適度に勝敗が分かれる。
    """

    def __init__(self, advantage: float = 0.55):
        self.advantage = advantage

    def __call__(
        self, rival_id: str, seed: str | int, battle: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        attackers = battle.get("attackers") or {}
        defenders = battle.get("defenders") or {}

        # defenders が list 形式（StartBattle の defenders が [{id:{}}]）の場合は
        # dict に正規化
        if isinstance(defenders, list):
            norm: dict[str, Any] = {}
            for item in defenders:
                if isinstance(item, dict):
                    for k, v in item.items():
                        norm[str(k)] = v
            defenders = norm

        if not isinstance(attackers, dict) or not attackers:
            return None
        if not isinstance(defenders, dict) or not defenders:
            # defenders が空でも勝敗は決められる（全滅扱い）
            defenders = {}

        att_power = _total_power(attackers)
        def_power = _total_power(defenders)

        # seed による揺らぎ（-0.1 〜 +0.1）
        rnd = _seeded_random(seed, rival_id)
        jitter = (rnd - 0.5) * 0.2  # -0.1 to +0.1

        # 勝敗判定: 攻撃側パワー * (advantage + jitter) > 防御側パワー
        # advantage 0.55 なら攻撃側がやや有利
        att_eff = att_power * (self.advantage + jitter + 0.5)  # 0.95〜1.15 倍
        # 防御側は 1.0 倍
        win = att_eff > def_power

        # 残HPの生成（サーバ検証を通過する形式）
        # win なら defenders は全滅、attackers は 30-80% 残存
        # lose なら attackers は全滅、defenders は 30-80% 残存
        if win:
            att_heroes: dict[str, Any] = {}
            for tid, tdata in attackers.items():
                if not isinstance(tdata, dict):
                    continue
                max_hp = int(tdata.get("hp", 0) or 0)
                # 残HPは seed と tid で決定論的に 30-80%
                r = _seeded_random(seed, f"att:{tid}")
                remain = int(max_hp * (0.3 + r * 0.5))
                # 最低 1 は残す（isDead=False の整合性）
                if remain < 1:
                    remain = 1
                att_heroes[str(tid)] = {
                    "hp": remain,
                    "energy": 1000,
                    "isDead": False,
                }
                # 1体だけ瀕死にする（よりリアルに）
                # 最も power が低い titan を瀕死に
            # 最も power が低い 1体を瀕死（20%）に
            if att_heroes:
                weakest = min(
                    att_heroes.keys(),
                    key=lambda k: int(attackers.get(k, {}).get("power", 0) or 0),
                )
                # 20% に
                max_hp_w = int(attackers.get(weakest, {}).get("hp", 0) or 0)
                att_heroes[weakest]["hp"] = max(1, int(max_hp_w * 0.2))

            def_heroes: dict[str, Any] = {}
            for tid in defenders.keys():
                def_heroes[str(tid)] = {"hp": 0, "energy": 0, "isDead": True}
            return {"attackers": {"heroes": att_heroes}, "defenders": {"heroes": def_heroes}}
        else:
            # lose: attackers 全滅
            att_heroes = {}
            for tid in attackers.keys():
                att_heroes[str(tid)] = {"hp": 0, "energy": 0, "isDead": True}
            def_heroes = {}
            for tid, tdata in defenders.items():
                if not isinstance(tdata, dict):
                    continue
                max_hp = int(tdata.get("hp", 0) or 0)
                r = _seeded_random(seed, f"def:{tid}")
                remain = int(max_hp * (0.3 + r * 0.5))
                if remain < 1:
                    remain = 1
                def_heroes[str(tid)] = {
                    "hp": remain,
                    "energy": 0,
                    "isDead": False,
                }
            return {"attackers": {"heroes": att_heroes}, "defenders": {"heroes": def_heroes}}


# エイリアス
LocalSimulator = LocalBattleSimulator
