"""GUI の対局の部品: 待たれている Player・wait・席の鍵・書き込みの排他。"""
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import GameStore, carddb, info, play  # noqa: E402
from mtgtable.store import StaleCursor  # noqa: E402
from helpers import game  # noqa: E402


class PlayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = GameStore(pathlib.Path(self.tmp.name) / "g")
        self.st.create(game().state)

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, actor, *acts, **kw):
        r = self.st.apply({"actor": actor, "acts": [{"act": list(a)} if isinstance(a, (list, tuple)) else a
                                                     for a in acts]}, **kw)
        self.assertIsNone(r["stopped"], r["stopped"])
        return r

    def test_waiting_on_follows_priority_stack_and_turn(self):
        self.assertEqual(play.waiting_on(self.st.load()), "p1")  # ゲーム前は先攻から
        self.apply("p1", [{"op": "declare", "kind": "keep"}])
        self.assertEqual(play.waiting_on(self.st.load()), "p2")  # まだキープしていない Player
        self.apply("p2", [{"op": "declare", "kind": "mulligan"}])
        self.assertEqual(play.waiting_on(self.st.load()), play.JUDGE)  # マリガン: 審判が引き直す
        self.st.apply({"actor": None, "acts": [{"act": [{"op": "declare", "player": "p2", "kind": "ruled", "text": "引き直し"}]}]})
        self.assertEqual(play.waiting_on(self.st.load()), "p2")  # 引き直した後もキープまで
        self.apply("p2", [{"op": "declare", "kind": "keep"}])
        self.assertEqual(play.waiting_on(self.st.load()), play.JUDGE)  # 全員キープ: 審判がゲーム開始の処理
        self.st.apply({"actor": None, "acts": [
            {"act": [{"op": "declare", "player": "p2", "kind": "ruled", "text": "ゲーム開始"}]},
            {"act": [{"op": "declare", "player": "p2", "kind": "ask", "text": "下に置く1枚は？"}]}]})
        self.assertEqual(play.waiting_on(self.st.load()), "p2")  # マリガンした p2 に質問
        self.apply("p2", [{"op": "declare", "kind": "answer", "text": "#c50"}])
        self.assertEqual(play.waiting_on(self.st.load()), play.JUDGE)  # 回答は審判へ
        self.st.apply({"actor": None, "acts": [{"act": [{"op": "declare", "player": "p2", "kind": "ruled", "text": "#c50 を下に"}]}]})
        self.assertEqual(play.waiting_on(self.st.load()), "p1")  # 処理が済んだら先攻
        self.apply("p1", {"proc": "turn_start", "to": "main1"})
        s = self.st.load()
        bear = next(c for c in s.zones["p1.hand"].cards if s.cards[c].name == "Grizzly Bears")
        self.apply("p1", [{"op": "cast", "card": bear}], [{"op": "pass"}])
        self.assertEqual(play.waiting_on(self.st.load()), "p2")  # 優先権を渡した
        self.apply("p2", [{"op": "pass"}])
        self.assertEqual(play.waiting_on(self.st.load()), play.JUDGE)  # 全員パス: 審判のいる対局では審判が解決する
        self.apply("p1", [{"op": "stack_remove", "card_to": "battlefield"}], {"proc": "turn_end"})
        self.assertEqual(play.waiting_on(self.st.load()), "p2")  # クリンナップ: 次のターンの Player
        self.apply("p2", [{"op": "declare", "kind": "concede"}])
        self.assertIsNone(play.waiting_on(self.st.load()))  # 決着

    def test_card_question_takes_candidates_and_checks_the_answer(self):
        s = self.st.load()
        a, b = s.zones["p1.hand"].cards[:2]
        ask = {"op": "declare", "player": "p1", "kind": "ask", "text": "戻すカードは？", "cards": [a, b.lstrip("#")], "min": 0}
        self.st.apply({"actor": None, "acts": [{"act": [ask]}]})
        q = play.decorate(__import__("mtgtable").info.player_view(self.st.load(), "p1"), self.st.load())["requests"]["asks"][0]
        self.assertEqual((q["cards"], q["pick"]), ([a, b], [0, 1]))  # min だけなら max は 1
        bad = self.st.apply({"actor": "p1", "acts": [{"act": [{"op": "declare", "kind": "answer", "text": "両方", "cards": [a, b]}]}]})
        self.assertIn("0-1", str(bad["stopped"]))  # 枚数が多い
        other = s.zones["p2.hand"].cards[0]
        self.assertIn("candidates", str(self.st.apply({"actor": "p1", "acts": [{"act": [
            {"op": "declare", "kind": "answer", "cards": [other]}]}]})["stopped"]))  # 候補でない
        self.apply("p1", [{"op": "declare", "kind": "answer", "text": "1枚", "cards": [b]}])
        d = self.st.load().declarations[-1]
        self.assertEqual((d.kind, d.cards), ("answer", [b]))
        self.assertEqual(play.waiting_on(self.st.load()), play.JUDGE)

    def test_a_free_text_question_takes_cards_too(self):
        s = self.st.load()
        a, b = s.zones["p1.hand"].cards[:2]
        self.st.apply({"actor": None, "acts": [{"act": [
            {"op": "declare", "player": "p1", "kind": "ask", "text": "手札に加える2枚と残りの順番は？"}]}]})
        label, ops = play.seat_answer(self.st.load(), "p1", {"text": "この2枚", "cards": [a, b]})
        self.apply("p1", ops)  # 候補の無い質問でも、挙げたカードはそのまま審判に届く
        d = self.st.load().declarations[-1]
        self.assertEqual((d.kind, d.text, d.cards), ("answer", "この2枚", [a, b]))
        self.assertEqual(play.waiting_on(self.st.load()), play.JUDGE)

    def test_plan_step_line_keeps_a_valid_target_step(self):
        s = self.st.load()
        _, ops = play.seat_request(s, "p1", {"plan": [
            {"kind": "step", "to": "beginning_of_combat", "text": "戦闘開始 へ進む"},
            {"kind": "step", "to": "nowhere", "text": "どこか へ進む"}]})
        plan = ops[0]["plan"]
        self.assertEqual(plan[0], {"kind": "step", "text": "戦闘開始 へ進む", "to": "beginning_of_combat"})
        self.assertEqual(plan[1], {"kind": "step", "text": "どこか へ進む"})  # 知らないステップは文だけ（審判が読む）

    def test_stops_are_skipped_when_the_seat_can_do_nothing(self):
        cards = tempfile.TemporaryDirectory()
        old = os.environ.get("MTG_CARDS_DIR")
        os.environ["MTG_CARDS_DIR"] = cards.name
        try:
            put = lambda rec: (carddb._path(rec["name"]).parent.mkdir(parents=True, exist_ok=True),
                               carddb._path(rec["name"]).write_text(json.dumps(rec), encoding="utf-8"))
            for rec in ({"name": "Forest", "type_line": "Basic Land — Forest", "oracle_text": "({T}: Add {G}.)"},
                        {"name": "Grizzly Bears", "type_line": "Creature — Bear", "mana_cost": "{1}{G}", "oracle_text": ""},
                        {"name": "Giant Growth", "type_line": "Instant", "mana_cost": "{G}", "oracle_text": "+3/+3."},
                        {"name": "Mountain", "type_line": "Basic Land — Mountain", "oracle_text": "({T}: Add {R}.)"},
                        {"name": "Lightning Bolt", "type_line": "Instant", "mana_cost": "{R}", "oracle_text": "3 damage."}):
                put(rec)
            self.apply("p1", [{"op": "declare", "kind": "keep"}])
            self.apply("p2", [{"op": "declare", "kind": "keep"}])
            self.apply("p1", {"proc": "turn_start", "to": "main1"})
            s = self.st.load()
            bear = next(c for c in s.zones["p1.hand"].cards if s.cards[c].name == "Grizzly Bears")
            self.apply("p1", [{"op": "cast", "card": bear}, {"op": "pass"}])
            stops = ["opp:spell"]
            self.assertFalse(play.needs_player(self.st.load(), "p2", stops))  # 土地も無い: 聞かない
            mountain = next(c for c in s.zones["p2.hand"].cards if s.cards[c].name == "Mountain")
            self.apply("p2", [{"op": "move", "card": mountain, "to": "battlefield"}])
            self.assertTrue(play.needs_player(self.st.load(), "p2", stops))  # アンタップの土地がある
            self.apply("p2", [{"op": "tap", "card": mountain}])
            self.assertFalse(play.needs_player(self.st.load(), "p2", stops))  # タップ済み
            put({"name": "Lightning Bolt", "type_line": "Instant", "mana_cost": "{R}",
                 "oracle_text": "You may pay 2 life rather than pay this spell's mana cost."})
            self.assertTrue(play.needs_player(self.st.load(), "p2", stops))  # 代替コストで唱えられる
            put({"name": "Lightning Bolt", "type_line": "Instant", "mana_cost": "{R}", "oracle_text": "3 damage."})
            put({"name": "Mountain", "type_line": "Basic Land — Mountain",
                 "oracle_text": "({T}: Add {R}.)\nSacrifice this land: Draw a card."})
            self.assertTrue(play.needs_player(self.st.load(), "p2", stops))  # タップ状態でも起動できる能力
        finally:
            if old is None:
                os.environ.pop("MTG_CARDS_DIR", None)
            else:
                os.environ["MTG_CARDS_DIR"] = old
            cards.cleanup()

    def test_the_defender_decides_blocks_and_is_not_auto_passed(self):
        self.apply("p1", [{"op": "declare", "kind": "keep"}])
        self.apply("p2", [{"op": "declare", "kind": "keep"}])
        self.apply("p1", {"proc": "turn_start", "to": "main1"})
        self.apply("p1", [{"op": "create", "name": "Wolf", "definition": {"type_line": "Creature — Wolf", "power": 3, "toughness": 3}}])
        self.apply("p2", [{"op": "create", "name": "Bear", "definition": {"type_line": "Creature — Bear", "power": 2, "toughness": 2}}])
        # 審判が攻撃を書き、declare_blockers へ進めてから防御側に優先権を渡した
        self.apply("p1", [{"op": "step", "to": "declare_attackers"}, {"op": "attack", "attackers": ["#t1"], "target": "p2"}],
                   [{"op": "step", "to": "declare_blockers"}, {"op": "priority", "player": "p2"}])
        s = self.st.load()
        self.assertTrue(play.blocks_undecided(s, "p2"))
        self.assertTrue(play.needs_player(s, "p2", []))  # 止める場所の設定が無くても止まる
        self.assertEqual(play.autopass(self.st), [])
        self.assertEqual(play.waiting_on(self.st.load()), "p2")
        view = play.decorate(info.player_view(s, "p2"), s)
        pts = {c["id"]: c.get("base_pt") for c in view["zones"]["battlefield"]["cards"]}
        self.assertEqual(pts, {"#t1": ["3", "3"], "#t2": ["2", "2"]})  # 戦闘中の P/T の元（GUI が足し引きする）
        self.apply("p2", [{"op": "declare", "kind": "intent", "text": "行動:\n1. ブロックしない\nその後: パス"}])
        self.assertFalse(play.blocks_undecided(self.st.load(), "p2"))  # 依頼を出したら、決めた

    def test_card_kinds_tell_lands_spells_and_destinations(self):
        s = self.st.load()
        lines = {"Forest": "Basic Land — Forest", "Grizzly Bears": "Creature — Bear", "Giant Growth": "Instant"}
        for c in s.cards.values():
            c.type_line = lines.get(c.name, "")
        hand = s.zones["p1.hand"].cards
        kinds = play.decorate(__import__("mtgtable").info.player_view(s, "p1"), s)["card_kinds"]
        got = {s.cards[c].name: kinds[c] for c in hand if c in kinds}
        self.assertEqual(got.get("Forest"), {"to": "battlefield", "land": True})  # 土地として出すだけ
        self.assertEqual(got.get("Grizzly Bears"), {"to": "battlefield", "spell": True})
        self.assertEqual(got.get("Giant Growth"), {"to": "graveyard", "spell": True})
        self.assertFalse(any(c in kinds for c in s.zones["p2.hand"].cards))  # 見えないカードは出さない
        s.cards[hand[0]].type_line = "Sorcery // Land"  # 両面: 唱えても土地としても
        kinds = play.card_kinds(__import__("mtgtable").info.player_view(s, "p1"), s)
        self.assertEqual(kinds[hand[0]], {"to": "graveyard", "land": True, "spell": True})

    def test_wait_returns_when_the_turn_comes_back(self):
        got = []
        t = threading.Thread(target=lambda: got.append(play.wait(self.st, "p2", timeout=5, interval=0.02)))
        t.start()
        self.apply("p1", {"proc": "turn_start", "to": "main1"})  # p1 が動いている間は返さない
        time.sleep(0.15)
        self.assertEqual(got, [])
        self.apply("p1", [{"op": "pass"}])
        t.join(5)
        self.assertEqual(got[0]["cursor"], 5)
        self.assertEqual([e["seq"] for e in got[0]["entries"]], [1, 2, 3, 4, 5])
        # 自分が書いた後から見る（apply と wait の間に相手が書いても取りこぼさない）
        self.apply("p2", [{"op": "pass"}])
        self.apply("p1", [{"op": "declare", "kind": "say", "text": "hi"}])
        got = play.wait(self.st, "p2", timeout=1, interval=0.02)
        self.assertEqual([e["seq"] for e in got["entries"]], [7])  # 宣言で返る
        self.assertIsNone(play.wait(self.st, "p2", since=7, timeout=0.1, interval=0.02))  # 時間切れ
        self.st.undo(1)
        self.assertTrue(play.wait(self.st, "p2", since=7, timeout=1, interval=0.02)["undone"])

    def test_stale_cursor_and_lock(self):
        self.apply("p1", [{"op": "declare", "kind": "keep"}], expect=0)
        with self.assertRaises(StaleCursor):
            self.apply("p2", [{"op": "declare", "kind": "keep"}], expect=0)
        with self.assertRaises(StaleCursor):
            self.st.undo(1, expect=0)
        with self.st.lock():
            with self.assertRaises(TimeoutError):
                with self.st.lock(timeout=0.1):
                    pass
        self.assertFalse(self.st.lock_path.exists())

    def test_seat_keys_are_stored_as_hashes(self):
        token = play.invite(self.st, "p1")
        self.assertTrue(play.check_token(self.st, "p1", token))
        self.assertFalse(play.check_token(self.st, "p2", token))
        self.assertFalse(play.check_token(self.st, "p1", None))
        self.assertNotIn(token, (self.st.root / "seats.json").read_text(encoding="utf-8"))
        again = play.invite(self.st, "p1")
        self.assertFalse(play.check_token(self.st, "p1", token))  # 作り直すと前の鍵は使えない
        self.assertTrue(play.check_token(self.st, "p1", again))
        with self.assertRaises(ValueError):
            play.invite(self.st, "p9")

if __name__ == "__main__":
    unittest.main()
