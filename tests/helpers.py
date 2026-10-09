"""テストの共通部品: 小さなデッキ2つで対局を作る・Act を適用して成功を確かめる・カードを探す。"""
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


def game(seed=1, hand=7, first="p1", **kw):
    decks = {"p1": parse_decklist(DECK_A, "green"), "p2": parse_decklist(DECK_B, "red")}
    return Engine(new_game(decks, seed=seed, hand=hand, first=first, **kw))


def ok(engine, actor, *ops, **act):
    r = engine.apply_act(actor, {"act": list(ops), **act})
    assert r.status == "applied", r.error
    return r


def run(engine, actor, *entries, **batch):
    """Act の並び（手順を含む）を1つの Batch で適用し、全部成功したことを確かめる。"""
    res = engine.apply_batch({"actor": actor, "acts": list(entries), **batch})
    assert res["stopped"] is None, res["stopped"]
    return res


def hand(e, pid):
    return list(e.state.zones["%s.hand" % pid].cards)


def find(e, pid, zone, name):
    return next(c for c in e.state.zones["%s.%s" % (pid, zone)].cards if e.state.cards[c].name == name)


