"""Operation Log・Undo/Redo・Replay・Diff・Fork。"""
import pathlib
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import state_diff  # noqa: E402
from helpers import game, store  # noqa: E402


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = store(self.tmp.name, "g")
        self.store.create(game().state)

    def tearDown(self):
        self.tmp.cleanup()

    def test_concurrent_dumps_to_the_same_file(self):
        # server and `auto` may write the same prompt file at once; each writer uses its own temp file
        from mtgtable.store import _dump
        path = pathlib.Path(self.tmp.name) / "pending.json"
        errors = []

        def write(n):
            try:
                for i in range(30):
                    _dump(path, {"n": n, "i": i})
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        ts = [threading.Thread(target=write, args=(n,)) for n in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual([x.name for x in path.parent.glob("pending.json*")], ["pending.json"])

    def test_undo_redo_replay(self):
        st = self.store
        st.apply({"actor": "p1", "acts": [{"act": [{"op": "shuffle", "zone": "library"}]}]})
        st.apply({"actor": "p1", "acts": [{"act": [{"op": "move", "card": {"zone": "hand", "random": 2},
                                                    "to": "graveyard"}]}]})
        after = st.load().to_dict()
        self.assertEqual(st.replay().to_dict(), after)
        self.assertEqual(st.undo(), 1)
        self.assertEqual(len(st.load().zones["p1.graveyard"].cards), 0)
        self.assertEqual(st.redo(), 2)
        self.assertEqual(st.load().to_dict(), after)
        st.undo()
        st.apply({"actor": "p1", "acts": [{"act": [{"op": "draw"}]}]})
        self.assertEqual(len(st.read_log()), 2)  # Redo 履歴は捨てられる
        self.assertEqual(st.read_log()[-1]["act"][0]["op"], "draw")

    def test_undo_to(self):
        st = self.store
        for amount in (1, 2, 3):
            st.apply({"actor": "p1", "acts": [{"act": [{"op": "life_loss", "amount": amount}]}]})
        self.assertEqual(st.undo(to=1), 1)
        self.assertEqual(st.load().players["p1"].life, 19)
        with self.assertRaises(ValueError):
            st.undo(to=5)

    def test_continued_act_is_one_undo_unit(self):
        # 「2枚引く。その後2枚捨てる」: 引いたカードを見てから捨てるので、cont で区切って続きを次の Batch に書く
        st = self.store
        st.apply({"actor": "p1", "acts": [{"act": [{"op": "life_loss", "amount": 1}]}]})
        st.apply({"actor": "p1", "acts": [{"act": [{"op": "draw", "count": 2}], "cont": True}]})
        drawn = st.read_log()[-1]["steps"][0]["op"]
        self.assertEqual(st.read_log()[-1]["cont"], True)
        hand = st.load().zones["p1.hand"].cards
        st.apply({"actor": "p1", "acts": [{"act": [{"op": "move", "cards": hand[-2:], "to": "graveyard"}]}]})
        st.apply({"actor": "p1", "acts": [{"act": [{"op": "life_loss", "amount": 2}]}]})
        self.assertEqual(st.undo(), 3)
        self.assertEqual(st.undo(), 1)  # 引く・捨てるの2件を1つの Act として戻す
        self.assertEqual(st.redo(), 3)
        self.assertEqual(st.undo(to=2), 1)  # Act の途中を指したら、その Act の始まりへ
        self.assertEqual(st.redo(2), 4)
        self.assertEqual(drawn["op"], "draw")

    def test_open_act_at_the_end_is_undone_as_a_whole(self):
        st = self.store
        st.apply({"actor": "p1", "acts": [{"act": [{"op": "draw"}], "cont": True},
                                          {"act": [{"op": "draw"}], "cont": True}]})
        self.assertEqual(st.undo(), 0)

    def test_diff_and_fork(self):
        st = self.store
        st.apply({"actor": "p1", "acts": [{"act": [{"op": "life_loss", "amount": 2}]}]})
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
