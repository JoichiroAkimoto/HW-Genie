// 認証サーバーへの送信クライアント。index.ts から import され、tests/ からも
// 直接 import される（fetch を注入可能にして HTTP 層をテストするため）。

const DEFAULT_TIMEOUT_MS = 5000;

/** AbortController ベースのタイムアウト付き fetch（AbortSignal.timeout 非依存）。 */
export function fetchWithTimeout(
  url: string,
  options: RequestInit = {},
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
  fetchImpl: typeof fetch = fetch,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetchImpl(url, { ...options, signal: controller.signal }).finally(() =>
    clearTimeout(timer),
  );
}

export async function fetchNonce(
  baseUrl: string,
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
  fetchImpl: typeof fetch = fetch,
): Promise<string | null> {
  try {
    const res = await fetchWithTimeout(
      `${baseUrl}/nonce`,
      {},
      timeoutMs,
      fetchImpl,
    );
    if (!res.ok) {
      return null;
    }
    const data = await res.json();
    return data.nonce;
  } catch {
    return null;
  }
}

export interface SendResult {
  ok: boolean;
  playerName: string | null;
}

/**
 * 捕捉した認証ヘッダーを認証サーバーへ送信し、結果を返す。
 *
 * - 非 2xx: 本文が JSON とは限らないため res.ok を先に確認し、text() で
 *   安全に読み取る（P3 回帰: json() を先に呼ばない）。
 * - 2xx でも status !== "success" なら ok:false。
 * - 成功時はプレイヤー名を返す（成功ログ用）。
 */
export async function sendHeadersToServer(
  baseUrl: string,
  headers: Record<string, string>,
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
  fetchImpl: typeof fetch = fetch,
): Promise<SendResult> {
  const nonce = await fetchNonce(baseUrl, timeoutMs, fetchImpl);
  if (!nonce) {
    return { ok: false, playerName: null };
  }
  try {
    const res = await fetchWithTimeout(
      `${baseUrl}/auth`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nonce, headers }),
      },
      timeoutMs,
      fetchImpl,
    );
    if (!res.ok) {
      // 非 2xx: 本文が JSON とは限らないため、先に ok を確認する
      await res.text();
      return { ok: false, playerName: null };
    }
    const data = await res.json();
    return {
      ok: data.status === "success",
      playerName: data.status === "success" ? (data.player?.name ?? null) : null,
    };
  } catch {
    return { ok: false, playerName: null };
  }
}
