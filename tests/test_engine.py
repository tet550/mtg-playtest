import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import Engine, GameStore, new_game, parse_decklist, player_view, state_diff  # noqa: E402
from mtgtable.render import render_view  # noqa: E402

DECK_A = """# a
Deck
20 Forest
20 Grizzly Bears
20 Giant Growth
Sideboard
2 Naturalize
"""
DECK_B = """Deck
30 Mountain
30 Lightning Bolt
"""


def game(seed=1, hand=7, **kw):
    decks = {"p1": parse_decklist(DECK_A, "green"), "p2": parse_decklist(DECK_B, "red")}
    return Engine(new_game(decks, seed=seed, hand=hand, **kw))


def ok(engine, actor, *ops, **group):
    r = engine.apply_group(actor, {"ops": list(ops), **group})
    assert r.status == "applied", r.error
    return r


def hand(e, pid):
    return list(e.state.zones["%s.hand" % pid].cards)


def find(e, pid, zone, name):
    return next(c for c in e.state.zones["%s.%s" % (pid, zone)].cards if e.state.cards[c].name == name)


class SetupTest(unittest.TestCase):
    def test_parse_decklist(self):
        d = parse_decklist(DECK_A + "\n1 Llanowar Elves (DOM) 168\n", "g")
        self.assertEqual(d.main_count, 60)
        self.assertEqual(d.sideboard[-1], (1, "Llanowar Elves"))

    def test_new_game_zones(self):
        e = game()
        s = e.state
        self.assertEqual(len(s.zones["p1.library"].cards), 53)
        self.assertEqual(len(hand(e, "p1")), 7)
        self.assertEqual(len(s.zones["p1.sideboard"].cards), 2)
        self.assertEqual(s.turn.active, "p1")

    def test_same_seed_same_game(self):
        self.assertEqual(game(seed=5).state.to_dict(), game(seed=5).state.to_dict())
        self.assertNotEqual(game(seed=5).state.zones["p1.library"].cards,
                            game(seed=6).state.zones["p1.library"].cards)


class InformationTest(unittest.TestCase):
    def test_opponent_hand_and_library_hidden(self):
        e = game()
        v = player_view(e.state, "p2")
        opp_hand = v["zones"]["p1.hand"]
        self.assertEqual(opp_hand["count"], 7)
        self.assertEqual(opp_hand["known"], [])
        text = json.dumps(v)
        for cid in hand(e, "p1") + e.state.zones["p1.library"].cards:
            self.assertNotIn('"%s"' % cid, text)
        self.assertEqual(len(player_view(e.state, "p1")["zones"]["p1.hand"]["cards"]), 7)

    def test_revealed_card_stays_known_in_hand(self):
        e = game()
        cid = hand(e, "p1")[0]
        ok(e, "p1", {"op": "reveal", "card": cid})
        v = player_view(e.state, "p2")["zones"]["p1.hand"]
        self.assertEqual([c["id"] for c in v["known"]], [cid])
        self.assertEqual(v["unknown"], 6)

    def test_look_then_shuffle_forgets_position_not_identity(self):
        e = game()
        r = ok(e, "p1", {"op": "look", "cards": {"zone": "library", "top": 1}})
        top = e.state.zones["p1.library"].cards[0]
        self.assertEqual([x["id"] for x in r.learned], [top])
        lib = player_view(e.state, "p1", library=True)["zones"]["p1.library"]
        self.assertEqual(lib["known_positions"][0]["id"], top)
        ok(e, "p1", {"op": "shuffle", "zone": "library"})
        lib = player_view(e.state, "p1", library=True)["zones"]["p1.library"]
        self.assertNotIn("known_positions", lib)
        self.assertEqual([c["id"] for c in lib["known_unordered"]], [top])
        # 位置が分からなくなったカードは id で指定できない
        r = e.apply_group("p1", {"ops": [{"op": "move", "card": top, "to": "hand"}]})
        self.assertEqual(r.status, "failed")

    def test_public_card_to_library_is_known_to_everyone(self):
        e = game()
        cid = hand(e, "p1")[0]
        ok(e, "p1", {"op": "move", "card": cid, "to": "graveyard"},
           {"op": "move", "card": cid, "to": "library", "position": "top"})
        lib = player_view(e.state, "p2", library=True)["zones"]["p1.library"]
        self.assertEqual(lib["known_positions"][0]["id"], cid)

    def test_unknown_card_reference_is_rejected(self):
        e = game()
        secret = e.state.zones["p2.library"].cards[0]
        r = e.apply_group("p1", {"ops": [{"op": "move", "card": secret, "to": "exile"}]})
        self.assertEqual(r.status, "failed")
        self.assertIn("not known", r.error)
        # 位置指定なら知らないカードでも操作できる（紙で一番上を追放するのと同じ）
        ok(e, "p1", {"op": "move", "card": {"zone": "p2.library", "top": 1}, "to": "exile"})
        self.assertEqual(e.state.cards[secret].zone, "exile")

    def test_face_down_permanent(self):
        e = game()
        cid = hand(e, "p1")[0]
        ok(e, "p1", {"op": "move", "card": cid, "to": "battlefield", "face_down": True})
        mine = player_view(e.state, "p1")["zones"]["battlefield"]["cards"][0]
        theirs = player_view(e.state, "p2")["zones"]["battlefield"]["cards"][0]
        self.assertIn("name", mine)
        # 裏向きのパーマネントはクリーチャーとして扱うので、出たターンは召喚酔いの表示が付く
        self.assertEqual(theirs, {"id": cid, "hidden": True, "owner": "p1", "controller": "p1",
                                  "face_down": True, "sick": True})

    def test_face_down_card_turns_face_up_when_it_moves(self):
        e = game()
        cid = hand(e, "p1")[0]
        ok(e, "p1", {"op": "move", "card": cid, "to": "battlefield", "face_down": True})
        ok(e, "p1", {"op": "move", "card": cid, "to": "exile"})
        self.assertFalse(e.state.cards[cid].face_down)
        ok(e, "p1", {"op": "move", "card": cid, "to": "battlefield"},
           {"op": "move", "card": cid, "to": "exile", "face_down": True})
        self.assertTrue(e.state.cards[cid].face_down)
        # 表で見たことのある p2 は覚えている。手札から直接裏向きで追放したカードは知らない
        other = hand(e, "p1")[0]
        ok(e, "p1", {"op": "move", "card": other, "to": "exile", "face_down": True})
        exile = {c["id"]: c for c in player_view(e.state, "p2")["zones"]["exile"]["cards"]}
        self.assertIn("name", exile[cid])
        self.assertTrue(exile[other]["hidden"])

    def test_results_do_not_expose_hidden_ids(self):
        e = game()
        r = ok(e, "p1", {"op": "move", "card": {"zone": "p2.hand", "random": 1},
                         "to": "library", "as": "x"})
        self.assertEqual(r.results[0]["cards"], ["hidden"])
        self.assertEqual(r.aliases["x"], ["hidden"])

    def test_policies(self):
        e = game(policies={"p1": "own_library"})
        top = e.state.zones["p1.library"].cards[0]
        v = player_view(e.state, "p1", library=True)
        self.assertEqual(v["zones"]["p1.library"]["known_positions"][0]["id"], top)
        self.assertNotIn("known_positions", v["zones"]["p2.library"])
        e.state.info_policy["p1"] = "omniscient"
        v = player_view(e.state, "p1")
        self.assertEqual(len(v["zones"]["p2.hand"]["cards"]), 7)

    def test_render_does_not_crash(self):
        e = game()
        ok(e, "p1", {"op": "move", "card": hand(e, "p1")[0], "to": "battlefield"})
        self.assertIn("battlefield (1)", render_view(player_view(e.state, "p1")))


class OperationTest(unittest.TestCase):
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
        bad = e.apply_group("p2", {"ops": [{"op": "block", "blocker": gob, "attacker": mountain}]})
        self.assertEqual(bad.status, "failed")
        ok(e, "p2", {"op": "block", "blocker": gob, "attacker": bear})
        self.assertEqual(len(e.state.combat.blocks), 1)
        ok(e, "p1", {"op": "move", "card": bear, "to": "graveyard"})
        self.assertEqual(e.state.combat.attacks, [])
        self.assertEqual(e.state.combat.blocks, [])

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
        r = e.apply_group("p1", {"ops": [{"op": "mana_spend", "color": "G", "amount": 2}]})
        self.assertEqual(r.status, "failed")
        ok(e, "p1", {"op": "mana_clear"})
        self.assertEqual(e.state.players["p1"].mana_pool.mana, [])
        self.assertEqual(e.state.notes, {})

    def test_counters_on_players(self):
        e = game()
        ok(e, "p1", {"op": "counter_add", "target": "p2", "kind": "poison", "amount": 3},
           {"op": "life", "player": "p2", "amount": -3})
        self.assertEqual(e.state.counters_on("p2"), {"poison": 3})
        self.assertEqual(e.state.players["p2"].life, 17)

    def test_turn_structure(self):
        e = game()
        ok(e, None, {"op": "step", "to": "main1"})
        self.assertEqual((e.state.turn.phase, e.state.turn.step, e.state.turn.priority),
                         ("main1", "main", "p1"))
        ok(e, None, {"op": "step", "to": "declare_attackers"}, {"op": "step", "to": "main2"})
        self.assertEqual((e.state.turn.turn, e.state.turn.phase), (1, "main2"))
        # 今より前のステップを指定すると次のターン
        ok(e, None, {"op": "step", "to": "untap"})
        self.assertEqual((e.state.turn.turn, e.state.turn.active, e.state.turn.step), (2, "p2", "untap"))
        self.assertIsNone(e.state.turn.priority)

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

    def test_opening_hand_permanent_is_not_sick_on_turn_1(self):
        from mtgtable.info import summoning_sick
        e = game()
        # 開始時の手札から戦場へ（Leyline など）はゲーム前
        bear = find(e, "p1", "hand", "Grizzly Bears")
        ok(e, "p1", {"op": "declare", "kind": "keep"}, {"op": "move", "card": bear, "to": "battlefield"})
        self.assertTrue(summoning_sick(e.state, e.state.cards[bear]))
        ok(e, None, {"op": "step", "to": "untap"})
        self.assertFalse(summoning_sick(e.state, e.state.cards[bear]))

    def test_pregame_to_any_step(self):
        e = game()
        ok(e, None, {"op": "step", "to": "main1"})
        self.assertEqual((e.state.turn.turn, e.state.turn.step, e.state.turn.priority), (1, "main", "p1"))

    def test_step_requires_a_name(self):
        e = game()
        ok(e, None, {"op": "step", "to": "untap"})
        for bad in ({"op": "step"}, {"op": "step", "to": "main"}, {"op": "step", "to": "untap"},
                    {"op": "step", "to": "second_main"}):
            self.assertEqual(e.apply_group(None, {"ops": [bad]}).status, "failed", bad)

    def test_declarations(self):
        e = game()
        ok(e, "p1", {"op": "declare", "kind": "pass"})
        self.assertEqual(player_view(e.state, "p2")["declarations"][0]["kind"], "pass")
        ok(e, "p2", {"op": "declare", "kind": "concede"})
        self.assertEqual(e.state.players["p2"].status, "conceded")

    def test_draw_from_empty_library(self):
        e = game(hand=0)
        r = ok(e, "p1", {"op": "draw", "count": 61})
        self.assertEqual(r.results[0]["short"], 1)
        self.assertTrue(any("empty library" in ev for ev in r.events))


class GroupAndBatchTest(unittest.TestCase):
    def test_action_group_is_atomic(self):
        e = game()
        before = e.state.to_dict()
        r = e.apply_group("p1", {"ops": [{"op": "draw"}, {"op": "move", "card": "nope", "to": "exile"}]})
        self.assertEqual(r.status, "failed")
        self.assertEqual(e.state.to_dict(), before)

    def test_unknown_draw_does_not_stop_batch(self):
        # 知らないカードを見ても止めない（区切るのは操作する側の責任）。知ったカードは learned で返る
        e = game()
        res = e.apply_batch({"actor": "p1", "groups": [
            {"ops": [{"op": "draw"}]},
            {"ops": [{"op": "move", "card": {"zone": "hand", "index": 0}, "to": "graveyard"}]},
        ]})
        self.assertEqual(res["applied"], 2)
        self.assertIsNone(res["stopped"])
        self.assertEqual(len(res["groups"][0].learned), 1)
        self.assertEqual(len(hand(e, "p1")), 7)

    def test_batch_continues_when_top_is_known(self):
        e = game()
        ok(e, "p1", {"op": "look", "cards": {"zone": "library", "top": 1}})
        top = e.state.zones["p1.library"].cards[0]
        res = e.apply_batch({"actor": "p1", "groups": [
            {"pre": [{"zone": "library", "top": [top]}], "ops": [{"op": "draw"}]},
            {"ops": [{"op": "move", "card": top, "to": "graveyard"}]},
        ]})
        self.assertEqual(res["applied"], 2)
        self.assertIsNone(res["stopped"])
        self.assertEqual(e.state.cards[top].zone, "p1.graveyard")

    def test_precondition_failure_stops_batch(self):
        e = game()
        res = e.apply_batch({"actor": "p1", "groups": [
            {"pre": [{"version": 99}], "ops": [{"op": "draw"}]},
            {"ops": [{"op": "draw"}]},
        ]})
        self.assertEqual(res["applied"], 0)
        self.assertEqual(res["stopped"]["reason"], "precondition_failed")
        self.assertEqual([g.status for g in res["groups"]], ["precondition_failed", "skipped"])

    def test_precondition_cannot_probe_hidden_cards(self):
        e = game()
        secret = e.state.zones["p2.library"].cards[0]
        r = e.apply_group("p1", {"pre": [{"zone": "p2.library", "top": [secret]}],
                                 "ops": [{"op": "draw"}]})
        self.assertEqual(r.status, "precondition_failed")
        self.assertIn("not known", r.error)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = GameStore(pathlib.Path(self.tmp.name) / "g")
        self.store.create(game().state)

    def tearDown(self):
        self.tmp.cleanup()

    def test_undo_redo_replay(self):
        st = self.store
        st.apply({"actor": "p1", "ops": [{"op": "shuffle", "zone": "library"}]})
        st.apply({"actor": "p1", "ops": [{"op": "move", "card": {"zone": "hand", "random": 2},
                                          "to": "graveyard"}]})
        after = st.load().to_dict()
        self.assertEqual(st.replay().to_dict(), after)
        self.assertEqual(st.undo(), 1)
        self.assertEqual(len(st.load().zones["p1.graveyard"].cards), 0)
        self.assertEqual(st.redo(), 2)
        self.assertEqual(st.load().to_dict(), after)
        st.undo()
        st.apply({"actor": "p1", "ops": [{"op": "draw"}]})
        self.assertEqual(len(st.read_log()), 2)  # Redo 履歴は捨てられる
        self.assertEqual(st.read_log()[-1]["ops"][0]["op"], "draw")

    def test_undo_to(self):
        st = self.store
        for amount in (-1, -2, -3):
            st.apply({"actor": "p1", "ops": [{"op": "life", "amount": amount}]})
        self.assertEqual(st.undo(to=1), 1)
        self.assertEqual(st.load().players["p1"].life, 19)
        with self.assertRaises(ValueError):
            st.undo(to=5)

    def test_diff_and_fork(self):
        st = self.store
        st.apply({"actor": "p1", "ops": [{"op": "life", "amount": -2}]})
        diff = state_diff(st.replay(0), st.replay(1))
        self.assertIn(("players.p1.life", 20, 18), diff)
        other = st.fork(pathlib.Path(self.tmp.name) / "f")
        self.assertEqual(other.load().players["p1"].life, 18)
        self.assertEqual(other.read_log(), [])

    def test_policy_survives_replay(self):
        st = self.store
        st.set_policies({"p1": "omniscient"})
        self.assertEqual(st.replay().info_policy["p1"], "omniscient")


if __name__ == "__main__":
    unittest.main()
