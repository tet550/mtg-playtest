"""手元のサイト（serve --site）: ホスト（手元のブラウザ）とゲスト（外から・席の鍵だけ）・Origin・鍵の期限と失効。"""
import json
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import play  # noqa: E402
from mtgtable.sqlstore import SqliteGameStore  # noqa: E402
from mtgtable.web import Handler, Viewer  # noqa: E402
from helpers import game  # noqa: E402

GUEST_HOST = "192.168.0.10"  # 外から（LAN のアドレス）で来た要求に見せる Host


class Browser:
    """小さなクライアント（ブラウザの代わり）。guest なら外のアドレスの Host で送る（ホストにならない）。"""

    def __init__(self, base, guest=False):
        self.base = base
        port = base.rsplit(":", 1)[1]
        self.host = "%s:%s" % (GUEST_HOST, port) if guest else base.split("//", 1)[1]
        self.guest = guest

    def call(self, path, body=None, token=None, origin="same", host=None, method=None, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        if token:
            h["Authorization"] = "Bearer " + token
        if body is not None and origin:
            h["Origin"] = "http://" + self.host if origin == "same" else origin
        if host or self.guest:
            h["Host"] = host or self.host
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())


def start_site(tmp, db, **viewer):
    v = Viewer(tmp, offline=True, db=db, site=True, **viewer)
    handler = type("SiteHandler", (Handler,), {"viewer": v})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, v, "http://127.0.0.1:%d" % httpd.server_address[1]


class SiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = pathlib.Path(self.tmp.name) / "mtg.sqlite"
        self.st = SqliteGameStore(self.db, "g")
        self.st.create(game().state)
        self.st.write_doc("seats", {"p1": {"claimed": "2026-01-01T00:00:00"}})  # ホストの席（サイトで作った対局）
        self.key = play.invite(self.st, "p2")  # ゲストの席
        SqliteGameStore(self.db, "other").create(game().state)  # 席の文書の無い対局（CLI の AI 同士など）
        self.httpd, self.viewer, self.base = start_site(self.tmp.name, self.db)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def test_site_needs_a_db(self):
        with self.assertRaises(ValueError):
            Viewer(self.tmp.name, site=True)

    def test_config_tells_host_from_guest(self):
        self.assertEqual(Browser(self.base).call("/api/config"),
                         (200, {"play": True, "site": True, "host": True, "ai": "claude-code"}))
        self.assertFalse(Browser(self.base, guest=True).call("/api/config")[1]["host"])
        # トンネル・プロキシ越し（手元のアドレスから来ても）はホストにしない
        for h in ({"X-Forwarded-For": "203.0.113.5"}, {"Forwarded": "for=203.0.113.5"}):
            self.assertFalse(Browser(self.base).call("/api/config", headers=h)[1]["host"])
        self.assertFalse(Browser(self.base).call("/api/config", host="mtg.example.com")[1]["host"])
        # Cookie は使わない
        with urllib.request.urlopen(self.base + "/api/config") as r:
            self.assertIsNone(r.headers.get("Set-Cookie"))

    def test_the_host_sees_every_game_and_plays_its_own_seat(self):
        b = Browser(self.base)
        games = {g["id"]: g["my_seat"] for g in b.call("/api/games")[1]}
        self.assertEqual(games, {"g": "p1", "other": None})
        # ホストの席は鍵なしで読み書きできる
        self.assertEqual(b.call("/api/games/g/timeline?seat=p1")[0], 200)
        self.assertEqual(b.call("/api/games/g/stops", {"seat": "p1", "stops": ["opp:end"]})[0], 200)
        self.assertEqual(b.call("/api/games/g/stops?seat=p1")[1], {"stops": ["opp:end"]})
        self.assertEqual(b.call("/api/games/g/declare", {"seat": "p1", "kind": "keep", "expect": 0})[0], 200)
        # 対局中は、相手（ゲスト）の席・全体の視点は見せない
        self.assertEqual(b.call("/api/games/g/timeline?seat=p2")[0], 403)
        self.assertEqual(b.call("/api/games/g/timeline?seat=judge")[0], 403)
        self.assertEqual(b.call("/api/games/g/log")[0], 403)
        self.assertEqual(b.call("/api/games/g/declare", {"seat": "p2", "kind": "keep", "expect": 1})[0], 403)

    def test_the_guest_only_has_its_seat_key(self):
        g = Browser(self.base, guest=True)
        self.assertEqual(g.call("/api/games"), (200, []))  # 一覧は見せない
        self.assertEqual(g.call("/api/games/g/timeline?seat=p1")[0], 403)  # ホストの席は鍵が無いと
        self.assertEqual(g.call("/api/games/g/timeline?seat=p2")[0], 403)
        self.assertEqual(g.call("/api/games/g/timeline?seat=p2", token=self.key)[0], 200)
        self.assertEqual(g.call("/api/games/g/timeline?seat=judge", token=self.key)[0], 403)
        for path in ("/api/decks", "/api/history", "/api/invites", "/api/ai-decks", "/api/games/g/access"):
            self.assertEqual(g.call(path)[0], 403, path)
        self.assertEqual(g.call("/api/decks", {"name": "x", "text": "1 Forest"})[0], 403)
        self.assertEqual(g.call("/api/games", {"deck": "x", "opponent": "ai"})[0], 403)

    def test_writes_need_the_same_origin(self):
        b = Browser(self.base)
        body = {"seat": "p1", "kind": "keep", "expect": 0}
        self.assertEqual(b.call("/api/games/g/declare", body, origin=None)[0], 403)
        self.assertEqual(b.call("/api/games/g/declare", body, origin="https://evil.example")[0], 403)
        self.assertEqual(self.st.cursor(), 0)
        self.assertEqual(b.call("/api/games/g/declare", body)[0], 200)

    def test_seat_keys_expire_and_can_be_revoked(self):
        g = Browser(self.base, guest=True)
        old = play.invite(self.st, "p2", ttl=-1)  # もう切れている
        self.assertEqual(g.call("/api/games/g/timeline?seat=p2", token=old)[0], 403)
        fresh = play.invite(self.st, "p2", ttl=1)
        self.assertTrue(play.check_token(self.st, "p2", fresh))
        self.assertTrue(play.revoke(self.st, "p2"))
        self.assertFalse(play.check_token(self.st, "p2", fresh))
        self.assertIn("p2", play.seats(self.st))  # 失効しても人間の席のまま（AI に替わらない）
        self.assertFalse(play.revoke(self.st, "p9"))


if __name__ == "__main__":
    unittest.main()
