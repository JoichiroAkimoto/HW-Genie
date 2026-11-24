"""render_brand_assets.py（ドットマップ → ブランド PNG 決定論生成）のテスト。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from despeckle_assets import PngImage, read_png, write_png  # noqa: E402
from render_brand_assets import (  # noqa: E402
    BACKGROUND_GLYPH,
    BANNER_NAME,
    DEFAULT_MAP,
    compose_banner,
    load_map,
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
    path = write_map(tmp_path / "m.txt", ["XVV", "GVX"])
    palette, grid = load_map(path)
    assert len(palette) == 6
    assert palette == SIX_COLOURS
    assert grid == ["XVV", "GVX"]
    # str パスでも開ける（CLI は Path を渡すが、テストと互換になる）
    assert load_map(str(path))[1] == grid


def test_load_map_rejects_ragged_rows(tmp_path):
    path = write_map(tmp_path / "m.txt", ["XVV", "GV"])
    with pytest.raises(ValueError, match="幅が揃っていません"):
        load_map(path)


def test_load_map_rejects_glyph_without_colour_declaration(tmp_path):
    path = write_map(tmp_path / "m.txt", ["XVV", "GVZ"])
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


MINI_PALETTE = {"X": NAVY, "V": GOLD, "G": VIOLET}
MINI_GRID = ["XXVX", "XVGV", "VGGV", "XXVX"]
MINI_SIZE = (24, 16)
MINI_OFFSET = (6, 3)
MINI_WORDMARK = (16, 24, 4, 14)
MINI_SPARKLE = (2, 4, 10, 12)


def mini_banner():
    """ワードマーク（右）とスパーク（左）に別色を塗った合成バナー。

    左上隅にはどの構成要素にも入らない古い描画（紫 2x2）を置いて、組み直しで
    キャンバスがフラットな地色から塗り直されていることを確認できるようにする。
    """
    width, height = MINI_SIZE
    pixels = [OPAQUE_NAVY] * (width * height)
    for y in range(MINI_WORDMARK[2], MINI_WORDMARK[3]):
        for x in range(MINI_WORDMARK[0], MINI_WORDMARK[1]):
            pixels[y * width + x] = OPAQUE_VIOLET if (x + y) % 3 else OPAQUE_GOLD
    for y in range(MINI_SPARKLE[2], MINI_SPARKLE[3]):
        for x in range(MINI_SPARKLE[0], MINI_SPARKLE[1]):
            pixels[y * width + x] = OPAQUE_GOLD
    for y in range(0, 2):
        for x in range(0, 2):
            pixels[y * width + x] = OPAQUE_VIOLET
    return PngImage(width, height, pixels, None)


def compose(source):
    return compose_banner(source, MINI_GRID, MINI_PALETTE, scale=2, offset=MINI_OFFSET, wordmark_box=MINI_WORDMARK, sparkle_box=MINI_SPARKLE)


def box_pixels(image_pixels, width, box):
    x0, x1, y0, y1 = box
    return [image_pixels[y * width + x] for y in range(y0, y1) for x in range(x0, x1)]


def test_compose_banner_is_idempotent():
    """組み直しを 2 周させても画素列が変わらない（＝生成者が決定的）。"""
    source = mini_banner()
    width, height, first = compose(source)
    assert (width, height) == MINI_SIZE
    second = compose(PngImage(width, height, first, None))[2]
    assert second == first


def test_compose_banner_preserves_wordmark_and_sparkle_bytes():
    """文字とスパークは描き直さず、差し替え元から画素単位でそのまま写る。"""
    source = mini_banner()
    _width, _height, pixels = compose(source)
    for box in (MINI_WORDMARK, MINI_SPARKLE):
        assert box_pixels(pixels, MINI_SIZE[0], box) == box_pixels(source.pixels, MINI_SIZE[0], box)


def test_compose_banner_pastes_the_mark_and_flattens_the_rest():
    """マークはオフセット位置に貼られ、構成要素外の古い描画はフラットな地色に戻る。"""
    source = mini_banner()
    _width, _height, pixels = compose(source)
    # マークの非 X セル（col 2, row 0 の `V`）がオフセット位置へ来ている
    assert pixels[MINI_OFFSET[1] * MINI_SIZE[0] + MINI_OFFSET[0] + 4] == OPAQUE_GOLD
    # 同セルの X 部分（col 0, row 0）は地色で塗られる
    assert pixels[MINI_OFFSET[1] * MINI_SIZE[0] + MINI_OFFSET[0]] == OPAQUE_NAVY
    # どの構成要素にも入らない古い描画（左上隅）は残らない
    assert box_pixels(pixels, MINI_SIZE[0], (0, 2, 0, 2)) == [OPAQUE_NAVY] * 4
    assert box_pixels(source.pixels, MINI_SIZE[0], (0, 2, 0, 2)) == [OPAQUE_VIOLET] * 4


def test_compose_banner_rejects_box_outside_canvas():
    source = mini_banner()
    with pytest.raises(ValueError, match="キャンバス"):
        compose_banner(source, MINI_GRID, MINI_PALETTE, scale=2, offset=MINI_OFFSET, wordmark_box=(16, 30, 4, 14), sparkle_box=MINI_SPARKLE)


def test_committed_repo_assets_match_the_map():
    """CI ゲート: コミット済み docs/assets がマップから再現できなければ失敗する。"""
    assert main(["--check"]) == 0
