import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import OperationError, player_view  # noqa: E402
from mtgtable.operations import summarize_op  # noqa: E402
from mtgtable.render import render_view  # noqa: E402
from test_engine import find, game, hand, ok  # noqa: E402


class ViewTest(unittest.TestCase):
    def test_attached_objects_are_not_grouped_together(self):
        e = game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        r = ok(e, "p1", {"op": "move", "card": bear, "to": "battlefield"},
               {"op": "create", "name": "Axe", "count": 2, "as": "axes"})
        a1, a2 = r.aliases["axes"]
        text = render_view(player_view(e.state, "p1"))
        self.assertIn("%s %s Axe ×2" % (a1, a2), text)
        ok(e, "p1", {"op": "link_add", "kind": "attached", "source": a1, "targets": bear})
        lines = render_view(player_view(e.state, "p1")).splitlines()
        self.assertTrue(any(l.strip().startswith(a1 + " Axe") and "attached->" + bear in l for l in lines))
        self.assertTrue(any(l.strip().startswith(a2 + " Axe") for l in lines))
        self.assertTrue(any(l.strip().startswith(bear) and "attached<-" + a1 in l for l in lines))

    def test_library_and_graveyard_collapsed_by_default(self):
        e = game()
        ok(e, "p1", {"op": "look", "cards": {"zone": "library", "top": 2}},
           {"op": "move", "card": hand(e, "p1")[0], "to": "graveyard"})
        v = player_view(e.state, "p1")
        self.assertEqual(v["zones"]["p1.library"], {"count": 53, "collapsed": True, "known_positions_count": 2})
        self.assertEqual(v["zones"]["p1.graveyard"], {"count": 1, "collapsed": True})
        text = render_view(v)
        self.assertIn("library (53, known positions: 2)", text)
        self.assertIn("graveyard (1)", text)
        full = player_view(e.state, "p1", library=True, graveyard=True)
        self.assertEqual(len(full["zones"]["p1.library"]["known_positions"]), 2)
        self.assertEqual(len(full["zones"]["p1.graveyard"]["cards"]), 1)


class SicknessAndLandsTest(unittest.TestCase):
    def _game(self):
        e = game()
        for c in e.state.cards.values():
            c.type_line = {"Forest": "Basic Land — Forest", "Grizzly Bears": "Creature — Bear",
                           "Giant Growth": "Instant"}.get(c.name, "")
        return e

    def _bf(self, e, viewer="p1"):
        return {c["id"]: c for c in player_view(e.state, viewer)["zones"]["battlefield"]["cards"]}

    def test_sick_until_controllers_next_turn(self):
        e = self._game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        forest = find(e, "p1", "hand", "Forest")
        ok(e, "p1", {"op": "step", "to": "main1"},
           {"op": "move", "cards": [bear, forest], "to": "battlefield"})
        bf = self._bf(e)
        self.assertTrue(bf[bear]["sick"])
        self.assertTrue(bf[forest]["land"])
        self.assertNotIn("sick", bf[forest])
        ok(e, None, {"op": "step", "to": "untap"})  # p2 のターン: まだ p1 のターンは来ていない
        self.assertTrue(self._bf(e)[bear]["sick"])
        ok(e, None, {"op": "step", "to": "cleanup"}, {"op": "step", "to": "untap"})  # p1 のターン
        self.assertNotIn("sick", self._bf(e)[bear])
        # コントロールが移ると、新しいコントローラーの次のターンまで召喚酔い
        ok(e, None, {"op": "set", "card": bear, "controller": "p2"})
        self.assertTrue(self._bf(e)[bear]["sick"])

    def test_lands_listed_first(self):
        e = self._game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        forest = find(e, "p1", "hand", "Forest")
        ok(e, "p1", {"op": "move", "cards": [bear, forest], "to": "battlefield"})
        lines = render_view(player_view(e.state, "p1")).splitlines()
        i = lines.index("  battlefield (2)")
        self.assertTrue(lines[i + 1].strip().startswith(forest))
        self.assertTrue(lines[i + 2].strip().startswith(bear))


class MoveOrderTest(unittest.TestCase):
    def test_random_order_to_bottom_forgets_positions(self):
        e = game()
        ok(e, "p1", {"op": "look", "cards": {"zone": "library", "top": 3}, "as": "seen"},
           {"op": "move", "cards": "$seen", "to": "library", "position": "bottom", "order": "random"})
        lib = e.state.zones["p1.library"].cards
        seen = set(lib[-3:])
        self.assertEqual(len(seen), 3)
        v = player_view(e.state, "p1", library=True)["zones"]["p1.library"]
        self.assertNotIn("known_positions", v)
        self.assertEqual({c["id"] for c in v["known_unordered"]}, seen)
        bad = e.apply_group("p1", {"ops": [{"op": "move", "card": hand(e, "p1")[0], "to": "exile", "order": "x"}]})
        self.assertEqual(bad.status, "failed")


class LogSummaryTest(unittest.TestCase):
    def test_summarize_op(self):
        self.assertEqual(summarize_op({"op": "declare", "kind": "concede"}), "declare kind=concede")
        self.assertEqual(summarize_op({"op": "player_set", "player": "p2", "status": "won"}),
                         "player_set status=won player=p2")
        self.assertEqual(summarize_op({"op": "move", "card": {"zone": "library", "top": 2}, "to": "graveyard"}),
                         "move library[top=2] to=graveyard")


class MultiActorBatchTest(unittest.TestCase):
    def test_both_players_in_one_batch(self):
        e = game(policies={"p1": "own_library", "p2": "own_library"})
        res = e.apply_batch({"groups": [
            {"actor": "p1", "ops": [{"op": "step", "to": "main1"}, {"op": "step", "to": "cleanup"}]},
            {"actor": "p2", "ops": [{"op": "step", "to": "untap"}, {"op": "untap_all"},
                                    {"op": "step", "to": "draw"}, {"op": "draw"}]},
            {"actor": "p2", "ops": [{"op": "step", "to": "main1"}]},
        ]})
        self.assertIsNone(res["stopped"])  # 自分のライブラリーの順を知っているので、ドローで止まらない
        self.assertEqual([g.actor for g in res["groups"]], ["p1", "p2", "p2"])
        self.assertEqual((e.state.turn.turn, e.state.turn.active), (2, "p2"))

    def test_unknown_draws_do_not_stop(self):
        e = game()
        res = e.apply_batch({"groups": [
            {"actor": "p2", "ops": [{"op": "draw"}]},
            {"actor": "p1", "ops": [{"op": "draw"}]},
        ]})
        self.assertIsNone(res["stopped"])
        self.assertEqual([len(g.learned) for g in res["groups"]], [1, 1])

    def test_groups_of_both_players_run_through(self):
        e = game()
        cid = hand(e, "p1")[0]
        res = e.apply_batch({"groups": [
            {"actor": "p1", "ops": [{"op": "stack_push", "card": cid, "as": "x"}, {"op": "pass"}]},
            {"actor": "p2", "ops": [{"op": "pass"}]},
            {"actor": "p1", "ops": [{"op": "stack_remove", "item": "$x", "card_to": "graveyard"}]},
        ]})
        self.assertIsNone(res["stopped"])
        # 相手（p2）が優先権を持っていても、次のグループが p1 でも止まらない
        res = e.apply_batch({"groups": [
            {"actor": "p1", "ops": [{"op": "stack_push", "card": hand(e, "p1")[0]}, {"op": "pass"}]},
            {"actor": "p2", "ops": [{"op": "declare", "kind": "concede"}]},
        ]})
        self.assertIsNone(res["stopped"])

    def test_group_actor_needs_judge_batch(self):
        e = game()
        with self.assertRaises(OperationError):
            e.apply_batch({"actor": "p1", "groups": [{"actor": "p2", "ops": [{"op": "draw"}]}]})

    def test_proxy_group_declares_for_other_player(self):
        e = game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        opp = hand(e, "p2")[0]
        res = e.apply_batch({"actor": "p1", "groups": [
            {"ops": [{"op": "move", "card": bear, "to": "battlefield"},
                     {"op": "attack", "attacker": bear, "target": "p2", "tap": True}]},
            {"proxy": "p2", "label": "no block", "ops": [{"op": "declare", "kind": "no_block"},
                                                        {"op": "pass"}]},
            {"ops": [{"op": "life", "player": "p2", "amount": -2}]},
        ]})
        self.assertIsNone(res["stopped"])
        g = res["groups"][1]
        self.assertEqual((g.actor, g.proxy_by), ("p2", "p1"))
        self.assertEqual(g.public()["proxy_by"], "p1")
        self.assertEqual(e.state.players["p2"].life, 18)
        # 代理の結果は操作者（p1）から見た形: p2 の手札の id は出さない
        res = e.apply_batch({"actor": "p1", "groups": [
            {"proxy": "p2", "ops": [{"op": "draw", "as": "d"}]}]})
        g = res["groups"][0]
        self.assertEqual(g.learned, [])
        self.assertEqual(g.aliases["d"], ["hidden"])
        self.assertNotIn(opp, str(g.public()))

    def test_proxy_needs_player_batch(self):
        e = game()
        with self.assertRaises(OperationError):
            e.apply_batch({"groups": [{"proxy": "p2", "ops": [{"op": "pass"}]}]})
        with self.assertRaises(OperationError):
            e.apply_batch({"actor": "p1", "groups": [{"proxy": "p1", "ops": [{"op": "pass"}]}]})


if __name__ == "__main__":
    unittest.main()
