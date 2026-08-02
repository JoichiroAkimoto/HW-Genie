// Regression tests for the auth-server HTTP client (auth-client.ts).
//
// These drive the PRODUCTION code directly with an injected fake fetch.

import { test } from "node:test";
import assert from "node:assert";
import { sendHeadersToServer } from "../auth-client.ts";

const BASE = "http://localhost:8765";

function makeResponse(status, body, isJson = true) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => (isJson ? JSON.parse(body) : { parseError: true }),
    text: async () => body,
  };
}

/** nonce と /auth を順に返す fake fetch。 */
function makeFetch({ nonceBody, authResponse }) {
  let call = 0;
  return async (url, init) => {
    call++;
    if (url.endsWith("/nonce")) {
      return nonceBody;
    }
    if (url.endsWith("/auth")) {
      assert.ok(init, "/auth は init 付きで呼ばれる");
      assert.strictEqual(init.method, "POST");
      const body = JSON.parse(init.body);
      assert.ok(body.nonce, "nonce が送信される");
      assert.ok(body.headers, "headers が送信される");
      return authResponse;
    }
    throw new Error("unexpected url: " + url);
  };
}

test("非 2xx + 非 JSON ボディで例外を投げず false を返す（res.ok 先確認の回帰）", async () => {
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(500, "Internal Server Error", false),
  });
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, false);
});

test("200 + status:success で true を返し、player 名も返す", async () => {
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(200, JSON.stringify({ status: "success", player: { name: "Joe" } })),
  });
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.playerName, "Joe");
});

test("200 + status:error で false を返す", async () => {
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(200, JSON.stringify({ status: "error", message: "Invalid signature" })),
  });
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, false);
});

test("200 + 不正 JSON（json() が throw）で例外なく false を返す", async () => {
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: {
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("Unexpected token");
      },
      text: async () => "not json",
    },
  });
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, false);
});

test("fetch 拒否（サーバー停止）で false を返す", async () => {
  const fetchImpl = async () => {
    throw new Error("connection refused");
  };
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, false);
});

test("nonce 取得失敗で false を返す", async () => {
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(500, "boom", false),
    authResponse: null,
  });
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, false);
});

test("AbortController タイムアウトで promise が settle する（ハングしない）", async () => {
  // /nonce は成功、/auth がハングしてタイムアウト abort する
  const fetchImpl = async (url, init) => {
    if (url.endsWith("/nonce")) {
      return makeResponse(200, JSON.stringify({ nonce: "n1" }));
    }
    if (url.endsWith("/auth")) {
      return new Promise((resolve, reject) => {
        const signal = init?.signal;
        if (signal) {
          if (signal.aborted) {
            reject(new DOMException("Aborted", "AbortError"));
            return;
          }
          signal.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        }
        // 解決しない（タイムアウト abort のみ）
      });
    }
    throw new Error("unexpected url: " + url);
  };
  // タイムアウト 50ms で settle することを確認
  const start = Date.now();
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 50, fetchImpl);
  const elapsed = Date.now() - start;
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.reason, "timeout"); // タイムアウトは network と区別する
  assert.ok(elapsed < 2000, `expected timeout abort, took ${elapsed}ms`);
});
