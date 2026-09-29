import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import GameStore, player_view  # noqa: E402
from mtgtable.render import id_ranges, render_view, strip_names  # noqa: E402
from test_engine import find, game, hand, ok  # noqa: E402


class IdTest(unittest.TestCase):
    def test_ids_have_hash_and_input_may_omit_it(self):
        e = game()
        cid = hand(e, "p1")[0]
        self.assertTrue(cid.startswith("#c"))
        ok(e, "p1", {"op": "move", "card": cid[1:], "to": "battlefield"},
           {"op": "counter_add", "target": cid[1:], "kind": "+1/+1"})
        self.assertEqual(e.state.counters_on(cid), {"+1/+1": 1})
        r = e.apply_group("p1", {"pre": [{"card": cid[1:], "zone": "battlefield"}], "ops": [{"op": "draw"}]})
        self.assertEqual(r.status, "applied")

    def test_id_ranges(self):
        self.assertEqual(id_ranges(["#t5", "#t6", "#t7", "#c2", "#t9", "#t10"]), "#t5..#t7 #c2 #t9 #t10")


class ViewOptionTest(unittest.TestCase):
    def test_sideboard_only_on_request(self):
        e = game()
        self.assertNotIn("p1.sideboard", player_view(e.state, "p1")["zones"])
        self.assertIn("p1.sideboard", player_view(e.state, "p1", sideboard=True)["zones"])

    def test_no_names(self):
        e = game()
        v = player_view(e.state, "p1", names=False)
        self.assertTrue(all("name" not in c for c in v["zones"]["p1.hand"]["cards"]))
        text = render_view(v)
        self.assertNotIn("Forest", text)
        self.assertNotIn("×", text)  # 名前が分からないカード同士はまとめない
        self.assertEqual(strip_names("move #c3(Forest): a -> b; #t2(Clue, Big) x"), "move #c3: a -> b; #t2 x")
        self.assertEqual(strip_names("move #c3 <Forest>: a -> b; #t2 <Clue, Big> x"), "move #c3: a -> b; #t2 x")


class PriorityTest(unittest.TestCase):
    def test_batch_does_not_stop_on_priority(self):
        # 優先権ではエンジンは止めない（相手の応答は AI がパスの宣言を求めて確かめる）
        e = game()
        cid = hand(e, "p1")[0]
        res = e.apply_batch({"actor": "p1", "groups": [
            {"ops": [{"op": "stack_push", "card": cid, "as": "spell"}, {"op": "pass"}]},
            {"ops": [{"op": "stack_remove", "item": "$spell", "card_to": "graveyard"}]},
        ]})
        self.assertIsNone(res["stopped"])
        self.assertEqual(e.state.cards[cid].zone, "p1.graveyard")

    def test_standing_pass_lets_batch_continue(self):
        e = game()
        ok(e, "p2", {"op": "pass", "until": "turn"})
        a, b = hand(e, "p1")[:2]
        res = e.apply_batch({"actor": "p1", "groups": [
            {"ops": [{"op": "stack_push", "card": a, "as": "x"}, {"op": "pass"}]},
            {"ops": [{"op": "stack_remove", "item": "$x", "card_to": "graveyard"}]},
            {"ops": [{"op": "stack_push", "card": b, "as": "y"}, {"op": "pass"}]},
            {"ops": [{"op": "stack_remove", "item": "$y", "card_to": "graveyard"}]},
        ]})
        self.assertIsNone(res["stopped"])
        self.assertEqual(res["groups"][0].results[1]["auto_passed"], ["p2"])
        self.assertTrue(res["groups"][0].results[1]["all_passed"])
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


class AliasTest(unittest.TestCase):
    def test_alias_spans_batch_and_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = GameStore(pathlib.Path(tmp) / "g")
            st.create(game().state)
            r = st.apply({"actor": "p1", "groups": [
                {"ops": [{"op": "create", "name": "Clue", "as": "clue"}]},
                {"ops": [{"op": "counter_add", "target": "$clue", "kind": "x"}]},
                {"ops": [{"op": "remove", "card": "$clue"}]},
            ]})
            self.assertEqual(r["applied"], 3)
            self.assertEqual(st.replay().to_dict(), st.load().to_dict())
            self.assertEqual(st.read_log()[1]["aliases_in"], {"clue": ["#t1"]})


class TokenTest(unittest.TestCase):
    def test_copy_of_references_original_and_groups(self):
        e = game()
        cid = find(e, "p1", "hand", "Grizzly Bears")
        ok(e, "p1", {"op": "move", "card": cid, "to": "battlefield"},
           {"op": "create", "copy_of": cid, "count": 3})
        toks = [c for c in e.state.cards.values() if c.token]
        self.assertEqual({t.definition["copy_of"] for t in toks}, {cid})
        self.assertEqual(toks[0].name, "Grizzly Bears")
        # コピーのコピーも元のカードを指す
        ok(e, "p1", {"op": "create", "copy_of": toks[0].id})
        newest = [c for c in e.state.cards.values() if c.token][-1]
        self.assertEqual(newest.definition["copy_of"], cid)
        text = render_view(player_view(e.state, "p1"))
        self.assertIn("#t1..#t4 <Grizzly Bears> ×4 (token, sick)  {copy of %s}" % cid, text)


    def test_sacrificed_token_can_be_ability_source(self):
        e = game()
        r = ok(e, "p1", {"op": "create", "name": "Mutagen", "as": "m"},
               {"op": "move", "card": "$m", "to": "graveyard"},
               {"op": "stack_push", "kind": "activated", "source": "$m", "text": "sacrificed as cost"},
               {"op": "remove", "card": "$m"})
        tok = r.aliases["m"][0]
        self.assertNotIn(tok, e.state.cards)
        self.assertEqual([it.source for it in e.state.stack.items], [tok])
        ok(e, "p1", {"op": "stack_remove"})


class NoteAndManaTest(unittest.TestCase):
    def test_note_update_and_grouping(self):
        e = game()
        cid = hand(e, "p1")[0]
        r = ok(e, "p1", {"op": "move", "card": cid, "to": "battlefield"},
               {"op": "note_add", "target": cid, "text": "+2/+2", "until": "end_of_turn", "as": "pt"},
               {"op": "note_add", "target": cid, "text": "+1/+1", "until": "end_of_turn"},
               {"op": "note_add", "target": cid, "text": "+1/+1", "until": "end_of_turn"})
        ok(e, "p1", {"op": "note_update", "note": r.aliases["pt"][0], "text": "+4/+4"})
        line = [l for l in render_view(player_view(e.state, "p1")).splitlines() if cid + " " in l][0]
        self.assertIn("+4/+4 until end_of_turn]", line)
        self.assertIn("[+1/+1 until end_of_turn ×2: #n2 #n3]", line)

    def test_identical_mana_merges(self):
        e = game()
        land = find(e, "p1", "hand", "Forest")
        ok(e, "p1", {"op": "mana_add", "color": "U", "amount": 3, "source": land},
           {"op": "mana_add", "color": "U", "amount": 5, "source": land},
           {"op": "mana_add", "color": "U", "amount": 1, "source": land, "note": "creature spells only"},
           {"op": "mana_add", "color": "U", "amount": 1, "source": land, "note": "creature spells only"},
           {"op": "mana_add", "color": "U", "amount": 1})
        pool = [(m.color, m.amount, m.source) for m in e.state.players["p1"].mana_pool.mana]
        self.assertEqual(pool, [("U", 8, land), ("U", 2, land), ("U", 1, None)])


if __name__ == "__main__":
    unittest.main()
