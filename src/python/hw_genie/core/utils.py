
def format_number_with_suffix(num: int) -> str:
    """数値を K, M, B, T などの接尾辞付きでフォーマットする"""
    if num < 1000:
        return str(num)
    
    suffixes = ["", "K", "M", "B", "T"]
    magnitude = 0
    num_float = float(num)
    
    while abs(num_float) >= 1000 and magnitude < len(suffixes) - 1:
        magnitude += 1
        num_float /= 1000.0
        
    return f"{num_float:.1f}{suffixes[magnitude]}"

def print_player_status(status: dict):
    """
    プレイヤー情報を標準出力に表示する。
    Args:
        status (dict): fetch_player_status() で取得した辞書
    """
    gold_str = format_number_with_suffix(status['gold'])
    gems_str = format_number_with_suffix(status['gems'])

    print("\n📊 --- Account Status ---")
    print(f"  👤 Name: {status['name']} (Lv.{status['level']})")
    print(f"  🏆 Arena Rank: {status['arena_rank']}")
    print(f"  👑 Grand Rank: {status['grand_rank']}")
    print(f"  ⚡️ Energy: {status['energy']} / {status['max_energy']}")
    print(f"  💰 Gold: {gold_str}")
    print(f"  💎 Emeralds: {gems_str}")
