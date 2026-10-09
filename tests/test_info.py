"""公開範囲・記憶・Player View・表示。"""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import info, player_view  # noqa: E402
from mtgtable.render import render_view, strip_names  # noqa: E402
from helpers import find, game, hand, ok  # noqa: E402


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
        r = e.apply_act("p1", {"act": [{"op": "move", "card": top, "to": "hand"}]})
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
        r = e.apply_act("p1", {"act": [{"op": "move", "card": secret, "to": "exile"}]})
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
                                  "face_down": True, "new": True})

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

    def test_unordered_library_cards_do_not_leak_order(self):
        e = game()
        ok(e, "p1", {"op": "look", "cards": {"zone": "library", "top": 10}}, {"op": "shuffle"})
        v = player_view(e.state, "p1", library=True)["zones"]["p1.library"]
        ids = [c["id"] for c in v["known_unordered"]]
        self.assertEqual(len(ids), 10)
        self.assertEqual(ids, sorted(ids, key=info.id_sort_key))


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


class ViewTest(unittest.TestCase):
    def test_attached_objects_are_not_grouped_together(self):
        e = game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        r = ok(e, "p1", {"op": "move", "card": bear, "to": "battlefield"},
               {"op": "create", "name": "Axe", "count": 2, "as": "axes"})
        a1, a2 = r.aliases["axes"]
        text = render_view(player_view(e.state, "p1"))
        self.assertIn("%s %s <Axe> ×2" % (a1, a2), text)
        ok(e, "p1", {"op": "link_add", "kind": "attached", "source": a1, "targets": bear})
        lines = render_view(player_view(e.state, "p1")).splitlines()
        self.assertTrue(any(l.strip().startswith(a1 + " <Axe>") and "attached->" + bear in l for l in lines))
        self.assertTrue(any(l.strip().startswith(a2 + " <Axe>") for l in lines))
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

    def test_new_until_controllers_next_turn(self):
        e = self._game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        forest = find(e, "p1", "hand", "Forest")
        ok(e, "p1", {"op": "step", "to": "main1"},
           {"op": "move", "cards": [bear, forest], "to": "battlefield"})
        bf = self._bf(e)
        self.assertTrue(bf[bear]["new"])
        self.assertTrue(bf[forest]["land"])
        self.assertNotIn("new", bf[forest])
        ok(e, None, {"op": "step", "to": "cleanup"}, {"op": "step", "to": "untap"})  # p2 のターン: まだ p1 のターンは来ていない
        self.assertTrue(self._bf(e)[bear]["new"])
        ok(e, None, {"op": "step", "to": "cleanup"}, {"op": "step", "to": "untap"})  # p1 のターン
        self.assertNotIn("new", self._bf(e)[bear])
        # コントロールが移ると、新しいコントローラーの次のターンまで召喚酔い
        ok(e, None, {"op": "set", "card": bear, "controller": "p2"})
        self.assertTrue(self._bf(e)[bear]["new"])

    def test_lands_listed_first(self):
        e = self._game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        forest = find(e, "p1", "hand", "Forest")
        ok(e, "p1", {"op": "move", "cards": [bear, forest], "to": "battlefield"})
        lines = render_view(player_view(e.state, "p1")).splitlines()
        i = lines.index("  battlefield (2)")
        self.assertTrue(lines[i + 1].strip().startswith(forest))
        self.assertTrue(lines[i + 2].strip().startswith(bear))

    def test_land_is_judged_by_card_types_of_the_face_up(self):
        e = self._game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        forest = find(e, "p1", "hand", "Forest")
        # 両面カード（表はクリーチャー、裏は土地）と、サブタイプに Land を含む土地でないトークン
        e.state.cards[bear].type_line = "Legendary Creature — God // Land"
        ok(e, "p1", {"op": "move", "cards": [bear, forest], "to": "battlefield"},
           {"op": "create", "name": "Lander", "definition": {"type_line": "Token Artifact — Lander"}, "as": "lander"})
        bf = self._bf(e)
        lander = next(i for i, c in bf.items() if c.get("name") == "Lander")
        self.assertNotIn("land", bf[lander])
        self.assertNotIn("land", bf[bear])
        self.assertTrue(bf[forest]["land"])
        ok(e, None, {"op": "set", "card": bear, "face": 1})
        self.assertTrue(self._bf(e)[bear]["land"])

    def test_land_play_turns_a_modal_double_faced_card_to_its_land_face(self):
        e = self._game()
        bear = find(e, "p1", "hand", "Grizzly Bears")
        e.state.cards[bear].type_line = "Instant // Land"
        ok(e, "p1", {"op": "step", "to": "main1"}, {"op": "land", "card": bear, "mana": "{U}"})
        self.assertEqual(e.state.cards[bear].face, 1)
        self.assertTrue(self._bf(e)[bear]["land"])
        forest = find(e, "p1", "hand", "Forest")
        ok(e, "p1", {"op": "land", "card": forest, "mana": "{G}"})
        self.assertEqual(e.state.cards[forest].face, 0)


if __name__ == "__main__":
    unittest.main()
