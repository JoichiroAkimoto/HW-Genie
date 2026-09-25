// Regression tests for the session-expiry auto-reload判定 (auth-recovery.ts).
//
// These drive the PRODUCTION code directly — not a copy.

import { test } from "node:test";
import assert from "node:assert";
import {
  MAX_BODY_BYTES,
  MAX_RELOADS_PER_WINDOW,
  RELOAD_COOLDOWN_MS,
  RELOAD_WINDOW_MS,
  STORAGE_KEY,
  defaultReloadState,
  evaluateReload,
  isSessionExpired,
  sanitizeReloadState,
  shouldAutoReload,
} from "../auth-recovery.ts";

test("定数が仕様値である", () => {
  assert.strictEqual(RELOAD_COOLDOWN_MS, 5 * 60 * 1000);
  assert.strictEqual(RELOAD_WINDOW_MS, 10 * 60 * 1000);
  assert.strictEqual(MAX_RELOADS_PER_WINDOW, 3);
  assert.strictEqual(typeof STORAGE_KEY, "string");
});

test("401 はボディによらず失効確定", () => {
  assert.strictEqual(isSessionExpired(401, '{"results":[]}'), true);
  assert.strictEqual(isSessionExpired(401, ""), true);
  assert.strictEqual(isSessionExpired(401, null), true);
  assert.strictEqual(isSessionExpired(401, undefined), true);
  assert.strictEqual(isSessionExpired(401, "not json"), true);
});

test("トップレベル error.name が auth / InvalidSession なら確定", () => {
  assert.strictEqual(
    isSessionExpired(200, JSON.stringify({ error: { name: "auth" } })),
    true,
  );
  assert.strictEqual(
    isSessionExpired(200, JSON.stringify({ error: { name: "InvalidSession" } })),
    true,
  );
});

test("error が文字列の場合も Python 同様に扱う (client.py / auth.py 正条件)", () => {
  assert.strictEqual(isSessionExpired(200, '{"error":"auth"}'), true);
  assert.strictEqual(
    isSessionExpired(200, JSON.stringify({ error: "InvalidSession" })),
    true,
  );
  assert.strictEqual(
    isSessionExpired(
      200,
      JSON.stringify({ results: [{ error: "auth" }] }),
    ),
    true,
  );
  assert.strictEqual(
    isSessionExpired(
      200,
      JSON.stringify({ results: [{ data: 1 }, { error: "InvalidSession" }] }),
    ),
    true,
  );
  assert.strictEqual(
    isSessionExpired(200, JSON.stringify({ error: "NotEnough" })),
    false,
  );
});

test("非 JSON ボディに Invalid signature を含む場合は確定 (auth.py _unexpected_response_message と一致)", () => {
  assert.strictEqual(isSessionExpired(200, "Invalid signature"), true);
  assert.strictEqual(
    isSessionExpired(200, "Invalid signature: session expired"),
    true,
  );
  assert.strictEqual(isSessionExpired(200, "not json{"), false);
  assert.strictEqual(isSessionExpired(200, "<html>error</html>"), false);
});

test("results 配列内の error.name が該当すれば確定", () => {
  const body = JSON.stringify({
    results: [{ error: { name: "InvalidSession" } }],
  });
  assert.strictEqual(isSessionExpired(200, body), true);
  const mixed = JSON.stringify({
    results: [{ data: 1 }, { error: { name: "auth" } }],
  });
  assert.strictEqual(isSessionExpired(200, mixed), true);
});

test("results 複数走査は意図的拡張 (Python call() は単一コール時のみ検査)", () => {
  // Python HwClient.call() は len(results) == 1 のときのみ error を検査し、
  // 複数コール時は ident 辞書をそのまま返す。リロードトリガとしては広め
  // (複数中の 1 件でも失効なら回復) に倒す方が安全なため全件走査とする。
  const body = JSON.stringify({
    results: [
      { ident: "user", result: { response: {} } },
      { ident: "arena", error: { name: "auth" } },
    ],
  });
  assert.strictEqual(isSessionExpired(200, body), true);
});

test("完全一致・大文字区別は Python と一致のため意図的に維持", () => {
  assert.strictEqual(
    isSessionExpired(200, JSON.stringify({ error: { name: "Auth" } })),
    false,
  );
  assert.strictEqual(
    isSessionExpired(200, JSON.stringify({ error: { name: "AUTH" } })),
    false,
  );
  assert.strictEqual(
    isSessionExpired(200, JSON.stringify({ error: { name: "invalidsession" } })),
    false,
  );
  assert.strictEqual(
    isSessionExpired(200, JSON.stringify({ error: "Auth" })),
    false,
  );
});

test("非該当 (NotEnough 等) は false", () => {
  assert.strictEqual(
    isSessionExpired(200, JSON.stringify({ error: { name: "NotEnough" } })),
    false,
  );
  assert.strictEqual(
    isSessionExpired(
      200,
      JSON.stringify({ results: [{ error: { name: "NotEnough" } }] }),
    ),
    false,
  );
  assert.strictEqual(isSessionExpired(200, JSON.stringify({ results: [] })), false);
  assert.strictEqual(isSessionExpired(200, JSON.stringify({ status: "ok" })), false);
});

test("不正 JSON・空文字・null は false（例外なし）", () => {
  assert.strictEqual(isSessionExpired(200, "not json{"), false);
  assert.strictEqual(isSessionExpired(200, ""), false);
  assert.strictEqual(isSessionExpired(200, null), false);
  assert.strictEqual(isSessionExpired(200, undefined), false);
});

test("HTTP 範囲外の status (0 等) はボディによらず false", () => {
  const expiredBody = JSON.stringify({ error: { name: "auth" } });
  assert.strictEqual(isSessionExpired(0, expiredBody), false);
  assert.strictEqual(isSessionExpired(0, '{"error":"auth"}'), false);
  assert.strictEqual(isSessionExpired(99, expiredBody), false);
  assert.strictEqual(isSessionExpired(600, expiredBody), false);
  assert.strictEqual(isSessionExpired(-1, expiredBody), false);
  assert.strictEqual(isSessionExpired(Number.NaN, expiredBody), false);
});

test("軽量化: キーワードなしボディは parse せず false", () => {
  assert.strictEqual(
    isSessionExpired(200, JSON.stringify({ status: "ok", data: [1, 2, 3] })),
    false,
  );
  assert.strictEqual(isSessionExpired(200, "plain text without markers"), false);
});

test("サイズガード: 256KB 超は 401 以外スキップ", () => {
  assert.ok(MAX_BODY_BYTES > 0);
  const bigAuth = `{"error":{"name":"auth"},"pad":"${"x".repeat(MAX_BODY_BYTES)}"}`;
  assert.ok(bigAuth.length > MAX_BODY_BYTES);
  assert.strictEqual(isSessionExpired(200, bigAuth), false);
  assert.strictEqual(isSessionExpired(401, bigAuth), true);
});

test("shouldAutoReload: クールダウン内は false", () => {
  const now = 1000000;
  const state = {
    lastReloadAt: now - 60 * 1000,
    windowStart: now - 60 * 1000,
    reloadCount: 0,
  };
  assert.strictEqual(shouldAutoReload(state, now), false);
});

test("shouldAutoReload: 上限超過は false", () => {
  const now = 1000000;
  const state = { lastReloadAt: 0, windowStart: now, reloadCount: 3 };
  assert.strictEqual(shouldAutoReload(state, now), false);
});

test("shouldAutoReload: window 外は true（呼び出し側がリセット想定）", () => {
  const now = 1000000;
  const state = {
    lastReloadAt: 0,
    windowStart: now - 11 * 60 * 1000,
    reloadCount: 3,
  };
  assert.strictEqual(shouldAutoReload(state, now), true);
});

test("shouldAutoReload: 正常時は true", () => {
  const now = 1000000;
  const state = { lastReloadAt: 0, windowStart: now, reloadCount: 0 };
  assert.strictEqual(shouldAutoReload(state, now), true);
});

test("evaluateReload: 正常時は reload＋カウント+1", () => {
  const now = 1000000;
  const d = evaluateReload(
    { lastReloadAt: 0, windowStart: now, reloadCount: 0 },
    now,
  );
  assert.strictEqual(d.action, "reload");
  assert.deepStrictEqual(d.state, {
    lastReloadAt: now,
    windowStart: now,
    reloadCount: 1,
  });
});

test("evaluateReload: クールダウン優先 (クールダウン内＋ウィンドウ外 → suppress)", () => {
  const now = 1000000;
  const state = {
    lastReloadAt: now - 60 * 1000,
    windowStart: now - 11 * 60 * 1000,
    reloadCount: 3,
  };
  const d = evaluateReload(state, now);
  assert.strictEqual(d.action, "suppress");
  assert.deepStrictEqual(d.state, state);
});

test("evaluateReload: ウィンドウ外はリセットして reload (count=1)", () => {
  const now = 1000000;
  const d = evaluateReload(
    { lastReloadAt: 0, windowStart: now - 11 * 60 * 1000, reloadCount: 3 },
    now,
  );
  assert.strictEqual(d.action, "reload");
  assert.deepStrictEqual(d.state, {
    lastReloadAt: now,
    windowStart: now,
    reloadCount: 1,
  });
});

test("evaluateReload: 上限超過は suppress＋不変", () => {
  const now = 1000000;
  const state = { lastReloadAt: 0, windowStart: now, reloadCount: 3 };
  const d = evaluateReload(state, now);
  assert.strictEqual(d.action, "suppress");
  assert.deepStrictEqual(d.state, state);
});

test("defaultReloadState はゼロ値", () => {
  assert.deepStrictEqual(defaultReloadState(), {
    lastReloadAt: 0,
    windowStart: 0,
    reloadCount: 0,
  });
});

test("sanitizeReloadState: 正常値は複製を返す", () => {
  const now = 1000000;
  const src = { lastReloadAt: 1, windowStart: 2, reloadCount: 3 };
  const sane = sanitizeReloadState(src, now);
  assert.deepStrictEqual(sane, src);
  assert.notStrictEqual(sane, src);
});

test("sanitizeReloadState: 破損値は null (自己回復の呼び出し側がデフォルト＋書き戻し)", () => {
  const now = 1000000;
  assert.strictEqual(sanitizeReloadState(null, now), null);
  assert.strictEqual(sanitizeReloadState("x", now), null);
  assert.strictEqual(
    sanitizeReloadState({ lastReloadAt: NaN, windowStart: 0, reloadCount: 0 }, now),
    null,
  );
  assert.strictEqual(
    sanitizeReloadState(
      { lastReloadAt: Infinity, windowStart: 0, reloadCount: 0 },
      now,
    ),
    null,
  );
  assert.strictEqual(
    sanitizeReloadState({ lastReloadAt: 0, windowStart: 0, reloadCount: 1.5 }, now),
    null,
  );
  assert.strictEqual(
    sanitizeReloadState({ lastReloadAt: 0, windowStart: 0, reloadCount: -1 }, now),
    null,
  );
  assert.strictEqual(
    sanitizeReloadState({ lastReloadAt: -1, windowStart: 0, reloadCount: 0 }, now),
    null,
  );
  assert.strictEqual(
    sanitizeReloadState(
      { lastReloadAt: now + 1, windowStart: 0, reloadCount: 0 },
      now,
    ),
    null,
  );
  assert.strictEqual(
    sanitizeReloadState(
      { lastReloadAt: 0, windowStart: now + 1, reloadCount: 0 },
      now,
    ),
    null,
  );
  assert.strictEqual(
    sanitizeReloadState({ lastReloadAt: "0", windowStart: 0, reloadCount: 0 }, now),
    null,
  );
});
