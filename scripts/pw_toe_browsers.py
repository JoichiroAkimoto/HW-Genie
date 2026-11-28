"""Launch one persistent, non-headless Chromium per ToE account.

Each context uses its own profile directory (cookies/localStorage isolated), so
you log in to each account once and it persists. The Dev userscript is injected
with ``context.add_init_script`` instead of Tampermonkey (the build has no GM_*
dependency and uses ``@grant none``), so every window talks to the local
auth-server and can claim its own live ToE jobs.

Keep the windows visible (not minimized). Then, in another terminal::

    uv run hw-genie multi toe --engine live --parallel 4

This environment needs the Playwright package (optional extra) and a resolvable
``libstdc++.so.6`` for greenlet. Example::

    LD_LIBRARY_PATH=/path/to/libstdc++ \
      uv run --with playwright python scripts/pw_toe_browsers.py Alice Bob

Log in manually in each window. Closed windows are not reaped or relaunched: the
launcher keeps its context entries (and the other windows) until Ctrl+C in the
terminal. Keep every window visible (not minimized); a hidden/background window
stops the battle loop.
"""

from __future__ import annotations

import argparse
import pathlib
import time


def main() -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("accounts", nargs="+", help="Account aliases (one window each)")
    parser.add_argument(
        "--profile-root",
        default=str(pathlib.Path.home() / ".hw-genie" / "pw-profiles"),
        help="Where per-account browser profiles live",
    )
    parser.add_argument(
        "--userscript",
        default=str(repo_root / "src/userscripts/dist/hw-genie-auth-capture-dev.user.js"),
        help="Dev userscript to inject (default: the built dev bundle)",
    )
    parser.add_argument("--url", default="https://www.hero-wars.com/", help="Game URL to open")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Open the windows, wait 15s, then close (feasibility check)",
    )
    args = parser.parse_args()

    script = pathlib.Path(args.userscript).read_text(encoding="utf-8")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        contexts = []
        try:
            for account in args.accounts:
                profile = pathlib.Path(args.profile_root) / account.replace(" ", "_")
                profile.mkdir(parents=True, exist_ok=True)
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(profile),
                    headless=False,
                    viewport={"width": 1100, "height": 720},
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context.add_init_script(script)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
                contexts.append(context)
                print(f"opened window for {account} (profile {profile})", flush=True)

            if args.probe:
                time.sleep(15)
                return

            print(
                "Log in to each window. Then run:\n"
                "  uv run hw-genie multi toe --engine live --parallel "
                f"{len(args.accounts)}",
                flush=True,
            )
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            pass
        finally:
            for context in contexts:
                try:
                    context.close()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
