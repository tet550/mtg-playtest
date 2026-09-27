"""CLIの契約を検証する回帰テスト。ルール判定はテーブルの責務に含めない。"""
import json
import unittest

from support import TableCase, cardcache


class InitTest(TableCase):
    def test_init_places_piles_and_reproduces_seed(self):
        self.start()
        st = self.st()
        self.assertEqual(len(st["zones"]["P1:library"]), 60)
        self.assertEqual(len(st["zones"]["P2:sideboard"]), 2)
        self.assertEqual(st["tracker"]["phase"], "pregame")
        self.assertEqual(st["players"]["P1"]["life"], 20)
        first = self.st()["zones"]["P1:library"]
        self.cli("init", "--deck1", str(self.deck), "--deck2", str(self.deck), "--seed", "7", "--force")
        self.assertEqual(first, self.st()["zones"]["P1:library"])

    def test_missing_card_does_not_save(self):
        self.deck.write_text("Deck\n60 Nonexistent Card\n", encoding="utf-8")
        out = self.cli("init", "--deck1", str(self.deck), "--goldfish", "--seed", "1", ok=False)
        self.assertIn("カード情報を用意できません", out)
        self.assertFalse(self.state.exists())

    def test_goldfish_has_no_opponent_library(self):
        self.cli("init", "--deck1", str(self.deck), "--goldfish", "--seed", "1")
        st = self.st()
        self.assertEqual(st["zones"]["P2:library"], [])
        self.assertEqual(st["players"]["P2"]["name"], "goldfish")


class RunTest(TableCase):
    def test_failed_run_preserves_state_history_and_hides_output(self):
        self.start()
        before, hist = self.st(), self.history()
        out = self.cli("run", "-", stdin="draw P1 7\ncounter P1 poison -1\n", ok=False)
        self.assertIn("2行目で停止", out)
        self.assertEqual(before, self.st())
        self.assertEqual(hist, self.history())
        self.assertNotIn("ドロー", out)

    def test_one_run_is_one_undo(self):
        self.start()
        self.cli("run", "-", stdin="draw P1 7\ndraw P2 7\n")
        self.cli("draw", "P1")
        self.assertEqual(len(self.st()["zones"]["P1:hand"]), 8)
        self.cli("undo", "2")
        st = self.st()
        self.assertEqual((len(st["zones"]["P1:hand"]), len(st["zones"]["P2:hand"])), (0, 0))

    def test_labels_refer_to_new_objects(self):
        self.start()
        self.cli("run", "-", stdin='token P1 Goblin --pt 1/1 --types "Creature Goblin" --label g\n'
                                   "counter $g +1/+1 +2\ntap $g\n")
        st = self.st()
        tok = st["objects"][str(st["zones"]["P1:battlefield"][0])]
        self.assertEqual(tok["counters"], {"+1/+1": 2})
        self.assertTrue(tok["tapped"])

    def test_forbidden_commands(self):
        self.start()
        out = self.cli("run", "-", stdin="undo\n", ok=False)
        self.assertIn("run の中で使えません", out)


class ComponentTest(TableCase):
    def test_leaving_battlefield_takes_off_dice_and_notes(self):
        self.start()
        bear = self.find("Grizzly Bears", "P1:library")
        aura = self.find("Pacifism", "P1:library")
        self.cli("run", "-", stdin="move %d P2:battlefield\nmove %d battlefield --controller P1\n"
                                   "attach %d --to %d\ncounter %d +1/+1 +1\ndamage %d +1\ntap %d\n"
                                   'note %d "+3/+3" --until eot\n'
                                   % (bear, aura, aura, bear, bear, bear, bear, bear))
        self.assertEqual(self.st()["objects"][str(bear)]["controller"], "P2")
        out = self.cli("move", str(bear), "graveyard")
        st = self.st()
        o = st["objects"][str(bear)]
        self.assertIn(bear, st["zones"]["P1:graveyard"])        # オーナーの墓地
        self.assertEqual((o["counters"], o["damage"], o["tapped"], o["notes"], o["controller"]),
                         ({}, 0, False, [], "P1"))
        self.assertIsNone(st["objects"][str(aura)]["under"])     # 重ね置きは外れるがオーラは残る
        self.assertIn(aura, st["zones"]["P1:battlefield"])
        self.assertIn("P1墓地", out)

    def test_library_needs_a_position(self):
        self.start()
        self.cli("draw", "P1", "2")
        a, b = self.st()["zones"]["P1:hand"]
        self.assertIn("位置を指定", self.cli("move", str(a), "library", ok=False))
        self.cli("move", str(a), "library", "--bottom")
        self.cli("move", str(b), "library", "--index", "1")
        lib = self.st()["zones"]["P1:library"]
        self.assertEqual((lib[0], lib[-2]), (a, b))

    def test_real_cards_cannot_vanish(self):
        self.start()
        oid = self.st()["zones"]["P1:library"][0]
        self.assertIn("実在のカード", self.cli("remove", str(oid), ok=False))
        self.cli("token", "P1", "--preset", "treasure")
        tok = self.st()["zones"]["P1:battlefield"][0]
        self.cli("run", "-", stdin="move %d graveyard\nremove %d\n" % (tok, tok))
        self.assertNotIn(str(tok), self.st()["objects"])

    def test_physical_floors(self):
        self.start()
        self.assertIn("0 より少なく", self.cli("counter", "P1", "poison", "-1", ok=False))
        self.assertIn("ありません", self.cli("mana", "P1", "pay", "G", ok=False))
        self.cli("life", "P1", "-25")
        self.assertEqual(self.st()["players"]["P1"]["life"], -5)   # ライフパッドは負も書ける

    def test_nothing_is_automatic(self):
        """致死ダメージもライフ0も、テーブルは何もしない。"""
        self.start()
        bear = self.find("Grizzly Bears", "P1:library")
        self.cli("run", "-", stdin="move %d battlefield\ndamage %d +5\nlife P1 =0\nturn next\n" % (bear, bear))
        st = self.st()
        self.assertIn(bear, st["zones"]["P1:battlefield"])
        self.assertEqual(st["objects"][str(bear)]["damage"], 5)
        self.assertEqual(st["zones"]["P1:hand"], [])              # turn next は引かない

    def test_expired_note_and_due_memo_are_only_shown(self):
        self.start()
        bear = self.find("Grizzly Bears", "P1:library")
        self.cli("run", "-", stdin='move %d battlefield\nnote %d "+3/+3" --until eot\n'
                                   'memo add "帰還させる" --player P2 --at ending.end\n' % (bear, bear))
        out = self.cli("turn", "next")
        self.assertIn("期限切れの付箋 N1", out)
        out = self.cli("phase", "ending.end")
        self.assertIn("時期のメモ M1", out)
        self.assertEqual(len(self.st()["objects"][str(bear)]["notes"]), 1)   # 外すのはAI
        self.cli("run", "-", stdin="note rm N1\nmemo done M1 --reason 済\n")
        self.assertNotIn("!!", self.cli("show"))

    def test_mana_counts_and_notes_rewrite_and_copies(self):
        self.start()
        self.cli("run", "-", stdin="mana P1 add G:12 C\nmana P1 pay G:5\n")
        self.assertEqual(self.st()["players"]["P1"]["mana"], {"G": 7, "C": 1})
        self.assertIn("{G×7C}", self.cli("show"))
        bear = self.find("Grizzly Bears", "P1:library")
        self.cli("run", "-", stdin='move %d battlefield\nnote %d "+2/+2" --until eot\n'
                                   'note set N1 "+8/+8（4周）"\ncopy P1 %d --to battlefield -n 3\n' % (bear, bear, bear))
        st = self.st()
        self.assertEqual(st["objects"][str(bear)]["notes"][0]["text"], "+8/+8（4周）")
        copies = [o for o in st["objects"].values() if o["kind"] == "copy"]
        self.assertEqual(len(copies), 3)
        self.assertTrue(all(o["arrived"] == st["tracker"]["turn"] for o in copies))
        self.cli("mana", "P1", "clear")
        self.cli("run", "-", stdin="mana P1 add RRG\nmana P1 pay RG\n")
        self.assertEqual(self.st()["players"]["P1"]["mana"], {"R": 1})
        self.cli("mana", "P1", "clear")
        self.assertEqual(self.st()["players"]["P1"]["mana"], {})


class DisplayTest(TableCase):
    def test_untap_all_and_compact_lands(self):
        self.start()
        forest = self.find("Forest", "P1:library")
        bear = self.find("Grizzly Bears", "P1:library")
        self.cli("run", "-", stdin="move %d battlefield\nmove %d battlefield\ntap %d %d\n" % (forest, bear, forest, bear))
        out = self.cli("show")
        self.assertIn("土地: [%d] Forest (タップ)" % forest, out)
        self.assertIn("(このターンに出た)", out)
        self.cli("turn", "next")
        self.cli("untap", "--all", "P1")
        st = self.st()
        self.assertFalse(st["objects"][str(forest)]["tapped"] or st["objects"][str(bear)]["tapped"])
        self.assertNotIn("このターンに出た", self.cli("show"))

    def test_identical_objects_fold_and_ranges_expand(self):
        self.start()
        self.cli("token", "P1", "Goblin", "--pt", "1/1", "--types", "Creature Goblin", "-n", "4")
        ids = self.st()["zones"]["P1:battlefield"]
        self.cli("tap", "%d-%d" % (ids[0], ids[1]))
        out = self.cli("show")
        self.assertIn("[%d-%d] Goblin 1/1 (タップ)" % (ids[0], ids[1]), out)
        self.assertIn("×2", out)
        self.cli("remove", "%d-%d" % (ids[0], ids[-1]))
        self.assertEqual(self.st()["zones"]["P1:battlefield"], [])
        self.assertIn("範囲にオブジェクトはありません", self.cli("remove", "900-905", ok=False))

    def test_attached_card_is_shown_once_under_its_host(self):
        self.start()
        bear = self.find("Grizzly Bears", "P1:library")
        aura = self.find("Pacifism", "P1:library")
        self.cli("run", "-", stdin="move %d P2:battlefield\nmove %d battlefield\nattach %d --to %d\n"
                                   % (bear, aura, aura, bear))
        out = self.cli("show")
        self.assertEqual(out.count("[%d] Pacifism" % aura), 1)
        self.assertIn("└ 下: [%d] Pacifism (このターンに出た) 〔P1がコントロール〕" % aura, out)

    def test_attacking_own_seat_is_annotated_not_stopped(self):
        self.start()
        bear = self.find("Grizzly Bears", "P1:library")
        self.cli("move", str(bear), "battlefield")
        out = self.cli("attack", str(bear), "--target", "P1")
        self.assertIn("P1 自身の席", out)
        self.assertIn("攻撃中→P1（自分の席）", self.cli("show"))

    def test_transformed_card_shows_back_face(self):
        cardcache.save(str(self.cards), dict(cardcache.blank_record("Front // Back"), source="manual",
                       unresolved=False, types=["Sorcery"], layout="transform", faces=[
                           {"name": "Front", "types": ["Sorcery"], "oracle_text_en": "front text"},
                           {"name": "Back", "types": ["Creature"], "power": "4", "toughness": "4",
                            "oracle_text_en": "back text"}]))
        self.deck.write_text("Deck\n60 Front // Back\n", encoding="utf-8")
        self.cli("init", "--deck1", str(self.deck), "--goldfish", "--seed", "1")
        oid = self.st()["zones"]["P1:library"][0]
        self.assertIn("Front", self.cli("reveal", str(oid)))             # ライブラリーから公開しても名前が出る
        self.cli("run", "-", stdin="move %d battlefield\nflip %d transform\n" % (oid, oid))
        out = self.cli("show")
        self.assertIn("Back 4/4", out)
        self.assertIn("back text", self.cli("card", str(oid)))


class HiddenInfoTest(TableCase):
    def test_seat_view(self):
        self.start()
        self.cli("run", "-", stdin="draw P1 7\ndraw P2 7\n")
        self.assertIn("相手の手札", self.cli("--as", "P1", "show", "--hand", "P2", ok=False))
        self.assertIn("--as P1", self.cli("--as", "P1", "draw", "P2", ok=False))
        mine = self.cli("--as", "P1", "show", "--hand", "P1")
        self.assertIn("P1 手札(7)", mine)
        secret = [e for e in self.st()["log"] if e.get("private") == "P2"]
        self.assertTrue(secret)
        self.assertNotIn(secret[0]["text"], self.cli("--as", "P1", "log"))

    def test_library_and_search_hide_order(self):
        self.start()
        self.assertIn("見られません", self.cli("zone", "P1:library", ok=False))
        out = self.cli("search", "P1", "Watcher")
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("  [")]), 6)
        self.assertIn("積み順は伏せています", out)


class ResultTest(TableCase):
    def test_goldfish_own_turn_and_stats(self):
        self.cli("init", "--deck1", str(self.deck), "--goldfish", "--seed", "1", "--first", "P1")
        self.cli("turn", "5", "--active", "P1")
        self.cli("end", "--winner", "P1", "--reason", "20点", "--tag", "A")
        results = self.state.with_name("results.jsonl")
        rec = json.loads(results.read_text(encoding="utf-8"))
        self.assertEqual(rec["own_turn"], 3)
        self.assertIn("記録済み", self.cli("end", "--winner", "P1", "--reason", "x", ok=False))
        self.assertIn("自ターン 平均3.00", self.cli("stats", str(results)))


if __name__ == "__main__":
    unittest.main()
