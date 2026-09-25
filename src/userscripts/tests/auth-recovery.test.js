// Regression tests for the session-expiry auto-reload判定 (auth-recovery.ts).
//
// These drive the PRODUCTION code directly — not a copy.

import { test } from "node:test";
import assert from "node:assert";
import {
  DISABLE_KEY,
  MAX_BODY_BYTES,
  MAX_RELOADS_PER_WINDOW,
  RELOAD_COOLDOWN_MS,
  RELOAD_WINDOW_MS,
  STORAGE_KEY,
  createApiResponseHandler,
  defaultReloadState,
  evaluateReload,
  isAutoReloadDisabled,
  isSessionExpired,
  sanitizeReloadState,
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

test("kill-switch: DISABLE_KEY が仕様値である", () => {
  assert.strictEqual(DISABLE_KEY, "hw-genie-auto-reload-disabled");
});

test("isAutoReloadDisabled: '1' のときのみ無効", () => {
  assert.strictEqual(isAutoReloadDisabled("1"), true);
});

test("isAutoReloadDisabled: null・その他は有効扱い (キー不在時は有効)", () => {
  assert.strictEqual(isAutoReloadDisabled(null), false);
  assert.strictEqual(isAutoReloadDisabled(""), false);
  assert.strictEqual(isAutoReloadDisabled("0"), false);
  assert.strictEqual(isAutoReloadDisabled("true"), false);
  assert.strictEqual(isAutoReloadDisabled(" 1"), false);
  assert.strictEqual(isAutoReloadDisabled("1 "), false);
});

// --- createApiResponseHandler の配線テスト（本番ロジックを直接検証） ---

const EXPIRED_BODY = JSON.stringify({ error: { name: "auth" } });

function makeDeps(overrides = {}) {
  const store = { session: null, ...("initialSession" in overrides ? {} : {}) };
  if ("initialSession" in overrides) {
    store.session = overrides.initialSession;
  }
  const calls = { writes: [], reloads: 0, logs: [] };
  const deps = {
    readDisabledValue: () => overrides.disabledValue ?? null,
    readStorage: () => store.session,
    writeStorage: (raw) => {
      calls.writes.push(raw);
      store.session = raw;
    },
    reload: () => {
      calls.reloads += 1;
    },
    now: () => overrides.now ?? 1000000,
    log: (msg) => calls.logs.push(msg),
    ...overrides.deps,
  };
  return { deps, calls, store };
}

test("handler: 正常時はリロード＋カウンタ書き込み", () => {
  const { deps, calls } = makeDeps();
  createApiResponseHandler(deps)(401, "{}");
  assert.strictEqual(calls.reloads, 1);
  assert.strictEqual(calls.writes.length, 1);
  assert.deepStrictEqual(JSON.parse(calls.writes[0]), {
    lastReloadAt: 1000000,
    windowStart: 1000000,
    reloadCount: 1,
  });
});

test("handler: 非失効レスポンスは何もしない", () => {
  const { deps, calls } = makeDeps();
  createApiResponseHandler(deps)(200, JSON.stringify({ status: "ok" }));
  assert.strictEqual(calls.reloads, 0);
  assert.strictEqual(calls.writes.length, 0);
});

test("handler: kill-switch 有効時はカウンタを消費しない", () => {
  const { deps, calls } = makeDeps({ disabledValue: "1" });
  createApiResponseHandler(deps)(401, "{}");
  assert.strictEqual(calls.reloads, 0);
  assert.strictEqual(calls.writes.length, 0);
  assert.ok(calls.logs.some((m) => m.includes("kill-switch")));
});

test("handler: localStorage 読取失敗時は suppress (fail-closed)", () => {
  const { deps, calls } = makeDeps({
    deps: {
      readDisabledValue: () => {
        throw new Error("denied");
      },
      readStorage: () => null,
      writeStorage: () => {
        throw new Error("must not write");
      },
      reload: () => {
        throw new Error("must not reload");
      },
      now: () => 1000000,
      log: (msg) => calls.logs.push(msg),
    },
  });
  createApiResponseHandler(deps)(401, "{}");
  assert.strictEqual(calls.reloads, 0);
  assert.strictEqual(calls.writes.length, 0);
  assert.ok(calls.logs.some((m) => m.includes("kill-switch")));
});

test("handler: 破損値は修復書き戻しした上でリロードする", () => {
  const { deps, calls, store } = makeDeps({ initialSession: "broken{{{" });
  createApiResponseHandler(deps)(401, "{}");
  assert.strictEqual(calls.reloads, 1);
  assert.ok(calls.writes.length >= 1);
  // 最初の書き込みはデフォルトへの修復、最後はリロード後状態。
  assert.strictEqual(
    calls.writes[0],
    JSON.stringify({ lastReloadAt: 0, windowStart: 0, reloadCount: 0 }),
  );
  assert.deepStrictEqual(JSON.parse(store.session), {
    lastReloadAt: 1000000,
    windowStart: 1000000,
    reloadCount: 1,
  });
});

test("handler: sessionStorage 不可用時は初回から suppress（リロードしない）", () => {
  const logs = [];
  let reloads = 0;
  let writes = 0;
  const deps = {
    readDisabledValue: () => null,
    readStorage: () => {
      throw new Error("unavailable");
    },
    writeStorage: () => {
      writes += 1;
    },
    reload: () => {
      reloads += 1;
    },
    now: () => 1000000,
    log: (msg) => logs.push(msg),
  };
  createApiResponseHandler(deps)(401, "{}");
  assert.strictEqual(reloads, 0);
  assert.strictEqual(writes, 0);
  assert.ok(logs.some((m) => m.includes("sessionStorage unavailable")));
});

test("handler: sessionStorage 書き込み不可時もリロードしない", () => {
  let reloads = 0;
  const deps = {
    readDisabledValue: () => null,
    readStorage: () => null,
    writeStorage: () => {
      throw new Error("unavailable");
    },
    reload: () => {
      reloads += 1;
    },
    now: () => 1000000,
  };
  createApiResponseHandler(deps)(401, "{}");
  assert.strictEqual(reloads, 0);
});

test("handler: クールダウン内は suppress（書き込みなし）", () => {
  const now = 1000000;
  const state = {
    lastReloadAt: now - 60 * 1000,
    windowStart: now - 60 * 1000,
    reloadCount: 1,
  };
  const { deps, calls } = makeDeps({ initialSession: JSON.stringify(state) });
  createApiResponseHandler(deps)(401, "{}");
  assert.strictEqual(calls.reloads, 0);
  assert.strictEqual(calls.writes.length, 0);
  assert.ok(calls.logs.some((m) => m.includes("cooldown/limit")));
});

test("handler: 失効ボディでも kill-switch 有効なら storage に触れない", () => {
  let reads = 0;
  const logs = [];
  const deps = {
    readDisabledValue: () => "1",
    readStorage: () => {
      reads += 1;
      return null;
    },
    writeStorage: () => {
      throw new Error("must not write");
    },
    reload: () => {
      throw new Error("must not reload");
    },
    now: () => 1000000,
    log: (msg) => logs.push(msg),
  };
  createApiResponseHandler(deps)(200, EXPIRED_BODY);
  assert.strictEqual(reads, 0);
  assert.ok(logs.some((m) => m.includes("kill-switch")));
});
