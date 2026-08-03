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

// ゲームがリクエスト毎にローテーションするため、dedupe / バックオフ比較の
// シリアライズから除外する（送信ペイロードには含め、サーバー側の必須ヘッダー
// 検証は維持する）。
export const SIGNATURE_HEADER_KEY = "x-auth-signature";
// セッション同一性キー（再ログイン検知と新旧混在ガードで使用。session.ts
// 内のみで使用するため export しない）。
const IDENTITY_HEADER_KEYS = ["x-auth-session-id", "x-auth-user-id"];

export interface SendDecision {
  shouldSend: boolean;
  serialized: string | null;
  snapshot: Record<string, string> | null;
}

/**
 * 指定キーのみで構成した正規化シリアライズ（キー順はソート済み）。
 *
 * serializeForDedupe と serializeIdentity の共通実装。
 */
function serializeHeaders(
  headersCaptured: Record<string, string>,
  keys: string[],
): string {
  const sorted = [...keys].sort();
  const snapshot: Record<string, string> = {};
  for (const key of sorted) {
    snapshot[key] = headersCaptured[key];
  }
  return JSON.stringify(snapshot);
}

/**
 * dedupe / バックオフ比較用の正規化シリアライズ。
 *
 * x-auth-signature を除外した必須キーのみで構成する。署名はリクエスト毎に
 * ローテーションされるため、これを含めると継続トラフィック下で値が常に変化し、
 * 同一セッションの再送が止まらなくなる。実セッション値（token 等を含む）が
 * 変わった場合のみ再送が発生する（署名が不変な安静状態ではページロード時に
 * 1 回の送信で止まる）。
 */
export function serializeForDedupe(
  headersCaptured: Record<string, string>,
  requiredKeys: string[],
): string {
  return serializeHeaders(
    headersCaptured,
    requiredKeys.filter((key) => key !== SIGNATURE_HEADER_KEY),
  );
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
 * 3. snapshot（送信用: 全必須キー）+ dedupe 用シリアライズ（署名除外）
 * 4. セッション同一性の変化検知 → pendingIdentityJson の確定待ち /
 *    クリア（再ログインは 2 連続観測で backoffMs をリセット）
 * 5. dedupe 用シリアライズが lastSentJson と一致 → 再送しない（dedupe）
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

  // セッション同一性キー（session-id / user-id）のシリアライズ。
  // 再ログイン検知と新旧混在ガードの両方で使う（署名は除外）。
  const identitySerialized = serializeIdentity(s.headersCaptured, requiredKeys);
  const identityChanged =
    identitySerialized !== s.lastAttemptedIdentityJson &&
    s.pendingIdentityJson !== identitySerialized;

  // コヒーレント集合チェック: キー間の観測時刻差が coherentWindowMs を超え、
  // かつ捕捉が進行中（遷移中）かつセッション同一性が変化している場合は新旧
  // 混在として送信しない。同一セッション内の遅延キー（非同期署名計算等）は
  // 混在ではなく、identity が不変なら抑止しない（継続トラフィック下の恒久
  // 抑止を回避）。
  // なお、pruneStaleKeys 実行後に必須キーが存在することは、全キーが TTL 内
  // （lastSeenAt が staleKeyTtlMs 以内）であることを論理的に保証するため、
  // 追加の TTL チェックは不要。
  const coherentWindowMs = Math.min(freshWindowMs, pollIntervalMs);
  const times = requiredKeys.map((key) => s.lastSeenAt[key] ?? 0);
  const spread = Math.max(...times) - Math.min(...times);
  const capturing = now - (s.lastCaptureAt ?? 0) < coherentWindowMs;
  if (spread > coherentWindowMs && capturing && identityChanged) {
    return { shouldSend: false, serialized: null, snapshot: null };
  }

  // 既知キーのみの snapshot + キー順正規化。送信ペイロードは全必須キー
  // （x-auth-signature 含む）を維持し、サーバー側の必須ヘッダー検証を通す。
  const snapshot: Record<string, string> = {};
  for (const key of requiredKeys) {
    snapshot[key] = s.headersCaptured[key];
  }
  // dedupe 用シリアライズは署名を除外（署名ローテーションでは再送しない）。
  const serialized = serializeForDedupe(s.headersCaptured, requiredKeys);

  // 再ログイン検知: セッション同一性キー（session-id / user-id）の変化のみで
  // バックオフをリセットする。署名はリクエスト毎にローテーションされうるため
  // リセット対象にしない（サーバー障害時のホットリトライを防ぐ）。
  // 同一性キーが 2 連続ポーリングで同じ新値を観測した場合のみ確定する。
  // dedupe チェックより前に置く: 送信済みセッションの定常状態では dedupe が
  // 毎ポーリング一致するため、ここが実行されないと pendingIdentityJson が
  // クリーンアップされず、非連続観測で誤確定する（フリッカー耐性が崩れる）。
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

  if (serialized === s.lastSentJson) {
    return { shouldSend: false, serialized, snapshot };
  }
  if (now - s.lastAttemptAt < s.backoffMs) {
    return { shouldSend: false, serialized, snapshot };
  }

  return { shouldSend: true, serialized, snapshot };
}

/** send 試行開始時に呼ぶ: セッション同一性と試行時刻を記録する。 */
export function beginSendAttempt(
  s: SessionState,
  identitySerialized: string,
  now: number,
): void {
  s.lastAttemptedIdentityJson = identitySerialized;
  s.lastAttemptAt = now;
}

/** セッション同一性キー（session-id / user-id）のみの正規化シリアライズ。 */
export function serializeIdentity(
  headersCaptured: Record<string, string>,
  requiredKeys: string[],
): string {
  return serializeHeaders(
    headersCaptured,
    requiredKeys.filter((key) => IDENTITY_HEADER_KEYS.includes(key)),
  );
}

/**
 * ポーリング 1 回分の「判定 + 送信準備」をまとめたオーケストレーション。
 *
 * evaluateSend の state 変更（pendingIdentityJson / backoffMs / lastSeenAt の
 * prune 等）は s に残るため、呼び出し側は常に s をクロージャへ反映するだけでよい。shouldSend=false（バックオフ抑止・確定待ち）
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
  beginSendAttempt(s, identitySerialized, now);
  return decision;
}

/**
 * send 成功時に呼ぶ: lastSentJson 更新とバックオフの基準間隔へのリセット。
 *
 * @param resetBackoffMs 成功後のバックオフ基準間隔（本番では MIN_SEND_INTERVAL_MS
 * を渡す。再ログイン検知以外ではこの間隔まで連続送信を抑える）
 */
export function markSendSuccess(s: SessionState, serialized: string, resetBackoffMs: number): void {
  s.lastSentJson = serialized;
  s.backoffMs = resetBackoffMs;
}

/** send 失敗時に呼ぶ: バックオフを指数関数的に増加（上限あり）。 */
export function markSendFailure(s: SessionState, maxBackoffMs: number): void {
  s.backoffMs = Math.min(s.backoffMs * 2, maxBackoffMs);
}
