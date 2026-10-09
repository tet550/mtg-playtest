"""人間どうしの対局: 両方の席に鍵を作り、審判だけを AI が回す（serve --play の席の API ＋ 審判の返答）。"""
import json
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import play, prompt  # noqa: E402
from mtgtable.web import Handler  # noqa: E402
from helpers import game, store, viewer  # noqa: E402


class TwoHumansTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = store(self.tmp.name, "g")
        self.st.create(game().state)
        self.keys = {p: play.invite(self.st, p) for p in ("p1", "p2")}
        handler = type("PlayHandler", (Handler,), {"viewer": viewer(self.tmp.name, offline=True, play=True)})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def call(self, path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def write(self, seat, what, body):
        return self.call("/api/games/g/" + what, dict(body, seat=seat), self.keys[seat])

    def view(self, seat):
        code, v = self.call("/api/games/g/view?seat=" + seat, token=self.keys[seat])
        self.assertEqual(code, 200)
        return v

    def judge(self, body):
        """auto と同じ順: 自動パス → 審判の番ならプロンプト → 返答を適用。"""
        play.autopass(self.st)
        self.assertEqual(prompt.next_role(self.st, prompt.ai_seats(self.st)), play.JUDGE)
        prompt.build(self.st, play.JUDGE)
        out = prompt.apply_response(self.st, "```json\n%s\n```" % json.dumps(body, ensure_ascii=False), play.JUDGE)
        self.assertTrue(out["ok"], out["error"])
        play.autopass(self.st)

    def waiting(self):
        return play.waiting_on(self.st.load())

    def card(self, pid, zone, name):
        s = self.st.load()
        return next(c for c in s.zones["%s.%s" % (pid, zone)].cards if s.cards[c].name == name)

    def test_two_humans_play_through_the_judge(self):
        self.assertEqual(prompt.ai_seats(self.st), [])  # 両方の席に鍵: AI の席は無い

        # ---- ゲーム前: 先攻からキープ。審判のプロンプトだけができ、Player のプロンプトは作らない
        self.assertEqual(self.write("p2", "declare", {"kind": "keep"})[0], 403)  # 先攻が先
        self.assertEqual(self.write("p1", "declare", {"kind": "keep"})[0], 200)
        self.assertEqual(self.waiting(), "p2")
        self.assertEqual(self.write("p2", "declare", {"kind": "keep"})[0], 200)
        self.assertEqual(self.waiting(), play.JUDGE)
        prompts = self.st.root / "prompts"
        self.assertTrue((prompts / "judge" / "latest.md").exists())
        self.assertFalse((prompts / "p1").exists() or (prompts / "p2").exists())
        self.judge({"batch": {"acts": [{"actor": "p1", "proc": "turn_start", "to": "main1"}]}, "message": "ゲーム開始"})
        self.assertEqual(self.waiting(), "p1")

        # ---- 公開範囲: 鍵は自分の席だけ。相手の手札は見えない
        self.assertEqual(self.call("/api/games/g/view?seat=p1", token=self.keys["p2"])[0], 403)
        self.assertEqual(self.call("/api/games/g/view?seat=judge", token=self.keys["p2"])[0], 403)
        p1_hand = self.st.load().zones["p1.hand"].cards
        seen = json.dumps(self.view("p2"))
        self.assertFalse(any('"%s"' % c in seen for c in p1_hand))

        # ---- 相手の番に依頼は出せない（優先権を持つ・聞かれている席だけ）
        mountain = self.card("p2", "hand", "Mountain")
        code, r = self.write("p2", "request", {"plan": [{"kind": "play_land", "cards": [mountain], "text": "土地"}]})
        self.assertEqual(code, 403, r)
        self.assertEqual(self.write("p2", "declare", {"kind": "say", "text": "よろしく"})[0], 200)  # 発言はいつでも

        # ---- T1 p1: 土地を出してターン終了 → 審判が p2 のターンを始める
        forest = self.card("p1", "hand", "Forest")
        self.assertEqual(self.write("p1", "request", {"plan": [{"kind": "play_land", "cards": [forest], "text": "土地"}],
                                                      "then": "end_turn"})[0], 200)
        self.judge({"batch": {"acts": [{"actor": "p1", "act": [{"op": "land", "card": forest, "mana": "{G}"}]},
                                       {"actor": "p1", "proc": "turn_end"}]}})
        self.assertEqual(self.waiting(), play.JUDGE)  # クリンナップ → 次のターンは審判が始める
        self.judge({"batch": {"acts": [{"actor": "p2", "proc": "turn_start", "to": "main1"}]}})
        self.assertEqual((self.st.load().turn.active, self.waiting()), ("p2", "p2"))

        # ---- T2 p2: 土地を出してターン終了。打ち消し・火力を構える止める場所を設定
        self.assertEqual(self.call("/api/games/g/stops", {"seat": "p2", "stops": ["opp:spell"]}, self.keys["p2"])[0], 200)
        self.assertEqual(self.write("p2", "request", {"plan": [{"kind": "play_land", "cards": [mountain], "text": "土地"}],
                                                      "then": "end_turn"})[0], 200)
        self.judge({"batch": {"acts": [{"actor": "p2", "act": [{"op": "land", "card": mountain, "mana": "{R}"}]},
                                       {"actor": "p2", "proc": "turn_end"}]}})
        self.judge({"batch": {"acts": [{"actor": "p1", "proc": "turn_start", "to": "main1"}]}})
        self.assertEqual((self.st.load().turn.turn, self.waiting()), (3, "p1"))

        # ---- T3 p1: 熊を唱える → p2（人間）に優先権。止める場所に当たるので自動でパスしない
        bear = self.card("p1", "hand", "Grizzly Bears")
        forest2 = self.card("p1", "hand", "Forest")
        self.assertEqual(self.write("p1", "request", {"plan": [
            {"kind": "play_land", "cards": [forest2], "text": "土地"},
            {"kind": "cast", "cards": [bear], "text": "熊を唱える"},
            {"kind": "attack", "text": "攻撃しない"}], "then": "end_turn"})[0], 200)
        self.judge({"batch": {"acts": [{"actor": "p1", "act": [{"op": "land", "card": forest2, "mana": "{G}"}]},
                                       {"actor": "p1", "act": [{"op": "cast", "card": bear}, {"op": "pass"}]}]},
                    "rest": [3]})
        self.assertEqual(self.waiting(), "p2")
        self.assertEqual(self.view("p2")["turn"]["waiting_on"], "p2")  # GUI も p2 の番と出す

        # p2 が考えている間、p1 は依頼を出せない（審判が順番を飛ばして処理しないように）
        self.assertEqual(self.write("p1", "request", {"plan": [{"kind": "other", "text": "やっぱり攻撃"}]})[0], 403)

        # ---- p2 が割り込む: 稲妻を p1 へ。審判が積んで解決し、p1 の計画は止まったまま本人に戻る
        bolt = self.card("p2", "hand", "Lightning Bolt")
        self.assertEqual(self.write("p2", "request", {"plan": [
            {"kind": "cast", "cards": [bolt], "targets": ["p1"], "text": "稲妻を p1 に"}], "then": "pass"})[0], 200)
        self.judge({"batch": {"acts": [{"actor": "p2", "act": [{"op": "cast", "card": bolt}, {"op": "pass"}]}]}})
        # p1 は何もできない（土地はタップ済み）ので自動でパス → 全員パス → 審判が稲妻を解決
        self.assertEqual(self.waiting(), play.JUDGE)
        self.judge({"batch": {"acts": [{"actor": "p2", "act": [{"op": "stack_remove", "card_to": "graveyard"},
                                                              {"op": "damage", "target": "p1", "amount": 3, "source": bolt}]},
                                       {"actor": "p1", "act": [{"op": "priority", "player": "p1"}]}]}})
        self.assertEqual(self.st.load().players["p1"].life, 17)

        # ---- 審判の質問は聞かれた席だけが答え、その間は相手も依頼を出せない
        self.assertEqual(self.waiting(), "p1")
        self.assertEqual(self.write("p1", "declare", {"kind": "pass"})[0], 200)  # 熊の解決へ
        self.assertEqual(self.waiting(), "p2")
        self.assertEqual(self.write("p2", "declare", {"kind": "pass"})[0], 200)
        self.judge({"batch": {"acts": [{"actor": "p1", "act": [{"op": "stack_remove", "card_to": "battlefield"}]}]},
                    "ask": {"to": "p2", "text": "確認: 熊の解決でよいですか？", "choices": ["はい", "いいえ"]}})
        self.assertEqual(self.waiting(), "p2")
        self.assertEqual(self.write("p1", "answer", {"text": "はい"})[0], 403)  # 聞かれていない
        self.assertEqual(self.write("p1", "request", {"plan": [{"kind": "other", "text": "続き"}]})[0], 403)
        self.assertEqual(self.write("p2", "answer", {"text": "はい"})[0], 200)
        self.assertEqual(self.waiting(), play.JUDGE)
        self.judge({"batch": None, "message": "了解"})

        # ---- 止まった計画の残り（攻撃しない・ターン終了）は p1 に戻る
        resume = self.view("p1").get("resume")
        self.assertIsNotNone(resume)
        self.assertEqual(resume["then"], "end_turn")
        self.assertIsNone(self.view("p2").get("resume"))

        # ---- 投了で決着し、どちらの席も書けなくなる
        self.assertEqual(self.write("p2", "declare", {"kind": "concede"})[0], 200)
        self.assertIsNone(self.waiting())
        self.assertEqual(self.write("p1", "request", {"plan": [{"kind": "other", "text": "x"}]})[0], 403)
        self.assertEqual(self.write("p2", "declare", {"kind": "say", "text": "gg"})[0], 403)

    def test_the_defending_human_blocks_from_the_gui(self):
        self.write("p1", "declare", {"kind": "keep"})
        self.write("p2", "declare", {"kind": "keep"})
        bear = self.card("p1", "hand", "Grizzly Bears")
        self.judge({"batch": {"acts": [
            {"actor": "p1", "proc": "turn_start", "to": "main1"},
            {"actor": "p1", "act": [{"op": "move", "card": bear, "to": "battlefield"}]},  # 盤面の用意（審判が置く）
            {"actor": "p2", "act": [{"op": "create", "name": "Wall", "controller": "p2", "as": "wall",
                                     "definition": {"type_line": "Creature — Wall", "power": "0", "toughness": "4"}}]}]}})
        wall = next(c for c in self.st.load().zones["battlefield"].cards if self.st.load().cards[c].name == "Wall")

        self.assertEqual(self.write("p1", "request", {"plan": [{"kind": "attack", "cards": [bear], "targets": ["p2"],
                                                               "text": "熊で攻撃"}], "then": "end_turn"})[0], 200)
        self.judge({"batch": {"acts": [{"actor": "p1", "act": [{"op": "step", "to": "declare_attackers"},
                                                              {"op": "attack", "attacker": bear, "target": "p2", "tap": True},
                                                              {"op": "priority", "player": "p2"}]}]},
                    "rest": []})
        # 攻撃された人間の席は、ブロックを決めるまで自動でパスしない。その間、攻撃側は依頼を出せない
        self.assertEqual(self.waiting(), "p2")
        self.assertEqual(self.write("p1", "request", {"plan": [{"kind": "other", "text": "早く"}]})[0], 403)
        self.assertEqual(self.write("p2", "request", {"plan": [{"kind": "block", "cards": [wall], "targets": [bear],
                                                               "text": "<Wall> で熊をブロックする"}], "then": "pass"})[0], 200)
        self.judge({"batch": {"acts": [{"actor": "p2", "act": [{"op": "step", "to": "declare_blockers"},
                                                              {"op": "block", "blocker": wall, "attacker": bear}]}]}})
        s = self.st.load()
        self.assertEqual([(b.blocker, b.attacker) for b in s.combat.blocks], [(wall, bear)])
        self.assertFalse(play.blocks_undecided(s, "p2"))


if __name__ == "__main__":
    unittest.main()
