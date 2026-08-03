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
  serializeForDedupe,
  serializeIdentity,
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
    lastCaptureAt: now,
    lastSentJson: null,
    pendingIdentityJson: null,
    lastAttemptedIdentityJson: null,
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

test("spread 超過かつ捕捉進行中（transitioning）で送信しない", () => {
  const now = 1000000;
  const s = fullState(now);
  s.lastSeenAt["x-auth-token"] = now - 750; // スプレッド 750 > coherent(500)
  s.lastCaptureAt = now; // 捕捉進行中
  const d = evaluateSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d.shouldSend, false);
});

test("settled 時に古いキーが TTL 超過なら送信しない", () => {
  const now = 1000000;
  const s = fullState(now);
  s.lastSeenAt["x-auth-token"] = now - STALE_TTL_MS - 1; // TTL 超過
  s.lastCaptureAt = now - 2000; // 捕捉停止（settled）
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

test("session-id 変更で 2 連続観測後に backoff がリセットされ即送信される（再ログイン検知）", () => {
  const s = fullState();
  // 失敗でバックオフが伸びた状態にする
  markSendFailure(s, MAX_BACKOFF_MS);
  markSendFailure(s, MAX_BACKOFF_MS);
  assert.strictEqual(s.backoffMs, 2000);
  // セッション同一性キー更新（再ログイン）
  s.headersCaptured["x-auth-session-id"] = "sid2";
  s.headersCaptured["x-auth-user-id"] = "u2";
  s.headersCaptured["x-auth-token"] = "tok2";
  s.headersCaptured["x-auth-signature"] = "sig2";
  const now = 1000000;
  for (const k of Object.keys(s.headersCaptured)) {
    s.lastSeenAt[k] = now;
  }
  // 1 回目の観測: pendingIdentityJson に identity シリアライズを記録（未確定）
  const d1 = evaluateSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(s.pendingIdentityJson, serializeIdentity(s.headersCaptured, REQUIRED_KEYS));
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
  // 毎ポーリング値が変わる（ローテーション）: 署名のみ変化、session-id は不変
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
  // 署名のみ変化（session-id は不変）なので pendingIdentityJson は確定せず、
  // バックオフはリセットされない（ホットリトライに戻らない）。ゲートは閉じたまま。
  results.forEach((r) => assert.strictEqual(r.shouldSend, false));
  assert.strictEqual(s.backoffMs, 2000); // リセットされていない
  // 同一性キーは不変なので pendingIdentityJson は確定しない（null のまま）
  assert.strictEqual(s.pendingIdentityJson, null);
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
  // 送信試行: identity と試行時刻を記録（呼び出し側の責務 = beginSendAttempt）
  s.lastAttemptedIdentityJson = serializeIdentity(s.headersCaptured, REQUIRED_KEYS);
  s.lastAttemptAt = 1000000;
  // 失敗 → バックオフ 1000ms
  markSendFailure(s, MAX_BACKOFF_MS);
  // 500ms 後に再評価 → backoff 中（同一ヘッダー・同一 identity なので
  // backoff がリセットされない）
  const d2 = evaluateSend(s, 1000500, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d2.shouldSend, false);
  // バックオフ経過後は送信
  const d3 = evaluateSend(s, 1001000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d3.shouldSend, true);
});

test("新旧混在: 捕捉が進行中（transitioning）で観測時刻差が大きい場合は送信しない", () => {
  const now = 1000000;
  const s = fullState(now);
  let i = 0;
  for (const k of REQUIRED_KEYS) {
    // fresh=1000 内（個別には per-key チェックを通る）だが、
    // スプレッド 750 > coherent(500=pollInterval) で reject
    s.lastSeenAt[k] = i < 4 ? now : now - 750;
    i++;
  }
  // 捕捉進行中（lastCaptureAt が直近）
  s.lastCaptureAt = now;
  const d = evaluateSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d.shouldSend, false);
});

test("コヒーレント: 観測時刻差が大きくても捕捉が止まっていれば（settled）送信する", () => {
  const now = 1000000;
  const s = fullState(now);
  let i = 0;
  for (const k of REQUIRED_KEYS) {
    s.lastSeenAt[k] = i < 4 ? now : now - 750;
    i++;
  }
  // 捕捉が止まっている（lastCaptureAt が coherent 窓より前）
  s.lastCaptureAt = now - 2000;
  const d = evaluateSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d.shouldSend, true);
});

test("コヒーレント境界値: spread === coherentWindowMs では reject しない", () => {
  const now = 1000000;
  const s = fullState(now);
  // スプレッドが pollIntervalMs ちょうど（reject しない境界）
  s.lastSeenAt[REQUIRED_KEYS[0]] = now;
  s.lastSeenAt[REQUIRED_KEYS[1]] = now - POLL_MS;
  s.lastCaptureAt = now;
  const d = evaluateSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d.shouldSend, true);
});

test("統合: beginSendAttempt により同一値のバックオフがリセットされない（P0 回帰）", () => {
  const s = fullState();
  const d1 = evaluateSend(s, 1000000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d1.shouldSend, true);
  assert.ok(d1.serialized);
  // 本番と同じ手順: 試行開始を記録
  beginSendAttempt(s, serializeIdentity(s.headersCaptured, REQUIRED_KEYS), 1000000);
  markSendFailure(s, MAX_BACKOFF_MS);
  assert.strictEqual(s.backoffMs, 1000);
  // 500ms 後、同一 identity で再評価 → identity 一致なので
  // バックオフはリセットされず、送信は抑止される（ホットリトライ防止）
  const d2 = evaluateSend(s, 1000500, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d2.shouldSend, false);
  assert.strictEqual(s.backoffMs, 1000); // リセットされていない
  // バックオフ経過後は送信される
  const d3 = evaluateSend(s, 1001000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d3.shouldSend, true);
});

test("統合: ゲート閉中に session-id が変化し、2 連続観測でバックオフがリセットされ送信される", () => {
  const now = 1000000;
  const s = fullState(now);
  // 初回送信
  const d0 = pollAndMaybeSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d0.shouldSend, true);
  assert.ok(d0.serialized);
  // 失敗でバックオフを伸ばす
  markSendFailure(s, MAX_BACKOFF_MS);
  markSendFailure(s, MAX_BACKOFF_MS); // backoff = 2000
  // 再ログイン（session-id 変更）
  s.headersCaptured["x-auth-session-id"] = "sid2";
  s.headersCaptured["x-auth-token"] = "tok2";
  s.headersCaptured["x-auth-signature"] = "sig2";
  for (const k of Object.keys(s.headersCaptured)) {
    s.lastSeenAt[k] = now + POLL_MS;
  }
  // 1 回目: ゲート閉 → 送信せず、確定待ちを保持
  const d1 = pollAndMaybeSend(s, now + POLL_MS, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d1.shouldSend, false);
  // 2 回目: 同一値で確定 → リセット → 即送信
  const d2 = pollAndMaybeSend(s, now + 2 * POLL_MS, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d2.shouldSend, true);
  assert.strictEqual(s.backoffMs, POLL_MS);
});

test("単一 state オブジェクト: ゲート閉中も pendingIdentityJson が保持される（反映漏れなし）", () => {
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
  // 値変化（再ログイン: session-id 変更）→ ゲート閉中の 1 回目観測
  s.headersCaptured["x-auth-session-id"] = "sid2";
  s.headersCaptured["x-auth-token"] = "tok2";
  for (const k of Object.keys(s.headersCaptured)) {
    s.lastSeenAt[k] = now + POLL_MS;
  }
  const d1 = pollAndMaybeSend(s, now + POLL_MS, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d1.shouldSend, false); // ゲート閉（backoff 2000 中）
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
  beginSendAttempt(s, serializeIdentity(s.headersCaptured, REQUIRED_KEYS), 1000000);
  markSendSuccess(s, d.serialized, POLL_MS);
  assert.strictEqual(s.backoffMs, POLL_MS);
  assert.strictEqual(s.lastSentJson, d.serialized);
  // dedupe: 同じ値を再送しない
  const d2 = evaluateSend(s, 1000500, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d2.shouldSend, false);
});

test("失敗→復旧パス: バックオフ経過後に最新署名で再送される", () => {
  // 注: lastSentJson が null（未成功）のため旧実装（署名込み dedupe）でも
  // パスするが、復旧時に snapshot へ最新の署名が載ることを固定する。
  const now = 1000000;
  const s = fullState(now);
  // 初回送信試行 → 失敗でバックオフ 1000ms
  const d0 = pollAndMaybeSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d0.shouldSend, true);
  markSendFailure(s, MAX_BACKOFF_MS);
  assert.strictEqual(s.backoffMs, 1000);
  // バックオフ中に署名だけがローテーションしても pendingIdentityJson は確定しない
  s.headersCaptured["x-auth-signature"] = "sig2";
  for (const k of Object.keys(s.headersCaptured)) {
    s.lastSeenAt[k] = now + 1000;
  }
  // バックオフ経過直後 → 再送される（snapshot には最新署名が含まれる）
  const d1 = pollAndMaybeSend(s, now + 1000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d1.shouldSend, true);
  assert.strictEqual(d1.snapshot["x-auth-signature"], "sig2");
});

test("送信済み identity を通過するフリッカーでも backoff がリセットされない（dedupe 経路の pending 残留防止）", () => {
  const now = 1000000;
  const s = fullState(now); // sid1 / u1（送信済みにできる値）
  // 初回送信成功
  const d0 = pollAndMaybeSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d0.shouldSend, true);
  markSendSuccess(s, d0.serialized, POLL_MS);
  // 障害中と想定してバックオフを伸長
  markSendFailure(s, MAX_BACKOFF_MS);
  markSendFailure(s, MAX_BACKOFF_MS);
  assert.strictEqual(s.backoffMs, 2000);
  // 送信済み値（sid1）を通過するフリッカー: sid2 → sid1 → sid2 → sid1
  // 定常状態の dedupe 経由（sid1 の観測）で pendingIdentityJson が残留する
  // と、2 連続観測でないのに確定して backoff が巻き戻る。それを禁止する。
  const seq = ["sid2", "sid1", "sid2", "sid1"];
  seq.forEach((sid, i) => {
    s.headersCaptured["x-auth-session-id"] = sid;
    s.headersCaptured["x-auth-user-id"] = sid === "sid1" ? "u1" : "u2";
    s.headersCaptured["x-auth-signature"] = "sig" + (i + 2);
    for (const k of Object.keys(s.headersCaptured)) {
      s.lastSeenAt[k] = now + i * POLL_MS;
    }
    const d = pollAndMaybeSend(s, now + i * POLL_MS, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
    assert.strictEqual(d.shouldSend, false); // ゲート閉のまま
  });
  // 2 連続観測が成立していないため backoff はリセットされない
  assert.strictEqual(s.backoffMs, 2000);
});

test("token 変化の再送は成功後の 2 秒ゲートを尊重する", () => {
  const now = 1000000;
  const s = fullState(now);
  const d0 = pollAndMaybeSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d0.shouldSend, true);
  // 本番の markSendSuccess は MIN_SEND_INTERVAL_MS = 2000 を渡す
  markSendSuccess(s, d0.serialized, 2000);
  // token のみ変化（署名・session-id は不変）
  s.headersCaptured["x-auth-token"] = "tok2";
  for (const k of Object.keys(s.headersCaptured)) {
    s.lastSeenAt[k] = now + 1000;
  }
  // 2 秒未満 → ゲート閉で送信しない
  const d1 = pollAndMaybeSend(s, now + 1000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d1.shouldSend, false);
  // 2 秒経過後 → 再送される
  const d2 = pollAndMaybeSend(s, now + 2000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d2.shouldSend, true);
});

test("ゲート開放中は session-id の 1 回目の観測で即送信される（再ログイン即送信）", () => {
  // 注: markSendSuccess にはテスト用の POLL_MS を渡す（本番は
  // MIN_SEND_INTERVAL_MS=2000）。目的は「ゲート開放時の 1 回目観測での
  // 即送信」の pin であり、間隔値自体は token テストで固定している。
  const now = 1000000;
  const s = fullState(now);
  const d0 = pollAndMaybeSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d0.shouldSend, true);
  markSendSuccess(s, d0.serialized, POLL_MS);
  // バックオフ経過後（ゲート開放）に session-id が変化
  s.headersCaptured["x-auth-session-id"] = "sid2";
  s.headersCaptured["x-auth-user-id"] = "u2";
  for (const k of Object.keys(s.headersCaptured)) {
    s.lastSeenAt[k] = now + 2 * POLL_MS;
  }
  const d2 = pollAndMaybeSend(s, now + 2 * POLL_MS, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  // 2 連続観測のガードはバックオフ中のみ有効。ゲート開放時は 1 回目で送信
  assert.strictEqual(d2.shouldSend, true);
});

test("identity フリッカー（sidA↔sidB）で 2 連続観測が成立せず backoff がリセットされない", () => {
  const s = fullState();
  const d0 = pollAndMaybeSend(s, 1000000, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d0.shouldSend, true);
  markSendFailure(s, MAX_BACKOFF_MS);
  markSendFailure(s, MAX_BACKOFF_MS);
  assert.strictEqual(s.backoffMs, 2000);
  // フリッカー: session-id が毎回変わる
  const now = 1000000;
  for (let i = 0; i < 4; i++) {
    s.headersCaptured["x-auth-session-id"] = i % 2 === 0 ? "sidA" : "sidB";
    for (const k of Object.keys(s.headersCaptured)) {
      s.lastSeenAt[k] = now + i * POLL_MS;
    }
    const d = pollAndMaybeSend(s, now + i * POLL_MS, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
    assert.strictEqual(d.shouldSend, false); // ゲート閉のまま
  }
  // 2 連続同一観測が成立しないためバックオフはリセットされない
  assert.strictEqual(s.backoffMs, 2000);
});

test("署名のみローテーションしても再送されない（dedupe から signature を除外）", () => {
  const s = fullState();
  const now = 1000000;
  // 初回送信成功
  const d0 = pollAndMaybeSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d0.shouldSend, true);
  markSendSuccess(s, d0.serialized, POLL_MS);
  // 継続トラフィック: 署名だけがローテーションし、署名の捕捉が 600ms 遅延する
  // （lastCaptureAt が常に直近 = capturing だが、dedupe は署名を除外して
  //   判定するため再送は発生しない）
  let t = now + 2000;
  for (let i = 2; i <= 4; i++) {
    for (const k of REQUIRED_KEYS) {
      s.lastSeenAt[k] = t;
    }
    s.lastSeenAt["x-auth-signature"] = t + 600;
    s.lastCaptureAt = t + 600;
    s.headersCaptured["x-auth-signature"] = "sig" + i; // d0 の sig1 と異なる値
    const d = pollAndMaybeSend(s, t + 600, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
    assert.strictEqual(d.shouldSend, false); // dedupe（署名除外）
    t += 2000;
  }
});

test("署名以外のキー（token）が変われば再送される", () => {
  const s = fullState();
  const now = 1000000;
  const d0 = pollAndMaybeSend(s, now, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d0.shouldSend, true);
  markSendSuccess(s, d0.serialized, POLL_MS);
  // token のみ変化（署名・session-id は不変）
  s.headersCaptured["x-auth-token"] = "tok2";
  for (const k of Object.keys(s.headersCaptured)) {
    s.lastSeenAt[k] = now + POLL_MS;
  }
  const d1 = pollAndMaybeSend(s, now + POLL_MS, REQUIRED_KEYS, STALE_TTL_MS, FRESH_WINDOW_MS, POLL_MS);
  assert.strictEqual(d1.shouldSend, true); // dedupe に含まれるキーの変化
});

test("serializeForDedupe は x-auth-signature を含まない", () => {
  const s = fullState();
  const serialized = serializeForDedupe(s.headersCaptured, REQUIRED_KEYS);
  const parsed = JSON.parse(serialized);
  assert.ok(!("x-auth-signature" in parsed));
  assert.deepStrictEqual(
    Object.keys(parsed).sort(),
    REQUIRED_KEYS.filter((k) => k !== "x-auth-signature").sort(),
  );
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
