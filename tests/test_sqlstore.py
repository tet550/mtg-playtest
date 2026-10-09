"""SQLite の保存先（sqlstore）: 対局フォルダからの移行・一覧の要約・トランザクション・同時の書き込み・CLI の --db。

共通の処理（apply・undo・replay など）は、MTGTABLE_TEST_STORE=sqlite で他のテストを丸ごと回して確かめる。"""
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import GameStore, cli, play  # noqa: E402
from mtgtable.sqlstore import SqliteGames, SqliteGameStore  # noqa: E402
from mtgtable.store import StaleCursor  # noqa: E402
from helpers import game  # noqa: E402

KEEP = {"act": [{"op": "declare", "kind": "keep"}]}


class SqliteStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.db = self.dir / "data" / "mtg.sqlite"
        self.st = SqliteGameStore(self.db, "g")
        self.st.create(game().state)

    def tearDown(self):
        self.tmp.cleanup()

    def test_work_files_sit_next_to_the_db(self):
        self.assertEqual(self.st.root, self.dir / "data" / "mtg-files" / "g")
        with self.assertRaises(ValueError):
            SqliteGameStore(self.db, "../x")
        with self.assertRaises(LookupError):
            SqliteGames(self.db).open(".hidden")
        with self.assertRaises(FileExistsError):
            self.st.create(game().state)

    def test_summary_tracks_the_end_of_the_game(self):
        g = self.st.summary()
        self.assertEqual((g["id"], g["turn"], g["ended"]), ("g", 0, None))
        self.assertEqual([p["status"] for p in g["players"]], ["playing", "playing"])
        before = self.st.stamp()
        self.st.apply({"actor": "p2", "acts": [{"act": [{"op": "declare", "kind": "concede"}]}]})
        self.assertNotEqual(self.st.stamp(), before)  # 観戦・SSE が変化に気づく
        self.assertIsNotNone(self.st.summary()["ended"])
        self.st.undo()
        self.assertIsNone(self.st.summary()["ended"])  # 巻き戻して決着前に戻れば消える

    def test_games_are_listed_newest_first(self):
        other = SqliteGameStore(self.db, "h")
        other.create(game().state)
        import time
        time.sleep(1.1)  # 日時は秒単位。g を後から更新する
        self.st.apply({"actor": "p1", "acts": [KEEP]})
        self.assertEqual(SqliteGames(self.db).ids(), ["g", "h"])

    def test_a_failure_inside_the_lock_rolls_everything_back(self):
        with self.assertRaises(RuntimeError):
            with self.st.lock():
                self.st._write_log([{"seq": 1, "x": 1}], keep=0)
                self.st.write_doc("seats", {"p1": {}})
                raise RuntimeError("boom")
        self.assertEqual((self.st.read_log(), self.st.read_doc("seats"), self.st.cursor()), ([], None, 0))

    def test_concurrent_writers_with_the_same_expect(self):
        results = []
        barrier = threading.Barrier(4)

        def writer(seat):
            st = SqliteGameStore(self.db, "g")  # サーバーは要求ごとに開く
            barrier.wait()
            try:
                st.apply({"actor": seat, "acts": [{"act": [{"op": "declare", "kind": "say", "text": seat}]}]}, expect=0)
                results.append("ok")
            except StaleCursor:
                results.append("stale")
        ts = [threading.Thread(target=writer, args=(p,)) for p in ("p1", "p2", "p1", "p2")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sorted(results), ["ok", "stale", "stale", "stale"])  # 1人だけ書け、残りは 409
        self.assertEqual(len(self.st.read_log()), 1)

    def test_undo_then_apply_drops_the_redo_history(self):
        for amount in (1, 2, 3):
            self.st.apply({"actor": "p1", "acts": [{"act": [{"op": "life_loss", "amount": amount}]}]})
        self.st.undo(2)
        self.st.apply({"actor": "p1", "acts": [{"act": [{"op": "draw"}]}]})
        self.assertEqual([e["seq"] for e in self.st.read_log()], [1, 2])
        self.assertEqual(self.st.read_log()[1]["act"][0]["op"], "draw")
        self.assertEqual(self.st.replay().to_dict(), self.st.load().to_dict())

    def test_import_from_a_game_folder(self):
        src = GameStore(self.dir / "playtest" / "g1")
        src.create(game().state)
        src.apply({"actor": "p1", "acts": [KEEP]})
        src.apply({"actor": "p2", "acts": [KEEP]})
        src.undo()  # Redo 履歴も移す
        token = play.invite(src, "p1")
        play.set_stops(src, "p1", ["opp:end"])
        (src.root / "prompts" / "judge").mkdir(parents=True)
        (src.root / "prompts" / "judge" / "latest.md").write_text("prompt", encoding="utf-8")

        dst = SqliteGameStore(self.db, "g1")
        dst.import_from(src)
        self.assertEqual(dst.read_log(), src.read_log())
        self.assertEqual((dst.cursor(), dst.load().to_dict()), (1, src.load().to_dict()))
        self.assertEqual(dst.initial().to_dict(), src.initial().to_dict())
        self.assertTrue(play.check_token(dst, "p1", token))
        self.assertEqual(play.get_stops(dst, "p1"), ["opp:end"])
        self.assertEqual((dst.root / "prompts" / "judge" / "latest.md").read_text(encoding="utf-8"), "prompt")
        self.assertEqual(dst.redo(), 2)
        with self.assertRaises(FileExistsError):
            dst.import_from(src)

    def test_cli_uses_the_db(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["--db", str(self.db), "fork", "playtest/g", "playtest/g2"])
            cli.main(["--db", str(self.db), "log", "playtest/g2"])
        self.assertIn("forked", out.getvalue())
        self.assertTrue(SqliteGameStore(self.db, "g2").exists())  # パスの最後の名前が対局の id
        self.assertFalse((self.dir / "playtest").exists())  # 対局フォルダは作らない
        src = GameStore(self.dir / "folder" / "f1")
        src.create(game().state)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["--db", str(self.db), "db-import", str(src.root)])
        self.assertEqual(json.loads(json.dumps(SqliteGameStore(self.db, "f1").load().to_dict())),
                         src.load().to_dict())


if __name__ == "__main__":
    unittest.main()
