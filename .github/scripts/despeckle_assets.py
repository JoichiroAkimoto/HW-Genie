#!/usr/bin/env python3
"""ブランド資産ピクセルアートから生成ノイズを吸収するスクリプト。

画像生成ツールはピクセルアートの上に 1-4px の別色の点をばらまく。形自体は
壊れていないので復元は決定的に行える:

1. 量子化先のパレット指定（どちらか一方だけ）
   * `--palette-from REF.png` … 参照 PNG の実パレット（PLTE 順、無ければ走査順）
   * `--palette "#RRGGBB,#RRGGBB,..."` … 色列表を直接指定（来歴をリポジトリ内の
     値だけで書きたいとき用。参照 PNG が成果物そのものになる循環を避けられる）
2. `min_area` 未満の 4 接続同色成分を、外周 4 近傍（上下左右）で最多の色へ吸収
   （4 近傍に限定するのは「1 周の塗り替えが新しい微小島を作らない」ための条件。
   8 近傍で数えると斜め接続だけの吸収先を選べてしまい、塗替え画素が吸収先成分に
   合体しないまま新しい min_area 未満の島になる）

再生成のたびに同じノイズが乗りうるため `--check` ゲートで検品し、コミット済み
資産がノイズフリーであることを機械的に確認できるようにする。PNG の読み書きは
標準ライブラリだけで実装する（本リポジトリの依存に Pillow を追加しない）。

Usage:
  python3 despeckle_assets.py [--min-area N] [--palette-from REF.png | --palette HEX,...] file ...
  python3 despeckle_assets.py [--min-area N] --palette "#03104D,#F1B11D" file ...
  python3 despeckle_assets.py --check [--min-area N] file ...

終了コード: 0 = 正常 / 1 = ゲート失敗 / 2 = 使い方・IO エラー（壊れた PNG / ICO も 2。
`--palette` の書式不正・重複・件数範囲外、`--palette` と `--palette-from` の同時指定も 2）
"""

import argparse
import re
import struct
import sys
import zlib
from array import array
from pathlib import Path
from typing import NamedTuple

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICO_SIGNATURE = b"\x00\x00\x01\x00"
DEFAULT_MIN_AREA = 128
DEFAULT_MAX_PASSES = 64
ABS_SIGNED = tuple(value if value < 128 else 256 - value for value in range(256))
PALETTE_HEX = re.compile(r"#([0-9A-Fa-f]{6})")
MAX_PALETTE_ENTRIES = 256


class PngImage(NamedTuple):
    """デコード済み画像。pixels は行優先の平坦な (r, g, b, a) リスト、palette は
    インデックス PNG の PLTE 順パレット（非インデックスなら None）。透過はすべて
    pixels の alpha に書き下ろす（インデックス PNG の tRNS、ダイレクトタイプの
    tRNS カラーキー、ICO の AND マスクいずれも反映済み）。
    """

    width: int
    height: int
    pixels: list
    palette: tuple | None


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw: bytes, height: int, stride: int, bpp: int) -> list:
    """5 種のスキャンラインフィルタ（None/Sub/Up/Average/Paeth）を逆適用する。"""
    rows = []
    prev = bytes(stride)
    pos = 0
    for _ in range(height):
        if pos + 1 + stride > len(raw):
            raise ValueError("IDAT がスキャンライン数と一致しません")
        ftype = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        pos += 1 + stride
        if ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                if i < bpp:
                    line[i] = (line[i] + prev[i]) & 0xFF
                else:
                    line[i] = (line[i] + _paeth(line[i - bpp], prev[i], prev[i - bpp])) & 0xFF
        elif ftype != 0:
            raise ValueError(f"不明なフィルタ種別 {ftype}")
        rows.append(bytes(line))
        prev = line
    if pos != len(raw):
        raise ValueError("IDAT がスキャンライン数と一致しません")
    return rows


def _indexed_pixels(rows: list, width: int, depth: int, colours: list) -> list:
    """ビット詰めされたインデックス行を (r, g, b, a) リストへ展開する。"""
    per_byte = 8 // depth
    mask = (1 << depth) - 1
    table = [[(byte >> (depth * (per_byte - 1 - i))) & mask for i in range(per_byte)] for byte in range(256)]
    limit = len(colours)
    pixels: list = []
    for row in rows:
        indices = []
        for byte in row:
            indices.extend(table[byte])
        # 行末尾のパディングビットは width 分だけを切り出すことで自然に落ちる
        indices = indices[:width]
        largest = max(indices, default=0)
        if largest >= limit:
            raise ValueError(f"パレットインデックスが範囲外です（{limit} 色に対し最大 {largest}）")
        pixels.extend(colours[index] for index in indices)
    return pixels


def _transparent_colour(body: bytes, color_type: int, depth: int) -> tuple:
    """ダイレクトタイプの `tRNS` カラーキーを 8bit サンプル値の組にする。

    グレースケール（color type 0）は 2 バイト、RGB（color type 2）は 6 バイトで、各
    チャンネルが big-endian 16bit。PNG 規格と libpng は bit depth 16 未満ではサンプル値を
    **下位バイトに右詰め**して書く（ImageMagick の `-transparent red` は `00 ff 00 00 00 00`）。
    だから bit depth 8 では値をそのまま比較し、255 を超える値は libpng が
    「out-of-range samples for bit_depth」と警告して無視する入力＝ここでは `ValueError`
    （黙って不透明扱いにするのが今回の不具合そのものなので、大きく落とす）。
    """
    expected = 2 if color_type == 0 else 6
    if len(body) != expected:
        raise ValueError(f"tRNS が color type {color_type} に対して {len(body)} バイトです（{expected} バイト必要）")
    if depth != 8:
        raise ValueError(f"カラーキーは bit depth 8 のみ対応（現状 {depth}）")
    values = struct.unpack(">" + "H" * (expected // 2), body)
    if any(value > 255 for value in values):
        raise ValueError(f"tRNS のサンプル値が bit depth 8 の範囲を超えています（{values}。libpng なら警告して無視する形です）")
    return values


def decode_png(data: bytes) -> PngImage:
    """PNG バイト列をデコードする（color type 0 / 2 / 3 / 6、非インターレスの 8bit 系）。

    ダイレクトタイプ（0 / 2）の `tRNS` はカラーキーとして実際に効かせる（一致画素は
    alpha 0）。カラーキーの長さが規格と違う場合、および alpha 付き（color type 6）に
    `tRNS` が付いている場合は黙って無視せず `ValueError` にする。
    """
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("PNG シグネチャがありません")
    width = height = 0
    depth = color_type = 0
    palette: list | None = None
    alphas: list | None = None
    colour_key: bytes | None = None
    idat = bytearray()
    off = len(PNG_SIGNATURE)
    while off + 12 <= len(data):
        length = struct.unpack(">I", data[off : off + 4])[0]
        tag = data[off + 4 : off + 8].decode("latin-1")
        if off + 12 + length > len(data):
            raise ValueError(f"{tag} がファイル末尾からはみ出しています")
        body = data[off + 8 : off + 8 + length]
        stored_crc = struct.unpack(">I", data[off + 8 + length : off + 12 + length])[0]
        if stored_crc != zlib.crc32(data[off + 4 : off + 8 + length]) & 0xFFFFFFFF:
            raise ValueError(f"{tag} の CRC が一致しません")
        if tag == "IHDR":
            if len(body) != 13:
                raise ValueError(f"IHDR の長さが 13 バイトではありません（{len(body)}）")
            width, height, depth, color_type, compression, method, interlace = struct.unpack(">IIBBBBB", body)
            if interlace:
                raise ValueError("インターレス PNG には対応していません")
            if compression or method:
                raise ValueError("圧縮・フィルタ方式が非標準です")
        elif tag == "PLTE":
            if len(body) % 3:
                raise ValueError(f"PLTE の長さが 3 の倍数ではありません（{len(body)}）")
            palette = [tuple(body[i : i + 3]) for i in range(0, len(body), 3)]
        elif tag == "tRNS":
            if color_type == 3:
                alphas = list(body)
            elif color_type == 6:
                raise ValueError("color type 6（alpha 付き）に tRNS は付けられません")
            else:
                colour_key = body
        elif tag == "IDAT":
            idat += body
        off += 12 + length
    if not width or not height:
        raise ValueError("IHDR がありません")
    raw = zlib.decompress(bytes(idat))

    if color_type == 3:
        if not palette:
            raise ValueError("カラータイプ 3 に PLTE がありません")
        if depth not in (1, 2, 4, 8):
            raise ValueError(f"インデックス PNG は bit depth 1/2/4/8 のみ対応（現状 {depth}）")
        trns = alphas or []
        colours = [(r, g, b, trns[i] if i < len(trns) else 255) for i, (r, g, b) in enumerate(palette)]
        rows = _unfilter(raw, height, (width * depth + 7) // 8, 1)
        pixels = _indexed_pixels(rows, width, depth, colours)
        return PngImage(width, height, pixels, tuple(colours))
    if color_type == 0:
        if depth != 8:
            raise ValueError(f"グレースケール PNG は bit depth 8 のみ対応（現状 {depth}）")
        pixels = []
        for row in _unfilter(raw, height, width, 1):
            pixels.extend((grey, grey, grey, 255) for grey in row)
    elif color_type == 2:
        if depth != 8:
            raise ValueError(f"RGB PNG は bit depth 8 のみ対応（現状 {depth}）")
        pixels = []
        for row in _unfilter(raw, height, width * 3, 3):
            pixels.extend((r, g, b, 255) for r, g, b in zip(row[0::3], row[1::3], row[2::3], strict=True))
    elif color_type == 6:
        if depth != 8:
            raise ValueError(f"RGBA PNG は bit depth 8 のみ対応（現状 {depth}）")
        pixels = []
        for row in _unfilter(raw, height, width * 4, 4):
            pixels.extend(zip(row[0::4], row[1::4], row[2::4], row[3::4], strict=True))
    else:
        raise ValueError(f"カラータイプ {color_type} は未対応です")
    if colour_key is not None:
        key = _transparent_colour(colour_key, color_type, depth)
        match = (key[0], key[0], key[0]) if len(key) == 1 else key
        pixels = [(r, g, b, 0 if (r, g, b) == match else a) for r, g, b, a in pixels]
    return PngImage(width, height, pixels, None)


def read_png(path) -> PngImage:
    return decode_png(Path(path).read_bytes())


def _chunk(tag: str, body: bytes) -> bytes:
    head = tag.encode("latin-1")
    crc = zlib.crc32(head + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + head + body + struct.pack(">I", crc)


def _filtered_candidates(row: bytes, prev: bytes, bpp: int) -> list:
    """1 行に 5 フィルタを当てはめた結果を返す（各先頭の 1 バイトはフィルタ種別）。"""
    pad = bytes(bpp)
    left_row = pad + row[:-bpp]
    above_left = pad + prev[:-bpp]
    return [
        b"\x00" + row,
        b"\x01" + row[:bpp] + bytes((a - b) & 0xFF for a, b in zip(row[bpp:], row[:-bpp])),
        b"\x02" + bytes((a - b) & 0xFF for a, b in zip(row, prev)),
        b"\x03" + bytes((a - ((left + up) >> 1)) & 0xFF for a, left, up in zip(row, left_row, prev)),
        b"\x04" + bytes((a - _paeth(left, up, cross)) & 0xFF for a, left, up, cross in zip(row, left_row, prev, above_left)),
    ]


def _scanlines(rows: list, bpp: int) -> bytes:
    """行ごとに絶対値和が最小のフィルタを選ぶ（PNG 標準の启发式）。"""
    out = bytearray()
    prev = bytes(len(rows[0])) if rows else b""
    for row in rows:
        best = min(_filtered_candidates(row, prev, bpp), key=lambda line: sum(ABS_SIGNED[b] for b in line[1:]))
        out += best
        prev = row
    return bytes(out)


def encode_png(width: int, height: int, pixels: list) -> bytes:
    """メタデータ無しの非インターレス PNG を作る。色数 256 以下ならインデックス化。"""
    if len(pixels) != width * height:
        raise ValueError(f"画素数 {len(pixels)} が {width}x{height} と一致しません")
    distinct = list(dict.fromkeys(pixels))
    opaque = all(c[3] == 255 for c in distinct)
    if len(distinct) <= 256:
        index_of = {colour: index for index, colour in enumerate(distinct)}
        depth = 1 if len(distinct) <= 2 else 2 if len(distinct) <= 4 else 4 if len(distinct) <= 16 else 8
        per_byte = 8 // depth
        rows = []
        for start in range(0, len(pixels), width):
            indices = [index_of[colour] for colour in pixels[start : start + width]]
            packed = bytearray()
            for row_start in range(0, width, per_byte):
                byte = 0
                for shift, index in enumerate(indices[row_start : row_start + per_byte]):
                    byte |= index << (depth * (per_byte - 1 - shift))
                packed.append(byte)
            rows.append(bytes(packed))
        chunks = [
            _chunk("IHDR", struct.pack(">IIBBBBB", width, height, depth, 3, 0, 0, 0)),
            _chunk("PLTE", b"".join(bytes(colour[:3]) for colour in distinct)),
        ]
        if not opaque:
            chunks.append(_chunk("tRNS", bytes(colour[3] for colour in distinct)))
        chunks.append(_chunk("IDAT", zlib.compress(_scanlines(rows, 1), 9)))
        chunks.append(_chunk("IEND", b""))
        return PNG_SIGNATURE + b"".join(chunks)

    if opaque:
        rows = [bytes(value for colour in pixels[start : start + width] for value in colour[:3]) for start in range(0, len(pixels), width)]
        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        bpp = 3
    else:
        rows = [bytes(value for colour in pixels[start : start + width] for value in colour) for start in range(0, len(pixels), width)]
        ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
        bpp = 4
    body = zlib.compress(_scanlines(rows, bpp), 9)
    return PNG_SIGNATURE + b"".join([_chunk("IHDR", ihdr), _chunk("IDAT", body), _chunk("IEND", b"")])


def write_png(path, width: int, height: int, pixels: list) -> None:
    Path(path).write_bytes(encode_png(width, height, pixels))


def colour_table(pixels: list) -> tuple:
    """走査順で最初に現れた順に重複を除いた色一覧（色 ID の順序定義になる）。"""
    return tuple(dict.fromkeys(pixels))


def quantize(pixels: list, palette: list) -> list:
    """RGB 二乗距離の最近傍パレット色へ写す。同距離はパレットで先に来る色を選ぶ。"""
    cache: dict = {}
    out = []
    for pixel in pixels:
        rgb = pixel[:3]
        hit = cache.get(rgb)
        if hit is None:
            hit = palette[0]
            best_distance = None
            for entry in palette:
                distance = sum((a - b) ** 2 for a, b in zip(rgb, entry[:3], strict=True))
                if best_distance is None or distance < best_distance:
                    hit, best_distance = entry, distance
            cache[rgb] = hit
        out.append(hit)
    return out


def _to_ids(pixels: list) -> array:
    index_of = {colour: index for index, colour in enumerate(colour_table(pixels))}
    return array("i", (index_of[colour] for colour in pixels))


def _components(ids: array, width: int, height: int):
    """4 接続同色成分を (色 ID, 画素番号リスト) の形で走査順に返す。"""
    total = width * height
    visited = bytearray(total)
    for start in range(total):
        if visited[start]:
            continue
        colour_id = ids[start]
        visited[start] = 1
        stack = [start]
        component = [start]
        while stack:
            i = stack.pop()
            y, x = divmod(i, width)
            if y > 0:
                j = i - width
                if not visited[j] and ids[j] == colour_id:
                    visited[j] = 1
                    stack.append(j)
                    component.append(j)
            if y + 1 < height:
                j = i + width
                if not visited[j] and ids[j] == colour_id:
                    visited[j] = 1
                    stack.append(j)
                    component.append(j)
            if x > 0:
                j = i - 1
                if not visited[j] and ids[j] == colour_id:
                    visited[j] = 1
                    stack.append(j)
                    component.append(j)
            if x + 1 < width:
                j = i + 1
                if not visited[j] and ids[j] == colour_id:
                    visited[j] = 1
                    stack.append(j)
                    component.append(j)
        yield colour_id, component


def _component_sizes(ids: array, width: int, height: int) -> array:
    """各画素が属する 4 接続同色成分の面積（吸収先の候補選定用）。"""
    sizes = array("i", bytes(4 * width * height))
    for _colour_id, component in _components(ids, width, height):
        for i in component:
            sizes[i] = len(component)
    return sizes


def _absorb_target(ids: array, sizes: array, width: int, height: int, colour_id: int, component: list, min_area: int) -> int | None:
    """成分の外周 **4 近傍**（上下左右・成分外）で最多の色 ID を返す。同数は色 ID が小さい順。

    候補は 2 段階で絞る:

    * **4 近傍だけ**を見る。8 近傍（ななめ込み）で数えると、斜めに接しているだけの
      色を吸収先に選べてしまい、塗り替えた画素が吸収先成分に 4 接続で合体しない。
      その結果、処理前には存在しなかった min_area 未満の島が 1 周目に生まれる
      （`tests/test_despeckle_assets.py` のレビュー反例で固定）。
    * 候補から「その画素自身の成分も min_area 未満」の色を落とす。小さな成分の色を
      吸収先にすると、その先が別の吸収で消えるため、塗り替えた画素だけが取り残されて
      1px の残滓を新たに作る（旧実装はこれを 2 周目で片付けていた）。

    この 2 つで「1 周の塗り替えが新しい微小島を生まない」が成立する。塗替え画素は
    必ず min_area 以上の成分に 4 接続で合体し、その成分自体は min_area 以上なので
    今回のパスで塗り替えられない。
    """
    counts: dict = {}
    for i in component:
        y, x = divmod(i, width)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            ny, nx = y + dy, x + dx
            if not 0 <= ny < height or not 0 <= nx < width:
                continue
            neighbour = ny * width + nx
            candidate = ids[neighbour]
            if candidate == colour_id or sizes[neighbour] < min_area:
                continue
            counts[candidate] = counts.get(candidate, 0) + 1
    if not counts:
        return None
    return min(counts.items(), key=lambda item: (-item[1], item[0]))[0]


def absorb_islands(pixels: list, width: int, height: int, min_area: int) -> tuple:
    """min_area 未満の 4 接続同色成分を外周 4 近傍（上下左右）の最多色へ吸収させる。

    入力は変更せず、(新しい画素リスト, 塗り替えた画素数) を返す。吸収先は成分
    自身の色を候補から除くので、周囲に `min_area` 以上の成分があれば島は周囲の
    色へ書き換えられる。逆に周囲すべてが小さな成分だと候補が尽きて行き先が無く、
    その島はそのまま残る（下の「ただし」＝残り島は `--check` の検品 4 が拾う）。
    成分分解と隣接色判定はすべて元の画像に対して行うため、結果は処理順に依存せず
    決定的になる。

    保証: **1 周の塗り替えは min_area 未満の島を新しく作らない**。吸収先は
    ① 成分の 4 近傍にいる ② その画素自身の成分が `min_area` 以上、という 2 条件で
    絞る（`_absorb_target`）。だから塗替え画素は必ず min_area 以上の成分に 4 接続で
    合体し、その成分は今周の塗り替え対象ではない。①を 8 近傍に緩めると斜め接続だけの
    塗替えが起きてこの保証が壊れ、②を外すと塗替え先そのものが消えて 1px の残滓が
    生まれる（どちらもレビュー反例をテストで固定）。
    ただし周囲すべてが小さな成分からなる島（例: `["ab"]` の a と b）は行き先を失って
    そのまま残る。その場合は変更なしで終わるので `absorb_to_fixed_point` は即収束し、
    残り島の検出は `--check` ゲートの仕事になる。
    """
    colours = colour_table(pixels)
    ids = _to_ids(pixels)
    sizes = _component_sizes(ids, width, height)
    out = list(pixels)
    changed = 0
    for colour_id, component in _components(ids, width, height):
        if len(component) >= min_area:
            continue
        target = _absorb_target(ids, sizes, width, height, colour_id, component, min_area)
        if target is None:
            continue
        new_pixel = colours[target]
        for i in component:
            if out[i] != new_pixel:
                changed += 1
            out[i] = new_pixel
    return out, changed


def smallest_island(pixels: list, width: int, height: int) -> int | None:
    """最も小さい 4 接続同色成分の面積を返す（画像が空なら None）。"""
    ids = _to_ids(pixels)
    smallest = None
    for _colour_id, component in _components(ids, width, height):
        if smallest is None or len(component) < smallest:
            smallest = len(component)
    return smallest


def absorb_to_fixed_point(pixels: list, width: int, height: int, min_area: int, max_passes: int = DEFAULT_MAX_PASSES) -> tuple:
    """`absorb_islands` を変化がなくなるまで繰り返し、(画素リスト, 合計塗り替え数, パス数, 上限打ち切り) を返す。

    1 周の塗り替えは min_area 未満の島を新しく作らない（`absorb_islands` の保証）。
    それでもパスを重ねる意味はある。小さな島が連なっている場合、1 周目はまだ自分も
    小さいという理由で隣を吸収先に選べない。2 周目以降は外側から島が消えていくので、
    内側の島が大きい成分を隣にもつようになり吸収が進む。
    どこにも吸収されえない孤立した島（例: `["ab"]` のように小さな色だけ）は変更なしで
    終わるので無害に打ち切られ、残り島の検出は `--check` ゲートの仕事になる。

    4 番目の要素は「`max_passes` に達した時点でまだ変化が残っていた」かどうかの
    フラグ。`passes=64` だけでは収束したのか打ち切られたのかを運用者が推測することに
    なるので、`process()` がこれを stderr に出す。
    """
    passes = 0
    total_changed = 0
    while passes < max_passes:
        pixels, changed = absorb_islands(pixels, width, height, min_area)
        total_changed += changed
        passes += 1
        if not changed:
            return pixels, total_changed, passes, False
    return pixels, total_changed, passes, True


def palette_of(image: PngImage) -> list:
    """参照画像から量子化先パレット (r, g, b, a) の列を作る。

    PLTE があるときはその並び順、無いときは走査順で最初に現れた順に重複を除く。
    これで最近傍の同距離打ち壊しが決定的になる。tRNS を反映した alpha を載せるので、
    透過入り参照を指定すれば透過色も量子化先候補になる。
    """
    source = image.palette if image.palette else colour_table(image.pixels)
    return list(dict.fromkeys(tuple(entry[:4]) for entry in source))


def parse_palette(text: str) -> list:
    """`--palette` のカンマ区切り 16 進色列表を量子化先 `(r, g, b, a)` の列へ直す。

    受け付けるのは `#RRGGBB` だけ（前後の空白は許容しない: 指定ミスを黙って直さない）。
    1〜256 件・重複不可で、違反は `ValueError`（`main` が exit 2 にする）。
    alpha は常に 255 —— 16 進 6 桁には透明情報を載せられないので、tRNS 付きの参照を
    量子化先したい場合は `--palette-from` を使う（ここは仕様上の制限）。
    """
    if not text.strip():
        raise ValueError("--palette は空以外を指定してください（#RRGGBB, #RRGGBB,... のカンマ区切り）")
    entries = text.split(",")
    if not 1 <= len(entries) <= MAX_PALETTE_ENTRIES:
        raise ValueError(f"--palette は 1〜{MAX_PALETTE_ENTRIES} 色が必要です（現在 {len(entries)} 色）")
    colours: list = []
    seen: dict = {}
    for entry in entries:
        matched = PALETTE_HEX.fullmatch(entry)
        if not matched:
            raise ValueError(f"--palette の '{entry}' は #RRGGBB の 16 進色ではありません")
        rgb = tuple(int(matched.group(1)[i : i + 2], 16) for i in (0, 2, 4))
        if rgb in seen:
            raise ValueError(f"--palette の #{matched.group(1)} が重複しています（先頭 {seen[rgb] + 1} 番目）")
        seen[rgb] = len(colours)
        colours.append((*rgb, 255))
    return colours


def ico_layers(data: bytes) -> list:
    """ICO の BMP レイヤーをデコードする（AND マスクで透明。書き戻しは非対応）。"""
    count = struct.unpack("<H", data[4:6])[0]
    layers = []
    for index in range(count):
        entry = data[6 + 16 * index : 6 + 16 * (index + 1)]
        width = entry[0] or 256
        height = entry[1] or 256
        offset = struct.unpack("<I", entry[12:16])[0]
        header_size, dib_width, dib_height, planes, bpp = struct.unpack("<IiiHH", data[offset : offset + 16])
        compression = struct.unpack("<I", data[offset + 16 : offset + 20])[0]
        if header_size != 40 or compression != 0 or planes != 1:
            raise ValueError(f"ICO レイヤー {index} は BITMAPINFOHEADER + BI_RGB のみ対応")
        if dib_height != height * 2 or dib_width != width:
            raise ValueError(f"ICO レイヤー {index} の幅・高さ指定（XOR + AND マスク）が非標準です")
        if bpp not in (4, 8, 24, 32):
            raise ValueError(f"ICO レイヤー {index} の bit depth {bpp} は未対応")
        body = data[offset + 40 :]
        palette_length = 0 if bpp >= 16 else 1 << bpp
        # パレットの 4 番目（予約バイト）は 0 運用が多いので 255 に直す。
        # 32bpp の方だけは実在の alpha チャンネルなので `or 255` をしてはいけない
        # （alpha 0 の画素が不透明に化ける）。
        palette = [(body[4 * i + 2], body[4 * i + 1], body[4 * i], body[4 * i + 3] or 255) for i in range(palette_length)]
        row_bytes = (width * bpp + 31) // 32 * 4
        mask_row_bytes = (width + 31) // 32 * 4
        pixel_base = palette_length * 4
        mask_base = pixel_base + row_bytes * height
        pixels: list = []
        index_mask = (1 << bpp) - 1
        for y in range(height - 1, -1, -1):
            row = body[pixel_base + y * row_bytes : pixel_base + (y + 1) * row_bytes]
            if bpp == 32:
                pixels.extend((row[i + 2], row[i + 1], row[i], row[i + 3]) for i in range(0, width * 4, 4))
            elif bpp == 24:
                pixels.extend((row[i + 2], row[i + 1], row[i], 255) for i in range(0, width * 3, 3))
            else:
                indices = []
                for byte in row[: (width * bpp + 7) // 8]:
                    indices.extend((byte >> shift) & index_mask for shift in range(8 - bpp, -1, -bpp))
                if max(indices[:width], default=0) >= palette_length:
                    raise ValueError(f"ICO レイヤー {index} のパレットインデックスが範囲外です")
                pixels.extend(palette[i] for i in indices[:width])
        mask = []
        for y in range(height - 1, -1, -1):
            row = body[mask_base + y * mask_row_bytes : mask_base + (y + 1) * mask_row_bytes]
            bits = []
            for byte in row:
                bits.extend((byte >> shift) & 1 for shift in range(7, -1, -1))
            mask.extend(bits[:width])
        if len(mask) != width * height:
            raise ValueError(f"ICO レイヤー {index} の AND マスクが短すぎます")
        # AND マスクの 1 ビット＝背景（ここでは完全透過）として alpha に反映する
        pixels = [(r, g, b, 0 if bit else a) for (r, g, b, a), bit in zip(pixels, mask, strict=True)]
        layers.append(PngImage(width, height, pixels, tuple(palette) or None))
    return layers


def read_image_file(path: Path) -> tuple:
    """PNG / ICO を (PngImage のリスト, 書き戻せるか) で開く（ICO はレイヤー単位）。"""
    data = path.read_bytes()
    if data.startswith(ICO_SIGNATURE):
        return ico_layers(data), False
    if data.startswith(PNG_SIGNATURE):
        return [decode_png(data)], True
    raise ValueError(f"{path.name}: PNG / ICO 以外の形式は開けません")


def process(path: Path, min_area: int, reference: list | None, check: bool) -> int:
    """1 ファイルを処理し、ゲート失敗の個数を返す。"""
    images, writable = read_image_file(path)
    failures = 0
    for layer, image in enumerate(images):
        suffix = f"#{layer}" if len(images) > 1 else ""
        colours = len(colour_table(image.pixels))
        if check:
            smallest = smallest_island(image.pixels, image.width, image.height)
            failed = smallest is not None and smallest < min_area
            print(
                f"{path.name}{suffix} {image.width}x{image.height} colors={colours} "
                f"smallest_island={smallest} min_area={min_area} {'FAIL' if failed else 'ok'}"
            )
            failures += 1 if failed else 0
            continue
        if not writable:
            raise ValueError(f"{path.name}: ICO は --check 専用です（書き戻し非対応）")
        pixels = quantize(image.pixels, reference) if reference is not None else image.pixels
        before = path.stat().st_size
        # `max_passes` を既定引数に任せずここで読むと、印字する上限値と実際に打ち切った
        # 周数が常に同じ値になる（テストでも `DEFAULT_MAX_PASSES` 1 本で挙動を止められる）。
        cleaned, changed, passes, hit_limit = absorb_to_fixed_point(pixels, image.width, image.height, min_area, max_passes=DEFAULT_MAX_PASSES)
        write_png(path, image.width, image.height, cleaned)
        print(
            f"{path.name}{suffix} {image.width}x{image.height} bytes={before}->{path.stat().st_size} "
            f"colors={colours}->{len(colour_table(cleaned))} recoloured={changed} passes={passes}"
        )
        if hit_limit:
            print(
                f"WARNING: {path.name}{suffix} は max_passes={DEFAULT_MAX_PASSES} で打ち切られました"
                f"（passes={passes} の時点でまだ吸収が進行中）。固定点まで流し直すか、"
                "残り島は --check ゲートで検品してください",
                file=sys.stderr,
            )
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ピクセルアート資産の微小な色島を吸収する")
    parser.add_argument("--min-area", type=int, default=DEFAULT_MIN_AREA, help="この面積未満の同色成分を吸収する")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--palette-from", type=Path, default=None, metavar="REF.png", help="量子化先の参照 PNG（実測パレットを使う）")
    group.add_argument("--palette", default=None, metavar='"#RRGGBB,#RRGGBB,..."', help="量子化先の色列表を直接指定（1〜256 色・重複不可）")
    parser.add_argument("--check", action="store_true", help="変更せず、閾値未満の島があれば exit 1 で検品する")
    parser.add_argument("files", nargs="+", type=Path, help="対象の PNG（ICO は --check のみ）")
    args = parser.parse_args(argv)
    if args.min_area < 1:
        print("ERROR: --min-area は 1 以上を指定してください", file=sys.stderr)
        return 2
    try:
        if args.palette is not None:
            reference = parse_palette(args.palette)
        elif args.palette_from is not None:
            reference = palette_of(read_png(args.palette_from))
        else:
            reference = None
        failures = 0
        for path in args.files:
            failures += process(path, args.min_area, reference, args.check)
    except (OSError, ValueError, zlib.error, struct.error, IndexError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
