#!/usr/bin/env python3
"""ブランド資産（ドットマップ → PNG）の決定論的レンダリングパイプライン。

Source of Truth は 2 つある。その 1 が `docs/assets/hw-genie-icon.grid.txt` の 64x64
ドットマップ（マークの意図）で、マーク系の PNG はすべてここから描画する。その 2 が
バナーの文字ブロックの SoT `docs/assets/hw-genie-wordmark.png`（本スクリプトは文字を
描画しないので、コミット済みこのファイルを入力にする＝入力は上記 2 ファイルだけ）。
生成ツールノイズの後処理も手作業の塗り潰しも介在させないため、描画は次の 3 段階だけ。

1. コメント行の `CH=#RRGGBB` 宣言だけでパレットを作る（`load_map`）
2. 1 セルを `scale x scale` のブロックに引き伸ばす（`render_pixels`＝最近傍拡大。
   `transparent_cell` を渡すとその字形だけ alpha 0 になる）
3. og:image バナーはフラットな地色のキャンバスにマークを貼り、文字ブロックだけを
   コミット済みの独立資産 `docs/assets/hw-genie-wordmark.png` から写す（`compose_banner`）。
   マークはマップ内の 3 個の＋型スパークを自前で持つので、スパークの書き写しはしない

PNG の読み書きは `despeckle_assets` の実装を再利用する（標準ライブラリのみ。
本リポジトリの依存に Pillow を追加しない）。`--check` は描画結果をコミット済みファイルと
突き合わせる drift ゲートで、マップを経ずに PNG を手編集すると必ず失敗する。
ただし比較は **画素単位**で、その範囲は明示的に限られる:
「画素を変えない再エンコードやパレット順の入れ替えは検出しない（来歴メタデータは
検品 2、色島と色数は 検品 4 と CI テストが担当）」。

バナーの文字は `hw-genie-wordmark.png` に固定してあるため、`--check` はバナーも
本当に検証できる（旧実装のように書き戻し先を自分で読むと自己参照になり、
その領域の drift を目隠ししてしまう）。

Usage:
  python3 render_brand_assets.py                                  # 全アセットを描画して上書き
  python3 render_brand_assets.py --check                          # drift 検品（差があれば exit 1）
  python3 render_brand_assets.py --map PATH --assets-dir DIR      # 位置指定（テスト・試し打ち用）

終了コード: 0 = 生成完了／一致 / 1 = --check で drift / 2 = 使い方・IO エラー
"""

import argparse
import re
import struct
import sys
import zlib
from pathlib import Path
from typing import NamedTuple

from despeckle_assets import colour_table, read_png, write_png

BACKGROUND_GLYPH = "X"
# 字形 1 文字 = #RRGGBB の宣言だけを受け取る（前後の単語境界付き）。末尾に 16 進が
# 続き 7 桁になる等の不正な色宣言は `MALFORMED_COLOUR` で拾って明示的に拒否する。
PALETTE_TOKEN = re.compile(r"\b([A-Za-z0-9])\s*=\s*#([0-9A-Fa-f]{6})\b")
MALFORMED_COLOUR = re.compile(r"([A-Za-z0-9])\s*=\s*#([0-9A-Fa-f]*)")

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
BANNER_WORDMARK_NAME = "hw-genie-wordmark.png"
BANNER_WIDTH = 1280
BANNER_HEIGHT = 640
BANNER_SCALE = 8

# バナー構成要素の位置。
#   旧（マップ化前）バナーの実測マーク帯  x[200,572) y[28,588) ＝ 中央 (386, 308)
#     ↑ 定数 offset (142, 64) はこの旧帯の中心に合わせて決めた由来の値。
#   現在のマーク着地 box（実測）         x[214,566) y[88,528)
#     ＝ マップの非 X セル x[9,53) y[3,58) を scale 8 で描いた 352x440 を
#        offset (142, 64) へ貼った結果（旧帯よりは左 14px・右 6px・上下 60px 内側に収まる）
#   ワードマーク  x[640,1130) y[130,540)（実描画は x[643,1127) y[138,535)。"HW-Genie" と副題）
#                 ＝ 差し替え元はコミット済み `hw-genie-wordmark.png`（この box 相当の切り出し 490x410）
# 元画はマップより縦に長く（560px 相当）高さだけが縮むが、左右の分量（左にマーク＋
# スパーク、右に文字）と上下の重心（文字ブロックの中心 y=336 に対しマーク y=308）を
# 元のまま保つので、構成＝見た目のバランスは変わらない。
# 旧運用がバナー内から写していたスパーク領域 x[162,197) y[276,311) は削除した。
# マップのマーク自体が＋型スパーク 3 個を持つためで、重複コピーは写し箱
# x[162,197) y[276,311) をマップ側スパーク x[214,238) y[304,328) の隣に重ねて
# 二重のスパークを発生させていた（実測: 両箱の最近画素間は 18px。x 方向に 17 列の
# 隙＝最近列間 18px、y は 7 行重なるので dy=0、よって Chebyshev = Manhattan = 18px。
# 同じ画像で 2 つの＋型の金の画素同士で見ると Chebyshev 18px / Manhattan 24px）。
BANNER_MARK_OFFSET = (142, 64)
BANNER_WORDMARK_BOX = (640, 1130, 130, 540)


class Asset(NamedTuple):
    """描画対象 1 件。`kind` が "icon" ならマップから直接、"banner" はマップのマーク
    ＋コミット済みワードマーク資産を合成する（いずれも書き戻し先を自分で読まない）。"""

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
    グリッドが行幅と同じ行数（正方グリッド）という条件を満たさなければ `ValueError`
    （意図をテキストで審査できるように、黙って直すことはしない）。
    """
    text = Path(path).read_text(encoding="utf-8")
    name = Path(path).name
    palette: dict = {}
    grid: list = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.startswith("#"):
            declared = MALFORMED_COLOUR.findall(line)
            for glyph, hex_value in declared:
                if len(hex_value) != 6:
                    raise ValueError(f"{name} {lineno} 行目: 字形 {glyph} の色宣言 #{hex_value} は 6 桁 16 進ではありません")
            matched = PALETTE_TOKEN.findall(line)
            missed = sorted({glyph for glyph, hex_value in declared if (glyph, hex_value) not in matched})
            if missed:
                raise ValueError(
                    f"{name} {lineno} 行目: 色宣言 {''.join(missed)} を解析できません（字形は 1 文字、#RRGGBB は単語境界内で宣言してください）"
                )
            for glyph, hex_value in matched:
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
    side = len(grid[0])
    if len(grid) != side:
        raise ValueError(f"{name}: グリッド行が {len(grid)} 行あります（{side} を期待。行幅と同じ正方グリッドが必要です）")
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


def _paste_block(canvas: list, block: list, block_width: int, block_height: int, box: tuple, width: int, height: int, label: str) -> None:
    """差し替え元画像の长方形 `block` をキャンバスの `box` へそのまま写す（描画し直さない）。

    `box` は `(x0, x1, y0, y1)` の半開区間で、実測値を書いた `(x[a,b) y[c,d))` という
    表記と同じ順。ブロック寸法と box のそれが違えば領域がずれるので `ValueError`、
    キャンバスからはみ出す場合も黙って切り捨てずに `ValueError` にする。
    """
    x0, x1, y0, y1 = box
    if not 0 <= x0 < x1 <= width or not 0 <= y0 < y1 <= height:
        raise ValueError(f"{label} の領域 x[{x0},{x1}) y[{y0},{y1}) がキャンバス {width}x{height} から外れています")
    if (x1 - x0, y1 - y0) != (block_width, block_height):
        raise ValueError(f"{label} の差し替え元 {block_width}x{block_height} が領域 {(x1 - x0)}x{(y1 - y0)} と一致しません")
    for y in range(y0, y1):
        canvas[y * width + x0 : y * width + x1] = block[(y - y0) * block_width : (y - y0) * block_width + block_width]


def compose_banner(
    wordmark,
    grid: list,
    palette: dict,
    *,
    scale: int = BANNER_SCALE,
    offset: tuple = BANNER_MARK_OFFSET,
    wordmark_box: tuple = BANNER_WORDMARK_BOX,
    width: int = BANNER_WIDTH,
    height: int = BANNER_HEIGHT,
) -> tuple:
    """og:image バナーを組み直す（`width` x `height` → 同じ寸法）。

    手順は 3 つだけ:

    1. マップの地色（`X` の RGB、現行では `#03104D`）でキャンバスをフラットに塗る
    2. マップから描画したマークを `offset` に貼る（重畳なし・そのまま置換。
       ＋型スパーク 3 個はマーク自身の一部なので、別途写す必要はない）
    3. 文字ブロックを **独立したコミット済み資産 `hw-genie-wordmark.png`** から
       画素単位でそのまま写す（文字は描き直さない）

    3 の差し替え元を書き戻し先バナー自身にすると自己参照になり、`--check` がその
    領域の drift を絶対に見つけられなくなる（古い実装は実際にそうなっていた）。
    ワードマークを別ファイルへ固定した今は、文字領域へ手編集で色を塗り込まれても
    `--check` が必ず 1 で落ちる。1〜3 の入力はすべてマップとワードマークだけなので、
    同じ入力 → 同じ出力で冪等。
    """
    background = palette[BACKGROUND_GLYPH]
    canvas = [(background[0], background[1], background[2], 255)] * (width * height)
    offset_x, offset_y = offset
    mark_width, mark_height, mark_pixels = render_pixels(grid, palette, scale)
    if offset_x < 0 or offset_y < 0 or offset_x + mark_width > width or offset_y + mark_height > height:
        raise ValueError(
            f"マーク描画 {mark_width}x{mark_height} をオフセット {offset} に貼るとキャンバス {width}x{height} から外れます（x[{offset_x},{offset_x + mark_width}) y[{offset_y},{offset_y + mark_height})）"
        )
    for y in range(mark_height):
        start = (offset_y + y) * width + offset_x
        canvas[start : start + mark_width] = mark_pixels[y * mark_width : (y + 1) * mark_width]
    _paste_block(canvas, wordmark.pixels, wordmark.width, wordmark.height, wordmark_box, width, height, "ワードマーク")
    return width, height, canvas


def plan_assets(assets_dir: Path = ASSET_DIR, map_path: Path = DEFAULT_MAP) -> tuple:
    """マップを読み、(パレット, グリッド, 描画対象リスト) を作る。"""
    palette, grid = load_map(map_path)
    assets_dir = Path(assets_dir)
    assets = [Asset(assets_dir / name, size, size, scale, transparent, "icon") for name, size, scale, transparent in ICON_SPECS]
    assets.append(Asset(assets_dir / BANNER_NAME, BANNER_WIDTH, BANNER_HEIGHT, BANNER_SCALE, None, "banner"))
    return palette, grid, assets


WORDMARK_SIZE = (BANNER_WORDMARK_BOX[1] - BANNER_WORDMARK_BOX[0], BANNER_WORDMARK_BOX[3] - BANNER_WORDMARK_BOX[2])


def load_wordmark(wordmark_path: Path, banner_path: Path):
    """バナーの文字差し替え元を読む。書き戻すバナー自身から派生した入力は禁止。"""
    if Path(wordmark_path).resolve() == Path(banner_path).resolve():
        raise ValueError(f"{wordmark_path.name}: ワードマークの差し替え元がバナー自身です（自己参照は drift ゲートを無効化するため不可）")
    wordmark = read_png(wordmark_path)
    if (wordmark.width, wordmark.height) != WORDMARK_SIZE:
        raise ValueError(
            f"{wordmark_path.name}: ワードマークが {WORDMARK_SIZE[0]}x{WORDMARK_SIZE[1]} ではなく {wordmark.width}x{wordmark.height} です（バナーの x[{BANNER_WORDMARK_BOX[0]},{BANNER_WORDMARK_BOX[1]}) y[{BANNER_WORDMARK_BOX[2]},{BANNER_WORDMARK_BOX[3]}) を切り出したものだけが入力になります）"
        )
    return wordmark


def render(asset: Asset, palette: dict, grid: list) -> tuple:
    """1 アセットを描画して (幅, 高さ, 画素リスト) を返す（バナーはマップのマーク＋ワードマーク資産）。"""
    if asset.kind == "banner":
        wordmark = load_wordmark(asset.path.parent / BANNER_WORDMARK_NAME, asset.path)
        return compose_banner(wordmark, grid, palette, scale=asset.scale, width=asset.width, height=asset.height)
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
    except (OSError, ValueError, zlib.error, struct.error, IndexError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.check and drift:
        print(f"drift: {drift} asset(s) do not match {args.map_path}", file=sys.stderr)
    return 1 if (args.check and drift) else 0


if __name__ == "__main__":
    sys.exit(main())
