# HW-Genie 移行・再構成実装プラン

> **For agentic workers:** REQUIRED: Use superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 現在の Hero Wars 関連資産を `HW-Genie/` ディレクトリに整理・抽出し、CLI パッケージおよびユーザースクリプト環境の基盤を作成する。

**Architecture:** Python `hw_genie` パッケージ、Gemini CLI アダプター、Bun ユーザースクリプト構成。

**Tech Stack:** Python 3.14, Click (CLI), Bun (Userscript), pytest

---

### Task 1: HW-Genie ディレクトリ構造の作成

**Files:**
- Create: `HW-Genie/`
- Create: `HW-Genie/src/python/hw_genie/core/`
- Create: `HW-Genie/src/python/hw_genie/commands/`
- Create: `HW-Genie/src/userscripts/src/`
- Create: `HW-Genie/.gemini/skills/`

- [ ] **Step 1: 必要なディレクトリを一括作成する**

```bash
mkdir -p HW-Genie/src/python/hw_genie/core \
         HW-Genie/src/python/hw_genie/commands \
         HW-Genie/src/python/tests \
         HW-Genie/src/userscripts/src \
         HW-Genie/.gemini/skills \
         HW-Genie/docs
```

- [ ] **Step 2: 基本ファイルの配置**

```bash
cp .python-version HW-Genie/
touch HW-Genie/src/python/hw_genie/__init__.py \
      HW-Genie/src/python/hw_genie/core/__init__.py \
      HW-Genie/src/python/hw_genie/commands/__init__.py
```

---

### Task 2: Python 基盤ロジックの移行と整理

**Files:**
- Migrate: `hw_client.py` -> `client.py`
- Migrate: `auth_manager.py` -> `auth.py`

- [ ] **Step 1: HWClient の移行**

`toolbox/.gemini/skills/common/hw_client.py` を `HW-Genie/src/python/hw_genie/core/client.py` にコピーし、インポートパスを調整。

- [ ] **Step 2: AuthManager の移行**

`toolbox/.gemini/skills/hero-wars-auth/auth_manager.py` を `HW-Genie/src/python/hw_genie/core/auth.py` にコピーし、クラス名や構成を整理。

---

### Task 3: 機能スクリプトの移行と CLI 化 (ハイフン -> アンダースコア)

**Files:**
- Migrate & Rename: 各スキルスクリプト

- [ ] **Step 1: ヒーローレイドの移行**
`hero-raid/scripts/hero_raid.py` -> `HW-Genie/src/python/hw_genie/commands/hero_raid.py`

- [ ] **Step 2: アイテムレイドの移行**
`item-raid/scripts/item_raid.py` -> `HW-Genie/src/python/hw_genie/commands/item_raid.py`

- [ ] **Step 3: ショッピングの移行**
`hero-shopping/scripts/shop_manager.py` -> `HW-Genie/src/python/hw_genie/commands/hero_shopping.py`

- [ ] **Step 4: デイリーレイドの移行**
`daily-raid/scripts/daily_raid.py` -> `HW-Genie/src/python/hw_genie/commands/daily_raid.py`

- [ ] **Step 5: main.py (CLI エントリーポイント) の作成**

`click` ライブラリ等を使用して、サブコマンド形式で呼び出せるように構築。

---

### Task 4: Python パッケージ定義とテストの移行

**Files:**
- Create: `pyproject.toml`
- Migrate: `tests/`

- [ ] **Step 1: `HW-Genie/src/python/pyproject.toml` の作成**

`hw-genie` コマンドのエントリーポイントを定義。

- [ ] **Step 2: テストのコピーと実行確認**

---

### Task 5: Gemini CLI アダプターと Bun 環境の準備

**Files:**
- Create: `HW-Genie/.gemini/skills/*/SKILL.md`
- Create: `HW-Genie/src/userscripts/package.json`

- [ ] **Step 1: 各スキルの `SKILL.md` を作成し、`hw-genie` コマンド呼び出しに変更する**

- [ ] **Step 2: Bun の初期化**

`HW-Genie/src/userscripts/` で `bun init` を実行。

---

### Task 6: 最終確認

- [ ] **Step 1: `pip install -e HW-Genie/src/python` を実行し、`hw-genie` コマンドの動作を確認する**
- [ ] **Step 2: README.md を作成し、プロジェクトの全貌を記述する**
