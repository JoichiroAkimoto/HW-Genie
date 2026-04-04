# HW-Genie 🧞‍♂️

[![Buy Me a Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg)](https://buymeacoffee.com/joichiroakimoto)

**HW-Genie** は、Hero Wars のプレイを強力にサポートする AI エージェント対応の自動化ツールキットです。
Python による高速な API 自動化 (CLI) と、ブラウザ画面での利便性を高めるユーザースクリプト (Userscript) を統合したハイブリッドな構成を採用しています。

## 主な機能
- **Daily Routine**: ヒーローレイドとショッピングをワンコマンドで連続実行（アイテムのスタミナ限界の場合は中断）。
- **Hero Raid**: 指定したミッションのヒーローレイドを実行。
- **Item Raid**: 特定のアイテムを目的とした繰り返しレイドの自動化（スタミナ不足または指定回数に達するまで）。
- **Hero Shopping**: ターゲットショップでのヒーローソウル購入と、ソウルショップでの全アイテムの一括購入（余剰ソウルの自動換金対応）。
- **Auth & Session Sync**: `curl` コマンドを利用したセッション情報の管理・更新。ユーザースクリプトを使用した自動同期機能も開発中。

## クイックスタート

### Python CLI (hw-genie)
Python 3.14 (3.10+) と [direnv](https://direnv.net/) の使用を推奨しています。

```bash
# 1. 仮想環境を作成
python -m venv .venv

# 2. direnv の設定
echo "source .venv/bin/activate" > .envrc
direnv allow

# 3. パッケージを開発モードでインストール
pip install -e src/python

# 実行
hw-genie --help
```

### Gemini CLI 連携
本リポジトリの `.agents/skills/` を読み込ませることで、Gemini CLI等のツールから自然言語でレイド等を指示できます。

## 開発環境
- **Backend**: Python 3.14 (Ruff, pytest)
- **Frontend**: TypeScript (Bun, Vite)

## ライセンス
[MIT License](LICENSE)
