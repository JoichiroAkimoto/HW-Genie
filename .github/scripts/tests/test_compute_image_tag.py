"""compute_image_tag.py（GHCR タグ計算）のテスト。"""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "compute_image_tag.py"


def run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def test_main_branch_returns_latest():
    p = run("branch", "refs/heads/main", "main")
    assert p.returncode == 0
    assert p.stdout.splitlines() == ["ghcr.io/joichiroakimoto/hw-genie:latest"]


def test_main_branch_with_arch_returns_suffix_tags():
    p = run("branch", "refs/heads/main", "main", "amd64", "arm64")
    assert p.returncode == 0
    assert p.stdout.splitlines() == [
        "ghcr.io/joichiroakimoto/hw-genie:latest",
        "ghcr.io/joichiroakimoto/hw-genie:latest-amd64",
        "ghcr.io/joichiroakimoto/hw-genie:latest-arm64",
    ]


def test_version_tag_returns_tag():
    p = run("tag", "refs/tags/v1.2.3", "v1.2.3")
    assert p.returncode == 0
    assert p.stdout.strip() == "ghcr.io/joichiroakimoto/hw-genie:v1.2.3"


def test_version_tag_with_patch_parts():
    p = run("tag", "refs/tags/v10.20.30", "v10.20.30")
    assert p.returncode == 0
    assert p.stdout.strip() == "ghcr.io/joichiroakimoto/hw-genie:v10.20.30"


def test_invalid_version_tag_fails():
    p = run("tag", "refs/tags/v1.2", "v1.2")
    assert p.returncode == 1
    assert "Invalid tag" in p.stderr


def test_empty_version_tag_fails():
    p = run("tag", "refs/tags/", "")
    assert p.returncode == 1
    assert "Invalid tag" in p.stderr


def test_non_main_branch_fails():
    p = run("branch", "refs/heads/feature/x", "feature/x")
    assert p.returncode == 1
    assert "only allowed from main" in p.stderr


def test_missing_args_returns_usage_error():
    p = run("branch", "refs/heads/main")
    assert p.returncode == 2
    assert "Usage" in p.stderr
