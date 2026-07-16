"""ai_review.py のメタデータ抽出・モデル解決ロジックのテスト。"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_review import (  # noqa: E402
    _extract_details_blocks,
    resolve_model,
    strip_review_metadata,
)


MODEL_CONFIG = {
    "flash-lite": {
        "name": "gemini-3.1-flash-lite",
        "aliases": ["gemini-flash-lite-latest", "gemini-flash-lite"],
        "input_cost_per_1m": 0.25,
        "output_cost_per_1m": 1.50,
        "max_diff_chars": 2000000,
    },
    "flash": {
        "name": "gemini-3.5-flash",
        "aliases": ["gemini-flash-latest"],
        "input_cost_per_1m": 1.50,
        "output_cost_per_1m": 9.00,
        "max_diff_chars": 2000000,
    },
}


def test_extract_details_nested_is_not_truncated():
    """入れ子の <details> を含む本文から、実行情報ブロックのみを正しく除去する。"""
    html = (
        "### 🤖 AI コードレビュー\n\n"
        "<details><summary>懸念点の詳細</summary>\n"
        "<details><summary>さらに深い説明</summary>ネスト内容</details>\n"
        "外側内容</details>\n\n"
        "<details open><summary>⚡ 今回の実行情報</summary>\n"
        "- **モデル**: `gemini-3.1-flash-lite`\n"
        "</details>\n\n<!-- ai-pr-reviewer-comment -->"
    )
    kept, blocks = _extract_details_blocks(html, "実行情報")
    # 入れ子の details は保持され、実行情報ブロックのみ抽出される
    assert "懸念点の詳細" in kept
    assert "さらに深い説明" in kept
    assert "今回の実行情報" in "".join(blocks)
    assert "実行情報" not in kept


def test_strip_review_metadata_separates_exec_info():
    review = (
        "<details open><summary>⚡ 今回の実行情報</summary>\n"
        "- **モデル**: `gemini-3.1-flash-lite`\n"
        "</details>\n\n"
        "### 🤖 AI コードレビュー\n\n"
        "本文です。\n\n<!-- ai-pr-reviewer-comment -->"
    )
    body, exec_info = strip_review_metadata(review)
    assert "本文です。" in body
    assert "実行情報" in exec_info
    assert "ai-pr-reviewer-comment" not in body
    assert "AI コードレビュー" not in body


def test_strip_review_metadata_removes_previous_exec_info():
    review = (
        "<details><summary>📝 前回の実行情報</summary>\n古い情報</details>\n\n"
        "<details open><summary>⚡ 今回の実行情報</summary>\n今回情報</details>\n\n"
        "### 🤖 AI コードレビュー\n\n本文\n<!-- ai-pr-reviewer-comment -->"
    )
    body, exec_info = strip_review_metadata(review)
    assert "前回の実行情報" not in body
    assert "今回情報" in exec_info
    assert "本文" in body


def test_strip_review_metadata_prefers_current_over_quoted():
    """AI が古い実行情報を引用して複数ブロックを出した場合、「今回」を優先する。"""
    review = (
        "<details open><summary>⚡ 実行情報</summary>\n最初の引用ブロック</details>\n\n"
        "<details open><summary>⚡ 今回の実行情報</summary>\n今回情報</details>\n\n"
        "### 🤖 AI コードレビュー\n\n本文\n<!-- ai-pr-reviewer-comment -->"
    )
    body, exec_info = strip_review_metadata(review)
    assert "今回情報" in exec_info
    assert "最初の引用ブロック" not in exec_info
    # 今回以外の実行情報ブロックは本文からも除去される（重複防止）
    assert "最初の引用ブロック" not in body


def test_strip_review_metadata_excludes_prefixed_blocks():
    """「📝」を含むブロックは exec_info に採用しない。"""
    review = (
        "<details open><summary>📝 前回の実行情報</summary>\n古い</details>\n\n"
        "<details open><summary>⚡ 今回の実行情報</summary>\n今回</details>\n\n"
        "本文\n<!-- ai-pr-reviewer-comment -->"
    )
    _, exec_info = strip_review_metadata(review)
    assert "今回" in exec_info
    assert "古い" not in exec_info


def test_strip_review_metadata_empty():
    assert strip_review_metadata("") == ("", "")


def test_resolve_model_by_key():
    info, name = resolve_model("flash-lite", MODEL_CONFIG)
    assert name == "gemini-3.1-flash-lite"
    assert info["input_cost_per_1m"] == 0.25


def test_resolve_model_by_alias():
    info, name = resolve_model("gemini-flash-lite-latest", MODEL_CONFIG)
    # エイリアス指定: 正式名としてそのまま、価格は flash-lite 相当
    assert name == "gemini-flash-lite-latest"
    assert info["input_cost_per_1m"] == 0.25


def test_resolve_model_by_name():
    info, name = resolve_model("gemini-3.5-flash", MODEL_CONFIG)
    assert name == "gemini-3.5-flash"


def test_resolve_model_unknown_keyword_fallback():
    info, name = resolve_model("gemini-flash-latest-xyz", MODEL_CONFIG)
    assert info["input_cost_per_1m"] == 1.50  # flash 相当
    assert name == "gemini-flash-latest-xyz"


def test_resolve_model_unknown_cost_is_none():
    info, name = resolve_model("some-unknown-model", MODEL_CONFIG)
    # 未知モデルはコスト不明(None)とし、高コストを安価と誤認しない
    assert info["input_cost_per_1m"] is None
    assert info["output_cost_per_1m"] is None
    assert info["max_diff_chars"] == 500000
    assert name == "some-unknown-model"


def test_resolve_model_partial_match_prefers_longer():
    # "flash-lite" を部分一致で渡しても flash ではなく flash-lite に一致する
    info, name = resolve_model("gemini-flash-lite", MODEL_CONFIG)
    assert info["input_cost_per_1m"] == 0.25
    assert name == "gemini-flash-lite"


def test_models_json_valid():
    """models.json が正しい構造であることを確認。"""
    path = os.path.join(os.path.dirname(__file__), "..", "models.json")
    with open(path) as f:
        config = json.load(f)
    for key, info in config.items():
        assert "name" in info
        assert "input_cost_per_1m" in info
        assert "output_cost_per_1m" in info
        assert "max_diff_chars" in info
        assert isinstance(info.get("aliases", []), list)
