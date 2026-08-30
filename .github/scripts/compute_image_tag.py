#!/usr/bin/env python3
"""GitHub Actions の GHCR イメージタグ計算を集約するスクリプト。

build ジョブと merge-manifest ジョブの両方から呼び出され、タグ検証と
生成ロジックの不整合を防ぐ。

Usage:
  python compute_image_tag.py <ref_type> <ref> <ref_name> [arch ...]

標準出力:
  1 行目: 統合タグ（アーキ suffix なし、例: ghcr.io/...:latest）
  2 行目以降: 各 arch の suffix 付きタグ（例: ghcr.io/...:latest-amd64）

不正な ref は exit 1 で終了する。
"""

import re
import sys

IMAGE = "ghcr.io/joichiroakimoto/hw-genie"
VERSION_TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")


def compute_image_tag(ref_type: str, ref: str, ref_name: str) -> str:
    """ref から GHCR の統合タグ（suffix なし）を返す。"""
    if ref_type == "tag":
        if not VERSION_TAG_RE.fullmatch(ref_name):
            raise ValueError(f"Invalid tag '{ref_name}'. Must be in format vX.Y.Z")
        return f"{IMAGE}:{ref_name}"
    if ref == "refs/heads/main":
        return f"{IMAGE}:latest"
    raise ValueError(
        f"Image push is only allowed from main or version tags (ref: {ref})"
    )


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 3:
        print(
            f"Usage: {sys.argv[0]} <ref_type> <ref> <ref_name> [arch ...]",
            file=sys.stderr,
        )
        return 2
    try:
        merge_tag = compute_image_tag(args[0], args[1], args[2])
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(merge_tag)
    for arch in args[3:]:
        print(f"{merge_tag}-{arch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
