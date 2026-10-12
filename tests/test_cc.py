"""Claude Code のエージェント（審判と AI の席を兼ねる）に送る量を減らす仕組み: 盤面の差分・自動のターン開始・answer / next の出力。"""
import contextlib
import io
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import cli, play, prompt  # noqa: E402
from helpers import STORE, game, store  # noqa: E402


class ClaudeCodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = store(self.tmp.name, "g")
        self.st.create(game().state)

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, actor, *acts):
        r = self.st.apply({"actor": actor, "acts": [{"act": list(a)} if isinstance(a, (list, tuple)) else a
                                                     for a in acts]})
        self.assertIsNone(r["stopped"], r["stopped"])
        return r

    def to_cleanup(self, *extra):
        """両者キープ → 審判がゲーム開始 → p1 の T1 を終えてクリンナップ（審判が次のターンを始める番）。"""
        self.apply("p1", [{"op": "declare", "kind": "keep"}])
        self.apply("p2", [{"op": "declare", "kind": "keep"}])
        self.st.apply({"actor": None, "acts": [{"act": [{"op": "declare", "player": "p1", "kind": "ruled", "text": "ゲーム開始"}]}]})
        self.apply("p1", {"proc": "turn_start", "to": "main1"}, *extra, {"proc": "turn_end"})
        self.assertTrue(play.turn_start_pending(self.st.load()))

    def test_turn_start_runs_without_the_judge_when_nothing_can_happen(self):
        self.to_cleanup()
        done = prompt.advance(self.st)
        s = self.st.load()
        self.assertTrue(done)
        self.assertEqual((s.turn.turn, s.turn.active, s.turn.step), (2, "p2", "main"))
        self.assertEqual(play.waiting_on(s), "p2")
        self.assertTrue(any("自動" in (e.get("batch_label") or "") for e in self.st.read_log()[-6:]))
        self.assertEqual(s.declarations[-1].kind, "ruled")  # 審判の処理と同じ印

    def test_turn_start_goes_to_the_judge_on_stops_and_upkeep_text(self):
        self.to_cleanup()
        play.set_stops(self.st, "p2", ["own:upkeep"])
        self.assertEqual(prompt.advance(self.st), [])
        self.assertEqual(play.waiting_on(self.st.load()), play.JUDGE)
        play.set_stops(self.st, "p2", ["opp:end"])  # 関係ない止める場所は止めない
        s = self.st.load()
        land = s.zones["p2.hand"].cards[0]
        self.st.apply({"actor": None, "acts": [{"actor": "p2", "act": [
            {"op": "note_add", "target": land, "text": "sacrifice at the beginning of your next upkeep"}]}]})
        self.assertIsNotNone(prompt.turn_start_reason(self.st, self.st.load()))
        self.assertEqual(prompt.advance(self.st), [])

    def test_turn_start_reads_the_oracle_of_deck_cards(self):
        """デッキのカードは definition に名前だけ持つ。それでもオラクルのアップキープの誘発を見て審判に回す。"""
        self.to_cleanup()
        s = self.st.load()
        card = s.cards[s.zones["p2.hand"].cards[0]]
        self.assertEqual(card.definition, {"name": card.name})
        self.st.apply({"actor": None, "acts": [{"actor": "p2", "act": [
            {"op": "move", "card": card.id, "to": "battlefield"}]}]})
        oracle = {"cards": {card.name: {"oracle_text": "At the beginning of your upkeep, you lose 1 life."}}}
        old = prompt._deck_data
        prompt._deck_data = lambda state, seat: oracle
        try:
            self.assertIn(card.id, prompt.turn_start_reason(self.st, self.st.load()) or "")
            self.assertEqual(prompt.advance(self.st), [])
        finally:
            prompt._deck_data = old

    def test_board_is_sent_as_a_diff_after_delivery(self):
        prompt.write_claude_code(self.st)
        first = prompt.build(self.st, play.JUDGE)
        self.assertNotIn(prompt.SAME, first["user"].read_text(encoding="utf-8"))  # 届ける前は全部
        prompt.delivered(first)
        self.apply("p1", [{"op": "declare", "kind": "keep"}])
        second = prompt.build(self.st, play.JUDGE)
        text = second["user"].read_text(encoding="utf-8")
        self.assertIn("hand (7)  " + prompt.SAME, text)  # 変わっていない手札は見出しだけ
        self.assertIn("-- p1", text)  # Player の行（ライフ）は毎回
        full = (second["dir"] / ("%s.view.txt" % second["stem"])).read_text(encoding="utf-8")
        self.assertNotIn(prompt.SAME, full)
        prompt.forget_delivered(self.st)  # 起動し直したエージェントには全部
        self.apply("p2", [{"op": "declare", "kind": "keep"}])
        self.assertNotIn(prompt.SAME, prompt.build(self.st, play.JUDGE)["user"].read_text(encoding="utf-8"))

    def run_cli(self, *args, stdin=""):
        out = io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out):
                try:
                    cli.main(list(args))
                except SystemExit as e:
                    if e.code not in (None, 0):
                        raise
        finally:
            sys.stdin = old
        return out.getvalue()

    @unittest.skipIf(STORE == "sqlite", "対局フォルダの指し方で CLI を呼ぶ")
    def test_answer_is_compact_and_reports_the_end_of_the_game(self):
        game_dir = str(self.st.root)
        self.to_cleanup()
        self.st.write_doc("seats", {"p1": {"hash": "x"}})  # p1 は GUI の人間（鍵のある席）
        prompt.write_claude_code(self.st)
        play.set_stops(self.st, "p2", ["own:upkeep"])  # 審判にターンを始めさせる
        out = self.run_cli("next", game_dir, "--ai", "p2", "--timeout", "0", "--claude-code", "--body")
        self.assertIn("prompt for 審判", out)
        self.assertIn("の本文（Read しない）", out)
        body = '```json\n{"batch": {"acts": [{"actor": "p2", "proc": "turn_start", "to": "main1"}]}}\n```'
        out = self.run_cli("answer", game_dir, "-", "--judge", stdin=body)
        self.assertRegex(out.splitlines()[0], r"^applied \d+ acts \(v\d+\)$")  # 1行（Act ごとの結果は出さない）
        self.assertIn("prompt for p2", out)
        out = self.run_cli("answer", game_dir, "-", "--as", "p2",
                           stdin='```json\n{"declare": {"kind": "concede"}}\n```')
        self.assertIn("決着", out)
        self.assertNotIn("waiting on -", out)

    def test_judge_asks_from_the_hand_after_a_draw(self):
        """引いて捨てる: 引いたカードの id を知らなくても、"cards": "hand" でその時点の手札全部を候補にできる。"""
        self.to_cleanup()
        play.set_stops(self.st, "p2", ["own:upkeep"])
        prompt.build(self.st, play.JUDGE)
        body = ('```json\n{"batch": {"acts": [{"actor": "p2", "proc": "turn_start", "to": "main1"}, '
                '{"actor": "p2", "act": [{"op": "draw", "count": 1}], "cont": true}]}, '
                '"ask": {"to": "p2", "text": "1枚捨てる", "cards": "hand"}}\n```')
        out = prompt.apply_response(self.st, body, play.JUDGE)
        self.assertTrue(out["ok"], out["error"])
        s = self.st.load()
        ask = s.declarations[-1]
        self.assertEqual((ask.kind, ask.cards), ("ask", s.zones["p2.hand"].cards))
        self.assertEqual(len(ask.cards), 9)  # 7 + T2 のドロー + 誘発で引いた1枚

    def test_judge_ask_with_a_placeholder_card_writes_nothing(self):
        """id でない候補は、batch を書く前に断る（batch と処理の印だけ書けて質問が残らないのを防ぐ）。"""
        self.to_cleanup()
        play.set_stops(self.st, "p2", ["own:upkeep"])
        prompt.build(self.st, play.JUDGE)
        cur = self.st.cursor()
        body = ('```json\n{"batch": {"acts": [{"actor": "p2", "proc": "turn_start", "to": "main1"}]}, '
                '"ask": {"to": "p2", "text": "1枚捨てる", "cards": ["HAND_DRAWN"]}}\n```')
        out = prompt.apply_response(self.st, body, play.JUDGE)
        self.assertFalse(out["ok"])
        self.assertIn('"hand"', out["error"])
        self.assertEqual(self.st.cursor(), cur)

    @unittest.skipIf(STORE == "sqlite", "対局フォルダの指し方で CLI を呼ぶ")
    def test_answer_wait_waits_through_the_human_turn(self):
        game_dir = str(self.st.root)
        self.to_cleanup()
        self.st.write_doc("seats", {"p2": {"hash": "x"}})  # p2 は GUI の人間。p1 が AI の席
        prompt.write_claude_code(self.st)
        play.set_stops(self.st, "p1", ["opp:upkeep"])  # 審判に p2 のターンを始めさせる
        self.run_cli("next", game_dir, "--ai", "p1", "--timeout", "0", "--claude-code", "--body")
        body = '```json\n{"batch": {"acts": [{"actor": "p2", "proc": "turn_start", "to": "main1"}]}}\n```'
        with self.assertRaises(SystemExit) as e:  # 人間（p2）の番のまま時間切れ: next と同じ終了コード 3
            self.run_cli("answer", game_dir, "-", "--judge", "--wait", "0", stdin=body)
        self.assertEqual(e.exception.code, 3)


if __name__ == "__main__":
    unittest.main()
