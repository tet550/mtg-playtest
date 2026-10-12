"""対局を作る（手元のサイト）: AI との対局・招待（ゲストはデッキを貼るか同梱のデッキで着く・1回だけ・期限・作り直し・
取り消し・同時に着く）・時間切れ。"""
import pathlib
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import lobby, play, prompt  # noqa: E402
from mtgtable.sqlstore import SqliteGameStore, connect  # noqa: E402
from test_decks import deck, fake_resolve  # noqa: E402
from test_site import Browser, start_site  # noqa: E402

GUEST_DECK = deck("56 Forest", "4 Grizzly Bears")


class LobbyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = pathlib.Path(self.tmp.name) / "mtg.sqlite"
        self.httpd, viewer, self.base = start_site(self.tmp.name, self.db)
        viewer.decks.resolve = fake_resolve
        self.limit = lobby.IDLE_LIMIT
        self.host = Browser(self.base)
        self.deck = self.add_deck("green", strategy="熊で殴る")

    def tearDown(self):
        lobby.IDLE_LIMIT = self.limit
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def add_deck(self, name, strategy=""):
        code, d = self.host.call("/api/decks", {"name": name, "format": "standard", "strategy": strategy,
                                                "text": deck("56 Forest", "4 Grizzly Bears")})
        self.assertEqual(code, 201, d)
        return d["id"]

    def store(self, game):
        return SqliteGameStore(self.db, game)

    def test_a_game_against_the_ai(self):
        a = self.host
        self.assertIn("piza", a.call("/api/ai-decks")[1])
        code, r = a.call("/api/games", {"deck": self.deck, "opponent": "ai", "ai_deck": "repo:piza"})
        self.assertEqual((code, r["seat"]), (201, "p1"))
        games = a.call("/api/games")[1]
        self.assertEqual([(g["id"], g["my_seat"]) for g in games], [(r["game"], "p1")])
        st = self.store(r["game"])
        self.assertEqual(prompt.ai_seats(st), ["p2"])  # 相手は AI の席
        self.assertEqual(lobby.host_seats(st), ["p1"])
        meta = st.initial().meta["decks"]
        self.assertEqual((meta["p1"]["name"], meta["p1"]["strategy"], meta["p2"]["name"], meta["p2"]["repo"]),
                         ("green", "熊で殴る", "piza", "piza"))
        self.assertNotIn("strategy", meta["p2"])  # 同梱のデッキはファイルの方針
        self.assertEqual(len(st.load().zones["p1.hand"].cards), 7)
        self.assertEqual(play.waiting_on(st.load()), st.load().turn.active)  # 先攻のキープ待ち
        # デッキを消しても、対局は作った時点の写しのまま
        a.call("/api/decks/" + self.deck, {}, method="DELETE")
        self.assertEqual(self.store(r["game"]).initial().meta["decks"]["p1"]["list"], deck("56 Forest", "4 Grizzly Bears"))
        self.assertEqual(a.call("/api/games/%s/timeline?seat=p1" % r["game"])[0], 200)
        # 登録した別のデッキを AI に持たせる
        code, r2 = a.call("/api/games", {"deck": self.add_deck("blue"), "opponent": "ai",
                                         "ai_deck": "deck:" + self.add_deck("red")})
        self.assertEqual(self.store(r2["game"]).initial().meta["decks"]["p2"]["name"], "red")
        self.assertEqual(a.call("/api/games", {"deck": "nope", "opponent": "ai", "ai_deck": "repo:piza"})[0], 404)
        self.assertEqual(a.call("/api/games", {"deck": self.add_deck("x1"), "opponent": "ai",
                                               "ai_deck": "repo:../secrets"})[0], 404)

    def invite(self):
        code, r = self.host.call("/api/games", {"deck": self.deck, "opponent": "human"})
        self.assertEqual(code, 201, r)
        return r

    def join(self, guest, inv, **deck_body):
        body = {"token": inv["token"], **(deck_body or {"text": GUEST_DECK, "name": "bears"})}
        return guest.call("/api/invites/%s/join" % inv["invite"], body)

    def test_invite_join_once(self):
        a, b, c = self.host, Browser(self.base, guest=True), Browser(self.base, guest=True)
        inv = self.invite()
        self.assertEqual(a.call("/api/games"), (200, []))  # 相手が着くまで対局は無い
        self.assertEqual([(i["id"], i["game"]) for i in a.call("/api/invites")[1]], [(inv["invite"], None)])
        code, info = b.call("/api/invites/%s?token=%s" % (inv["invite"], inv["token"]))
        self.assertEqual((code, info["deck"]), (200, "green"))
        self.assertIn("piza", info["repo_decks"])  # ゲストが選べる同梱のデッキ
        self.assertNotIn("text", info)  # 相手のデッキリストは見せない
        self.assertEqual(b.call("/api/invites/%s?token=wrong" % inv["invite"])[0], 403)
        code, r = self.join(b, inv, text="3 Forest")  # 検査に通らないデッキ
        self.assertEqual(code, 400)
        self.assertTrue(r["check"]["errors"])
        code, r = self.join(b, inv)
        self.assertEqual((code, r["seat"]), (201, "p2"))
        self.assertTrue(r["token"])
        self.assertEqual([(g["id"], g["my_seat"]) for g in a.call("/api/games")[1]], [(r["game"], "p1")])
        self.assertEqual(b.call("/api/games"), (200, []))  # ゲストに一覧は見せない
        st = self.store(r["game"])
        self.assertEqual(prompt.ai_seats(st), [])  # 両方とも人間の席
        self.assertEqual(lobby.host_seats(st), ["p1"])  # p2 は鍵で入るゲストの席
        self.assertEqual([st.initial().meta["decks"][p]["name"] for p in ("p1", "p2")], ["green", "bears"])
        self.assertEqual(a.call("/api/invites")[1][0]["game"], r["game"])
        # ゲストは受け取った鍵で自分の席だけ
        self.assertEqual(b.call("/api/games/%s/timeline?seat=p2" % r["game"], token=r["token"])[0], 200)
        self.assertEqual(b.call("/api/games/%s/timeline?seat=p1" % r["game"], token=r["token"])[0], 403)
        self.assertEqual(a.call("/api/games/%s/timeline?seat=p2" % r["game"])[0], 403)  # ホストも相手の席は見ない
        # 1回だけ: 使われた招待は誰も使えない
        self.assertEqual(self.join(c, inv)[0], 403)
        self.assertEqual(c.call("/api/games/%s/timeline?seat=p2" % r["game"])[0], 403)
        self.assertEqual(a.call("/api/invites/%s" % inv["invite"], {}, method="DELETE")[0], 403)  # 取り消せない

    def test_the_guest_can_pick_a_bundled_deck(self):
        inv = self.invite()
        g = Browser(self.base, guest=True)
        self.assertEqual(self.join(g, inv, repo="../secrets")[0], 404)
        code, r = self.join(g, inv, repo="piza")
        self.assertEqual(code, 201, r)
        self.assertEqual(self.store(r["game"]).initial().meta["decks"]["p2"]["repo"], "piza")

    def test_two_guests_at_once_only_one_sits(self):
        guests = [Browser(self.base, guest=True) for _ in range(4)]
        inv = self.invite()
        codes = []
        barrier = threading.Barrier(len(guests))

        def join(g):
            barrier.wait()
            codes.append(self.join(g, inv)[0])
        ts = [threading.Thread(target=join, args=(g,)) for g in guests]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sorted(codes), [201, 403, 403, 403])
        self.assertEqual(len(self.host.call("/api/games")[1]), 1)

    def test_renew_cancel_and_expiry(self):
        a, b = self.host, Browser(self.base, guest=True)
        inv = self.invite()
        code, new = a.call("/api/invites/%s/renew" % inv["invite"], {})
        self.assertEqual(code, 200)
        self.assertEqual(b.call("/api/invites/%s?token=%s" % (inv["invite"], inv["token"]))[0], 403)  # 前の URL は無効
        self.assertEqual(b.call("/api/invites/%s?token=%s" % (inv["invite"], new["token"]))[0], 200)
        self.assertEqual(b.call("/api/invites/%s/renew" % inv["invite"], {})[0], 403)  # ゲストは作り直せない
        conn = connect(self.db)
        conn.execute("UPDATE invites SET expires_at = '2000-01-01T00:00:00'")
        conn.close()
        self.assertIn("expired", b.call("/api/invites/%s?token=%s" % (inv["invite"], new["token"]))[1]["error"])
        self.assertTrue(a.call("/api/invites")[1][0]["expired"])
        self.assertEqual(a.call("/api/invites/%s" % inv["invite"], {}, method="DELETE"), (200, {"ok": True}))
        self.assertEqual(a.call("/api/invites"), (200, []))

    def test_timeout_when_the_other_player_stays_idle(self):
        inv = self.invite()
        guest = Browser(self.base, guest=True)
        r = self.join(guest, inv)[1]
        game, token = r["game"], r["token"]
        st = self.store(game)
        first = st.load().turn.active
        # 先攻のキープ待ち。待っている側（後攻）が申し立てる
        idle_seat, my_seat = first, "p2" if first == "p1" else "p1"
        me = (lambda path, body: guest.call(path, body, token=token)) if my_seat == "p2" else self.host.call
        idle = (lambda path, body: guest.call(path, body, token=token)) if idle_seat == "p2" else self.host.call
        tl = (guest.call("/api/games/%s/timeline?seat=p2" % game, token=token) if my_seat == "p2"
              else self.host.call("/api/games/%s/timeline?seat=p1" % game))[1]
        self.assertEqual((tl["idle"]["limit"], tl["idle"]["humans"]), (lobby.IDLE_LIMIT, ["p1", "p2"]))
        code, out = me("/api/games/%s/timeout" % game, {"seat": my_seat})
        self.assertEqual(code, 403)
        self.assertIn("more minutes", out["error"])  # まだ早い
        lobby.IDLE_LIMIT = 0
        self.assertEqual(idle("/api/games/%s/timeout" % game, {"seat": idle_seat})[0], 403)  # 自分の番
        self.assertEqual(me("/api/games/%s/timeout" % game, {"seat": idle_seat})[0], 403)  # 自分の席ではない
        self.assertEqual(me("/api/games/%s/timeout" % game, {"seat": my_seat}), (200, {"loser": idle_seat}))
        s = st.load()
        self.assertEqual(s.players[idle_seat].status, "conceded")
        self.assertIsNone(play.waiting_on(s))
        self.assertIn("時間切れ", st.read_log()[-1]["act"][0]["text"])
        self.assertEqual(me("/api/games/%s/timeout" % game, {"seat": my_seat})[0], 403)  # 決着した後

    def test_no_timeout_against_the_ai(self):
        game = self.host.call("/api/games", {"deck": self.deck, "opponent": "ai", "ai_deck": "repo:piza"})[1]["game"]
        lobby.IDLE_LIMIT = 0
        st = self.store(game)
        if st.load().turn.active == "p1":
            st.apply({"actor": "p1", "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        self.assertEqual(play.waiting_on(st.load()), "p2")
        self.assertEqual(self.host.call("/api/games/%s/timeout" % game, {"seat": "p1"})[0], 403)  # AI の番


if __name__ == "__main__":
    unittest.main()
