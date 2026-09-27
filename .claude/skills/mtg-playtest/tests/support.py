"""オフラインのカードと一時ディレクトリを使うCLIテストの共通準備。"""
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import cardcache  # noqa: E402
import table  # noqa: E402
from table_store import history_dir  # noqa: E402

CARDS = {
    "Forest": dict(types=["Land"], supertypes=["Basic"], subtypes=["Forest"],
                   oracle_text_en="({T}: Add {G}.)"),
    "Grizzly Bears": dict(types=["Creature"], subtypes=["Bear"], mana_cost="{1}{G}",
                          power="2", toughness="2"),
    "Giant Growth": dict(types=["Instant"], mana_cost="{G}",
                         oracle_text_en="Target creature gets +3/+3 until end of turn."),
    "Watcher": dict(types=["Creature"], mana_cost="{2}{G}", power="1", toughness="1",
                    oracle_text_en="At the beginning of your upkeep, put a +1/+1 counter on this creature.\n"
                                   "Whenever another creature you control enters, you gain 1 life."),
    "Leyline of Testing": dict(types=["Enchantment"], mana_cost="{2}{G}{G}",
                               oracle_text_en="If this card is in your opening hand, you may begin the game with it on the battlefield."),
    "Pacifism": dict(types=["Enchantment"], subtypes=["Aura"], mana_cost="{1}{W}",
                     oracle_text_en="Enchant creature\nEnchanted creature can't attack or block."),
}
DECK = "Deck\n20 Forest\n20 Grizzly Bears\n10 Giant Growth\n6 Watcher\n4 Pacifism\n\nSideboard\n2 Giant Growth\n"


class TableCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = self.root = pathlib.Path(self.tmp.name)
        self.cards = root / "cards"
        for name, fields in CARDS.items():
            cardcache.put_manual(name, str(self.cards), **fields)
        self.deck = root / "green.txt"
        self.deck.write_text(DECK, encoding="utf-8")
        self.state = root / "game" / "g01.json"
        self.base = ["--state", str(self.state), "--cards-dir", str(self.cards),
                     "--decks-dir", str(root / "decks"), "--offline"]

    def cli(self, *argv, stdin=None, ok=True):
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(stdin or "")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = table.main(self.base + list(argv))
        if ok:
            self.assertEqual(code, 0, err.getvalue())
        else:
            self.assertEqual(code, 1, out.getvalue())
        return out.getvalue() + err.getvalue()

    def st(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def start(self, *extra):
        self.cli("init", "--deck1", str(self.deck), "--deck2", str(self.deck), "--seed", "7", *extra)

    def history(self):
        return len(list(history_dir(self.state).glob("*.json")))

    def find(self, name, zone):
        st = self.st()
        return next(o for o in st["zones"][zone] if st["objects"][str(o)]["card"] == name)
