"""観戦ビューアと GUI の対局（24〜26節）: 対局フォルダを読んでブラウザに表示し、`--play` なら席の操作も受ける。

`python -m mtgtable serve [--root playtest] [--port 8765] [--play]` で起動し、http://127.0.0.1:8765/ を開く。
対局フォルダ（initial.json・log.jsonl・state.json）が正本で、AI が CLI で書いた変更も SSE で画面に届く。
`--db data/mtg.sqlite`（か環境変数 MTGTABLE_DB）なら、対局フォルダの代わりに SQLite の DB から読む（sqlstore）。

席（seat）ごとに、その Player が知り得る情報だけを返す（info.player_view）。log は AI のラベルに非公開の
情報が入りうるので、全知の judge の席にだけ返す。画面上の配置・選択などは画面の側だけで持つ（25節）。

`--play` では、`invite` で鍵を作った対局（席の鍵がある対局）を席の鍵で守る: 読むのも書くのも、
鍵を持つ席だけ（judge の席・他の席は見せない）。席が書けるのは依頼（request）・宣言（declare）・回答（answer）
だけで、卓の op は書けない（盤面は審判が動かす。play.seat_batch）。見ていた cursor（expect）と違えば 409 で断る
（CLI の AI・審判と同時に書いても、古い盤面のまま書かない）。
"""
from __future__ import annotations

import http.cookies
import json
import pathlib
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from . import carddb, info, play, prompt
from .engine import public_result
from .operations import OperationError, summarize_op
from .decks import Decks, Invalid, check as check_deck_text
from . import llm, worker
from .lobby import IDLE_LIMIT, IDLE_NOTICE, Lobby, Refused as LobbyRefused, repo_decks
from .owners import Owners
from .sqlstore import SqliteGames
from .store import BaseStore, FileGames, StaleCursor

STATIC = pathlib.Path(__file__).with_name("web")
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8"}
IMAGE_MAX_AGE = 7 * 24 * 3600  # ブラウザにもキャッシュさせる
KEYFRAME_EVERY = 25  # 時系列は、この件数ごとに丸ごとの view、間は前の view からの差分
MAX_BODY = 256 * 1024  # 書き込みの要求の大きさの上限
OWNER_COOKIE = "mtg_owner"  # 公開のサーバーの所有者の鍵
OWNER_MAX_AGE = 5 * 365 * 24 * 3600


def _announce(built) -> None:
    """プロンプトを作ったら、サーバーのコンソールに出す（手動でモデルに渡す人が見る）。"""
    if built:
        who = "審判" if built["role"] == "judge" else built["role"]
        print("[%s] prompt for %s: %s" % (built["dir"].parent.parent.name, who, built["latest"]), flush=True)


class Forbidden(Exception):
    """席の鍵が無い・違う（403）。"""


def view_diff(a, b, path=()) -> list:
    """2つの view（JSON）の差分。["s", path, value]（設定）と ["d", path]（削除）の並び。
    リストは長さが同じなら要素ごとに、違えば丸ごと置き換える。"""
    if type(a) is not type(b) or not isinstance(b, (dict, list)):
        return [] if a == b else [["s", list(path), b]]
    out = []
    if isinstance(b, dict):
        for k in a:
            if k not in b:
                out.append(["d", list(path) + [k]])
        for k, v in b.items():
            out.extend(view_diff(a[k], v, path + (k,)) if k in a else [["s", list(path) + [k], v]])
        return out
    if len(a) != len(b):
        return [["s", list(path), b]]
    for i, (x, y) in enumerate(zip(a, b)):
        out.extend(view_diff(x, y, path + (i,)))
    return out


class Viewer:
    """対局の読み取りと、途中の時点の状態のキャッシュ。対局は root（対局フォルダの親）か db（SQLite）から開く。"""

    def __init__(self, root, offline: bool = False, play: bool = False, db=None, site: bool = False):
        if site and not db:
            raise ValueError("serve --site needs --db (the site keeps owners in the database)")
        self.source = SqliteGames(db) if db else FileGames(root)
        self.offline = offline
        self.site = site  # 公開のサーバー: 所有者の鍵（Cookie）で、自分が席を持つ対局だけ見せる
        self.play = play or site  # 席の操作（書き込み）を受けるか
        self.owners = Owners(db) if site else None
        self.decks = Decks(db) if site else None
        self.lobby = Lobby(db, self.source, self.decks, self.owners, offline) if site else None
        self.worker = None  # serve が AI のワーカーを動かすとき（審判と AI の席をサーバーの中で回す）
        self._no_image: set = set()  # 画像が無かった・取れなかったカード（何度も取りに行かない）
        self._timelines: "OrderedDict[tuple, dict]" = OrderedDict()
        self._cache: "OrderedDict[tuple, object]" = OrderedDict()
        self._lock = threading.Lock()
        self._changed = threading.Condition()  # サーバー自身が書いたら、SSE の購読者をすぐ起こす

    def notify(self) -> None:
        with self._changed:
            self._changed.notify_all()

    def wait_change(self, timeout: float) -> None:
        """サーバーが書くか timeout 秒たつまで待つ（別のプロセスの書き込みは、呼び出し側が stamp で拾う）。"""
        with self._changed:
            self._changed.wait(timeout)

    def store(self, game: str) -> BaseStore:
        st = self.source.open(game)
        if not st.exists():
            raise LookupError("unknown game %r" % game)
        return st

    def games(self, owner: Optional[str] = None) -> list:
        """対局の一覧。公開のサーバーでは owner が席を持つ対局だけで、my_seat にその席（鍵が無くても Cookie で対局できる）。"""
        mine = self.owners.seats_of(owner) if self.site else None
        out = []
        for gid in self.source.ids():
            if mine is not None and gid not in mine:
                continue
            st = self.source.open(gid)
            g = st.summary()
            row = {"id": gid, "version": g["version"], "turn": g["turn"], "players": g["players"],
                   "seats": sorted(play.seats(st)) if self.play else []}
            if mine is not None:
                row["my_seat"] = mine[gid]
            out.append(row)
        return out

    def guarded(self, st: BaseStore) -> bool:
        """席の鍵で守る対局か（--play で、invite した対局）。"""
        return self.play and bool(play.seats(st))

    def authorize(self, game: str, seat: Optional[str], token: Optional[str], write: bool = False,
                  owner: Optional[str] = None) -> BaseStore:
        """seat として読む・書くことを許すか。書くには --play と、その席の鍵が要る。
        公開のサーバー（site）では、その席を持つ所有者（Cookie）か、まだ誰も取っていない席の鍵だけ。鍵を使った所有者が
        その席を取る（以後は Cookie だけで通り、同じ鍵を他の人が使っても通らない）。judge の席と、鍵の無い対局は見せない。"""
        st = self.store(game)
        if write and not self.play:
            raise Forbidden("this server is read-only (start it with serve --play)")
        if self.site:
            if seat is None:
                raise Forbidden("the judge seat is not shown on this server")
            holder = self.owners.seat_owner(game, seat)
            if owner and holder == owner:
                return st
            if holder is None and play.check_token(st, seat, token):
                if owner:
                    self.owners.claim(game, seat, owner)
                return st
            raise Forbidden("this seat is not yours" if holder else
                            "this seat needs its key (the invite URL)")
        if (write or self.guarded(st)) and (seat is None or not play.check_token(st, seat, token)):
            raise Forbidden("this seat needs its key (python -m mtgtable invite %s --seat pN)" % game)
        return st

    def resume_ai(self, game: str, seat: str, token: Optional[str], owner: Optional[str] = None) -> dict:
        """中断した対局の AI を再開する（席を持つ人だけ）。"""
        st = self.authorize(game, seat, token, write=True, owner=owner)
        if not worker.suspended(st):
            raise Forbidden("the AI of this game is not suspended")
        worker.resume(st)
        if self.worker is not None:
            self.worker.forget(game)  # 盤面は変わっていないので、見直すように言う
        self.notify()
        return {"ok": True}

    def ai_status(self, game: str) -> Optional[dict]:
        return worker.status(self.store(game)) if self.worker is not None else None

    def participant(self, game: str, owner: Optional[str]) -> BaseStore:
        """公開のサーバーで、owner がその対局の席を持つか（更新の通知など、席を問わない読み取り）。"""
        st = self.store(game)
        if self.site and not self.owners.seats_of(owner, game):
            raise Forbidden("this game is not yours")
        return st

    def seat_write(self, game: str, seat: str, token: Optional[str], what: str, body: dict,
                   owner: Optional[str] = None) -> dict:
        """席の Player の書き込み（request / declare / answer）。卓の op は受けない（play.seat_batch が宣言に変える）。"""
        st = self.authorize(game, seat, token, write=True, owner=owner)
        with st.lock():
            cur = st._check(body.get("expect"))
            try:
                batch = play.seat_batch(st.load(), seat, what, body)
            except play.Refused as e:
                raise Forbidden(str(e))
            result = st._apply(batch, cur)
        result.pop("_state", None)
        if self.worker is None:  # 手で回すとき: 依頼・マリガンで審判の番になったら、審判のプロンプトをすぐ作る
            _announce(prompt.auto_build(st))
        self.notify()
        return {"result": public_result(result), "cursor": st.cursor()}

    def get_stops(self, game: str, seat: str, token: Optional[str], owner: Optional[str] = None) -> dict:
        st = self.authorize(game, seat, token, write=True, owner=owner)
        return {"stops": play.get_stops(st, seat)}

    def set_stops(self, game: str, seat: str, token: Optional[str], body: dict, owner: Optional[str] = None) -> dict:
        """止める場所（非公開。卓の記録には載せず、本人と審判のプロンプトにだけ出る）。"""
        st = self.authorize(game, seat, token, write=True, owner=owner)
        if play.waiting_on(st.load()) == play.JUDGE:  # 審判が今の止める場所で処理している間は変えない
            raise Forbidden("the judge is processing; change the stops after the ruling")
        return {"stops": play.set_stops(st, seat, body.get("stops"))}

    @staticmethod
    def stamp(st: BaseStore) -> tuple:
        return st.stamp()

    def state_at(self, st: BaseStore, at: Optional[int]):
        cur = st.cursor()
        if at is None or at >= cur:
            return st.load(), cur
        at = max(0, at)
        key = (str(st.root), at, self.stamp(st))
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key], at
        s = st.replay(at)
        with self._lock:
            self._cache[key] = s
            while len(self._cache) > 32:
                self._cache.popitem(last=False)
        return s, at

    def view(self, game: str, seat: Optional[str], at: Optional[int], library: bool = False) -> dict:
        st = self.store(game)
        s, pos = self.state_at(st, at)
        if seat is not None and seat not in s.players:
            raise LookupError("unknown seat %r" % seat)
        v = play.decorate(info.player_view(s, seat, graveyard=True, library=library), s)
        v["position"] = pos
        v["cursor"] = st.cursor()
        return v

    def timeline(self, game: str, seat: Optional[str]) -> dict:
        """0件目から cursor までの、席から見た view の時系列。最初から1回だけ適用し直して作る。

        frames[i] は i 件目の後の view。KEYFRAME_EVERY 件ごとに丸ごと（{"k": view}）、間は直前の view からの
        差分（{"d": [...]}）。再生はクライアントだけで行い、対局が更新されたら取り直す。"""
        st = self.store(game)
        key = (str(st.root), seat, self.stamp(st))
        with self._lock:
            if key in self._timelines:
                return self._timelines[key]
        cur = st.cursor()
        frames, prev = [], None
        for pos, s in st.replay_iter(cur):
            if seat is not None and seat not in s.players:
                raise LookupError("unknown seat %r" % seat)
            v = play.decorate(info.player_view(s, seat, graveyard=True, library=True), s)
            v["position"], v["cursor"] = pos, cur
            frames.append({"k": v} if prev is None or pos % KEYFRAME_EVERY == 0 else {"d": view_diff(prev, v)})
            prev = v
        out = {"cursor": cur, "seat": seat or "judge", "keyframe_every": KEYFRAME_EVERY, "frames": frames}
        with self._lock:
            self._timelines[key] = out
            while len(self._timelines) > 4:
                self._timelines.popitem(last=False)
        return out

    def log(self, game: str) -> list:
        st = self.store(game)
        cur = st.cursor()
        out = []
        entries = st.read_log()
        for e in entries:
            prev_cont = e["seq"] > 1 and entries[e["seq"] - 2].get("cont")
            out.append({
                "seq": e["seq"], "version": e["version"], "batch": e.get("batch"),
                "batch_label": e.get("batch_label", ""), "actor": e["actor"] or "judge",
                "label": e["label"], "summary": "; ".join(summarize_op(op) for op in e["act"]),
                "proc": e.get("proc", ""), "proxy_by": e.get("proxy_by"),
                "cont": bool(e.get("cont")), "continued": bool(prev_cont), "undone": e["seq"] > cur,
                "steps": [{"op": summarize_op(s["op"]), "parent": s.get("parent"), "events": s["events"]}
                          for s in e["steps"]],
            })
        return out


    def image(self, name: str, face: int):
        key = (name, face)
        if not name or key in self._no_image:
            return None
        try:
            p = carddb.image(name, face, offline=self.offline)
        except (LookupError, OSError):
            p = None
        if p is None and not self.offline:
            self._no_image.add(key)
        return p


    def symbol(self, code: str):
        key = ("symbol", code)
        if key in self._no_image:
            return None
        try:
            p = carddb.symbol(code, offline=self.offline)
        except (LookupError, OSError):
            p = None
        if p is None and not self.offline:
            self._no_image.add(key)
        return p


def oracle_text(name: str) -> Optional[str]:
    rec = carddb.lookup(name, offline=True)
    return carddb.format_card(rec) if rec else None


class Handler(BaseHTTPRequestHandler):
    viewer: Viewer = None  # serve() が設定する

    def log_message(self, fmt, *args):  # アクセスログは出さない
        pass

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for cookie in getattr(self, "_cookies", ()):
            self.send_header("Set-Cookie", cookie)
        self._cookies = []
        self.end_headers()
        self.wfile.write(body)

    # ---- 公開のサーバー（site）: 所有者の鍵の Cookie と、書き込みの Origin

    def _owner(self) -> Optional[str]:
        if not self.viewer.site:
            return None
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return None
        morsel = jar.get(OWNER_COOKIE)
        return self.viewer.owners.owner_of(morsel.value if morsel else None)

    def _give_owner_key(self, key: str) -> None:
        """所有者の鍵を Cookie で渡す（JavaScript からは読めない。手元の 127.0.0.1 以外では HTTPS だけで送る）。"""
        host = urlparse("//" + (self.headers.get("Host") or "")).hostname or ""
        secure = "" if host in ("localhost", "127.0.0.1", "::1") else "; Secure"
        self._cookies = getattr(self, "_cookies", []) + [
            "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s" % (OWNER_COOKIE, key, OWNER_MAX_AGE, secure)]

    def _same_origin(self) -> bool:
        """書き込みは、このサーバーのページからだけ（Cookie で通るので、他のサイトからの送信を断る）。"""
        origin = self.headers.get("Origin")
        return bool(origin) and urlparse(origin).netloc == (self.headers.get("Host") or "")

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _token(self) -> Optional[str]:
        auth = self.headers.get("Authorization", "")
        return auth[7:].strip() if auth.startswith("Bearer ") else None

    def _write(self, method: str) -> None:
        """POST・PUT・DELETE の共通: 本文を読み、Origin を確かめ、振り分けて、例外を HTTP の状態に直す。"""
        parts = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json({"error": "request too large"}, 413)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
            if self.viewer.site and not self._same_origin():
                raise Forbidden("writes must come from this site (Origin)")
            owner = self._owner()
            if parts[:2] == ["api", "decks"] and self.viewer.site:
                return self._decks(method, parts[2:], body, owner)
            if self.viewer.site and (parts[:2] == ["api", "invites"] or parts == ["api", "games"]
                                     or parts[:2] == ["api", "games"] and parts[3:] == ["timeout"]):
                return self._lobby(method, parts, body, owner)
            if method != "POST":
                return self._json({"error": "not found"}, 404)
            return self._post(parts, body, owner)
        except (Forbidden, LobbyRefused) as e:
            self._json({"error": str(e)}, 403)
        except StaleCursor as e:
            self._json({"error": str(e), "stale": True}, 409)
        except TimeoutError as e:
            self._json({"error": str(e)}, 503)
        except LookupError as e:
            self._json({"error": str(e)}, 404)
        except Invalid as e:
            self._json({"error": str(e), "check": e.result}, 400)
        except (ValueError, OperationError) as e:  # JSONDecodeError は ValueError
            self._json({"error": str(e)}, 400)

    def do_POST(self):  # noqa: N802
        self._write("POST")

    def do_PUT(self):  # noqa: N802
        self._write("PUT")

    def do_DELETE(self):  # noqa: N802
        self._write("DELETE")

    def _decks(self, method: str, rest: list, body: dict, owner: Optional[str]) -> None:
        """デッキ（公開のサーバー）: POST /api/decks（登録）・POST /api/decks/check（検査だけ）・
        PUT / DELETE /api/decks/<id>。読むのは GET（do_GET）。どれも自分のデッキだけ。"""
        if not owner:
            raise Forbidden("no owner key (open the site first)")
        decks = self.viewer.decks
        if method == "POST" and rest == ["check"]:
            return self._json(check_deck_text(body.get("text"), body.get("format") or "standard", decks.resolve))
        if method == "POST" and not rest:
            return self._json(decks.create(owner, body), 201)
        if method == "PUT" and len(rest) == 1:
            return self._json(decks.update(owner, rest[0], body))
        if method == "DELETE" and len(rest) == 1:
            decks.delete(owner, rest[0])
            return self._json({"ok": True})
        self._json({"error": "not found"}, 404)

    def _lobby(self, method: str, parts: list, body: dict, owner: Optional[str]) -> None:
        """対局を作る（公開のサーバー）: POST /api/games（AI と・招待）・POST /api/games/<対局>/timeout（時間切れ）・
        POST /api/invites/<id>/join・renew・DELETE /api/invites/<id>。招待の中身は GET（do_GET）。"""
        if not owner:
            raise Forbidden("no owner key (open the site first)")
        lobby = self.viewer.lobby
        if method == "POST" and parts == ["api", "games"]:
            out = lobby.create(owner, body)
            self.viewer.notify()
            return self._json(out, 201)
        if method == "POST" and parts[3:] == ["timeout"]:
            out = lobby.timeout(owner, parts[2], body.get("seat"))
            self.viewer.notify()
            return self._json(out)
        if method == "POST" and len(parts) == 4 and parts[3] == "join":
            return self._json(lobby.join(owner, parts[2], body), 201)
        if method == "POST" and len(parts) == 4 and parts[3] == "renew":
            return self._json(lobby.renew(owner, parts[2]))
        if method == "DELETE" and len(parts) == 3:
            lobby.cancel(owner, parts[2])
            return self._json({"ok": True})
        self._json({"error": "not found"}, 404)

    def _post(self, parts: list, body: dict, owner: Optional[str]) -> None:
        """POST /api/games/<対局>/request・declare・answer・stops・claim、/api/me/recovery・recover。"""
        if parts == ["api", "me", "recovery"] and self.viewer.site:
            if not owner:
                raise Forbidden("no owner key (open the site first)")
            return self._json({"token": self.viewer.owners.new_recovery(owner)})
        if parts == ["api", "me", "recover"] and self.viewer.site:
            found = self.viewer.owners.recover(body.get("token"))
            if not found:
                raise Forbidden("the recovery link is wrong or was replaced")
            self._give_owner_key(found[1])
            return self._json({"ok": True})
        if parts[:2] == ["api", "games"] and len(parts) == 4 and parts[3] == "claim" and self.viewer.site:
            # 招待の URL を開いた: 席の鍵で、その席をこの所有者のものにする（一覧に出るように）
            if not owner:
                raise Forbidden("no owner key (open the site first)")
            self.viewer.authorize(parts[2], body.get("seat"), self._token(), owner=owner)
            return self._json({"game": parts[2], "seat": body.get("seat")})
        if parts[:2] == ["api", "games"] and len(parts) == 4 and parts[3] in play.SEAT_WRITES:
            return self._json(self.viewer.seat_write(parts[2], body.get("seat"), self._token(), parts[3], body,
                                                     owner=owner))
        if parts[:2] == ["api", "games"] and len(parts) == 4 and parts[3] == "stops":
            return self._json(self.viewer.set_stops(parts[2], body.get("seat"), self._token(), body, owner=owner))
        if parts[:2] == ["api", "games"] and len(parts) == 4 and parts[3] == "resume" and self.viewer.play:
            return self._json(self.viewer.resume_ai(parts[2], body.get("seat"), self._token(), owner=owner))
        self._json({"error": "not found"}, 404)

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        try:
            if not parts:
                return self._static("index.html")
            if parts[0] == "static" and len(parts) == 2:
                return self._static(parts[1])
            owner = self._owner()
            if parts[:2] == ["api", "games"] and len(parts) == 2:
                return self._json(self.viewer.games(owner))
            if parts[:2] == ["api", "decks"] and self.viewer.site and len(parts) <= 3:
                if not owner:
                    raise Forbidden("no owner key (open the site first)")
                return self._json(self.viewer.decks.get(owner, parts[2]) if len(parts) == 3
                                  else self.viewer.decks.list(owner))
            if parts == ["api", "ai-decks"] and self.viewer.site:
                return self._json(repo_decks())
            if parts[:2] == ["api", "invites"] and self.viewer.site and len(parts) <= 3:
                if not owner:
                    raise Forbidden("no owner key (open the site first)")
                return self._json(self.viewer.lobby.invite_info(parts[2], q.get("token")) if len(parts) == 3
                                  else self.viewer.lobby.invites(owner))
            if parts[:2] == ["api", "config"]:
                if self.viewer.site and not owner:  # 初めて来たブラウザ（か鍵が消えた）: 所有者を作って鍵を渡す
                    self._give_owner_key(self.viewer.owners.create()[1])
                return self._json({"play": self.viewer.play, "site": self.viewer.site})
            if parts[:2] == ["api", "image"]:
                p = self.viewer.image(q.get("name", ""), int(q.get("face", 0)))
                if p is None:
                    return self._json({"error": "no image"}, 404)
                return self._send(200, p.read_bytes(), "image/jpeg",
                                  "public, max-age=%d" % IMAGE_MAX_AGE)
            if parts[:2] == ["api", "symbol"]:
                p = self.viewer.symbol(q.get("s", ""))
                if p is None:
                    return self._json({"error": "no symbol"}, 404)
                return self._send(200, p.read_bytes(), "image/svg+xml", "public, max-age=%d" % IMAGE_MAX_AGE)
            if parts[:2] == ["api", "oracle"]:
                return self._json({"name": q.get("name"), "text": oracle_text(q.get("name", ""))})
            if parts[:2] == ["api", "games"] and len(parts) == 4:
                game, what = parts[2], parts[3]
                seat = None if q.get("seat", "judge") == "judge" else q["seat"]
                if what != "events":  # 通知は version・cursor だけ（auto の審判は席の鍵を持たずにつなぐ）
                    self.viewer.authorize(game, seat, self._token(), owner=owner)
                elif self.viewer.site:  # 公開のサーバーでは、席を持つ人だけ
                    self.viewer.participant(game, owner)
                if what == "stops" and seat is not None:
                    return self._json(self.viewer.get_stops(game, seat, self._token(), owner=owner))
                if what == "log" and seat is not None and self.viewer.play:
                    return self._json(play.player_log(self.viewer.store(game), seat))
                if what == "view":
                    at = int(q["at"]) if q.get("at") not in (None, "") else None
                    return self._json(self.viewer.view(game, seat, at, library=q.get("library") == "1"))
                if what == "timeline":
                    tl = self.viewer.timeline(game, seat)
                    ai = self.viewer.ai_status(game)
                    if ai:  # AI の状態（中断しているか・理由）。取るたびに変わりうるので、写しに足す
                        tl = dict(tl, ai=ai)
                    if self.viewer.site:  # 相手の番がどれだけ続いているか（時間切れの知らせ。取るたびに変わるので、写しに足す）
                        tl = dict(tl, idle={"seconds": self.viewer.lobby.idle_seconds(self.viewer.store(game)),
                                            "notice": IDLE_NOTICE, "limit": IDLE_LIMIT,
                                            "humans": sorted(self.viewer.owners.seats_in(game))})
                    return self._json(tl)
                if what == "log":
                    if seat is not None:  # ラベルに非公開の情報が入りうるので judge の席だけ
                        return self._json({"error": "log is shown only to the judge seat"}, 403)
                    return self._json(self.viewer.log(game))
                if what == "events":
                    return self._events(game)
            self._json({"error": "not found"}, 404)
        except (Forbidden, LobbyRefused) as e:
            self._json({"error": str(e)}, 403)
        except (LookupError, ValueError) as e:
            self._json({"error": str(e)}, 404)

    def _static(self, name: str) -> None:
        path = STATIC / name
        if path.parent != STATIC or not path.is_file():
            return self._json({"error": "not found"}, 404)
        self._send(200, path.read_bytes(), _TYPES.get(path.suffix, "application/octet-stream"))

    def _events(self, game: str) -> None:
        """SSE: 対局フォルダが変わったら {version, cursor} を送る。サーバー自身の書き込みはすぐ、別のプロセス（CLI・auto）の
        書き込みは 0.5 秒ごとの見張りで拾う。GUI の自動更新と、auto --watch の待ち合わせに使う。"""
        st = self.viewer.store(game)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last, beat = None, time.time()
        try:
            while True:
                stamp = self.viewer.stamp(st)
                if stamp != last:
                    last = stamp
                    s = st.load()
                    msg = json.dumps({"version": s.version, "cursor": st.cursor()})
                    self.wfile.write(("data: %s\n\n" % msg).encode("utf-8"))
                    self.wfile.flush()
                elif time.time() - beat > 15:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    beat = time.time()
                self.viewer.wait_change(0.5)
        except (OSError, LookupError):  # 切断（BrokenPipe など）・対局が消えた
            pass


# ---------------------------------------------------------------- 静的サイト

def _card_names(value, out: set) -> None:
    """view（JSON）の中のカード名を集める。"""
    if isinstance(value, dict):
        if isinstance(value.get("name"), str) and isinstance(value.get("id"), str) and value["id"].startswith("#"):
            out.add(value["name"])
        for v in value.values():
            _card_names(v, out)
    elif isinstance(value, list):
        for v in value:
            _card_names(v, out)


def export_site(root, dest, games=None, offline: bool = False, db=None) -> list:
    """観戦ビューアを静的サイトとして書き出す（GitHub Pages などに置く用）。judge の席だけ。

    dest/index.html・static/・data/games.json・data/<対局>/{timeline,log}.json・data/cards.json を作る。
    カードの画像・文（オラクル）・マナ・シンボルは含めず、見る人のブラウザが Scryfall から取る
    （cards.json は画像の URL だけ）。書き出した対局の一覧を返す。"""
    viewer = Viewer(root, offline, db=db)
    dest = pathlib.Path(dest)
    available = {g["id"]: g for g in viewer.games()}
    ids = games or list(available)
    (dest / "data").mkdir(parents=True, exist_ok=True)
    listed, names = [], set()
    for gid in ids:
        tl = viewer.timeline(gid, None)
        for f in tl["frames"]:
            _card_names(f, names)
        d = dest / "data" / gid
        d.mkdir(parents=True, exist_ok=True)
        (d / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        (d / "log.json").write_text(json.dumps(viewer.log(gid), ensure_ascii=False, separators=(",", ":")),
                                    encoding="utf-8")
        listed.append(available[gid])
    cards = {}
    for name in sorted(names):
        try:
            cards[name] = {"image": carddb.image_url(name, offline=offline)}
        except (LookupError, OSError):
            cards[name] = {"image": ""}
    (dest / "data" / "games.json").write_text(json.dumps(listed, ensure_ascii=False), encoding="utf-8")
    (dest / "data" / "cards.json").write_text(json.dumps(cards, ensure_ascii=False, separators=(",", ":")),
                                              encoding="utf-8")
    (dest / "static").mkdir(parents=True, exist_ok=True)
    for asset in STATIC.iterdir():
        if asset.suffix in (".js", ".css"):
            (dest / "static" / asset.name).write_bytes(asset.read_bytes())
    index = (STATIC / "index.html").read_text(encoding="utf-8").replace('<html lang="ja">', '<html lang="ja" data-static="1">')
    (dest / "index.html").write_text(index, encoding="utf-8")
    (dest / ".nojekyll").write_text("", encoding="utf-8")  # GitHub Pages に、そのまま配らせる
    return [g["id"] for g in listed]


def start_worker(viewer: Viewer, db=None, limits=None, report=print) -> "worker.Worker":
    """AI のワーカーを動かす（審判と AI の席をサーバーの中で回す）。利用は db があれば DB に数える。"""
    budget = worker.Budget(limits or worker.Limits.from_env(), db)
    owner_of = (lambda game: viewer.owners.seat_owner(game, "p1")) if viewer.site else None  # 対局を作った人
    viewer.worker = worker.Worker(viewer, budget, owner_of=owner_of, report=report).start()
    return viewer.worker


def serve(root="playtest", host: str = "127.0.0.1", port: int = 8765, offline: bool = False,
          play: bool = False, db=None, site: bool = False, ai: bool = False) -> None:
    Handler.viewer = Viewer(root, offline, play, db=db, site=site)
    if ai:
        start_worker(Handler.viewer, db)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    if ai:
        print("  審判と AI の席はサーバーの中で回す（%s/%s、AI の席 %s/%s）"
              % (llm.provider(prompt.JUDGE), llm.model(prompt.JUDGE), llm.provider("p"), llm.model("p")))
    print("mtgtable %s: http://%s:%d/  (%s, Ctrl+C で終了)"
          % ("site" if site else "play" if play else "viewer", host, port, "db: %s" % db if db else "root: %s" % root))
    if play:
        print("  席の URL は python -m mtgtable invite <対局> --seat p1 --port %d で作る" % port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
