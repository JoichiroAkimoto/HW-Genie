"""Titan Arena 戦闘シミュレーター（HWH 経由 / 将来のローカル移植用）。

サーバーは titanArenaEndBattle に送られた seed + 初期配置から戦闘を再計算し、
progress の各タイタンの残 HP / 死亡と完全に一致するかを検証する。
そのため EndBattle には「実際の戦闘シミュレーション結果」が不可欠。

本モジュールは `battle_sim` 抽象（`callable(rival_id, seed, battle) -> progress`）
の HWH 実装。Chrome DevTools Protocol (CDP) 経由で HWH 拡張の BattleCalc
(`get_titanClanPvp`) を呼び出す。Chrome 非依存の将来実装（Node/Python への
BattleCalc 移植）も同じ `battle_sim` シグネチャで差し替え可能で、
`titan_arena.py` の固定リスト（`DEFAULT_TEAM_ROTATION`）＋ `10回` リトライの
コアロジックは `battle_sim` に依存しない。

HWH を使う場合:
  1. Chrome で Hero Wars を開き、HWH 拡張が有効であること。
  2. リモートデバッグ有効で起動していること
     (例: /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \
           --remote-debugging-port=9222)
  3. TitanSimulatorHWH(headers=...) を battle_sim として run_titan_arena に渡す。
     Chrome が無い/不要な環境では `battle_sim=None` で `Invalid battle` を
     検出してリトライするフォールバックが働き、将来のローカルシミュレーター
     （`LocalBattleSimulator`）に差し替え可能。

注意: HWH の BattleCalc はクロージャ内にあり直接は呼べない。Chrome 非依存の
移植時は `heroes.4e2c73d...js` の `BattleInstantPlay` 部分を Node/Python で
再現する。
"""

from __future__ import annotations

import json
from typing import Any, Optional

try:
    import websocket  # noqa: F401  (websocket-client)
    _HAVE_WS = True
except Exception:  # pragma: no cover
    _HAVE_WS = False



# ---------------------------------------------------------------------------
# CDP ヘルパ (軽量実装: websocket-client がなければ urllib で http のみ)
# ---------------------------------------------------------------------------

def _list_cdp_targets(port: int = 9222, timeout: float = 5.0) -> list[dict]:
    import urllib.request

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=timeout) as r:
        return json.loads(r.read().decode())


def _find_game_target(targets: list[dict], url_substring: str = "hero-wars") -> Optional[dict]:
    sub = url_substring.lower()
    for t in targets:
        if sub in t.get("url", "").lower() and t.get("type") == "page":
            return t
    # fallback: 最初の page
    for t in targets:
        if t.get("type") == "page":
            return t
    return None


class TitanSimulatorHWH:
    """HWH 拡張が inject されたゲームページから戦闘結果(progress)を取得する。

    battle_sim(rival_id, seed, battle) -> dict | None
      battle: StartBattle のレスポンスの battle ブロック
      戻り値: {"attackers": {"heroes": {id: {hp,isDead,energy}}},
                "defenders": {"heroes": {id: {hp,isDead,energy}}}}
               （なければ None）
    """

    def __init__(self, headers: dict[str, str] | None = None, port: int = 9222):
        self.port = port
        self.headers = headers or {}

    def __call__(self, rival_id: str, seed: str, battle: dict[str, Any]) -> Optional[dict[str, Any]]:
        # CDP でゲームページの window.Game から戦闘シミュレーションを実行し、
        # progress を取得する。実装はゲームページ側で BattleCalc 相当を再現。
        try:
            progress = self._simulate_via_cdp(rival_id, seed, battle)
            return progress
        except Exception as e:  # pragma: no cover - 環境依存
            print(f"[titan_sim_hwh] simulation failed: {e}")
            return None

    # ----- CDP 経由のシミュレーション -----

    def _simulate_via_cdp(self, rival_id: str, seed: str, battle: dict) -> Optional[dict]:
        if not _HAVE_WS:
            raise RuntimeError(
                "websocket-client が必要です: pip install websocket-client"
            )
        import websocket

        targets = _list_cdp_targets(self.port)
        target = _find_game_target(targets)
        if not target:
            raise RuntimeError("ゲームページが見つかりません (Chrome を --remote-debugging-port で起動)")

        ws_url = target["webSocketDebuggerUrl"]
        ws = websocket.create_connection(ws_url, timeout=5)
        try:
            # BattleCalc は HWH が window.BattleCalc として公開している
            # battleData は StartBattle の battle ブロックをそのまま渡す
            # seed は StartBattle が文字列で返すが BattleCalc は数値で扱う
            battle_json = json.dumps(battle, ensure_ascii=False)
            # rival_id と seed を battleData に統合（seed は数値で渡す）
            try:
                seed_int = int(seed)
            except (TypeError, ValueError):
                seed_int = 0
            expr = (
                "(function(){\n"
                "  return new Promise((resolve, reject) => {\n"
                "    try {\n"
                "      if (typeof BattleCalc === 'undefined') { resolve({error:'no BattleCalc'}); return; }\n"
                "      const battleData = " + battle_json + ";\n"
                "      battleData.typeId = " + json.dumps(str(rival_id)) + ";\n"
                "      battleData.seed = " + json.dumps(seed_int) + ";\n"
                "      if (!battleData.progress) battleData.progress = [];\n"
                "      BattleCalc(battleData, 'get_titanClanPvp', (res) => {\n"
                "        try {\n"
                "          const p = res.progress && res.progress[0] ? res.progress[0] : res.progress;\n"
                "          const out = {\n"
                "            attackers: p ? p.attackers : null,\n"
                "            defenders: p ? p.defenders : null,\n"
                "            result: res.result,\n"
                "            win: res.result ? res.result.win : null\n"
                "          };\n"
                "          resolve(out);\n"
                "        } catch(e){ resolve({error: 'parse:'+e.toString()}); }\n"
                "      });\n"
                "    } catch(e){ resolve({error: e.toString()}); }\n"
                "  });\n"
                "})()"
            )
            # awaitPromise で Promise の解決を待つ
            result = self._cdp_evaluate(ws, expr, await_promise=True)
            if not result or result.get("error"):
                return None
            # result は {attackers, defenders, result, win}
            # titan_arena.py が期待する形式に変換
            # attackers/defenders は既に {heroes: {...}} 形式
            if result.get("attackers") is None and result.get("defenders") is None:
                return None
            # 正常系は attackers/defenders をそのまま返す
            # 不足があれば空で補完
            attackers = result.get("attackers") or {"heroes": {}}
            defenders = result.get("defenders") or {"heroes": {}}
            # attackers/defenders が heroes を持たない場合はラップ
            if isinstance(attackers, dict) and "heroes" not in attackers:
                attackers = {"heroes": attackers}
            if isinstance(defenders, dict) and "heroes" not in defenders:
                defenders = {"heroes": defenders}
            return {"attackers": attackers, "defenders": defenders}
        finally:
            ws.close()

    def _cdp_evaluate(self, ws, expr: str, await_promise: bool = False, timeout: float = 10.0):
        import random
        import time

        msg_id = random.randint(1, 1_000_000)
        params: dict[str, Any] = {"expression": expr, "returnByValue": True}
        if await_promise:
            params["awaitPromise"] = True
        ws.send(json.dumps({"id": msg_id, "method": "Runtime.evaluate", "params": params}))
        # CDP のハングを避けるため recv にタイムアウトを設定
        try:
            ws.settimeout(timeout)
        except Exception:
            pass
        start = time.time()
        while True:
            if time.time() - start > timeout:
                raise TimeoutError("CDP evaluate timed out")
            try:
                raw = ws.recv()
            except Exception as e:
                raise TimeoutError(f"CDP recv timed out: {e}") from e
            data = json.loads(raw)
            if data.get("id") == msg_id:
                # エラーハンドリング
                if "error" in data:
                    raise RuntimeError(f"CDP evaluate error: {data['error']}")
                res = data.get("result", {}).get("result", {})
                if res.get("subtype") == "error":
                    raise RuntimeError(f"JS error: {res.get('description')}")
                return res.get("value")


# 後方互換エイリアス（旧スタッシュの HWHBattleSimulator 名）
HWHBattleSimulator = TitanSimulatorHWH
