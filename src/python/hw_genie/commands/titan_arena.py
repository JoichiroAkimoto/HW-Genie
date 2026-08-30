"""
Titan Arena 自動バトルコマンド

仕様（HWH / 実クライアントの挙動を解析して判明）:
- 戦闘開始 API (titanArenaStartBattle) を呼び、レスポンスの battle ブロックから
  `seed` と初期配置（attackers/defenders のフルHP）を取得する。
- 戦闘終了 API (titanArenaEndBattle) を呼ぶ。
  ※ サーバーは送られた seed + 初期配置 から戦闘を「再計算」し、
     送られた progress[0].attackers.heroes / defenders.heroes の各タイタンの
     「残HP / 死亡」と完全に一致するかを検証する。
     一致しなければ "Invalid battle" を返す（戦闘は消費されない）。
     → つまり EndBattle には「実際に戦闘シミュレーションを回した結果
       （各タイタンの残HP・isDead）」を詰めなければならない。
       適当なHP（全生存フルHPなど）を送ると必ず Invalid battle になる。
  ※ HWH は _(e,s) 内で e.seed = floor(Date.now()/1000)+random(0,1000) と
     自分で seed を決めてから BattleCalc（戦闘シミュレーター）を回し、
     その結果の progress を送っている。
- 重要: 常に win=True のみを送る。Invalid battle ならその seed は負けなので、
  再び StartBattle して新しい seed をもらいリトライする。敵は実際に勝てたときだけ消費される。

ペイロードの要点（実クライアントの EndBattle 送信値 + HWH BattleCalc 解析）:
- seed は符号付き 32bit で送る（StartBattle は符号なし文字列で返す）。
- attackers.heroes は「戦闘後の残HP」を {hp, energy:1000, isDead:false/true} で渡す。
    全生存フルHPはダメ。BattleCalc 等のシミュレーション結果が必要。
- defenders.heroes は「戦闘後の残HP（通常は全滅 hp:0, isDead:true）」を渡す。
    defenders は StartBattle の battle.defenders がリスト形式 ["<id>":{...}] で
    返る場合があるので両形式に対応する。
- battleSim コールバックを渡せば、それを使って正しい progress を生成できる
  （ブラウザ経由で HWH.BattleCalc を呼ぶハイブリッド方式など）。

ステージ（tier）進行（複数敵対応）:
- 各 tier には可変数の敵（rivals）がいる。tier 内の全ての alive な敵を
  順に倒してから titanArenaCompleteTier で次 tier へ進む（GetStatus の
  rivals[].titans[].state.isDead で alive 判定、attackScore 昇順で選択）。
- 最終 tier (tier == maxTier) の全敵を倒したら completeTier を呼ばず停止する。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from hw_genie.core.client import Emojis, ErrorName
from hw_genie.core.utils import print_player_status

logger = logging.getLogger(__name__)


class BattleSim(Protocol):
    """battle_sim の型プロトコル。将来のカスタムシミュレーターでも同じシグネチャで差し替え可能。"""

    def __call__(self, rival_id: str, seed: str | int, battle: dict[str, Any]) -> Optional[dict[str, Any]]: ...


# ----------------------------------------------------------------------------
# 設定
# ----------------------------------------------------------------------------

# デフォルト編成（アカウントごとに1つ決めておく想定）
DEFAULT_TITANS = [4044, 4023, 4043, 4024, 4022]

# 固定の編成ローテーション（サーバ負荷・BANリスクを避けるため全組合せ探索はせず、
# 事前に登録した数パターンを頭から順に 10回ずつ試す）。
# このリストは後ほど共有・修正される前提で、編集容易な定数として保持する。
# 例: Champion の ToE 実績チーム [4044,4012,4013,4043,4010] を先頭に配置。
DEFAULT_TEAM_ROTATION = [
    [4044, 4012, 4013, 4043, 4010],  # Champion ToE 実績（ステージ4,5で勝利）
    [4044, 4023, 4043, 4024, 4022],
    [4023, 4043, 4024, 4022, 4040],
]

DEFAULT_MAX_ATTEMPTS = 10
DEFAULT_RIVAL_ID = "-480906"
DEFAULT_MAX_STAGES = 20


@dataclass
class TitanArenaResult:
    win: bool
    attempts: int
    team: list[int]
    seed: str = ""
    detail: Any = None


# ----------------------------------------------------------------------------
# ペイロード生成
# ----------------------------------------------------------------------------


def _build_start_payload(rival_id: str, titans: list[int], action_ts: int = 0) -> dict[str, Any]:
    """titanArenaStartBattle 用ペイロード生成"""
    return {
        "calls": [
            {
                "name": "titanArenaStartBattle",
                "args": {
                    "rivalId": rival_id,
                    "titans": titans,
                },
                "context": {"actionTs": action_ts},
                "ident": "body",
            }
        ]
    }


def _build_complete_tier_payload(action_ts: int = 0) -> dict[str, Any]:
    """titanArenaCompleteTier 用ペイロード生成（ステージ進行）"""
    return {
        "calls": [
            {
                "name": "titanArenaCompleteTier",
                "args": {},
                "context": {"actionTs": action_ts},
                "ident": "body",
            }
        ]
    }


def _to_signed32(seed: str | int) -> int:
    """seed は StartBattle では符号なし 32bit 文字列で返るが、EndBattle では
    符号付き 32bit として送る必要がある。これを怠るとサーバーが異なる seed で
    シミュレーションし、'Invalid battle' になる（根本原因）。"""
    try:
        v = int(seed)
    except (TypeError, ValueError):
        return 0
    if v > 0x7FFFFFFF:
        v -= 0x100000000
    return v


def _extract_defender_ids(battle: dict[str, Any]) -> list[str]:
    """StartBattle の battle.defenders は
    - 辞書形式: {"<id>": {...}, ...}
    - リスト形式: [{"<id>": {...}}, ...]
    の両方があり得る。どちらでも ID のリストを返す。
    """
    d = battle.get("defenders", {})
    ids: list[str] = []
    if isinstance(d, dict):
        ids = [str(k) for k in d.keys()]
    elif isinstance(d, list):
        for item in d:
            if isinstance(item, dict):
                ids.extend(str(k) for k in item.keys())
    return ids


def _build_end_payload(
    rival_id: str,
    seed: str | int | None,
    win: bool,
    battle: dict[str, Any] | None = None,
    stars: int = 1,
    action_ts: int = 0,
    server_version: int = 287,
    battle_sim: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """titanArenaEndBattle 用ペイロード生成。

    実際のクライアント送信例(titanArenaEndBattle) + HWH BattleCalc 解析に基づく:
      progress[0] = {
        "v": 287, "b": 0, "seed": <符号付き32bit>,
        "attackers": {"input": ["auto",0,0,"auto",11,0.133],
                     "heroes": { "<titanID>": {"hp": <残HP>, "energy": 1000, "isDead": <bool>} }},
        "defenders": {"input": [], "heroes": { "<titanID>": {"hp": <残HP>, "isDead": <bool>} }}
      }

    ※ サーバーは送られた seed + 初期配置から戦闘を「再計算」し、
      progress の各タイタンの残HP/死亡と完全に一致するかを検証する。
      したがって attackers/defenders の heroes には
      「実際の戦闘シミュレーション結果（残HP・isDead）」を詰めなければならない。
      適当なHP（全生存フルHPなど）を送ると必ず Invalid battle になる。

    battle_sim: 外部の戦闘シミュレーター（HWH.BattleCalc 等）が生成した
      {"attackers": {id: {hp,isDead,...}}, "defenders": {id: {hp,isDead,...}}}
      を渡すと、それをそのまま heroes に反映する。
      None の場合は StartBattle の初期HPで全生存扱いとする（= Invalid battle になるので
      実運用では battle_sim を渡すこと）。
    """
    battle = battle or {}

    # battle_sim 優先。なければ StartBattle の初期HPで全生存扱い。
    if battle_sim:
        # battle_sim 構造: {"attackers": {"heroes": {id: {hp,isDead,energy}}},
        #                   "defenders": {"heroes": {id: {hp,isDead,energy}}}}
        att_src = (battle_sim.get("attackers") or {}).get("heroes") or {}
        att_heroes = {}
        for tid, h in att_src.items():
            if isinstance(h, dict):
                att_heroes[str(tid)] = {
                    "hp": h.get("hp", 0),
                    "energy": h.get("energy", 1000),
                    "isDead": bool(h.get("isDead", False)),
                }
        def_src = (battle_sim.get("defenders") or {}).get("heroes") or {}
        def_heroes = {}
        for tid, h in def_src.items():
            if isinstance(h, dict):
                def_heroes[str(tid)] = {
                    "hp": h.get("hp", 0),
                    "energy": h.get("energy", 0),
                    "isDead": bool(h.get("isDead", False)),
                }
        # defenders が空なら StartBattle の defenders ID を全滅で補完
        if not def_heroes:
            for tid in _extract_defender_ids(battle):
                def_heroes[tid] = {"hp": 0, "energy": 0, "isDead": True}
    else:
        # 初期HPで全生存扱い（= 実運用では Invalid battle になる）
        attackers_map = battle.get("attackers", {})
        att_heroes = {}
        if isinstance(attackers_map, dict):
            for tid, tdata in attackers_map.items():
                if isinstance(tdata, dict):
                    att_heroes[str(tid)] = {
                        "hp": tdata.get("hp", 0),
                        "energy": 1000,
                        "isDead": False,
                    }
        # battle_sim なし時は defenders を空 heroes とする（テスト互換）
        def_heroes = {}

    progress = [
        {
            "v": server_version,
            "b": 0,
            "seed": _to_signed32(seed) if seed not in (None, "") else 0,
            "attackers": {
                "input": ["auto", 0, 0, "auto", 11, 0.133],
                "heroes": att_heroes,
            },
            "defenders": {
                "input": [],
                "heroes": def_heroes,
            },
        }
    ]
    return {
        "calls": [
            {
                "name": "titanArenaEndBattle",
                "args": {
                    "result": {"win": win, "stars": stars},
                    "progress": progress,
                    "rivalId": rival_id,
                },
                "context": {"actionTs": action_ts},
                "ident": "body",
            }
        ]
    }


# ----------------------------------------------------------------------------
# メイン処理
# ----------------------------------------------------------------------------


def run_titan_arena(
    client_or_headers,
    rival_id: str = DEFAULT_RIVAL_ID,
    team_rotation: Optional[list[list[int]]] = None,
    max_attempts_per_team: int = DEFAULT_MAX_ATTEMPTS,
    account: Optional[str] = None,
    battle_sim: Optional[BattleSim] = None,
):
    """Titan Arena 自動バトル実行（1 ステージ分）。

    Args:
        client_or_headers: HWClient または ヘッダー辞書
        rival_id:          対戦相手 ID
        team_rotation:     試行する編成のリスト（任意の数・組合せ）
        max_attempts_per_team: 1編成あたりの最大試行回数（任意指定可能）
        account:           ステータス表示用アカウント名
        battle_sim:        戦闘シミュレーター呼び出し。
            callable(rival_id, seed, battle) -> {"attackers":{id:{hp,isDead,...}},
                                                  "defenders":{id:{hp,isDead,...}}}
            を返す。None の場合は初期HPで全生存扱い（= Invalid battle になるので
            実運用では HWH の BattleCalc 等を渡すこと）。
    """
    if isinstance(client_or_headers, dict):
        from hw_genie.core.client import HWClient

        client = HWClient(client_or_headers)
    else:
        client = client_or_headers

    if not team_rotation:
        team_rotation = DEFAULT_TEAM_ROTATION

    print(f"\n{Emojis.START}Starting Titan Arena Auto-Battle...", flush=True)
    print(f"  Rival: {rival_id}", flush=True)
    print(f"  Teams: {len(team_rotation)} | Max attempts/team: {max_attempts_per_team}", flush=True)
    if battle_sim is None:
        print(f"  {Emojis.WARNING}battle_sim=None -> progress uses full-HP (will get 'Invalid battle').", flush=True)

    for team_idx, titans in enumerate(team_rotation):
        print(f"\n{Emojis.STEP}Team {team_idx + 1}/{len(team_rotation)}: titans={titans}", flush=True)

        for attempt in range(1, max_attempts_per_team + 1):
            print(f"  {Emojis.STEP}Attempt {attempt}/{max_attempts_per_team}: starting battle...", flush=True)
            # 1. 戦闘開始
            start_payload = _build_start_payload(rival_id, titans)
            start_res = client.call(start_payload)

            if not start_res.is_success:
                err = start_res.error_name
                # 相手がいない / 戦闘不可 の場合は、この rival ではどうやっても
                # 勝てないので全編成・全試行を即スキップして終了する
                if err in (ErrorName.NOT_FOUND, ErrorName.NOT_AVAILABLE):
                    print(f"    {Emojis.ERROR}Rival unavailable ({err}). Aborting all attempts.", flush=True)
                    status = client.fetch_player_status()
                    print_player_status(status)
                    return TitanArenaResult(win=False, attempts=attempt, team=titans, detail={"error": err})
                print(f"    {Emojis.ERROR}StartBattle failed: {err}", flush=True)
                break

            battle = _extract_battle(start_res.detail)
            seed = _extract_seed(start_res.detail)
            print(f"    {Emojis.INFO}seed={seed}", flush=True)

            # 2. 戦闘終了
            #    サーバーは seed + 初期配置 から戦闘を「再計算」し、
            #    送られた progress の各タイタンの残HP/死亡と完全に一致するかを検証する。
            #    一致しなければ "Invalid battle" を返す（戦闘は消費されない）。
            #    → 正しく勝つには、実際の戦闘シミュレーション結果
            #      （各タイタンの残HP・isDead）を progress に詰める必要がある。
            #      battle_sim(rival_id, seed, battle) がその結果を返す。
            #    ※ win=False を送ると「負け」として受理され敗北が消費される。
            #      そのため、常に win=True のみを送り、Invalid battle なら
            #      再び StartBattle して新しい seed をもらいリトライする。
            sim = None
            if battle_sim is not None:
                try:
                    sim = battle_sim(rival_id, seed, battle)
                except Exception as ex:
                    logger.warning("battle_sim failed for rival %s seed %s: %s", rival_id, seed, ex)
                    sim = None
            end_payload = _build_end_payload(
                rival_id=rival_id,
                seed=seed,
                win=True,
                battle=battle,
                battle_sim=sim,
            )
            end_res = client.call(end_payload)

            if not end_res.is_success:
                # ネットワーク等の予期せぬエラー（NotFound は戦闘済み=勝利の証）
                if end_res.error_name == ErrorName.NOT_FOUND:
                    print(f"    {Emojis.SUCCESS}WIN confirmed (rival consumed, seed={seed}).", flush=True)
                    win = True
                    final_detail = end_res.detail
                else:
                    print(f"    {Emojis.ERROR}EndBattle failed: {end_res.error_name}", flush=True)
                    break
            elif _is_invalid_battle(end_res.detail):
                # この seed は負け → 敵は消費されていない。新しい seed でリトライ
                print(f"    {Emojis.WARNING}seed={seed} is a loss. Retrying for a winnable seed...", flush=True)
                client.sleep()
                continue
            else:
                # win=True が受理された → 勝利
                win = True
                final_detail = end_res.detail

            if win:
                print(f"    {Emojis.SUCCESS}WIN! (team={titans}, seed={seed})", flush=True)
                _print_summary(final_detail)
                status = client.fetch_player_status()
                print_player_status(status)
                return TitanArenaResult(win=True, attempts=attempt, team=titans, seed=seed, detail=final_detail)

            print(f"    {Emojis.WARNING}Lost (attempt {attempt}).", flush=True)
            client.sleep()

        print(f"  {Emojis.INFO}Team {team_idx + 1} exhausted {max_attempts_per_team} attempts.", flush=True)
    print(f"\n{Emojis.ERROR}Titan Arena failed on all teams/attempts.", flush=True)
    status = client.fetch_player_status()
    print_player_status(status)
    return TitanArenaResult(win=False, attempts=max_attempts_per_team, team=team_rotation[-1] if team_rotation else [])


def _build_status_payload() -> dict[str, Any]:
    """titanArenaGetStatus 用ペイロード生成"""
    return {
        "calls": [
            {
                "name": "titanArenaGetStatus",
                "args": {},
                "context": {"actionTs": 0},
                "ident": "body",
            }
        ]
    }


def _extract_response(detail: Any) -> Optional[dict[str, Any]]:
    """detail から response dict を抽出する共通ヘルパ。"""
    try:
        if isinstance(detail, dict):
            if "response" in detail and isinstance(detail["response"], dict):
                return detail["response"]
            if "body" in detail and isinstance(detail["body"], dict) and isinstance(detail["body"].get("response"), dict):
                return detail["body"]["response"]  # type: ignore[return-value]
        elif isinstance(detail, list):
            for item in detail:
                if isinstance(item, dict) and isinstance(item.get("response"), dict):
                    return item["response"]  # type: ignore[return-value]
    except Exception:
        pass
    return None


def _parse_status_detail(detail: Any) -> Optional[dict[str, Any]]:
    """GetStatus / CompleteTier 共通の tier 情報パース。"""
    resp = _extract_response(detail)
    if isinstance(resp, dict):
        return {
            "tier": resp.get("tier"),
            "max_tier": resp.get("maxTier"),
            "can_raid": resp.get("canRaid"),
            "status": resp.get("status"),
            "rivals": resp.get("rivals", {}) or {},
            "defenders": resp.get("defenders", {}) or {},
            "_raw": resp,
        }
    return None


def _fetch_arena_status(client) -> Optional[dict[str, Any]]:
    """現在の tier / rivals を取得する。"""
    try:
        res = client.call(_build_status_payload())
        if not res.is_success:
            return None
        return _parse_status_detail(res.detail)
    except Exception:
        return None


def _is_rival_alive(rival: Any) -> bool:
    """rival の titans[].state.isDead を見て生存判定。"""
    if not isinstance(rival, dict):
        return False
    titans = rival.get("titans")
    # 実データでは titans は常に dict。テスト用 FakeClient は titans を持たない
    # 簡易 rival を返すため、None の場合のみ alive とみなす。
    if titans is None:
        return True
    if isinstance(titans, list):
        return False
    if not isinstance(titans, dict):
        return False
    if len(titans) == 0:
        return False
    for tdata in titans.values():
        if not isinstance(tdata, dict):
            continue
        state = tdata.get("state") or {}
        if not isinstance(state, dict):
            continue
        # isDead が明示的に False なら生存
        if state.get("isDead") is False:
            return True
        # isDead が無い場合は hp > 0 で判定
        if "isDead" not in state and isinstance(state.get("hp"), (int, float)) and state.get("hp", 0) > 0:
            return True
    return False


def _get_alive_rivals_sorted(tier_info: dict[str, Any]) -> list[str]:
    """alive な rivalId を attackScore 昇順（非bot優先）で返す。"""
    rivals = tier_info.get("rivals") or {}
    alive = [uid for uid, r in rivals.items() if _is_rival_alive(r)]

    def score(uid: str) -> int:
        r = rivals.get(uid)
        if not isinstance(r, dict):
            return 10**9
        try:
            return int(r.get("attackScore", 0) or 0)
        except Exception:
            return 10**9

    humans = [uid for uid in alive if not (rivals.get(uid) or {}).get("isBot")]
    bots = [uid for uid in alive if (rivals.get(uid) or {}).get("isBot")]
    humans.sort(key=score)
    bots.sort(key=score)
    return humans + bots


def _tier_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def run_titan_arena_auto(
    client_or_headers,
    initial_rival_id: str = DEFAULT_RIVAL_ID,
    team_rotation: Optional[list[list[int]]] = None,
    max_attempts_per_team: int = DEFAULT_MAX_ATTEMPTS,
    max_stages: int = DEFAULT_MAX_STAGES,
    account: Optional[str] = None,
    battle_sim: Optional[BattleSim] = None,
) -> int:
    """
    Titan Arena 自動進行（複数ステージ・複数敵対応）。

    各 tier には可変数の敵（rivals）がいる。tier 内の全ての alive な敵を
    順に倒してから titanArenaCompleteTier で次 tier へ進む。
    各敵に対しては team_rotation の各編成を max_attempts_per_team 回ずつ
    ローテーションして勝つまでリトライし、全編成全試行で勝てなければ abort する。
    最終 tier (tier == maxTier) の全敵を倒したら CompleteTier を呼ばず停止。
    戻り値は CompleteTier で進んだ tier 数 + 最終 tier クリア時は +1 を含む。
    """
    if isinstance(client_or_headers, dict):
        from hw_genie.core.client import HWClient

        client = HWClient(client_or_headers)
    else:
        client = client_or_headers

    if not team_rotation:
        team_rotation = DEFAULT_TEAM_ROTATION

    print(f"\n{Emojis.START}Titan Arena AUTO mode (max {max_stages} tiers)...", flush=True)

    # 初回 status 取得（失敗時は initial_rival_id にフォールバック）
    status = _fetch_arena_status(client)
    if status is None:
        print(f"  {Emojis.WARNING}Could not fetch arena status, using initial rival {initial_rival_id}", flush=True)
        # フォールバック: 旧来の1敵1tierモード
        status = {"tier": None, "max_tier": None, "rivals": {initial_rival_id: {"attackScore": 0}}, "_raw": {}}

    stages_cleared = 0
    # 現在の tier から開始、max_stages tier 分を試す
    for tier_attempt in range(1, max_stages + 1):
        # 最新の tier 情報を毎回再取得（前 tier の CompleteTier 後や、敵撃破後の残り確認のため）
        if status is None:
            status = _fetch_arena_status(client)
            if status is None:
                print(f"{Emojis.WARNING}Could not fetch status. Stopping.", flush=True)
                break

        tier = status.get("tier")
        max_tier = status.get("max_tier")
        # tier が取れなければ不明として表示
        tier_label = f"{tier}/{max_tier}" if tier is not None else "unknown"
        alive_initial = _get_alive_rivals_sorted(status)
        print(f"\n{Emojis.STEP}=== Tier {tier_label} | {len(alive_initial)} rival(s) remaining ===", flush=True)
        ti = _tier_int(tier)
        mti = _tier_int(max_tier)
        if ti is not None and mti is not None and ti > mti:
            print(f"{Emojis.FINISH}Tier {tier} exceeds maxTier {max_tier}. Stopping.", flush=True)
            break

        auto_advanced = False
        # この tier 内の全 alive 敵を順に倒す
        while True:
            alive = _get_alive_rivals_sorted(status)
            if not alive:
                print(f"  {Emojis.INFO}Tier {tier_label}: all rivals cleared.", flush=True)
                break

            # 次の敵を選ぶ。initial_rival_id が alive に含まれていればそれを優先（初回のみ）
            rival_id = None
            if initial_rival_id and initial_rival_id in alive:
                rival_id = initial_rival_id
                # 一度使ったらクリア（次からは attackScore 順）
                initial_rival_id = None  # type: ignore
            else:
                rival_id = alive[0]

            print(f"  {Emojis.STEP}Rival {rival_id} ({len(alive)} left in tier {tier_label})", flush=True)
            res = run_titan_arena(
                client,
                rival_id=rival_id,
                team_rotation=team_rotation,
                max_attempts_per_team=max_attempts_per_team,
                account=account,
                battle_sim=battle_sim,
            )
            if not res.win:
                print(f"{Emojis.ERROR}Failed on rival {rival_id} in tier {tier_label}. Aborting auto.", flush=True)
                print(f"\n{Emojis.FINISH}Auto complete: {stages_cleared} tier(s) cleared.", flush=True)
                return stages_cleared

            # 勝利したら status を再取得して残り敵を更新
            new_status = _fetch_arena_status(client)
            if new_status is None:
                # 取得失敗時は楽観的に1体減ったとみなして継続（次ループで再fetch）
                print(f"  {Emojis.WARNING}Could not refresh status after win, assuming 1 rival cleared.", flush=True)
                # rivals から手動で削除して継続
                if rival_id in status.get("rivals", {}):
                    try:
                        del status["rivals"][rival_id]  # type: ignore
                    except Exception:
                        pass
            else:
                # tier が変わっていれば（サーバ側で自動進行した等）それを反映
                if _tier_int(new_status.get("tier")) != _tier_int(tier):
                    print(f"  {Emojis.INFO}Tier changed {tier}->{new_status.get('tier')} after win (server auto-advance?)", flush=True)
                    stages_cleared += 1
                    status = new_status
                    auto_advanced = True
                    break  # この tier は離脱、次 tier のループへ（CompleteTier スキップ）
                status = new_status
            client.sleep()

        if auto_advanced:
            # サーバが自動進行した tier は CompleteTier を呼ばず次 tier へ
            client.sleep()
            continue

        # while を抜けたらこの tier の全敵を倒した（alive 0)
        # 最終 tier なら CompleteTier 不要
        tier = status.get("tier") if status else tier
        max_tier = status.get("max_tier") if status else max_tier
        ti = _tier_int(tier)
        mti = _tier_int(max_tier)
        if ti is not None and mti is not None and ti >= mti:
            # 最終 tier の全敵を倒したので完了
            final_cleared = stages_cleared + 1
            print(
                f"{Emojis.FINISH}Reached final tier {tier} == maxTier {max_tier} and all rivals cleared. Stopping (no further CompleteTier).",
                flush=True,
            )
            print(f"\n{Emojis.FINISH}Auto complete: {final_cleared} tier(s) cleared.", flush=True)
            return final_cleared

        # 次 tier へ進行
        print(f"  {Emojis.STEP}Calling CompleteTier to advance from tier {tier}...", flush=True)
        ct_res = client.call(_build_complete_tier_payload())
        info = _extract_tier_info(ct_res.detail)
        # CompleteTier のレスポンスが取れなければ GetStatus で再試行
        if info is None:
            print(f"  {Emojis.WARNING}CompleteTier response unreadable, fetching status...", flush=True)
            fetched = _fetch_arena_status(client)
            if fetched is None:
                print(f"{Emojis.WARNING}Could not read tier info from CompleteTier and status fetch failed. Stopping.", flush=True)
                break
            info = fetched

        new_tier = info.get("tier")
        new_max = info.get("max_tier")
        print(
            f"  {Emojis.INFO}Stage advanced -> tier={new_tier}/{new_max}, canRaid={info.get('can_raid')}, status={info.get('status')}",
            flush=True,
        )
        stages_cleared += 1

        nti = _tier_int(new_tier)
        nmti = _tier_int(new_max)
        if nti is not None and nmti is not None and nti >= nmti:
            # 最終 tier に到達したら、その tier の敵は次のループで倒す必要がある
            # ただし、既に rivals が空なら完了、alive があれば次のループで処理
            next_status = _fetch_arena_status(client)
            if next_status is None:
                next_status = info
            alive_next = _get_alive_rivals_sorted(next_status)
            if not alive_next:
                print(
                    f"{Emojis.FINISH}Reached final tier {new_tier} == maxTier {new_max} with no rivals. Stopping.",
                    flush=True,
                )
                print(f"\n{Emojis.FINISH}Auto complete: {stages_cleared} tier(s) cleared.", flush=True)
                return stages_cleared
            # alive が残っていれば次のループで最終 tier の敵を倒す
            print(f"  {Emojis.INFO}Final tier {new_tier} has {len(alive_next)} rival(s), will clear them next.", flush=True)
            status = next_status
            # 最終 tier の敵をクリアするためにループ継続（ただし max_stages 残りで）
            continue

        # 通常の次 tier へ
        # CompleteTier の info には次の tier の rivals が含まれるが、確実のため再取得
        status = _fetch_arena_status(client) or info
        client.sleep()

    print(f"\n{Emojis.FINISH}Auto complete: {stages_cleared} tier(s) cleared.", flush=True)
    return stages_cleared


# ----------------------------------------------------------------------------
# レスポンス解析ヘルパ
# ----------------------------------------------------------------------------


def _extract_battle(detail: Any) -> dict[str, Any]:
    """StartBattle / EndBattle レスポンスから battle ブロックを抽出。

    HWClient.call は results[].result を detail に格納するため、実際の構造は:
      detail["response"]["battle"]                  (単一コール)
      detail["body"]["response"]["battle"]          (ident=body)
      item["response"]["battle"]  (list の各要素)
    """
    try:
        if isinstance(detail, dict):
            if "response" in detail and isinstance(detail["response"], dict):
                return detail["response"].get("battle", {})
            if "body" in detail:
                body = detail["body"]
                if isinstance(body, dict) and "response" in body:
                    return body["response"].get("battle", {})
        if isinstance(detail, list):
            for item in detail:
                if isinstance(item, dict) and "response" in item:
                    return item["response"].get("battle", {})
    except Exception:
        pass
    return {}


def _extract_seed(detail: Any) -> str:
    battle = _extract_battle(detail)
    seed = battle.get("seed")
    return str(seed) if seed is not None else ""


def _extract_tier_info(detail: Any) -> Optional[dict[str, Any]]:
    """titanArenaCompleteTier レスポンスからステージ情報を抽出。

    構造: result.response = { tier, maxTier, canRaid, status, rivals{...}, ... }
    """
    resp = _extract_response(detail)
    if isinstance(resp, dict):
        return {
            "tier": resp.get("tier"),
            "max_tier": resp.get("maxTier"),
            "can_raid": resp.get("canRaid"),
            "status": resp.get("status"),
            "rivals": resp.get("rivals", {}) or {},
        }
    return None


def _pick_next_rival(tier_info: dict[str, Any]) -> Optional[str]:
    """CompleteTier が返した rivals から次の対戦相手 ID を選ぶ。

    HWH の挙動に合わせ、attackScore が低い（= 過去に負けていて自分より弱い）
    相手を優先する。また bot (isBot:true) は高パワー固定（1.8M 級）なので、
    実人間（非 bot）を優先する。
    """
    lst = _get_alive_rivals_sorted(tier_info)
    return lst[0] if lst else None


def _is_invalid_battle(detail: Any) -> bool:
    """EndBattle レスポンスが 'Invalid battle' エラーかを判定"""
    try:
        if isinstance(detail, dict):
            resp = detail.get("response")
            if isinstance(resp, dict) and resp.get("error") == "Invalid battle":
                return True
        if isinstance(detail, list):
            for item in detail:
                if isinstance(item, dict) and _is_invalid_battle(item):
                    return True
    except Exception:
        pass
    return False


def _extract_win(detail: Any) -> bool:
    """EndBattle レスポンスから勝敗を抽出"""
    try:
        battle = None
        if isinstance(detail, dict):
            if "response" in detail and isinstance(detail["response"], dict):
                battle = detail["response"].get("battle")
            elif "body" in detail:
                body = detail["body"]
                battle = body.get("response", {}).get("battle") if isinstance(body, dict) else None
        elif isinstance(detail, list):
            for item in detail:
                if isinstance(item, dict) and "response" in item:
                    battle = item["response"].get("battle")
                    break

        if isinstance(battle, dict):
            result = battle.get("result", {})
            if isinstance(result, dict):
                return bool(result.get("win"))
    except Exception:
        pass
    return False


def _print_summary(detail: Any) -> None:
    """EndBattle レスポンスから報酬・スコアを表示"""
    try:
        battle = None
        if isinstance(detail, dict):
            if "response" in detail and isinstance(detail["response"], dict):
                battle = detail["response"].get("battle")
        if isinstance(battle, dict):
            result = battle.get("result", {})
            score_attack = result.get("scoreAttack", "?")
            score_defence = result.get("scoreDefence", "?")
            stars = result.get("stars", "?")
            print(f"    {Emojis.SOUL_STONE}Stars: {stars} | AtkScore: {score_attack} | DefScore: {score_defence}", flush=True)
    except Exception:
        pass
