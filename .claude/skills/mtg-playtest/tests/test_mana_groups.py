import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import mtg
import mana


class ManaGroupsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / "state.json"
        self.call("init", "--seed", "1")

    def call(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mtg.dispatch(["--state", str(self.path), "--offline", *args])
        return out.getvalue()

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_normal_and_noted_mana_never_pay_from_each_other(self):
        self.call("mana", "P1", "add", "G")
        self.call("mana", "P1", "add", "GGG", "--note", "能力の起動のみ")
        before = self.path.read_bytes()
        for args in [("2",), ("4", "--group", "M1")]:
            with self.assertRaises(SystemExit):
                self.call("mana", "P1", "spend", *args)
            self.assertEqual(self.path.read_bytes(), before)
        self.call("mana", "P1", "spend", "G")
        out = self.call("mana", "P1", "spend", "2", "--group", "M1")
        self.assertIn("能力の起動のみ", out)
        p = self.read()["players"]["P1"]
        self.assertEqual(p["pool"], {})
        self.assertEqual(p["mana_groups"]["M1"]["pool"], {"G": 1})

    def test_same_color_different_notes_and_explicit_top_up(self):
        self.call("mana", "P1", "add", "R", "--note", "能力のみ")
        self.call("mana", "P1", "add", "R", "--note", "打ち消されない")
        self.call("mana", "P1", "add", "RR", "--group", "M1")
        self.call("mana", "P1", "spend", "3", "--group", "M1")
        groups = self.read()["players"]["P1"]["mana_groups"]
        self.assertNotIn("M1", groups)
        self.assertEqual(groups["M2"], {"note": "打ち消されない", "pool": {"R": 1}})
        self.assertIn("能力のみ", self.call("log"))
        self.call("mana", "P1", "add", "G", "--note", "new")
        self.assertIn("M3", self.read()["players"]["P1"]["mana_groups"])

    def test_groups_belong_to_the_requested_player(self):
        self.call("mana", "P1", "add", "G", "--note", "one")
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            self.call("mana", "P2", "spend", "G", "--group", "M1")
        self.assertEqual(self.path.read_bytes(), before)
        self.call("mana", "P2", "add", "R", "--note", "two")
        self.call("mana", "P2", "spend", "R", "--group", "M1")
        self.assertEqual(self.read()["players"]["P1"]["mana_groups"]["M1"]["pool"], {"G": 1})

    def test_generic_failure_does_not_mutate_input_pool(self):
        pool = {"R": 1, "G": 1}
        with self.assertRaises(SystemExit):
            mana.spend(pool, "2R")
        self.assertEqual(pool, {"R": 1, "G": 1})
        self.assertEqual(mana.spend({"R": 2, "C": 1}, "1R"), {"R": 1})
        with self.assertRaises(SystemExit):
            mana.spend({"G": 2}, "C")

    def test_invalid_options_leave_state_unchanged(self):
        before = self.path.read_bytes()
        for args in [("add", "2"), ("add", "0"), ("add", "X"), ("add",),
                     ("spend",), ("add", "G", "--note", " "),
                     ("add", "G", "--note", "x", "--group", "normal"),
                     ("spend", "G", "--note", "x"), ("clear", "G"),
                     ("clear", "--group", "M99")]:
            with self.subTest(args=args), self.assertRaises(SystemExit):
                self.call("mana", "P1", *args)
            self.assertEqual(self.path.read_bytes(), before)

    def test_list_and_show_retain_note_without_saving(self):
        self.call("mana", "P1", "add", "GG", "--note", "能力の起動のみ")
        before = self.path.read_bytes()
        count = len(list((self.path.parent / "history" / "state").glob("*.json")))
        for args in [("mana", "P1", "list"), ("show",)]:
            out = self.call(*args)
            self.assertIn("M1", out)
            self.assertIn("能力の起動のみ", out)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(list((self.path.parent / "history" / "state").glob("*.json"))), count)

    def test_clear_and_all_phase_boundaries_expire_both_pools(self):
        self.call("mana", "P1", "add", "G")
        self.call("mana", "P1", "add", "R", "--note", "one")
        original = self.path.read_bytes()
        for args in [("mana", "P1", "clear"), ("phase", "next"),
                     ("phase", "to", "precombat_main"), ("phase", "set", "precombat_main"),
                     ("turn", "next")]:
            with self.subTest(args=args):
                self.path.write_bytes(original)
                self.call(*args)
                p = self.read()["players"]["P1"]
                self.assertEqual(p["pool"], {})
                self.assertEqual(p["mana_groups"], {})
        self.call("mana", "P1", "add", "G", "--note", "new")
        self.assertIn("M2", self.read()["players"]["P1"]["mana_groups"])

    def test_targeted_clear_and_undo_restore_note_and_quantity(self):
        self.call("mana", "P1", "add", "G")
        self.call("mana", "P1", "add", "R", "--note", "one")
        before = self.read()
        self.call("mana", "P1", "clear", "--group", "M1")
        self.assertEqual(self.read()["players"]["P1"]["pool"], {"G": 1})
        self.call("undo")
        self.assertEqual(self.read(), before)
        self.call("mana", "P1", "clear", "--group", "normal")
        self.assertIn("M1", self.read()["players"]["P1"]["mana_groups"])

    def test_resolution_rolls_back_all_group_payments(self):
        self.call("mana", "P1", "add", "G")
        self.call("mana", "P1", "add", "R", "--note", "one")
        self.call("pending", "add", "test", "--controller", "P1")
        self.call("pending", "stack", "T1")
        f = self.path.parent / "effects.mtg"
        f.write_text("mana P1 spend G\nmana P1 spend 2 --group M1\n", encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            self.call("pending", "resolve", "T1", "--file", str(f), "--part", "pay")
        self.assertEqual(self.path.read_bytes(), before)
        f.write_text("mana P1 spend G\nmana P1 spend R --group M1\n", encoding="utf-8")
        self.call("pending", "resolve", "T1", "--file", str(f), "--part", "pay", "--pause")
        self.call("mana", "P1", "list")
        self.call("undo")
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
