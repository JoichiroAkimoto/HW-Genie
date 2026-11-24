#!/usr/bin/env python3
"""ブランド資産（ドットマップ → PNG）の決定論的レンダリングパイプライン。

`docs/assets/hw-genie-icon.grid.txt` の 64x64 ドットマップが意図の唯一の源
（Source of Truth）で、配布 PNG はすべてここから描画する。生成ツールノイズの後処理も
手作業の塗り潰しも介在させないため、描画は次の 3 段階だけ。

1. コメント行の `CH=#RRGGBB` 宣言だけでパレットを作る（`load_map`）
2. 1 セルを `scale x scale` のブロックに引き伸ばす（`render_pixels`＝最近傍拡大。
   `transparent_cell` を渡すとその字形だけ alpha 0 になる）
3. og:image バナーはフラットな地色のキャンバスにマークを貼り、文字ブロック
   （ワードマーク）とスパークだけは現行ファイルからそのまま写す（`compose_banner`）

PNG の読み書きは `despeckle_assets` の実装を再利用する（標準ライブラリのみ。この環境に
Pillow は無く、追加もしない）。`--check` は描画結果をコミット済みファイルと突き合わせる
drift ゲートで、マップを経ずに PNG を手編集すると必ず失敗する。

Usage:
  python3 render_brand_assets.py                                  # 全アセットを描画して上書き
  python3 render_brand_assets.py --check                          # drift 検品（差があれば exit 1）
  python3 render_brand_assets.py --map PATH --assets-dir DIR      # 位置指定（テスト・試し打ち用）

終了コード: 0 = 生成完了／一致 / 1 = --check で drift / 2 = 使い方・IO エラー
"""

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

from despeckle_assets import colour_table, read_png, write_png

BACKGROUND_GLYPH = "X"
PALETTE_TOKEN = re.compile(r"([A-Za-z0-9])\s*=\s*#([0-9A-Fa-f]{6})")

REPO_ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = REPO_ROOT / "docs" / "assets"
DEFAULT_MAP = ASSET_DIR / "hw-genie-icon.grid.txt"

# (ファイル名, 一辺のピクセル数, スケール, 透過にする字形)
ICON_SPECS = (
    ("hw-genie-icon-1024.png", 1024, 16, None),
    ("hw-genie-icon.png", 512, 8, BACKGROUND_GLYPH),
    ("hw-genie-icon-solid.png", 512, 8, None),
)
BANNER_NAME = "hw-genie-social-preview.png"
BANNER_WIDTH = 1280
BANNER_HEIGHT = 640
BANNER_SCALE = 8

# 現行 `docs/assets/hw-genie-social-preview.png` で実測した構成要素の位置。
#   マーク帯      x[200,572) y[28,588)   ＝ 中央 (386, 308)
#   スパーク      x[162,197) y[276,311)  ＝ マークの左隣。絵の一部なのでそのまま写す
#   ワードマーク  x[640,1130) y[130,540)（実描画は x[643,1127) y[138,535)。"HW-Genie" と副題）
# スケール 8 のマーク描画内容はマップ上の非 X 範囲 x[64,424) y[24,464)（360x440、
# 中心 (244, 244)）なので、その中心をマーク帯の実測中心 (386, 308) へ持ってくる
# オフセットが (142, 64)。元画はマップより縦に長く（560px 相当）高さだけが縮むが、
# 左右の分量（左にマーク＋スパーク、右に文字）と上下の重心（文字ブロックの中心
# y=336 に対しマーク y=308）を元のまま保つので、構成＝見た目のバランスは変わらない。
BANNER_MARK_OFFSET = (142, 64)
BANNER_WORDMARK_BOX = (640, 1130, 130, 540)
BANNER_SPARKLE_BOX = (162, 197, 276, 311)


class Asset(NamedTuple):
    """描画対象 1 件。`kind` が "icon" ならマップから直接、"banner" は現行画像を写す。"""

    path: Path
    width: int
    height: int
    scale: int
    transparent_cell: str | None
    kind: str


def load_map(path) -> tuple:
    """ドットマップを開き、(パレット, グリッド行) を返す。

    パレットはコメント行（`#` 始まり）に現れる `CH=#RRGGBB` だけを収集して作る。
    それ以外の行がグリッドで、全行が同幅・全字形がパレットに存在・行が 1 行以上、
    という条件を満たさなければ `ValueError`（意図をテキストで審査できるように、
    黙って直すことはしない）。
    """
    text = Path(path).read_text(encoding="utf-8")
    name = Path(path).name
    palette: dict = {}
    grid: list = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.startswith("#"):
            for glyph, hex_value in PALETTE_TOKEN.findall(line):
                colour = tuple(int(hex_value[i : i + 2], 16) for i in (0, 2, 4))
                known = palette.get(glyph)
                if known is not None and known != colour:
                    raise ValueError(f"{name} {lineno} 行目: 字形 {glyph} の色が二重宣言されています（{known} / {colour}）")
                palette[glyph] = colour
            continue
        if not line.strip():
            continue
        grid.append(line)
    if not grid:
        raise ValueError(f"{name}: グリッド行がありません（コメントだけのマップは不可）")
    if not palette:
        raise ValueError(f"{name}: コメント行に `CH=#RRGGBB` の色宣言がありません")
    widths = {len(row) for row in grid}
    if len(widths) > 1:
        reference = len(grid[0])
        ragged = [lineno for lineno, row in enumerate(grid, start=1) if len(row) != reference]
        raise ValueError(f"グリッド行の幅が揃っていません（1 行目 {reference} に対し {sorted(widths)}。行番号 {ragged[:5]}）")
    unknown = sorted({glyph for row in grid for glyph in row} - set(palette))
    if unknown:
        raise ValueError(f"{name}: 色宣言のない字形が使われています: {''.join(unknown)}")
    return palette, grid


def render_pixels(grid: list, palette: dict, scale: int, transparent_cell: str | None = None) -> tuple:
    """グリッドを `scale` 倍（1 セル = `scale`x`scale` のブロック）で描画する。

    補間は一切行わない（最近傍相当）。`transparent_cell` の字形は alpha 0（RGB はその
    まま）にして、それ以外は不透明にする。
    """
    if scale < 1:
        raise ValueError(f"scale は 1 以上が必要です（現状 {scale}）")
    if not grid:
        raise ValueError("グリッドが空です")
    if transparent_cell is not None and transparent_cell not in palette:
        raise ValueError(f"透過指定の字形 {transparent_cell} がパレットにありません")
    width = len(grid[0]) * scale
    height = len(grid) * scale
    pixels: list = []
    for row in grid:
        run: list = []
        for glyph in row:
            colour = palette.get(glyph)
            if colour is None:
                raise ValueError(f"パレットにない字形 {glyph} です（色宣言を追加してください）")
            alpha = 0 if glyph == transparent_cell else 255
            run.extend([(colour[0], colour[1], colour[2], alpha)] * scale)
        for _ in range(scale):
            pixels.extend(run)
    return width, height, pixels


def _copy_box(canvas: list, source: list, box: tuple, width: int, height: int, source_width: int, source_height: int, label: str) -> None:
    """現行バナーの長方形領域 `box` をキャンバスへそのまま写す（描画し直さない）。

    `box` は `(x0, x1, y0, y1)` の半開区間で、実測値を書いた `(x[a,b) y[c,d))` という
    表記と同じ順にしている。
    """
    x0, x1, y0, y1 = box
    if not 0 <= x0 < x1 <= width or not 0 <= y0 < y1 <= height:
        raise ValueError(f"{label} の領域 x[{x0},{x1}) y[{y0},{y1}) がキャンバス {width}x{height} から外れています")
    if x1 > source_width or y1 > source_height:
        raise ValueError(f"{label} の領域 x[{x0},{x1}) y[{y0},{y1}) が差し替え元バナー {source_width}x{source_height} を覆っていません")
    for y in range(y0, y1):
        canvas[y * width + x0 : y * width + x1] = source[y * source_width + x0 : y * source_width + x1]


def compose_banner(
    source,
    grid: list,
    palette: dict,
    *,
    scale: int = BANNER_SCALE,
    offset: tuple = BANNER_MARK_OFFSET,
    wordmark_box: tuple = BANNER_WORDMARK_BOX,
    sparkle_box: tuple = BANNER_SPARKLE_BOX,
) -> tuple:
    """og:image バナーを組み直す（1280x640 → 同じ寸法）。

    手順は 3 つだけ:

    1. マップの地色（`X` の RGB、現行では `#03104D`）でキャンバスをフラットに塗る
    2. マップから描画したマークを `offset` に貼る（重畳なし・そのまま置換）
    3. ワードマーク領域とスパーク領域を **差し替え元（現行コミット済みバナー）から
       画素単位でそのまま写す**（文字は描き直さない）

    3 を 2 の後に行うのが冪等性の鍵。マークの貼り付け幅（512px）はワードマーク領域の
    左端やスパークと重なっているが、写し込む元は常に「現在のコミット済みファイル」
    であり、そこには 1 周目の実行で同じ値が書き戻されているため、2 周目以降もバイト列が
    変わらない。文字や地色の領域を自分で描き直さない限り、同じ入力 → 同じ出力になる。
    """
    width, height = source.width, source.height
    background = palette[BACKGROUND_GLYPH]
    canvas = [(background[0], background[1], background[2], 255)] * (width * height)
    mark_width, mark_height, mark_pixels = render_pixels(grid, palette, scale)
    offset_x, offset_y = offset
    for y in range(mark_height):
        target_y = offset_y + y
        if not 0 <= target_y < height:
            continue
        x0 = max(0, offset_x)
        x1 = min(width, offset_x + mark_width)
        if x0 >= x1:
            continue
        start = target_y * width + x0
        canvas[start : start + (x1 - x0)] = mark_pixels[(y * mark_width + x0 - offset_x) : (y * mark_width + x1 - offset_x)]
    _copy_box(canvas, source.pixels, wordmark_box, width, height, source.width, source.height, "ワードマーク")
    _copy_box(canvas, source.pixels, sparkle_box, width, height, source.width, source.height, "スパーク")
    return width, height, canvas


def plan_assets(assets_dir: Path = ASSET_DIR, map_path: Path = DEFAULT_MAP) -> tuple:
    """マップを読み、(パレット, グリッド, 描画対象リスト) を作る。"""
    palette, grid = load_map(map_path)
    assets_dir = Path(assets_dir)
    assets = [Asset(assets_dir / name, size, size, scale, transparent, "icon") for name, size, scale, transparent in ICON_SPECS]
    assets.append(Asset(assets_dir / BANNER_NAME, BANNER_WIDTH, BANNER_HEIGHT, BANNER_SCALE, None, "banner"))
    return palette, grid, assets


def render(asset: Asset, palette: dict, grid: list) -> tuple:
    """1 アセットを描画して (幅, 高さ, 画素リスト) を返す（バナーは現行ファイルを読む）。"""
    if asset.kind == "banner":
        source = read_png(asset.path)
        if (source.width, source.height) != (asset.width, asset.height):
            raise ValueError(f"{asset.path.name}: 差し替え元バナーが {asset.width}x{asset.height} ではなく {source.width}x{source.height} です")
        return compose_banner(source, grid, palette, scale=asset.scale)
    width, height, pixels = render_pixels(grid, palette, asset.scale, asset.transparent_cell)
    if (width, height) != (asset.width, asset.height):
        raise ValueError(
            f"{asset.path.name}: マップ {len(grid[0])}x{len(grid)} × scale {asset.scale} = {width}x{height} が期待サイズ {asset.width}x{asset.height} と一致しません"
        )
    return width, height, pixels


def report_asset(asset: Asset, width: int, height: int, pixels: list) -> None:
    colours = len(colour_table(pixels))
    print(f"{asset.path.name} {width}x{height} colors={colours} bytes={asset.path.stat().st_size}")


def check_asset(asset: Asset, width: int, height: int, pixels: list) -> int:
    """コミット済みファイルと描画結果を画素単位で比べ、drift の有無を 1/0 で返す。"""
    try:
        image = read_png(asset.path)
    except FileNotFoundError:
        print(f"{asset.path.name} {width}x{height} MISMATCH missing")
        return 1
    if (image.width, image.height) != (width, height):
        print(f"{asset.path.name} {image.width}x{image.height} MISMATCH size differs (rendered {width}x{height})")
        return 1
    diff = sum(1 for expected, actual in zip(pixels, image.pixels, strict=True) if expected != actual)
    if diff:
        print(f"{asset.path.name} {width}x{height} MISMATCH {diff} pixels differ")
        return 1
    print(f"{asset.path.name} {width}x{height} match")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ドットマップからブランド資産 PNG を決定的に生成する")
    parser.add_argument("action", nargs="?", default="all", choices=("all",), help="all = 全アセットを描画して上書き（既定）")
    parser.add_argument("--check", action="store_true", help="描画はメモリ内のみ。コミット済みファイルと比較して差があれば exit 1")
    parser.add_argument(
        "--map",
        dest="map_path",
        type=Path,
        default=DEFAULT_MAP,
        metavar="PATH",
        help="ドットマップの位置（既定: docs/assets/hw-genie-icon.grid.txt）",
    )
    parser.add_argument(
        "--assets-dir",
        dest="assets_dir",
        type=Path,
        default=ASSET_DIR,
        metavar="DIR",
        help="資産ディレクトリ（既定: docs/assets。テスト・試し打ち用）",
    )
    args = parser.parse_args(argv)
    try:
        palette, grid, assets = plan_assets(args.assets_dir, args.map_path)
        drift = 0
        for asset in assets:
            if args.check and not asset.path.exists():
                print(f"{asset.path.name} MISMATCH missing")
                drift += 1
                continue
            width, height, pixels = render(asset, palette, grid)
            if args.check:
                drift += check_asset(asset, width, height, pixels)
                continue
            write_png(asset.path, width, height, pixels)
            report_asset(asset, width, height, pixels)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.check and drift:
        print(f"drift: {drift} asset(s) do not match {args.map_path}", file=sys.stderr)
    return 1 if (args.check and drift) else 0


if __name__ == "__main__":
    sys.exit(main())
