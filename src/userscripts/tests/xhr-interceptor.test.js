// Regression tests for the XHR interceptor's coexistence with other
// userscripts (e.g. HW Goodwin) that also wrap XMLHttpRequest.
//
// The interceptor must:
//  1. capture x-auth-* headers from API XHRs
//  2. not break other scripts' setRequestHeader wrappers (resolve the
//     reference at open() time, not at install time)
//  3. not stack multiple wrappers when the same XHR object is re-opened
//     (WeakSet guard) — otherwise capture is lost or infinite recursion occurs
//  4. keep working in both install orders (before / after the other script)

import { test } from "node:test";
import assert from "node:assert";

const API_URL = "https://heroes-wb.nextersglobal.com/api/";

class NativeXHR {
  constructor() {
    this._headers = {};
  }
  open(method, url) {
    this._url = url;
  }
  setRequestHeader(name, value) {
    this._headers[name] = value;
  }
  send() {}
}

/** HW-Genie の interceptXHR と同等のパッチを適用する。 */
function installGenieInterceptor(XHRClass, captured) {
  const originalOpen = XHRClass.prototype.open;
  const wrapped = new WeakSet();

  XHRClass.prototype.open = function (method, url, async, username, password) {
    const urlString = url.toString();
    if (!urlString.includes("heroes-wb.nextersglobal.com/api/")) {
      return originalOpen.call(this, method, url, async ?? true, username, password);
    }
    if (!wrapped.has(this)) {
      wrapped.add(this);
      const setRequestHeaderRef = this.setRequestHeader.bind(this);
      this.setRequestHeader = function (name, value) {
        if (name.toLowerCase().startsWith("x-auth-")) {
          captured.push([name, value]);
        }
        setRequestHeaderRef(name, value);
      };
    }
    return originalOpen.call(this, method, url, async ?? true, username, password);
  };
}

/** 他ユーザースクリプト（HW Goodwin 相当）の setRequestHeader ラッパーを適用する。 */
function installOtherScriptWrapper(XHRClass, seen) {
  const protoSRH = XHRClass.prototype.setRequestHeader;
  XHRClass.prototype.setRequestHeader = function (name, value) {
    seen.push([name, value]);
    return protoSRH.call(this, name, value);
  };
}

function setAuthHeaders(xhr, token) {
  xhr.setRequestHeader("x-auth-token", token);
  xhr.setRequestHeader("x-auth-session-id", token + "_sid");
  xhr.setRequestHeader("x-auth-network-ident", "web");
}

test("HW-Genie が先にインストール: 捕捉と他スクリプトのラッパーが共存する", () => {
  const captured = [];
  const seen = [];
  installGenieInterceptor(NativeXHR, captured);
  installOtherScriptWrapper(NativeXHR, seen);

  const xhr = new NativeXHR();
  xhr.open("POST", API_URL);
  setAuthHeaders(xhr, "tok1");

  assert.strictEqual(captured.length, 3);
  assert.strictEqual(seen.length, 3);
  assert.deepStrictEqual(Object.keys(xhr._headers), [
    "x-auth-token",
    "x-auth-session-id",
    "x-auth-network-ident",
  ]);
});

test("他スクリプトが先にインストール: 捕捉とラッパーが共存する", () => {
  const captured = [];
  const seen = [];
  installOtherScriptWrapper(NativeXHR, seen);
  installGenieInterceptor(NativeXHR, captured);

  const xhr = new NativeXHR();
  xhr.open("POST", API_URL);
  setAuthHeaders(xhr, "tok1");

  assert.strictEqual(captured.length, 3);
  assert.strictEqual(seen.length, 3);
  assert.deepStrictEqual(Object.keys(xhr._headers), [
    "x-auth-token",
    "x-auth-session-id",
    "x-auth-network-ident",
  ]);
});

test("同一 XHR の再オープンでラッパーが積み重ならない（捕捉が継続する）", () => {
  const captured = [];
  const seen = [];
  installGenieInterceptor(NativeXHR, captured);
  installOtherScriptWrapper(NativeXHR, seen);

  const xhr = new NativeXHR();
  xhr.open("POST", API_URL);
  setAuthHeaders(xhr, "tok1");
  // 再オープン（ゲームは同一 XHR を再利用することがある）
  xhr.open("POST", API_URL);
  xhr.setRequestHeader("x-auth-token", "tok2");
  xhr.setRequestHeader("x-auth-network-ident", "web");

  // 5 件すべて捕捉され、ラッパーにも渡る（再ラップによる喪失・再帰がない）
  assert.strictEqual(captured.length, 5);
  assert.strictEqual(seen.length, 5);
  assert.strictEqual(xhr._headers["x-auth-token"], "tok2");
  assert.strictEqual(xhr._headers["x-auth-network-ident"], "web");
});

test("API 以外の XHR は捕捉されない", () => {
  const captured = [];
  installGenieInterceptor(NativeXHR, captured);

  const xhr = new NativeXHR();
  xhr.open("POST", "https://other.example.com/api/");
  setAuthHeaders(xhr, "tok1");

  assert.strictEqual(captured.length, 0);
  assert.deepStrictEqual(Object.keys(xhr._headers), [
    "x-auth-token",
    "x-auth-session-id",
    "x-auth-network-ident",
  ]);
});
