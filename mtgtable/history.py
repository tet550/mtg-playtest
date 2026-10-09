"""履歴・公開の対局・観戦（公開のサーバー）: 誰がどの対局のどの視点を見られるか、共有 URL、履歴から消す。

見える範囲（access）:
- 対局中: 席を持つ人は自分の席の視点だけ（今までどおり）。共有 URL を持つ人は、その URL の席の視点（judge は出さない）
- 終わった公開の対局: 誰でも、どの席の視点も全体（judge）の視点も再生できる
- 終わった非公開の対局: 席を持つ人は、どの席の視点も全体の視点も。他の人は共有 URL の視点だけ
- 共有 URL: 席を持つ人が作り、取り消せる再生専用の URL。視点は作るときに選ぶ（自分の席か全体）。全体の視点の URL は
  終わった対局だけで、人間どうしの対局では両方の席の人が全体の視点の共有に同意していること（同意は一度すれば残る）
- 審判と AI のやりとり（プロンプト・返答の記録）は、どの視点でも Web には出さない（作業ファイルのまま）

履歴から消す: 自分の履歴の一覧と公開の一覧から外す。席を持つ人が全員消したら、対局そのもの（記録・作業ファイル）を消す。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from typing import Optional

from . import play
from .sqlstore import _now, connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS shares (
    id TEXT PRIMARY KEY,
    game TEXT NOT NULL,
    owner TEXT NOT NULL,
    view TEXT NOT NULL,
    token_sha256 TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS shares_game ON shares (game, owner);
CREATE TABLE IF NOT EXISTS hidden (
    game TEXT NOT NULL,
    owner TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (game, owner)
);
CREATE TABLE IF NOT EXISTS judge_consent (
    game TEXT NOT NULL,
    owner TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (game, owner)
);
"""
JUDGE = "judge"
PAGE = 50


class Refused(ValueError):
    """その操作はできない（サーバーは 403）。"""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def result_of(players: list, seat: Optional[str]) -> str:
    """seat から見た結果: won / lost / ongoing（決着していない）/ over（席を持たない人から見た、終わった対局）。"""
    live = [p for p in players if p["status"] == "playing"]
    if len(live) >= 2:
        return "ongoing"
    if seat is None:
        return "over"
    me = next((p for p in players if p["id"] == seat), None)
    return "won" if me and me["status"] in ("playing", "won") else "lost"


class History:
    def __init__(self, db, games, owners):
        self.db, self.games, self.owners = db, games, owners
        conn = connect(db)
        try:
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    def _run(self, sql: str, args=(), many: bool = False):
        conn = connect(self.db)
        try:
            cur = conn.execute(sql, args)
            return cur.fetchall() if many else (cur.fetchone() if cur.description else cur.rowcount)
        finally:
            conn.close()

    # ---- 見える範囲

    def _info(self, game: str) -> dict:
        st = self.games.open(game)
        if not st.exists():
            raise LookupError("unknown game %r" % game)
        g = st.summary()
        site = st.read_doc("site") or {}
        return {"store": st, "summary": g, "finished": g["ended"] is not None,
                "visibility": site.get("visibility", "private"),  # サイトの外で作った対局は非公開
                "humans": self.owners.seats_in(game)}

    def _share(self, game: str, token: Optional[str]) -> Optional[dict]:
        if not token:
            return None
        row = self._run("SELECT id, view, owner FROM shares WHERE game = ? AND token_sha256 = ? AND revoked_at IS NULL",
                        (game, _hash(token)))
        return {"id": row[0], "view": row[1], "owner": row[2]} if row else None

    def access(self, game: str, owner: Optional[str], share: Optional[str] = None) -> dict:
        """owner（と共有 URL の鍵 share）が見られる視点。{"views": [...], "finished", "live", "visibility", "mine",
        "players", "hidden"}。views が空なら何も見られない。"""
        info = self._info(game)
        g, finished = info["summary"], info["finished"]
        mine = sorted(s for s, o in info["humans"].items() if owner and o == owner)
        seats = [p["id"] for p in g["players"]]
        views = set(mine)
        if finished and (info["visibility"] == "public" or mine):
            views.update(seats + [JUDGE])
        if not mine and self._hidden_by_anyone(game):
            views = set()  # 誰かが履歴から消した対局は、公開でも他の人には出さない（共有 URL は別。下）
        s = self._share(game, share)
        if s and (s["view"] != JUDGE or finished):
            views.add(s["view"])
        hidden = bool(owner and self._run("SELECT 1 FROM hidden WHERE game = ? AND owner = ?", (game, owner)))
        order = ([JUDGE] if JUDGE in views else []) + [p for p in seats if p in views]
        return {"views": order, "finished": finished, "live": not finished, "visibility": info["visibility"],
                "mine": mine, "players": g["players"], "turn": g["turn"], "hidden": hidden}

    def _hidden_by_anyone(self, game: str) -> bool:
        return bool(self._run("SELECT 1 FROM hidden WHERE game = ? LIMIT 1", (game,)))

    def can_view(self, game: str, view: str, owner: Optional[str], share: Optional[str] = None) -> bool:
        return view in self.access(game, owner, share)["views"]

    # ---- 一覧

    def mine(self, owner: str) -> list:
        """自分が席を持つ対局（消したものを除く）。新しい順。"""
        rows = []
        for game, seat in self.owners.seats_of(owner).items():
            if self._run("SELECT 1 FROM hidden WHERE game = ? AND owner = ?", (game, owner)):
                continue
            try:
                info = self._info(game)
            except LookupError:
                continue
            g = info["summary"]
            rows.append({"id": game, "seat": seat, "created": g["created"], "updated": g["updated"], "ended": g["ended"],
                         "turn": g["turn"], "players": g["players"], "visibility": info["visibility"],
                         "opponent": "human" if len(info["humans"]) > 1 else "ai",
                         "result": result_of(g["players"], seat)})
        rows.sort(key=lambda r: r["updated"], reverse=True)
        return rows

    def public(self, deck: Optional[str] = None, before: Optional[str] = None, limit: int = PAGE) -> list:
        """終わった公開の対局（誰かが消したものは除く）。終わった日時の新しい順。before でその前から（ページ送り）。"""
        sql = ("SELECT g.id, g.summary, g.created_at, g.ended_at FROM games g JOIN docs d ON d.game = g.id AND d.name = 'site' "
               "WHERE g.ended_at IS NOT NULL AND json_extract(d.data, '$.visibility') = 'public' "
               "AND NOT EXISTS (SELECT 1 FROM hidden h WHERE h.game = g.id)")
        args = []
        if before:
            sql += " AND g.ended_at < ?"
            args.append(before)
        if deck:
            sql += " AND EXISTS (SELECT 1 FROM json_each(g.summary, '$.players') p WHERE json_extract(p.value, '$.name') = ?)"
            args.append(deck)
        sql += " ORDER BY g.ended_at DESC, g.id LIMIT ?"
        args.append(max(1, min(int(limit), PAGE)))
        out = []
        for gid, summary, created, ended in self._run(sql, tuple(args), many=True):
            s = json.loads(summary)
            out.append({"id": gid, "created": created, "ended": ended, "turn": s["turn"], "players": s["players"],
                        "winner": next((p["id"] for p in s["players"] if p["status"] in ("playing", "won")), None)})
        return out

    # ---- 共有 URL

    def _seat_of(self, game: str, owner: str) -> list:
        return sorted(s for s, o in self.owners.seats_in(game).items() if o == owner)

    def share(self, owner: str, game: str, view: str) -> dict:
        """共有 URL の鍵を作る。view は自分の席か "judge"。"""
        mine = self._seat_of(game, owner)
        if not mine:
            raise Refused("この対局の席を持っていません")
        info = self._info(game)
        if view == JUDGE:
            if not info["finished"]:
                raise Refused("全体の視点は、対局が終わってから共有できます")
            others = [o for o in info["humans"].values() if o != owner]
            consented = {r[0] for r in self._run("SELECT owner FROM judge_consent WHERE game = ?", (game,), many=True)}
            if any(o not in consented for o in others):
                raise Refused("相手が全体の視点の共有に同意していません（相手の履歴の「共有」から同意できます）")
        elif view not in mine:
            raise Refused("共有できるのは自分の席の視点か、全体の視点だけです")
        token, sid = secrets.token_urlsafe(24), secrets.token_hex(5)
        self._run("INSERT INTO shares (id, game, owner, view, token_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                  (sid, game, owner, view, _hash(token), _now()))
        return {"id": sid, "game": game, "view": view, "token": token}

    def shares(self, owner: str, game: str) -> list:
        rows = self._run("SELECT id, view, created_at FROM shares WHERE game = ? AND owner = ? AND revoked_at IS NULL "
                         "ORDER BY created_at", (game, owner), many=True)
        return [{"id": r[0], "view": r[1], "created": r[2]} for r in rows]

    def revoke(self, owner: str, game: str, share_id: str) -> None:
        if self._run("UPDATE shares SET revoked_at = ? WHERE id = ? AND game = ? AND owner = ? AND revoked_at IS NULL",
                     (_now(), share_id, game, owner)) != 1:
            raise LookupError("no share %r" % share_id)

    def consent(self, owner: str, game: str) -> dict:
        """全体の視点の共有に同意する（人間どうしの対局で、相手が全体の視点の URL を作れるように）。"""
        if not self._seat_of(game, owner):
            raise Refused("この対局の席を持っていません")
        self._run("INSERT OR IGNORE INTO judge_consent (game, owner, at) VALUES (?, ?, ?)", (game, owner, _now()))
        return {"consented": True}

    def consents(self, game: str) -> list:
        return [r[0] for r in self._run("SELECT owner FROM judge_consent WHERE game = ?", (game,), many=True)]

    # ---- 履歴から消す

    def hide(self, owner: str, game: str) -> dict:
        """自分の履歴から消す。席を持つ人が全員消したら、対局そのものを消す。進行中の対局は消せない（投了してから）。"""
        if not self._seat_of(game, owner):
            raise Refused("この対局の席を持っていません")
        info = self._info(game)
        if not info["finished"] and play.waiting_on(info["store"].load()) is not None:
            raise Refused("対局中は消せません（投了してから）")
        self._run("INSERT OR IGNORE INTO hidden (game, owner, at) VALUES (?, ?, ?)", (game, owner, _now()))
        humans = set(info["humans"].values())
        gone = {r[0] for r in self._run("SELECT owner FROM hidden WHERE game = ?", (game,), many=True)}
        if humans <= gone:
            self._delete(game, info["store"])
            return {"hidden": True, "deleted": True}
        return {"hidden": True, "deleted": False}

    def _delete(self, game: str, st) -> None:
        conn = connect(self.db)
        try:
            conn.execute("BEGIN IMMEDIATE")
            for table, col in (("log", "game"), ("docs", "game"), ("participants", "game"), ("shares", "game"),
                               ("judge_consent", "game"), ("games", "id")):
                conn.execute("DELETE FROM %s WHERE %s = ?" % (table, col), (game,))
            conn.execute("COMMIT")
        finally:
            conn.close()
        shutil.rmtree(st.root, ignore_errors=True)
