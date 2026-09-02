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
  // Try multiple window contexts: the game may be in an iframe or wrappedJSObject.
  const candidates: unknown[] = [];
  try { candidates.push(window); } catch {}
  try { const uw = (window as unknown as { unsafeWindow?: unknown }).unsafeWindow; if (uw) candidates.push(uw); } catch {}
  try { const wj = (window as unknown as { wrappedJSObject?: unknown }).wrappedJSObject; if (wj) candidates.push(wj); } catch {}
  try { if (window.top && window.top !== window) candidates.push(window.top); } catch {}
  try {
    const ifr = document.querySelector("iframe") as HTMLIFrameElement | null;
    if (ifr?.contentWindow) candidates.push(ifr.contentWindow);
    for (let i = 0; i < window.frames.length; i++) {
      try { const f = window.frames[i]; if (f) candidates.push(f); } catch {}
    }
  } catch {}
  for (const c of candidates) {
    try {
      const g = (c as { Game?: { BattlePresets?: unknown; BattleInstantPlay?: unknown } })?.Game;
      if (g?.BattlePresets && g?.BattleInstantPlay) return g as { BattlePresets: unknown; BattleInstantPlay: unknown };
    } catch {}
  }
  // Fallback: any candidate with Game even if presets not yet loaded (for account reading)
  for (const c of candidates) {
    try {
      const g = (c as { Game?: unknown })?.Game;
      if (g) return g as { BattlePresets?: unknown; BattleInstantPlay?: unknown };
    } catch {}
  }
  return null;
}

function getGameWindow(): unknown {
  const candidates: unknown[] = [];
  try { candidates.push(window); } catch {}
  try { const uw = (window as unknown as { unsafeWindow?: unknown }).unsafeWindow; if (uw) candidates.push(uw); } catch {}
  try { const wj = (window as unknown as { wrappedJSObject?: unknown }).wrappedJSObject; if (wj) candidates.push(wj); } catch {}
  try { if (window.top && window.top !== window) candidates.push(window.top); } catch {}
  try {
    const ifr = document.querySelector("iframe") as HTMLIFrameElement | null;
    if (ifr?.contentWindow) candidates.push(ifr.contentWindow);
    for (let i = 0; i < window.frames.length; i++) {
      try { const f = window.frames[i]; if (f) candidates.push(f); } catch {}
    }
  } catch {}
  for (const c of candidates) {
    try {
      if ((c as { Game?: unknown })?.Game) return c;
    } catch {}
  }
  return window;
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
  const url = account
    ? `${baseUrl}/toe/next?account=${encodeURIComponent(account)}`
    : `${baseUrl}/toe/next`;
  const res = await fetchWithTimeout(url);
  if (!res || res.status === 204 || !res.ok) {
    // If account-specific poll returned 204, try without account as fallback (for multi-account)
    if (account && res?.status === 204) {
      const res2 = await fetchWithTimeout(`${baseUrl}/toe/next`);
      if (!res2 || res2.status === 204 || !res2.ok) return null;
      try { return (await res2.json()) as ToeJob; } catch { return null; }
    }
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
    const gw = getGameWindow() as { Game?: { DataStorage?: unknown } };
    const dataStorage = gw?.Game?.DataStorage;
    if (!dataStorage) {
      // Fallback: try pickGame's Game
      const pg = pickGame() as unknown as { DataStorage?: unknown };
      if (!pg?.DataStorage) return null;
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
    // Try multiple Game locations and multiple player paths. The web version
    // may have moved from window.Game to iframe or wrappedJSObject.
    const tryGame = (g: unknown): string => {
      try {
        const game = g as {
          ModelManager?: { getInstance?: () => unknown };
          DataStorage?: unknown;
        };
        const mm = game?.ModelManager?.getInstance?.() as
          | { player?: { userInfo?: { id?: string | number }; id?: string | number } }
          | undefined;
        if (mm?.player?.userInfo?.id) return String(mm.player.userInfo.id);
        if (mm?.player?.id) return String(mm.player.id);
        // Fallback: DataStorage
        const ds = (game as { DataStorage?: { player?: { id?: string } } })?.DataStorage;
        if (ds?.player?.id) return String(ds.player.id);
      } catch {}
      return "";
    };
    const candidates: unknown[] = [];
    try { candidates.push(window); } catch {}
    try { const uw = (window as unknown as { unsafeWindow?: unknown }).unsafeWindow; if (uw) candidates.push((uw as { Game?: unknown })?.Game ?? uw); } catch {}
    try { const wj = (window as unknown as { wrappedJSObject?: unknown }).wrappedJSObject; if (wj) candidates.push((wj as { Game?: unknown })?.Game ?? wj); } catch {}
    try { if (window.top && window.top !== window) candidates.push((window.top as { Game?: unknown })?.Game ?? window.top); } catch {}
    try {
      const ifr = document.querySelector("iframe") as HTMLIFrameElement | null;
      if (ifr?.contentWindow) candidates.push((ifr.contentWindow as { Game?: unknown })?.Game ?? ifr.contentWindow);
      for (let i = 0; i < window.frames.length; i++) {
        try { const f = window.frames[i] as unknown as { Game?: unknown }; if (f?.Game) candidates.push(f.Game); else if (f) candidates.push(f); } catch {}
      }
    } catch {}
    // Also try pickGame() result
    const pg = pickGame();
    if (pg) {
      const r = tryGame({ ModelManager: (pg as unknown as { ModelManager?: unknown })?.ModelManager } as unknown);
      if (r) return r;
    }
    for (const c of candidates) {
      const g = (c as { Game?: unknown })?.Game ?? c;
      const r = tryGame(g);
      if (r) return r;
    }
    // Last fallback: try to read x-auth-user-id from any captured headers in localStorage / cookie
    try {
      const ls = localStorage.getItem("x-auth-user-id") || localStorage.getItem("auth_user_id");
      if (ls) return String(ls);
    } catch {}
    return "";
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
      // Even if still empty, try polling without account (server will return any pending job)
      // This handles cases where Game is in an iframe or wrappedJSObject and readAccount temporarily fails.
      if (!account) {
        log("loop: no account yet, polling without account as fallback");
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
