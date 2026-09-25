"""Goldfish runs: the opponent's turn is skipped, batches stop at randomness and lethal."""
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import mtg


class GoldfishTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.path = self.root / "g01.json"
        self.fixture("P1")

    def fixture(self, first):
        self.call("init", "--seed", "3", "--first", first)
        st = self.read()
        st["goldfish"] = True
        for oid in range(1, 11):
            name = "Card%d" % oid
            st["objects"][str(oid)] = mtg.new_object(oid, name, "P1")
            st["cards"][name] = dict(token=True, types=["Creature"], subtypes=[],
                                     power="2", toughness="2", oracle="")
            st["zones"]["P1:library"].append(oid)
        st["next_oid"] = 11
        self.write(st)

    def call(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mtg.dispatch(["--state", str(self.path), "--offline", *args])
        return out.getvalue()

    def run_batch(self, text):
        batch = self.root / "batch.mtg"
        batch.write_text(text, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            try:
                mtg.dispatch(["--state", str(self.path), "--offline", "run", str(batch), "--compact"])
            except SystemExit:
                pass
        return out.getvalue()

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, st):
        self.path.write_text(json.dumps(st), encoding="utf-8")

    def test_init_requires_exactly_one_deck(self):
        with self.assertRaises(SystemExit) as caught:
            self.call("init", "--goldfish", "--seed", "1")
        self.assertIn("--deck1", str(caught.exception.code))

    def test_opponent_turn_is_skipped_and_own_turn_counted(self):
        out = self.run_batch("phase to precombat_main\nmove 1 battlefield\n"
                             "turn next --to precombat_main --draw\nshow --ids\n")
        st = self.read()
        self.assertEqual((st["turn"], st["active"], st["phase"]), (3, "P1", "precombat_main"))
        self.assertIn("T2 P2 は何もしないため省略", out)
        self.assertNotIn("=== T2 P2", out)
        self.assertIn("自ターン2・先手", out)
        self.assertEqual(st["zones"]["P1:hand"], [2])
        self.assertIn("山札上（公開", out)
        self.assertIn("1.[3]Card3", out)

    def test_on_the_draw_counts_own_turns(self):
        self.fixture("P2")
        self.assertIn("自ターン前・後手", self.call("show"))
        out = self.call("turn", "next", "--to", "precombat_main", "--draw")
        self.assertIn("自ターン1・後手", out)
        self.assertEqual(self.read()["zones"]["P1:hand"], [1])

    def test_trigger_on_opponent_turn_stops_the_skip_and_the_batch(self):
        st = self.read()
        st["cards"]["Card1"]["oracle"] = "At the beginning of each upkeep, you gain 1 life."
        st["zones"]["P1:library"].remove(1)
        st["zones"]["P1:battlefield"].append(1)
        st["phase"] = "precombat_main"
        self.write(st)
        out = self.run_batch("turn next --to precombat_main --draw\nmove 2 battlefield\n")
        st = self.read()
        self.assertEqual((st["turn"], st["active"]), (2, "P2"))
        self.assertIn("省略しません", out)
        self.assertIn("相手ターンに確認事項", out)
        self.assertNotIn(2, st["zones"]["P1:battlefield"])

    def test_timing_stop_is_skipped_only_when_answered_on_the_next_line(self):
        st = self.read()
        st["cards"]["Card1"]["oracle"] = "At the beginning of your end step, you gain 1 life."
        st["zones"]["P1:library"].remove(1)
        st["zones"]["P1:battlefield"].append(1)
        st["phase"] = "precombat_main"
        self.write(st)
        out = self.run_batch("phase to ending.end\nnote \"x\"\n")
        self.assertIn("タイミングの確認待ちでバッチ停止", out)
        self.call("undo")
        out = self.run_batch("phase to ending.end\npending cancel T1 --reason \"test\"\n"
                             "turn next --to precombat_main --draw\n")
        self.assertNotIn("バッチ停止", out)
        self.assertEqual((self.read()["turn"], self.read()["active"]), (3, "P1"))

    def test_batch_stops_after_randomness_and_shows_new_order(self):
        out = self.run_batch("shuffle P1\ndraw P1 1\n")
        self.assertIn("乱数", out)
        self.assertIn("山札上（公開", out)
        self.assertEqual(self.read()["zones"]["P1:hand"], [])

    def test_batch_stops_after_lethal_but_allows_recording(self):
        out = self.run_batch("life P2 -20\nnote \"lethal\"\nlife P2 -1\n")
        self.assertEqual(out.count("リーサル到達:"), 1)
        self.assertIn("リーサル到達後のためバッチ停止", out)
        self.assertEqual(self.read()["players"]["P2"]["life"], 0)
        out = self.call("end", "--winner", "P1", "--reason", "combat", "--tag", "gf")
        self.assertIn("リーサル: 自ターン1（先手）", out)
        rec = json.loads((self.root / "results.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(rec["goldfish"], {"deck": None, "on": "play", "lethal_turn": 1})

    def test_end_refuses_a_win_before_lethal(self):
        with self.assertRaises(SystemExit) as caught:
            self.call("end", "--winner", "P1", "--reason", "early")
        self.assertIn("リーサル未到達", str(caught.exception.code))
        self.assertFalse((self.root / "results.jsonl").exists())
        self.call("end", "--draw", "--reason", "cap")
        rec = json.loads((self.root / "results.jsonl").read_text(encoding="utf-8"))
        self.assertIsNone(rec["goldfish"]["lethal_turn"])

    def test_stats_summarise_lethal_turns(self):
        lines = []
        for turn, on in ((3, "play"), (4, "play"), (5, "draw"), (None, "draw")):
            lines.append(json.dumps({"tag": "gf", "turn": 9, "winner": "P1" if turn else "draw",
                                     "mulligans": {"P1": 1, "P2": 0},
                                     "goldfish": {"deck": "d", "on": on, "lethal_turn": turn}}))
        (self.root / "results.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = self.call("stats")
        self.assertIn("4ゲーム（ゴールドフィッシュ）", out)
        self.assertIn("全体 4件: 平均 4.00 / 中央値 4 / 最速 3 / 最遅 5 / リーサルなし 1件", out)
        self.assertIn("先手 2件: 平均 3.50", out)
        self.assertIn("T3:1件(累計25%) T4:1件(累計50%) T5:1件(累計75%)", out)
        self.assertNotIn("勝(", out)

    def test_turn_history_is_off_unless_requested(self):
        st = self.read()
        st["turn_history"] = False
        self.write(st)
        self.call("turn", "next")
        self.assertFalse((self.root / "output" / "g01" / "turn-starts.md").exists())

    def test_turn_history_path_is_not_repeated_every_turn(self):
        st = self.read()
        st.pop("goldfish")
        self.write(st)
        self.assertNotIn("ターン履歴", self.call("turn", "next"))
        self.assertTrue((self.root / "output" / "g01" / "turn-starts.md").exists())

    def test_reveal_count_follows_the_state(self):
        st = self.read()
        st["reveal"] = 8
        self.write(st)
        out = self.call("show")
        self.assertIn("8.[8]Card8", out)
        self.assertIn("…残り2枚", out)
        self.assertNotIn("9.[9]", out)

    def test_init_names_the_dummy_and_stores_options(self):
        with patch.object(mtg.decks, "resolve_source", return_value={"legal": True, "name": "d", "main": []}), \
                patch.object(mtg.decks, "card_names", return_value=[]):
            self.call("init", "--goldfish", "--deck1", "d", "--seed", "1", "--reveal", "7")
        st = self.read()
        self.assertEqual(st["players"]["P2"]["name"], "goldfish")
        self.assertEqual((st["goldfish"], st["reveal"], st["turn_history"]), (True, 7, False))

    def fake_deck(self):
        deck = {"legal": True, "name": "d", "main": [{"name": "Old", "count": 4}, {"name": "Filler", "count": 36}]}
        return (patch.object(mtg.decks, "resolve_source", return_value=deck),
                patch.object(mtg, "canonical_name", side_effect=lambda n: n),
                patch.object(mtg.cardcache, "load", side_effect=lambda d, n, **_: {"name": n, "key": n, "types": ["Creature"]}))

    def library_names(self, path):
        st = json.loads(path.read_text(encoding="utf-8"))
        return [st["objects"][str(o)]["name"] for o in st["zones"]["P1:library"]], st

    def test_swap_keeps_every_other_card_in_place_for_the_same_seed(self):
        a, b, c = self.fake_deck()
        base, alt = self.root / "base.json", self.root / "alt.json"
        with a, b, c, contextlib.redirect_stdout(io.StringIO()):
            mtg.dispatch(["--state", str(base), "--offline", "init", "--goldfish", "--deck1", "d", "--seed", "5"])
            mtg.dispatch(["--state", str(alt), "--offline", "init", "--goldfish", "--deck1", "d", "--seed", "5",
                          "--swap", "Old=New", "--swap", "Old=New"])
        (nb, sb), (na, sa) = self.library_names(base), self.library_names(alt)
        self.assertEqual(sb["zones"]["P1:library"], sa["zones"]["P1:library"])
        changed = [(x, y) for x, y in zip(nb, na) if x != y]
        self.assertEqual(changed, [("Old", "New"), ("Old", "New")])
        self.assertEqual((na.count("Old"), na.count("New")), (2, 2))
        self.assertEqual(sa["players"]["P1"]["swaps"], ["Old→New", "Old→New"])
        with self.assertRaises(SystemExit):
            mtg.goldfish.apply_swaps(["Old"], [("Old", "New"), ("Old", "New")], lambda n: n)

    def test_seeds_finds_opening_hands_with_the_swapped_slot(self):
        names = ["Old"] * 4 + ["Filler"] * 36
        swapped, slots, _ = mtg.goldfish.apply_swaps(names, [("Old", "New")], lambda n: n)
        a, b, c = self.fake_deck()
        with a, b, c:
            out = self.call("seeds", "--deck1", "d", "--swap", "Old=New", "--count", "3")
        seeds = [int(x) for x in out.split("seeds: ")[1].splitlines()[0].split()]
        self.assertEqual(len(seeds), 3)
        for seed in seeds:
            self.assertTrue(mtg.goldfish.seed_hits(swapped, slots, seed, 7))
        skipped = [s for s in range(1, seeds[-1]) if s not in seeds]
        self.assertFalse(any(mtg.goldfish.seed_hits(swapped, slots, s, 7) for s in skipped))

    def test_stats_pair_compares_the_same_seed_and_side(self):
        rows = [("base", 1, 4), ("base", 2, 5), ("base", 3, None), ("alt", 1, 3), ("alt", 2, 5),
                ("alt", 3, 6), ("alt", 4, 4)]
        (self.root / "results.jsonl").write_text("".join(
            json.dumps({"tag": t, "seed": s, "turn": 9, "goldfish": {"deck": "d", "on": "play", "lethal_turn": v,
                        **({"swap": ["Old→New"]} if t == "alt" else {})}}) + "\n" for t, s, v in rows),
            encoding="utf-8")
        out = self.call("stats", "--pair", "base", "alt")
        self.assertIn("3組", out)
        self.assertIn("入れ替え: Old→New", out)
        self.assertIn("平均差 -0.50", out)
        self.assertIn("速い 1・同じ 1・遅い 0", out)
        self.assertIn("リーサルなしを含む組 1", out)
        self.assertIn("相手のいない記録 1件", out)

    def test_reveal_option_is_goldfish_only_and_positive(self):
        with self.assertRaises(SystemExit) as caught:
            self.call("init", "--seed", "1", "--reveal", "8")
        self.assertIn("--goldfish 専用", str(caught.exception.code))
        with self.assertRaises(SystemExit) as caught:
            self.call("init", "--goldfish", "--deck1", "x", "--seed", "1", "--reveal", "0")
        self.assertIn("1以上", str(caught.exception.code))

    def test_library_is_listed_only_in_goldfish(self):
        self.assertIn("1. [1] Card1", self.call("zone", "P1:library"))
        st = self.read()
        st.pop("goldfish")
        self.write(st)
        with self.assertRaises(SystemExit):
            self.call("zone", "P1:library")
        self.assertNotIn("山札上", self.call("show"))


if __name__ == "__main__":
    unittest.main()
