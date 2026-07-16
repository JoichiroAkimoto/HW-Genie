import os
import time
from google import genai
from google.genai import types
import subprocess
import json
import sys
import datetime
import re
import io
from html.parser import HTMLParser
from unidiff import PatchSet

# Marker used to identify comments posted by this reviewer.
REVIEW_MARKER = "<!-- ai-pr-reviewer-comment -->"
REVIEW_HEADER_RE = re.compile(r"^#{1,4}\s*🤖\s*AI\s*コードレビュー\s*\n*", re.MULTILINE)


class _DetailsExtractor(HTMLParser):
    """HTMLパーサーを用いて、特定の <summary> を持つ <details> ブロックを
    抽出・削除する。正規表現では扱えない入れ子の <details> にも対応。

    summary_keyword を含む <details> ブロックを「対象」とみなし、それ以外の
    部分はそのまま保持する。
    """

    def __init__(self, summary_keyword: str):
        super().__init__(convert_charrefs=True)
        self.summary_keyword = summary_keyword
        self._depth = 0
        self._capturing = False
        self._capture_depth = 0
        self._buffer = []
        self._current_summary = None
        self._in_summary = False
        self.kept_parts = []
        self.captured_blocks = []

    def handle_starttag(self, tag, attrs):
        if tag == "details":
            self._depth += 1
            if not self._capturing:
                # 新しい details 開始: この中に該当 summary があるか確認するため
                # 一旦キャプチャ候補としてバッファに蓄積開始
                self._capturing = True
                self._capture_depth = self._depth
                self._current_summary = None
                self._in_summary = False
                self._buffer = [self.get_starttag_text()]
            else:
                self._buffer.append(self.get_starttag_text())
        elif tag == "summary" and self._capturing:
            self._in_summary = True
            self._buffer.append(self.get_starttag_text())
        else:
            if self._capturing:
                self._buffer.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag == "summary" and self._capturing:
            self._in_summary = False
            self._buffer.append("</summary>")
            return
        if tag == "details":
            if self._capturing:
                self._buffer.append("</details>")
                if self._depth == self._capture_depth:
                    # この details ブロックの終了
                    block = "".join(self._buffer)
                    if (
                        self._current_summary is not None
                        and self.summary_keyword in self._current_summary
                    ):
                        self.captured_blocks.append(block)
                    else:
                        self.kept_parts.append(block)
                    self._capturing = False
                    self._current_summary = None
                    self._buffer = []
                self._depth -= 1
        else:
            if self._capturing:
                self._buffer.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._capturing:
            self.kept_parts.append(data)
            return
        if self._in_summary:
            if self._current_summary is None:
                self._current_summary = data
            else:
                self._current_summary += data
        self._buffer.append(data)


def _extract_details_blocks(html: str, summary_keyword: str) -> tuple[str, list[str]]:
    """summary_keyword を含む <details> ブロックを抽出し、それを除いた HTML を返す。

    Returns:
        tuple: (ブロック除去後の文字列, 抽出されたブロックのリスト)
    """
    parser = _DetailsExtractor(summary_keyword)
    parser.feed(html)
    return "".join(parser.kept_parts), parser.captured_blocks


def strip_review_metadata(review_body: str) -> tuple[str, str]:
    """前回のレビューコメントからメタデータを分離する。

    HTMLパーサーを使用するため、AI が生成した本文内に入れ子の <details> が
    含まれていても安全に実行情報ブロックのみを抽出・除去できる。

    Returns:
        tuple: (レビュー本文（ヘッダ・実行情報・マーカーを除去済み）, 実行情報セクション)
    """
    if not review_body:
        return "", ""

    # HTMLコメントマーカーを除去
    body = review_body.replace(REVIEW_MARKER, "").strip()

    # 1. 「⚡ 今回の実行情報」または「⚡ 実行情報」の<details>ブロックを抽出・除去
    body, current_blocks = _extract_details_blocks(body, "実行情報")
    exec_info = ""
    for block in current_blocks:
        # ⚡ (今回の) 実行情報 のみを「今回」として採用
        if "📝" not in block:
            exec_info = block
            break
    if not exec_info and current_blocks:
        exec_info = current_blocks[0]

    # 2. 「📝 前回の実行情報」の<details>ブロックも除去（既に除去済みだが念のため）
    body, _ = _extract_details_blocks(body, "前回の実行情報")

    # 末尾に残った水平線 (---) を除去
    body = re.sub(r"\n*---\s*$", "", body).strip()

    # "### 🤖 AI コードレビュー" ヘッダを除去（行頭の # レベルは問わない）
    body = REVIEW_HEADER_RE.sub("", body).strip()

    return body, exec_info


def load_model_config():
    config_path = os.path.join(os.path.dirname(__file__), "models.json")
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading models.json: {e}")
        # Fallback basic config
        return {
            "flash-lite": {
                "name": "gemini-3.1-flash-lite",
                "aliases": ["gemini-flash-lite-latest", "gemini-flash-lite"],
                "input_cost_per_1m": 0.25,
                "output_cost_per_1m": 1.50,
                "max_diff_chars": 2000000,
            }
        }


DEFAULT_MODEL_KEY = "flash-lite"


def resolve_model(model_key: str, model_config: dict) -> tuple[dict, str]:
    """models.json のキー・name・aliases のいずれかに一致するモデル設定を解決する。

    model_key が未知の文字列（例: ``gemini-flash-lite-latest``）の場合は、
    部分一致・キーワード推測で価格/上限を当て、正式名としてそのままの文字列を返す。

    Returns:
        tuple: (model_info, model_name)
    """
    # 1. 直接キー / name / aliases の完全一致
    for key, info in model_config.items():
        aliases = info.get("aliases", [])
        if model_key == key or model_key == info.get("name") or model_key in aliases:
            # キーまたは正式名で一致: 正式名を返す
            # エイリアスで一致: 表示用にエイリアス自身を返す（API で正規名解決のため）
            display_name = info["name"] if model_key in (key, info.get("name")) else model_key
            return info, display_name

    # 2. 部分一致（aliases / name / key のいずれかが model_key を含む、または逆）
    matched_info = None
    for key, info in model_config.items():
        candidates = [key, info.get("name", "")] + info.get("aliases", [])
        if any(model_key in c or c in model_key for c in candidates if c):
            matched_info = info
            break

    # 3. キーワードによる推測
    if not matched_info:
        if "flash-lite" in model_key:
            matched_info = model_config.get("flash-lite")
        elif "flash" in model_key:
            matched_info = model_config.get("flash")
        elif "gemma" in model_key:
            matched_info = model_config.get("gemma")

    if matched_info:
        return (
            {
                "name": model_key,
                "input_cost_per_1m": matched_info["input_cost_per_1m"],
                "output_cost_per_1m": matched_info["output_cost_per_1m"],
                "max_diff_chars": matched_info["max_diff_chars"],
            },
            model_key,
        )

    # 4. それでも不明: ゼロコスト・デフォルト上限でフォールバック
    return (
        {
            "name": model_key,
            "input_cost_per_1m": 0.0,
            "output_cost_per_1m": 0.0,
            "max_diff_chars": 2000000,
        },
        model_key,
    )


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY is not set.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    # 日本時間のタイムゾーン設定
    JST = datetime.timezone(datetime.timedelta(hours=9), "JST")

    model_config = load_model_config()
    additional_context = os.environ.get("ADDITIONAL_CONTEXT", "")
    env_model_key = os.environ.get("MODEL_KEY", DEFAULT_MODEL_KEY)

    model_key = env_model_key
    # Parse --model from additional_context (must be start of line or preceded by whitespace)
    model_match = re.search(
        r"(?m)(?:^|\s+)--model\s+([\w.-]+)", additional_context, re.IGNORECASE
    )
    if model_match:
        model_key = model_match.group(1)

    # models.json のキー/name/aliases に基づいて model_info と model_name を解決
    model_info, model_name = resolve_model(model_key, model_config)

    print(f"Using model: {model_key} ({model_name})")

    # エイリアス（gemini-flash-lite-latest 等）が指定された場合、API で正規名を解決して
    # 表示用に正式名（gemini-3.1-flash-lite 等）へ正規化する。失敗時はそのまま表示。
    resolved_model_name = model_name
    if model_key != model_name:
        try:
            api_model_info = client.models.get(model=model_name)
            if (
                api_model_info
                and hasattr(api_model_info, "name")
                and api_model_info.name
            ):
                fetched_name = api_model_info.name
                if fetched_name.startswith("models/"):
                    fetched_name = fetched_name[len("models/"):]
                resolved_model_name = fetched_name
                print(f"Resolved canonical model name from API: {resolved_model_name}")
        except Exception as e:
            print(
                f"Note: Could not resolve canonical model name via API ({e}). Using: {model_name}"
            )

    pr_number = os.environ.get("PR_NUMBER")
    repo = os.environ.get("GITHUB_REPOSITORY")

    if not pr_number or not repo:
        print("Error: PR_NUMBER or GITHUB_REPOSITORY is missing.")
        sys.exit(1)

    try:
        raw_diff = subprocess.check_output(["gh", "pr", "diff", pr_number]).decode(
            "utf-8"
        )
    except Exception as e:
        print(f"Error getting diff: {e}")
        sys.exit(1)

    if not raw_diff:
        print("No diff found.")
        sys.exit(0)

    try:
        patch = PatchSet(io.StringIO(raw_diff))
        filtered_diff = ""
        files_modified_count = 0
        for file in patch:
            path = file.path if hasattr(file, "path") and file.path else ""
            if re.search(
                r"(package-lock\.json|yarn\.lock|bun\.lockb|pnpm-lock\.yaml|poetry\.lock|\.lock|\.svg|\.png|\.jpg|\.jpeg|\.gif|\.mp4|\.zip)$",
                path,
                re.IGNORECASE,
            ):
                continue
            filtered_diff += str(file) + "\n"
            files_modified_count += 1
        diff = filtered_diff
    except Exception as e:
        print(f"Failed to parse diff with unidiff: {e}")
        diff = raw_diff
        files_modified_count = "N/A"

    if not diff.strip():
        print("Diff contains only ignored files.")
        sys.exit(0)

    try:
        comments_json = subprocess.check_output(
            ["gh", "api", f"repos/{repo}/issues/{pr_number}/comments?per_page=100"]
        ).decode("utf-8")
        comments = json.loads(comments_json)
        existing_comment = next(
            (
                c
                for c in comments
                if "<!-- ai-pr-reviewer-comment -->" in c.get("body", "")
            ),
            None,
        )

        previous_review = ""
        previous_exec_info = ""
        if existing_comment:
            raw_previous = existing_comment.get("body", "")
            previous_review, previous_exec_info = strip_review_metadata(raw_previous)
    except Exception as e:
        print(f"Error fetching previous comments: {e}")
        existing_comment = None
        previous_review = ""
        previous_exec_info = ""

    limit = model_info.get("max_diff_chars", 500000)
    is_truncated = False
    if len(diff) > limit:
        diff = diff[:limit]
        is_truncated = True

    prompt = f"""
あなたは非常に厳格で批判的なシニアエンジニアです。
以下のPull Requestの差分（diff）を深く考察し、コードレビューを行ってください。

【前回のレビュー結果】
{previous_review if previous_review else "初回レビューです。"}

【レビューのガイドライン】
1. 前回のレビューがある場合、指摘された「懸念点」や「改善案」が現在の差分で正しく修正されているかを厳格に検証してください。
2. 依然として残っている問題や、新しい変更によって導入された問題がないかを確認してください。
3. 「軽微な指摘（スタイルの好みなど）」と「クリティカルな問題（バグ、セキュリティリスク、重大な設計ミス）」を明確に区別してください。
4. 重大な問題がすべて解消されており、残りが軽微な指摘のみである場合、冒頭に「【COMPLETE】」と明記してください。そうでなければ、問題が解消されるまで「未完了」として扱い、厳しい指摘を続けてください。

フォーマット（必ず日本語で出力すること）：
- **判定**: 【COMPLETE】または【未完了】
- **要約**: 変更点（箇条書き）
- **懸念点**: 重大なバグ、パフォーマンス、セキュリティ（特に前回の指摘が修正されたか）
- **改善案**: コード品質向上
- **称賛**: 良い実装

---
【差分 (diff)】
{diff}
"""

    try:
        start_time = datetime.datetime.now(JST)

        config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_level=os.environ.get("GEMINI_THINKING_LEVEL", "HIGH")
            )
        )

        # Retry logic for errors
        max_retries = 3
        retry_delay = 5
        response = None

        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=model_name, contents=prompt, config=config
                )
                break
            except Exception as e:
                if any(code in str(e) for code in ["503", "429", "500"]):
                    if attempt < max_retries - 1:
                        print(f"Gemini API Error ({e}). Retrying in {retry_delay}s... (Attempt {attempt + 1}/{max_retries})")
                        time.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                print(f"Gemini Review Error: {e}")
                sys.exit(1) # Exit with error for non-retryable or final attempt failure

        if response is None:
            print("Failed to get response from Gemini after retries.")
            sys.exit(1)

        end_time = datetime.datetime.now(JST)
        duration = (end_time - start_time).total_seconds()

        try:
            body = response.text
        except ValueError:
            reason = (
                str(response.candidates[0].finish_reason)
                if response.candidates
                else "UNKNOWN"
            )
            body = f"> [!CAUTION]\n> AIによるレビュー生成が中断されました（理由: {reason}）。\n"

        # 今回の実行情報
        metadata = "\n\n---\n"

        # 前回の実行情報がある場合、折りたたんだ状態で表示
        if previous_exec_info:
            # 「⚡ 今回の実行情報」や「⚡ 実行情報」を「📝 前回の実行情報」に変更
            # かつ、<details open> があれば <details>（openなし）に変更して折りたたむ
            prev_section = re.sub(r"⚡\s*(?:今回の)?\s*実行情報", "📝 前回の実行情報", previous_exec_info)
            prev_section = re.sub(r"<details\s+open\s*>", "<details>", prev_section)
            metadata += f"\n{prev_section}\n\n"

        metadata += "<details open><summary>⚡ 今回の実行情報</summary>\n\n"
        metadata += f"- **モデル**: `{resolved_model_name}`\n"
        metadata += f"- **完了日時**: `{end_time.strftime('%Y-%m-%d %H:%M:%S JST')}`\n"
        metadata += f"- **所要時間**: `{duration:.2f} 秒`\n"
        metadata += f"- **変更ファイル数**: `{files_modified_count}`\n"

        try:
            usage = response.usage_metadata
            in_tokens = usage.prompt_token_count
            out_tokens = usage.candidates_token_count

            # コスト計算 (1M tokens あたりの単価)
            in_cost = (in_tokens / 1_000_000) * model_info["input_cost_per_1m"]
            out_cost = (out_tokens / 1_000_000) * model_info["output_cost_per_1m"]
            total_cost = in_cost + out_cost

            metadata += f"- **トークン**: 入力={in_tokens}, 出力={out_tokens}\n"
            metadata += f"- **推定コスト**: `${total_cost:.6f}`\n"
        except Exception:
            metadata += "- **トークン/コスト**: (取得できませんでした)\n"

        if is_truncated:
            metadata += "- **ステータス**: ⚠️ 差分が長すぎるため切り詰められました。\n"
        metadata += "</details>\n\n<!-- ai-pr-reviewer-comment -->"

        review_text = f"### 🤖 AI コードレビュー\n\n{body}{metadata}"

        with open("review.md", "w") as f:
            f.write(review_text)

        try:
            if existing_comment:
                subprocess.run(
                    [
                        "gh",
                        "api",
                        "-X",
                        "PATCH",
                        f"repos/{repo}/issues/comments/{existing_comment['id']}",
                        "-F",
                        "body=@review.md",
                    ],
                    check=True,
                )
            else:
                subprocess.run(
                    ["gh", "pr", "comment", pr_number, "--body-file", "review.md"],
                    check=True,
                )

        except Exception as api_e:
            print(f"GitHub API Error: {api_e}")
            sys.exit(1)

    except Exception as e:
        print(f"Gemini Review Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
