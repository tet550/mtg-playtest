"""観戦ビューアのサーバー（席ごとの view・log・静的ファイル）。"""
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import GameStore, carddb  # noqa: E402
from mtgtable.web import Handler, Viewer, STATIC, export_site  # noqa: E402
from helpers import game  # noqa: E402


class ViewerServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        st = GameStore(pathlib.Path(cls.tmp.name) / "g1")
        st.create(game().state)
        st.apply({"actor": "p1", "label": "T1", "acts": [{"proc": "turn_start", "to": "main1"},
                                                          {"act": [{"op": "draw"}]}]})
        cls.old_cards = os.environ.get("MTG_CARDS_DIR")
        os.environ["MTG_CARDS_DIR"] = str(pathlib.Path(cls.tmp.name) / "cards")
        img = carddb.image_path("Grizzly Bears")
        img.parent.mkdir(parents=True)
        img.write_bytes(b"fake-jpeg")
        sym = carddb.cache_dir() / "symbols" / "G.svg"
        sym.parent.mkdir(parents=True)
        sym.write_text("<svg/>", encoding="utf-8")
        Handler.viewer = Viewer(cls.tmp.name, offline=True)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.httpd.daemon_threads = True
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        if cls.old_cards is None:
            os.environ.pop("MTG_CARDS_DIR", None)
        else:
            os.environ["MTG_CARDS_DIR"] = cls.old_cards
        cls.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read().decode("utf-8")

    def test_games_and_static(self):
        code, body = self.get("/api/games")
        self.assertEqual((code, [g["id"] for g in json.loads(body)]), (200, ["g1"]))
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("mtgtable", body)
        self.assertEqual(self.get("/static/app.js")[0], 200)
        for asset in STATIC.glob("*.js"):
            self.assertEqual(self.get("/static/" + asset.name)[0], 200)
        self.assertEqual(self.get("/static/..%2Fweb.py")[0], 404)

    def test_view_is_per_seat_and_replayable(self):
        p1 = json.loads(self.get("/api/games/g1/view?seat=p1")[1])
        p2 = json.loads(self.get("/api/games/g1/view?seat=p2")[1])
        self.assertEqual(len(p1["zones"]["p1.hand"]["cards"]), 8)
        self.assertEqual(p2["zones"]["p1.hand"]["known"], [])  # 相手の手札は見えない
        before = json.loads(self.get("/api/games/g1/view?seat=judge&at=0")[1])
        self.assertEqual((before["position"], before["cursor"], before["turn"]["turn"]), (0, 5, 0))

    def test_timeline_rebuilds_every_position(self):
        tl = json.loads(self.get("/api/games/g1/timeline?seat=p2")[1])
        self.assertEqual((tl["cursor"], len(tl["frames"]), tl["seat"]), (5, 6, "p2"))
        self.assertIn("k", tl["frames"][0])
        v = tl["frames"][0]["k"]
        for f in tl["frames"][1:]:
            for op in f["d"]:
                o = v
                for k in op[1][:-1]:
                    o = o[k]
                if op[0] == "s":
                    o[op[1][-1]] = op[2]
                else:
                    del o[op[1][-1]]
        want = json.loads(self.get("/api/games/g1/view?seat=p2")[1])
        want.pop("position"), v.pop("position")
        self.assertEqual(v["zones"]["p1.hand"], want["zones"]["p1.hand"])  # 相手の手札は見えないまま
        self.assertEqual(v["turn"], want["turn"])
        self.assertEqual(self.get("/api/games/g1/timeline?seat=p9")[0], 404)

    def test_log_only_for_judge_and_no_path_escape(self):
        code, body = self.get("/api/games/g1/log")
        log = json.loads(body)
        self.assertEqual((code, len(log), log[0]["batch_label"]), (200, 5, "T1"))
        self.assertEqual(self.get("/api/games/g1/log?seat=p1")[0], 403)
        self.assertEqual(self.get("/api/games/..%2Fg1/view")[0], 404)
        self.assertEqual(self.get("/api/games/g1/view?seat=p9")[0], 404)

    def test_card_image_from_cache(self):
        with urllib.request.urlopen(self.base + "/api/image?name=Grizzly%20Bears") as r:
            self.assertEqual((r.status, r.headers["Content-Type"], r.read()), (200, "image/jpeg", b"fake-jpeg"))
            self.assertIn("max-age", r.headers["Cache-Control"])
        self.assertEqual(self.get("/api/image?name=Forest")[0], 404)  # offline でキャッシュに無い

    def test_mana_symbol_from_cache(self):
        with urllib.request.urlopen(self.base + "/api/symbol?s=G") as r:
            self.assertEqual((r.status, r.headers["Content-Type"]), (200, "image/svg+xml"))
        self.assertEqual(self.get("/api/symbol?s=U")[0], 404)  # offline でキャッシュに無い
        self.assertEqual(self.get("/api/symbol?s=..%2Fx")[0], 404)  # 記号以外は受け付けない


    def test_export_static_site(self):
        from mtgtable.web import export_site
        out = pathlib.Path(self.tmp.name) / "site"
        self.assertEqual(export_site(self.tmp.name, out, offline=True), ["g1"])
        index = (out / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-static="1"', index)
        self.assertIn('src="static/app.js"', index)  # 相対パス（GitHub Pages のサブパスでも動く）
        tl = json.loads((out / "data" / "g1" / "timeline.json").read_text(encoding="utf-8"))
        self.assertEqual((tl["seat"], len(tl["frames"])), ("judge", 6))
        self.assertTrue((out / "data" / "g1" / "log.json").exists())
        cards = json.loads((out / "data" / "cards.json").read_text(encoding="utf-8"))
        self.assertIn("Forest", cards)  # offline でキャッシュに無いカードも、名前だけは載る
        self.assertTrue((out / "static" / "style.css").exists() and (out / ".nojekyll").exists())
        for asset in STATIC.glob("*.js"):
            self.assertEqual((out / "static" / asset.name).read_bytes(), asset.read_bytes())

    def test_export_empty_site(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as dest:
            self.assertEqual(export_site(root, dest, offline=True), [])
            self.assertEqual(json.loads((pathlib.Path(dest) / "data/games.json").read_text()), [])


if __name__ == "__main__":
    unittest.main()


class PlayServerTest(unittest.TestCase):
    """serve --play: 席の鍵・席の書き込み（依頼・宣言・回答だけ）・楽観的排他。"""

    def setUp(self):
        from mtgtable import play
        self.tmp = tempfile.TemporaryDirectory()
        self.st = GameStore(pathlib.Path(self.tmp.name) / "g")
        self.st.create(game().state)
        self.token = play.invite(self.st, "p1")
        GameStore(pathlib.Path(self.tmp.name) / "open").create(game().state)  # invite していない対局
        handler = type("PlayHandler", (Handler,), {"viewer": Viewer(self.tmp.name, offline=True, play=True)})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def call(self, path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def write(self, what, body, expect=None, token="mine", seat="p1"):
        token = self.token if token == "mine" else token
        return self.call("/api/games/g/" + what, dict(body, seat=seat, expect=expect), token)

    def judge(self, acts, text="処理"):
        r = self.st.apply({"actor": None, "acts": [dict(a, actor="p1") for a in acts] + [
            {"act": [{"op": "declare", "player": "p1", "kind": "ruled", "text": text}]}]})
        self.assertIsNone(r["stopped"])

    def test_seat_key_guards_reading(self):
        self.assertEqual(self.call("/api/config"), (200, {"play": True}))
        self.assertEqual(self.call("/api/games/g/timeline?seat=p1")[0], 403)
        self.assertEqual(self.call("/api/games/g/timeline?seat=p1", token="wrong")[0], 403)
        self.assertEqual(self.call("/api/games/g/timeline?seat=judge", token=self.token)[0], 403)
        self.assertEqual(self.call("/api/games/g/view?seat=p2", token=self.token)[0], 403)
        code, tl = self.call("/api/games/g/timeline?seat=p1", token=self.token)
        self.assertEqual((code, tl["seat"]), (200, "p1"))
        self.assertEqual(tl["frames"][0]["k"]["turn"]["waiting_on"], "p1")  # ゲーム前は先攻
        code, log = self.call("/api/games/g/log?seat=p1", token=self.token)
        self.assertEqual((code, log), (200, []))  # 鍵を持つ席には、その席に見せる形の Log
        self.assertEqual(self.call("/api/games/open/timeline?seat=judge")[0], 200)  # 鍵の無い対局は観戦できる
        games = {g["id"]: g for g in self.call("/api/games")[1]}
        self.assertEqual((games["g"]["seats"], games["open"]["seats"]), (["p1"], []))

    def test_seat_writes_only_requests_and_decisions(self):
        code, r = self.write("declare", {"kind": "keep"}, expect=0)
        self.assertEqual((code, r["cursor"], r["result"]["stopped"]), (200, 1, None))
        self.assertEqual(self.st.read_log()[0]["actor"], "p1")
        self.assertEqual(self.write("declare", {"kind": "say", "text": "hi"}, expect=0)[0], 409)  # 見ていた盤面より進んでいる
        self.assertEqual(self.write("declare", {"kind": "say", "text": "hi"}, token=None)[0], 403)
        self.assertEqual(self.write("declare", {"kind": "say", "text": "hi"}, seat="p2")[0], 403)  # 他の席の鍵ではない
        # 卓の op を書く入口は無い
        for path in ("apply", "undo"):
            self.assertEqual(self.call("/api/games/g/" + path, {"seat": "p1", "batch": {"acts": [{"act": [{"op": "draw"}]}]}},
                                       self.token)[0], 404)
        self.assertEqual(self.write("declare", {"kind": "ruled", "text": "x"})[0], 400)  # 審判の宣言・自由な kind は書けない
        self.assertEqual(self.write("declare", {"kind": "keep"})[0], 403)  # キープは1回
        self.assertEqual(self.st.cursor(), 1)
        self.assertEqual(self.write("request", {"comment": "手札がおかしい"})[0], 200)  # ゲーム前も審判への依頼は出せる
        self.assertEqual(self.write("request", {"comment": "もう1つ"})[0], 403)  # 審判の処理待ちの間は出さない

    def test_request_checks_the_scene_and_card_references(self):
        self.write("declare", {"kind": "keep"})
        self.st.apply({"actor": "p2", "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        self.judge([{"proc": "turn_start", "to": "main1"}], "ゲーム開始")
        s = self.st.load()
        mine, theirs = s.zones["p1.hand"].cards[0], s.zones["p2.hand"].cards[0]
        lib = s.zones["p1.library"].cards[0]
        self.assertEqual(self.write("request", {"plan": [{"kind": "cast", "cards": [theirs], "text": "x"}]})[0], 403)  # 知らないカード
        self.assertEqual(self.write("request", {"plan": [{"kind": "other", "cards": [lib], "text": "x"}]})[0], 403)
        self.assertEqual(self.write("request", {})[0], 400)  # 空の依頼
        cur = self.st.cursor()
        code, r = self.write("request", {"plan": [{"kind": "play_land", "cards": [mine], "text": "土地を出す"},
                                                  {"kind": "draw", "count": 1, "text": "1 枚引く"},
                                                  {"kind": "move", "text": "様子を見る"}],
                                         "then": "pass", "comment": "補足"}, expect=cur)
        self.assertEqual((code, r["result"]["stopped"]), (200, None))
        e = self.st.read_log()[-1]
        op = e["act"][0]
        self.assertEqual((e["actor"], op["kind"], op["then"], op["plan"][1]), ("p1", "intent", "pass",
                                                                            {"kind": "draw", "text": "1 枚引く", "count": 1}))
        self.assertEqual(op["plan"][2]["kind"], "other")  # 知らない種類は other に
        self.assertEqual(op["text"], "行動:\n1. 土地を出す\n2. 1 枚引く\n3. 様子を見る\nその後: パス（相手に渡す）\n補足: 補足")
        self.assertEqual(self.st.load().zones["p1.hand"].cards[0], mine)  # 盤面は動かない
        self.assertEqual(self.write("request", {"comment": "もう1つ"})[0], 403)  # 審判の処理待ちの間は組み立てない
        self.assertEqual(self.write("declare", {"kind": "pass"})[0], 403)
        # 審判の処理待ちの間は、発言・投了・止める場所の変更も受けない（処理が済んでから）
        self.assertEqual(self.write("declare", {"kind": "say", "text": "待ってます"})[0], 403)
        self.assertEqual(self.write("declare", {"kind": "concede"})[0], 403)
        self.assertEqual(self.call("/api/games/g/stops", {"seat": "p1", "stops": ["opp:end"]}, self.token)[0], 403)
        self.judge([])
        self.assertEqual(self.write("declare", {"kind": "say", "text": "どうぞ"})[0], 200)

    def test_mulligan_waits_for_the_judge(self):
        self.write("declare", {"kind": "mulligan"})
        hand = self.st.load().zones["p1.hand"].cards
        self.assertEqual(self.st.load().zones["p1.hand"].cards, hand)  # 引き直すのは審判
        from mtgtable import play
        self.assertEqual(play.waiting_on(self.st.load()), "judge")
        self.assertEqual(self.write("declare", {"kind": "keep"})[0], 403)  # 審判の処理待ち

    def test_answer_only_when_asked(self):
        self.assertEqual(self.write("answer", {"text": "はい"})[0], 403)
        self.st.apply({"actor": None, "acts": [{"act": [{"op": "declare", "player": "p1", "kind": "ask", "text": "使う？",
                                                          "choices": ["はい", "いいえ"]}]}]})
        self.assertEqual(self.write("declare", {"kind": "keep"})[0], 403)  # 質問に答えるまで
        code, r = self.write("answer", {"text": "はい"})
        self.assertEqual((code, self.st.read_log()[-1]["act"][0]["kind"]), (200, "answer"))

    def test_request_from_the_seat_builds_the_judge_prompt(self):
        latest = self.st.root / "prompts" / "judge" / "latest.md"
        self.write("declare", {"kind": "keep"})
        self.assertFalse(latest.exists())  # 審判の番ではない（p2 のキープ待ち）
        self.st.apply({"actor": "p2", "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        self.judge([{"proc": "turn_start", "to": "main1"}], "ゲーム開始")
        latest.unlink(missing_ok=True)
        self.write("request", {"then": "end_turn"})
        self.assertTrue(latest.exists())  # GUI の依頼で、すぐに審判のプロンプトができる
        self.assertIn("その後: ターン終了", latest.read_text(encoding="utf-8"))

    def test_stops_are_private_to_the_seat(self):
        self.assertEqual(self.call("/api/games/g/stops?seat=p1", token=self.token), (200, {"stops": []}))
        self.assertEqual(self.call("/api/games/g/stops", {"seat": "p1", "stops": ["opp:end", "opp:spell"]}, self.token),
                         (200, {"stops": ["opp:end", "opp:spell"]}))
        self.assertEqual(self.call("/api/games/g/stops?seat=p1", token=self.token)[1], {"stops": ["opp:end", "opp:spell"]})
        self.assertEqual(self.call("/api/games/g/stops?seat=p1")[0], 403)  # 鍵が要る
        self.assertEqual(self.call("/api/games/g/stops", {"seat": "p1", "stops": ["x:y"]}, self.token)[0], 400)
        self.assertEqual(self.st.cursor(), 0)  # 卓の記録には載らない

    def test_auto_watch_wakes_on_the_server_notification(self):
        import time
        from mtgtable import llm
        changes = llm.Changes("g", self.base, interval=30, retry=30, report=lambda *_: None)  # 見張りの間隔は長くしておく
        deadline = time.monotonic() + 5
        while not changes.connected and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(changes.connected)
        changes.wait()  # つないだ直後の通知（今の状態）を受け流す
        threading.Timer(0.2, lambda: self.write("declare", {"kind": "keep"})).start()
        start = time.monotonic()
        changes.wait()  # GUI の書き込みで、すぐ起きる（30 秒の見張りを待たない）
        self.assertLess(time.monotonic() - start, 3)
        self.assertEqual(self.st.cursor(), 1)

    def test_read_only_server_refuses_writes(self):
        self.httpd.RequestHandlerClass.viewer.play = False
        self.assertEqual(self.write("declare", {"kind": "keep"})[0], 403)
        self.assertEqual(self.call("/api/games/g/timeline?seat=judge")[0], 200)  # 観戦だけのサーバーは今まで通り
