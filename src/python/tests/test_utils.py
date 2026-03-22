import pytest
from hw_genie.core.utils import format_number_with_suffix, print_player_status

@pytest.mark.parametrize("num, expected", [
    (0, "0"),
    (999, "999"),
    (1000, "1.0K"),
    (1500, "1.5K"),
    (10000, "10.0K"),
    (999900, "999.9K"),
    (1000000, "1.0M"),
    (2500000, "2.5M"),
    (1234567890, "1.2B"),
    (5000000000000, "5.0T"),
])
def test_format_number_with_suffix(num, expected):
    assert format_number_with_suffix(num) == expected

def test_print_player_status(capsys):
    status = {
        "name": "TestPlayer",
        "level": 120,
        "arena_rank": 5,
        "grand_rank": 10,
        "energy": 100,
        "max_energy": 180,
        "gold": 1234567,
        "gems": 9876,
    }
    
    print_player_status(status)
    captured = capsys.readouterr()
    
    assert "👤 Name: TestPlayer (Lv.120)" in captured.out
    assert "💰 Gold: 1.2M" in captured.out
    assert "💎 Emeralds: 9.9K" in captured.out
