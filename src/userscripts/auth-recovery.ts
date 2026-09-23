// セッション失効時の自動リロード判定を純関数として分離したモジュール。
// index.ts（userscript 本体）から import され、tests/ からも直接 import
// される（本番コードをそのまま検証するため）。
//
// 設計: DOM 監視はしない。XHR レスポンス監視のみでセッション失効を検知し、
// location.reload() で回復を図る。無限リロード防止のためクールダウンと
// ウィンドウ上限を sessionStorage カウンタで制御する。

export const RELOAD_COOLDOWN_MS = 5 * 60 * 1000;
export const RELOAD_WINDOW_MS = 10 * 60 * 1000;
export const MAX_RELOADS_PER_WINDOW = 3;
export const STORAGE_KEY = "hw-genie-auto-reload";
// 正常レスポンスの JSON.parse を避けるための上限。超過分は 401 以外スキップする。
export const MAX_BODY_BYTES = 256 * 1024;

export interface ReloadState {
  lastReloadAt: number;
  windowStart: number;
  reloadCount: number;
}

export type ReloadAction = "reload" | "suppress";

// セッション失効を示す error.name の集合。
// 意図的に完全一致・大文字区別とする。Python 側の正条件
// (client.py call() / auth.py get_user_info(): error_name in ("auth", "InvalidSession"))
// と一致させるためであり、小文字化・部分一致はしない。
const SESSION_ERROR_NAMES: ReadonlySet<string> = new Set([
  "auth",
  "InvalidSession",
]);

/**
 * 候補オブジェクトの `error` からエラー名を取り出す。
 *
 * Python 正条件 (client.py call(): `error.get("name") if isinstance(error, dict)
 * else str(error)` / auth.py get_user_info(): `error.get("name") if
 * isinstance(error, dict) else error`) との整合のため、`error` が文字列の
 * 場合はそのままエラー名として扱う（例: `{"error":"auth"}` → "auth"）。
 * dict の場合は `name` が文字列のときのみ返す。非該当は null。
 */
function extractErrorName(candidate: unknown): string | null {
  if (typeof candidate !== "object" || candidate === null) {
    return null;
  }
  const err: unknown = (candidate as Record<string, unknown>).error;
  if (typeof err === "string") {
    return err;
  }
  if (typeof err !== "object" || err === null) {
    return null;
  }
  const name = (err as Record<string, unknown>).name;
  return typeof name === "string" ? name : null;
}

/**
 * XHR レスポンスがセッション失効を示すか判定する。
 *
 * - HTTP 範囲外の status（0 等。100〜599 以外）→ false（仕様）。
 * - status === 401 → true（サイズガードより優先）
 * - 非 JSON ボディに "Invalid signature" を含む場合 → true
 *   （Python auth.py `_unexpected_response_message` と一致。JSON でない
 *   API 応答の署名失効メッセージを拾う）
 * - bodyText を JSON.parse し、トップレベルの `error` または `results`
 *   配列内の各要素の `error` の name が "auth" / "InvalidSession"
 *   （完全一致・大文字区別。Python と一致させるため意図的）なら true
 * - `results` の複数走査は意図的な拡張である: Python call() は単一コール時
 *   (len(results) == 1) のみエラーチェックし、複数コール時は ident 辞書を
 *   そのまま返す。一方リロードトリガとしては広め（複数コール中の 1 件でも
 *   失効なら回復）に倒す方が安全なため、全件走査とする。
 * - 性能: 全正常レスポンスでの JSON.parse を避けるため、部分文字列
 *   ("InvalidSession" / `"auth"` / "Invalid signature" / `"error"` の
 *   いずれか) を含まなければ parse せず false。256KB 超のボディは
 *   401 以外スキップする。
 * - parse 失敗・非該当 → false。例外を投げない。
 */
export function isSessionExpired(
  status: number,
  bodyText: string | null | undefined,
): boolean {
  try {
    if (!Number.isInteger(status) || status < 100 || status > 599) {
      return false;
    }
    if (status === 401) {
      return true;
    }
    if (typeof bodyText !== "string" || bodyText.length === 0) {
      return false;
    }
    if (bodyText.length > MAX_BODY_BYTES) {
      return false;
    }
    if (
      !bodyText.includes("InvalidSession") &&
      !bodyText.includes('"auth"') &&
      !bodyText.includes("Invalid signature") &&
      !bodyText.includes('"error"')
    ) {
      return false;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(bodyText);
    } catch {
      // 非 JSON ボディの署名失効メッセージは失効確定とする
      // (Python auth.py `_unexpected_response_message` と一致)。
      return bodyText.includes("Invalid signature");
    }
    if (typeof parsed !== "object" || parsed === null) {
      return bodyText.includes("Invalid signature");
    }
    const topName = extractErrorName(parsed);
    if (topName !== null && SESSION_ERROR_NAMES.has(topName)) {
      return true;
    }
    const results = (parsed as Record<string, unknown>).results;
    if (Array.isArray(results)) {
      for (const item of results) {
        const name = extractErrorName(item);
        if (name !== null && SESSION_ERROR_NAMES.has(name)) {
          return true;
        }
      }
    }
    return false;
  } catch {
    return false;
  }
}

/**
 * 自動リロードしてよいか判定する（純関数）。
 *
 * - now - lastReloadAt < cooldownMs → false
 * - window 外 (now - windowStart >= windowMs) → true（呼び出し側が
 *   window をリセットすることを想定）
 * - それ以外は reloadCount < maxReloads で判定する。
 *
 * 新規コードは `evaluateReload` を使うこと。こちらは後方互換のため残す。
 */
export function shouldAutoReload(
  state: { lastReloadAt: number; windowStart: number; reloadCount: number },
  now: number,
  cooldownMs: number = RELOAD_COOLDOWN_MS,
  windowMs: number = RELOAD_WINDOW_MS,
  maxReloads: number = MAX_RELOADS_PER_WINDOW,
): boolean {
  if (now - state.lastReloadAt < cooldownMs) {
    return false;
  }
  if (now - state.windowStart >= windowMs) {
    return true;
  }
  return state.reloadCount < maxReloads;
}

export interface ReloadDecision {
  /** リロード確定時は更新後の状態、suppress 時は入力の複製（不変）。 */
  state: ReloadState;
  action: ReloadAction;
}

/**
 * 次状態と action を一括で返す純関数。index.ts はこちらだけを使う。
 *
 * 仕様:
 * - クールダウン優先: クールダウン内 (now - lastReloadAt < cooldownMs) なら
 *   ウィンドウ外であっても suppress（状態不変）。リロード直後の連続失効で
 *   ウィンドウリセットによる即リロードを防ぐため。
 * - クールダウン外かつウィンドウ外 (now - windowStart >= windowMs) なら
 *   ウィンドウをリセットして reload（reloadCount は 1 から数え直し）。
 * - クールダウン外かつウィンドウ内は reloadCount < maxReloads なら reload、
 *   そうでなければ suppress（状態不変）。
 * - 例外を投げない。
 */
export function evaluateReload(
  state: ReloadState,
  now: number,
  cooldownMs: number = RELOAD_COOLDOWN_MS,
  windowMs: number = RELOAD_WINDOW_MS,
  maxReloads: number = MAX_RELOADS_PER_WINDOW,
): ReloadDecision {
  const snapshot: ReloadState = {
    lastReloadAt: state.lastReloadAt,
    windowStart: state.windowStart,
    reloadCount: state.reloadCount,
  };
  if (now - snapshot.lastReloadAt < cooldownMs) {
    return { state: snapshot, action: "suppress" };
  }
  if (now - snapshot.windowStart >= windowMs) {
    return {
      state: { lastReloadAt: now, windowStart: now, reloadCount: 1 },
      action: "reload",
    };
  }
  if (snapshot.reloadCount < maxReloads) {
    return {
      state: {
        lastReloadAt: now,
        windowStart: snapshot.windowStart,
        reloadCount: snapshot.reloadCount + 1,
      },
      action: "reload",
    };
  }
  return { state: snapshot, action: "suppress" };
}

/** 空の初期状態を返す。 */
export function defaultReloadState(): ReloadState {
  return { lastReloadAt: 0, windowStart: 0, reloadCount: 0 };
}

/**
 * sessionStorage から読んだ値のバリデーション（純関数）。
 *
 * 有効条件: 3 フィールドとも `Number.isFinite`、reloadCount は 0 以上の整数、
 * lastReloadAt / windowStart は 0 以上 now 以下。違反時は null を返し、
 * 呼び出し側はデフォルトに修復＋書き戻し（破損 sessionStorage 値の自己回復）
 * すること。有効時は複製を返す（エイリアス共有しない）。
 */
export function sanitizeReloadState(
  value: unknown,
  now: number,
): ReloadState | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const v = value as Record<string, unknown>;
  const { lastReloadAt, windowStart, reloadCount } = v;
  if (
    typeof lastReloadAt !== "number" ||
    typeof windowStart !== "number" ||
    typeof reloadCount !== "number"
  ) {
    return null;
  }
  if (
    !Number.isFinite(lastReloadAt) ||
    !Number.isFinite(windowStart) ||
    !Number.isFinite(reloadCount)
  ) {
    return null;
  }
  if (!Number.isInteger(reloadCount) || reloadCount < 0) {
    return null;
  }
  if (lastReloadAt < 0 || windowStart < 0) {
    return null;
  }
  if (lastReloadAt > now || windowStart > now) {
    return null;
  }
  return { lastReloadAt, windowStart, reloadCount };
}
