"""SQLite に保存する対局（サーバーで動かすとき）。標準ライブラリの sqlite3 だけを使う。

共通の処理（apply・undo・redo・replay・fork）は store.BaseStore。ここは読み書きだけを持つ。

    games  対局ごとに1行: 初期状態・現在状態・cursor・変化の印（stamp）・一覧に出す要約・作った／更新した／終わった日時
    log    Operation Log を1件1行（対局の id と seq）
    docs   卓の外の文書（席の鍵 "seats"・席ごとの非公開の設定 "private/<席>"）

書き込みの排他は `BEGIN IMMEDIATE`（書き手は DB 全体で1人ずつ。読み手は WAL で止まらない）。lock() の中の読み書きは、
同じ接続（同じトランザクション）を使う。プロンプトなどの作業ファイルは root（既定は DB の隣の <名前>-files/<対局>/）に置く。
"""
from __future__ import annotations

import contextlib
import datetime
import json
import pathlib
import shutil
import sqlite3
import threading
from typing import Optional

from .model import GameState
from .store import (LOCK_TIMEOUT, BaseStore, GameStore, finished, summary_of, valid_id)

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    initial TEXT NOT NULL,
    state TEXT NOT NULL,
    cursor INTEGER NOT NULL,
    stamp INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE INDEX IF NOT EXISTS games_updated ON games (updated_at);
CREATE TABLE IF NOT EXISTS log (
    game TEXT NOT NULL,
    seq INTEGER NOT NULL,
    entry TEXT NOT NULL,
    PRIMARY KEY (game, seq)
);
CREATE TABLE IF NOT EXISTS docs (
    game TEXT NOT NULL,
    name TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (game, name)
);
"""

_ready: set = set()  # スキーマを作った DB（プロセスごとに1回）
_ready_lock = threading.Lock()


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def connect(path) -> sqlite3.Connection:
    """接続を開く（自動コミット。トランザクションは lock() が明示する）。初めての DB ならスキーマを作る。"""
    path = pathlib.Path(path)
    key = str(path.resolve())
    if key not in _ready:
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(path), timeout=LOCK_TIMEOUT, isolation_level=None)
    except sqlite3.OperationalError as e:  # 置き場所が無い・開けない（対局フォルダが無いときと同じ扱い）
        raise FileNotFoundError("cannot open %s: %s" % (path, e))
    if key not in _ready:
        with _ready_lock:
            if key not in _ready:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(SCHEMA)
                _ready.add(key)
    return conn


def files_dir(db) -> pathlib.Path:
    """作業ファイル（プロンプトなど）の置き場所の既定: data/mtg.sqlite → data/mtg-files/。"""
    db = pathlib.Path(db)
    return db.with_name(db.stem + "-files")


class SqliteGameStore(BaseStore):
    def __init__(self, db, game: str, files=None):
        if not valid_id(game):
            raise ValueError("invalid game id %r (letters, digits, - _ .)" % game)
        self.db = pathlib.Path(db)
        self.name = game
        self.root = pathlib.Path(files or files_dir(self.db)) / game
        self._local = threading.local()  # lock() の中の接続（スレッドごと）

    # ---- 接続

    @contextlib.contextmanager
    def _conn(self):
        """lock() の中ならその接続、外なら新しい接続（使い終わったら閉じる）。"""
        held = getattr(self._local, "conn", None)
        if held is not None:
            yield held
            return
        conn = connect(self.db)
        try:
            yield conn
        finally:
            conn.close()

    @contextlib.contextmanager
    def lock(self, timeout: float = LOCK_TIMEOUT):
        """書き込みを1人ずつにする（別のプロセスの CLI とサーバーの間でも効く）。中の読み書きは1つのトランザクション。"""
        if getattr(self._local, "conn", None) is not None:
            raise RuntimeError("lock() is not reentrant")
        conn = connect(self.db)
        conn.execute("PRAGMA busy_timeout = %d" % int(timeout * 1000))
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            conn.close()
            raise TimeoutError("%s is locked by another writer (%s)" % (self.db, e))
        self._local.conn = conn
        try:
            yield
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            self._local.conn = None
            conn.close()

    def _row(self, cols: str):
        with self._conn() as c:
            row = c.execute("SELECT %s FROM games WHERE id = ?" % cols, (self.name,)).fetchone()
        if row is None:
            raise FileNotFoundError("no game %r in %s" % (self.name, self.db))
        return row

    # ---- 対局

    def exists(self) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM games WHERE id = ?", (self.name,)).fetchone() is not None

    def create(self, state: GameState, overwrite: bool = False) -> None:
        with self.lock():
            c = self._local.conn
            if self.exists():
                if not overwrite:
                    raise FileExistsError("%s already holds game %r (use --force to replace it)" % (self.db, self.name))
                for table, col in (("log", "game"), ("docs", "game"), ("games", "id")):
                    c.execute("DELETE FROM %s WHERE %s = ?" % (table, col), (self.name,))
            now = _now()
            c.execute("INSERT INTO games (id, initial, state, cursor, summary, created_at, updated_at) "
                      "VALUES (?, ?, ?, 0, ?, ?, ?)",
                      (self.name, _dumps(state.to_dict()), _dumps(state.to_dict()), _dumps(summary_of(state)), now, now))

    def read_log(self) -> list:
        with self._conn() as c:
            rows = c.execute("SELECT entry FROM log WHERE game = ? ORDER BY seq", (self.name,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def _write_log(self, entries: list, keep: int = 0) -> None:
        """keep 件目までは前と同じ。その先（Undo で捨てる Redo 履歴を含む）を entries の残りに置き換える。"""
        with self._conn() as c:
            c.execute("DELETE FROM log WHERE game = ? AND seq > ?", (self.name, keep))
            c.executemany("INSERT INTO log (game, seq, entry) VALUES (?, ?, ?)",
                          [(self.name, e["seq"], _dumps(e)) for e in entries[keep:]])
            c.execute("UPDATE games SET stamp = stamp + 1 WHERE id = ?", (self.name,))

    def _save_state(self, state: GameState, cursor: int) -> None:
        now = _now()
        with self._conn() as c:
            ended = c.execute("SELECT ended_at FROM games WHERE id = ?", (self.name,)).fetchone()[0]
            ended = (ended or now) if finished(state) else None  # 巻き戻して決着前に戻れば消す
            c.execute("UPDATE games SET state = ?, cursor = ?, stamp = stamp + 1, summary = ?, updated_at = ?, "
                      "ended_at = ? WHERE id = ?",
                      (_dumps(state.to_dict()), cursor, _dumps(summary_of(state)), now, ended, self.name))

    def _save_initial(self, state: GameState) -> None:
        with self._conn() as c:
            c.execute("UPDATE games SET initial = ?, stamp = stamp + 1 WHERE id = ?", (_dumps(state.to_dict()), self.name))

    def cursor(self) -> int:
        return self._row("cursor")[0]

    def load(self) -> GameState:
        return GameState.from_dict(json.loads(self._row("state")[0]))

    def initial(self) -> GameState:
        return GameState.from_dict(json.loads(self._row("initial")[0]))

    # ---- 卓の外の文書

    def read_doc(self, name: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT data FROM docs WHERE game = ? AND name = ?", (self.name, name)).fetchone()
        return json.loads(row[0]) if row else None

    def write_doc(self, name: str, data: dict) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO docs (game, name, data) VALUES (?, ?, ?) "
                      "ON CONFLICT (game, name) DO UPDATE SET data = excluded.data", (self.name, name, _dumps(data)))

    # ---- 一覧・変化の検知

    def stamp(self) -> tuple:
        return (self._row("stamp")[0],)

    def summary(self) -> dict:
        summary, created, updated, ended = self._row("summary, created_at, updated_at, ended_at")
        return dict(json.loads(summary), id=self.name, created=created, updated=updated, ended=ended)

    def _sibling(self, dest) -> "SqliteGameStore":
        return SqliteGameStore(self.db, pathlib.Path(str(dest)).name, self.root.parent)

    # ---- 移行

    def import_from(self, src: GameStore, overwrite: bool = False) -> None:
        """対局フォルダ（GameStore）の中身（初期状態・log・現在状態・席の鍵・非公開の設定）をそのまま移す。"""
        self.create(src.initial(), overwrite=overwrite)
        entries = src.read_log()
        with self.lock():
            self._write_log(entries, keep=0)
            self._save_state(src.load(), src.cursor())
            for p in sorted(src.root.glob("*.json")) + sorted(src.root.glob("private/*.json")):
                name = p.relative_to(src.root).with_suffix("").as_posix()
                if name not in ("initial", "state"):
                    self.write_doc(name, src.read_doc(name))
        if (src.root / "prompts").is_dir():  # AI のプロンプトと返答（作業ファイル）も続きから使えるように
            shutil.copytree(src.root / "prompts", self.root / "prompts", dirs_exist_ok=True)


class SqliteGames:
    """SQLite の DB の中の対局（serve --db）。"""

    def __init__(self, db, files=None):
        self.db = pathlib.Path(db)
        self.files = pathlib.Path(files) if files else files_dir(self.db)

    def open(self, game: str) -> SqliteGameStore:
        if not valid_id(game):
            raise LookupError("unknown game %r" % game)
        return SqliteGameStore(self.db, game, self.files)

    def ids(self) -> list:
        conn = connect(self.db)
        try:
            return [r[0] for r in conn.execute("SELECT id FROM games ORDER BY updated_at DESC, id")]
        finally:
            conn.close()
