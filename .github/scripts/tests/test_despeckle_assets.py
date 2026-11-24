"""despeckle_assets.py（PNG 読み書き・微小色島吸収）のテスト。"""

import os
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from despeckle_assets import (  # noqa: E402
    DEFAULT_MAX_PASSES,
    MAX_PALETTE_ENTRIES,
    PngImage,
    _components,
    _to_ids,
    absorb_islands,
    ico_layers,
    absorb_to_fixed_point,
    colour_table,
    decode_png,
    encode_png,
    main,
    palette_of,
    parse_palette,
    quantize,
    read_png,
    smallest_island,
    write_png,
)

BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)
RED = (255, 0, 0, 255)
CYAN = (0, 255, 255, 255)
HOLE = (10, 20, 30, 0)
HALF = (40, 50, 60, 128)
# レビュー反例（斜め接続だけの吸収先）を 3 色で書くための字
GREY = (10, 10, 10, 255)
MAROON = (200, 0, 0, 255)
METADATA_CHUNKS = ("tEXt", "zTXt", "iTXt", "tIME")
CHAR_COLOURS = {"A": BLACK, "B": WHITE, "C": CYAN, "x": RED, "y": GREY, "z": MAROON}


def grid(rows):
    """文字図から write_png 用の (width, height, pixels) を作る。"""
    return len(rows[0]), len(rows), [CHAR_COLOURS[ch] for row in rows for ch in row]


def island_sets(pixels, width, height, min_area):
    """min_area 未満の 4 接続同色成分を（画素番号の frozenset）集合で返す。

    「1 周で新しい微小島が生まれない」を判定する補助。中身が前の島の部分集合ではなく
    **同じ画素集合そのもの**なら既存の島（行き先を失って残った島）、そうでなければ
    新しく作られた島＝保証の違反として扱う。
    """
    ids = _to_ids(pixels)
    return {frozenset(component) for _cid, component in _components(ids, width, height) if len(component) < min_area}


def chunk_tags(data):
    """チャンク名を struct で順にwalkして返す。"""
    tags = []
    off = 8
    while off + 12 <= len(data):
        length = struct.unpack(">I", data[off : off + 4])[0]
        tags.append(data[off + 4 : off + 8].decode("latin-1"))
        off += 12 + length
    return tags


def chunk(tag, body):
    crc = zlib.crc32(tag + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)


def build_png(color_type, depth, width, height, scanlines, palette=b"", trns=b"", interlace=0):
    """スキャンライン（フィルタバイト込み）をそのまま IDAT に載せる PNG を作る。"""
    ihdr = struct.pack(">IIBBBBB", width, height, depth, color_type, 0, 0, interlace)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
    if palette:
        data += chunk(b"PLTE", palette)
    if trns:
        data += chunk(b"tRNS", trns)
    data += chunk(b"IDAT", zlib.compress(scanlines))
    return data + chunk(b"IEND", b"")


def paeth_predictor(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def test_absorb_removes_single_intruder_in_field():
    width, height, pixels = grid(["AAAAA", "AAAAA", "AAxAA", "AAAAA", "AAAAA"])
    cleaned, changed = absorb_islands(pixels, width, height, 4)
    assert changed == 1
    assert cleaned[12] == BLACK
    assert RED not in cleaned
    assert cleaned.count(BLACK) == width * height


def test_absorb_leaves_component_of_exactly_min_area():
    width, height, pixels = grid(["AAAAAA", "AABBAA", "AABBAA", "AAAAAA"])
    cleaned, changed = absorb_islands(pixels, width, height, 4)
    assert changed == 0
    assert cleaned == pixels
    # 閾値を 1 つ上げると同じ成分が吸収される
    cleaned, changed = absorb_islands(pixels, width, height, 5)
    assert changed == 4


def test_absorb_does_not_mutate_input():
    width, height, pixels = grid(["AAAAA", "AAAAA", "AAxAA", "AAAAA"])
    original = list(pixels)
    cleaned, changed = absorb_islands(pixels, width, height, 4)
    assert pixels == original
    assert changed == 1
    assert cleaned is not pixels


def test_absorb_chooses_majority_colour_of_the_four_neighbour_ring():
    """吸収先は成分の外周 **4 近傍**（上下左右）の最多色。

    x の 4 近傍は 上=B・下=B・左=A・右=B → B 3 対 A 1。8 近傍で数るとななめ側の
    A ばかりが増えて決まらない（あるいは斜め接続の色を選んで合体しない）ので、
    4 近傍限定をこの字図で固定する。
    """
    width, height, pixels = grid(["AABBB", "AABBB", "AABxB", "AABBB", "AABBB"])
    cleaned, changed = absorb_islands(pixels, width, height, 4)
    assert changed == 1
    assert cleaned[12] == WHITE
    assert RED not in cleaned


def test_absorb_breaks_border_ties_by_first_colour_order():
    width, height, pixels = grid(["AAB", "AxB", "ABB"])
    cleaned, changed = absorb_islands(pixels, width, height, 3)
    assert changed == 1
    assert cleaned[4] == BLACK
    assert absorb_islands(pixels, width, height, 3)[0] == cleaned


def test_absorb_never_repaints_into_an_island_that_is_itself_too_small():
    """吸収先成分が自分の吸収で消えるケース（旧実装は成分ラベルを古いまま使っていた）。

    `['...','..a','.a.']`（＝ `AAA/AAx/AxA`）では 2 つの `x` に囲まれた 1 セルの `A`
    がそれ自体 min_area 未満の成分。旧実装はその孤立成分を `x` 色へ塗り替えて、
    `x` 島が消えた後に 1px の残滓として残していた（2 周目でようやく片付く）。
    吸収先を「その画素の成分が min_area 以上」に限定した今は単発の `absorb_islands`
    で消え、1 周目の塗替え数がそのまま固定点での総数になる。
    """
    width, height, pixels = grid(["AAA", "AAx", "AxA"])
    cleaned, changed = absorb_islands(pixels, width, height, 2)
    assert changed == 2
    assert RED not in cleaned
    assert smallest_island(cleaned, width, height) == width * height
    # 固定点版も塗替えは 1 周だけ（2 周目は変化なしで打ち切り）
    fixed, total, passes, hit_limit = absorb_to_fixed_point(pixels, width, height, 2)
    assert fixed == cleaned and total == 2 and passes == 2 and not hit_limit


def test_absorb_one_pass_creates_no_new_island_when_targets_are_four_connected():
    """レビュー反例: 8 近傍で吸収先を選んだ場合の「1 周目で生まれた新島」の回帰テスト。

    5x5・min_area=4・3 色（C0=黒 / C1=灰 / C2=赤）の字図では、8 近傍版だと
    斜めに接しているだけの C1 を吸収先として選ぶ画素（左上付近の C0 島 2 つと
    右下の C0 島）が出て、塗替え後に **処理前へは存在しなかった C1 の 3px 島**
    （画素 1・5・6）が現れる。4 近傍限定なら吸収先は必ず 4 接続で合体するため、
    1 周後の min_area 未満島は「塗替え先を持たなかった元の島」の部分集合になる。
    """
    rows = ["20100", "10201", "22101", "00120", "00112"]
    colour_of = {"0": BLACK, "1": GREY, "2": MAROON}
    width = height = 5
    pixels = [colour_of[ch] for row in rows for ch in row]
    before = island_sets(pixels, width, height, 4)
    assert colour_table(pixels) == (MAROON, BLACK, GREY)  # 字図が意図どおり 3 色・この走査順（同数判定の基準）

    cleaned, changed = absorb_islands(pixels, width, height, 4)
    after = island_sets(cleaned, width, height, 4)
    assert changed == 8
    # 新しい島は 1 つも生まれない（残るのは ①C2 の孤立 1px ②C1 の孤立 1px だけ）
    assert after <= before
    assert frozenset({1, 5, 6}) not in after
    assert after == {frozenset({0}), frozenset({5})}

    # 固定点には 3 周で収束し、残島はゼロになる（上限打ち切りではない）
    fixed, total, passes, hit_limit = absorb_to_fixed_point(pixels, width, height, 4)
    assert hit_limit is False and passes == 3 and total == 10
    assert island_sets(fixed, width, height, 4) == set()
    assert smallest_island(fixed, width, height) >= 4


def test_absorb_leaves_islands_that_have_nowhere_to_go_but_terminates():
    """行き先のない微小島（互いに小さいだけ）を塗り替えの無限ループにしない。"""
    width, height, pixels = grid(["xA"])
    fixed, total, passes, hit_limit = absorb_to_fixed_point(pixels, width, height, 2, max_passes=64)
    assert passes == 1 and total == 0 and fixed == pixels and hit_limit is False
    # ゲートだけがこの島を問題視する
    assert smallest_island(fixed, width, height) == 1


def test_absorb_reports_when_it_stops_at_max_passes():
    """`passes=64` だけ見て運用者が収束か打ち切りかを推測できない状態を無くす。

    連なった島の剥がれには周数がかかる（`test_absorb_peels_chained_islands_over_passes`
    は 3 周必要）ので、`max_passes` をそれより小さくすれば打ち切りフラグが立つ。
    """
    width, height, pixels = grid(["xBAA", "BBAA", "AAAA", "AAAA"])
    _fixed, _total, passes, hit_limit = absorb_to_fixed_point(pixels, width, height, 4, max_passes=2)
    assert passes == 2 and hit_limit is True
    # 打ち切られた時点ではまだ島が残っている
    _fixed, total, passes, hit_limit = absorb_to_fixed_point(pixels, width, height, 4)
    assert passes == 3 and hit_limit is False and total == 4


def test_absorb_peels_chained_islands_over_passes():
    """小さな島が連なっている場合は外側から消えていく（複数周目の本当の効用）。"""
    width, height, pixels = grid(["xBAA", "BBAA", "AAAA", "AAAA"])
    cleaned, changed = absorb_islands(pixels, width, height, 4)
    assert changed == 3  # B（3px）は地色へ吸収されるが、B しかなかった x はまだ行き先がない
    assert cleaned.count(RED) == 1
    fixed, total, passes, hit_limit = absorb_to_fixed_point(pixels, width, height, 4)
    assert RED not in fixed and WHITE not in fixed
    assert total == 4 and passes == 3 and hit_limit is False


def test_quantize_maps_to_nearest_palette_entry():
    palette = [BLACK, WHITE]
    pixels = [(10, 10, 10, 255), (240, 240, 240, 255), (0, 0, 0, 255)]
    assert quantize(pixels, palette) == [BLACK, WHITE, BLACK]


def test_quantize_is_noop_on_quantized_image():
    palette = [BLACK, WHITE, RED]
    pixels = [WHITE, RED, BLACK, RED]
    assert quantize(pixels, palette) == pixels


def test_quantize_prefers_earlier_entry_on_equal_distance():
    palette = [(0, 0, 255, 255), (0, 0, 255, 0)]
    assert quantize([(0, 0, 255, 255)], palette) == [(0, 0, 255, 255)]


def test_round_trip_indexed_with_transparency(tmp_path):
    pixels = [BLACK, WHITE, HOLE, HALF] * 4
    path = tmp_path / "indexed.png"
    write_png(path, 4, 4, pixels)
    data = path.read_bytes()
    assert struct.unpack(">IIBBBBB", data[16:29])[3] == 3
    assert "PLTE" in chunk_tags(data) and "tRNS" in chunk_tags(data)
    image = read_png(path)
    assert (image.width, image.height) == (4, 4)
    assert image.pixels == pixels


def many_colours(count, alpha=None):
    """count 個の重複なし色を作る（256 超で RGB8 / RGBA8 ルートを通るため）。"""
    colours = []
    for i in range(count):
        red, green, blue = i % 256, (i // 256) * 60 + 7, (i * 7) % 256
        colours.append((red, green, blue, 255 if alpha is None else alpha(i)))
    return colours


def test_round_trip_rgb8(tmp_path):
    colours = many_colours(300)
    pixels = [colour for colour in colours for _ in range(2)]
    path = tmp_path / "rgb.png"
    write_png(path, 2, len(pixels) // 2, pixels)
    data = path.read_bytes()
    assert struct.unpack(">IIBBBBB", data[16:29])[3] == 2
    assert "PLTE" not in chunk_tags(data)
    assert read_png(path).pixels == pixels


def test_round_trip_rgba8(tmp_path):
    pixels = many_colours(300, alpha=lambda i: i % 256)
    path = tmp_path / "rgba.png"
    write_png(path, 3, len(pixels) // 3, pixels)
    assert struct.unpack(">IIBBBBB", path.read_bytes()[16:29])[3] == 6
    assert read_png(path).pixels == pixels


def test_written_png_has_no_metadata_chunks(tmp_path):
    indexed = tmp_path / "indexed.png"
    rgb = tmp_path / "rgb.png"
    write_png(indexed, *grid(["AAA", "AxA", "xAA"]))
    write_png(rgb, 20, 20, many_colours(400))
    for path in (indexed, rgb):
        tags = chunk_tags(path.read_bytes())
        assert [tag for tag in tags if tag in METADATA_CHUNKS] == []
        assert tags[0] == "IHDR" and tags[-1] == "IEND"


def test_all_five_scanline_filters_decode():
    rows = [
        bytes([0, 0, 0, 255, 10, 20, 30, 255, 40, 50, 60, 255]),
        bytes([100, 10, 200, 255, 5, 5, 5, 255, 250, 240, 230, 128]),
        bytes([1, 2, 3, 255, 200, 190, 180, 64, 12, 34, 56, 255]),
        bytes([9, 8, 7, 255, 11, 22, 33, 255, 44, 55, 66, 255]),
        bytes([255, 255, 255, 255, 0, 0, 0, 0, 17, 17, 17, 255]),
    ]
    expected = []
    scanlines = bytearray()
    prev = bytes(12)
    for ftype, row in enumerate(rows):
        filtered = bytearray(len(row))
        for i, value in enumerate(row):
            left = row[i - 4] if i >= 4 else 0
            above_left = prev[i - 4] if i >= 4 else 0
            if ftype == 1:
                predicted = left
            elif ftype == 2:
                predicted = prev[i]
            elif ftype == 3:
                predicted = (left + prev[i]) >> 1
            elif ftype == 4:
                predicted = paeth_predictor(left, prev[i], above_left)
            else:
                predicted = 0
            filtered[i] = (value - predicted) & 0xFF
        scanlines.append(ftype)
        scanlines += filtered
        prev = row
        expected.extend((row[i], row[i + 1], row[i + 2], row[i + 3]) for i in range(0, 12, 4))

    image = decode_png(build_png(6, 8, 3, 5, bytes(scanlines)))
    assert (image.width, image.height) == (3, 5)
    assert image.pixels == expected


@pytest.mark.parametrize("depth,colour_count", [(1, 2), (2, 4), (4, 16), (8, 256)])
def test_indexed_png_decodes_every_bit_depth(depth, colour_count):
    """ビット深さ 1/2/4/8 と、行末パディングビットの無視を確認する。"""
    palette = bytes(value for i in range(colour_count) for value in (i, (i * 3) % 256, (i * 5) % 256))
    indices = [i % colour_count for i in range(5)]
    per_byte = 8 // depth
    row = bytearray()
    byte = 0
    for position, index in enumerate(indices):
        byte |= index << (depth * (per_byte - 1 - position % per_byte))
        if position % per_byte == per_byte - 1:
            row.append(byte)
            byte = 0
    if len(indices) % per_byte:
        # 行末のパディングビットは埋めても、幅で切り捨てられるため無視される
        padding_bits = depth * (per_byte - len(indices) % per_byte)
        row.append(byte | ((1 << padding_bits) - 1))
    image = decode_png(build_png(3, depth, 5, 1, b"\x00" + bytes(row), palette=palette))
    assert image.pixels == [(i, (i * 3) % 256, (i * 5) % 256, 255) for i in indices]


def test_encoder_output_is_readable_by_a_strict_chunk_walk(tmp_path):
    data = encode_png(20, 20, many_colours(400))
    assert decode_png(data).pixels == many_colours(400)
    assert chunk_tags(data) == ["IHDR", "IDAT", "IEND"]


def test_encode_png_size_mismatch_is_a_value_error_not_an_assert(tmp_path):
    """`python -O` では assert が消えて不正な PNG を黙って書き出していた。

    ValueError にしたので呼び出し側（`main`）で exit 2 に落ちる。終了コード契約上
    1 = ゲート失敗なので、ここは 2 の側。
    """
    with pytest.raises(ValueError, match="一致しません"):
        encode_png(3, 3, [BLACK] * 8)
    with pytest.raises(ValueError, match="一致しません"):
        write_png(tmp_path / "bad.png", 3, 3, [BLACK] * 8)
    assert not (tmp_path / "bad.png").exists()


def test_smallest_island_and_gate_threshold():
    width, height, pixels = grid(["AAAAA", "AAAAA", "AAxAA", "AAAAA", "AAAAA"])
    assert smallest_island(pixels, width, height) == 1
    cleaned, _changed = absorb_islands(pixels, width, height, 4)
    assert smallest_island(cleaned, width, height) == width * height


def test_palette_of_prefers_plte_order(tmp_path):
    path = tmp_path / "ref.png"
    write_png(path, 3, 2, [WHITE, BLACK, HOLE] * 2)
    assert palette_of(read_png(path)) == [WHITE, BLACK, HOLE]


def test_palette_of_falls_back_to_raster_order(tmp_path):
    path = tmp_path / "ref.png"
    write_png(path, 4, 1, [CYAN, WHITE, WHITE, BLACK])
    assert palette_of(PngImage(4, 1, [CYAN, WHITE, WHITE, BLACK], None)) == [CYAN, WHITE, BLACK]
    assert palette_of(read_png(path)) == [CYAN, WHITE, BLACK]


def test_check_fails_on_noisy_file_and_passes_on_clean_one(tmp_path, capsys):
    noisy = tmp_path / "noisy.png"
    write_png(noisy, *grid(["AAAAA", "AABAA", "AAxAA", "AABAA", "AAAAA"]))
    before = noisy.read_bytes()
    assert main(["--check", "--min-area", "8", str(noisy)]) == 1
    assert noisy.read_bytes() == before
    assert "smallest_island=1" in capsys.readouterr().out

    clean = tmp_path / "clean.png"
    write_png(clean, *grid(["AAAAA", "ABBBA", "ABBBA", "ABBBA", "AAAAA"]))
    assert main(["--check", "--min-area", "8", str(clean)]) == 0
    line = capsys.readouterr().out
    assert "smallest_island=9" in line and "FAIL" not in line


def test_rewrite_in_place_absorbs_every_island(tmp_path, capsys):
    path = tmp_path / "icon.png"
    write_png(path, *grid(["AAAAA", "AABAA", "AAxAA", "AABAA", "AAAAA"]))
    assert main(["--min-area", "8", str(path)]) == 0
    out = capsys.readouterr().out
    assert "recoloured=3" in out
    image = read_png(path)
    assert colour_table(image.pixels) == (BLACK,)
    assert smallest_island(image.pixels, image.width, image.height) == 25


def test_cli_warns_on_stderr_when_absorption_hits_the_pass_limit(tmp_path, capsys, monkeypatch):
    """打ち切り WARNING は `process()` の印字経路で、関数単体のテストはここを通らない。

    既定の 64 周では実資産が必ず収束するのでこの経路は普段黙る。だからここでは
    `DEFAULT_MAX_PASSES` を 2 に下げて、本来 3 周必要な連なり島
    （`test_absorb_peels_chained_islands_over_passes` と同じ字）を実ファイルで流す。
    印字される上限値が実際に打ち切った周数と揃っていることも同時に確認する。
    """
    monkeypatch.setattr("despeckle_assets.DEFAULT_MAX_PASSES", 2)
    capped = tmp_path / "capped.png"
    write_png(capped, *grid(["xBAA", "BBAA", "AAAA", "AAAA"]))
    assert main(["--min-area", "4", str(capped)]) == 0
    captured = capsys.readouterr()
    assert "passes=2" in captured.out
    assert "WARNING" in captured.err
    assert "max_passes=2" in captured.err
    # 打ち切り時点で吸収が進行中だった（2 周目まで塗り替えが続いている）ことを印字で確認する
    assert "passes=2 の時点でまだ吸収が進行中" in captured.err

    # 対照: 同じ字を既定の周数（実測 3 周で固定点）に戻して流すと警告は出ない
    monkeypatch.setattr("despeckle_assets.DEFAULT_MAX_PASSES", DEFAULT_MAX_PASSES)
    full = tmp_path / "full.png"
    write_png(full, *grid(["xBAA", "BBAA", "AAAA", "AAAA"]))
    assert main(["--min-area", "4", str(full)]) == 0
    captured = capsys.readouterr()
    assert "passes=3" in captured.out and "WARNING" not in captured.err


def test_palette_from_reference_quantizes_then_absorbs(tmp_path, capsys):
    reference = tmp_path / "ref.png"
    write_png(reference, *grid(["AB", "BA"]))
    target = tmp_path / "banner.png"
    write_png(
        target,
        4,
        2,
        [
            (250, 250, 250, 255),
            (3, 4, 5, 255),
            (250, 250, 250, 255),
            (250, 250, 250, 255),
            (250, 250, 250, 255),
            (250, 250, 250, 255),
            (250, 250, 250, 255),
            (250, 250, 250, 255),
        ],
    )
    assert main(["--min-area", "3", "--palette-from", str(reference), str(target)]) == 0
    assert read_png(target).pixels == [WHITE] * 8
    out = capsys.readouterr().out
    assert "colors=2->1" in out and "recoloured=1" in out


def test_explicit_palette_is_plain_rgb_with_no_alpha():
    assert parse_palette("#000000,#14283C") == [(0, 0, 0, 255), (20, 40, 60, 255)]
    # 大文字小文字は同じ色とみなし、順序は指定どおりに残す（同距離打ち壊しの決定性につながる）
    assert parse_palette("#14283c,#000000") == [(20, 40, 60, 255), (0, 0, 0, 255)]
    assert parse_palette("#14283C,#000000") == list(reversed(parse_palette("#000000,#14283C")))
    assert parse_palette("#F1B11D") == [(241, 177, 29, 255)]


def test_explicit_palette_quantizes_identically_to_palette_from(tmp_path):
    """同一色列表を `--palette` と `--palette-from` で与えたとき、成果物が一致する。

    最近傍の同距離打ち壊しはパレットの**先頭順**で決まるので、ズレを効かせるため
    BLACK と TEAL から厳密に等距離の画素 (10, 20, 30) を使う。両者の結果が
    指定順・逆順のどちらでも一致することまで見る。
    """
    teal = (20, 40, 60, 255)
    ambiguous = (10, 20, 30, 255)
    references = {}
    for name, first, second in (("black-first", BLACK, teal), ("teal-first", teal, BLACK)):
        path = tmp_path / f"ref-{name}.png"
        write_png(path, 2, 1, [first, second])
        references[name] = path

    hex_black_first = "#000000,#14283C"
    hex_teal_first = "#14283C,#000000"
    outputs = {}
    for name, hex_value in (("black-first", hex_black_first), ("teal-first", hex_teal_first)):
        via_file = tmp_path / f"out-{name}-from.png"
        via_text = tmp_path / f"out-{name}-text.png"
        for path in (via_file, via_text):
            write_png(path, 4, 1, [ambiguous] * 4)
        # min_area 1 = 吸収を一切起こさない → 量子化だけの差分に絞り込める
        assert main(["--min-area", "1", "--palette-from", str(references[name]), str(via_file)]) == 0
        assert main(["--min-area", "1", "--palette", hex_value, str(via_text)]) == 0
        assert via_file.read_bytes() == via_text.read_bytes(), name
        outputs[name] = read_png(via_text).pixels
    # 打ち壊し方向が実際に逆になっていること（一致が偶然の通り越しでないことの担保）
    assert outputs["black-first"] == [BLACK] * 4
    assert outputs["teal-first"] == [teal] * 4


def test_explicit_palette_absorbs_identically_to_palette_from(tmp_path):
    """量子化＋吸収の全系でも `--palette` と `--palette-from` が一致する。

    量子化で生まれた同色画素が吸収で動くので、吸収段階まで含めて同一かを見る。
    """
    teal = (20, 40, 60, 255)
    ambiguous = (10, 20, 30, 255)
    reference = tmp_path / "ref.png"
    write_png(reference, 2, 1, [BLACK, teal])
    rows = [".....", ".###.", ".###.", "....."]  # '.' = BLACK、'#' = 同距離の曖昧画素（中央 3x2 = 6px）
    layout = [ambiguous if ch == "#" else BLACK for row in rows for ch in row]
    via_file = tmp_path / "from.png"
    via_text = tmp_path / "text.png"
    for path in (via_file, via_text):
        write_png(path, 5, 4, layout)
    assert main(["--min-area", "8", "--palette-from", str(reference), str(via_file)]) == 0
    assert main(["--min-area", "8", "--palette", "#000000,#14283C", str(via_text)]) == 0
    assert via_file.read_bytes() == via_text.read_bytes()
    # 中央の 3x2 = 6px が min_area 8 に満たないので地色 BLACK へ吸収される（＝吸収が効いている）
    assert read_png(via_text).pixels == [BLACK] * 20


def test_explicit_palette_and_palette_from_are_mutually_exclusive(tmp_path, capsys):
    reference = tmp_path / "ref.png"
    write_png(reference, *grid(["AB", "BA"]))
    target = tmp_path / "icon.png"
    write_png(target, *grid(["AAAAA", "AABAA", "AABAA", "AABAA", "AAAAA"]))
    before = target.read_bytes()
    # 排他指定は argparse が parser.error() で SystemExit(2)（`main` の return 2 と同じ終了コード）。
    # 例外として漏れないことは `main(argv)` を直接呼ぶこのテストでしか確認できないので、
    # プロセス終了コード 2 であることをここで固定する。
    with pytest.raises(SystemExit) as excinfo:
        main(["--palette", "#000000", "--palette-from", str(reference), str(target)])
    assert excinfo.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
    assert target.read_bytes() == before


@pytest.mark.parametrize(
    "spec",
    [
        "000000",  # # が無い
        "#000",  # 3 桁短縮形は受け付けない
        "#00000G",  # 16 進ではない
        "#0000000",  # 7 桁
        "#000000,",  # 末尾カンマで空エントリ
        ",#000000",
        "#000000,#00FF",
        "",
    ],
)
def test_explicit_palette_rejects_malformed_hex(tmp_path, capsys, spec):
    target = tmp_path / "icon.png"
    write_png(target, *grid(["AAAAA", "AABAA", "AABAA", "AABAA", "AAAAA"]))
    before = target.read_bytes()
    assert main(["--palette", spec, str(target)]) == 2
    err = capsys.readouterr().err
    assert "ERROR" in err and ("#RRGGBB" in err or "空以外" in err)
    assert target.read_bytes() == before  # 前処理でファイルに触れていない


@pytest.mark.parametrize("spec", ["#000000,#000000", "#000000,#FFFFFF,#000000", "#0000FF,#0000ff"])
def test_explicit_palette_rejects_duplicates(tmp_path, capsys, spec):
    target = tmp_path / "icon.png"
    write_png(target, *grid(["AAAAA", "AABAA", "AABAA", "AABAA", "AAAAA"]))
    before = target.read_bytes()
    assert main(["--palette", spec, str(target)]) == 2
    assert "重複" in capsys.readouterr().err
    assert target.read_bytes() == before


def test_explicit_palette_rejects_entry_count_out_of_range(tmp_path, capsys):
    target = tmp_path / "icon.png"
    write_png(target, *grid(["AAAAA", "AABAA", "AABAA", "AABAA", "AAAAA"]))
    too_many = ",".join(f"#{index:06X}" for index in range(1, 258))
    assert len(too_many.split(",")) == MAX_PALETTE_ENTRIES + 1
    assert main(["--palette", too_many, str(target)]) == 2
    assert "1〜256" in capsys.readouterr().err
    # 境界値そのものは有効（256 色は OK / 2 色も OK）
    boundary = tmp_path / "boundary.png"
    write_png(boundary, *grid(["AAAAA", "AABAA", "AABAA", "AABAA", "AAAAA"]))
    assert main(["--min-area", "8", "--palette", ",".join(f"#{index:06X}" for index in range(1, 257)), str(boundary)]) == 0
    assert len(colour_table(read_png(boundary).pixels)) == 1
    single = tmp_path / "single.png"
    write_png(single, *grid(["AAAAA", "AABAA", "AABAA", "AABAA", "AAAAA"]))
    assert main(["--min-area", "8", "--palette", "#000000,#FFFFFF", str(single)]) == 0
    assert colour_table(read_png(single).pixels) == (BLACK,)


def test_missing_file_returns_io_error(tmp_path, capsys):
    assert main([str(tmp_path / "absent.png")]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_unsupported_format_returns_io_error(tmp_path, capsys):
    path = tmp_path / "not-an-image.png"
    path.write_bytes(b"GIF89a........")
    assert main(["--check", str(path)]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_interlaced_png_is_rejected(tmp_path, capsys):
    data = build_png(6, 8, 1, 1, b"\x00" + bytes(4), interlace=1)
    path = tmp_path / "interlaced.png"
    path.write_bytes(data)
    assert main(["--check", str(path)]) == 2
    assert "インターレス" in capsys.readouterr().err


def test_gray_png_is_rejected():
    data = build_png(4, 8, 1, 1, b"\x00" + bytes(2))
    with pytest.raises(ValueError, match="カラータイプ"):
        decode_png(data)


def test_gray_png_decodes_with_transparency_colour_key():
    """グレースケール（color type 0）の tRNS は 2 バイト。一致画素は alpha 0。"""
    data = build_png(0, 8, 3, 1, b"\x00" + bytes([128, 200, 128]), trns=struct.pack(">H", 128))
    image = decode_png(data)
    assert image.palette is None
    assert image.pixels == [(128, 128, 128, 0), (200, 200, 200, 255), (128, 128, 128, 0)]


def test_rgb_png_applies_transparency_colour_key():
    """RGB（color type 2）の tRNS カラーキーを無視していた旧実装の回帰テスト。

    キーの書き方は libpng / ImageMagick 実測に合わせる（bit depth 8 ではサンプル値を
    下位バイトに右詰め。`magick -size 4x4 xc:red -transparent red PNG24:` が
    `tRNS 00ff00000000` を書き、その画素を alpha 0 で読む）。
    """
    key = (255, 0, 0)
    data = build_png(
        2,
        8,
        2,
        2,
        b"\x00" + bytes([255, 0, 0, 10, 20, 30]) + b"\x00" + bytes([9, 9, 9, 255, 0, 0]),
        trns=struct.pack(">HHH", *key),
    )
    image = decode_png(data)
    assert image.pixels == [(255, 0, 0, 0), (10, 20, 30, 255), (9, 9, 9, 255), (255, 0, 0, 0)]


def test_colour_key_rejects_malformed_trns(tmp_path, capsys):
    """キーの長さが規格と違う／範囲外／alpha 付きに付いている場合は黙って無視しない。"""
    data = build_png(2, 8, 1, 1, b"\x00" + bytes([1, 2, 3]), trns=b"\x00\xff\x00\x00")
    with pytest.raises(ValueError, match="tRNS"):
        decode_png(data)
    data = build_png(6, 8, 1, 1, b"\x00" + bytes([1, 2, 3, 4]), trns=b"\x00\x01\x00\x02\x00\x03")
    with pytest.raises(ValueError, match="tRNS"):
        decode_png(data)
    # 上位バイトに値を置く書き方は bit depth 8 では範囲外（libpng は警告して無視する）
    data = build_png(2, 8, 1, 1, b"\x00" + bytes([255, 0, 0]), trns=struct.pack(">HHH", 255 << 8, 0, 0))
    with pytest.raises(ValueError, match="範囲を超えて"):
        decode_png(data)
    wide = tmp_path / "wide-key.png"
    wide.write_bytes(data)
    assert main(["--check", str(wide)]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_colour_key_matches_pillow_parsing(tmp_path):
    """キー解釈が libpng/Pillow と一致することの実地照合。**手動チェック専用で CI では必ず skip**。

    Pillow は `uv.lock` に含まれていない（`uv run --locked --with google-genai --with
    unidiff pytest` でも入らない）ため、CI の `Python Checks` はこの 1 本を必ず
    skip する。通したいときは Pillow を持つ解釈器で直接
    `python3 -m pytest .github/scripts/tests/test_despeckle_assets.py -k pillow` を実行する。

    tRNS カラーキー自体を CI で保証しているのは下の 3 本（いずれも PIL 不要で、
    codec を libpng / ImageMagick の実測バイト列と突き合わせて検証する）:
      * `test_gray_png_decodes_with_transparency_colour_key`（type 0 の 2 バイトキー）
      * `test_rgb_png_applies_transparency_colour_key`（type 2 の 6 バイトキー。
        `magick -transparent` が書く下位バイト右詰めを実測で固定）
      * `test_colour_key_rejects_malformed_trns`（長さ違い／範囲外／alpha 付きは exit 2）
    注: Pillow は `convert("RGBA")` でダイレクトタイプの tRNS を**適用しない**（実測で
    alpha 255 のまま）。したがって比較するのは Pillow が解析したキー値そのものであり、
    透明化は本codec側で仕様のとおりに行っていることを併せて確認する。
    """
    Image = pytest.importorskip("PIL.Image", reason="Pillow は開発依存ではない")

    data = build_png(2, 8, 2, 1, b"\x00" + bytes([255, 0, 0, 10, 20, 30]), trns=struct.pack(">HHH", 255, 0, 0))
    path = tmp_path / "key.png"
    path.write_bytes(data)
    with Image.open(path) as handle:
        assert handle.info["transparency"] == (255, 0, 0)
        assert handle.convert("RGB").tobytes() == bytes([255, 0, 0, 10, 20, 30])
    assert decode_png(data).pixels == [(255, 0, 0, 0), (10, 20, 30, 255)]


def test_truncated_ihdr_is_a_usage_error(tmp_path, capsys):
    """IHDR が短いと struct.error が飛んでいた（旧実装は exit 1＝ゲート失敗で出ていた）。"""
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBB", 2, 2, 8, 6))
    path = tmp_path / "short-ihdr.png"
    path.write_bytes(data)
    with pytest.raises(ValueError, match="IHDR の長さ"):
        decode_png(data)
    assert main(["--check", str(path)]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_out_of_range_palette_index_is_a_usage_error(tmp_path, capsys):
    data = build_png(3, 8, 2, 1, b"\x00" + bytes([0, 7]), palette=bytes([0, 0, 0, 255, 255, 255]))
    path = tmp_path / "bad-index.png"
    path.write_bytes(data)
    with pytest.raises(ValueError, match="パレットインデックスが範囲外"):
        decode_png(data)
    assert main(["--check", str(path)]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_truncated_idat_is_a_usage_error(tmp_path, capsys):
    """IDAT を切り詰めると zlib.error。終了コード契約（2 = 使い方・IO）を守る。"""
    full = build_png(6, 8, 2, 1, b"\x00" + bytes(8))
    off, idat_body = 8, None
    while off + 12 <= len(full):
        length = struct.unpack(">I", full[off : off + 4])[0]
        if full[off + 4 : off + 8] == b"IDAT":
            idat_body = full[off + 8 : off + 8 + length]
            break
        off += 12 + length
    assert idat_body
    data = full[:off] + chunk(b"IDAT", idat_body[: len(idat_body) // 2]) + full[off + 12 + len(idat_body) :]
    path = tmp_path / "short-idat.png"
    path.write_bytes(data)
    with pytest.raises(zlib.error):
        decode_png(data)
    assert main(["--check", str(path)]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_bad_crc_is_rejected():
    data = build_png(6, 8, 1, 1, b"\x00" + bytes(4))
    broken = data[:-4] + b"\x00\x00\x00\x00"
    with pytest.raises(ValueError):
        decode_png(broken)


def build_ico(width, height, rows, palette, mask_zero=True, bpp=4):
    """ICO を 1 枚作る。`bpp` は 4 / 8 / 24 / 32。

    4・8 はインデックス（`rows` はパレット番号の行列、`palette` は (r, g, b)）、
    24・32 は直接指定（`rows` はそれぞれ (r, g, b)、(r, g, b, a) の行列）で、
    マスターの 4bpp は行頭が 4 ビット詰めになる。
    """
    header = struct.pack("<IiiHHIIiiII", 40, width, height * 2, 1, bpp, 0, 0, 0, 0, 0, 0)
    row_bytes = (width * bpp + 31) // 32 * 4
    palette_bytes = b""
    pixels = bytearray()
    if bpp >= 16:
        for row in reversed(rows):
            packed = bytearray(row_bytes)
            for position, value in enumerate(row):
                step = bpp // 8
                blue, green, red = value[0], value[1], value[2]
                packed[position * step : position * step + step] = bytes((blue, green, red) + ((value[3],) if step == 4 else ()))
            pixels += packed
    else:
        # インデックス DIB は 2**bpp 個のパレットが必須（使わない枠は 0 で埋める）
        padded = list(palette) + [(0, 0, 0)] * ((1 << bpp) - len(palette))
        palette_bytes = b"".join(struct.pack("BBBB", b, g, r, 0) for r, g, b in padded)
        per_byte = 8 // bpp
        for row in reversed(rows):
            packed = bytearray(row_bytes)
            for position, index in enumerate(row):
                shift = bpp * (per_byte - 1 - position % per_byte)
                packed[position // per_byte] |= index << shift
            pixels += packed
    mask = bytes(row_bytes * height) if mask_zero else bytes([0xFF] * row_bytes * height)
    image = header + palette_bytes + bytes(pixels) + mask
    directory = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", width, height, 0, 0, 1, bpp, len(image), 22)
    return directory + image


def test_check_opens_ico_layers(tmp_path, capsys):
    ico = tmp_path / "icon.ico"
    rows = [[0, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    ico.write_bytes(build_ico(4, 4, rows, [(0, 0, 0), (255, 255, 255)]))
    assert main(["--check", "--min-area", "4", str(ico)]) == 1
    out = capsys.readouterr().out
    assert "icon.ico 4x4 colors=2 smallest_island=1" in out
    assert main(["--check", "--min-area", "1", str(ico)]) == 0


@pytest.mark.parametrize("bpp", [8, 24, 32])
def test_ico_decodes_every_supported_bit_depth(tmp_path, capsys, bpp):
    """8 / 24 / 32bpp レイヤーを読む。32bpp の alpha 0 は予約バイトと違い `or 255` してはいけない。"""
    white, green = (255, 255, 255), (0, 255, 0)
    marked = (*green, 0) if bpp == 32 else (*green, 255)
    if bpp == 8:
        rows = [[0, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
        data = build_ico(4, 4, rows, [white, green])
    elif bpp == 24:
        rows = [[white] * 4 for _ in range(4)]
        rows[1][2] = green
        data = build_ico(4, 4, rows, [], bpp=24)
    else:
        rows = [[(*white, 255)] * 4 for _ in range(4)]
        rows[1][2] = (*green, 0)  # 旧実装は `or 255` でこれを実在の透明ごと消していた
        data = build_ico(4, 4, rows, [], bpp=32)
    ico = tmp_path / "icon.ico"
    ico.write_bytes(data)
    image = ico_layers(data)[0]
    assert (image.width, image.height) == (4, 4)
    assert image.pixels[6] == marked
    assert image.pixels.count((*white, 255)) == 15
    assert len(colour_table(image.pixels)) == 2
    assert main(["--check", "--min-area", "4", str(ico)]) == 1
    assert "icon.ico 4x4 colors=2 smallest_island=1" in capsys.readouterr().out
    assert main(["--check", "--min-area", "1", str(ico)]) == 0


def test_truncated_ico_is_a_usage_error(tmp_path, capsys):
    """ICO の本体が短いと IndexError 系で抜けていた。終了コード契約（2 = 使い方・IO）を守る。"""
    ico = tmp_path / "short.ico"
    data = build_ico(4, 4, [[0, 0, 0, 0]] * 4, [(0, 0, 0), (255, 255, 255)])
    ico.write_bytes(data[: len(data) - 10])
    with pytest.raises((IndexError, ValueError, struct.error)):
        ico_layers(ico.read_bytes())
    assert main(["--check", str(ico)]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_ico_write_mode_is_rejected(tmp_path, capsys):
    ico = tmp_path / "icon.ico"
    ico.write_bytes(build_ico(4, 4, [[0, 0, 0, 0]] * 4, [(0, 0, 0), (255, 255, 255)]))
    before = ico.read_bytes()
    assert main(["--min-area", "4", str(ico)]) == 2
    assert ico.read_bytes() == before
    assert "ICO は --check 専用" in capsys.readouterr().err


def test_min_area_below_one_is_usage_error(tmp_path, capsys):
    path = tmp_path / "icon.png"
    write_png(path, *grid(["AA", "AA"]))
    assert main(["--min-area", "0", str(path)]) == 2
    assert "min-area" in capsys.readouterr().err


def test_read_png_accepts_str_path(tmp_path):
    path = Path(tmp_path) / "small.png"
    write_png(path, *grid(["AB", "BA"]))
    image = read_png(str(path))
    assert isinstance(image, PngImage)
    assert image.pixels.count(BLACK) == 2
    assert isinstance(image.palette, tuple)
