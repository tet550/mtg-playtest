"""履歴・公開の対局・観戦（公開のサーバー）: 見える範囲・公開の一覧・共有 URL（同意）・履歴から消す・時系列の保存。"""
import json
import pathlib
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable.sqlstore import SqliteGameStore  # noqa: E402
from mtgtable.web import Handler, Viewer  # noqa: E402
from test_decks import deck, fake_resolve  # noqa: E402
from test_site import Browser  # noqa: E402


class HistoryTest(unittest.TestCase):
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
        self.a, self.b, self.c = self.owner("alpha"), self.owner("beta"), Browser(self.base).open_site()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def owner(self, name):
        b = Browser(self.base).open_site()
        b.deck = b.call("/api/decks", {"name": name, "format": "standard",
                                       "text": deck("56 Forest", "4 Grizzly Bears")})[1]["id"]
        return b

    def pvp(self, visibility="public"):
        inv = self.a.call("/api/games", {"deck": self.a.deck, "opponent": "human", "visibility": visibility})[1]
        return self.b.call("/api/invites/%s/join" % inv["invite"], {"token": inv["token"], "deck": self.b.deck})[1]["game"]

    def finish(self, game, loser="p2"):
        SqliteGameStore(self.db, game).apply({"actor": loser, "acts": [{"act": [{"op": "declare", "kind": "concede"}]}]})

    def views(self, who, game, share=None):
        code, r = who.call("/api/games/%s/access%s" % (game, "?share=" + share if share else ""))
        return r["views"] if code == 200 else code

    def tl(self, who, game, seat="judge", share=None):
        return who.call("/api/games/%s/timeline?seat=%s%s" % (game, seat, "&share=" + share if share else ""))[0]

    def test_what_each_person_sees_before_and_after_the_end(self):
        g = self.pvp("public")
        self.assertEqual((self.views(self.a, g), self.views(self.b, g), self.views(self.c, g)), (["p1"], ["p2"], 403))
        self.assertEqual((self.tl(self.a, g), self.tl(self.a, g, "p2"), self.tl(self.c, g, "p1")), (403, 403, 403))
        self.assertEqual(self.c.call("/api/public"), (200, []))
        self.finish(g)
        self.assertEqual(self.views(self.c, g), ["judge", "p1", "p2"])  # 終わった公開の対局は誰でも
        self.assertEqual((self.tl(self.c, g), self.tl(self.c, g, "p2")), (200, 200))
        self.assertEqual(self.c.call("/api/games/%s/log" % g)[0], 200)
        self.assertEqual(self.c.call("/api/games/%s/declare" % g, {"seat": "p1", "kind": "say", "text": "x"})[0], 403)
        public = self.c.call("/api/public")[1]
        self.assertEqual([(p["id"], p["winner"]) for p in public], [(g, "p1")])
        self.assertEqual(len(self.c.call("/api/public?deck=beta")[1]), 1)
        self.assertEqual(self.c.call("/api/public?deck=gamma")[1], [])
        self.assertEqual(self.c.call("/api/public?before=" + public[0]["ended"])[1], [])  # ページ送り
        mine = {r["id"]: r for r in self.a.call("/api/history")[1]}
        self.assertEqual((mine[g]["result"], mine[g]["opponent"], mine[g]["seat"]), ("won", "human", "p1"))
        self.assertEqual(self.b.call("/api/history")[1][0]["result"], "lost")
        self.assertEqual(self.c.call("/api/history"), (200, []))

    def test_private_games_and_share_links(self):
        g = self.pvp("private")
        code, s = self.a.call("/api/games/%s/shares" % g, {"view": "p1"})  # 対局中でも自分の席は共有できる
        self.assertEqual(code, 201)
        self.assertEqual(self.views(self.c, g, s["token"]), ["p1"])
        self.assertEqual((self.tl(self.c, g, "p1", s["token"]), self.tl(self.c, g, "p2", s["token"])), (200, 403))
        self.assertEqual(self.a.call("/api/games/%s/shares" % g, {"view": "judge"})[0], 403)  # 全体は終わってから
        self.assertEqual(self.a.call("/api/games/%s/shares" % g, {"view": "p2"})[0], 403)  # 相手の席は共有できない
        self.assertEqual(self.c.call("/api/games/%s/shares" % g, {"view": "p1"})[0], 403)
        self.finish(g)
        self.assertEqual(self.views(self.c, g), 403)  # 非公開
        self.assertEqual(self.views(self.a, g), ["judge", "p1", "p2"])  # 席を持つ人は全部
        code, r = self.a.call("/api/games/%s/shares" % g, {"view": "judge"})
        self.assertEqual((code, "同意" in r["error"]), (403, True))  # 相手の同意が要る
        self.assertEqual(self.b.call("/api/games/%s/consent" % g, {}), (200, {"consented": True}))
        code, j = self.a.call("/api/games/%s/shares" % g, {"view": "judge"})
        self.assertEqual(code, 201)
        self.assertEqual(self.views(self.c, g, j["token"]), ["judge"])
        self.assertEqual([x["view"] for x in self.a.call("/api/games/%s/shares" % g)[1]], ["p1", "judge"])
        self.assertEqual(self.b.call("/api/games/%s/shares/%s" % (g, j["id"]), {}, method="DELETE")[0], 404)
        self.assertEqual(self.a.call("/api/games/%s/shares/%s" % (g, j["id"]), {}, method="DELETE"), (200, {"ok": True}))
        self.assertEqual(self.views(self.c, g, j["token"]), 403)  # 取り消した URL は使えない
        self.assertEqual(self.views(self.c, g, s["token"]), ["p1"])  # 他の URL はそのまま

    def test_whole_table_share_of_an_ai_game_needs_no_consent(self):
        g = self.a.call("/api/games", {"deck": self.a.deck, "opponent": "ai", "ai_deck": "repo:piza",
                                       "visibility": "private"})[1]["game"]
        self.finish(g)
        self.assertEqual(self.a.call("/api/games/%s/shares" % g, {"view": "judge"})[0], 201)

    def test_hide_from_history_and_delete_when_everyone_did(self):
        g = self.pvp("public")
        self.assertEqual(self.a.call("/api/games/%s/hide" % g, {})[0], 403)  # 対局中は消せない
        self.finish(g)
        self.assertEqual(self.c.call("/api/games/%s/hide" % g, {})[0], 403)  # 席を持たない人
        self.assertEqual(self.a.call("/api/games/%s/hide" % g, {}), (200, {"hidden": True, "deleted": False}))
        self.assertEqual(self.a.call("/api/history"), (200, []))
        self.assertEqual(self.c.call("/api/public"), (200, []))  # 公開の一覧からも外れる
        self.assertEqual(self.views(self.c, g), 403)
        self.assertEqual(self.views(self.b, g), ["judge", "p1", "p2"])  # 相手はまだ見られる
        st = SqliteGameStore(self.db, g)
        self.tl(self.b, g)
        self.assertTrue(st.root.exists())
        self.assertEqual(self.b.call("/api/games/%s/hide" % g, {}), (200, {"hidden": True, "deleted": True}))
        self.assertFalse(st.exists() or st.root.exists())  # 全員が消したら、対局そのものを消す
        self.assertEqual(self.tl(self.b, g), 404)

    def test_finished_timelines_are_saved_once(self):
        g = self.pvp("public")
        self.finish(g)
        st = SqliteGameStore(self.db, g)
        code, first = self.c.call("/api/games/%s/timeline?seat=judge" % g)
        files = list((st.root / "cache").glob("timeline-judge-*.json"))
        self.assertEqual((code, len(files)), (200, 1))
        self.assertEqual(json.loads(files[0].read_text(encoding="utf-8"))["cursor"], first["cursor"])
        files[0].write_text(json.dumps(dict(first, marker=True)), encoding="utf-8")
        self.httpd.RequestHandlerClass.viewer._timelines.clear()
        self.assertTrue(self.c.call("/api/games/%s/timeline?seat=judge" % g)[1]["marker"])  # ファイルから
        st.undo()  # 巻き戻すと（決着前に戻る）、保存した時系列は使わない
        self.assertNotIn("marker", self.a.call("/api/games/%s/timeline?seat=p1" % g)[1])


if __name__ == "__main__":
    unittest.main()
