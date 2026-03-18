# HW-Genie 移行・再構成設計書

## 1. 目的
現在の `toolbox` リポジトリにある Hero Wars 関連資産を、専門性の高い新しいパブリックリポジトリ `HW-Genie` として切り出し、再構成する。

## 2. 設計方針
- **AI Agent アグノスティック**: Gemini CLI に依存しすぎないコアパッケージ (`hw_genie`) を構築。
- **命名規則の統一**: Python モジュール名からハイフンを排除し、アンダースコアに統一。
- **CLI ツール化**: サブコマンド形式で全ての機能を呼び出せるようにする。
- **フロントエンド連携**: Bun を使用した TypeScript ユーザースクリプト環境の構築。

## 3. ディレクトリ構成
```text
HW-Genie/
├── .gemini/
│   └── skills/           # Gemini CLI アダプター
├── src/
│   ├── python/
│   │   ├── hw_genie/     # コアパッケージ
│   │   │   ├── main.py   # CLI エントリーポイント
│   │   │   ├── core/     # client.py, auth.py
│   │   │   └── commands/ # 各機能の実装
│   │   ├── tests/        # pytest
│   │   └── pyproject.toml# パッケージ定義
│   └── userscripts/      # Bun / TS
├── docs/                 # ドキュメント
├── .python-version       # 3.14
└── README.md
```

## 4. 移行タスク
1. `HW-Genie/` ディレクトリの作成と初期化。
2. 既存の `common/hw_client.py` などを `core/` へ移動・リネーム。
3. 各スキルスクリプトを `commands/` へ移動し、ハイフンをアンダースコアにリネーム。
4. `main.py` で CLI エントリーポイントを構築。
5. Gemini CLI 用の `SKILL.md` を `hw-genie` コマンド呼び出しに書き換え。
6. Bun によるユーザースクリプト環境の雛形作成。
