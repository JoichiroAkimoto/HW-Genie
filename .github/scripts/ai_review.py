import os
from google import genai
from google.genai import types
import subprocess
import json
import sys

def main():
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("Error: GEMINI_API_KEY is not set.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    
    # 軽量・高速モデルをデフォルトとして設定
    model_name = os.environ.get('GEMINI_MODEL', 'gemini-3.1-flash-lite-preview')
    
    pr_number = os.environ.get('PR_NUMBER')
    repo = os.environ.get('GITHUB_REPOSITORY')

    if not pr_number or not repo:
        print("Error: PR_NUMBER or GITHUB_REPOSITORY is missing.")
        sys.exit(1)

    # PRの差分を取得
    try:
        raw_diff = subprocess.check_output(['gh', 'pr', 'diff', pr_number]).decode('utf-8')
    except Exception as e:
        print(f"Error getting diff: {e}")
        sys.exit(1) # CIを正しく失敗させる

    if not raw_diff:
        print("No diff found.")
        sys.exit(0)

    # ロックファイルや画像の差分を堅牢に除外する
    import re
    from unidiff import PatchSet
    import io
    
    try:
        patch = PatchSet(io.StringIO(raw_diff))
        filtered_diff = ""
        for file in patch:
            path = file.path if hasattr(file, 'path') and file.path else ""
            # 不要なファイルを正規表現で除外
            if re.search(r'(package-lock\.json|yarn\.lock|bun\.lockb|pnpm-lock\.yaml|poetry\.lock|\.lock|\.svg|\.png|\.jpg|\.jpeg|\.gif|\.mp4|\.zip)$', path, re.IGNORECASE):
                continue
            filtered_diff += str(file) + "\n"
        diff = filtered_diff
    except Exception as e:
        print(f"Failed to parse diff with unidiff: {e}")
        # パース失敗時は元の差分をフォールバック
        diff = raw_diff

    if not diff.strip():
        print("Diff contains only ignored files.")
        sys.exit(0)

    # 長すぎる差分を切り詰め (50万)
    limit = 500000
    is_truncated = False
    if len(diff) > limit:
        diff = diff[:limit]
        is_truncated = True

    # プロンプトの準備
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
        # レビュー生成
        config_kwargs = {}
        # 推論(Thinking)モデル使用時、思考能力を最大化するためデフォルトで HIGH に設定
        if 'thinking' in model_name.lower() or os.environ.get('GEMINI_THINKING_LEVEL'):
            config_kwargs['thinking_config'] = types.ThinkingConfig(
                thinking_level=os.environ.get('GEMINI_THINKING_LEVEL', 'HIGH')
            )
        
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
        )
        
        try:
            body = response.text
        except ValueError:
            # Safety Settings によるブロック等を検知
            reason = str(response.candidates[0].finish_reason) if response.candidates else "UNKNOWN"
            body = f"> [!CAUTION]\n> AIによるレビュー生成が中断されました（理由: {reason}）。\n> 差分に機密情報やセーフティフィルターに抵触する内容が含まれている可能性があります。\n"

        
        # 実行結果メタデータの作成 (Review Metadata -> Execution Info に変更)
        metadata = "\n\n---\n<details><summary>⚡ Execution Info</summary>\n\n"
        metadata += f"- **Model**: `{model_name}`\n"
        
        try:
            usage = response.usage_metadata
            metadata += f"- **Tokens**: In={usage.prompt_token_count}, Out={usage.candidates_token_count}\n"
        except Exception:
            metadata += "- **Tokens**: (Usage metadata not available)\n"
            
        if is_truncated:
            metadata += "- **Status**: ⚠️ Diff was truncated to 500,000 characters due to limits.\n"
        metadata += "</details>\n\n<!-- ai-pr-reviewer-comment -->"

        review_text = f"### 🤖 AI Code Review\n\n{body}{metadata}"

        with open('review.md', 'w') as f:
            f.write(review_text)

        # 既存のコメントを探す (per_page=100で確実性を高める)
        try:
            comments_json = subprocess.check_output(
                ['gh', 'api', f'repos/{repo}/issues/{pr_number}/comments?per_page=100']
            ).decode('utf-8')
            comments = json.loads(comments_json)
            existing_comment = next((c for c in comments if "<!-- ai-pr-reviewer-comment -->" in c.get('body', '')), None)

            if existing_comment:
                subprocess.run(['gh', 'api', '-X', 'PATCH', f'repos/{repo}/issues/comments/{existing_comment["id"]}', '-F', 'body=@review.md'], check=True)
                print(f"Updated comment {existing_comment['id']}")
            else:
                subprocess.run(['gh', 'pr', 'comment', pr_number, '--body-file', 'review.md'], check=True)
                print("Created new comment")
                
        except Exception as api_e:
            print(f"GitHub API Error (Non-fatal): {api_e}")

    except Exception as e:
        print(f"Gemini Review Error: {e}")

if __name__ == "__main__":
    main()
