#!/usr/bin/env python3
import subprocess
import json
import sys


def fetch_pr_details(owner, repo, pr_number):
    query = """
    query($owner:String!, $repo:String!, $pull:Int!) {
      repository(owner:$owner, name:$repo) {
        pullRequest(number:$pull) {
          title
          body
          comments(last: 100) { nodes { body author { login } } }
          reviews(last: 100) {
            nodes {
              body
              author { login }
              comments(last: 100) { nodes { body path line diffHunk } }
            }
          }
        }
      }
    }
    """

    cmd = [
        "gh",
        "api",
        "graphql",
        "-f",
        "query=" + query,
        "-f",
        "owner=" + owner,
        "-f",
        "repo=" + repo,
        "-F",
        "pull=" + str(pr_number),
    ]

    try:
        result = subprocess.check_output(cmd).decode("utf-8")
        return json.loads(result)
    except subprocess.CalledProcessError as e:
        print(f"Error fetching PR details: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python fetch_pr_details.py <owner> <repo> <pr_number>")
        sys.exit(1)

    owner = sys.argv[1]
    repo = sys.argv[2]
    pr_number = sys.argv[3]

    details = fetch_pr_details(owner, repo, pr_number)
    print(json.dumps(details, indent=2, ensure_ascii=False))
