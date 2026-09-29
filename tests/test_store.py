"""Operation Log・Undo/Redo・Replay・Diff・Fork。"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import GameStore, state_diff  # noqa: E402
from helpers import game  # noqa: E402


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
