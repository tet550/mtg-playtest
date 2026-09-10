"""A deck's strategy doc must surface on its own, before the first play decision.

`decklists/strategy/<登録名>.md` を置いただけで、そのデッキを使うときに案内が出ること。
登録JSONには方針を持たないので、後から置いても登録し直さずに効く。
"""
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import decks
import mtg

LIST = "Deck\n60 Forest\n\nSideboard\n15 Island\n"


class StrategyDocTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.strategy = self.root / "strategy"
        self.strategy.mkdir()
        self.cards = self.root / "cards"
        self.cards.mkdir()
        for name in ("Forest", "Island"):
            (self.cards / ("%s.json" % name)).write_text(json.dumps({
                "schema": "mtg-playtest/card@2", "key": name.lower(), "name": name,
                "en_name": name, "printed_name": None, "oracle_id": name.lower(),
                "types": ["Land"], "supertypes": ["Basic"], "subtypes": [],
                "mana_value": 0, "colors": [], "source": "manual", "unresolved": False,
            }, ensure_ascii=False), encoding="utf-8")
        self.decks_dir = self.root / "decks"
        self.list_file = self.root / "combo.txt"
        self.list_file.write_text(LIST, encoding="utf-8")

    def call(self, *command, state=None):
        argv = ["--offline", "--cards-dir", str(self.cards),
                "--decks-dir", str(self.decks_dir)]
        if state:
            argv = ["--state", str(state)] + argv
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mtg.dispatch(argv + list(command))
        return out.getvalue()

    def register(self):
        self.call("deck", "add", str(self.list_file), "--name", "combo", "--brief")

    def test_missing_doc_is_silent(self):
        self.register()
        with patch.object(decks, "STRATEGY_DIR", str(self.strategy)):
            self.assertIsNone(decks.strategy_path("combo"))
            self.assertNotIn("方針", self.call("deck", "show", "combo", "--brief"))

    def test_doc_surfaces_without_re_registering(self):
        self.register()
        # 登録の**後**に方針を置く。登録JSONは触らない。
        (self.strategy / "combo.md").write_text("# 方針\n", encoding="utf-8")
        with patch.object(decks, "STRATEGY_DIR", str(self.strategy)):
            for out in (self.call("deck", "show", "combo", "--brief"),
                        self.call("deck", "show", "combo"),
                        self.call("deck", "list")):
                self.assertIn("方針", out)
            registered = json.loads(next(self.decks_dir.glob("combo-*.json"))
                                    .read_text(encoding="utf-8"))
            self.assertNotIn("strategy", registered)

    def test_init_names_the_doc_for_the_seat_using_that_deck(self):
        self.register()
        (self.strategy / "combo.md").write_text("# 方針\n", encoding="utf-8")
        state = self.root / "g01.json"
        with patch.object(decks, "STRATEGY_DIR", str(self.strategy)):
            out = self.call("init", "--deck2", "combo", "--seed", "1", state=state)
        self.assertIn("方針: P2 = %s" % (self.strategy / "combo.md").as_posix(), out)
        self.assertNotIn("方針: P1", out)

    def test_sideboarded_list_still_finds_the_base_deck_doc(self):
        """G2は対局フォルダのファイルを読むが、方針は素の構築のものを使う。"""
        self.register()
        (self.strategy / "combo.md").write_text("# 方針\n", encoding="utf-8")
        g2 = self.root / "g02-combo.txt"
        g2.write_text(LIST, encoding="utf-8")
        state = self.root / "g02.json"
        with patch.object(decks, "STRATEGY_DIR", str(self.strategy)):
            out = self.call("init", "--deck1", str(g2), "--deck1-name", "combo",
                            "--seed", "2", state=state)
        self.assertIn("方針: P1 = %s" % (self.strategy / "combo.md").as_posix(), out)


if __name__ == "__main__":
    unittest.main()
