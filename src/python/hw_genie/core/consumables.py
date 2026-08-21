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

    ``player_reward_choice_index`` は選択式報酬ボックス（Chest of X Titans 等）
    の報酬選択インデックスで、``consumableUseLootBox`` の args に
    ``playerRewardChoiceIndex`` として渡す。``None`` は渡さない。
    """

    name: str
    method: str
    max_amount: int = 0
    player_reward_choice_index: int | None = None


#: libId → 消費アイテム情報（実測で判明したものだけ登録する）。
#:
#: カテゴリ別にセクション分けして管理する（登録順 = CONSUMABLE_USE_TARGETS
#: の実行順。追加時は両方に同じセクション順で追記すること）。
#:
#: - Stamina: スタミナ回復（consumableUseStamina）
#: - Titan / Artifact Chests: 選択式報酬ボックス（playerRewardChoiceIndex 指定）
#: - Crystals: 1000 分割対象（max_amount=1000）
#: - Equipment Fragment Boxes: 装備片ボックス
#: - Other Chests: その他チェスト（マトリョーシカ＝開封で再出現するものを含む）
CONSUMABLE_REGISTRY: dict[int, ConsumableInfo] = {
    # --- Stamina ---
    17: ConsumableInfo(name="Stamina Potion (120)", method="consumableUseStamina"),
    # --- Titan / Artifact Chests（playerRewardChoiceIndex 指定）---
    47: ConsumableInfo(
        name="Chest of Defender Titans",
        method="consumableUseLootBox",
        player_reward_choice_index=2,
    ),
    48: ConsumableInfo(
        name="Chest of Marksman Titans",
        method="consumableUseLootBox",
        player_reward_choice_index=2,
    ),
    49: ConsumableInfo(
        name="Chest of Support Titans",
        method="consumableUseLootBox",
        player_reward_choice_index=2,
    ),
    50: ConsumableInfo(
        name="Chest of Supertitans",
        method="consumableUseLootBox",
        player_reward_choice_index=2,
    ),
    328: ConsumableInfo(
        name="Titan of Your Choice",
        method="consumableUseLootBox",
        player_reward_choice_index=0,
    ),
    62: ConsumableInfo(
        name="Artifact Essence Chest",
        method="consumableUseLootBox",
        player_reward_choice_index=4,
    ),
    63: ConsumableInfo(
        name="Artifact Scroll Chest",
        method="consumableUseLootBox",
        player_reward_choice_index=4,
    ),
    64: ConsumableInfo(
        name="Artifact Metal Chest",
        method="consumableUseLootBox",
        player_reward_choice_index=4,
    ),
    # --- Crystals（1000 分割対象）---
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
    271: ConsumableInfo(
        name="Chest of Random Crystals", method="consumableUseLootBox", max_amount=1000
    ),
    272: ConsumableInfo(
        name="Chest of Random Insignia", method="consumableUseLootBox", max_amount=1000
    ),
    # --- Equipment Fragment Boxes ---
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
    # --- Other Chests ---
    215: ConsumableInfo(name="Equipment Fragment Chest", method="consumableUseLootBox"),
    188: ConsumableInfo(name="Element Summoning Doll", method="consumableUseLootBox"),
    153: ConsumableInfo(name="Lesser Pet Soul Chest", method="consumableUseLootBox"),
    225: ConsumableInfo(name="Nature Box", method="consumableUseLootBox"),
    149: ConsumableInfo(name="Ancient Titan Artifact Chest", method="consumableUseLootBox"),
    393: ConsumableInfo(name="Hero Upgrade Chest", method="consumableUseLootBox"),
    421: ConsumableInfo(name="Silver Chest", method="consumableUseLootBox"),
    469: ConsumableInfo(name="Adventure Chest", method="consumableUseLootBox"),
    492: ConsumableInfo(name="Cosmic Titans Battle Chest", method="consumableUseLootBox"),
    497: ConsumableInfo(name="Cosmic Battle Chest", method="consumableUseLootBox"),
    493: ConsumableInfo(name="Titan Upgrade Chest", method="consumableUseLootBox"),
    422: ConsumableInfo(name="Buccaneer Stash", method="consumableUseLootBox"),
}

#: 一括消費（``consumable run``・``multi consumable``）の対象 libId。
#: セクション構成と並びは CONSUMABLE_REGISTRY と一致させる。
CONSUMABLE_USE_TARGETS: list[int] = [
    # --- Titan / Artifact Chests（playerRewardChoiceIndex 指定）---
    47,
    48,
    49,
    50,
    328,
    62,
    63,
    64,
    # --- Crystals（1000 分割対象）---
    169,
    170,
    171,
    172,
    173,
    271,
    272,
    # --- Equipment Fragment Boxes ---
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
    # --- Other Chests ---
    215,
    188,
    153,
    225,
    149,
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


def player_reward_choice_index(lib_id: int) -> int | None:
    """libId の報酬選択インデックスを返す（未登録・未指定は ``None``）。"""
    info = CONSUMABLE_REGISTRY.get(lib_id)
    return info.player_reward_choice_index if info else None


def display_name(lib_id: int) -> str | None:
    """登録済みアイテムの表示名を返す（未登録は ``None``）。"""
    info = CONSUMABLE_REGISTRY.get(lib_id)
    return info.name if info else None
