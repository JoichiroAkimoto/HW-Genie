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

/**
 * レスポンスボディの読み取りにタイムアウトを適用する。
 *
 * fetchWithTimeout はヘッダー到着時点で resolve しタイマーが破棄されるため、
 * ボディを返さない（stall する）サーバーでは res.text() / res.json() が
 * 永久に pending になり、呼び出し側の sending フラグが固まり得る。これを防ぐ。
 */
async function readBodyWithTimeout(
  res: Response,
  timeoutMs: number,
): Promise<string> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () => reject(new DOMException("Aborted", "AbortError")),
      timeoutMs,
    );
  });
  try {
    return await Promise.race([res.text(), timeout]);
  } finally {
    clearTimeout(timer);
  }
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
    const body = await readBodyWithTimeout(res, timeoutMs);
    const data = JSON.parse(body);
    return data.nonce;
  } catch {
    return null;
  }
}

export interface SendResult {
  ok: boolean;
  playerName: string | null;
  reason?: "nonce-failed" | `http-${number}` | "status-error" | "network" | "timeout";
  /** サーバーが返した失敗理由（FastAPI の detail など）。診断ログ用。 */
  detail?: string;
}

const MAX_DETAIL_LENGTH = 300;

/**
 * 診断ログ用の詳細文字列を最大 300 文字（コードポイント基準）に切り詰める。
 *
 * ``String.prototype.slice`` は UTF-16 コードユニットで切るため、絵文字など
 * サロゲートペアの途中で切ると壊れた文字（孤立サロゲート）になる。
 * ``Array.from`` でコードポイント配列にしてから切る（JSON / 非 JSON 共通）。
 */
function truncateDetail(text: string): string {
  const codePoints = Array.from(text);
  return codePoints.length > MAX_DETAIL_LENGTH
    ? `${codePoints.slice(0, MAX_DETAIL_LENGTH).join("")}…`
    : text;
}

/**
 * エラー本文から表示用の理由を取り出す。
 *
 * FastAPI はエラーを ``{"detail": "..."}`` で返すため、JSON なら detail
 * （無ければ message）の文字列だけを使う。それ以外の JSON 本文
 * （422 の ``detail`` 配列など）は生の本文を出さず undefined にする
 * （送信ヘッダー等の秘匿値がコンソールに残るのを避ける）。
 * 非 JSON 本文・JSON の detail / message はいずれも長すぎる場合は
 * 300 文字 + ``…`` に切り詰める。
 */
function extractErrorDetail(body: string): string | undefined {
  const text = body.trim();
  if (!text) {
    return undefined;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    // JSON でない本文はそのまま表示する
    return truncateDetail(text);
  }
  if (typeof parsed !== "object" || parsed === null) {
    return undefined;
  }
  const data = parsed as { detail?: unknown; message?: unknown };
  if (typeof data.detail === "string" && data.detail.trim()) {
    return truncateDetail(data.detail.trim());
  }
  if (typeof data.message === "string" && data.message.trim()) {
    return truncateDetail(data.message.trim());
  }
  return undefined;
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
    return { ok: false, playerName: null, reason: "nonce-failed" };
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
      const errorBody = await readBodyWithTimeout(res, timeoutMs);
      return {
        ok: false,
        playerName: null,
        reason: `http-${res.status}`,
        detail: extractErrorDetail(errorBody),
      };
    }
    const body = await readBodyWithTimeout(res, timeoutMs);
    const data = JSON.parse(body);
    if (data.status === "success") {
      return { ok: true, playerName: data.player?.name ?? null };
    }
    return {
      ok: false,
      playerName: null,
      reason: "status-error",
      detail:
        typeof data.message === "string" && data.message.trim()
          ? truncateDetail(data.message.trim())
          : undefined,
    };
  } catch (e) {
    // 環境（Bun / Node / Tampermonkey サンドボックス）によっては fetch の
    // 中断エラーが DOMException の直接インスタンスでないことがあるため、
    // name ベースで判定する（e?.name === "AbortError"）。
    if ((e instanceof DOMException || (e as Error)?.name === "AbortError")) {
      return { ok: false, playerName: null, reason: "timeout" };
    }
    return { ok: false, playerName: null, reason: "network" };
  }
}
