"""Behavioral checks for derived effects and incarnation-safe exile links."""
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import mtg
import relations


class RelationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / "g.json"
        self.call("init", "--seed", "11")
        st = self.read()
        st["phase"] = "precombat_main"
        for oid in range(1, 8):
            name = "fixture" + str(oid)
            st["objects"][str(oid)] = mtg.new_object(oid, name, "P1")
            st["zones"]["P1:battlefield"].append(oid)
            st["cards"][name] = dict(token=True, types=["Creature"], power="2", toughness="2", oracle="")
        for oid in (3, 4):
            st["cards"]["fixture" + str(oid)].update(types=["Artifact"], subtypes=["Equipment"])
        st["cards"]["fixture6"].update(types=["Enchantment"], subtypes=["Aura"])
        st["objects"]["6"]["attached_to"] = 1
        st["next_oid"] = 8
        self.write(st)

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, st):
        self.path.write_text(json.dumps(st), encoding="utf-8")

    def call(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mtg.dispatch(["--state", str(self.path), "--offline", *argv])
        return out.getvalue()

    def fx(self, source="3", *extras):
        return self.call("fx", "add", "--src", source, "--ability", "bonus", "--pt", "+1/+0", *extras)

    def pt(self, oid):
        st = self.read()
        return mtg.pt(st, mtg.obj(st, oid))

    def link(self, mode="until-source-leaves", *extras):
        return self.call("linked", "exile", "1", "--src", "5@1", "--ability", "exile", "--return", mode, *extras)

    def test_reattach_derives_both_targets_preserves_other_sources_and_undo(self):
        self.call("attach", "3", "--to", "1")
        self.call("attach", "4", "--to", "1")
        self.fx("3", "--grant", "Trample")
        self.fx("4", "--grant", "Double strike")
        before = self.read()
        output = self.call("attach", "3", "--to", "2")
        self.assertEqual(self.pt(1), (3, 2))
        self.assertEqual(self.pt(2), (3, 2))
        self.assertEqual(mtg.granted(self.read(), 1), ["Double strike"])
        self.assertTrue(mtg.has_kw(self.read(), 2, "trample"))
        self.assertIn("旧mod・grant除去", output)
        self.assertEqual(self.read()["objects"]["2"]["mods"], [])
        self.call("undo")
        self.assertEqual(self.read(), before)

    def test_counters_set_correction_and_readonly_are_idempotent(self):
        self.call("attach", "3", "--to", "1")
        self.fx("3", "--counter", "charge", "--per-counter", "+2/+1")
        self.call("counter", "3", "charge", "3", "--set")
        self.assertEqual(self.pt(1), (9, 5))
        for _ in range(2):
            self.call("fx", "set", "E1", "--src", "3", "--ability", "bonus", "--pt", "+2/+2")
        self.assertEqual(self.pt(1), (4, 4))
        before = self.path.read_bytes()
        self.call("fx", "list")
        self.call("fx", "check")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.read()["fx"]), 1)

    def test_duplicate_attached_definition_rejected_even_after_detach(self):
        self.call("attach", "3", "--to", "1")
        self.fx()
        for target in (None, "2"):
            self.call("attach", "3", *( ["--to", target] if target else ["--detach"] ))
            before = self.path.read_bytes()
            with self.assertRaisesRegex(SystemExit, "E1.*attach.*fx set"):
                self.fx()
            self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.pt(2), (3, 2))
        self.call("fx", "set", "E1", "--src", "3", "--ability", "bonus", "--pt", "+2/+0")
        self.assertEqual(self.pt(2), (4, 2))

    def test_distinct_abilities_and_repeated_resolved_effects_can_add(self):
        self.call("attach", "3", "--to", "1")
        self.fx()
        self.call("fx", "add", "--src", "3", "--ability", "other", "--pt", "+1/+0")
        for _ in range(2):
            self.fx("3", "--scope", "fixed", "--targets", "1", "--until", "eot")
        self.assertEqual(self.pt(1), (6, 2))

    def test_blinked_source_can_register_same_ability_again(self):
        self.fx()
        self.call("move", "3", "exile")
        self.call("move", "3", "battlefield")
        self.call("attach", "3", "--to", "1")
        self.fx()
        self.assertEqual(self.pt(1), (3, 2))

    def test_invalid_src_and_legacy_migration_do_not_double_count(self):
        self.call("attach", "3", "--to", "1")
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            self.call("grant", "2", "Trample", "--src", "3")
        self.assertEqual(self.path.read_bytes(), before)
        self.call("mod", "1", "+2/+0", "--until", "attached", "--src", "3")
        self.call("grant", "1", "Trample", "--src", "3")
        with self.assertRaises(SystemExit):
            self.fx()
        self.fx("3", "--replace-legacy", "--grant", "Trample")
        self.assertEqual(self.pt(1), (3, 2))
        self.assertEqual(self.read()["objects"]["1"]["grants"], [])
        with self.assertRaises(SystemExit):
            self.call("mod", "1", "+2/+0", "--until", "attached", "--src", "3")

    def test_grant_clear_source_preserves_other_source(self):
        for oid, kw in (("3", "Trample"), ("4", "Haste")):
            self.call("attach", oid, "--to", "1")
            self.call("grant", "1", kw, "--src", oid)
        self.call("grant", "1", "--clear", "--src", "3")
        self.assertEqual(mtg.granted(self.read(), 1), ["Haste"])

    def test_source_blink_ends_static_but_not_resolved_effect(self):
        self.call("attach", "3", "--to", "1")
        self.fx()
        self.call("fx", "add", "--src", "3", "--ability", "pump", "--scope", "fixed", "--targets", "1", "--until", "eot", "--pt", "+2/+2")
        self.call("move", "3", "exile")
        self.call("move", "3", "battlefield")
        self.call("attach", "3", "--to", "2")
        self.assertEqual(self.pt(1), (4, 4))
        self.assertEqual(self.pt(2), (2, 2))
        self.assertEqual(self.read()["objects"]["3"]["incarnation"], 3)
        self.call("move", "1", "exile")
        self.call("move", "1", "battlefield")
        self.assertEqual(self.pt(1), (2, 2))

    def test_static_selector_and_snapshot_have_different_recipients(self):
        self.call("fx", "add", "--src", "5", "--ability", "anthem", "--scope", "controller-creatures", "--pt", "+1/+1")
        self.call("fx", "add", "--src", "5", "--ability", "pump", "--scope", "fixed", "--targets", "1", "2", "--until", "eot", "--pt", "+2/+0")
        self.assertEqual(self.pt(1), (5, 3))
        self.assertEqual(self.pt(7), (3, 3))
        self.call("move", "5", "P2:battlefield")
        self.assertEqual(self.pt(1), (4, 2))
        self.assertEqual(self.read()["objects"]["5"]["incarnation"], 1)

    def test_base_setting_precedes_additive_and_counters(self):
        self.call("fx", "add", "--src", "5", "--ability", "base", "--scope", "fixed", "--targets", "1", "--until", "eot", "--base-pt", "0/1")
        self.call("counter", "1", "+1/+1", "2")
        self.assertEqual(self.pt(1), (2, 3))
        self.call("phase", "to", "ending.cleanup")
        self.assertEqual(self.pt(1), (4, 4))

    def test_until_leave_returns_and_undo_restores_whole_event(self):
        self.link()
        before = self.read()
        self.call("move", "5", "graveyard")
        st = self.read()
        self.assertIn(1, st["zones"]["P1:battlefield"])
        self.assertEqual(st["links"][0]["status"], "returned")
        self.assertEqual(st["zones"]["stack"], [])
        self.call("undo")
        self.assertEqual(self.read(), before)

    def test_stale_source_does_not_exile(self):
        self.call("move", "5", "graveyard")
        output = self.link()
        self.assertIn("追放しません", output)
        self.assertIn(1, self.read()["zones"]["P1:battlefield"])
        self.assertEqual(self.read()["links"], [])

    def test_exiled_card_moved_and_reexiled_is_not_returned(self):
        self.link()
        self.call("move", "1", "hand")
        self.call("move", "1", "exile")
        self.call("move", "5", "graveyard")
        self.assertIn(1, self.read()["zones"]["P1:exile"])

    def test_leave_trigger_pending_counter_and_resolve(self):
        self.link("leave-trigger")
        self.call("move", "5", "graveyard")
        with self.assertRaises(SystemExit):
            self.call("turn", "next")
        self.call("linked", "trigger", "L1")
        with self.assertRaises(SystemExit):
            self.call("stack", "pop")
        self.call("linked", "counter", "L1")
        self.assertIn(1, self.read()["zones"]["P1:exile"])
        self.call("undo")
        self.call("linked", "resolve", "L1")
        self.assertIn(1, self.read()["zones"]["P1:battlefield"])

    def test_delayed_return_stops_advance_at_end_step(self):
        self.link("next-end")
        self.call("move", "5", "graveyard")
        self.call("phase", "to", "ending.cleanup")
        self.assertEqual(self.read()["phase"], "ending.end")
        self.call("linked", "trigger", "L1")
        self.call("linked", "resolve", "L1")
        self.call("phase", "to", "ending.cleanup")
        self.assertIn(1, self.read()["zones"]["P1:battlefield"])

    def test_next_end_created_during_end_waits_for_following_turn(self):
        self.call("phase", "to", "ending.end")
        self.link("next-end")
        self.call("turn", "next")
        self.call("phase", "to", "ending.end")
        self.assertEqual(self.read()["links"][0]["status"], "pending")

    def test_permission_expires_and_only_tracks_exile_incarnation(self):
        self.link("none", "--play-until", "eot", "--player", "P1")
        self.assertEqual(self.read()["links"][0]["permission"]["player"], "P1")
        self.call("phase", "to", "ending.cleanup")
        self.assertIsNone(self.read()["links"][0]["permission"])

    def test_sba_source_death_returns_link_and_host_death_removes_aura(self):
        self.link()
        self.call("damage", "5", "2")
        self.call("sba", "--apply-deaths")
        self.assertIn(1, self.read()["zones"]["P1:battlefield"])
        self.assertIn(6, self.read()["zones"]["P1:graveyard"])

    def test_manual_guard_rejects_combat_without_state_change(self):
        self.fx("3", "--manual", "能力喪失との依存関係を確認")
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            self.call("combat", "damage")
        self.assertEqual(self.path.read_bytes(), before)
        self.call("fx", "remove", "E1")

    def test_multi_exile_and_source_blink_do_not_merge_groups(self):
        self.call("linked", "exile", "1", "2", "--src", "5@1", "--ability", "exile", "--return", "until-source-leaves")
        self.call("move", "5", "exile")
        self.call("move", "5", "battlefield")
        self.assertTrue({1, 2}.issubset(self.read()["zones"]["P1:battlefield"]))
        self.call("linked", "exile", "7", "--src", "5@3", "--ability", "exile", "--return", "until-source-leaves")
        self.assertEqual(self.read()["links"][0]["source"]["incarnation"], 1)
        self.assertEqual(self.read()["links"][1]["source"]["incarnation"], 3)

    def test_attach_invalid_is_atomic_and_player_aura_is_supported(self):
        before = self.path.read_bytes()
        for target in ("3", "P1", "99"):
            with self.assertRaises(SystemExit):
                self.call("attach", "3", "--to", target)
            self.assertEqual(self.path.read_bytes(), before)
        self.call("attach", "6", "--to", "P2")
        self.assertEqual(self.read()["objects"]["6"]["attached_to"], "P2")

    def test_batch_keeps_first_success_and_does_not_repeat_failed_effect(self):
        batch = pathlib.Path(self.tmp.name) / "b.mtg"
        batch.write_text('attach 3 --to 1\nfx add --src 3 --ability bonus --pt +1/+0\nattach 3 --to 99\n', encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.call("run", str(batch), "--compact", "--delta")
        self.assertEqual(self.pt(1), (3, 2))
        self.assertEqual(len(self.read()["fx"]), 1)

    def test_linked_play_checks_player_timing_and_consumes_incarnation(self):
        self.link("none", "--play-until", "eot", "--player", "P1")
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            self.call("linked", "play", "L1", "1", "--player", "P2")
        self.assertEqual(self.path.read_bytes(), before)
        self.call("linked", "play", "L1", "1", "--player", "P1")
        self.assertIn(1, self.read()["zones"]["stack"])
        self.call("move", "1", "exile")
        with self.assertRaises(SystemExit):
            self.call("linked", "play", "L1", "1", "--player", "P1")

    def test_source_on_stack_is_remembered_if_it_leaves_before_resolution(self):
        self.call("stack", "push", "Exile", "--ability", "--src", "5", "--ability-key", "etb")
        self.call("move", "5", "graveyard")
        out = self.call("linked", "exile", "1", "--via", "8", "--return", "until-source-leaves")
        self.assertIn("追放しません", out)
        self.assertIn(1, self.read()["zones"]["P1:battlefield"])

    def test_phase_set_cannot_skip_due_delayed_trigger(self):
        self.link("next-end")
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            self.call("phase", "set", "ending.cleanup")
        self.assertEqual(self.path.read_bytes(), before)

    def test_simultaneous_sba_deaths_recompute_anthem_until_stable(self):
        self.call("fx", "add", "--src", "5", "--ability", "anthem", "--scope", "controller-creatures", "--pt", "+0/+1")
        self.call("damage", "5", "3")
        self.call("damage", "1", "2")
        self.call("sba", "--apply-deaths")
        self.assertTrue({1, 5, 6}.issubset(self.read()["zones"]["P1:graveyard"]))

    def test_spell_effect_tracks_resolution_but_not_later_blink(self):
        self.call("move", "1", "hand")
        self.call("stack", "push", "1")
        self.call("fx", "add", "--src", "5", "--ability", "spell-bonus", "--scope", "fixed", "--targets", "1", "--until", "eot", "--pt", "+1/+1")
        self.call("move", "1", "battlefield")
        self.assertEqual(self.pt(1), (3, 3))
        self.call("move", "1", "exile")
        self.call("move", "1", "battlefield")
        self.assertEqual(self.pt(1), (2, 2))

    def test_all_zone_commands_increment_incarnation(self):
        self.call("move", "1", "library")
        initial = self.read()["objects"]["1"]["incarnation"]
        self.call("draw", "P1", "1")
        self.call("bottom", "P1", "1")
        self.call("search", "P1", "--oid", "1", "--to", "hand")
        self.assertEqual(self.read()["objects"]["1"]["incarnation"], initial + 3)
        self.call("move", "1", "library")
        self.call("mill", "P1", "1")
        self.assertEqual(self.read()["objects"]["1"]["incarnation"], initial + 5)

    def test_duplicate_exile_is_atomic(self):
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            self.call("linked", "exile", "1", "1", "--src", "5", "--ability", "exile")
        self.assertEqual(self.path.read_bytes(), before)

    def test_reattach_updates_base_setting_timestamp_but_same_target_does_not(self):
        for oid, base in (("3", "1/1"), ("4", "4/4")):
            self.call("attach", oid, "--to", "1")
            self.call("fx", "add", "--src", oid, "--ability", "base", "--base-pt", base)
        self.assertEqual(self.pt(1), (4, 4))
        self.call("attach", "3", "--to", "1")
        self.assertEqual(self.pt(1), (4, 4))
        self.call("attach", "3", "--to", "2")
        self.call("attach", "3", "--to", "1")
        self.assertEqual(self.pt(1), (1, 1))

    def test_old_state_migration_is_readonly_and_future_schema_is_rejected(self):
        st = self.read()
        for key in ("schema_version", "fx", "links", "next_effect", "next_link", "source_history"):
            st.pop(key, None)
        for o in st["objects"].values():
            o.pop("incarnation", None)
        self.write(st)
        before = self.path.read_bytes()
        self.call("fx", "check")
        self.assertEqual(self.path.read_bytes(), before)
        self.fx()
        self.assertEqual(self.read()["schema_version"], 2)
        st = self.read()
        st["schema_version"] = 3
        self.write(st)
        with self.assertRaises(SystemExit):
            self.call("fx", "list")

    def test_token_source_can_disappear_before_delayed_effect_is_created(self):
        st = self.read()
        st["objects"]["5"]["token"] = True
        self.write(st)
        self.call("stack", "push", "Blink", "--ability", "--src", "5")
        self.call("move", "5", "graveyard")
        self.call("sba", "--apply")
        self.assertNotIn("5", self.read()["objects"])
        self.call("linked", "exile", "1", "--via", "8", "--return", "next-end")
        self.call("stack", "pop")
        self.call("phase", "to", "ending.end")
        self.call("linked", "trigger", "L1")
        self.call("linked", "resolve", "L1")
        self.assertIn(1, self.read()["zones"]["P1:battlefield"])

    def test_pending_triggers_must_all_be_registered_before_resolving(self):
        for oid in ("1", "2"):
            self.call("linked", "exile", oid, "--src", "5", "--ability", "exile", "--return", "next-end")
        self.call("phase", "to", "ending.end")
        self.call("linked", "trigger", "L1")
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            self.call("linked", "resolve", "L1")
        self.assertEqual(self.path.read_bytes(), before)
        self.call("linked", "trigger", "L2")
        self.call("linked", "resolve", "L2")
        self.call("linked", "resolve", "L1")
        self.assertTrue({1, 2}.issubset(self.read()["zones"]["P1:battlefield"]))


if __name__ == "__main__":
    unittest.main()
