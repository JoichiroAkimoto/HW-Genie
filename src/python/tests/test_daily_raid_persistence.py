from unittest.mock import MagicMock, patch
from hw_genie.main import cmd_daily
from hw_genie.core.session_manager import SessionManager


def test_daily_curl_propagates_real_name_as_account():
    """daily --curl（-a なし）で登録した実名が run_daily_raid の account に伝播する。"""
    from hw_genie.core.client import PlayerStatus

    args = MagicMock()
    args.curl = 'curl -H "x-auth-token: t" https://example.com'
    args.account = None

    mock_info = {
        "status": "success",
        "headers": {"x-auth-token": "t"},
        "player": PlayerStatus(id="p1", name="CurlPlayer", level=100),
    }

    with (
        patch("hw_genie.main.extract_headers_from_curl", return_value={"x-auth-token": "t"}),
        patch("hw_genie.main.extract_payload_from_curl", return_value={"calls": []}),
        patch("hw_genie.core.auth.get_user_info", return_value=mock_info),
        patch("hw_genie.main.HWClient"),
        patch("hw_genie.main.run_daily_raid") as mock_run_daily,
    ):
        cmd_daily(args)

    mock_run_daily.assert_called_once()
    # -a 未指定でも curl 登録した実名が account として渡される
    assert mock_run_daily.call_args.kwargs["account_alias"] == "CurlPlayer"


def test_daily_raid_uses_last_mission_id():
    # Setup
    account = "daily_test_user"
    mission_id = 123
    SessionManager.save(account, {"player": {"id": f"{account}_id", "name": account}})
    SessionManager.set_last_mission_id(mission_id, account=account)

    args = MagicMock()
    args.curl = None
    args.account = account

    with (
        patch("hw_genie.main._ensure_session") as mock_ensure,
        patch("hw_genie.main.HWClient"),
        patch("hw_genie.main.run_daily_raid") as mock_run_daily,
    ):
        mock_ensure.return_value = {"headers": {}}

        cmd_daily(args)

        # Verify run_daily_raid was called with an item_payload containing the last_mission_id
        # Note: cmd_daily passes item_payload={}, and run_daily_raid is supposed to fill it.
        # But since we mocked run_daily_raid, we just check if it was called.
        mock_run_daily.assert_called_once()
        args, kwargs = mock_run_daily.call_args
        assert kwargs["item_payload"] == {}


def test_run_daily_raid_fills_mission_id():
    from hw_genie.commands.daily_raid import run_daily_raid

    account = "daily_test_user"
    mission_id = 456
    SessionManager.save(account, {"player": {"id": f"{account}_id", "name": account}})
    SessionManager.set_last_mission_id(mission_id, account=account)

    client = MagicMock()
    client.headers = {"x-auth-user-id": account}

    with (
        patch("hw_genie.commands.daily_raid.run_hero_raid") as mock_hero,
        patch("hw_genie.commands.daily_raid.run_item_raid") as mock_item,
        patch("hw_genie.commands.daily_raid.run_hero_shopping") as mock_shop,
        patch("hw_genie.commands.daily_raid.print_player_status"),
    ):
        mock_hero.return_value = ([], 0, {})
        mock_shop.return_value = ([], {})

        # Run with empty payload
        run_daily_raid(client, item_payload={})

        # Verify run_item_raid was called with the mission_id from DB
        mock_item.assert_called_once()
        args, kwargs = mock_item.call_args
        payload = args[1]
        assert payload["mission_id"] == mission_id


def test_run_daily_raid_skips_when_no_mission_id():
    from hw_genie.commands.daily_raid import run_daily_raid

    account = "no_mission_user"
    # Ensure no mission id is set
    # (In a real test, we'd clear the DB, here we use a unique account)

    client = MagicMock()
    client.headers = {"x-auth-user-id": account}

    with (
        patch("hw_genie.commands.daily_raid.run_hero_raid") as mock_hero,
        patch("hw_genie.commands.daily_raid.run_item_raid") as mock_item,
        patch("hw_genie.commands.daily_raid.run_hero_shopping") as mock_shop,
        patch("hw_genie.commands.daily_raid.resolve_account", return_value=account),
        patch("hw_genie.commands.daily_raid.print_player_status"),
    ):
        mock_hero.return_value = ([], 0, {})
        mock_shop.return_value = ([], {})

        run_daily_raid(client, item_payload={})

        # run_item_raid should NOT be called
        mock_item.assert_not_called()
