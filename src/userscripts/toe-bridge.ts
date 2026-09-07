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

/**
 * Minimal port of HerowarsHelper's ``connectGame``: the Haxe game bundle
 * does NOT publish ``window.Game`` by itself — HWH creates that global and
 * captures classes by trapping assignments of well-known Haxe class paths
 * (``game.battle.controller.thread.BattlePresets``, ...) via setters on
 * ``Object.prototype``. Without HWH (e.g. HW Goodwin only), no frame ever
 * sees an engine, so this bridge installs the same traps for the small set
 * of classes battle calc needs.
 *
 * Coexistence rules (order-independent):
 * - If a populated ``window.Game`` already exists (HWH active), do nothing.
 * - If a target prop already has a setter on ``Object.prototype`` (HWH's
 *   traps installed after us), skip that prop — HWH populates the shared
 *   ``window.Game`` which ``pickGame`` also reads.
 * - Captured values are stored back as ``prop + '_'`` (HWH-compatible) so
 *   the game keeps working.
 *
 * Timing note: traps catch class registration while the bundle executes.
 * On an already-booted tab (bundle long ran) they miss — reload the tab
 * once with this script active (HWH off, Goodwin on is fine).
 */
const ENGINE_CLASS_PATHS: ReadonlyArray<{ name: string; prop: string }> = [
  { name: "BattlePresets", prop: "game.battle.controller.thread.BattlePresets" },
  { name: "BattleInstantPlay", prop: "game.battle.controller.instant.BattleInstantPlay" },
  { name: "MultiBattleInstantReplay", prop: "game.battle.controller.instant.MultiBattleInstantReplay" },
  { name: "MultiBattleResult", prop: "game.battle.controller.MultiBattleResult" },
  { name: "DataStorage", prop: "game.data.storage.DataStorage" },
  { name: "BattleConfigStorage", prop: "game.data.storage.battle.BattleConfigStorage" },
];

const bridgeDiag = { owned: 0, captured: 0 };

function gameBridge(): Record<string, unknown> {
  const w = window as unknown as { Game?: unknown };
  if (!w.Game || typeof w.Game !== "object") {
    w.Game = {};
  }
  return w.Game as Record<string, unknown>;
}

/** One-line per-realm diagnostic (run once after boot, see installToeBridge). */
export function realmDiag(): string {
  let gameKeys = "";
  try {
    const g = (window as unknown as { Game?: unknown }).Game as Record<string, unknown> | undefined;
    gameKeys = g && typeof g === "object" ? Object.keys(g).join(",") : typeof g;
  } catch {
    gameKeys = "unreadable";
  }
  let href = "";
  try {
    href = location.href;
  } catch {
    href = "unreadable";
  }
  return (
    `realm url=${href} trapsOwned=${bridgeDiag.owned} captured=${bridgeDiag.captured} ` +
    `engine=${hasLocalEngine()} gameKeys=[${gameKeys}]`
  );
}

export function ensureEngineBridge(): void {
  if (typeof window === "undefined" || typeof Object.defineProperty !== "function") {
    return;
  }
  try {
    const existing = (window as unknown as { Game?: Record<string, unknown> }).Game;
    if (existing && existing["BattlePresets"]) {
      return; // HWH (or a previous install) already exposes the engine.
    }
    for (const { name, prop } of ENGINE_CLASS_PATHS) {
      try {
        const prev = Object.getOwnPropertyDescriptor(Object.prototype, prop);
        if (prev && (prev.set || prev.get)) {
          bridgeDiag.owned += 1;
          log(`trap for ${name}: already owned, skipping`);
          continue; // Owned by HWH's traps — it populates shared window.Game.
        }
        Object.defineProperty(Object.prototype, prop, {
          configurable: true,
          set(this: Record<string, unknown>, value: unknown) {
            try {
              const bridge = gameBridge();
              if (!bridge[name]) {
                bridge[name] = value;
                bridgeDiag.captured += 1;
                log(`captured ${name}`);
              }
            } catch {}
            this[prop + "_"] = value;
          },
          get(this: Record<string, unknown>) {
            return this[prop + "_"];
          },
        });
      } catch {}
    }
  } catch {}
}

function getCandidateWindows(): unknown[] {
  // Single enumeration of every window context the game may live in: the
  // local frame, userscript sandboxes, the top frame, and child frames.
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
  return candidates;
}

/**
 * Pure predicate: does this game object carry everything ``computeBattle``
 * needs (battle classes + data storage)?
 *
 * Exported for unit tests (see ``tests/toe-bridge.test.js``). Frames whose
 * local game fails this check must never claim jobs — otherwise an
 * engine-less frame (e.g. the top window while the game lives in a
 * cross-origin iframe) claims first and its dummy loss consumes the job
 * before the engine frame can compute the real result.
 */
export function hasBattleEngine(game: unknown): boolean {
  try {
    const g = game as {
      BattlePresets?: unknown;
      BattleInstantPlay?: unknown;
      DataStorage?: unknown;
    } | null | undefined;
    return Boolean(g && g.BattlePresets && g.BattleInstantPlay && g.DataStorage);
  } catch {
    return false;
  }
}

/**
 * Engine-frame gate: scan every candidate window for a full battle engine.
 *
 * This must NOT derive from a single ``pickGame()`` result: ``pickGame``
 * also returns partial matches (presets loaded but ``DataStorage`` missing)
 * as a fallback for account reading, so ``hasBattleEngine(pickGame())``
 * could false-negative and silence a frame that actually carries an engine
 * in a later candidate window. Only frames passing this gate may poll —
 * otherwise an engine-less frame (e.g. the top window while the game lives
 * in a cross-origin iframe) claims first and its dummy loss consumes the
 * job before the engine frame can compute the real result.
 *
 * Exported for unit tests (see ``tests/toe-bridge.test.js``).
 */
export function hasLocalEngine(): boolean {
  for (const c of getCandidateWindows()) {
    try {
      const g = (c as { Game?: unknown })?.Game;
      if (g && hasBattleEngine(g)) return true;
    } catch {}
  }
  return false;
}

/**
 * Read ``.Game`` off one candidate window without ever throwing.
 *
 * A cross-origin ``WindowProxy`` (game iframe vs top window) throws
 * ``DOMException: Permission denied`` on ANY property read, including the
 * ``?.Game`` access itself — so every such read needs its own guard.
 * Returns ``undefined`` for unreadable candidates so callers can skip them.
 *
 * Exported for unit tests (see ``tests/toe-bridge.test.js``).
 */
export function safeGameOf(candidate: unknown): unknown {
  try {
    return (candidate as { Game?: unknown })?.Game ?? candidate;
  } catch {
    return undefined;
  }
}

/** Return the first window context carrying a ``Game`` object (any shape). */
function getGameWindow(): unknown {
  for (const c of getCandidateWindows()) {
    try {
      if ((c as { Game?: unknown })?.Game) return c;
    } catch {}
  }
  try {
    return window;
  } catch {
    return null;
  }
}

function pickGame(): { BattlePresets?: unknown; BattleInstantPlay?: unknown } | null {
  // The game exposes the bridge classes through the global ``Game`` object
  // once its JS bundle has loaded (any game page; no Titan Arena navigation
  // is required). Only frames whose local ``Game`` carries the battle classes
  // may poll — see ``hasLocalEngine``.
  // Try multiple window contexts: the game may be in an iframe or wrappedJSObject.
  const candidates = getCandidateWindows();
  // Prefer a full-engine match first so a partial Game (presets loaded but
  // DataStorage missing) earlier in the list never shadows a complete one.
  for (const c of candidates) {
    try {
      const g = (c as { Game?: unknown })?.Game;
      if (g && hasBattleEngine(g)) return g as { BattlePresets: unknown; BattleInstantPlay: unknown };
    } catch {}
  }
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

/**
 * Minified-name resolvers for the Haxe-compiled game bundle.
 *
 * The game mangles method names per build, but keeps a
 * ``__properties__`` map (minified -> original). These helpers resolve the
 * current minified key, ported verbatim from HerowarsHelper's working
 * ``BattleCalc``. Exported for unit tests.
 */
export function getF(classF: unknown, nameF: string): string {
  const props = (classF as { prototype?: { __properties__?: Record<string, string> } })?.prototype?.__properties__ ?? {};
  const found = Object.entries(props)
    .filter((e) => e[1] === nameF)
    .pop();
  if (!found) throw new Error(`getF: ${nameF} not found`);
  return found[0];
}

export function getFn(classF: unknown, nF: number): string {
  return Object.keys(classF as object)[nF];
}

export function getProtoFn(classF: unknown, nF: number): string {
  return Object.keys((classF as { prototype?: object })?.prototype ?? {})[nF];
}

/** Battle ``type`` -> ``BattleConfigStorage`` getter. This bridge only runs ToE. */
export function battleConfigFor(battleType: unknown): string {
  if (typeof battleType === "string" && battleType.includes("titan_arena")) {
    return "get_titanPvpManual";
  }
  return "get_titanPvpManual";
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

let lastFetchFailLog = 0;
async function pollNextJob(account: string, baseUrl: string): Promise<ToeJob | null> {
  const url = account
    ? `${baseUrl}/toe/next?account=${encodeURIComponent(account)}`
    : `${baseUrl}/toe/next`;
  const res = await fetchWithTimeout(url);
  if (!res) {
    // Network-level failure (server down, mixed-content block, firewall).
    // Throttled: this would otherwise spam once per second.
    const now = Date.now();
    if (now - lastFetchFailLog > 60000) {
      lastFetchFailLog = now;
      log(`cannot reach auth server at ${baseUrl} (fetch failed)`);
    }
    return null;
  }
  if (res.status === 204 || !res.ok) {
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
 * Returns ``null`` when the game bundle is not loaded, ``DataStorage`` is
 * unavailable, or the calc fails / times out. ``tick`` submits a fast loss
 * only after such a genuine calc failure on an engine frame.
 */
async function computeBattle(battle: unknown): Promise<BattleResultPayload | null> {
  const game = pickGame() as unknown as Record<string, unknown> | null;
  if (!game || !game["BattlePresets"] || !game["BattleInstantPlay"]) {
    log("computeBattle: no engine in this frame (pickGame miss)");
    return null;
  }
  if (!game["DataStorage"]) {
    log("computeBattle: engine without DataStorage");
    return null;
  }
  try {
    // Ported from HerowarsHelper's working BattleCalc:
    // presets take the battle's own progress plus the titan PvP config from
    // BattleConfigStorage, the instant play reports via a Haxe signal (NOT
    // DOM addEventListener), and results come from MultiBattleResult getters.
    const b = battle as { progress?: unknown; type?: unknown };
    const dataStorage = game["DataStorage"] as Record<string, unknown>;
    const configStorageKey = getFn(dataStorage, 25);
    const dataStores = dataStorage as unknown as Record<string, Record<string, () => unknown>>;
    const config = dataStores[configStorageKey][getF(game["BattleConfigStorage"], battleConfigFor(b.type))]();
    const presets = new (
      game["BattlePresets"] as new (
        progress: unknown,
        isReplay: boolean,
        autoOnStart: boolean,
        config: unknown,
        showBothTeams: boolean,
      ) => unknown
    )(b.progress ?? [], false, true, config, false);
    const BattleInstantPlay = game["BattleInstantPlay"] as new (
      data: unknown,
      presets: unknown,
    ) => Record<string, unknown>;
    const instant: Record<string, unknown> =
      Array.isArray(b.progress) && (b.progress as unknown[]).length > 1 && game["MultiBattleInstantReplay"]
        ? new (game["MultiBattleInstantReplay"] as new (data: unknown, presets: unknown) => Record<string, unknown>)(
            battle,
            presets,
          )
        : new BattleInstantPlay(battle, presets);
    let result: BattleResultPayload | null = null;
    return new Promise<BattleResultPayload | null>((resolve) => {
      try {
        const signal = instant[getProtoFn(game["BattleInstantPlay"], 9)] as {
          add: (cb: (bi: unknown) => void) => void;
        };
        signal.add((battleInstant: unknown) => {
          const bi = battleInstant as Record<string, () => unknown>;
          const getters = bi[getF(game["BattleInstantPlay"], "get_result")]() as Record<string, () => unknown>;
          const progress = getters[getF(game["MultiBattleResult"], "get_progress")]() as BattleProgress[];
          const res = getters[getF(game["MultiBattleResult"], "get_result")]() as {
            win: boolean;
            stars: number;
          };
          result = { progress: progress ?? [], result: res ?? { win: false, stars: 0 } };
          resolve(result);
        });
        (instant["start"] as () => void).call(instant);
        // Safety timeout: if the engine never completes, resolve null so
        // tick submits the fast loss instead of hanging.
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

export async function tick(account: string, baseUrl: string): Promise<void> {
  // Engine-less frames must never claim: claiming without the ability to
  // compute would consume the job with a dummy loss before the engine frame
  // sees it. The auth-server reclaim timeout is only a safety net.
  if (!hasLocalEngine()) {
    return;
  }
  const job = await pollNextJob(account, baseUrl);
  if (!job) {
    return;
  }
  log(`claimed job ${job.id} (type=${(job.battle as { type?: unknown })?.type ?? "?"})`);
  const computed = await computeBattle(job.battle);
  if (!computed) {
    // The engine exists but this battle failed to compute (calc error or
    // timeout). Submit a fast loss so Python proceeds instead of waiting
    // out its full bridge timeout.
    log(`job ${job.id}: compute failed, submitting dummy loss`);
    await submitResult(job.id, account, {
      progress: [{ attackers: { heroes: {} }, defenders: { heroes: {} } }],
      result: { win: false, stars: 0 },
    }, baseUrl);
    return;
  }
  log(`job ${job.id}: complete win=${computed.result.win} stars=${computed.result.stars} rounds=${computed.progress.length}`);
  await submitResult(job.id, account, computed, baseUrl);
}

export function installToeBridge(opts: { authServerUrl?: string; pollIntervalMs?: number } = {}): () => void {
  const baseUrl = (opts.authServerUrl ?? AUTH_SERVER_URL).replace(/\/+$/, "");
  const interval = opts.pollIntervalMs ?? POLL_INTERVAL_MS;
  let stopped = false;
  let timer: ReturnType<typeof setInterval> | null = null;

  // Capture game classes as the bundle registers them (HWH-independent).
  ensureEngineBridge();

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
    for (const c of getCandidateWindows()) {
      const g = safeGameOf(c);
      if (g !== undefined) candidates.push(g);
    }
    // Also try pickGame() result
    const pg = pickGame();
    if (pg) {
      const r = tryGame({ ModelManager: (pg as unknown as { ModelManager?: unknown })?.ModelManager } as unknown);
      if (r) return r;
    }
    for (const g of candidates) {
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

  let lastNoEngineLog = 0;
  let engineLogged = false;
  async function loop(): Promise<void> {
    if (stopped) {
      return;
    }
    // Only engine frames poll. Non-engine frames stay silent so the server
    // log shows polling if and only if a frame can actually compute.
    if (!hasLocalEngine()) {
      engineLogged = false;
      // Throttled diagnostic: without this, a missing engine is invisible.
      const now = Date.now();
      if (now - lastNoEngineLog > 60000) {
        lastNoEngineLog = now;
        log("no battle engine in this frame (top/game url=" + location.href + ")");
      }
      return;
    }
    if (!engineLogged) {
      engineLogged = true;
      log("battle engine detected, polling active");
    }
    if (!account) {
      account = readAccount();
      // Even if still empty, try polling without account (server will return any pending job).
      // The tick gate above already guarantees this frame has an engine, so
      // a claimed job can actually be computed.
    }
    await tick(account, baseUrl);
  }

  try {
    setTimeout(() => {
      try {
        log(realmDiag());
      } catch {}
    }, 15000);
  } catch {}
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
