"""Asgard（ギルドレイド）ショップの自動購入。

``clanRaid_getInfo`` の ``response.shop``（slotId → 商品）から Valor Emblem
（コイン ID 30）支払いの商品を読み、Osh / Maestro 週それぞれの購入ルールに
従って ``clanRaid_shopBuy`` で購入する。ゴールドバフ（slot 1〜5）は週に応じて
購入する（デフォルト: Osh 週は購入しない、Maestro 週は購入する。
``gold_buffs=True`` / ``False`` で明示的に上書き）。

- **Osh 週**: ラインナップは固定（slot 1〜5 はゴールドバフ、slot 6〜21 が
  Valor Emblem 商品で buffId 61〜81）。``shop`` の buffId 集合がシグネチャ
  （61〜81）の部分集合（非空）の場合に Osh 週と判定し、固定優先度
  （``OSH_PRIORITY``）に従って購入する。優先度 1 → 2 → 3 の slot を順に
  購入し、残りの未購入商品は価格昇順（同額は slot 昇順）で購入する。
- **Maestro 週**: ラインナップは buffId 112〜133 の範囲（slot 1〜5 は
  ゴールドバフ、slot 6〜21 が Valor Emblem 商品）。slot → 効果の対応は週ごとに
  変わる場合があるため、``MAESTRO_PRIORITY``（slot → 順位の固定表）を確認済み
  ラインナップ（slot→バフ対応・順位・価格は下表の実測値）に基づいて定義し、
  組み合わせ最適化（``select_maestro_plan``）で購入プランを選定する。順位 1〜6
  が S、7〜9 が A、10〜11 が B、それ以外（C）は購入対象外。
  - 残高（Valor Emblem）を上限として、S → A → B の優先度を崩さず、同一優先度
    では高順位を優先し、コイン内で最も優先度の高いバフ構成になる組み合わせを
    購入する。優先度は S クラス数を最優先し（S クラス 1 個の確保は A/B クラスの
    複数購入より優先）、次に同一クラス内の順位合計、最後に合計コストが小さい
    （= 残コインを多くする）方を選ぶ。
- **その他の週**: 判定不能（ラインナップ不明・空 shop）の場合は購入対象
  なしとしてスキップし正常完了する（購入は発生しないので実害なし）。
- **ゴールドバフ**: ``gold_buffs=None``（デフォルト）のとき週依存で購入する
  （Osh 週は購入しない、Maestro 週は購入する）。``gold_buffs=True`` で常に
  購入、``False`` で常にスキップ。購入時は slot 1〜5 のゴールドバフ
  （100 万ゴールド、buyLimit 5）を残り購入回数分購入する。購入前に
  ``fetch_player_status`` で最新のゴールド残高を取得し、不足時はスキップする
  （実際の購入失敗 NotEnough 時もスキップの安全策併用）。
- **残高**: ``response.coins`` の Valor Emblem 残高を追跡し、残高不足の
  商品は購入しない。さらに実際の購入失敗（NotEnough）が起きた場合も
  以降の購入をスキップする（両方併用の安全策）。
- **dry_run**: 実行計画（購入順・合計コスト）の表示のみ行い、購入はしない。

認証エラー（HWAuthError）は握りつぶさず再送出する（上位で共通処理）。
"""

import itertools
import logging
from dataclasses import dataclass
from typing import Any

from hw_genie.core.client import ApiAction, Emojis, ErrorName, HWAuthError, HWClient, ResponseStatus

logger = logging.getLogger(__name__)

# Osh 週（Realm Traveler）の固定優先度。キーが優先レベル（小さいほど先）、
# 値が購入対象 slot のリスト（リスト内の順序も保持される）。
OSH_PRIORITY: dict[int, list[int]] = {
    1: [8, 17, 20, 12, 13, 19],
    2: [6, 10, 21, 18],
    3: [15, 16, 11],
}

# Osh のラインナップシグネチャ（slot 1〜5 のゴールドバフも含む全 buffId）。
# 判定は部分集合（非空）で行う: 買い切った slot が省略されたり、将来
# ラインナップが追加された場合でも Osh 週として扱えるようにする。
OSH_BUFF_IDS: frozenset[int] = frozenset(range(61, 82))

# Maestro 週（Phantom Orchestra）のラインナップシグネチャ（buffId 112〜133）。
# Osh と同様に部分集合（非空）で判定する。
MAESTRO_BUFF_IDS: frozenset[int] = frozenset(range(112, 134))

# Maestro 週の購入優先度（slot → 順位）。順位 1〜6 が S、7〜9 が A、
# 10〜11 が B。それ以外（C）は購入対象外（後日検討）。
# ラインナップ（slot→バフの対応）は週ごとに変わる場合があるため、この表は
# 確認済みラインナップ（2026-08 実測）に基づく固定表。週が変わった際は更新すること。
# 実測の slot → バフ名（価格, buffId）:
#   S: 11 Unbridled Energy (100, 118) / 15 At the Speed of Light (50, 125) /
#      9 Pillar of Confidence (50, 116) / 7 Effective Tactics (150, 114) /
#      17 Secret Weapon (150, 128) / 16 Strength in Perseverance (150, 127)
#   A: 14 At the Limit (50, 122) / 12 Perfect Storm (100, 119) /
#      10 Charmer's Skill (100, 117)
#   B: 19 Through a Prism (50, 133) / 8 The Tireless (50, 115)
#   C: 6 (150, 112) / 13 (100, 120) / 18 (50, 121) / 21 (100, 132)（バフ名不明）
#   ゴールドバフ（slot 1〜5）: 100 万ゴールド、buffId 113 / 123 / 126 / 129 / 130
MAESTRO_PRIORITY: dict[int, int] = {
    11: 1, 15: 2, 9: 3, 7: 4, 17: 5, 16: 6,  # S
    14: 7, 12: 8, 10: 9,  # A
    19: 10, 8: 11,  # B
}

# ゴールドバフ（slot 1〜5）の価格は API レスポンスの cost.gold からパースする
# （100 万ゴールドが通常だが、個別価格に追従する）。

# Valor Emblem のコイン ID（cost["coin"] のキー）。
VALOR_COIN_ID = 30


class AsgardShopReadError(Exception):
    """clanRaid_getInfo の取得・パースが失敗したことを表す（認証エラーは HWAuthError のまま）。"""


@dataclass
class AsgardItem:
    """購入候補の 1 slot 分の情報。"""

    slot_id: int
    buff_id: int
    buff_value: int
    price: int

    @property
    def label(self) -> str:
        return f"[Realm Traveler] Slot:{self.slot_id} -> buff {self.buff_id} (x{self.buff_value}, {self.price} Valor Emblems)"


@dataclass
class AsgardResult:
    """購入（またはスキップ）1 件の実行結果。"""

    action: str
    status: ResponseStatus
    error: str | None = None


@dataclass
class AsgardRunResult:
    """1 アカウント分の実行結果サマリ。"""

    coins: int
    spent: int
    remaining: int
    bought: int
    skipped: bool
    items: list[AsgardResult]
    # ゴールドバフ購入を実行した場合は実行順（ゴールドバフ → Valor 商品）で並ぶ。
    # 在庫取得失敗など、購入処理自体を実行できなかった場合の理由（成功時は None）。
    error: str | None = None
    # ゴールドバフ（slot 1〜5）の購入数・消費ゴールド（購入対象外・残高不足時は 0）。
    gold_bought: int = 0
    gold_spent: int = 0

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.items if item.status == ResponseStatus.ERROR)


def fetch_clan_raid_shop(client: HWClient) -> tuple[dict[str, Any], int]:
    """clanRaid_getInfo を呼び、``(shop, coins)`` を返す。

    Raises:
        HWAuthError: 認証エラー（握りつぶさず再送出）
        AsgardShopReadError: 通信・API エラー、または予期しないレスポンス形式
    """
    try:
        res = client.call(
            {"calls": [{"name": ApiAction.CLAN_RAID_GET_INFO, "args": {}, "ident": "body"}]}
        )
    except HWAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AsgardShopReadError(f"clanRaid_getInfo failed: {exc}") from exc
    if not res.is_success:
        raise AsgardShopReadError(
            f"clanRaid_getInfo failed ({res.error_name or res.status.value})"
        )
    detail = res.detail if isinstance(res.detail, dict) else {}
    response = detail.get("response")
    if not isinstance(response, dict):
        raise AsgardShopReadError("clanRaid_getInfo returned unexpected response (missing 'response' dict)")
    shop = response.get("shop")
    if not isinstance(shop, dict):
        raise AsgardShopReadError("clanRaid_getInfo returned unexpected response (missing 'shop' dict)")
    return shop, _safe_int(response.get("coins"))


def _safe_int(value: Any, default: int = 0) -> int:
    """int 安全変換（失敗時は default）。

    client.py の同名関数と異なり default を指定でき、bool（True/False）も
    数値（1/0）に変換する（API レスポンスの型の揺れはこちらで吸収する）。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _slot_buff_ids(shop: dict[str, Any]) -> set[int]:
    """shop 内の全 slot の buffId 集合（Osh 判定用）。"""
    buff_ids: set[int] = set()
    for item in shop.values():
        if isinstance(item, dict) and item.get("buffId") is not None:
            buff_ids.add(_safe_int(item["buffId"]))
    return buff_ids


def is_osh_shop(shop: dict[str, Any]) -> bool:
    """Osh 週のショップかどうかを buffId シグネチャで判定する。

    部分集合（非空）で判定するため、買い切った slot が shop から省略された
    場合やラインナップが将来追加された場合でも Osh 週として扱える。
    Maestro 週など Osh と異なるラインナップの場合は False。
    空 shop は判定不能のため False（= スキップ扱い。購入対象が存在しないので
    実害はない）。
    """
    buff_ids = _slot_buff_ids(shop)
    return bool(buff_ids) and buff_ids.issubset(OSH_BUFF_IDS)


def is_maestro_shop(shop: dict[str, Any]) -> bool:
    """Maestro 週（Phantom Orchestra）のショップかどうかを判定する。

    Osh と同様に buffId 集合が ``MAESTRO_BUFF_IDS``（112〜133）の部分集合
    （非空）かを調べる。空 shop は判定不能のため False。
    """
    buff_ids = _slot_buff_ids(shop)
    return bool(buff_ids) and buff_ids.issubset(MAESTRO_BUFF_IDS)


def parse_slot(slot_id: Any, item: Any) -> AsgardItem | None:
    """slot を Valor Emblem 商品としてパースする。

    ゴールドバフ（cost に Valor コイン ``coin[30]`` を含まない）、価格が
    0 以下（パース失敗を含む）、構造不正・slotId が数値でない slot は
    ``None`` を返す（購入候補から除外される）。
    """
    if not isinstance(item, dict):
        return None
    # slotId は clanRaid_shopBuy の args にそのまま渡るため、数値化できない
    # キー（"x" 等）は除外する（変換失敗時に 0 で購入リクエストが飛ぶのを防ぐ）。
    if not str(slot_id).isdigit():
        return None
    cost = item.get("cost")
    if not isinstance(cost, dict):
        return None
    coins = cost.get("coin")
    if not isinstance(coins, dict) or str(VALOR_COIN_ID) not in coins:
        return None
    price = _safe_int(coins[str(VALOR_COIN_ID)])
    if price <= 0:
        return None
    return AsgardItem(
        slot_id=_safe_int(slot_id),
        buff_id=_safe_int(item.get("buffId")),
        buff_value=_safe_int(item.get("buffValue")),
        price=price,
    )


def parse_gold_slot(slot_id: Any, item: Any) -> AsgardItem | None:
    """slot をゴールドバフ（cost.gold 支払い）としてパースする。

    ゴールド価格が 0 以下（パース失敗を含む）、構造不正・slotId が数値で
    ない slot は ``None`` を返す（購入候補から除外される）。
    """
    if not isinstance(item, dict):
        return None
    if not str(slot_id).isdigit():
        return None
    cost = item.get("cost")
    if not isinstance(cost, dict):
        return None
    gold_price = cost.get("gold")
    if gold_price is None:
        return None
    price = _safe_int(gold_price)
    if price <= 0:
        return None
    return AsgardItem(
        slot_id=_safe_int(slot_id),
        buff_id=_safe_int(item.get("buffId")),
        buff_value=_safe_int(item.get("buffValue")),
        price=price,
    )


def is_bought(item: Any) -> bool:
    """slot の購入済み判定（boughtCount >= buyLimit で購入済みとみなす）。"""
    if not isinstance(item, dict):
        return True
    bought_count = _safe_int(item.get("boughtCount"))
    buy_limit = _safe_int(item.get("buyLimit"), 1)
    return bought_count >= buy_limit


def build_buy_queue(shop: dict[str, Any]) -> list[AsgardItem]:
    """未購入の Valor Emblem 商品を優先度順に並べた購入キューを構築する。

    優先度 1〜3 に含まれる slot はその優先度・リスト順で先頭に並び、
    それ以外の商品は価格昇順（同額は slot 昇順）で末尾に続く。
    （優先度外の商品は効果量・週替わり価格のバランスが不明なため、安い
    ものを先に買う価格昇順で揃えている。）
    購入済み・ゴールドバフ・構造不正の slot は除外される。
    """
    shop_items = {
        _safe_int(slot_id): parse_slot(slot_id, item)
        for slot_id, item in shop.items()
        if not is_bought(item)
    }
    candidates = {k: v for k, v in shop_items.items() if v is not None}

    queue: list[AsgardItem] = []
    queued_slots: set[int] = set()
    for level in sorted(OSH_PRIORITY):
        for slot_id in OSH_PRIORITY[level]:
            item = candidates.get(slot_id)
            if item is not None:
                queue.append(item)
                queued_slots.add(slot_id)

    remaining = sorted(
        (item for slot_id, item in candidates.items() if slot_id not in queued_slots),
        key=lambda item: (item.price, item.slot_id),
    )
    queue.extend(remaining)
    return queue


def _maestro_eval_key(items: tuple[AsgardItem, ...]) -> tuple[int, ...]:
    """購入プランの辞書式評価キーを計算する。

    (S 数, -S 順位合計, A 数, -A 順位合計, B 数, -B 順位合計, -合計コスト)
    のタプルで、大きいほど良い。S 数を最優先し（S クラス 1 個の確保は
    A/B クラスの複数購入より優先）、同一クラス内では順位合計が小さい
    （= 高順位を多く含む）ものを優先し、最後に合計コストが小さい
    （= 残コインを多くする）ものを優先する。
    """
    s_count = a_count = b_count = 0
    s_rank_sum = a_rank_sum = b_rank_sum = 0
    total = 0
    for item in items:
        # 優先度表外の slot は C ランク（優先度 0）として扱い、クラスカウント
        # にも合計コストにも含めない（防御的対応。呼び出し元 select_maestro_plan
        # は表内 slot のみを渡す）。
        priority = MAESTRO_PRIORITY.get(item.slot_id, 0)
        if priority <= 0:
            continue
        total += item.price
        if priority <= 6:
            s_count += 1
            s_rank_sum += priority
        elif priority <= 9:
            a_count += 1
            a_rank_sum += priority
        else:
            b_count += 1
            b_rank_sum += priority
    return (s_count, -s_rank_sum, a_count, -a_rank_sum, b_count, -b_rank_sum, -total)


def select_maestro_plan(shop: dict[str, Any], coins: int) -> list[AsgardItem]:
    """Maestro 週の購入プランを組み合わせ最適化で選定する。

    候補は ``MAESTRO_PRIORITY`` に載る未購入の Valor Emblem 商品のみ
    （C ランクは購入対象外）。合計コストが ``coins`` を超えない全組み合わせ
    から、辞書式評価（``_maestro_eval_key``）が最大の組み合わせを選ぶ。

    Returns:
        購入する商品のリスト（購入順は順位昇順: S の高順位から B の低順位へ）。
        購入対象がない場合は空リスト。
    """
    candidates: list[AsgardItem] = []
    for slot_id, item in shop.items():
        if is_bought(item):
            continue
        parsed = parse_slot(slot_id, item)
        if parsed is None or parsed.slot_id not in MAESTRO_PRIORITY:
            continue
        candidates.append(parsed)
    if not candidates:
        return []

    best: tuple[AsgardItem, ...] = ()
    best_key: tuple[int, ...] | None = None
    for size in range(len(candidates) + 1):
        for combo in itertools.combinations(candidates, size):
            if sum(item.price for item in combo) > coins:
                continue
            key = _maestro_eval_key(combo)
            if best_key is None or key > best_key:
                best_key = key
                best = combo
    return sorted(best, key=lambda item: MAESTRO_PRIORITY[item.slot_id])


def _gold_label(item: AsgardItem) -> str:
    """ゴールドバフの表示ラベル。"""
    return f"[Realm Traveler Gold] Slot:{item.slot_id} -> buff {item.buff_id} (x{item.buff_value}, {item.price} Gold)"


def _gold_buff_remaining(item: dict[str, Any]) -> int:
    """ゴールドバフ slot の残り購入回数（buyLimit - boughtCount、下限 0）。"""
    return max(0, _safe_int(item.get("buyLimit"), 1) - _safe_int(item.get("boughtCount")))


def _gold_buff_slots(shop: dict[str, Any]) -> list[tuple[AsgardItem, int]]:
    """ゴールドバフ（cost.gold 支払い・未購入）の購入候補を slot 昇順で返す。

    現在の仕様では slot 1〜5 がゴールドバフ（それ以外は Valor Emblem 商品）
    だが、slot は限定せず ``cost.gold`` の有無で判定する（API のラインナップ
    変更に追従するため）。

    Returns:
        ``(parse 済み商品, 残り購入回数)`` のリスト。
    """
    queue: list[tuple[AsgardItem, int]] = []
    for slot_id in sorted(shop, key=lambda k: _safe_int(k)):
        item = shop.get(slot_id)
        if not isinstance(item, dict):
            continue
        parsed = parse_gold_slot(slot_id, item)
        if parsed is None:
            continue
        remaining = _gold_buff_remaining(item)
        if remaining > 0:
            queue.append((parsed, remaining))
    return queue


def _purchase_gold_buffs(
    client: HWClient,
    shop: dict[str, Any],
    gold_budget: int,
    prefix: str,
) -> tuple[list[AsgardResult], int, int]:
    """ゴールドバフ（slot 1〜5）を残り購入回数分購入する。

    ``gold_budget`` が対象商品の最低価格未満の場合は何も購入しない。購入
    失敗（NotEnough）時は以降のゴールドバフ購入をすべて打ち切る（Valor 商品の
    残高不足時と同じ安全策）。

    Returns:
        ``(results, bought, spent)``。bought / spent はゴールドバフ分のみ。
    """
    results: list[AsgardResult] = []
    bought = 0
    spent = 0
    gold_slots = _gold_buff_slots(shop)
    if not gold_slots:
        return results, bought, spent
    if gold_budget < min(parsed.price for parsed, _ in gold_slots):
        print(
            f"{Emojis.INFO}{prefix}Gold buffs: insufficient gold ({gold_budget}) - skipping.",
            flush=True,
        )
        return results, bought, spent

    print(f"\n{Emojis.STEP}{prefix}--- Purchasing Gold Buffs ---", flush=True)
    funds_exhausted = False
    for parsed, remaining in gold_slots:
        for _ in range(remaining):
            if funds_exhausted or gold_budget < parsed.price:
                print(
                    f"{Emojis.WARNING}{prefix}Skipping {_gold_label(parsed)} (Insufficient gold).",
                    flush=True,
                )
                results.append(
                    AsgardResult(action=_gold_label(parsed), status=ResponseStatus.SKIPPED, error="Insufficient gold")
                )
                continue
            print(f"{prefix}Purchasing {_gold_label(parsed)}...", flush=True)
            buy_call = {
                "name": ApiAction.CLAN_RAID_SHOP_BUY,
                "args": {"slotId": parsed.slot_id},
                "context": {"actionTs": 0},
                "ident": f"gold_buy_{parsed.slot_id}",
            }
            res = client.call({"calls": [buy_call]})
            if res.is_success:
                print(f"  Result: {Emojis.SUCCESS}Success", flush=True)
                results.append(AsgardResult(action=_gold_label(parsed), status=ResponseStatus.SUCCESS))
                gold_budget -= parsed.price
                spent += parsed.price
                bought += 1
            else:
                error_name = res.error_name or "unknown"
                print(f"  Result: {Emojis.ERROR}Failed ({error_name})", flush=True)
                results.append(AsgardResult(action=_gold_label(parsed), status=ResponseStatus.ERROR, error=error_name))
                if error_name == ErrorName.NOT_ENOUGH:
                    print(f"  -> {Emojis.WARNING}Insufficient gold. Skipping rest of the gold buffs.", flush=True)
                    funds_exhausted = True
                    break
            client.sleep()
    return results, bought, spent


def _plan_gold_buffs(
    shop: dict[str, Any],
    gold_budget: int,
    prefix: str,
) -> tuple[list[AsgardResult], int, int]:
    """ゴールドバフの購入計画（dry-run）を表示し、結果サマリを返す。"""
    results: list[AsgardResult] = []
    bought = 0
    spent = 0
    gold_slots = _gold_buff_slots(shop)
    if not gold_slots:
        return results, bought, spent
    print(f"\n{Emojis.STEP}{prefix}--- Gold Buff Plan (dry-run) ---", flush=True)
    if gold_budget < min(parsed.price for parsed, _ in gold_slots):
        print(f"  {Emojis.WARNING}Insufficient gold ({gold_budget}) - skipping all gold buffs.", flush=True)
        return results, bought, spent
    for parsed, remaining in gold_slots:
        for _ in range(remaining):
            affordable = gold_budget >= parsed.price
            if affordable:
                gold_budget -= parsed.price
                bought += 1
                spent += parsed.price
                results.append(AsgardResult(action=_gold_label(parsed), status=ResponseStatus.SUCCESS))
            else:
                results.append(
                    AsgardResult(action=_gold_label(parsed), status=ResponseStatus.SKIPPED, error="Insufficient gold")
                )
            mark = "✅" if affordable else "⏭ "
            print(f"  {mark} {_gold_label(parsed)}", flush=True)
    return results, bought, spent


def run_asgard_shop(
    client: HWClient,
    dry_run: bool = False,
    account_alias: str | None = None,
    gold_buffs: bool | None = None,
) -> AsgardRunResult:
    """Asgard ショップの購入を実行（または計画表示）する。

    Args:
        client: HWClient インスタンス。
        dry_run: True の場合は購入せず計画（購入順・合計コスト）のみ表示。
        account_alias: 表示用のアカウント名（None なら省略）。
        gold_buffs: None（デフォルト）なら週依存（Osh 週は購入しない、
            Maestro 週は購入する）。True で常に購入、False で常にスキップ。

    Returns:
        AsgardRunResult。Osh / Maestro 以外のラインナップや空 shop の場合は
        ``skipped=True``。

    Note:
        Valor Emblem 残高（coins）は 1 ショップ分の連続購入ではローカル減算で
        十分（このツール以外の購入が挟まらないため）。実際の購入失敗
        （NotEnough）が起きた場合も以降をスキップする安全策を併用している。
        ゴールドバフの残高は ``fetch_player_status`` で取得した最新値を基準に
        し、同様に購入失敗時はスキップする。
    """
    prefix = f"[{account_alias}] " if account_alias else ""

    print(f"\n{Emojis.STEP}{prefix}--- Fetching current Asgard shop status ---", flush=True)
    try:
        shop, coins = fetch_clan_raid_shop(client)
    except AsgardShopReadError as exc:
        print(f"{Emojis.ERROR}{prefix}Error: Failed to fetch Asgard shop data. {exc}", flush=True)
        return AsgardRunResult(
            coins=0, spent=0, remaining=0, bought=0, skipped=False, items=[], error=str(exc)
        )

    if is_osh_shop(shop):
        lineup = "Osh"
        plan = build_buy_queue(shop)
    elif is_maestro_shop(shop):
        lineup = "Maestro"
        plan = select_maestro_plan(shop, coins)
    else:
        # 空 shop（買い切り済み等）と未知のラインナップはどちらも購入対象
        # なしとしてスキップする（buy は一切発生しない）。
        if shop:
            print(
                f"{Emojis.INFO}{prefix}Current Guild Raid shop is not a supported lineup "
                "(Osh or Maestro) - skipping.",
                flush=True,
            )
        else:
            print(
                f"{Emojis.INFO}{prefix}Asgard shop is empty (all slots bought out) - nothing to buy.",
                flush=True,
            )
        return AsgardRunResult(coins=coins, spent=0, remaining=coins, bought=0, skipped=True, items=[])

    total_cost = sum(item.price for item in plan)
    print(f"{Emojis.INFO}{prefix}{lineup} week detected: {len(plan)} item(s) available, budget: {coins} Valor Emblems "
          f"(total cost: {total_cost}).", flush=True)

    # ゴールドバフ（Valor 商品より先に購入。通貨が独立しているため順序は任意）。
    # gold_buffs=None は週依存: Osh 週は購入しない、Maestro 週は購入する（デフォルト）。
    gold_enabled = gold_buffs
    if gold_enabled is None:
        gold_enabled = lineup == "Maestro"
        if not gold_enabled:
            print(
                f"{Emojis.INFO}{prefix}Gold buffs: skipped (default off for {lineup} week; "
                "use --gold to enable).",
                flush=True,
            )
    gold_results: list[AsgardResult] = []
    gold_bought = 0
    gold_spent = 0
    if gold_enabled and _gold_buff_slots(shop):
        try:
            gold_budget = _safe_int(client.fetch_player_status().gold)
        except HWAuthError:
            raise
        except Exception:  # noqa: BLE001 - 残高取得失敗時はゴールドバフをスキップして続行
            print(
                f"{Emojis.WARNING}{prefix}Failed to fetch gold balance - skipping gold buffs.",
                flush=True,
            )
        else:
            if dry_run:
                gold_results, gold_bought, gold_spent = _plan_gold_buffs(shop, gold_budget, prefix)
            else:
                gold_results, gold_bought, gold_spent = _purchase_gold_buffs(client, shop, gold_budget, prefix)

    if dry_run:
        print(f"\n{Emojis.STEP}{prefix}--- Purchase Plan (dry-run) ---", flush=True)
        results: list[AsgardResult] = []
        budget = coins
        planned_bought = 0
        planned_spent = 0
        for i, item in enumerate(plan):
            affordable = item.price <= budget
            if affordable:
                budget -= item.price
                planned_bought += 1
                planned_spent += item.price
                results.append(AsgardResult(action=item.label, status=ResponseStatus.SUCCESS))
            else:
                results.append(
                    AsgardResult(action=item.label, status=ResponseStatus.SKIPPED, error="Insufficient budget")
                )
            mark = "✅" if affordable else "⏭ "
            print(f"  [{i + 1}/{len(plan)}] {mark} {item.label}", flush=True)
        print(
            f"\n{Emojis.FINISH}{prefix}--- Plan Summary --- "
            f"Planned: {planned_bought} item(s) for {planned_spent} Valor Emblems "
            f"(remaining budget: {budget}), "
            f"Gold buffs: {gold_bought} item(s) for {gold_spent} Gold.",
            flush=True,
        )
        return AsgardRunResult(
            coins=coins,
            spent=planned_spent,
            remaining=budget,
            bought=planned_bought,
            skipped=False,
            items=gold_results + results,
            gold_bought=gold_bought,
            gold_spent=gold_spent,
        )

    print(f"\n{Emojis.STEP}{prefix}--- Purchasing Target Items ---", flush=True)
    results = []
    budget = coins
    bought = 0
    spent = 0
    funds_exhausted = False
    for i, item in enumerate(plan):
        if funds_exhausted or item.price > budget:
            print(
                f"[{i + 1}/{len(plan)}] {Emojis.WARNING}Skipping {item.label} (Insufficient Valor Emblems).",
                flush=True,
            )
            results.append(AsgardResult(action=item.label, status=ResponseStatus.SKIPPED, error="Insufficient budget"))
            continue

        print(f"[{i + 1}/{len(plan)}] Purchasing {item.label}...", flush=True)
        buy_call = {
            "name": ApiAction.CLAN_RAID_SHOP_BUY,
            "args": {"slotId": item.slot_id},
            "context": {"actionTs": 0},
            "ident": f"buy_{item.slot_id}",
        }
        res = client.call({"calls": [buy_call]})
        if res.is_success:
            print(f"  Result: {Emojis.SUCCESS}Success", flush=True)
            results.append(AsgardResult(action=item.label, status=ResponseStatus.SUCCESS))
            budget -= item.price
            spent += item.price
            bought += 1
        else:
            error_name = res.error_name or "unknown"
            print(f"  Result: {Emojis.ERROR}Failed ({error_name})", flush=True)
            results.append(AsgardResult(action=item.label, status=ResponseStatus.ERROR, error=error_name))
            if error_name == ErrorName.NOT_ENOUGH:
                print(f"  -> {Emojis.WARNING}Insufficient Valor Emblems. Skipping rest of the shop.", flush=True)
                funds_exhausted = True
        client.sleep()

    print(f"\n{Emojis.FINISH}{prefix}--- Asgard Shop Results Summary ---", flush=True)
    print(f"  {Emojis.SUCCESS}Bought: {bought} item(s), Spent: {spent} / {coins}, Remaining: {budget}", flush=True)
    if gold_bought:
        print(f"  {Emojis.SUCCESS}Gold buffs: Bought: {gold_bought} item(s), Spent: {gold_spent} Gold", flush=True)
    return AsgardRunResult(
        coins=coins,
        spent=spent,
        remaining=budget,
        bought=bought,
        skipped=False,
        items=gold_results + results,
        gold_bought=gold_bought,
        gold_spent=gold_spent,
    )