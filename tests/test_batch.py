"""Act・Batch・エイリアス・actor・代理の宣言。"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import OperationError  # noqa: E402
from helpers import find, game, hand, ok, store  # noqa: E402


class ActAndBatchTest(unittest.TestCase):
    def test_act_is_atomic(self):
        e = game()
        before = e.state.to_dict()
        r = e.apply_act("p1", {"act": [{"op": "draw"}, {"op": "move", "card": "nope", "to": "exile"}]})
        self.assertEqual(r.status, "failed")
        self.assertEqual(e.state.to_dict(), before)

    def test_unknown_draw_does_not_stop_batch(self):
        # 知らないカードを見ても止めない（区切るのは操作する側の責任）。知ったカードは learned で返る
        e = game()
        res = e.apply_batch({"actor": "p1", "acts": [
            {"act": [{"op": "draw"}]},
            {"act": [{"op": "move", "card": {"zone": "hand", "index": 0}, "to": "graveyard"}]},
        ]})
        self.assertEqual(res["applied"], 2)
        self.assertIsNone(res["stopped"])
        self.assertEqual(len(res["acts"][0].learned), 1)
        self.assertEqual(len(hand(e, "p1")), 7)

    def test_batch_continues_when_top_is_known(self):
        e = game()
        ok(e, "p1", {"op": "look", "cards": {"zone": "library", "top": 1}})
        top = e.state.zones["p1.library"].cards[0]
        res = e.apply_batch({"actor": "p1", "acts": [
            {"pre": [{"zone": "library", "top": [top]}], "act": [{"op": "draw"}]},
            {"act": [{"op": "move", "card": top, "to": "graveyard"}]},
        ]})
        self.assertEqual(res["applied"], 2)
        self.assertIsNone(res["stopped"])
        self.assertEqual(e.state.cards[top].zone, "p1.graveyard")

    def test_precondition_failure_stops_batch(self):
        e = game()
        res = e.apply_batch({"actor": "p1", "acts": [
            {"pre": [{"version": 99}], "act": [{"op": "draw"}]},
            {"act": [{"op": "draw"}]},
        ]})
        self.assertEqual(res["applied"], 0)
        self.assertEqual(res["stopped"]["reason"], "precondition_failed")
        self.assertEqual([g.status for g in res["acts"]], ["precondition_failed", "skipped"])

    def test_precondition_cannot_probe_hidden_cards(self):
        e = game()
        secret = e.state.zones["p2.library"].cards[0]
        r = e.apply_act("p1", {"pre": [{"zone": "p2.library", "top": [secret]}],
                                 "act": [{"op": "draw"}]})
        self.assertEqual(r.status, "precondition_failed")
        self.assertIn("not known", r.error)


class AliasTest(unittest.TestCase):
    def test_alias_spans_batch_and_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = store(tmp, "g")
            st.create(game().state)
            r = st.apply({"actor": "p1", "acts": [
                {"act": [{"op": "create", "name": "Clue", "as": "clue"}]},
                {"act": [{"op": "counter_add", "target": "$clue", "kind": "x"}]},
                {"act": [{"op": "remove", "card": "$clue"}]},
            ]})
            self.assertEqual(r["applied"], 3)
            self.assertEqual(st.replay().to_dict(), st.load().to_dict())
            self.assertEqual(st.read_log()[1]["steps"][0]["op"]["target"], "#t1")  # エイリアスは id で残る


class MultiActorBatchTest(unittest.TestCase):
    def test_both_players_in_one_batch(self):
        e = game(policies={"p1": "own_library", "p2": "own_library"})
        res = e.apply_batch({"acts": [
            {"actor": "p1", "act": [{"op": "step", "to": "main1"}, {"op": "step", "to": "cleanup"}]},
            {"actor": "p2", "act": [{"op": "step", "to": "untap"}, {"op": "untap_all"},
                                    {"op": "step", "to": "draw"}, {"op": "draw"}]},
            {"actor": "p2", "act": [{"op": "step", "to": "main1"}]},
        ]})
        self.assertIsNone(res["stopped"])  # 自分のライブラリーの順を知っているので、ドローで止まらない
        self.assertEqual([g.actor for g in res["acts"]], ["p1", "p2", "p2"])
        self.assertEqual((e.state.turn.turn, e.state.turn.active), (2, "p2"))

    def test_unknown_draws_do_not_stop(self):
        e = game()
        res = e.apply_batch({"acts": [
            {"actor": "p2", "act": [{"op": "draw"}]},
            {"actor": "p1", "act": [{"op": "draw"}]},
        ]})
        self.assertIsNone(res["stopped"])
        self.assertEqual([len(g.learned) for g in res["acts"]], [1, 1])

    def test_acts_of_both_players_run_through(self):
        e = game()
        cid = hand(e, "p1")[0]
        res = e.apply_batch({"acts": [
            {"actor": "p1", "act": [{"op": "stack_push", "card": cid, "as": "x"}, {"op": "pass"}]},
            {"actor": "p2", "act": [{"op": "pass"}]},
            {"actor": "p1", "act": [{"op": "stack_remove", "item": "$x", "card_to": "graveyard"}]},
        ]})
        self.assertIsNone(res["stopped"])
        # 相手（p2）が優先権を持っていても、次の Act が p1 でも止まらない
        res = e.apply_batch({"acts": [
            {"actor": "p1", "act": [{"op": "stack_push", "card": hand(e, "p1")[0]}, {"op": "pass"}]},
            {"actor": "p2", "act": [{"op": "declare", "kind": "concede"}]},
        ]})
        self.assertIsNone(res["stopped"])

    def test_act_actor_needs_judge_batch(self):
        e = game()
        with self.assertRaises(OperationError):
            e.apply_batch({"actor": "p1", "acts": [{"actor": "p2", "act": [{"op": "draw"}]}]})

    def test_proxy_act_declares_for_other_player(self):
        e = game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        opp = hand(e, "p2")[0]
        res = e.apply_batch({"actor": "p1", "acts": [
            {"act": [{"op": "move", "card": bear, "to": "battlefield"},
                     {"op": "attack", "attacker": bear, "target": "p2", "tap": True}]},
            {"proxy": "p2", "label": "no block", "act": [{"op": "declare", "kind": "no_block"},
                                                        {"op": "pass"}]},
            {"act": [{"op": "damage", "target": "p2", "amount": 2, "source": bear}]},
        ]})
        self.assertIsNone(res["stopped"])
        g = res["acts"][1]
        self.assertEqual((g.actor, g.proxy_by), ("p2", "p1"))
        self.assertEqual(g.public()["proxy_by"], "p1")
        self.assertEqual(e.state.players["p2"].life, 18)
        # 代理の結果は操作者（p1）から見た形: p2 の手札の id は出さない
        res = e.apply_batch({"actor": "p1", "acts": [
            {"proxy": "p2", "act": [{"op": "draw", "as": "d"}]}]})
        g = res["acts"][0]
        self.assertEqual(g.learned, [])
        self.assertEqual(g.aliases["d"], ["hidden"])
        self.assertNotIn(opp, str(g.public()))

    def test_proxy_needs_player_batch(self):
        e = game()
        with self.assertRaises(OperationError):
            e.apply_batch({"acts": [{"proxy": "p2", "act": [{"op": "pass"}]}]})
        with self.assertRaises(OperationError):
            e.apply_batch({"actor": "p1", "acts": [{"proxy": "p1", "act": [{"op": "pass"}]}]})


if __name__ == "__main__":
    unittest.main()
