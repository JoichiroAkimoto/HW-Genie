// Regression tests for the session send state machine (session.ts).
//
// These drive the PRODUCTION code directly and pin the behavior that fixes
// the v1.0.3 stale-session bug: after a re-login, old header keys must not be
// merged into the new session, mixed old/new payloads must not be sent, and
// dedupe/backoff must behave correctly.

import { test } from "node:test";
import assert from "node:assert";
import {
  beginSendAttempt,
  evaluateSend,
  markSendFailure,
  markSendSuccess,
  pollAndMaybeSend,
  pruneStaleKeys,
} from "../session.ts";

const REQUIRED_KEYS = [
  "x-auth-application-id",
  "x-auth-network-ident",
  "x-auth-session-id",
  "x-auth-signature",
  "x-auth-token",
  "x-auth-user-id",
];
const POLL_MS = 500;
const STALE_TTL_MS = 5000;
const FRESH_WINDOW_MS = 1000;
const MAX_BACKOFF_MS = 30000;

function fullState(now = 1000000, overrides = {}) {
  const headersCaptured = {
    "x-auth-application-id": "3",
    "x-auth-network-ident": "web",
    "x-auth-session-id": "sid1",
    "x-auth-signature": "sig1",
    "x-auth-token": "tok1",
    "x-auth-user-id": "u1",
  };
  const lastSeenAt = {};
  for (const k of Object.keys(headersCaptured)) {
    lastSeenAt[k] = now;
  }
  return {
    headersCaptured,
    lastSeenAt,
    lastSentJson: null,
    lastAttemptedJson: null,
    pendingChangeJson: null,
    backoffMs: POLL_MS,
    lastAttemptAt: 0,
    ...overrides,
  };
}

test("古いセッションのキーが TTL 超過で prune され、新セッションと混ざらない", () => {
  const s = fullState();
  // 古いキー x-auth-player-id を追加（TTL 超過）
  s.headersCaptured["x-auth-player-id"] = "old";
  s.lastSeenAt["x-auth-player-id"] = 0; // 古い
  pruneStaleKeys(s.headersCaptured, s.lastSeenAt, 1000000, STALE_TTL_MS);
  assert.ok(!("x-auth-player-id" in s.headersCaptured));
  assert.ok(!("x-auth-player-id" in s.lastSeenAt));
  // 必須キーは残る
  for (const k of REQUIRED_KEYS) {
    assert.ok(k in s.headersCaptured);
  }
});

test("6 キー中 1 キーのみ古い（FRESH_WINDOW 超過）場合は送信しない", () => {
  const now = 1000000;
  const s = fullState(now);
  s.lastSeenAt["x-auth-token"] = now - FRESH_WINDOW_MS - 1; // 古い
  const d = evaluateSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d.shouldSend, false);
});

test("必須キーが揃っていない場合は送信しない", () => {
  const s = fullState();
  delete s.headersCaptured["x-auth-token"];
  const d = evaluateSend(s, 1000000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d.shouldSend, false);
});

test("同一値は lastSentJson と一致し再送されない（dedupe）", () => {
  const s = fullState();
  const d1 = evaluateSend(s, 1000000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d1.shouldSend, true);
  assert.ok(d1.serialized);
  markSendSuccess(s, d1.serialized, POLL_MS);
  // 同じ状態で再評価 → 再送しない
  const d2 = evaluateSend(s, 1000000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d2.shouldSend, false);
});

test("値が変われば 2 連続観測後に backoff がリセットされ即送信される（再ログイン検知）", () => {
  const s = fullState();
  // 失敗でバックオフが伸びた状態にする
  markSendFailure(s, MAX_BACKOFF_MS);
  markSendFailure(s, MAX_BACKOFF_MS);
  assert.strictEqual(s.backoffMs, 2000);
  // ヘッダー更新（再ログイン）
  s.headersCaptured["x-auth-token"] = "tok2";
  s.headersCaptured["x-auth-signature"] = "sig2";
  const now = 1000000;
  for (const k of Object.keys(s.headersCaptured)) {
    s.lastSeenAt[k] = now;
  }
  // 1 回目の観測: pendingChangeJson に記録（未確定）
  const d1 = evaluateSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(s.pendingChangeJson, d1.serialized);
  // 2 回目の観測（同一値）: 確定 → バックオフリセット
  const d2 = evaluateSend(s, now + POLL_MS, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d2.shouldSend, true);
  assert.strictEqual(s.backoffMs, POLL_MS); // リセットされた
});

test("リクエスト毎に値が変わる（署名ローテーション）場合、バックオフはリセットされない", () => {
  const s = fullState();
  // 初回送信で lastAttemptAt を設定してから失敗でバックオフを伸ばす
  const d0 = pollAndMaybeSend(s, 1000000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d0.shouldSend, true);
  markSendFailure(s, MAX_BACKOFF_MS);
  markSendFailure(s, MAX_BACKOFF_MS);
  assert.strictEqual(s.backoffMs, 2000);
  const now = 1000000;
  // 毎ポーリング値が変わる（ローテーション）
  let counter = 0;
  const evaluateWithRotatingSignature = () => {
    counter++;
    s.headersCaptured["x-auth-signature"] = "sig" + counter;
    for (const k of Object.keys(s.headersCaptured)) {
      s.lastSeenAt[k] = now + counter * POLL_MS;
    }
    return pollAndMaybeSend(s, now + counter * POLL_MS, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  };
  const results = [];
  results.push(evaluateWithRotatingSignature());
  results.push(evaluateWithRotatingSignature());
  results.push(evaluateWithRotatingSignature());
  // 値が毎回変わるため pendingChangeJson は確定せず、バックオフは
  // リセットされない（ホットリトライに戻らない）。ゲートは閉じたまま。
  results.forEach((r) => assert.strictEqual(r.shouldSend, false));
  assert.strictEqual(s.backoffMs, 2000); // リセットされていない
  // pendingChangeJson は最後の評価の値（未確定のまま）
  assert.ok(s.pendingChangeJson !== null);
});

test("失敗継続で backoff が 2 倍され、MAX_BACKOFF_MS で頭打ちになる", () => {
  const s = fullState();
  s.backoffMs = POLL_MS;
  markSendFailure(s, MAX_BACKOFF_MS);
  assert.strictEqual(s.backoffMs, 1000);
  markSendFailure(s, MAX_BACKOFF_MS);
  assert.strictEqual(s.backoffMs, 2000);
  // 何度も失敗して上限に達する
  for (let i = 0; i < 10; i++) {
    markSendFailure(s, MAX_BACKOFF_MS);
  }
  assert.strictEqual(s.backoffMs, MAX_BACKOFF_MS);
});

test("backoff 中の同一ヘッダーは送信しない", () => {
  const s = fullState();
  const d1 = evaluateSend(s, 1000000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d1.shouldSend, true);
  assert.ok(d1.serialized);
  // 送信試行: lastAttemptedJson と lastAttemptAt を記録（呼び出し側の責務）
  s.lastAttemptedJson = d1.serialized;
  s.lastAttemptAt = 1000000;
  // 失敗 → バックオフ 1000ms
  markSendFailure(s, MAX_BACKOFF_MS);
  // 500ms 後に再評価 → backoff 中（同一ヘッダーなので lastSentJson 不一致でも
  // backoff がリセットされない）
  const d2 = evaluateSend(s, 1000500, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d2.shouldSend, false);
  // バックオフ経過後は送信
  const d3 = evaluateSend(s, 1001000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d3.shouldSend, true);
});

test("新旧混在: 全キーが個別には FRESH 内でも観測時刻差が大きく送信しない（コヒーレント集合）", () => {
  const now = 1000000;
  const s = fullState(now);
  let i = 0;
  for (const k of REQUIRED_KEYS) {
    // fresh=1000 内（個別には per-key チェックを通る）だが、
    // スプレッド 750 > coherent(500=pollInterval) で reject
    s.lastSeenAt[k] = i < 4 ? now : now - 750;
    i++;
  }
  const d = evaluateSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d.shouldSend, false);
});

test("統合: beginSendAttempt により同一値のバックオフがリセットされない（P0 回帰）", () => {
  const s = fullState();
  const d1 = evaluateSend(s, 1000000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d1.shouldSend, true);
  assert.ok(d1.serialized);
  // 本番と同じ手順: 試行開始を記録
  beginSendAttempt(s, d1.serialized, 1000000);
  markSendFailure(s, MAX_BACKOFF_MS);
  assert.strictEqual(s.backoffMs, 1000);
  // 500ms 後、同一ヘッダーで再評価 → lastAttemptedJson 一致なので
  // バックオフはリセットされず、送信は抑止される（ホットリトライ防止）
  const d2 = evaluateSend(s, 1000500, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d2.shouldSend, false);
  assert.strictEqual(s.backoffMs, 1000); // リセットされていない
  // バックオフ経過後は送信される
  const d3 = evaluateSend(s, 1001000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d3.shouldSend, true);
});

test("統合: ゲート閉中に値が変化し、2 連続観測でバックオフがリセットされ送信される", () => {
  const now = 1000000;
  const s = fullState(now);
  // 初回送信
  const d0 = pollAndMaybeSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d0.shouldSend, true);
  assert.ok(d0.serialized);
  // 失敗でバックオフを伸ばす
  markSendFailure(s, MAX_BACKOFF_MS);
  markSendFailure(s, MAX_BACKOFF_MS); // backoff = 2000
  // 再ログイン（値変更）
  s.headersCaptured["x-auth-token"] = "tok2";
  for (const k of Object.keys(s.headersCaptured)) {
    s.lastSeenAt[k] = now + POLL_MS;
  }
  // 1 回目: ゲート閉 → 送信せず、確定待ちを保持
  const d1 = pollAndMaybeSend(s, now + POLL_MS, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d1.shouldSend, false);
  assert.strictEqual(s.pendingChangeJson, d1.serialized); // 保持されないと P1 バグ
  // 2 回目: 同一値で確定 → リセット → 即送信
  const d2 = pollAndMaybeSend(s, now + 2 * POLL_MS, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d2.shouldSend, true);
  assert.strictEqual(s.backoffMs, POLL_MS);
});

test("単一 state オブジェクト: ゲート閉中も pendingChangeJson が保持される（反映漏れなし）", () => {
  // index.ts は単一の state オブジェクトを保持し、pollAndMaybeSend がそれを
  // 破壊的に更新する。クロージャへの手動コピーがないため、反映漏れバグの
  // クラスが存在しないことを確認する（複数オブジェクト間のコピーが必要ない）。
  const s = fullState();
  const now = 1000000;
  // 初回送信試行を記録してから失敗でバックオフを伸ばす（ゲート閉状態を作る）
  const d0 = pollAndMaybeSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d0.shouldSend, true);
  assert.ok(d0.serialized);
  markSendFailure(s, MAX_BACKOFF_MS);
  markSendFailure(s, MAX_BACKOFF_MS); // backoff = 2000, lastAttemptAt = now
  // 値変化（再ログイン）→ ゲート閉中の 1 回目観測
  s.headersCaptured["x-auth-token"] = "tok2";
  for (const k of Object.keys(s.headersCaptured)) {
    s.lastSeenAt[k] = now + POLL_MS;
  }
  const d1 = pollAndMaybeSend(s, now + POLL_MS, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d1.shouldSend, false); // ゲート閉（backoff 2000 中）
  assert.strictEqual(s.pendingChangeJson, d1.serialized); // 同じオブジェクトに残る
  // 2 回目: 確定 → リセット → 送信
  const d2 = pollAndMaybeSend(s, now + 2 * POLL_MS, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d2.shouldSend, true);
  assert.strictEqual(s.backoffMs, POLL_MS);
});

test("統合: pollAndMaybeSend → 成功 → dedupe で再送しない", () => {
  const s = fullState();
  const d0 = pollAndMaybeSend(s, 1000000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d0.shouldSend, true);
  assert.ok(d0.serialized);
  markSendSuccess(s, d0.serialized, POLL_MS);
  assert.strictEqual(s.pendingChangeJson, null); // beginSendAttempt でクリア済み
  const d1 = pollAndMaybeSend(s, 1000500, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d1.shouldSend, false); // dedupe
});

test("成功でバックオフがリセットされる（サーバー復旧パス）", () => {
  const s = fullState();
  markSendFailure(s, MAX_BACKOFF_MS); // 1000
  markSendFailure(s, MAX_BACKOFF_MS); // 2000
  const d = evaluateSend(s, 1000000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d.shouldSend, true);
  assert.ok(d.serialized);
  beginSendAttempt(s, d.serialized, 1000000);
  markSendSuccess(s, d.serialized, POLL_MS);
  assert.strictEqual(s.backoffMs, POLL_MS);
  assert.strictEqual(s.lastSentJson, d.serialized);
  // dedupe: 同じ値を再送しない
  const d2 = evaluateSend(s, 1000500, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d2.shouldSend, false);
});

test("snapshot は既知キーのみでキー順が正規化される", () => {
  const s = fullState();
  // 未知キーを混入
  s.headersCaptured["x-auth-unknown"] = "extra";
  s.lastSeenAt["x-auth-unknown"] = 1000000;
  const d = evaluateSend(s, 1000000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.ok(d.snapshot);
  assert.ok(!("x-auth-unknown" in d.snapshot)); // 未知キーは転送しない
  assert.deepStrictEqual(Object.keys(d.snapshot).sort(), [...REQUIRED_KEYS].sort());
});
