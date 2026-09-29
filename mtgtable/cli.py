"""コマンドライン入口。`python -m mtgtable <command> ...`

AI も人間も同じ操作系（Operation の Batch）で卓を動かす（26節）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import carddb, info
from .engine import public_result
from .model import INFO_POLICIES
from .operations import OperationError, describe_operations, summarize_op
from .render import render_view, strip_names
from .setup import load_decklist, new_game
from .store import GameStore, state_diff


def _utf8():
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _actor(value):
    return None if value in (None, "judge", "none", "all") else value


def _pairs(values, what):
    out = {}
    for v in values or []:
        if "=" not in v:
            raise SystemExit("%s must look like p1=VALUE, got %r" % (what, v))
        k, _, val = v.partition("=")
        out[k.strip()] = val.strip()
    return out


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def _zone_flags(a):
    full = getattr(a, "full", False)
    return {"sideboard": full or getattr(a, "sideboard", False), "library": full or getattr(a, "library", False),
            "graveyard": full or getattr(a, "graveyard", False)}


def _view(state, actor, as_json, oracle=False, names=True, **zones):
    v = info.player_view(state, actor, names=names, **zones)
    if oracle and names:
        names = set()
        for zv in v["zones"].values():
            for key in ("cards", "known", "known_positions", "known_unordered"):
                names.update(c["name"] for c in zv.get(key, []) if "name" in c)
        v["oracle"] = {}
        for n in sorted(names):
            rec = carddb.lookup(n, offline=True)
            if rec:
                v["oracle"][n] = carddb.format_card(rec)
    if as_json:
        _emit(v)
    else:
        print(render_view(v))
        for n, text in v.get("oracle", {}).items():
            print("\n## " + text)


# ---------------------------------------------------------------- commands

def cmd_new(a):
    decks = {}
    for pid, path in _pairs(a.deck, "--deck").items():
        decks[pid] = load_decklist(path)
    if not decks:
        raise SystemExit("give at least one --deck p1=PATH")
    # デッキのオラクルを束ねたキャッシュを用意する（無いカードだけ取りに行く）。
    # タイプ行はカードに持たせ、表示の並べ替え（土地を前に）と召喚酔いの表示に使う
    caches = {pid: carddb.build_deck_cache(deck, fetch=not a.offline) for pid, deck in decks.items()}
    type_lines = {name: rec.get("type_line", "") for c in caches.values() for name, rec in c["cards"].items()}
    state = new_game(decks, seed=a.seed, life=a.life, first=a.first, hand=a.hand,
                     policies=_pairs(a.policy, "--policy"), type_lines=type_lines)
    for pid, deck in decks.items():
        state.meta["decks"][pid]["oracle"] = str(carddb.deck_cache_path(deck))
        state.meta["decks"][pid]["oracle_missing"] = caches[pid]["missing"]
    GameStore(a.game).create(state, overwrite=a.force)
    for pid, d in state.meta["decks"].items():
        print("%s: %s (%d cards, sideboard %d)" % (pid, d["name"], d["main"], d["sideboard"]))
        print("    oracle: %s%s" % (d["oracle"], "  missing: " + ", ".join(d["oracle_missing"])
                                   if d["oracle_missing"] else ""))
    print("created %s (seed %d)" % (a.game, a.seed))


def cmd_view(a):
    _view(GameStore(a.game).load(), _actor(a.as_), a.json, a.oracle, not a.no_names, **_zone_flags(a))


def cmd_apply(a):
    store = GameStore(a.game)
    text = sys.stdin.read() if a.file in (None, "-") else pathlib.Path(a.file).read_text(encoding="utf-8")
    try:
        batch = json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit("batch is not valid JSON: %s" % e)
    if a.as_ is not None:
        if isinstance(batch, list):
            batch = {"ops": batch}
        if isinstance(batch, dict):
            if "actor" in batch and batch["actor"] != _actor(a.as_):
                raise SystemExit("--as %s conflicts with actor %r in the batch" % (a.as_, batch["actor"]))
            batch = {**batch, "actor": _actor(a.as_)}
    try:
        result = store.apply(batch)
    except OperationError as e:
        raise SystemExit("error: %s" % e)
    state = result.pop("_state")
    # learned（新しく知ったカード）は --no-names でも名前を残す。id と名前の対応を知る唯一の機会なので
    _emit(public_result(result))
    if a.view:
        _view(state, result["actor"], a.json, names=not a.no_names, **_zone_flags(a))
    if result["stopped"] and result["stopped"]["reason"] in ("failed", "precondition_failed"):
        sys.exit(2)


def cmd_undo(a):
    try:
        print("cursor -> %d" % GameStore(a.game).undo(a.n, to=a.to))
    except ValueError as e:
        raise SystemExit(str(e))


def cmd_redo(a):
    print("cursor -> %d" % GameStore(a.game).redo(a.n))


def cmd_log(a):
    store = GameStore(a.game)
    cur = store.cursor()
    entries = store.read_log()
    if a.last:
        entries = entries[-a.last:]
    for e in entries:
        mark = " " if e["seq"] <= cur else "~"  # ~ = Undo 済み（Redo できる）
        label = e["label"] or "; ".join(summarize_op(op) for op in e["ops"])
        if e.get("proxy_by"):
            label = "[proxy by %s] %s" % (e["proxy_by"], label)
        print("%s%4d v%-4d %-5s %s" % (mark, e["seq"], e["version"], e["actor"] or "judge", label))
        if a.events:
            for ev in e["events"]:
                print("            " + (strip_names(ev) if a.no_names else ev))


def cmd_replay(a):
    state = GameStore(a.game).replay(a.to)
    if a.json:
        _emit(state.to_dict())
    else:
        _view(state, _actor(a.as_), False, names=not a.no_names, **_zone_flags(a))


def cmd_diff(a):
    store = GameStore(a.game)
    before = store.replay(a.from_)
    after = store.replay(a.to if a.to is not None else store.cursor())
    for path, x, y in state_diff(before, after):
        if path.startswith("knowledge") and not a.all:
            continue
        print("%s: %s -> %s" % (path, json.dumps(x, ensure_ascii=False), json.dumps(y, ensure_ascii=False)))


def cmd_fork(a):
    GameStore(a.game).fork(a.dest, a.at, overwrite=a.force)
    print("forked %s -> %s" % (a.game, a.dest))


def cmd_policy(a):
    try:
        policies = GameStore(a.game).set_policies(_pairs(a.set, "policy"))
    except ValueError as e:
        raise SystemExit(str(e))
    print(", ".join("%s=%s" % kv for kv in policies.items()))


def cmd_ids(a):
    """id とカード名の対応を引く（名前無しの view を使うときの照会用）。"""
    state = GameStore(a.game).load()
    viewer = _actor(a.as_)
    if a.ids:
        cids = []
        for cid in a.ids:
            cid = cid if cid.startswith("#") else "#" + cid
            if cid not in state.cards:
                raise SystemExit("unknown card %s" % cid)
            cids.append(cid)
    else:
        cids = [cid for cid, c in state.cards.items()
                if state.zones[c.zone].kind in ("hand", "battlefield", "stack", "exile", "command", "graveyard")]
    for cid in cids:
        c = state.cards[cid]
        if info.knows_identity(state, viewer, cid):
            print("%s %s (%s)" % (cid, c.name, c.zone))
        elif a.ids:
            print("%s ? (not known to %s)" % (cid, viewer or "judge"))


def cmd_ops(a):
    for name, doc in describe_operations():
        print("%-15s %s" % (name, doc))


def _known_card_names(state, viewer, ids):
    """viewer が中身を知っているカードの名前。ids 指定時はその中から（知らないカードは拒否）、
    省略時はゲーム内（サイドボード以外）で知っているカード全部。"""
    if ids:
        cids = []
        for cid in ids:
            cid = cid if cid.startswith("#") else "#" + cid
            if cid not in state.cards:
                raise SystemExit("unknown card %s" % cid)
            if not info.knows_identity(state, viewer, cid):
                raise SystemExit("card %s is not known to %s" % (cid, viewer or "judge"))
            cids.append(cid)
    else:
        cids = [cid for cid, c in state.cards.items()
                if state.zones[c.zone].kind != "sideboard" and info.knows_identity(state, viewer, cid)]
    names = []
    for cid in cids:
        if state.cards[cid].name not in names:
            names.append(state.cards[cid].name)
    return names


def cmd_oracle(a):
    if a.deck:
        d = load_decklist(a.deck)
        data = carddb.build_deck_cache(d, fetch=not a.offline, refresh=a.refresh)
        print(carddb.format_deck(data, sideboard=not a.no_sideboard, brief=a.brief))
        sys.exit(1 if data["missing"] else 0)
    names = list(a.names)
    if a.game:
        state = GameStore(a.game).load()
        names += _known_card_names(state, _actor(a.as_), a.card)
    if not names:
        raise SystemExit("give card names, --deck FILE, or --game GAME [--card ID ...]")
    status = 0
    for n in names:
        try:
            rec = carddb.lookup(n, offline=a.offline, refresh=a.refresh)
        except (LookupError, OSError) as e:
            print("%s: %s" % (n, e), file=sys.stderr)
            status = 1
            continue
        if rec is None:
            print("%s: not cached" % n, file=sys.stderr)
            status = 1
        elif a.json:
            _emit(rec)
        else:
            print(carddb.format_card_compact(rec, brief=a.brief))
    sys.exit(status)


def cmd_deck(a):
    d = load_decklist(a.file)
    print("%s: main %d, sideboard %d" % (d.name, d.main_count, sum(n for n, _ in d.sideboard)))
    data = carddb.build_deck_cache(d, fetch=a.fetch, refresh=a.refresh)
    print("oracle cache: %s (%d cards%s)" % (carddb.deck_cache_path(d), len(data["cards"]),
                                             ", missing: " + ", ".join(data["missing"]) if data["missing"] else ""))
    for name, err in data["errors"].items():
        print("  %s: %s" % (name, err))
    if data["missing"] and not a.fetch:
        print("  (--fetch で Scryfall から取得)")


def build_parser():
    ap = argparse.ArgumentParser(prog="mtgtable", description="AI が紙の MTG をプレイするためのデジタル卓")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="対局を作る")
    p.add_argument("game")
    p.add_argument("--deck", action="append", help="p1=decklists/piza.txt（1つなら一人回し）")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--life", type=int, default=20)
    p.add_argument("--first", help="先攻の Player（既定は最初の --deck）")
    p.add_argument("--hand", type=int, default=7, help="初期手札の枚数（0 で引かない）")
    p.add_argument("--policy", action="append", help="p1=%s" % "|".join(INFO_POLICIES))
    p.add_argument("--offline", action="store_true", help="オラクルのキャッシュに無いカードを取りに行かない")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("view", help="Player View を表示する")
    p.add_argument("game")
    p.add_argument("--as", dest="as_", default="judge", help="p1 / p2 / judge")
    p.add_argument("--json", action="store_true")
    p.add_argument("--oracle", action="store_true", help="見えているカードのオラクル（キャッシュ済みのみ）も出す")
    p.add_argument("--sideboard", action="store_true", help="サイドボード（ゲーム外）も出す")
    p.add_argument("--library", action="store_true", help="ライブラリーの既知のカードも出す（既定は枚数だけ）")
    p.add_argument("--graveyard", action="store_true", help="墓地の中身も出す（既定は枚数だけ）")
    p.add_argument("--full", action="store_true", help="サイドボード・ライブラリー・墓地を全部出す")
    p.add_argument("--no-names", action="store_true", help="カード名を出さない（id だけ）。照会は ids コマンド")
    p.set_defaults(fn=cmd_view)

    p = sub.add_parser("apply", help="Operation の Batch（JSON）を適用する")
    p.add_argument("game")
    p.add_argument("file", nargs="?", help="JSON ファイル（省略か - で標準入力）")
    p.add_argument("--as", dest="as_", help="操作する Player（batch の actor を上書き。judge で全知）")
    p.add_argument("--view", action="store_true", help="適用後の Player View も出す")
    p.add_argument("--json", action="store_true", help="--view を JSON で")
    p.add_argument("--sideboard", action="store_true", help="--view にサイドボードも出す")
    p.add_argument("--library", action="store_true", help="--view にライブラリーの既知のカードも出す")
    p.add_argument("--graveyard", action="store_true", help="--view に墓地の中身も出す")
    p.add_argument("--full", action="store_true", help="--view に全部出す")
    p.add_argument("--no-names", action="store_true", help="--view にカード名を出さない（learned の名前は残る）")
    p.set_defaults(fn=cmd_apply)

    for name, fn in (("undo", cmd_undo), ("redo", cmd_redo)):
        p = sub.add_parser(name, help="ActionGroup 単位で%s" % ("戻す" if name == "undo" else "やり直す"))
        p.add_argument("game")
        p.add_argument("n", nargs="?", type=int, default=1)
        if name == "undo":
            p.add_argument("--to", type=int, help="log の N 件目の直後まで戻す（割り込みの巻き戻し）")
        p.set_defaults(fn=fn)

    p = sub.add_parser("log", help="Operation Log")
    p.add_argument("game")
    p.add_argument("--last", type=int)
    p.add_argument("--events", action="store_true", help="全情報のイベントも出す（観戦・デバッグ用）")
    p.add_argument("--no-names", action="store_true", help="イベントにカード名を出さない")
    p.set_defaults(fn=cmd_log)

    p = sub.add_parser("replay", help="log の途中時点の状態を再現する")
    p.add_argument("game")
    p.add_argument("--to", type=int)
    p.add_argument("--as", dest="as_", default="judge")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-names", action="store_true")
    p.add_argument("--full", action="store_true", help="サイドボード・ライブラリー・墓地も出す")
    p.set_defaults(fn=cmd_replay)

    p = sub.add_parser("diff", help="log の2時点間の State Diff")
    p.add_argument("game")
    p.add_argument("--from", dest="from_", type=int, required=True)
    p.add_argument("--to", type=int)
    p.add_argument("--all", action="store_true", help="knowledge の差分も出す")
    p.set_defaults(fn=cmd_diff)

    p = sub.add_parser("fork", help="途中時点から別の対局を作る")
    p.add_argument("game")
    p.add_argument("dest")
    p.add_argument("--at", type=int)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_fork)

    p = sub.add_parser("policy", help="Information Policy を変える（一人回し・テスト用）")
    p.add_argument("game")
    p.add_argument("set", nargs="+", help="p1=omniscient など")
    p.set_defaults(fn=cmd_policy)

    p = sub.add_parser("ids", help="id とカード名の対応（知っているカードだけ）")
    p.add_argument("game")
    p.add_argument("ids", nargs="*", help="#c12 など。省略で手札・戦場・スタック・墓地・追放の知っているカード全部")
    p.add_argument("--as", dest="as_", default="judge")
    p.set_defaults(fn=cmd_ids)

    p = sub.add_parser("ops", help="Operation の一覧")
    p.set_defaults(fn=cmd_ops)

    p = sub.add_parser("oracle", help="カードのオラクル・テキスト（Scryfall キャッシュ）")
    p.add_argument("names", nargs="*", help="カード名（英語）")
    p.add_argument("--deck", help="デッキリストの全カードを1枚の一覧で（デッキのキャッシュから）")
    p.add_argument("--no-sideboard", action="store_true", help="--deck でサイドボードを省く")
    p.add_argument("--brief", action="store_true", help="注釈文（括弧内の説明）を省く")
    p.add_argument("--game", help="対局の中のカード。--card で id 指定、省略で --as が知っているカード全部")
    p.add_argument("--as", dest="as_", default="judge")
    p.add_argument("--card", action="append", help="--game と一緒に。#c12 など（複数可）")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_oracle)

    p = sub.add_parser("deck", help="デッキリストを読んで枚数を確かめる")
    p.add_argument("file")
    p.add_argument("--fetch", action="store_true", help="キャッシュに無いカードを Scryfall から取得する")
    p.add_argument("--refresh", action="store_true", help="キャッシュを無視して取り直す")
    p.set_defaults(fn=cmd_deck)
    return ap


def main(argv=None):
    _utf8()
    a = build_parser().parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
