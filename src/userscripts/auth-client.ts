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

/**
 * 捕捉した認証ヘッダーを認証サーバーへ送信し、成功したかどうかを返す。
 *
 * - 非 2xx: 本文が JSON とは限らないため res.ok を先に確認し、text() で
 *   安全に読み取る（P3 回帰: json() を先に呼ばない）。
 * - 2xx でも status !== "success" なら false。
 */
export async function sendHeadersToServer(
  baseUrl: string,
  headers: Record<string, string>,
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
  fetchImpl: typeof fetch = fetch,
): Promise<boolean> {
  const nonce = await fetchNonce(baseUrl, timeoutMs, fetchImpl);
  if (!nonce) {
    return false;
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
      return false;
    }
    const data = await res.json();
    return data.status === "success";
  } catch {
    return false;
  }
}
