"""Hero Wars API のリクエスト署名。

ゲームの web クライアントは全 RPC リクエストに署名を付け、サーバーはそれを検証する
（キャプチャ済みのヘッダーを別ボディで再利用すると ``Invalid signature`` で拒否される）。
クライアント側の実装はゲームバンドルの
``com.progrestar.common.util.RequestSignatureHelper``:

    md5(f"{requestId}:{token}:{sessionId}:{body}:{env}")

- ``body`` はクライアントの compact な JSON シリアライズ（サーバー側で正規化されるため、
  wire 上の空白の差は問題にならない）。
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


def _header(headers: Mapping[str, str], name: str) -> str:
    """ヘッダー値を大小文字を無視して引く。

    HTTP ヘッダー名は大小文字を区別しないため、重複キーがある場合は
    requests のマージ意味論（後勝ち＝最後に一致した値）に合わせて最後の
    一致を返す。
    """
    target = name.lower()
    found = ""
    for key, value in headers.items():
        if key.lower() == target:
            found = value or ""
    return found


def _env_signature_part(headers: Mapping[str, str]) -> str:
    """``X-Env*`` ヘッダーから ``env`` 部分（``NAME=value`` の連結）を作る。"""
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
            _header(headers, "x-request-id"),
            _header(headers, "x-auth-token"),
            _header(headers, "x-auth-session-id"),
            body,
            _env_signature_part(headers),
        )
    )
    return hashlib.md5(source.encode("utf-8")).hexdigest()


def sign_headers(headers: dict[str, str], body: str) -> None:
    """``headers`` に ``body`` 用の ``x-auth-signature`` を設定する。"""
    set_header(headers, SIGNATURE_HEADER, compute_signature(headers, body))
