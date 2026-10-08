"""デッキリストの読み込みと対局の初期状態づくり。

デッキリストの書式は decklists/README.md のとおり（"4 Forest"、Deck / Sideboard 見出し、# はコメント）。
"""
from __future__ import annotations

import pathlib
import random
import re
from dataclasses import dataclass, field

from . import info
from .model import Card, GameState, PREGAME, zone_name

_LINE = re.compile(r"^\s*(\d+)\s*x?\s+(.+?)\s*$")
_SET_SUFFIX = re.compile(r"\s+\([A-Za-z0-9]{2,6}\)\s*[\w-]*$")
_MAIN = {"deck", "main", "maindeck", "デッキ", "メインデッキ"}
_SIDE = {"sideboard", "side", "サイドボード"}


@dataclass
class Decklist:
    name: str
    main: list = field(default_factory=list)  # [(count, card name)]
    sideboard: list = field(default_factory=list)

    @property
    def main_count(self) -> int:
        return sum(n for n, _ in self.main)


def parse_decklist(text: str, name: str = "deck") -> Decklist:
    deck = Decklist(name=name)
    section = deck.main
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        head = line.rstrip(":").strip().casefold()
        if head in _MAIN:
            section = deck.main
            continue
        if head in _SIDE:
            section = deck.sideboard
            continue
        m = _LINE.match(line)
        if not m:
            raise ValueError("cannot parse decklist line: %r" % raw)
        card = _SET_SUFFIX.sub("", m.group(2)).strip()
        section.append((int(m.group(1)), card))
    return deck


def load_decklist(path) -> Decklist:
    p = pathlib.Path(path)
    return parse_decklist(p.read_text(encoding="utf-8"), name=p.stem)


MIN_MAIN = 60


def check_deck(deck: Decklist) -> None:
    """メインデッキが MIN_MAIN 枚未満なら ValueError（空のデッキリストで対局が始まるのを防ぐ）。"""
    if deck.main_count < MIN_MAIN:
        raise ValueError("deck %r has %d main-deck cards (needs at least %d)"
                         % (deck.name, deck.main_count, MIN_MAIN))


def new_game(decks: dict, seed: int = 0, life: int = 20, first: str = None,
             hand: int = 0, policies: dict = None, type_lines: dict = None) -> GameState:
    """初期状態を作る。decks は {player id: Decklist}。

    ライブラリーはシャッフル済み。hand > 0 なら各自その枚数を引いた状態で始める
    （マリガンは AI が Operation で行う）。カード id は無作為に振る
    （デッキリストの並びから id で中身が推測できないように）。
    """
    for deck in decks.values():
        check_deck(deck)
    s = GameState(seed=seed)
    rng = random.Random("%s:setup" % seed)
    for pid, deck in decks.items():
        s.add_player(pid, deck.name, life)
    s.ensure_shared_zones()
    entries = []
    for pid, deck in decks.items():
        for section, zone in ((deck.main, "library"), (deck.sideboard, "sideboard")):
            for n, cname in section:
                entries.extend([(pid, zone, cname)] * n)
    numbers = rng.sample(range(1, len(entries) + 1), len(entries))
    for (pid, zone, cname), num in zip(entries, numbers):
        cid = "#c%d" % num
        zn = zone_name(pid, zone)
        s.cards[cid] = Card(id=cid, name=cname, owner=pid, controller=pid, zone=zn,
                            definition={"name": cname}, type_line=(type_lines or {}).get(cname, ""))
        s.zones[zn].cards.append(cid)
    s.next_ids["c"] = len(entries) + 1
    for pid in decks:
        rng.shuffle(s.zones[zone_name(pid, "library")].cards)
        for i in range(hand):
            lib = s.zones[zone_name(pid, "library")]
            if lib.cards:
                cid = lib.cards.pop(0)
                s.zones[zone_name(pid, "hand")].cards.append(cid)
                s.cards[cid].zone = zone_name(pid, "hand")
    for pid, pol in (policies or {}).items():
        info.set_policy(s, pid, pol)
    # ゲーム前から始める（マリガン・開始時の手札からの行動はここ）。active は先攻。最初の step で T1 に入る
    s.turn.turn = 0
    s.turn.active = first or s.player_order[0]
    s.turn.phase, s.turn.step = PREGAME
    s.turn.priority = None
    s.meta["hand"] = hand  # 初期手札の枚数（マリガンの後に下へ置く枚数の計算に使う）
    s.meta["decks"] = {pid: {"name": d.name, "main": d.main_count,
                             "sideboard": sum(n for n, _ in d.sideboard)} for pid, d in decks.items()}
    info.refresh_knowledge(s)
    return s
