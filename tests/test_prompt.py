"""AI の席のプロンプト: 固定部分が変わらないこと（キャッシュ）、見せる情報の範囲、返答の適用。"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import GameStore, llm, play, prompt  # noqa: E402
from helpers import game  # noqa: E402


def answer(batch, memo=None):
    body = {"batch": batch}
    if memo is not None:
        body["memo"] = memo
    return "考え。\n\n```json\n%s\n```\n" % json.dumps(body, ensure_ascii=False)


class PromptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = GameStore(pathlib.Path(self.tmp.name) / "g")
        self.st.create(game().state)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fixed_part_is_byte_stable_and_cached(self):
        first = prompt.build(self.st, "p1", mode="direct")
        req1 = json.loads(first["request"].read_text(encoding="utf-8"))
        self.st.apply({"actor": "p1", "acts": [{"proc": "turn_start", "to": "main1"}]})
        second = prompt.build(self.st, "p1", mode="direct")
        req2 = json.loads(second["request"].read_text(encoding="utf-8"))
        # OpenAI の Chat Completions: 固定部分の system 2つが先頭（自動のプロンプト・キャッシュが効く）、今回の分が最後の user
        self.assertEqual([m["role"] for m in req1["messages"]], ["system", "system", "user"])
        self.assertEqual(req1["messages"][:2], req2["messages"][:2])  # 盤面が進んでも固定部分は同じ
        self.assertNotEqual(req1["messages"][2], req2["messages"][2])
        self.assertEqual(req1["model"], llm.model())
        self.assertIn("あなたは p1", req1["messages"][1]["content"])
        self.assertNotIn("v%d" % self.st.load().version, req2["messages"][0]["content"] + req2["messages"][1]["content"])
        self.assertEqual(prompt.common_system(), req1["messages"][0]["content"])  # 全対局・全席で共通
        self.assertNotEqual(first["stem"], second["stem"])
        latest = second["latest"].read_text(encoding="utf-8")
        self.assertTrue(latest.endswith(req2["messages"][2]["content"]))

    def test_user_message_shows_only_the_seat_view(self):
        s = self.st.load()
        p1_hand = {s.cards[c].name for c in s.zones["p1.hand"].cards}
        p2_hand = {s.cards[c].name for c in s.zones["p2.hand"].cards}
        text = prompt.user_message(self.st, "p2")
        self.assertTrue(all("<%s>" % n in text for n in p2_hand))
        self.assertFalse(any("<%s>" % n in text for n in p1_hand - p2_hand))  # 相手の手札は出ない
        self.assertIn("待たれているのは p1", text)

    def test_answer_applies_batch_and_carries_memo(self):
        prompt.build(self.st, "p1", mode="direct")
        out = prompt.apply_answer(self.st, "p1", answer(
            {"label": "キープ", "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]}, memo="様子を見る"))
        self.assertTrue(out["ok"])
        self.assertEqual(self.st.read_log()[0]["actor"], "p1")
        self.assertIn("様子を見る", prompt.user_message(self.st, "p1"))
        self.assertIsNone(out["next"])  # キープしたら、まだキープしていない p2 の番
        self.st.apply({"actor": "p2", "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        prompt.build(self.st, "p1", mode="direct")
        out = prompt.apply_answer(self.st, "p1", answer(
            {"label": "T1", "acts": [{"proc": "turn_start", "to": "main1"}, {"act": [{"op": "pass"}]}]}))
        self.assertEqual((out["ok"], out["next"]), (True, None))  # 相手に渡した
        self.assertFalse((prompt.prompt_dir(self.st, "p1") / "pending.json").exists())
        self.assertTrue((prompt.prompt_dir(self.st, "p1") / ("%s.response.md" % "0002")).exists())

    def test_failures_rebuild_the_prompt_with_the_reason(self):
        prompt.build(self.st, "p1", mode="direct")
        out = prompt.apply_answer(self.st, "p1", "JSON が無い")
        self.assertFalse(out["ok"])
        self.assertIn("前回のあなたの返答は適用できなかった", out["next"]["latest"].read_text(encoding="utf-8"))
        out = prompt.apply_answer(self.st, "p1", answer(
            {"acts": [{"act": [{"op": "move", "card": "#nothing", "to": "graveyard"}]}]}))
        self.assertIn("failed", out["error"])
        out = prompt.apply_answer(self.st, "p1", answer({"acts": [{"act": [{"op": "pass"}], "actor": "p2"}]}))
        self.assertFalse(out["ok"])
        self.st.apply({"actor": "p2", "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})  # 返答の間に相手が書いた
        out = prompt.apply_answer(self.st, "p1", answer({"acts": [{"act": [{"op": "pass"}]}]}))
        self.assertIn("盤面が進んだ", out["error"])
        self.assertEqual(self.st.cursor(), 1)


class JudgeFlowTest(unittest.TestCase):
    """Player の意図 → 審判の Batch・質問 → 回答 の流れ。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = GameStore(pathlib.Path(self.tmp.name) / "g")
        self.st.create(game().state)

    def tearDown(self):
        self.tmp.cleanup()

    def respond(self, role, body):
        return prompt.apply_response(self.st, "```json\n%s\n```" % json.dumps(body, ensure_ascii=False), role)

    def test_intent_goes_to_the_judge_and_back(self):
        built = prompt.build(self.st, "p1")  # 既定は intent
        req = json.loads(built["request"].read_text(encoding="utf-8"))
        self.assertIn("何をするかを決める", req["messages"][0]["content"])
        self.assertNotIn("Batch と Act", req["messages"][0]["content"])  # Player には操作の書式を渡さない
        out = self.respond("p1", {"declare": {"kind": "keep"}, "memo": "様子見"})
        self.assertTrue(out["ok"])
        self.assertEqual(play.waiting_on(self.st.load()), "p2")
        prompt.build(self.st, "p2")
        hand = self.st.load().zones["p2.hand"].cards
        self.assertTrue(self.respond(None, {"declare": {"kind": "mulligan"}})["ok"])
        self.assertEqual(self.st.load().zones["p2.hand"].cards, hand)  # 引き直すのは審判
        self.assertEqual(play.waiting_on(self.st.load()), play.JUDGE)  # マリガンは審判へ
        self.assertEqual(prompt.next_role(self.st, ["p2"]), play.JUDGE)

        built = prompt.build(self.st, play.JUDGE)
        text = built["latest"].read_text(encoding="utf-8")
        self.assertIn("p2 mulligan: マリガン", text)
        s = self.st.load()
        self.assertTrue(all("<%s>" % s.cards[c].name in text for c in s.zones["p1.hand"].cards))  # 審判は全部見える
        out = self.respond(None, {
            "batch": {"label": "p2 のマリガン", "acts": [{"actor": "p2", "act": [
                {"op": "move", "card": {"zone": "hand", "all": True}, "to": "library"},
                {"op": "shuffle"}, {"op": "draw", "count": 7}]}]},
            "ask": {"to": "p2", "text": "下に置く1枚は？", "choices": ["#c1", "#c2"]},
            "message": "p2 はマリガン"})
        self.assertTrue(out["ok"], out["error"])
        log = self.st.read_log()
        self.assertEqual([e["actor"] for e in log[-3:]], ["p2", None, None])  # 操作は p2 として、印と質問は審判
        self.assertEqual(play.waiting_on(self.st.load()), "p2")  # 質問された Player
        built = prompt.build(self.st, "p2")
        self.assertIn("下に置く1枚は？", built["latest"].read_text(encoding="utf-8"))
        self.assertIn("answer", self.respond(None, {"declare": {"kind": "pass"}})["error"])  # 質問中は回答だけ
        self.assertTrue(self.respond("p2", {"answer": {"text": "#c1"}})["ok"])
        self.assertEqual(play.waiting_on(self.st.load()), play.JUDGE)

    def test_card_question_lists_candidates_and_takes_cards(self):
        for pid in ("p1", "p2"):
            self.st.apply({"actor": pid, "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        s = self.st.load()
        a, b = s.zones["p1.hand"].cards[:2]
        prompt.build(self.st, play.JUDGE)
        out = self.respond(None, {"batch": {"acts": [{"actor": "p1", "proc": "turn_start", "to": "main1"}]},
                                  "ask": {"to": "p1", "text": "手札に戻してもよい", "cards": [a, b], "min": 0, "max": 1},
                                  "message": "T1 開始"})
        self.assertTrue(out["ok"], out["error"])
        text = prompt.user_message(self.st, "p1", mode="intent")
        self.assertIn("候補のカード（0〜1 枚を cards で選ぶ）: %s <%s>" % (a, s.cards[a].name), text)
        prompt.build(self.st, "p1")
        self.assertTrue(self.respond("p1", {"answer": {"cards": [b]}})["ok"])
        d = self.st.load().declarations[-1]
        self.assertEqual((d.cards, d.text), ([b], "%s <%s>" % (b, s.cards[b].name)))
        self.assertIn("選んだカード: %s <%s>" % (b, s.cards[b].name), prompt.judge_message(self.st))

    def test_judge_sees_what_was_revealed(self):
        # 探索などで一番上を公開したら、審判はそのカードを記録と盤面で見られる（Player に名前を聞かずに続きを処理する）
        self.st.apply({"actor": "p1", "acts": [{"act": [{"op": "reveal", "card": {"zone": "p1.library", "top": 1}}]}]})
        s = self.st.load()
        top = s.zones["p1.library"].cards[0]
        name = "%s <%s>" % (top, s.cards[top].name)
        judge = prompt.judge_message(self.st)
        self.assertIn("→ reveal %s to all" % name, judge)
        self.assertIn("%s (index 0) (known to p1, p2)" % name, judge)
        self.assertIn("→ reveal %s to all" % name, prompt.user_message(self.st, "p2"))

    def test_judge_sees_the_log_from_its_last_ruling(self):
        for pid in ("p1", "p2"):
            self.st.apply({"actor": pid, "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        self.st.apply({"actor": None, "acts": [{"actor": "p1", "proc": "turn_start", "to": "main1"},
                                               {"act": [{"op": "declare", "player": "p1", "kind": "ruled", "text": "開始"}]}]})
        for i in range(12):  # 前回の処理の後に 12 件
            self.st.apply({"actor": "p1", "acts": [{"act": [{"op": "declare", "kind": "say", "text": "x%d" % i}]}]})
        seqs = [e["seq"] for e in prompt.recent_entries(self.st)]
        ruled = next(e["seq"] for e in self.st.read_log() if e["act"][0].get("kind") == "ruled")
        self.assertEqual(seqs[0], ruled)  # 前回の処理の印から
        self.assertEqual(seqs[-1], self.st.cursor())
        self.st.apply({"actor": None, "acts": [{"act": [{"op": "declare", "player": "p1", "kind": "ruled", "text": "済"}]}]})
        self.assertEqual(len(prompt.recent_entries(self.st)), prompt.RECENT_MIN)  # 直後でも直前の流れは見せる

    def test_game_start_goes_to_the_judge(self):
        prompt.build(self.st, "p1")
        self.respond("p1", {"declare": {"kind": "keep"}})
        prompt.build(self.st, "p2")
        self.respond("p2", {"declare": {"kind": "keep"}})
        self.assertEqual(prompt.next_role(self.st, ["p2"]), play.JUDGE)  # キープは宣言、開始の処理は審判
        self.assertIn("ゲーム開始の処理", prompt.judge_message(self.st))
        prompt.build(self.st, play.JUDGE)
        out = self.respond(None, {"batch": {"acts": [{"actor": "p1", "proc": "turn_start", "to": "main1"}]},
                                  "message": "マリガンなし。T1 開始"})
        self.assertTrue(out["ok"], out["error"])
        s = self.st.load()
        self.assertEqual((s.turn.turn, play.waiting_on(s)), (1, "p1"))

    def test_stop_settings_are_private_and_reach_the_judge(self):
        prompt.build(self.st, "p1")
        self.assertTrue(self.respond("p1", {"declare": {"kind": "keep"}, "stops": ["opp:end", "opp:spell"]})["ok"])
        self.assertEqual(play.get_stops(self.st, "p1"), ["opp:end", "opp:spell"])
        self.assertNotIn("stops", json.dumps(self.st.read_log()))  # 卓の記録には載らない
        self.assertIn("p1: 相手のターン の end, 相手のターン の 呪文・能力を積んだとき", prompt.judge_message(self.st))
        self.assertIn("相手のターン の end", prompt.user_message(self.st, "p1", mode="intent"))
        self.assertNotIn("呪文・能力を積んだとき", prompt.user_message(self.st, "p2", mode="intent"))  # 相手には見えない
        prompt.build(self.st, "p2")
        self.assertIn("stops", self.respond("p2", {"declare": {"kind": "keep"}, "stops": ["opp:nowhere"]})["error"])

    def test_requests_are_hidden_from_the_opponent(self):
        for pid in ("p1", "p2"):
            self.st.apply({"actor": pid, "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        self.st.apply({"actor": None, "acts": [{"actor": "p1", "proc": "turn_start", "to": "main1"},
                                               {"act": [{"op": "declare", "player": "p1", "kind": "ruled", "text": "開始"}]}]})
        s = self.st.load()
        forest = next(c for c in s.zones["p1.hand"].cards if s.cards[c].name == "Forest")
        self.st.apply(play.seat_batch(s, "p1", "request", {
            "plan": [{"kind": "play_land", "cards": [forest], "text": "<Forest> を出す"}], "comment": "秘密の計画"}))
        self.assertEqual(prompt.next_role(self.st, ["p2"]), play.JUDGE)
        self.assertNotIn("秘密の計画", json.dumps(play.player_log(self.st, "p2"), ensure_ascii=False))
        self.assertNotIn("<Forest> を出す", prompt.user_message(self.st, "p2", mode="intent"))
        self.assertIn("秘密の計画", json.dumps(play.player_log(self.st, "p1"), ensure_ascii=False))
        judge = prompt.judge_message(self.st)
        self.assertIn("1. <Forest> を出す", judge)
        self.assertIn("補足: 秘密の計画", judge)
        prompt.build(self.st, play.JUDGE)
        self.respond(None, {"batch": None, "ask": {"to": "p1", "text": "どの土地をタップ？"}, "message": "p1: <Forest>"})
        p2_view = json.dumps(play.decorate(__import__("mtgtable").info.player_view(self.st.load(), "p2"), self.st.load()),
                             ensure_ascii=False)
        self.assertNotIn("どの土地をタップ", p2_view)  # 質問は質問された本人だけ
        self.assertNotIn("どの土地をタップ", prompt.user_message(self.st, "p2", mode="intent"))
        self.assertIn("どの土地をタップ", prompt.user_message(self.st, "p1", mode="intent"))

    def test_ai_mulligan_and_bottom_cards_go_through_the_judge(self):
        prompt.build(self.st, "p1")
        self.respond("p1", {"declare": {"kind": "keep"}})
        prompt.build(self.st, "p2")
        self.assertTrue(self.respond("p2", {"declare": {"kind": "mulligan"}})["ok"])
        prompt.build(self.st, play.JUDGE)
        out = self.respond(None, {"batch": {"acts": [{"actor": "p2", "act": [
            {"op": "move", "card": {"zone": "hand", "all": True}, "to": "library"}, {"op": "shuffle"},
            {"op": "draw", "count": 7}]}]}, "message": "p2 は引き直した"})
        self.assertTrue(out["ok"], out["error"])
        self.assertEqual(play.waiting_on(self.st.load()), "p2")
        self.assertIn("マリガン 1 回", prompt.user_message(self.st, "p2", mode="intent"))
        prompt.build(self.st, "p2")
        self.assertTrue(self.respond("p2", {"declare": {"kind": "keep"}})["ok"])
        self.assertEqual(len(self.st.load().zones["p2.hand"].cards), 7)  # 下に置くのは審判
        self.assertIn("p2 マリガン 1 回（下に置いた 0 枚。残り 1 枚を本人に聞く）", prompt.judge_message(self.st))
        self.assertIn('exactly one of', self.respond("p2", {"action": "keep"})["error"])  # 前の形は受けない

    def _start(self):
        """両者キープ → 審判がゲーム開始（turn_start の to を書き忘れても メイン1 まで進む）。"""
        for pid in ("p1", "p2"):
            self.st.apply({"actor": pid, "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        prompt.build(self.st, play.JUDGE)
        out = self.respond(None, {"batch": {"label": "審判: 開始", "acts": [{"actor": "p1", "proc": "turn_start"}]}})
        self.assertTrue(out["ok"], out["error"])
        s = self.st.load()
        self.assertEqual((s.turn.turn, s.turn.step), (1, "main"))
        self.assertFalse(any(e.get("batch_label", "").startswith("審判: 審判") for e in self.st.read_log()))

    def test_ai_auto_passes_and_the_judge_resolves(self):
        play.invite(self.st, "p1")  # p1 は人間、p2 は AI
        self._start()
        s = self.st.load()
        bear = next(c for c in s.zones["p1.hand"].cards if s.cards[c].name == "Grizzly Bears")
        self.st.apply(play.seat_batch(s, "p1", "request", {
            "plan": [{"kind": "cast", "cards": [bear], "text": "<Grizzly Bears> を唱える"}], "then": "pass"}))
        prompt.build(self.st, play.JUDGE)
        self.assertTrue(self.respond(None, {"batch": {"acts": [
            {"actor": "p1", "act": [{"op": "cast", "card": bear}, {"op": "pass"}]}]}})["ok"])
        self.assertEqual(play.waiting_on(self.st.load()), "p2")  # 優先権は AI へ
        built = prompt.auto_build(self.st)  # GUI・answer の後に呼ばれる: AI は止める場所が無いので自動でパス
        self.assertEqual(self.st.read_log()[-1]["label"], "自動パス（止める場所に当たらない）")
        self.assertEqual((built["role"], play.waiting_on(self.st.load())), (play.JUDGE, play.JUDGE))  # 全員パス → 審判が解決
        self.assertIn("全員がパスした", built["latest"].read_text(encoding="utf-8"))
        out = self.respond(None, {"batch": {"acts": [{"actor": "p1", "act": [{"op": "stack_remove", "card_to": "battlefield"}]}]}})
        self.assertTrue(out["ok"], out["error"])  # 依頼が無くても、解決の処理として受ける
        self.assertEqual(play.waiting_on(self.st.load()), "p1")

    def test_human_seat_auto_passes_unless_its_stops_match(self):
        # 審判が人間の席に優先権を渡しても、止める場所に当たらなければ人間にパスを押させない（AI と同じ決まり）
        play.invite(self.st, "p1")  # p1 は人間、p2 は AI
        self._start()
        s = self.st.load()
        bear = s.zones["p2.hand"].cards[0]  # 誘発の発生源（何でもよい）
        self.st.apply({"actor": None, "acts": [
            {"actor": "p1", "act": [{"op": "pass"}]},  # p1 の T1 は何もせず p2 へ
            {"actor": "p2", "act": [{"op": "stack_push", "source": bear, "kind": "triggered", "text": "誘発"},
                                    {"op": "pass"}]},
            {"act": [{"op": "declare", "player": "p2", "kind": "ruled", "text": "x"}]}]})
        self.assertEqual(play.waiting_on(self.st.load()), "p1")  # 審判が人間に番を回した
        prompt.auto_build(self.st)
        self.assertEqual(self.st.read_log()[-1]["label"], "自動パス（止める場所に当たらない）")
        self.assertEqual(play.waiting_on(self.st.load()), play.JUDGE)  # 全員パス → 審判が解決
        # 止める場所（opp:spell）を設定していれば、人間に番が回ったまま
        self.st.undo(1)
        play.set_stops(self.st, "p1", ["opp:spell"])
        prompt.auto_build(self.st)
        self.assertEqual(play.waiting_on(self.st.load()), "p1")

    def test_ai_stops_keep_its_response_window(self):
        play.invite(self.st, "p1")
        self._start()
        play.set_stops(self.st, "p2", ["opp:spell"])  # 打ち消しを構える
        s = self.st.load()
        mountain = next(c for c in s.zones["p2.hand"].cards if s.cards[c].name == "Mountain")
        self.st.apply({"actor": "p2", "acts": [{"act": [{"op": "move", "card": mountain, "to": "battlefield"}]}]})  # 構えるマナ
        bear =next(c for c in s.zones["p1.hand"].cards if s.cards[c].name == "Grizzly Bears")
        self.st.apply({"actor": None, "acts": [{"actor": "p1", "act": [{"op": "cast", "card": bear}, {"op": "pass"}]},
                                               {"act": [{"op": "declare", "player": "p1", "kind": "ruled", "text": "x"}]}]})
        self.assertEqual(play.autopass(self.st), [])
        self.assertEqual(prompt.next_role(self.st, ["p2"]), "p2")  # AI に聞く
        s = self.st.load()
        self.assertTrue(play.needs_player(s, "p2", []) is False)  # 設定が無ければ自動でパスしてよい場面

    def test_judge_mistakes_are_reported_back(self):
        prompt.build(self.st, "p1")
        self.respond("p1", {"declare": {"kind": "mulligan"}})
        prompt.build(self.st, play.JUDGE)
        out = self.respond(None, {"batch": {"acts": [{"actor": "p1", "proxy": "p2", "act": [{"op": "pass"}]}]}})
        self.assertFalse(out["ok"])
        self.assertIn("proxy", out["error"])
        out = self.respond(None, {"batch": {"acts": [{"actor": "p1", "act": [{"op": "move", "card": "#zz", "to": "hand"}]}]}})
        self.assertFalse(out["ok"])
        self.assertEqual(play.waiting_on(self.st.load()), play.JUDGE)  # 印（ruled）は書かれていない
        out = self.respond(None, {"batch": None, "ask": {"to": "p2", "text": "どれでブロックしますか？", "choices": ["ブロックしない"]}})
        self.assertFalse(out["ok"])  # ブロックは質問で選ばせない（防御側が GUI・依頼で決める）
        self.assertIn("declare_blockers", out["error"])
        out = self.respond(None, {"batch": {"acts": [{"act": [{"op": "shuffle"}]}]}, "message": "引き直し"})
        self.assertTrue(out["ok"])  # actor を省くと依頼した Player
        self.assertEqual(self.st.read_log()[-2]["actor"], "p1")
        self.assertEqual(play.waiting_on(self.st.load()), "p1")  # 引き直した後もキープまで
        with self.assertRaises(play.Refused):
            play.seat_batch(self.st.load(), "p2", "answer", {"text": "x"})  # 質問が無いのに回答

    def test_request_plan_reaches_the_judge_and_the_board_waits(self):
        for pid in ("p1", "p2"):
            self.st.apply({"actor": pid, "acts": [{"act": [{"op": "declare", "kind": "keep"}]}]})
        self.st.apply({"actor": None, "acts": [{"actor": "p1", "proc": "turn_start", "to": "main1"},
                                               {"act": [{"op": "declare", "player": "p1", "kind": "ruled", "text": "開始"}]}]})
        s = self.st.load()
        forest = next(c for c in s.zones["p1.hand"].cards if s.cards[c].name == "Forest")
        prompt.build(self.st, "p1")
        out = self.respond("p1", {"request": {"plan": [{"kind": "play_land", "cards": [forest], "text": "<Forest> を出す"},
                                                      {"kind": "draw", "count": 1, "text": "1 枚引く"}], "then": "pass"}})
        self.assertTrue(out["ok"], out["error"])
        self.assertIn(forest, self.st.load().zones["p1.hand"].cards)  # 依頼だけでは盤面は動かない
        text = prompt.judge_message(self.st)
        self.assertIn("行動:\n      | 1. <Forest> を出す\n      | 2. 1 枚引く\n      | その後: パス（相手に渡す）", text)
        self.assertNotIn("\n1. <Forest>", text)  # Player の文は2行目から字下げ（行頭で見出しを装えない）
        log = play.player_log(self.st, "p1")
        self.assertEqual((log[-1]["request"], log[-1]["texts"][0][:3]), (True, "行動:"))
        prompt.build(self.st, play.JUDGE)
        out = self.respond(None, {"batch": {"acts": [{"actor": "p1", "act": [{"op": "land", "card": forest, "mana": "{G}"}]},
                                                     {"actor": "p1", "act": [{"op": "pass"}]}]},
                                  "message": "1 枚引くは、引く効果が無いので行わない"})
        self.assertTrue(out["ok"], out["error"])
        log = play.player_log(self.st, "p1")
        self.assertTrue(log[-1]["judge"] and not log[-1]["mine"])  # 審判が p1 として書いた件は「自分の行動」ではない
        self.assertEqual(play.waiting_on(self.st.load()), "p2")
        prompt.build(self.st, "p1")
        self.assertIn("not known", self.respond("p1", {"request": {"plan": [
            {"kind": "cast", "cards": [self.st.load().zones["p2.hand"].cards[0]], "text": "x"}]}})["error"])  # 相手の手札

    def test_judge_keeps_seeing_earlier_requests_of_the_turn(self):
        self._start()
        prompt.build(self.st, "p1")
        self.assertTrue(self.respond("p1", {"request": {"comment": "エンド時の誘発は引く"}})["ok"])
        self.assertIn("エンド時の誘発は引く", prompt.judge_message(self.st).split("# 先の指示")[0])  # まず処理する依頼として
        prompt.build(self.st, play.JUDGE)
        self.assertTrue(self.respond(None, {"batch": None, "message": "了解"})["ok"])
        text = prompt.judge_message(self.st)
        self.assertIn("# 先の指示", text)  # 処理した後も、このターンの間は先の指示として届く
        self.assertIn("補足: エンド時の誘発は引く", text.split("# 先の指示")[1])
        for _ in range(2):  # 2ターン進むと届かなくなる
            s = self.st.load()
            self.st.apply({"actor": None, "acts": [{"actor": s.turn.active, "proc": "turn_end"},
                                                   {"actor": [p for p in s.player_order if p != s.turn.active][0],
                                                    "proc": "turn_start", "to": "main1"}]})
        self.assertNotIn("エンド時の誘発は引く", prompt.judge_message(self.st))

    def test_judge_batch_size_is_capped(self):
        self._start()
        prompt.build(self.st, "p1")
        self.assertTrue(self.respond("p1", {"request": {"comment": "ループ 30 回"}})["ok"])
        ok = [{"actor": "p1", "act": [{"op": "life", "player": "p1", "delta": 0}] * prompt.JUDGE_MAX_OPS}]
        prompt.judge_acts(self.st.load(), {"batch": {"acts": ok}})  # 上限ちょうどは通る
        over = ok + [{"actor": "p1", "proc": "turn_end"}]
        with self.assertRaisesRegex(ValueError, r"ops \(max %d\).*ループ k/N" % prompt.JUDGE_MAX_OPS):
            prompt.judge_acts(self.st.load(), {"batch": {"acts": over}})
        prompt.build(self.st, play.JUDGE)
        out = self.respond(None, {"batch": {"acts": over}})
        self.assertFalse(out["ok"])  # 審判に理由を付けて書き直させる（卓には何も書かない）
        self.assertIn("max %d" % prompt.JUDGE_MAX_OPS, out["error"])

    def test_pregame_request_unknown_then_and_long_memo(self):
        prompt.build(self.st, "p1")
        out = self.respond("p1", {"request": {"comment": "手札が配られていない", "then": "somewhere"}, "memo": "m" * 5000})
        self.assertTrue(out["ok"], out["error"])  # ゲーム前でも依頼は審判に届く。知らない then は補足に残す
        d = self.st.load().declarations[-1]
        self.assertEqual((d.kind, d.then if hasattr(d, "then") else "continue"), ("intent", "continue"))
        self.assertIn("その後の指定: somewhere", d.text)
        self.assertEqual(play.waiting_on(self.st.load()), play.JUDGE)
        self.assertEqual(len(prompt.memo_path(self.st, "p1").read_text(encoding="utf-8").strip()), play.MAX_MEMO)

    def test_escalate_hands_a_stuck_seat_to_the_judge(self):
        self._start()
        self.st.apply({"actor": None, "acts": [{"act": [
            {"op": "declare", "player": "p1", "kind": "ask", "text": "どれを手札に？"}]}]})
        out = prompt.escalate(self.st, "p1", "some error", '{"answer": {}}')
        self.assertTrue(out["ok"], out["error"])  # 質問中なら回答として
        d = self.st.load().declarations[-1]
        self.assertEqual((d.player, d.kind), ("p1", "answer"))
        self.assertIn("some error", d.text)
        self.assertEqual(play.waiting_on(self.st.load()), play.JUDGE)
        self.st.apply({"actor": None, "acts": [{"act": [{"op": "declare", "player": "p1", "kind": "ruled", "text": "x"}]}]})
        out = prompt.escalate(self.st, "p2", "e" * 2000, "r" * 2000)
        self.assertTrue(out["ok"], out["error"])  # 質問が無ければ依頼として（長い返答は切り詰める）
        self.assertEqual(self.st.load().declarations[-1].kind, "intent")


if __name__ == "__main__":
    unittest.main()
