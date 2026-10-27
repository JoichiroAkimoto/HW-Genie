---
name: arena
description: HW-Genie を使用して Arena の状態・ランキングを確認し、チームプリセットを管理して、自分より上位の指定プレイヤーを攻撃します。
---
# Arena (HW-Genie 版)
## 概要
Arena の順位・攻撃回数・保存チームを確認し、名前付きチームプリセットを作成して、**自分より上位** のプレイヤーを `--rank` または `--user-id` で指定して攻撃します。

- **対象は上位のみ**: 同順位・下位・順位不明のプレイヤーは攻撃しません（安全側で拒否）。
- **攻撃範囲**: 実測で自分の順位 ±21。上位のみ対象のため、実質 `own_place - 21` 〜 `own_place - 1`。最終判定は `arenaCheckTargetRange`（1リクエスト最大3件）に従います。
- **ランキング**: `topGet` はトップ50のみ（ページングなし）。候補 `arenaFindEnemies` は約13位上までです。それ以外は `--user-id` を使います。
- **チーム**: `--heroes`（ちょうど5体・重複なし・ID < 6000）/ `--pet`（>= 6000）/ `--banner` / `--favor HERO=PATRON`（繰り返し可）。未指定フィールドはプリセット → ゲーム保存チームの順に補完されます。
- **favor**: 明示 `--favor` が最優先。なければプリセット → 同モードの保存 favor（`teamGetFavor.arena`）から選択ヒーローに一致する分だけを使い、欠落ヒーローは警告されます。他モードの favor は流用しません。
- **`--dry-run`**: 読み取り API と `arenaCheckTargetRange` のみ実行し、`arenaAttack` は **絶対に送りません**。
- **実攻撃がデフォルト**: `--dry-run` を付けない場合は攻撃します（`--yes` はありません）。

## ワークフロー
> **Tip**: 複数アカウントでは `uv run hw-genie auth --list` で正しいアカウント名を確認してください。

1. 状態確認（読み取りのみ）:
   ```bash
   uv run hw-genie arena status -a Alice
   ```
2. ランキング確認:
   ```bash
   uv run hw-genie arena top -a Alice --limit 20
   uv run hw-genie arena top -a Alice --raw
   ```
3. チームプリセット作成:
   ```bash
   # 現在のゲーム保存チームを取り込む
   uv run hw-genie arena team set atk1 -a Alice --from-saved

   # 直接指定（favor は必要な分だけ）
   uv run hw-genie arena team set atk1 -a Alice \
     --heroes 29 13 73 71 35 --pet 6008 --banner 2 \
     --favor 13=6003 --favor 29=6011

   uv run hw-genie arena team list -a Alice
   uv run hw-genie arena team remove atk1 -a Alice
   ```
4. 攻撃前の予行確認（攻撃しません）:
   ```bash
   uv run hw-genie arena attack -a Alice --rank 10 --team atk1 --dry-run
   uv run hw-genie arena attack -a Alice --user-id 11111111 --dry-run
   ```
5. 実攻撃（攻撃回数を消費します）:
   ```bash
   uv run hw-genie arena attack -a Alice --rank 10 --team atk1
   uv run hw-genie arena attack -a Alice --rank 10 \
     --heroes 29 13 73 71 35 --pet 6008 --banner 2 --favor 13=6003
   ```

## 注意
- **まず `--dry-run`**: ターゲット解決・範囲チェック・チーム内容を確認してから実攻撃してください。
- **上位のみ**: `--rank` / `--user-id` が自分以上、または順位を解決できない場合は exit 1 で攻撃しません。
- **トップ50の制限**: `--rank` はトップ50または現在の候補に存在する必要があります。見つからない場合は `--user-id` を指定してください（その場合も上位と確認できる必要があります）。
- **攻撃回数**: `NotEnough` / `Refillable` は攻撃せず警告して exit 0 です。`arena status` で残回数とクールダウンを確認できます。
- **キャンペーン用途ではありません**: 自動周回・自動ターゲット選択・`multi arena` は未対応です。1回ずつ明示的に指定してください。
- **初回の実攻撃**: 範囲チェック `true` でも、候補外の上位ターゲットを `arenaAttack` が受理するかは未確認です。初回は範囲内の1件だけで確認することを推奨します。
- API 仕様の詳細は `docs/api/ARENA_API.md` を参照してください。
