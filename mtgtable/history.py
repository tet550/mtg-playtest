"""履歴（手元のサイト）: 対局の一覧・どの視点を再生できるか・対局を消す。

使うのは手元の1人（ホスト）だけなので、DB の対局はすべてホストのもの。ホストの席は、卓の外の文書 "seats" のうち
鍵の無い席（lobby.host_seats）。招待で着いた相手の席は鍵で入る（ホストの席ではない）。

見える範囲（access。ホストから見て）:
- 対局中: ホストの席の視点だけ（全体の視点は相手・AI の手札が見えるので出さない）
- 終わった対局: どの席の視点も、全体（judge）の視点も
- 審判と AI のやりとり（プロンプト・返答の記録）は、どの視点でも Web には出さない（作業ファイルのまま）
"""
from __future__ import annotations

import shutil
from typing import Optional

from . import play
from .lobby import host_seats
from .sqlstore import connect

JUDGE = "judge"


class Refused(ValueError):
    """その操作はできない（サーバーは 403）。"""


def result_of(players: list, seat: Optional[str]) -> str:
    """seat から見た結果: won / lost / ongoing（決着していない）/ over（席を持たない対局の、終わった対局）。"""
    live = [p for p in players if p["status"] == "playing"]
    if len(live) >= 2:
        return "ongoing"
    if seat is None:
        return "over"
    me = next((p for p in players if p["id"] == seat), None)
    return "won" if me and me["status"] in ("playing", "won") else "lost"


class History:
    def __init__(self, db, games):
        self.db, self.games = db, games

    def _info(self, game: str) -> dict:
        st = self.games.open(game)
        if not st.exists():
            raise LookupError("unknown game %r" % game)
        g = st.summary()
        return {"store": st, "summary": g, "finished": g["ended"] is not None, "mine": host_seats(st),
                "guests": sorted(s for s, rec in play.seats(st).items() if rec.get("token_sha256"))}

    # ---- 見える範囲

    def access(self, game: str) -> dict:
        """ホストが見られる視点。{"views": [...], "finished", "live", "mine", "players", "turn"}。"""
        info = self._info(game)
        g, finished = info["summary"], info["finished"]
        seats = [p["id"] for p in g["players"]]
        views = set(seats + [JUDGE]) if finished else set(info["mine"])
        order = ([JUDGE] if JUDGE in views else []) + [p for p in seats if p in views]
        return {"views": order, "finished": finished, "live": not finished, "mine": info["mine"],
                "players": g["players"], "turn": g["turn"]}

    def can_view(self, game: str, view: str) -> bool:
        return view in self.access(game)["views"]

    # ---- 一覧

    def mine(self) -> list:
        """対局の一覧。新しい順。"""
        rows = []
        for game in self.games.ids():
            try:
                info = self._info(game)
            except LookupError:
                continue
            g = info["summary"]
            seat = info["mine"][0] if info["mine"] else None
            rows.append({"id": game, "seat": seat, "created": g["created"], "updated": g["updated"], "ended": g["ended"],
                         "turn": g["turn"], "players": g["players"],
                         "opponent": "human" if info["guests"] else "ai",
                         "result": result_of(g["players"], seat)})
        rows.sort(key=lambda r: r["updated"], reverse=True)
        return rows

    # ---- 消す

    def delete(self, game: str) -> dict:
        """対局そのもの（記録・作業ファイル）を消す。進行中の対局は消せない（投了してから）。"""
        info = self._info(game)
        if not info["finished"] and play.waiting_on(info["store"].load()) is not None:
            raise Refused("対局中は消せません（投了してから）")
        conn = connect(self.db)
        try:
            conn.execute("BEGIN IMMEDIATE")
            for table, col in (("log", "game"), ("docs", "game"), ("games", "id")):
                conn.execute("DELETE FROM %s WHERE %s = ?" % (table, col), (game,))
            conn.execute("COMMIT")
        finally:
            conn.close()
        shutil.rmtree(info["store"].root, ignore_errors=True)
        return {"deleted": True}
