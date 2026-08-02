// セッション送信の状態機械を純関数として分離したモジュール。
// index.ts（userscript 本体）から import され、tests/ からも直接 import
// される（本番コードをそのまま検証するため）。

export interface SessionState {
  headersCaptured: Record<string, string> | null;
  lastSeenAt: Record<string, number>;
  // 最後に必須キーのいずれかを捕捉した時刻。コヒーレント集合チェックで
  // 「捕捉が進行中か」を判定するために使う（落ち着いた完全セットの恒久
  // 抑止を防ぐ）。
  lastCaptureAt: number;
  lastSentJson: string | null;
  lastAttemptedJson: string | null;
  // 再ログイン検知の確定待ち: セッション同一性キー（session-id / user-id）が
  // 変わったとき 1 回目はここに記録し、2 連続ポーリングで同じ新値が観測された
  // 場合のみバックオフをリセットする。署名のローテーション（毎回変化）では
  // リセットしない（ホットリトライに戻るのを防ぐ）。
  pendingIdentityJson: string | null;
  // セッション同一性キーのみの直近試行値（バックオフリセット判定用）
  lastAttemptedIdentityJson: string | null;
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

  // 必須キーの存在
  if (!requiredKeys.every((key) => key in s.headersCaptured!)) {
    return { shouldSend: false, serialized: null, snapshot: null };
  }
  // コヒーレント集合チェック: キー間の観測時刻差が coherentWindowMs を超え、
  // かつ捕捉が進行中（遷移中）の場合は新旧混在として送信しない。
  // 捕捉が止まっていれば「落ち着いた完全セット」とみなし、per-key 鮮度は
  // TTL 基準（staleKeyTtlMs）で判定する（並行 XHR / 非同期署名計算による
  // 恒久抑止を回避。FRESH_WINDOW 基準だと settled 前に最古キーが窓を超える）。
  const coherentWindowMs = Math.min(freshWindowMs, pollIntervalMs);
  const times = requiredKeys.map((key) => s.lastSeenAt[key] ?? 0);
  const spread = Math.max(...times) - Math.min(...times);
  const transitioning =
    spread > coherentWindowMs && now - (s.lastCaptureAt ?? 0) < coherentWindowMs;
  if (transitioning) {
    return { shouldSend: false, serialized: null, snapshot: null };
  }
  // settled（またはコヒーレント）: 全キーが TTL 内に観測されていること
  if (
    !requiredKeys.every(
      (key) => now - (s.lastSeenAt[key] ?? 0) <= staleKeyTtlMs,
    )
  ) {
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

  // 再ログイン検知: セッション同一性キー（session-id / user-id）の変化のみで
  // バックオフをリセットする。署名はリクエスト毎にローテーションされうるため
  // リセット対象にしない（サーバー障害時のホットリトライを防ぐ）。
  // 同一性キーが 2 連続ポーリングで同じ新値を観測した場合のみ確定する。
  const identityKeys = requiredKeys.filter(
    (key) => key === "x-auth-session-id" || key === "x-auth-user-id",
  );
  const identitySnapshot: Record<string, string> = {};
  for (const key of identityKeys) {
    identitySnapshot[key] = s.headersCaptured[key];
  }
  const identitySerialized = JSON.stringify(
    identitySnapshot,
    Object.keys(identitySnapshot).sort(),
  );
  if (identitySerialized !== s.lastAttemptedIdentityJson) {
    if (s.pendingIdentityJson === identitySerialized) {
      // 同一の新値が 2 連続観測 → 本物の再ログイン
      s.backoffMs = pollIntervalMs;
      s.pendingIdentityJson = null;
    } else {
      // 1 回目の観測: 確定待ち
      s.pendingIdentityJson = identitySerialized;
    }
  } else {
    // 前回試行と同一 → 変化なし
    s.pendingIdentityJson = null;
  }
  if (now - s.lastAttemptAt < s.backoffMs) {
    return { shouldSend: false, serialized, snapshot };
  }

  return { shouldSend: true, serialized, snapshot };
}

/** send 試行開始時に呼ぶ: 送信した値とセッション同一性を記録する。 */
export function beginSendAttempt(
  s: SessionState,
  serialized: string,
  identitySerialized: string,
  now: number,
): void {
  s.lastAttemptedJson = serialized;
  s.lastAttemptedIdentityJson = identitySerialized;
  s.lastAttemptAt = now;
}

/** セッション同一性キー（session-id / user-id）のみの正規化シリアライズ。 */
export function serializeIdentity(
  headersCaptured: Record<string, string>,
  requiredKeys: string[],
): string {
  const identityKeys = requiredKeys.filter(
    (key) => key === "x-auth-session-id" || key === "x-auth-user-id",
  );
  const identitySnapshot: Record<string, string> = {};
  for (const key of identityKeys) {
    identitySnapshot[key] = headersCaptured[key];
  }
  return JSON.stringify(identitySnapshot, Object.keys(identitySnapshot).sort());
}

/**
 * ポーリング 1 回分の「判定 + 送信準備」をまとめたオーケストレーション。
 *
 * evaluateSend の state 変更（pendingChangeJson / pendingIdentityJson /
 * backoffMs / lastSeenAt の prune 等）は s に残るため、呼び出し側は常に s を
 * クロージャへ反映するだけでよい。shouldSend=false（バックオフ抑止・確定待ち）
 * でも確定待ちがポーリング間で失われない。
 */
export function pollAndMaybeSend(
  s: SessionState,
  now: number,
  requiredKeys: string[],
  staleKeyTtlMs: number,
  freshWindowMs: number,
  pollIntervalMs: number,
): SendDecision {
  const decision = evaluateSend(
    s,
    now,
    requiredKeys,
    staleKeyTtlMs,
    freshWindowMs,
    pollIntervalMs,
  );
  if (!decision.shouldSend || !decision.serialized || !decision.snapshot) {
    return decision;
  }
  const identitySerialized = serializeIdentity(
    s.headersCaptured!,
    requiredKeys,
  );
  beginSendAttempt(s, decision.serialized, identitySerialized, now);
  return decision;
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
