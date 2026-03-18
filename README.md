# HW-Genie 🧞‍♂️

**HW-Genie** は、Hero Wars のプレイを強力にサポートする AI エージェント対応の自動化ツールキットです。
Python による高速な API 自動化 (CLI) と、ブラウザ画面での利便性を高めるユーザースクリプト (Userscript) を統合したハイブリッドな構成を採用しています。

## 主な機能
- **Hero Raid**: ヒーローミッションの高速レイド（スタミナ自動回復・ソウルストーン自動換金対応）。
- **Item Raid**: 特定のアイテム獲得のための繰り返しレイドを自動化。
- **Hero Shopping**: ターゲットショップでのヒーローソウルと、ソウルショップでの全アイテムの一括購入。
- **Daily Routine**: 定型的なレイドとショッピングをワンコマンドで実行。
- **Session Sync**: ユーザースクリプトを使用したセッション情報の自動同期（開発中）。

## クイックスタート

### Python CLI (hw-genie)
Python 3.14 (3.10+) が必要です。

```bash
# 環境構築
python -m venv .venv
source .venv/bin/activate
pip install -e src/python

# 実行
hw-genie --help
```

### Gemini CLI 連携
本リポジトリの `.gemini/skills/` を読み込ませることで、Gemini CLI から自然言語でレイド等を指示できます。

## 開発環境
- **Backend**: Python 3.14 (Ruff, pytest)
- **Frontend**: TypeScript (Bun, Vite)

## ライセンス
[MIT License](LICENSE)
