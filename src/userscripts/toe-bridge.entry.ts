// ==UserScript==
// @name         HW-Genie ToE Bridge
// @namespace    https://github.com/JoichiroAkimoto/HW-Genie
// @version      1.0.7
// @description  Titan Arena battle calc bridge for HW-Genie CLI (no screen operation needed)
// @author       JoichiroAkimoto
// @license      MIT
// @supportURL   https://github.com/JoichiroAkimoto/HW-Genie/issues
// @match        https://www.hero-wars.com/*
// @match        https://heroes-wb.nextersglobal.com/*
// @grant        none
// @run-at       document-start
// @downloadURL  __DOWNLOAD_URL__
// @updateURL    __UPDATE_URL__
// ==/UserScript==

/**
 * Standalone ToE bridge entry.
 *
 * Must run at document-start (NOT idle): it installs Object.prototype traps
 * (see ensureEngineBridge in toe-bridge.ts) that capture Haxe game classes
 * as the bundle registers them. At document-idle the bundle may already have
 * run and the traps would miss everything — that is exactly why the bridge
 * used to work only while HerowarsHelper (document-start) was active.
 *
 * Kept separate from the auth-capture script on purpose: the auth
 * interceptor must stay at document-idle to coexist with HW Goodwin, while
 * this bridge must start early. Install both scripts.
 *
 * Build: Run `bash build.sh` in this directory (also emits the -dev variant
 * with `bash build.sh --dev`).
 */

import { installToeBridge } from "./toe-bridge";

(() => {
  "use strict";

  const AUTH_SERVER_URL = "http://localhost:8765";

  console.log("[HW-Genie/ToE] bridge script loaded (document-start)");
  installToeBridge({ authServerUrl: AUTH_SERVER_URL });
})();
