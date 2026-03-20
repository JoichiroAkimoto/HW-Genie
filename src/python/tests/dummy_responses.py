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
        }
    ]
}
