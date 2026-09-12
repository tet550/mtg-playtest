"""Game 2 lists live in the match folder, so stats must still see one deck per player.

サイド後のリストを登録デッキとして増やすと、同じ物理デッキがG1とG2で別行になり
`stats` の勝率が読めなくなる（playtest/dw-piza-bo1-20260908 の B-6）。
対局フォルダのファイルを読んでも、集計名は素の構築の登録名に寄せられること。
"""
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import mtg

LIST = """\
# fixture
Deck
60 Forest

Sideboard
15 Island
"""


class SideboardIdentityTests(unittest.TestCase):
    def call(self, state, results, *command):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mtg.dispatch(["--state", str(state), "--results", str(results),
                          "--offline", "--cards-dir", str(self.cards), *command])
        return out.getvalue()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cards = self.root / "cards"
        self.cards.mkdir()
        for name in ("Forest", "Island"):
            mtg.cardcache.path_for(self.cards, name).write_text(json.dumps({
                "schema": "mtg-playtest/card@2", "key": name.lower(), "name": name,
                "en_name": name, "printed_name": None, "oracle_id": name.lower(),
                "types": ["Land"], "supertypes": ["Basic"], "subtypes": [],
                "mana_value": 0, "colors": [], "source": "manual", "unresolved": False,
            }, ensure_ascii=False), encoding="utf-8")
        self.results = self.root / "results.jsonl"
        self.g2list = self.root / "g02-fix.txt"
        self.g2list.write_text(LIST, encoding="utf-8")

    def records(self):
        return [json.loads(l) for l in
                self.results.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_deck_name_override_keeps_one_row_per_deck(self):
        g1 = self.root / "g01.json"
        self.call(g1, self.results, "init", "--deck1", str(self.g2list),
                  "--deck2", str(self.g2list), "--seed", "1")
        self.call(g1, self.results, "end", "--winner", "P1", "--tag", "m1")
        g2 = self.root / "g02.json"
        self.call(g2, self.results, "init", "--deck1", str(self.g2list),
                  "--deck1-name", "fixture-deck", "--deck2", str(self.g2list),
                  "--deck2-name", "fixture-deck", "--seed", "2")
        self.call(g2, self.results, "end", "--winner", "P2", "--tag", "m1")
        recs = self.records()
        # 素の構築名を渡さないとファイル名がデッキ名になる（これが集計を割る原因）
        self.assertEqual(recs[0]["decks"], {"P1": "g02-fix", "P2": "g02-fix"})
        # --deckN-name を渡すと集計名が寄る
        self.assertEqual(recs[1]["decks"], {"P1": "fixture-deck", "P2": "fixture-deck"})
        # 実際に読んだリストは deck_sources に残るので、どの構成かは辿れる
        self.assertEqual(recs[1]["deck_sources"], {"P1": str(self.g2list),
                                                   "P2": str(self.g2list)})

    def test_stats_groups_both_games_under_one_deck(self):
        for i, (state, winner) in enumerate([("g01.json", "P1"), ("g02.json", "P2")]):
            path = self.root / state
            self.call(path, self.results, "init", "--deck1", str(self.g2list),
                      "--deck1-name", "fixture-deck", "--deck2", str(self.g2list),
                      "--deck2-name", "fixture-deck", "--seed", str(i + 1))
            self.call(path, self.results, "end", "--winner", winner, "--tag", "m1")
        out = self.call(self.root / "g02.json", self.results, "stats", "--tag", "m1")
        self.assertIn("2ゲーム", out)
        self.assertEqual(out.count("fixture-deck"), 1)


if __name__ == "__main__":
    unittest.main()
