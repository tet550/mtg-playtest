"""Infrastructure（29節）: Operation Log / Undo / Redo / Replay / Snapshot / State Diff。

ゲームモデルとは分けて持つ。AI はこれらを管理しない。

対局フォルダの中身:
    initial.json   初期状態
    log.jsonl      適用した Act を1行1件（actor・AI が書いた act・実際に適用した基本の op と event（steps）・Batch の番号・手順）
    state.json     現在状態のキャッシュと、log のどこまでが有効か（cursor）

Replay は各件の steps（複合 op を展開し、エイリアスを id に置き換えた基本の op）だけを適用し直す。
乱数は Act ごとに (seed, version) から決まるので、initial.json と log を先頭から適用し直せば同じ状態に戻る。Undo はこの Replay で cursor を戻すだけ。
"""
from __future__ import annotations

import datetime
import json
import pathlib
from typing import Optional

from . import info
from .engine import Engine
from .model import GameState


def _dump(path: pathlib.Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


class GameStore:
    def __init__(self, root):
        self.root = pathlib.Path(root)

    # ---- files

    @property
    def initial_path(self):
        return self.root / "initial.json"

    @property
    def log_path(self):
        return self.root / "log.jsonl"

    @property
    def state_path(self):
        return self.root / "state.json"

    def exists(self) -> bool:
        return self.initial_path.exists()

    def create(self, state: GameState, overwrite: bool = False) -> None:
        if self.exists() and not overwrite:
            raise FileExistsError("%s already holds a game (use --force to replace it)" % self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        _dump(self.initial_path, state.to_dict())
        self.log_path.write_text("", encoding="utf-8")
        self._save_state(state, 0)

    def read_log(self) -> list:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _write_log(self, entries: list) -> None:
        self.log_path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
                                 encoding="utf-8")

    def _save_state(self, state: GameState, cursor: int) -> None:
        _dump(self.state_path, {"cursor": cursor, "state": state.to_dict()})

    def cursor(self) -> int:
        return json.loads(self.state_path.read_text(encoding="utf-8"))["cursor"]

    def load(self) -> GameState:
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        return GameState.from_dict(data["state"])

    def initial(self) -> GameState:
        return GameState.from_dict(json.loads(self.initial_path.read_text(encoding="utf-8")))

    # ---- apply

    def apply(self, batch) -> dict:
        """Batch を適用して記録する。Undo 後に適用すると、それより先の Redo 履歴は捨てる。"""
        state = self.load()
        cursor = self.cursor()
        engine = Engine(state)
        result = engine.apply_batch(batch)
        applied = result.pop("_applied")
        if applied:
            entries = self.read_log()[:cursor]
            now = datetime.datetime.now().isoformat(timespec="seconds")
            batch = max([e.get("batch", 0) for e in entries] or [0]) + 1
            for i, (act, r) in enumerate(applied):
                entry = {"seq": len(entries) + 1, "version": r.version, "time": now, "batch": batch,
                         "actor": act.get("actor", act.get("proxy", result["actor"])),
                         "label": act.get("label", ""),
                         "pre": act.get("pre", []), "act": act["act"],
                         "steps": r.steps}
                if act.get("cont"):
                    entry["cont"] = True  # この Act は次の件に続く
                if act.get("proc_of"):
                    entry["proc"] = act["proc_of"]  # 手順から展開した Act
                if i == 0 and result.get("label"):
                    entry["batch_label"] = result["label"]
                if "proxy" in act:
                    entry["proxy_by"] = result["actor"]  # 代理の宣言（指摘があれば Undo で巻き戻す）
                entries.append(entry)
            self._write_log(entries)
            cursor = len(entries)
        self._save_state(engine.state, cursor)
        result["_state"] = engine.state
        return result

    def set_policies(self, policies: dict) -> dict:
        """Information Policy を変える。卓の設定なので log には載せず、
        初期状態と現在状態の両方へ反映する（Replay しても同じ Policy になる）。"""
        state, init = self.load(), self.initial()
        for pid, pol in policies.items():
            if pid not in state.players:
                raise ValueError("unknown player %r" % pid)
            info.set_policy(state, pid, pol)
            info.set_policy(init, pid, pol)
        _dump(self.initial_path, init.to_dict())
        self._save_state(state, self.cursor())
        return dict(state.info_policy)

    # ---- replay / undo / redo

    def replay(self, upto: Optional[int] = None) -> GameState:
        """初期状態から log を upto 件（省略で cursor まで）適用し直した状態。"""
        entries = self.read_log()
        upto = self.cursor() if upto is None else upto
        if not 0 <= upto <= len(entries):
            raise ValueError("log has %d entries; cannot replay to %d" % (len(entries), upto))
        engine = Engine(self.initial())
        for e in entries[:upto]:
            r = engine.apply_act(e["actor"], {"label": e["label"], "act": [s["op"] for s in e["steps"]]})
            if r.status != "applied":
                raise RuntimeError("replay diverged at entry %d: %s" % (e["seq"], r.error))
        return engine.state

    @staticmethod
    def _act_start(entries: list, i: int) -> int:
        """log の i 件目の直後が Act の途中（cont の件の後）なら、その Act の始まりまで戻した位置。"""
        while i > 0 and entries[i - 1].get("cont"):
            i -= 1
        return i

    def undo(self, n: int = 1, to: Optional[int] = None) -> int:
        """Act を n 個戻す。to を渡すと log の to 件目を適用した直後の時点まで戻す（巻き戻しの請求用）。
        戻す先は Act の境目（Act の途中を指したら、その Act の始まり）。"""
        entries = self.read_log()
        cur = self.cursor()
        if to is not None:
            if not 0 <= to <= cur:
                raise ValueError("cannot undo to %d (cursor is %d)" % (to, cur))
            cur = self._act_start(entries, to)
        else:
            for _ in range(n):
                if cur > 0:
                    cur = self._act_start(entries, cur - 1)
        self._save_state(self.replay(cur), cur)
        return cur

    def redo(self, n: int = 1) -> int:
        """Act を n 個やり直す。"""
        entries = self.read_log()
        cur = self.cursor()
        for _ in range(n):
            if cur < len(entries):
                cur += 1
                while cur < len(entries) and entries[cur - 1].get("cont"):
                    cur += 1
        self._save_state(self.replay(cur), cur)
        return cur

    def fork(self, dest, upto: Optional[int] = None, overwrite: bool = False) -> "GameStore":
        """log の途中時点の状態を新しい対局の初期状態にする（Snapshot からの分岐）。"""
        state = self.replay(upto)
        other = GameStore(dest)
        other.create(state, overwrite=overwrite)
        return other


# ---------------------------------------------------------------- diff

def state_diff(a, b, path: str = "") -> list:
    """2つの状態（dict）の差分を [(path, before, after)] で返す。"""
    if isinstance(a, GameState):
        a = a.to_dict()
    if isinstance(b, GameState):
        b = b.to_dict()
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            p = "%s.%s" % (path, k) if path else str(k)
            if k not in a:
                out.append((p, None, b[k]))
            elif k not in b:
                out.append((p, a[k], None))
            else:
                out.extend(state_diff(a[k], b[k], p))
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and \
            all(isinstance(x, dict) for x in a + b):
        for i, (x, y) in enumerate(zip(a, b)):
            out.extend(state_diff(x, y, "%s[%d]" % (path, i)))
    elif a != b:
        out.append((path, a, b))
    return out

