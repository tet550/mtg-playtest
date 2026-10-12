"""サーバーの中で審判と AI の席を回す（serve の AI ワーカー）。対局ごとに `auto --watch` を動かす代わり。

- 見張り: サーバー自身の書き込み（席の操作・対局を作る）で Viewer.notify が起こすので、すぐ動く。別のプロセス（CLI）の
  書き込みは INTERVAL 秒ごとに見て拾う。対局の変化の印（stamp）が前に見たときと同じなら何もしない
- 回す: 変わった対局ごとに llm.run（自動パス → 審判か AI の席の番ならプロンプトを送って返答を卓に書く → 人間の番まで）。
  同じ対局は同時に1つだけ、全体で CONCURRENCY 個まで。1手ごとに Viewer.notify（観戦・相手の画面がすぐ変わる）
- 止める: 返答を続けて卓に書けない（llm.run の決まり。AI の席は一度審判に回してから）・API を呼べない・利用の上限に
  当たったら、その対局を「中断」にする（卓の文書 "ai" に理由）。画面に理由を出し、ホストが「再開」できる
- 利用の上限（Limits。環境変数で変える）: 対局ごとの呼び出し回数とトークン数。数えるのはトークンの合計
  （入力（キャッシュ分を含む）＋出力）

既定（serve --site）ではワーカーを動かさず、審判と AI の席は Claude Code が回す（`next` の監視と mtg-table エージェント）。
ワーカーは serve --ai のときだけ（API のキーを使う）。
"""
from __future__ import annotations

import datetime
import os
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Optional

from . import llm
from .sqlstore import connect

INTERVAL = 2.0  # 別のプロセスの書き込みを見に行く間隔（秒）
CONCURRENCY = 4  # 同時に回す対局の数（API の待ち時間が長いので、対局ごとにスレッド）

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_usage (
    game TEXT NOT NULL,
    day TEXT NOT NULL,
    owner TEXT,
    calls INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game, day)
);
CREATE INDEX IF NOT EXISTS ai_usage_day ON ai_usage (day, owner);
"""


@dataclass
class Limits:
    calls_per_game: int = 600
    tokens_per_game: int = 6_000_000

    ENV = {"calls_per_game": "MTGTABLE_AI_CALLS_PER_GAME", "tokens_per_game": "MTGTABLE_AI_TOKENS_PER_GAME"}

    @classmethod
    def from_env(cls) -> "Limits":
        values = {}
        for field, name in cls.ENV.items():
            raw = os.environ.get(name, "").strip()
            if raw:
                try:
                    values[field] = int(raw.replace("_", ""))
                except ValueError:
                    raise ValueError("%s must be an integer" % name)
        return cls(**values)


class LimitReached(Exception):
    """利用の上限に当たった（その対局を中断にする。理由は画面に出る）。"""


def _today() -> str:
    return datetime.date.today().isoformat()


class Budget:
    """AI の利用（呼び出し回数・トークン数）を対局ごとに数える。db があれば SQLite（再起動しても続く）、無ければメモリ。"""

    def __init__(self, limits: Limits, db=None):
        self.limits, self.db = limits, db
        self._mem: dict = {}  # (game, day) → [calls, tokens]
        self._lock = threading.Lock()
        if db:
            conn = connect(db)
            try:
                conn.executescript(SCHEMA)
            finally:
                conn.close()

    def used(self, game: str) -> dict:
        if self.db:
            conn = connect(self.db)
            try:
                g = conn.execute("SELECT COALESCE(SUM(calls), 0), COALESCE(SUM(tokens), 0) FROM ai_usage WHERE game = ?",
                                 (game,)).fetchone()
            finally:
                conn.close()
            return {"game_calls": g[0], "game_tokens": g[1]}
        with self._lock:
            rows = [v for (gm, _), v in self._mem.items() if gm == game]
        return {"game_calls": sum(v[0] for v in rows), "game_tokens": sum(v[1] for v in rows)}

    def check(self, game: str) -> None:
        u, lim = self.used(game), self.limits
        if u["game_calls"] >= lim.calls_per_game:
            raise LimitReached("この対局の AI の呼び出しが上限（%d 回）に達した" % lim.calls_per_game)
        if u["game_tokens"] >= lim.tokens_per_game:
            raise LimitReached("この対局の AI の利用が上限（%d トークン）に達した" % lim.tokens_per_game)

    def record(self, game: str, usage: dict) -> None:
        tokens, day = int(usage.get("total_tokens") or 0), _today()
        if self.db:
            conn = connect(self.db)
            try:
                conn.execute("INSERT INTO ai_usage (game, day, calls, tokens) VALUES (?, ?, 1, ?) "
                             "ON CONFLICT (game, day) DO UPDATE SET calls = calls + 1, tokens = tokens + excluded.tokens",
                             (game, day, tokens))
            finally:
                conn.close()
            return
        with self._lock:
            row = self._mem.setdefault((game, day), [0, 0])
            row[0] += 1
            row[1] += tokens


class Guard:
    """llm.step に渡す: 呼ぶ前に上限を確かめ、呼んだ後に数える。"""

    def __init__(self, budget: Budget, game: str):
        self.budget, self.game = budget, game

    def check(self) -> None:
        self.budget.check(self.game)

    def record(self, usage: dict) -> None:
        self.budget.record(self.game, usage)


# ---------------------------------------------------------------- 中断・再開

def status(store) -> Optional[dict]:
    """対局の AI の状態（卓の文書 "ai"）。中断していれば {"status": "suspended", "reason", "at"}。"""
    return store.read_doc("ai")


def suspend(store, reason: str) -> None:
    store.write_doc("ai", {"status": "suspended", "reason": reason,
                           "at": datetime.datetime.now().isoformat(timespec="seconds")})


def resume(store) -> None:
    store.write_doc("ai", {"status": "running", "at": datetime.datetime.now().isoformat(timespec="seconds")})


def suspended(store) -> bool:
    return (status(store) or {}).get("status") == "suspended"


# ---------------------------------------------------------------- ワーカー

def _needs_ai(st) -> bool:
    """審判か AI の席の番か（自動パスで進む所は進めてから）。"""
    from . import prompt
    if prompt.advance(st):
        return True
    return prompt.next_role(st, prompt.ai_seats(st)) is not None


def _settled(reason: str) -> bool:
    """llm.run の止まった理由が、正常（人間の番・決着）か。"""
    return reason == llm.GAME_OVER or reason.startswith("waiting on ")


class Worker:
    def __init__(self, viewer, budget: Budget, report=print,
                 interval: float = INTERVAL, concurrency: int = CONCURRENCY):
        self.viewer, self.budget, self.report = viewer, budget, report
        self.interval, self.concurrency = interval, concurrency
        self.seen: dict = {}  # 対局 → 最後に回し終えたときの stamp（変わっていなければ回さない）
        self.running: set = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self) -> "Worker":
        threading.Thread(target=self._loop, name="mtgtable-ai", daemon=True).start()
        return self

    def forget(self, game: str) -> None:
        """次の見張りで、盤面が変わっていなくてもその対局を回す（再開したとき）。"""
        self.seen.pop(game, None)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception:  # noqa: BLE001  見張りは止めない
                self.report("[ai] scan failed:\n" + traceback.format_exc())
            self.viewer.wait_change(self.interval)

    def scan(self) -> list:
        """変わった対局を回し始める。始めた対局の id を返す。"""
        started = []
        for game in self.viewer.source.ids():
            with self._lock:
                if game in self.running or len(self.running) >= self.concurrency:
                    continue
            st = self.viewer.source.open(game)
            try:
                stamp = st.stamp()
            except (OSError, LookupError):
                continue
            if self.seen.get(game) == stamp or suspended(st):
                continue
            with self._lock:
                self.running.add(game)
            started.append(game)
            threading.Thread(target=self._handle, args=(game, st, stamp), daemon=True).start()
        return started

    def _handle(self, game: str, st, stamp) -> None:
        try:
            while True:
                reason = self.run_game(game, st)
                # 印を先に取ってから、まだ AI の仕事があるか見る。回し終えた直後に人間が書いても、その書き込みは
                # 印より後なので次の見張りで拾うか、ここで見つかる（取りこぼさない）
                stamp = st.stamp()
                if not _settled(reason) or not _needs_ai(st):
                    break
        except (OSError, LookupError):
            pass
        finally:
            self.seen[game] = stamp
            with self._lock:
                self.running.discard(game)
            self.viewer.notify()

    def run_game(self, game: str, st) -> str:
        """1つの対局を、人間の番か決着まで回す。止まった理由を返す（中断したら、その理由）。"""
        def report(line: str) -> None:
            self.report("[ai %s] %s" % (game, line))
            self.viewer.notify()  # 1手ごとに画面へ
        guard = Guard(self.budget, game)
        try:
            reason = llm.run(st, report=report, guard=guard)
        except LimitReached as e:
            reason = str(e)
        except llm.LLMError as e:
            reason = "AI の API を呼べなかった: %s" % e
        except Exception as e:  # noqa: BLE001  1つの対局の失敗で、他の対局を止めない
            reason = "AI の処理で失敗した: %s: %s" % (type(e).__name__, e)
            self.report("[ai %s] %s" % (game, traceback.format_exc()))
        if not _settled(reason):
            suspend(st, reason)
            self.report("[ai %s] 中断: %s" % (game, reason))
        return reason

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """テスト用: 回している対局が無くなるまで待つ。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self.running:
                    return True
            time.sleep(0.02)
        return False

