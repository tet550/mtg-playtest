"""対局を作る（公開のサーバー）: 登録したデッキで、AI と・招待した人と。

- AI と: すぐに対局を作る。自分は p1（席を持つ）、p2 は鍵の無い席（AI）。AI のデッキは自分の登録デッキか、
  リポジトリの decklists/*.txt
- 招待した人と: まず招待（表 invites）だけを作り、URL を返す。招待された人が自分の登録デッキを選んで席に着いた時点で
  対局を作る（相手のデッキが決まるまで初期状態を作れないので）。招待の URL は1回だけ使える（使われたら対局になり、
  もう誰も使えない）。期限つきで、作った人は取り消し・URL の作り直しができる
- デッキは対局を作る時点の写しを初期状態に入れる（meta.decks.<席>: 名前・フォーマット・本文・プレイ方針）。後でデッキを
  編集・削除しても、対局と Replay は変わらない
- 公開・非公開（既定は公開）は対局の文書 "site" に置く。終わった対局の公開の一覧はフェーズ 6
- 時間切れ: 人間どうしの対局で、相手の番のまま IDLE_LIMIT 秒書き込みが無ければ、待っている側が勝ちを申し立てられる
  （相手の投了として卓に書く）
"""
from __future__ import annotations

import datetime
import json
import pathlib
import secrets
from typing import Optional

from . import carddb, play
from .decks import Decks, decklist
from .owners import Owners, _hash
from .setup import check_deck, load_decklist, new_game
from .sqlstore import SqliteGames, _now, connect

REPO_DECKS = pathlib.Path(__file__).resolve().parents[1] / "decklists"
INVITE_TTL = 72  # 招待の URL の期限（時間）
IDLE_NOTICE = 5 * 60  # 相手の番がこれだけ続いたら、画面に知らせる（秒）
IDLE_LIMIT = 30 * 60  # これだけ続いたら、時間切れで勝ちを申し立てられる（秒）
VISIBILITY = ("public", "private")

SCHEMA = """
CREATE TABLE IF NOT EXISTS invites (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    deck TEXT NOT NULL,
    visibility TEXT NOT NULL,
    token_sha256 TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    game TEXT
);
CREATE INDEX IF NOT EXISTS invites_owner ON invites (owner, created_at);
"""


class Refused(ValueError):
    """その操作はできない（サーバーは 403）。"""


def snapshot(deck: dict) -> dict:
    """登録したデッキの、対局に写す部分。"""
    return {k: deck[k] for k in ("name", "format", "text", "cards", "strategy")}


def repo_decks() -> list:
    return sorted(p.stem for p in REPO_DECKS.glob("*.txt"))


def start_game(store, entries: dict, seed: Optional[int] = None, offline: bool = False):
    """entries {席: 登録デッキの写し（snapshot）か、リポジトリのデッキ名（str）} で対局を作る（cli new と同じ準備）。"""
    decks, strategies, sources = {}, {}, {}
    for pid, e in entries.items():
        if isinstance(e, str):
            if e not in repo_decks():
                raise LookupError("unknown deck %r" % e)
            decks[pid] = load_decklist(REPO_DECKS / ("%s.txt" % e))
            sources[pid] = {"repo": e}  # プレイ方針は decklists/strategy/ のファイル（prompt.seat_system）
        else:
            decks[pid] = decklist(e["text"], e["cards"], e["name"])
            strategies[pid] = e.get("strategy") or ""
            sources[pid] = {"format": e["format"], "list": e["text"]}
        check_deck(decks[pid])
    if not offline:  # 足りないカードを 75 枚ずつまとめて引いておく（デッキのキャッシュは、それを束ねるだけになる）
        carddb.resolve_names([n for d in decks.values() for _, n in d.main + d.sideboard])
    caches = {pid: carddb.build_deck_cache(d, fetch=not offline) for pid, d in decks.items()}
    type_lines = {name: rec.get("type_line", "") for c in caches.values() for name, rec in c["cards"].items()}
    state = new_game(decks, seed=secrets.randbelow(2 ** 31) if seed is None else seed, hand=7, type_lines=type_lines)
    for pid, d in decks.items():
        meta = state.meta["decks"][pid]
        meta.update(sources[pid])
        meta["oracle"] = str(carddb.deck_cache_path(d))
        meta["oracle_missing"] = caches[pid]["missing"]
        if pid in strategies:
            meta["strategy"] = strategies[pid]
    store.create(state)
    return state


class Lobby:
    def __init__(self, db, games: SqliteGames, decks: Decks, owners: Owners, offline: bool = False):
        self.db, self.games, self.decks, self.owners, self.offline = db, games, decks, owners, offline
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

    def _new_game(self, entries: dict, humans: dict, visibility: str, game: Optional[str] = None) -> str:
        """対局を作り、人間の席を owner のものにする（席の鍵は作らない。Cookie で入る）。"""
        game = game or secrets.token_hex(5)
        st = self.games.open(game)
        start_game(st, entries, offline=self.offline)
        now = _now()
        st.write_doc("seats", {seat: {"claimed": now} for seat in humans})  # 鍵の無い人間の席（AI に回さない）
        st.write_doc("site", {"visibility": visibility, "created": now})
        for seat, owner in humans.items():
            self.owners.claim(game, seat, owner)
        return game

    @staticmethod
    def _visibility(body: dict) -> str:
        v = body.get("visibility") or "public"
        if v not in VISIBILITY:
            raise ValueError("visibility must be public or private")
        return v

    # ---- 作る

    def create(self, owner: str, body: dict) -> dict:
        """{"deck": 自分のデッキの id, "opponent": "ai" | "human", "ai_deck": "deck:<id>" | "repo:<名前>", "visibility"}"""
        mine = snapshot(self.decks.get(owner, str(body.get("deck") or "")))
        visibility = self._visibility(body)
        if body.get("opponent") == "ai":
            ai = str(body.get("ai_deck") or "")
            kind, _, ref = ai.partition(":")
            if kind == "deck":
                other = snapshot(self.decks.get(owner, ref))
            elif kind == "repo":
                other = ref
            else:
                raise ValueError('ai_deck must be "deck:<id>" or "repo:<name>"')
            game = self._new_game({"p1": mine, "p2": other}, {"p1": owner}, visibility)
            return {"game": game, "seat": "p1"}
        if body.get("opponent") != "human":
            raise ValueError("opponent must be ai or human")
        invite, token = secrets.token_hex(5), secrets.token_urlsafe(24)
        expires = (datetime.datetime.now() + datetime.timedelta(hours=INVITE_TTL)).isoformat(timespec="seconds")
        self._run("INSERT INTO invites (id, owner, deck, visibility, token_sha256, expires_at, created_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (invite, owner, json.dumps(mine, ensure_ascii=False), visibility, _hash(token), expires, _now()))
        return {"invite": invite, "token": token, "expires": expires}

    # ---- 招待

    def invites(self, owner: str) -> list:
        """自分が作った招待（相手待ちと、相手が着いて対局になったもの）。新しい順。"""
        rows = self._run("SELECT id, deck, visibility, expires_at, created_at, game FROM invites WHERE owner = ? "
                         "ORDER BY created_at DESC LIMIT 50", (owner,), many=True)
        return [{"id": r[0], "deck": json.loads(r[1])["name"], "visibility": r[2], "expires": r[3], "created": r[4],
                 "game": r[5], "expired": r[5] is None and r[3] < _now()} for r in rows]

    def _invite(self, invite: str, token: Optional[str]):
        row = self._run("SELECT owner, deck, visibility, token_sha256, expires_at, game FROM invites WHERE id = ?",
                        (invite,))
        if row is None or not token or not secrets.compare_digest(row[3], _hash(token)):
            raise Refused("this invite link is wrong or was replaced")
        if row[5]:
            raise Refused("this invite has already been used")
        if row[4] < _now():
            raise Refused("this invite has expired")
        return row

    def invite_info(self, invite: str, token: Optional[str]) -> dict:
        """招待された人に見せる中身（相手のデッキの名前とフォーマット・公開か）。デッキリストは見せない。"""
        owner, deck, visibility, _, expires, _ = self._invite(invite, token)
        deck = json.loads(deck)
        return {"invite": invite, "deck": deck["name"], "format": deck["format"], "visibility": visibility,
                "expires": expires}

    def join(self, owner: str, invite: str, body: dict) -> dict:
        """招待に着く: 自分のデッキを選び、対局を作る。招待した人が p1、着いた人が p2。"""
        host, deck, visibility, _, _, _ = self._invite(invite, body.get("token"))
        if host == owner:
            raise Refused("you cannot join your own invite (open it in the other player's browser)")
        mine = snapshot(self.decks.get(owner, str(body.get("deck") or "")))
        # 先に招待を使用済みにする（同時に2人が着いても、対局になるのは1人だけ）
        game = secrets.token_hex(5)
        if self._run("UPDATE invites SET game = ? WHERE id = ? AND game IS NULL", (game, invite)) != 1:
            raise Refused("this invite has already been used")
        try:
            self._new_game({"p1": json.loads(deck), "p2": mine}, {"p1": host, "p2": owner}, visibility, game)
        except Exception:
            self._run("UPDATE invites SET game = NULL WHERE id = ?", (invite,))
            raise
        return {"game": game, "seat": "p2"}

    def _own_invite(self, owner: str, invite: str) -> None:
        row = self._run("SELECT game FROM invites WHERE id = ? AND owner = ?", (invite, owner))
        if row is None:
            raise LookupError("no invite %r" % invite)
        if row[0]:
            raise Refused("this invite has already been used")

    def renew(self, owner: str, invite: str) -> dict:
        """招待の URL を作り直す（前の URL は使えなくなる。期限も延びる）。"""
        self._own_invite(owner, invite)
        token = secrets.token_urlsafe(24)
        expires = (datetime.datetime.now() + datetime.timedelta(hours=INVITE_TTL)).isoformat(timespec="seconds")
        self._run("UPDATE invites SET token_sha256 = ?, expires_at = ? WHERE id = ?", (_hash(token), expires, invite))
        return {"invite": invite, "token": token, "expires": expires}

    def cancel(self, owner: str, invite: str) -> None:
        self._own_invite(owner, invite)
        self._run("DELETE FROM invites WHERE id = ? AND owner = ?", (invite, owner))

    # ---- 時間切れ

    @staticmethod
    def idle_seconds(store) -> float:
        """最後に書かれてから（盤面が動いてから）の秒数。"""
        last = datetime.datetime.fromisoformat(store.summary()["updated"])
        return max(0.0, (datetime.datetime.now() - last).total_seconds())

    def timeout(self, owner: str, game: str, seat: str) -> dict:
        """相手（人間）の番のまま IDLE_LIMIT 秒書き込みが無ければ、相手の投了として書く（seat の勝ち）。"""
        if not seat or self.owners.seat_owner(game, seat) != owner:
            raise Refused("this seat is not yours")
        st = self.games.open(game)
        idle = play.waiting_on(st.load())
        if idle is None:
            raise Refused("the game is over")
        if idle == seat or idle not in st.load().players or self.owners.seat_owner(game, idle) is None:
            raise Refused("the game is not waiting on the other player")  # 自分の番・審判・AI の番
        waited = self.idle_seconds(st)
        if waited < IDLE_LIMIT:
            raise Refused("wait %d more minutes" % -(-(IDLE_LIMIT - waited) // 60))
        text = "時間切れ: %d 分以上操作が無かった（%s の申し立て）" % (waited // 60, seat)
        st.apply({"actor": None, "label": "時間切れ", "acts": [
            {"actor": idle, "act": [{"op": "declare", "kind": "concede", "text": text}], "label": "時間切れ"}]})
        return {"loser": idle}
