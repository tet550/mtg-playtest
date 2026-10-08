"""デッキリスト・初期状態。"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import new_game, parse_decklist  # noqa: E402
from helpers import DECK_A, DECK_B, game, hand  # noqa: E402


class SetupTest(unittest.TestCase):
    def test_parse_decklist(self):
        d = parse_decklist(DECK_A + "\n1 Llanowar Elves (DOM) 168\n", "g")
        self.assertEqual(d.main_count, 60)
        self.assertEqual(d.sideboard[-1], (1, "Llanowar Elves"))

    def test_new_game_zones(self):
        e = game()
        s = e.state
        self.assertEqual(len(s.zones["p1.library"].cards), 53)
        self.assertEqual(len(hand(e, "p1")), 7)
        self.assertEqual(len(s.zones["p1.sideboard"].cards), 2)
        self.assertEqual(s.turn.active, "p1")

    def test_same_seed_same_game(self):
        self.assertEqual(game(seed=5).state.to_dict(), game(seed=5).state.to_dict())
        self.assertNotEqual(game(seed=5).state.zones["p1.library"].cards,
                            game(seed=6).state.zones["p1.library"].cards)

    def test_short_deck_rejected(self):
        for text in ("", "Deck\n59 Forest\nSideboard\n15 Naturalize\n"):
            decks = {"p1": parse_decklist(DECK_B, "red"), "p2": parse_decklist(text, "short")}
            with self.assertRaisesRegex(ValueError, r"'short' has \d+ main-deck cards"):
                new_game(decks, hand=7)


if __name__ == "__main__":
    unittest.main()
