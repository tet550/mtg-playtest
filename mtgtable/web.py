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
from .sqlstore import SqliteGames
from .store import BaseStore, FileGames, StaleCursor

STATIC = pathlib.Path(__file__).with_name("web")
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8"}
IMAGE_MAX_AGE = 7 * 24 * 3600  # ブラウザにもキャッシュさせる
KEYFRAME_EVERY = 25  # 時系列は、この件数ごとに丸ごとの view、間は前の view からの差分
MAX_BODY = 256 * 1024  # 書き込みの要求の大きさの上限


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

    def __init__(self, root, offline: bool = False, play: bool = False, db=None):
        self.source = SqliteGames(db) if db else FileGames(root)
        self.offline = offline
        self.play = play  # 席の操作（書き込み）を受けるか
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

    def games(self) -> list:
        out = []
        for gid in self.source.ids():
            st = self.source.open(gid)
            g = st.summary()
            out.append({"id": gid, "version": g["version"], "turn": g["turn"], "players": g["players"],
                        "seats": sorted(play.seats(st)) if self.play else []})
        return out

    def guarded(self, st: BaseStore) -> bool:
        """席の鍵で守る対局か（--play で、invite した対局）。"""
        return self.play and bool(play.seats(st))

    def authorize(self, game: str, seat: Optional[str], token: Optional[str], write: bool = False) -> BaseStore:
        """seat として読む・書くことを許すか。書くには --play と、その席の鍵が要る。"""
        st = self.store(game)
        if write and not self.play:
            raise Forbidden("this server is read-only (start it with serve --play)")
        if (write or self.guarded(st)) and (seat is None or not play.check_token(st, seat, token)):
            raise Forbidden("this seat needs its key (python -m mtgtable invite %s --seat pN)" % game)
        return st

    def seat_write(self, game: str, seat: str, token: Optional[str], what: str, body: dict) -> dict:
        """席の Player の書き込み（request / declare / answer）。卓の op は受けない（play.seat_batch が宣言に変える）。"""
        st = self.authorize(game, seat, token, write=True)
        with st.lock():
            cur = st._check(body.get("expect"))
            try:
                batch = play.seat_batch(st.load(), seat, what, body)
            except play.Refused as e:
                raise Forbidden(str(e))
            result = st._apply(batch, cur)
        result.pop("_state", None)
        _announce(prompt.auto_build(st))  # 依頼・マリガンで審判の番になったら、審判のプロンプトをすぐ作る
        self.notify()
        return {"result": public_result(result), "cursor": st.cursor()}

    def get_stops(self, game: str, seat: str, token: Optional[str]) -> dict:
        st = self.authorize(game, seat, token, write=True)
        return {"stops": play.get_stops(st, seat)}

    def set_stops(self, game: str, seat: str, token: Optional[str], body: dict) -> dict:
        """止める場所（非公開。卓の記録には載せず、本人と審判のプロンプトにだけ出る）。"""
        st = self.authorize(game, seat, token, write=True)
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
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _token(self) -> Optional[str]:
        auth = self.headers.get("Authorization", "")
        return auth[7:].strip() if auth.startswith("Bearer ") else None

    def do_POST(self):  # noqa: N802
        """POST /api/games/<対局>/request・declare・answer・stops（--play のときだけ）。本文は {"seat", "expect", ...}。"""
        parts = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json({"error": "request too large"}, 413)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
            if parts[:2] == ["api", "games"] and len(parts) == 4 and parts[3] in play.SEAT_WRITES:
                return self._json(self.viewer.seat_write(parts[2], body.get("seat"), self._token(), parts[3], body))
            if parts[:2] == ["api", "games"] and len(parts) == 4 and parts[3] == "stops":
                return self._json(self.viewer.set_stops(parts[2], body.get("seat"), self._token(), body))
            self._json({"error": "not found"}, 404)
        except Forbidden as e:
            self._json({"error": str(e)}, 403)
        except StaleCursor as e:
            self._json({"error": str(e), "stale": True}, 409)
        except TimeoutError as e:
            self._json({"error": str(e)}, 503)
        except LookupError as e:
            self._json({"error": str(e)}, 404)
        except (ValueError, OperationError) as e:  # JSONDecodeError は ValueError
            self._json({"error": str(e)}, 400)

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        try:
            if not parts:
                return self._static("index.html")
            if parts[0] == "static" and len(parts) == 2:
                return self._static(parts[1])
            if parts[:2] == ["api", "games"] and len(parts) == 2:
                return self._json(self.viewer.games())
            if parts[:2] == ["api", "config"]:
                return self._json({"play": self.viewer.play})
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
                    self.viewer.authorize(game, seat, self._token())
                if what == "stops" and seat is not None:
                    return self._json(self.viewer.get_stops(game, seat, self._token()))
                if what == "log" and seat is not None and self.viewer.play:
                    return self._json(play.player_log(self.viewer.store(game), seat))
                if what == "view":
                    at = int(q["at"]) if q.get("at") not in (None, "") else None
                    return self._json(self.viewer.view(game, seat, at, library=q.get("library") == "1"))
                if what == "timeline":
                    return self._json(self.viewer.timeline(game, seat))
                if what == "log":
                    if seat is not None:  # ラベルに非公開の情報が入りうるので judge の席だけ
                        return self._json({"error": "log is shown only to the judge seat"}, 403)
                    return self._json(self.viewer.log(game))
                if what == "events":
                    return self._events(game)
            self._json({"error": "not found"}, 404)
        except Forbidden as e:
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


def serve(root="playtest", host: str = "127.0.0.1", port: int = 8765, offline: bool = False,
          play: bool = False, db=None) -> None:
    Handler.viewer = Viewer(root, offline, play, db=db)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print("mtgtable %s: http://%s:%d/  (%s, Ctrl+C で終了)"
          % ("play" if play else "viewer", host, port, "db: %s" % db if db else "root: %s" % root))
    if play:
        print("  席の URL は python -m mtgtable invite <対局> --seat p1 --port %d で作る" % port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
