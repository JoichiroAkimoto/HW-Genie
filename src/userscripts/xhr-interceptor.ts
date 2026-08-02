// XHR インターセプタ本体。index.ts（userscript 本体）から import され、
// tests/ からも直接 import される（本番コードをそのまま検証するため）。
//
// 設計メモ:
// - setRequestHeader の参照は open() 時点で解決する。未ラップのインスタンス
//   では、他ユーザースクリプト（例: HW Goodwin）が後から
//   prototype.setRequestHeader をラップしても、そのラッパーが連鎖に残る。
//   ただし、既にラップ済みのインスタンスでは own プロパティが優先される
//   ため、その後に追加された prototype ラッパーは影に隠れる（この方式の
//   既知の制約。document-idle では他のスクリプトが先にラップ済みのため
//   実質発生しない）。
// - インスタンスごとに Symbol マーカーでラップ済みを追跡し、open 毎に
//   「未ラップならラップ」する。再オープンでラッパーが積み重ならず、
//   初回 open が非 API だった XHR を再オープンで API にした場合も捕捉できる。
// - 非 API への再オープンでは自分自身のラッパーを外す（非 API は捕捉しない
//   契約を維持）。
// - この方式は「他スクリプトが open 毎に再ラップしない」前提に依存する。
//   両者が open 毎に再ラップする方式を取るとチェーンが成長しうるが、
//   無限再帰にはならない。

// モジュールスコープで一度だけ定義される。テストからは export せず、
// ラッパー同一性を関数参照の比較で検証する。
const HW_GENIE_WRAPPED = Symbol("hw-genie-wrapped-setRequestHeader");
// インスタンスが現在 API リクエスト用に open されているかを示すフラグ。
// 非 API への再オープン時にラッパーを外せないケース（他スクリプトが own
// プロパティで上書きした場合など）でも、残存ラッパーが捕捉を発火しない
// ようにする。
const HW_GENIE_ACTIVE = Symbol("hw-genie-active-setRequestHeader");

/** API URL かどうかをホスト+パスで判定する（部分文字列一致にしない）。 */
export function isApiUrl(urlString: string, baseHref: string): boolean {
  try {
    const url = new URL(urlString, baseHref);
    return (
      url.hostname === "heroes-wb.nextersglobal.com" &&
      (url.pathname === "/api" || url.pathname.startsWith("/api/"))
    );
  } catch {
    return false;
  }
}

/**
 * XMLHttpRequest.prototype.open をラップして x-auth-* ヘッダーを捕捉する。
 *
 * @param isApi    URL 文字列が捕捉対象か判定する関数
 * @param capture  x-auth-* ヘッダーを捕捉するコールバック
 */
export function installXhrInterceptor(
  isApi: (urlString: string) => boolean,
  capture: (name: string, value: string) => void,
): void {
  const originalOpen: (
    method: string,
    url: string | URL,
    async?: boolean,
    username?: string | null,
    password?: string | null,
  ) => void = XMLHttpRequest.prototype.open;

  XMLHttpRequest.prototype.open = function (
    method: string,
    url: string | URL,
    async?: boolean,
    username?: string | null,
    password?: string | null,
  ) {
    let urlString: string;
    try {
      urlString = url.toString();
    } catch {
      // Never let capture interfere with the game's own requests.
      return originalOpen.call(this, method, url, async, username, password);
    }

    if (isApi(urlString)) {
      // Wrap setRequestHeader on every open unless this instance is already
      // wrapped. Resolving `this.setRequestHeader` now (not at install time)
      // keeps wrappers installed later by other userscripts (e.g. HW Goodwin)
      // in the chain; the per-instance marker prevents stacking.
      const self = this as XMLHttpRequest & {
        [HW_GENIE_WRAPPED]?: (name: string, value: string) => void;
        [HW_GENIE_ACTIVE]?: boolean;
      };
      self[HW_GENIE_ACTIVE] = true;
      const currentSetRequestHeader = self.setRequestHeader;
      if (currentSetRequestHeader !== self[HW_GENIE_WRAPPED]) {
        const setRequestHeaderRef = currentSetRequestHeader.bind(self);
        const wrapper = function (name: string, value: string): void {
          if (self[HW_GENIE_ACTIVE]) {
            try {
              capture(name, value);
            } catch {
              // capture の例外はゲームのリクエストを壊さないよう握りつぶす。
            }
          }
          // ヘッダー設定は常に元の setRequestHeader へ委譲する。
          setRequestHeaderRef(name, value);
        };
        self[HW_GENIE_WRAPPED] = wrapper;
        self.setRequestHeader = wrapper;
      }
    } else {
      // 非 API への再オープン: 捕捉を無効化し、可能なら自前のラッパーも
      // 外してプロトタイプ解決に戻す。他スクリプトが own プロパティで
      // 上書きした場合はラッパーは残るが、ACTIVE=false により捕捉は
      // 発火しない。次の API open で ACTIVE=true になり捕捉が復帰する。
      const self = this as XMLHttpRequest & {
        [HW_GENIE_WRAPPED]?: (name: string, value: string) => void;
        [HW_GENIE_ACTIVE]?: boolean;
      };
      self[HW_GENIE_ACTIVE] = false;
      if (self[HW_GENIE_WRAPPED] && self.setRequestHeader === self[HW_GENIE_WRAPPED]) {
        delete self[HW_GENIE_WRAPPED];
        // 自前の own プロパティとして張ったラッパーを外し、プロトタイプ解決に戻す。
        delete (self as unknown as { setRequestHeader?: unknown }).setRequestHeader;
      }
    }

    // The native open() applies its own default (async = true) when the
    // argument is undefined; pass it through unchanged so an explicit null
    // (which WebIDL converts to a synchronous request) is not altered.
    return originalOpen.call(this, method, url, async, username, password);
  };
}
