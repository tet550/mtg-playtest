"""対局を作る（公開のサーバー）: AI との対局・招待（1回だけ・期限・作り直し・取り消し・同時に着く）・時間切れ。"""
import pathlib
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import lobby, play, prompt  # noqa: E402
from mtgtable.sqlstore import SqliteGameStore, connect  # noqa: E402
from mtgtable.web import Handler, Viewer  # noqa: E402
from test_decks import deck, fake_resolve  # noqa: E402
from test_site import Browser  # noqa: E402


class LobbyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = pathlib.Path(self.tmp.name) / "mtg.sqlite"
        viewer = Viewer(self.tmp.name, offline=True, db=self.db, site=True)
        viewer.decks.resolve = fake_resolve
        handler = type("SiteHandler", (Handler,), {"viewer": viewer})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.limit = lobby.IDLE_LIMIT

    def tearDown(self):
        lobby.IDLE_LIMIT = self.limit
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def owner(self, name="green", strategy=""):
        b = Browser(self.base).open_site()
        code, d = b.call("/api/decks", {"name": name, "format": "standard", "strategy": strategy,
                                        "text": deck("56 Forest", "4 Grizzly Bears")})
        self.assertEqual(code, 201, d)
        b.deck = d["id"]
        return b

    def store(self, game):
        return SqliteGameStore(self.db, game)

    def test_a_game_against_the_ai(self):
        a = self.owner(strategy="熊で殴る")
        self.assertIn("piza", a.call("/api/ai-decks")[1])
        code, r = a.call("/api/games", {"deck": a.deck, "opponent": "ai", "ai_deck": "repo:piza"})
        self.assertEqual((code, r["seat"]), (201, "p1"))
        games = a.call("/api/games")[1]
        self.assertEqual([(g["id"], g["my_seat"]) for g in games], [(r["game"], "p1")])
        st = self.store(r["game"])
        self.assertEqual(prompt.ai_seats(st), ["p2"])  # 相手は AI の席
        meta = st.initial().meta["decks"]
        self.assertEqual((meta["p1"]["name"], meta["p1"]["strategy"], meta["p2"]["name"], meta["p2"]["repo"]),
                         ("green", "熊で殴る", "piza", "piza"))
        self.assertNotIn("strategy", meta["p2"])  # リポジトリのデッキはファイルの方針
        self.assertEqual(len(st.load().zones["p1.hand"].cards), 7)
        self.assertEqual(play.waiting_on(st.load()), st.load().turn.active)  # 先攻のキープ待ち
        self.assertEqual(st.read_doc("site")["visibility"], "public")
        # デッキを消しても、対局は作った時点の写しのまま
        a.call("/api/decks/" + a.deck, {}, method="DELETE")
        self.assertEqual(self.store(r["game"]).initial().meta["decks"]["p1"]["list"], deck("56 Forest", "4 Grizzly Bears"))
        self.assertEqual(a.call("/api/games/%s/timeline?seat=p1" % r["game"])[0], 200)
        # 自分の別のデッキを AI に持たせる・非公開
        code, r2 = a.call("/api/games", {"deck": self.owner_deck(a, "blue"), "opponent": "ai",
                                         "ai_deck": "deck:" + self.owner_deck(a, "red"), "visibility": "private"})
        self.assertEqual(self.store(r2["game"]).read_doc("site")["visibility"], "private")
        self.assertEqual(a.call("/api/games", {"deck": "nope", "opponent": "ai", "ai_deck": "repo:piza"})[0], 404)
        self.assertEqual(a.call("/api/games", {"deck": r2 and self.owner_deck(a, "x1"), "opponent": "ai",
                                               "ai_deck": "repo:../secrets"})[0], 404)
        self.assertEqual(a.call("/api/games", {"deck": self.owner_deck(a, "x2"), "opponent": "ai",
                                               "ai_deck": "repo:piza", "visibility": "friends"})[0], 400)

    def owner_deck(self, b, name):
        return b.call("/api/decks", {"name": name, "format": "standard",
                                     "text": deck("56 Forest", "4 Grizzly Bears")})[1]["id"]

    def invite(self, host):
        code, r = host.call("/api/games", {"deck": host.deck, "opponent": "human", "visibility": "private"})
        self.assertEqual(code, 201, r)
        return r

    def test_invite_join_once(self):
        a, b, c = self.owner("host"), self.owner("guest"), self.owner("late")
        inv = self.invite(a)
        self.assertEqual(a.call("/api/games"), (200, []))  # 相手が着くまで対局は無い
        self.assertEqual([(i["id"], i["game"]) for i in a.call("/api/invites")[1]], [(inv["invite"], None)])
        code, info = b.call("/api/invites/%s?token=%s" % (inv["invite"], inv["token"]))
        self.assertEqual((code, info["deck"], info["visibility"]), (200, "host", "private"))
        self.assertNotIn("text", info)  # 相手のデッキリストは見せない
        self.assertEqual(b.call("/api/invites/%s?token=wrong" % inv["invite"])[0], 403)
        self.assertEqual(a.call("/api/invites/%s/join" % inv["invite"], {"token": inv["token"], "deck": a.deck})[0], 403)
        code, r = b.call("/api/invites/%s/join" % inv["invite"], {"token": inv["token"], "deck": b.deck})
        self.assertEqual((code, r["seat"]), (201, "p2"))
        self.assertEqual([(g["id"], g["my_seat"]) for g in a.call("/api/games")[1]], [(r["game"], "p1")])
        self.assertEqual([(g["id"], g["my_seat"]) for g in b.call("/api/games")[1]], [(r["game"], "p2")])
        st = self.store(r["game"])
        self.assertEqual(prompt.ai_seats(st), [])  # 両方とも人間の席
        self.assertEqual([st.initial().meta["decks"][p]["name"] for p in ("p1", "p2")], ["host", "guest"])
        self.assertEqual(a.call("/api/invites")[1][0]["game"], r["game"])
        # 1回だけ: 使われた招待は誰も使えない
        self.assertEqual(c.call("/api/invites/%s/join" % inv["invite"], {"token": inv["token"], "deck": c.deck})[0], 403)
        self.assertEqual(c.call("/api/games/%s/timeline?seat=p2" % r["game"])[0], 403)
        self.assertEqual(a.call("/api/invites/%s" % inv["invite"], {}, method="DELETE")[0], 403)  # 取り消せない

    def test_two_guests_at_once_only_one_sits(self):
        a = self.owner("host")
        guests = [self.owner("g%d" % i) for i in range(4)]
        inv = self.invite(a)
        codes = []
        barrier = threading.Barrier(len(guests))

        def join(g):
            barrier.wait()
            codes.append(g.call("/api/invites/%s/join" % inv["invite"], {"token": inv["token"], "deck": g.deck})[0])
        ts = [threading.Thread(target=join, args=(g,)) for g in guests]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sorted(codes), [201, 403, 403, 403])
        self.assertEqual(len(a.call("/api/games")[1]), 1)

    def test_renew_cancel_and_expiry(self):
        a, b = self.owner("host"), self.owner("guest")
        inv = self.invite(a)
        code, new = a.call("/api/invites/%s/renew" % inv["invite"], {})
        self.assertEqual(code, 200)
        self.assertEqual(b.call("/api/invites/%s?token=%s" % (inv["invite"], inv["token"]))[0], 403)  # 前の URL は無効
        self.assertEqual(b.call("/api/invites/%s?token=%s" % (inv["invite"], new["token"]))[0], 200)
        self.assertEqual(b.call("/api/invites/%s/renew" % inv["invite"], {})[0], 404)  # 他の人の招待
        conn = connect(self.db)
        conn.execute("UPDATE invites SET expires_at = '2000-01-01T00:00:00'")
        conn.close()
        self.assertIn("expired", b.call("/api/invites/%s?token=%s" % (inv["invite"], new["token"]))[1]["error"])
        self.assertTrue(a.call("/api/invites")[1][0]["expired"])
        self.assertEqual(a.call("/api/invites/%s" % inv["invite"], {}, method="DELETE"), (200, {"ok": True}))
        self.assertEqual(a.call("/api/invites"), (200, []))

    def test_timeout_when_the_other_player_stays_idle(self):
        a, b = self.owner("host"), self.owner("guest")
        inv = self.invite(a)
        game = b.call("/api/invites/%s/join" % inv["invite"], {"token": inv["token"], "deck": b.deck})[1]["game"]
        st = self.store(game)
        first = st.load().turn.active
        waiting, other = (a, b) if first == "p1" else (b, a)  # 先攻のキープ待ち。待っている側（後攻）が申し立てる
        idle_seat, my_seat = first, "p2" if first == "p1" else "p1"
        tl = other.call("/api/games/%s/timeline?seat=%s" % (game, my_seat))[1]
        self.assertEqual((tl["idle"]["limit"], tl["idle"]["humans"]), (lobby.IDLE_LIMIT, ["p1", "p2"]))
        code, r = other.call("/api/games/%s/timeout" % game, {"seat": my_seat})
        self.assertEqual(code, 403)
        self.assertIn("more minutes", r["error"])  # まだ早い
        lobby.IDLE_LIMIT = 0
        self.assertEqual(waiting.call("/api/games/%s/timeout" % game, {"seat": idle_seat})[0], 403)  # 自分の番
        self.assertEqual(other.call("/api/games/%s/timeout" % game, {"seat": idle_seat})[0], 403)  # 自分の席ではない
        self.assertEqual(other.call("/api/games/%s/timeout" % game, {"seat": my_seat}), (200, {"loser": idle_seat}))
        s = st.load()
        self.assertEqual(s.players[idle_seat].status, "conceded")
        self.assertIsNone(play.waiting_on(s))
        self.assertIn("時間切れ", st.read_log()[-1]["act"][0]["text"])
        self.assertEqual(other.call("/api/games/%s/timeout" % game, {"seat": my_seat})[0], 403)  # 決着した後

    def test_no_timeout_against_the_ai(self):
        a = self.owner()
        game = a.call("/api/games", {"deck": a.deck, "opponent": "ai", "ai_deck": "repo:piza"})[1]["game"]
        lobby.IDLE_LIMIT = 0
        st = self.store(game)
        if st.load().turn.active == "p1":
            st.apply({"actor": "p1", "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        self.assertEqual(play.waiting_on(st.load()), "p2")
        self.assertEqual(a.call("/api/games/%s/timeout" % game, {"seat": "p1"})[0], 403)  # AI の番


if __name__ == "__main__":
    unittest.main()
