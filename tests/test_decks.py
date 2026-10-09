"""デッキ登録: 検査（書式・カード名・構築・フォーマット）、Scryfall のまとめ引き、デッキの API、AI のプレイ方針。"""
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import carddb, decks, prompt  # noqa: E402
from mtgtable.sqlstore import SqliteGameStore  # noqa: E402
from mtgtable.web import Handler, Viewer  # noqa: E402
from helpers import game  # noqa: E402
from test_site import Browser  # noqa: E402

LEGAL = {"standard": "legal", "modern": "legal", "vintage": "legal"}
CARDS = {
    "Forest": {"name": "Forest", "type_line": "Basic Land — Forest", "legalities": LEGAL},
    "Grizzly Bears": {"name": "Grizzly Bears", "type_line": "Creature — Bear", "legalities": LEGAL},
    "Lightning Bolt": {"name": "Lightning Bolt", "type_line": "Instant",
                       "legalities": {"standard": "not_legal", "modern": "legal", "vintage": "legal"}},
    "Relentless Rats": {"name": "Relentless Rats", "type_line": "Creature — Rat", "legalities": LEGAL,
                        "oracle_text": "A deck can have any number of cards named Relentless Rats."},
    "Seven Dwarves": {"name": "Seven Dwarves", "type_line": "Creature — Dwarf", "legalities": LEGAL,
                      "oracle_text": "A deck can have up to seven cards named Seven Dwarves."},
    "Black Lotus": {"name": "Black Lotus", "type_line": "Artifact",
                    "legalities": {"standard": "not_legal", "modern": "not_legal", "vintage": "restricted"}},
    "Fire // Ice": {"name": "Fire // Ice", "type_line": "Instant // Instant", "legalities": LEGAL},
    "Mystery": {"name": "Mystery", "type_line": "Artifact"},  # legalities が分からない
    "delver of secrets": {"name": "Delver of Secrets // Insectile Aberration", "type_line": "Creature — Human Wizard",
                          "legalities": LEGAL, "faces": [{"name": "Delver of Secrets"}, {"name": "Insectile Aberration"}]},
}


def fake_resolve(names, need_legalities=False):
    found = {n: CARDS[n] for n in names if n in CARDS}
    return {"found": found, "missing": [n for n in dict.fromkeys(names) if n not in CARDS]}


def deck(*lines, side=()):
    return "Deck\n" + "\n".join(lines) + ("\n\nSideboard\n" + "\n".join(side) if side else "") + "\n"


class CheckTest(unittest.TestCase):
    def check(self, text, fmt="standard"):
        return decks.check(text, fmt, fake_resolve)

    def messages(self, r):
        return [(e["line"], e["message"]) for e in r["errors"]]

    def test_a_legal_deck(self):
        r = self.check(deck("56 Forest", "4 Grizzly Bears", side=["4 Forest"]))
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual((r["main"], r["sideboard"]), (60, 4))
        self.assertEqual(r["cards"], {"Forest": "Forest", "Grizzly Bears": "Grizzly Bears"})

    def test_copies_count_main_and_sideboard_together(self):
        r = self.check(deck("56 Forest", "3 Grizzly Bears", "1 Grizzly Bears", side=["1 Grizzly Bears"]))
        self.assertEqual(self.messages(r), [(3, "「Grizzly Bears」が 5 枚です（4 枚まで）")])  # 最初に書いた行
        self.assertTrue(self.check(deck("40 Forest", "20 Relentless Rats"))["ok"])  # 好きな枚数を入れてよい
        self.assertIn("7 枚まで", self.check(deck("52 Forest", "8 Seven Dwarves"))["errors"][0]["message"])
        self.assertTrue(self.check(deck("53 Forest", "7 Seven Dwarves"))["ok"])

    def test_sizes(self):
        r = self.check(deck("59 Forest", side=["16 Forest"]))
        self.assertEqual([m for _, m in self.messages(r)],
                         ["メインデッキが 59 枚です（60 枚以上）", "サイドボードが 16 枚です（15 枚まで）"])

    def test_unreadable_lines_and_unknown_cards_have_line_numbers(self):
        r = self.check("Deck\n60 Forest\nfour Grizzly Bears\n0 Grizzly Bears\n1 Grizzzly Bears\n")
        self.assertEqual([line for line, _ in self.messages(r)], [3, 4, 5])
        self.assertIn("見つかりません", r["errors"][2]["message"])
        self.assertEqual(r["errors"][2]["card"], "Grizzzly Bears")

    def test_formats(self):
        bolt = deck("56 Forest", "4 Lightning Bolt")
        self.assertIn("standard では使えないカード", self.check(bolt)["errors"][0]["message"])
        self.assertTrue(self.check(bolt, "modern")["ok"])
        self.assertTrue(self.check(bolt, "free")["ok"])  # フォーマットを見ない
        lotus = deck("56 Forest", "4 Black Lotus")
        self.assertIn("1 枚まで", self.check(lotus, "vintage")["errors"][0]["message"])  # 制限カード
        self.assertTrue(self.check(deck("59 Forest", "1 Black Lotus"), "vintage")["ok"])
        self.assertEqual(self.check(bolt, "commander")["errors"][0]["message"][:10], "フォーマットは st")
        r = self.check(deck("56 Forest", "4 Mystery"))
        self.assertTrue(r["ok"])
        self.assertIn("分かりませんでした", r["warnings"][0]["message"])

    def test_arena_export_and_split_cards(self):
        text = "About\nName My Deck\n\nDeck\n56 Forest (FDN) 272\n4 Fire // Ice (MH3) 290\n"
        r = self.check(text)
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(decks.decklist(text, r["cards"], "x").main, [(56, "Forest"), (4, "Fire // Ice")])
        r = self.check(deck("56 Forest", "4 delver of secrets"))
        self.assertEqual(r["cards"]["delver of secrets"], "Delver of Secrets")  # 両面カードは表の面の名前で卓に置く

    def test_size_limits_and_scryfall_failure(self):
        self.assertIn("長すぎ", self.check("1 Forest\n" * 300)["errors"][0]["message"])
        self.assertIn("空", self.check("  ")["errors"][0]["message"])

        def down(names, need_legalities=False):
            raise LookupError("HTTP 503")
        r = decks.check(deck("60 Forest"), "standard", down)
        self.assertFalse(r["ok"])
        self.assertIn("Scryfall", r["errors"][0]["message"])


class FakeScryfall(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        names = [i["name"] for i in body["identifiers"]]
        FakeScryfall.calls.append(names)
        data = []
        for n in names:
            if n == "Fire // Ice":  # 本物の Scryfall と同じく、分割カードの両方の名前では引けない
                continue
            if n == "Fire":  # 片方の名前なら引ける
                data.append({"name": "Fire // Ice", "type_line": "Instant // Instant", "legalities": LEGAL,
                             "card_faces": [{"name": "Fire"}, {"name": "Ice"}]})
            elif not n.startswith("Nope"):
                data.append({"name": n.title(), "type_line": "Creature", "legalities": LEGAL})
        out = json.dumps({"data": data, "not_found": [{"name": n} for n in names if n.startswith("Nope")]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class ResolveNamesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeScryfall)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.saved = (carddb.COLLECTION_API, carddb.MIN_INTERVAL, os.environ.get("MTG_CARDS_DIR"))
        carddb.COLLECTION_API = "http://127.0.0.1:%d/cards/collection" % self.httpd.server_address[1]
        carddb.MIN_INTERVAL = 0
        os.environ["MTG_CARDS_DIR"] = self.tmp.name
        FakeScryfall.calls = []

    def tearDown(self):
        carddb.COLLECTION_API, carddb.MIN_INTERVAL, cards_dir = self.saved
        if cards_dir is None:
            os.environ.pop("MTG_CARDS_DIR", None)
        else:
            os.environ["MTG_CARDS_DIR"] = cards_dir
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def test_batches_of_75_cached_and_matched_by_face(self):
        names = ["card %d" % i for i in range(80)] + ["Fire", "Nope Card", "card 1", "fire//ice"]
        r = carddb.resolve_names(names, need_legalities=True)
        self.assertEqual([len(c) for c in FakeScryfall.calls], [75, 8])  # 1枚ずつではなく 75 枚ずつ
        self.assertEqual(r["found"]["fire//ice"]["name"], "Fire // Ice")  # 最初の面で引いて、全体の名前と照らす
        self.assertEqual(r["missing"], ["Nope Card"])
        self.assertEqual(r["found"]["Fire"]["name"], "Fire // Ice")
        self.assertEqual(r["found"]["card 1"]["name"], "Card 1")
        self.assertEqual(r["found"]["card 1"]["legalities"], LEGAL)
        FakeScryfall.calls = []
        r = carddb.resolve_names(["card 3", "Fire"], need_legalities=True)
        self.assertEqual((FakeScryfall.calls, sorted(r["found"])), ([], ["Fire", "card 3"]))  # キャッシュから
        self.assertEqual(carddb.lookup("Card 3", offline=True)["name"], "Card 3")  # 正式な名前でも引ける

    def test_old_cache_without_legalities_is_fetched_again(self):
        carddb._save("Old Card", {"name": "Old Card", "type_line": "Creature"})
        self.assertEqual(carddb.resolve_names(["Old Card"])["found"]["Old Card"]["name"], "Old Card")
        self.assertEqual(FakeScryfall.calls, [])
        carddb.resolve_names(["Old Card"], need_legalities=True)
        self.assertEqual(FakeScryfall.calls, [["Old Card"]])


class DeckApiTest(unittest.TestCase):
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

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def call(self, b, method, path, body=None, origin="same"):
        import urllib.error
        import urllib.request
        headers = {"Content-Type": "application/json", "Cookie": "mtg_owner=%s" % b.cookie}
        if origin:
            headers["Origin"] = "http://" + b.host
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_register_list_update_delete_only_your_own(self):
        a, b = Browser(self.base).open_site(), Browser(self.base).open_site()
        body = {"name": "green", "format": "standard", "text": deck("56 Forest", "4 Grizzly Bears"),
                "strategy": "熊で殴る"}
        code, d = self.call(a, "POST", "/api/decks", body)
        self.assertEqual((code, d["name"], d["main"], d["strategy"]), (201, "green", 60, "熊で殴る"))
        self.assertEqual([x["name"] for x in self.call(a, "GET", "/api/decks")[1]], ["green"])
        self.assertEqual(self.call(b, "GET", "/api/decks"), (200, []))  # 他の人のデッキは見えない
        self.assertEqual(self.call(b, "GET", "/api/decks/" + d["id"])[0], 404)
        self.assertEqual(self.call(b, "DELETE", "/api/decks/" + d["id"])[0], 404)
        self.assertEqual(self.call(a, "POST", "/api/decks", body)[1]["check"]["errors"][0]["message"],
                         "同じ名前のデッキがあります")
        self.assertEqual(self.call(b, "POST", "/api/decks", body)[0], 201)  # 名前は所有者ごと
        # 更新も同じ検査を通る
        code, r = self.call(a, "PUT", "/api/decks/" + d["id"], dict(body, text=deck("55 Forest", "5 Grizzly Bears")))
        self.assertEqual((code, r["check"]["errors"][0]["line"]), (400, 3))
        code, r = self.call(a, "PUT", "/api/decks/" + d["id"], dict(body, name="green-2", text=deck("60 Forest")))
        self.assertEqual((code, r["name"], self.call(a, "GET", "/api/decks/" + d["id"])[1]["text"]),
                         (200, "green-2", deck("60 Forest")))
        self.assertEqual(self.call(a, "DELETE", "/api/decks/" + d["id"], origin=None)[0], 403)  # Origin が要る
        self.assertEqual(self.call(a, "DELETE", "/api/decks/" + d["id"]), (200, {"ok": True}))
        self.assertEqual(self.call(a, "GET", "/api/decks"), (200, []))

    def test_check_without_saving_and_input_errors(self):
        a = Browser(self.base).open_site()
        code, r = self.call(a, "POST", "/api/decks/check", {"text": deck("56 Forest", "4 Lightning Bolt"), "format": "modern"})
        self.assertEqual((code, r["ok"]), (200, True))
        self.assertEqual(self.call(a, "GET", "/api/decks"), (200, []))
        code, r = self.call(a, "POST", "/api/decks", {"name": "Bad Name", "text": deck("60 Forest"),
                                                       "strategy": "x" * (decks.MAX_STRATEGY + 1)})
        self.assertEqual(code, 400)
        self.assertEqual([e["message"][:6] for e in r["check"]["errors"]], ["デッキ名は半", "プレイ方針は"])
        self.assertEqual(self.call(Browser(self.base), "GET", "/api/decks")[0], 403)  # 所有者の鍵が無い


class StrategyPromptTest(unittest.TestCase):
    def test_registered_strategy_replaces_the_repo_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = SqliteGameStore(pathlib.Path(tmp) / "mtg.sqlite", "g")
            state = game().state
            state.meta["decks"]["p1"].update({"name": "piza", "strategy": "序盤は土地を並べる"})
            state.meta["decks"]["p2"].update({"name": "piza", "strategy": ""})
            st.create(state)
            p1, p2 = prompt.seat_system(st, "p1"), prompt.seat_system(st, "p2")
            self.assertIn("序盤は土地を並べる", p1)
            self.assertNotIn("piza.md", p1 + p2)  # 同じ名前でも decklists/strategy/ のファイルは入れない
            self.assertNotIn("戦略メモ", p2)


if __name__ == "__main__":
    unittest.main()
