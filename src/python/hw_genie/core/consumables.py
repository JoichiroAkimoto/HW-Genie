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
    """consumable 1 種の表示名と消費 RPC メソッド。"""

    name: str
    method: str


#: libId → 消費アイテム情報（実測で判明したものだけ登録する）。
CONSUMABLE_REGISTRY: dict[int, ConsumableInfo] = {
    17: ConsumableInfo(name="Stamina Potion (120)", method="consumableUseStamina"),
    215: ConsumableInfo(name="Equipment Fragment Chest", method="consumableUseLootBox"),
}

#: 一括消費（``consumable run``・``multi consumable``）の対象 libId。登録順。
CONSUMABLE_USE_TARGETS: list[int] = [
    215,
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


def display_name(lib_id: int) -> str | None:
    """登録済みアイテムの表示名を返す（未登録は ``None``）。"""
    info = CONSUMABLE_REGISTRY.get(lib_id)
    return info.name if info else None