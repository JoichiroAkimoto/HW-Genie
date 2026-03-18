from hw_genie.core.client import Emojis, ResponseStatus


def run_item_raid(client_or_headers, payload_template, max_iterations=10, times=None):
    if isinstance(client_or_headers, dict):
        from hw_genie.core.client import HWClient

        client = HWClient(client_or_headers)
    else:
        client = client_or_headers
    times = times or max_iterations
    print(f"\n{Emojis.START}Starting Item Raid for {times} times...", flush=True)

    for i in range(times):
        print(f"{Emojis.STEP}Raid {i + 1}/{times}...", end=" ", flush=True)

        payload = client.prepare_item_payload(payload_template)
        res = client.call(payload)

        if res.is_success:
            print(f"{Emojis.SUCCESS}Success", flush=True)
        elif res.status == ResponseStatus.AUTH_ERROR:
            print(f"{Emojis.ERROR}{Emojis.AUTH_MSG}", flush=True)
            return
        else:
            print(f"{Emojis.ERROR}Failed ({res.error_name})", flush=True)
            break

        if i < times - 1:
            client.sleep()

    print(f"\n{Emojis.FINISH}Item Raid Completed.", flush=True)
