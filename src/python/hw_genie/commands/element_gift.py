"""エレメントギフト（`heroTitanGift*`）の一括 MAX 化・リセット。

月 1 回ある「所持パワーを競う」イベント用に、
  - `gift up`    : 全ヒーローのギフトを MAX まで活性化する（コインが続くところまで）
  - `gift reset` : イベント後に低 power ヒーローのギフトを降ろしてコインを回収する
する。ギフトのコイン（consumable libId 24）は drop で全額返却されるため、
up → イベント → reset が実質タダで回せる。

状態は DB に持たない。`heroGetAll` の `titanGiftLevel` / `titanCoinsSpent` /
`power` がサーバー側の正であり、手動操作が挟まっても次の計画が自動で現在値に
追随する（`enabled` ゲートや実行時刻の記録が不要）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from hw_genie.core.client import (
    ApiAction,
    Emojis,
    HWAuthError,
    HWClient,
    Messages,
    ResponseStatus,
    _safe_int,
)

logger = logging.getLogger(__name__)

#: `core/client.py` が単一コール／グローバルエラーで HWAuthError に変換する
#: のと同一の名前（batch 内 per-call エラーは変換されないためここで拾う）。
AUTH_ERROR_NAMES = frozenset({"auth", "InvalidSession"})

#: ギフトの MAX レベル（実測: level 130 のヒーローは全員 30 で頭打ち）。
GIFT_MAX_LEVEL = 30
#: 0 → MAX の累計コスト。実測で MAX ヒーロー 33 体すべてが同一値だったため
#: 定数化し、残コストは ``GIFT_FULL_COST - titanCoinsSpent`` で見積もる。
GIFT_FULL_COST = 65150
#: ギフトのコイン（consumable libId）。
GIFT_COIN_LIB_ID = 24
#: `gift reset` の既定の power 上限（この値以下のヒーローを回収対象にする）。
DEFAULT_MAX_POWER = 100000

class GiftReadError(Exception):
    """`heroGetAll` / `inventoryGet` の読み取り・パース失敗（認証エラーは除く）。"""

    def __init__(self, message: str, error_name: str | None = None):
        super().__init__(message)
        self.error_name = error_name


@dataclass(frozen=True)
class GiftHero:
    """`heroGetAll` 1 件から抽出した、ギフト判定に必要な項目だけ。"""

    hero_id: int
    power: int
    level: int
    star: int
    gift_level: int
    coins_spent: int
    #: `power` が実際に読めたか（欠損・非数値なら False）。`_safe_int` の 0 への
    #: 丸めと区別するため、読み取り成否をここで保持する。
    power_known: bool = True

    @property
    def is_max(self) -> bool:
        return self.gift_level >= GIFT_MAX_LEVEL

    @property
    def levels_needed(self) -> int:
        return max(0, GIFT_MAX_LEVEL - self.gift_level)

    @property
    def remaining_cost(self) -> int:
        """MAX までに必要なコインの見積もり（消費済み分を差し引く）。"""
        return max(0, GIFT_FULL_COST - self.coins_spent)

    @property
    def refundable(self) -> int:
        """drop で返ってくるコインの見積もり（消費済み分がそのまま返る）。"""
        return self.coins_spent


@dataclass
class GiftUpAction:
    """`gift up` の 1 体分（1 ヒーロー = 1 リクエストにレベル分だけ calls を並べる）。"""

    hero: GiftHero
    levels: int
    cost: int


@dataclass
class GiftRunResult:
    """1 アカウント 1 実行の結果。`multi` のサマリー表と run_logs に載せる。"""

    account: str
    mode: str  # "up" / "reset"
    changed: int = 0  # up: MAX 化できた体数 / reset: drop した体数
    levels: int = 0  # up で実際に上げたレベル合計
    coins: int = 0  # up: 実消費（実行前後の残高差） / reset: 実返却合計
    balance: int | None = None  # 実行後のコイン残高
    blocked: int = 0  # コイン不足で対象外になった体数
    failed: int = 0  # RPC 失敗した体数
    dry_run: bool = False
    error: str | None = None
    details: list[str] = field(default_factory=list)

    @property
    def is_failure(self) -> bool:
        return bool(self.error) or self.failed > 0


# ---------------------------------------------------------------- 読み取り


def _read_power(raw: Any) -> tuple[int, bool]:
    """`heroGetAll` の `power` を (値, 読めたか) に分離する。

    `_safe_int` は欠損・非数値を 0 に丸めてしまうため、`gift reset` の閾値判定が
    「読めない＝power 0＝主力でも対象にする」に化けないよう、成否を別途返す
    （モジュール方針「読めなければ対象にしない＋警告」）。
    """
    if isinstance(raw, bool) or raw is None:
        return 0, False
    if isinstance(raw, (int, float)):
        return _safe_int(raw), True
    if isinstance(raw, str):
        try:
            return int(float(raw)), True
        except (TypeError, ValueError, OverflowError):
            return 0, False
    return 0, False


def parse_heroes(payload: Any) -> list[GiftHero]:
    """`heroGetAll` の `response`（ヒーロー ID 辞書）を `GiftHero` 一覧にする。

    dict でない項目・`type` が明示的に `hero` 以外（ペット等）は対象外。
    `titanCoinsSpent` は未活性化ヒーローでは `{"consumable": {}}` のため欠損扱い 0。
    `consumable` が辞書でない形（`null` 等）も 0 として扱い、クラッシュせず続行する
    （モジュールの方針「読めれば続行／読めなければ GiftReadError」の「読めれば」側で、
    そのヒーローは消費済み 0＝MAX まで 65,150 として計画に載る）。
    `power` が読めない（欠損・非数値）場合は `power_known=False` を持つ
    （`gift reset` の閾値判定で黙って 0 として扱わず、対象から除外＋警告）。
    """
    if not isinstance(payload, dict):
        return []
    heroes: list[GiftHero] = []
    for raw_id, data in payload.items():
        if not isinstance(data, dict):
            continue
        hero_type = data.get("type")
        if hero_type is not None and hero_type != "hero":
            continue
        hero_id = _safe_int(data.get("id", raw_id), default=-1)
        if hero_id < 0:
            continue
        spent = data.get("titanCoinsSpent")
        coins = 0
        if isinstance(spent, dict):
            consumable = spent.get("consumable")
            if isinstance(consumable, dict):
                coins = _safe_int(consumable.get(str(GIFT_COIN_LIB_ID)))
        power, power_known = _read_power(data.get("power"))
        heroes.append(
            GiftHero(
                hero_id=hero_id,
                power=power,
                power_known=power_known,
                level=_safe_int(data.get("level")),
                star=_safe_int(data.get("star")),
                gift_level=_safe_int(data.get("titanGiftLevel")),
                coins_spent=coins,
            )
        )
    heroes.sort(key=lambda h: (-h.power, h.hero_id))
    return heroes


def parse_coin_balance(payload: Any) -> int:
    """`inventoryGet` の `response.consumable` からギフトコイン残高を取り出す。"""
    if not isinstance(payload, dict):
        return 0
    consumable = payload.get("consumable")
    if not isinstance(consumable, dict):
        return 0
    return _safe_int(consumable.get(str(GIFT_COIN_LIB_ID)))


def _unwrap(detail: Any, ident: str | None) -> dict[str, Any]:
    """`HWClient.call` の `detail` から `response` dict を取り出す。

    単一コールでは `detail = {"response": ...}`、複数コールでは
    `detail = {ident: {"result": {"response": ...}}}` と形が変わるため、
    両方をここで平坦化する。

    `response` を dict として読めない場合は必ず GiftReadError を投げる。
    wrapper 自体を返すフォールバックを持つと、キー欠損で 0 を返す
    `parse_coin_balance` と組み合わさって「API の形変更」が「残高 0 ＝
    コイン不足」に化けて無音で握りつぶされる（`gift up` が exit 0 になる）。
    形変更は失敗として報告する。
    """
    if not isinstance(detail, dict):
        raise GiftReadError(f"unexpected response format for {ident or 'call'}")
    if ident is not None:
        entry = detail.get(ident)
        if not isinstance(entry, dict):
            raise GiftReadError(f"missing {ident} in batched response")
        inner = entry.get("result")
        if "error" in entry and entry.get("error") is not None:
            error = entry["error"]
            name = error.get("name") if isinstance(error, dict) else str(error)
            raise GiftReadError(f"{ident} failed ({name})", error_name=str(name))
    else:
        inner = detail
    if not isinstance(inner, dict):
        raise GiftReadError(f"no result object for {ident or 'call'}")
    response = inner.get("response")
    if not isinstance(response, dict):
        raise GiftReadError(f"no response object for {ident or 'call'}")
    return response


def fetch_state(client: HWClient) -> tuple[list[GiftHero], int]:
    """`heroGetAll` と `inventoryGet` を 1 リクエストで読み、(ヒーロー, コイン残高) を返す。

    Raises:
        HWAuthError: 認証エラー（呼び出し側で中断扱いにするため再送出）
        GiftReadError: 通信・API エラー、または想定外のレスポンス形式
    """
    payload = {
        "calls": [
            {
                "name": ApiAction.HERO_GET_ALL,
                "args": {},
                "context": {"actionTs": 0},
                "ident": "heroes",
            },
            {"name": ApiAction.INVENTORY_GET, "args": {}, "ident": "inventory"},
        ]
    }
    res = client.call(payload)
    if res.status != ResponseStatus.SUCCESS:
        raise GiftReadError(
            f"gift state read failed ({res.error_name or res.status.value})",
            error_name=res.error_name,
        )
    heroes = parse_heroes(_unwrap(res.detail, "heroes"))
    balance = parse_coin_balance(_unwrap(res.detail, "inventory"))
    if not heroes:
        raise GiftReadError("heroGetAll returned no heroes")
    return heroes, balance


# ---------------------------------------------------------------- 計画


def plan_up(
    heroes: list[GiftHero], balance: int, limit: int | None = None
) -> tuple[list[GiftUpAction], int, int]:
    """power 降順に 1 体ずつ MAX 化する計画を作る。

    見積残高が次の 1 体を MAX にできなくなった時点で打ち切る（全員に薄く
    分配しない方針）。打ち切り地点以降の体数は ``blocked`` で返す。

    Returns:
        ``(actions, blocked, beyond_limit)``
    """
    eligible = [h for h in heroes if not h.is_max]
    eligible.sort(key=lambda h: (-h.power, h.hero_id))
    actions: list[GiftUpAction] = []
    running = balance
    blocked = 0
    beyond_limit = 0
    for index, hero in enumerate(eligible):
        if limit is not None and len(actions) >= limit:
            beyond_limit = len(eligible) - index
            break
        cost = hero.remaining_cost
        if cost > running:
            blocked = len(eligible) - index
            break
        actions.append(GiftUpAction(hero=hero, levels=hero.levels_needed, cost=cost))
        running -= cost
    return actions, blocked, beyond_limit


def plan_reset(
    heroes: list[GiftHero], max_power: int = DEFAULT_MAX_POWER, limit: int | None = None
) -> list[GiftHero]:
    """ギフトが載っていて power が `max_power` 以下のヒーローを drop 対象にする。

    power は実行時点の `heroGetAll` 値（ギフト込み）で判定する。gift_level が 0
    のものは降ろすものがないので対象外。`power` が読めない体（`power_known=False`）は
    閾値を判定できないため対象外にする（`_safe_int` の 0 丸めで「主力でも閾値以下」に
    化してイベント当日にパワーが飛ぶ事故を防ぐ）。除外した gifted 体は
    :func:`unknown_power_gifted_heroes` で取り出し、実行時に警告行を出す。
    """
    targets = [
        h for h in heroes if h.gift_level > 0 and h.power_known and h.power <= max_power
    ]
    targets.sort(key=lambda h: (-h.power, h.hero_id))
    if limit is not None:
        return targets[:limit]
    return targets


def unknown_power_gifted_heroes(heroes: list[GiftHero]) -> list[GiftHero]:
    """power が読めなくて reset 対象から黙って除外された gifted 体を返す。"""
    return [h for h in heroes if h.gift_level > 0 and not h.power_known]


# ---------------------------------------------------------------- 送信


def gift_up_idents(levels: int) -> list[str]:
    """送信する calls の ident 列（送信順そのまま）。

    `HWClient.call` は複数コールを ident 辞書で返すため、応答を「最初のエラー」
    として読むには辞書順ではなくこの送信順で走査しなければならない
    （`sorted()` だと `body_10` が `body_2` より先に来る）。
    """
    return ["body" if i == 0 else f"body_{i}" for i in range(max(0, levels))]


def gift_up_payload(hero_id: int, levels: int) -> dict[str, Any]:
    """1 体分のレベルアップ calls（ident は `body`, `body_1`, ... の実観測形式）。"""
    return {
        "calls": [
            {
                "name": ApiAction.HERO_TITAN_GIFT_LEVEL_UP,
                "args": {"heroId": hero_id},
                "context": {"actionTs": 0},
                "ident": ident,
            }
            for ident in gift_up_idents(levels)
        ]
    }


def _call_results(res: Any, expected: int) -> tuple[int, str | None]:
    """バッチ応答から成功コール数を数え、送信順で最初に当たったエラー名を返す。

    `HWClient.call` は単一コールだと `detail` が結果そのもの、複数コールだと
    ident 辞書になるため、`expected` が 1 の場合だけ前者として扱う。
    走査順は送信した calls の順序（`gift_up_idents`）。応答に無い ident や
    想定外の ident が混ざっても取りこぼさないよう、送信順で先に走査したあと
    残ったキーを辞書の挿入順で補う。
    """
    detail = res.detail if isinstance(res.detail, dict) else {}
    if expected == 1:
        if "error" in detail and detail.get("error") is not None:
            return 0, _error_name(detail["error"])
        if res.status != ResponseStatus.SUCCESS:
            return 0, res.error_name or res.status.value
        return 1, None
    idents = [ident for ident in gift_up_idents(expected) if ident in detail]
    idents += [key for key in detail if key not in idents]
    succeeded = 0
    first_error: str | None = None
    for ident in idents:
        entry = detail.get(ident)
        if not isinstance(entry, dict):
            continue
        error = entry.get("error")
        if error is not None:
            if first_error is None:
                first_error = _error_name(error)
            continue
        if entry.get("result") is not None:
            succeeded += 1
    if first_error is None and res.status != ResponseStatus.SUCCESS:
        first_error = res.error_name or res.status.value
    return succeeded, first_error


def _error_name(error: Any) -> str:
    if isinstance(error, dict):
        return str(error.get("name") or error.get("message") or "unknown")
    return str(error)


def _drop_refund(res: Any) -> int:
    """`heroTitanGiftDrop` の応答から実返却コインを読む（`response.consumable.24`）。"""
    try:
        return parse_coin_balance(_unwrap(res.detail, None))
    except GiftReadError:
        return 0


def _confirm(text: str, confirm: bool) -> bool:
    """y/N で本実行を確認する。`confirm=True`（`--yes` / multi）は無条件に通す。

    標準入力が閉じている（EOFError）だけでなく、capture 済み・tty でない
    （OSError）環境でも実行を続けることはない。Docker / cron からの呼び出しは
    `--yes` を付けるのが正なので、入力が読めない場合は確認できないものとして
    中断する。
    """
    if confirm:
        return True
    try:
        answer = input(f"   {Emojis.WARNING}{text}? [y/N] ")
    except (EOFError, OSError):
        print(
            f"   {Emojis.ERROR}No interactive input available; re-run with --yes to proceed unattended."
        )
        return False
    return answer.strip().lower() in ("y", "yes")


# ---------------------------------------------------------------- 表示


def _hero_row(hero: GiftHero) -> str:
    flag = "MAX" if hero.is_max else f"{hero.gift_level}/{GIFT_MAX_LEVEL}"
    return (
        f"  hero {hero.hero_id:<4} power={hero.power:<9,} star={hero.star} "
        f"gift={flag:<10} spent={hero.coins_spent:,}"
    )


def print_status(client: HWClient, account: str, raw: bool = False) -> None:
    """現況（ヒーローごとのギフトレベル・コイン残高・up/reset できる体数）を表示する。"""
    heroes, balance = fetch_state(client)
    if raw:
        print(
            json.dumps(
                {
                    "account": account,
                    "coin_balance": balance,
                    "heroes": [
                        {
                            "heroId": h.hero_id,
                            "power": h.power,
                            "level": h.level,
                            "star": h.star,
                            "titanGiftLevel": h.gift_level,
                            "coinsSpent": h.coins_spent,
                        }
                        for h in heroes
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    maxed = [h for h in heroes if h.is_max]
    unmaxed = [h for h in heroes if not h.is_max]
    actions, blocked, _ = plan_up(heroes, balance)
    needed = sum(h.remaining_cost for h in unmaxed)
    reset_targets = plan_reset(heroes)
    print(f"\n=== Element gift status: {account} ===")
    print(
        f"  coins (libId {GIFT_COIN_LIB_ID}): {balance:,} | "
        f"heroes: {len(heroes)} (MAX {len(maxed)} / not MAX {len(unmaxed)})"
    )
    print(
        f"  up    : {len(actions)} hero(es) affordable now "
        f"(cost {sum(a.cost for a in actions):,} of {needed:,} needed for all; "
        f"{blocked} blocked by coins)"
    )
    print(
        f"  reset : {len(reset_targets)} hero(es) with gift at power <= "
        f"{DEFAULT_MAX_POWER:,} (refund {sum(h.refundable for h in reset_targets):,})"
    )
    for hero in heroes:
        print(_hero_row(hero))


# ---------------------------------------------------------------- 実行


def run_gift_up(
    client: HWClient,
    account_alias: str | None = None,
    dry_run: bool = False,
    confirm: bool = False,
    limit: int | None = None,
) -> GiftRunResult:
    """power 降順で 1 体ずつギフトを MAX まで上げる（コインが続くところまで）。

    - 1 体 = レベル分だけ calls を並べた 1 リクエストで送る。
    - 計画は読んだ直後の実残高で立てるので、打ち切り地点は原則正しい。実コストが
      見積（MAX まで = 65,150 − 消費済み）より安い場合は、`gift status` を見てから
      再実行すれば追加の体を拾える。
    - バッチ内エラーは理由で分岐する（`quests.FALLBACK_ERROR_NAMES` の資源枯渇名
      ＝コイン枯れは同じ残高では次の体も失敗するため打ち切り、それ以外の
      1 体個別エラー（`notAvailable` 等）は drop と同じ契約で「その体だけ失敗」と
      して記録し次の体へ継続する）。
    - バッチ内の per-call エラーが `auth` / `InvalidSession` の場合は HWAuthError
      として扱う（`HWClient.call` は単一コール・グローバルエラーしか自動変換
      しないため、ここで合流させる）。
    - 書き込みの途中で `HWAuthError`（認証切れ）が出たら、そこまでの部分結果で
      最終レポートを出してから例外を再送出する（`multi` はそれをアカウント単位の
      失敗として記録する）。
    - 終了時に `heroGetAll` + `inventoryGet` を 1 回読み直し、消費コイン・MAX 体数・
      合計 power を実測値で報告する。
    """
    account = account_alias or "<account>"
    # コイン枯れ（資源不足）を示す既存エラー名集合を流用する（新規集合を作らない）。
    from hw_genie.commands.quests import FALLBACK_ERROR_NAMES as COIN_ERROR_NAMES

    result = GiftRunResult(account=account, mode="up", dry_run=dry_run)
    try:
        heroes, balance = fetch_state(client)
    except GiftReadError as exc:
        result.error = str(exc)
        print(f"{Emojis.ERROR}[{account}] gift up: state read failed: {exc}")
        return result

    actions, blocked, beyond_limit = plan_up(heroes, balance, limit)
    print(f"\n{Emojis.START}[{account}] Element gift up "
          f"(coins {balance:,}, MAX {GIFT_MAX_LEVEL} costs {GIFT_FULL_COST:,}/hero)")
    if not actions:
        reason = "no hero below MAX" if not [h for h in heroes if not h.is_max] else "not enough coins"
        print(f"  {Emojis.INFO}Nothing to run: {reason}"
              + (f" ({beyond_limit} beyond --limit)" if beyond_limit else ""))
        result.balance = balance
        result.blocked = blocked
        return result

    total_cost = sum(a.cost for a in actions)
    print(f"  plan: {len(actions)} hero(es), {sum(a.levels for a in actions)} level(s), "
          f"{total_cost:,} coins"
          + (f" | {blocked} blocked by coins" if blocked else "")
          + (f" | {beyond_limit} beyond --limit" if beyond_limit else ""))
    for action in actions:
        hero = action.hero
        print(
            f"    hero {hero.hero_id:<4} power={hero.power:<9,} "
            f"gift {hero.gift_level} -> {GIFT_MAX_LEVEL} ({action.levels} levels, ~{action.cost:,})"
        )
    result.blocked = blocked
    if dry_run:
        # multi のサマリ表に計画を反映する（consumable / seers-game の dry-run と同じ慣習）。
        # is_failure は error / failed しか見ないので、計画値を載せても ok 判定は変わらない。
        result.changed = len(actions)
        result.coins = total_cost
        result.balance = balance
        print(f"  {Emojis.INFO}dry-run: nothing executed.")
        return result

    if not _confirm(f"Spend up to {total_cost:,} gift coins to max {len(actions)} hero(es)", confirm):
        print(f"  {Emojis.INFO}Cancelled by user.")
        result.error = "skipped by user"
        return result

    initial_balance = balance
    spent_estimate = 0
    auth_error: HWAuthError | None = None
    for index, action in enumerate(actions, start=1):
        hero = action.hero
        try:
            res = client.call(gift_up_payload(hero.hero_id, action.levels))
        except HWAuthError as exc:
            # 認証切れでも「何体 MAX になり、いまコインがいくらか」を捨てない。
            # 部分結果つきで最終レポートを出してから raise し、multi / cmd_gift の
            # err 経路（アカウント単位の失敗）に渡す。
            auth_error = exc
            result.failed += 1
            result.details.append(f"hero {hero.hero_id}: auth error")
            result.error = f"hero {hero.hero_id}: auth error"
            print(f"  {Emojis.ERROR}[{index}/{len(actions)}] hero {hero.hero_id}: "
                  f"auth error; stopping.")
            break
        succeeded, error_name = _call_results(res, action.levels)
        client.sleep()
        if error_name is None and succeeded == action.levels:
            result.changed += 1
            result.levels += succeeded
            spent_estimate += action.cost
            print(f"  {Emojis.SUCCESS}[{index}/{len(actions)}] hero {hero.hero_id}: "
                  f"+{succeeded} levels (gift MAX)")
            continue

        if error_name in AUTH_ERROR_NAMES:
            # batch 内の per-call 認証エラーは `HWClient.call` が HWAuthError に
            # 変換しない（単一コール・グローバルのみ変換）ため、ここで `except
            # HWAuthError` と同じ部分レポート経路に合流させる。
            auth_error = HWAuthError(Messages.AUTH_ERROR)
            result.failed += 1
            result.levels += succeeded
            result.details.append(f"hero {hero.hero_id}: auth error")
            result.error = f"hero {hero.hero_id}: auth error"
            print(f"  {Emojis.ERROR}[{index}/{len(actions)}] hero {hero.hero_id}: "
                  f"auth error; stopping.")
            break

        reason = error_name or "error"
        result.failed += 1
        result.levels += succeeded
        result.details.append(f"hero {hero.hero_id} partial: {succeeded}/{action.levels} ({reason})")
        if error_name in COIN_ERROR_NAMES:
            # コイン枯れ: 同じ残高では次の体も失敗するため打ち切る。
            print(f"  {Emojis.ERROR}[{index}/{len(actions)}] hero {hero.hero_id}: "
                  f"{succeeded}/{action.levels} level(s) applied ({reason}); stopping.")
            result.error = f"hero {hero.hero_id}: {reason}"
            break
        # コインと無関係な 1 体個別エラー（notAvailable 等）: この体だけを失敗と
        # して記録し、次の体は買える可能性があるので継続する（drop と同じ契約）。
        print(f"  {Emojis.ERROR}[{index}/{len(actions)}] hero {hero.hero_id}: "
              f"{succeeded}/{action.levels} level(s) applied ({reason}); continuing.")

    # 見積ではなく実測で報告する（heroGetAll + inventoryGet を 1 リクエストで再読）。
    # 認証切れのときはこの再読も失敗するため、レポート自体は見積で出す。
    final_heroes: list[GiftHero] | None = None
    try:
        final_heroes, balance = fetch_state(client)
        result.coins = max(0, initial_balance - balance)
    except (GiftReadError, HWAuthError) as exc:
        logger.warning("gift up: final state read failed: %s", exc)
        balance = initial_balance - spent_estimate
        result.coins = spent_estimate
    result.balance = balance
    maxed = len([h for h in final_heroes if h.is_max]) if final_heroes else None
    maxed_before = len([h for h in heroes if h.is_max])
    power_now = sum(h.power for h in final_heroes) if final_heroes else None
    power_before = sum(h.power for h in heroes)
    print(
        f"  {Emojis.FINISH}[{account}] up done: {result.changed} hero(es) maxed "
        f"(MAX {maxed_before} -> {maxed if maxed is not None else '?'} of {len(heroes)}), "
        f"{result.levels} level(s), {result.coins:,} coins spent, balance {balance:,}"
        + (
            f", total power {power_before:,} -> {power_now:,}"
            if power_now is not None
            else ""
        )
        + (f", {result.failed} failed" if result.failed else "")
    )
    if result.error:
        print(f"  {Emojis.WARNING}Stopped early: {result.error}. Re-run `gift status` to see the rest.")
    if auth_error is not None:
        raise auth_error
    return result


def gift_drop_payload(hero_id: int) -> dict[str, Any]:
    """1 体のギフトを降ろす calls（ゲームクライアントは前に popup stash を挟むが不要）。"""
    return {
        "calls": [
            {
                "name": ApiAction.HERO_TITAN_GIFT_DROP,
                "args": {"heroId": hero_id},
                "context": {"actionTs": 0},
                "ident": "body",
            }
        ]
    }


def run_gift_reset(
    client: HWClient,
    account_alias: str | None = None,
    dry_run: bool = False,
    confirm: bool = False,
    max_power: int = DEFAULT_MAX_POWER,
    limit: int | None = None,
) -> GiftRunResult:
    """power が `max_power` 以下のヒーローのギフトを drop してコインを回収する。

    drop は消費コインが全額返る（実測: `titanCoinsSpent` と同額が返却）ため、
    1 体が失敗しても他を続行して問題ない（資源を消費しない）。返却額は応答の
    `response.consumable.24` を実測値として合計する。

    `power` が読めない gifted 体は閾値判定できないので対象外にし、警告行で
    黙って除外された体数を明示する（API 形変更時に主力のギフトが落ちる事故防止）。

    書き込みの途中で `HWAuthError` が出たら、drop 済み・回収済みの部分結果で
    最終レポートを出してから例外を再送出する（`up` と同じ契約）。
    """
    account = account_alias or "<account>"
    result = GiftRunResult(account=account, mode="reset", dry_run=dry_run)
    try:
        heroes, balance = fetch_state(client)
    except GiftReadError as exc:
        result.error = str(exc)
        print(f"{Emojis.ERROR}[{account}] gift reset: state read failed: {exc}")
        return result

    targets = plan_reset(heroes, max_power, limit)
    skipped_power = unknown_power_gifted_heroes(heroes)
    print(
        f"\n{Emojis.START}[{account}] Element gift reset "
        f"(coins {balance:,}, target: gift on and power <= {max_power:,})"
    )
    if skipped_power:
        # power が読めない gifted 体は閾値を判定できないので対象外（黙って除外せず警告）。
        ids = ", ".join(str(h.hero_id) for h in skipped_power)
        print(
            f"  {Emojis.WARNING}{len(skipped_power)} gifted hero(es) skipped: "
            f"power unreadable ({ids}). Verify `gift status` before resetting."
        )
    if not targets:
        print(f"  {Emojis.INFO}Nothing to drop "
              f"(no hero with gift at power <= {max_power:,}"
              + (f" within --limit {limit}" if limit else "")
              + ").")
        result.balance = balance
        return result

    expected = sum(h.refundable for h in targets)
    print(f"  plan: {len(targets)} hero(es), refund ~{expected:,} coins")
    for hero in targets:
        print(
            f"    hero {hero.hero_id:<4} power={hero.power:<9,} "
            f"gift {hero.gift_level}/{GIFT_MAX_LEVEL} refund ~{hero.refundable:,}"
        )
    if dry_run:
        # multi のサマリ表に計画を反映する（`up` の dry-run と同じ。ok 判定は
        # error / failed のみなので変わらない）。
        result.changed = len(targets)
        result.coins = expected
        result.balance = balance
        print(f"  {Emojis.INFO}dry-run: nothing executed.")
        return result

    if not _confirm(f"Drop the gift of {len(targets)} hero(es) to recover ~{expected:,} coins", confirm):
        print(f"  {Emojis.INFO}Cancelled by user.")
        result.error = "skipped by user"
        return result

    auth_error: HWAuthError | None = None
    for index, hero in enumerate(targets, start=1):
        try:
            res = client.call(gift_drop_payload(hero.hero_id))
        except HWAuthError as exc:
            # up と同じく、認証切れでも drop 済みの体数と回収コインを報告してから上げる。
            auth_error = exc
            result.failed += 1
            result.details.append(f"hero {hero.hero_id}: auth error")
            print(f"  {Emojis.ERROR}[{index}/{len(targets)}] hero {hero.hero_id}: drop failed (auth error)")
            break
        refunded = _drop_refund(res) if res.status == ResponseStatus.SUCCESS else 0
        client.sleep()
        if res.status != ResponseStatus.SUCCESS:
            result.failed += 1
            error_name = res.error_name or res.status.value
            result.details.append(f"hero {hero.hero_id}: {error_name}")
            print(f"  {Emojis.ERROR}[{index}/{len(targets)}] hero {hero.hero_id}: drop failed ({error_name})")
            continue
        result.changed += 1
        result.coins += refunded
        note = "" if refunded or not hero.refundable else " (no refund reported)"
        print(f"  {Emojis.SUCCESS}[{index}/{len(targets)}] hero {hero.hero_id}: "
              f"gift dropped, +{refunded:,} coins{note}")

    # 返却額は応答の実測値を合計しているので、ここでの再読は残高と残ったギフトの
    # 確認だけ（他の収入で残高が動いても result.coins は汚れない）。
    initial_balance = balance
    final_heroes: list[GiftHero] | None = None
    balance = initial_balance + result.coins
    try:
        final_heroes, balance = fetch_state(client)
    except (GiftReadError, HWAuthError) as exc:
        logger.warning("gift reset: final state read failed: %s", exc)
    result.balance = balance
    gifted = len([h for h in final_heroes if h.gift_level > 0]) if final_heroes else None
    gifted_before = len([h for h in heroes if h.gift_level > 0])
    power_now = sum(h.power for h in final_heroes) if final_heroes else None
    power_before = sum(h.power for h in heroes)
    print(
        f"  {Emojis.FINISH}[{account}] reset done: {result.changed} hero(es) dropped, "
        f"{result.coins:,} coins recovered (balance {balance:,}), "
        f"gift on {gifted_before} -> {gifted if gifted is not None else '?'} of {len(heroes)}"
        + (f", total power {power_before:,} -> {power_now:,}" if power_now is not None else "")
        + (f", {result.failed} failed" if result.failed else "")
    )
    if auth_error is not None:
        raise auth_error
    return result
