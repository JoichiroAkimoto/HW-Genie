// ==UserScript==
// @name         HW-Genie Auth Capture
// @namespace    https://github.com/HW-Genie
// @version      1.0.3
// @description  Automatically capture auth headers and send to HW-Genie auth server
// @author       JoichiroAkimoto
// @license      MIT
// @supportURL   https://github.com/HW-Genie/HW-Genie/issues
// @match        https://www.hero-wars.com/*
// @match        https://heroes-wb.nextersglobal.com/*
// @grant        none
// @run-at       document-start
// @downloadURL  __DOWNLOAD_URL__
// @updateURL    __UPDATE_URL__
// ==/UserScript==

/**
 * Build: Run `bash build.sh` in this directory. It extracts the metadata
 * block above, bundles this file with `bun build`, and writes
 * `dist/hw-genie-auth-capture.user.js` (with __DOWNLOAD_URL__/__UPDATE_URL__
 * substituted when --inject-url is passed).
 */

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

  // Prune header keys that have not been observed for STALE_KEY_TTL_MS. Called
  // before every send so a changed key set cannot silently accumulate stale
  // entries that would break the signature validation.
  function pruneStaleKeys(now: number): void {
    if (!headersCaptured) {
      return;
    }
    for (const key of Object.keys(headersCaptured)) {
      if (now - (lastSeenAt[key] ?? 0) > STALE_KEY_TTL_MS) {
        delete headersCaptured[key];
        delete lastSeenAt[key];
      }
    }
  }

  function isApiUrl(urlString: string): boolean {
    // Resolve relative URLs against the current page so the filter works even
    // if the game switches to relative API paths. Never throw: a malformed URL
    // must not break the game's own XHR/fetch calls.
    try {
      const url = new URL(urlString, window.location.href);
      return (
        url.hostname === "heroes-wb.nextersglobal.com" &&
        (url.pathname === "/api" || url.pathname.startsWith("/api/"))
      );
    } catch {
      return false;
    }
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

  function collectHeaders(source: HeadersInit | Headers | undefined): void {
    if (!source) {
      return;
    }
    const headers = source instanceof Headers ? source : new Headers(source);
    headers.forEach((value, key) => {
      captureHeaders(key, value);
    });
  }

  function interceptXHR() {
    const originalOpen = XMLHttpRequest.prototype.open;
    // Bound once to the prototype so re-opening the same XHR object does not
    // stack multiple capture wrappers on top of each other.
    const protoSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;

    XMLHttpRequest.prototype.open = function (
      method: string,
      url: string | URL,
      async?: boolean,
      username?: string | null,
      password?: string | null,
    ) {
      let urlString: string;
      try {
        urlString = url.toString();
      } catch {
        // Never let capture interfere with the game's own requests.
        return originalOpen.call(this, method, url, async ?? true, username, password);
      }

      if (!isApiUrl(urlString)) {
        // The native default for a missing `async` argument is true
        // (asynchronous); preserve it so non-API XHRs keep their semantics.
        return originalOpen.call(this, method, url, async ?? true, username, password);
      }

      this.setRequestHeader = function (name: string, value: string): void {
        captureHeaders(name, value);
        protoSetRequestHeader.call(this, name, value);
      };

      return originalOpen.call(this, method, url, async ?? true, username, password);
    };
  }

  function interceptFetch() {
    if (typeof fetch !== "function") {
      return;
    }
    const originalFetch = window.fetch.bind(window);

    window.fetch = function (
      input: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> {
      try {
        let urlString: string;
        if (typeof input === "string") {
          urlString = input;
        } else if (input instanceof URL) {
          urlString = input.toString();
        } else {
          urlString = input.url;
        }
        if (isApiUrl(urlString)) {
          // Effective headers = input.headers merged with init.headers (init
          // wins for duplicate names), so collect both and let captureHeaders
          // overwrite on duplicate names.
          if (input instanceof Request) {
            collectHeaders(input.headers);
          }
          collectHeaders(init?.headers);
        }
      } catch {
        // Never let capture interfere with the game's own requests.
      }
      return originalFetch(input, init);
    };
  }

  function trySend(): void {
    if (sending) {
      return;
    }
    const now = Date.now();
    pruneStaleKeys(now);
    if (!headersCaptured) {
      return;
    }
    // All required keys must be present AND observed within the freshness
    // window. The freshness check prevents a mid-transition mix of old and
    // new session keys (count >= 6 alone would pass) from being pushed.
    if (!REQUIRED_HEADER_KEYS.every((key) => key in headersCaptured!)) {
      return;
    }
    if (
      !REQUIRED_HEADER_KEYS.every(
        (key) => now - (lastSeenAt[key] ?? 0) <= FRESH_WINDOW_MS,
      )
    ) {
      return;
    }

    // Send a snapshot of only the known headers, not the live object: an
    // in-flight re-login must not mutate the payload after it was serialized
    // for dedupe/backoff, and unknown x-auth-* keys are not forwarded.
    const snapshot: Record<string, string> = {};
    for (const key of REQUIRED_HEADER_KEYS) {
      snapshot[key] = headersCaptured[key];
    }
    // Normalize key order so dedupe/backoff are order-independent even after
    // prune -> re-capture reorders the underlying object.
    const serialized = JSON.stringify(snapshot, Object.keys(snapshot).sort());
    if (serialized === lastSentJson) {
      // Already sent and accepted; nothing changed.
      return;
    }

    // Headers changed since the last attempt (re-login) -> reset backoff so a
    // fresh session is pushed immediately. The serialized value of the last
    // attempt (not the last success) is the correct baseline: while the same
    // stale headers keep failing, the backoff must keep growing.
    if (serialized !== lastAttemptedJson) {
      backoffMs = POLL_INTERVAL_MS;
    }
    if (now - lastAttemptAt < backoffMs) {
      return;
    }

    sending = true;
    lastAttemptAt = now;
    lastAttemptedJson = serialized;
    sendHeaders(snapshot).then(
      (success) => {
        sending = false;
        if (success) {
          lastSentJson = serialized;
          backoffMs = POLL_INTERVAL_MS;
          log("Auth capture complete.");
        } else {
          // The send failed (stale session, server down, ...). Keep the
          // backoff growing so we do not spam the server, but still retry; a
          // re-login will change the headers and reset the backoff above.
          backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
        }
      },
      (err) => {
        // sendHeaders normally never rejects (all awaits are guarded), but if
        // it ever does, keep the poll alive instead of wedging `sending`.
        sending = false;
        backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
        log(`sendHeaders rejected: ${err}`);
      },
    );
  }

  function installInterceptors() {
    interceptXHR();
    interceptFetch();
  }

  function startPolling() {
    log("Starting auth capture...");
    setInterval(trySend, POLL_INTERVAL_MS);
  }

  // Install the interceptors immediately (document_start equivalent): if the
  // game bundle captures window.fetch / XHR prototypes before DOMContentLoaded,
  // late installation would silently miss every API call. Only the polling
  // (which does not depend on the DOM) waits for DOMContentLoaded.
  installInterceptors();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startPolling);
  } else {
    startPolling();
  }
})();
