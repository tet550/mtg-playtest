"""卓（エンジンと保存）の不変の仕様: 再現性・Act の原子性・公開範囲・同時の書き込み・置き場所。"""
import json
import os
import pathlib
import tempfile
import threading
import unittest
from unittest import mock

from helpers import game, hand, site_db, stores
from mtgtable import paths, player_view
from mtgtable.sqlstore import SqliteGameStore
from mtgtable.store import StaleCursor

REPO = pathlib.Path(__file__).resolve().parents[1]


class TableTest(unittest.TestCase):
    def test_same_seed_and_log_reproduce_the_same_state(self):
        # 乱数は (seed, version) から決まる。初期状態と log を適用し直せば同じ状態になる（Replay・Undo の前提）
        self.assertEqual(game(seed=5).state.to_dict(), game(seed=5).state.to_dict())
        with tempfile.TemporaryDirectory() as tmp:
            for st in stores(tmp):
                st.create(game().state)
                st.apply({"actor": "p1", "acts": [{"act": [{"op": "shuffle", "zone": "library"}]}]})
                st.apply({"actor": "p1", "acts": [{"act": [{"op": "move", "card": {"zone": "hand", "random": 2},
                                                            "to": "graveyard"}]}]})
                after = st.load().to_dict()
                self.assertEqual(st.replay().to_dict(), after, st)
                self.assertEqual((st.undo(), st.redo()), (1, 2))
                self.assertEqual(st.load().to_dict(), after, st)
                st.undo()
                st.apply({"actor": "p1", "acts": [{"act": [{"op": "draw"}]}]})
                self.assertEqual(len(st.read_log()), 2, st)  # 書いたら Redo の履歴は捨てる

    def test_an_act_is_atomic_and_a_batch_stops_only_on_failure(self):
        e = game()
        before = e.state.to_dict()
        r = e.apply_act("p1", {"act": [{"op": "draw"}, {"op": "move", "card": "nope", "to": "exile"}]})
        self.assertEqual((r.status, e.state.to_dict()), ("failed", before))  # 丸ごと適用しないか、丸ごと適用する
        # 知らないカードを引いても止めない（区切るのは操作する側）。止まるのは失敗と前提不成立だけで、前の Act は残る
        res = e.apply_batch({"actor": "p1", "acts": [
            {"act": [{"op": "draw"}]},
            {"act": [{"op": "move", "card": {"zone": "hand", "index": 0}, "to": "graveyard"}]},
            {"act": [{"op": "move", "card": "nope", "to": "exile"}]},
            {"act": [{"op": "draw"}]}]})
        self.assertEqual((res["applied"], res["stopped"]["reason"]), (2, "failed"))
        self.assertEqual([a.status for a in res["acts"]], ["applied", "applied", "failed", "skipped"])
        self.assertEqual(len(res["acts"][0].learned), 1)

    def test_a_player_sees_and_touches_only_what_it_knows(self):
        e = game()
        text = json.dumps(player_view(e.state, "p2"))
        for cid in hand(e, "p1") + e.state.zones["p1.library"].cards:
            self.assertNotIn('"%s"' % cid, text)  # 相手の手札・ライブラリーは id も出さない
        secret = e.state.zones["p2.library"].cards[0]
        r = e.apply_act("p1", {"act": [{"op": "move", "card": secret, "to": "exile"}]})
        self.assertIn("not known", r.error)  # 知らないカードは id で指せない
        r = e.apply_act("p1", {"pre": [{"zone": "p2.library", "top": [secret]}], "act": [{"op": "draw"}]})
        self.assertEqual(r.status, "precondition_failed")  # 前提で中身を探ることもできない
        # 見たカードは覚えている。シャッフルで位置だけを忘れる
        e.apply_act("p1", {"act": [{"op": "look", "cards": {"zone": "library", "top": 1}}]})
        top = e.state.zones["p1.library"].cards[0]
        e.apply_act("p1", {"act": [{"op": "shuffle", "zone": "library"}]})
        lib = player_view(e.state, "p1", library=True)["zones"]["p1.library"]
        self.assertEqual(([c["id"] for c in lib["known_unordered"]], "known_positions" in lib), ([top], False))

    def test_concurrent_writers_with_the_same_expect(self):
        with tempfile.TemporaryDirectory() as tmp:
            SqliteGameStore(site_db(tmp), "g").create(game().state)
            results, barrier = [], threading.Barrier(4)

            def writer(seat):
                st = SqliteGameStore(site_db(tmp), "g")  # サーバーは要求ごとに開く
                barrier.wait()
                try:
                    st.apply({"actor": seat, "acts": [{"act": [{"op": "declare", "kind": "say", "text": seat}]}]},
                             expect=0)
                    results.append("ok")
                except StaleCursor:
                    results.append("stale")
            ts = [threading.Thread(target=writer, args=(p,)) for p in ("p1", "p2", "p1", "p2")]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.assertEqual(sorted(results), ["ok", "stale", "stale", "stale"])  # 1人だけ書け、残りは古い盤面として断る

    def test_paths_in_the_repo_and_in_an_installed_package(self):
        with mock.patch.dict(os.environ):
            for k in ("MTGTABLE_HOME", "MTG_CARDS_DIR"):
                os.environ.pop(k, None)
            self.assertEqual((paths.data_home(), paths.decklists_dir()), (REPO, REPO / "decklists"))
            with mock.patch.object(paths, "checkout", return_value=None):  # リポジトリの外（パッケージとして入れた）
                self.assertNotEqual(paths.data_home(), REPO)
                self.assertEqual(paths.decklists_dir(), paths.ASSETS / "decklists")
                self.assertEqual(paths.references_dir(), paths.ASSETS / "references")


if __name__ == "__main__":
    unittest.main()
