from dataclasses import dataclass
from typing import Any

from hw_genie.core.client import (
    Emojis,
    ErrorName,
    ResponseStatus,
)
from hw_genie.core.utils import print_player_status


# デフォルトのミッションリスト
DEFAULT_HERO_MISSION_IDS = [
    1,
    5,
    10,
    11,
    16,
    20,
    25,
    26,
    31,
    35,
    40,
    41,
    46,
    50,
    55,
    57,
    61,
    65,
    70,
    72,
    76,
    80,
    85,
    86,
    91,
    95,
    100,
    101,
    106,
    110,
    115,
    116,
    121,
    125,
    130,
    131,
    136,
    140,
    146,
    151,
    155,
    161,
    166,
    170,
    177,
    181,
    185,
    193,
    198,
    203,
    204,
    205,
    209,
    214,
]


@dataclass
class MissionResult:
    id: int
    status: ResponseStatus
    name: str | None = None


def is_stamina_error(error_name: str | None, detail: Any) -> bool:
    if error_name == ErrorName.NOT_ENOUGH_STAMINA:
        return True
    if error_name == ErrorName.NOT_ENOUGH:
        desc = detail.get("description", "") if isinstance(detail, dict) else str(detail)
        if "Has" in desc and "need" in desc:
            return True
        if "Refillable" in desc:
            return True
    return False


def is_limit_reached_error(error_name: str | None, detail: Any) -> bool:
    if error_name == ErrorName.LIMIT_REACHED:
        return True
    if error_name == ErrorName.NOT_ENOUGH:
        desc = detail.get("description", "") if isinstance(detail, dict) else str(detail)
        if "Tries" in desc:
            return True
    return False


def run_hero_raid(client_or_headers, mission_ids: list[int] | int | None = None, times: int = 3, allow_recovery: bool = True):
    if isinstance(client_or_headers, dict):
        from hw_genie.core.client import HWClient

        client = HWClient(client_or_headers)
    else:
        client = client_or_headers

    # mission_ids が None または空リストの場合、デフォルトリストを使用
    if mission_ids is None or (isinstance(mission_ids, list) and len(mission_ids) == 0):
        mission_ids = DEFAULT_HERO_MISSION_IDS

    if isinstance(mission_ids, int):
        mission_ids = [mission_ids]

    results: list[MissionResult] = []
    total_recovery_count = 0

    print(f"\n{Emojis.STEP}Executing Hero Raids", flush=True)

    # 実行前に各ミッションのクリア状況を取得してスキップ判定
    mission_status = {}
    print(f"{Emojis.STEP}Checking mission status...", flush=True)
    status_res = client.mission_get_all()
    if status_res.is_success and status_res.detail:
        try:
            missions = status_res.detail.get("response", [])
            if isinstance(missions, list):
                for m in missions:
                    if "id" in m and "triesSpent" in m:
                        mission_status[m["id"]] = m["triesSpent"]
        except Exception:
            pass

    for m_id in mission_ids:
        tries_spent = mission_status.get(m_id, 0)
        if tries_spent > 0:
            print(f"  {Emojis.INFO}Skipping Mission ID: {m_id} (Already completed today)", flush=True)
            results.append(MissionResult(id=m_id, status=ResponseStatus.SKIPPED))
            continue

        payload = client.build_mission_payload(m_id, times)
        stamina_recovered_for_this_mission = False

        while True:
            print(f"{Emojis.STEP}Executing Raid for Mission ID: {m_id}...", flush=True)
            res = client.call(payload)

            if not res.is_success:
                error_name = res.error_name
                detail = res.detail

                if is_stamina_error(error_name, detail):
                    if not allow_recovery:
                        print(f"  {Emojis.ERROR}Stamina deficiency detected.", flush=True)
                        results.append(MissionResult(id=m_id, status=ResponseStatus.STAMINA_ERROR))
                        break
                    if stamina_recovered_for_this_mission:
                        print(f"  {Emojis.ERROR}Continuous stamina deficiency. Stopping for safety.", flush=True)
                        results.append(MissionResult(id=m_id, status=ResponseStatus.STAMINA_ERROR))
                        break
                    print(f"  {Emojis.RECOVERY}Not enough stamina. Recovering...", flush=True)
                    recovery_res = client.recover_stamina(lib_id=17, amount=1)
                    if recovery_res.is_success:
                        print(f"  {Emojis.RECOVERY}Stamina recovered! Retrying...", flush=True)
                        stamina_recovered_for_this_mission = True
                        total_recovery_count += 1
                        client.sleep()
                        continue
                    else:
                        print(f"  {Emojis.ERROR}Recovery failed.", flush=True)
                        results.append(MissionResult(id=m_id, status=ResponseStatus.STAMINA_ERROR))
                        break

                if is_limit_reached_error(error_name, detail):
                    print(f"  Result: {Emojis.WARNING}Daily limit reached", flush=True)
                    results.append(MissionResult(id=m_id, status=ResponseStatus.LIMIT_REACHED))
                    break
                else:
                    print(f"  Result: {Emojis.ERROR}Error - {error_name}", flush=True)
                    results.append(MissionResult(id=m_id, status=ResponseStatus.ERROR, name=error_name))
                    break
            else:
                print(f"  Result: {Emojis.SUCCESS}Success", flush=True)
                results.append(MissionResult(id=m_id, status=ResponseStatus.SUCCESS))
                break
        client.sleep()

    print(f"\n{Emojis.STEP}Exchanging Soul Stones", flush=True)
    ex_res = client.exchange_stones()

    # Summary
    success_ids = [r.id for r in results if r.status == ResponseStatus.SUCCESS]
    limit_ids = [r.id for r in results if r.status == ResponseStatus.LIMIT_REACHED]
    skipped_ids = [r.id for r in results if r.status == ResponseStatus.SKIPPED]
    error_ids = [r.id for r in results if r.status not in [ResponseStatus.SUCCESS, ResponseStatus.LIMIT_REACHED, ResponseStatus.SKIPPED]]

    print(f"\n{Emojis.FINISH}--- Hero Raid Results Summary ---", flush=True)
    print(f"  {Emojis.SUCCESS}Successfully Completed: {len(success_ids)} missions", flush=True)
    print(f"  {Emojis.WARNING}Daily Limit Reached: {len(limit_ids)} missions", flush=True)
    if skipped_ids:
        print(f"  {Emojis.INFO}Skipped (Already Completed): {len(skipped_ids)} missions", flush=True)
    if ex_res.exchange_info:
        print(f"  {Emojis.SOUL_STONE}Soul Stones Exchanged: {ex_res.exchange_info.stones} stones", flush=True)
    if error_ids:
        print(f"  {Emojis.ERROR}Failed/Stopped: {len(error_ids)} missions", flush=True)
    print(f"  {Emojis.RECOVERY}Total Stamina Recoveries: {total_recovery_count} times", flush=True)

    # Status
    status = client.fetch_player_status()
    print_player_status(status)

    return results, total_recovery_count, ex_res.exchange_info
