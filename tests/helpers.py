"""テストの共通部品: 小さなデッキ2つで対局を作る・ActionGroup を適用して成功を確かめる・カードを探す。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import Engine, new_game, parse_decklist  # noqa: E402

DECK_A = """# a
Deck
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


def game(seed=1, hand=7, **kw):
    decks = {"p1": parse_decklist(DECK_A, "green"), "p2": parse_decklist(DECK_B, "red")}
    return Engine(new_game(decks, seed=seed, hand=hand, **kw))


def ok(engine, actor, *ops, **group):
    r = engine.apply_group(actor, {"ops": list(ops), **group})
    assert r.status == "applied", r.error
    return r


def hand(e, pid):
    return list(e.state.zones["%s.hand" % pid].cards)


def find(e, pid, zone, name):
    return next(c for c in e.state.zones["%s.%s" % (pid, zone)].cards if e.state.cards[c].name == name)


