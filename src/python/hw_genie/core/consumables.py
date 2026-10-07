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
    lootbox 以外のメソッドとは併用できない（登録時に拒否する）。

    ``recursive`` は ``consumableUseLootBox`` の args に ``recursive`` として
    渡すサーバー側 recursive 開封フラグ。``True`` のマトリョーシカ系アイテムはサーバー側で
    入れ子を自動開封するため、開封ラウンド数を減らせると期待できる（定量比較は
    未実施）。付与の証拠基準は (a) 本番実測済み（478/509/513）または (b) SKILL
    に libId ではなく名前で記載された既知マトリョーシカ
    （Ancient Titan Artifact Chest=149 / Adventure Chest=469 /
    Cosmic Titans Battle Chest=492 / Cosmic Battle Chest=497）に該当する libId
    のいずれかを満たすこととし、
    同名のみでは不十分とする（508・493 は対象外）。対象外は未検証・ERROR 時の
    回帰リスクがあるため ``False`` とし、実測で拡大する（lootbox 以外の
    メソッドには付与しない）。なおクライアント側ラウンドループ（在庫再確認の
    反復）とサーバー側 recursive（入れ子自動開封）は別概念である。
    """

    name: str
    method: str
    max_amount: int = 0
    player_reward_choice_index: int | None = None
    recursive: bool = False

    def __post_init__(self) -> None:
        if self.recursive and self.method != "consumableUseLootBox":
            raise ValueError(f"recursive=True requires consumableUseLootBox (got {self.method!r})")
        if self.player_reward_choice_index is not None and self.method != "consumableUseLootBox":
            raise ValueError(
                f"player_reward_choice_index requires consumableUseLootBox (got {self.method!r})"
            )


#: libId → 消費アイテム情報（実測で判明したものだけ登録する）。
#:
#: カテゴリ別にセクション分けして管理する（登録順 = CONSUMABLE_USE_TARGETS
#: の実行順。追加時は両方に同じセクション順で追記すること）。
#:
#: 17（Stamina Potion）はレジストリに登録するが一括消費の対象外（手動消費の
#: ため）とし、CONSUMABLE_USE_TARGETS には含めない。
#:
#: - Stamina: スタミナ回復（consumableUseStamina、targets 外）
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
    # recursive=True の証拠基準：(a) 本番実測済み（478/509/513）または (b) SKILL
    # に libId ではなく名前で記載された既知マトリョーシカ
    # （Ancient Titan Artifact Chest=149 / Adventure Chest=469 /
    # Cosmic Titans Battle Chest=492 / Cosmic Battle Chest=497）に該当する libId。
    # 同名のみでは不十分のため、508（492 と同名だが自体の証拠なし）・493（513 と
    # 同名）は False のままとし、実測で拡大する。(b) の 4 件は tests の8種行
    # （149/469/492/497＋Doll 系 176/185/187/190：クライアント側ラウンドループの
    # 既存定義）のうち SKILL 名記載に該当する部分集合である。
    # クライアント側ラウンドループ（在庫再確認の反復）と
    # サーバー側 recursive（入れ子自動開封）は別概念である。
    215: ConsumableInfo(name="Equipment Fragment Chest", method="consumableUseLootBox"),
    188: ConsumableInfo(name="Element Summoning Doll", method="consumableUseLootBox"),
    153: ConsumableInfo(name="Lesser Pet Soul Chest", method="consumableUseLootBox"),
    225: ConsumableInfo(name="Nature Box", method="consumableUseLootBox"),
    149: ConsumableInfo(
        name="Ancient Titan Artifact Chest", method="consumableUseLootBox", recursive=True
    ),
    398: ConsumableInfo(name="Hero Upgrade Chest", method="consumableUseLootBox"),
    421: ConsumableInfo(name="Silver Chest", method="consumableUseLootBox"),
    469: ConsumableInfo(
        name="Adventure Chest", method="consumableUseLootBox", recursive=True
    ),
    492: ConsumableInfo(
        # 492/508: 同名の別libId
        name="Cosmic Titans Battle Chest", method="consumableUseLootBox", recursive=True
    ),
    497: ConsumableInfo(
        name="Cosmic Battle Chest", method="consumableUseLootBox", recursive=True
    ),
    493: ConsumableInfo(
        # 493/513: 同名の別libId（493 は個別実測待ちのため False）
        name="Titan Upgrade Chest",
        method="consumableUseLootBox",
    ),
    422: ConsumableInfo(name="Buccaneer Stash", method="consumableUseLootBox"),
    176: ConsumableInfo(name="Otherworldly Doll", method="consumableUseLootBox"),
    185: ConsumableInfo(name="Charged Doll", method="consumableUseLootBox"),
    187: ConsumableInfo(name="Fair Wind Doll", method="consumableUseLootBox"),
    190: ConsumableInfo(name="Imprisoned Doll", method="consumableUseLootBox"),
    317: ConsumableInfo(name="Cosmic Box", method="consumableUseLootBox"),
    78: ConsumableInfo(name="Boxy's Gift", method="consumableUseLootBox"),
    186: ConsumableInfo(name="Doll of Glorious Heroes", method="consumableUseLootBox"),
    189: ConsumableInfo(name="Doll of Loyal Companions", method="consumableUseLootBox"),
    468: ConsumableInfo(name="Explorer's Bag", method="consumableUseLootBox"),
    502: ConsumableInfo(name="Ascension Chest", method="consumableUseLootBox"),
    508: ConsumableInfo(
        # 492/508: 同名の別libId（508 は自体の証拠なしのため False。同名のみでは不十分）
        name="Cosmic Titans Battle Chest",
        method="consumableUseLootBox",
    ),
    478: ConsumableInfo(
        name="Titans Tesseract of Luck", method="consumableUseLootBox", recursive=True
    ),
    509: ConsumableInfo(
        name="Mead Festival Chest",
        method="consumableUseLootBox",
        recursive=True,
    ),
    513: ConsumableInfo(
        # 493/513: 同名の別libId
        name="Titan Upgrade Chest",
        method="consumableUseLootBox",
        recursive=True,
    ),
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
    398,
    421,
    469,
    492,
    497,
    493,
    422,
    176,
    185,
    187,
    190,
    317,
    78,
    186,
    189,
    468,
    502,
    508,
    478,
    509,
    513,
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


def recursive_flag(lib_id: int) -> bool:
    """libId の再帰開封フラグを返す（未登録・未指定は ``False``）。"""
    info = CONSUMABLE_REGISTRY.get(lib_id)
    return info.recursive if info else False


def display_name(lib_id: int) -> str | None:
    """登録済みアイテムの表示名を返す（未登録は ``None``）。"""
    info = CONSUMABLE_REGISTRY.get(lib_id)
    return info.name if info else None
