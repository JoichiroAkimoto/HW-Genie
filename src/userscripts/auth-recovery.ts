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
// ランタイム kill-switch 用 localStorage キー。値が "1" なら検知しても
// リロードせずログのみにする（無効化手順は README の既知制限を参照）。
export const DISABLE_KEY = "hw-genie-auto-reload-disabled";
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
 * ランタイム kill-switch の判定（純関数）。
 *
 * localStorage から読んだ `hw-genie-auto-reload-disabled` の値を渡す。
 * 値がちょうど "1" のときのみ無効 (true)。キーが存在しない場合 (null) や
 * その他の値は有効扱い (false)。localStorage の読み取り自体が失敗した
 * 場合（例外）は、呼び出し側（createApiResponseHandler）が "1" 相当に
 * 読み替えて suppress する（fail-closed: 読めない＝止める）。
 */
export function isAutoReloadDisabled(storageValue: string | null): boolean {
  return storageValue === "1";
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

/** createApiResponseHandler への依存注入（storage・reloader・now）。 */
export interface ApiResponseHandlerDeps {
  /** localStorage の DISABLE_KEY 読み。失敗時は例外を投げてよい。 */
  readDisabledValue: () => string | null;
  /** sessionStorage の STORAGE_KEY 生文字読み。失敗時は例外を投げてよい。 */
  readStorage: () => string | null;
  /** sessionStorage への生文字書き込み。失敗時は例外を投げてよい。 */
  writeStorage: (raw: string) => void;
  /** リロード実行（例: () => location.reload()）。 */
  reload: () => void;
  /** 現在時刻。省略時は Date.now。 */
  now?: () => number;
  /** ログ出力。省略時は無出力。 */
  log?: (message: string) => void;
}

/**
 * XHR レスポンス受信時の配線済みハンドラを生成する。index.ts は薄い配線
 * （実 storage・location.reload・Date.now の注入）だけ残し、判定ロジックは
 * こちらに寄せる。テストから直接検証できる。
 *
 * 振る舞い（固定）:
 * - 非失効レスポンス → 何もしない。
 * - kill-switch 有効 ("1") 時はカウンタを消費しない（読み書きなし）。
 * - localStorage 読み取り失敗時 → "1" 相当に読み替えて suppress
 *  （fail-closed: 読めない＝止める）。
 * - sessionStorage 破損値 → デフォルトに修復書き戻し（自己回復）した上で
 *   判定に進む。
 * - sessionStorage 読み書き不可時（例外）→ 初回から suppress して
 *   リロードしない（メモリフォールバックによるリロードはしない）。
 * - クールダウン内・上限超過時 → suppress（書き込みなし）。
 * - 例外を投げない。
 */
export function createApiResponseHandler(
  deps: ApiResponseHandlerDeps,
): (status: number, bodyText: string) => void {
  const nowFn = deps.now ?? Date.now;
  const logFn = deps.log ?? (() => {});
  return (status: number, bodyText: string): void => {
    try {
      if (!isSessionExpired(status, bodyText)) {
        return;
      }
      let disabledValue: string | null;
      try {
        disabledValue = deps.readDisabledValue();
      } catch {
        // 読めない＝止める（fail-closed）。
        disabledValue = "1";
      }
      if (isAutoReloadDisabled(disabledValue)) {
        logFn(
          "Session expired detected but auto-reload disabled by kill-switch.",
        );
        return;
      }
      const now = nowFn();
      let raw: string | null;
      try {
        raw = deps.readStorage();
      } catch {
        logFn(
          "Session expired detected but auto-reload suppressed (sessionStorage unavailable).",
        );
        return;
      }
      let current: ReloadState;
      if (raw === null) {
        current = defaultReloadState();
      } else {
        let parsed: unknown;
        try {
          parsed = JSON.parse(raw);
        } catch {
          parsed = undefined;
        }
        const sane = sanitizeReloadState(parsed, now);
        if (sane !== null) {
          current = sane;
        } else {
          // 破損値はデフォルトに修復＋書き戻し（自己回復）。
          current = defaultReloadState();
          try {
            deps.writeStorage(JSON.stringify(current));
          } catch {
            logFn(
              "Session expired detected but auto-reload suppressed (sessionStorage unavailable).",
            );
            return;
          }
        }
      }
      const decision = evaluateReload(current, now);
      if (decision.action !== "reload") {
        logFn(
          "Session expired detected but auto-reload suppressed (cooldown/limit).",
        );
        return;
      }
      try {
        deps.writeStorage(JSON.stringify(decision.state));
      } catch {
        // カウンタを永続化できないならリロードしない。
        logFn(
          "Session expired detected but auto-reload suppressed (sessionStorage unavailable).",
        );
        return;
      }
      logFn("Session expired detected. Reloading page to recover session...");
      try {
        deps.reload();
      } catch {
        // リロード自体の失敗は握りつぶす（ゲームのリクエストを壊さない）。
      }
    } catch {
      // 判定ロジックは例外を投げない契約。念のため最終防御。
    }
  };
}
