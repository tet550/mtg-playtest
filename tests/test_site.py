"""公開のサーバー（serve --site）: 所有者の鍵の Cookie・席を取る・自分の対局だけ・復元 URL・Origin・鍵の期限と失効。"""
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
from mtgtable.web import OWNER_COOKIE, Handler, Viewer  # noqa: E402
from helpers import game  # noqa: E402


class Browser:
    """Cookie を覚えるだけの小さなクライアント（ブラウザの代わり）。"""

    def __init__(self, base):
        self.base, self.cookie = base, None
        self.host = base.split("//", 1)[1]

    def call(self, path, body=None, token=None, origin="same", host=None, method=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if self.cookie:
            headers["Cookie"] = "%s=%s" % (OWNER_COOKIE, self.cookie)
        if body is not None and origin:
            headers["Origin"] = "http://" + self.host if origin == "same" else origin
        if host:
            headers["Host"] = host
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                self._keep(r.headers.get_all("Set-Cookie"))
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def _keep(self, cookies):
        for c in cookies or []:
            self.last_set_cookie = c
            name, _, rest = c.partition("=")
            if name == OWNER_COOKIE:
                self.cookie = rest.split(";", 1)[0]

    def open_site(self):
        self.call("/api/config")
        return self


class SiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = pathlib.Path(self.tmp.name) / "mtg.sqlite"
        self.st = SqliteGameStore(self.db, "g")
        self.st.create(game().state)
        self.key = play.invite(self.st, "p1")
        SqliteGameStore(self.db, "other").create(game().state)  # 鍵を作っていない対局
        handler = type("SiteHandler", (Handler,), {"viewer": Viewer(self.tmp.name, offline=True, db=self.db, site=True)})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def browser(self):
        return Browser(self.base).open_site()

    def test_site_needs_a_db(self):
        with self.assertRaises(ValueError):
            Viewer(self.tmp.name, site=True)

    def test_owner_key_cookie(self):
        b = Browser(self.base)
        self.assertEqual(b.call("/api/config"), (200, {"play": True, "site": True, "operator": "", "contact": ""}))
        self.assertTrue(b.cookie)
        c = b.last_set_cookie
        self.assertIn("HttpOnly", c)
        self.assertIn("SameSite=Lax", c)
        self.assertNotIn("Secure", c)  # 手元の 127.0.0.1 では HTTP のまま使えるように
        first = b.cookie
        b.call("/api/config")
        self.assertEqual(b.cookie, first)  # 鍵があれば作り直さない
        self.assertNotIn(first, self.db.read_bytes().decode("latin-1"))  # DB にはハッシュだけ
        other = Browser(self.base)
        other.call("/api/config", host="mtg.example.com")
        self.assertIn("Secure", other.last_set_cookie)  # 外向きの名前では HTTPS だけで送る

    def test_only_your_games_and_no_judge_seat(self):
        b = self.browser()
        self.assertEqual(b.call("/api/games"), (200, []))
        self.assertEqual(b.call("/api/games/other/timeline?seat=judge")[0], 403)  # 鍵の無い対局も見せない
        self.assertEqual(b.call("/api/games/other/timeline?seat=p1")[0], 403)
        self.assertEqual(b.call("/api/games/g/timeline?seat=judge", token=self.key)[0], 403)
        self.assertEqual(b.call("/api/games/g/log?seat=judge")[0], 403)
        self.assertEqual(b.call("/api/games/g/events")[0], 403)

    def test_the_first_owner_to_use_the_key_takes_the_seat(self):
        a, b = self.browser(), self.browser()
        self.assertEqual(a.call("/api/games/g/timeline?seat=p1", token=self.key)[0], 200)  # 席を取る
        games = a.call("/api/games")[1]
        self.assertEqual([(g["id"], g["my_seat"]) for g in games], [("g", "p1")])
        # 取った後は Cookie だけで読み書きできる（鍵を覚えていない端末でも）
        self.assertEqual(a.call("/api/games/g/timeline?seat=p1")[0], 200)
        self.assertEqual(a.call("/api/games/g/declare", {"seat": "p1", "kind": "keep", "expect": 0})[0], 200)
        self.assertEqual(a.call("/api/games/g/stops", {"seat": "p1", "stops": ["opp:end"]})[0], 200)
        self.assertEqual(a.call("/api/games/g/stops?seat=p1")[1], {"stops": ["opp:end"]})
        # 同じ鍵を他の人が使っても通らない（招待の URL が漏れても、取った席は守られる）
        self.assertEqual(b.call("/api/games/g/timeline?seat=p1", token=self.key)[0], 403)
        self.assertEqual(b.call("/api/games"), (200, []))
        self.assertEqual(Browser(self.base).call("/api/games/g/timeline?seat=p1", token=self.key)[0], 403)  # Cookie 無しも
        self.assertEqual(b.call("/api/games/g/claim", {"seat": "p1"}, token=self.key)[0], 403)
        # 持っていない席は読めない
        self.assertEqual(a.call("/api/games/g/timeline?seat=p2")[0], 403)

    def test_opening_the_invite_url_claims_the_seat(self):
        a = self.browser()
        self.assertEqual(a.call("/api/games/g/claim", {"seat": "p1"})[0], 403)  # 鍵が要る
        self.assertEqual(a.call("/api/games/g/claim", {"seat": "p1"}, token=self.key), (200, {"game": "g", "seat": "p1"}))
        self.assertEqual([g["my_seat"] for g in a.call("/api/games")[1]], ["p1"])
        self.assertEqual(Browser(self.base).call("/api/games/g/claim", {"seat": "p1"}, token=self.key)[0], 403)  # Cookie が要る

    def test_writes_need_the_same_origin(self):
        a = self.browser()
        a.call("/api/games/g/timeline?seat=p1", token=self.key)
        body = {"seat": "p1", "kind": "keep", "expect": 0}
        self.assertEqual(a.call("/api/games/g/declare", body, origin=None)[0], 403)
        self.assertEqual(a.call("/api/games/g/declare", body, origin="https://evil.example")[0], 403)
        self.assertEqual(self.st.cursor(), 0)
        self.assertEqual(a.call("/api/games/g/declare", body)[0], 200)

    def test_recovery_link_brings_the_owner_to_another_device(self):
        a = self.browser()
        a.call("/api/games/g/timeline?seat=p1", token=self.key)
        code, r = a.call("/api/me/recovery", {})
        self.assertEqual(code, 200)
        phone = self.browser()
        self.assertEqual(phone.call("/api/games"), (200, []))
        self.assertEqual(phone.call("/api/me/recover", {"token": r["token"]}), (200, {"ok": True}))
        self.assertEqual([g["id"] for g in phone.call("/api/games")[1]], ["g"])
        self.assertEqual(phone.call("/api/games/g/timeline?seat=p1")[0], 200)
        self.assertEqual(a.call("/api/games/g/timeline?seat=p1")[0], 200)  # 元の端末もそのまま
        # 作り直すと前の URL は使えない
        a.call("/api/me/recovery", {})
        self.assertEqual(self.browser().call("/api/me/recover", {"token": r["token"]})[0], 403)
        self.assertEqual(Browser(self.base).call("/api/me/recovery", {})[0], 403)  # 所有者の鍵が無い

    def test_seat_keys_expire_and_can_be_revoked(self):
        old = play.invite(self.st, "p2", ttl=-1)  # もう切れている
        self.assertEqual(self.browser().call("/api/games/g/timeline?seat=p2", token=old)[0], 403)
        fresh = play.invite(self.st, "p2", ttl=1)
        self.assertTrue(play.check_token(self.st, "p2", fresh))
        self.assertTrue(play.revoke(self.st, "p2"))
        self.assertFalse(play.check_token(self.st, "p2", fresh))
        self.assertIn("p2", play.seats(self.st))  # 失効しても人間の席のまま（AI に替わらない）
        self.assertFalse(play.revoke(self.st, "p9"))


if __name__ == "__main__":
    unittest.main()
