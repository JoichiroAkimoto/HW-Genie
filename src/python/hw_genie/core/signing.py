"""Hero Wars API のリクエスト署名。

ゲームの web クライアントは全 RPC リクエストに署名を付け、サーバーはそれを検証する
（キャプチャ済みのヘッダーを別ボディで再利用すると ``Invalid signature`` で拒否される）。
クライアント側の実装はゲームバンドルの
``com.progrestar.common.util.RequestSignatureHelper``:

    md5(f"{requestId}:{token}:{sessionId}:{body}:{env}")

- ``body`` はクライアントの compact な JSON シリアライズ。署名は **送信するバイト列**
  に対して計算するため、:func:`serialize_body` の出力をそのまま ``data=`` で送ること。
  サーバーが受け取った後に再直列化して比較しているかは未検証で、wire 上の空白差が
  許容されるかは確認していない。``json=payload`` に戻したり ``dumps`` の設定を
  変えたりすると署名対象と送信バイト列がずれ、``Invalid signature`` になる。
- ``env`` は名前に ``X-Env`` を含む（大小文字区別）ヘッダーについて
  ``"<NAME>=<value>"`` を連結したもの。``NAME`` はヘッダー名の 7 文字目以降を
  大文字化した値で、``NAME`` 昇順にソートする。現状のキャプチャ対象は ``x-auth-*``
  のみなので ``env`` 部分は通常空（空でも有効）。``X-Env*`` を送る場合はキーの
  大小文字をゲーム実装に合わせる必要がある（ゲーム実装は ``"X-Env"`` を
  大文字小文字区別で含むかどうかで判定するため、``x-env-*`` は対象外になる）。

ヘッダー名は HTTP 上は大小文字を区別しないが、requests の
``CaseInsensitiveDict`` は登録順の後勝ちでマージするため、同じ名前の大小違いが
両方残っていると「署名対象の値」と「実際に送られる値」がズレる。署名は必ず
送信と同じ辞書・同じ値から計算し、重複キーは :func:`set_header` で排除する。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

SIGNATURE_HEADER = "x-auth-signature"

#: ゲーム本体が送っている Content-Type。
JSON_CONTENT_TYPE = "application/json; charset=UTF-8"

#: 署名が受理されなかったときにサーバーが返す本文の目印。
#: ステータスコードは 401 / 403 / 200 + text/plain など一定しないため、
#: 本文の部分一致（大小文字区別）で判定する。
INVALID_SIGNATURE_MARKER = "Invalid signature"


def is_invalid_signature_response(text: object) -> bool:
    """レスポンス本文が署名不正を示すか判定する。

    ``None`` や非 ``str`` でも例外を投げず ``False`` を返す。判定は
    サーバーの返す文言に合わせた **大小文字区別の部分一致**。
    """
    return isinstance(text, str) and INVALID_SIGNATURE_MARKER in text


def serialize_body(payload: Mapping[str, Any]) -> str:
    """クライアントと同じ形式（compact・UTF-8 そのまま）で body を直列化する。

    署名は送信バイト列に紐づくため、エスケープを増やす ``ensure_ascii=True``
    （既定）は使わない。``allow_nan=False`` で ``NaN`` / ``Infinity`` を弾くのは、
    それらが厳密な JSON ではないうえ JS 側の表現と一致しないため。

    注意: float は JS の数値表現とズレる場合がある（例: 1.0 の出力や丸め）。
    署名対象の body を作る際は原則 int / str / bool / None を使い、
    float が必要な場合だけ検証すること。
    """
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def set_header(headers: dict[str, str], name: str, value: str) -> None:
    """``headers`` の ``name`` を（大小文字を無視して）1 つだけにする。

    大文字小文字違いの既存キーをすべて削除してから ``headers[name] = value`` を
    設定する。requests の ``CaseInsensitiveDict`` は「後から入れた値」でマージ
    するため、重複を残すと送信値と署名対象がズレる。
    """
    target = name.lower()
    for key in [key for key in headers if key.lower() == target]:
        del headers[key]
    headers[name] = value


def ensure_json_content_type(headers: dict[str, str]) -> None:
    """Content-Type が（大小文字を問わず）無ければ JSON 用の値を設定する。"""
    if any(key.lower() == "content-type" for key in headers):
        return
    headers["Content-Type"] = JSON_CONTENT_TYPE


def get_header(headers: Mapping[str, str], name: str) -> str:
    """ヘッダー値を大小文字を無視して引く（無ければ空文字）。

    HTTP ヘッダー名は大小文字を区別しないため、重複キーがある場合は
    requests のマージ意味論（後勝ち＝最後に一致した値）に合わせて最後の
    一致を返す。保存済みヘッダーの `X-Request-Id` / `x-request-id` のような
    大小違いを呼び出し側で吸収するために公開している。

    値は常に ``str`` で返す。``--update '{"x-auth-token": 123}'`` のように
    非文字列が保存されていても ``":".join`` が ``TypeError`` にならないよう、
    ``None`` は空文字、それ以外は ``str(value)`` に変換する。
    """
    target = name.lower()
    found = ""
    for key, value in headers.items():
        if key.lower() == target:
            found = "" if value is None else str(value)
    return found


def _env_signature_part(headers: Mapping[str, str]) -> str:
    """``X-Env*`` ヘッダーから ``env`` 部分（``NAME=value`` の連結）を作る。"""
    # ゲーム実装はヘッダー名に ``"X-Env"`` を **大小文字区別の部分一致**
    # （JS の ``indexOf``）で含むかどうかで判定しているため、ここも意図的に
    # ``startswith`` ではなく ``in`` で判定する。``startswith`` にすると
    # ``X-Env`` が先頭以外に現れる名前を取りこぼし、ゲームと結果がズレる。
    pairs = [
        (key[6:].upper(), value or "")
        for key, value in headers.items()
        if "X-Env" in key
    ]
    pairs.sort(key=lambda item: item[0])
    return "".join(f"{name}={value}" for name, value in pairs)


def compute_signature(headers: Mapping[str, str], body: str) -> str:
    """``headers`` と ``body`` に対応する ``x-auth-signature`` の値を返す。"""
    source = ":".join(
        (
            get_header(headers, "x-request-id"),
            get_header(headers, "x-auth-token"),
            get_header(headers, "x-auth-session-id"),
            body,
            _env_signature_part(headers),
        )
    )
    return hashlib.md5(source.encode("utf-8")).hexdigest()


def sign_headers(headers: dict[str, str], body: str) -> None:
    """``headers`` に ``body`` 用の ``x-auth-signature`` を設定する。"""
    set_header(headers, SIGNATURE_HEADER, compute_signature(headers, body))
