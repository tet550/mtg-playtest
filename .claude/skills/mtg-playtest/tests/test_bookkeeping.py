"""Declared triggers, atomic effect application, and small reminder hooks."""
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import mtg


class BookkeepingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.path = self.root / "state.json"
        self.call("init", "--seed", "12")
        st = self.read()
        st["phase"] = "precombat_main"
        for oid, name, types, zone in [(1, "Scout", ["Creature"], "battlefield"),
                                        (2, "Plains", ["Land"], "hand"),
                                        (3, "Spell", ["Sorcery"], "hand"),
                                        (4, "Card", ["Creature"], "library")]:
            st["objects"][str(oid)] = mtg.new_object(oid, name, "P1")
            st["cards"][name] = dict(token=True, types=types, power="2", toughness="2", oracle="")
            st["zones"]["P1:" + zone].append(oid)
        st["next_oid"] = 5
        self.write(st)

    def call(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mtg.dispatch(["--state", str(self.path), "--offline", *args])
        return out.getvalue()

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, st):
        self.path.write_text(json.dumps(st), encoding="utf-8")

    def file(self, text, name="effect.mtg"):
        p = self.root / name
        p.write_text(text, encoding="utf-8")
        return str(p)

    def add(self):
        return self.call("pending", "add", "果敢", "--controller", "P1", "--src", "1")

    def stack(self):
        self.add()
        self.call("pending", "stack", "T1")

    def resolve(self, file, *extra):
        return self.call("pending", "resolve", "T1", "--file", file, "--part", "first", *extra)

    def test_same_name_abilities_and_real_card_name_never_share_kind(self):
        with patch.object(mtg, "cached_record", side_effect=AssertionError("ability looked up as card")):
            self.call("stack", "push", "Scout", "--ability")
            self.call("stack", "push", "Scout", "--ability")
            self.call("stack", "pop")
            st = self.read()
            self.assertEqual(mtg.card_of(st, st["objects"]["5"])["types"], ["Ability"])
            self.assertEqual(mtg.card_of(st, st["objects"]["1"])["types"], ["Creature"])
            self.call("stack", "pop")
        self.assertIn("Scout", self.read()["cards"])

    def test_legacy_shared_definition_remains_until_last_ability_removed(self):
        st = self.read()
        for oid in (5, 6):
            st["objects"][str(oid)] = mtg.new_object(oid, "Legacy", "P1")
            st["objects"][str(oid)]["note"] = "能力"
            st["zones"]["stack"].append(oid)
        st["cards"]["Legacy"] = {"types": ["Ability"]}
        self.write(st)
        self.call("stack", "pop")
        self.assertIn("Legacy", self.read()["cards"])
        self.call("stack", "pop")
        self.assertNotIn("Legacy", self.read()["cards"])

    def test_landfall_self_name_in_effect_and_multiple_lines(self):
        st = self.read()
        st["zones"]["P1:hand"].remove(2)
        st["zones"]["P1:battlefield"].append(2)
        st["cards"]["Scout"]["oracle"] = (
            "Landfall — Whenever a land you control enters, Scout gets +1/+1 until end of turn.\n"
            "Whenever a land you control enters, draw a card.")
        self.assertEqual(len(mtg.bookkeeping.enter_watchers(mtg, st, 2)[0]), 2)
        st["cards"]["Scout"]["oracle"] = "上陸 — あなたがコントロールしている土地が戦場に出るたび、Scoutは+1/+1の修整を受ける。"
        self.assertEqual(len(mtg.bookkeeping.enter_watchers(mtg, st, 2)[0]), 1)

    def test_own_control_in_effect_does_not_suppress_opponent_entry(self):
        st = self.read()
        st["objects"]["2"]["controller"] = "P2"
        st["zones"]["P1:hand"].remove(2)
        st["zones"]["P2:battlefield"].append(2)
        st["cards"]["Scout"]["oracle"] = "Whenever a land enters, target creature you control gets +1/+1 until end of turn."
        self.assertEqual(len(mtg.bookkeeping.enter_watchers(mtg, st, 2)[0]), 1)
        st["cards"]["Scout"]["oracle"] = "Whenever Scout enters, draw a card."
        self.assertEqual(len(mtg.bookkeeping.enter_watchers(mtg, st, 2)[0]), 0)

    def test_resolution_and_completion_save_once_and_undo_together(self):
        self.stack()
        before = self.read()
        history = self.root / "history" / "state"
        count = len(list(history.glob("*.json")))
        self.resolve(self.file("mod 1 +1/+1 --until eot\nlife P1 2\n"))
        st = self.read()
        self.assertEqual(mtg.pt(st, st["objects"]["1"]), (3, 3))
        self.assertEqual(st["players"]["P1"]["life"], 22)
        self.assertEqual(st["pending"][0]["status"], "done")
        self.assertEqual(st["zones"]["stack"], [])
        self.assertEqual(len(list(history.glob("*.json"))), count + 1)
        with self.assertRaises(SystemExit):
            self.resolve(self.file("life P1 2\n"))
        self.call("undo")
        self.assertEqual(self.read(), before)

    def test_failed_resolution_rolls_back_all_lines(self):
        self.stack()
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            self.resolve(self.file("life P1 2\nmove 999 hand\n"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_pause_resume_duplicate_part_and_new_trigger(self):
        self.stack()
        self.resolve(self.file("draw P1 1\n"), "--pause")
        self.assertIn(4, self.read()["zones"]["P1:hand"])
        self.assertEqual(self.read()["pending"][0]["status"], "resolving")
        self.add()  # a newly noticed draw trigger, waits until the current resolution ends
        before = self.path.read_bytes()
        for argv in [("pass", "P1"), ("pending", "stack", "T2"), ("life", "P1", "3"),
                     ("pending", "cancel", "T1", "--reason", "wrong")]:
            with self.assertRaises(SystemExit):
                self.call(*argv)
            self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(SystemExit):
            self.resolve(self.file("draw P1 1\n"), "--pause")
        self.call("show")
        self.call("pending", "resolve", "T1", "--file", self.file("life P1 1\n"), "--part", "rest")
        self.call("pending", "stack", "T2")
        self.assertEqual(self.read()["pending"][0]["status"], "done")

    def test_waiting_guards_boundaries_but_permits_effects_and_sba(self):
        self.add()
        for argv in [("phase", "next"), ("turn", "next"), ("pass", "P1"),
                     ("stack", "push", "3"), ("stack", "resolve")]:
            with self.assertRaises(SystemExit):
                self.call(*argv)
        self.call("life", "P1", "1")
        self.call("sba", "--apply")
        self.call("pending", "stack", "T1")
        self.call("pass", "P1")

    def test_resolve_only_top_and_all_pending_registered(self):
        self.stack()
        self.add()
        with self.assertRaises(SystemExit):
            self.resolve(self.file("life P1 1\n"))
        self.call("pending", "stack", "T2")
        with self.assertRaises(SystemExit):
            self.resolve(self.file("life P1 1\n"))
        self.call("pending", "cancel", "T2", "--reason", "打ち消し")
        self.resolve(self.file("life P1 1\n"))

    def test_cannot_bypass_completion_by_popping_or_moving(self):
        self.stack()
        before = self.path.read_bytes()
        for argv in [("stack", "pop"), ("move", "5", "exile")]:
            with self.assertRaises(SystemExit):
                self.call(*argv)
            self.assertEqual(self.path.read_bytes(), before)

    def test_external_actions_and_operations_after_draw_rejected_before_changes(self):
        self.stack()
        before = self.path.read_bytes()
        for text in ("life P1 1\nend --winner P1\n", "life P1 1\nundo\n",
                     "card set Fixture --types Land\n", "draw P1 1\nlife P1 1\n",
                     "sba --apply\n", "run other.mtg\n", "fx list\n"):
            with self.assertRaises(SystemExit):
                self.resolve(self.file(text))
            self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / "results.jsonl").exists())

    def test_readonly_lists_do_not_create_history(self):
        self.add()
        before = self.path.read_bytes()
        self.call("pending", "list", "--all")
        self.call("remind", "list")
        self.assertEqual(self.path.read_bytes(), before)

    def test_reminders_are_notifications_not_triggers_and_cast_is_explicit(self):
        self.call("remind", "add", "果敢を確認", "--on", "cast", "--player", "P1", "--src", "1")
        output = self.call("stack", "push", "3")
        self.assertNotIn("確認 R", output)
        self.assertNotIn("cast_counts", self.read())
        self.call("move", "3", "hand")
        output = self.call("stack", "push", "3", "--cast", "--controller", "P1")
        self.assertIn("確認 R1", output)
        self.assertEqual(self.read()["cast_counts"]["1"]["P1"], 1)
        self.assertNotIn("pending", self.read())
        self.call("move", "3", "hand")
        self.call("move", "1", "exile")
        output = self.call("stack", "push", "3", "--cast")
        self.assertNotIn("確認 R1", output)

    def test_enter_and_turn_reminders_and_token_grouping(self):
        self.call("remind", "add", "登場後に確認", "--on", "enter", "--player", "P1")
        out = self.call("move", "2", "battlefield")
        self.assertIn("確認 R1", out)
        out = self.call("token", "P1", "FixtureToken", "--power", "1", "--toughness", "1", "-n", "3")
        self.assertEqual(out.count("確認 R1"), 1)
        self.assertIn("3件", out)
        self.call("remind", "add", "開始時の確認", "--on", "turn", "--player", "P2")
        self.assertIn("確認 R2", self.call("turn", "next"))

    def test_linked_pending_shown_without_duplicate_record(self):
        self.call("linked", "exile", "4", "--src", "1", "--ability", "blink", "--return", "next-end", "--to", "battlefield")
        self.call("phase", "to", "ending.end")
        self.assertIn("L1", self.call("pending", "list"))
        self.assertNotIn("pending", self.read())
        self.call("linked", "trigger", "L1")
        self.call("linked", "resolve", "L1")

    def test_resolution_inherits_seat_restrictions(self):
        self.stack()
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
            mtg.dispatch(["--state", str(self.path), "--as", "P1", "--offline", "pending", "resolve", "T1",
                          "--file", self.file("draw P2 1\n"), "--part", "draw"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_linked_spell_cast_records_once_and_undo_restores_permission_state(self):
        self.call("remind", "add", "CAST_CHECK", "--on", "cast", "--player", "P1")
        self.call("linked", "exile", "3", "--src", "1", "--ability", "permission",
                  "--play-until", "eot", "--player", "P1")
        st = self.read()
        st["passed"] = ["P2"]
        st["priority"] = "P1"
        self.write(st)
        before = self.read()
        output = self.call("linked", "play", "L1", "3", "--player", "P1")
        st = self.read()
        self.assertEqual(st["cast_counts"]["1"]["P1"], 1)
        self.assertEqual(st["passed"], [])
        self.assertEqual(st["objects"]["3"]["controller"], "P1")
        self.assertEqual(output.count("CAST_CHECK"), 1)
        saved = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            self.call("linked", "play", "L1", "3", "--player", "P1")
        self.assertEqual(self.path.read_bytes(), saved)
        self.call("undo")
        self.assertEqual(self.read(), before)

    def test_linked_land_is_not_cast_and_rejected_spell_has_no_effect(self):
        self.call("linked", "exile", "2", "3", "--src", "1", "--ability", "permission",
                  "--play-until", "eot", "--player", "P1")
        self.call("linked", "play", "L1", "2", "--player", "P1")
        self.assertNotIn("cast_counts", self.read())
        self.assertEqual(self.read()["players"]["P1"]["lands_played"], 1)
        self.call("phase", "to", "combat.begin")
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            self.call("linked", "play", "L1", "3", "--player", "P1")
        self.assertEqual(self.path.read_bytes(), before)

    def test_cleanup_step_and_turn_shortcut_expire_same_effects(self):
        self.call("mod", "1", "+1/+1", "--until", "eot")
        self.call("mod", "1", "+2/+2", "--until", "permanent")
        self.call("grant", "1", "flying", "--until", "eot")
        self.call("grant", "1", "vigilance", "--until", "permanent")
        self.call("effect", "add", "temporary", "--until", "eot")
        self.call("effect", "add", "lasting")
        self.call("damage", "1", "1")
        self.call("fx", "add", "--src", "1", "--ability", "bonus", "--scope", "fixed",
                  "--targets", "1", "--until", "eot", "--pt", "+1/+1")
        self.call("linked", "exile", "3", "--src", "1", "--ability", "permission",
                  "--play-until", "eot", "--player", "P1")
        original = self.read()
        self.call("phase", "to", "ending.cleanup")
        ended = self.read()
        self.write(original)
        self.call("turn", "next")
        shortcut = self.read()
        for field in ("mods", "grants", "damage"):
            self.assertEqual(ended["objects"]["1"][field], shortcut["objects"]["1"][field])
        self.assertEqual(ended["objects"]["1"]["damage"], 0)
        self.assertEqual(len(ended["objects"]["1"]["mods"]), 1)
        self.assertEqual(ended["objects"]["1"]["grants"][0]["kw"], "vigilance")
        for field in ("fx", "effects", "links"):
            self.assertEqual(ended[field], shortcut[field])
        self.assertEqual(ended["fx"], [])
        self.assertEqual([e["text"] for e in ended["effects"]], ["lasting"])
        self.assertIsNone(ended["links"][0]["permission"])

    def test_entry_notices_group_candidates_without_hiding_other_abilities(self):
        st = self.read()
        st["cards"]["Scout"]["oracle"] = ("Whenever a creature you control enters, gain 1 life.\n"
                                            "Whenever a creature you control enters, draw a card.")
        self.write(st)
        self.call("remind", "add", "CHECK", "--on", "enter", "--player", "P1", "--src", "1")
        output = self.call("token", "P1", "FixtureToken", "--power", "1", "--toughness", "1", "-n", "3")
        self.assertEqual(output.count("戦場登場の確認:"), 1)
        self.assertEqual(output.count("誘発の確認:"), 2)
        self.assertEqual(output.count("候補の登場 3件"), 2)
        self.assertEqual(output.count("CHECK"), 1)
        output = self.call("move", "1", "P2:battlefield")
        self.assertNotIn("戦場登場の確認", output)

    def test_shared_file_parsing_keeps_save_units_and_bom_support(self):
        for mode in ("run", "pending"):
            with self.subTest(mode=mode):
                start = self.read()["players"]["P1"]["life"]
                file = self.file('\ufeff# comment\n\nlife P1 1\nlife P1 2\n')
                if mode == "pending":
                    self.stack()
                    self.resolve(file)
                else:
                    self.call("run", file)
                self.assertEqual(self.read()["players"]["P1"]["life"], start + 3)
                self.call("undo")
                self.assertEqual(self.read()["players"]["P1"]["life"], start if mode == "pending" else start + 1)

    def test_shared_parser_reports_original_line_before_any_save(self):
        for mode in ("run", "pending"):
            if mode == "pending":
                self.stack()
            before = self.path.read_bytes()
            file = self.file('\ufeff# comment\nlife P1 2\n\nlife P1 nope\n')
            with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as error:
                if mode == "pending":
                    self.resolve(file)
                else:
                    mtg.dispatch(["--state", str(self.path), "--offline", "run", file])
            self.assertIn(":4:", str(error.exception) if mode == "pending" else output.getvalue())
            self.assertEqual(self.path.read_bytes(), before)

    def test_outer_compact_batch_stops_after_failed_resolution(self):
        self.stack()
        effect = self.file("life P1 2\nmove 99 hand\n")
        outer = self.file('pending resolve T1 --file "' + effect.replace('\\', '/') + '" --part x\nlife P1 9\n', "outer.mtg")
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            self.call("run", outer, "--compact", "--delta")
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
