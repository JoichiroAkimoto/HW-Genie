// Regression tests for the ToE bridge engine gate (toe-bridge.ts).
//
// Background: frames without the game engine (e.g. the top window while the
// game lives in a cross-origin iframe) must never claim jobs. Previously the
// top window claimed first and submitted a dummy loss, consuming the job
// before the engine frame could compute the real result — which made it look
// like the Titan Arena screen had to be open.
import { test } from "node:test";
import assert from "node:assert";
import { hasBattleEngine, hasLocalEngine, safeGameOf, tick } from "../toe-bridge.ts";

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
  // No window/document globals here, so no candidate can carry an engine.
  assert.strictEqual(hasLocalEngine(), false);
});

test("tick performs zero fetch when no local engine", async () => {
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
