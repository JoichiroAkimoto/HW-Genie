---
name: ヒーローレイド
description: HW-Genie を使用して Hero Wars のミッションレイドを自動化します。
---

# ヒーローレイド (HW-Genie 版)

このスキルは `hw-genie` コマンドを使用して、Hero Wars でのミッションレイドを自動的に実行します。

## ワークフロー

1.  **セッションの確認**: `session.json` が存在することを確認します。存在しない場合はユーザーに `curl` コマンドを求めます。
2.  **実行**: 以下のコマンドを実行します。
    ```bash
    .venv/bin/hw-genie raid hero <mission_ids> --times <times>
    ```
    - `<mission_ids>`: ミッションIDのリスト（例: `1 5 10`）
    - `<times>`: 1ミッションあたりのレイド回数（デフォルト: 3）

## 使用例
「ミッション 1 5 10 を 5回ずつレイドして」
-> `.venv/bin/hw-genie raid hero 1 5 10 --times 5`
