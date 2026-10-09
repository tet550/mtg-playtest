"""OpenAI の API で審判と AI の席を回す（llm.py）。外には出ず、手元の偽の API サーバーで確かめる。"""
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import llm, play  # noqa: E402
from helpers import game, store  # noqa: E402


class FakeOpenAI(BaseHTTPRequestHandler):
    """Chat Completions の形で答える。審判の依頼か Player の依頼かで返答を変える。"""
    requests = []
    fail_next = 0

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOpenAI.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if FakeOpenAI.fail_next:
            FakeOpenAI.fail_next -= 1
            self.send_response(500)
            self.end_headers()
            return
        system = body["messages"][0]["content"]
        if "対局の審判（judge）" in system:
            reply = {"batch": {"label": "開始", "acts": [{"actor": "p1", "proc": "turn_start", "to": "main1"}]},
                     "ask": None, "message": "T1 開始"}
        else:
            reply = {"declare": {"kind": "keep"}, "memo": "様子見"}
        out = {"model": body["model"], "choices": [{"message": {"content": "考え\n```json\n%s\n```" % json.dumps(reply)}}],
               "usage": {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 80}}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class LLMTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = store(self.tmp.name, "g")
        self.st.create(game().state)
        play.invite(self.st, "p1")  # p1 は GUI の人間、p2 は AI
        FakeOpenAI.requests, FakeOpenAI.fail_next = [], 0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.env = dict(os.environ)
        os.environ.update({"OPENAI_API_KEY": "test-key", "MTGTABLE_OPENAI_MODEL": "test-model",
                           "OPENAI_BASE_URL": "http://127.0.0.1:%d/v1" % self.httpd.server_address[1]})

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def test_runs_the_judge_and_the_ai_until_the_human_turn(self):
        self.st.apply({"actor": "p1", "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        lines = []
        self.assertEqual(llm.run(self.st, report=lines.append), "waiting on p1（人間の番）")
        self.assertEqual(len(FakeOpenAI.requests), 2)  # p2 のキープ → 審判のゲーム開始
        req = FakeOpenAI.requests[0]
        self.assertEqual((req["path"], req["auth"], req["body"]["model"]), ("/v1/chat/completions", "Bearer test-key", "test-model"))
        self.assertEqual([m["role"] for m in req["body"]["messages"]], ["system", "system", "user"])
        self.assertEqual(lines, ["p2: ok（cached 80 tokens）", "judge: ok（cached 80 tokens）"])
        s = self.st.load()
        self.assertEqual((s.turn.turn, play.waiting_on(s)), (1, "p1"))
        usage = (self.st.root / "prompts" / "p2" / "usage.jsonl").read_text(encoding="utf-8")
        self.assertIn('"cached_tokens": 80', usage)
        # 調査用の記録: 呼び出しごとに、プロンプトの stem・返答の全文・卓に書けたか
        log = [json.loads(x) for x in llm.ai_log_path(self.st).read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["role"], r["ok"], r["model"]) for r in log], [("p2", True, "test-model"), ("judge", True, "test-model")])
        self.assertIn('"keep"', log[0]["response"])
        self.assertTrue((self.st.root / log[1]["request"]).exists())

    def test_logs_responses_that_could_not_be_applied(self):
        self.st.apply({"actor": "p1", "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        self.st.apply({"actor": "p2", "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        self.st.apply({"actor": "p1", "acts": [{"proc": "turn_start", "to": "main1"}]})
        self.st.apply({"actor": "p1", "acts": [{"act": [{"op": "declare", "kind": "intent", "text": "攻撃する"}]}]})
        out = llm.step(self.st)  # 偽の審判は turn_start を返す（もう T1 なので適用できない）
        self.assertEqual((out["role"], out["ok"]), ("judge", False))
        rec = json.loads(llm.ai_log_path(self.st).read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((rec["role"], rec["ok"]), ("judge", False))
        self.assertTrue(rec["error"])
        self.assertEqual(rec["retry_stem"], out["next"]["stem"])  # 失敗の理由を付けて作り直したプロンプト

    def test_retries_server_errors_and_reports_a_missing_key(self):
        FakeOpenAI.fail_next = 1
        llm.RETRIES, saved = 1, llm.RETRIES
        try:
            res = llm.complete({"model": "m", "messages": [{"role": "user", "content": "x"}]})
        finally:
            llm.RETRIES = saved
        self.assertIn("keep", res["text"])
        self.assertEqual(len(FakeOpenAI.requests), 2)
        os.environ.pop("OPENAI_API_KEY")
        llm.KEY_FILE, saved = pathlib.Path(self.tmp.name) / "no-key", llm.KEY_FILE
        try:
            with self.assertRaises(llm.LLMError):
                llm.api_key()
            llm.KEY_FILE.write_text("file-key\n", encoding="utf-8")
            self.assertEqual(llm.api_key(), "file-key")  # secrets/openai_api_key の代わり
        finally:
            llm.KEY_FILE = saved


    def test_models_come_from_the_config_file(self):
        os.environ.pop("MTGTABLE_OPENAI_MODEL")
        llm.CONFIG_FILE, saved = pathlib.Path(self.tmp.name) / "openai.json", llm.CONFIG_FILE
        try:
            self.assertEqual(llm.model(), llm.DEFAULT_MODEL)  # ファイルが無ければ既定
            llm.CONFIG_FILE.write_text(json.dumps({"model": "m-all", "judge_model": "m-judge", "reasoning_effort": "low"}),
                                       encoding="utf-8")
            self.assertEqual((llm.model(play.JUDGE), llm.model("p2"), llm.model()), ("m-judge", "m-all", "m-all"))
            body = llm.request_body(["s"], "u", play.JUDGE)
            self.assertEqual((body["model"], body["reasoning_effort"]), ("m-judge", "low"))
            built = __import__("mtgtable").prompt.build(self.st, "p2")
            self.assertEqual(json.loads(built["request"].read_text(encoding="utf-8"))["model"], "m-all")
            os.environ["MTGTABLE_OPENAI_MODEL"] = "m-env"  # 環境変数（auto --model）が優先
            self.assertEqual(llm.model(play.JUDGE), "m-env")
            os.environ.pop("MTGTABLE_OPENAI_MODEL")
            llm.CONFIG_FILE.write_text('{"modle": "typo"}', encoding="utf-8")
            with self.assertRaises(llm.LLMError):  # 書き間違いに気づける
                llm.model()
        finally:
            llm.CONFIG_FILE = saved

if __name__ == "__main__":
    unittest.main()
