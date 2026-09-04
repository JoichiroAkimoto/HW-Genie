// Regression tests for the ToE bridge engine gate (toe-bridge.ts).
//
// Background: frames without the game engine (e.g. the top window while the
// game lives in a cross-origin iframe) must never claim jobs. Previously the
// top window claimed first and submitted a dummy loss, consuming the job
// before the engine frame could compute the real result — which made it look
// like the Titan Arena screen had to be open.
import { test } from "node:test";
import assert from "node:assert";
import { hasBattleEngine } from "../toe-bridge.ts";

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
