import os
from google import genai
from google.genai import types
import subprocess
import json
import sys
import datetime
import re
import io
from unidiff import PatchSet

MODEL_CONFIG = {
    "flash-lite": {
        "name": "gemini-3.1-flash-lite-preview",
        "input_cost_per_1m": 0.25,
        "output_cost_per_1m": 1.50,
        "max_diff_chars": 2000000,
    },
    "gemma": {
        "name": "gemma-4-31b-it",
        "input_cost_per_1m": 0.0,
        "output_cost_per_1m": 0.0,
        "max_diff_chars": 500000,
    },
}
DEFAULT_MODEL_KEY = "flash-lite"

def main():
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("Error: GEMINI_API_KEY is not set.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    
    # 日本時間のタイムゾーン設定
    JST = datetime.timezone(datetime.timedelta(hours=9), 'JST')
    
    additional_context = os.environ.get('ADDITIONAL_CONTEXT', '')
    env_model_key = os.environ.get('MODEL_KEY', DEFAULT_MODEL_KEY)
    
    model_key = env_model_key
    # Parse --model from additional_context
    model_match = re.search(r'--model\s+([\w-]+)', additional_context)
    if model_match:
        model_key = model_match.group(1)
    
    # Fallback to default if the key is not in config
    if model_key not in MODEL_CONFIG:
        model_key = DEFAULT_MODEL_KEY
        
    model_info = MODEL_CONFIG[model_key]
    model_name = model_info["name"]
    
    pr_number = os.environ.get('PR_NUMBER')
    repo = os.environ.get('GITHUB_REPOSITORY')

    if not pr_number or not repo:
        print("Error: PR_NUMBER or GITHUB_REPOSITORY is missing.")
        sys.exit(1)

    try:
        raw_diff = subprocess.check_output(['gh', 'pr', 'diff', pr_number]).decode('utf-8')
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
            path = file.path if hasattr(file, 'path') and file.path else ""
            if re.search(r'(package-lock\.json|yarn\.lock|bun\.lockb|pnpm-lock\.yaml|poetry\.lock|\.lock|\.svg|\.png|\.jpg|\.jpeg|\.gif|\.mp4|\.zip)$', path, re.IGNORECASE):
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
    
    limit = model_info.get("max_diff_chars", 500000)
    is_truncated = False
    if len(diff) > limit:
        diff = diff[:limit]
        is_truncated = True


    prompt = f"""
以下のPull Requestの差分（diff）を深く考察し（思考レベル: High）、簡潔にレビューしてください。

フォーマット：
- **要約**: 変更点（箇条書き）
- **懸念点**: 重大なバグ、パフォーマンス、セキュリティ
- **改善案**: コード品質向上
- **称賛**: 良い実装

---
{diff}
"""

    try:
        start_time = datetime.datetime.now(JST)
        config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_level=os.environ.get('GEMINI_THINKING_LEVEL', 'HIGH')
            )
        )
        
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=config
        )
        end_time = datetime.datetime.now(JST)
        duration = (end_time - start_time).total_seconds()
        
        try:
            body = response.text
        except ValueError:
            reason = str(response.candidates[0].finish_reason) if response.candidates else "UNKNOWN"
            body = f"> [!CAUTION]\n> AIによるレビュー生成が中断されました（理由: {reason}）。\n"

        metadata = "\n\n---\n<details><summary>⚡ Execution Info</summary>\n\n"
        metadata += f"- **Model**: `{model_name}`\n"
        metadata += f"- **Completed at**: `{end_time.strftime('%Y-%m-%d %H:%M:%S JST')}`\n"
        metadata += f"- **Duration**: `{duration:.2f} seconds`\n"
        metadata += f"- **Files modified**: `{files_modified_count}`\n"
        
        try:
            usage = response.usage_metadata
            in_tokens = usage.prompt_token_count
            out_tokens = usage.candidates_token_count
            
            # コスト計算 (1M tokens あたりの単価)
            in_cost = (in_tokens / 1_000_000) * model_info["input_cost_per_1m"]
            out_cost = (out_tokens / 1_000_000) * model_info["output_cost_per_1m"]
            total_cost = in_cost + out_cost
            
            metadata += f"- **Tokens**: In={in_tokens}, Out={out_tokens}\n"
            metadata += f"- **Estimated Cost**: `${total_cost:.6f}`\n"
        except Exception:
            metadata += "- **Tokens/Cost**: (Usage metadata not available)\n"
            
        if is_truncated:
            metadata += "- **Status**: ⚠️ Diff was truncated.\n"
        metadata += "</details>\n\n<!-- ai-pr-reviewer-comment -->"

        review_text = f"### 🤖 AI Code Review\n\n{body}{metadata}"

        with open('review.md', 'w') as f:
            f.write(review_text)

        try:
            comments_json = subprocess.check_output(
                ['gh', 'api', f'repos/{repo}/issues/{pr_number}/comments?per_page=100']
            ).decode('utf-8')
            comments = json.loads(comments_json)
            existing_comment = next((c for c in comments if "<!-- ai-pr-reviewer-comment -->" in c.get('body', '')), None)

            if existing_comment:
                subprocess.run(['gh', 'api', '-X', 'PATCH', f'repos/{repo}/issues/comments/{existing_comment["id"]}', '-F', 'body=@review.md'], check=True)
            else:
                subprocess.run(['gh', 'pr', 'comment', pr_number, '--body-file', 'review.md'], check=True)
                
        except Exception as api_e:
            print(f"GitHub API Error: {api_e}")

    except Exception as e:
        print(f"Gemini Review Error: {e}")

if __name__ == "__main__":
    main()
