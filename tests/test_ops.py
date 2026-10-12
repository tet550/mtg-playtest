"""サーバーの運用: セキュリティのヘッダー・思いがけない失敗・バックアップ。"""
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

from mtgtable import cli  # noqa: E402
from mtgtable.sqlstore import SqliteGameStore  # noqa: E402
from mtgtable.web import Handler, Viewer  # noqa: E402
from helpers import game  # noqa: E402


class SiteOpsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = pathlib.Path(self.tmp.name) / "mtg.sqlite"
        SqliteGameStore(self.db, "g").create(game().state)
        self.viewer = Viewer(self.tmp.name, offline=True, db=self.db, site=True)
        self.handler = type("SiteHandler", (Handler,), {"viewer": self.viewer})
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
        self.assertEqual(self.raw("/static/favicon.svg")[1]["Content-Type"], "image/svg+xml")

    def test_crashes_are_500(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.viewer.games = lambda host=True: 1 / 0
            code, _, body = self.raw("/api/games")
        self.assertEqual((code, json.loads(body)), (500, {"error": "internal error"}))
        self.assertIn("ZeroDivisionError", out.getvalue())  # 理由はサーバーのログにだけ


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
