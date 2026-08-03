import { test } from "node:test";
import assert from "node:assert";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const SCRIPT_PATH = path.resolve(__dirname, "../release-metadata.sh");

function runScript(args) {
  const result = spawnSync("bash", [SCRIPT_PATH, ...args], {
    encoding: "utf-8",
  });
  return {
    status: result.status,
    stdout: result.stdout ? result.stdout.trim() : "",
    stderr: result.stderr ? result.stderr.trim() : "",
  };
}

function withTempFile(content, callback) {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "metadata-test-"));
  const tmpFile = path.join(tmpDir, "file.tmp");
  fs.writeFileSync(tmpFile, content, "utf-8");
  try {
    return callback(tmpFile);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
}

test("version: valid 1.0.5 extraction", () => {
  const content = `// ==UserScript==
// @name Test
// @version 1.0.5
// ==/UserScript==
`;
  withTempFile(content, (file) => {
    const res = runScript(["version", file]);
    assert.strictEqual(res.status, 0);
    assert.strictEqual(res.stdout, "1.0.5");
  });
});

test("version: missing metadata block", () => {
  const content = `// @version 1.0.5`;
  withTempFile(content, (file) => {
    const res = runScript(["version", file]);
    assert.notStrictEqual(res.status, 0);
    assert.ok(res.stderr.includes("metadata block not found"));
  });
});

test("version: duplicate @version lines in metadata block", () => {
  const content = `// ==UserScript==
// @version 1.0.5
// @version 1.0.6
// ==/UserScript==
`;
  withTempFile(content, (file) => {
    const res = runScript(["version", file]);
    assert.notStrictEqual(res.status, 0);
    assert.ok(res.stderr.includes("multiple @version lines"));
  });
});

test("version: invalid version format (e.g., 1.0)", () => {
  const content = `// ==UserScript==
// @version 1.0
// ==/UserScript==
`;
  withTempFile(content, (file) => {
    const res = runScript(["version", file]);
    assert.notStrictEqual(res.status, 0);
    assert.ok(res.stderr.includes("not a valid semver"));
  });
});

test("validate-tag: matching and mismatching tags", () => {
  const content = `// ==UserScript==
// @version 1.0.5
// ==/UserScript==
`;
  withTempFile(content, (file) => {
    const matchRes = runScript(["validate-tag", file, "v1.0.5"]);
    assert.strictEqual(matchRes.status, 0);

    const mismatchRes = runScript(["validate-tag", file, "v1.0.4"]);
    assert.notStrictEqual(mismatchRes.status, 0);
    assert.ok(mismatchRes.stderr.includes("does not match"));
  });
});

test("validate-artifact: valid generated artifact metadata", () => {
  const content = `// ==UserScript==
// @name Test
// @version 1.0.5
// @downloadURL https://github.com/owner/repo/releases/latest/download/hw-genie-auth-capture.user.js
// @updateURL https://github.com/owner/repo/releases/latest/download/hw-genie-auth-capture.user.js
// ==/UserScript==
(() => {})();
`;
  withTempFile(content, (file) => {
    const res = runScript(["validate-artifact", file, "1.0.5", "owner/repo"]);
    assert.strictEqual(res.status, 0);
  });
});

test("validate-artifact: version mismatch", () => {
  const content = `// ==UserScript==
// @version 1.0.4
// @downloadURL https://github.com/owner/repo/releases/latest/download/hw-genie-auth-capture.user.js
// @updateURL https://github.com/owner/repo/releases/latest/download/hw-genie-auth-capture.user.js
// ==/UserScript==
`;
  withTempFile(content, (file) => {
    const res = runScript(["validate-artifact", file, "1.0.5", "owner/repo"]);
    assert.notStrictEqual(res.status, 0);
    assert.ok(res.stderr.includes("version mismatch"));
  });
});

test("validate-artifact: download URL mismatch", () => {
  const content = `// ==UserScript==
// @version 1.0.5
// @downloadURL https://github.com/owner/repo/releases/download/v1.0.4/hw-genie-auth-capture.user.js
// @updateURL https://github.com/owner/repo/releases/latest/download/hw-genie-auth-capture.user.js
// ==/UserScript==
`;
  withTempFile(content, (file) => {
    const res = runScript(["validate-artifact", file, "1.0.5", "owner/repo"]);
    assert.notStrictEqual(res.status, 0);
    assert.ok(res.stderr.includes("downloadURL mismatch"));
  });
});

test("validate-artifact: update URL mismatch", () => {
  const content = `// ==UserScript==
// @version 1.0.5
// @downloadURL https://github.com/owner/repo/releases/latest/download/hw-genie-auth-capture.user.js
// @updateURL https://github.com/owner/repo/releases/download/v1.0.5/hw-genie-auth-capture.user.js
// ==/UserScript==
`;
  withTempFile(content, (file) => {
    const res = runScript(["validate-artifact", file, "1.0.5", "owner/repo"]);
    assert.notStrictEqual(res.status, 0);
    assert.ok(res.stderr.includes("updateURL mismatch"));
  });
});

test("validate-artifact: unresolved placeholders", () => {
  const content = `// ==UserScript==
// @version 1.0.5
// @downloadURL __DOWNLOAD_URL__
// @updateURL https://github.com/owner/repo/releases/latest/download/hw-genie-auth-capture.user.js
// ==/UserScript==
`;
  withTempFile(content, (file) => {
    const res = runScript(["validate-artifact", file, "1.0.5", "owner/repo"]);
    assert.notStrictEqual(res.status, 0);
    assert.ok(res.stderr.includes("unresolved placeholders"));
  });
});
