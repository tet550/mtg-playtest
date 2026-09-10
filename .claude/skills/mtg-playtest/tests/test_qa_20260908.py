"""Regressions observed during the 2026-09-08 BO1; synthetic public fixtures."""
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


class QARegressionTests(unittest.TestCase):
    def state(self):
        return dict(turn=1, active="P1", phase="precombat_main", log=[],
                    players={p: dict(life=20, poison=0) for p in ("P1", "P2")},
                    zones={"P1:battlefield": [1, 2], "P2:battlefield": [],
                           "P1:graveyard": [], "stack": []},
                    objects={str(i): mtg.new_object(i, "fixture" + str(i), "P1")
                             for i in (1, 2)}, cards={})

    def test_creature_entry_warning_filters_noncreatures_but_keeps_tokens(self):
        for types, expected in [(["Enchantment"], False), (["Land"], False),
                                (["Creature"], True), (["Artifact", "Creature"], True)]:
            with self.subTest(types=types):
                st = self.state()
                st["objects"]["1"]["token"] = True
                def card_of(state, ob):
                    if ob["oid"] == 1:
                        return dict(types=types)
                    return dict(oracle="Whenever a creature you control enters, scry 1 and put a plan counter on this enchantment.")
                with patch.object(mtg, "card_of", side_effect=card_of):
                    hits, _ = mtg.bookkeeping.enter_watchers(mtg, st, 1)
                self.assertEqual(bool(hits), expected)

    def test_mixed_entry_subject_remains_a_candidate(self):
        st = self.state()
        def card_of(state, ob):
            return (dict(types=["Artifact"]) if ob["oid"] == 1 else
                    dict(oracle="Whenever a creature or artifact you control enters, draw a card."))
        with patch.object(mtg, "card_of", side_effect=card_of):
            self.assertEqual(len(mtg.bookkeeping.enter_watchers(mtg, st, 1)[0]), 1)

    def test_sba_report_is_explicit_and_apply_removes_token(self):
        st = self.state()
        st["zones"]["P1:battlefield"] = []
        st["zones"]["P1:graveyard"] = [1]
        st["objects"]["1"]["token"] = True
        before = copy.deepcopy(st)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mtg.cmd_sba(NS(apply=False, apply_deaths=False), st)
        self.assertEqual(st, before)
        self.assertIn("状態は変更していません", out.getvalue())
        self.assertIn("sba --apply", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            mtg.cmd_sba(NS(apply=True, apply_deaths=False), st)
        self.assertEqual(st["zones"]["P1:graveyard"], [])
        self.assertNotIn("1", st["objects"])


if __name__ == "__main__":
    unittest.main()
