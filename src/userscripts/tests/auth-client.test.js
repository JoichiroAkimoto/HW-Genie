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
  const ok = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(ok, false);
});

test("200 + status:success で true を返す", async () => {
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(200, JSON.stringify({ status: "success", player: { name: "Joe" } })),
  });
  const ok = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(ok, true);
});

test("200 + status:error で false を返す", async () => {
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(200, JSON.stringify({ status: "error", message: "Invalid signature" })),
  });
  const ok = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(ok, false);
});

test("200 + 不正 JSON で例外なく false を返す", async () => {
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(200, "not json", false),
  });
  const ok = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(ok, false);
});

test("fetch 拒否（サーバー停止）で false を返す", async () => {
  const fetchImpl = async () => {
    throw new Error("connection refused");
  };
  const ok = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(ok, false);
});

test("nonce 取得失敗で false を返す", async () => {
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(500, "boom", false),
    authResponse: null,
  });
  const ok = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(ok, false);
});
