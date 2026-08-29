"""ai_review.py のヘルパー関数（リトライ・PR コメント取得・ファイル全文取得・メタデータ抽出・モデル解決）のテスト。"""

import json
import os
import re
import subprocess
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai_review  # noqa: E402
from ai_review import (  # noqa: E402
    _extract_details_blocks,
    build_system_instruction,
    resolve_model,
    strip_review_metadata,
)


MODEL_CONFIG = {
    "flash-lite": {
        "name": "gemini-3.1-flash-lite",
        "aliases": ["gemini-flash-lite-latest", "gemini-flash-lite", "gemini-3-flash-lite"],
        "input_cost_per_1m": 0.25,
        "output_cost_per_1m": 1.50,
        "max_diff_chars": 2000000,
    },
    "flash": {
        "name": "gemini-3.5-flash",
        "aliases": ["gemini-flash-latest", "gemini-flash"],
        "input_cost_per_1m": 1.50,
        "output_cost_per_1m": 9.00,
        "max_diff_chars": 2000000,
    },
    "gemma": {
        "name": "gemma-4-31b-it",
        "aliases": ["gemma-latest", "gemma"],
        "input_cost_per_1m": 0.0,
        "output_cost_per_1m": 0.0,
        "max_diff_chars": 500000,
    },
}


def test_build_system_instruction_includes_cutoff_rule():
    """学習カットオフ起因の誤断定を防ぐため、「要確認」化・断定禁止ルールが含まれる。"""
    instruction = build_system_instruction()
    assert "知識カットオフ" in instruction
    assert "断定しない" in instruction
    assert "要確認" in instruction
    # 極性を固定して検証する: カットオフ起因の指摘は「クリティカルとして扱わない」旨が
    # 明記されていなければならない（「扱う」へ逆転した場合に検知できるよう否定語で断言）
    assert "として扱わないでください" in instruction


def test_build_system_instruction_markers_used_by_flow():
    """レビューの流れで参照される 【✅ APPROVE】/【🔴 CHANGES_REQUESTED】 判定と出力フォーマットが維持されている。"""
    instruction = build_system_instruction()
    assert "【✅ APPROVE】" in instruction
    assert "【🔴 CHANGES_REQUESTED】" in instruction
    assert "**判定**" in instruction
    assert "**要約**" in instruction


def test_build_system_instruction_does_not_assert_and_does_not_miss_repo_evidence():
    """「断定禁止」がリポジトリ内で確認できる根拠の評価まで無効化しない。

    知識カットオフに影響されない根拠（差分内の import 不整合、他ワークフロー
    での同一 Action の利用実績など実リポジトリ由来の事実）は、通常のレビュー
    根拠として扱える旨が明記されていること（過剰な要確認化による偽陰性を防ぐ）。
    """
    instruction = build_system_instruction()
    assert "リポジトリ" in instruction
    assert "差分" in instruction
    # 例外節の極性も固定する: リポジトリ内の根拠は「通常のレビュー根拠として扱い」
    # 評価する旨でなければならない（「扱わない」へ逆転した場合に検知できる）
    assert "通常のレビュー根拠として扱い" in instruction


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
    # 設定キー入力は正規名へ解決される（エイリアスではない）
    info, name = resolve_model("flash-lite", MODEL_CONFIG)
    assert name == "gemini-3.1-flash-lite"
    assert info["input_cost_per_1m"] == 0.25


def test_resolve_model_by_self_alias_returns_alias():
    # "gemini-flash-lite" は flash-lite のエイリアス → 表示はエイリアス自身
    # （本番では API で正規名へ解決される）
    info, name = resolve_model("gemini-flash-lite", MODEL_CONFIG)
    assert name == "gemini-flash-lite"
    assert info["input_cost_per_1m"] == 0.25


def test_resolve_model_exact_alias_does_not_become_unknown():
    # "gemini-flash" は flash の厳密エイリアス → 未知にならない
    info, name = resolve_model("gemini-flash", MODEL_CONFIG)
    assert info["input_cost_per_1m"] == 1.50
    assert name == "gemini-flash"


def test_resolve_model_by_alias():
    info, name = resolve_model("gemini-flash-lite-latest", MODEL_CONFIG)
    # エイリアス指定: 正式名としてそのまま、価格は flash-lite 相当
    assert name == "gemini-flash-lite-latest"
    assert info["input_cost_per_1m"] == 0.25


def test_resolve_model_by_name():
    info, name = resolve_model("gemini-3.5-flash", MODEL_CONFIG)
    assert name == "gemini-3.5-flash"


def test_resolve_model_unknown_keyword_fallback():
    # "gemini-flash-latest-xyz" は flash のエイリアスを一意に含むため flash 相当
    info, name = resolve_model("gemini-flash-latest-xyz", MODEL_CONFIG)
    assert info["input_cost_per_1m"] == 1.50  # flash 相当
    assert name == "gemini-flash-latest-xyz"


def test_resolve_model_ambiguous_input_is_unknown():
    # "gemini" のように複数モデルに曖昧にマッチする入力は意図しない推測を
    # 避けるため未知（コスト不明）として扱う
    info, name = resolve_model("gemini", MODEL_CONFIG)
    assert info["input_cost_per_1m"] is None
    assert info["output_cost_per_1m"] is None
    assert name == "gemini"


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


class _FakeResponse:
    def __init__(self, text="ok"):
        self.text = text


class _CodeError(Exception):
    """HTTP ステータスコード（e.code）を持つ例外のフェイク。"""

    def __init__(self, code):
        super().__init__("some message")
        self.code = code


def _fake_client(*exceptions):
    """指定した順に例外を送出し、その後 _FakeResponse を返す fake client。"""
    return mock.Mock(
        models=mock.Mock(
            generate_content=mock.Mock(side_effect=[*exceptions, _FakeResponse()])
        )
    )


def test_generate_with_retry_succeeds_first_try():
    client = _fake_client()
    resp = ai_review._generate_with_retry(client, "model", "prompt", None)
    assert resp.text == "ok"
    assert client.models.generate_content.call_count == 1


def test_generate_with_retry_retries_429():
    client = _fake_client(Exception("429 Too Many Requests"))
    with mock.patch("ai_review.time.sleep") as mock_sleep, mock.patch(
        "ai_review.random.uniform", return_value=0.5
    ):
        resp = ai_review._generate_with_retry(client, "model", "prompt", None)
    assert resp.text == "ok"
    # 1 回目の失敗で 30s + ジッター 0.5s → 2 回目で成功
    assert client.models.generate_content.call_count == 2
    mock_sleep.assert_called_once_with(30.5)


def test_generate_with_retry_429_exponential_backoff_and_jitter():
    client = _fake_client(
        Exception("429 Too Many Requests"),
        Exception("429 Too Many Requests"),
        Exception("429 Too Many Requests"),
    )
    with mock.patch("ai_review.time.sleep") as mock_sleep, mock.patch(
        "ai_review.random.uniform", return_value=1.0
    ):
        resp = ai_review._generate_with_retry(client, "model", "prompt", None)
    assert resp.text == "ok"
    assert client.models.generate_content.call_count == 4
    # 30s → 60s → 120s（各 + ジッター 1.0s）
    assert mock_sleep.call_args_list == [
        mock.call(31.0),
        mock.call(61.0),
        mock.call(121.0),
    ]


def test_generate_with_retry_retries_5xx():
    client = _fake_client(Exception("503 Service Unavailable"))
    with mock.patch("ai_review.time.sleep") as mock_sleep:
        resp = ai_review._generate_with_retry(client, "model", "prompt", None)
    assert resp.text == "ok"
    assert client.models.generate_content.call_count == 2
    mock_sleep.assert_called_once_with(5)


def test_generate_with_retry_5xx_exponential_backoff():
    client = _fake_client(
        Exception("502 Bad Gateway"),
        Exception("503 Service Unavailable"),
    )
    with mock.patch("ai_review.time.sleep") as mock_sleep:
        resp = ai_review._generate_with_retry(client, "model", "prompt", None)
    assert resp.text == "ok"
    assert client.models.generate_content.call_count == 3
    # 5s → 10s
    assert mock_sleep.call_args_list == [mock.call(5), mock.call(10)]


def test_generate_with_retry_raises_on_non_retryable():
    client = _fake_client(Exception("400 Bad Request"))
    with mock.patch("ai_review.time.sleep") as mock_sleep:
        with pytest.raises(Exception, match="400 Bad Request"):
            ai_review._generate_with_retry(client, "model", "prompt", None)
    assert client.models.generate_content.call_count == 1
    mock_sleep.assert_not_called()


def test_generate_with_retry_raises_after_max_retries():
    client = mock.Mock(
        models=mock.Mock(
            generate_content=mock.Mock(
                side_effect=Exception("503 Service Unavailable")
            )
        )
    )
    with mock.patch("ai_review.time.sleep"):
        with pytest.raises(Exception, match="503 Service Unavailable"):
            ai_review._generate_with_retry(client, "model", "prompt", None)
    # 初回 + 5 回リトライの計 6 試行で打ち切る
    assert client.models.generate_content.call_count == ai_review.MAX_ATTEMPTS


def test_is_retryable_api_error_by_code():
    """HTTP ステータスコード（e.code）でリトライ可否を判定する。"""
    assert ai_review._is_retryable_api_error(_CodeError(429)) == (True, 429)
    assert ai_review._is_retryable_api_error(_CodeError(500)) == (True, 500)
    assert ai_review._is_retryable_api_error(_CodeError(503)) == (True, 503)
    assert ai_review._is_retryable_api_error(_CodeError(400)) == (False, None)


def test_generate_with_retry_429_backoff_capped():
    """429 バックオフは上限（120 秒）で cap される。"""
    client = _fake_client(
        Exception("429 Too Many Requests"),
        Exception("429 Too Many Requests"),
        Exception("429 Too Many Requests"),
        Exception("429 Too Many Requests"),
    )
    with mock.patch("ai_review.time.sleep") as mock_sleep, mock.patch(
        "ai_review.random.uniform", return_value=0.0
    ):
        resp = ai_review._generate_with_retry(client, "model", "prompt", None)
    assert resp.text == "ok"
    # 30 → 60 → 120（cap）→ 120（cap）→ 成功
    assert mock_sleep.call_args_list == [
        mock.call(30.0),
        mock.call(60.0),
        mock.call(120.0),
        mock.call(120.0),
    ]


def test_generate_with_retry_uses_error_code():
    """e.code 属性を持つ例外はコードで判定してリトライする。"""
    client = _fake_client(_CodeError(429))
    with mock.patch("ai_review.time.sleep") as mock_sleep, mock.patch(
        "ai_review.random.uniform", return_value=0.0
    ):
        resp = ai_review._generate_with_retry(client, "model", "prompt", None)
    assert resp.text == "ok"
    assert client.models.generate_content.call_count == 2
    mock_sleep.assert_called_once_with(30)


def test_is_retryable_api_error_message_fallback():
    """code 属性を持たない例外はメッセージ文字列で判定する（フォールバック）。"""
    assert ai_review._is_retryable_api_error(Exception("429 Too Many Requests")) == (
        True,
        429,
    )
    assert ai_review._is_retryable_api_error(Exception("503 Service Unavailable")) == (
        True,
        503,
    )
    # 本文に "500" を含む非リトライエラーはフォールバックでも誤判定しない
    assert ai_review._is_retryable_api_error(
        Exception("quota 5000 exceeded")
    ) == (False, None)


def test_is_retryable_api_error_non_retryable():
    assert ai_review._is_retryable_api_error(Exception("400 Bad Request")) == (
        False,
        None,
    )
    assert ai_review._is_retryable_api_error(ValueError("boom")) == (False, None)


def test_fetch_pr_metadata_returns_title_and_body():
    payload = json.dumps({"title": "feat: 新機能", "body": "概要です"})
    with mock.patch(
        "ai_review.subprocess.check_output", return_value=payload.encode()
    ) as mock_run:
        result = ai_review._fetch_pr_metadata("owner/repo", "42")
    mock_run.assert_called_once_with(
        [
            "gh",
            "pr",
            "view",
            "42",
            "--repo",
            "owner/repo",
            "--json",
            "title,body",
        ]
    )
    assert "タイトル: feat: 新機能" in result
    assert "概要です" in result


def test_fetch_pr_metadata_empty_on_error():
    with mock.patch(
        "ai_review.subprocess.check_output", side_effect=Exception("gh error")
    ):
        assert ai_review._fetch_pr_metadata("owner/repo", "42") == ""


def _comment_api_responses(issue_comments, inline_comments=None, reviews=None):
    """_fetch_pr_comments 用の API レスポンスモック。

    3 つのエンドポイント（issues comments / pulls comments / pulls reviews）を
    呼び出し順に返す。コマンドは ["gh", "api", "--paginate", "URL"] なので
    URL は cmd[3]。
    """
    inline_comments = inline_comments or []
    reviews = reviews or []

    def fake_run(cmd, **kwargs):
        url = cmd[3]
        if "issues/" in url:
            return json.dumps(issue_comments).encode()
        if "pulls/" in url and "/comments" in url:
            return json.dumps(inline_comments).encode()
        if "pulls/" in url and "/reviews" in url:
            return json.dumps(reviews).encode()
        raise Exception(f"unexpected url: {url}")

    return fake_run


def test_fetch_pr_comments_skips_bot_marker():
    payload = json.dumps(
        [
            {"user": {"login": "bot"}, "body": "<!-- ai-pr-reviewer-comment -->既存レビュー"},
            {"user": {"login": "alice"}, "body": "修正しました"},
            {"user": {"login": "bob"}, "body": "   "},
        ]
    )
    with mock.patch(
        "ai_review.subprocess.check_output", side_effect=_comment_api_responses(
            issue_comments=json.loads(payload)
        )
    ) as mock_run:
        result, existing = ai_review._fetch_pr_comments("owner/repo", "42")
    assert "bot" not in result
    assert "既存レビュー" not in result
    assert "alice" in result
    assert "修正しました" in result
    # bot のマーカーコメントは更新対象として返す
    assert existing is not None
    assert existing["user"]["login"] == "bot"
    # ページネーションで全コメントを取得する
    assert "--paginate" in mock_run.call_args_list[0][0][0]


def test_fetch_pr_comments_returns_existing_comment():
    """bot のマーカーコメントを existing_comment として返す。"""
    issue_comments = [
        {"id": 1, "user": {"login": "alice"}, "body": "確認します"},
        {"id": 2, "user": {"login": "bot"}, "body": "<!-- ai-pr-reviewer-comment -->前回レビュー"},
    ]
    with mock.patch(
        "ai_review.subprocess.check_output",
        side_effect=_comment_api_responses(issue_comments=issue_comments),
    ):
        result, existing = ai_review._fetch_pr_comments("owner/repo", "42")
    assert "確認します" in result
    assert "前回レビュー" not in result
    assert existing == {"id": 2, "user": {"login": "bot"}, "body": "<!-- ai-pr-reviewer-comment -->前回レビュー"}


def test_fetch_pr_comments_includes_inline_and_reviews():
    """インラインコードレビューコメントとレビュー本体も取得する。"""
    issue_comments = [
        {"id": 1, "user": {"login": "alice"}, "body": "トップレベルコメント"},
        {"id": 2, "user": {"login": "bot"}, "body": "<!-- ai-pr-reviewer-comment -->既存レビュー"},
    ]
    inline_comments = [
        {"user": {"login": "bob"}, "body": "ここが問題", "path": "src/app.py", "line": 42},
        {"user": {"login": "bot"}, "body": "<!-- ai-pr-reviewer-comment -->既存"},
    ]
    reviews = [
        {"user": {"login": "carol"}, "body": "レビューサマリ", "state": "APPROVED"},
        {"user": {"login": "bot"}, "body": "<!-- ai-pr-reviewer-comment -->既存レビュー"},
    ]
    with mock.patch(
        "ai_review.subprocess.check_output",
        side_effect=_comment_api_responses(
            issue_comments=issue_comments,
            inline_comments=inline_comments,
            reviews=reviews,
        ),
    ):
        result, existing = ai_review._fetch_pr_comments("owner/repo", "42")
    assert "トップレベルコメント" in result
    assert "bob" in result
    assert "src/app.py:42" in result
    assert "ここが問題" in result
    assert "carol" in result
    assert "APPROVED" in result
    assert "レビューサマリ" in result
    # bot のマーカーコメントは既存として返す（ユーザーコメントには含めない）
    assert existing is not None
    assert existing["id"] == 2
    assert "既存" not in result
    assert "既存レビュー" not in result


def test_fetch_pr_comments_inline_line_fallback():
    """インラインコメントの line が None の場合、original_line にフォールバックする。"""
    issue_comments = []
    inline_comments = [
        {"user": {"login": "bob"}, "body": "コメント", "path": "src/app.py", "line": None, "original_line": 10},
        {"user": {"login": "carol"}, "body": "パスなしコメント", "path": "", "line": None},
    ]
    reviews = []
    with mock.patch(
        "ai_review.subprocess.check_output",
        side_effect=_comment_api_responses(
            issue_comments=issue_comments,
            inline_comments=inline_comments,
            reviews=reviews,
        ),
    ):
        result, existing = ai_review._fetch_pr_comments("owner/repo", "42")
    assert "src/app.py:10" in result
    assert "インライン" in result
    assert existing is None


def test_fetch_pr_comments_empty_on_error():
    with mock.patch(
        "ai_review.subprocess.check_output", side_effect=Exception("gh error")
    ):
        assert ai_review._fetch_pr_comments("owner/repo", "42") == ("", None)


def test_fetch_pr_comments_partial_failure_still_returns_issue_comments():
    """インライン/レビュー取得が失敗しても、Issue コメントは返す。"""
    issue_comments = [{"user": {"login": "alice"}, "body": "トップレベル"}]
    call_count = 0

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return json.dumps(issue_comments).encode()
        raise Exception("inline fetch failed")

    with mock.patch("ai_review.subprocess.check_output", side_effect=fake_run):
        result, existing = ai_review._fetch_pr_comments("owner/repo", "42")
    assert "トップレベル" in result
    assert existing is None
    # 部分失敗の注記が含まれる
    assert "インラインコードレビューコメントの取得に失敗" in result
    assert "レビュー本体の取得に失敗" in result


def test_fetch_pr_comments_partial_failure_notes_only_failed_endpoint():
    """インラインのみ失敗した場合、レビュー本体の注記は付かない。"""
    issue_comments = [{"user": {"login": "alice"}, "body": "トップレベル"}]
    reviews = [{"user": {"login": "carol"}, "body": "サマリ", "state": "APPROVED"}]
    call_count = 0

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return json.dumps(issue_comments).encode()
        if call_count == 2:
            raise Exception("inline fetch failed")
        return json.dumps(reviews).encode()

    with mock.patch("ai_review.subprocess.check_output", side_effect=fake_run):
        result, existing = ai_review._fetch_pr_comments("owner/repo", "42")
    assert "インラインコードレビューコメントの取得に失敗" in result
    assert "レビュー本体の取得に失敗" not in result
    assert "サマリ" in result


def _git_show_responses(contents):
    """_fetch_file_contents 用の git レスポンスモック。

    - fetch: ["git", "fetch", "origin", "refs/pull/42/head:refs/remotes/pull/42/head"]
      → 成功を返す
    - show: ["git", "show", "refs/remotes/pull/42/head:{path}"] → パスに対応する内容
    """

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git" and cmd[1] == "fetch":
            return b""
        spec = cmd[2]
        for path, data in contents.items():
            if spec == f"refs/remotes/pull/42/head:{path}":
                return data.encode()
        raise Exception(f"not found: {spec}")

    return fake_run


def test_fetch_file_contents_includes_all_within_limit():
    paths = ["a.py", "b.py"]
    contents = {"a.py": "print('a')", "b.py": "print('b')"}

    with mock.patch(
        "ai_review.subprocess.check_output", side_effect=_git_show_responses(contents)
    ) as mock_run:
        result = ai_review._fetch_file_contents("42", paths, limit=100000)
    assert "### a.py" in result
    assert "print('a')" in result
    assert "### b.py" in result
    assert "print('b')" in result
    assert mock_run.call_count == 3  # fetch 1 + show 2


def test_fetch_file_contents_truncates_when_over_limit():
    paths = ["big.py", "small.py"]
    contents = {"big.py": "x" * 300, "small.py": "y"}

    with mock.patch(
        "ai_review.subprocess.check_output", side_effect=_git_show_responses(contents)
    ) as mock_run:
        result = ai_review._fetch_file_contents("42", paths, limit=400)
    # big.py (300) + small.py (1) = 301 < 400 なので両方含まれる
    assert "### big.py" in result
    assert "### small.py" in result
    assert "省略" not in result
    assert mock_run.call_count == 3  # fetch 1 + show 2


def test_fetch_file_contents_budget_exceeded_truncates_head():
    paths = ["big.py", "small.py"]
    contents = {"big.py": "x" * 300, "small.py": "y"}

    with mock.patch(
        "ai_review.subprocess.check_output", side_effect=_git_show_responses(contents)
    ) as mock_run:
        result = ai_review._fetch_file_contents("42", paths, limit=250)
    # 合計超過時: ヘッダは全文保持され、big.py は先頭トランケート、small.py は全文含まれる
    assert "### big.py" in result
    assert "省略: バッファ上限超過" in result
    assert "### small.py" in result
    assert "y" in result
    assert "x" * 300 not in result
    assert mock_run.call_count == 3  # fetch 1 + show 2


def test_fetch_file_contents_greedy_reallocates_to_large_files():
    """小さいファイルの余剰枠が大きいファイルへ再分配される。

    実測（limit=200）: 均等枠のみなら large.py 本文は 70 文字。
    greedy 再分配後は large.py 本文が 130 文字になる（出力全体の 'l' 数は
    small.py パスの 'l' を含めて 133）。閾値 100 で両者を確実に弁別できる。
    """
    paths = ["small.py", "large.py"]
    # small は 10 文字、large は 1000 文字。limit=200 では均等枠は 70 程度。
    contents = {"small.py": "s" * 10, "large.py": "l" * 1000}

    with mock.patch(
        "ai_review.subprocess.check_output", side_effect=_git_show_responses(contents)
    ):
        result = ai_review._fetch_file_contents("42", paths, limit=200)
    # small.py は全文採用（10 文字）され、余剰が large.py に再分配される
    assert "### small.py" in result
    assert "s" * 10 in result
    assert "### large.py" in result
    # 再分配なし（出力全体の 'l' 数は 73）では通らない閾値で、再分配を実証する
    assert result.count("l") > 100
    assert len(result) <= 200


def test_fetch_file_contents_skips_missing_and_empty():
    paths = ["missing.py", "empty.py"]
    contents = {"empty.py": "   "}

    with mock.patch(
        "ai_review.subprocess.check_output", side_effect=_git_show_responses(contents)
    ) as mock_run:
        result = ai_review._fetch_file_contents("42", paths, limit=1000)
    assert "### missing.py" not in result
    assert "### empty.py" not in result
    assert result == ""
    assert mock_run.call_count == 3  # fetch 1 + show 2


def test_fetch_file_contents_total_stays_within_budget():
    """ファイル数が多くても合計出力はバッファ上限を超えない。"""
    n_files = 50
    paths = [f"file_{i}.py" for i in range(n_files)]
    contents = {f"file_{i}.py": "z" * 1000 for i in range(n_files)}

    limit = 2000
    with mock.patch(
        "ai_review.subprocess.check_output", side_effect=_git_show_responses(contents)
    ):
        result = ai_review._fetch_file_contents("42", paths, limit=limit)
    # 全ファイルのヘッダは保持され、合計は上限以内
    assert "### file_0.py" in result
    assert "### file_49.py" in result
    assert len(result) <= limit


def test_fetch_file_contents_tiny_budget_never_exceeds():
    """本文枠が 0 になる極端なケース（ヘッダだけで上限に近い）でも上限を超えない。"""
    n_files = 10
    paths = [f"dir/very/long/path/file_{i}.py" for i in range(n_files)]
    contents = {f"dir/very/long/path/file_{i}.py": "x" * 500 for i in range(n_files)}

    # ヘッダ（約 30 文字 × 10）で 300 文字。limit=100 では本文枠が 0 になる
    limit = 100
    with mock.patch(
        "ai_review.subprocess.check_output", side_effect=_git_show_responses(contents)
    ):
        result = ai_review._fetch_file_contents("42", paths, limit=limit)
    assert len(result) <= limit


def test_fetch_file_contents_separator_counted_in_budget():
    """join セパレータ（\n\n）もバッファ上限に計上される。

    entries 合計 = (11+116) + (11+117) = 255。limit=256 では旧実装（セパレータ未計上、
    255 <= 256 で高速経路→257 文字返却）なら違反になる境界値。セパレータ計上
    （257 > 256 → トランケート経路）なら上限内に収まる。
    """
    paths = ["a.py", "b.py"]
    contents = {"a.py": "x" * 116, "b.py": "y" * 117}

    limit = 256
    with mock.patch(
        "ai_review.subprocess.check_output", side_effect=_git_show_responses(contents)
    ):
        result = ai_review._fetch_file_contents("42", paths, limit=limit)
    assert len(result) <= limit


def test_fetch_file_contents_fetch_failure_falls_back():
    """fetch 失敗（CalledProcessError）でも警告のみで続行し、show は試みる。"""
    paths = ["a.py"]
    contents = {"a.py": "content a"}
    call_count = 0

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if cmd[0] == "git" and cmd[1] == "fetch":
            raise subprocess.CalledProcessError(
                128, cmd, stderr=b"fatal: couldn't find remote ref"
            )
        spec = cmd[2]
        for path, data in contents.items():
            if spec == f"refs/remotes/pull/42/head:{path}":
                return data.encode()
        raise Exception(f"not found: {spec}")

    with mock.patch("ai_review.subprocess.check_output", side_effect=fake_run) as mock_run:
        result = ai_review._fetch_file_contents("42", paths, limit=1000)
    assert "### a.py" in result
    assert "content a" in result
    assert mock_run.call_count == 2  # fetch 失敗 1 + show 1


def test_fetch_file_contents_fetch_oserror_does_not_abort():
    """fetch が OSError（git 不在等）でも abort せず show に進む。"""
    paths = ["a.py"]
    contents = {"a.py": "content a"}

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git" and cmd[1] == "fetch":
            raise OSError("git binary not found")
        spec = cmd[2]
        for path, data in contents.items():
            if spec == f"refs/remotes/pull/42/head:{path}":
                return data.encode()
        raise Exception(f"not found: {spec}")

    with mock.patch("ai_review.subprocess.check_output", side_effect=fake_run):
        result = ai_review._fetch_file_contents("42", paths, limit=1000)
    assert "### a.py" in result
    assert "content a" in result


def test_fetch_file_contents_counts_header_in_budget():
    """ヘッダ行・区切り文字もバッファ上限に計上される。"""
    paths = ["big.py"]
    contents = {"big.py": "x" * 300}

    # 本文 300 + ヘッダ "### big.py\n" (11) = 311 > 250 なのでトランケートされる
    with mock.patch(
        "ai_review.subprocess.check_output", side_effect=_git_show_responses(contents)
    ):
        result = ai_review._fetch_file_contents("42", paths, limit=250)
    assert "省略" in result
    # トランケートされるため、元の 300 文字全部は含まれない（先頭部分は残る）
    assert "x" * 300 not in result
    assert "x" in result
    assert len(result) <= 250


# ---------------------------------------------------------------------------
# Thinking Config & Execution Metadata Tests
# ---------------------------------------------------------------------------


def test_build_thinking_config_defaults_to_high():
    """未設定時はデフォルトで最大レベル HIGH の ThinkingConfig が生成される。"""
    with mock.patch.dict(os.environ, {}, clear=True):
        config = ai_review._build_thinking_config({})
        assert config is not None
        assert str(config.thinking_level).upper().endswith("HIGH")


def test_build_thinking_config_uses_model_info():
    """models.json に thinking_level がある場合はそのレベルが使われる。"""
    with mock.patch.dict(os.environ, {}, clear=True):
        config_high = ai_review._build_thinking_config({"thinking_level": "HIGH"})
        assert config_high is not None
        assert str(config_high.thinking_level).upper().endswith("HIGH")

        config_med = ai_review._build_thinking_config({"thinking_level": "MEDIUM"})
        assert config_med is not None
        assert str(config_med.thinking_level).upper().endswith("MEDIUM")


def test_build_thinking_config_disabled_for_unsupported_models():
    """thinking_level が null または false のモデル（Gemma 等）は None を返す。"""
    with mock.patch.dict(os.environ, {}, clear=True):
        config_none = ai_review._build_thinking_config({"thinking_level": None})
        assert config_none is None

        config_false = ai_review._build_thinking_config({"thinking_level": False})
        assert config_false is None

        config_off = ai_review._build_thinking_config({"thinking_level": "OFF"})
        assert config_off is None


def test_build_thinking_config_env_override():
    """環境変数 GEMINI_THINKING_LEVEL が最優先で適用される。"""
    with mock.patch.dict(os.environ, {"GEMINI_THINKING_LEVEL": "LOW"}):
        config = ai_review._build_thinking_config({"thinking_level": "HIGH"})
        assert config is not None
        assert str(config.thinking_level).upper().endswith("LOW")

    with mock.patch.dict(os.environ, {"GEMINI_THINKING_LEVEL": "OFF"}):
        config = ai_review._build_thinking_config({"thinking_level": "HIGH"})
        assert config is None


def test_get_thinking_level_display():
    """ThinkingConfig から表示用レベル文字列が取得できる。"""
    assert ai_review._get_thinking_level_display(None) is None
    cfg = ai_review._build_thinking_config({"thinking_level": "HIGH"})
    assert ai_review._get_thinking_level_display(cfg) == "HIGH"


def test_build_execution_metadata_comprehensive():
    """実行メタデータにモデル、実バージョン、思考レベル、変更規模、コミットリンク、Actions ログが含まれる。"""
    mock_usage = mock.MagicMock()
    mock_usage.prompt_token_count = 10000
    mock_usage.candidates_token_count = 1500
    mock_usage.thoughts_token_count = 1000

    mock_resp = mock.MagicMock()
    mock_resp.model_version = "gemini-2.5-flash-001"
    mock_resp.usage_metadata = mock_usage
    mock_resp._retry_count = 1

    model_info = {
        "name": "gemini-flash-latest",
        "input_cost_per_1m": 1.50,
        "output_cost_per_1m": 9.00,
        "thinking_level": "HIGH",
    }
    thinking_cfg = ai_review._build_thinking_config(model_info)

    metadata = ai_review.build_execution_metadata(
        model_name="gemini-flash-latest",
        resolved_model_name="gemini-2.5-flash",
        model_info=model_info,
        response=mock_resp,
        thinking_config=thinking_cfg,
        files_modified_count=3,
        lines_added=120,
        lines_deleted=45,
        duration=4.5,
        repo="JoichiroAkimoto/HW-Genie",
        server_url="https://github.com",
        run_id="123456",
        commit_sha="a1b2c3d",
    )

    assert "⚡ 今回の実行情報" in metadata
    # モデル名と実バージョンと正規名
    assert "gemini-flash-latest" in metadata
    assert "gemini-2.5-flash-001" in metadata
    assert "思考: `HIGH`" in metadata
    # 対象コミットリンクと Actions ログリンク
    assert "https://github.com/JoichiroAkimoto/HW-Genie/commit/a1b2c3d" in metadata
    assert "https://github.com/JoichiroAkimoto/HW-Genie/actions/runs/123456" in metadata
    # 変更規模
    assert "3 ファイル (+120 / -45 行)" in metadata
    # トークン内訳（思考トークン含む）
    assert "うち思考=`1,000`" in metadata
    # コスト計算 ($0.015000 + $0.013500 = $0.028500)
    assert "$0.028500" in metadata
    # リトライ回数
    assert "APIリトライ" in metadata
    assert "1 回" in metadata


def test_build_execution_metadata_unknown_cost_and_no_thinking():
    """Thinking なし、コスト不明モデルでの表示が正しく処理される。"""
    mock_resp = mock.MagicMock()
    mock_resp.model_version = None
    mock_resp.usage_metadata = None
    mock_resp._retry_count = 0

    model_info = {
        "name": "custom-model",
        "input_cost_per_1m": None,
        "output_cost_per_1m": None,
    }

    metadata = ai_review.build_execution_metadata(
        model_name="custom-model",
        resolved_model_name="custom-model",
        model_info=model_info,
        response=mock_resp,
        thinking_config=None,
        files_modified_count="N/A",
        duration=2.0,
    )

    assert "`custom-model`" in metadata
    assert "思考" not in metadata
    assert "(取得できませんでした)" in metadata



# ---------------------------------------------------------------------------
# Context7 integration tests
# ---------------------------------------------------------------------------


def test_has_dep_file_matches_toml_lock_requirements():
    """依存関係ファイル (.toml / .lock / requirements*) の検知。"""
    assert ai_review._has_dep_file(["pyproject.toml"]) is True
    assert ai_review._has_dep_file(["src/python/uv.lock"]) is True
    assert ai_review._has_dep_file(["requirements.txt"]) is True
    assert ai_review._has_dep_file(["requirements-ai.txt"]) is True
    assert ai_review._has_dep_file(["src/foo.py", "pyproject.toml"]) is True
    # 依存関係ファイルを含まない
    assert ai_review._has_dep_file(["src/foo.py"]) is False
    assert ai_review._has_dep_file([]) is False
    assert ai_review._has_dep_file(None) is False


def test_extract_libraries_from_dependency_name_marker():
    """Dependabot 形式 `dependency-name: xyz` から抽出できる。"""
    diff = """
    some context
    dependency-name: "pytest-cov"
    other: foo
    """
    libs = ai_review._extract_libraries_from_diff(diff, [".github/dependabot.yml"])
    assert libs == ["pytest-cov"]


def test_extract_libraries_skips_empty_numeric_and_common_words():
    """空 / 数字のみ / 一般的すぎる単語は除外される。"""
    diff = """
    dependency-name: "123"
    dependency-name: "fix"
    dependency-name: "1.2.3"
    """
    libs = ai_review._extract_libraries_from_diff(diff, [".github/dependabot.yml"])
    assert libs == []


def test_extract_libraries_toml_quoted_only_when_dep_file_changed():
    """pyproject.toml / uv.lock 変更時のみ quoted-library パターンが有効。"""
    diff = '+ "pytest-cov"\n+ "ruff"\n'
    # dep file が無いと quoted パターンは適用されない
    assert ai_review._extract_libraries_from_diff(diff, ["src/foo.py"]) == []
    libs = ai_review._extract_libraries_from_diff(diff, ["pyproject.toml"])
    assert libs == ["pytest-cov", "ruff"]


def test_extract_libraries_excludes_stdlib_from_toml_pattern():
    """quoted パターンは stdlib 名を除外する（誤検出防止）。"""
    diff = '+ "json"\n+ "os"\n+ "httpx"\n'
    libs = ai_review._extract_libraries_from_diff(diff, ["pyproject.toml"])
    assert "json" not in libs
    assert "os" not in libs
    assert "httpx" in libs


def test_extract_libraries_pep621_quoted_format():
    """pyproject.toml の PEP 621 クォート形式 `+ "library>=version"` は抽出する。
    ベアな `+ key = "value"` 形式はブランチを削除済みのため抽出されない
    （`minversion = "6.0"` 等の設定キーとの区別が不可能なため）。
    """
    diff = (
        '+ "pytest>=8.0.0"\n'
        '+ "ruff>=0.6.0"\n'
    )
    libs = ai_review._extract_libraries_from_diff(diff, ["pyproject.toml"])
    # PEP 621 形式では抽出される
    assert "pytest" in libs
    assert "ruff" in libs


def test_extract_libraries_import_fallback_only_without_dep_libs():
    """依存関係由来が無いときのみ import 文から抽出する。"""
    # 依存ライブラリが見つかった場合、import パターンは無視される
    diff_with_dep = (
        'dependency-name: "pytest-cov"\n'
        "+import json\n"
        "+from pathlib import Path\n"
        "+import httpx\n"
    )
    libs = ai_review._extract_libraries_from_diff(diff_with_dep, [".github/dependabot.yml"])
    assert "httpx" not in libs
    assert libs == ["pytest-cov"]

    # 依存ライブラリが無い場合は import 由来を採用
    diff_import_only = "+import httpx\n+from pathlib import Path\n"
    libs = ai_review._extract_libraries_from_diff(diff_import_only, ["src/foo.py"])
    assert "httpx" in libs
    assert "pathlib" not in libs  # stdlib 除外


def test_extract_libraries_respects_max_libraries():
    """最大 CONTEXT7_MAX_LIBRARIES 件までに制限される。"""
    diff = "\n".join(f'dependency-name: "lib{i}"' for i in range(10))
    libs = ai_review._extract_libraries_from_diff(diff, [".github/dependabot.yml"])
    assert len(libs) == ai_review.CONTEXT7_MAX_LIBRARIES
    assert len(libs) == 3


def test_extract_libraries_empty_diff():
    """空 diff では空リストを返す。"""
    assert ai_review._extract_libraries_from_diff("", ["pyproject.toml"]) == []
    assert ai_review._extract_libraries_from_diff("   \n", []) == []


def test_parse_mcp_body_plain_json():
    """通常の JSON レスポンスをパースする。"""
    body = '{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
    parsed = ai_review._parse_mcp_body(body)
    assert parsed == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}


def test_parse_mcp_body_sse_format():
    """SSE 形式 (data: {...}) をパースする。"""
    body = (
        'event: message\n'
        'data: {"jsonrpc":"2.0","id":1,"result":{"foo":"bar"}}\n\n'
    )
    parsed = ai_review._parse_mcp_body(body)
    assert parsed == {"jsonrpc": "2.0", "id": 1, "result": {"foo": "bar"}}


def test_parse_mcp_body_sse_with_done_marker():
    """SSE 末尾の [DONE] マーカーはスキップされる。"""
    body = (
        'data: {"jsonrpc":"2.0","id":1,"result":{"v":1}}\n\n'
        'data: [DONE]\n\n'
    )
    parsed = ai_review._parse_mcp_body(body)
    assert parsed == {"jsonrpc": "2.0", "id": 1, "result": {"v": 1}}


def test_parse_mcp_body_invalid_returns_none():
    """不正な JSON / 空文字は None を返す。"""
    assert ai_review._parse_mcp_body("") is None
    assert ai_review._parse_mcp_body("not json {") is None
    assert ai_review._parse_mcp_body("   ") is None


def test_is_valid_context7_id_accepts_valid():
    """正しい /org/name 形式を受け入れる。"""
    assert ai_review._is_valid_context7_id("/pytest-dev/pytest") is True
    assert ai_review._is_valid_context7_id("/pydantic/pydantic") is True


def test_is_valid_context7_id_rejects_filesystem_paths():
    """ファイルパスは除外される。"""
    assert ai_review._is_valid_context7_id("/usr/local/bin") is False
    assert ai_review._is_valid_context7_id("/etc/passwd") is False
    assert ai_review._is_valid_context7_id("/home/user/repo") is False
    assert ai_review._is_valid_context7_id("/opt/whatever") is False


def test_is_valid_context7_id_rejects_malformed():
    """セグメント数や文字種の不正な形式を拒否する。"""
    assert ai_review._is_valid_context7_id("pytest-dev/pytest") is False  # 先頭 /
    assert ai_review._is_valid_context7_id("/only-one") is False  # 1 セグメント
    assert ai_review._is_valid_context7_id("/a/b/c/d/e") is False  # 多すぎ
    assert ai_review._is_valid_context7_id("/a/b$c") is False  # 禁止文字
    assert ai_review._is_valid_context7_id("") is False


def test_extract_library_id_from_structured_content():
    """structuredContent.libraryId から直接抽出できる。"""
    result = {"structuredContent": {"libraryId": "/pytest-dev/pytest"}}
    assert ai_review._extract_library_id_from_mcp_result(result) == "/pytest-dev/pytest"


def test_extract_library_id_from_blob_fallback():
    """structuredContent がない場合、JSON blob 全体から抽出する。"""
    result = {"content": [{"text": 'See /pydantic/pydantic for details'}]}
    assert ai_review._extract_library_id_from_mcp_result(result) == "/pydantic/pydantic"


def test_extract_library_id_returns_none_for_invalid():
    """不正な ID は None を返す。"""
    assert ai_review._extract_library_id_from_mcp_result(None) is None
    assert ai_review._extract_library_id_from_mcp_result({}) is None
    assert ai_review._extract_library_id_from_mcp_result({"structuredContent": {"libraryId": "/etc/passwd"}}) is None


def test_extract_docs_from_content_list():
    """content[].text から結合して返す。"""
    result = {"content": [{"text": "hello"}, {"text": "world"}]}
    assert ai_review._extract_docs_from_mcp_result(result) == "hello\n\nworld"


def test_extract_docs_from_structured_content():
    """structuredContent.docs もサポートする。"""
    result = {"structuredContent": {"docs": "documentation text"}}
    assert ai_review._extract_docs_from_mcp_result(result) == "documentation text"


def test_extract_docs_returns_none_for_empty():
    """ドキュメントが見つからない場合 None を返す。"""
    assert ai_review._extract_docs_from_mcp_result(None) is None
    assert ai_review._extract_docs_from_mcp_result({}) is None
    assert ai_review._extract_docs_from_mcp_result({"content": []}) is None
    assert ai_review._extract_docs_from_mcp_result({"structuredContent": {"docs": ""}}) is None


def test_fetch_context7_docs_empty_libraries():
    """空 libraries では空文字を返す（ネットワークコールしない）。"""
    assert ai_review._fetch_context7_docs([], timeout=5.0) == ""


def test_fetch_context7_docs_merges_and_truncates_to_budget():
    """複数ライブラリの結果を結合し、TOTAL_BUDGET で切る。"""
    fake_doc_a = "A" * 5000
    fake_doc_b = "B" * 5000

    def fake_fetch(lib, query, api_key, timeout):
        if lib == "lib-a":
            return fake_doc_a
        if lib == "lib-b":
            return fake_doc_b
        return None

    with mock.patch.object(ai_review, "_fetch_context7_for_library", side_effect=fake_fetch):
        result = ai_review._fetch_context7_docs(["lib-a", "lib-b"], timeout=5.0)
    assert len(result) <= ai_review.CONTEXT7_TOTAL_BUDGET
    assert "### lib-a" in result
    assert "### lib-b" in result


def test_fetch_context7_docs_skips_libraries_with_no_docs():
    """doc が None のライブラリはスキップされる。"""
    def fake_fetch(lib, query, api_key, timeout):
        return None if lib == "lib-empty" else "ok docs"

    with mock.patch.object(ai_review, "_fetch_context7_for_library", side_effect=fake_fetch):
        result = ai_review._fetch_context7_docs(["lib-empty", "lib-good"], timeout=5.0)
    assert "lib-empty" not in result
    assert "### lib-good" in result


def test_fetch_context7_docs_per_lib_truncation():
    """per-lib 上限が効く（header 込みで CONTEXT7_MAX_CHARS_PER_LIB 以下）。"""
    big = "X" * 10000  # 上限 3000 を超える

    def fake_fetch(lib, query, api_key, timeout):
        return big

    with mock.patch.object(ai_review, "_fetch_context7_for_library", side_effect=fake_fetch):
        result = ai_review._fetch_context7_docs(["lib-big"], timeout=5.0)
    # header "### lib-big\n" + body (3000 - header 程度) でおさまる
    # 余裕を見て CONTENT 全体でも per-lib 上限 + ヘッダ長以下であるべき
    assert len(result) <= ai_review.CONTEXT7_MAX_CHARS_PER_LIB + len("### lib-big\n")


# ---------------------------------------------------------------------------
# _extract_libraries_from_diff / _parse_mcp_body の回帰テスト
# ---------------------------------------------------------------------------


def test_extract_libraries_pep621_versioned_deps():
    """PEP 621 の versioned dependencies（+ "httpx>=0.27", など）も抽出できる。"""
    diff = '+ "httpx>=0.27",\n+ "pydantic>=2.0",\n+ "ruff==0.6.0"\n'
    result = ai_review._extract_libraries_from_diff(diff, ["pyproject.toml"])
    assert result == ["httpx", "pydantic", "ruff"]


def test_extract_libraries_uv_lock():
    """uv.lock の name = "library" から value（ライブラリ名）を抽出する。"""
    diff = '+ name = "httpx"\n+ version = "0.27.0"\n+ name = "pydantic"\n+ version = "2.0"\n'
    result = ai_review._extract_libraries_from_diff(diff, ["uv.lock"])
    assert result == ["httpx", "pydantic"]


def test_extract_libraries_uv_lock_dedup():
    """uv.lock 内で同じライブラリが複数回現れても重複排除される。"""
    diff = (
        '+ name = "httpx"\n'
        '+ version = "0.27.0"\n'
        '+ name = "httpx"\n'
        '+ version = "0.27.1"\n'
    )
    result = ai_review._extract_libraries_from_diff(diff, ["uv.lock"])
    assert result == ["httpx"]


def test_extract_libraries_requirements_txt():
    """requirements.txt の + httpx>=0.27 形式も抽出できる（json は stdlib なので除外）。"""
    diff = "+ httpx>=0.27\n+ pydantic==2.0\n+ json\n"
    result = ai_review._extract_libraries_from_diff(diff, ["requirements.txt"])
    assert result == ["httpx", "pydantic"]


def test_extract_libraries_pep621_quoted_excludes_stdlib():
    """PEP 621 クォート形式で stdlib（json）は除外される。
    ベアな `+ key = "value"` ブランチは削除済み。
    """
    diff = '+ "json"\n+ "httpx>=0.27"\n'
    result = ai_review._extract_libraries_from_diff(diff, ["pyproject.toml"])
    assert "json" not in result
    assert "httpx" in result


def test_parse_mcp_body_json_with_data_substring_in_payload():
    """本文中に \\ndata: を含む JSON は SSE と誤判定せず JSON としてパースされる。

    body 内の \\n は JSON 文字列のエスケープ（バックスラッシュ + n の 2 文字）で、
    パース後は実際の改行になる。
    """
    body = r'{"text": "line1\ndata: not SSE"}'
    result = ai_review._parse_mcp_body(body)
    assert result == {"text": "line1\ndata: not SSE"}


def test_parse_mcp_body_sse_first_line_marker():
    """先頭行が data: で始まる SSE は JSON としてパースされる。"""
    body = 'data: {"v": 1}\n\n'
    result = ai_review._parse_mcp_body(body)
    assert result == {"v": 1}


def test_parse_mcp_body_sse_does_not_match_arbitrary_substring():
    """JSON 値内の "data:" 部分文字列だけでは SSE と判定しない。"""
    body = '{"x": "data: foo"}'
    result = ai_review._parse_mcp_body(body)
    assert result == {"x": "data: foo"}


# ---------------------------------------------------------------------------
# uv.lock extraction edge cases
# ---------------------------------------------------------------------------


def test_extract_libraries_uv_lock_skips_project_name():
    """uv.lock の最上位の + name = "..." はプロジェクト自身の名前なので
    抽出から除外する。[[package]] 内の name のみ拾う。"""
    diff = (
        '+name = "my-project"\n'
        '+version = "0.1.0"\n'
        '+\n'
        '+[[package]]\n'
        '+name = "httpx"\n'
        '+version = "0.27"\n'
        '+[[package]]\n'
        '+name = "anyio"\n'
    )
    result = ai_review._extract_libraries_from_diff(diff, ["uv.lock"])
    assert "my-project" not in result, f"project name leaked: {result}"
    assert "httpx" in result
    assert "anyio" in result
    # 順序は保持される
    assert result.index("httpx") < result.index("anyio")


def test_extract_libraries_uv_lock_no_package_marker():
    """+[[package]] マーカーが diff に無い場合はフォールバックで
    すべての + name = "..." を抽出する。"""
    diff = '+ name = "httpx"\n+ name = "pydantic"\n'
    result = ai_review._extract_libraries_from_diff(diff, ["uv.lock"])
    assert "httpx" in result
    assert "pydantic" in result


def test_extract_libraries_toml_key_value_branch_removed_or_restricted():
    """pyproject.toml の [tool.pytest.ini_options] 等の設定キー
    (minversion / addopts / target-version) は依存ではないため抽出されない。
    ベアな `+ key = "value"` ブランチは偽陽性過多のため削除済み。"""
    diff = (
        '+ minversion = "6.0"\n'
        '+ addopts = "-ra -q"\n'
        '+ target-version = "py310"\n'
        '+ httpx = "0.27"\n'
    )
    result = ai_review._extract_libraries_from_diff(diff, ["pyproject.toml"])
    for false_positive in ("minversion", "addopts", "target-version"):
        assert false_positive not in result, (
            f"false positive '{false_positive}' in result: {result}"
        )


def test_parse_mcp_body_sse_with_comment_line():
    """SSE のコメント行（":" 始まり）で本文が始まっても、続く data: 行を
    正しく検出して JSON としてパースする。"""
    body = ': keep-alive\ndata: {"v": 1}\n\n'
    result = ai_review._parse_mcp_body(body)
    assert result == {"v": 1}, f"expected SSE detection, got {result!r}"


# ---------------------------------------------------------------------------
# uv.lock project source markers
# ---------------------------------------------------------------------------


def test_extract_libraries_uv_lock_project_in_first_package_block():
    """uv.lock ではプロジェクト自身が最初の [[package]] ブロックに
    `+source = { virtual = "." }` 付きで表れる。その name は依存ではないので
    スキップし、続く依存の name のみを抽出する。"""
    diff = (
        '+[[package]]\n'
        '+name = "hw-genie"\n'
        '+version = "0.1.0"\n'
        '+source = { virtual = "." }\n'
        '+\n'
        '+[[package]]\n'
        '+name = "httpx"\n'
        '+version = "0.27"\n'
        '+\n'
        '+[[package]]\n'
        '+name = "anyio"\n'
        '+version = "4.0"\n'
    )
    result = ai_review._extract_libraries_from_diff(diff, ["uv.lock"])
    assert "hw-genie" not in result, f"project name leaked: {result}"
    assert "httpx" in result
    assert "anyio" in result
    assert result == ["httpx", "anyio"], f"unexpected order/contents: {result}"


def test_extract_libraries_uv_lock_first_block_no_virtual_marker():
    """最初の [[package]] ブロックが `virtual = "..."` マーカーを持たない場合、
    プロジェクト自身ではないと判断し、フォールバックで全 name を抽出する。"""
    diff = (
        '+[[package]]\n'
        '+name = "httpx"\n'
        '+\n'
        '+[[package]]\n'
        '+name = "pydantic"\n'
    )
    result = ai_review._extract_libraries_from_diff(diff, ["uv.lock"])
    assert "httpx" in result
    assert "pydantic" in result
    assert result == ["httpx", "pydantic"]


def test_extract_libraries_requirements_txt_bare_names():
    """requirements.txt でバージョン指定なしの `+ httpx` / `+ pydantic` のような
    行も抽出対象になる。"""
    diff = '+ httpx\n+ pydantic\n'
    result = ai_review._extract_libraries_from_diff(diff, ["requirements.txt"])
    assert result == ["httpx", "pydantic"], f"unexpected: {result}"


def test_extract_libraries_requirements_txt_comments_and_includes():
    """requirements.txt で `#` コメント行や `-r other.txt` インクルード指示は
    依存ではないので抽出されず、`+httpx>=0.27` のような行のみが抽出される。"""
    diff = (
        '+# this is a comment\n'
        '+-r other-requirements.txt\n'
        '+httpx>=0.27\n'
        '+pydantic\n'
    )
    result = ai_review._extract_libraries_from_diff(diff, ["requirements.txt"])
    # コメント / インクルード指示に起因する偽陽性がないこと
    for false_positive in ("# this is a comment", "r", "other-requirements.txt"):
        assert false_positive not in result, (
            f"false positive '{false_positive}' in result: {result}"
        )
    assert "httpx" in result
    assert "pydantic" in result
    assert result == ["httpx", "pydantic"]


# ---------------------------------------------------------------------------
# uv.lock project source marker variants (editable / path / url)
# ---------------------------------------------------------------------------


def test_extract_libraries_uv_lock_editable_source_marker():
    """uv.lock のプロジェクト自身が `source = { editable = "src/python" }` で
    表れる場合、その [[package]] ブロックの name は依存ではないのでスキップする。
    実プロジェクトの uv.lock は `editable` を使う形式が主流。"""
    diff = (
        '+[[package]]\n'
        '+name = "hw-genie"\n'
        '+version = "0.1.0"\n'
        '+source = { editable = "src/python" }\n'
        '+\n'
        '+[[package]]\n'
        '+name = "new-lib"\n'
        '+version = "0.1.0"\n'
        '+source = { registry = "https://pypi.org/simple" }\n'
    )
    result = ai_review._extract_libraries_from_diff(diff, ["uv.lock"])
    assert "hw-genie" not in result, f"project name leaked: {result}"
    assert "new-lib" in result, f"new-lib missing: {result}"
    assert result == ["new-lib"], f"unexpected: {result}"


def test_extract_libraries_uv_lock_path_source_marker():
    """uv.lock のプロジェクト / workspace メンバーが `source = { path = "..." }`
    で表れる場合も、そのブロックの name は依存ではないのでスキップする。"""
    diff = (
        '+[[package]]\n'
        '+name = "proj"\n'
        '+version = "0.1.0"\n'
        '+source = { path = "../shared-lib" }\n'
        '+\n'
        '+[[package]]\n'
        '+name = "httpx"\n'
        '+version = "0.27"\n'
        '+source = { registry = "https://pypi.org/simple" }\n'
    )
    result = ai_review._extract_libraries_from_diff(diff, ["uv.lock"])
    assert "proj" not in result, f"project name leaked: {result}"
    assert "httpx" in result, f"httpx missing: {result}"
    assert result == ["httpx"], f"unexpected: {result}"


def test_extract_libraries_uv_lock_url_source_marker():
    """uv.lock で `source = { url = "..." }` の [[package]] ブロックは
    URL 依存（registry ではない）ため name は抽出しない。"""
    diff = (
        '+[[package]]\n'
        '+name = "url-only"\n'
        '+version = "0.1.0"\n'
        '+source = { url = "https://example.com/foo.tar.gz" }\n'
        '+\n'
        '+[[package]]\n'
        '+name = "pydantic"\n'
        '+version = "2.0"\n'
        '+source = { registry = "https://pypi.org/simple" }\n'
    )
    result = ai_review._extract_libraries_from_diff(diff, ["uv.lock"])
    assert "url-only" not in result, f"url-only leaked: {result}"
    assert "pydantic" in result, f"pydantic missing: {result}"
    assert result == ["pydantic"], f"unexpected: {result}"


def test_extract_libraries_uv_lock_virtual_marker_still_works():
    """`source = { virtual = "..." }` 形式のプロジェクトマーカーが
    正規表現拡張後も引き続き機能することを保証する。"""
    diff = (
        '+[[package]]\n'
        '+name = "hw-genie"\n'
        '+version = "0.1.0"\n'
        '+source = { virtual = "." }\n'
        '+\n'
        '+[[package]]\n'
        '+name = "httpx"\n'
        '+version = "0.27"\n'
    )
    result = ai_review._extract_libraries_from_diff(diff, ["uv.lock"])
    assert "hw-genie" not in result
    assert result == ["httpx"], f"unexpected: {result}"


def test_extract_libraries_uv_lock_fallback_when_no_project_marker():
    """最初の [[package]] ブロックが project マーカー（virtual/editable/path/url
    のいずれも）を持たない場合は、フォールバックで全 name を抽出する。"""
    diff = (
        '+[[package]]\n'
        '+name = "httpx"\n'
        '+\n'
        '+[[package]]\n'
        '+name = "pydantic"\n'
    )
    result = ai_review._extract_libraries_from_diff(diff, ["uv.lock"])
    assert result == ["httpx", "pydantic"], f"unexpected: {result}"


def test_extract_libraries_requirements_txt_numeric_filter():
    """requirements.txt で `+ 1.2.3` のようなバージョン番号だけの行は
    ライブラリ名ではないので除外する。"""
    diff = '+ 1.2.3\n+ httpx\n+ 0.27.0\n+ pydantic\n'
    result = ai_review._extract_libraries_from_diff(diff, ["requirements.txt"])
    # バージョン番号だけの行は依存ではないので抽出されないこと
    assert "1.2.3" not in result, f"numeric leaked: {result}"
    assert "0.27.0" not in result, f"numeric leaked: {result}"
    assert "httpx" in result
    assert "pydantic" in result
    assert result == ["httpx", "pydantic"], f"unexpected: {result}"


# ---------------------------------------------------------------------------
# Integration: extraction_diff flow (uv.lock-only PR)
# ---------------------------------------------------------------------------


def test_extraction_diff_contains_lock_body_for_uv_lock_only_pr(monkeypatch):
    """uv.lock のみの変更でもライブラリ抽出できる。

    filtered_diff は lock 本文をプレースホルダに置換するため、抽出用には
    extraction_diff を別途保持して _extract_libraries_from_diff に渡す。
    """
    import io
    from pathlib import Path
    from unidiff import PatchSet

    real_diff_path = Path("/tmp/real_uv_full.diff")
    if not real_diff_path.exists():
        pytest.skip("real_uv_full.diff fixture not available")
    raw_diff = real_diff_path.read_text()

    patch = PatchSet(io.StringIO(raw_diff))
    assert len(patch) == 1
    assert patch[0].path == "uv.lock"

    is_lock_or_manifest_re = re.compile(
        r"(\.lock$|requirements.*\.txt$|pyproject\.toml$|package\.json$|Cargo\.toml$|go\.mod$|Pipfile$|poetry\.lock$)",
        re.IGNORECASE,
    )

    filtered_diff = ""
    extraction_diff = ""
    changed_paths = []
    dep_extraction_paths = []

    for f in patch:
        path = f.path
        is_removed = getattr(f, "is_removed_file", False)
        is_ignored = re.search(
            r"(package-lock\.json|yarn\.lock|bun\.lockb|pnpm-lock\.yaml|poetry\.lock|\.lock|\.svg|\.png|\.jpg|\.jpeg|\.gif|\.mp4|\.zip)$",
            path,
            re.IGNORECASE,
        )
        is_dep_manifest = bool(is_lock_or_manifest_re.search(path))
        if is_ignored:
            filtered_diff += "[注: ...省略...]\n"
            if is_dep_manifest and not is_removed:
                dep_extraction_paths.append(path)
                extraction_diff += str(f) + "\n"
            continue
        filtered_diff += str(f) + "\n"
        extraction_diff += str(f) + "\n"
        if not is_removed:
            changed_paths.append(path)
            if is_dep_manifest:
                dep_extraction_paths.append(path)

    assert "uv.lock" in dep_extraction_paths
    assert "name = \"fastapi\"" in extraction_diff
    assert "name = \"fastapi\"" not in filtered_diff

    extraction_paths = list(dict.fromkeys(changed_paths + dep_extraction_paths))
    libs = ai_review._extract_libraries_from_diff(extraction_diff, extraction_paths)
    # fastapi と pytest は root プロジェクトの [package.dependencies] に直接 dep として
    # 記載されているため、`{ name = "fastapi", specifier = "..." }` パターンがマッチする
    assert "fastapi" in libs, f"Expected fastapi, got {libs}"
    assert "pytest" in libs, f"Expected pytest, got {libs}"

    # 旧フロー（filtered_diff を渡した場合）は何も抽出できない
    libs_old = ai_review._extract_libraries_from_diff(filtered_diff, extraction_paths)
    assert libs_old == [], f"Old flow should extract nothing for lock-only PR, got {libs_old}"
