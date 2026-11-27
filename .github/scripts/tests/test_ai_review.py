"""ai_review.py のヘルパー関数（リトライ・PR コメント取得・ファイル全文取得・メタデータ抽出・モデル解決）のテスト。"""

import base64
import html
import json
import os
import subprocess
import sys
from contextlib import ExitStack
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai_review  # noqa: E402
from ai_review import (  # noqa: E402
    _extract_details_blocks,
    _generate_with_opencode,
    _is_opencode_model_id,
    _is_review_output_valid,
    _opencode_variant,
    build_system_instruction,
    resolve_model,
    strip_review_metadata,
)


MODEL_CONFIG = {
    "flash-lite": {
        "name": "gemini-3.5-flash-lite",
        "aliases": [
            "gemini-3.5-flash-lite",
            "gemini-flash-lite-latest",
            "gemini-flash-lite",
        ],
        "input_cost_per_1m": 0.30,
        "output_cost_per_1m": 2.50,
        "max_diff_chars": 2000000,
    },
    "flash": {
        "name": "gemini-3.8-flash",
        "aliases": ["gemini-3.8-flash", "gemini-flash-latest", "gemini-flash"],
        "input_cost_per_1m": 0.75,
        "output_cost_per_1m": 3.75,
        "max_diff_chars": 2000000,
        "provider": "gemini",
        "thinking_level": "MEDIUM",
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
        "- **モデル**: `gemini-3.8-flash`\n"
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
        "- **モデル**: `gemini-3.8-flash`\n"
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
    assert name == "gemini-3.5-flash-lite"
    assert info["input_cost_per_1m"] == 0.30


def test_resolve_model_by_self_alias_returns_canonical():
    # "gemini-flash-lite" は flash-lite のエイリアス → 正式名へ正規化される
    # （API v1 はエイリアスを解決しないため、エイリアスを API へ渡さない）
    info, name = resolve_model("gemini-flash-lite", MODEL_CONFIG)
    assert name == "gemini-3.5-flash-lite"
    assert info["input_cost_per_1m"] == 0.30


def test_resolve_model_exact_alias_does_not_become_unknown():
    # "gemini-flash" は flash の厳密エイリアス → 未知にならず正式名へ正規化
    info, name = resolve_model("gemini-flash", MODEL_CONFIG)
    assert info["input_cost_per_1m"] == 0.75
    assert name == "gemini-3.8-flash"


def test_resolve_model_by_alias():
    info, name = resolve_model("gemini-flash-lite-latest", MODEL_CONFIG)
    # エイリアス指定: 正式名へ正規化、価格は flash-lite 相当
    assert name == "gemini-3.5-flash-lite"
    assert info["input_cost_per_1m"] == 0.30


def test_resolve_model_by_name():
    info, name = resolve_model("gemini-3.8-flash", MODEL_CONFIG)
    assert name == "gemini-3.8-flash"


def test_resolve_model_unknown_keyword_fallback():
    # "gemini-flash-latest-xyz" は flash のエイリアスを一意に含むため flash 相当
    info, name = resolve_model("gemini-flash-latest-xyz", MODEL_CONFIG)
    assert info["input_cost_per_1m"] == 0.75  # flash 相当
    assert name == "gemini-flash-latest-xyz"
    # 部分一致でも provider / thinking_level は一致エントリから引き継がれる
    assert info["provider"] == "gemini"
    assert info["thinking_level"] == "MEDIUM"


def test_resolve_model_partial_match_preserves_openrouter_provider():
    """実 models.json の部分一致でも provider / reasoning_effort が保持される。"""
    config = _prod_model_config()
    info, name = resolve_model("openrouter/free-xyz", config)
    assert name == "openrouter/free-xyz"
    assert info.get("provider") == "openrouter"
    assert info.get("reasoning_effort") == "high"


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


def test_models_json_uses_explicit_gemini_model_ids():
    """models.json の Gemini モデルは API v1 で解決可能な明示的 GA ID を使う。

    google-genai 3.x では Gemini Developer API の既定が v1 になり、``-latest``
    系エイリアスは 404 を返す。このテストは各エントリの ``name`` フィールド
    そのものを検証する（エイリアスは対象外）。既定の flash-lite / flash と
    hydrogen の ``name`` が明示 ID であり、provider=gemini の全エントリの
    ``name`` が ``-latest`` / ``-preview`` を含まないことを確認する。
    """
    path = os.path.join(os.path.dirname(__file__), "..", "models.json")
    with open(path) as f:
        config = json.load(f)
    assert config["flash"]["name"] == "gemini-3.8-flash"
    assert config["flash-lite"]["name"] == "gemini-3.5-flash-lite"
    assert config["hydrogen"]["name"] == "gemini-4-hydrogen"
    for key, info in config.items():
        if info.get("provider") != "gemini":
            continue
        name = info["name"]
        assert not name.endswith("-latest"), f"{key}: {name} must not be a -latest alias"
        assert "-preview" not in name, f"{key}: {name} must not be a preview model"


def test_resolve_model_hydrogen_alias():
    """実 models.json で hydrogen を解決すると正式名と thinking_level が返る。"""
    path = os.path.join(os.path.dirname(__file__), "..", "models.json")
    with open(path) as f:
        config = json.load(f)
    info, name = resolve_model("hydrogen", config)
    assert name == "gemini-4-hydrogen"
    assert info["thinking_level"] == "HIGH"
    # "gemini-hydrogen" エイリアスも同じ正式名へ正規化される
    assert resolve_model("gemini-hydrogen", config)[1] == "gemini-4-hydrogen"


def _prod_model_config():
    path = os.path.join(os.path.dirname(__file__), "..", "models.json")
    with open(path) as f:
        return json.load(f)


def test_default_model_key_is_hydrogen():
    """既定は Gemini チェーンの先頭。DeepSeek はその後（gemini-3.8-flash の次）。"""
    assert ai_review.DEFAULT_MODEL_KEY == "hydrogen"
    assert ai_review.OPENCODE_FALLBACK_MODEL_KEYS == (
        "opencode-go-deepseek-flash",
        "opencode-deepseek-free",
    )
    config = _prod_model_config()
    assert [resolve_model(k, config)[1] for k in ai_review.OPENCODE_FALLBACK_MODEL_KEYS] == [
        "opencode-go/deepseek-v4.1-flash",
        "opencode/deepseek-v4.1-flash-free",
    ]


def test_workflow_default_model_matches_python_constant():
    """workflow と Python で既定モデル文字列が二重管理されていないことを担保する。"""
    path = os.path.join(os.path.dirname(__file__), "..", "..", "workflows", "ai-pr-reviewer.yml")
    with open(path, encoding="utf-8") as f:
        workflow = f.read()
    key = ai_review.DEFAULT_MODEL_KEY
    assert f"default: '{key}'" in workflow
    assert f"inputs.model || '{key}'" in workflow


# 旧モデル ID は workflow に残さない（deny-list の第1要素で前缀も検出できる）。
# 変更履歴は PR 本文に書く。
DEPRECATED_MODEL_IDS = ("gemini-3.1-flash-lite",)


def test_workflow_timeout_and_python_budget_share_one_variable():
    """ジョブ制限と Python 側予算が同じ repo variable から導出される。"""
    path = os.path.join(os.path.dirname(__file__), "..", "..", "workflows", "ai-pr-reviewer.yml")
    with open(path, encoding="utf-8") as f:
        workflow = f.read()
    assert "timeout-minutes: ${{ fromJSON(vars.REVIEW_TIMEOUT_MINUTES || '15') }}" in workflow
    assert "REVIEW_TIMEOUT_MINUTES: ${{ vars.REVIEW_TIMEOUT_MINUTES || '15' }}" in workflow
    expected = float(os.environ.get("REVIEW_TIMEOUT_MINUTES", "15") or 15) * 60
    assert ai_review.JOB_TIMEOUT_SECONDS == expected


def test_workflow_verifies_cli_install():
    """固定バージョン／チェックサムを指定した導入結果を検証する。"""
    path = os.path.join(os.path.dirname(__file__), "..", "..", "workflows", "ai-pr-reviewer.yml")
    with open(path, encoding="utf-8") as f:
        workflow = f.read()
    assert "OPENCODE_CLI_SHA256: ${{ vars.OPENCODE_CLI_SHA256 }}" in workflow
    assert '"$installed" != "$OPENCODE_CLI_VERSION"' in workflow
    assert "sha256sum -c -" in workflow
    assert "OpenCode CLI is not installed; skipping verification" in workflow
    assert "OPENCODE_CLI_VERSION: ${{ vars.OPENCODE_CLI_VERSION || '2.4.2' }}" in workflow
    assert "Smoke test OpenCode CLI" in workflow
    assert "for flag in --agent --model --file --standalone --archived; do" in workflow
    assert 'rm -rf "$HOME/.opencode/bin"  # 検証失敗バイナリは実行もキャッシュもしない' in workflow
    assert '|| reject "OpenCode CLI --version failed"' in workflow
    assert 'if [ ! -x "$bin" ]; then' in workflow


def test_workflows_have_no_stale_model_ids():
    workflows_dir = os.path.join(os.path.dirname(__file__), "..", "..", "workflows")
    for name in sorted(os.listdir(workflows_dir)):
        if not name.endswith((".yml", ".yaml")):
            continue
        with open(os.path.join(workflows_dir, name), encoding="utf-8") as f:
            text = f.read()
        for stale in DEPRECATED_MODEL_IDS:
            assert stale not in text, f"{name} still contains stale model ID: {stale}"


def test_gemini_model_chain_default_order():
    chain = ai_review._gemini_model_chain("hydrogen", _prod_model_config())
    assert [name for _, _, name in chain] == [
        "gemini-4-hydrogen",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ]
    assert [info["thinking_level"] for _, info, _ in chain] == ["HIGH", "HIGH", "MEDIUM"]


def test_gemini_model_chain_starts_at_selected_model():
    config = _prod_model_config()
    assert [name for _, _, name in ai_review._gemini_model_chain("flash-lite", config)] == [
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ]
    assert [name for _, _, name in ai_review._gemini_model_chain("flash", config)] == [
        "gemini-3.8-flash",
    ]


def test_gemini_model_chain_resolves_hydrogen_alias():
    chain = ai_review._gemini_model_chain("gemini-4-hydrogen", _prod_model_config())
    assert [name for _, _, name in chain] == [
        "gemini-4-hydrogen",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ]


def test_gemini_model_chain_non_chain_models_are_alone():
    config = _prod_model_config()
    for key in ("gemma", "openrouter/free"):
        assert len(ai_review._gemini_model_chain(key, config)) == 1


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


def test_generate_with_model_chain_falls_back(monkeypatch):
    calls = []

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            calls.append(model)
            if model == "gemini-4-hydrogen":
                raise Exception("429 RESOURCE_EXHAUSTED")
            return _FakeResponse("ok")

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(ai_review.genai, "Client", FakeClient)
    chain = [
        ("hydrogen", {"name": "gemini-4-hydrogen", "thinking_level": "HIGH"}, "gemini-4-hydrogen"),
        ("flash-lite", {"name": "gemini-3.5-flash-lite", "thinking_level": "HIGH"}, "gemini-3.5-flash-lite"),
    ]
    resp, name, info, thinking, exc = ai_review._generate_with_model_chain(
        chain, ["k1"], "prompt", "system", None
    )
    assert calls == ["gemini-4-hydrogen", "gemini-3.5-flash-lite"]
    assert resp is not None
    assert name == "gemini-3.5-flash-lite"
    assert info["name"] == "gemini-3.5-flash-lite"
    assert thinking is not None
    assert exc is None
    assert resp._retry_count == 1


def test_generate_with_model_chain_rotates_keys(monkeypatch):
    calls = []

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            calls.append(model)
            if len(calls) == 1:
                raise Exception("401 invalid key")
            return _FakeResponse("ok")

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(ai_review.genai, "Client", FakeClient)
    chain = [("hydrogen", {"name": "gemini-4-hydrogen", "thinking_level": "HIGH"}, "gemini-4-hydrogen")]
    resp, name, info, thinking, exc = ai_review._generate_with_model_chain(
        chain, ["k1", "k2"], "prompt", "system", None
    )
    assert calls == ["gemini-4-hydrogen", "gemini-4-hydrogen"]
    assert name == "gemini-4-hydrogen"
    assert exc is None
    assert resp._retry_count == 1


def test_gemini_model_chain_unknown_is_alone():
    """未知モデルは連鎖を持たず単独で返る。"""
    chain = ai_review._gemini_model_chain("some-unknown-model", _prod_model_config())
    assert len(chain) == 1
    assert chain[0][2] == "some-unknown-model"


def test_generate_with_model_chain_returns_last_exc(monkeypatch):
    """全滅したら最後の例外を返し、結果系はすべて None になる。"""

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            raise Exception("404 NOT_FOUND")

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(ai_review.genai, "Client", FakeClient)
    chain = [("hydrogen", {"name": "gemini-4-hydrogen", "thinking_level": "HIGH"}, "gemini-4-hydrogen")]
    resp, name, info, thinking, exc = ai_review._generate_with_model_chain(
        chain, ["k1"], "prompt", "system", None
    )
    assert resp is None
    assert name is None
    assert info is None
    assert thinking is None
    assert exc is not None
    assert "404" in str(exc)


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


def test_fetch_file_contents_falls_back_to_gh_api(monkeypatch):
    """git が使えない環境では Contents API からファイル全文を取得する。"""
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    encoded = base64.b64encode(b"api content").decode()

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            raise OSError("git binary not found")
        if cmd[0] == "gh" and cmd[1] == "api":
            assert "repos/owner/repo/contents/a.py?ref=refs/pull/42/head" in cmd[2]
            return encoded
        raise AssertionError(cmd)

    with mock.patch("ai_review.subprocess.check_output", side_effect=fake_run):
        result = ai_review._fetch_file_contents("42", ["a.py"], limit=1000)
    assert "### a.py" in result
    assert "api content" in result


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


def test_build_thinking_config_defaults_to_none():
    """未設定時は None を返し API のデフォルト挙動に委ねる（HIGH 強制を廃止）。"""
    with mock.patch.dict(os.environ, {}, clear=True):
        config = ai_review._build_thinking_config({})
        assert config is None


def test_build_thinking_config_effort_option():
    """--effort 値（effort 引数）が環境変数・models.json より優先される。"""
    with mock.patch.dict(os.environ, {"GEMINI_THINKING_LEVEL": "LOW"}):
        config = ai_review._build_thinking_config({"thinking_level": "MEDIUM"}, "HIGH")
        assert config is not None
        assert str(config.thinking_level).upper().endswith("HIGH")

    with mock.patch.dict(os.environ, {}, clear=True):
        config = ai_review._build_thinking_config({"thinking_level": "MEDIUM"}, "minimal")
        assert config is not None
        assert str(config.thinking_level).upper().endswith("MINIMAL")

        # OFF 系は無効化（None）
        assert ai_review._build_thinking_config({}, "off") is None

        # "auto" は未指定扱い → models.json の設定へフォールバック
        config_auto = ai_review._build_thinking_config({"thinking_level": "LOW"}, "auto")
        assert config_auto is not None
        assert str(config_auto.thinking_level).upper().endswith("LOW")


def test_parse_comment_option():
    """``--flag value`` を行頭/空白区切りで抽出し、末尾の読点を除去する。"""
    text = "@gemini-cli /quick-review --model gemini-flash-latest --effort high,"
    assert ai_review._parse_comment_option(text, "model") == "gemini-flash-latest"
    assert ai_review._parse_comment_option(text, "effort") == "high"

    # openrouter 名（スラッシュ・コロン）も許容
    assert (
        ai_review._parse_comment_option("--model openrouter/free", "model")
        == "openrouter/free"
    )
    # 前置空白必須（"x--model" は無視）
    assert ai_review._parse_comment_option("x--model foo", "model") is None
    # 未指定は None
    assert ai_review._parse_comment_option("/quick-review", "effort") is None


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


def test_models_json_per_model_default_effort():
    """models.json の既定: flash=MEDIUM / flash-lite=HIGH / openrouter=high。"""
    cfg = ai_review.load_model_config()
    with mock.patch.dict(os.environ, {}, clear=True):
        flash_cfg = ai_review._build_thinking_config(cfg["flash"])
        assert flash_cfg is not None
        assert str(flash_cfg.thinking_level).upper().endswith("MEDIUM")

        lite_cfg = ai_review._build_thinking_config(cfg["flash-lite"])
        assert lite_cfg is not None
        assert str(lite_cfg.thinking_level).upper().endswith("HIGH")

        # --effort はモデル既定より優先
        override = ai_review._build_thinking_config(cfg["flash-lite"], "MINIMAL")
        assert override is not None
        assert str(override.thinking_level).upper().endswith("MINIMAL")

    assert cfg["openrouter-free"].get("reasoning_effort") == "high"


def test_thinking_effort_display():
    """実行情報の思考レベル表示は既定/指定/無効化のいずれも出所付きで常時分かる。"""
    with mock.patch.dict(os.environ, {}, clear=True):
        # models.json 既定（flash=MEDIUM / lite=HIGH）
        assert ai_review._thinking_effort_display({"thinking_level": "MEDIUM"}, None) == "MEDIUM （既定）"
        assert ai_review._thinking_effort_display({"thinking_level": "HIGH"}, None) == "HIGH （既定）"
        # --effort 指定は出所を明示
        assert ai_review._thinking_effort_display({"thinking_level": "MEDIUM"}, "low") == "LOW （--effort 指定）"
        # 設定なしは API 既定扱い
        assert ai_review._thinking_effort_display({}, None) == "未設定（API 既定）"
        # 明示無効化（models.json の null / --effort off）は OFF として表示
        assert ai_review._thinking_effort_display({"thinking_level": None}, None) == "OFF （既定）"
        assert ai_review._thinking_effort_display({}, "off") == "OFF （--effort 指定）"

    # 環境変数由来は「既定」扱い
    with mock.patch.dict(os.environ, {"GEMINI_THINKING_LEVEL": "LOW"}):
        assert ai_review._thinking_effort_display({}, None) == "LOW （既定）"


def test_resolve_effort_display_gemini_path():
    """OpenRouter 未使用時は Gemini 向け解決（models.json 既定 / --effort 指定）を表示。"""
    with mock.patch.dict(os.environ, {}, clear=True):
        assert ai_review._resolve_effort_display(
            used_openrouter=False,
            or_used_effort=None,
            initial_reasoning_effort=None,
            thinking_effort=None,
            model_info={"thinking_level": "HIGH"},
        ) == "HIGH （既定）"
        assert ai_review._resolve_effort_display(
            used_openrouter=False,
            or_used_effort=None,
            initial_reasoning_effort=None,
            thinking_effort="low",
            model_info={"thinking_level": "HIGH"},
        ) == "LOW （--effort 指定）"


def test_resolve_effort_display_openrouter_fallback_divergence():
    """Gemini 全滅→OpenRouter フォールバック成功で reasoning 未送信の回帰。

    model_info に Gemini 用の thinking_level が残っていても Gemini レベルを
    表示せず、実送信どおり「reasoning なし」を出す。
    """
    with mock.patch.dict(os.environ, {}, clear=True):
        assert ai_review._resolve_effort_display(
            used_openrouter=True,
            or_used_effort=None,
            initial_reasoning_effort="high",
            thinking_effort="HIGH",
            model_info={"name": "openrouter/free", "thinking_level": "HIGH"},
        ) == "reasoning なし（モデル既定）"


def test_resolve_effort_display_openrouter_used_effort():
    """実際に送った reasoning.effort を出所付きで表示する。"""
    with mock.patch.dict(os.environ, {}, clear=True):
        # --effort 未指定（models.json 由来）は「既定」
        assert ai_review._resolve_effort_display(
            used_openrouter=True,
            or_used_effort="high",
            initial_reasoning_effort="high",
            thinking_effort=None,
            model_info={"reasoning_effort": "high"},
        ) == "reasoning: high （既定）"
        # --effort 指定が実際に使われた場合のみ --effort 帰属
        assert ai_review._resolve_effort_display(
            used_openrouter=True,
            or_used_effort="high",
            initial_reasoning_effort="high",
            thinking_effort="HIGH",
            model_info={"reasoning_effort": "high"},
        ) == "reasoning: high （--effort 指定）"


def test_resolve_effort_display_openrouter_source_attribution():
    """auto / 不正値が models.json にフォールバックした場合は --effort に帰属しない。"""
    with mock.patch.dict(os.environ, {}, clear=True):
        # --effort auto → models.json の reasoning_effort "high" が実送信された
        assert ai_review._resolve_effort_display(
            used_openrouter=True,
            or_used_effort="high",
            initial_reasoning_effort="high",
            thinking_effort="auto",
            model_info={"reasoning_effort": "high"},
        ) == "reasoning: high （既定）"
        # --effort が OpenRouter 不正値 → models.json の値が実送信された
        assert ai_review._resolve_effort_display(
            used_openrouter=True,
            or_used_effort="high",
            initial_reasoning_effort="high",
            thinking_effort="bogus",
            model_info={"reasoning_effort": "high"},
        ) == "reasoning: high （既定）"


def test_openrouter_reasoning_resolution():
    """--effort > models.json reasoning_effort > 未指定 の解決順。"""
    assert ai_review._openrouter_reasoning("HIGH", {"reasoning_effort": "low"}) == "high"
    assert ai_review._openrouter_reasoning(None, {"reasoning_effort": "high"}) == "high"
    # auto はモデル既定へフォールバック、off は明示的なパラメータなし
    assert ai_review._openrouter_reasoning("auto", {"reasoning_effort": "high"}) == "high"
    assert ai_review._openrouter_reasoning("off", {"reasoning_effort": "high"}) is None
    assert ai_review._openrouter_reasoning(None, {}) is None
    # OpenRouter が受け付けない値は無視
    assert ai_review._openrouter_reasoning("bogus", {}) is None


def test_run_openrouter_pass_retries_without_reasoning():
    """effort 付きで全モデル失敗時は reasoning なしの 2 パス目が走る。"""
    calls: list[tuple[str, str | None]] = []

    def fake_generate(model, prompt, sys_instr, keys, reasoning_effort=None, **kwargs):
        calls.append((model, reasoning_effort))
        if reasoning_effort is not None:
            raise Exception("400 reasoning unsupported")
        return ai_review.OpenRouterResponse(text="ok")

    with mock.patch.object(ai_review, "_generate_with_openrouter", fake_generate):
        resp, used_model, used_effort, exc = ai_review._run_openrouter_pass(
            ["a", "b"], "p", "s", ["k"], "high"
        )
    assert resp is not None and resp.text == "ok"
    assert used_model == "a"
    assert used_effort is None  # 1 パス目全滅 → 2 パス目（reasoning なし）で成功
    assert exc is None
    # 1 パス目: 全モデル high で失敗 → 2 パス目: 先頭 a が成功
    assert calls == [("a", "high"), ("b", "high"), ("a", None)]


def test_run_openrouter_pass_single_pass_without_effort():
    """effort 未指定なら 1 パスだけで完了する（既存挙動のまま）。"""
    calls: list[tuple[str, str | None]] = []

    def fake_generate(model, prompt, sys_instr, keys, reasoning_effort=None, **kwargs):
        calls.append((model, reasoning_effort))
        raise Exception("boom")

    with mock.patch.object(ai_review, "_generate_with_openrouter", fake_generate):
        resp, used_model, used_effort, exc = ai_review._run_openrouter_pass(
            ["a", "b"], "p", "s", ["k"], None
        )
    assert resp is None and used_model is None and used_effort is None
    assert exc is not None
    assert calls == [("a", None), ("b", None)]


def test_run_openrouter_pass_reports_used_effort():
    """1 パス目で成功した場合は実際に送った effort が返る。"""

    def fake_generate(model, prompt, sys_instr, keys, reasoning_effort=None, **kwargs):
        return ai_review.OpenRouterResponse(text="ok")

    with mock.patch.object(ai_review, "_generate_with_openrouter", fake_generate):
        resp, used_model, used_effort, exc = ai_review._run_openrouter_pass(
            ["a", "b"], "p", "s", ["k"], "high"
        )
    assert resp is not None and used_model == "a"
    assert used_effort == "high"
    assert exc is None


def _openrouter_post_mock(data: dict) -> mock.MagicMock:
    """_generate_with_openrouter 用の httpx.post 戻り値モック。"""
    resp = mock.MagicMock()
    resp.status_code = 200
    resp.text = json.dumps(data, ensure_ascii=False)
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


def test_openrouter_fallback_models_default_order():
    """既定 chain は個別 :free 2 モデル → 動的ルーターの順（ルーターは最後の安全網）。"""
    expected = [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3.5-lightning:free",
        "openrouter/free",
    ]
    assert ai_review.OPENROUTER_FALLBACK_MODELS == expected
    with mock.patch.dict(os.environ, {}, clear=True):
        assert ai_review._get_openrouter_fallback_models() == expected
    assert expected[-1] == "openrouter/free"


def test_openrouter_fallback_models_env_override_and_gemma_filter():
    """env 上書きと gemma 除外フィルタは維持される。"""
    with mock.patch.dict(
        os.environ,
        {
            "OPENROUTER_FALLBACK_MODELS": (
                "some/gemma:free, custom/model:free , openrouter/free"
            )
        },
        clear=True,
    ):
        assert ai_review._get_openrouter_fallback_models() == [
            "custom/model:free",
            "openrouter/free",
        ]


def test_reasoning_payload_per_model():
    """effort 対応モデルと非対応モデルで reasoning ペイロードを切り替える。"""
    # 明記モデル＋未知モデル（env 指定を含む）は既存互換の effort 経路
    assert ai_review._reasoning_payload(
        "nvidia/nemotron-3-ultra-550b-a55b:free", "high"
    ) == {"effort": "high", "exclude": True}
    assert ai_review._reasoning_payload("custom/unknown-model:free", "medium") == {
        "effort": "medium",
        "exclude": True,
    }
    # effort 非対応の明記モデルは enabled + exclude（effort を送らない）
    assert ai_review._reasoning_payload(
        "nvidia/nemotron-3.5-lightning:free", "high"
    ) == {"enabled": True, "exclude": True}
    assert ai_review._reasoning_payload(
        "nvidia/nemotron-3.5-super-vl:free", "high"
    ) == {"enabled": True, "exclude": True}
    # 2 パス目（reasoning なし再試行）ではどのモデルにも送らない
    assert (
        ai_review._reasoning_payload("nvidia/nemotron-3-ultra-550b-a55b:free", None) is None
    )
    assert ai_review._reasoning_payload("nvidia/nemotron-3.5-lightning:free", None) is None


def test_generate_with_openrouter_payload_reasoning_and_max_tokens():
    """リクエスト payload に reasoning（exclude 付き）と max_tokens=16000 が入る。"""
    data = {
        "choices": [{"message": {"content": "レビュー本文"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "completion_tokens_details": {"reasoning_tokens": 20},
        },
        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
    }
    with mock.patch("httpx.post", return_value=_openrouter_post_mock(data)) as post:
        resp = ai_review._generate_with_openrouter(
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "prompt",
            "system",
            ["key"],
            reasoning_effort="high",
        )
    payload = post.call_args.kwargs["json"]
    assert ai_review.OPENROUTER_DEFAULT_MAX_TOKENS == 16000
    assert payload["max_tokens"] == 16000
    assert payload["reasoning"] == {"effort": "high", "exclude": True}
    assert payload["model"] == "nvidia/nemotron-3-ultra-550b-a55b:free"
    # reasoning トークン数が実行メタデータ用に保持される
    assert resp.usage_metadata.thoughts_token_count == 20
    assert resp.text == "レビュー本文"


def test_generate_with_openrouter_non_effort_model_payload():
    """effort 非対応モデルには effort を送らず enabled + exclude を送る。"""
    data = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {},
        "model": "nvidia/nemotron-3.5-lightning:free",
    }
    with mock.patch("httpx.post", return_value=_openrouter_post_mock(data)) as post:
        ai_review._generate_with_openrouter(
            "nvidia/nemotron-3.5-lightning:free", "p", "s", ["k"], reasoning_effort="high"
        )
    reasoning = post.call_args.kwargs["json"]["reasoning"]
    assert reasoning == {"enabled": True, "exclude": True}
    assert "effort" not in reasoning


def test_reasoning_display_value_from_payload():
    """表示値は実際に送る reasoning ペイロードから導出する（単一情報源）。"""
    assert (
        ai_review._reasoning_display_value({"effort": "high", "exclude": True}) == "high"
    )
    assert (
        ai_review._reasoning_display_value({"enabled": True, "exclude": True})
        == ai_review.OPENROUTER_REASONING_ENABLED_DISPLAY
    )
    assert ai_review._reasoning_display_value(None) is None


def test_run_openrouter_pass_non_effort_model_display_integration():
    """lightning が勝った場合、実行情報に high も「reasoning なし」も出ない。"""
    lightning = "nvidia/nemotron-3.5-lightning:free"

    def fake_generate(model, prompt, sys_instr, keys, reasoning_effort=None, **kwargs):
        return ai_review.OpenRouterResponse(text="ok")

    with mock.patch.object(ai_review, "_generate_with_openrouter", fake_generate):
        resp, used_model, used_effort, exc = ai_review._run_openrouter_pass(
            [lightning, "openrouter/free"], "p", "s", ["k"], "high"
        )
    assert resp is not None and used_model == lightning and exc is None
    display = ai_review._resolve_effort_display(
        used_openrouter=True,
        or_used_effort=used_effort,
        initial_reasoning_effort="high",
        thinking_effort="HIGH",
        model_info={"name": lightning},
    )
    assert "有効" in display
    assert "high" not in display.lower()
    assert "reasoning なし" not in display
    # effort 経路は従来どおり表示される（回帰防止）
    assert ai_review._resolve_effort_display(
        used_openrouter=True,
        or_used_effort="high",
        initial_reasoning_effort="high",
        thinking_effort="HIGH",
        model_info={"reasoning_effort": "high"},
    ) == "reasoning: high （--effort 指定）"


def test_generate_with_openrouter_choices_text_body():
    """choices[0].text のみのレスポンスも本文として採用する（従来互換）。"""
    data = {"choices": [{"text": "本文です", "finish_reason": "stop"}], "usage": {}}
    with mock.patch("httpx.post", return_value=_openrouter_post_mock(data)):
        resp = ai_review._generate_with_openrouter("openrouter/free", "p", "s", ["k"], None)
    assert resp.text == "本文です"


def test_generate_with_openrouter_list_content_text_parts_only():
    """list 型 content は text パートのみ連結し、reasoning パートは無視する。"""
    data = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "指摘1。\n"},
                        {"type": "reasoning", "text": "Let me think..."},
                        {"type": "text", "text": "指摘2。"},
                    ]
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {},
    }
    with mock.patch("httpx.post", return_value=_openrouter_post_mock(data)):
        resp = ai_review._generate_with_openrouter("openrouter/free", "p", "s", ["k"], None)
    assert resp.text == "指摘1。\n指摘2。"
    assert "Let me think" not in resp.text


def test_generate_with_openrouter_router_payload():
    """安全網の openrouter/free にも effort + exclude の reasoning を送る。"""
    data = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {},
        "model": "openrouter/free",
    }
    with mock.patch("httpx.post", return_value=_openrouter_post_mock(data)) as post:
        ai_review._generate_with_openrouter("openrouter/free", "p", "s", ["k"], "high")
    assert post.call_args.kwargs["json"]["reasoning"] == {
        "effort": "high",
        "exclude": True,
    }


def test_generate_with_openrouter_ignores_reasoning_only_body():
    """content が空で reasoning 系フィールドだけある場合は本文に使わず例外にする。"""
    data = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "reasoning": "Let me carefully review the diff...",
                    "reasoning_details": [
                        {"type": "reasoning.text", "text": "内部の調査メモ"}
                    ],
                },
                "reasoning": "choices 直下の reasoning",
                "finish_reason": "stop",
            }
        ],
        "usage": {},
    }
    with mock.patch("httpx.post", return_value=_openrouter_post_mock(data)):
        with pytest.raises(Exception, match="empty content"):
            ai_review._generate_with_openrouter("openrouter/free", "p", "s", ["k"], "high")


def test_generate_with_openrouter_length_finish_reason_message():
    """本文が空で finish_reason=length なら例外メッセージにその旨を含める。"""
    data = {
        "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        "usage": {},
    }
    with mock.patch("httpx.post", return_value=_openrouter_post_mock(data)):
        with pytest.raises(Exception, match="length"):
            ai_review._generate_with_openrouter("openrouter/free", "p", "s", ["k"], "high")


def test_run_openrouter_pass_empty_content_falls_back_to_next_model():
    """空本文の例外は次のモデルへフォールバックし、ルーターまで試す。"""
    calls = []

    def fake_generate(model, prompt, sys_instr, keys, reasoning_effort=None, **kwargs):
        calls.append(model)
        if model == "nvidia/nemotron-3-ultra-550b-a55b:free":
            raise Exception("OpenRouter returned empty content")
        return ai_review.OpenRouterResponse(text="ok")

    with mock.patch.object(ai_review, "_generate_with_openrouter", fake_generate):
        resp, used_model, used_effort, exc = ai_review._run_openrouter_pass(
            [
                "nvidia/nemotron-3-ultra-550b-a55b:free",
                "nvidia/nemotron-3.5-lightning:free",
                "openrouter/free",
            ],
            "p",
            "s",
            ["k"],
            "high",
        )
    assert resp is not None and resp.text == "ok"
    assert used_model == "nvidia/nemotron-3.5-lightning:free"
    assert exc is None
    assert calls == [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3.5-lightning:free",
    ]


def test_run_openrouter_pass_all_empty_content_fails():
    """ルーターまで本文が空ならフォールバック全体を失敗させる。"""

    def fake_generate(model, prompt, sys_instr, keys, reasoning_effort=None, **kwargs):
        raise Exception("OpenRouter returned empty content")

    with mock.patch.object(ai_review, "_generate_with_openrouter", fake_generate):
        resp, used_model, used_effort, exc = ai_review._run_openrouter_pass(
            ["openrouter/free"], "p", "s", ["k"], "high"
        )
    assert resp is None and used_model is None and used_effort is None
    assert exc is not None and "empty content" in str(exc)


def test_build_system_instruction_output_contract():
    """思考の露出禁止・最大 8 件・2 文以内・前置き不要の出力契約が含まれる。"""
    instruction = build_system_instruction()
    assert "出力契約" in instruction
    assert "内省モノローグ" in instruction
    assert "最大 8 件" in instruction
    assert "2 文以内" in instruction
    assert "前置き" in instruction


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
    # 出所付き表示（呼び出し側が渡した effort_display がそのまま使われる）
    metadata_annotated = ai_review.build_execution_metadata(
        model_name="gemini-flash-latest",
        resolved_model_name="gemini-2.5-flash",
        model_info=model_info,
        response=mock_resp,
        thinking_config=thinking_cfg,
        effort_display="HIGH （既定）",
        files_modified_count=3,
        duration=4.5,
    )
    assert "思考: `HIGH （既定）`" in metadata_annotated
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
    """Thinking なし・コスト不明モデルでも実行情報には思考レベル（未設定）が常時表示される。"""
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
    assert "思考: `未設定（API 既定）`" in metadata
    assert "(取得できませんでした)" in metadata


# ---------------------------------------------------------------------------
# Review comment posting tests
# ---------------------------------------------------------------------------


REST_POST_CMD = [
    "gh",
    "api",
    "-X",
    "POST",
    "repos/owner/repo/issues/42/comments",
    "-F",
    "body=@review.md",
]


def _transient_post_error(cmd, message="GraphQL error: Service Unavailable (HTTP 503)"):
    return subprocess.CalledProcessError(1, cmd, stderr=message)


def _set_review_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PR_NUMBER", "42")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("MODEL_KEY", "flash-lite")
    monkeypatch.delenv("ADDITIONAL_CONTEXT", raising=False)
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    return summary


def test_post_review_comment_uses_rest_post_for_new_comment(tmp_path, monkeypatch):
    """新規コメントは GraphQL (gh pr comment) ではなく REST POST で投稿する。"""
    monkeypatch.chdir(tmp_path)
    with mock.patch("ai_review.subprocess.run", return_value=mock.Mock(returncode=0)) as run:
        assert ai_review._post_review_comment("owner/repo", "42", None) is True
    run.assert_called_once()
    assert run.call_args.args[0] == REST_POST_CMD
    assert run.call_args.kwargs["check"] is True
    # capture_output が外されると CalledProcessError の本文が空になり、
    # 実 CI で一時エラーのリトライが効かなくなるため、両方を固定する。
    assert run.call_args.kwargs["capture_output"] is True
    assert run.call_args.kwargs["text"] is True


def test_post_review_comment_warning_includes_captured_error_body(
    tmp_path, monkeypatch, capsys
):
    """capture_output で拾った gh api のエラー本文が警告に残る（実 CI と同じ挙動）。"""
    monkeypatch.chdir(tmp_path)

    def fake_run(cmd, **kwargs):
        assert kwargs["capture_output"] is True and kwargs["text"] is True
        raise subprocess.CalledProcessError(
            1,
            cmd,
            output="stdout detail",
            stderr="GraphQL error: Service Unavailable (HTTP 503)\nsecond line",
        )

    with mock.patch("ai_review.subprocess.run", side_effect=fake_run) as run, mock.patch(
        "ai_review.time.sleep"
    ) as sleep:
        assert ai_review._post_review_comment("owner/repo", "42", None) is False
    assert run.call_count == ai_review.COMMENT_POST_ATTEMPTS
    assert sleep.call_args_list == [mock.call(2), mock.call(4)]
    out = capsys.readouterr().out
    assert "::warning::GitHub API Error (POST):" in out
    assert "GraphQL error: Service Unavailable (HTTP 503)" in out
    assert "stdout detail" in out
    assert "second line" in out
    # ::warning:: は 1 行で出力する必要がある（改行でコマンドが壊れるのを防ぐ）
    assert "\nsecond line" not in out


def test_post_review_comment_retries_graphql_transient_error_via_rest(tmp_path, monkeypatch):
    """GraphQL 一時エラー文言が出ても REST 経路でリトライして回復する。"""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            raise _transient_post_error(cmd)
        return mock.Mock(returncode=0)

    monkeypatch.chdir(tmp_path)
    calls = []
    with mock.patch("ai_review.subprocess.run", side_effect=fake_run) as run, mock.patch(
        "ai_review.time.sleep"
    ) as sleep:
        assert ai_review._post_review_comment("owner/repo", "42", None) is True
    assert [call.args[0] for call in run.call_args_list] == [REST_POST_CMD, REST_POST_CMD]
    sleep.assert_called_once_with(ai_review.COMMENT_POST_DELAYS[0])


def test_post_review_comment_retries_503_with_short_backoff(tmp_path, monkeypatch):
    """5xx は 2s/4s の短いバックオフで最大 3 回まで再試行する。"""
    monkeypatch.chdir(tmp_path)
    attempts = 0

    def fake_run(cmd, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _transient_post_error(cmd, "HTTP 503 Service Unavailable")
        return mock.Mock(returncode=0)

    with mock.patch("ai_review.subprocess.run", side_effect=fake_run) as run, mock.patch(
        "ai_review.time.sleep"
    ) as sleep:
        assert ai_review._post_review_comment("owner/repo", "42", None) is True
    assert run.call_count == 3
    assert sleep.call_args_list == [mock.call(2), mock.call(4)]


def test_post_review_comment_does_not_retry_non_transient_error(tmp_path, monkeypatch):
    """404 等の恒久エラーは即座に諦め、リトライ待機しない。"""
    monkeypatch.chdir(tmp_path)
    with mock.patch(
        "ai_review.subprocess.run",
        side_effect=lambda cmd, **kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, cmd, stderr="HTTP 404 Not Found")
        ),
    ) as run, mock.patch("ai_review.time.sleep") as sleep:
        assert ai_review._post_review_comment("owner/repo", "42", None) is False
    assert run.call_count == 1
    sleep.assert_not_called()


def test_post_review_comment_updates_existing_comment_via_rest_patch(tmp_path, monkeypatch):
    """既存コメントの更新は REST PATCH 経路を維持する。"""
    monkeypatch.chdir(tmp_path)
    with mock.patch("ai_review.subprocess.run", return_value=mock.Mock(returncode=0)) as run:
        assert ai_review._post_review_comment("owner/repo", "42", {"id": 987}) is True
    assert run.call_args.args[0] == [
        "gh",
        "api",
        "-X",
        "PATCH",
        "repos/owner/repo/issues/comments/987",
        "-F",
        "body=@review.md",
    ]


def test_publish_review_keeps_review_on_final_post_failure(tmp_path, monkeypatch, capsys):
    """投稿が最終失敗しても review.md と step summary を残し、例外を投げない。"""
    summary = _set_review_env(monkeypatch, tmp_path)
    with mock.patch(
        "ai_review.subprocess.run",
        side_effect=lambda cmd, **kwargs: (_ for _ in ()).throw(_transient_post_error(cmd)),
    ) as run, mock.patch("ai_review.time.sleep") as sleep:
        assert (
            ai_review._publish_review(
                "### 🤖 AI コードレビュー\n\n生成本文", "owner/repo", "42", None
            )
            is False
        )
    assert run.call_count == ai_review.COMMENT_POST_ATTEMPTS
    assert sleep.call_args_list == [mock.call(2), mock.call(4)]
    assert "生成本文" in (tmp_path / "review.md").read_text(encoding="utf-8")
    saved_summary = summary.read_text(encoding="utf-8")
    assert "生成本文" in saved_summary
    assert "投稿" in saved_summary
    assert "::warning::" in capsys.readouterr().out


def test_write_review_failure_summary_escapes_llm_html(tmp_path, monkeypatch):
    """LLM 本文の HTML（特に </details>）をエスケープし、summary の構造を壊さない。"""
    summary = _set_review_env(monkeypatch, tmp_path)
    review_text = "本文</details>脆弱 <b>太字</b>"
    ai_review._write_review_failure_summary(review_text)
    saved = summary.read_text(encoding="utf-8")
    # 生の summary の閉じタグはラッパーの 1 つだけ（LLM 本文由来は残らない）
    assert saved.count("</details>") == 1
    assert "&lt;/details&gt;" in saved
    assert "&lt;b&gt;" in saved
    # HTML エンティティを戻せば本文を完全に取り出せる
    assert review_text in html.unescape(saved)


def test_publish_review_keeps_review_in_summary_when_file_write_fails(
    tmp_path, monkeypatch, capsys
):
    """review.md が書けない場合も step summary に本文を残し、check を落とさない。"""
    summary = _set_review_env(monkeypatch, tmp_path)
    (tmp_path / "review.md").mkdir()  # ファイルではなくディレクトリにして open() を失敗させる
    with mock.patch("ai_review.subprocess.run") as run:
        assert (
            ai_review._publish_review("保存失敗時の本文", "owner/repo", "42", None) is False
        )
    run.assert_not_called()
    assert "保存失敗時の本文" in summary.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "::warning::" in out
    assert "step summary" in out


def _patch_review_dependencies(generation_result):
    """main() の生成フェーズを差し替える共通モック（diff は 1 ファイルの最小差分）。"""
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
    return (
        mock.patch.object(ai_review, "_collect_gemini_keys", return_value=["key"]),
        mock.patch.object(ai_review, "_collect_openrouter_keys", return_value=[]),
        mock.patch.object(ai_review.subprocess, "check_output", return_value=diff.encode()),
        mock.patch.object(ai_review, "_fetch_pr_metadata", return_value=""),
        mock.patch.object(ai_review, "_fetch_pr_comments", return_value=("", None)),
        mock.patch.object(ai_review, "_fetch_file_contents", return_value=""),
        mock.patch.object(ai_review, "_generate_with_model_chain", return_value=generation_result),
    )


def test_main_publishes_failure_review_when_generation_fails(tmp_path, monkeypatch):
    """生成が全滅してもコメントを失わず、失敗レビューを投稿する。"""
    _set_review_env(monkeypatch, tmp_path)
    patches = _patch_review_dependencies((None, None, None, None, Exception("503 Service Unavailable")))
    with mock.patch("ai_review.subprocess.run", return_value=mock.Mock(returncode=0)) as run:
        for patcher in patches:
            patcher.start()
        try:
            with pytest.raises(SystemExit) as excinfo:
                ai_review.main()
        finally:
            for patcher in reversed(patches):
                patcher.stop()
    assert excinfo.value.code == 1
    assert run.called
    assert "生成に失敗しました" in (tmp_path / "review.md").read_text(encoding="utf-8")


def test_main_does_not_fail_when_comment_posting_fails(tmp_path, monkeypatch):
    """生成成功後の投稿失敗は警告のみで check を落とさない。"""
    summary = _set_review_env(monkeypatch, tmp_path)
    response = _FakeResponse("生成本文")
    generation = (
        response,
        "gemini-test-flash",
        {"name": "gemini-test-flash", "input_cost_per_1m": None, "output_cost_per_1m": None},
        None,
        None,
    )
    patches = _patch_review_dependencies(generation)
    with mock.patch(
        "ai_review.subprocess.run",
        side_effect=lambda cmd, **kwargs: (_ for _ in ()).throw(_transient_post_error(cmd)),
    ) as run, mock.patch("ai_review.time.sleep"):
        for patcher in patches:
            patcher.start()
        try:
            ai_review.main()  # SystemExit を出さないこと
        finally:
            for patcher in reversed(patches):
                patcher.stop()
    assert run.call_count == ai_review.COMMENT_POST_ATTEMPTS
    assert "生成本文" in (tmp_path / "review.md").read_text(encoding="utf-8")
    assert "生成本文" in summary.read_text(encoding="utf-8")


def test_main_new_comment_uses_rest_post_not_gh_pr_comment(tmp_path, monkeypatch):
    """main() の新規コメント経路では gh pr comment を呼ばず REST POST を使う。"""
    _set_review_env(monkeypatch, tmp_path)
    generation = (
        _FakeResponse("生成本文"),
        "gemini-test-flash",
        {"name": "gemini-test-flash", "input_cost_per_1m": None, "output_cost_per_1m": None},
        None,
        None,
    )
    patches = _patch_review_dependencies(generation)
    with mock.patch("ai_review.subprocess.run", return_value=mock.Mock(returncode=0)) as run:
        for patcher in patches:
            patcher.start()
        try:
            ai_review.main()  # SystemExit を出さないこと
        finally:
            for patcher in reversed(patches):
                patcher.stop()
    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [REST_POST_CMD]
    assert not any(cmd[:3] == ["gh", "pr", "comment"] for cmd in commands)
    assert run.call_args.kwargs["capture_output"] is True


# ---------------------------------------------------------------------------
# OpenRouter フォールバックの reasoning / キー回転 / 途中終了通知
# ---------------------------------------------------------------------------


def test_openrouter_fallback_reasoning_uses_openrouter_free_default():
    """Gemini プライマリに reasoning_effort が無くても openrouter-free の既定を使う。"""
    config = {"openrouter-free": {"reasoning_effort": "high"}}
    assert ai_review._openrouter_fallback_reasoning(None, config) == "high"
    assert ai_review._openrouter_fallback_reasoning("auto", config) == "high"
    # 明示 --effort が最優先、off 系は尊重
    assert ai_review._openrouter_fallback_reasoning("low", config) == "low"
    assert ai_review._openrouter_fallback_reasoning("off", config) is None
    # 設定が無ければ None（従来どおり）
    assert ai_review._openrouter_fallback_reasoning(None, {}) is None


def _run_main_gemini_to_openrouter(
    tmp_path, monkeypatch, additional_context=None, *, response=None
):
    """hydrogen プライマリ + Gemini 全滅 -> OpenRouter 成功を main() で再現する。"""
    _set_review_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MODEL_KEY", "hydrogen")
    if additional_context is None:
        monkeypatch.delenv("ADDITIONAL_CONTEXT", raising=False)
    else:
        monkeypatch.setenv("ADDITIONAL_CONTEXT", additional_context)

    if response is None:
        response = ai_review.OpenRouterResponse(text="OpenRouter 本文")
    seen_efforts = []

    def fake_generate(model, prompt, system_instruction, keys, reasoning_effort=None, **kwargs):
        seen_efforts.append(reasoning_effort)
        return response

    diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(ai_review, "_collect_gemini_keys", return_value=["key"]))
        stack.enter_context(
            mock.patch.object(ai_review, "_collect_openrouter_keys", return_value=["or-key"])
        )
        stack.enter_context(
            mock.patch.object(ai_review.subprocess, "check_output", return_value=diff.encode())
        )
        stack.enter_context(mock.patch.object(ai_review, "_fetch_pr_metadata", return_value=""))
        stack.enter_context(
            mock.patch.object(ai_review, "_fetch_pr_comments", return_value=("", None))
        )
        stack.enter_context(mock.patch.object(ai_review, "_fetch_file_contents", return_value=""))
        stack.enter_context(
            mock.patch.object(
                ai_review,
                "_generate_with_model_chain",
                return_value=(None, None, None, None, Exception("Gemini 全滅")),
            )
        )
        stack.enter_context(
            mock.patch.object(ai_review, "_generate_with_openrouter", fake_generate)
        )
        run_pass = stack.enter_context(
            mock.patch.object(
                ai_review, "_run_openrouter_pass", wraps=ai_review._run_openrouter_pass
            )
        )
        stack.enter_context(
            mock.patch("ai_review.subprocess.run", return_value=mock.Mock(returncode=0))
        )
        ai_review.main()  # SystemExit を出さないこと
    return run_pass, seen_efforts, (tmp_path / "review.md").read_text(encoding="utf-8")


def test_main_openrouter_fallback_sends_default_reasoning_without_effort(tmp_path, monkeypatch):
    """--effort 未指定でも openrouter-free の high がフォールバックで送られる。"""
    run_pass, seen, review = _run_main_gemini_to_openrouter(tmp_path, monkeypatch)
    assert run_pass.call_count == 1
    # 第 5 引数が reasoning_effort（プライマリ hydrogen の None ではない）
    assert run_pass.call_args.args[4] == "high"
    assert seen == ["high"]
    assert "reasoning: high （既定）" in review


def test_main_openrouter_fallback_honors_explicit_effort(tmp_path, monkeypatch):
    """明示 --effort はフォールバックでも最優先され、表示にも出所が出る。"""
    run_pass, seen, review = _run_main_gemini_to_openrouter(
        tmp_path, monkeypatch, "@gemini-cli /quick-review --effort low"
    )
    assert run_pass.call_args.args[4] == "low"
    assert seen == ["low"]
    assert "reasoning: low （--effort 指定）" in review


def test_main_openrouter_fallback_respects_effort_off(tmp_path, monkeypatch):
    """--effort off はフォールバックでも reasoning を送らない。"""
    run_pass, seen, review = _run_main_gemini_to_openrouter(
        tmp_path, monkeypatch, "@gemini-cli /quick-review --effort off"
    )
    assert run_pass.call_args.args[4] is None
    assert seen == [None]
    assert "reasoning なし" in review


def test_generate_with_openrouter_empty_content_does_not_rotate_keys():
    """空本文はキーを変えても改善しないため、残りキーを回さない。"""
    data = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": {}}
    with mock.patch("httpx.post", return_value=_openrouter_post_mock(data)) as post:
        with pytest.raises(Exception, match="empty content"):
            ai_review._generate_with_openrouter("openrouter/free", "p", "s", ["k1", "k2"], "high")
    assert post.call_count == 1


def test_run_openrouter_pass_empty_content_skips_keys_of_same_model():
    """空本文/length なら同一モデルの残りキーを飛ばし、次のモデルへ移る。"""
    data = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}], "usage": {}}
    with mock.patch("httpx.post", return_value=_openrouter_post_mock(data)) as post:
        resp, model, used_effort, exc = ai_review._run_openrouter_pass(
            ["model-a", "model-b"], "p", "s", ["k1", "k2", "k3"], None
        )
    assert resp is None and model is None and used_effort is None
    assert exc is not None and "empty content" in str(exc)
    assert post.call_count == 2  # モデルごとに 1 回だけ（キー回転なし）


def test_generate_with_openrouter_length_finish_reason_marks_truncated():
    """本文あり + finish_reason=length は本文を保持し truncated を立てる。"""
    data = {
        "choices": [{"message": {"content": "途中までの本文"}, "finish_reason": "length"}],
        "usage": {},
    }
    with mock.patch("httpx.post", return_value=_openrouter_post_mock(data)):
        resp = ai_review._generate_with_openrouter("openrouter/free", "p", "s", ["k"], "high")
    assert resp.text == "途中までの本文"
    assert resp.truncated is True


def test_generate_with_openrouter_normal_finish_reason_not_truncated():
    """通常終了なら truncated は False。"""
    data = {
        "choices": [{"message": {"content": "完整な本文"}, "finish_reason": "stop"}],
        "usage": {},
    }
    with mock.patch("httpx.post", return_value=_openrouter_post_mock(data)):
        resp = ai_review._generate_with_openrouter("openrouter/free", "p", "s", ["k"], "high")
    assert resp.truncated is False


def test_build_execution_metadata_openrouter_truncated_notice():
    """途中終了レスポンスは実行情報に注記を出し、平常時は出さない。"""
    mock_resp = mock.MagicMock()
    mock_resp.model_version = None
    mock_resp.usage_metadata = None
    mock_resp._retry_count = 0

    def build(truncated):
        return ai_review.build_execution_metadata(
            model_name="openrouter/free",
            resolved_model_name="openrouter/free",
            model_info={"name": "openrouter/free"},
            response=mock_resp,
            files_modified_count="N/A",
            duration=1.0,
            response_truncated=truncated,
        )

    assert "途中終了" in build(True)
    assert "途中終了" not in build(False)


def test_main_openrouter_fallback_reports_truncated_body(tmp_path, monkeypatch):
    """フォールバックで途中終了した本文が投稿本文と実行情報の両方に残る。"""
    response = ai_review.OpenRouterResponse(text="途中本文", truncated=True)
    _, _, review = _run_main_gemini_to_openrouter(tmp_path, monkeypatch, response=response)
    assert "途中本文" in review
    assert "途中終了" in review


def test_opencode_model_id_validation():
    """opencode / opencode-go の接頭辞と安全な文字だけを受理する。"""
    assert _is_opencode_model_id("opencode/deepseek-v4.1-flash-free")
    assert _is_opencode_model_id("opencode-go/future-model")
    assert not _is_opencode_model_id("opencode/../etc/passwd")
    assert not _is_opencode_model_id("gemini-4-hydrogen")
    assert not _is_opencode_model_id("opencode/evil;rm")


def test_resolve_model_undeclared_opencode_model_needs_no_predefinition():
    """事前定義のない opencode モデルもコスト不明・variant なしで解決できる。"""
    info, name = resolve_model("opencode/future-model", _prod_model_config())
    assert name == "opencode/future-model"
    assert info["provider"] == "opencode"
    assert info["input_cost_per_1m"] is None
    assert info["variants"] == []


def test_resolve_model_opencode_go_deepseek_flash():
    """Go の有料 DeepSeek は正式名・コスト・variant を保持して解決される。"""
    info, name = resolve_model("opencode-go/deepseek-v4.1-flash", _prod_model_config())
    assert name == "opencode-go/deepseek-v4.1-flash"
    assert info["provider"] == "opencode"
    assert info["input_cost_per_1m"] == 0.15
    assert info["output_cost_per_1m"] == 0.6
    assert info["variants"] == ["low", "high", "max"]
    assert info["default_variant"] == "high"


def test_opencode_variant_default_and_effort():
    """既定 variant と --effort の上書き、表示の出所を検証する。"""
    info = _prod_model_config()["opencode-deepseek-free"]
    assert _opencode_variant(info, None) == ("high", "high （既定）")
    variant, display = _opencode_variant(info, "max")
    assert variant == "max"
    assert display.startswith("max ")
    assert "（--effort 指定）" in display


def test_opencode_variant_unsupported_effort_falls_back_to_default():
    """非対応 effort はレビューを止めず、既定 variant とその旨を表示する。"""
    info = _prod_model_config()["opencode-hy4-free"]
    variant, display = _opencode_variant(info, "medium")
    assert variant == "high"
    assert display == "high （--effort medium は非対応のため既定）"
    assert "(既定)（" not in display
    assert _opencode_variant(info, "none")[0] == "none"


def test_opencode_variant_off_without_none_variant():
    """none variant が無いモデルでも --effort off はエラーにせず実送信を表示する。"""
    info = _prod_model_config()["opencode-deepseek-free"]
    variant, display = _opencode_variant(info, "off")
    assert variant is None
    assert "OFF 指定（variant 非対応のためモデル既定）" == display


def test_opencode_variant_for_undeclared_model_uses_effort():
    """未定義モデルでも安全な effort なら #variant として渡す。"""
    info, _ = resolve_model("opencode/future-model", _prod_model_config())
    variant, display = _opencode_variant(info, "xhigh")
    assert variant == "xhigh"
    assert "（--effort 指定）" in display
    assert _opencode_variant(info, "bad;variant")[0] is None


def test_generate_with_opencode_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    info = _prod_model_config()["opencode-hy4-free"]
    with pytest.raises(RuntimeError, match="OPENCODE_API_KEY"):
        _generate_with_opencode("opencode/hy4-flash-free", "prompt", "system", None, info)


def test_generate_with_opencode_passes_variant_and_attached_prompt(monkeypatch):
    """--model に #variant を付け、プロンプトは --file の一時ファイルで渡す。"""
    monkeypatch.setenv("OPENCODE_API_KEY", "secret")
    monkeypatch.setenv("GH_TOKEN", "must-not-leak")
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-leak")
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-leak")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = dict(kwargs["env"])
        captured["cwd"] = kwargs.get("cwd")
        captured["stdin"] = kwargs.get("stdin")
        captured["encoding"] = kwargs.get("encoding")
        captured["key"] = kwargs["env"]["OPENCODE_API_KEY"]
        attached = cmd[cmd.index("--file") + 1]
        with open(attached, encoding="utf-8") as f:
            captured["attached"] = f.read()
        captured["attached_path"] = attached
        agent_path = os.path.join(
            os.path.dirname(attached), ".opencode", "agents", "reviewer.md"
        )
        with open(agent_path, encoding="utf-8") as f:
            captured["agent"] = f.read()
        config_path = os.path.join(os.path.dirname(attached), "opencode.json")
        with open(config_path, encoding="utf-8") as f:
            captured["config"] = json.load(f)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="**判定**: 【✅ APPROVE】\n**要約**: 問題なし\n",
            stderr="",
        )

    monkeypatch.setattr(ai_review.subprocess, "run", fake_run)
    info = _prod_model_config()["opencode-deepseek-free"]
    resp, display = _generate_with_opencode(
        "opencode/deepseek-v4.1-flash-free", "【差分】xyz", "system instruction", "max", info
    )

    assert "**判定**" in resp.text
    assert resp.model_version == "opencode/deepseek-v4.1-flash-free#max"
    assert display.startswith("max ")
    assert captured["cmd"][:4] == ["opencode", "run", "--standalone", "--archived"]
    assert captured["cmd"][captured["cmd"].index("--agent") + 1] == "reviewer"
    assert captured["cmd"][-1] == ai_review.OPENCODE_PROMPT
    assert "system instruction" in captured["agent"]  # システム指示は agent 側に分離
    assert captured["config"]["permissions"] == [
        {"action": "*", "resource": "*", "effect": "deny"}
    ]
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == (
        "opencode/deepseek-v4.1-flash-free#max"
    )
    assert captured["attached"] == "【差分】xyz"
    assert captured["key"] == "secret"
    # GH_TOKEN / 他プロバイダのキーはサブプロセスへ渡さない（許可リスト方式）。
    for leaked in ("GH_TOKEN", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        assert leaked not in captured["env"]
    assert "PATH" in captured["env"]
    assert "HOME" in captured["env"]
    assert captured["env"].get("HTTPS_PROXY") == "http://proxy.example:8080"
    # 一時ディレクトリを cwd にし、stdin を閉じる（リポジトリ／対話チャネルを触らせない）。
    assert captured["cwd"] == os.path.dirname(captured["attached_path"])
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["encoding"] == "utf-8"
    assert not os.path.exists(captured["attached_path"])  # 一時ディレクトリは後始末される


def test_generate_with_opencode_empty_output_raises(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "secret")
    monkeypatch.setattr(ai_review.time, "sleep", mock.Mock())
    monkeypatch.setattr(
        ai_review.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )
    info = _prod_model_config()["opencode-hy4-free"]
    with pytest.raises(RuntimeError, match="empty or invalid output"):
        _generate_with_opencode("opencode/hy4-flash-free", "p", "s", None, info)


def test_generate_with_opencode_retries_invalid_output(monkeypatch):
    """判定セクションの無い stdout は 1 回再試行し、改善なければ例外にする。"""
    monkeypatch.setenv("OPENCODE_API_KEY", "secret")
    bad = subprocess.CompletedProcess([], 0, stdout="\x1b[2KInstalling...\n", stderr="")
    run = mock.Mock(return_value=bad)
    monkeypatch.setattr(ai_review.subprocess, "run", run)
    monkeypatch.setattr(ai_review.time, "sleep", mock.Mock())
    info = _prod_model_config()["opencode-hy4-free"]
    with pytest.raises(RuntimeError, match="empty or invalid output"):
        _generate_with_opencode("opencode/hy4-flash-free", "p", "s", None, info)
    assert run.call_count == 2


def test_review_output_contract_and_ansi_strip():
    assert _is_review_output_valid("**判定**: 【✅ APPROVE】")
    assert not _is_review_output_valid(" Installing... done ")
    assert "\x1b" not in ai_review._strip_ansi("\x1b[2K**判定**: 【✅ APPROVE】")
    cleaned = ai_review._sanitize_review_output(
        "Installing...\n**判定**: 【✅ APPROVE】\n**要約**: ok\nTools: read, bash\n╰───",
        "oc_sk_test_123",
    )
    assert "Installing" not in cleaned
    assert "Tools:" not in cleaned
    assert "╰" not in cleaned
    assert "**要約**: ok" in cleaned
    cleaned_with_blank = ai_review._sanitize_review_output(
        "**判定**: 【✅ APPROVE】\n**要約**: ok\n\nTools: read, bash", ""
    )
    assert "Tools:" not in cleaned_with_blank


def _run_main_opencode(
    tmp_path,
    monkeypatch,
    additional_context=None,
    *,
    model_key="opencode/deepseek-v4.1-flash-free",
    opencode_key="secret",
    opencode_error=None,
    gemini_error=None,
):
    """OpenCode プライマリを main() レベルで再現する。"""
    _set_review_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MODEL_KEY", model_key)
    if additional_context is None:
        monkeypatch.delenv("ADDITIONAL_CONTEXT", raising=False)
    else:
        monkeypatch.setenv("ADDITIONAL_CONTEXT", additional_context)
    if opencode_key is None:
        monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENCODE_API_KEY", opencode_key)

    oc_response = ai_review.OpenCodeResponse(text="OpenCode 本文")

    def fake_opencode(*args, **kwargs):
        if opencode_error is not None:
            raise opencode_error
        return oc_response, "high （既定）"

    gemini_response = _FakeResponse("Gemini 本文")
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(ai_review, "_collect_gemini_keys", return_value=["key"]))
        stack.enter_context(
            mock.patch.object(ai_review, "_collect_openrouter_keys", return_value=["or-key"])
        )
        stack.enter_context(
            mock.patch.object(ai_review.subprocess, "check_output", return_value=diff.encode())
        )
        stack.enter_context(mock.patch.object(ai_review, "_fetch_pr_metadata", return_value=""))
        stack.enter_context(
            mock.patch.object(ai_review, "_fetch_pr_comments", return_value=("", None))
        )
        stack.enter_context(mock.patch.object(ai_review, "_fetch_file_contents", return_value=""))
        oc = stack.enter_context(
            mock.patch.object(ai_review, "_generate_with_opencode", side_effect=fake_opencode)
        )
        gc = stack.enter_context(
            mock.patch.object(
                ai_review,
                "_generate_with_model_chain",
                return_value=(
                    (None, None, None, None, gemini_error)
                    if gemini_error
                    else (
                        gemini_response,
                        "gemini-4-hydrogen",
                        {
                            "name": "gemini-4-hydrogen",
                            "input_cost_per_1m": 1.50,
                            "output_cost_per_1m": 7.50,
                            "max_diff_chars": 2000000,
                            "thinking_level": "HIGH",
                        },
                        None,
                        None,
                    )
                ),
            )
        )
        orp = stack.enter_context(
            mock.patch.object(
                ai_review,
                "_run_openrouter_pass",
                return_value=(
                    ai_review.OpenRouterResponse(text="OR 本文"),
                    "openrouter/free",
                    "high",
                    None,
                ),
            )
        )
        stack.enter_context(
            mock.patch("ai_review.subprocess.run", return_value=mock.Mock(returncode=0))
        )
        ai_review.main()
    return oc, gc, orp, (tmp_path / "review.md").read_text(encoding="utf-8")


def test_main_opencode_success_does_not_call_other_providers(tmp_path, monkeypatch):
    oc, gc, orp, review = _run_main_opencode(tmp_path, monkeypatch)
    assert oc.call_count == 1
    assert gc.call_count == 0
    assert orp.call_count == 0
    assert "OpenCode 本文" in review
    assert "high （既定）" in review
    assert "追加コスト: $0" in review
    assert "取得できませんでした" not in review


def test_main_paid_opencode_model_does_not_report_zero_cost(tmp_path, monkeypatch):
    """有料モデルでは実行情報に $0（無料モデル）と表示しない。"""
    oc, gc, orp, review = _run_main_opencode(
        tmp_path, monkeypatch, model_key="opencode-go/deepseek-v4.1-flash"
    )
    assert oc.call_count == 1
    assert gc.call_count == 0
    assert "有料モデル" in review
    assert "0.15" in review
    assert "$0（無料モデル）" not in review


def test_main_opencode_failure_falls_back_to_gemini(tmp_path, monkeypatch):
    oc, gc, orp, review = _run_main_opencode(
        tmp_path, monkeypatch, opencode_error=RuntimeError("CLI unavailable")
    )
    assert oc.call_count == 1
    assert gc.call_count == 1
    assert orp.call_count == 0
    assert "Gemini 本文" in review


def test_main_opencode_failure_then_gemini_failure_falls_back_to_openrouter(
    tmp_path, monkeypatch
):
    """OpenCode 失敗 -> Gemini 全滅 -> OpenRouter の 3 段連鎖を担保する。"""
    oc, gc, orp, review = _run_main_opencode(
        tmp_path,
        monkeypatch,
        opencode_error=RuntimeError("CLI unavailable"),
        gemini_error=Exception("Gemini exhausted"),
    )
    assert oc.call_count == 1
    assert gc.call_count == 1
    assert orp.call_count == 1
    assert "OR 本文" in review
    assert "reasoning: high （既定）" in review


def test_main_insufficient_budget_skips_opencode_and_falls_back(tmp_path, monkeypatch):
    """残時間が足りなければ OpenCode を飛ばして Gemini レビューを残す。"""
    monkeypatch.setattr(ai_review, "JOB_TIMEOUT_SECONDS", 1.0)
    oc, gc, orp, review = _run_main_opencode(tmp_path, monkeypatch)
    assert oc.call_count == 0
    assert gc.call_count == 1
    assert gc.call_args.args[0][0][0] == "hydrogen"
    assert "Gemini 本文" in review


def test_main_opencode_without_key_falls_back_to_hydrogen(tmp_path, monkeypatch):
    oc, gc, orp, review = _run_main_opencode(tmp_path, monkeypatch, opencode_key=None)
    assert oc.call_count == 0
    assert gc.call_count == 1
    assert gc.call_args.args[0][0][0] == "hydrogen"
    assert "Gemini 本文" in review


def test_main_rejects_unsafe_opencode_model_from_comment(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as excinfo:
        _run_main_opencode(
            tmp_path,
            monkeypatch,
            "@gemini-cli /quick-review --model opencode/../evil",
        )
    assert excinfo.value.code == 1


def test_main_allows_undeclared_opencode_model_from_comment(tmp_path, monkeypatch):
    """事前定義のない opencode モデルもコメントから指定できる。"""
    oc, _, _, review = _run_main_opencode(
        tmp_path, monkeypatch, "@gemini-cli /quick-review --model opencode/future-model"
    )
    assert oc.call_count == 1
    assert oc.call_args.args[0] == "opencode/future-model"
    assert "OpenCode 本文" in review


def test_main_rejects_unsafe_comment_options_without_truncation(tmp_path, monkeypatch):
    """--model / --effort の途中切り詰めで危険文字列が通らないようにする。"""
    with pytest.raises(SystemExit) as excinfo:
        _run_main_opencode(
            tmp_path,
            monkeypatch,
            "@gemini-cli /quick-review --model opencode/evil;rm",
        )
    assert excinfo.value.code == 1
    with pytest.raises(SystemExit) as excinfo:
        _run_main_opencode(
            tmp_path,
            monkeypatch,
            "@gemini-cli /quick-review --effort high;rm",
        )
    assert excinfo.value.code == 1


def test_generate_with_opencode_redacts_api_key(monkeypatch):
    """CLI stdout にキーが混入しても PR コメントへ出さない。"""
    monkeypatch.setenv("OPENCODE_API_KEY", "oc_sk_test_123")
    stdout = "Installing...\n**判定**: 【✅ APPROVE】 key=oc_sk_test_123\n"
    monkeypatch.setattr(
        ai_review.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=""),
    )
    info = _prod_model_config()["opencode-hy4-free"]
    resp, _ = _generate_with_opencode("opencode/hy4-flash-free", "p", "s", None, info)
    assert "oc_sk_test_123" not in resp.text
    assert "[REDACTED]" in resp.text
    assert "Installing..." not in resp.text


def test_generate_with_opencode_retries_transient_failure(monkeypatch):
    """429/5xx 等の一時失敗は 1 回だけ再試行する。"""
    monkeypatch.setenv("OPENCODE_API_KEY", "secret")
    transient = mock.Mock(returncode=1, stderr="HTTP 429 rate limit", stdout="")
    success = subprocess.CompletedProcess(
        [], 0, stdout="**判定**: 【✅ APPROVE】\n**要約**: 問題なし\n", stderr=""
    )
    run = mock.Mock(side_effect=[transient, success])
    monkeypatch.setattr(ai_review.subprocess, "run", run)
    sleep = mock.Mock()
    monkeypatch.setattr(ai_review.time, "sleep", sleep)

    info = _prod_model_config()["opencode-deepseek-free"]
    resp, _ = _generate_with_opencode("opencode/deepseek-v4.1-flash-free", "p", "s", None, info)
    assert "**判定**" in resp.text
    assert run.call_count == 2
    sleep.assert_called_once_with(ai_review.OPENCODE_RETRY_DELAY)


def test_annotation_safe_strips_newlines():
    assert ai_review._annotation_safe("line1\nline2\rline3") == "line1 line2 line3"


def _run_main_default_priority(
    tmp_path,
    monkeypatch,
    *,
    gemini_error=None,
    opencode_error=None,
    openrouter_failure=False,
    existing_comment=None,
):
    """既定（Gemini -> Go DeepSeek -> 無料 DeepSeek -> OpenRouter）の連鎖を再現する。"""
    _set_review_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MODEL_KEY", "hydrogen")
    monkeypatch.setenv("OPENCODE_API_KEY", "secret")
    monkeypatch.delenv("ADDITIONAL_CONTEXT", raising=False)

    errors = (
        list(opencode_error)
        if isinstance(opencode_error, (list, tuple))
        else [opencode_error]
    )

    def fake_opencode(*args, **kwargs):
        if errors:
            err = errors.pop(0)
            if err is not None:
                raise err
        return ai_review.OpenCodeResponse(text="OpenCode 本文"), "high （既定）"

    gemini_result = (
        (None, None, None, None, gemini_error)
        if gemini_error
        else (_FakeResponse("Gemini 本文"), "gemini-4-hydrogen", {"name": "gemini-4-hydrogen"}, None, None)
    )
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(ai_review, "_collect_gemini_keys", return_value=["key"]))
        stack.enter_context(
            mock.patch.object(ai_review, "_collect_openrouter_keys", return_value=["or-key"])
        )
        stack.enter_context(
            mock.patch.object(ai_review.subprocess, "check_output", return_value=diff.encode())
        )
        stack.enter_context(mock.patch.object(ai_review, "_fetch_pr_metadata", return_value=""))
        stack.enter_context(
            mock.patch.object(
                ai_review, "_fetch_pr_comments", return_value=("", existing_comment)
            )
        )
        stack.enter_context(mock.patch.object(ai_review, "_fetch_file_contents", return_value=""))
        gc = stack.enter_context(
            mock.patch.object(ai_review, "_generate_with_model_chain", return_value=gemini_result)
        )
        oc = stack.enter_context(
            mock.patch.object(ai_review, "_generate_with_opencode", side_effect=fake_opencode)
        )
        or_result = (
            (None, None, None, Exception("OpenRouter exhausted"))
            if openrouter_failure
            else (
                ai_review.OpenRouterResponse(text="OR 本文"),
                "openrouter/free",
                "high",
                None,
            )
        )
        orp = stack.enter_context(
            mock.patch.object(ai_review, "_run_openrouter_pass", return_value=or_result)
        )
        stack.enter_context(
            mock.patch("ai_review.subprocess.run", return_value=mock.Mock(returncode=0))
        )
        ai_review.main()
    return gc, oc, orp, (tmp_path / "review.md").read_text(encoding="utf-8")


def test_default_priority_gemini_then_go_deepseek(tmp_path, monkeypatch):
    """gemini-3.8-flash 全滅後は Go 版 DeepSeek が先に走る。"""
    gc, oc, orp, review = _run_main_default_priority(
        tmp_path, monkeypatch, gemini_error=Exception("Gemini exhausted")
    )
    assert gc.call_count == 1
    assert oc.call_count == 1
    assert orp.call_count == 0
    assert oc.call_args.args[0] == "opencode-go/deepseek-v4.1-flash"
    assert "OpenCode 本文" in review


def test_default_priority_go_deepseek_failure_then_free_deepseek(tmp_path, monkeypatch):
    """Go 版が失敗したら無料 DeepSeek に落ちる。"""
    gc, oc, orp, review = _run_main_default_priority(
        tmp_path,
        monkeypatch,
        gemini_error=Exception("Gemini exhausted"),
        opencode_error=[RuntimeError("Go key unavailable"), None],
    )
    assert gc.call_count == 1
    assert oc.call_count == 2
    assert [c.args[0] for c in oc.call_args_list] == [
        "opencode-go/deepseek-v4.1-flash",
        "opencode/deepseek-v4.1-flash-free",
    ]
    assert orp.call_count == 0
    assert "OpenCode 本文" in review


def test_default_priority_deepseek_failure_then_openrouter(tmp_path, monkeypatch):
    gc, oc, orp, review = _run_main_default_priority(
        tmp_path,
        monkeypatch,
        gemini_error=Exception("Gemini exhausted"),
        opencode_error=[RuntimeError("Go failed"), RuntimeError("Free failed")],
    )
    assert gc.call_count == 1
    assert oc.call_count == 2
    assert orp.call_count == 1
    assert "OR 本文" in review


def test_all_providers_fail_still_publishes_review(tmp_path, monkeypatch):
    """全プロバイダ失敗でも review.md に失敗レビューを残す。"""
    with pytest.raises(SystemExit) as excinfo:
        _run_main_default_priority(
            tmp_path,
            monkeypatch,
            gemini_error=Exception("Gemini exhausted"),
            opencode_error=[RuntimeError("Go failed"), RuntimeError("Free failed")],
            openrouter_failure=True,
        )
    assert excinfo.value.code == 1
    failure_review = (tmp_path / "review.md").read_text(encoding="utf-8")
    assert "生成に失敗しました" in failure_review
    assert "opencode/deepseek-v4.1-flash-free" not in failure_review


def test_openrouter_fallback_budget_trims_models_and_attempts(
    tmp_path, monkeypatch
):
    """既定予算 300 のまま残時間が少ない時だけパス・モデル・試行を削る。"""
    monkeypatch.setattr(ai_review, "_time_left_seconds", lambda start: 100.0)
    gc, oc, orp, review = _run_main_default_priority(
        tmp_path, monkeypatch, gemini_error=Exception("Gemini exhausted")
    )
    assert orp.call_count == 1
    assert orp.call_args.kwargs["max_attempts"] == 1
    # reasoning パスを落とし、先頭モデル＋安全網ルーターだけを残す。
    assert orp.call_args.args[4] is None
    assert orp.call_args.args[0] == [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "openrouter/free",
    ]
    assert "OR 本文" in review


def test_effort_off_keeps_all_openrouter_models_with_full_budget(
    tmp_path, monkeypatch
):
    """--effort off は予算充足ならモデルを削らず reasoning も送らない。"""
    run_pass, seen, review = _run_main_gemini_to_openrouter(
        tmp_path, monkeypatch, "@gemini-cli /quick-review --effort off"
    )
    assert len(run_pass.call_args.args[0]) == 3
    assert seen == [None]
    assert "reasoning: " not in review


def test_openrouter_max_attempts_limits_retries(monkeypatch):
    """max_attempts=1 なら 429 でも 1 回で打ち切る。"""
    resp = mock.MagicMock()
    resp.status_code = 429
    resp.text = "rate limit"
    monkeypatch.setattr(ai_review.time, "sleep", mock.Mock())
    with mock.patch("httpx.post", return_value=resp) as post:
        with pytest.raises(Exception, match="OpenRouter API Error 429"):
            ai_review._generate_with_openrouter(
                "openrouter/free", "p", "s", ["key"], max_attempts=1
            )
        assert post.call_count == 1
        post.reset_mock()
        with pytest.raises(Exception, match="OpenRouter API Error 429"):
            ai_review._generate_with_openrouter("openrouter/free", "p", "s", ["key"])
        assert post.call_count == ai_review.MAX_ATTEMPTS


def test_build_review_prompt_respects_model_diff_limit():
    """フォールバック先の上限で差分が切り詰められる。"""
    prompt, truncated = ai_review._build_review_prompt(
        "meta", "comments", "files", "previous", "x" * 600000, 500000
    )
    assert truncated
    assert "x" * 500000 in prompt
    assert "x" * 500001 not in prompt


def test_failure_review_does_not_overwrite_existing_comment(
    tmp_path, monkeypatch
):
    """生成全滅時は既存レビューを上書きせず、失敗レビューを別投稿し exit 1 する。"""
    published = {}

    def fake_publish(text, repo, pr, existing):
        published["existing"] = existing
        (tmp_path / "review.md").write_text(text, encoding="utf-8")

    monkeypatch.setattr(ai_review, "_publish_review", fake_publish)
    with pytest.raises(SystemExit) as excinfo:
        _run_main_default_priority(
            tmp_path,
            monkeypatch,
            gemini_error=Exception("Gemini exhausted"),
            opencode_error=[RuntimeError("Go failed"), RuntimeError("Free failed")],
            openrouter_failure=True,
            existing_comment={"id": 1, "body": "既存の正常レビュー"},
        )
    assert excinfo.value.code == 1
    assert published["existing"] is None
    assert "生成に失敗しました" in (tmp_path / "review.md").read_text(encoding="utf-8")


def test_opencode_timeout_budget_fits_job_limit():
    """2 試行＋リトライ待機が予算（ジョブ全体 - 予備時間）内に収まること。"""
    assert (
        ai_review.OPENCODE_ATTEMPT_BUDGET
        < ai_review.JOB_TIMEOUT_SECONDS - ai_review.OPENROUTER_FALLBACK_BUDGET
    )


def test_opencode_transient_detection_uses_word_boundaries():
    assert ai_review._is_opencode_transient("HTTP 429 too many requests")
    assert ai_review._is_opencode_transient("HTTP 503 service unavailable")
    assert not ai_review._is_opencode_transient("context 50000 tokens")
    assert not ai_review._is_opencode_transient("HTTP-1500 error")
    assert not ai_review._is_opencode_transient("401 unauthorized")


def test_generate_with_opencode_does_not_retry_auth_error(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "oc_sk_test_123")
    run = mock.Mock(
        return_value=mock.Mock(
            returncode=1,
            stderr="401 " + "x" * 991 + "oc_sk_test_123" + "y" * 20,
            stdout="",
        )
    )
    monkeypatch.setattr(ai_review.subprocess, "run", run)
    monkeypatch.setattr(ai_review.time, "sleep", mock.Mock())
    info = _prod_model_config()["opencode-deepseek-free"]
    with pytest.raises(RuntimeError, match="401") as excinfo:
        _generate_with_opencode("opencode/deepseek-v4.1-flash-free", "p", "s", None, info)
    assert "oc_sk_test_123" not in str(excinfo.value)
    assert "oc_sk_test" not in str(excinfo.value)  # 境界をまたいで一部も残さない
    assert "[RED" in str(excinfo.value)  # 境界でマーカー自体が切れてもキーは残らない
    assert run.call_count == 1


def test_generate_with_opencode_retries_timeout_once_then_raises(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "secret")
    timeout = subprocess.TimeoutExpired(["opencode"], ai_review.OPENCODE_TIMEOUT)
    run = mock.Mock(side_effect=[timeout, timeout])
    monkeypatch.setattr(ai_review.subprocess, "run", run)
    sleep = mock.Mock()
    monkeypatch.setattr(ai_review.time, "sleep", sleep)
    info = _prod_model_config()["opencode-deepseek-free"]
    with pytest.raises(subprocess.TimeoutExpired):
        _generate_with_opencode("opencode/deepseek-v4.1-flash-free", "p", "s", None, info)
    assert run.call_count == 2
    sleep.assert_called_once_with(ai_review.OPENCODE_RETRY_DELAY)


def test_default_priority_gemini_success_skips_other_providers(tmp_path, monkeypatch):
    gc, oc, orp, review = _run_main_default_priority(tmp_path, monkeypatch)
    assert gc.call_count == 1
    assert oc.call_count == 0
    assert orp.call_count == 0
    assert "Gemini 本文" in review


def _run_main_openrouter_primary(tmp_path, monkeypatch):
    _set_review_env(monkeypatch, tmp_path)
    monkeypatch.setenv("MODEL_KEY", "openrouter/free")
    monkeypatch.setenv("OPENCODE_API_KEY", "secret")
    monkeypatch.delenv("ADDITIONAL_CONTEXT", raising=False)
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(ai_review, "_collect_gemini_keys", return_value=["key"]))
        stack.enter_context(
            mock.patch.object(ai_review, "_collect_openrouter_keys", return_value=["or-key"])
        )
        stack.enter_context(
            mock.patch.object(ai_review.subprocess, "check_output", return_value=diff.encode())
        )
        stack.enter_context(mock.patch.object(ai_review, "_fetch_pr_metadata", return_value=""))
        stack.enter_context(
            mock.patch.object(ai_review, "_fetch_pr_comments", return_value=("", None))
        )
        stack.enter_context(mock.patch.object(ai_review, "_fetch_file_contents", return_value=""))
        gc = stack.enter_context(
            mock.patch.object(ai_review, "_generate_with_model_chain", return_value=(None, None, None, None, None))
        )
        oc = stack.enter_context(
            mock.patch.object(
                ai_review,
                "_generate_with_opencode",
                return_value=(ai_review.OpenCodeResponse(text="暗黙に呼ばれた"), "high"),
            )
        )
        orp = stack.enter_context(
            mock.patch.object(
                ai_review,
                "_run_openrouter_pass",
                return_value=(
                    ai_review.OpenRouterResponse(text="OR 本文"),
                    "openrouter/free",
                    "high",
                    None,
                ),
            )
        )
        stack.enter_context(
            mock.patch("ai_review.subprocess.run", return_value=mock.Mock(returncode=0))
        )
        ai_review.main()
    return gc, oc, orp, (tmp_path / "review.md").read_text(encoding="utf-8")


def test_openrouter_primary_skips_opencode_fallback(tmp_path, monkeypatch):
    gc, oc, orp, review = _run_main_openrouter_primary(tmp_path, monkeypatch)
    assert gc.call_count == 0
    assert oc.call_count == 0
    assert orp.call_count == 1
    assert "暗黙に呼ばれた" not in review
    assert "OR 本文" in review

