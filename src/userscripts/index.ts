// ==UserScript==
// @name         HW-Genie Auth Capture
// @namespace    https://github.com/JoichiroAkimoto/HW-Genie
// @version      1.0.5
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
 * `dist/hw-genie-auth-capture.user.js`. Pass --inject-download-url /
 * --inject-update-url to set @downloadURL / @updateURL individually (use
 * releases/latest/download/... for the update URL so Tampermonkey auto-update
 * detects new releases); --inject-url sets both to the same value.
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
  markSendFailure,
  markSendSuccess,
  pollAndMaybeSend,
} from "./session";
// 認証サーバーへの送信クライアント（fetch 注入可能。テストから検証）。
import { sendHeadersToServer } from "./auth-client";
import type { SessionState } from "./session";

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
  // 成功時の最小送信間隔。ゲームがリクエスト毎に署名をローテーションする
  // 場合、値が安定せず dedupe が効かないため、この間隔で連続送信を抑える
  // （再ログイン直後の即送信は pendingIdentityJson 確定パスで維持される）。
  const MIN_SEND_INTERVAL_MS = 2000;
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

  // セッション状態は単一の SessionState オブジェクトで管理する。
  // pollAndMaybeSend / markSendSuccess / markSendFailure はこのオブジェクトを
  // 破壊的に更新するため、クロージャ変数への手動コピー（反映漏れのバグ源）が
  // 不要になる。
  const state: SessionState = {
    headersCaptured: null,
    lastSeenAt: {},
    lastCaptureAt: 0,
    lastSentJson: null,
    lastAttemptedJson: null,
    pendingIdentityJson: null,
    lastAttemptedIdentityJson: null,
    backoffMs: POLL_INTERVAL_MS,
    lastAttemptAt: 0,
  };
  let sending = false;

  function log(msg: string, ...args: unknown[]) {
    console.log(`[HW-Genie] ${msg}`, ...args);
  }

  function captureHeaders(name: string, value: string): void {
    const lowerName = name.toLowerCase();
    if (!lowerName.startsWith("x-auth-")) {
      return;
    }
    if (!state.headersCaptured) {
      state.headersCaptured = {};
    }
    state.headersCaptured[lowerName] = value;
    state.lastSeenAt[lowerName] = Date.now();
    state.lastCaptureAt = Date.now();
  }

  async function sendHeaders(headers: Record<string, string>): Promise<boolean> {
    const result = await sendHeadersToServer(
      AUTH_SERVER_URL,
      headers,
      FETCH_TIMEOUT_MS,
    );
    if (!result.ok) {
      log(`Failed to send auth headers to server (${result.reason ?? "unknown"}).`);
    } else {
      log(`Auth headers sent (Player: ${result.playerName ?? "unknown"})`);
    }
    return result.ok;
  }

  function trySend(): void {
    if (sending) {
      return;
    }
    const now = Date.now();
    // セッション状態の判定と送信準備は pollAndMaybeSend に委譲（session.ts）。
    // state オブジェクトは破壊的に更新されるため、クロージャへの手動反映は
    // 不要（反映漏れバグのクラスが存在しない）。
    const decision = pollAndMaybeSend(
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
    sending = true;

    sendHeaders(decision.snapshot).then(
      (success) => {
        sending = false;
        if (success) {
          // ローテーション時の連続送信を抑えるため、成功後のバックオフは
          // MIN_SEND_INTERVAL_MS にする（再ログイン検知は pendingIdentityJson
          // 確定パスで 500ms に戻るため、即送信は維持される）。
          markSendSuccess(state, decision.serialized!, MIN_SEND_INTERVAL_MS);
          // 成功ログは sendHeaders 内で出力済み（Player 名入り）
        } else {
          // The send failed (stale session, server down, ...). Keep the
          // backoff growing so we do not spam the server, but still retry; a
          // re-login will change the headers and reset the backoff above.
          markSendFailure(state, MAX_BACKOFF_MS);
        }
      },
      (err) => {
        // sendHeaders normally never rejects (all awaits are guarded), but if
        // it ever does, keep the poll alive instead of wedging `sending`.
        sending = false;
        markSendFailure(state, MAX_BACKOFF_MS);
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
