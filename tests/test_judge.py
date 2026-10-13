"""審判のいる対局の不変の仕様: 誰が動く番か・席の意図 → 審判の Batch・AI の席に見せる範囲・Claude Code が回す CLI。"""
import contextlib
import io
import json
import sys
import tempfile
import unittest

from helpers import game, site_db, stores
from mtgtable import cli, play, prompt
from mtgtable.sqlstore import SqliteGameStore


def reply(body):
    return "```json\n%s\n```" % json.dumps(body, ensure_ascii=False)


class JudgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = stores(self.tmp.name)[0]
        self.st.create(game().state)

    def tearDown(self):
        self.tmp.cleanup()

    def judge(self, *acts):
        self.st.apply({"actor": None, "acts": [{"act": list(a)} for a in acts]})

    def test_waiting_on_follows_the_game(self):
        wait = lambda: play.waiting_on(self.st.load())  # noqa: E731
        self.assertEqual(wait(), "p1")  # ゲーム前は先攻から
        self.st.apply({"actor": "p1", "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        self.st.apply({"actor": "p2", "acts": [{"act": [{"op": "declare", "kind": "mulligan"}]}]})
        self.assertEqual(wait(), play.JUDGE)  # マリガンの引き直しは審判
        self.judge([{"op": "declare", "player": "p2", "kind": "ruled", "text": "redraw"}])
        self.st.apply({"actor": "p2", "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        self.assertEqual(wait(), play.JUDGE)  # 全員キープ: 審判がゲームを始める
        self.judge([{"op": "declare", "player": "p2", "kind": "ruled", "text": "start"}],
                   [{"op": "declare", "player": "p2", "kind": "ask", "text": "bottom?"}])
        self.assertEqual(wait(), "p2")  # 質問された Player
        self.st.apply({"actor": "p2", "acts": [{"act": [{"op": "declare", "kind": "answer", "text": "#c50"}]}]})
        self.assertEqual(wait(), play.JUDGE)  # 回答は審判へ
        self.judge([{"op": "declare", "player": "p2", "kind": "ruled", "text": "done"}])
        self.assertEqual(wait(), "p1")
        self.st.apply({"actor": "p1", "acts": [{"proc": "turn_start", "to": "main1"}, {"act": [{"op": "pass"}]}]})
        self.assertEqual(wait(), "p2")  # 優先権を渡した
        self.st.apply({"actor": "p2", "acts": [{"act": [{"op": "declare", "kind": "concede"}]}]})
        self.assertIsNone(wait())  # 決着

    def test_intent_goes_through_the_judge_and_the_ai_seat_sees_only_its_view(self):
        respond = lambda role, body: prompt.apply_response(self.st, reply(body), role)  # noqa: E731
        prompt.build(self.st, "p1")
        self.assertTrue(respond("p1", {"declare": {"kind": "keep"}})["ok"])
        prompt.build(self.st, "p2")
        hand = self.st.load().zones["p2.hand"].cards
        self.assertTrue(respond("p2", {"declare": {"kind": "mulligan"}})["ok"])
        self.assertEqual(self.st.load().zones["p2.hand"].cards, hand)  # 席は盤面を動かさない。引き直すのは審判
        s = self.st.load()
        names = lambda pid: {"<%s>" % s.cards[c].name for c in s.zones["%s.hand" % pid].cards}  # noqa: E731
        seat_text = prompt.user_message(self.st, "p2")
        self.assertTrue(all(n in seat_text for n in names("p2")))
        self.assertFalse(any(n in seat_text for n in names("p1") - names("p2")))  # AI の席に相手の手札は見せない
        judge_text = prompt.build(self.st, play.JUDGE)["latest"].read_text(encoding="utf-8")
        self.assertTrue(all(n in judge_text for n in names("p1")))  # 審判は全部見える
        out = respond(None, {"batch": {"acts": [{"actor": "p2", "act": [
            {"op": "move", "card": {"zone": "hand", "all": True}, "to": "library"},
            {"op": "shuffle"}, {"op": "draw", "count": 7}]}]},
            "ask": {"to": "p2", "text": "bottom?"}, "message": "mulligan"})
        self.assertTrue(out["ok"], out["error"])
        self.assertEqual([e["actor"] for e in self.st.read_log()[-3:]], ["p2", None, None])  # 操作は p2 として、印と質問は審判
        self.assertEqual(play.waiting_on(self.st.load()), "p2")
        prompt.build(self.st, "p2")
        bad = respond(None, {"declare": {"kind": "pass"}})
        self.assertFalse(bad["ok"])  # 質問されている間は回答だけ。書けなければ理由を付けてプロンプトを作り直す
        self.assertIn("前回のあなたの返答は適用できなかった", bad["next"]["latest"].read_text(encoding="utf-8"))

    def test_claude_code_picks_up_a_site_game_and_answers_through_the_cli(self):
        db = str(site_db(self.tmp.name))
        st = SqliteGameStore(db, "s1")
        st.create(game().state)
        st.write_doc("seats", {"p1": {"claimed": "2026-01-01T00:00:00"}})  # サイトで作った対局: p1 はホスト、p2 は AI

        def run(*args, stdin=""):
            out, old = io.StringIO(), sys.stdin
            sys.stdin = io.StringIO(stdin)
            try:
                with contextlib.redirect_stdout(out):
                    cli.main(["--db", db, *args])
            finally:
                sys.stdin = old
            return out.getvalue()
        with self.assertRaises(SystemExit) as e:  # ホストの番の間は待つ（時間切れは終了コード 3）
            run("next", "--timeout", "0")
        self.assertEqual(e.exception.code, 3)
        st.apply({"actor": "p1", "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        out = run("next", "--timeout", "0", "--claude-code")
        self.assertIn("game: python -m mtgtable --db %s <コマンド> s1" % db, out)
        self.assertIn("wait: ", out)
        self.assertIn("prompt for p2", out)
        self.assertTrue(prompt.claude_code_path(st).exists())
        out = run("answer", "s1", "-", "--as", "p2", stdin=reply({"declare": {"kind": "keep"}}))
        self.assertIn("prompt for 審判", out)  # 全員キープ → 続けて審判の番
        out = run("answer", "s1", "-", "--judge",
                  stdin=reply({"batch": {"acts": [{"actor": "p1", "proc": "turn_start", "to": "main1"}]}}))
        self.assertRegex(out.splitlines()[0], r"^applied \d+ acts \(v\d+\)$")
        self.assertEqual(st.load().turn.step, "main")


if __name__ == "__main__":
    unittest.main()
