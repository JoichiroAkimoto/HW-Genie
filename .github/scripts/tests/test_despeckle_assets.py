"""despeckle_assets.py（PNG 読み書き・微小色島吸収）のテスト。"""

import os
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from despeckle_assets import (  # noqa: E402
    PngImage,
    absorb_islands,
    colour_table,
    decode_png,
    encode_png,
    main,
    palette_of,
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
METADATA_CHUNKS = ("tEXt", "zTXt", "iTXt", "tIME")
CHAR_COLOURS = {"A": BLACK, "B": WHITE, "C": CYAN, "x": RED}


def grid(rows):
    """文字図から write_png 用の (width, height, pixels) を作る。"""
    return len(rows[0]), len(rows), [CHAR_COLOURS[ch] for row in rows for ch in row]


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


def test_absorb_chooses_majority_border_colour():
    width, height, pixels = grid(["AAB", "AxB", "BBB"])
    cleaned, changed = absorb_islands(pixels, width, height, 3)
    assert changed == 1
    assert cleaned[4] == WHITE


def test_absorb_breaks_border_ties_by_first_colour_order():
    width, height, pixels = grid(["AAB", "AxB", "ABB"])
    cleaned, changed = absorb_islands(pixels, width, height, 3)
    assert changed == 1
    assert cleaned[4] == BLACK
    assert absorb_islands(pixels, width, height, 3)[0] == cleaned


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
    data = build_png(0, 8, 2, 1, b"\x00" + bytes(2))
    with pytest.raises(ValueError, match="カラータイプ"):
        decode_png(data)


def test_bad_crc_is_rejected():
    data = build_png(6, 8, 1, 1, b"\x00" + bytes(4))
    broken = data[:-4] + b"\x00\x00\x00\x00"
    with pytest.raises(ValueError):
        decode_png(broken)


def build_ico(width, height, rows, palette, mask_zero=True):
    """4bpp indexed BMP レイヤー 1 枚の ICO を作る（rows は上から下の行）。"""
    header = struct.pack("<IiiHHIIiiII", 40, width, height * 2, 1, 4, 0, 0, 0, 0, 0, 0)
    # 4bpp の DIB はパレット 16 必須（使わない枠は 0 で埋める）
    padded = list(palette) + [(0, 0, 0)] * (16 - len(palette))
    palette_bytes = b"".join(struct.pack("BBBB", b, g, r, 0) for r, g, b in padded)
    row_bytes = (width * 4 + 31) // 32 * 4
    pixels = bytearray()
    for row in reversed(rows):
        packed = bytearray(row_bytes)
        for position, index in enumerate(row):
            packed[position // 2] |= index << (4 * (1 - position % 2))
        pixels += packed
    mask = bytes(row_bytes * height) if mask_zero else bytes([0xFF] * row_bytes * height)
    image = header + palette_bytes + bytes(pixels) + mask
    directory = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", width, height, 0, 0, 1, 4, len(image), 22)
    return directory + image


def test_check_opens_ico_layers(tmp_path, capsys):
    ico = tmp_path / "icon.ico"
    rows = [[0, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    ico.write_bytes(build_ico(4, 4, rows, [(0, 0, 0), (255, 255, 255)]))
    assert main(["--check", "--min-area", "4", str(ico)]) == 1
    out = capsys.readouterr().out
    assert "icon.ico 4x4 colors=2 smallest_island=1" in out
    assert main(["--check", "--min-area", "1", str(ico)]) == 0


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
