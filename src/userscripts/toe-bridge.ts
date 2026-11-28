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
// The poll request only hits the local auth server (no XHR proxying in this
// path), so a 250ms cadence is cheap and removes most of the queue wait a
// battle would otherwise see between POST /toe/job and this script noticing it.
const POLL_INTERVAL_MS = 250;
// Jobs computed per poll tick. Raid simulations are queued all at once, so
// draining several in one tick runs them back-to-back instead of paying one
// poll interval per battle.
const JOBS_PER_TICK = 4;
const REQUEST_TIMEOUT_MS = 5000;

/**
 * localStorage key the auth-capture side writes the live ``x-auth-user-id``
 * to. Shared by ``index.ts`` and this bridge so both agree on the tab identity
 * used for live job claims.
 */
export const AUTH_USER_ID_STORAGE_KEY = "hwgenie-x-auth-user-id";

/**
 * localStorage key the live-job failure path writes a one-shot diagnostic to
 * (rival ids wanted vs. present, minified-method presence). Read it from the
 * game tab console when a live job reports ``rival not found``.
 */
export const TOE_DIAG_KEY = "hwgenie-toe-diag";

// Build variant sentinel. Normal builds ship with the NORMAL value and never
// execute any ToE code (Goodwin separation, see #134); `build.sh --dev`
// rewrites it to the DEV value so DEV builds run the full ToE bridge.
const TOE_VARIANT: string = "__TOE_VARIANT_NORMAL__";
const TOE_VARIANT_DEV: string = "__TOE_VARIANT_DEV__";

/**
 * Whether this build runs the ToE bridge (traps + polling).
 *
 * Normal builds: false (auth capture only). DEV builds: true via the
 * build-time sentinel rewrite. Tests flip it with
 * {@link __setToeEnabledForTests}.
 */
export let TOE_ENABLED: boolean = TOE_VARIANT === TOE_VARIANT_DEV;

/** Test-only seam. テスト専用。本番コードからは呼ばないこと。 */
export function __setToeEnabledForTests(value: boolean): void {
  TOE_ENABLED = value;
}

interface ToeJob {
  id: string;
  account: string;
  battle: unknown;
  /** ``"sim"`` (offline instant calc, default) or ``"live"`` (drive the real game battle). */
  kind?: string;
  /** Live action: ``"battle"`` or ``"reload"`` (tier transition). */
  action?: string;
  rivalId?: string;
  titans?: number[];
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
  /** Calc-failure reason carried on dummy submissions (diagnostics only). */
  error?: string;
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
/**
 * Haxe class paths trapped for engine exposure.
 *
 * Ported from HerowarsHelper's ``ObjectsList`` (v2.458). Captured refs
 * contribute to a pre-existing shared ``window.Game`` only — one is never
 * created, so other scripts' feature detection is unaffected. Only the
 * ``CORE_ENGINE_NAMES`` subset is needed for battle calc; the rest exists
 * so cohabitants trapping the same paths keep working through chaining.
 */
const ENGINE_CLASS_PATHS: ReadonlyArray<{ name: string; prop: string }> = [
  { name: "BattlePresets", prop: "game.battle.controller.thread.BattlePresets" },
  { name: "DataStorage", prop: "game.data.storage.DataStorage" },
  { name: "BattleConfigStorage", prop: "game.data.storage.battle.BattleConfigStorage" },
  { name: "BattleInstantPlay", prop: "game.battle.controller.instant.BattleInstantPlay" },
  { name: "MultiBattleInstantReplay", prop: "game.battle.controller.instant.MultiBattleInstantReplay" },
  { name: "MultiBattleResult", prop: "game.battle.controller.MultiBattleResult" },
  { name: "PlayerMissionData", prop: "game.model.user.mission.PlayerMissionData" },
  { name: "PlayerMissionBattle", prop: "game.model.user.mission.PlayerMissionBattle" },
  { name: "GameModel", prop: "game.model.GameModel" },
  { name: "CommandManager", prop: "game.command.CommandManager" },
  { name: "MissionCommandList", prop: "game.command.rpc.mission.MissionCommandList" },
  { name: "RPCCommandBase", prop: "game.command.rpc.RPCCommandBase" },
  { name: "PlayerTowerData", prop: "game.model.user.tower.PlayerTowerData" },
  { name: "TowerCommandList", prop: "game.command.tower.TowerCommandList" },
  { name: "PlayerHeroTeamResolver", prop: "game.model.user.hero.PlayerHeroTeamResolver" },
  { name: "BattlePausePopup", prop: "game.view.popup.battle.BattlePausePopup" },
  { name: "BattlePopup", prop: "game.view.popup.battle.BattlePopup" },
  { name: "DisplayObjectContainer", prop: "starling.display.DisplayObjectContainer" },
  { name: "GuiClipContainer", prop: "engine.core.clipgui.GuiClipContainer" },
  { name: "BattlePausePopupClip", prop: "game.view.popup.battle.BattlePausePopupClip" },
  { name: "ClipLabel", prop: "game.view.gui.components.ClipLabel" },
  { name: "ClipLabelBase", prop: "game.view.gui.components.ClipLabelBase" },
  { name: "Translate", prop: "com.progrestar.common.lang.Translate" },
  { name: "ClipButtonLabeledCentered", prop: "game.view.gui.components.ClipButtonLabeledCentered" },
  { name: "BattlePausePopupMediator", prop: "game.mediator.gui.popup.battle.BattlePausePopupMediator" },
  { name: "SettingToggleButton", prop: "game.mechanics.settings.popup.view.SettingToggleButton" },
  { name: "PlayerDungeonData", prop: "game.mechanics.dungeon.model.PlayerDungeonData" },
  { name: "NextDayUpdatedManager", prop: "game.model.user.NextDayUpdatedManager" },
  { name: "BattleController", prop: "game.battle.controller.BattleController" },
  { name: "BattleSettingsModel", prop: "game.battle.controller.BattleSettingsModel" },
  { name: "BooleanProperty", prop: "engine.core.utils.property.BooleanProperty" },
  { name: "RuleStorage", prop: "game.data.storage.rule.RuleStorage" },
  { name: "BattleConfig", prop: "battle.BattleConfig" },
  { name: "BattleGuiMediator", prop: "game.battle.gui.BattleGuiMediator" },
  { name: "BooleanPropertyWriteable", prop: "engine.core.utils.property.BooleanPropertyWriteable" },
  { name: "BattleLogEncoder", prop: "battle.log.BattleLogEncoder" },
  { name: "BattleLogReader", prop: "battle.log.BattleLogReader" },
  {
    name: "PlayerSubscriptionInfoValueObject",
    prop: "game.model.user.subscription.PlayerSubscriptionInfoValueObject",
  },
  { name: "AdventureMapCamera", prop: "game.mechanics.adventure.popup.map.AdventureMapCamera" },
];

/** Subset of ENGINE_CLASS_PATHS required to compute battles. */
const CORE_ENGINE_NAMES: ReadonlyArray<string> = [
  "BattlePresets",
  "BattleInstantPlay",
  "MultiBattleInstantReplay",
  "MultiBattleResult",
  "DataStorage",
  "BattleConfigStorage",
];

const bridgeDiag = { owned: 0, captured: 0 };

/**
 * Class references captured by our own traps, keyed by short name.
 *
 * This is the primary engine source — NOT ``window.Game``. Trap setters
 * run in the same realm that later computes, so refs stored here are always
 * readable, regardless of which object ``window.Game`` points at (or whether
 * another script replaced it). ``window.Game`` is still updated
 * best-effort for visibility/debugging.
 */
const capturedClasses: Record<string, unknown> = {};

/** Names captured so far (for diagnostics/tests). */
export function capturedClassNames(): string[] {
  return Object.keys(capturedClasses);
}

/**
 * Upgrade-aware store: keep the best-known class ref. First registration
 * wins by default, but a re-registration upgrades when the incoming value
 * is usable and the stored one is not (or a different function, e.g. SPA
 * re-boot). Returns true when the stored ref changed.
 */
export function storeCapturedClass(name: string, value: unknown): boolean {
  try {
    const cur = capturedClasses[name];
    if (cur === value) {
      return false;
    }
    const curFn = typeof cur === "function";
    const nextFn = typeof value === "function";
    if (cur === undefined || (!curFn && nextFn) || (curFn && nextFn)) {
      capturedClasses[name] = value;
      return true;
    }
  } catch {}
  return false;
}

/**
 * Shared registration handler for our traps (own and chained-foreign).
 *
 * Observe-only: capture our ref (upgrade-aware), publish the shared Game
 * entry, then materialize a plain own data property so the holder looks
 * EXACTLY as if no trap ever existed (enumerable own key, no ghost keys).
 * Later reads/wraps (e.g. Goodwin wrapping battle classes for pre-calc)
 * hit the own property natively. Throws (frozen holder) fall back to the
 * ghost key.
 */
export function observeRegistration(name: string, prop: string, value: unknown, holder: unknown): void {
  try {
    if (storeCapturedClass(name, value)) {
      bridgeDiag.captured += 1;
      log(`captured ${name}`);
    }
  } catch {}
  try {
    // Contribute to a pre-existing shared window.Game only (e.g. created by
    // HWH or Goodwin). Never create it: an unexpected global changes other
    // scripts' feature detection ("HWH present?") and breaks coexistence.
    const w = window as unknown as { Game?: unknown };
    if (w.Game && typeof w.Game === "object") {
      const bridge = w.Game as Record<string, unknown>;
      if (bridge[name] !== value) {
        bridge[name] = value;
      }
    }
  } catch {}
  try {
    Object.defineProperty(holder as object, prop, {
      value,
      writable: true,
      enumerable: true,
      configurable: true,
    });
    // Remember materialization: a later `delete holder[prop]` must read as
    // undefined (as if no trap existed), not as the stale ghost key below.
    try {
      materializedHolders.add(holder as object);
    } catch {}
  } catch {
    try {
      (holder as Record<string, unknown>)[prop + "_"] = value;
    } catch {}
  }
  maybeRemoveTraps();
}

/** Holders materialized with a plain own prop (see observeRegistration). */
const materializedHolders: WeakSet<object> = new WeakSet();

/** Test-only: clear captured refs, traps and diag counters. */
export function __resetBridgeForTests(): void {
  engineReadyLogged = false;
  // Restore the shipped normal default (TOE_ENABLED=false). Tests needing
  // DEV behavior opt in explicitly via __setToeEnabledForTests(true).
  TOE_ENABLED = false;
  for (const k of Object.keys(capturedClasses)) {
    delete capturedClasses[k];
  }
  for (const { prop } of ENGINE_CLASS_PATHS) {
    try {
      const cur = Object.getOwnPropertyDescriptor(Object.prototype, prop);
      if (cur && (cur.set || cur.get)) {
        delete (Object.prototype as Record<string, unknown>)[prop];
      }
    } catch {}
  }
  for (const k of Object.keys(installedTraps)) {
    delete installedTraps[k];
  }
  bridgeDiag.owned = 0;
  bridgeDiag.captured = 0;
  // Live state must not leak between tests (a pending reload flag would make
  // every later tick return early).
  liveJobRunner = null;
  liveAutoForced = false;
  liveAutoApplied = false;
  liveAutoPatchInstalled = false;
  liveAutoShape = "";
  liveReloadPending = false;
  clearReloadPending();
}

function capturedEngine(): Record<string, unknown> | null {
  // Gate through the callable check: polluted captures (non-function
  // BattlePresets/BattleInstantPlay) must never pass, otherwise the frame
  // claims jobs it cannot compute and banks dummy losses.
  if (hasBattleEngine(capturedClasses)) {
    return capturedClasses as Record<string, unknown>;
  }
  return null;
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
    `engine=${hasLocalEngine()} gameKeys=[${gameKeys}] capturedKeys=[${capturedClassNames().join(",")}]`
  );
}

/**
 * Our installed trap descriptors, for post-capture removal (see below).
 *
 * MUST be a null-prototype object: a plain ``{}`` inherits the very
 * ``Object.prototype`` traps installed below, so ``installedTraps[prop] =
 * trap`` would fire our own setter, capture our own trap functions as game
 * classes, and delete the traps before the game bundle ever runs.
 */
const installedTraps: Record<string, { set: (this: any, v: unknown) => void; get: (this: any) => unknown }> =
  Object.create(null);

let engineReadyLogged = false;

function maybeRemoveTraps(): void {
  // Readiness log only. Traps intentionally stay installed for the whole
  // session (exactly like HWH): the game runtime may resolve classes via
  // these paths lazily, and removing them mid-boot stalls loading at 1-2%.
  if (!engineReadyLogged && CORE_ENGINE_NAMES.every((n) => capturedClasses[n])) {
    engineReadyLogged = true;
    log("engine captured");
  }
}

export function ensureEngineBridge(): void {
  // Normal builds never install Object.prototype traps (Goodwin separation).
  if (!TOE_ENABLED) {
    return;
  }
  if (typeof window === "undefined" || typeof Object.defineProperty !== "function") {
    return;
  }
  try {
    const existing = (window as unknown as { Game?: Record<string, unknown> }).Game;
    if (existing && hasBattleEngine(existing)) {
      return; // A complete engine is already exposed (e.g. HWH active).
    }
    for (const { name, prop } of ENGINE_CLASS_PATHS) {
      try {
        const prev = Object.getOwnPropertyDescriptor(Object.prototype, prop);
        if (prev && (prev.set || prev.get)) {
          // Idempotent re-install: our own trap from a previous
          // ensureEngineBridge() call is not foreign ownership — skip
          // without bumping the owned (HWH) diagnostic.
          if (installedTraps[prop] && prev.set === installedTraps[prop].set) {
            continue;
          }
          // Foreign trap (HWH/Goodwin): chain instead of skipping so we
          // still capture. Call through FIRST to preserve their behavior
          // exactly, then run our own logic. Non-configurable foreign
          // traps cannot be chained — leave those alone.
          if (!prev.configurable) {
            bridgeDiag.owned += 1;
            log(`trap for ${name}: foreign and locked, skipping`);
            continue;
          }
          const foreign = prev;
          const trap = {
            set(this: Record<string, unknown>, value: unknown) {
              try {
                foreign.set?.call(this, value);
              } catch {}
              observeRegistration(name, prop, value, this);
            },
            get(this: Record<string, unknown>) {
              try {
                return foreign.get?.call(this);
              } catch {
                return undefined;
              }
            },
          };
          Object.defineProperty(Object.prototype, prop, { configurable: true, ...trap });
          installedTraps[prop] = trap;
          bridgeDiag.owned += 1;
          log(`trap for ${name}: chained foreign trap`);
          continue;
        }
        const trap = {
          set(this: Record<string, unknown>, value: unknown) {
            observeRegistration(name, prop, value, this);
          },
          get(this: Record<string, unknown>) {
            // A materialized holder that lost its own prop via `delete`
            // must read as undefined (transparent). Otherwise serve the
            // frozen-holder ghost fallback.
            try {
              if (materializedHolders.has(this)) {
                return undefined;
              }
            } catch {}
            return this[prop + "_"];
          },
        };
        Object.defineProperty(Object.prototype, prop, { configurable: true, ...trap });
        installedTraps[prop] = trap;
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
    // Classes must be callable constructors — guards against captured
    // non-class objects ever passing as an engine.
    return Boolean(
      g &&
        typeof g.BattlePresets === "function" &&
        typeof g.BattleInstantPlay === "function" &&
        g.DataStorage,
    );
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
  if (capturedEngine()) return true;
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
function pickGame(): { BattlePresets?: unknown; BattleInstantPlay?: unknown } | null {
  // Primary source: classes captured by our own traps (same-realm refs —
  // always readable). Falls back to scanning window contexts for a shared
  // ``Game`` object (e.g. populated by HWH).
  const direct = capturedEngine();
  if (direct) {
    return direct as { BattlePresets?: unknown; BattleInstantPlay?: unknown };
  }
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
 * ``BattleCalc`` (ordinals DataStorage key 25 / BattleInstantPlay signal 10
 * pinned to that source; re-validate there when a game update breaks
 * resolution — every throw below is diag-logged with the failing key).
 * The signal moved from 9 to 10 with the 2026-11-20 game update
 * (HerowarsHelper 2.476 made the same change).
 * Exported for unit tests.
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

/**
 * Resolve the BattleInstantPlay completion-signal key on a live instance.
 *
 * Haxe prototype key order (ordinals) shifts with game builds, so the pinned
 * ordinal is only a hint: try it first (the value HerowarsHelper currently
 * uses), then fall back to any field (prototype or own) whose instance value
 * exposes the Haxe signal shape. A plain List also has ``add``, so the shape
 * also needs ``dispatch`` (DOM-like) or ``subscribe`` (Haxe ``signals.Signal1``
 * inherits ``add`` + ``subscribe``; its dispatch method is minified per build).
 * A drifted build self-heals instead of failing every job. Exported for unit
 * tests.
 */
export function resolveSignalKey(
  instant: unknown,
  classF: unknown,
  preferredIndex: number,
): { key: string; index: number } | null {
  const protoKeys = Object.keys((classF as { prototype?: object })?.prototype ?? {});
  const instance = (instant ?? {}) as Record<string, unknown>;
  const isSignal = (v: unknown) =>
    !!v &&
    typeof (v as { add?: unknown }).add === "function" &&
    (typeof (v as { dispatch?: unknown }).dispatch === "function" ||
      typeof (v as { subscribe?: unknown }).subscribe === "function");
  const preferred = getProtoFn(classF, preferredIndex);
  if (preferred && isSignal(instance[preferred])) {
    return { key: preferred, index: preferredIndex };
  }
  // Prototype order first (its indexes are the ordinals callers reason
  // about), then own enumerable fields: a signal can be an instance field.
  const seen = new Set<string>();
  const keys: string[] = [];
  for (const key of [...protoKeys, ...Object.keys(instance)]) {
    if (seen.has(key)) continue;
    seen.add(key);
    keys.push(key);
  }
  for (let i = 0; i < keys.length; i += 1) {
    if (isSignal(instance[keys[i]])) {
      return { key: keys[i], index: protoKeys.indexOf(keys[i]) };
    }
  }
  return null;
}

/**
 * Battle ``type`` -> ``BattleConfigStorage`` getter. This bridge only runs ToE.
 *
 * The game's own ``TitanArenaBattleThread`` builds its presets with
 * ``get_titanClanPvp`` (``BattleThreadFactory`` maps ``titan_arena`` to
 * ``I.qj.f7a()``). The 2026-11-26 update (serverVersion 302) started
 * rejecting ``get_titanPvpManual`` progress as ``Invalid battle``, so the
 * offline sim must use the same config as the client.
 */
export function battleConfigFor(battleType: unknown): string {
  if (typeof battleType === "string" && battleType.includes("titan_arena")) {
    return "get_titanClanPvp";
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

export async function submitResult(
  jobId: string,
  account: string,
  result: unknown,
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
 * Why the most recent :func:`computeBattle` returned null. Sent along with
 * the dummy loss so Python can surface the real cause (ordinal drift, config
 * lookup failure, ...) without the browser console.
 */
let lastComputeError: string | null = null;

/**
 * Compute a titan battle using the in-page game engine.
 *
 * Returns ``null`` when the game bundle is not loaded, ``DataStorage`` is
 * unavailable, or the calc fails / times out. ``tick`` submits a fast loss
 * only after such a genuine calc failure on an engine frame.
 */
async function computeBattle(battle: unknown): Promise<BattleResultPayload | null> {
  lastComputeError = null;
  const game = pickGame() as unknown as Record<string, unknown> | null;
  // Same callable gate as the frame gate: a cohabitant may have replaced
  // window.Game between poll and compute.
  if (!game || !hasBattleEngine(game)) {
    lastComputeError = "no engine in this frame (pickGame miss)";
    log("computeBattle: no engine in this frame (pickGame miss)");
    return null;
  }
  try {
    // Ported from HerowarsHelper's working BattleCalc (ordinals pinned, see
    // getF/getFn/getProtoFn docs): presets take the ToE battle config
    // (get_titanClanPvp, matching the game's TitanArenaBattleThread) and the
    // client's isReplay flag; the instant play reports via a Haxe signal
    // (NOT DOM addEventListener), and results come from MultiBattleResult
    // getters.
    const b = battle as { progress?: unknown; type?: unknown };
    const dataStorage = game["DataStorage"] as Record<string, unknown>;
    log("computeBattle: resolving config");
    let configStorageKey: string;
    let configGetter: string;
    try {
      configStorageKey = getFn(dataStorage, 25);
      configGetter = getF(game["BattleConfigStorage"], battleConfigFor(b.type));
    } catch (e) {
      // Ordinal drift after a game update lands here — the key names above
      // identify which resolver broke, so compare with HerowarsHelper.
      lastComputeError = `engine ordinal resolution failed: ${String(e)}`;
      log("computeBattle: engine ordinal resolution failed:", e);
      return null;
    }
    const dataStores = dataStorage as unknown as Record<string, Record<string, () => unknown>>;
    log(`computeBattle: config key=${String(configStorageKey)} getter=${configGetter}`);
    let config: unknown;
    try {
      config = dataStores[configStorageKey][configGetter]();
    } catch (e) {
      lastComputeError = `config lookup failed: ${String(e)}`;
      log("computeBattle: config lookup failed:", e);
      return null;
    }
    log("computeBattle: presets next");
    // BattlePresets(isReplay, autoToggleable, autoOnStart, config, showBothTeams).
    // isReplay=false matches the client's instant/skip paths (dungeon skip,
    // TitanArenaRaid). isReplay=true would make BattleInstantPlay build the
    // BattleReseedSession from effects.ultReseed; an offline sim has no
    // battleSeed_next seeds/proof, so the server rejects that progress as
    // ``Invalid battle`` (2026-11-26, serverVersion 302).
    // showBothTeams only affects rendering, so the battle thread's true is
    // irrelevant here; the instant path uses false.
    const presets = new (
      game["BattlePresets"] as new (
        isReplay: boolean,
        autoToggleable: boolean,
        autoOnStart: boolean,
        config: unknown,
        showBothTeams: boolean,
      ) => unknown
    )(false, false, true, config, false);
    log("computeBattle: presets ok, instant next");
    const BattleInstantPlay = game["BattleInstantPlay"] as new (
      data: unknown,
      presets: unknown,
    ) => Record<string, unknown>;
    const MultiReplay = game["MultiBattleInstantReplay"];
    const instant: Record<string, unknown> =
      Array.isArray(b.progress) &&
      (b.progress as unknown[]).length > 1 &&
      typeof MultiReplay === "function"
        ? new (MultiReplay as new (data: unknown, presets: unknown) => Record<string, unknown>)(battle, presets)
        : new BattleInstantPlay(battle, presets);
    log("computeBattle: instant ok, subscribing");
    let result: BattleResultPayload | null = null;
    return new Promise<BattleResultPayload | null>((resolve) => {
      try {
        const preferredSignalIndex = 10;
        const signalSpec = resolveSignalKey(instant, game["BattleInstantPlay"], preferredSignalIndex);
        if (!signalSpec) {
          lastComputeError = "BattleInstantPlay completion signal not found (prototype ordinal drift)";
          log("computeBattle: no completion signal on BattleInstantPlay instance");
          resolve(null);
          return;
        }
        if (signalSpec.index !== preferredSignalIndex) {
          log(
            `computeBattle: signal ordinal drifted — using #${signalSpec.index} (${signalSpec.key}; pinned ${preferredSignalIndex})`,
          );
        }
        const signal = instant[signalSpec.key] as {
          add: (cb: (bi: unknown) => void) => void;
        };
        signal.add((battleInstant: unknown) => {
          // A wrongly-scanned listener must not throw uncaught; the timeout
          // below still turns a bad payload into a diagnosable dummy.
          try {
            const bi = battleInstant as Record<string, () => unknown>;
            const getters = bi[getF(game["BattleInstantPlay"], "get_result")]() as Record<string, () => unknown>;
            const progress = getters[getF(game["MultiBattleResult"], "get_progress")]() as BattleProgress[];
            const res = getters[getF(game["MultiBattleResult"], "get_result")]() as {
              win: boolean;
              stars: number;
            };
            result = { progress: progress ?? [], result: res ?? { win: false, stars: 0 } };
            resolve(result);
          } catch (e) {
            lastComputeError = `result extraction failed: ${String(e)}`;
            log("computeBattle: result extraction failed:", e);
            // Resolve now so the job's diagnostic is submitted immediately
            // instead of waiting out the 10s safety timeout.
            resolve(null);
          }
        });
        (instant["start"] as () => void).call(instant);
        // Safety timeout: if the engine never completes, resolve null so
        // tick submits the fast loss instead of hanging. Kept conditional so
        // a real failure diagnostic is not overwritten by the timeout one.
        setTimeout(() => {
          if (result === null) {
            if (!lastComputeError) {
              lastComputeError = "engine timed out without completing";
              log("computeBattle: engine timed out without completing");
            }
            resolve(null);
          }
        }, 10_000);
      } catch (e) {
        lastComputeError = `engine setup failed: ${String(e)}`;
        log("computeBattle threw:", e);
        resolve(null);
      }
    });
  } catch (e) {
    lastComputeError = `computeBattle outer error: ${String(e)}`;
    log("computeBattle outer error:", e);
    return null;
  }
}

// --- live ToE jobs ---------------------------------------------------------
// EXPERIMENTAL / 開発途中. Live solo/wall battles are the only v302 path (the
// server enforces the ultReseed proof), but they rely on minified game names
// (Ka.y7 / W5b / cmc / y7.DBo, with x7 / V5b / bmc / kBo accepted as fallbacks)
// that drift on every game update. Re-pin them when the game bundle changes;
// raids stay API/offline. This is not a finished ToE solution.

type AnyRecord = Record<string, any>;
type AnyFn = (...args: any[]) => any;
/**
 * First function-valued member among ``names`` (primary key first, fallback
 * keys after), or undefined. Minified game names vary; centralizing the lookup
 * keeps every call site on the same rule.
 */
function pickFn<T extends AnyFn>(obj: AnyRecord | undefined | null, ...names: string[]): T | undefined {
  if (!obj) return undefined;
  for (const name of names) {
    try {
      const value = obj[name];
      if (typeof value === "function") return value as T;
    } catch {}
  }
  return undefined;
}
type LiveJobResult = Record<string, unknown>;
let liveJobRunner: ((job: ToeJob) => Promise<LiveJobResult>) | null = null;
// Set while a live battle is being driven so the BattleController patch only
// forces Auto for those battles, never for the user's manual fights.
let liveAutoForced = false;
// Set by the BattleController patch when it could actually toggle Auto.
let liveAutoApplied = false;
// Tracks whether the BattleController.start wrapper is actually installed;
// included in live-battle timeouts so a shape drift is visible without eval.
let liveAutoPatchInstalled = false;
// Captured inside BattleController.start: the settings-model fields that are
// BooleanProperty instances (Auto candidates). Empty until a live battle runs.
let liveAutoShape = "";
// Set while a tier-transition reload is scheduled (or after this page's
// sessionStorage flag was written) until the page is torn down. Stops the
// interval from claiming the next job, which would die with the old page.
let liveReloadPending = false;

export type LiveDiagInput = {
  visibility: string;
  visibilityAtStart: string;
  controllerCaptured: boolean;
  autoPatchInstalled: boolean;
  autoApplied: boolean;
  autoForced: boolean;
  battleStarted: boolean;
};

/** Pure formatter so live-battle diagnostics can be unit-tested. */
export function formatLiveDiag(d: LiveDiagInput): string {
  return [
    `vis=${d.visibility}(start=${d.visibilityAtStart})`,
    `bc=${d.controllerCaptured ? 1 : 0}`,
    `patch=${d.autoPatchInstalled ? 1 : 0}`,
    `auto=${d.autoApplied ? 1 : 0}`,
    `forced=${d.autoForced ? 1 : 0}`,
    `start=${d.battleStarted ? 1 : 0}`,
  ].join(" ");
}

/** Current live-battle state; attached to timeout errors and the Dev handle. */
function liveDiag(visibilityAtStart = "n/a", battleStarted = false): string {
  try {
    const base = formatLiveDiag({
      visibility: typeof document !== "undefined" ? document.visibilityState : "n/a",
      visibilityAtStart,
      controllerCaptured: !!capturedClasses["BattleController"],
      autoPatchInstalled: liveAutoPatchInstalled,
      autoApplied: liveAutoApplied,
      autoForced: liveAutoForced,
      battleStarted,
    });
    const extra: string[] = [];
    if (liveAutoShape) extra.push(`shape=${liveAutoShape}`);
    return extra.length ? `${base} ${extra.join(" ")}` : base;
  } catch {
    return "diag unavailable";
  }
}

/**
 * Record which settings fields are BooleanProperty instances so a drift in the
 * minified Auto field name (``Qya``) is visible in the next timeout error.
 */
function captureAutoShape(settings: AnyRecord | undefined): void {
  if (!settings) {
    liveAutoShape = "no-settings";
    return;
  }
  try {
    const bp = capturedClasses["BooleanProperty"] as unknown as (new () => unknown) | undefined;
    const keys = new Set<string>();
    let obj: AnyRecord | null = settings;
    while (obj && obj !== Object.prototype) {
      for (const key of Object.getOwnPropertyNames(obj)) keys.add(key);
      obj = Object.getPrototypeOf(obj) as AnyRecord | null;
    }
    const boolKeys = [...keys].filter((key) => {
      try {
        const value = settings[key];
        return !!bp && value instanceof bp;
      } catch {
        return false;
      }
    });
    const hasQya = Object.prototype.hasOwnProperty.call(settings, "Qya") || keys.has("Qya");
    liveAutoShape = `n=${keys.size} qya=${hasQya ? 1 : 0} bool=${boolKeys.slice(0, 12).join(",")}`;
  } catch {
    liveAutoShape = "shape-error";
  }
}

/**
 * Force the game's Auto toggle on when a live battle controller starts.
 *
 * The client's Auto state is the ``BattleController`` settings model
 * ``Rr.Qya`` BooleanProperty (the same one its Auto button toggles). The
 * minified names are pinned to the current bundle; when they drift the patch
 * is skipped and the live job returns a clear diagnostic instead of clicking
 * blind canvas coordinates (the bottom-right x5 button is a real-money
 * Valkyrie's Favor purchase popup).
 */
function installLiveAutoPatch(): boolean {
  liveAutoPatchInstalled = false;
  try {
    const cls = capturedClasses["BattleController"] as unknown as { prototype?: AnyRecord } | undefined;
    const proto = cls?.prototype;
    if (!proto) return false;
    if (proto["__hwAutoPatch"]) {
      liveAutoPatchInstalled = true;
      return true;
    }
    const originalStart = proto["start"];
    if (typeof originalStart !== "function") return false;
    proto["start"] = function (this: AnyRecord, ...args: unknown[]) {
      const result = originalStart.apply(this, args);
      if (liveAutoForced) {
        try {
          const settings = this["Rr"] as AnyRecord | undefined;
          captureAutoShape(settings);
          const auto = settings?.["Qya"] as { ba?: (value: boolean) => void } | undefined;
          if (auto && typeof auto.ba === "function") {
            auto.ba(true);
            liveAutoApplied = true;
          }
          // Do NOT flip every BooleanProperty: some of the 18 toggles are not
          // Auto/Ult and one of them stalls the battle (observed: all battles
          // timed out when all were set). Only the known Auto toggle is forced.
        } catch {}
      }
      return result;
    };
    proto["__hwAutoPatch"] = true;
    liveAutoPatchInstalled = true;
    return true;
  } catch {
    return false;
  }
}

/**
 * Resolved in-page ToE data + command model.
 *
 * ``commands`` is the Haxe command list. It is resolved from ``Ka.y7`` when
 * present, falling back to ``Ka.x7``; ``commandKey`` records the property that
 * supplied it.
 */
export type ToeModel = {
  toe: AnyRecord;
  commands: AnyRecord;
  commandKey: "y7" | "x7";
  commandRoot: AnyRecord;
};

/** Resolve the in-page ToE data + command model, or null before the game loads. */
export function resolveToeModel(): ToeModel | null {
  const gameModelClass = capturedClasses["GameModel"] as unknown as { F?: () => AnyRecord } | undefined;
  const model = gameModelClass?.F?.();
  const player = model?.["player"] as AnyRecord | undefined;
  const toe = player?.["cl"] as AnyRecord | undefined;
  const commandRoot = model?.["Ka"] as AnyRecord | undefined;
  // Resolve the ToE command list among the known keys (y7, then x7).
  let commandKey: "y7" | "x7" = "x7";
  let commands: AnyRecord | undefined;
  if (commandRoot?.["y7"]) {
    commandKey = "y7";
    commands = commandRoot["y7"] as AnyRecord;
  } else if (commandRoot?.["x7"]) {
    commandKey = "x7";
    commands = commandRoot["x7"] as AnyRecord;
  }
  return toe && commands && commandRoot ? { toe, commands, commandKey, commandRoot } : null;
}

/**
 * Wait for the game model after a page reload.
 *
 * Battles keep the page, but a tier transition reloads it (see the reload
 * action) and can queue a job while the fresh page is still booting — the
 * captured classes exist but ``GameModel.F().player.cl`` does not yet.
 * Failing instantly there made the CLI skip a perfectly good team as "ToE model
 * unavailable", so poll instead.
 */
export async function waitForToeModel(timeoutMs: number): Promise<ToeModel | null> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const resolved = resolveToeModel();
    if (resolved) return resolved;
    if (Date.now() >= deadline) return null;
    await sleep(500);
  }
}

/**
 * Wait for the ToE rival list to include ``rivalId``.
 *
 * Right after a reload the model exists but its status fetch may still be in
 * flight, so the rivals getter (``W5b``, with ``V5b`` as fallback) is empty.
 * Refresh with ``mKl`` (or ``WJl`` when present) and retry instead of failing
 * with "rival not found" (which aborts the wall in the CLI).
 */
export async function waitForRival(
  toe: AnyRecord,
  rivalId: string | number,
  timeoutMs: number,
): Promise<AnyRecord | null> {
  const want = String(rivalId);
  const deadline = Date.now() + timeoutMs;
  let lastRefresh = 0;
  // Single one-time-clear flag: drives both the first-refresh clear and the
  // timeout restore (``lastRefresh === 0`` is not a reliable "first" signal).
  let cleared = false;
  let prevVge: unknown;
  let prevDtd: unknown;
  for (;;) {
    try {
      // Resolve the rivals getter among the known keys (W5b, then V5b).
      // Bind `this` to the model: these are Haxe instance methods and calling
      // the extracted function unbound loses `this.Dtd`.
      const getRivals = pickFn<(this: AnyRecord) => AnyRecord[]>(toe, "W5b", "V5b");
      const rivals = getRivals?.call(toe) || [];
      const found = rivals.find((r) => String(r?.["G"]?.()) === want);
      if (found) return found;
    } catch {}
    const now = Date.now();
    if (now - lastRefresh >= 3000) {
      // Resolve the status refresh BEFORE clearing anything: a bundle with
      // neither name must never have its usable rival list destroyed.
      const refresh = pickFn<(this: AnyRecord) => unknown>(toe, "mKl", "WJl");
      if (typeof refresh === "function") {
        if (!cleared) {
          cleared = true;
          // update() rebuilds the rivals list (Dtd) only when nextDayTs/tier
          // change. After a daily reset that keeps the same tier, the wall ids
          // change but update() matches the existing list by id, so the new
          // walls never appear. Clear once so update() rebuilds.
          try {
            prevVge = toe["Vge"];
            prevDtd = toe["Dtd"];
            toe["Vge"] = null;
            toe["Dtd"] = null;
          } catch {}
        }
        try {
          refresh.call(toe);
        } catch {}
      }
      lastRefresh = now;
    }
    if (now >= deadline) {
      // Give up. Restore only when the rebuild never happened: a successful
      // refresh may legitimately produce a different wall list (daily reset)
      // and must be kept for later jobs.
      const rebuilt = Array.isArray(toe["Dtd"]);
      if (cleared && !rebuilt) {
        try {
          toe["Vge"] = prevVge;
        } catch {}
        try {
          toe["Dtd"] = prevDtd;
        } catch {}
      }
      return null;
    }
    await sleep(500);
  }
}

// Survives the post-battle reload: after a fresh page boots, the userscript must
// not claim the next live job until the ToE rival data is actually loaded, or
// ``cmc`` (``bmc`` as fallback) cannot find the rival ("rival not found").
// sessionStorage is cleared as soon as the data is ready.
export const RELOAD_PENDING_KEY = "hwgenie-toe-reload-pending";
// Give up gating after this long so a tab that never loads ToE data (tier
// complete screen, city screen) cannot wedge the job queue forever.
const RELOAD_PENDING_MAX_MS = 180000;
// Navigation is delayed so the reload job's result POST can complete first.
const RELOAD_NAVIGATION_DELAY_MS = 1500;
// If the navigation is blocked (e.g. a beforeunload dialog), stop wedging the
// tab after this long.
const RELOAD_NAVIGATION_WATCHDOG_MS = 10000;

function markReloadPending(): void {
  try {
    sessionStorage.setItem(RELOAD_PENDING_KEY, String(Date.now()));
  } catch {}
}

function reloadPending(): boolean {
  try {
    const raw = sessionStorage.getItem(RELOAD_PENDING_KEY);
    if (!raw) return false;
    const markedAt = Number(raw);
    if (!Number.isFinite(markedAt) || Date.now() - markedAt > RELOAD_PENDING_MAX_MS) {
      clearReloadPending();
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

function clearReloadPending(): void {
  try {
    sessionStorage.removeItem(RELOAD_PENDING_KEY);
  } catch {}
}

/**
 * Stop this page from claiming more jobs and mark the reload as pending.
 *
 * Call this *before* acknowledging the reload job; the navigation itself is
 * scheduled afterwards by {@link scheduleReloadNavigation} so a slow result
 * POST is not aborted by the page unloading.
 */
export function blockReloadClaims(): void {
  liveReloadPending = true;
  markReloadPending();
}

/**
 * Schedule the tier-transition navigation.
 *
 * ``liveReloadPending`` covers the delay window; the sessionStorage flag covers
 * the fresh page's boot. A watchdog releases the gate if the navigation never
 * happens (blocked dialog), so the tab does not stop claiming forever.
 */
export function scheduleReloadNavigation(): void {
  setTimeout(() => {
    try {
      location.reload();
    } catch {}
  }, RELOAD_NAVIGATION_DELAY_MS);
  setTimeout(() => {
    if (!liveReloadPending) return;
    liveReloadPending = false;
    clearReloadPending();
  }, RELOAD_NAVIGATION_WATCHDOG_MS);
}

/** True once the in-page ToE rival list is populated (post-reload readiness). */
export function toeDataReady(): boolean {
  const model = resolveToeModel();
  if (!model) return false;
  try {
    const getRivals = pickFn<(this: AnyRecord) => AnyRecord[]>(model.toe, "W5b", "V5b");
    const rivals = getRivals?.call(model.toe);
    return Array.isArray(rivals) && rivals.length > 0;
  } catch {
    return false;
  }
}

/**
 * Drive a real ToE battle through the game's own client.
 *
 * The live path is required after the 2026-11-26 update: offline instant
 * simulations cannot produce the ``battleSeed_next`` proof the server now
 * validates. ``PlayerTitanArenaData.cmc`` (``bmc`` as fallback) runs the real
 * battle thread and the client submits ``titanArenaEndBattle`` with a valid
 * proof by itself. Tier transitions are a separate ``reload`` job handled by
 * ``tick``.
 */
async function runLiveJob(job: ToeJob, resolveAccount: () => string): Promise<LiveJobResult> {
  if (!hasLocalEngine()) return { ok: false, error: "no engine" };
  // Own-tab guard: live jobs act on the page's logged-in game state, so the
  // account must be readable and match exactly. No jobAccount fallback.
  const myAccount = resolveAccount();
  if (!myAccount || String(job.account) !== String(myAccount)) {
    return { ok: false, error: `account mismatch (job=${job.account} tab=${myAccount})` };
  }
  if (job.action !== "battle") return { ok: false, error: "unknown live action" };
  liveAutoForced = false;
  liveAutoApplied = false;
  const resolvedModel = await waitForToeModel(25000);
  if (!resolvedModel) return { ok: false, error: "ToE model unavailable" };
  const { toe, commands, commandKey, commandRoot } = resolvedModel;
  const titans = Array.isArray(job.titans) ? job.titans.map((v) => Number(v)).filter((v) => Number.isFinite(v)) : [];
  if (titans.length !== 5) return { ok: false, error: "bad titans" };

  // Python may have advanced the tier with CompleteTier between jobs; refresh
  // the in-page ToE model before looking up the rival. waitForRival does the
  // status refresh (and a one-time rivals rebuild) itself.
  const want = String(job.rivalId ?? "");
  const rival = await waitForRival(toe, want, 20000);
  if (!rival) {
    let have: string[] = [];
    try {
      // Read the current list after waitForRival. Its timeout restore only
      // puts the old list back when no rebuild happened, so a successful
      // rebuild is reported here instead of the pre-clear walls.
      const getRivals = pickFn<(this: AnyRecord) => AnyRecord[]>(toe, "W5b", "V5b");
      have = ((getRivals?.call(toe) || []) as AnyRecord[])
        .slice(0, 12)
        .map((r) => String(r?.["G"]?.()));
    } catch {}
    try {
      const vge = toe["Vge"];
      // Never serialize a raw game object: it may be circular. typeof/String
      // keeps the diagnostic JSON-safe.
      const vgeDiag = vge === null || vge === undefined ? String(vge) : typeof vge === "object" ? "object" : String(vge);
      const href =
        typeof location !== "undefined" ? `${location.origin}${location.pathname}` : null;
      localStorage.setItem(
        TOE_DIAG_KEY,
        JSON.stringify({
          want,
          have,
          hidden: typeof document !== "undefined" ? document.hidden : null,
          vis: typeof document !== "undefined" ? document.visibilityState : null,
          href,
          y7: typeof commandRoot["y7"],
          x7: typeof commandRoot["x7"],
          commandKey,
          cmc: typeof toe["cmc"],
          dbo: typeof commands["DBo"],
          kbo: typeof commands["kBo"],
          w5b: typeof toe["W5b"],
          v5b: typeof toe["V5b"],
          dtd: Array.isArray(toe["Dtd"]) ? toe["Dtd"].length : typeof toe["Dtd"],
          vge: vgeDiag,
          mkl: typeof toe["mKl"],
          wjl: typeof toe["WJl"],
        }),
      );
      log(`wrote ${TOE_DIAG_KEY} (want=${want} have=${have.join(",")})`);
    } catch {}
    return {
      ok: false,
      error: `rival not found in ToE model (want=${want} have=${have.join(",")})`,
    };
  }

  return await new Promise<LiveJobResult>((resolve) => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const visibilityAtStart = typeof document !== "undefined" ? document.visibilityState : "n/a";
    let battleStarted = false;
    // Resolve the EndBattle command among the known keys (DBo, then kBo) first
    // so an unavailable method fails with a diagnostic instead of wrapping
    // undefined.
    const prevEnd = pickFn<(this: unknown, ...args: unknown[]) => AnyRecord>(commands, "DBo", "kBo");
    const endKey = prevEnd && commands["DBo"] === prevEnd ? "DBo" : "kBo";
    const finish = (value: LiveJobResult) => {
      if (settled) return;
      settled = true;
      liveAutoForced = false;
      liveAutoApplied = false;
      if (timer) clearTimeout(timer);
      // Only restore when there was something to wrap; assigning undefined
      // would create an own ``kBo`` property on the game object.
      if (typeof prevEnd === "function") {
        try {
          commands[endKey] = prevEnd;
        } catch {}
      }
      resolve(value);
    };
    if (typeof prevEnd !== "function") {
      finish({
        ok: false,
        error: `endbattle method unavailable (${liveDiag(visibilityAtStart, battleStarted)})`,
      });
      return;
    }
    try {
      commands[endKey] = function (this: unknown, ...args: unknown[]) {
        const cmd = prevEnd.apply(this, args) as AnyRecord;
        try {
          const originalCc = cmd["cc"];
          cmd["cc"] = function (this: unknown, cb: (r: unknown) => unknown) {
            return originalCc.call(this, (res: AnyRecord) => {
              try {
                const err = typeof res?.["c7a"] === "function" ? res["c7a"]() : null;
                const ok = typeof res?.["MI"] === "function" ? !!res["MI"]() : !err;
                let result: unknown = undefined;
                try {
                  const zk = typeof res?.["zk"] === "function" ? res["zk"]() : null;
                  if (zk && typeof zk["ag"] === "function") result = zk["ag"]();
                } catch {}
                finish({ ok: !!ok && !err, error: err || undefined, result, rivalId: String(job.rivalId), action: "battle" });
              } catch (e) {
                finish({ ok: false, error: String(e) });
              }
              return cb(res);
            });
          };
        } catch {}
        return cmd;
      };
    } catch (e) {
      finish({ ok: false, error: `wrap failed (${liveDiag(visibilityAtStart, battleStarted)}): ${String(e)}` });
      return;
    }
    liveAutoForced = true;
    liveAutoApplied = false;
    liveAutoShape = "";
    if (!installLiveAutoPatch()) {
      // Never click blind canvas coordinates: the bottom-right x5 control is a
      // real-money Valkyrie's Favor purchase popup. Without the settings-model
      // Auto patch the battle would stall, so fail with a clear diagnostic.
      finish({
        ok: false,
        error: `auto patch unavailable (${liveDiag(visibilityAtStart, battleStarted)}); update the Dev userscript / game bundle`,
      });
      return;
    }
    timer = setTimeout(
      () => finish({ ok: false, error: `live battle timeout (${liveDiag(visibilityAtStart, battleStarted)})` }),
      180000,
    );
    if (visibilityAtStart === "hidden") {
      log("live battle starts in a hidden tab; the game loop may not advance (bring the game tab to the foreground)");
    }
    try {
      // StartBattle: cmc (bmc as fallback), using the f3b team config.
      const startBattle = pickFn<(this: AnyRecord, r: AnyRecord, cfg: AnyRecord) => unknown>(
        toe,
        "cmc",
        "bmc",
      );
      if (typeof startBattle !== "function") {
        finish({ ok: false, error: `start method unavailable (${liveDiag(visibilityAtStart, battleStarted)})` });
        return;
      }
      startBattle.call(toe, rival, { f3b: () => titans });
      battleStarted = true;
    } catch (e) {
      finish({ ok: false, error: `start failed (${liveDiag(visibilityAtStart, battleStarted)}): ${String(e)}` });
    }
  });
}

export async function tick(
  account: string,
  baseUrl: string,
  maxJobs = 1,
  shouldStop?: () => boolean,
): Promise<void> {
  // Engine-less frames must never claim: claiming without the ability to
  // compute would consume the job with a dummy loss before the engine frame
  // sees it. The auth-server reclaim timeout is only a safety net.
  if (!hasLocalEngine()) {
    return;
  }
  const limit = Number.isFinite(maxJobs) ? Math.max(1, Math.floor(maxJobs)) : 1;
  // Drain up to `limit` jobs: when Python queued a whole raid up front, the
  // simulations run back-to-back instead of one per poll interval.
  for (let handled = 0; handled < limit; handled += 1) {
    // A drain can outlast the interval; stop() must interrupt it instead of
    // claiming more jobs after teardown.
    if (shouldStop?.()) {
      return;
    }
    // A live battle just finished and this page is about to reload; claim
    // nothing else here or the new job dies with the page.
    if (liveReloadPending) {
      return;
    }
    // After that reload, wait until the fresh page has the ToE rival data before
    // claiming, otherwise cmc (bmc as fallback) cannot resolve the rival
    // ("rival not found"). Only clear the flag from a tick that is not
    // scheduling a reload.
    if (!liveReloadPending && reloadPending()) {
      if (!toeDataReady()) {
        return;
      }
      clearReloadPending();
    }
    const job = await pollNextJob(account, baseUrl);
    if (!job) {
      return;
    }
    log(`claimed job ${job.id} (kind=${job.kind ?? "sim"}${job.action ? ` action=${job.action}` : ""})`);
    if (job.kind === "live") {
      if (job.action === "reload") {
        // Tier-transition reload: block claims first, acknowledge the job, and
        // only then schedule the navigation so a slow POST is not aborted.
        blockReloadClaims();
        await submitWithRetry(job.id, account, { ok: true, action: "reload" }, baseUrl);
        scheduleReloadNavigation();
        return;
      }
      let live: LiveJobResult;
      try {
        live = liveJobRunner
          ? await liveJobRunner(job)
          : { ok: false, error: "live engine not installed" };
      } catch (e) {
        // Never leave a live job in_flight: an unexpected throw (e.g. a shape
        // drift in the game bundle) must still release the CLI's 240s wait.
        live = { ok: false, error: `live runner threw (${liveDiag()}): ${String(e)}` };
      }
      await submitWithRetry(job.id, account, live, baseUrl);
      // A reload scheduled by the runner (or a reload action above) must stop
      // this drain before another /toe/next claim.
      if (liveReloadPending) {
        return;
      }
      if (job.action === "battle") {
        // Keep the loaded page: give the battle thread a moment to settle so
        // the next job reuses the already-loaded ToE model. The result popup
        // is left to the game (no synthetic canvas clicks).
        await sleep(2000);
      }
      continue;
    }
    const computed = await computeBattle(job.battle);
    if (!computed) {
      // The engine exists but this battle failed to compute (calc error or
      // timeout). Submit a fast loss so Python proceeds instead of waiting
      // out its full bridge timeout. ``error`` carries the failing stage so
      // the CLI can diagnose an ordinal drift without the browser console.
      log(`job ${job.id}: compute failed, submitting dummy loss`);
      await submitWithRetry(job.id, account, {
        progress: [{ attackers: { heroes: {} }, defenders: { heroes: {} } }],
        result: { win: false, stars: 0 },
        error: lastComputeError ?? "compute failed",
      }, baseUrl);
      continue;
    }
    log(`job ${job.id}: complete win=${computed.result.win} stars=${computed.result.stars} rounds=${computed.progress.length}`);
    await submitWithRetry(job.id, account, computed, baseUrl);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function submitWithRetry(
  jobId: string,
  account: string,
  result: unknown,
  baseUrl: string,
): Promise<boolean> {
  let ok = await submitResult(jobId, account, result, baseUrl);
  if (!ok) {
    // The job may have expired (TTL cleanup → 404). Retry once after 1s so
    // a transient window doesn't silently drop a computed battle.
    log(`job ${jobId}: submit failed, retrying once after 1s`);
    await sleep(1000);
    ok = await submitResult(jobId, account, result, baseUrl);
    if (!ok) {
      log(`job ${jobId}: submit retry failed (job may have expired)`);
    }
  }
  return ok;
}

/**
 * DEV-only in-page debug handle for `eval`-driven development.
 *
 * Normal builds never install it. It exposes only refs the page already owns
 * (captured game classes, the local engine gate, the offline sim) so an agent
 * operating the real game tab can inspect the engine without guessing at
 * minified globals. Do not put credentials or auth headers here.
 */
function exposeDebugHandle(): void {
  if (!TOE_ENABLED) return;
  try {
    const w = window as unknown as Record<string, unknown>;
    w.__hwGenieToe = {
      captured: () => capturedClasses,
      capturedNames: () => capturedClassNames(),
      hasEngine: () => hasLocalEngine(),
      engine: () => capturedEngine(),
      diag: () => realmDiag(),
      liveDiag: () => liveDiag(),
      compute: (battle: unknown) => computeBattle(battle),
    };
  } catch {}
}

export function installToeBridge(opts: { authServerUrl?: string; pollIntervalMs?: number; jobsPerTick?: number } = {}): () => void {
  // Normal builds never poll (Goodwin separation). Return the stop handle
  // shape so callers need no variant branching.
  if (!TOE_ENABLED) {
    return () => {};
  }
  exposeDebugHandle();
  const baseUrl = (opts.authServerUrl ?? AUTH_SERVER_URL).replace(/\/+$/, "");
  const interval = opts.pollIntervalMs ?? POLL_INTERVAL_MS;
  const jobsPerTick = opts.jobsPerTick ?? JOBS_PER_TICK;
  let stopped = false;
  // A drain of several jobs can outlast the poll interval; without this guard
  // the next interval callback would start a second, concurrent drain that
  // runs overlapping BattleInstantPlay calls in the same tab.
  let running = false;
  let timer: ReturnType<typeof setInterval> | null = null;

  // Capture game classes as the bundle registers them (HWH-independent).
  ensureEngineBridge();

  // The script needs to know which account to claim jobs for. We pull it
  // from the game's user object so the user doesn't have to configure it.
  function readAccount(): string {
    // Live jobs are keyed by the auth x-auth-user-id (what the Python CLI
    // sends), not by the in-game player id. The auth capture side records it
    // on every API request; prefer it so live jobs match the right tab.
    try {
      const authId = localStorage.getItem(AUTH_USER_ID_STORAGE_KEY);
      if (authId) return String(authId);
    } catch {}
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
    // Captured Haxe GameModel (works without HWH's window.Game).
    try {
      const gm = capturedClasses["GameModel"] as unknown as { F?: () => unknown } | undefined;
      const model = gm?.F?.() as Record<string, unknown> | undefined;
      const player = model?.["player"] as Record<string, unknown> | undefined;
      if (player) {
        const cls = (player as { __class__?: { prototype?: { __properties__?: Record<string, string> } } }).__class__;
        const props = cls?.prototype?.__properties__ || {};
        const key = Object.entries(props).find(([, v]) => v === "get_id")?.[0];
        const raw = key && typeof player[key] === "function" ? (player[key] as () => unknown)() : player["id"];
        if (raw !== undefined && raw !== null && String(raw)) return String(raw);
      }
    } catch {}
    return "";
  }

  liveJobRunner = (job) => runLiveJob(job, readAccount);

  if (!readAccount()) {
    log("installToeBridge: no player id on window.Game yet; will retry on every poll");
  }

  let lastNoEngineLog = 0;
  let lastHiddenLog = 0;
  let engineLogged = false;
  async function loop(): Promise<void> {
    if (stopped || running) {
      return;
    }
    running = true;
    try {
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
      // This gate is broad: every claim (sim jobs included) stops while the
      // tab is hidden. A hidden tab's requestAnimationFrame loop is throttled
      // or stopped, so claiming there would consume jobs it cannot finish and
      // starve the visible tab. It only covers the claim boundary — a battle
      // already claimed when the tab hides still stalls until its timeout.
      if (typeof document !== "undefined" && document.hidden) {
        const now = Date.now();
        if (now - lastHiddenLog > 60000) {
          lastHiddenLog = now;
          log("tab hidden; not claiming");
        }
        return;
      }
      // Re-read on every poll: the auth-capture key may appear after login
      // (this tab is then picked up without an install/reload cycle).
      await tick(readAccount(), baseUrl, jobsPerTick, () => stopped);
    } finally {
      running = false;
    }
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
