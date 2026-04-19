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
from unidiff import PatchSet


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
                "name": "gemini-3.1-flash-lite-preview",
                "input_cost_per_1m": 0.25,
                "output_cost_per_1m": 1.50,
                "max_diff_chars": 2000000,
            }
        }


DEFAULT_MODEL_KEY = "flash-lite"


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
        r"(?m)(?:^|\s+)--model\s+([\w-]+)", additional_context, re.IGNORECASE
    )
    if model_match:
        potential_key = model_match.group(1)
        if potential_key in model_config:
            model_key = potential_key
        else:
            print(
                f"Warning: Model '{potential_key}' not found in models.json, using default: {model_key}"
            )

    # Fallback to default if the key is not in config (extra safety)
    if model_key not in model_config:
        model_key = DEFAULT_MODEL_KEY

    model_info = model_config[model_key]
    model_name = model_info["name"]

    print(f"Using model: {model_key} ({model_name})")

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
        if existing_comment:
            previous_review = existing_comment.get("body", "")
    except Exception as e:
        print(f"Error fetching previous comments: {e}")
        existing_comment = None
        previous_review = ""

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

        metadata = "\n\n---\n<details><summary>⚡ 実行情報</summary>\n\n"
        metadata += f"- **モデル**: `{model_name}`\n"
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
