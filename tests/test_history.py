"""履歴（手元のサイト）: ホストが見られる視点（対局中と終わった後）・一覧・対局を消す・時系列の保存。"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable.sqlstore import SqliteGameStore  # noqa: E402
from test_decks import deck, fake_resolve  # noqa: E402
from test_site import Browser, start_site  # noqa: E402


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = pathlib.Path(self.tmp.name) / "mtg.sqlite"
        self.httpd, viewer, self.base = start_site(self.tmp.name, self.db)
        viewer.decks.resolve = fake_resolve
        self.host, self.guest = Browser(self.base), Browser(self.base, guest=True)
        self.deck = self.host.call("/api/decks", {"name": "alpha", "format": "standard",
                                                  "text": deck("56 Forest", "4 Grizzly Bears")})[1]["id"]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def pvp(self):
        inv = self.host.call("/api/games", {"deck": self.deck, "opponent": "human"})[1]
        r = self.guest.call("/api/invites/%s/join" % inv["invite"], {"token": inv["token"], "repo": "piza"})[1]
        return r["game"], r["token"]

    def ai_game(self):
        return self.host.call("/api/games", {"deck": self.deck, "opponent": "ai", "ai_deck": "repo:piza"})[1]["game"]

    def finish(self, game, loser="p2"):
        SqliteGameStore(self.db, game).apply({"actor": loser, "acts": [{"act": [{"op": "declare", "kind": "concede"}]}]})

    def views(self, who, game):
        code, r = who.call("/api/games/%s/access" % game)
        return r["views"] if code == 200 else code

    def tl(self, who, game, seat="judge", token=None):
        return who.call("/api/games/%s/timeline?seat=%s" % (game, seat), token=token)[0]

    def test_what_the_host_sees_before_and_after_the_end(self):
        g, token = self.pvp()
        self.assertEqual((self.views(self.host, g), self.views(self.guest, g)), (["p1"], 403))
        self.assertEqual((self.tl(self.host, g), self.tl(self.host, g, "p2")), (403, 403))
        self.finish(g)
        self.assertEqual(self.views(self.host, g), ["judge", "p1", "p2"])  # 終わった対局はどの視点も
        self.assertEqual((self.tl(self.host, g), self.tl(self.host, g, "p2")), (200, 200))
        self.assertEqual(self.host.call("/api/games/%s/log" % g)[0], 200)
        self.assertEqual(self.tl(self.guest, g, token=token), 403)  # ゲストは全体の視点を見ない
        self.assertEqual(self.tl(self.guest, g, "p2", token=token), 200)
        self.assertEqual(self.host.call("/api/games/%s/declare" % g, {"seat": "p1", "kind": "say", "text": "x"})[0], 403)
        mine = {r["id"]: r for r in self.host.call("/api/history")[1]}
        self.assertEqual((mine[g]["result"], mine[g]["opponent"], mine[g]["seat"]), ("won", "human", "p1"))
        a = self.ai_game()
        mine = {r["id"]: r for r in self.host.call("/api/history")[1]}
        self.assertEqual((mine[a]["result"], mine[a]["opponent"]), ("ongoing", "ai"))
        self.assertEqual(self.views(self.host, a), ["p1"])  # AI の手札は見せない

    def test_games_made_outside_the_site_are_listed_too(self):
        from helpers import game
        SqliteGameStore(self.db, "cli").create(game().state)
        mine = {r["id"]: r for r in self.host.call("/api/history")[1]}
        self.assertEqual((mine["cli"]["seat"], mine["cli"]["result"]), (None, "ongoing"))
        self.assertEqual(self.views(self.host, "cli"), 403)  # 対局中でホストの席が無い

    def test_delete_a_finished_game(self):
        g, _ = self.pvp()
        self.assertEqual(self.host.call("/api/games/%s" % g, {}, method="DELETE")[0], 403)  # 対局中は消せない
        self.finish(g)
        self.assertEqual(self.guest.call("/api/games/%s" % g, {}, method="DELETE")[0], 403)  # ホストだけ
        st = SqliteGameStore(self.db, g)
        self.tl(self.host, g)
        self.assertTrue(st.root.exists())
        self.assertEqual(self.host.call("/api/games/%s" % g, {}, method="DELETE"), (200, {"deleted": True}))
        self.assertFalse(st.exists() or st.root.exists())
        self.assertEqual(self.host.call("/api/history"), (200, []))
        self.assertEqual(self.tl(self.host, g), 404)

    def test_finished_timelines_are_saved_once(self):
        g = self.ai_game()
        self.finish(g)
        st = SqliteGameStore(self.db, g)
        code, first = self.host.call("/api/games/%s/timeline?seat=judge" % g)
        files = list((st.root / "cache").glob("timeline-judge-*.json"))
        self.assertEqual((code, len(files)), (200, 1))
        self.assertEqual(json.loads(files[0].read_text(encoding="utf-8"))["cursor"], first["cursor"])
        files[0].write_text(json.dumps(dict(first, marker=True)), encoding="utf-8")
        self.httpd.RequestHandlerClass.viewer._timelines.clear()
        self.assertTrue(self.host.call("/api/games/%s/timeline?seat=judge" % g)[1]["marker"])  # ファイルから
        st.undo()  # 巻き戻すと（決着前に戻る）、保存した時系列は使わない
        self.assertNotIn("marker", self.host.call("/api/games/%s/timeline?seat=p1" % g)[1])


if __name__ == "__main__":
    unittest.main()
