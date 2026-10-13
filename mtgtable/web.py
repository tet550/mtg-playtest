"""手元のサイト（24〜26節）: デッキの登録・対局を作る（AI と・招待した人と）・対局・履歴。

`python -m mtgtable serve [--port 8765]` で起動し、http://127.0.0.1:8765/ を開く。対局は SQLite の DB（sqlstore）に置き、
Claude Code が CLI で書いた変更も SSE で画面に届く。

使うのは手元のブラウザ（ホスト）で、アカウントや Cookie は無い。ホストかどうかは、要求が手元から直接来たか（_host）で決める。
招待のゲストは、席に着いたときに受け取る席の鍵で自分の席だけを読み書きする。審判と AI の席は Claude Code が回す
（`next` の監視と mtg-table エージェント）。

席（seat）ごとに、その Player が知り得る情報だけを返す（info.player_view）。席が書けるのは依頼（request）・宣言（declare）・
回答（answer）だけで、卓の op は書けない（盤面は審判が動かす。play.seat_batch）。見ていた cursor（expect）と違えば 409 で断る
（CLI の審判・AI と同時に書いても、古い盤面のまま書かない）。画面上の配置・選択などは画面の側だけで持つ（25節）。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import threading
import time
import traceback
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from . import carddb, info, paths, play, prompt
from .engine import public_result
from .operations import OperationError, summarize_op
from .decks import Decks, Invalid, check as check_deck_text
from . import history as history_mod, tutorial
from .history import History, Refused as HistoryRefused
from .lobby import IDLE_LIMIT, IDLE_NOTICE, Lobby, Refused as LobbyRefused, host_seats, repo_decks
from .sqlstore import SqliteGames
from .store import BaseStore, StaleCursor

STATIC = pathlib.Path(__file__).with_name("web")
_TYPES = {".svg": "image/svg+xml", ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8"}
IMAGE_MAX_AGE = 7 * 24 * 3600  # ブラウザにもキャッシュさせる
KEYFRAME_EVERY = 25  # 時系列は、この件数ごとに丸ごとの view、間は前の view からの差分
MAX_BODY = 256 * 1024  # 書き込みの要求の大きさの上限
MAX_NAMES = 200  # /api/names で1回に聞ける名前の数
JA_BUDGET = 3.0  # /api/names で、まだ引いていない日本語版を Scryfall から引く時間の上限（秒）


def _announce(built) -> None:
    """プロンプトを作ったら、サーバーのコンソールに出す（手動でモデルに渡す人が見る）。"""
    if built:
        who = "審判" if built["role"] == "judge" else built["role"]
        print("[%s] prompt for %s: %s" % (built["dir"].parent.parent.name, who, built["latest"] or built["user"]), flush=True)


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


VIEW_FORMAT = 2  # 見せる形（player_view）を変えたら上げる。保存した時系列を作り直させる


def _saved_timeline(st: BaseStore, seat: Optional[str]):
    """終わった対局の時系列を置くファイル（終わっていなければ None）。変化の印（stamp）を名前に入れるので、
    巻き戻しなどで変われば別のファイルになる。"""
    try:
        if st.summary()["ended"] is None:
            return None
    except (OSError, LookupError, KeyError):
        return None
    tag = hashlib.sha1(repr((st.stamp(), VIEW_FORMAT)).encode()).hexdigest()[:12]
    return st.root / "cache" / ("timeline-%s-%s.json" % (seat or "judge", tag))


class Viewer:
    """対局の読み取りと、途中の時点の状態のキャッシュ。対局・デッキ・招待・履歴は db（SQLite）に置く。"""

    def __init__(self, db, offline: bool = False):
        self.source = SqliteGames(db)
        self.offline = offline
        self.decks = Decks(db)
        self.lobby = Lobby(db, self.source, self.decks, offline)
        self.history = History(db, self.source)
        self._no_image: set = set()  # 画像が無かった・取れなかったカード（何度も取りに行かない）
        self._ja_lock = threading.Lock()  # 日本語版を Scryfall から引くのは1度に1つ（同時に聞かれても重ねて引かない）
        self._tutorial_lock = threading.Lock()  # チュートリアルを作るのは1度に1つ（作っている間に押されたら、それを待つ）
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

    def games(self, host: bool = True) -> list:
        """対局の一覧。ホストにだけ全部を見せ（ゲストには空）、my_seat にホストの席（鍵が無くても対局できる）。"""
        if not host:
            return []
        out = []
        for gid in self.source.ids():
            st = self.source.open(gid)
            g = st.summary()
            row = {"id": gid, "version": g["version"], "turn": g["turn"], "players": g["players"],
                   "seats": sorted(play.seats(st))}
            mine = host_seats(st)
            row["my_seat"] = mine[0] if mine else None
            out.append(row)
        return out

    def authorize(self, game: str, seat: Optional[str], token: Optional[str], host: bool = False) -> BaseStore:
        """seat として読む・書くことを許すか。ホストの席（鍵の無い人間の席）はホストなら鍵なしで、ほかの席は鍵で。
        judge の席はここでは見せない（終わった対局の履歴は read が通す）。"""
        st = self.store(game)
        if seat is None:
            raise Forbidden("the judge seat is shown only for finished games")
        if host and seat in host_seats(st):
            return st
        if play.check_token(st, seat, token):
            return st
        raise Forbidden("this seat needs its key (the invite URL)")

    def read(self, game: str, seat: Optional[str], token: Optional[str], host: bool = False) -> BaseStore:
        """seat（None は全体の視点）として読むことを許すか。ホストは見える範囲（history.access: 自分の席・
        終わった対局のどの視点も）に入っていれば通す。入っていなければ席の鍵。"""
        if host and self.history.can_view(game, seat or history_mod.JUDGE):
            return self.store(game)
        return self.authorize(game, seat, token, host=host)

    def seat_write(self, game: str, seat: str, token: Optional[str], what: str, body: dict,
                   host: bool = False) -> dict:
        """席の Player の書き込み（request / declare / answer）。卓の op は受けない（play.seat_batch が宣言に変える）。"""
        st = self.authorize(game, seat, token, host=host)
        guided = tutorial.active(st)
        with st.lock():
            cur = st._check(body.get("expect"))
            try:
                if guided:  # チュートリアル: 台本の今の段と合う操作だけ
                    tutorial.check(st, seat, what, body)
                batch = play.seat_batch(st.load(), seat, what, body)
            except play.Refused as e:
                raise Forbidden(str(e))
            result = st._apply(batch, cur)
        result.pop("_state", None)
        if guided:
            if not (what == "declare" and body.get("kind") == "say"):
                tutorial.accepted(st)
            tutorial.run(st)  # 審判と相手の段を、次のホストの段まで台本で書く
        else:
            _announce(prompt.auto_build(st))  # 依頼・マリガンで審判の番になったら、審判のプロンプトをすぐ作る
        self.notify()
        return {"result": public_result(result), "cursor": st.cursor()}

    def get_stops(self, game: str, seat: str, token: Optional[str], host: bool = False) -> dict:
        st = self.authorize(game, seat, token, host=host)
        return {"stops": play.get_stops(st, seat)}

    def set_stops(self, game: str, seat: str, token: Optional[str], body: dict, host: bool = False) -> dict:
        """止める場所（非公開。卓の記録には載せず、本人と審判のプロンプトにだけ出る）。"""
        st = self.authorize(game, seat, token, host=host)
        if tutorial.active(st):  # チュートリアルの台本は決まった止める場所で進む
            raise Forbidden("tutorial: the stops are fixed in the tutorial")
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
        saved = _saved_timeline(st, seat)  # 終わった対局は、一度作った時系列をファイルに残して使い回す
        if saved is not None and saved.exists():
            try:
                out = json.loads(saved.read_text(encoding="utf-8"))
                with self._lock:
                    self._timelines[key] = out
                return out
            except (OSError, ValueError):
                pass
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
        if saved is not None:
            try:
                saved.parent.mkdir(parents=True, exist_ok=True)
                tmp = saved.with_suffix(".tmp")
                tmp.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                tmp.replace(saved)
            except OSError:
                pass
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


    def image(self, name: str, face: int, lang: str = "en"):
        """カードの画像。lang="ja" なら日本語版の画像（無ければ英語の画像）。"""
        if lang == "ja" and (name, face, "ja") not in self._no_image:
            try:
                p = carddb.image_ja(name, face, offline=self.offline)
            except (LookupError, OSError):
                p = None
            if p is not None:
                return p
            if not self.offline:
                self._no_image.add((name, face, "ja"))
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

    def ja_names(self, names: list, budget: float = JA_BUDGET) -> dict:
        """英語名 → 日本語名（日本語版の無いカードは出さない）。まだ引いていないカードは budget 秒まで Scryfall から引き、
        間に合わなかった・断られた（429 など）名前は pending（画面が少し後に聞き直す）。Scryfall を待って止まらないよう、
        引くのは1度に1つの要求だけで、やり直しはしない。英語のキャッシュに無い名前は引かない（carddb.lookup_ja）。"""
        out, pending = {}, []
        until = time.time() + budget
        for name in dict.fromkeys(n for n in names if isinstance(n, str) and n):
            ja = carddb.ja_name(name, offline=True)
            if ja or self.offline or not carddb.lookup(name, offline=True) or carddb.lookup_ja(name, offline=True):
                if ja:
                    out[name] = ja
                continue  # 分かった・日本語版が無い・引けないカード
            if time.time() > until or not self._ja_lock.acquire(timeout=max(0.0, until - time.time())):
                pending.append(name)
                continue
            try:
                ja = carddb.ja_name(name, offline=False, retries=0)
            except (LookupError, OSError):
                pending.append(name)  # 断られた・通信の失敗（キャッシュしない。後で聞き直される）
                continue
            finally:
                self._ja_lock.release()
            if ja:
                out[name] = ja
        return {"names": out, "pending": pending}

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


def oracle_json(name: str, lang: str = "en", offline: bool = True) -> dict:
    """カードの詳細。lang="ja" で日本語版があれば text は日本語版（印刷された文）、oracle は英語のオラクル。"""
    text = oracle_text(name)
    if lang == "ja":
        try:
            ja = carddb.format_card_ja(name, offline=offline)
        except (LookupError, OSError):
            ja = None
        if ja:
            return {"name": name, "text": ja, "lang": "ja", "oracle": text}
    return {"name": name, "text": text, "lang": "en"}


# どの画面でも付けるセキュリティのヘッダー。スクリプト・スタイル・画像・通信はこのサーバーからだけ（画像は data: も）
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                               "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                               "form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")
PROXY_HEADERS = ("X-Forwarded-For", "Forwarded", "X-Real-IP")  # トンネル・プロキシ越し（手元からに見えても外から）


class Handler(BaseHTTPRequestHandler):
    viewer: Viewer = None  # serve() が設定する
    timeout = 60  # 読み書きが止まった接続を切る（秒）

    def log_message(self, fmt, *args):  # 決まりのログ（要求の行。クエリに鍵が入りうる）は出さない
        pass

    def end_headers(self):
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        super().end_headers()

    def _local(self) -> bool:
        return (urlparse("//" + (self.headers.get("Host") or "")).hostname or "") in LOCAL_HOSTS

    def _host(self) -> bool:
        """サイトのホスト（手元の1人）からの要求か: 手元のアドレスから、手元の名前（Host）で、プロキシを通らずに来たもの。
        --host で外に開いたときの招待のゲスト・トンネル越しの要求はホストにならない（席の鍵だけで入る）。"""
        addr = self.client_address[0] if self.client_address else ""
        return addr in LOOPBACK and self._local() and not any(self.headers.get(h) for h in PROXY_HEADERS)

    def _need_host(self) -> None:
        if not self._host():
            raise Forbidden("only the host (the browser on this machine) can do this")

    def _crash(self) -> None:
        """思いがけない失敗: 理由をサーバーのログに残し、500 を返す（中身は見せない）。"""
        print("[error] %s %s\n%s" % (self.command, urlparse(self.path).path, traceback.format_exc()), flush=True)
        try:
            self._json({"error": "internal error"}, 500)
        except OSError:
            pass

    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command != "HEAD":  # HEAD（監視など）はヘッダーだけ
            self.wfile.write(body)

    def _same_origin(self) -> bool:
        """書き込みは、このサーバーのページからだけ（ホストは手元からというだけで通るので、他のサイトからの送信を断る）。"""
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
            if not self._same_origin():
                raise Forbidden("writes must come from this site (Origin)")
            host = self._host()
            if parts[:2] == ["api", "decks"]:
                return self._decks(method, parts[2:], body)
            if method == "DELETE" and parts[:2] == ["api", "games"] and len(parts) == 3:
                self._need_host()
                out = self.viewer.history.delete(parts[2])
                self.viewer.notify()
                return self._json(out)
            if parts == ["api", "tutorial"] and method == "POST":  # チュートリアルを（作り直して）始める
                self._need_host()
                lock = self.viewer._tutorial_lock
                if not lock.acquire(blocking=False):  # 作っている途中（初回はカードの情報を引くので時間がかかる）: 作り直さず、それを待つ
                    with lock:
                        return self._json({"game": tutorial.GAME, "seat": "p1"}, 201)
                try:
                    out = {"game": tutorial.create(self.viewer.source, self.viewer.offline), "seat": "p1"}
                finally:
                    lock.release()
                self.viewer.notify()
                return self._json(out, 201)
            if parts == ["api", "tutorial", "finish"] and method == "POST":  # 台本の後: このまま続ける・終える
                self._need_host()
                st = self.viewer.source.open(tutorial.GAME)
                try:
                    tutorial.finish(st, bool(body.get("continue")))
                except play.Refused as e:
                    raise Forbidden(str(e))
                if body.get("continue"):
                    _announce(prompt.auto_build(st))  # ここからは Claude Code が審判と相手を回す
                self.viewer.notify()
                return self._json({"ok": True})
            if parts == ["api", "tutorial", "next"] and method == "POST":  # チュートリアルの説明の段を読んで「次へ」
                self._need_host()
                try:
                    tutorial.next_step(self.viewer.source.open(tutorial.GAME))
                except play.Refused as e:
                    raise Forbidden(str(e))
                self.viewer.notify()
                return self._json({"ok": True})
            if (parts[:2] == ["api", "invites"] or parts == ["api", "games"]
                    or parts[:2] == ["api", "games"] and parts[3:] == ["timeout"]):
                return self._lobby(method, parts, body, host)
            if method != "POST":
                return self._json({"error": "not found"}, 404)
            return self._post(parts, body, host)
        except (Forbidden, LobbyRefused, HistoryRefused) as e:
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
        except Exception:  # noqa: BLE001
            self._crash()

    def do_POST(self):  # noqa: N802
        self._write("POST")

    def do_PUT(self):  # noqa: N802
        self._write("PUT")

    def do_DELETE(self):  # noqa: N802
        self._write("DELETE")

    def _decks(self, method: str, rest: list, body: dict) -> None:
        """デッキ（サイト。ホストだけ）: POST /api/decks（登録）・POST /api/decks/check（検査だけ）・
        PUT / DELETE /api/decks/<id>。読むのは GET（do_GET）。"""
        self._need_host()
        decks = self.viewer.decks
        if method == "POST" and rest == ["check"]:
            return self._json(check_deck_text(body.get("text"), body.get("format") or "standard", decks.resolve))
        if method == "POST" and not rest:
            return self._json(decks.create(body), 201)
        if method == "PUT" and len(rest) == 1:
            return self._json(decks.update(rest[0], body))
        if method == "DELETE" and len(rest) == 1:
            decks.delete(rest[0])
            return self._json({"ok": True})
        self._json({"error": "not found"}, 404)

    def _lobby(self, method: str, parts: list, body: dict, host: bool) -> None:
        """対局を作る（サイト）: POST /api/games（AI と・招待。ホスト）・POST /api/games/<対局>/timeout（時間切れ。
        その席を持つ人）・POST /api/invites/<id>/join（招待の鍵を持つゲスト）・renew・DELETE /api/invites/<id>（ホスト）。
        招待の中身は GET（do_GET）。"""
        lobby = self.viewer.lobby
        if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "invites"] and parts[3] == "join":
            out = lobby.join(parts[2], body)
            self.viewer.notify()
            return self._json(out, 201)
        if method == "POST" and parts[3:] == ["timeout"]:
            self.viewer.authorize(parts[2], body.get("seat"), self._token(), host=host)
            out = lobby.timeout(parts[2], body.get("seat"))
            self.viewer.notify()
            return self._json(out)
        self._need_host()
        if method == "POST" and parts == ["api", "games"]:
            out = lobby.create(body)
            self.viewer.notify()
            return self._json(out, 201)
        if method == "POST" and len(parts) == 4 and parts[3] == "renew":
            return self._json(lobby.renew(parts[2]))
        if method == "DELETE" and len(parts) == 3:
            lobby.cancel(parts[2])
            return self._json({"ok": True})
        self._json({"error": "not found"}, 404)

    def _post(self, parts: list, body: dict, host: bool) -> None:
        """POST /api/games/<対局>/request・declare・answer・stops。"""
        if parts[:2] == ["api", "games"] and len(parts) == 4 and parts[3] in play.SEAT_WRITES:
            return self._json(self.viewer.seat_write(parts[2], body.get("seat"), self._token(), parts[3], body,
                                                     host=host))
        if parts[:2] == ["api", "games"] and len(parts) == 4 and parts[3] == "stops":
            return self._json(self.viewer.set_stops(parts[2], body.get("seat"), self._token(), body, host=host))
        self._json({"error": "not found"}, 404)

    def do_HEAD(self):  # noqa: N802
        """GET と同じ答えのヘッダーだけ（監視・リンクの確かめ）。更新の通知（SSE）は流し続けるので受けない。"""
        if urlparse(self.path).path.endswith("/events"):
            return self._json({"error": "use GET for events"}, 405)
        self.do_GET()

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        try:
            if not parts:
                return self._static("index.html")
            if parts[0] == "static" and len(parts) == 2:
                return self._static(parts[1])
            host = self._host()
            if parts[:2] == ["api", "games"] and len(parts) == 2:
                return self._json(self.viewer.games(host))
            if parts[:2] == ["api", "decks"] and len(parts) <= 3:
                self._need_host()
                return self._json(self.viewer.decks.get(parts[2]) if len(parts) == 3 else self.viewer.decks.list())
            if parts == ["api", "history"]:
                self._need_host()
                return self._json(self.viewer.history.mine())
            if parts[:2] == ["api", "games"] and len(parts) == 4 and parts[3] == "access":
                self._need_host()
                a = self.viewer.history.access(parts[2])
                if not a["views"]:
                    raise Forbidden("この対局は見られません（対局中は自分の席だけ。終わった対局はどの視点も）")
                return self._json(a)
            if parts == ["api", "ai-decks"]:
                self._need_host()
                return self._json(repo_decks())
            if parts[:2] == ["api", "invites"] and len(parts) == 3:
                return self._json(self.viewer.lobby.invite_info(parts[2], q.get("token")))  # 招待の鍵で（ゲスト）
            if parts == ["api", "invites"]:
                self._need_host()
                return self._json(self.viewer.lobby.invites())
            if parts[:2] == ["api", "config"]:
                return self._json({"host": host})
            if parts[:2] == ["api", "image"]:
                p = self.viewer.image(q.get("name", ""), int(q.get("face", 0)), q.get("lang", "en"))
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
                return self._json(oracle_json(q.get("name", ""), q.get("lang", "en"), offline=self.viewer.offline))
            if parts[:2] == ["api", "names"]:  # カード名の日本語（?lang=ja&n=<英語名>&n=…。画面が知っているカードだけ）
                names = parse_qs(url.query).get("n", [])[:MAX_NAMES]
                return self._json(self.viewer.ja_names(names) if q.get("lang") == "ja" else {"names": {}, "pending": []})
            if parts[:2] == ["api", "games"] and len(parts) == 4:
                game, what = parts[2], parts[3]
                seat = None if q.get("seat", "judge") == "judge" else q["seat"]
                if what != "events":  # 通知は version・cursor だけ（ゲストは鍵を付けずにつなぐ）
                    self.viewer.read(game, seat, self._token(), host=host)
                if what == "stops" and seat is not None:
                    return self._json(self.viewer.get_stops(game, seat, self._token(), host=host))
                if what == "log" and seat is not None:
                    return self._json(play.player_log(self.viewer.store(game), seat))
                if what == "view":
                    at = int(q["at"]) if q.get("at") not in (None, "") else None
                    return self._json(self.viewer.view(game, seat, at, library=q.get("library") == "1"))
                if what == "timeline":
                    tl = self.viewer.timeline(game, seat)
                    st = self.viewer.store(game)  # 相手の番がどれだけ続いているか（時間切れの知らせ。取るたびに変わるので、写しに足す）
                    tl = dict(tl, idle={"seconds": self.viewer.lobby.idle_seconds(st),
                                        "notice": IDLE_NOTICE, "limit": IDLE_LIMIT,
                                        "humans": sorted(play.seats(st))})
                    if tutorial.active(st):  # チュートリアルの今の段の案内（取るたびに変わるので、写しに足す）
                        tl = dict(tl, tutorial=tutorial.status(st))
                    return self._json(tl)
                if what == "log":
                    if seat is not None:  # ラベルに非公開の情報が入りうるので judge の席だけ
                        return self._json({"error": "log is shown only to the judge seat"}, 403)
                    return self._json(self.viewer.log(game))
                if what == "events":
                    return self._events(game)
            self._json({"error": "not found"}, 404)
        except (Forbidden, LobbyRefused, HistoryRefused) as e:
            self._json({"error": str(e)}, 403)
        except (LookupError, ValueError) as e:
            self._json({"error": str(e)}, 404)
        except Exception:  # noqa: BLE001
            self._crash()

    def _static(self, name: str) -> None:
        path = STATIC / name
        if path.parent != STATIC or not path.is_file():
            return self._json({"error": "not found"}, 404)
        self._send(200, path.read_bytes(), _TYPES.get(path.suffix, "application/octet-stream"))

    def _events(self, game: str) -> None:
        """SSE: 対局が変わったら {version, cursor} を送る。サーバー自身の書き込みはすぐ、別のプロセス（CLI）の
        書き込みは 0.5 秒ごとの見張りで拾う。GUI の自動更新に使う。"""
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


def serve(db, host: str = "127.0.0.1", port: int = 8765, offline: bool = False) -> None:
    Handler.viewer = Viewer(db, offline)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print("mtgtable: http://%s:%d/  (db: %s, Ctrl+C で終了)" % (host, port, db))
    print("  審判と AI の席は Claude Code が回す（%s --db %s next --claude-code の監視と、"
          "エージェント mtg-table。mtgtable プラグインの mtg-playtest スキル）" % (paths.command(), paths.shell_path(db)))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
