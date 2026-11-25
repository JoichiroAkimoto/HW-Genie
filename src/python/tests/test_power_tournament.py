import re
from unittest.mock import MagicMock

import pytest

from hw_genie.core.client import ApiAction, HWAuthError, ResponseStatus
from hw_genie.core.utils import display_width, pad

STATE_PAYLOAD = {
    "id": 1000,
    "startTime": 1_700_000_000,
    "endTime": 1_700_100_000,
    "state": 1,
    "type": "titans",
    "currentDayRewards": [
        {
            "id": 5,
            "points": 300,
            "reward": {"consumable": {"149": 2}},
            "isFarmed": 0,
        },
        {
            "id": 6,
            "points": 800,
            "reward": {"consumable": {"20": 2500}},
            "isFarmed": 0,
        },
        {
            "id": 7,
            "points": 1500,
            "reward": {"coin": {"24": 2000}},
            "isFarmed": 1,
        },
        {
            "id": 8,
            "points": 3000,
            "reward": {"powerTournamentCoins": 3000},
            "isFarmed": 0,
        },
    ],
    "currentDailyPoints": 900,
    "currentTournamentPoints": 1234,
    "statueLevel": 1,
    "finalRewardFarmed": 0,
}

GROUP_PAYLOAD = {
    "users": {
        "1": {"id": "1", "name": "Bob", "lastLoginTime": "1700000000", "titansPower": 1000},
        "2": {"id": "2", "name": "Alice", "lastLoginTime": "1700000100", "titansPower": 2000},
        "3": {"id": "3", "name": "Carol", "lastLoginTime": "1699990000", "titansPower": 3000},
        "4": {"id": "4", "name": "Dave", "lastLoginTime": "1699900000", "titansPower": 4000},
    },
    "points": {"1": 500, "2": 900, "3": 900, "4": 100},
    "rewards": [{"place": 1, "reward": {"coin": {"3941001120": 200}}}],
}


def _res(payload, *, status=ResponseStatus.SUCCESS, error_name=None):
    res = MagicMock()
    res.is_success = status == ResponseStatus.SUCCESS
    res.status = status
    res.error_name = error_name
    res.detail = {"response": payload}
    return res


def _state_res():
    return _res(STATE_PAYLOAD)


def _group_res():
    return _res(GROUP_PAYLOAD)


def _user_res(user_id="2"):
    return _res({"id": user_id, "name": "Alice", "level": "130"})


def test_api_action_has_power_tournament():
    assert ApiAction.POWER_TOURNAMENT_GET_STATE == "powerTournament_getState"
    assert ApiAction.POWER_TOURNAMENT_GET_GROUP_INFO == "powerTournament_getGroupInfo"
    assert ApiAction.POWER_TOURNAMENT_DAILY_FARM == "powerTournament_dailyFarm"
    assert ApiAction.CLAN_INVITES_SEARCH == "clanInvites_search"


def test_power_field_matches_event_type():
    from hw_genie.commands.power_tournament import _power_field

    assert _power_field("titans") == "titansPower"
    assert _power_field("titan") == "titansPower"
    assert _power_field("heroes") == "heroesPower"
    assert _power_field("hero") == "heroesPower"


@pytest.mark.parametrize("tournament_type", ["", "  ", "unknown", "solo", "titanx"])
def test_power_field_unknown_type_is_none(tournament_type):
    from hw_genie.commands.power_tournament import _power_field

    # Unknown types must not silently fall back to Titan power.
    assert _power_field(tournament_type) is None


def test_parse_state_titans_and_claimability():
    from hw_genie.commands.power_tournament import parse_state

    state = parse_state(STATE_PAYLOAD, now=1_700_050_000)
    assert state is not None
    assert state.tournament_type == "titans"
    assert state.type_label == "Titan"
    assert state.daily_points == 900
    assert state.total_points == 1234
    assert state.ended is False
    assert state.final_reward_farmed is False
    rewards = {reward.reward_id: reward for reward in state.rewards}
    assert rewards[5].claimable is True
    assert rewards[6].claimable is True
    assert rewards[7].claimable is False  # already farmed
    assert rewards[8].claimable is False  # 3000 > 900 daily points


def test_parse_state_ended():
    from hw_genie.commands.power_tournament import parse_state

    state = parse_state(STATE_PAYLOAD, now=1_700_200_000)
    assert state is not None
    assert state.ended is True


def test_parse_state_rejects_unexpected_shape():
    from hw_genie.commands.power_tournament import (
        PowerTournamentReadError,
        parse_state,
    )

    with pytest.raises(PowerTournamentReadError):
        parse_state({})
    with pytest.raises(PowerTournamentReadError):
        parse_state({"foo": 1})


@pytest.mark.parametrize(
    "current_day_rewards",
    [
        {"5": {"id": 5, "points": 300, "isFarmed": 0}},
        "bad",
    ],
)
def test_parse_state_rejects_non_list_current_day_rewards(current_day_rewards):
    from hw_genie.commands.power_tournament import (
        PowerTournamentReadError,
        parse_state,
    )

    payload = dict(STATE_PAYLOAD)
    payload["currentDayRewards"] = current_day_rewards
    with pytest.raises(PowerTournamentReadError) as excinfo:
        parse_state(payload, now=1_700_050_000)
    assert "unexpected getState currentDayRewards shape" in str(excinfo.value)


@pytest.mark.parametrize(
    "entry",
    [[{"points": 300}], [5], [None]],
)
def test_parse_state_rejects_malformed_current_day_reward_entry(entry):
    from hw_genie.commands.power_tournament import (
        PowerTournamentReadError,
        parse_state,
    )

    payload = dict(STATE_PAYLOAD)
    payload["currentDayRewards"] = entry
    with pytest.raises(PowerTournamentReadError):
        parse_state(payload, now=1_700_050_000)


def test_parse_state_empty_current_day_rewards_is_valid():
    from hw_genie.commands.power_tournament import parse_state

    payload = dict(STATE_PAYLOAD)
    payload["currentDayRewards"] = []
    state = parse_state(payload, now=1_700_050_000)
    assert state.rewards == []
    assert state.daily_points == 900


def test_claimable_uses_daily_points_not_total():
    from hw_genie.commands.power_tournament import parse_state

    payload = dict(STATE_PAYLOAD)
    payload["currentDayRewards"] = [
        {"id": 9, "points": 1000, "reward": {"coin": {"1": 1}}, "isFarmed": 0}
    ]
    state = parse_state(payload, now=1_700_050_000)
    # daily 900 < 1000 <= total 1234: using total would wrongly mark it claimable.
    assert state.daily_points == 900
    assert state.total_points == 1234
    assert state.rewards[0].claimable is False


def test_reward_without_points_threshold_is_not_claimable():
    from hw_genie.commands.power_tournament import parse_state

    payload = dict(STATE_PAYLOAD)
    payload["currentDayRewards"] = [
        {"id": 9, "reward": {"coin": {"1": 1}}, "isFarmed": 0}
    ]
    state = parse_state(payload, now=1_700_050_000)
    assert state.rewards[0].points is None
    assert state.rewards[0].claimable is False


def test_missing_daily_and_total_points_make_rewards_unclaimable():
    from hw_genie.commands.power_tournament import parse_state

    payload = dict(STATE_PAYLOAD)
    payload.pop("currentDailyPoints")
    payload.pop("currentTournamentPoints")
    state = parse_state(payload, now=1_700_050_000)
    assert state.daily_points is None
    assert state.total_points is None
    assert state.rewards
    assert all(reward.claimable is False for reward in state.rewards)


def test_claimable_uses_total_only_when_daily_points_missing():
    from hw_genie.commands.power_tournament import parse_state

    payload = dict(STATE_PAYLOAD)
    payload.pop("currentDailyPoints")
    payload["currentDayRewards"] = [
        {"id": 9, "points": 1000, "reward": {"coin": {"1": 1}}, "isFarmed": 0}
    ]
    state = parse_state(payload, now=1_700_050_000)
    # 1000 <= known total 1234: the documented total-points fallback.
    assert state.rewards[0].claimable is True


def test_parse_group_info_rejects_unexpected_shape():
    from hw_genie.commands.power_tournament import (
        PowerTournamentReadError,
        parse_group_info,
    )

    for payload in ({"foo": 1}, "bad", {}, {"points": []}):
        with pytest.raises(PowerTournamentReadError):
            parse_group_info(payload, power_field="titansPower")


def test_parse_group_info_empty_points_is_valid():
    from hw_genie.commands.power_tournament import parse_group_info

    assert parse_group_info({"points": {}}, power_field="titansPower") == []


def test_parse_group_info_orders_and_marks_self():
    from hw_genie.commands.power_tournament import parse_group_info

    entries = parse_group_info(
        GROUP_PAYLOAD, power_field="titansPower", own_user_id="2"
    )
    # Competition ranking: Alice/Carol tie at 900 -> 1, 1, then 500 -> 3.
    assert [entry.place for entry in entries] == [1, 1, 3, 4]
    assert [entry.user_id for entry in entries] == ["2", "3", "1", "4"]
    assert entries[0].points == 900
    assert entries[0].is_self is True
    assert entries[2].last_login_ts == 1_700_000_000


def test_run_dry_run_sends_only_reads(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, mock_call = mock_client
    mock_call.side_effect = [_state_res(), _group_res(), _user_res()]
    result = run_power_tournament(client, account_alias="Alice", dry_run=True)

    names = [call.args[0]["calls"][0]["name"] for call in mock_call.call_args_list]
    assert ApiAction.POWER_TOURNAMENT_DAILY_FARM not in names
    assert result.claims == []
    assert result.state is not None
    assert result.own_place == 1
    assert len(result.ranking) == 4


def test_run_claims_claimable_ids_only(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, mock_call = mock_client
    mock_call.side_effect = [
        _state_res(),
        _res({}),
        _res({}, status=ResponseStatus.ERROR, error_name="NotAvailable"),
        _group_res(),
        _user_res(),
    ]
    result = run_power_tournament(client, account_alias="Alice")

    claim_calls = [
        (call.args[0]["calls"][0], call.kwargs)
        for call in mock_call.call_args_list
        if call.args[0]["calls"][0]["name"] == ApiAction.POWER_TOURNAMENT_DAILY_FARM
    ]
    assert [call["args"]["id"] for call, _ in claim_calls] == [5, 6]
    assert all(kwargs["max_retries"] == 0 for _, kwargs in claim_calls)
    assert [claim.status for claim in result.claims] == [
        ResponseStatus.SUCCESS,
        ResponseStatus.SKIPPED,
    ]
    assert result.failed_claims == 0


def test_run_malformed_state_sets_error(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, mock_call = mock_client
    mock_call.return_value = _res({"foo": 1})
    result = run_power_tournament(client, account_alias="Alice")

    assert result.error is not None
    assert "unexpected getState response shape" in result.error
    assert result.state is None
    assert result.claims == []
    assert mock_call.call_count == 1


def test_run_unknown_claim_error_counts_failed(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, mock_call = mock_client
    # Only id 5 is claimable in this fixture if we shrink the reward list.
    payload = dict(STATE_PAYLOAD)
    payload["currentDayRewards"] = [STATE_PAYLOAD["currentDayRewards"][0]]
    mock_call.side_effect = [
        _res(payload),
        _res({}, status=ResponseStatus.ERROR, error_name="ServerBoom"),
        _group_res(),
        _user_res(),
    ]

    result = run_power_tournament(client, account_alias="Alice")
    assert result.failed_claims == 1
    assert result.claims[0].error == "ServerBoom"


def test_run_malformed_group_info_sets_ranking_error(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, mock_call = mock_client
    # getState succeeds with two claimable rewards, then getGroupInfo returns a
    # malformed success payload: this must not look like an empty ranking.
    mock_call.side_effect = [
        _state_res(),
        _res({}),
        _res({}),
        _res({"foo": 1}),
        _user_res(),
    ]
    result = run_power_tournament(client, account_alias="Alice")

    assert result.state is not None
    assert [claim.reward_id for claim in result.claims] == [5, 6]
    assert result.ranking_error is not None
    assert "unexpected getGroupInfo response shape" in result.ranking_error
    assert result.ranking == []
    assert mock_call.call_count == 5


def test_claim_rewards_sleeps_after_every_claim(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import TournamentReward, claim_rewards

    client, mock_call = mock_client
    mock_call.side_effect = [_res({}), _res({})]
    rewards = [
        TournamentReward(5, 300, True, False),
        TournamentReward(6, 800, True, False),
        TournamentReward(7, 1500, False, False),
    ]
    results = claim_rewards(client, rewards)

    assert len(results) == 2
    # One sleep per attempted claim, including the final one.
    assert mock_sleep.call_count == 2


def test_ranking_failure_keeps_state_and_claims(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, mock_call = mock_client
    mock_call.side_effect = [
        _state_res(),
        _res({}),
        _res({}),
        _res({}, status=ResponseStatus.ERROR, error_name="NotAvailable"),
    ]
    result = run_power_tournament(client, account_alias="Alice")

    assert result.state is not None
    assert result.failed_claims == 0
    assert result.ranking_error is not None
    assert result.ranking == []


def test_no_active_event_is_clean(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, mock_call = mock_client
    mock_call.return_value = _res(
        {}, status=ResponseStatus.ERROR, error_name="NotAvailable"
    )
    result = run_power_tournament(client, account_alias="Alice")

    assert result.state is None
    assert result.error is None
    assert result.claims == []
    assert mock_call.call_count == 1


def test_auth_error_propagates(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, mock_call = mock_client
    mock_call.side_effect = HWAuthError("expired")
    with pytest.raises(HWAuthError):
        run_power_tournament(client, account_alias="Alice")


def test_top_validation(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, _ = mock_client
    for value in (0, 51, True, "5"):
        with pytest.raises(ValueError):
            run_power_tournament(client, account_alias="Alice", top=value)


def test_render_shows_top_and_claims(capsys):
    from hw_genie.commands.power_tournament import (
        PowerTournamentResult,
        TournamentClaimResult,
        parse_group_info,
        parse_state,
        render_power_tournament,
    )

    state = parse_state(STATE_PAYLOAD, now=1_700_050_000)
    result = PowerTournamentResult(
        account="Alice",
        dry_run=False,
        state=state,
        ranking=parse_group_info(
            GROUP_PAYLOAD, power_field="titansPower", own_user_id="2"
        )[:5],
        claims=[
            TournamentClaimResult(5, ResponseStatus.SUCCESS),
            TournamentClaimResult(6, ResponseStatus.SKIPPED, "NotAvailable"),
        ],
        own_user_id="2",
        own_place=1,
    )
    render_power_tournament(result, now=1_700_050_000)
    out = capsys.readouterr().out
    assert "Power Tournament" in out
    assert "Type: Titan" in out
    assert "Alice: rank" in out
    assert "Last login" in out
    assert "UserID" not in out
    assert "Bob" in out
    assert "← you" in out
    assert "Rewards: received 3 / unreceived 1" in out


def test_render_dry_run(capsys):
    from hw_genie.commands.power_tournament import (
        PowerTournamentResult,
        parse_group_info,
        parse_state,
        render_power_tournament,
    )

    state = parse_state(STATE_PAYLOAD, now=1_700_050_000)
    result = PowerTournamentResult(
        account="Alice",
        dry_run=True,
        state=state,
        ranking=parse_group_info(
            GROUP_PAYLOAD, power_field="titansPower", own_user_id="2"
        ),
        own_user_id="2",
        own_place=1,
    )
    render_power_tournament(result, now=1_700_050_000)
    out = capsys.readouterr().out
    assert "Rewards: received 1 / unreceived 3 (claimable 2)" in out
    assert "Dry-run: rewards were not claimed." in out


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(line: str) -> str:
    return _ANSI_RE.sub("", line)


def test_clip_stops_at_column_boundary():
    from hw_genie.commands.power_tournament import _clip

    assert _clip("日本語", 3) == "日"
    clipped = _clip("日本語", 3)
    assert display_width(clipped, ambiguous_wide=False) == 2
    # Only one 1-column space is needed to fill a 3-column narrow cell.
    padded = pad(clipped, 3, ambiguous_wide=False)
    assert padded == "日 "
    assert len(padded) - len(clipped) == 1


def test_render_rank_table_aligns_names_across_scripts(capsys):
    from hw_genie.commands.power_tournament import (
        PowerTournamentResult,
        TournamentRankEntry,
        parse_state,
        render_power_tournament,
    )

    state = parse_state(STATE_PAYLOAD, now=1_700_050_000)
    names = ["Тест", "Bob", "日本語", "ДлинноеИмя", "⚡Storm"]
    result = PowerTournamentResult(
        account="Alice",
        dry_run=True,
        state=state,
        ranking=[
            TournamentRankEntry(1, "1", "Тест", 100, 1_700_000_000),
            TournamentRankEntry(2, "2", "Bob", 90, 1_700_000_000),
            TournamentRankEntry(3, "3", "日本語", 80, 1_700_000_000),
            TournamentRankEntry(4, "4", "ДлинноеИмя", 70, 1_700_000_000),
            TournamentRankEntry(5, "5", "⚡Storm", 60, 1_700_000_000),
        ],
    )
    render_power_tournament(result, now=1_700_050_000)
    lines = [_plain(line) for line in capsys.readouterr().out.splitlines()]

    header = next(line for line in lines if line.lstrip().startswith("#"))
    rule = next(line for line in lines if line.lstrip().startswith("---"))
    rows = [
        line
        for line in lines
        if line.startswith("  ") and line.lstrip()[:1].isdigit()
    ]
    assert len(rows) == 5

    # The Name column is data-driven (longest name, at least the header) under
    # the narrow-ambiguous policy, for Cyrillic, ASCII, CJK and emoji names.
    expected_name_width = max(
        display_width("Name", ambiguous_wide=False),
        *(display_width(name, ambiguous_wide=False) for name in names),
    )
    name_cells = [row.split(" | ")[1] for row in rows]
    assert all(
        display_width(cell, ambiguous_wide=False) == expected_name_width
        for cell in name_cells
    )
    # Cyrillic rows: raw len equals the visual width, proving A counted as 1.
    for cell in name_cells[:1] + name_cells[3:4]:
        assert len(cell) == expected_name_width
        # The old default (A=2) under-pads these cells, so this width differs.
        assert display_width(cell) != expected_name_width

    # Header, rule and data rows share one visual width.
    assert len({display_width(line, ambiguous_wide=False) for line in [header, rule, *rows]}) == 1


def test_render_rank_table_marks_self_row_without_breaking_alignment(capsys):
    from hw_genie.commands.power_tournament import (
        PowerTournamentResult,
        TournamentRankEntry,
        parse_state,
        render_power_tournament,
    )

    state = parse_state(STATE_PAYLOAD, now=1_700_050_000)
    result = PowerTournamentResult(
        account="Alice",
        dry_run=True,
        state=state,
        ranking=[
            TournamentRankEntry(1, "1", "Тест", 100, 1_700_000_000),
            TournamentRankEntry(2, "2", "Пример", 90, 1_700_000_000, is_self=True),
        ],
    )
    render_power_tournament(result, now=1_700_050_000)
    lines = [_plain(line) for line in capsys.readouterr().out.splitlines()]
    rows = [
        line
        for line in lines
        if line.startswith("  ") and line.lstrip()[:1].isdigit()
    ]
    assert len(rows) == 2

    self_rows = [row for row in rows if "← you" in row]
    assert len(self_rows) == 1
    marked = self_rows[0]
    assert marked.rstrip().endswith("← you")
    # Excluding the marker, the self row matches the other row's width.
    widths = {display_width(row.split(" ← you")[0], ambiguous_wide=False) for row in rows}
    assert len(widths) == 1
    # Dynamic Name width: the longest of "Name", "Тест" (4) and "Пример" (6).
    assert all(display_width(row.split(" | ")[1], ambiguous_wide=False) == 6 for row in rows)


def test_render_rank_table_widths_follow_widest_values(capsys):
    from hw_genie.commands.power_tournament import (
        PowerTournamentResult,
        TournamentMemberStats,
        TournamentRankEntry,
        parse_state,
        render_power_tournament,
    )

    state = parse_state(STATE_PAYLOAD, now=1_700_050_000)
    result = PowerTournamentResult(
        account="Alice",
        dry_run=True,
        state=state,
        ranking=[
            TournamentRankEntry(
                1,
                "1",
                "Bob",
                104_500,
                1_700_000_000,
                server_id="161",
                power=999_999,
                stats=TournamentMemberStats(
                    activity_today=25_000,
                    activity_week=123_500,
                    titanite_today=1,
                    titanite_week=20,
                    prestige_today=3,
                    prestige_week=30,
                ),
            ),
            TournamentRankEntry(2, "2", "Carol", 90, 1_700_000_000, server_id="9"),
        ],
    )
    render_power_tournament(result, now=1_700_050_000)
    lines = [_plain(line) for line in capsys.readouterr().out.splitlines()]

    header = next(line for line in lines if line.lstrip().startswith("#"))
    rule = next(line for line in lines if line.lstrip().startswith("---"))
    rows = [
        line
        for line in lines
        if line.startswith("  ") and line.lstrip()[:1].isdigit()
    ]
    assert len(rows) == 2
    row = rows[0]
    # 999_999 formats to 1000.0K (7 chars, wider than the old fixed 6).
    assert "1000.0K" in row
    # 25_000/123_500 formats to 25.0K/123.5K (12 chars, wider than the old 11).
    assert "25.0K/123.5K" in row

    cells = row.split(" | ")
    assert display_width(cells[3], ambiguous_wide=False) == 7
    assert display_width(cells[5], ambiguous_wide=False) == 12

    # Shorter values in the second row are padded to the same widths, and the
    # header, rule and every data row share one visual width.
    widths = {
        display_width(line, ambiguous_wide=False) for line in [header, rule, *rows]
    }
    assert len(widths) == 1


def test_render_rank_table_clips_long_names_to_18(capsys):
    from hw_genie.commands.power_tournament import (
        PowerTournamentResult,
        TournamentRankEntry,
        parse_state,
        render_power_tournament,
    )

    state = parse_state(STATE_PAYLOAD, now=1_700_050_000)
    result = PowerTournamentResult(
        account="Alice",
        dry_run=True,
        state=state,
        ranking=[TournamentRankEntry(1, "1", "X" * 40, 100, 1_700_000_000)],
    )
    render_power_tournament(result, now=1_700_050_000)
    lines = [_plain(line) for line in capsys.readouterr().out.splitlines()]
    row = next(
        line
        for line in lines
        if line.startswith("  ") and line.lstrip()[:1].isdigit()
    )
    name_cell = row.split(" | ")[1]
    assert display_width(name_cell, ambiguous_wide=False) == 18
    assert name_cell.strip() == "X" * 18


def test_parse_group_info_reads_server_power_and_member_stats():
    from hw_genie.commands.power_tournament import parse_group_info

    response = {
        "users": {
            "1": {
                "id": "1",
                "name": "Bob",
                "serverId": "161",
                "clanId": "42",
                "titansPower": "1234567",
                "todayActivity": 100,
                "activitySum": 2000,
                "todayDungeonActivity": 10,
                "dungeonActivitySum": 300,
                "todayPrestige": 5,
                "prestigeSum": 500,
            }
        },
        "points": {"1": 100},
        "rewards": [],
    }
    entry = parse_group_info(response, power_field="titansPower")[0]
    assert entry.server_id == "161"
    assert entry.clan_id == "42"
    assert entry.power == 1_234_567
    assert entry.stats is not None
    assert entry.stats.activity_today == 100
    assert entry.stats.activity_week == 2000
    assert entry.stats.titanite_today == 10
    assert entry.stats.titanite_week == 300
    assert entry.stats.prestige_today == 5
    assert entry.stats.prestige_week == 500

    hero = parse_group_info(
        {
            "users": {
                "1": {
                    "id": "1",
                    "name": "Bob",
                    "heroesPower": "100",
                    "titansPower": "50",
                }
            },
            "points": {"1": 1},
        },
        power_field="heroesPower",
    )[0]
    assert hero.power == 100


def test_fetch_member_power_single(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import (
        TournamentRankEntry,
        fetch_member_power,
    )

    client, mock_call = mock_client
    mock_call.return_value = _res(
        {
            "items": [
                {
                    "user": {
                        "id": "1",
                        "heroesPower": "100",
                        "titansPower": "50",
                    }
                }
            ]
        }
    )
    entries = [TournamentRankEntry(1, "1", "Bob", 100)]

    powers, error = fetch_member_power(client, entries, "titansPower")

    assert error is None
    assert powers["1"] == 50
    payload = mock_call.call_args.args[0]
    assert payload["calls"][0]["name"] == "clanInvites_search"
    assert payload["calls"][0]["args"]["id"] == "1"


def test_fetch_member_power_batches(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import (
        TournamentRankEntry,
        fetch_member_power,
    )

    client, mock_call = mock_client
    res = MagicMock()
    res.is_success = True
    res.error_name = None
    res.detail = {
        "power_1": {
            "result": {
                "response": {
                    "items": [
                        {
                            "user": {
                                "id": "1",
                                "heroesPower": "100",
                                "titansPower": "50",
                            }
                        }
                    ]
                }
            }
        },
        "power_2": {
            "result": {
                "response": {
                    "items": [
                        {
                            "user": {
                                "id": "2",
                                "heroesPower": "200",
                                "titansPower": "100",
                            }
                        }
                    ]
                }
            }
        },
    }
    mock_call.return_value = res
    entries = [
        TournamentRankEntry(1, "1", "Bob", 100),
        TournamentRankEntry(2, "2", "Carol", 90),
    ]

    powers, error = fetch_member_power(client, entries, "titansPower")

    assert error is None
    assert powers == {"1": 50, "2": 100}
    assert mock_call.call_count == 1


def test_run_fetches_power_from_clan_invites_search(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, mock_call = mock_client
    group = {
        "users": {"1": {"id": "1", "name": "Bob"}},
        "points": {"1": 100},
        "rewards": [],
    }
    mock_call.side_effect = [
        _res(STATE_PAYLOAD),
        _res(group),
        _user_res("2"),
        _res(
            {
                "items": [
                    {
                        "user": {
                            "id": "1",
                            "heroesPower": "100",
                            "titansPower": "50",
                        }
                    }
                ]
            }
        ),
    ]

    result = run_power_tournament(
        client, account_alias="Alice", top=1, dry_run=True
    )

    assert result.ranking[0].power == 50
    assert result.enrichment_error is None


def test_fetch_member_power_single_warns_on_unexpected_payload(
    mock_client, mock_sleep
):
    from hw_genie.commands.power_tournament import (
        TournamentRankEntry,
        fetch_member_power,
    )

    client, mock_call = mock_client
    mock_call.return_value = _res(["unexpected"])

    powers, error = fetch_member_power(
        client, [TournamentRankEntry(1, "1", "Bob", 100)], "titansPower"
    )

    assert powers == {}
    assert error == "clanInvites_search returned an unexpected payload"


def test_fetch_member_power_empty_items_is_not_a_warning(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import (
        TournamentRankEntry,
        fetch_member_power,
    )

    client, mock_call = mock_client
    mock_call.return_value = _res({"items": []})

    powers, error = fetch_member_power(
        client, [TournamentRankEntry(1, "9", "Bob", 100)], "titansPower"
    )

    # An empty result means "user not found", which is not a broken payload.
    assert powers == {}
    assert error is None


def test_fetch_member_power_promotes_batched_auth_error(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import (
        TournamentRankEntry,
        fetch_member_power,
    )

    client, mock_call = mock_client
    res = MagicMock()
    res.is_success = True
    res.error_name = None
    res.detail = {
        "power_1": {"error": {"name": "auth"}},
        "power_2": {
            "result": {
                "response": {
                    "items": [{"user": {"id": "2", "titansPower": "50"}}]
                }
            }
        },
    }
    mock_call.return_value = res
    entries = [
        TournamentRankEntry(1, "1", "Bob", 100),
        TournamentRankEntry(2, "2", "Carol", 90),
    ]

    with pytest.raises(HWAuthError):
        fetch_member_power(client, entries, "titansPower")


def test_run_hero_event_uses_type_specific_power(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    hero_state = dict(STATE_PAYLOAD, type="heroes")
    group = {
        "users": {"1": {"id": "1", "name": "Bob"}},
        "points": {"1": 100},
        "rewards": [],
    }
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res(hero_state),
        _res(group),
        _user_res("2"),
        _res(
            {
                "items": [
                    {
                        "user": {
                            "id": "1",
                            "heroesPower": "100",
                            "titansPower": "50",
                        }
                    }
                ]
            }
        ),
    ]

    result = run_power_tournament(client, account_alias="Alice", top=1, dry_run=True)

    assert result.state is not None
    assert result.state.type_label == "Hero"
    # Hero events use heroesPower only: never the Titan value and never a sum.
    assert result.ranking[0].power == 100
    assert result.enrichment_error is None


def test_run_unknown_type_skips_power_with_warning(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, mock_call = mock_client
    unknown_state = dict(STATE_PAYLOAD, type="mystery")
    group = {
        "users": {
            "1": {
                "id": "1",
                "name": "Bob",
                "heroesPower": 9,
                "titansPower": 7,
            }
        },
        "points": {"1": 100},
        "rewards": [],
    }
    mock_call.side_effect = [_res(unknown_state), _res(group), _user_res("2")]

    result = run_power_tournament(client, account_alias="Alice", top=1, dry_run=True)

    # No power field: profile power is ignored and no search call is made.
    assert result.ranking[0].power is None
    assert result.enrichment_error is not None
    assert "unknown tournament type 'mystery'; power skipped" in result.enrichment_error
    names = [call.args[0]["calls"][0]["name"] for call in mock_call.call_args_list]
    assert ApiAction.CLAN_INVITES_SEARCH not in names


def test_run_power_failure_warns_but_keeps_ranking(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament
    from hw_genie.main import _run_log_account_failure

    client, mock_call = mock_client
    group = {
        "users": {"1": {"id": "1", "name": "Bob"}},
        "points": {"1": 100},
        "rewards": [],
    }
    mock_call.side_effect = [
        _state_res(),
        _res(group),
        _user_res("2"),
        _res({}, status=ResponseStatus.ERROR, error_name="NotAvailable"),
    ]

    result = run_power_tournament(client, account_alias="Alice", top=1, dry_run=True)

    # A power failure is a warning only: the ranking survives and the run is ok.
    assert [entry.name for entry in result.ranking] == ["Bob"]
    assert result.ranking[0].power is None
    assert result.enrichment_error is not None
    assert "clanInvites_search failed" in result.enrichment_error
    assert _run_log_account_failure("power-tournament", result, None) is None


def test_run_combines_power_and_clan_errors(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, mock_call = mock_client
    group = {
        "users": {"1": {"id": "1", "name": "Bob", "clanId": "42"}},
        "points": {"1": 100},
        "rewards": [],
    }
    mock_call.side_effect = [
        _state_res(),
        _res(group),
        _user_res("2"),
        _res({}, status=ResponseStatus.ERROR, error_name="NotAvailable"),
        _res({}, status=ResponseStatus.ERROR, error_name="NotAvailable"),
    ]

    result = run_power_tournament(client, account_alias="Alice", top=1, dry_run=True)

    assert [entry.name for entry in result.ranking] == ["Bob"]
    assert result.enrichment_error is not None
    assert "clanInvites_search failed" in result.enrichment_error
    assert "clanGetInfo failed" in result.enrichment_error


def test_fetch_member_stats_fills_from_clan(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import (
        TournamentRankEntry,
        fetch_member_stats,
    )

    client, mock_call = mock_client
    mock_call.return_value = _res(
        {
            "membersStat": [
                {
                    "userId": "2",
                    "todayActivity": 10,
                    "activitySum": 100,
                    "todayDungeonActivity": 1,
                    "dungeonActivitySum": 20,
                    "todayPrestige": 3,
                    "prestigeSum": 30,
                }
            ]
        }
    )
    entries = [TournamentRankEntry(1, "2", "Bob", 100, clan_id="42")]

    stats, error = fetch_member_stats(client, entries)

    assert error is None
    assert stats["2"].activity_today == 10
    payload = mock_call.call_args.args[0]
    assert payload["calls"][0]["name"] == "clanGetInfo"
    assert payload["calls"][0]["args"] == {"clanId": "42"}


def test_fetch_member_stats_batches_multiple_clans(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import (
        TournamentRankEntry,
        fetch_member_stats,
    )

    client, mock_call = mock_client
    res = MagicMock()
    res.is_success = True
    res.error_name = None
    res.detail = {
        "clan_42": {
            "result": {
                "response": {
                    "membersStat": [{"userId": "2", "todayActivity": 10}]
                }
            }
        },
        "clan_43": {
            "result": {
                "response": {
                    "membersStat": [{"userId": "3", "todayActivity": 20}]
                }
            }
        },
    }
    mock_call.return_value = res
    entries = [
        TournamentRankEntry(1, "2", "Bob", 100, clan_id="42"),
        TournamentRankEntry(2, "3", "Carol", 90, clan_id="43"),
    ]

    stats, error = fetch_member_stats(client, entries)

    assert error is None
    assert stats["2"].activity_today == 10
    assert stats["3"].activity_today == 20
    assert mock_call.call_count == 1


def test_fetch_member_stats_uses_object_key_when_user_id_missing(
    mock_client, mock_sleep
):
    from hw_genie.commands.power_tournament import (
        TournamentRankEntry,
        fetch_member_stats,
    )

    client, mock_call = mock_client
    # Object form keyed by userId where the value omits userId entirely.
    mock_call.return_value = _res(
        {"membersStat": {"2": {"todayActivity": 10, "activitySum": 100}}}
    )
    entries = [TournamentRankEntry(1, "2", "Bob", 100, clan_id="42")]

    stats, error = fetch_member_stats(client, entries)

    assert error is None
    assert stats["2"].activity_today == 10
    assert stats["2"].activity_week == 100


def test_fetch_member_stats_reports_per_clan_error(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import (
        TournamentRankEntry,
        fetch_member_stats,
    )

    client, mock_call = mock_client
    res = MagicMock()
    res.is_success = True
    res.error_name = None
    res.detail = {
        "clan_42": {"error": {"name": "NotAvailable"}},
        "clan_43": {
            "result": {
                "response": {
                    "membersStat": [{"userId": "3", "todayActivity": 20}]
                }
            }
        },
    }
    mock_call.return_value = res
    entries = [
        TournamentRankEntry(1, "2", "Bob", 100, clan_id="42"),
        TournamentRankEntry(2, "3", "Carol", 90, clan_id="43"),
    ]

    stats, error = fetch_member_stats(client, entries)

    assert error is not None
    assert "clan 42" in error
    assert "NotAvailable" in error
    assert "clan 43" not in error
    assert stats["3"].activity_today == 20
    assert "2" not in stats


def test_fetch_member_stats_accepts_positional_idents(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import (
        TournamentRankEntry,
        fetch_member_stats,
    )

    client, mock_call = mock_client
    res = MagicMock()
    res.is_success = True
    res.error_name = None
    res.detail = {
        "0": {
            "result": {
                "response": {
                    "membersStat": [{"userId": "2", "todayActivity": 10}]
                }
            }
        },
        "1": {
            "result": {
                "response": {
                    "membersStat": [{"userId": "3", "todayActivity": 20}]
                }
            }
        },
    }
    mock_call.return_value = res
    entries = [
        TournamentRankEntry(1, "2", "Bob", 100, clan_id="42"),
        TournamentRankEntry(2, "3", "Carol", 90, clan_id="43"),
    ]

    stats, error = fetch_member_stats(client, entries)

    assert error is None
    assert stats["2"].activity_today == 10
    assert stats["3"].activity_today == 20


def test_fetch_member_stats_propagates_auth_error(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import (
        TournamentRankEntry,
        fetch_member_stats,
    )

    client, mock_call = mock_client
    mock_call.side_effect = HWAuthError("expired")

    with pytest.raises(HWAuthError):
        fetch_member_stats(
            client, [TournamentRankEntry(1, "2", "Bob", 100, clan_id="42")]
        )


def test_run_merges_partial_profile_stats_with_clan(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, mock_call = mock_client
    group = {
        "users": {
            "1": {
                "id": "1",
                "name": "Bob",
                "serverId": "161",
                "clanId": "42",
                "titansPower": 1,
                "todayActivity": 1,
            }
        },
        "points": {"1": 100},
        "rewards": [],
    }
    mock_call.side_effect = [
        _res(STATE_PAYLOAD),
        _res(group),
        _user_res("2"),
        _res(
            {
                "membersStat": [
                    {
                        "userId": "1",
                        "todayActivity": 10,
                        "activitySum": 100,
                        "todayDungeonActivity": 2,
                        "dungeonActivitySum": 20,
                        "todayPrestige": 3,
                        "prestigeSum": 30,
                    }
                ]
            }
        ),
    ]

    result = run_power_tournament(
        client, account_alias="Alice", top=1, dry_run=True
    )

    stats = result.ranking[0].stats
    assert stats is not None
    # The profile value wins; the clan fills the fields the profile omitted.
    assert stats.activity_today == 1
    assert stats.activity_week == 100
    assert stats.titanite_today == 2
    assert stats.titanite_week == 20
    assert stats.prestige_today == 3
    assert stats.prestige_week == 30


def test_fetch_member_stats_promotes_batched_auth_error(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import (
        TournamentRankEntry,
        fetch_member_stats,
    )

    client, mock_call = mock_client
    res = MagicMock()
    res.is_success = True
    res.error_name = None
    res.detail = {
        "clan_42": {"error": {"name": "auth"}},
        "clan_43": {
            "result": {"response": {"membersStat": []}}
        },
    }
    mock_call.return_value = res
    entries = [
        TournamentRankEntry(1, "2", "Bob", 100, clan_id="42"),
        TournamentRankEntry(2, "3", "Carol", 90, clan_id="43"),
    ]

    with pytest.raises(HWAuthError):
        fetch_member_stats(client, entries)


def test_run_enriches_stats_from_clan(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, mock_call = mock_client
    group = {
        "users": {
            "1": {
                "id": "1",
                "name": "Bob",
                "serverId": "161",
                "clanId": "42",
                "titansPower": 1,
            }
        },
        "points": {"1": 100},
        "rewards": [],
    }
    mock_call.side_effect = [
        _res(STATE_PAYLOAD),
        _res(group),
        _user_res("2"),
        _res(
            {
                "membersStat": [
                    {"userId": "1", "todayActivity": 10, "activitySum": 100}
                ]
            }
        ),
    ]

    result = run_power_tournament(
        client, account_alias="Alice", top=1, dry_run=True
    )

    entry = result.ranking[0]
    assert entry.server_id == "161"
    assert entry.stats is not None
    assert entry.stats.activity_today == 10
    assert result.enrichment_error is None


def test_run_skips_clan_get_info_when_profiles_carry_stats(mock_client, mock_sleep):
    from hw_genie.commands.power_tournament import run_power_tournament

    client, mock_call = mock_client
    group = {
        "users": {
            "1": {
                "id": "1",
                "name": "Bob",
                "clanId": "42",
                "titansPower": 1,
                "todayActivity": 10,
                "activitySum": 100,
                "todayDungeonActivity": 1,
                "dungeonActivitySum": 20,
                "todayPrestige": 3,
                "prestigeSum": 30,
            }
        },
        "points": {"1": 100},
        "rewards": [],
    }
    mock_call.side_effect = [_state_res(), _res(group), _user_res("2")]

    result = run_power_tournament(client, account_alias="Alice", top=1, dry_run=True)

    names = [call.args[0]["calls"][0]["name"] for call in mock_call.call_args_list]
    assert ApiAction.CLAN_GET_INFO not in names
    assert result.ranking[0].stats is not None
    assert result.ranking[0].stats.activity_today == 10
    assert result.enrichment_error is None


def test_run_enrichment_error_warns_but_keeps_ranking(mock_client, mock_sleep, capsys):
    from hw_genie.commands.power_tournament import (
        render_power_tournament,
        run_power_tournament,
    )
    from hw_genie.main import _run_log_account_failure

    client, mock_call = mock_client
    group = {
        "users": {"1": {"id": "1", "name": "Bob", "clanId": "42", "titansPower": 1}},
        "points": {"1": 100},
        "rewards": [],
    }
    mock_call.side_effect = [
        _state_res(),
        _res(group),
        _user_res("2"),
        _res({}, status=ResponseStatus.ERROR, error_name="NotAvailable"),
    ]

    result = run_power_tournament(client, account_alias="Alice", top=1, dry_run=True)

    # A stats failure is a warning only: the ranking survives and the run is ok.
    assert [entry.name for entry in result.ranking] == ["Bob"]
    assert result.enrichment_error is not None
    assert "clanGetInfo failed" in result.enrichment_error
    assert _run_log_account_failure("power-tournament", result, None) is None

    render_power_tournament(result, now=1_700_050_000)
    out = capsys.readouterr().out
    assert "Member info unavailable: clanGetInfo failed" in out


def test_render_shows_stats_power_and_server(capsys):
    from hw_genie.commands.power_tournament import (
        PowerTournamentResult,
        TournamentMemberStats,
        TournamentRankEntry,
        parse_state,
        render_power_tournament,
    )

    state = parse_state(STATE_PAYLOAD, now=1_700_050_000)
    result = PowerTournamentResult(
        account="Alice",
        dry_run=True,
        state=state,
        ranking=[
            TournamentRankEntry(
                1,
                "1",
                "Bob",
                100,
                1_700_000_000,
                server_id="161",
                power=1_234_567,
                stats=TournamentMemberStats(
                    activity_today=10,
                    activity_week=100,
                    titanite_today=1,
                    titanite_week=20,
                    prestige_today=3,
                    prestige_week=30,
                ),
            )
        ],
    )
    render_power_tournament(result, now=1_700_050_000)
    out = capsys.readouterr().out

    assert "Act T/W" in out
    assert "Tit T/W" in out
    assert "Pre T/W" in out
    assert "161" in out
    assert "1.2M" in out
    assert "10/100" in out
    assert "1/20" in out
    assert "3/30" in out


def test_render_shows_enrichment_error_as_warning(capsys):
    from hw_genie.commands.power_tournament import (
        PowerTournamentResult,
        TournamentRankEntry,
        parse_state,
        render_power_tournament,
    )

    state = parse_state(STATE_PAYLOAD, now=1_700_050_000)
    result = PowerTournamentResult(
        account="Alice",
        dry_run=True,
        state=state,
        ranking=[TournamentRankEntry(1, "1", "Bob", 100, 1_700_000_000)],
        own_user_id="1",
        own_place=1,
        enrichment_error="clan 42: NotAvailable",
    )
    render_power_tournament(result, now=1_700_050_000)
    out = capsys.readouterr().out

    assert "Member info unavailable: clan 42: NotAvailable" in out
    assert "Ranking unavailable" not in out
    assert "Rewards:" in out


def test_reward_counts_do_not_treat_limit_reached_as_received():
    from hw_genie.commands.power_tournament import (
        PowerTournamentResult,
        TournamentClaimResult,
        _reward_counts,
        parse_state,
    )

    state = parse_state(STATE_PAYLOAD, now=1_700_050_000)
    result = PowerTournamentResult(
        account="Alice",
        dry_run=False,
        state=state,
        claims=[
            TournamentClaimResult(5, ResponseStatus.SUCCESS),
            TournamentClaimResult(6, ResponseStatus.SKIPPED, "limitReached"),
        ],
    )
    # farmed (id 7) + success (id 5) = 2 received; id 6 and id 8 remain unreceived.
    assert _reward_counts(result) == (2, 2, 0)


def test_render_reports_failed_claim_count(capsys):
    from hw_genie.commands.power_tournament import (
        PowerTournamentResult,
        TournamentClaimResult,
        parse_state,
        render_power_tournament,
    )

    state = parse_state(STATE_PAYLOAD, now=1_700_050_000)
    result = PowerTournamentResult(
        account="Alice",
        dry_run=False,
        state=state,
        claims=[
            TournamentClaimResult(5, ResponseStatus.ERROR, "ServerBoom"),
            TournamentClaimResult(6, ResponseStatus.ERROR, "ServerBoom"),
        ],
    )
    render_power_tournament(result, now=1_700_050_000)
    out = capsys.readouterr().out
    assert "Rewards: received 1 / unreceived 3 (failed 2)" in out


def test_cli_registered(monkeypatch, capsys):
    import sys

    import hw_genie.main as main_mod

    monkeypatch.setattr(sys, "argv", ["hw-genie", "power-tournament", "--help"])
    with pytest.raises(SystemExit) as excinfo:
        main_mod.main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--dry-run" in out
    assert "--top" in out


def test_multi_help_lists_power_tournament(monkeypatch, capsys):
    import sys

    import hw_genie.main as main_mod

    # A wide terminal keeps argparse from breaking the long mode list mid-word,
    # so the assertion below can pin the exact sentence.
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(sys, "argv", ["hw-genie", "multi", "--help"])
    with pytest.raises(SystemExit) as excinfo:
        main_mod.main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "power-tournament" in out
    # The --dry-run help must list every mode the gate accepts, not a stale
    # subset (power-tournament / asgard-raid were missing before).
    flat = " ".join(out.split())
    assert (
        "dry-run Show the execution plan without running anything "
        "(quests/consumable/seers-game/power-tournament/gift-up/gift-reset/"
        "challenge/asgard-raid modes only)" in flat
    )


def test_run_log_account_failure_power_tournament():
    from hw_genie.commands.power_tournament import (
        PowerTournamentResult,
        TournamentClaimResult,
    )
    from hw_genie.main import _run_log_account_failure

    ok = PowerTournamentResult(account="Alice", dry_run=False)
    assert _run_log_account_failure("power-tournament", ok, None) is None

    failed = PowerTournamentResult(
        account="Alice",
        dry_run=False,
        claims=[
            TournamentClaimResult(5, ResponseStatus.ERROR, "ServerBoom"),
        ],
    )
    assert (
        _run_log_account_failure("power-tournament", failed, None)
        == "1 reward claim(s) failed"
    )

    ranking = PowerTournamentResult(
        account="Alice", dry_run=False, ranking_error="boom"
    )
    assert (
        _run_log_account_failure("power-tournament", ranking, None)
        == "ranking failed: boom"
    )

    errored = PowerTournamentResult(
        account="Alice", dry_run=False, error="read failed"
    )
    assert (
        _run_log_account_failure("power-tournament", errored, None) == "read failed"
    )

    assert (
        _run_log_account_failure("power-tournament", None, None)
        == "power-tournament result unavailable"
    )


def test_runner_routine_and_summary(mock_client, mock_sleep, capsys):
    from hw_genie.runner import power_tournament_routine, summarize_power_tournament

    client, mock_call = mock_client
    mock_call.side_effect = [
        _state_res(),
        _res({}),
        _res({}),
        _group_res(),
        _user_res(),
    ]
    routine = power_tournament_routine(top=5)
    result = routine(client, "Alice")
    failed = summarize_power_tournament([("Alice", (result, None))])

    out = capsys.readouterr().out
    assert failed == 0
    assert "Power Tournament" in out
    assert "Multi power-tournament summary" in out


def test_summarize_counts_ranking_failure(mock_client, mock_sleep, capsys):
    from hw_genie.runner import summarize_power_tournament

    client, mock_call = mock_client
    mock_call.side_effect = [
        _state_res(),
        _res({}),
        _res({}),
        _res({}, status=ResponseStatus.ERROR, error_name="NotAvailable"),
    ]
    from hw_genie.commands.power_tournament import run_power_tournament

    result = run_power_tournament(client, account_alias="Alice")
    failed = summarize_power_tournament([("Alice", (result, None))])

    out = capsys.readouterr().out
    assert failed == 1
    assert "Ranking unavailable" in out
