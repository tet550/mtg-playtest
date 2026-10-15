"""対局を作る（手元のサイト）: 登録したデッキで、AI と・招待した人と。

使うのは手元の1人（ホスト）。ホストの席は、卓の外の文書 "seats" に鍵の無い席として置く（host_seats）。
審判と AI の席は Claude Code が回す（`next` の監視と mtg-table エージェント）。

- デッキは登録したデッキか、同梱のデッキ（デッキを登録していなくても対局を作れる）
- AI と: すぐに対局を作る。ホストは p1、p2 は "seats" に無い席（AI）
- 招待した人と: まず招待（表 invites）だけを作り、URL を返す。招待された人（ゲスト）は、デッキリストを貼るか同梱のデッキを
  選んで席に着く。その時点で対局を作り（相手のデッキが決まるまで初期状態を作れないので）、ゲストの席の鍵を返す。
  ゲストはその鍵（ブラウザが覚える）で自分の席だけを読み書きする。招待の URL は1回だけ使え、期限つきで、ホストは
  取り消し・URL の作り直しができる
- デッキは対局を作る時点の写しを初期状態に入れる（meta.decks.<席>: 名前・フォーマット・本文・プレイ方針）。後でデッキを
  編集・削除しても、対局と Replay は変わらない
- 時間切れ: 人間どうしの対局で、相手の番のまま IDLE_LIMIT 秒書き込みが無ければ、待っている側が勝ちを申し立てられる
  （相手の投了として卓に書く）
"""
from __future__ import annotations

import datetime
import hashlib
import json
import secrets
from typing import Optional

from . import carddb, paths, play
from .decks import LOCAL, Decks, Invalid, check as check_deck_text, decklist
from .setup import bundled_decks, check_deck, load_decklist, new_game
from .sqlstore import SqliteGames, _now, connect

INVITE_TTL = 72  # 招待の URL の期限（時間）
IDLE_NOTICE = 5 * 60  # 相手の番がこれだけ続いたら、画面に知らせる（秒）
IDLE_LIMIT = 30 * 60  # これだけ続いたら、時間切れで勝ちを申し立てられる（秒）

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
    return bundled_decks()


def start_game(store, entries: dict, seed: Optional[int] = None, offline: bool = False):
    """entries {席: 登録デッキの写し（snapshot）か、リポジトリのデッキ名（str）} で対局を作る（cli new と同じ準備）。"""
    decks, strategies, sources = {}, {}, {}
    for pid, e in entries.items():
        if isinstance(e, str):
            if e not in repo_decks():
                raise LookupError("unknown deck %r" % e)
            decks[pid] = load_decklist(paths.decklists_dir() / ("%s.txt" % e))  # 名前で渡すと今いるフォルダのファイルが優先される
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


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def host_seats(store) -> list:
    """ホスト（手元の1人）の席: "seats" にあって鍵の無い席（対局を作るときに置く）。鍵のある席は招待のゲスト（URL の鍵で入る）。"""
    return sorted(s for s, rec in play.seats(store).items() if "claimed" in rec and not rec.get("token_sha256"))


class Lobby:
    def __init__(self, db, games: SqliteGames, decks: Decks, offline: bool = False):
        self.db, self.games, self.decks, self.offline = db, games, decks, offline
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

    def _new_game(self, entries: dict, host: list, game: Optional[str] = None) -> str:
        """対局を作り、host の席をホストの席にする（鍵は作らない）。"""
        game = game or secrets.token_hex(5)
        st = self.games.open(game)
        start_game(st, entries, offline=self.offline)
        now = _now()
        st.write_doc("seats", {seat: {"claimed": now} for seat in host})  # 鍵の無い人間の席（AI に回さない）
        return game

    # ---- 作る

    def create(self, body: dict) -> dict:
        """{"deck": "deck:<id>" | "repo:<名前>"（登録したデッキか同梱のデッキ）, "opponent": "ai" | "human", "ai_deck": 同じ形}"""
        mine = self._entry(str(body.get("deck") or ""))
        if body.get("opponent") == "ai":
            game = self._new_game({"p1": mine, "p2": self._entry(str(body.get("ai_deck") or ""))}, ["p1"])
            return {"game": game, "seat": "p1"}
        if body.get("opponent") != "human":
            raise ValueError("opponent must be ai or human")
        invite, token = secrets.token_hex(5), secrets.token_urlsafe(24)
        expires = (datetime.datetime.now() + datetime.timedelta(hours=INVITE_TTL)).isoformat(timespec="seconds")
        self._run("INSERT INTO invites (id, owner, deck, visibility, token_sha256, expires_at, created_at) "
                  "VALUES (?, ?, ?, '', ?, ?, ?)",
                  (invite, LOCAL, json.dumps(mine if isinstance(mine, dict) else {"repo": mine, "name": mine, "format": "standard"},
                                             ensure_ascii=False), _hash(token), expires, _now()))
        return {"invite": invite, "token": token, "expires": expires}

    def _entry(self, ref: str):
        """"deck:<id>" は登録したデッキの写し、"repo:<名前>" は同梱のデッキの名前（start_game の entries の値）。"""
        kind, _, key = ref.partition(":")
        if kind == "deck":
            return snapshot(self.decks.get(key))
        if kind == "repo":
            if key not in repo_decks():
                raise LookupError("unknown deck %r" % key)
            return key
        raise ValueError('deck must be "deck:<id>" or "repo:<name>"')

    # ---- 招待

    def invites(self) -> list:
        """作った招待（相手待ちと、相手が着いて対局になったもの）。新しい順。"""
        rows = self._run("SELECT id, deck, expires_at, created_at, game FROM invites "
                         "ORDER BY created_at DESC LIMIT 50", many=True)
        return [{"id": r[0], "deck": json.loads(r[1])["name"], "expires": r[2], "created": r[3],
                 "game": r[4], "expired": r[4] is None and r[2] < _now()} for r in rows]

    def _invite(self, invite: str, token: Optional[str]):
        row = self._run("SELECT deck, token_sha256, expires_at, game FROM invites WHERE id = ?", (invite,))
        if row is None or not token or not secrets.compare_digest(row[1], _hash(token)):
            raise Refused("this invite link is wrong or was replaced")
        if row[3]:
            raise Refused("this invite has already been used")
        if row[2] < _now():
            raise Refused("this invite has expired")
        return row

    def invite_info(self, invite: str, token: Optional[str]) -> dict:
        """招待された人に見せる中身（相手のデッキの名前とフォーマット・選べる同梱のデッキ）。デッキリストは見せない。"""
        deck, _, expires, _ = self._invite(invite, token)
        deck = json.loads(deck)
        return {"invite": invite, "deck": deck["name"], "format": deck["format"], "expires": expires,
                "repo_decks": repo_decks()}

    def _guest_deck(self, body: dict):
        """ゲストのデッキ: {"repo": 同梱のデッキの名前} か {"text": デッキリスト, "format", "name"}（検査してから写す）。"""
        if body.get("repo"):
            name = str(body["repo"])
            if name not in repo_decks():
                raise LookupError("unknown deck %r" % name)
            return name
        fmt = body.get("format") or "standard"
        result = check_deck_text(body.get("text"), fmt, self.decks.resolve)
        if not result["ok"]:
            raise Invalid(result)
        name = str(body.get("name") or "guest").strip()[:40] or "guest"
        return {"name": name, "format": fmt, "text": body["text"], "cards": result["cards"], "strategy": ""}

    def join(self, invite: str, body: dict) -> dict:
        """招待に着く: ゲストのデッキで対局を作り、ゲストの席（p2）の鍵を返す。招待した人（ホスト）が p1。"""
        deck = self._invite(invite, body.get("token"))[0]
        guest = self._guest_deck(body)
        # 先に招待を使用済みにする（同時に2人が着いても、対局になるのは1人だけ）
        game = secrets.token_hex(5)
        if self._run("UPDATE invites SET game = ? WHERE id = ? AND game IS NULL", (game, invite)) != 1:
            raise Refused("this invite has already been used")
        try:
            host = json.loads(deck)
            self._new_game({"p1": host.get("repo") or host, "p2": guest}, ["p1"], game)
            token = play.invite(self.games.open(game), "p2")
        except Exception:
            self._run("UPDATE invites SET game = NULL WHERE id = ?", (invite,))
            raise
        return {"game": game, "seat": "p2", "token": token}

    def _pending_invite(self, invite: str) -> None:
        row = self._run("SELECT game FROM invites WHERE id = ?", (invite,))
        if row is None:
            raise LookupError("no invite %r" % invite)
        if row[0]:
            raise Refused("this invite has already been used")

    def renew(self, invite: str) -> dict:
        """招待の URL を作り直す（前の URL は使えなくなる。期限も延びる）。"""
        self._pending_invite(invite)
        token = secrets.token_urlsafe(24)
        expires = (datetime.datetime.now() + datetime.timedelta(hours=INVITE_TTL)).isoformat(timespec="seconds")
        self._run("UPDATE invites SET token_sha256 = ?, expires_at = ? WHERE id = ?", (_hash(token), expires, invite))
        return {"invite": invite, "token": token, "expires": expires}

    def cancel(self, invite: str) -> None:
        self._pending_invite(invite)
        self._run("DELETE FROM invites WHERE id = ?", (invite,))

    # ---- 時間切れ

    @staticmethod
    def idle_seconds(store) -> float:
        """最後に書かれてから（盤面が動いてから）の秒数。"""
        last = datetime.datetime.fromisoformat(store.summary()["updated"])
        return max(0.0, (datetime.datetime.now() - last).total_seconds())

    def timeout(self, game: str, seat: str) -> dict:
        """相手（人間）の番のまま IDLE_LIMIT 秒書き込みが無ければ、相手の投了として書く（seat の勝ち）。
        seat として書いてよいか（ホストの席か、席の鍵）は呼ぶ側（web）が確かめる。"""
        st = self.games.open(game)
        idle = play.waiting_on(st.load())
        if idle is None:
            raise Refused("the game is over")
        if idle == seat or idle not in st.load().players or idle not in play.seats(st):
            raise Refused("the game is not waiting on the other player")  # 自分の番・審判・AI の番
        waited = self.idle_seconds(st)
        if waited < IDLE_LIMIT:
            raise Refused("wait %d more minutes" % -(-(IDLE_LIMIT - waited) // 60))
        text = "時間切れ: %d 分以上操作が無かった（%s の申し立て）" % (waited // 60, seat)
        st.apply({"actor": None, "label": "時間切れ", "acts": [
            {"actor": idle, "act": [{"op": "declare", "kind": "concede", "text": text}], "label": "時間切れ"}]})
        return {"loser": idle}
