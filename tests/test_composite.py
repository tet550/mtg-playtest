"""複合 op（pay / cast / land）と手順（turn_start / turn_end）。"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import GameStore, OperationError, player_view  # noqa: E402
from mtgtable.render import render_view  # noqa: E402
from helpers import find, game, hand, ok, run  # noqa: E402


class CompositeTest(unittest.TestCase):
    def _lands(self, e, pid, n):
        """テスト用に土地を n 枚戦場へ（type_line が無いので move で置く）。"""
        name = "Forest" if pid == "p1" else "Mountain"
        ids = [c for c in hand(e, pid) if e.state.cards[c].name == name][:n]
        ok(e, pid, {"op": "move", "cards": ids, "to": "battlefield"})
        return ids

    def test_land_writes_mana_note(self):
        e = game()
        forest = find(e, "p1", "hand", "Forest")
        run(e, "p1", {"proc": "turn_start", "to": "main1"},
            {"act": [{"op": "land", "card": forest, "mana": "{G}"}]})
        self.assertIn("[#n1 mana: {G}]", render_view(player_view(e.state, "p1")))

    def test_cast_then_resolve(self):
        e = game()
        f1, f2 = self._lands(e, "p1", 2)
        bear = find(e, "p1", "hand", "Grizzly Bears")
        e.state.cards[bear].type_line = "Creature — Bear"
        r = ok(e, "p1", {"op": "cast", "card": bear, "pay": {f1: "G", f2: "G"}, "as": "b"})
        self.assertEqual(e.state.stack.items[0].card, bear)
        self.assertEqual(r.results[0], {"card": bear, "item": e.state.stack.items[0].id})
        self.assertEqual(r.aliases["b"], [bear])
        self.assertTrue(e.state.cards[f1].tapped and e.state.cards[f2].tapped)
        self.assertEqual(e.state.players["p1"].mana_pool.mana, [])
        # 解決: 一番上を取り除くと、行き先はタイプ行から（パーマネントは戦場）
        ok(e, "p1", {"op": "stack_remove"})
        self.assertEqual(e.state.cards[bear].zone, "battlefield")
        self.assertEqual(e.state.stack.items, [])

    def test_cast_moves_to_stack_before_paying(self):
        # 601.2: スタックへ移して対象を選んでから払う（唱えている呪文はコストの候補にならない）
        e = game()
        bolt = find(e, "p2", "hand", "Lightning Bolt")
        r = ok(e, "p2", {"op": "cast", "card": bolt, "targets": ["p1"],
                         "cost": [{"op": "move", "card": {"zone": "hand", "index": 0}, "to": "graveyard"}]})
        self.assertEqual(e.state.cards[bolt].zone, "stack")
        self.assertEqual([ev.split()[0] for ev in r.events], ["move", "stack", "link", "move"])
        self.assertEqual(len(e.state.zones["p2.graveyard"].cards), 1)
        self.assertNotIn(bolt, e.state.zones["p2.graveyard"].cards)

    def test_resolution_act_with_effects_and_pool_and_life(self):
        e = game()
        (m,) = self._lands(e, "p2", 1)
        bolt = find(e, "p2", "hand", "Lightning Bolt")
        e.state.cards[bolt].type_line = "Instant"
        ok(e, "p2", {"op": "mana_add", "color": "R"})
        run(e, "p2", {"act": [{"op": "cast", "card": bolt, "pay": {"pool": "R", "life": 1}, "targets": ["p1"]}]},
            {"act": [{"op": "damage", "target": "p1", "amount": 3, "source": bolt}, {"op": "stack_remove"}]})
        self.assertEqual(e.state.cards[bolt].zone, "p2.graveyard")
        self.assertEqual((e.state.players["p1"].life, e.state.players["p2"].life), (17, 19))
        self.assertFalse(e.state.cards[m].tapped)
        self.assertEqual(e.state.links, {})

    def test_counter_removes_the_stack_item(self):
        e = game()
        a, b = [find(e, pid, "hand", "Lightning Bolt" if pid == "p2" else "Giant Growth") for pid in ("p2", "p1")]
        e.state.cards[a].type_line = e.state.cards[b].type_line = "Instant"
        res = run(e, None,
                  {"actor": "p2", "act": [{"op": "cast", "card": a, "targets": ["p1"]}]},
                  {"actor": "p1", "act": [{"op": "cast", "card": b, "targets": ["#s1"]}]},
                  {"actor": "p1", "act": [{"op": "stack_remove", "item": "#s1", "card_to": "graveyard"},
                                          {"op": "stack_remove"}]})
        self.assertEqual(len(res["acts"]), 3)
        self.assertEqual((e.state.cards[a].zone, e.state.cards[b].zone), ("p2.graveyard", "p1.graveyard"))
        self.assertEqual(e.state.players["p1"].life, 20)

    def test_stack_remove_needs_card_to_when_type_unknown(self):
        e = game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        e.state.cards[bear].type_line = ""
        ok(e, "p1", {"op": "cast", "card": bear})
        self.assertEqual(e.apply_act("p1", {"act": [{"op": "stack_remove"}]}).status, "failed")
        ok(e, "p1", {"op": "stack_remove", "card_to": "battlefield"})

    def test_ability_push_and_resolve_with_restricted_mana(self):
        e = game()
        (f,) = self._lands(e, "p1", 1)
        bear = find(e, "p1", "hand", "Grizzly Bears")
        ok(e, "p1", {"op": "move", "card": bear, "to": "battlefield"})
        # 能力にしか使えない 2 マナを出し、その場で使う
        run(e, "p1", {"act": [{"op": "pay", f: {"color": "G", "amount": 2, "note": "abilities only"}},
                              {"op": "stack_push", "kind": "activated", "source": bear, "text": "+1/+1 counter"}]},
            {"act": [{"op": "counter_add", "target": bear, "kind": "+1/+1"}, {"op": "stack_remove"}]})
        self.assertEqual(e.state.counters_on(bear), {"+1/+1": 1})
        self.assertEqual(e.state.players["p1"].mana_pool.mana, [])
        self.assertEqual(e.state.notes, {})
        self.assertEqual(e.state.stack.items, [])

    def test_failed_cast_rolls_back(self):
        e = game()
        (f,) = self._lands(e, "p1", 1)
        bear = find(e, "p1", "hand", "Grizzly Bears")
        before = e.state.version
        r = e.apply_act("p1", {"act": [{"op": "cast", "card": bear, "pay": {f: "G", "pool": "U"}}]})
        self.assertEqual(r.status, "failed")
        self.assertEqual(e.state.version, before)
        self.assertFalse(e.state.cards[f].tapped)
        self.assertEqual(e.state.cards[bear].zone, "p1.hand")


class ProcedureTest(unittest.TestCase):
    def test_turn_start_and_end(self):
        e = game()
        res = run(e, "p1", {"proc": "turn_start", "to": "main1", "as": "d"})
        t = e.state.turn
        self.assertEqual((t.turn, t.active, t.step), (1, "p1", "main"))
        # アンタップ / アップキープ / ドロー・ステップ / メインへ、の4つの Act
        self.assertEqual([a.proc for a in res["acts"]], ["turn_start to=main1 %d/4" % i for i in range(1, 5)])
        self.assertEqual(res["acts"][2].aliases["d"], [])  # ゲームの最初のターンは引かない
        ok(e, "p1", {"op": "note_add", "target": "p1", "text": "x", "until": "end_of_turn"},
           {"op": "mana_add", "color": "G"})
        res = run(e, "p1", {"proc": "turn_end"})
        self.assertEqual(len(res["acts"]), 3)
        self.assertEqual(e.state.turn.step, "cleanup")
        self.assertEqual(e.state.notes, {})
        self.assertEqual(e.state.players["p1"].mana_pool.mana, [])
        res = run(e, "p2", {"proc": "turn_start", "as": "d"})
        self.assertEqual((e.state.turn.turn, e.state.turn.active, e.state.turn.step), (2, "p2", "draw"))
        self.assertEqual(len(res["acts"]), 3)
        self.assertEqual(len(res["acts"][2].aliases["d"]), 1)
        self.assertEqual(len(res["acts"][2].learned), 1)

    def test_upkeep_end_cleanup_are_acts(self):
        e = game()
        run(e, "p1", {"proc": "turn_start", "to": "main1"})
        discard = hand(e, "p1")[0]
        res = run(e, "p1", {"proc": "turn_end", "end": [{"act": [{"op": "life_loss", "amount": 1}]}],
                            "cleanup": [{"act": [{"op": "move", "card": discard, "to": "graveyard"}]}]})
        self.assertEqual(len(res["acts"]), 5)
        self.assertEqual(e.state.players["p1"].life, 19)
        res = run(e, "p2", {"proc": "turn_start", "upkeep": [
            {"act": [{"op": "stack_push", "kind": "triggered", "text": "upkeep"}]},
            {"act": [{"op": "life_loss", "amount": 2}, {"op": "stack_remove"}]}]})
        self.assertEqual(len(res["acts"]), 5)
        self.assertEqual(e.state.players["p2"].life, 18)

    def test_turn_start_can_stop_at_upkeep(self):
        # アップキープで止める: ドローはまだ。続きは step draw から（次のターンへ飛ばない）
        e = game()
        run(e, "p1", {"proc": "turn_start", "to": "main1"})
        run(e, "p1", {"proc": "turn_end"})
        hand_before = len(e.state.zones["p2.hand"].cards)
        res = run(e, "p2", {"proc": "turn_start", "to": "upkeep"})
        t = e.state.turn
        self.assertEqual((len(res["acts"]), t.turn, t.active, t.step), (2, 2, "p2", "upkeep"))
        self.assertEqual(len(e.state.zones["p2.hand"].cards), hand_before)
        ok(e, "p2", {"op": "step", "to": "draw"}, {"op": "draw"})
        self.assertEqual((e.state.turn.turn, e.state.turn.active), (2, "p2"))

    def test_turn_end_from_the_end_step(self):
        # 終了ステップで相手の呪文を解決した後など、既に end にいるならステップの開始を省く
        e = game()
        run(e, "p1", {"proc": "turn_start", "to": "main1"})
        ok(e, "p1", {"op": "step", "to": "end"})
        res = run(e, "p1", {"proc": "turn_end"})
        self.assertEqual(len(res["acts"]), 2)
        self.assertEqual(e.state.turn.step, "cleanup")
        res = run(e, "p1", {"proc": "turn_end"})  # クリンナップからは片付けだけ
        self.assertEqual((len(res["acts"]), e.state.turn.step), (1, "cleanup"))

    def test_inner_act_keeps_its_own_actor(self):
        # 相手の終了ステップに、もう一方の Player がインスタントを唱える
        e = game()
        run(e, "p1", {"proc": "turn_start", "to": "main1"})
        bolt = find(e, "p2", "hand", "Lightning Bolt")
        e.state.cards[bolt].type_line = "Instant"
        res = run(e, None, {"actor": "p1", "proc": "turn_end", "end": [
            {"actor": "p2", "act": [{"op": "cast", "card": bolt, "targets": ["p1"]}]},
            {"actor": "p2", "act": [{"op": "damage", "target": "p1", "amount": 3, "source": bolt},
                                    {"op": "stack_remove"}]}]})
        self.assertEqual([a.actor for a in res["acts"]], ["p1", "p2", "p2", "p1", "p1"])
        self.assertEqual(e.state.players["p1"].life, 17)
        # Player の Batch の中で actor を書くのは、最上位と同じくエラー
        res = e.apply_batch({"actor": "p2", "acts": [{"proc": "turn_start", "upkeep": [
            {"actor": "p1", "act": [{"op": "draw"}]}]}]})
        self.assertEqual(res["stopped"]["reason"], "failed")

    def test_failure_keeps_earlier_acts(self):
        e = game()
        res = e.apply_batch({"actor": "p1", "acts": [
            {"proc": "turn_start", "to": "main1", "upkeep": [{"act": [{"op": "move", "card": "nope", "to": "exile"}]}]},
            {"act": [{"op": "draw"}]}]})
        self.assertEqual([a.status for a in res["acts"]], ["applied", "applied", "failed", "skipped", "skipped", "skipped"])
        self.assertEqual(e.state.turn.step, "upkeep")

    def test_entries_need_act_or_proc(self):
        e = game()
        for bad in ({"op": "draw"}, [{"op": "draw"}],
                    {"act": [{"op": "draw"}], "proc": "turn_end"}):
            with self.assertRaises(OperationError):
                e.apply_batch({"actor": "p1", "acts": [bad]})
        with self.assertRaises(OperationError):
            e.apply_batch({"actor": "p1", "act": [{"op": "draw"}]})
        self.assertEqual(e.apply_batch({"actor": "p1", "acts": [{"proc": "nope"}]})["stopped"]["reason"], "failed")

    def test_log_keeps_primitive_steps_and_replays_from_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = GameStore(pathlib.Path(tmp) / "g")
            e = game()
            forest = find(e, "p1", "hand", "Forest")
            bear = find(e, "p1", "hand", "Grizzly Bears")
            e.state.cards[bear].type_line = "Creature — Bear"
            st.create(e.state)
            st.apply({"actor": "p1", "acts": [
                {"proc": "turn_start", "to": "main1"},
                {"act": [{"op": "land", "card": forest, "mana": "{G}", "as": "f"}]},
                {"act": [{"op": "cast", "card": bear, "pay": {forest: "G"}, "as": "b"}]},
                {"act": [{"op": "counter_add", "target": "$b", "kind": "+1/+1"}, {"op": "stack_remove"}]}]})
            log = st.read_log()
            steps = [s for x in log for s in x["steps"]]
            self.assertFalse({s["op"]["op"] for s in steps} & {"pay", "cast", "land"})
            self.assertNotIn("$", json.dumps([s["op"] for s in steps]))
            cast = log[5]["steps"]
            self.assertEqual([s["op"]["op"] for s in cast], ["stack_push", "tap", "mana_add", "mana_spend"])
            self.assertEqual({s["parent"] for s in cast}, {"cast"})
            self.assertEqual(log[6]["steps"][0]["op"]["target"], bear)
            self.assertIn("events", cast[0])
            # Replay は steps だけを使う（AI が書いた act は使わない）
            for x in log:
                x["act"] = [{"op": "nope"}]
            st._write_log(log)
            self.assertEqual(st.replay().to_dict(), st.load().to_dict())

    def test_procedures_are_logged_per_act_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = GameStore(pathlib.Path(tmp) / "g")
            e = game()
            bear = find(e, "p1", "hand", "Grizzly Bears")
            e.state.cards[bear].type_line = "Creature — Bear"
            st.create(e.state)
            st.apply({"actor": "p1", "label": "T1", "acts": [
                {"proc": "turn_start", "to": "main1"},
                {"act": [{"op": "cast", "card": bear}]},
                {"act": [{"op": "stack_remove"}]}]})
            st.apply({"actor": "p1", "acts": [{"proc": "turn_end"}]})
            log = st.read_log()
            self.assertEqual(len(log), 9)
            self.assertEqual([x["batch"] for x in log], [1] * 6 + [2] * 3)
            self.assertEqual(log[0]["batch_label"], "T1")
            self.assertEqual(log[0]["proc"], "turn_start to=main1 1/4")
            self.assertNotIn("proc", log[4])
            self.assertEqual(st.replay().to_dict(), st.load().to_dict())
            st.undo(to=5)  # 唱えた後・解決の前
            self.assertEqual(st.load().stack.items[0].card, bear)
            st.apply({"actor": "p1", "acts": [{"act": [{"op": "draw"}]}]})
            self.assertEqual(st.read_log()[-1]["batch"], 2)


if __name__ == "__main__":
    unittest.main()
