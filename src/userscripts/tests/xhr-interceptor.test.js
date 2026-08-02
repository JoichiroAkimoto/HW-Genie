// Regression tests for the XHR interceptor's coexistence with other
// userscripts (e.g. HW Goodwin) that also wrap XMLHttpRequest.
//
// The interceptor must:
//  1. capture x-auth-* headers from API XHRs
//  2. not break other scripts' setRequestHeader wrappers (resolve the
//     reference at open() time, not at install time)
//  3. not stack multiple wrappers when the same XHR object is re-opened
//     (per-instance Symbol marker) — otherwise capture is lost or infinite
//     recursion occurs
//  4. keep working in both install orders (before / after the other script)
//  5. match the production isApiUrl() (host + path), not substring matching

import { test } from "node:test";
import assert from "node:assert";

const API_URL = "https://heroes-wb.nextersglobal.com/api/";
const PAGE_URL = "https://www.hero-wars.com/";

// index.ts の isApiUrl と同じ実装（テスト対象を本番ロジックと 1:1 にする）
function isApiUrl(urlString) {
  try {
    const url = new URL(urlString, PAGE_URL);
    return (
      url.hostname === "heroes-wb.nextersglobal.com" &&
      (url.pathname === "/api" || url.pathname.startsWith("/api/"))
    );
  } catch {
    return false;
  }
}

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

/** テスト間でプロトタイプのパッチが残らないよう、新しい XHR クラスを生成する。 */
function freshXHRClass() {
  return class extends NativeXHR {};
}

/** HW-Genie の interceptXHR と同じパッチを適用する。 */
function installGenieInterceptor(XHRClass, captured) {
  const originalOpen = XHRClass.prototype.open;
  const HW_GENIE_WRAPPED = Symbol("hw-genie-wrapped-setRequestHeader");

  XHRClass.prototype.open = function (method, url, async, username, password) {
    const urlString = url.toString();
    if (isApiUrl(urlString)) {
      const currentSetRequestHeader = this.setRequestHeader;
      if (currentSetRequestHeader !== this[HW_GENIE_WRAPPED]) {
        const setRequestHeaderRef = currentSetRequestHeader.bind(this);
        const wrapper = function (name, value) {
          if (name.toLowerCase().startsWith("x-auth-")) {
            captured.push([name, value]);
          }
          setRequestHeaderRef(name, value);
        };
        this[HW_GENIE_WRAPPED] = wrapper;
        this.setRequestHeader = wrapper;
      }
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

test("isApiUrl はホスト+パスで判定し、部分文字列一致にしない", () => {
  assert.strictEqual(isApiUrl("https://heroes-wb.nextersglobal.com/api/"), true);
  assert.strictEqual(isApiUrl("https://heroes-wb.nextersglobal.com/api"), true);
  assert.strictEqual(isApiUrl("https://heroes-wb.nextersglobal.com/api/../api/"), true);
  assert.strictEqual(isApiUrl("https://attacker.example.com/heroes-wb.nextersglobal.com/api/"), false);
  assert.strictEqual(isApiUrl("not a url"), false);
});

test("HW-Genie が先にインストール: 捕捉と他スクリプトのラッパーが共存する", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  const seen = [];
  installGenieInterceptor(XHRClass, captured);
  installOtherScriptWrapper(XHRClass, seen);

  const xhr = new XHRClass();
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
  const XHRClass = freshXHRClass();
  const captured = [];
  const seen = [];
  installOtherScriptWrapper(XHRClass, seen);
  installGenieInterceptor(XHRClass, captured);

  const xhr = new XHRClass();
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

test("同一 XHR の再オープンでも捕捉が継続し、ラッパーが積み重ならない", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  const seen = [];
  installGenieInterceptor(XHRClass, captured);
  installOtherScriptWrapper(XHRClass, seen);

  const xhr = new XHRClass();
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

test("非 API の open 後に再オープンで API になっても捕捉される", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  installGenieInterceptor(XHRClass, captured);

  const xhr = new XHRClass();
  // 初回は非 API URL → ラッパー未設置
  xhr.open("POST", "https://other.example.com/");
  xhr.setRequestHeader("x-auth-token", "should-not-capture");
  // 再オープンで API URL → この時点でラップされ捕捉される
  xhr.open("POST", API_URL);
  xhr.setRequestHeader("x-auth-token", "tok1");

  assert.strictEqual(captured.length, 1);
  assert.strictEqual(captured[0][1], "tok1");
  assert.strictEqual(xhr._headers["x-auth-token"], "tok1");
});

test("API 以外の XHR は捕捉されない", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  installGenieInterceptor(XHRClass, captured);

  const xhr = new XHRClass();
  xhr.open("POST", "https://other.example.com/api/");
  setAuthHeaders(xhr, "tok1");

  assert.strictEqual(captured.length, 0);
  assert.deepStrictEqual(Object.keys(xhr._headers), [
    "x-auth-token",
    "x-auth-session-id",
    "x-auth-network-ident",
  ]);
});
