#!/usr/bin/env bash
# Mock `uv run hw-genie ...` shim used only for VHS demo recording.
# Replays canned CLI output so the GIF never touches the real API or DB.
# Bash 3.2+ required (macOS default bash). Not POSIX sh compatible (uses bashisms).
# NOTE: Shop/quest output is mocked and may diverge from real CLI if upstream changes.
#
# Usage (from repo root):
#   vhs scripts/demo/demo.tape
# Requires: bash, sleep (coreutils)

set -euo pipefail

args=("$@")

cmd=""
sub=""
found=0
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ "${args[i]}" == "hw-genie" ]]; then
    found=1
    cmd="${args[$((i + 1))]:-}"
    sub="${args[$((i + 2))]:-}"
    break
  fi
done

if (( ! found )); then
  printf 'mock uv: unsupported invocation: %s\n' "$*" >&2
  exit 127
fi

say() { printf '%s\n' "$1"; sleep "${2:-0.14}"; }
pause() { sleep "$1"; }

case "$cmd:$sub" in
auth:--list)
  say $'\e[1;36mName    | Arena | GA | Gold   | Gems  | Mission | Energy | Updated (Asia/Tokyo) | Memo      \e[0m' 0.01
  say $'\e[2m--------------------------------------------------------------------------------------------\e[0m' 0.01
  say $'\e[1mArthur \e[0m | \e[33m1    \e[0m | \e[32m6 \e[0m | 148.2M | 12.5K | 179     | \e[31m436   \e[0m | 2026-08-24 10:27:48  | Main      ' 0.1
  say $'\e[1;2mMorgana\e[0m | \e[32m13   \e[0m | \e[33m1 \e[0m | \e[2m96.4M \e[0m | \e[2m8.1K \e[0m | \e[2m208    \e[0m | \e[2m178   \e[0m | \e[2m2026-08-24 10:27:48 \e[0m | \e[2mAlt       \e[0m' 0.1
  say $'\e[1mElyndra\e[0m | 16    | \e[32m8 \e[0m | 201.5M | 18.3K | 135     | 142    | 2026-08-24 10:27:48  | Event     ' 0.1
  say $'\e[1;2mKaito  \e[0m | \e[2m15   \e[0m | \e[2m18\e[0m | \e[2m42.1M \e[0m | \e[2m2.4K \e[0m | \e[2m118    \e[0m | \e[2m188   \e[0m | \e[2m2026-08-24 10:27:48 \e[0m | \e[2mLow-res   \e[0m' 0.1
  ;;
daily:*|daily:)
  say $'\n🚀 Starting Daily Routine...' 0.6

  say $'\n🔹 Executing Hero Raids' 0.3
  say '🔹 Checking mission status...' 0.5
  say $'  \e[2mℹ️  Skipping Mission ID: 76 (Already completed today)\e[0m' 0.18
  say $'  \e[2mℹ️  Skipping Mission ID: 116 (Already completed today)\e[0m' 0.18
  say $'  \e[2mℹ️  Skipping Mission ID: 193 (Already completed today)\e[0m' 0.18
  for m in 198 203 204 214; do
    say "🔹 Executing Raid for Mission ID: ${m}... ✅ Success" 0.22
  done
  pause 0.25
  say '🔹 Exchanging Soul Stones' 0.3
  say $'\n🏁 --- Hero Raid Results Summary ---' 0.3
  say '  ✅ Successfully Completed: 4 missions' 0.12
  say $'  \e[2mℹ️  Skipped (Already Completed): 3 missions\e[0m' 0.12
  say '  💎 Soul Stones Exchanged: 4 stones' 0.12
  say '  ⚡️ Total Stamina Recoveries: 0 times' 0.35

  say $'\n📊 --- Account Status ---' 0.15
  say '  👤 Name: Arthur (Lv.130)' 0.10
  say '  🏆 Arena Rank: 1' 0.10
  say '  👑 Grand Rank: 6' 0.10
  say $'  ⚡️ Energy: \e[31m212 / 190\e[0m \e[2m(over cap)\e[0m' 0.10
  say '  💰 Gold: 148.2M' 0.10
  say '  💎 Emeralds: 12.5K' 0.35

  say $'\n🔹 Executing Item Raids (Stamina Limit)...' 0.5
  say '🚀 Starting Item Raid (Max: 9999, Mission ID: 179)...' 0.4
  for it in 1 2; do
    say "🔹 Iteration ${it}: Executing Request... ✅ Success" 0.35
  done
  say $'\n🏁 Item Raid Completed. 20 successful raids.' 0.5

  say $'\n🔹 --- Fetching current shop status ---' 0.4
  say $'\n🔹 --- Purchasing Target Items ---' 0.3
  say '[1/5] Purchasing [Soul] Slot:2 -> consumable:12 (x3)...' 0.25
  say '  Result: ✅ Success' 0.16
  say '[2/5] Purchasing [Soul] Slot:3 -> gear:125 ...' 0.25
  say '  Result: ✅ Success' 0.16
  say '[3/5] Purchasing [Soul] Slot:4 -> fragmentScroll:200 (x10)...' 0.25
  say '  Result: ✅ Success' 0.16
  say '[4/5] Purchasing [Soul] Slot:5 -> fragmentScroll:198 (x10)...' 0.25
  say '  Result: ✅ Success' 0.16
  say '[5/5] Purchasing [Soul] Slot:6 -> fragmentScroll:254 (x5)...' 0.25
  say '  Result: ✅ Success' 0.16
  pause 0.2
  say $'\n🏁 --- Shopping Results Summary ---' 0.3
  say '  ✅ Total Hero Souls Purchased: 5' 0.35

  say $'\n📊 --- Account Status ---' 0.15
  say '  👤 Name: Arthur (Lv.130)' 0.10
  say '  🏆 Arena Rank: 1' 0.10
  say '  👑 Grand Rank: 6' 0.10
  say '  ⚡️ Energy: 42 / 190' 0.10
  say '  💰 Gold: 147.8M' 0.10
  say '  💎 Emeralds: 12.6K' 0.35

  say $'\n🔹 Executing 10028 Level up any Titan Artifact ...' 0.4
  say $'   \e[32m✅ 10028 Level up any Titan Artifact completed (step: titanArtifactLevelUp). Claiming reward...\e[0m' 0.35
  say '   🎁 Reward claimed for 10028 Level up any Titan Artifact' 0.45

  say $'\n🏁 Daily Routine Completed.' 0.3
  ;;
*)
  printf 'mock uv: unsupported hw-genie subcommand: %s %s\n' "$cmd" "$sub" >&2
  exit 1
  ;;
esac
