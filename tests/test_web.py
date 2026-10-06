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
from mtgtable.web import Handler, Viewer  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
