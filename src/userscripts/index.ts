// ==UserScript==
// @name         HW-Genie Auth Capture
// @namespace    https://github.com/HW-Genie
// @version      1.0.0
// @description  Automatically capture auth headers and send to HW-Genie auth server
// @match        https://heroes-wb.nextersglobal.com/*
// @grant        none
// ==/UserScript==

(() => {
  "use strict";

  const AUTH_SERVER_URL = "http://localhost:8765";
  let captured = false;
  let headersCaptured: Record<string, string> | null = null;

  function log(msg: string, ...args: unknown[]) {
    console.log(`[HW-Genie] ${msg}`, ...args);
  }

  /**
   * Fetch a nonce from the auth server.
   */
  async function fetchNonce(): Promise<string | null> {
    try {
      const res = await fetch(`${AUTH_SERVER_URL}/nonce`);
      if (!res.ok) return null;
      const data = await res.json();
      return data.nonce;
    } catch {
      return null;
    }
  }

  /**
   * Send captured headers to the auth server.
   */
  async function sendHeaders(headers: Record<string, string>): Promise<boolean> {
    const nonce = await fetchNonce();
    if (!nonce) {
      log("Failed to fetch nonce. Is the auth server running?");
      return false;
    }

    try {
      const res = await fetch(`${AUTH_SERVER_URL}/auth`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nonce, headers, account: "default" }),
      });

      const data = await res.json();

      if (res.ok && data.status === "success") {
        log(`Auth captured successfully! Player: ${data.player.name} (Lv.${data.player.level})`);
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

  /**
   * Intercept XMLHttpRequest to capture x-auth-* headers.
   */
  function interceptXHR() {
    const originalOpen = XMLHttpRequest.prototype.open;

    XMLHttpRequest.prototype.open = function (
      method: string,
      url: string | URL,
      async?: boolean,
      username?: string | null,
      password?: string | null,
    ) {
      const urlString = url.toString();

      // Only intercept requests to the Hero Wars API
      if (!urlString.includes("heroes-wb.nextersglobal.com/api/")) {
        return originalOpen.call(this, method, url, async as boolean, username, password);
      }

      const originalSetRequestHeader = this.setRequestHeader.bind(this);
      this.setRequestHeader = function (name: string, value: string): void {
        const lowerName = name.toLowerCase();
        if (lowerName.startsWith("x-auth-")) {
          if (!headersCaptured) {
            headersCaptured = {};
          }
          headersCaptured[lowerName] = value;
        }
        originalSetRequestHeader(name, value);
      };

      return originalOpen.call(this, method, url, async as boolean, username, password);
    };
  }

  /**
   * Main: start interception and send headers once captured.
   */
  async function main() {
    log("Starting auth capture...");
    interceptXHR();

    // Poll for captured headers
    const pollInterval = setInterval(() => {
      if (captured) {
        clearInterval(pollInterval);
        return;
      }
      if (headersCaptured && Object.keys(headersCaptured).length >= 6) {
        captured = true;
        clearInterval(pollInterval);

        sendHeaders(headersCaptured).then((success) => {
          if (success) {
            log("Auth capture complete.");
          } else {
            captured = false; // Allow retry on failure
          }
        });
      }
    }, 500);
  }

  // Start when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();
