// Regression tests for the ToE bridge engine gate (toe-bridge.ts).
//
// Background: frames without the game engine (e.g. the top window while the
// game lives in a cross-origin iframe) must never claim jobs. Previously the
// top window claimed first and submitted a dummy loss, consuming the job
// before the engine frame could compute the real result — which made it look
// like the Titan Arena screen had to be open.
import { test } from "node:test";
import assert from "node:assert";
import { __resetBridgeForTests, battleConfigFor, capturedClassNames, ensureEngineBridge, getF, getFn, getProtoFn, hasBattleEngine, hasLocalEngine, realmDiag, safeGameOf, tick } from "../toe-bridge.ts";

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
    assert.strictEqual(globalThis.window.Game["BattlePresets"], FakePresets);
    // Game keeps working: the value is readable back through the trap.
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

test("traps are removed after full capture (Goodwin coexistence)", () => {
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
      assert.strictEqual(
        Object.getOwnPropertyDescriptor(Object.prototype, p),
        undefined,
        `${p} trap removed`,
      );
    }
    // Values stay stored under the HWH-compatible ghost key; the game
    // holds direct refs post-registration so prototype reads are unneeded.
    assert.strictEqual(typeof holder[props[0] + "_"], "function");
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
