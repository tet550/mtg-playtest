"""手元のサイトの不変の仕様: ホストとゲストの区別・席の鍵・席が書けるもの・対局を作る・デッキの検査。"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from helpers import game, site_db
from mtgtable import decks, play, prompt
from mtgtable.sqlstore import SqliteGameStore
from mtgtable.web import Handler, Viewer

LEGAL = {"standard": "legal"}
CARDS = {"Forest": {"name": "Forest", "type_line": "Basic Land — Forest", "legalities": LEGAL},
         "Grizzly Bears": {"name": "Grizzly Bears", "type_line": "Creature — Bear", "legalities": LEGAL},
         "Lightning Bolt": {"name": "Lightning Bolt", "type_line": "Instant", "legalities": {"standard": "not_legal"}}}


def fake_resolve(names, need_legalities=False):
    return {"found": {n: CARDS[n] for n in names if n in CARDS},
            "missing": [n for n in dict.fromkeys(names) if n not in CARDS]}


def deck(*lines):
    return "Deck\n" + "\n".join(lines) + "\n"


class Browser:
    """ブラウザの代わり。guest なら外のアドレスの Host で送る（ホストにならない）。書き込みには同じサイトの Origin を付ける。"""

    def __init__(self, base, guest=False):
        self.base = base
        self.host = ("192.168.0.10:" + base.rsplit(":", 1)[1]) if guest else base.split("//", 1)[1]
        self.guest = guest

    def call(self, path, body=None, token=None, origin="same", headers=None, method=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        if token:
            h["Authorization"] = "Bearer " + token
        if body is not None and origin:
            h["Origin"] = "http://" + self.host if origin == "same" else origin
        if self.guest:
            h["Host"] = self.host
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers=h, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())


class SiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = site_db(self.tmp.name)
        self.st = SqliteGameStore(self.db, "g")
        self.st.create(game().state)
        self.st.write_doc("seats", {"p1": {"claimed": "2026-01-01T00:00:00"}})  # ホストの席
        self.key = play.invite(self.st, "p2")  # ゲストの席
        viewer = Viewer(self.db, offline=True)
        viewer.decks.resolve = fake_resolve
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (Handler,), {"viewer": viewer}))
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.host, self.guest = Browser(self.base), Browser(self.base, guest=True)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def test_the_host_is_only_the_browser_on_this_machine(self):
        self.assertEqual(self.host.call("/api/config"), (200, {"host": True}))
        self.assertFalse(self.guest.call("/api/config")[1]["host"])
        for h in ({"X-Forwarded-For": "203.0.113.5"}, {"Forwarded": "for=203.0.113.5"}, {"Host": "mtg.example.com"}):
            self.assertFalse(self.host.call("/api/config", headers=h)[1]["host"], h)  # トンネル・プロキシ越しはホストにしない

    def test_each_seat_reads_only_with_its_right(self):
        h, g = self.host, self.guest
        self.assertEqual(h.call("/api/games/g/timeline?seat=p1")[0], 200)  # ホストの席は鍵なしで
        self.assertEqual(g.call("/api/games/g/timeline?seat=p2", token=self.key)[0], 200)  # ゲストは自分の席の鍵で
        for who, seat, token in ((h, "p2", None), (h, "judge", None), (g, "p1", self.key), (g, "judge", self.key)):
            self.assertEqual(who.call("/api/games/g/timeline?seat=" + seat, token=token)[0], 403, (who.guest, seat))
        self.assertEqual(g.call("/api/games"), (200, []))  # ゲストに一覧・デッキ・履歴は見せない
        for path in ("/api/decks", "/api/history", "/api/invites"):
            self.assertEqual(g.call(path)[0], 403, path)
        self.st.apply({"actor": "p2", "acts": [{"act": [{"op": "declare", "kind": "concede"}]}]})
        self.assertEqual(h.call("/api/games/g/timeline?seat=judge")[0], 200)  # 終わった対局は全体の視点も再生できる

    def test_seats_write_only_requests_and_declarations(self):
        h = self.host
        keep = {"seat": "p1", "kind": "keep", "expect": 0}
        self.assertEqual(h.call("/api/games/g/declare", keep, origin=None)[0], 403)  # 書き込みは同じサイトのページから
        self.assertEqual(h.call("/api/games/g/declare", keep, origin="https://evil.example")[0], 403)
        self.assertEqual(h.call("/api/games/g/declare", keep)[0], 200)
        self.assertEqual(h.call("/api/games/g/declare", dict(keep, kind="say", text="hi"))[0], 409)  # 見ていた盤面より進んでいる
        self.assertEqual(h.call("/api/games/g/declare", {"seat": "p1", "kind": "ruled", "text": "x"})[0], 400)  # 審判の宣言は書けない
        self.assertEqual(h.call("/api/games/g/apply", {"seat": "p1", "batch": {"acts": [{"act": [{"op": "draw"}]}]}})[0],
                         404)  # 卓の op を書く入口は無い
        self.assertEqual(h.call("/api/games/g/declare", {"seat": "p2", "kind": "keep"})[0], 403)  # 他の席
        self.assertEqual(self.st.cursor(), 1)

    def test_games_are_made_against_the_ai_or_by_a_one_time_invite(self):
        h = self.host
        code, d = h.call("/api/decks", {"name": "green", "format": "standard",
                                        "text": deck("56 Forest", "4 Grizzly Bears")})
        self.assertEqual(code, 201, d)
        code, r = h.call("/api/games", {"deck": "deck:" + d["id"], "opponent": "ai", "ai_deck": "repo:piza"})
        self.assertEqual((code, r["seat"]), (201, "p1"))
        self.assertEqual(prompt.ai_seats(SqliteGameStore(self.db, r["game"])), ["p2"])  # 相手は AI の席（鍵の無い席）
        code, inv = h.call("/api/games", {"deck": "deck:" + d["id"], "opponent": "human"})
        join = lambda: Browser(self.base, guest=True).call(  # noqa: E731
            "/api/invites/%s/join" % inv["invite"], {"token": inv["token"], "text": deck("60 Forest"), "name": "lands"})
        code, j = join()
        self.assertEqual((code, j["seat"]), (201, "p2"))
        self.assertEqual(prompt.ai_seats(SqliteGameStore(self.db, j["game"])), [])  # 両方とも人間の席
        self.assertEqual(join()[0], 403)  # 招待は1回だけ

    def test_deck_check(self):
        ok = decks.check(deck("56 Forest", "4 Grizzly Bears"), "standard", fake_resolve)
        self.assertTrue(ok["ok"], ok["errors"])
        bad = decks.check(deck("50 Forest", "5 Grizzly Bears", "4 Lightning Bolt", "1 Nope"), "standard", fake_resolve)
        self.assertFalse(bad["ok"])
        lines = {e["line"] for e in bad["errors"]}
        self.assertTrue({3, 4, 5} <= lines, bad["errors"])  # 5 枚目・フォーマット外・見つからない名前は、行番号つき


if __name__ == "__main__":
    unittest.main()
