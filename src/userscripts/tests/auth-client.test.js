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
  assert.strictEqual(result.reason, "http-500");
  assert.strictEqual(result.detail, "Internal Server Error");
});

test("非 2xx の JSON detail を SendResult.detail に載せる（500 の原因を可視化）", async () => {
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(
      500,
      JSON.stringify({ detail: "Session expired or invalid signature." }),
    ),
  });
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.reason, "http-500");
  assert.strictEqual(result.detail, "Session expired or invalid signature.");
});

test("非 2xx の JSON detail も 300 文字 + … に切り詰める（非 JSON 本文と対称）", async () => {
  const longDetail = "x".repeat(400);
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(500, JSON.stringify({ detail: longDetail })),
  });
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.reason, "http-500");
  assert.strictEqual(result.detail.length, 301);
  assert.ok(result.detail.endsWith("…"));
});

test("422 + JSON の detail 配列は生の本文を detail に出さない（秘匿値の露出回避）", async () => {
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(
      422,
      JSON.stringify({
        detail: [
          {
            loc: ["body", "headers", "X-Auth-Token"],
            msg: "field required",
            input: "sensitive-value",
          },
        ],
      }),
    ),
  });
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.reason, "http-422");
  assert.strictEqual(result.detail, undefined);
});

test("200 + status:success で true を返し、player 名も返す", async () => {
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(200, JSON.stringify({ status: "success", player: { name: "Alice" } })),
  });
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.playerName, "Alice");
});

test("非 2xx の JSON message も SendResult.detail に載せる（detail が無い場合の代替）", async () => {
  // 既存の status:error + message ケースとは別経路（非 2xx の本文解析）。
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(500, JSON.stringify({ message: "Server exploded" })),
  });
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.reason, "http-500");
  assert.strictEqual(result.detail, "Server exploded");
});

test("200 + status:error で false を返し message を detail に載せる", async () => {
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(200, JSON.stringify({ status: "error", message: "Invalid signature" })),
  });
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.detail, "Invalid signature");
});

test("長い detail はコードポイント基準で切り詰め、サロゲートペアを壊さない", async () => {
  // 絵文字（サロゲートペア）301 個 → 先頭 300 個 + …（孤立サロゲートを作らない）
  const longDetail = "🙂".repeat(301);
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(500, JSON.stringify({ detail: longDetail })),
  });
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.detail, "🙂".repeat(300) + "…");
  // 孤立サロゲート（壊れた文字）を含まない
  const loneSurrogate =
    /(?:[\uD800-\uDBFF](?![\uDC00-\uDFFF]))|(?:(?<![\uD800-\uDBFF])[\uDC00-\uDFFF])/;
  assert.ok(!loneSurrogate.test(result.detail));
  assert.strictEqual(Array.from(result.detail).length, 301);
});

test("300 コードポイントちょうどの detail は切り詰めない", async () => {
  const exact = "🙂".repeat(300);
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(500, JSON.stringify({ detail: exact })),
  });
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.detail, exact);
});

test("200 + status:error の長い message も 300 文字 + … に切り詰める（非 2xx と対称）", async () => {
  const longMessage = "x".repeat(400);
  const fetchImpl = makeFetch({
    nonceBody: makeResponse(200, JSON.stringify({ nonce: "n1" })),
    authResponse: makeResponse(200, JSON.stringify({ status: "error", message: longMessage })),
  });
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 5000, fetchImpl);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.reason, "status-error");
  assert.strictEqual(result.detail, "x".repeat(300) + "…");
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

test("ボディ読み取りがハングしてもタイムアウトで settle する（sending が固まらない）", async () => {
  // /nonce は成功、/auth のボディが決して settle しない（stall するサーバー）
  const fetchImpl = async (url) => {
    if (url.endsWith("/nonce")) {
      return makeResponse(200, JSON.stringify({ nonce: "n1" }));
    }
    if (url.endsWith("/auth")) {
      return {
        ok: true,
        status: 200,
        // ボディ読み取りが永久に pending になる（res.text() が解決しない）
        text: () => new Promise(() => {}),
      };
    }
    throw new Error("unexpected url: " + url);
  };
  const start = Date.now();
  const result = await sendHeadersToServer(BASE, { "x-auth-token": "t" }, 50, fetchImpl);
  const elapsed = Date.now() - start;
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.reason, "timeout"); // ボディ読み取りタイムアウト
  assert.ok(elapsed < 2000, `expected body-read timeout, took ${elapsed}ms`);
});
