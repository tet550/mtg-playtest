"""オラクルのキャッシュ（1枚ごと・デッキごと）と表示。"""
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import carddb, parse_decklist  # noqa: E402

DECK = """Deck
4 Grizzly Bears
20 Forest
Sideboard
1 Naturalize
"""
BEARS = {"name": "Grizzly Bears", "mana_cost": "{1}{G}", "type_line": "Creature — Bear",
         "power": "2", "toughness": "2", "oracle_text": ""}
FOREST = {"name": "Forest", "type_line": "Basic Land — Forest", "oracle_text": "({T}: Add {G}.)"}


class DeckCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("MTG_CARDS_DIR")
        os.environ["MTG_CARDS_DIR"] = self.tmp.name

    def tearDown(self):
        if self.old is None:
            os.environ.pop("MTG_CARDS_DIR", None)
        else:
            os.environ["MTG_CARDS_DIR"] = self.old
        self.tmp.cleanup()

    def _put(self, rec):
        p = carddb._path(rec["name"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec), encoding="utf-8")

    def test_offline_build_reports_missing_then_completes(self):
        deck = parse_decklist(DECK, "green")
        data = carddb.build_deck_cache(deck, fetch=False)
        self.assertEqual(sorted(data["missing"]), ["Forest", "Grizzly Bears", "Naturalize"])
        self._put(BEARS)
        self._put(FOREST)
        self._put({"name": "Naturalize", "mana_cost": "{1}{G}", "type_line": "Instant",
                   "oracle_text": "Destroy target artifact or enchantment."})
        data = carddb.build_deck_cache(deck, fetch=False)
        self.assertEqual(data["missing"], [])
        self.assertEqual(carddb.load_deck_cache(deck)["cards"]["Grizzly Bears"]["power"], "2")
        sheet = carddb.format_deck(data)
        self.assertIn("4 <Grizzly Bears> {1}{G} — Creature — Bear 2/2", sheet)
        self.assertIn("## Sideboard (1)", sheet)
        self.assertNotIn("Naturalize", carddb.format_deck(data, sideboard=False))

    def test_hash_follows_decklist_contents(self):
        a = parse_decklist(DECK, "green")
        b = parse_decklist(DECK.replace("20 Forest", "19 Forest"), "green")
        self.assertNotEqual(carddb.deck_cache_path(a), carddb.deck_cache_path(b))
        self.assertEqual(carddb.deck_hash(a), carddb.deck_hash(parse_decklist(DECK, "green")))

    def test_brief_drops_reminder_text(self):
        self.assertEqual(carddb.format_card_compact(FOREST), "<Forest> — Basic Land — Forest\n    ({T}: Add {G}.)")
        self.assertEqual(carddb.format_card_compact(FOREST, brief=True), "<Forest> — Basic Land — Forest")


if __name__ == "__main__":
    unittest.main()
