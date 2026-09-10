"""Regression cases discovered in the 2026-09-07 Boros vs piza BO1."""
import contextlib
import copy
import io
import pathlib
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import mtg

class RegressionTests(unittest.TestCase):
    def state(self):
        return dict(turn=1, active="P1", phase="precombat_main", priority="P1",
                    passed=[], log=[], effects=[], zones={"stack": [], "P1:battlefield": [1, 2, 3, 4]},
                    players={p: {"pool": {}, "lands_played": 0} for p in ("P1", "P2")},
                    objects={str(i): mtg.new_object(i, "fixture" + str(i), "P1")
                             for i in range(1, 5)}, cards={
                                 "fixture" + str(i): {"token": True, "types": ["Creature"] if i < 3 else ["Artifact"],
                                                     "subtypes": [] if i < 3 else ["Equipment"],
                                                     "power": "2", "toughness": "1"}
                                 for i in range(1, 5)})

    def call(self, fn, args, st):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            fn(args, st)
        return out.getvalue()

    def test_reattach_clears_only_source_and_changes_combat_power(self):
        st = self.state()
        st["objects"]["3"]["attached_to"] = 1
        old = st["objects"]["1"]
        old["mods"] = [{"p": 1, "t": 0, "until": "attached", "src": "3"},
                       {"p": 1, "t": 1, "until": "attached", "src": "4"}]
        old["grants"] = [{"kw": "速攻", "until": "attached", "src": "3"},
                         {"kw": "二段攻撃", "until": "attached", "src": "4"}]
        self.call(mtg.cmd_attach, NS(ref="3", to="2", detach=False), st)
        with patch.object(mtg, "card_of", return_value={"power": 2, "toughness": 1}):
            self.assertEqual(mtg.pt(st, old), (3, 2))
        self.assertEqual(mtg.granted(st, 1), ["二段攻撃"])
        self.assertEqual(st["objects"]["3"]["attached_to"], 2)
        self.assertEqual(st["objects"]["2"]["mods"], [])

    def test_reattach_same_target_keeps_effects(self):
        st = self.state()
        st["objects"]["3"]["attached_to"] = 1
        st["objects"]["1"]["mods"] = [{"p": 1, "until": "attached", "src": 3}]
        before = copy.deepcopy(st["objects"]["1"])
        self.call(mtg.cmd_attach, NS(ref="3", to="1", detach=False), st)
        self.assertEqual(st["objects"]["1"], before)

    def test_nonempty_stack_blocks_phase_and_turn_without_mutation(self):
        for fn, args in [(mtg.cmd_phase, NS(op=op, value="combat.begin"))
                         for op in ("next", "to", "set")] + [
                             (mtg.cmd_turn, NS(op="next", to=None))]:
            with self.subTest(command=fn.__name__, op=args.op):
                st = self.state()
                st["zones"]["stack"] = [4]
                before = copy.deepcopy(st)
                with self.assertRaisesRegex(SystemExit, "スタックが空ではありません"):
                    self.call(fn, args, st)
                self.assertEqual(st, before)

    def test_empty_stack_allows_phase(self):
        st = self.state()
        self.call(mtg.cmd_phase, NS(op="to", value="combat.begin"), st)
        self.assertEqual(st["phase"], "combat.begin")

    def test_granted_haste_suppresses_false_warning_only(self):
        for kw in ("速攻", "haste", None):
            st = self.state()
            if kw:
                st["objects"]["1"]["grants"] = [{"kw": kw}]
            out = self.call(mtg.cmd_attack, NS(refs=["1"], target="P2", no_tap=False), st)
            self.assertEqual("召喚酔い" in out, kw is None)
            self.assertEqual(st["combat"]["attackers"], {"1": "P2"})

if __name__ == "__main__":
    unittest.main()
