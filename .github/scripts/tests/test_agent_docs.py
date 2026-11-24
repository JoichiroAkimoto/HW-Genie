"""AGENTS.md とスキルメタデータのドリフト防止テスト。

スキル一覧は AGENTS.md に複製せず、各 `SKILL.md` の frontmatter を正とする。
このテストは (1) 全スキルの name/description が discover 可能であること、
(2) AGENTS.md の相対リンクが実在すること、を CI で保証する。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AGENTS = ROOT / "AGENTS.md"
SKILLS_DIR = ROOT / ".agents" / "skills"


def _read_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    assert match is not None, f"{path} has no YAML frontmatter"
    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            data[key.strip()] = value.strip()
    return data


def test_skill_frontmatter_is_discoverable():
    skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    assert skill_files, "no skills found"

    skill_dirs = {path for path in SKILLS_DIR.iterdir() if path.is_dir()}
    assert skill_dirs == {path.parent for path in skill_files}, (
        "every skill directory must contain SKILL.md"
    )

    names: list[str] = []
    for path in skill_files:
        meta = _read_frontmatter(path)
        assert meta.get("name") == path.parent.name, (
            f"{path}: frontmatter name must match the directory name"
        )
        description = meta.get("description", "")
        assert description, f"{path}: description is required"
        assert len(description) >= 20, f"{path}: description is too short"
        names.append(meta["name"])

    assert len(names) == len(set(names)), "skill names must be unique"


def test_agents_relative_links_resolve():
    text = AGENTS.read_text(encoding="utf-8")
    missing: list[str] = []
    for link in re.findall(r"\]\(([^)]+)\)", text):
        if "://" in link or link.startswith("mailto:"):
            continue
        target = link.split("#", 1)[0]
        if target and not (ROOT / target).exists():
            missing.append(link)
    assert not missing, f"broken AGENTS.md links: {missing}"


def test_agents_stays_a_routing_index():
    """AGENTS.md は入口とハードルールに保つ（詳細は README / SKILL / docs へ）。"""
    text = AGENTS.read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 120, "AGENTS.md is growing; move details to docs/skills"
    assert len(text) <= 12_000, "AGENTS.md is growing; move details to docs/skills"
