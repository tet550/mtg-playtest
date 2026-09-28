"""Infrastructure（29節）: Operation Log / Undo / Redo / Replay / Snapshot / State Diff。

ゲームモデルとは分けて持つ。AI はこれらを管理しない。

対局フォルダの中身:
    initial.json   初期状態
    log.jsonl      適用した ActionGroup を1行1件（actor・ops・events）
    state.json     現在状態のキャッシュと、log のどこまでが有効か（cursor）

乱数は ActionGroup ごとに (seed, version) から決まるので、initial.json と log を
先頭から適用し直せば同じ状態に戻る。Undo はこの Replay で cursor を戻すだけ。
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
            for group, r in applied:
                entries.append({"seq": len(entries) + 1, "version": r.version, "time": now,
                                "actor": group.get("actor", result["actor"]), "label": group.get("label", ""),
                                "pre": group.get("pre", []), "ops": group["ops"],
                                "aliases_in": r.aliases_in,
                                "events": r.events})
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
            r = engine.apply_group(e["actor"], {"label": e["label"], "pre": [], "ops": e["ops"]},
                                   e.get("aliases_in"))
            if r.status != "applied":
                raise RuntimeError("replay diverged at entry %d: %s" % (e["seq"], r.error))
        return engine.state

    def undo(self, n: int = 1, to: Optional[int] = None) -> int:
        """n 件戻す。to を渡すと log の to 件目を適用した直後の時点まで戻す（巻き戻しの請求用）。"""
        cur = max(0, self.cursor() - n) if to is None else to
        if not 0 <= cur <= self.cursor():
            raise ValueError("cannot undo to %d (cursor is %d)" % (cur, self.cursor()))
        self._save_state(self.replay(cur), cur)
        return cur

    def redo(self, n: int = 1) -> int:
        total = len(self.read_log())
        cur = min(total, self.cursor() + n)
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

