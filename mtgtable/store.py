"""Infrastructure（29節）: Operation Log / Undo / Redo / Replay / Snapshot / State Diff。

ゲームモデルとは分けて持つ。AI はこれらを管理しない。

対局フォルダの中身:
    initial.json   初期状態
    log.jsonl      適用した Act を1行1件（actor・AI が書いた act・実際に適用した基本の op と event（steps）・Batch の番号・手順）
    state.json     現在状態のキャッシュと、log のどこまでが有効か（cursor）
    seats.json     GUI から操作する席の鍵（`invite` が作る。鍵そのものではなくハッシュ）
    .lock          書き込み中の印（CLI の AI と GUI の人間が同時に書いても壊さない）

Replay は各件の steps（複合 op を展開し、エイリアスを id に置き換えた基本の op）だけを適用し直す。
乱数は Act ごとに (seed, version) から決まるので、initial.json と log を先頭から適用し直せば同じ状態に戻る。Undo はこの Replay で cursor を戻すだけ。
"""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import pathlib
import threading
import time
from typing import Optional

from . import info
from .engine import Engine
from .model import GameState


def _replace(tmp: pathlib.Path, path: pathlib.Path) -> None:
    """書き終えた一時ファイルで置き換える。Windows では読み手（観戦・wait）が開いている間は置き換えられないので、少し待って再試行する。"""
    for i in range(50):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if i == 49:
                raise
            time.sleep(0.02)


def _read(path: pathlib.Path) -> str:
    """読む。Windows では書き手が置き換えている瞬間に開くと PermissionError になるので、少し待って再試行する。"""
    for i in range(50):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            if i == 49:
                raise
            time.sleep(0.02)


def _dump(path: pathlib.Path, data) -> None:
    # 一時ファイルは書き手（プロセス・スレッド）ごとに別の名前にする。サーバーと auto が同じプロンプトを同時に作ると、
    # 同じ名前では Windows で片方が開けない（PermissionError）
    tmp = path.with_suffix("%s.%d-%d.tmp" % (path.suffix, os.getpid(), threading.get_ident()))
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        _replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class StaleCursor(Exception):
    """書こうとした人が見ていた時点（expect）の後に、別の書き手が log を進めた（GUI の 409）。"""


LOCK_TIMEOUT = 10.0  # 他の書き手を待つ長さ
LOCK_STALE = 60.0  # これより古い .lock は、落ちた書き手の残りとみなして消す


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
        return [json.loads(line) for line in _read(self.log_path).splitlines() if line.strip()]

    def _write_log(self, entries: list) -> None:
        tmp = self.log_path.with_suffix(".jsonl.tmp")  # 読み手（観戦・wait）に書きかけを見せない
        tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")
        _replace(tmp, self.log_path)

    @property
    def lock_path(self):
        return self.root / ".lock"

    @contextlib.contextmanager
    def lock(self, timeout: float = LOCK_TIMEOUT):
        """対局フォルダへの書き込みを1人ずつにする（別のプロセスの CLI とサーバーの間でも効く）。"""
        deadline = time.monotonic() + timeout
        while True:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - self.lock_path.stat().st_mtime > LOCK_STALE:
                        self.lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError("%s is locked by another writer" % self.root)
                time.sleep(0.05)
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            yield
        finally:
            with contextlib.suppress(FileNotFoundError):
                self.lock_path.unlink()

    def _check(self, expect: Optional[int]) -> int:
        cur = self.cursor()
        if expect is not None and expect != cur:
            raise StaleCursor("the game moved on (cursor %d, expected %d)" % (cur, expect))
        return cur

    def _save_state(self, state: GameState, cursor: int) -> None:
        _dump(self.state_path, {"cursor": cursor, "state": state.to_dict()})

    def cursor(self) -> int:
        return json.loads(_read(self.state_path))["cursor"]

    def load(self) -> GameState:
        data = json.loads(_read(self.state_path))
        return GameState.from_dict(data["state"])

    def initial(self) -> GameState:
        return GameState.from_dict(json.loads(_read(self.initial_path)))

    # ---- apply

    def apply(self, batch, expect: Optional[int] = None) -> dict:
        """Batch を適用して記録する。Undo 後に適用すると、それより先の Redo 履歴は捨てる。
        expect を渡すと、cursor がその値のときだけ適用する（見ていた盤面のまま書く。違えば StaleCursor）。"""
        with self.lock():
            cursor = self._check(expect)
            return self._apply(batch, cursor)

    def _apply(self, batch, cursor: int) -> dict:
        state = self.load()
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
        with self.lock():
            return self._set_policies(policies)

    def _set_policies(self, policies: dict) -> dict:
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

    def replay_iter(self, upto: Optional[int] = None):
        """初期状態から log を1件ずつ適用し直し、(件数, 状態) を順に返す（0件目は初期状態）。
        状態は同じオブジェクトを書き換えて進むので、受け取った側でその場で使う。"""
        entries = self.read_log()
        upto = self.cursor() if upto is None else upto
        if not 0 <= upto <= len(entries):
            raise ValueError("log has %d entries; cannot replay to %d" % (len(entries), upto))
        engine = Engine(self.initial())
        yield 0, engine.state
        for e in entries[:upto]:
            r = engine.apply_act(e["actor"], {"label": e["label"], "act": [s["op"] for s in e["steps"]]})
            if r.status != "applied":
                raise RuntimeError("replay diverged at entry %d: %s" % (e["seq"], r.error))
            yield e["seq"], engine.state

    def replay(self, upto: Optional[int] = None) -> GameState:
        """初期状態から log を upto 件（省略で cursor まで）適用し直した状態。"""
        state = None
        for _, state in self.replay_iter(upto):
            pass
        return state

    @staticmethod
    def _act_start(entries: list, i: int) -> int:
        """log の i 件目の直後が Act の途中（cont の件の後）なら、その Act の始まりまで戻した位置。"""
        while i > 0 and entries[i - 1].get("cont"):
            i -= 1
        return i

    def undo(self, n: int = 1, to: Optional[int] = None, expect: Optional[int] = None) -> int:
        """Act を n 個戻す。to を渡すと log の to 件目を適用した直後の時点まで戻す（巻き戻しの請求用）。
        戻す先は Act の境目（Act の途中を指したら、その Act の始まり）。expect は apply と同じ。"""
        with self.lock():
            return self._undo(n, to, self._check(expect))

    def last_act(self) -> list:
        """cursor の直前の Act（cont でつながったパートも含む）の log の件。"""
        entries = self.read_log()
        cur = self.cursor()
        return entries[self._act_start(entries, cur - 1):cur] if cur else []

    def _undo(self, n: int, to: Optional[int], cur: int) -> int:
        entries = self.read_log()
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
        with self.lock():
            return self._redo(n)

    def _redo(self, n: int) -> int:
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

