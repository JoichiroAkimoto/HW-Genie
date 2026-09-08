// Regression tests for the ToE bridge engine gate (toe-bridge.ts).
//
// Background: frames without the game engine (e.g. the top window while the
// game lives in a cross-origin iframe) must never claim jobs. Previously the
// top window claimed first and submitted a dummy loss, consuming the job
// before the engine frame could compute the real result — which made it look
// like the Titan Arena screen had to be open.
import { test } from "node:test";
import assert from "node:assert";
import { __resetBridgeForTests, battleConfigFor, capturedClassNames, ensureEngineBridge, getF, getFn, getProtoFn, hasBattleEngine, hasLocalEngine, realmDiag, safeGameOf, submitResult, submitWithRetry, tick } from "../toe-bridge.ts";

const FULL_ENGINE = {
  BattlePresets: function () {},
  BattleInstantPlay: function () {},
  DataStorage: {},
};

test("nullish or empty game objects have no engine", () => {
  assert.strictEqual(hasBattleEngine(null), false);
  assert.strictEqual(hasBattleEngine(undefined), false);
  assert.strictEqual(hasBattleEngine({}), false);
});

test("missing BattlePresets means no engine", () => {
  const { BattlePresets, ...rest } = FULL_ENGINE;
  assert.strictEqual(hasBattleEngine(rest), false);
});

test("missing BattleInstantPlay means no engine", () => {
  const { BattleInstantPlay, ...rest } = FULL_ENGINE;
  assert.strictEqual(hasBattleEngine(rest), false);
});

test("missing DataStorage means no engine", () => {
  const { DataStorage, ...rest } = FULL_ENGINE;
  assert.strictEqual(hasBattleEngine(rest), false);
});

test("full engine object is detected", () => {
  assert.strictEqual(hasBattleEngine(FULL_ENGINE), true);
});

test("hasLocalEngine is false with no game window (bare node env)", () => {
  __resetBridgeForTests();
  // No window/document globals here, so no candidate can carry an engine.
  assert.strictEqual(hasLocalEngine(), false);
});

test("tick performs zero fetch when no local engine", async () => {
  __resetBridgeForTests();
  const calls = [];
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (...args) => {
    calls.push(args);
    throw new Error("tick must not fetch without an engine");
  };
  try {
    await tick("", "http://127.0.0.1:1");
  } finally {
    if (origFetch === undefined) delete globalThis.fetch;
    else globalThis.fetch = origFetch;
  }
  assert.strictEqual(calls.length, 0);
});

test("safeGameOf returns Game or the object itself without throwing", () => {
  const game = { id: 1 };
  assert.strictEqual(safeGameOf({ Game: game }), game);
  assert.deepStrictEqual(safeGameOf({}), {});
  assert.strictEqual(safeGameOf(null), null);
  assert.strictEqual(safeGameOf(undefined), undefined);
});

test("getF resolves minified key via __properties__", () => {
  const cls = { prototype: { __properties__: { a1: "get_result", b2: "other" } } };
  assert.strictEqual(getF(cls, "get_result"), "a1");
  assert.throws(() => getF(cls, "missing"), /not found/);
});

test("getFn/getProtoFn resolve by ordinal", () => {
  assert.strictEqual(getFn({ x: 1, y: 2 }, 1), "y");
  assert.strictEqual(getProtoFn({ prototype: { p: 1, q: 2 } }, 0), "p");
});

test("battleConfigFor maps titan_arena to manual PvP", () => {
  assert.strictEqual(battleConfigFor("titan_arena"), "get_titanPvpManual");
  assert.strictEqual(battleConfigFor("titan_arena_wall"), "get_titanPvpManual");
});

test("safeGameOf swallows cross-origin access throws", () => {
  // A cross-origin WindowProxy throws DOMException on any property read.
  // readAccount maps every candidate through safeGameOf, so this must never
  // propagate (previously: `tick error: Permission denied...` every second
  // once the game iframe loaded).
  const evil = new Proxy(
    {},
    {
      get() {
        throw new Error("Permission denied to access property");
      },
    },
  );
  assert.strictEqual(safeGameOf(evil), undefined);
});

test("ensureEngineBridge is a safe no-op without a DOM", () => {
  __resetBridgeForTests();
  // bun/node env: no window global must not throw.
  assert.strictEqual(ensureEngineBridge(), undefined);
});

test("ensureEngineBridge captures class registration via traps", () => {
  __resetBridgeForTests();
  const prevWindow = globalThis.window;
  const props = [
    "game.battle.controller.thread.BattlePresets",
    "game.battle.controller.instant.BattleInstantPlay",
    "game.battle.controller.instant.MultiBattleInstantReplay",
    "game.battle.controller.MultiBattleResult",
    "game.data.storage.DataStorage",
    "game.data.storage.battle.BattleConfigStorage",
  ];
  globalThis.window = {};
  try {
    ensureEngineBridge();
    function FakePresets() {}
    const holder = {};
    // Simulate the Haxe bundle registering the class on some object.
    holder["game.battle.controller.thread.BattlePresets"] = FakePresets;
    // The ref lands in our own store and is published into the shared
    // window.Game (HWH-compatible, for Goodwin-style dependents).
    assert.ok(capturedClassNames().includes("BattlePresets"));
    assert.strictEqual(globalThis.window.Game["BattlePresets"], FakePresets);
    // Game keeps working: the holder carries a plain own data property,
    // exactly as if no trap had ever existed (Goodwin-safe: enumeration,
    // wrapping assignments and re-reads all behave natively).
    assert.strictEqual(holder["game.battle.controller.thread.BattlePresets"], FakePresets);
    assert.ok(Object.keys(holder).includes("game.battle.controller.thread.BattlePresets"));
    assert.ok(!Object.keys(holder).some((k) => k.endsWith("_")));
  } finally {
    for (const p of props) {
      try {
        delete Object.prototype[p];
      } catch {}
    }
    if (prevWindow === undefined) delete globalThis.window;
    else globalThis.window = prevWindow;
  }
});

test("realmDiag reports without throwing in bare env", () => {
  __resetBridgeForTests();
  const line = realmDiag();
  assert.match(line, /trapsOwned=\d+ captured=\d+ engine=false/);
});

test("trap capture feeds capturedClassNames (no window.Game needed)", () => {
  __resetBridgeForTests();
  const prevWindow = globalThis.window;
  // Frozen window: gameBridge() cannot attach Game, but the trap's own
  // capturedClasses store must still receive the class.
  globalThis.window = Object.freeze({});
  const props = [
    "game.battle.controller.thread.BattlePresets",
    "game.battle.controller.instant.BattleInstantPlay",
    "game.data.storage.DataStorage",
  ];
  try {
    ensureEngineBridge();
    function FakePresets() {}
    const holder = {};
    holder["game.battle.controller.thread.BattlePresets"] = FakePresets;
    assert.ok(capturedClassNames().includes("BattlePresets"));
    assert.strictEqual(holder["game.battle.controller.thread.BattlePresets"], FakePresets);
  } finally {
    for (const p of props) {
      try {
        delete Object.prototype[p];
      } catch {}
    }
    if (prevWindow === undefined) delete globalThis.window;
    else globalThis.window = prevWindow;
  }
});

test("traps stay installed for the session (HWH parity)", () => {
  // Removing traps mid-boot stalls game loading at 1-2%: the runtime may
  // resolve classes via these paths lazily. Like HWH, traps persist.
  __resetBridgeForTests();
  const prevWindow = globalThis.window;
  const props = [
    "game.battle.controller.thread.BattlePresets",
    "game.battle.controller.instant.BattleInstantPlay",
    "game.battle.controller.instant.MultiBattleInstantReplay",
    "game.battle.controller.MultiBattleResult",
    "game.data.storage.DataStorage",
    "game.data.storage.battle.BattleConfigStorage",
  ];
  globalThis.window = {};
  try {
    ensureEngineBridge();
    const holder = {};
    for (const p of props) {
      holder[p] = function () {};
    }
    for (const p of props) {
      const d = Object.getOwnPropertyDescriptor(Object.prototype, p);
      assert.ok(d && typeof d.set === "function", `${p} trap persists`);
    }
    // Values are plain own data properties; no ghost keys leak anywhere.
    assert.strictEqual(typeof holder[props[0]], "function");
    assert.ok(!Object.keys(holder).some((k) => k.endsWith("_")));
  } finally {
    for (const p of props) {
      try {
        delete Object.prototype[p];
      } catch {}
    }
    if (prevWindow === undefined) delete globalThis.window;
    else globalThis.window = prevWindow;
  }
});

test("non-function BattlePresets never passes the engine gate", () => {
  __resetBridgeForTests();
  const prevWindow = globalThis.window;
  globalThis.window = {};
  try {
    ensureEngineBridge();
    const holder = {};
    // Pollute captures with non-callable classes (trap self-capture bug).
    holder["game.battle.controller.thread.BattlePresets"] = { notAFunction: true };
    holder["game.battle.controller.instant.BattleInstantPlay"] = { notAFunction: true };
    holder["game.data.storage.DataStorage"] = {};
    assert.strictEqual(hasBattleEngine({ BattlePresets: {}, BattleInstantPlay: {}, DataStorage: {} }), false);
    assert.strictEqual(hasLocalEngine(), false);
  } finally {
    for (const p of [
      "game.battle.controller.thread.BattlePresets",
      "game.battle.controller.instant.BattleInstantPlay",
      "game.data.storage.DataStorage",
    ]) {
      try { delete Object.prototype[p]; } catch {}
    }
    if (prevWindow === undefined) delete globalThis.window;
    else globalThis.window = prevWindow;
    __resetBridgeForTests();
  }
});

test("double ensureEngineBridge does not mis-log owned traps", () => {
  __resetBridgeForTests();
  const prevWindow = globalThis.window;
  globalThis.window = {};
  const logs = [];
  const origLog = console.log;
  console.log = (...a) => { logs.push(a.join(" ")); };
  try {
    ensureEngineBridge();
    ensureEngineBridge();
    assert.ok(!logs.some((l) => l.includes("already owned")), `no owned log on re-install, got: ${logs}`);
  } finally {
    console.log = origLog;
    for (const p of [
      "game.battle.controller.thread.BattlePresets",
      "game.battle.controller.instant.BattleInstantPlay",
      "game.battle.controller.instant.MultiBattleInstantReplay",
      "game.battle.controller.MultiBattleResult",
      "game.data.storage.DataStorage",
      "game.data.storage.battle.BattleConfigStorage",
    ]) {
      try { delete Object.prototype[p]; } catch {}
    }
    if (prevWindow === undefined) delete globalThis.window;
    else globalThis.window = prevWindow;
    __resetBridgeForTests();
  }
});

test("submitWithRetry retries once after failure", async () => {
  let calls = 0;
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    calls += 1;
    if (calls === 1) return { ok: false, status: 404 };
    return { ok: true, status: 200 };
  };
  try {
    const ok = await submitWithRetry("jid", "acc", { progress: [], result: { win: true, stars: 3 } }, "http://127.0.0.1:1");
    assert.strictEqual(ok, true);
    assert.strictEqual(calls, 2);
  } finally {
    if (origFetch === undefined) delete globalThis.fetch;
    else globalThis.fetch = origFetch;
  }
});

test("submitWithRetry reports failure after second miss", async () => {
  let calls = 0;
  const origFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    calls += 1;
    return { ok: false, status: 404 };
  };
  try {
    const ok = await submitWithRetry("jid", "acc", { progress: [], result: { win: false, stars: 0 } }, "http://127.0.0.1:1");
    assert.strictEqual(ok, false);
    assert.strictEqual(calls, 2);
  } finally {
    if (origFetch === undefined) delete globalThis.fetch;
    else globalThis.fetch = origFetch;
  }
});

test("tick retries submit once on 404 (injected failing fetch)", async () => {
  __resetBridgeForTests();
  const prevWindow = globalThis.window;
  globalThis.window = { Game: { BattlePresets: function () {}, BattleInstantPlay: function () {}, DataStorage: {} } };
  const origFetch = globalThis.fetch;
  let submits = 0;
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    if (u.includes("/toe/next")) {
      return { ok: true, status: 200, json: async () => ({ id: "j1", account: "", battle: { type: "titan_arena" } }) };
    }
    if (u.includes("/result")) {
      submits += 1;
      if (submits === 1) return { ok: false, status: 404 };
      return { ok: true, status: 200 };
    }
    return { ok: false, status: 404 };
  };
  try {
    await tick("", "http://127.0.0.1:1");
    assert.strictEqual(submits, 2);
  } finally {
    globalThis.fetch = origFetch;
    if (prevWindow === undefined) delete globalThis.window;
    else globalThis.window = prevWindow;
    __resetBridgeForTests();
  }
});
