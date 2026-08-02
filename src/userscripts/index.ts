// ==UserScript==
// @name         HW-Genie Auth Capture
// @namespace    https://github.com/JoichiroAkimoto/HW-Genie
// @version      1.0.4
// @description  Automatically capture auth headers and send to HW-Genie auth server
// @author       JoichiroAkimoto
// @license      MIT
// @supportURL   https://github.com/JoichiroAkimoto/HW-Genie/issues
// @match        https://www.hero-wars.com/*
// @match        https://heroes-wb.nextersglobal.com/*
// @grant        none
// @run-at       document-idle
// @downloadURL  __DOWNLOAD_URL__
// @updateURL    __UPDATE_URL__
// ==/UserScript==

/**
 * Build: Run `bash build.sh` in this directory. It extracts the metadata
 * block above, bundles this file with `bun build`, and writes
 * `dist/hw-genie-auth-capture.user.js` (with __DOWNLOAD_URL__/__UPDATE_URL__
 * substituted when --inject-url is passed).
 *
 * 既知の制限: このスクリプトは XHR (setRequestHeader) 経由のリクエストのみ
 * ヘッダーを捕捉する。v1.0.3 で実装された window.fetch / Request ヘッダー捕捉
 * (interceptFetch / collectHeaders) は、同じページで動く他ユーザースクリプト
 * （例: HW Goodwin）の fetch ラッパーと競合して UI を壊すため削除した。
 * したがってゲームが fetch のみで API を呼ぶ環境では認証ヘッダーを捕捉できず、
 * 認証サーバーへの送信は行われない（v1.0.2 と同じ挙動）。
 */

// XHR インターセプタは共有モジュールに分離し、テストから本番コードを
// 直接検証できるようにする（詳細は xhr-interceptor.ts を参照）。
import { isApiUrl, installXhrInterceptor } from "./xhr-interceptor";
// セッション送信の状態機械も純関数モジュールに分離（詳細は session.ts）。
import {
  beginSendAttempt,
  evaluateSend,
  markSendFailure,
  markSendSuccess,
} from "./session";

(() => {
  "use strict";

  const AUTH_SERVER_URL = "http://localhost:8765";
  // The game attaches exactly these six x-auth-* headers to its API requests.
  // A session is only sent once all of them have been captured, so partial
  // sessions (e.g. during a re-login) are never pushed to the auth server.
  const REQUIRED_HEADER_KEYS = [
    "x-auth-application-id",
    "x-auth-network-ident",
    "x-auth-session-id",
    "x-auth-signature",
    "x-auth-token",
    "x-auth-user-id",
  ];
  const POLL_INTERVAL_MS = 500;
  // After a failed send, retry with exponential backoff (x2 per failure) so a
  // temporarily unreachable auth server does not cause a hot retry loop.
  const MAX_BACKOFF_MS = 30000;
  // x-auth-* keys not seen for this long are pruned. A re-login changes the
  // header key set (and values); without pruning, keys from an old session
  // would be merged into the new one forever and every send would fail.
  const STALE_KEY_TTL_MS = 5000;
  // Every required header must have been observed within this window before a
  // send, so a mid-transition mix of old and new keys cannot be pushed.
  const FRESH_WINDOW_MS = 2 * POLL_INTERVAL_MS;
  // Bounded timeout for the (local) auth server calls so a stalled fetch can
  // never wedge `sending` and block re-captures after a re-login.
  const FETCH_TIMEOUT_MS = 5000;

  let headersCaptured: Record<string, string> | null = null;
  // Millisecond timestamp of the last time each header key was seen, used to
  // prune keys that stopped appearing (see STALE_KEY_TTL_MS).
  let lastSeenAt: Record<string, number> = {};
  // JSON of the headers that were last accepted by the auth server. New
  // captures are re-sent only when the serialized value differs, so a fresh
  // login (new token/signature) is pushed even if the page stays open.
  let lastSentJson: string | null = null;
  // JSON of the headers of the most recent send attempt (success or failure).
  // Used to detect that the headers actually changed (re-login) and reset the
  // backoff, without conflating "changed" with "never sent yet".
  let lastAttemptedJson: string | null = null;
  // 再ログイン検知の確定待ち（session.ts の pendingChangeJson に対応）
  let pendingChangeJson: string | null = null;
  let sending = false;
  let backoffMs = POLL_INTERVAL_MS;
  let lastAttemptAt = 0;

  function log(msg: string, ...args: unknown[]) {
    console.log(`[HW-Genie] ${msg}`, ...args);
  }

  function captureHeaders(name: string, value: string): void {
    const lowerName = name.toLowerCase();
    if (!lowerName.startsWith("x-auth-")) {
      return;
    }
    if (!headersCaptured) {
      headersCaptured = {};
    }
    headersCaptured[lowerName] = value;
    lastSeenAt[lowerName] = Date.now();
  }

  // Timeout wrapper compatible with older browsers / WebViews that lack
  // AbortSignal.timeout(). Aborts via an AbortController after timeoutMs.
  function fetchWithTimeout(
    url: string,
    options: RequestInit = {},
    timeoutMs: number = FETCH_TIMEOUT_MS,
  ): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    return fetch(url, { ...options, signal: controller.signal }).finally(() =>
      clearTimeout(timer),
    );
  }

  async function fetchNonce(): Promise<string | null> {
    try {
      const res = await fetchWithTimeout(`${AUTH_SERVER_URL}/nonce`);
      if (!res.ok) {
        return null;
      }
      const data = await res.json();
      return data.nonce;
    } catch {
      return null;
    }
  }

  async function sendHeaders(headers: Record<string, string>): Promise<boolean> {
    const nonce = await fetchNonce();
    if (!nonce) {
      log("Failed to fetch nonce. Is the auth server running?");
      return false;
    }

    try {
      const res = await fetchWithTimeout(`${AUTH_SERVER_URL}/auth`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nonce, headers }),
      });

      const data = await res.json();

      if (res.ok && data.status === "success") {
        log(
          `Auth captured successfully! Player: ${data.player?.name} (Lv.${data.player?.level})`,
        );
        return true;
      } else {
        log(`Auth failed: ${data.message || data.detail || "Unknown error"}`);
        return false;
      }
    } catch (e) {
      log(`Error sending auth: ${e}`);
      return false;
    }
  }

  function trySend(): void {
    if (sending) {
      return;
    }
    const now = Date.now();
    // セッション状態の判定は純関数 evaluateSend に委譲（session.ts）。
    // pruneStaleKeys / 新旧混在ガード / dedupe / バックオフ判定をテストから
    // 直接検証できるようにするため。
    const state = {
      headersCaptured,
      lastSeenAt,
      lastSentJson,
      lastAttemptedJson,
      pendingChangeJson,
      backoffMs,
      lastAttemptAt,
    };
    const decision = evaluateSend(
      state,
      now,
      REQUIRED_HEADER_KEYS,
      STALE_KEY_TTL_MS,
      FRESH_WINDOW_MS,
      POLL_INTERVAL_MS,
    );
    if (!decision.shouldSend || !decision.snapshot || !decision.serialized) {
      return;
    }
    // 送信試行の開始を記録（lastAttemptedJson / lastAttemptAt）。
    // これにより再ログイン検知（値変化でバックオフリセット）と
    // 同一値の再送抑止が正しく機能する。
    beginSendAttempt(state, decision.serialized, now);
    // state の変更をクロージャ変数へ反映する。
    headersCaptured = state.headersCaptured;
    lastSeenAt = state.lastSeenAt;
    lastAttemptedJson = state.lastAttemptedJson;
    pendingChangeJson = state.pendingChangeJson;
    backoffMs = state.backoffMs;
    lastAttemptAt = state.lastAttemptAt;
    sending = true;

    sendHeaders(decision.snapshot).then(
      (success) => {
        sending = false;
        if (success) {
          markSendSuccess(state, decision.serialized!, POLL_INTERVAL_MS);
          lastSentJson = state.lastSentJson;
          backoffMs = state.backoffMs;
          // 成功ログは sendHeaders 内で出力済み（Player 名入り）
        } else {
          // The send failed (stale session, server down, ...). Keep the
          // backoff growing so we do not spam the server, but still retry; a
          // re-login will change the headers and reset the backoff above.
          markSendFailure(state, MAX_BACKOFF_MS);
          backoffMs = state.backoffMs;
        }
      },
      (err) => {
        // sendHeaders normally never rejects (all awaits are guarded), but if
        // it ever does, keep the poll alive instead of wedging `sending`.
        sending = false;
        markSendFailure(state, MAX_BACKOFF_MS);
        backoffMs = state.backoffMs;
        log(`sendHeaders rejected: ${err}`);
      },
    );
  }

  function startPolling() {
    log("Starting auth capture...");
    setInterval(trySend, POLL_INTERVAL_MS);
  }

  // Install the XHR interceptor at document-idle (see @run-at). Running at
  // document-start would replace XMLHttpRequest.prototype.open before other
  // userscripts (e.g. HW Goodwin) install their own XHR wrappers, breaking
  // their request/response hooks and hiding their UI. At document-idle the
  // other scripts have already wrapped the prototypes, and resolving
  // setRequestHeader per-call keeps their wrappers in the chain.
  //
  // 既知の制限: document-idle より前のゲーム初期 API 呼び出しは捕捉されない
  // が、以降の API 呼び出しで同一の x-auth-* ヘッダーが再設定されるため、
  // ポーリングが拾って実用上回復する。
  function installInterceptor() {
    installXhrInterceptor(
      (urlString: string) => isApiUrl(urlString, window.location.href),
      captureHeaders,
    );
  }

  // @run-at document-idle により、通常は readyState === "complete" で到達する。
  // document-start 相当への変更があった場合に備えて readyState を確認し、
  // まだ読み込み中なら DOMContentLoaded まで遅延する（安全側に倒す）。
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      installInterceptor();
      startPolling();
    });
  } else {
    installInterceptor();
    startPolling();
  }
})();
