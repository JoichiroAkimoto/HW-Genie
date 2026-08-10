# --- Mission Raid ---
MISSION_RAID_SUCCESS = {
    "results": [{"ident": "body", "result": {"response": {"reward": {"coin": {"1": 100}, "fragmentHero": {"195": 1}}, "exp": 10}}}],
    "date": 1773215224,
}

MISSION_RAID_STAMINA_ERROR = {
    "results": [{"ident": "body", "error": {"name": "notEnoughStamina", "detail": {"description": "Has 5, need 10"}}}],
    "date": 1773215224,
}

MISSION_RAID_LIMIT_REACHED = {
    "results": [{"ident": "body", "error": {"name": "limitReached", "detail": {"description": "Daily limit reached"}}}],
    "date": 1773215224,
}

# --- Error & Malformed Responses ---
AUTH_ERROR_RESPONSE = {"error": {"name": "auth"}}
INVALID_SESSION_RESPONSE = {"error": {"name": "InvalidSession"}}
EMPTY_RESULTS = {"results": []}
MALFORMED_RESPONSE = {"results": [{"ident": "body"}]}
INCOMPLETE_DATA_RESPONSE = {"results": [{"ident": "body", "result": {"response": {}}}]}

# --- Inventory Exchange ---
# 複数のヒーロー(ID 3, 12, 8)が換金されるケース
INVENTORY_EXCHANGE_STONES_MULTI = {
    "results": [
        {
            "ident": "body",
            "result": {
                "response": {
                    "reward": {
                        "coin": {"5": 1500}  # ソウルコイン
                    },
                    "cost": {"fragmentHero": {"3": 5, "12": 3, "8": 7}},
                }
            },
        }
    ],
    "date": 1773215224,
}

INVENTORY_EXCHANGE_STONES_SINGLE = {
    "results": [{"ident": "body", "result": {"response": {"reward": {"coin": {"5": 200}}, "cost": {"fragmentHero": {"44": 2}}}}}],
    "date": 1773215224,
}

# --- Stamina Recovery ---
STAMINA_RECOVERY_SUCCESS = {
    "results": [{"ident": "stamina_recovery", "result": {"response": {"reward": {"stamina": 120}, "cost": {"consumable": {"17": 1}}}}}],
    "date": 1773215224,
}

# --- Inventory (consumable 在庫) ---
# 実測の inventoryGet レスポンスに基づく（libId 215 = Equipment Fragment Chest）。
INVENTORY_GET_CONSUMABLE = {
    "results": [
        {
            "ident": "inventory",
            "result": {
                "response": {
                    "consumable": {
                        "17": 327,  # Stamina Potion
                        "20": 1142335,
                        "215": 48,  # Equipment Fragment Chest
                    },
                    "gear": {"4": 866},
                    "scroll": {"100": 5},
                }
            },
        }
    ],
    "date": 1786306606,
}

# 215 を全消費済みの在庫（VitaminD 相当: 在庫 0 またはキー消失）。
INVENTORY_GET_NO_STOCK = {
    "results": [
        {
            "ident": "inventory",
            "result": {
                "response": {
                    "consumable": {"17": 327, "20": 1142335},
                    "gear": {"4": 866},
                }
            },
        }
    ],
    "date": 1786306606,
}

# レジストリ未登録の consumable（201）を含む在庫。
INVENTORY_GET_UNREGISTERED = {
    "results": [
        {
            "ident": "inventory",
            "result": {
                "response": {
                    "consumable": {"17": 327, "201": 360, "215": 48},
                    "gear": {"4": 866},
                }
            },
        }
    ],
    "date": 1786306606,
}

# --- Consumable Use (LootBox) ---
# 実測の consumableUseLootBox レスポンス（libId 215 を 48 個消費）。
CONSUMABLE_USE_LOOT_BOX_SUCCESS = {
    "results": [
        {
            "ident": "consumable_use",
            "result": {
                "response": {
                    "48": {
                        "fragmentScroll": {"218": 5, "192": 10, "193": 15, "216": 5},
                        "fragmentGear": {"91": 10, "93": 10, "171": 5, "94": 5},
                    }
                }
            },
        }
    ],
    "date": 1786306606,
}

CONSUMABLE_USE_LIMIT_REACHED = {
    "results": [
        {
            "ident": "consumable_use",
            "error": {"name": "limitReached", "detail": {"description": "Daily limit reached"}},
        }
    ],
    "date": 1786306606,
}

# --- Shop ---
SHOP_GET_ALL_DUMMY = {
    "results": [
        {
            "ident": "shopGetAll",
            "result": {
                "response": {
                    "4": {  # Arena
                        "slots": {
                            "1": {"reward": {"fragmentHero": {"18": 5}}, "cost": {"coin": {"4": 500}}, "bought": 0},
                            "2": {"reward": {"fragmentHero": {"19": 5}}, "cost": {"coin": {"4": 500}}, "bought": 1},  # 購入済み
                        }
                    },
                    "8": {  # Soul Shop
                        "slots": {"1": {"reward": {"fragmentHero": {"31": 3}}, "cost": {"coin": {"5": 1500}}, "bought": 0}}
                    },
                }
            },
        }
    ]
}

# 複数ショップ、混合状態のダミーデータ
SHOP_GET_ALL_VARIED = {
    "results": [
        {
            "ident": "shopGetAll",
            "result": {
                "response": {
                    "4": {  # Arena
                        "slots": {
                            "1": {"reward": {"fragmentHero": {"18": 5}}, "cost": {"coin": {"4": 500}}, "bought": 0},
                            "2": {"reward": {"fragmentHero": {"19": 5}}, "cost": {"coin": {"4": 500}}, "bought": 1},  # スキップ
                        }
                    },
                    "8": {  # Soul Shop
                        "slots": {"1": {"reward": {"fragmentHero": {"31": 3}}, "cost": {"coin": {"5": 1500}}, "bought": 0},
                                  "2": {"reward": {"item": {"101": 1}}, "cost": {"coin": {"5": 1500}}, "bought": 0}},  # ソウルショップなので購入対象
                    },
                    "9": {  # Friend Shop
                        "slots": {"1": {"reward": {"fragmentHero": {"44": 5}}, "cost": {"coin": {"9": 500}}, "bought": 0}}
                    },
                }
            },
        }
    ]
}

SHOP_BUY_SUCCESS = {"results": [{"ident": "shopBuy", "result": {"response": {"reward": {"fragmentHero": {"18": 5}}, "cost": {"coin": {"4": 500}}}}}]}

SHOP_BUY_NOT_ENOUGH = {"results": [{"ident": "shopBuy", "error": {"name": "NotEnough", "detail": {"description": "Insufficient funds"}}}]}

# --- Asgard (Guild Raid) Shop ---
# Osh 週の価格表（slot 6〜21 の Valor Emblem 価格。週替わりだがシグネチャは固定）
_OSH_PRICES = {
    6: 50, 7: 150, 8: 50, 9: 150, 10: 50,
    11: 100, 12: 100, 13: 100, 14: 150,
    15: 50, 16: 100, 17: 50, 18: 50, 19: 150, 20: 100, 21: 50,
}

# Osh 週の slot → buffValue（実測値ベース）
_OSH_BUFF_VALUES = {
    1: 3, 2: 3, 3: 20, 4: 5, 5: 3,
    6: 3, 7: 25, 8: 15, 9: 4, 10: 25,
    11: 10, 12: 7, 13: 10, 14: 25,
    15: 50, 16: 20, 17: 10, 18: 10, 19: 5, 20: 7, 21: 4,
}


def _osh_shop_slots(bought: set[int] | None = None) -> dict:
    """Osh 週（buffId 61〜81、slot 1〜5 はゴールドバフ）のショップスロットを生成する。"""
    bought = bought or set()
    slots = {}
    for slot in range(1, 6):
        slots[str(slot)] = {
            "branch": "", "buffId": 60 + slot, "buffValue": _OSH_BUFF_VALUES[slot],
            "buyLimit": 5, "cost": {"gold": 1000000}, "rank": 0, "requirement": "", "boughtCount": 0,
        }
    for slot in range(6, 22):
        price = _OSH_PRICES[slot]
        slots[str(slot)] = {
            "branch": "", "buffId": 60 + slot, "buffValue": _OSH_BUFF_VALUES[slot],
            "buyLimit": 1, "cost": {"coin": {"30": price}},
            "rank": 3 if price == 50 else (2 if price == 100 else 1),
            "requirement": "", "boughtCount": 1 if slot in bought else 0,
        }
    return slots


# 未購入・残高 1000 の Osh 週（全 16 商品が購入可能）
CLAN_RAID_GET_INFO_OSH = {
    "results": [{"ident": "body", "result": {"response": {"shop": _osh_shop_slots(), "coins": 1000}}}]
}

# slot 8, 17 は購入済み・残高 100 の Osh 週
CLAN_RAID_GET_INFO_OSH_BOUGHT = {
    "results": [{"ident": "body", "result": {"response": {"shop": _osh_shop_slots(bought={8, 17}), "coins": 100}}}]
}

# Maestro 週（Osh と異なる buffId シグネチャ）→ スキップ対象
CLAN_RAID_GET_INFO_MAESTRO = {
    "results": [
        {
            "ident": "body",
            "result": {
                "response": {
                    "shop": {
                        str(slot): {
                            "branch": "", "buffId": 100 + slot, "buffValue": slot,
                            "buyLimit": 1, "cost": {"coin": {"30": 50}},
                            "rank": 3, "requirement": "", "boughtCount": 0,
                        }
                        for slot in range(6, 22)
                    },
                    "coins": 1000,
                }
            },
        }
    ]
}

CLAN_RAID_SHOP_BUY_SUCCESS = {"results": [{"ident": "buy_6", "result": {"response": {}}}]}

CLAN_RAID_SHOP_BUY_NOT_ENOUGH = {"results": [{"ident": "buy_6", "error": {"name": "NotEnough", "detail": {"description": "Insufficient funds"}}}]}

# --- User Info ---
USER_INFO_SUCCESS = {
    "results": [
        {
            "ident": "body",
            "result": {
                "response": {
                    "user": {"id": 123},
                    "name": "TestPlayer",
                    "level": 120,
                    "experience": 5000,
                    "gold": 1000000,
                    "starMoney": 500,
                    "refillable": [
                        {"id": 1, "amount": 150},  # Energy
                        {"id": 2, "amount": 10},
                    ],
                }
            },
        },
        {
            "ident": "arena",
            "result": {
                "response": {
                    "arenaPlace": "42",
                    "grandPlace": "15"
                }
            }
        }
    ]
}
