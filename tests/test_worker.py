"""サーバーの中で審判と AI の席を回す（worker）: 人間が書いたらすぐ回る・利用の上限で中断・再開・API の失敗・数え方。"""
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import play, worker  # noqa: E402
from mtgtable.web import Handler, start_worker  # noqa: E402
from helpers import db_path, game, store, viewer  # noqa: E402
from test_llm import FakeOpenAI  # noqa: E402


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = store(self.tmp.name, "g")
        self.st.create(game().state)  # 先攻 p1
        self.key = play.invite(self.st, "p1")  # p1 は人間、p2 は AI
        self.api = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=self.api.serve_forever, daemon=True).start()
        FakeOpenAI.requests, FakeOpenAI.fail_next = [], 0
        self.env = dict(os.environ)
        for k in ("MTGTABLE_LLM_PROVIDER", "MTGTABLE_MODEL"):
            os.environ.pop(k, None)
        os.environ.update({"OPENAI_API_KEY": "k", "MTGTABLE_OPENAI_MODEL": "m",
                           "OPENAI_BASE_URL": "http://127.0.0.1:%d/v1" % self.api.server_address[1]})
        self.viewer = viewer(self.tmp.name, offline=True, play=True)
        self.lines = []
        self.worker = start_worker(self.viewer, db_path(self.tmp.name), worker.Limits(), report=self.lines.append)
        self.worker.interval = 0.2
        handler = type("PlayHandler", (Handler,), {"viewer": self.viewer})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        self.worker.stop()
        self.worker.wait_idle()
        os.environ.clear()
        os.environ.update(self.env)
        for h in (self.httpd, self.api):
            h.shutdown()
            h.server_close()
        self.tmp.cleanup()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def settle(self, until, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if until():
                self.assertTrue(self.worker.wait_idle())
                return
            time.sleep(0.05)
        self.fail("the worker did not get there: %s" % self.lines)

    def test_the_human_writes_and_the_server_runs_the_ai_and_the_judge(self):
        self.assertEqual(self.call("/api/games/g/declare", {"seat": "p1", "kind": "keep"})[0], 200)
        self.settle(lambda: self.st.load().turn.turn == 1)  # p2（AI）のキープ → 審判のゲーム開始
        self.assertEqual(play.waiting_on(self.st.load()), "p1")
        self.assertEqual(len(FakeOpenAI.requests), 2)
        self.assertFalse((self.st.root / "prompts" / "judge" / "0001-2.user.md").exists())  # 同じプロンプトを2度作らない
        time.sleep(0.5)
        self.assertEqual(len(FakeOpenAI.requests), 2)  # 人間の番の間は呼ばない
        used = self.worker.budget.used("g", None)
        self.assertEqual((used["game_calls"], used["game_tokens"]), (2, 200))
        self.assertIsNone(self.call("/api/games/g/timeline?seat=p1")[1].get("ai"))

    def test_a_limit_suspends_the_game_and_the_seat_can_resume(self):
        self.worker.budget.limits.calls_per_game = 1
        self.call("/api/games/g/declare", {"seat": "p1", "kind": "keep"})
        self.settle(lambda: worker.suspended(self.st))
        self.assertEqual((len(FakeOpenAI.requests), self.st.load().turn.turn), (1, 0))  # p2 のキープまで、審判は呼ばない
        ai = self.call("/api/games/g/timeline?seat=p1")[1]["ai"]
        self.assertEqual(ai["status"], "suspended")
        self.assertIn("上限（1 回）", ai["reason"])
        time.sleep(0.5)
        self.assertEqual(len(FakeOpenAI.requests), 1)  # 中断している間は回さない
        req = urllib.request.Request(self.base + "/api/games/g/resume", data=b'{"seat": "p1"}',
                                     headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)  # 席の鍵が要る
        self.assertEqual(e.exception.code, 403)
        self.worker.budget.limits.calls_per_game = 10
        self.assertEqual(self.call("/api/games/g/resume", {"seat": "p1"}), (200, {"ok": True}))
        self.settle(lambda: self.st.load().turn.turn == 1)
        self.assertEqual(self.call("/api/games/g/resume", {"seat": "p1"})[0], 403)  # もう中断していない

    def test_api_failures_suspend_the_game(self):
        os.environ.pop("OPENAI_API_KEY")
        from mtgtable import llm
        saved, llm.KEY_FILE = llm.KEY_FILE, pathlib.Path(self.tmp.name) / "no-key"
        try:
            self.call("/api/games/g/declare", {"seat": "p1", "kind": "keep"})
            self.settle(lambda: worker.suspended(self.st))
        finally:
            llm.KEY_FILE = saved
        self.assertIn("API を呼べなかった", worker.status(self.st)["reason"])


class BudgetTest(unittest.TestCase):
    def check_budget(self, db):
        b = worker.Budget(worker.Limits(calls_per_game=3, tokens_per_game=1000, tokens_per_owner_day=500,
                                        tokens_per_day=800), db)
        b.record("a", "alice", {"total_tokens": 300})
        b.record("b", "alice", {"total_tokens": 250})
        b.check("c", "bob")  # 他の人・他の対局は通る
        with self.assertRaises(worker.LimitReached) as e:
            b.check("c", "alice")  # alice の今日の合計 550 ≥ 500
        self.assertIn("1人の上限", str(e.exception))
        b.record("c", "bob", {"total_tokens": 300})
        with self.assertRaises(worker.LimitReached) as e:
            b.check("d", "carol")  # サイト全体 850 ≥ 800
        self.assertIn("サイト全体", str(e.exception))
        b.limits.tokens_per_day = 10_000
        b.limits.tokens_per_owner_day = 10_000
        b.record("a", "alice", {"total_tokens": 1})
        b.record("a", "alice", {"total_tokens": 1})
        with self.assertRaises(worker.LimitReached):
            b.check("a", "alice")  # a は 4 回 ≥ 3
        self.assertEqual(b.used("a", "alice")["game_tokens"], 302)

    def test_in_memory_and_in_the_db(self):
        self.check_budget(None)
        with tempfile.TemporaryDirectory() as tmp:
            self.check_budget(pathlib.Path(tmp) / "mtg.sqlite")

    def test_limits_from_the_environment(self):
        saved = dict(os.environ)
        try:
            os.environ["MTGTABLE_AI_TOKENS_PER_DAY"] = "2_000_000"
            self.assertEqual(worker.Limits.from_env().tokens_per_day, 2_000_000)
            os.environ["MTGTABLE_AI_CALLS_PER_GAME"] = "many"
            with self.assertRaises(ValueError):
                worker.Limits.from_env()
        finally:
            os.environ.clear()
            os.environ.update(saved)


if __name__ == "__main__":
    unittest.main()
