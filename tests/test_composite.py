"""複合 Operation（pay / cast / push_resolve / land / turn_start / turn_end）。"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import player_view  # noqa: E402
from mtgtable.render import render_view  # noqa: E402
from helpers import find, game, hand, ok  # noqa: E402


class CompositeTest(unittest.TestCase):
    def _lands(self, e, pid, n):
        """テスト用に土地を n 枚戦場へ（type_line が無いので move で置く）。"""
        name = "Forest" if pid == "p1" else "Mountain"
        ids = [c for c in hand(e, pid) if e.state.cards[c].name == name][:n]
        ok(e, pid, {"op": "move", "cards": ids, "to": "battlefield"})
        return ids

    def test_turn_start_and_end(self):
        e = game()
        r = ok(e, "p1", {"op": "turn_start", "to": "main1", "as": "d"})
        t = e.state.turn
        self.assertEqual((t.turn, t.active, t.step), (1, "p1", "main"))
        self.assertEqual(r.results[0]["drawn"], [])  # ゲームの最初のターンは引かない
        ok(e, "p1", {"op": "note_add", "target": "p1", "text": "x", "until": "end_of_turn"},
           {"op": "mana_add", "color": "G"})
        ok(e, "p1", {"op": "turn_end"})
        self.assertEqual(e.state.turn.step, "cleanup")
        self.assertEqual(e.state.notes, {})
        self.assertEqual(e.state.players["p1"].mana_pool.mana, [])
        r = ok(e, "p2", {"op": "turn_start"})
        self.assertEqual((e.state.turn.turn, e.state.turn.active, e.state.turn.step), (2, "p2", "draw"))
        self.assertEqual(len(r.results[0]["drawn"]), 1)
        self.assertEqual(len(r.learned), 1)

    def test_land_writes_mana_note(self):
        e = game()
        forest = find(e, "p1", "hand", "Forest")
        ok(e, "p1", {"op": "turn_start", "to": "main1"}, {"op": "land", "card": forest, "mana": "{G}"})
        self.assertIn("[#n1 mana: {G}]", render_view(player_view(e.state, "p1")))

    def test_cast_pays_and_resolves(self):
        e = game()
        f1, f2 = self._lands(e, "p1", 2)
        bear = find(e, "p1", "hand", "Grizzly Bears")
        e.state.cards[bear].type_line = "Creature — Bear"
        ok(e, "p1", {"op": "cast", "card": bear, "pay": {f1: "G", f2: "G"}})
        self.assertEqual(e.state.cards[bear].zone, "battlefield")
        self.assertTrue(e.state.cards[f1].tapped and e.state.cards[f2].tapped)
        self.assertEqual(e.state.players["p1"].mana_pool.mana, [])
        self.assertEqual(e.state.stack.items, [])

    def test_cast_instant_with_then_and_pool_and_life(self):
        e = game()
        (m,) = self._lands(e, "p2", 1)
        bolt = find(e, "p2", "hand", "Lightning Bolt")
        e.state.cards[bolt].type_line = "Instant"
        ok(e, "p2", {"op": "mana_add", "color": "R"})
        ok(e, "p2", {"op": "cast", "card": bolt, "pay": {"pool": "R", "life": 1}, "targets": ["p1"],
                     "then": [{"op": "life", "player": "p1", "amount": -3}]})
        self.assertEqual(e.state.cards[bolt].zone, "p2.graveyard")
        self.assertEqual((e.state.players["p1"].life, e.state.players["p2"].life), (17, 19))
        self.assertFalse(e.state.cards[m].tapped)
        self.assertEqual(e.state.links, {})

    def test_cast_without_resolving(self):
        e = game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        r = ok(e, "p1", {"op": "cast", "card": bear, "resolve": False, "as": "b"})
        self.assertEqual(e.state.stack.items[0].card, bear)
        self.assertEqual(r.aliases["b"], [bear])

    def test_cast_needs_to_when_type_unknown(self):
        e = game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        e.state.cards[bear].type_line = ""
        self.assertEqual(e.apply_group("p1", {"ops": [{"op": "cast", "card": bear}]}).status, "failed")
        ok(e, "p1", {"op": "cast", "card": bear, "to": "battlefield"})

    def test_push_resolve_with_cost_and_restricted_mana(self):
        e = game()
        (f,) = self._lands(e, "p1", 1)
        bear = find(e, "p1", "hand", "Grizzly Bears")
        ok(e, "p1", {"op": "move", "card": bear, "to": "battlefield"})
        # 能力にしか使えない 2 マナを出し、その場で使う
        ok(e, "p1", {"op": "push_resolve", "kind": "activated", "source": bear, "text": "+1/+1 counter",
                     "pay": {f: {"color": "G", "amount": 2, "note": "abilities only"}},
                     "then": [{"op": "counter_add", "target": bear, "kind": "+1/+1"}]})
        self.assertEqual(e.state.counters_on(bear), {"+1/+1": 1})
        self.assertEqual(e.state.players["p1"].mana_pool.mana, [])
        self.assertEqual(e.state.notes, {})
        # 誘発（既定）: 積んで解決し、as はスタックの項目
        r = ok(e, "p1", {"op": "push_resolve", "source": bear, "text": "ETB", "as": "t",
                         "then": [{"op": "draw"}]})
        self.assertEqual(e.state.stack.items, [])
        self.assertRegex(r.aliases["t"][0], r"^#s\d+$")
        self.assertIn("stack push %s triggered" % r.aliases["t"][0], " ".join(r.events))

    def test_failed_composite_rolls_back(self):
        e = game()
        (f,) = self._lands(e, "p1", 1)
        bear = find(e, "p1", "hand", "Grizzly Bears")
        before = e.state.version
        r = e.apply_group("p1", {"ops": [{"op": "cast", "card": bear, "to": "battlefield",
                                          "pay": {f: "G", "pool": "U"}}]})
        self.assertEqual(r.status, "failed")
        self.assertEqual(e.state.version, before)
        self.assertFalse(e.state.cards[f].tapped)


if __name__ == "__main__":
    unittest.main()
