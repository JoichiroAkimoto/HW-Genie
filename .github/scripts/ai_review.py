import os
import google.generativeai as genai
import subprocess
import json
import sys

def main():
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("Error: GEMINI_API_KEY is not set.")
        sys.exit(1)

    genai.configure(api_key=api_key)
    
    # モデルの取得（環境変数がなければデフォルト値を設定）
    model_name = os.environ.get('GEMINI_MODEL', 'gemini-3-flash-preview')
    model = genai.GenerativeModel(model_name)
    
    pr_number = os.environ.get('PR_NUMBER')
    repo = os.environ.get('GITHUB_REPOSITORY')

    if not pr_number or not repo:
        print("Error: PR_NUMBER or GITHUB_REPOSITORY is missing.")
        sys.exit(1)

    # PRの差分を取得
    try:
        diff = subprocess.check_output(['gh', 'pr', 'diff', pr_number]).decode('utf-8')
    except Exception as e:
        print(f"Error getting diff: {e}")
        sys.exit(0)

    if not diff:
        print("No diff found.")
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
        response = model.generate_content(prompt)
        body = response.text
        
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
