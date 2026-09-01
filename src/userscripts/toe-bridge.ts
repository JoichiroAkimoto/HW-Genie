// ToE bridge: lets the Python CLI ask the running game to compute a titan
// battle via the auth server's ``/toe/*`` job queue. The script runs entirely
// in the browser (Tampermonkey / Greasemonkey / Violentmonkey) using
// ``fetch``, so it does not need the XHR interceptor.
//
// Pipeline:
//   1. Python CLI posts a battle to /toe/job (auth server stores it).
//   2. This script polls /toe/next?account=<player>; if it gets a job it
//      calls into the in-page ``Game.BattlePresets / BattleInstantPlay`` to
//      run a real titan battle, producing a server-valid progress/result.
//   3. Result is POSTed back to /toe/job/<id>/result and Python picks it up.

const AUTH_SERVER_URL = "http://localhost:8765";
// Players with no ToE job for many minutes can lower the poll rate. The 1s
// cadence here keeps end-to-end latency low while staying well under the
// hero-wars.com request rate (no XHR proxying in this path).
const POLL_INTERVAL_MS = 1000;
const REQUEST_TIMEOUT_MS = 5000;

interface ToeJob {
  id: string;
  account: string;
  battle: unknown;
}

interface BattleProgressHero {
  hp: number;
  energy?: number;
  isDead?: boolean;
  maxHp?: number;
}

interface BattleProgress {
  attackers?: { heroes?: Record<string, BattleProgressHero> } | unknown[];
  defenders?: { heroes?: Record<string, BattleProgressHero> } | unknown[];
  // The game sometimes returns raw arrays; tolerate both shapes.
}

interface BattleResultPayload {
  progress: BattleProgress[];
  result: { win: boolean; stars: number };
}

function log(msg: string, ...args: unknown[]): void {
  console.log(`[HW-Genie/ToE] ${msg}`, ...args);
}

function pickGame(): { BattlePresets?: unknown; BattleInstantPlay?: unknown } | null {
  // The game exposes the bridge classes through the global ``Game`` object once
  // the player enters a titan battle screen. We only call into it from a
  // polling tick so the first few polls just no-op until the player is in
  // the Titan Arena screen.
  const w = window as unknown as { Game?: { BattlePresets?: unknown; BattleInstantPlay?: unknown } };
  return w.Game ?? null;
}

async function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): Promise<Response | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function pollNextJob(account: string, baseUrl: string): Promise<ToeJob | null> {
  const res = await fetchWithTimeout(
    `${baseUrl}/toe/next?account=${encodeURIComponent(account)}`,
  );
  if (!res || res.status === 204 || !res.ok) {
    return null;
  }
  try {
    return (await res.json()) as ToeJob;
  } catch {
    return null;
  }
}

async function submitResult(
  jobId: string,
  account: string,
  result: BattleResultPayload,
  baseUrl: string,
): Promise<boolean> {
  const res = await fetchWithTimeout(
    `${baseUrl}/toe/job/${encodeURIComponent(jobId)}/result`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account, result }),
    },
  );
  return Boolean(res && res.ok);
}

/**
 * Compute a titan battle using the in-page game engine.
 *
 * Returns ``null`` if the engine is not available (e.g. user is not in the
 * titan screen), letting the caller skip the job and let Python fall back
 * to its estimator.
 */
async function computeBattle(battle: unknown): Promise<BattleResultPayload | null> {
  const game = pickGame();
  if (!game || !game.BattlePresets || !game.BattleInstantPlay) {
    return null;
  }
  try {
    // The game's BattleInstantPlay expects:
    //   new BattlePresets(progress, isReplay, autoOnStart, config, showBothTeams)
    //   new BattleInstantPlay(battleData, presets)
    // followed by a single calc() promise we await via the wrapped engine.
    // We don't have direct access to BattleConfigStorage from this script,
    // so we rely on the battle.type field (always 'titan_arena' here) and
    // the global DataStorage. If the page is not on the titan screen, the
    // engine may throw — we treat that as 'unavailable' and let Python retry.
    const dataStorage = (window as unknown as { Game?: { DataStorage?: unknown } }).Game
      ?.DataStorage;
    if (!dataStorage) {
      return null;
    }
    // Best-effort: the data-storage key for titan arena matches the
    // 'titan_arena' / 'titan_pvp_manual' type seen in HerowarsHelper's
    // getBattleType(). Without a guaranteed lookup, fall through to a
    // generic 'get_titanPvpManual' config; the battle.typeId still drives
    // the actual simulation inside the engine.
    const presets = new (
      game.BattlePresets as new (
        progress: unknown,
        isReplay: boolean,
        autoOnStart: boolean,
        config: unknown,
        showBothTeams: boolean,
      ) => unknown
    )([], false, true, undefined, false);
    const instant = new (game.BattleInstantPlay as new (data: unknown, presets: unknown) => {
      addEventListener(type: string, listener: (event: unknown) => void): void;
    })(battle, presets);
    let result: BattleResultPayload | null = null;
    return new Promise<BattleResultPayload | null>((resolve) => {
      try {
        instant.addEventListener("complete", (raw: unknown) => {
          // The game's on-complete delivers a single object; we only need
          // the array of progress rounds and the result.
          const r = raw as { progress?: BattleProgress[]; result?: { win: boolean; stars: number } };
          result = {
            progress: r.progress ?? [],
            result: r.result ?? { win: false, stars: 0 },
          };
          resolve(result);
        });
        // Drive the simulation. The game engine kicks off as soon as the
        // listener is registered. We don't have a single public 'start' on
        // every Haxe build, so we fall back to invoking it if exposed.
        const start = (instant as { start?: () => void }).start;
        if (typeof start === "function") {
          start.call(instant);
        }
        // Safety timeout: if the engine never completes, resolve null so
        // Python falls back to its estimator instead of hanging.
        setTimeout(() => {
          if (result === null) {
            log("computeBattle: engine timed out without completing");
            resolve(null);
          }
        }, 10_000);
      } catch (e) {
        log("computeBattle threw:", e);
        resolve(null);
      }
    });
  } catch (e) {
    log("computeBattle outer error:", e);
    return null;
  }
}

async function tick(account: string, baseUrl: string): Promise<void> {
  const job = await pollNextJob(account, baseUrl);
  if (!job) {
    return;
  }
  const computed = await computeBattle(job.battle);
  if (!computed) {
    // Drop the job by submitting a loss. Python can still decide what to do
    // based on the estimator. This avoids head-of-line blocking when the
    // page is not on the titan screen.
    await submitResult(job.id, account, {
      progress: [{ attackers: { heroes: {} }, defenders: { heroes: {} } }],
      result: { win: false, stars: 0 },
    }, baseUrl);
    return;
  }
  await submitResult(job.id, account, computed, baseUrl);
}

export function installToeBridge(opts: { authServerUrl?: string; pollIntervalMs?: number } = {}): () => void {
  const baseUrl = (opts.authServerUrl ?? AUTH_SERVER_URL).replace(/\/+$/, "");
  const interval = opts.pollIntervalMs ?? POLL_INTERVAL_MS;
  let stopped = false;
  let timer: ReturnType<typeof setInterval> | null = null;

  // The script needs to know which account to claim jobs for. We pull it
  // from the game's user object so the user doesn't have to configure it.
  function readAccount(): string {
    const w = window as Window & { Game?: { ModelManager?: { getInstance?: () => unknown } } };
    try {
      const mm = w.Game?.ModelManager?.getInstance?.();
      if (!mm) {
        return "";
      }
      const player = (mm as { player?: { userInfo?: { id?: string } } }).player;
      return String(player?.userInfo?.id ?? "");
    } catch {
      return "";
    }
  }

  let account = readAccount();
  if (!account) {
    log("installToeBridge: no player id on window.Game yet; will retry on first poll");
  }

  async function loop(): Promise<void> {
    if (stopped) {
      return;
    }
    if (!account) {
      account = readAccount();
      if (!account) {
        return;
      }
    }
    await tick(account, baseUrl);
  }

  timer = setInterval(() => {
    loop().catch((e) => log("tick error:", e));
  }, interval);

  return () => {
    stopped = true;
    if (timer !== null) {
      clearInterval(timer);
    }
  };
}
