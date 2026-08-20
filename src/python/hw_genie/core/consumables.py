"""consumable（消費アイテム）のレジストリと一括消費対象の定義。

Hero Wars の consumable は ``inventoryGet`` の ``response.consumable`` に
``{libId: 個数}`` で現れ、消費時はアイテム種別ごとに異なる RPC メソッド
（``consumableUseLootBox`` 等）を呼ぶ。libId だけでメソッドを特定できない
ため、実測で判明した libId → ``(名前, メソッド)`` をここに登録する。

``DEFAULT_HERO_MISSION_IDS``（hero_raid）と同様に、一括消費の対象も
コード内定数で固定管理する。追加・変更は PR で行う。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ConsumableInfo:
    """consumable 1 種の表示名・消費 RPC メソッド・1 リクエストあたりの消費量上限。

    ``max_amount`` は 1 回の RPC で消費できる上限（サーバー側の制限）。
    0 は制限なし（在庫全量を 1 リクエストで消費）を意味する。
    """

    name: str
    method: str
    max_amount: int = 0


#: libId → 消費アイテム情報（実測で判明したものだけ登録する）。
#: 1000 分割対象（Random Crystal 等）は ``max_amount=1000``。
CONSUMABLE_REGISTRY: dict[int, ConsumableInfo] = {
    17: ConsumableInfo(name="Stamina Potion (120)", method="consumableUseStamina"),
    215: ConsumableInfo(name="Equipment Fragment Chest", method="consumableUseLootBox"),
    188: ConsumableInfo(name="Element Summoning Doll", method="consumableUseLootBox"),
    153: ConsumableInfo(name="Lesser Pet Soul Chest", method="consumableUseLootBox"),
    169: ConsumableInfo(name="Random Crystal", method="consumableUseLootBox", max_amount=1000),
    170: ConsumableInfo(
        name="Random Vibrant Crystal", method="consumableUseLootBox", max_amount=1000
    ),
    171: ConsumableInfo(
        name="Random Radiant Crystal", method="consumableUseLootBox", max_amount=1000
    ),
    172: ConsumableInfo(name="Random Insignia", method="consumableUseLootBox", max_amount=1000),
    173: ConsumableInfo(
        name="Random Greater Insignia", method="consumableUseLootBox", max_amount=1000
    ),
    225: ConsumableInfo(name="Nature Box", method="consumableUseLootBox"),
    271: ConsumableInfo(
        name="Chest of Random Crystals", method="consumableUseLootBox", max_amount=1000
    ),
    272: ConsumableInfo(
        name="Chest of Random Insignia", method="consumableUseLootBox", max_amount=1000
    ),
    149: ConsumableInfo(name="Ancient Titan Artifact Chest", method="consumableUseLootBox"),
    369: ConsumableInfo(name="Violet Equipment Fragment Box - Mage", method="consumableUseLootBox"),
    370: ConsumableInfo(name="Violet Equipment Fragment Box - Tank", method="consumableUseLootBox"),
    371: ConsumableInfo(
        name="Violet Equipment Fragment Box - Marksman", method="consumableUseLootBox"
    ),
    372: ConsumableInfo(name="Violet Equipment Fragment Box - Healer", method="consumableUseLootBox"),
    373: ConsumableInfo(
        name="Violet Equipment Fragment Box - Support", method="consumableUseLootBox"
    ),
    374: ConsumableInfo(
        name="Violet Equipment Fragment Box - Warrior", method="consumableUseLootBox"
    ),
    375: ConsumableInfo(
        name="Violet Equipment Fragment Box - Control", method="consumableUseLootBox"
    ),
    376: ConsumableInfo(name="Orange Equipment Fragment Box - Mage", method="consumableUseLootBox"),
    377: ConsumableInfo(name="Orange Equipment Fragment Box - Tank", method="consumableUseLootBox"),
    378: ConsumableInfo(
        name="Orange Equipment Fragment Box - Marksman", method="consumableUseLootBox"
    ),
    379: ConsumableInfo(name="Orange Equipment Fragment Box - Healer", method="consumableUseLootBox"),
    380: ConsumableInfo(
        name="Orange Equipment Fragment Box - Support", method="consumableUseLootBox"
    ),
    381: ConsumableInfo(
        name="Orange Equipment Fragment Box - Warrior", method="consumableUseLootBox"
    ),
    382: ConsumableInfo(
        name="Orange Equipment Fragment Box - Control", method="consumableUseLootBox"
    ),
    383: ConsumableInfo(name="Red Equipment Fragment Box - Mage", method="consumableUseLootBox"),
    384: ConsumableInfo(name="Red Equipment Fragment Box - Tank", method="consumableUseLootBox"),
    385: ConsumableInfo(
        name="Red Equipment Fragment Box - Marksman", method="consumableUseLootBox"
    ),
    386: ConsumableInfo(name="Red Equipment Fragment Box - Healer", method="consumableUseLootBox"),
    387: ConsumableInfo(name="Red Equipment Fragment Box - Support", method="consumableUseLootBox"),
    388: ConsumableInfo(name="Red Equipment Fragment Box - Warrior", method="consumableUseLootBox"),
    389: ConsumableInfo(name="Red Equipment Fragment Box - Control", method="consumableUseLootBox"),
    393: ConsumableInfo(name="Hero Upgrade Chest", method="consumableUseLootBox"),
    421: ConsumableInfo(name="Silver Chest", method="consumableUseLootBox"),
    469: ConsumableInfo(name="Adventure Chest", method="consumableUseLootBox"),
    492: ConsumableInfo(name="Cosmic Titans Battle Chest", method="consumableUseLootBox"),
    497: ConsumableInfo(name="Cosmic Battle Chest", method="consumableUseLootBox"),
    493: ConsumableInfo(name="Titan Upgrade Chest", method="consumableUseLootBox"),
    422: ConsumableInfo(name="Buccaneer Stash", method="consumableUseLootBox"),
}

#: 一括消費（``consumable run``・``multi consumable``）の対象 libId。登録順。
CONSUMABLE_USE_TARGETS: list[int] = [
    215,
    188,
    153,
    169,
    170,
    171,
    172,
    173,
    225,
    271,
    272,
    149,
    369,
    370,
    371,
    372,
    373,
    374,
    375,
    376,
    377,
    378,
    379,
    380,
    381,
    382,
    383,
    384,
    385,
    386,
    387,
    388,
    389,
    393,
    421,
    469,
    492,
    497,
    493,
    422,
]


def resolve_use_method(lib_id: int, override: str | None = None) -> str | None:
    """libId の消費 RPC メソッドを返す。

    Args:
        lib_id: 消費対象の consumable libId。
        override: 明示指定（``--method``）があればレジストリより優先する。

    Returns:
        メソッド名。レジストリに無く、override も無い場合は ``None``。
    """
    if override:
        return override
    info = CONSUMABLE_REGISTRY.get(lib_id)
    return info.method if info else None


def max_amount(lib_id: int) -> int:
    """libId の 1 リクエストあたり消費量上限を返す（未登録・制限なしは 0）。"""
    info = CONSUMABLE_REGISTRY.get(lib_id)
    return info.max_amount if info else 0


def display_name(lib_id: int) -> str | None:
    """登録済みアイテムの表示名を返す（未登録は ``None``）。"""
    info = CONSUMABLE_REGISTRY.get(lib_id)
    return info.name if info else None
