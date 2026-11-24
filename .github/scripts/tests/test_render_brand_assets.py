"""render_brand_assets.py（ドットマップ → ブランド PNG 決定論生成）のテスト。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from despeckle_assets import PngImage, colour_table, read_png, smallest_island, write_png  # noqa: E402
from render_brand_assets import (  # noqa: E402
    ASSET_DIR,
    BACKGROUND_GLYPH,
    BANNER_NAME,
    BANNER_WORDMARK_NAME,
    DEFAULT_MAP,
    WORDMARK_SIZE,
    compose_banner,
    load_map,
    load_wordmark,
    main,
    render_pixels,
)

NAVY = (3, 16, 77)
GOLD = (241, 177, 29)
VIOLET = (66, 27, 151)
SMOKE = (111, 190, 246)
AMBER = (173, 89, 20)
EDGE = (167, 154, 225)
OPAQUE_NAVY = (*NAVY, 255)
OPAQUE_GOLD = (*GOLD, 255)
OPAQUE_VIOLET = (*VIOLET, 255)
HOLED_NAVY = (*NAVY, 0)

HEADER = """# HW-Genie brand mark — test map.
# palette: X=#03104D background  V=#F1B11D gold  G=#421B97 shield violet
#          B=#6FBEF6 smoke core  L=#AD5914 amber shadow  A=#A79AE1 smoke edge
"""
SIX_COLOURS = {
    "X": NAVY,
    "V": GOLD,
    "G": VIOLET,
    "B": SMOKE,
    "L": AMBER,
    "A": EDGE,
}


def write_map(path, rows):
    path.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def full_rows(body):
    """64x64 マップを作る（既定は地色のみ。`body` は (x, y, 文字) の指定）。"""
    grid = [["X"] * 64 for _ in range(64)]
    for x, y, glyph in body:
        grid[y][x] = glyph
    return ["".join(row) for row in grid]


def test_load_map_parses_header_and_grid(tmp_path):
    path = write_map(tmp_path / "m.txt", ["XVV", "GVX", "VXG"])
    palette, grid = load_map(path)
    assert len(palette) == 6
    assert palette == SIX_COLOURS
    assert grid == ["XVV", "GVX", "VXG"]
    # str パスでも開ける（CLI は Path を渡すが、テストと互換になる）
    assert load_map(str(path))[1] == grid


def test_load_map_rejects_ragged_rows(tmp_path):
    path = write_map(tmp_path / "m.txt", ["XVV", "GV"])
    with pytest.raises(ValueError, match="幅が揃っていません"):
        load_map(path)


def test_load_map_reports_actual_row_count_when_grid_is_short(tmp_path):
    """行数が幅に未達なら、後段のサイズエラーではなく行数そのものを報告する。"""
    path = write_map(tmp_path / "m.txt", ["XVV", "GVX"])
    with pytest.raises(ValueError, match="グリッド行が 2 行あります（3 を期待"):
        load_map(path)


def test_load_map_rejects_non_six_digit_hex_colour(tmp_path):
    """`#` 直後の 16 進が 6 桁でなければ黙って無視せず拒否する（7 桁の残り 1 桁も）。"""
    for hex_value in ("03104", "03104DD", "zzzzzz"):
        path = tmp_path / "m.txt"
        path.write_text(f"# palette: X=#{hex_value} bg\n#          V=#F1B11D gold\nXXX\nXXX\nXXX\n", encoding="utf-8")
        with pytest.raises(ValueError, match="6 桁 16 進"):
            load_map(path)


def test_load_map_rejects_colour_declaration_it_cannot_parse(tmp_path):
    """語の途中に埋れた字形（`AX=#...`）は 6 桁でも収集できないので明示的に拒否する。"""
    path = tmp_path / "m.txt"
    path.write_text("# palette: AX=#03104D bg\nXXX\nXXX\nXXX\n", encoding="utf-8")
    with pytest.raises(ValueError, match="解析できません"):
        load_map(path)


def test_load_map_rejects_glyph_without_colour_declaration(tmp_path):
    path = write_map(tmp_path / "m.txt", ["XVV", "GVZ", "XXV"])
    with pytest.raises(ValueError, match="色宣言のない字形"):
        load_map(path)


def test_load_map_rejects_empty_grid(tmp_path):
    path = tmp_path / "m.txt"
    path.write_text(HEADER, encoding="utf-8")
    with pytest.raises(ValueError, match="グリッド行がありません"):
        load_map(path)


def test_load_map_rejects_double_colour_declaration(tmp_path):
    path = tmp_path / "m.txt"
    path.write_text("# X=#03104D a\n# X=#FFFFFF b\nXXX\nXXX\nXXX\n", encoding="utf-8")
    with pytest.raises(ValueError, match="二重宣言"):
        load_map(path)


def test_real_map_is_64x64_with_six_colours():
    """Source of Truth 側のマップ（コミット済みテキスト）の形自体を保つ。"""
    palette, grid = load_map(DEFAULT_MAP)
    assert palette == SIX_COLOURS
    assert len(grid) == 64
    assert {len(row) for row in grid} == {64}


def test_render_pixels_makes_exact_scale_blocks():
    width, height, pixels = render_pixels(["XV", "VX"], {"X": NAVY, "V": GOLD}, 3)
    assert (width, height) == (6, 6)
    expected = []
    for row in ["XV", "VX"]:
        for _ in range(3):
            for glyph in row:
                colour = OPAQUE_NAVY if glyph == "X" else OPAQUE_GOLD
                expected.extend([colour] * 3)
    assert pixels == expected


def test_render_pixels_is_resolution_independent():
    """同じマップの 2 倍 / 4 倍描画は、セル単位で見れば完全に一致する。"""
    grid = ["XV", "VX"]
    palette = {"X": NAVY, "V": GOLD}
    _w, _h, small = render_pixels(grid, palette, 2)
    _w, _h, large = render_pixels(grid, palette, 4)
    for y in range(4):
        for x in range(4):
            assert large[(y * 2) * 8 + x * 2] == small[y * 4 + x]


def test_render_pixels_keys_transparent_cell_only():
    width, height, pixels = render_pixels(["XV", "VX"], {"X": NAVY, "V": GOLD}, 2, transparent_cell=BACKGROUND_GLYPH)
    assert (width, height) == (4, 4)
    assert pixels[:2] == [HOLED_NAVY, HOLED_NAVY]
    assert pixels[2:4] == [OPAQUE_GOLD, OPAQUE_GOLD]
    assert pixels.count(HOLED_NAVY) == 8
    assert pixels.count(OPAQUE_GOLD) == 8
    # 透過でも RGB は地色のまま（`identify` の colors= に地色が残る）
    assert {pixel[:3] for pixel in pixels} == {NAVY, GOLD}


def test_render_pixels_rejects_bad_arguments():
    palette = {"X": NAVY}
    with pytest.raises(ValueError, match="scale"):
        render_pixels(["X"], palette, 0)
    with pytest.raises(ValueError, match="グリッドが空"):
        render_pixels([], palette, 2)
    with pytest.raises(ValueError, match="透過指定"):
        render_pixels(["X"], palette, 2, transparent_cell="Z")


def build_fixture(tmp_path, body=()):
    """docs/assets と同じファイル名・同じ寸法の一時資産を作る（バナーは地色のみ）。"""
    assets = tmp_path / "assets"
    assets.mkdir()
    map_path = write_map(assets / "map.txt", full_rows(body))
    write_png(assets / BANNER_WORDMARK_NAME, WORDMARK_SIZE[0], WORDMARK_SIZE[1], [OPAQUE_NAVY] * (WORDMARK_SIZE[0] * WORDMARK_SIZE[1]))
    write_png(assets / BANNER_NAME, 1280, 640, [OPAQUE_NAVY] * (1280 * 640))
    return map_path, assets


def cli(args, map_path, assets):
    return main([*args, "--map", str(map_path), "--assets-dir", str(assets)])


def test_check_passes_on_rendered_files_and_fails_on_hand_edit(tmp_path, capsys):
    body = [(4, 4, "V"), (5, 4, "G"), (4, 5, "G"), (5, 5, "V")]
    map_path, assets = build_fixture(tmp_path, body)
    assert cli([], map_path, assets) == 0
    out = capsys.readouterr().out
    assert "hw-genie-icon-1024.png 1024x1024" in out and "hw-genie-social-preview.png 1280x640" in out

    assert cli(["--check"], map_path, assets) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 4 and all("match" in line and "MISMATCH" not in line for line in lines)

    # 手編集（マップを通さない 1px の書き換え）は必ず drift になる
    target = assets / "hw-genie-icon-solid.png"
    image = read_png(target)
    pixels = list(image.pixels)
    pixels[100] = OPAQUE_VIOLET
    write_png(target, image.width, image.height, pixels)
    assert cli(["--check"], map_path, assets) == 1
    report = capsys.readouterr().out
    assert "hw-genie-icon-solid.png 512x512 MISMATCH 1 pixels differ" in report
    assert "hw-genie-icon.png 512x512 match" in report


def test_check_reports_missing_asset_as_drift(tmp_path, capsys):
    map_path, assets = build_fixture(tmp_path)
    assert cli([], map_path, assets) == 0
    capsys.readouterr()
    (assets / "hw-genie-icon.png").unlink()
    assert cli(["--check"], map_path, assets) == 1
    assert "hw-genie-icon.png MISMATCH missing" in capsys.readouterr().out


def test_missing_map_is_io_error(tmp_path, capsys):
    assert main(["--check", "--map", str(tmp_path / "absent.txt")]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_malformed_asset_png_is_an_io_error(tmp_path, capsys):
    """壊れたアセット PNG は exit 2（終了コード契約: 1 はゲート失敗の意味）。"""
    import struct
    import zlib

    map_path, assets = build_fixture(tmp_path)
    assert cli([], map_path, assets) == 0
    capsys.readouterr()
    body = struct.pack(">IIBB", 4, 4, 8, 6)  # IHDR として 4 バイト短い
    broken = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(body)) + b"IHDR" + body + struct.pack(">I", zlib.crc32(b"IHDR" + body) & 0xFFFFFFFF)
    for name in ("hw-genie-icon.png", "hw-genie-icon-solid.png", "hw-genie-icon-1024.png", BANNER_WORDMARK_NAME, BANNER_NAME):
        (assets / name).write_bytes(broken)
        assert cli(["--check"], map_path, assets) == 2, name
        assert "ERROR" in capsys.readouterr().err, name


MINI_PALETTE = {"X": NAVY, "V": GOLD, "G": VIOLET}
MINI_GRID = ["XXVX", "XVGV", "VGGV", "XXVX"]
MINI_SIZE = (24, 16)
MINI_OFFSET = (6, 3)
MINI_WORDMARK = (16, 24, 4, 14)
MINI_WORDMARK_SIZE = (MINI_WORDMARK[1] - MINI_WORDMARK[0], MINI_WORDMARK[3] - MINI_WORDMARK[2])


def mini_wordmark():
    """差し替え元ワードマーク（8x10）。斜線で 2 色を混ぜ、そのまま写ることを確認しやすくする。"""
    width, height = MINI_WORDMARK_SIZE
    pixels = [OPAQUE_VIOLET if (x + y) % 3 else OPAQUE_GOLD for y in range(height) for x in range(width)]
    return PngImage(width, height, pixels, None)


def legacy_banner():
    """旧実装が差し替え元にしていた自己参照バナー相当（スパーク帯と古い左上隅の紫 2x2）。"""
    width, height = MINI_SIZE
    pixels = [OPAQUE_NAVY] * (width * height)
    for y in range(MINI_WORDMARK[2], MINI_WORDMARK[3]):
        for x in range(MINI_WORDMARK[0], MINI_WORDMARK[1]):
            pixels[y * width + x] = OPAQUE_VIOLET if (x + y) % 3 else OPAQUE_GOLD
    for y in range(10, 12):
        for x in range(2, 4):
            pixels[y * width + x] = OPAQUE_GOLD
    for y in range(0, 2):
        for x in range(0, 2):
            pixels[y * width + x] = OPAQUE_VIOLET
    return PngImage(width, height, pixels, None)


def compose(wordmark=None):
    wordmark = wordmark or mini_wordmark()
    return compose_banner(
        wordmark,
        MINI_GRID,
        MINI_PALETTE,
        scale=2,
        offset=MINI_OFFSET,
        wordmark_box=MINI_WORDMARK,
        width=MINI_SIZE[0],
        height=MINI_SIZE[1],
    )


def box_pixels(image_pixels, width, box):
    x0, x1, y0, y1 = box
    return [image_pixels[y * width + x] for y in range(y0, y1) for x in range(x0, x1)]


def test_compose_banner_is_idempotent():
    """組み直しを 2 周させても画素列が変わらない（同じマップ＋同じワードマークなら決定的）。"""
    width, height, first = compose()
    assert (width, height) == MINI_SIZE
    assert compose()[2] == first
    # バナー側から切り出した文字ブロックを差し替え元に回しても結果は不変（＝生成側に記憶がない）
    cropped = PngImage(MINI_WORDMARK_SIZE[0], MINI_WORDMARK_SIZE[1], box_pixels(first, width, MINI_WORDMARK), None)
    assert compose(cropped)[2] == first


def test_compose_banner_takes_the_wordmark_from_its_own_asset():
    """文字は描き直さず、ワードマーク資産から画素単位でそのまま写る。"""
    wordmark = mini_wordmark()
    _width, _height, pixels = compose(wordmark)
    assert box_pixels(pixels, MINI_SIZE[0], MINI_WORDMARK) == wordmark.pixels


def test_compose_banner_ignores_whatever_the_banner_used_to_contain():
    """旧自己参照バナーの内容（スパーク帯・構成要素外の古い描画）は一切写らない。

    スパークはマップのマーク自身が持つので、旧実装のようにバナー側から領域を写して
    二重にすることはない（旧写し箱 x[162,197) y[276,311) はマップ側スパーク
    x[214,238) y[304,328) に x 方向 17 列の隙（最近列間 18px）で離れ、y は 7 行
    重なるので両箱の最近画素間は Chebyshev = Manhattan = 18px。同じ画像で 2 つの
    ＋型の金の画素同士で見ると Chebyshev 18px / Manhattan 24px。これで二重スパークに
    なっていた）。またバナーへ手編集で
    塗り込んだ色も、地色／マーク／ワードマークのどれでもなければ消える（＝drift ゲートが効く）。
    """
    _width, _height, pixels = compose()
    legacy = legacy_banner().pixels
    # 旧バナーのスパーク帯（x[2,4) y[10,12)）はマーク着地領域 x[6,14) y[3,11) の外側なので地色に戻る
    assert box_pixels(pixels, MINI_SIZE[0], (2, 4, 10, 12)) != box_pixels(legacy, MINI_SIZE[0], (2, 4, 10, 12))
    # どの構成要素にも入らない領域はフラットな地色
    assert box_pixels(pixels, MINI_SIZE[0], (0, 2, 0, 2)) == [OPAQUE_NAVY] * 4
    assert box_pixels(legacy, MINI_SIZE[0], (0, 2, 0, 2)) == [OPAQUE_VIOLET] * 4


def test_compose_banner_pastes_the_mark_at_the_offset():
    """マークはオフセット位置に貼られ、その X セルは地色で塗られる。"""
    _width, _height, pixels = compose()
    # マークの非 X セル（col 2, row 0 の `V`）がオフセット位置へ来ている
    assert pixels[MINI_OFFSET[1] * MINI_SIZE[0] + MINI_OFFSET[0] + 4] == OPAQUE_GOLD
    assert pixels[MINI_OFFSET[1] * MINI_SIZE[0] + MINI_OFFSET[0]] == OPAQUE_NAVY


def test_compose_banner_rejects_geometry_that_leaves_the_canvas():
    source = mini_wordmark()
    with pytest.raises(ValueError, match="キャンバス"):
        compose_banner(
            source, MINI_GRID, MINI_PALETTE, scale=2, offset=MINI_OFFSET, wordmark_box=(16, 30, 4, 14), width=MINI_SIZE[0], height=MINI_SIZE[1]
        )
    with pytest.raises(ValueError, match="キャンバス"):
        compose_banner(
            source, MINI_GRID, MINI_PALETTE, scale=2, offset=MINI_OFFSET, wordmark_box=(16, 24, 4, 20), width=MINI_SIZE[0], height=MINI_SIZE[1]
        )
    # 旧実装は黙って切り捨てていたマークのはみ出しもエラーにする
    with pytest.raises(ValueError, match="マーク描画"):
        compose_banner(source, MINI_GRID, MINI_PALETTE, scale=2, offset=(20, 3), wordmark_box=MINI_WORDMARK, width=MINI_SIZE[0], height=MINI_SIZE[1])
    with pytest.raises(ValueError, match="マーク描画"):
        compose_banner(
            source, MINI_GRID, MINI_PALETTE, scale=4, offset=MINI_OFFSET, wordmark_box=MINI_WORDMARK, width=MINI_SIZE[0], height=MINI_SIZE[1]
        )
    # ブロック寸法と box が食い違えば領域がずれるので拒否する
    with pytest.raises(ValueError, match="一致しません"):
        compose_banner(
            PngImage(8, 9, mini_wordmark().pixels[: 8 * 9], None),
            MINI_GRID,
            MINI_PALETTE,
            scale=2,
            offset=MINI_OFFSET,
            wordmark_box=MINI_WORDMARK,
            width=MINI_SIZE[0],
            height=MINI_SIZE[1],
        )


def test_load_wordmark_refuses_the_banner_and_wrong_sizes(tmp_path):
    """ワードマークはバナーとは別ファイル・指定寸法でなければ入力として使えない。"""
    banner = tmp_path / BANNER_NAME
    write_png(banner, 1280, 640, [OPAQUE_NAVY] * (1280 * 640))
    with pytest.raises(ValueError, match="自己参照"):
        load_wordmark(banner, banner)
    shifted = tmp_path / BANNER_WORDMARK_NAME
    write_png(shifted, WORDMARK_SIZE[0] + 1, WORDMARK_SIZE[1], [OPAQUE_NAVY] * ((WORDMARK_SIZE[0] + 1) * WORDMARK_SIZE[1]))
    with pytest.raises(ValueError, match="ワードマークが"):
        load_wordmark(shifted, banner)
    ok = tmp_path / BANNER_WORDMARK_NAME
    write_png(ok, WORDMARK_SIZE[0], WORDMARK_SIZE[1], [OPAQUE_NAVY] * (WORDMARK_SIZE[0] * WORDMARK_SIZE[1]))
    assert load_wordmark(ok, banner).pixels[0] == OPAQUE_NAVY


def test_check_fails_when_the_banner_wordmark_region_is_hand_painted(tmp_path, capsys):
    """旧実装の自己参照だと在这里の手編集は絶対に見えなかった。今は必ず exit 1。"""
    body = [(4, 4, "V"), (5, 4, "G"), (4, 5, "G"), (5, 5, "V")]
    map_path, assets = build_fixture(tmp_path, body)
    assert cli([], map_path, assets) == 0
    capsys.readouterr()

    banner = assets / BANNER_NAME
    image = read_png(banner)
    pixels = list(image.pixels)
    red = (255, 0, 0, 255)
    for y in range(200, 320):
        for x in range(700, 900):
            pixels[y * image.width + x] = red
    write_png(banner, image.width, image.height, pixels)

    assert cli(["--check"], map_path, assets) == 1
    report = capsys.readouterr().out
    assert "hw-genie-social-preview.png 1280x640 MISMATCH" in report


def test_committed_wordmark_is_the_banner_text_crop():
    """コミット済みワードマークは実バナーの文字ブロックと画素単位で一致する。

    これは **box 定数（x[640,1130) y[130,540)）と寸法 490x410 の番兵**で、自己参照
    バグの守りはではない。旧実装（バナー自身から文字を写す）でも同じ比較は成立して
    いて必ずパスした。文字領域の手編集を落とすのは `load_wordmark` の自己参照拒否と
    `test_check_fails_when_the_banner_wordmark_region_is_hand_painted` の側。
    """
    wordmark = read_png(ASSET_DIR / BANNER_WORDMARK_NAME)
    banner = read_png(ASSET_DIR / BANNER_NAME)
    x0, x1, y0, y1 = 640, 1130, 130, 540
    assert (wordmark.width, wordmark.height) == (x1 - x0, y1 - y0) == WORDMARK_SIZE
    crop = [banner.pixels[y * banner.width + x] for y in range(y0, y1) for x in range(x0, x1)]
    assert wordmark.pixels == crop


def test_real_icon_master_has_no_small_islands():
    """CI ゲート: マスター 1024 はマップ由来なので 1 セル = 256px 以上。ノイズ混入を弾く。"""
    image = read_png(ASSET_DIR / "hw-genie-icon-1024.png")
    assert smallest_island(image.pixels, image.width, image.height) >= 128


def test_real_banner_clears_the_noise_gate():
    """CI ゲート: バナーに 40px 未満の色島を残さない。

    運用側の 検品 4 は `--min-area 48`（実測 49 で通る）。CI は 48 だと実測に
    1px しか余裕がないので 40 まで落としてある——マップの 1 セル = 64px、
    文字の最小島 = 49px のどちらかが 40〜47 の範囲に収まるような意図変更は
    「簡略化」であってノイズではないため、ここでは落とさない。
    """
    image = read_png(ASSET_DIR / BANNER_NAME)
    assert smallest_island(image.pixels, image.width, image.height) >= 40


def test_real_banner_smallest_island_is_the_wordmark_stroke():
    """参考情報: 現行バナーの最小島は 49px（ワードマークの最小の字画）。

    CI のノイズゲートは上の 40px で、この 49 は「いま何が残っているか」の記録。
    マップのセル 1 つ = 64px なので、49 より小さい島は必ずワードマーク側由来。
    意図して字画を細く／短くしたときはこの行も更新する（ゲートは 40 なので落ちない）。
    """
    image = read_png(ASSET_DIR / BANNER_NAME)
    assert smallest_island(image.pixels, image.width, image.height) == 49


def test_real_banner_colour_count_tripwire():
    """CI ゲート: バナーは 6 色＋文字の色（現行 7）。迷彩色で色数が膨らんだら失敗する。"""
    image = read_png(ASSET_DIR / BANNER_NAME)
    assert len(colour_table(image.pixels)) <= 16


def test_committed_repo_assets_match_the_map():
    """CI ゲート: コミット済み docs/assets がマップから再現できなければ失敗する。"""
    assert main(["--check"]) == 0
