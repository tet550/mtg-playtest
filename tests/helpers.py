"""テストの共通部品: 小さなデッキ2つの対局と、2つの保存先（対局フォルダ・SQLite）。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import Engine, GameStore, new_game, parse_decklist  # noqa: E402
from mtgtable.sqlstore import SqliteGameStore  # noqa: E402

DECK_A = """Deck
20 Forest
20 Grizzly Bears
20 Giant Growth
Sideboard
2 Naturalize
"""
DECK_B = """Deck
30 Mountain
30 Lightning Bolt
"""


def game(seed=1, first="p1"):
    decks = {"p1": parse_decklist(DECK_A, "green"), "p2": parse_decklist(DECK_B, "red")}
    return Engine(new_game(decks, seed=seed, hand=7, first=first))


def hand(e, pid):
    return list(e.state.zones["%s.hand" % pid].cards)


def site_db(tmp):
    """手元のサイトの DB（サイトはいつも SQLite）。"""
    return pathlib.Path(tmp) / "site.sqlite"


def stores(tmp, name="g"):
    """同じ対局を置く2つの保存先（まだ作らない）。共通の処理はどちらでも同じに動く。"""
    return [GameStore(pathlib.Path(tmp) / name), SqliteGameStore(site_db(tmp), name)]
