"""table.py（紙モデルの中核）の回帰テスト。ネットワークには出ない。"""
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import cardcache  # noqa: E402
import decks  # noqa: E402
import table  # noqa: E402

CARDS = {
    "Forest": dict(types=["Land"], supertypes=["Basic"], subtypes=["Forest"],
                   oracle_text_en="({T}: Add {G}.)"),
    "Grizzly Bears": dict(types=["Creature"], subtypes=["Bear"], mana_cost="{1}{G}",
                          power="2", toughness="2"),
    "Giant Growth": dict(types=["Instant"], mana_cost="{G}",
                         oracle_text_en="Target creature gets +3/+3 until end of turn."),
    "Watcher": dict(types=["Creature"], mana_cost="{2}{G}", power="1", toughness="1",
                    oracle_text_en="At the beginning of your upkeep, put a +1/+1 counter on this creature.\n"
                                   "Whenever another creature you control enters, you gain 1 life."),
    "Pacifism": dict(types=["Enchantment"], subtypes=["Aura"], mana_cost="{1}{W}",
                     oracle_text_en="Enchant creature\nEnchanted creature can't attack or block."),
}
DECK = "Deck\n20 Forest\n20 Grizzly Bears\n10 Giant Growth\n6 Watcher\n4 Pacifism\n\nSideboard\n2 Giant Growth\n"


class TableCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.cards = root / "cards"
        for name, fields in CARDS.items():
            cardcache.put_manual(name, str(self.cards), **fields)
        self.deck = root / "green.txt"
        self.deck.write_text(DECK, encoding="utf-8")
        self.state = root / "game" / "g01.json"
        self.base = ["--state", str(self.state), "--cards-dir", str(self.cards),
                     "--decks-dir", str(root / "decks"), "--offline"]

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *argv, stdin=None, ok=True):
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = table.main(self.base + list(argv))
        finally:
            sys.stdin = old
        if ok:
            self.assertEqual(code, 0, err.getvalue())
        else:
            self.assertEqual(code, 1, out.getvalue())
        return out.getvalue() + err.getvalue()

    def st(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def start(self, *extra):
        self.cli("init", "--deck1", str(self.deck), "--deck2", str(self.deck), "--seed", "7", *extra)

    def history(self):
        return len(list(table.history_dir(self.state).glob("*.json")))

    def find(self, name, zone):
        st = self.st()
        return next(o for o in st["zones"][zone] if st["objects"][str(o)]["card"] == name)


class InitTest(TableCase):
    def test_places_libraries_and_sideboards(self):
        self.start()
        st = self.st()
        self.assertEqual(len(st["zones"]["P1:library"]), 60)
        self.assertEqual(len(st["zones"]["P2:sideboard"]), 2)
        self.assertEqual(st["tracker"]["phase"], "pregame")
        self.assertEqual(st["players"]["P1"]["life"], 20)

    def test_same_seed_same_order(self):
        self.start()
        first = self.st()["zones"]["P1:library"]
        self.cli("init", "--deck1", str(self.deck), "--deck2", str(self.deck), "--seed", "7", "--force")
        self.assertEqual(first, self.st()["zones"]["P1:library"])

    def test_missing_card_does_not_save(self):
        self.deck.write_text("Deck\n60 Nonexistent Card\n", encoding="utf-8")
        out = self.cli("init", "--deck1", str(self.deck), "--goldfish", "--seed", "1", ok=False)
        self.assertIn("カード情報を用意できません", out)
        self.assertFalse(self.state.exists())

    def test_goldfish_has_no_opponent_library(self):
        self.cli("init", "--deck1", str(self.deck), "--goldfish", "--seed", "1")
        st = self.st()
        self.assertEqual(st["zones"]["P2:library"], [])
        self.assertEqual(st["players"]["P2"]["name"], "goldfish")


class RunTest(TableCase):
    def test_failed_line_applies_nothing(self):
        self.start()
        before, hist = self.st(), self.history()
        out = self.cli("run", "-", stdin="draw P1 7\ncounter P1 poison -1\n", ok=False)
        self.assertIn("2行目で停止", out)
        self.assertEqual(before, self.st())
        self.assertEqual(hist, self.history())

    def test_one_run_is_one_undo(self):
        self.start()
        self.cli("run", "-", stdin="draw P1 7\ndraw P2 7\n")
        self.cli("draw", "P1")
        self.assertEqual(len(self.st()["zones"]["P1:hand"]), 8)
        self.cli("undo", "2")
        st = self.st()
        self.assertEqual((len(st["zones"]["P1:hand"]), len(st["zones"]["P2:hand"])), (0, 0))

    def test_labels_refer_to_new_objects(self):
        self.start()
        self.cli("run", "-", stdin='token P1 Goblin --pt 1/1 --types "Creature Goblin" --label g\n'
                                   "counter $g +1/+1 +2\ntap $g\n")
        st = self.st()
        tok = st["objects"][str(st["zones"]["P1:battlefield"][0])]
        self.assertEqual(tok["counters"], {"+1/+1": 2})
        self.assertTrue(tok["tapped"])

    def test_forbidden_commands(self):
        self.start()
        out = self.cli("run", "-", stdin="undo\n", ok=False)
        self.assertIn("run の中で使えません", out)


class ComponentTest(TableCase):
    def test_leaving_battlefield_takes_off_dice_and_notes(self):
        self.start()
        self.cli("draw", "P1", "7")
        bear = self.find("Grizzly Bears", "P1:library") if not any(
            self.st()["objects"][str(o)]["card"] == "Grizzly Bears" for o in self.st()["zones"]["P1:hand"]) \
            else self.find("Grizzly Bears", "P1:hand")
        aura = self.find("Pacifism", "P1:library")
        self.cli("run", "-", stdin="move %d P2:battlefield\nmove %d battlefield --controller P1\n"
                                   "attach %d --to %d\ncounter %d +1/+1 +1\ndamage %d +1\ntap %d\n"
                                   'note %d "+3/+3" --until eot\n'
                                   % (bear, aura, aura, bear, bear, bear, bear, bear))
        self.assertEqual(self.st()["objects"][str(bear)]["controller"], "P2")
        out = self.cli("move", str(bear), "graveyard")
        st = self.st()
        o = st["objects"][str(bear)]
        self.assertIn(bear, st["zones"]["P1:graveyard"])        # オーナーの墓地
        self.assertEqual((o["counters"], o["damage"], o["tapped"], o["notes"], o["controller"]),
                         ({}, 0, False, [], "P1"))
        self.assertIsNone(st["objects"][str(aura)]["under"])     # 重ね置きは外れるがオーラは残る
        self.assertIn(aura, st["zones"]["P1:battlefield"])
        self.assertIn("P1墓地", out)

    def test_library_needs_a_position(self):
        self.start()
        self.cli("draw", "P1", "2")
        a, b = self.st()["zones"]["P1:hand"]
        self.assertIn("位置を指定", self.cli("move", str(a), "library", ok=False))
        self.cli("move", str(a), "library", "--bottom")
        self.cli("move", str(b), "library", "--index", "1")
        lib = self.st()["zones"]["P1:library"]
        self.assertEqual((lib[0], lib[-2]), (a, b))

    def test_real_cards_cannot_vanish(self):
        self.start()
        oid = self.st()["zones"]["P1:library"][0]
        self.assertIn("実在のカード", self.cli("remove", str(oid), ok=False))
        self.cli("token", "P1", "--preset", "treasure")
        tok = self.st()["zones"]["P1:battlefield"][0]
        self.cli("run", "-", stdin="move %d graveyard\nremove %d\n" % (tok, tok))
        self.assertNotIn(str(tok), self.st()["objects"])

    def test_physical_floors(self):
        self.start()
        self.assertIn("0 より少なく", self.cli("counter", "P1", "poison", "-1", ok=False))
        self.assertIn("ありません", self.cli("mana", "P1", "pay", "G", ok=False))
        self.cli("life", "P1", "-25")
        self.assertEqual(self.st()["players"]["P1"]["life"], -5)   # ライフパッドは負も書ける

    def test_nothing_is_automatic(self):
        """致死ダメージもライフ0も、テーブルは何もしない。"""
        self.start()
        bear = self.find("Grizzly Bears", "P1:library")
        self.cli("run", "-", stdin="move %d battlefield\ndamage %d +5\nlife P1 =0\nturn next\n" % (bear, bear))
        st = self.st()
        self.assertIn(bear, st["zones"]["P1:battlefield"])
        self.assertEqual(st["objects"][str(bear)]["damage"], 5)
        self.assertEqual(st["zones"]["P1:hand"], [])              # turn next は引かない


class HiddenInfoTest(TableCase):
    def test_library_is_never_listed(self):
        self.start()
        self.assertIn("見られません", self.cli("zone", "P1:library", ok=False))

    def test_search_hides_order(self):
        self.start()
        out = self.cli("search", "P1", "Watcher")
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("  [")]), 6)
        self.assertIn("積み順は伏せています", out)

    def test_seat_view(self):
        self.start()
        self.cli("run", "-", stdin="draw P1 7\ndraw P2 7\n")
        self.assertIn("相手の手札", self.cli("--as", "P1", "show", "--hand", "P2", ok=False))
        self.assertIn("--as P1", self.cli("--as", "P1", "draw", "P2", ok=False))
        mine = self.cli("--as", "P1", "show", "--hand", "P1")
        self.assertIn("P1 手札(7)", mine)
        secret = [e for e in self.st()["log"] if e.get("private") == "P2"]
        self.assertTrue(secret)
        self.assertNotIn(secret[0]["text"], self.cli("--as", "P1", "log"))


class ReminderTest(TableCase):
    def test_expired_note_and_due_memo_are_only_shown(self):
        self.start()
        bear = self.find("Grizzly Bears", "P1:library")
        self.cli("run", "-", stdin='move %d battlefield\nnote %d "+3/+3" --until eot\n'
                                   'memo add "帰還させる" --player P2 --at ending.end\n' % (bear, bear))
        out = self.cli("turn", "next")
        self.assertIn("期限切れの付箋 N1", out)
        out = self.cli("phase", "ending.end")
        self.assertIn("時期のメモ M1", out)
        self.assertEqual(len(self.st()["objects"][str(bear)]["notes"]), 1)   # 外すのはAI
        self.cli("run", "-", stdin="note rm N1\nmemo done M1 --reason 済\n")
        self.assertNotIn("!!", self.cli("show"))


class AidTest(TableCase):
    def test_aid_does_not_change_state(self):
        self.start()
        w = self.find("Watcher", "P1:library")
        bear = self.find("Grizzly Bears", "P1:library")
        self.cli("run", "-", stdin="move %d battlefield\nmove %d battlefield\ndamage %d +2\n" % (w, bear, bear))
        before, hist = self.state.read_text(encoding="utf-8"), self.history()
        trig = self.cli("aid", "triggers", "--phase", "beginning.upkeep")
        self.assertIn("upkeep", trig)
        self.assertNotIn("Whenever another creature", trig)
        self.assertIn("Whenever another creature", self.cli("aid", "triggers"))
        self.assertIn("ダメージがタフネス以上", self.cli("aid", "creatures"))
        self.assertIn("タフネスを超える", self.cli("aid", "check"))
        self.assertEqual(before, self.state.read_text(encoding="utf-8"))
        self.assertEqual(hist, self.history())


class ResultTest(TableCase):
    def test_goldfish_own_turn_and_stats(self):
        self.cli("init", "--deck1", str(self.deck), "--goldfish", "--seed", "1", "--first", "P1")
        self.cli("turn", "5", "--active", "P1")
        self.cli("end", "--winner", "P1", "--reason", "20点", "--tag", "A")
        results = self.state.with_name("results.jsonl")
        rec = json.loads(results.read_text(encoding="utf-8"))
        self.assertEqual(rec["own_turn"], 3)
        self.assertIn("記録済み", self.cli("end", "--winner", "P1", "--reason", "x", ok=False))
        self.assertIn("自ターン 平均3.00", self.cli("stats", str(results)))

    def test_mana_dice(self):
        self.start()
        self.cli("run", "-", stdin="mana P1 add RRG\nmana P1 pay RG\n")
        self.assertEqual(self.st()["players"]["P1"]["mana"], {"R": 1})
        self.cli("mana", "P1", "clear")
        self.assertEqual(self.st()["players"]["P1"]["mana"], {})


class DeckVerifyTest(unittest.TestCase):
    def test_verify_does_not_write_without_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            cards = root / "cards"
            for name, fields in CARDS.items():
                cardcache.put_manual(name, str(cards), **fields)
            deck = decks.build("green", DECK, str(cards), offline=True)
            path = decks.save(str(root / "decks"), deck)
            before = path.read_text(encoding="utf-8")
            empty = root / "empty-cards"
            for extra in ([], ["--write"]):
                argv = ["decks.py", "--dir", str(root / "decks"), "--cards-dir", str(empty),
                        "--offline", "verify", "green"] + extra
                old = sys.argv
                sys.argv = argv
                try:
                    with contextlib.redirect_stdout(io.StringIO()) as out, \
                            contextlib.redirect_stderr(io.StringIO()):
                        decks.main()
                finally:
                    sys.argv = old
                self.assertEqual(before, path.read_text(encoding="utf-8"))
            self.assertIn("書き込みません", out.getvalue())


if __name__ == "__main__":
    unittest.main()
