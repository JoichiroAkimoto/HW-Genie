// セッション送信の状態機械を純関数として分離したモジュール。
// index.ts（userscript 本体）から import され、tests/ からも直接 import
// される（本番コードをそのまま検証するため）。

export interface SessionState {
  headersCaptured: Record<string, string> | null;
  lastSeenAt: Record<string, number>;
  lastSentJson: string | null;
  lastAttemptedJson: string | null;
  backoffMs: number;
  lastAttemptAt: number;
}

export interface SendDecision {
  shouldSend: boolean;
  serialized: string | null;
  snapshot: Record<string, string> | null;
}

export function pruneStaleKeys(
  headersCaptured: Record<string, string>,
  lastSeenAt: Record<string, number>,
  now: number,
  staleKeyTtlMs: number,
): void {
  for (const key of Object.keys(headersCaptured)) {
    if (now - (lastSeenAt[key] ?? 0) > staleKeyTtlMs) {
      delete headersCaptured[key];
      delete lastSeenAt[key];
    }
  }
}

/**
 * 現在の状態から「送信すべきか」を判定する関数。
 *
 * 副作用は引数の SessionState（s）に限定される: pruneStaleKeys が
 * headersCaptured / lastSeenAt を破壊的に変更し、バックオフのリセット
 * （再ログイン検知時）も s.backoffMs を書き換える。ネットワーク I/O や
 * グローバル状態への副作用はない。
 *
 * 1. STALE_KEY_TTL_MS 超過キーを prune（呼び出し側で headersCaptured を
 *    変更するため、この関数は判定前に実行すること）
 * 2. 必須 6 キーの存在 + FRESH_WINDOW 内であること（新旧混在ガード）+
 *    コヒーレント集合チェック（キー間の観測時刻差）
 * 3. snapshot + キーソート正規化シリアライズ
 * 4. lastSentJson と一致 → 再送しない（dedupe）
 * 5. lastAttemptedJson と不一致 → backoffMs をリセット（再ログイン検知）
 * 6. now - lastAttemptAt < backoffMs → 送らない
 */
export function evaluateSend(
  s: SessionState,
  now: number,
  requiredKeys: string[],
  staleKeyTtlMs: number,
  freshWindowMs: number,
  pollIntervalMs: number,
): SendDecision {
  if (!s.headersCaptured) {
    return { shouldSend: false, serialized: null, snapshot: null };
  }
  pruneStaleKeys(s.headersCaptured, s.lastSeenAt, now, staleKeyTtlMs);

  // 必須キーの存在 + 全キーが FRESH_WINDOW 内に観測されていること
  if (!requiredKeys.every((key) => key in s.headersCaptured!)) {
    return { shouldSend: false, serialized: null, snapshot: null };
  }
  if (
    !requiredKeys.every(
      (key) => now - (s.lastSeenAt[key] ?? 0) <= freshWindowMs,
    )
  ) {
    return { shouldSend: false, serialized: null, snapshot: null };
  }
  // コヒーレント集合チェック: 全キーの観測時刻が freshWindowMs 以内に収まって
  // いること。再ログイン遷移中に旧セッションのキーが「直近観測」のまま残って
  // いると、新旧混在ペイロードを送信し得るため、キー間の時刻差も検証する。
  const times = requiredKeys.map((key) => s.lastSeenAt[key] ?? 0);
  if (Math.max(...times) - Math.min(...times) > freshWindowMs) {
    return { shouldSend: false, serialized: null, snapshot: null };
  }

  // 既知キーのみの snapshot + キー順正規化
  const snapshot: Record<string, string> = {};
  for (const key of requiredKeys) {
    snapshot[key] = s.headersCaptured[key];
  }
  const serialized = JSON.stringify(snapshot, Object.keys(snapshot).sort());
  if (serialized === s.lastSentJson) {
    return { shouldSend: false, serialized, snapshot };
  }

  // ヘッダーが前回試行と変わったらバックオフをリセット（再ログイン検知）
  if (serialized !== s.lastAttemptedJson) {
    s.backoffMs = pollIntervalMs;
  }
  if (now - s.lastAttemptAt < s.backoffMs) {
    return { shouldSend: false, serialized, snapshot };
  }

  return { shouldSend: true, serialized, snapshot };
}

/** send 試行開始時に呼ぶ: 送信した値と時刻を記録する。 */
export function beginSendAttempt(
  s: SessionState,
  serialized: string,
  now: number,
): void {
  s.lastAttemptedJson = serialized;
  s.lastAttemptAt = now;
}

/** send 成功時に呼ぶ: lastSentJson 更新とバックオフリセット。 */
export function markSendSuccess(s: SessionState, serialized: string, pollIntervalMs: number): void {
  s.lastSentJson = serialized;
  s.backoffMs = pollIntervalMs;
}

/** send 失敗時に呼ぶ: バックオフを指数関数的に増加（上限あり）。 */
export function markSendFailure(s: SessionState, maxBackoffMs: number): void {
  s.backoffMs = Math.min(s.backoffMs * 2, maxBackoffMs);
}
