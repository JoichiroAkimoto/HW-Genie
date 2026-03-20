from hw_genie.core.client import Emojis, Messages, ResponseStatus


def run_item_raid(client_or_headers, payload_template, max_iterations=9999):
    if isinstance(client_or_headers, dict):
        from hw_genie.core.client import HWClient

        client = HWClient(client_or_headers)
    else:
        client = client_or_headers

    print(f"\n{Emojis.START}Starting Item Raid (Max: {max_iterations})...", flush=True)

    success_count = 0
    for i in range(max_iterations):
        print(f"{Emojis.STEP}Iteration {i + 1}: Executing Request...", end=" ", flush=True)

        payload = client.prepare_item_payload(payload_template)
        res = client.call(payload)

        if res.is_success:
            print(f"{Emojis.SUCCESS}Success", flush=True)
            success_count += 1
        elif res.status == ResponseStatus.AUTH_ERROR:
            print(f"{Emojis.ERROR}{Messages.AUTH_ERROR}", flush=True)
            break
        elif res.error_name in ["notEnoughStamina", "limitReached"]:
            print(f"{Emojis.WARNING}Stopping: {res.error_name}", flush=True)
            break
        else:
            print(f"{Emojis.ERROR}Failed ({res.error_name})", flush=True)
            break

        client.sleep()

    print(f"\n{Emojis.FINISH}Item Raid Completed. {success_count} successful raids.", flush=True)
