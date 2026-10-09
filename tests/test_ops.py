"""公開のサーバーの運用: レート制限・セキュリティのヘッダー・アクセス・ログ・思いがけない失敗・/healthz・バックアップ。"""
import contextlib
import io
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import cli, web  # noqa: E402
from mtgtable.ratelimit import RULES, RateLimiter, rules_from_env  # noqa: E402
from mtgtable.sqlstore import SqliteGameStore  # noqa: E402
from mtgtable.web import Handler, Viewer  # noqa: E402
from helpers import game  # noqa: E402
from test_site import Browser  # noqa: E402


class RateLimiterTest(unittest.TestCase):
    def test_window_slides_and_kinds_and_people_are_separate(self):
        now = [0.0]
        rl = RateLimiter({"write": (2, 10)}, clock=lambda: now[0])
        self.assertEqual((rl.hit("write", "a"), rl.hit("write", "a")), (0.0, 0.0))
        self.assertAlmostEqual(rl.hit("write", "a"), 10.0)  # 3回目は待つ
        self.assertEqual(rl.hit("write", "b"), 0.0)  # 他の人は別
        now[0] = 9.5
        self.assertAlmostEqual(rl.hit("write", "a"), 0.5)
        now[0] = 10.1
        self.assertEqual(rl.hit("write", "a"), 0.0)  # 古いものが窓から出た
        now[0] = 1000
        rl.hit("write", "c")
        self.assertNotIn(("write", "a"), rl._hits)  # 使われなくなった鍵は捨てる

    def test_rules_from_the_environment(self):
        saved = dict(os.environ)
        try:
            os.environ["MTGTABLE_RATE_WRITE"] = "5/30"
            self.assertEqual(rules_from_env()["write"], (5, 30.0))
            self.assertEqual(rules_from_env()["read"], RULES["read"])
            os.environ["MTGTABLE_RATE_READ"] = "lots"
            with self.assertRaises(ValueError):
                rules_from_env()
        finally:
            os.environ.clear()
            os.environ.update(saved)


class SiteOpsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = pathlib.Path(self.tmp.name) / "mtg.sqlite"
        SqliteGameStore(self.db, "g").create(game().state)
        self.viewer = Viewer(self.tmp.name, offline=True, db=self.db, site=True)
        self.handler = type("SiteHandler", (Handler,), {"viewer": self.viewer, "limiter": RateLimiter(dict(RULES)),
                                                        "access_log": False})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def raw(self, path, headers=None, data=None):
        req = urllib.request.Request(self.base + path, headers=headers or {}, data=data)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, dict(e.headers), e.read()

    def test_security_headers_on_every_response(self):
        for path in ("/", "/api/config", "/api/nope", "/static/favicon.svg"):
            code, h, _ = self.raw(path)
            self.assertIn("default-src 'self'", h["Content-Security-Policy"], path)
            self.assertEqual((h["X-Content-Type-Options"], h["X-Frame-Options"]), ("nosniff", "DENY"))
            self.assertNotIn("Strict-Transport-Security", h)  # 127.0.0.1 では付けない
        self.assertEqual(self.raw("/static/favicon.svg")[1]["Content-Type"], "image/svg+xml")
        self.assertIn("Strict-Transport-Security", self.raw("/api/config", {"Host": "mtg.example.com"})[1])

    def test_too_many_requests(self):
        self.handler.limiter = RateLimiter({**RULES, "read": (3, 60)})
        codes = [self.raw("/api/config")[0] for _ in range(4)]
        self.assertEqual(codes, [200, 200, 200, 429])
        code, h, body = self.raw("/api/config")
        self.assertEqual((code, json.loads(body)["retry_after"] > 0), (429, True))
        self.assertGreater(int(h["Retry-After"]), 0)

    def test_owner_keys_per_ip_are_capped_and_proxies_are_trusted_only_when_told(self):
        self.handler.limiter = RateLimiter({**RULES, "owner": (2, 3600)})
        got = [bool(Browser(self.base).open_site().cookie) for _ in range(3)]
        self.assertEqual(got, [True, True, False])  # 3人目には鍵を作らない（見るだけ）
        self.handler.trust_proxy = True
        b = Browser(self.base)
        b.call("/api/config", host=None)
        self.assertIsNone(b.cookie)  # 同じ IP（XFF 無し）
        req = urllib.request.Request(self.base + "/api/config", headers={"X-Forwarded-For": "6.6.6.6, 10.0.0.9"})
        with urllib.request.urlopen(req) as r:
            self.assertTrue(r.headers.get("Set-Cookie"))  # プロキシが付けた最後の IP は別の人

    def test_writes_are_limited_per_kind(self):
        self.handler.limiter = RateLimiter({**RULES, "deck_check": (1, 600)})
        b = Browser(self.base).open_site()
        self.assertNotEqual(b.call("/api/decks/check", {"text": "x"})[0], 429)
        self.assertEqual(b.call("/api/decks/check", {"text": "x"})[0], 429)
        self.assertEqual(b.call("/api/me/recovery", {})[0], 200)  # 他の種類はまだ通る

    def test_access_log_hides_query_strings_and_crashes_are_500(self):
        out = io.StringIO()
        self.handler.access_log = True
        with contextlib.redirect_stdout(out):
            self.raw("/api/invites/abc?token=SECRET-TOKEN")
            self.viewer.games = lambda owner=None: 1 / 0
            code, _, body = self.raw("/api/games")
        self.assertEqual((code, json.loads(body)), (500, {"error": "internal error"}))
        log = out.getvalue()
        self.assertIn("GET /api/invites/abc", log)
        self.assertNotIn("SECRET-TOKEN", log)
        self.assertIn("ZeroDivisionError", log)  # 理由はサーバーのログにだけ

    def test_healthz(self):
        req = urllib.request.Request(self.base + "/healthz", method="HEAD")
        with urllib.request.urlopen(req) as r:
            self.assertEqual((r.status, r.read()), (200, b""))  # 監視の HEAD はヘッダーだけ
        code, _, body = self.raw("/healthz")
        h = json.loads(body)
        self.assertEqual((code, h["ok"]), (200, True))
        self.assertGreater(h["db_bytes"], 0)
        saved = web.MIN_FREE_BYTES
        web.MIN_FREE_BYTES = 1 << 62
        try:
            self.assertEqual(self.raw("/healthz")[0], 503)  # 空きが足りない
        finally:
            web.MIN_FREE_BYTES = saved


class BackupTest(unittest.TestCase):
    def test_backup_while_running_and_keep_the_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = pathlib.Path(tmp) / "mtg.sqlite"
            st = SqliteGameStore(db, "g")
            st.create(game().state)
            st.apply({"actor": "p1", "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
            dest = pathlib.Path(tmp) / "backups"
            names = []
            for i in range(3):
                with contextlib.redirect_stdout(io.StringIO()):
                    cli.main(["--db", str(db), "db-backup", str(dest) + "/", "--keep", "2"])
                names = sorted(p.name for p in dest.glob("mtg-*.sqlite"))
                if i < 2:
                    import time
                    time.sleep(1.05)  # 名前は秒まで
            self.assertEqual(len(names), 2)
            copy = SqliteGameStore(dest / names[-1], "g")
            self.assertEqual((copy.cursor(), copy.read_log()), (st.cursor(), st.read_log()))
            conn = sqlite3.connect(dest / names[-1])
            try:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
