// Regression tests for the XHR interceptor's coexistence with other
// userscripts (e.g. HW Goodwin) that also wrap XMLHttpRequest.
//
// These tests drive the PRODUCTION code (xhr-interceptor.ts) directly —
// not a copy — by installing it on a fake XMLHttpRequest class.

import { test } from "node:test";
import assert from "node:assert";
import { isApiUrl, installXhrInterceptor } from "../xhr-interceptor.ts";

const API_URL = "https://heroes-wb.nextersglobal.com/api/";
const PAGE_URL = "https://www.hero-wars.com/";

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

// テスト間でプロトタイプのパッチが残らないよう、新しい XHR クラスを生成する。
function freshXHRClass() {
  return class extends NativeXHR {};
}

function installGenieInterceptor(XHRClass, captured) {
  const originalXHR = globalThis.XMLHttpRequest;
  globalThis.XMLHttpRequest = XHRClass;
  try {
    installXhrInterceptor(
      (u) => isApiUrl(u, PAGE_URL),
      (name, value) => captured.push([name, value]),
    );
  } finally {
    globalThis.XMLHttpRequest = originalXHR;
  }
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
  assert.strictEqual(isApiUrl("https://heroes-wb.nextersglobal.com/api/", PAGE_URL), true);
  assert.strictEqual(isApiUrl("https://heroes-wb.nextersglobal.com/api", PAGE_URL), true);
  assert.strictEqual(isApiUrl("https://heroes-wb.nextersglobal.com/api/../api/", PAGE_URL), true);
  assert.strictEqual(isApiUrl("https://attacker.example.com/heroes-wb.nextersglobal.com/api/", PAGE_URL), false);
  assert.strictEqual(isApiUrl("not a url", PAGE_URL), false);
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

test("他スクリプトが後から open をラップしてもチェーンが維持される", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  const seen = [];
  const openCalls = [];
  installGenieInterceptor(XHRClass, captured);
  // HW Goodwin が後から open をラップ
  const origOpen = XHRClass.prototype.open;
  XHRClass.prototype.open = function (...args) {
    openCalls.push(args[1]);
    return origOpen.apply(this, args);
  };
  installOtherScriptWrapper(XHRClass, seen);

  const xhr = new XHRClass();
  xhr.open("POST", API_URL);
  setAuthHeaders(xhr, "tok1");

  assert.strictEqual(captured.length, 3);
  assert.strictEqual(seen.length, 3);
  assert.strictEqual(openCalls.length, 1);
});

test("他スクリプトが先に open をラップしていてもチェーンが維持される", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  const seen = [];
  const openCalls = [];
  // HW Goodwin が先に open をラップ
  const origOpen0 = XHRClass.prototype.open;
  XHRClass.prototype.open = function (...args) {
    openCalls.push(args[1]);
    return origOpen0.apply(this, args);
  };
  installOtherScriptWrapper(XHRClass, seen);
  installGenieInterceptor(XHRClass, captured);

  const xhr = new XHRClass();
  xhr.open("POST", API_URL);
  setAuthHeaders(xhr, "tok1");

  assert.strictEqual(captured.length, 3);
  assert.strictEqual(seen.length, 3);
  assert.strictEqual(openCalls.length, 1);
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

  // ラッパー参照が同一のまま（スタックしていない）
  const wrapper1 = xhr.setRequestHeader;
  xhr.open("POST", API_URL);
  assert.strictEqual(xhr.setRequestHeader, wrapper1);
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

test("他スクリプトがインスタンスの setRequestHeader を差し替えても再ラップで捕捉継続", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  installGenieInterceptor(XHRClass, captured);

  const xhr = new XHRClass();
  xhr.open("POST", API_URL);
  const orig = xhr.setRequestHeader;
  xhr.setRequestHeader = function (n, v) {
    return orig.call(this, n, v);
  }; // 他スクリプトが差し替え（元のラッパーを呼ぶ）
  xhr.open("POST", API_URL); // marker 不一致 → 再ラップ
  xhr.setRequestHeader("x-auth-token", "tok2");

  // 再ラップにより捕捉は継続する（差し替え関数が元ラッパーを呼ぶため
  // 二重に捕捉されることもあるが、捕捉が失われてはならない）
  assert.ok(captured.length >= 1);
  assert.strictEqual(xhr._headers["x-auth-token"], "tok2");
});

test("両者が open 毎に再ラップしても無限再帰せず（チェーンは非有界だが有限）", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  const seen = [];
  installGenieInterceptor(XHRClass, captured);
  installOtherScriptWrapper(XHRClass, seen);

  const xhr = new XHRClass();
  xhr.open("POST", API_URL);
  for (let i = 0; i < 100; i++) {
    const cur = xhr.setRequestHeader;
    xhr.setRequestHeader = function (n, v) {
      return cur.call(this, n, v);
    };
    xhr.open("POST", API_URL); // 毎 open で双方が再ラップ
  }
  xhr.setRequestHeader("x-auth-token", "tok");

  // 捕捉が最後の値に到達し、XHR にも設定される（無限再帰しない）
  assert.strictEqual(captured[captured.length - 1][1], "tok");
  assert.strictEqual(xhr._headers["x-auth-token"], "tok");
});

test("installXhrInterceptor が二重実行されても捕捉が壊れない", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  installGenieInterceptor(XHRClass, captured);
  installGenieInterceptor(XHRClass, captured); // 2 回目

  const xhr = new XHRClass();
  xhr.open("POST", API_URL);
  setAuthHeaders(xhr, "tok1");

  // 二重捕捉されず、3 件のみ
  assert.strictEqual(captured.length, 3);
  assert.deepStrictEqual(Object.keys(xhr._headers), [
    "x-auth-token",
    "x-auth-session-id",
    "x-auth-network-ident",
  ]);
});

test("capture が例外を投げてもゲームの setRequestHeader は壊れない", () => {
  const XHRClass = freshXHRClass();
  const capturing = () => {
    throw new Error("capture boom");
  };
  const originalXHR = globalThis.XMLHttpRequest;
  globalThis.XMLHttpRequest = XHRClass;
  const origDebug = console.debug;
  console.debug = () => {}; // 本番のデバッグログをテスト出力に流さない
  try {
    installXhrInterceptor((u) => isApiUrl(u, PAGE_URL), capturing);
  } finally {
    globalThis.XMLHttpRequest = originalXHR;
    console.debug = origDebug;
  }

  const xhr = new XHRClass();
  xhr.open("POST", API_URL);
  // 例外が投げられても setRequestHeader は成功する（finally で委譲）
  xhr.setRequestHeader("x-auth-token", "tok");
  assert.strictEqual(xhr._headers["x-auth-token"], "tok");
});

test("open の async/username/password 引数が素通しされる", () => {
  class RecordingXHR extends NativeXHR {
    open(method, url, async, username, password) {
      this._openArgs = { method, url, async, username, password };
      super.open(method, url);
    }
  }
  const captured = [];
  const originalXHR = globalThis.XMLHttpRequest;
  globalThis.XMLHttpRequest = RecordingXHR;
  try {
    installXhrInterceptor(
      (u) => isApiUrl(u, PAGE_URL),
      (n, v) => captured.push([n, v]),
    );
  } finally {
    globalThis.XMLHttpRequest = originalXHR;
  }

  const xhr = new RecordingXHR();
  // 明示指定: null は WebIDL で同期リクエストになるため、変換されず届くこと
  xhr.open("POST", API_URL, false, "user", "pass");
  assert.deepStrictEqual(xhr._openArgs, {
    method: "POST", url: API_URL, async: false, username: "user", password: "pass",
  });

  // 省略時: undefined がそのまま届き、native の既定値 (async=true) に任せること
  xhr.open("GET", API_URL);
  assert.strictEqual(xhr._openArgs.async, undefined);

  // 明示的な null: WebIDL で ToBoolean(null)=false（同期）になるため、変換されず届くこと
  xhr.open("POST", API_URL, null);
  assert.strictEqual(xhr._openArgs.async, null);
});

test("API open 後に非 API へ再オープンするとラッパーが外れ、以後捕捉しない", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  installGenieInterceptor(XHRClass, captured);

  const xhr = new XHRClass();
  xhr.open("POST", API_URL);
  xhr.setRequestHeader("x-auth-token", "tok1"); // 捕捉される
  xhr.open("POST", "https://other.example.com/");
  xhr.setRequestHeader("x-auth-token", "tok2"); // 捕捉されない
  assert.strictEqual(captured.length, 1);

  // 再び API open すれば再ラップされ捕捉が復帰する
  xhr.open("POST", API_URL);
  xhr.setRequestHeader("x-auth-token", "tok3");
  assert.strictEqual(captured.length, 2);
});

test("他スクリプト先行インストール時も API→非API→API で解除・再ラップが機能する", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  const seen = [];
  installOtherScriptWrapper(XHRClass, seen);
  installGenieInterceptor(XHRClass, captured);

  const xhr = new XHRClass();
  xhr.open("POST", API_URL);
  xhr.setRequestHeader("x-auth-token", "tok1"); // 捕捉される
  xhr.open("POST", "https://other.example.com/");
  xhr.setRequestHeader("x-auth-token", "tok2"); // 捕捉されない（解除）
  xhr.open("POST", API_URL);
  xhr.setRequestHeader("x-auth-token", "tok3"); // 再ラップで捕捉復帰

  assert.deepStrictEqual(captured, [
    ["x-auth-token", "tok1"],
    ["x-auth-token", "tok3"],
  ]);
  assert.strictEqual(seen.length, 3); // 他スクリプト側は常に通る
});

test("他スクリプトが own sRH を上書きしたまま非 API へ再オープンしても捕捉は残らない", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  installGenieInterceptor(XHRClass, captured);

  const xhr = new XHRClass();
  xhr.open("POST", API_URL); // W1 設置
  xhr.setRequestHeader("x-auth-token", "tok1");
  const orig = xhr.setRequestHeader; // W1
  xhr.setRequestHeader = function (n, v) {
    return orig.call(this, n, v);
  }; // 他スクリプトが own 上書き
  xhr.open("POST", "https://other.example.com/"); // 非 API
  xhr.setRequestHeader("x-auth-token", "tok2"); // 契約上 capture されてはならない

  // tok2 が混入しない（ACTIVE=false で捕捉が発火しない）
  assert.deepStrictEqual(captured.map(([n]) => n), ["x-auth-token"]);
});

test("他スクリプトが open 内でインスタンス own sRH を毎回張る（genie が内側）", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  const seen = [];
  installGenieInterceptor(XHRClass, captured); // 先にインストール → open チェーンの内側
  const origOpen = XHRClass.prototype.open;
  XHRClass.prototype.open = function (m, u) {
    const protoSRH = XHRClass.prototype.setRequestHeader;
    this.setRequestHeader = function (n, v) {
      seen.push([n, v]);
      return protoSRH.call(this, n, v);
    };
    return origOpen.call(this, m, u);
  };
  const xhr = new XHRClass();
  xhr.open("POST", API_URL);
  xhr.setRequestHeader("x-auth-token", "tok");

  assert.ok(captured.length >= 1); // 内側なら捕捉継続
  assert.ok(seen.length >= 1); // 他スクリプト側も生きる
});

test("同パターンで genie が外側のとき（他スクリプト先行）", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  const seen = [];
  // 他スクリプトを先にインストール → genie の open が外側
  const origOpen0 = XHRClass.prototype.open;
  XHRClass.prototype.open = function (m, u) {
    const protoSRH = XHRClass.prototype.setRequestHeader;
    this.setRequestHeader = function (n, v) {
      seen.push([n, v]);
      return protoSRH.call(this, n, v);
    };
    return origOpen0.call(this, m, u);
  };
  installGenieInterceptor(XHRClass, captured);

  const xhr = new XHRClass();
  xhr.open("POST", API_URL);
  xhr.setRequestHeader("x-auth-token", "tok");

  // 他スクリプト側は常に動く。genie 側は open 内で own sRH が毎回上書きされる
  // ため捕捉が失われる可能性がある（将来の改善の足がかりとして固定）。
  assert.ok(seen.length >= 1);
});

test("open 後に setRequestHeader を複数回呼んでも最後の値が捕捉される（stale しない）", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  installGenieInterceptor(XHRClass, captured);

  const xhr = new XHRClass();
  xhr.open("POST", API_URL);
  xhr.setRequestHeader("x-auth-token", "A");
  xhr.setRequestHeader("x-auth-token", "B"); // 上書き
  xhr.setRequestHeader("x-auth-token", "C"); // 最終値

  assert.strictEqual(captured.length, 3);
  assert.strictEqual(captured[2][1], "C"); // 最後の値が捕捉される
  assert.strictEqual(xhr._headers["x-auth-token"], "C");
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

test("実ゲーム相当: 同一 XHR を open→setHeader→send→再 open で再利用しても捕捉が継続する", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  const seen = [];
  installGenieInterceptor(XHRClass, captured);
  installOtherScriptWrapper(XHRClass, seen);

  const xhr = new XHRClass();
  // 1 回目のリクエスト
  xhr.open("POST", API_URL);
  xhr.setRequestHeader("x-auth-token", "tok1");
  xhr.setRequestHeader("x-auth-session-id", "tok1_sid");
  xhr.setRequestHeader("x-auth-network-ident", "web");
  xhr.send();
  // 2 回目（同一 XHR 再利用）
  xhr.open("POST", API_URL);
  xhr.setRequestHeader("x-auth-token", "tok2");
  xhr.setRequestHeader("x-auth-session-id", "tok2_sid");
  xhr.setRequestHeader("x-auth-network-ident", "web");
  xhr.send();
  // 3 回目（再ログイン相当でヘッダー更新）
  xhr.open("POST", API_URL);
  xhr.setRequestHeader("x-auth-token", "tok3");
  xhr.setRequestHeader("x-auth-network-ident", "web");
  xhr.send();

  // すべて捕捉され、ラッパーにも渡る
  assert.strictEqual(captured.length, 8);
  assert.strictEqual(seen.length, 8);
  assert.strictEqual(xhr._headers["x-auth-token"], "tok3");
});

test("実ゲーム相当: API→非 API→API の 3 連続 open でラッパー解除と再ラップが機能する", () => {
  const XHRClass = freshXHRClass();
  const captured = [];
  installGenieInterceptor(XHRClass, captured);

  const xhr = new XHRClass();
  // API open → ラッパー設置
  xhr.open("POST", API_URL);
  xhr.setRequestHeader("x-auth-token", "tok1");
  assert.strictEqual(captured.length, 1);
  // 非 API へ再オープン → ラッパー解除され捕捉しない
  xhr.open("POST", "https://other.example.com/");
  xhr.setRequestHeader("x-auth-token", "tok2");
  assert.strictEqual(captured.length, 1);
  // 再び API open → 再ラップされ捕捉が復帰
  xhr.open("POST", API_URL);
  xhr.setRequestHeader("x-auth-token", "tok3");
  assert.strictEqual(captured.length, 2);
  assert.strictEqual(xhr._headers["x-auth-token"], "tok3");
});
