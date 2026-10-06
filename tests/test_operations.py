"""Operation（カード・Link・トークン・スタック・戦闘・マナ・ライブラリー・ログの文字列）。"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import info, player_view  # noqa: E402
from mtgtable.operations import summarize_op  # noqa: E402
from mtgtable.render import id_ranges, render_view  # noqa: E402
from helpers import find, game, hand, ok  # noqa: E402


class CardAndLinkTest(unittest.TestCase):
    def test_leaving_battlefield_resets_physical_state(self):
        e = game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        ok(e, "p1", {"op": "move", "card": bear, "to": "battlefield"},
           {"op": "tap", "card": bear},
           {"op": "counter_add", "target": bear, "kind": "+1/+1", "amount": 2},
           {"op": "note_add", "target": bear, "text": "+3/+3", "until": "end_of_turn"})
        t0 = e.state.cards[bear].entered_at
        ok(e, "p1", {"op": "move", "card": bear, "to": "graveyard"})
        c = e.state.cards[bear]
        self.assertFalse(c.tapped)
        self.assertEqual(e.state.counters_on(bear), {})
        self.assertEqual(e.state.notes_on(bear), [])
        self.assertGreater(c.entered_at, t0)
        self.assertEqual(c.zone, "p1.graveyard")

    def test_links_are_dropped_on_zone_change(self):
        e = game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        mountain = find(e, "p2", "hand", "Mountain")
        ok(e, None, {"op": "move", "card": bear, "to": "battlefield"},
           {"op": "move", "card": mountain, "to": "battlefield"})
        # 追放した後に Link を張る（Banishing Light 型）
        ok(e, "p1", {"op": "move", "card": mountain, "to": "exile"},
           {"op": "link_add", "kind": "exiled_by", "source": bear, "targets": mountain})
        self.assertEqual(len(e.state.links), 1)
        # 追放元が戦場を離れたら Link は外れ、中身が結果で返る
        r = ok(e, "p1", {"op": "move", "card": bear, "to": "graveyard"})
        self.assertEqual(e.state.links, {})
        self.assertEqual(r.links_removed[0]["kind"], "exiled_by")
        self.assertEqual(r.links_removed[0]["targets"], [mountain])
        self.assertEqual(r.public()["links_removed"][0]["because"], bear)

    def test_multi_target_link_loses_only_moved_card(self):
        e = game()
        a, b = [c for c in hand(e, "p1")][:2]
        ok(e, "p1", {"op": "move", "card": [a, b], "to": "battlefield"},
           {"op": "stack_push", "kind": "activated", "source": "p1", "targets": [a, b]})
        ok(e, "p1", {"op": "move", "card": a, "to": "graveyard"})
        link = list(e.state.links.values())[0]
        self.assertEqual(link.targets, [b])

    def test_keep_links(self):
        e = game()
        a, b = hand(e, "p1")[:2]
        ok(e, "p1", {"op": "move", "card": [a, b], "to": "battlefield"},
           {"op": "link_add", "kind": "paired", "source": a, "targets": b})
        ok(e, "p1", {"op": "move", "card": a, "to": "exile", "keep": ["links"]})
        self.assertEqual(len(e.state.links), 1)

    def test_counters_drop_when_leaving_exile(self):
        e = game()
        cid = hand(e, "p1")[0]
        ok(e, "p1", {"op": "move", "card": cid, "to": "exile"},
           {"op": "counter_add", "target": cid, "kind": "time", "amount": 3})
        ok(e, "p1", {"op": "stack_push", "card": cid})
        self.assertEqual(e.state.counters_on(cid), {})

    def test_move_to_owner_zone(self):
        e = game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        ok(e, "p1", {"op": "move", "card": bear, "to": "battlefield", "controller": "p2"})
        ok(e, "p2", {"op": "move", "card": bear, "to": "graveyard"})
        self.assertEqual(e.state.cards[bear].zone, "p1.graveyard")
        self.assertEqual(e.state.cards[bear].controller, "p1")

    def test_counters_on_players(self):
        e = game()
        ok(e, "p1", {"op": "counter_add", "target": "p2", "kind": "poison", "amount": 3},
           {"op": "life_loss", "player": "p2", "amount": 3})
        self.assertEqual(e.state.counters_on("p2"), {"poison": 3})
        self.assertEqual(e.state.players["p2"].life, 17)


class TapAliasTest(unittest.TestCase):
    def test_tap_and_untap_set_alias(self):
        e = game()
        forest = find(e, "p1", "hand", "Forest")
        ok(e, "p1", {"op": "move", "card": forest, "to": "battlefield"})
        r = ok(e, "p1", {"op": "tap", "card": {"zone": "battlefield", "name": "Forest"}, "as": "f"},
               {"op": "untap", "card": "$f"})
        self.assertEqual(r.aliases["f"], [forest])
        self.assertFalse(e.state.cards[forest].tapped)
        self.assertNotIn("'as'", r.events[0])


class LifeAndDamageTest(unittest.TestCase):
    def test_damage_loss_gain(self):
        e = game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        ok(e, "p1", {"op": "move", "card": bear, "to": "battlefield"})
        r = ok(e, "p2", {"op": "damage", "target": "p1", "amount": 3, "source": bear},
               {"op": "life_loss", "player": "p1", "amount": 2}, {"op": "life_gain", "player": "p1", "amount": 4})
        self.assertEqual(e.state.players["p1"].life, 19)
        self.assertEqual(r.events[0], "damage 3 to p1 from %s <Grizzly Bears>: life p1 20 -> 17" % bear)
        # パーマネントへのダメージは damage N の Note（ターン終了まで）にまとまる
        ok(e, "p2", {"op": "damage", "target": bear, "amount": 1}, {"op": "damage", "target": bear, "amount": 2})
        (note,) = e.state.notes_on(bear)
        self.assertEqual((note.text, note.until), ("damage 3", "end_of_turn"))

    def test_zero_does_nothing_and_negative_is_rejected(self):
        e = game()
        r = ok(e, "p1", {"op": "damage", "target": "p2", "amount": 0}, {"op": "life_gain", "amount": 0},
               {"op": "life_loss", "amount": 0}, {"op": "pay", "life": 0})
        self.assertEqual(r.events, [])
        self.assertEqual(e.state.players["p1"].life, 20)
        for op in ({"op": "damage", "target": "p2", "amount": -1}, {"op": "life_gain", "amount": -1},
                   {"op": "life_loss", "amount": -2}):
            self.assertEqual(e.apply_act("p1", {"act": [op]}).status, "failed")

    def test_damage_without_applying_the_result(self):
        # 感染など: 与えた記録だけ残し、結果（毒カウンター）は続けて書く
        e = game()
        r = ok(e, "p1", {"op": "damage", "target": "p2", "amount": 2, "apply": False},
               {"op": "counter_add", "target": "p2", "kind": "poison", "amount": 2})
        self.assertEqual(e.state.players["p2"].life, 20)
        self.assertIn("(result written separately)", r.events[0])
        self.assertEqual(e.apply_act("p1", {"act": [{"op": "damage", "target": hand(e, "p1")[0], "amount": 1}]}).status,
                         "failed")  # 戦場に無いカードには与えられない


class TokenTest(unittest.TestCase):
    def test_token_alias_and_link(self):
        e = game()
        r = ok(e, "p1",
               {"op": "create", "name": "Saproling", "definition": {"type_line": "Creature — Saproling",
                                                                    "power": 1, "toughness": 1},
                "count": 2, "as": "saps"},
               {"op": "counter_add", "target": "$saps", "kind": "+1/+1"},
               {"op": "link_add", "kind": "created_by", "source": "p1", "targets": "$saps"})
        saps = r.aliases["saps"]
        self.assertEqual(len(saps), 2)
        self.assertEqual(e.state.counters_on(saps[0]), {"+1/+1": 1})
        ok(e, "p1", {"op": "remove", "card": saps[0]})
        self.assertNotIn(saps[0], e.state.cards)
        self.assertEqual(list(e.state.links.values())[0].targets, [saps[1]])

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
        self.assertIn("#t1..#t4 <Grizzly Bears> ×4 (token, new)  {copy of %s}" % cid, text)

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


class StackAndCombatTest(unittest.TestCase):
    def test_stack_cast_and_resolve(self):
        e = game()
        growth = find(e, "p1", "hand", "Giant Growth") if any(
            e.state.cards[c].name == "Giant Growth" for c in hand(e, "p1")) else None
        if growth is None:
            self.skipTest("no Giant Growth in opening hand for this seed")
        r = ok(e, "p1", {"op": "stack_push", "card": growth, "targets": "p1", "as": "spell"})
        sid = r.aliases["spell"][0]
        self.assertEqual(e.state.cards[growth].zone, "stack")
        self.assertEqual(len(e.state.links), 1)
        ok(e, "p1", {"op": "stack_remove", "card_to": "graveyard"})
        self.assertEqual(e.state.stack.items, [])
        self.assertEqual(e.state.links, {})
        self.assertEqual(e.state.cards[growth].zone, "p1.graveyard")
        self.assertEqual(sid, "#s1")

    def test_moving_card_off_stack_removes_item(self):
        e = game()
        cid = hand(e, "p1")[0]
        ok(e, "p1", {"op": "stack_push", "card": cid},
           {"op": "stack_push", "kind": "triggered", "source": "p1", "text": "whenever..."})
        ok(e, "p1", {"op": "move", "card": cid, "to": "graveyard"})
        self.assertEqual([i.kind for i in e.state.stack.items], ["triggered"])

    def test_combat(self):
        e = game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        mountain = find(e, "p2", "hand", "Mountain")
        ok(e, None, {"op": "move", "card": bear, "to": "battlefield"},
           {"op": "create", "name": "Goblin", "controller": "p2", "as": "gob"},
           {"op": "move", "card": mountain, "to": "battlefield"})
        ok(e, "p1", {"op": "attack", "attacker": bear, "target": "p2", "tap": True})
        gob = e.state.zones["battlefield"].cards[1]
        bad = e.apply_act("p2", {"act": [{"op": "block", "blocker": gob, "attacker": mountain}]})
        self.assertEqual(bad.status, "failed")
        ok(e, "p2", {"op": "block", "blocker": gob, "attacker": bear})
        self.assertEqual(len(e.state.combat.blocks), 1)
        ok(e, "p1", {"op": "move", "card": bear, "to": "graveyard"})
        self.assertEqual(e.state.combat.attacks, [])
        self.assertEqual(e.state.combat.blocks, [])


class NoteAndManaTest(unittest.TestCase):
    def test_mana_pool(self):
        e = game()
        ok(e, "p1", {"op": "mana_add", "color": "G", "amount": 2},
           {"op": "mana_add", "color": "G", "amount": 1},
           {"op": "mana_add", "color": "G", "amount": 1, "note": "creature spells only"})
        pool = e.state.players["p1"].mana_pool.mana
        self.assertEqual([(m.color, m.amount) for m in pool], [("G", 3), ("G", 1)])
        ok(e, "p1", {"op": "mana_spend", "color": "G", "amount": 3})
        pool = e.state.players["p1"].mana_pool.mana
        self.assertEqual(len(pool), 1)
        self.assertEqual(e.state.notes_on(pool[0].id)[0].text, "creature spells only")
        r = e.apply_act("p1", {"act": [{"op": "mana_spend", "color": "G", "amount": 2}]})
        self.assertEqual(r.status, "failed")
        ok(e, "p1", {"op": "mana_clear"})
        self.assertEqual(e.state.players["p1"].mana_pool.mana, [])
        self.assertEqual(e.state.notes, {})

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


class LibraryTest(unittest.TestCase):
    def test_search_moves_only_the_found_card(self):
        e = game()
        lib = len(e.state.zones["p1.library"].cards)
        r = ok(e, "p1", {"op": "search", "name": "Grizzly Bears", "to": "hand", "reveal": True, "as": "b"})
        self.assertEqual(len(r.learned), 1)
        bear = r.learned[0]["id"]
        self.assertEqual((r.learned[0]["name"], r.learned[0]["zone"]), ("Grizzly Bears", "p1.hand"))
        self.assertEqual(r.aliases["b"], [bear])
        self.assertEqual(len(e.state.zones["p1.library"].cards), lib - 1)
        self.assertTrue(info.knows_identity(e.state, "p2", bear))  # 公開した
        # 残りのライブラリーは覚えない（シャッフル済みで位置も分からない）
        self.assertEqual(player_view(e.state, "p1")["zones"]["p1.library"].get("known_positions_count"), None)
        # 見つからない・足りないなら失敗
        self.assertEqual(e.apply_act("p1", {"act": [{"op": "search", "name": "Nope", "to": "hand"}]}).status,
                         "failed")

    def test_search_to_battlefield_and_to_top(self):
        e = game()
        r = ok(e, "p1", {"op": "search", "name": ["Forest", "Island"], "count": 2, "to": "battlefield",
                         "tapped": True})
        cards = r.results[0]["cards"]
        self.assertTrue(all(e.state.cards[c].tapped and e.state.cards[c].zone == "battlefield" for c in cards))
        # 「探してシャッフルし、その後一番上に置く」
        r = ok(e, "p1", {"op": "search", "name": "Grizzly Bears", "to": "library", "position": "top"})
        top = r.results[0]["cards"][0]
        self.assertEqual(e.state.zones["p1.library"].cards[0], top)
        self.assertTrue(info.knows_position(e.state, "p1", top))
        self.assertFalse(info.knows_identity(e.state, "p2", top))

    def test_draw_from_empty_library(self):
        e = game(hand=0)
        r = ok(e, "p1", {"op": "draw", "count": 61})
        self.assertEqual(r.results[0]["short"], 1)
        self.assertTrue(any("empty library" in ev for ev in r.events))

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
        bad = e.apply_act("p1", {"act": [{"op": "move", "card": hand(e, "p1")[0], "to": "exile", "order": "x"}]})
        self.assertEqual(bad.status, "failed")


class LogTextTest(unittest.TestCase):
    def test_event_lists_are_plain_text(self):
        e = game()
        r = e.apply_act("p1", {"act": [{"op": "look", "cards": {"zone": "library", "top": 2}}]})
        line = [x for x in r.events if "looks at" in x][0]
        self.assertNotIn("[", line)
        self.assertRegex(line, r"^p1 looks at #c\d+ <[^<>]+>, #c\d+ <[^<>]+>$")

    def test_summarize_op(self):
        self.assertEqual(summarize_op({"op": "declare", "kind": "concede"}), "declare kind=concede")
        self.assertEqual(summarize_op({"op": "player_set", "player": "p2", "status": "won"}),
                         "player_set status=won player=p2")
        self.assertEqual(summarize_op({"op": "move", "card": {"zone": "library", "top": 2}, "to": "graveyard"}),
                         "move library[top=2] to=graveyard")


class IdTest(unittest.TestCase):
    def test_ids_have_hash_and_input_may_omit_it(self):
        e = game()
        cid = hand(e, "p1")[0]
        self.assertTrue(cid.startswith("#c"))
        ok(e, "p1", {"op": "move", "card": cid[1:], "to": "battlefield"},
           {"op": "counter_add", "target": cid[1:], "kind": "+1/+1"})
        self.assertEqual(e.state.counters_on(cid), {"+1/+1": 1})
        r = e.apply_act("p1", {"pre": [{"card": cid[1:], "zone": "battlefield"}], "act": [{"op": "draw"}]})
        self.assertEqual(r.status, "applied")

    def test_id_ranges(self):
        self.assertEqual(id_ranges(["#t5", "#t6", "#t7", "#c2", "#t9", "#t10"]), "#t5..#t7 #c2 #t9 #t10")


if __name__ == "__main__":
    unittest.main()
