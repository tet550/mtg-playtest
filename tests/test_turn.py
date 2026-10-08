"""ターン・ステップ・ゲーム前・宣言・優先権のパス。"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import player_view  # noqa: E402
from mtgtable.render import render_view  # noqa: E402
from helpers import find, game, hand, ok  # noqa: E402


class TurnTest(unittest.TestCase):
    def test_turn_structure(self):
        e = game()
        ok(e, None, {"op": "step", "to": "main1"})
        self.assertEqual((e.state.turn.phase, e.state.turn.step, e.state.turn.priority),
                         ("main1", "main", "p1"))
        ok(e, None, {"op": "step", "to": "declare_attackers"}, {"op": "step", "to": "main2"})
        self.assertEqual((e.state.turn.turn, e.state.turn.phase), (1, "main2"))
        # 次のターンへはクリンナップから（途中から前のステップへ戻ると、アンタップ・ドローを飛ばしてしまう）
        self.assertEqual(e.apply_act(None, {"act": [{"op": "step", "to": "upkeep"}]}).status, "failed")
        # クリンナップから今より前のステップを指定すると次のターン
        ok(e, None, {"op": "step", "to": "cleanup"}, {"op": "step", "to": "untap"})
        self.assertEqual((e.state.turn.turn, e.state.turn.active, e.state.turn.step), (2, "p2", "untap"))
        self.assertIsNone(e.state.turn.priority)

    def test_step_requires_a_name(self):
        e = game()
        ok(e, None, {"op": "step", "to": "untap"})
        for bad in ({"op": "step"}, {"op": "step", "to": "main"}, {"op": "step", "to": "untap"},
                    {"op": "step", "to": "second_main"}):
            self.assertEqual(e.apply_act(None, {"act": [bad]}).status, "failed", bad)

    def test_declarations(self):
        e = game()
        ok(e, "p1", {"op": "declare", "kind": "pass"})
        self.assertEqual(player_view(e.state, "p2")["declarations"][0]["kind"], "pass")
        ok(e, "p2", {"op": "declare", "kind": "concede"})
        self.assertEqual(e.state.players["p2"].status, "conceded")


class PregameTest(unittest.TestCase):
    def test_game_starts_in_pregame(self):
        e = game(first="p2")
        t = e.state.turn
        self.assertEqual((t.turn, t.phase, t.step, t.active, t.priority), (0, "pregame", "pregame", "p2", None))
        self.assertIn("Pregame  first p2", render_view(player_view(e.state, "p1")))
        ok(e, None, {"op": "step", "to": "untap"})
        t = e.state.turn
        self.assertEqual((t.turn, t.phase, t.step, t.active, t.priority), (1, "beginning", "untap", "p2", None))
        # T1 からは通常どおり（クリンナップの後の untap で次のターン）
        ok(e, None, {"op": "step", "to": "cleanup"}, {"op": "step", "to": "untap"})
        self.assertEqual((e.state.turn.turn, e.state.turn.active), (2, "p1"))

    def test_opening_hand_permanent_is_not_new_on_turn_1(self):
        from mtgtable.info import is_new
        e = game()
        # 開始時の手札から戦場へ（Leyline など）はゲーム前
        bear = find(e, "p1", "hand", "Grizzly Bears")
        ok(e, "p1", {"op": "declare", "kind": "keep"}, {"op": "move", "card": bear, "to": "battlefield"})
        self.assertTrue(is_new(e.state, e.state.cards[bear]))
        ok(e, None, {"op": "step", "to": "untap"})
        self.assertFalse(is_new(e.state, e.state.cards[bear]))

    def test_pregame_to_any_step(self):
        e = game()
        ok(e, None, {"op": "step", "to": "main1"})
        self.assertEqual((e.state.turn.turn, e.state.turn.step, e.state.turn.priority), (1, "main", "p1"))


class PriorityTest(unittest.TestCase):
    def test_batch_does_not_stop_on_priority(self):
        # 優先権ではエンジンは止めない（相手の応答は AI がパスの宣言を求めて確かめる）
        e = game()
        cid = hand(e, "p1")[0]
        res = e.apply_batch({"actor": "p1", "acts": [
            {"act": [{"op": "stack_push", "card": cid, "as": "spell"}, {"op": "pass"}]},
            {"act": [{"op": "stack_remove", "item": "$spell", "card_to": "graveyard"}]},
        ]})
        self.assertIsNone(res["stopped"])
        self.assertEqual(e.state.cards[cid].zone, "p1.graveyard")

    def test_standing_pass_lets_batch_continue(self):
        e = game()
        ok(e, "p2", {"op": "pass", "until": "turn"})
        a, b = hand(e, "p1")[:2]
        res = e.apply_batch({"actor": "p1", "acts": [
            {"act": [{"op": "stack_push", "card": a, "as": "x"}, {"op": "pass"}]},
            {"act": [{"op": "stack_remove", "item": "$x", "card_to": "graveyard"}]},
            {"act": [{"op": "stack_push", "card": b, "as": "y"}, {"op": "pass"}]},
            {"act": [{"op": "stack_remove", "item": "$y", "card_to": "graveyard"}]},
        ]})
        self.assertIsNone(res["stopped"])
        self.assertEqual(res["acts"][0].results[1]["auto_passed"], ["p2"])
        self.assertTrue(res["acts"][0].results[1]["all_passed"])
        self.assertEqual(e.state.turn.priority, "p1")  # 解決後はアクティブ・プレイヤー
        kinds = [(d.player, d.text) for d in e.state.declarations if d.kind == "pass"]
        self.assertIn(("p2", "standing: until turn"), kinds)

    def test_standing_pass_expires(self):
        e = game()
        ok(e, "p2", {"op": "pass", "until": "step"})
        ok(e, None, {"op": "step", "to": "upkeep"})
        self.assertEqual(e.state.standing_passes, {})
        ok(e, "p2", {"op": "pass", "until": "stack"})
        cid = hand(e, "p1")[0]
        ok(e, "p1", {"op": "stack_push", "card": cid})
        self.assertIn("p2", e.state.standing_passes)
        ok(e, "p1", {"op": "stack_remove", "card_to": "graveyard"})
        self.assertEqual(e.state.standing_passes, {})
        ok(e, "p2", {"op": "pass", "until": "turn"}, {"op": "hold"})
        self.assertEqual(e.state.standing_passes, {})


if __name__ == "__main__":
    unittest.main()
