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
import compact_output
import pizza_recipe


class CompactTests(unittest.TestCase):
    def test_filter_keeps_warnings_unknowns_and_inspection(self):
        text = "ok\n!! 誘発の確認: foo\n未知の警告\n"
        self.assertEqual(compact_output.compact(text, ["tap", "1"]), text[3:])
        for command in (["draw", "P1"], ["look", "P1"], ["search", "P1"],
                        ["stack", "show"], ["card", "show", "X"]):
            self.assertEqual(compact_output.compact(text, command), text)
        sba = "- Warrior: 消滅\n確認のみ：状態は変更していません\n"
        self.assertEqual(compact_output.compact(sba, ["sba"]), sba)

    def test_delta_shows_removed_and_added_information(self):
        out = compact_output.delta("P1 life20\nhand A\n", "P1 life19\nhand B\n")
        self.assertIn("-hand A", out)
        self.assertIn("+hand B", out)

    def test_summary_fields_and_unknown_lines(self):
        before = "★P1 You: ライフ 20 | 手札 4 | 山札 50\n未知 A\n"
        after = "★P1 You: ライフ 17 | 手札 4 | 山札 49\n未知 A\n"
        out = compact_output.delta(before, after)
        self.assertIn("ライフ 20→17", out)
        self.assertIn("山札 50→49", out)
        self.assertNotIn("手札", out)
        self.assertIn("-未知 A", compact_output.delta(before, after.replace("未知 A", "未知 B")))

    def test_preflight_all_errors_before_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            st, out, failed = self.run_fixture(pathlib.Path(d), ["--compact"],
                "life P1 -2\nmana P2 add G --unknown-option x\nphase to combat.declare_attackers\n")
            self.assertTrue(failed)
            self.assertEqual(st["players"]["P1"]["life"], 20)
            self.assertIn("batch.mtg:2:", out)
            self.assertIn("batch.mtg:3:", out)
            self.assertFalse(list(pathlib.Path(d).glob("output/game/*.jsonl")))

    def test_pass_macro_preserves_state_history_and_source_reference(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first, _, _ = self.run_fixture(pathlib.Path(a), ["--compact"], "pass P1\npass P2\n")
            second, out, failed = self.run_fixture(pathlib.Path(b), ["--compact"], "# agreed\npass-both P1\n")
            self.assertFalse(failed)
            self.assertEqual(first, second)
            for root in (a, b):
                self.assertEqual(len(list(pathlib.Path(root).glob("history/game/*.json"))), 2)
            records = [json.loads(x) for x in next(pathlib.Path(b).glob("output/game/*.jsonl")).read_text(encoding="utf-8").splitlines()]
            self.assertEqual([x["source_line"] for x in records], [2, 2])
            self.assertEqual([x["command"] for x in records], ["pass P1", "pass P2"])
            self.assertIn(".jsonl:1-2", out)

    def test_seat_cannot_use_pass_macro(self):
        with tempfile.TemporaryDirectory() as d:
            _, out, failed = self.run_fixture(pathlib.Path(d), ["--compact"], "pass-both P1\n", seat="P1")
            self.assertTrue(failed)
            self.assertIn("状態変更なし", out)

    def run_fixture(self, root, flags, body, seat=None):
        state = root / "game.json"
        with contextlib.redirect_stdout(io.StringIO()):
            mtg.dispatch(["--state", str(state), "init", "--seed", "7", "--first", "P1"])
        batch = root / "batch.mtg"
        batch.write_text(body, encoding="utf-8")
        argv = ["--state", str(state)]
        if seat:
            argv += ["--as", seat]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            try:
                mtg.dispatch(argv + ["run", str(batch), *flags])
                failed = False
            except SystemExit:
                failed = True
        return json.loads(state.read_text(encoding="utf-8")), out.getvalue(), failed

    def test_state_history_transcript_and_delta(self):
        body = "life P1 -2\nsba\nshow --hand both\nshow --hand both\n"
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            st1, normal, _ = self.run_fixture(pathlib.Path(a), ["--quiet"], body)
            st2, compact, failed = self.run_fixture(pathlib.Path(b), ["--compact", "--delta"], body)
            self.assertFalse(failed)
            self.assertEqual(st1, st2)
            self.assertEqual(len(list(pathlib.Path(a).glob("history/game/*.json"))),
                             len(list(pathlib.Path(b).glob("history/game/*.json"))))
            self.assertIn("変更なし", compact)
            rows = [json.loads(line) for line in next(pathlib.Path(b).glob("output/game/*.jsonl")).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 4)
            self.assertIn("=== Turn", rows[-1]["stdout"])

    def test_error_stops_and_reports_line(self):
        with tempfile.TemporaryDirectory() as d:
            st, out, failed = self.run_fixture(pathlib.Path(d), ["--compact"],
                                               "life P1 -2\nmove 999 hand\nlife P1 -5\n")
            self.assertTrue(failed)
            self.assertEqual(st["players"]["P1"]["life"], 18)
            self.assertIn("失敗した行: move 999 hand", out)

    def test_new_view_scope_is_shown_in_full(self):
        with tempfile.TemporaryDirectory() as d:
            _, out, failed = self.run_fixture(pathlib.Path(d), ["--compact", "--delta"],
                                              "show\nshow --hand P1\n")
            self.assertFalse(failed)
            self.assertIn("=== Turn", out)
            self.assertIn("の手札", out)

    def test_seat_restriction_survives_delta(self):
        with tempfile.TemporaryDirectory() as d:
            _, out, failed = self.run_fixture(pathlib.Path(d), ["--compact", "--delta"],
                                              "show --hand both\n", seat="P1")
            self.assertTrue(failed)
            self.assertNotIn("=== Turn", out)

    def test_recipe_growing_cost_and_ids(self):
        recipe = pizza_recipe.generate("P2", 70, 71, 92, 7, 145, 5, "verified")
        self.assertEqual(recipe.count("life P2 3"), 5)
        self.assertIn("grant 162 Haste", recipe)
        self.assertIn("mana P2 add " + "G" * 15 + "\n", recipe)
        with self.assertRaises(ValueError):
            pizza_recipe.generate("P2", 70, 71, 92, 5, 145, 5, "verified")

    def test_brief_deck_keeps_problems_and_omits_sideboard_contents(self):
        deck = dict(name="fixture", main_total=1, sideboard_total=1,
                    main=[dict(name="Main card", count=1)],
                    sideboard=[dict(name="Side card", count=1)],
                    problems=["invalid count"], warnings=["unresolved card"])
        args = mtg.build_parser().parse_args(["deck", "show", "fixture", "--brief"])
        with patch.object(mtg.decks, "load", return_value=deck), contextlib.redirect_stdout(io.StringIO()) as out:
            mtg.cmd_deck(args, None)
        self.assertIn("1 Main card", out.getvalue())
        self.assertIn("!! invalid count", out.getvalue())
        self.assertIn("!! unresolved card", out.getvalue())
        self.assertNotIn("Side card", out.getvalue())


if __name__ == "__main__":
    unittest.main()
