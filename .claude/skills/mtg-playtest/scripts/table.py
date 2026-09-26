#!/usr/bin/env python3
"""仮想のテーブル。紙のMTGでテーブルの上にある物だけを持つ。

カード・領域・カウンター・ダメージ用ダイス・ライフパッド・マーカー・付箋・メモ帳・
ターンのトラッカー・乱数。どれもルールを知らない。ターンの進行、SBA、誘発、
P/Tの計算、コストの支払い方はすべて操作する側（AI）が判断する。

止めるのは物理的に不可能な操作だけ（存在しないカード、1枚を2か所に置く、
負のカウンター、実在のカードを消す、見えないはずの情報を見る）。ルール違反では止めない。

設計: design/paper-model.md
"""
import argparse
import copy
import json
import os
import pathlib
import random
import re
import shlex
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import aid  # noqa: E402
import cardcache  # noqa: E402
import decks  # noqa: E402

SCHEMA = "mtg-playtest/table@1"
SEATS = ("P1", "P2")
PILES = ("library", "hand", "battlefield", "graveyard", "exile", "command", "sideboard")
HIDDEN = ("library", "hand", "sideboard")      # 持ち主にも順番を見せない／他席に見せない
PHASES = [
    ("pregame", "開始前"),
    ("beginning.untap", "アンタップ"), ("beginning.upkeep", "アップキープ"),
    ("beginning.draw", "ドロー"), ("precombat_main", "戦闘前メイン"),
    ("combat.begin", "戦闘開始"), ("combat.attackers", "攻撃クリーチャー指定"),
    ("combat.blockers", "ブロック・クリーチャー指定"), ("combat.damage", "戦闘ダメージ"),
    ("combat.end", "戦闘終了"), ("postcombat_main", "戦闘後メイン"),
    ("ending.end", "終了"), ("ending.cleanup", "クリンナップ"),
]
PHASE_NAMES = [p for p, _ in PHASES]
PHASE_JA = dict(PHASES)
MANA = "WUBRGC"
TOKEN_PRESETS = {
    "treasure": ("Treasure", "Artifact", "Treasure", "{T}, Sacrifice this token: Add one mana of any color."),
    "clue": ("Clue", "Artifact", "Clue", "{2}, Sacrifice this token: Draw a card."),
    "food": ("Food", "Artifact", "Food", "{2}, {T}, Sacrifice this token: You gain 3 life."),
    "blood": ("Blood", "Artifact", "Blood", "{1}, {T}, Discard a card, Sacrifice this token: Draw a card."),
    "map": ("Map", "Artifact", "Map", "{1}, {T}, Sacrifice this token: Target creature you control explores. Activate only as a sorcery."),
    "lander": ("Lander", "Artifact", "Lander", "{2}, {T}, Sacrifice this token: Search your library for a basic land card, put it onto the battlefield tapped, then shuffle. Activate only as a sorcery."),
}


class Stop(Exception):
    """物理的に不可能な操作。状態は変えない。"""


# ---------------------------------------------------------------- 状態

def state_path(args):
    p = args.state or os.environ.get("MTG_STATE")
    if not p:
        raise Stop("--state <パス> か環境変数 MTG_STATE で状態ファイルを指定してください。")
    return pathlib.Path(p)


def history_dir(path):
    return path.with_name(path.stem + ".history")


def load(args):
    p = state_path(args)
    if not p.exists():
        raise Stop("状態ファイルがありません: %s（init で作ります）" % p)
    st = json.loads(p.read_text(encoding="utf-8"))
    if st.get("schema") != SCHEMA:
        raise Stop("%s は %s の状態ではありません。" % (p, SCHEMA))
    return st


def save(path, st):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        hist = history_dir(path)
        hist.mkdir(parents=True, exist_ok=True)
        n = len(list(hist.glob("*.json")))
        (hist / ("%04d.json" % n)).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(path))


def where(st):
    t = st["tracker"]
    return "T%d %s %s" % (t["turn"], t["active"], PHASE_JA.get(t["phase"], t["phase"]))


def log(st, text, private=None):
    st["log"].append({"at": where(st), "text": text, "private": private})


def other(seat):
    return "P2" if seat == "P1" else "P1"


# ---------------------------------------------------------------- 参照

def obj(st, ref):
    key = str(ref).lstrip("[").rstrip("]")
    if key not in st["objects"]:
        raise Stop("オブジェクト %s はありません。" % ref)
    return st["objects"][key]


def zone_of(st, oid):
    for z, ids in st["zones"].items():
        if int(oid) in ids:
            return z
    return None


def seat_arg(value):
    v = value.upper()
    if v not in SEATS:
        raise Stop("席は P1 か P2 です: %s" % value)
    return v


def card(st, o):
    """カードとして印刷されている内容。トークン・能力・コピーは作成時の定義。"""
    if o.get("def"):
        return o["def"]
    return st["cards"].get(o["card"], {"name": o["card"]})


def name_of(st, o, viewer="all"):
    if o.get("face_down") and viewer != "all" and viewer != o["controller"]:
        return "裏向きのカード"
    c = card(st, o)
    base = c.get("printed_name") or c.get("name") or o["card"]
    return base


def visible(st, o, viewer):
    """viewer（P1/P2/all）がこのオブジェクトの表を見られるか。"""
    z = zone_of(st, o["oid"]) or ""
    if viewer == "all":
        return not z.endswith(":library")
    seat, pile = (z.split(":") + [""])[:2] if ":" in z else ("", z)
    if pile == "library":
        return False
    if pile in ("hand", "sideboard"):
        return seat == viewer
    if o.get("face_down"):
        return o["controller"] == viewer
    return True


def label(st, o, viewer="all"):
    if not visible(st, o, viewer):
        return "[%d] (非公開)" % o["oid"]
    return "[%d] %s" % (o["oid"], name_of(st, o, viewer))


def parse_zone(st, o, spec, controller=None):
    """移動先の領域名。`hand` なら持ち主の、`battlefield` は操作する席の領域。"""
    if spec == "stack":
        return "stack"
    if ":" in spec:
        seat, pile = spec.split(":", 1)
        seat = seat_arg(seat)
    else:
        pile = spec
        if pile == "battlefield":
            here = zone_of(st, o["oid"]) or ""
            seat = controller or (o["controller"] if here.endswith(":battlefield") or here == "stack" else o["owner"])
        else:
            seat = o["owner"]
    if pile not in PILES:
        raise Stop("領域は %s / stack です: %s" % (" / ".join(PILES), spec))
    return "%s:%s" % (seat, pile)


def new_oid(st):
    oid = st["next_oid"]
    st["next_oid"] += 1
    return oid


def new_object(st, owner, card_name=None, definition=None, kind="card"):
    oid = new_oid(st)
    o = {"oid": oid, "card": card_name or (definition or {}).get("name"), "owner": owner,
         "controller": owner, "kind": kind, "tapped": False, "face_down": False,
         "flipped": False, "counters": {}, "damage": 0, "notes": [], "under": None,
         "attacking": None, "blocking": None}
    if definition:
        o["def"] = definition
    st["objects"][str(oid)] = o
    return o


def clear_table_marks(st, o):
    """テーブルから離れたカードからは、乗っていたダイスや付箋を外す。"""
    o.update(tapped=False, counters={}, damage=0, notes=[], attacking=None, blocking=None,
             face_down=False)
    o["controller"] = o["owner"]
    for other_obj in st["objects"].values():
        if other_obj.get("under") == o["oid"]:
            other_obj["under"] = None
            log(st, "%s の下にあった %s の重ね置きを外した" % (label(st, o), label(st, other_obj)))
        if other_obj.get("blocking") == o["oid"]:
            other_obj["blocking"] = None
    if o.get("under") is not None:
        o["under"] = None


def place(st, o, dest, position="top", index=None):
    src = zone_of(st, o["oid"])
    if src:
        st["zones"][src].remove(o["oid"])
    pile = st["zones"][dest]
    if position == "bottom":
        pile.insert(0, o["oid"])
    elif index is not None:
        pile.insert(max(0, len(pile) - index), o["oid"])
    else:
        pile.append(o["oid"])
    left = src and (src.endswith(":battlefield") or src == "stack")
    staying = dest.endswith(":battlefield") or dest == "stack"
    if left and not staying:
        clear_table_marks(st, o)
    elif src and src.endswith(":battlefield") and dest == "stack":
        clear_table_marks(st, o)
    return src


# ---------------------------------------------------------------- 乱数

def rng(st):
    st["rng_seq"] += 1
    return random.Random("%s:%d" % (st["seed"], st["rng_seq"])), st["rng_seq"]


def shuffle_pile(st, seat, pile="library"):
    r, n = rng(st)
    r.shuffle(st["zones"]["%s:%s" % (seat, pile)])
    return n


# ---------------------------------------------------------------- 表示

def pt_text(c):
    if c.get("power") is None:
        return ""
    return " %s/%s" % (c["power"], c["toughness"])


def perm_line(st, o, viewer, indent):
    c = card(st, o)
    bits = [label(st, o, viewer)]
    if visible(st, o, viewer):
        types = c.get("types") or []
        if "Creature" in types or o.get("kind") == "token" and c.get("power") is not None:
            bits.append(pt_text(c).strip())
        elif c.get("loyalty") is not None:
            bits.append("忠誠%s" % c["loyalty"])
    if o["tapped"]:
        bits.append("(タップ)")
    if o.get("face_down"):
        bits.append("(裏向き)")
    if o.get("flipped"):
        bits.append("(裏面)")
    if o.get("kind") in ("token", "copy"):
        bits.append("〈%s〉" % ("トークン" if o["kind"] == "token" else "コピー"))
    for k, v in sorted(o["counters"].items()):
        bits.append("%s×%d" % (k, v))
    if o["damage"]:
        bits.append("ダメージ%d" % o["damage"])
    if o.get("attacking"):
        bits.append("攻撃中→%s" % o["attacking"])
    if o.get("blocking") is not None:
        bits.append("ブロック中→[%d]" % o["blocking"])
    if o["controller"] != o["owner"]:
        bits.append("(オーナー %s)" % o["owner"])
    lines = [indent + " ".join(b for b in bits if b)]
    for n in o["notes"]:
        lines.append(indent + "   付箋%s「%s」%s" % (n["id"], n["text"], until_text(n)))
    return lines


def until_text(item):
    u = item.get("until")
    if not u:
        return ""
    if u == "eot":
        return "（T%dのターン終了時まで）" % item["turn"]
    return "（%s）" % u


def under_tree(st, host, viewer, indent):
    lines = []
    for o in sorted(st["objects"].values(), key=lambda x: x["oid"]):
        if o.get("under") == host["oid"]:
            z = zone_of(st, o["oid"])
            lines.append(indent + "└ 下: %s（%s）" % (label(st, o, viewer), zone_ja(z)))
    return lines


def zone_ja(z):
    if z == "stack":
        return "スタック"
    if not z:
        return "?"
    seat, pile = z.split(":")
    return seat + {"library": "ライブラリー", "hand": "手札", "battlefield": "戦場",
                   "graveyard": "墓地", "exile": "追放", "command": "統率領域",
                   "sideboard": "サイドボード"}[pile]


def hand_line(st, oid, viewer, reveal=False):
    o = obj(st, oid)
    if not reveal and not visible(st, o, viewer):
        return "[%d]" % oid
    c = card(st, o)
    extra = " ".join(x for x in (c.get("mana_cost") or "", "/".join(cardcache.TYPES_JA.get(t, t) for t in c.get("types") or []), pt_text(c).strip()) if x)
    return "[%d] %s %s" % (oid, name_of(st, o, viewer), extra)


def expired_notes(st):
    t = st["tracker"]
    out = []
    for o in st["objects"].values():
        for n in o["notes"]:
            if n.get("until") == "eot" and (t["turn"] > n["turn"] or t["phase"] == "ending.cleanup"):
                out.append((o, n))
    return out


def due_memos(st):
    t = st["tracker"]
    out = []
    for m in st["memos"]:
        if m["done"] or not m.get("at"):
            continue
        if m["at"] != t["phase"]:
            continue
        if m.get("player") and m["player"] != t["active"]:
            continue
        if m.get("turn") and t["turn"] < m["turn"]:
            continue
        out.append(m)
    return out


def render(st, viewer="all", hands=()):
    t = st["tracker"]
    out = ["== T%d %sのターン / %s ==（トラッカーはAIが動かす）"
           % (t["turn"], t["active"], PHASE_JA.get(t["phase"], t["phase"]))]
    for seat in (other(t["active"]), t["active"]):
        p = st["players"][seat]
        z = lambda pile: st["zones"]["%s:%s" % (seat, pile)]  # noqa: E731
        head = "%s %s ライフ%d 手札%d ライブラリー%d 墓地%d 追放%d" % (
            seat, p["name"], p["life"], len(z("hand")), len(z("library")),
            len(z("graveyard")), len(z("exile")))
        if p["counters"]:
            head += " " + " ".join("%s×%d" % kv for kv in sorted(p["counters"].items()))
        pool = "".join(c * p["mana"].get(c, 0) for c in MANA)
        if pool:
            head += " マナ{%s}" % pool
        out.append(head)
        for oid in z("battlefield"):
            o = obj(st, oid)
            out += perm_line(st, o, viewer, "   ")
            out += under_tree(st, o, viewer, "      ")
        for pile in ("graveyard", "exile", "command"):
            if z(pile):
                out.append("   %s: %s" % (zone_ja(seat + ":" + pile)[2:],
                                          " / ".join(label(st, obj(st, x), viewer) for x in z(pile))))
    if st["zones"]["stack"]:
        out.append("スタック（上から）:")
        for oid in reversed(st["zones"]["stack"]):
            o = obj(st, oid)
            text = ""
            if o.get("kind") == "ability":
                text = "  %s" % o["def"].get("text", "")
            out.append("   %s 〔%s〕%s" % (label(st, o, viewer), o["controller"], text))
            for n in o["notes"]:
                out.append("      付箋%s「%s」" % (n["id"], n["text"]))
    if st["markers"]:
        out.append("マーカー: " + " / ".join(
            "%s%s%s" % (k, "=" + v["holder"] if v.get("holder") else "",
                        "(%s)" % v["value"] if v.get("value") else "")
            for k, v in sorted(st["markers"].items())))
    open_memos = [m for m in st["memos"] if not m["done"]]
    if open_memos:
        out.append("メモ帳:")
        for m in open_memos:
            when = " ".join(x for x in (m.get("player") or "", PHASE_JA.get(m.get("at"), m.get("at") or ""),
                                        "T%d以降" % m["turn"] if m.get("turn") else "") if x)
            out.append("   %s%s「%s」" % (m["id"], " [%s]" % when if when else "", m["text"]))
    for o, n in expired_notes(st):
        out.append("!! 期限切れの付箋 %s: %s「%s」%s" % (n["id"], label(st, o, viewer), n["text"], until_text(n)))
    for m in due_memos(st):
        out.append("!! 時期のメモ %s「%s」" % (m["id"], m["text"]))
    for seat in hands:
        ids = st["zones"]["%s:hand" % seat]
        out.append("%s 手札(%d):" % (seat, len(ids)))
        out += ["   " + hand_line(st, x, viewer) for x in ids]
    return "\n".join(out)


def tracker_notices(st):
    lines = []
    for o, n in expired_notes(st):
        lines.append("!! 期限切れの付箋 %s: %s「%s」" % (n["id"], label(st, o), n["text"]))
    for m in due_memos(st):
        lines.append("!! 時期のメモ %s「%s」" % (m["id"], m["text"]))
    return lines


# ---------------------------------------------------------------- 開始

def snapshot_card(rec):
    keep = ("name", "printed_name", "mana_cost", "mana_value", "colors", "supertypes", "types",
            "subtypes", "type_line_en", "type_line_printed", "power", "toughness", "loyalty",
            "defense", "oracle_text_en", "oracle_text_printed", "keywords", "layout", "faces")
    return {k: rec.get(k) for k in keep}


def cmd_init(args, _st):
    path = state_path(args)
    if path.exists() and not args.force:
        raise Stop("%s は既にあります（別の名前にするか --force）。" % path)
    if args.goldfish and args.deck2:
        raise Stop("--goldfish では --deck2 を指定しません。")
    if not args.goldfish and not args.deck2:
        raise Stop("--deck2 か --goldfish が必要です。")
    specs = [("P1", args.deck1)] + ([("P2", args.deck2)] if args.deck2 else [])
    st = {"schema": SCHEMA, "seed": args.seed, "rng_seq": 0, "next_oid": 1,
          "players": {}, "objects": {}, "cards": {}, "zones": {"stack": []},
          "markers": {}, "memos": [], "next_note": 1, "next_memo": 1, "log": [],
          "goldfish": bool(args.goldfish), "decks": {},
          "tracker": {"turn": 1, "active": "P1", "phase": "pregame"}}
    missing = []
    for seat in SEATS:
        for pile in PILES:
            st["zones"]["%s:%s" % (seat, pile)] = []
        st["players"][seat] = {"name": "goldfish" if seat == "P2" and args.goldfish else seat,
                               "life": args.life, "counters": {}, "mana": {}, "mulligans": 0}
    for seat, spec in specs:
        deck = decks.resolve_source(spec, args.decks_dir, args.cards_dir, offline=args.offline)
        st["players"][seat]["name"] = deck["name"]
        st["decks"][seat] = {"name": deck["name"], "source": spec,
                             "strategy": (decks.strategy_path(deck["name"]).as_posix()
                                          if decks.strategy_path(deck["name"]) else None)}
        for pile, entries in (("library", deck["main"]), ("sideboard", deck.get("sideboard", []))):
            for e in entries:
                rec, how = cardcache.get(e["name"], args.cards_dir, offline=args.offline)
                if rec.get("unresolved") or how in ("missing", "offline", "notfound", "ambiguous"):
                    missing.append(e["name"])
                    continue
                st["cards"][rec["name"]] = snapshot_card(rec)
                for _ in range(e["count"]):
                    o = new_object(st, seat, rec["name"])
                    st["zones"]["%s:%s" % (seat, pile)].append(o["oid"])
    if missing:
        raise Stop("カード情報を用意できません（状態は保存していません）: %s"
                   % ", ".join(sorted(set(missing))))
    first = args.first
    if first == "random":
        first = random.Random("%s:first" % args.seed).choice(SEATS)
    st["tracker"]["active"] = first
    st["first"] = first
    for seat, _ in specs:
        n = shuffle_pile(st, seat)
        log(st, "%s のライブラリーをシャッフル rng#%d" % (seat, n))
    log(st, "開始 seed=%s 先手=%s" % (args.seed, first))
    save(path, st)
    print("開始: %s  seed=%s  先手=%s%s" % (path, args.seed, first, "（抽選）" if args.first == "random" else ""))
    for seat, _ in specs:
        d = st["decks"][seat]
        print("%s %s ライブラリー%d サイドボード%d" % (seat, d["name"], len(st["zones"][seat + ":library"]),
                                                  len(st["zones"][seat + ":sideboard"])))
        if d["strategy"]:
            print("方針: %s = %s（最初のプレイ判断より前に読む）" % (seat, d["strategy"]))
    print("初手は draw P1 7 / draw P2 7。以後の進行・判断はすべて操作する側が行います。")
    return None


# ---------------------------------------------------------------- 操作（状態を変える）

def cmd_draw(args, st):
    seat = seat_arg(args.seat)
    lib = st["zones"][seat + ":library"]
    drawn = []
    for _ in range(args.n):
        if not lib:
            break
        o = obj(st, lib[-1])
        place(st, o, seat + ":hand")
        drawn.append(o)
    short = args.n - len(drawn)
    names = ", ".join(label(st, o, seat) for o in drawn)
    log(st, "%s ドロー %d枚" % (seat, len(drawn)))
    if drawn:
        log(st, "%s ドロー: %s" % (seat, names), private=seat)
    print("%s ドロー: %s（ライブラリー残り%d）" % (seat, names if not args.quiet else "%d枚" % len(drawn), len(lib)))
    if short:
        print("!! ライブラリーが空で、%d枚は引けませんでした。" % short)


def cmd_mill(args, st):
    seat = seat_arg(args.seat)
    lib = st["zones"][seat + ":library"]
    milled = []
    for _ in range(min(args.n, len(lib))):
        o = obj(st, lib[-1])
        place(st, o, seat + ":graveyard")
        milled.append(label(st, o))
    log(st, "%s ライブラリーの上から墓地へ: %s" % (seat, ", ".join(milled)))
    print("墓地へ: %s（ライブラリー残り%d）" % (", ".join(milled) or "なし", len(lib)))


def cmd_move(args, st):
    moved = []
    for ref in args.refs:
        o = obj(st, ref)
        dest = parse_zone(st, o, args.zone, args.controller and seat_arg(args.controller))
        if dest.endswith(":library") and not (args.top or args.bottom or args.index is not None):
            raise Stop("ライブラリーへは --top / --bottom / --index N のどれかで位置を指定してください。")
        if o.get("kind") == "ability" and dest != "stack":
            raise Stop("%s は能力の目印です。解決後は remove で取り除きます。" % label(st, o))
        src = place(st, o, dest, "bottom" if args.bottom else "top", args.index)
        if dest.endswith(":battlefield") or dest == "stack":
            if args.controller:
                o["controller"] = seat_arg(args.controller)
            elif dest != "stack":
                o["controller"] = dest.split(":")[0]
        if dest.endswith(":battlefield"):
            if args.tapped:
                o["tapped"] = True
            if args.face_down:
                o["face_down"] = True
        viewer = "all" if not (dest.endswith(":hand") or dest.endswith(":library")) else o["owner"]
        text = "%s: %s → %s" % (label(st, o, viewer), zone_ja(src), zone_ja(dest))
        moved.append(text)
        private = o["owner"] if dest.endswith(":hand") and src and src.endswith(":library") else None
        log(st, text, private=private)
    print("\n".join(moved))


def cmd_remove(args, st):
    for ref in args.refs:
        o = obj(st, ref)
        if o.get("kind") == "card":
            raise Stop("%s は実在のカードです。テーブルから消せません（move で領域を移します）。" % label(st, o))
        z = zone_of(st, o["oid"])
        text = "取り除いた: %s（%s）" % (label(st, o), zone_ja(z))
        clear_table_marks(st, o)
        st["zones"][z].remove(o["oid"])
        del st["objects"][str(o["oid"])]
        log(st, text)
        print(text)


def cmd_tap(args, st):
    for ref in args.refs:
        o = obj(st, ref)
        o["tapped"] = args.cmd == "tap"
    text = "%s: %s" % ("タップ" if args.cmd == "tap" else "アンタップ",
                       ", ".join(label(st, obj(st, r)) for r in args.refs))
    log(st, text)
    print(text)


def cmd_flip(args, st):
    o = obj(st, args.ref)
    if args.what == "face-down":
        o["face_down"] = True
    elif args.what == "face-up":
        o["face_down"] = False
    else:
        o["flipped"] = not o["flipped"]
    text = "%s を%s" % (label(st, o), {"face-down": "裏向きにした", "face-up": "表向きにした"}.get(args.what, "反対の面にした"))
    log(st, text)
    print(text)


DELTA = re.compile(r"^([+-=])?(\d+)$")


def apply_delta(current, spec, floor=0):
    m = DELTA.match(spec)
    if not m:
        raise Stop("数は +N / -N / =N で指定します: %s" % spec)
    sign, n = m.group(1) or "+", int(m.group(2))
    value = n if sign == "=" else current + n if sign == "+" else current - n
    if floor is not None and value < floor:
        raise Stop("%d より少なくはできません（現在 %d、指定 %s）。" % (floor, current, spec))
    return value


def cmd_counter(args, st):
    if args.ref.upper() in SEATS:
        holder = st["players"][args.ref.upper()]["counters"]
        who = args.ref.upper()
    else:
        o = obj(st, args.ref)
        holder = o["counters"]
        who = label(st, o)
    before = holder.get(args.name, 0)
    after = apply_delta(before, args.delta)
    if after:
        holder[args.name] = after
    else:
        holder.pop(args.name, None)
    text = "%s %s: %d → %d" % (who, args.name, before, after)
    log(st, text)
    print(text)


def cmd_damage(args, st):
    if args.ref == "clear":
        n = 0
        for o in st["objects"].values():
            n += 1 if o["damage"] else 0
            o["damage"] = 0
        log(st, "ダメージ用ダイスをすべて外した（%d個）" % n)
        print("ダメージ用ダイスをすべて外した（%d個）" % n)
        return
    if args.delta is None:
        raise Stop("damage <oid> <+N|-N|=N> または damage clear")
    o = obj(st, args.ref)
    before = o["damage"]
    o["damage"] = apply_delta(before, args.delta)
    text = "%s ダメージ: %d → %d" % (label(st, o), before, o["damage"])
    log(st, text)
    print(text)


def cmd_life(args, st):
    seat = seat_arg(args.seat)
    p = st["players"][seat]
    before = p["life"]
    p["life"] = apply_delta(before, args.delta, floor=None)   # ライフパッドは負の数も書ける
    text = "%s ライフ: %d → %d" % (seat, before, p["life"])
    log(st, text)
    print(text)


def cmd_mana(args, st):
    seat = seat_arg(args.seat)
    pool = st["players"][seat]["mana"]
    if args.op == "clear":
        if args.symbols:
            raise Stop("clear には色を書きません。")
        pool.clear()
    else:
        if not args.symbols or not re.fullmatch("[%s]+" % MANA, args.symbols):
            raise Stop("mana %s %s <色>。色は WUBRGC で書きます（不特定コストも払う色で書く）。" % (seat, args.op))
        after = dict(pool)
        for c in args.symbols:
            if args.op == "add":
                after[c] = after.get(c, 0) + 1
            elif after.get(c, 0) <= 0:
                raise Stop("%s のマナ・ダイスに %s がありません（現在 {%s}）。" % (seat, c, pool_text(pool)))
            else:
                after[c] -= 1
        pool.clear()
        pool.update({c: n for c, n in after.items() if n})
    text = "%s マナ %s %s → {%s}" % (seat, args.op, args.symbols or "", pool_text(pool))
    log(st, text)
    print(text)


def pool_text(pool):
    return "".join(c * pool.get(c, 0) for c in MANA)


def cmd_token(args, st):
    seat = seat_arg(args.seat)
    if args.preset:
        if args.name or args.types or args.text or args.pt:
            raise Stop("--preset と個別の定義は併用できません。")
        name, types, subtypes, text = TOKEN_PRESETS[args.preset]
        definition = {"name": name, "types": [types], "subtypes": [subtypes], "oracle_text_en": text}
    else:
        if not args.name:
            raise Stop("token P1 <名前> [--pt 1/1] [--types ...] [--text ...] または --preset")
        types = args.types.split() if args.types else []
        definition = {"name": args.name, "types": [t for t in types if t in cardcache.CARD_TYPES],
                      "supertypes": [t for t in types if t in cardcache.SUPERTYPES],
                      "subtypes": [t for t in types if t not in cardcache.CARD_TYPES + cardcache.SUPERTYPES],
                      "oracle_text_en": args.text or "", "colors": list(args.colors or "")}
        if args.pt:
            m = re.fullmatch(r"([^/]+)/([^/]+)", args.pt)
            if not m:
                raise Stop("--pt は 1/1 の形で指定します。")
            definition["power"], definition["toughness"] = m.group(1), m.group(2)
    made = []
    for _ in range(args.n):
        o = new_object(st, seat, definition=dict(definition), kind="token")
        o["controller"] = seat
        st["zones"][seat + ":battlefield"].append(o["oid"])
        o["tapped"] = bool(args.tapped)
        made.append(o)
    text = "トークン生成: %s" % ", ".join(label(st, o) for o in made)
    log(st, text)
    print(text)
    return [o["oid"] for o in made]


def cmd_copy(args, st):
    seat = seat_arg(args.seat)
    src = obj(st, args.ref)
    definition = dict(card(st, src))
    o = new_object(st, seat, definition=definition, kind="copy")
    o["controller"] = seat
    dest = "stack" if args.to == "stack" else seat + ":battlefield"
    st["zones"][dest].append(o["oid"])
    text = "コピー生成: %s ← %s（%s）" % (label(st, o), label(st, src), zone_ja(dest))
    log(st, text)
    print(text)
    return [o["oid"]]


def cmd_ability(args, st):
    seat = seat_arg(args.seat)
    source = obj(st, args.src) if args.src else None
    title = "能力: " + (name_of(st, source) if source else args.text[:20])
    definition = {"name": title, "types": ["Ability"], "text": args.text,
                  "source": source["oid"] if source else None}
    o = new_object(st, seat, definition=definition, kind="ability")
    st["zones"]["stack"].append(o["oid"])
    text = "スタックへ %s 〔%s〕 %s" % (label(st, o), seat, args.text)
    log(st, text)
    print(text)
    return [o["oid"]]


def cmd_note(args, st):
    if args.ref == "rm":
        ids = set(args.args)
        found = False
        for o in st["objects"].values():
            keep = [n for n in o["notes"] if n["id"] not in ids]
            if len(keep) != len(o["notes"]):
                found = True
                removed = [n for n in o["notes"] if n["id"] in ids]
                o["notes"] = keep
                for n in removed:
                    log(st, "付箋%s を外した: %s「%s」" % (n["id"], label(st, o), n["text"]))
                    print("付箋%s を外した: %s「%s」" % (n["id"], label(st, o), n["text"]))
        if not found:
            raise Stop("付箋 %s はありません。" % " ".join(args.args))
        return
    if len(args.args) != 1:
        raise Stop('note <oid> "内容" [--until eot|"説明"] / note rm N1 N2')
    o = obj(st, args.ref)
    n = {"id": "N%d" % st["next_note"], "text": args.args[0], "until": args.until,
         "turn": st["tracker"]["turn"]}
    st["next_note"] += 1
    o["notes"].append(n)
    text = "付箋%s: %s「%s」%s" % (n["id"], label(st, o), n["text"], until_text(n))
    log(st, text)
    print(text)


def cmd_memo(args, st):
    if args.op == "list":
        rows = [m for m in st["memos"] if args.all or not m["done"]]
        for m in rows:
            print("%s %s%s「%s」%s" % (m["id"], "済 " if m["done"] else "",
                                       " ".join(x for x in (m.get("player") or "", m.get("at") or "") if x),
                                       m["text"], " — " + m["reason"] if m.get("reason") else ""))
        if not rows:
            print("メモはありません。")
        return "readonly"
    if args.op == "done":
        for mid in args.value:
            m = next((x for x in st["memos"] if x["id"] == mid), None)
            if not m:
                raise Stop("メモ %s はありません。" % mid)
            if m["done"]:
                raise Stop("メモ %s は既に済んでいます。" % mid)
            m["done"] = True
            m["reason"] = args.reason
            log(st, "メモ%s 済: %s%s" % (mid, m["text"], " — " + args.reason if args.reason else ""))
            print("メモ%s 済" % mid)
        return
    if len(args.value) != 1:
        raise Stop('memo add "内容" [--player P1] [--at フェイズ] [--turn N]')
    if args.at and args.at not in PHASE_NAMES:
        raise Stop("--at はフェイズ名です: %s" % " / ".join(PHASE_NAMES[1:]))
    turn = None
    if args.turn == "next":
        turn = st["tracker"]["turn"] + 1
    elif args.turn:
        turn = int(args.turn)
    m = {"id": "M%d" % st["next_memo"], "text": args.value[0], "player": args.player and seat_arg(args.player),
         "at": args.at, "turn": turn, "done": False, "written": where(st)}
    st["next_memo"] += 1
    st["memos"].append(m)
    log(st, "メモ%s: %s" % (m["id"], m["text"]))
    print("メモ%s: %s" % (m["id"], m["text"]))


def cmd_marker(args, st):
    if args.remove:
        if args.name not in st["markers"]:
            raise Stop("マーカー %s はありません。" % args.name)
        del st["markers"][args.name]
        text = "マーカー %s を片付けた" % args.name
    else:
        st["markers"][args.name] = {"holder": args.holder and seat_arg(args.holder), "value": args.value}
        text = "マーカー %s%s%s" % (args.name, " → " + args.holder.upper() if args.holder else "",
                                   " (%s)" % args.value if args.value else "")
    log(st, text)
    print(text)


def cmd_attach(args, st):
    o = obj(st, args.ref)
    if args.off:
        o["under"] = None
        text = "%s の重ね置きを外した" % label(st, o)
    else:
        if args.to is None:
            raise Stop("attach <oid> --to <下に置く先> / attach <oid> --off")
        host = obj(st, args.to)
        if host["oid"] == o["oid"]:
            raise Stop("自分自身の下には置けません。")
        cur = host
        while cur.get("under") is not None:
            if cur["under"] == o["oid"]:
                raise Stop("重ね置きが循環します。")
            cur = obj(st, cur["under"])
        o["under"] = host["oid"]
        text = "%s を %s に重ねた" % (label(st, o), label(st, host))
    log(st, text)
    print(text)


def cmd_attack(args, st):
    target = args.target.upper() if args.target.upper() in SEATS else "[%d]" % obj(st, args.target)["oid"]
    for ref in args.refs:
        obj(st, ref)["attacking"] = target
    text = "攻撃の位置: %s → %s" % (", ".join(label(st, obj(st, r)) for r in args.refs), target)
    log(st, text)
    print(text)


def cmd_block(args, st):
    b, a = obj(st, args.blocker), obj(st, args.attacker)
    b["blocking"] = a["oid"]
    text = "ブロックの位置: %s → %s" % (label(st, b), label(st, a))
    log(st, text)
    print(text)


def cmd_combat(args, st):
    for o in st["objects"].values():
        o["attacking"] = None
        o["blocking"] = None
    log(st, "攻撃・ブロックの位置を戻した")
    print("攻撃・ブロックの位置を戻した")


def cmd_turn(args, st):
    t = st["tracker"]
    if args.what == "next":
        t["turn"] += 1
        t["active"] = other(t["active"])
        t["phase"] = "beginning.untap"
    else:
        t["turn"] = int(args.what)
        if args.active:
            t["active"] = seat_arg(args.active)
    text = "トラッカー: %s" % where(st)
    log(st, text)
    print(text)
    for line in tracker_notices(st):
        print(line)


def cmd_phase(args, st):
    if args.name not in PHASE_NAMES:
        raise Stop("フェイズ名: %s" % " / ".join(PHASE_NAMES))
    st["tracker"]["phase"] = args.name
    text = "トラッカー: %s" % where(st)
    log(st, text)
    print(text)
    for line in tracker_notices(st):
        print(line)


def cmd_shuffle(args, st):
    seat = seat_arg(args.seat)
    n = shuffle_pile(st, seat)
    log(st, "%s のライブラリーをシャッフル rng#%d" % (seat, n))
    print("%s のライブラリーをシャッフル（rng#%d）" % (seat, n))


def cmd_mulligan(args, st):
    seat = seat_arg(args.seat)
    for oid in list(st["zones"][seat + ":hand"]):
        place(st, obj(st, oid), seat + ":library")
    n = shuffle_pile(st, seat)
    st["players"][seat]["mulligans"] += 1
    log(st, "%s 手札をライブラリーに戻してシャッフル rng#%d（%d回目）" % (seat, n, st["players"][seat]["mulligans"]))
    args.n, args.quiet = args.draw, False
    cmd_draw(args, st)


def cmd_roll(args, st):
    m = re.fullmatch(r"(\d*)d(\d+)", args.dice)
    if not m:
        raise Stop("roll 2d6 / roll d20")
    r, n = rng(st)
    vals = [r.randint(1, int(m.group(2))) for _ in range(int(m.group(1) or 1))]
    text = "ダイス %s: %s（合計%d）rng#%d" % (args.dice, vals, sum(vals), n)
    log(st, text)
    print(text)


def cmd_coin(args, st):
    r, n = rng(st)
    text = "コイン: %s rng#%d" % (r.choice(["表", "裏"]), n)
    log(st, text)
    print(text)


def cmd_pick(args, st):
    r, n = rng(st)
    choice = r.choice(args.items)
    text = "無作為に選択: %s（候補 %s）rng#%d" % (choice, " ".join(args.items), n)
    log(st, text)
    print(text)


def cmd_say(args, st):
    log(st, args.text, private=args.private and seat_arg(args.private))
    print("記録: %s" % args.text)


def cmd_reveal(args, st):
    text = "公開: %s" % ", ".join(label(st, obj(st, r)) for r in args.refs)
    log(st, text)
    print(text)


def cmd_look(args, st):
    seat = seat_arg(args.seat)
    viewer = args.as_ or seat
    lib = st["zones"][seat + ":library"]
    top = list(reversed(lib[-args.n:])) if args.n else []
    log(st, "%s が %s のライブラリーの上%d枚を見た" % (viewer, seat, len(top)))
    log(st, "上から: %s" % ", ".join(label(st, obj(st, x)) for x in top), private=viewer)
    print("%s のライブラリー 上から:" % seat)
    for i, x in enumerate(top, 1):
        print("  %d. %s" % (i, hand_line(st, x, viewer, reveal=True)))


def cmd_search(args, st):
    seat = seat_arg(args.seat)
    viewer = args.as_ or seat
    lib = st["zones"][seat + ":library"]
    rows = sorted(lib, key=lambda x: (name_of(st, obj(st, x)), x))
    if args.name:
        q = args.name.lower()
        rows = [x for x in rows if q in (name_of(st, obj(st, x)) + " " + obj(st, x)["card"]).lower()]
    log(st, "%s が %s のライブラリーを探した" % (viewer, seat))
    print("%s のライブラリー（名前順。積み順は伏せています）%s:" % (seat, " 絞り込み「%s」" % args.name if args.name else ""))
    for x in rows:
        print("  " + hand_line(st, x, viewer, reveal=True))
    if not rows:
        print("  該当なし")


def cmd_end(args, st):
    results = pathlib.Path(args.results) if args.results else state_path(args).with_name("results.jsonl")
    t = st["tracker"]
    rec = {"state": str(state_path(args)), "seed": st["seed"], "first": st.get("first"),
           "goldfish": st.get("goldfish", False),
           "decks": {s: d["name"] for s, d in st["decks"].items()},
           "winner": None if args.winner == "draw" else seat_arg(args.winner),
           "turn": t["turn"], "own_turn": None,
           "reason": args.reason, "tag": args.tag,
           "mulligans": {s: p["mulligans"] for s, p in st["players"].items()}}
    # 自ターン数: 先手なら (T+1)//2、後手なら T//2
    if rec["winner"]:
        rec["own_turn"] = (t["turn"] + 1) // 2 if st.get("first") == rec["winner"] else t["turn"] // 2
    if any(x.get("state") == rec["state"] for x in read_results(results)):
        raise Stop("この対局の結果は既に記録済みです: %s" % results)
    results.parent.mkdir(parents=True, exist_ok=True)
    with results.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    st["ended"] = rec
    log(st, "終了: 勝者 %s — %s" % (args.winner, args.reason))
    print("記録しました: %s（勝者 %s、T%d）" % (results, args.winner, t["turn"]))


# ---------------------------------------------------------------- 読むだけ

def read_results(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def cmd_show(args, st):
    viewer = args.as_ or "all"
    hands = []
    if args.hand in ("P1", "P2"):
        hands = [args.hand]
    elif args.hand == "both":
        hands = ["P1", "P2"]
    if args.as_ and any(h != args.as_ for h in hands):
        raise Stop("--as %s では相手の手札を表示できません。" % args.as_)
    print(render(st, viewer, hands))
    return "readonly"


def cmd_zone(args, st):
    viewer = args.as_ or "all"
    z = args.zone
    if ":" in z:
        seat, pile = z.split(":", 1)
        z = "%s:%s" % (seat.upper(), pile)
    if z not in st["zones"]:
        raise Stop("zone P1:graveyard のように指定します。")
    if z.endswith(":library"):
        raise Stop("ライブラリーの中身は見られません（look / search を使います）。")
    ids = st["zones"][z]
    print("%s（%d枚）:" % (zone_ja(z), len(ids)))
    for x in ids:
        print("  " + hand_line(st, x, viewer))
    return "readonly"


def cmd_card(args, st):
    for ref in args.refs:
        if ref.isdigit() or re.fullmatch(r"\[\d+\]", ref):
            o = obj(st, ref)
            if not visible(st, o, args.as_ or "all"):
                raise Stop("%s は見えません。" % label(st, o, args.as_ or "all"))
            c = card(st, o)
            if o.get("kind") == "ability":
                print("%s  %s" % (label(st, o), c.get("text")))
                continue
        else:
            c = st["cards"].get(ref) or next((v for v in st["cards"].values()
                                              if ref in (v.get("printed_name"), v.get("name"))), None)
            if c is None:
                rec, how = cardcache.get(ref, args.cards_dir, offline=args.offline)
                cardcache.report(ref, rec, how)
                if rec.get("unresolved"):
                    print("[missing] %s" % ref)
                    continue
                c = rec
        rec = cardcache.blank_record(c.get("name") or "?")
        rec.update({k: v for k, v in c.items() if v is not None})
        print(cardcache.summary(rec))
    return "readonly"


def cmd_log(args, st):
    viewer = args.as_
    rows = [e for e in st["log"] if not e.get("private") or viewer in (None, e["private"])]
    for e in rows[-args.tail:] if args.tail else rows:
        print("%s  %s%s" % (e["at"], e["text"], "  [秘匿:%s]" % e["private"] if e.get("private") else ""))
    return "readonly"


def cmd_aid(args, st):
    aid.run(args.what, st, sys.modules[__name__], args)
    return "readonly"


def cmd_stats(args, _st):
    path = pathlib.Path(args.results)
    rows = [r for r in read_results(path) if not args.tag or r.get("tag") == args.tag]
    if not rows:
        print("記録がありません: %s" % path)
        return "readonly"
    gold = [r for r in rows if r.get("goldfish")]
    duel = [r for r in rows if not r.get("goldfish")]
    if gold:
        turns = sorted(r["own_turn"] for r in gold if r.get("winner") and r.get("own_turn"))
        print("一人回し %d件: リーサル %d件 / 自ターン 平均%.2f 中央値%s 最速%s 最遅%s" % (
            len(gold), len(turns), sum(turns) / len(turns) if turns else 0,
            turns[len(turns) // 2] if turns else "-", turns[0] if turns else "-", turns[-1] if turns else "-"))
        for k in sorted(set(turns)):
            print("  自ターン%d: %d件" % (k, turns.count(k)))
    if duel:
        wins = {}
        for r in duel:
            name = r["decks"].get(r["winner"], "引き分け") if r.get("winner") else "引き分け"
            wins[name] = wins.get(name, 0) + 1
        print("対戦 %d件: %s" % (len(duel), " / ".join("%s %d勝" % kv for kv in sorted(wins.items()))))
    return "readonly"


# ---------------------------------------------------------------- バッチ

LABEL_RE = re.compile(r"\$([a-z][a-z0-9_]*)")
FORBIDDEN_IN_RUN = {"init", "run", "undo", "end", "stats"}


def cmd_run(args, st):
    """行を上から実行し、全部成功したときだけ1回保存する（1手 = 1回の保存）。"""
    text = sys.stdin.read() if args.file == "-" else pathlib.Path(args.file).read_text(encoding="utf-8-sig")
    parser = build_parser()
    draft = copy.deepcopy(st)
    labels = {}
    changed = False
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        def sub(m):
            if m.group(1) not in labels:
                raise Stop("%d行目: $%s は未定義です。" % (n, m.group(1)))
            return str(labels[m.group(1)])
        try:
            words = shlex.split(LABEL_RE.sub(sub, line))
            a = parser.parse_args(words)
        except SystemExit:
            raise Stop("%d行目の構文が不正です: %s（何も適用していません）" % (n, line))
        except Stop as e:
            raise Stop("%s（何も適用していません）" % e)
        if a.cmd in FORBIDDEN_IN_RUN:
            raise Stop("%d行目: %s は run の中で使えません（何も適用していません）。" % (n, a.cmd))
        a.state, a.cards_dir, a.offline = args.state, args.cards_dir, True
        a.as_ = a.as_ or args.as_
        try:
            enforce_seat(a, draft)
            result = HANDLERS[a.cmd](a, draft)
        except Stop as e:
            raise Stop("%d行目で停止: %s — %s（何も適用していません）" % (n, line, e))
        if result != "readonly":
            changed = True
        if getattr(a, "label", None):
            if not isinstance(result, list) or len(result) != 1:
                raise Stop("%d行目: --label は1つだけ作る行に付けます。" % n)
            if a.label in labels:
                raise Stop("%d行目: $%s は既に使われています。" % (n, a.label))
            labels[a.label] = result[0]
    if changed:
        st.clear()
        st.update(draft)
        return None
    return "readonly"


# ---------------------------------------------------------------- 席の制限

def enforce_seat(a, st):
    """--as P1 のとき、相手の非公開情報に触れる操作を止める。"""
    seat = getattr(a, "as_", None)
    if not seat:
        return
    if a.cmd in ("draw", "mulligan", "shuffle") and seat_arg(a.seat) != seat:
        raise Stop("--as %s では %s の %s はできません。" % (seat, a.seat, a.cmd))
    if a.cmd in ("look", "search") and seat_arg(a.seat) != seat and not getattr(a, "by_effect", False):
        raise Stop("相手のライブラリーを見るのは効果があるときだけです（--by-effect）。")
    if a.cmd == "show" and a.hand in ("both",) or (a.cmd == "show" and a.hand in SEATS and a.hand != seat):
        raise Stop("--as %s では相手の手札を表示できません。" % seat)


# ---------------------------------------------------------------- 取り消し

def cmd_undo(args, _st):
    path = state_path(args)
    hist = history_dir(path)
    files = sorted(hist.glob("*.json")) if hist.exists() else []
    if not 1 <= args.n <= len(files):
        raise Stop("戻せるのは1〜%d回分です（状態は変えていません）。" % len(files))
    target = files[-args.n]
    json.loads(target.read_text(encoding="utf-8"))
    path.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    for f in files[-args.n:]:
        f.unlink()
    print("%d回分戻しました（残り履歴 %d）。" % (args.n, len(files) - args.n))
    return "readonly"


# ---------------------------------------------------------------- 引数

def build_parser():
    ap = argparse.ArgumentParser(prog="table.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", help="状態ファイル（環境変数 MTG_STATE でも可）")
    ap.add_argument("--cards-dir", default=cardcache.DEFAULT_DIR)
    ap.add_argument("--decks-dir", default=decks.DEFAULT_DIR)
    ap.add_argument("--offline", action="store_true", help="カード情報を取得しない")
    ap.add_argument("--as", dest="as_", type=str.upper, choices=SEATS,
                    help="この席から見える範囲だけ表示・操作する")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="デッキを置き、シャッフルしてテーブルを作る")
    s.add_argument("--deck1", required=True, help="登録名かデッキリストのパス")
    s.add_argument("--deck2")
    s.add_argument("--goldfish", action="store_true", help="P2 はライブラリーなしの何もしない相手")
    s.add_argument("--seed", required=True)
    s.add_argument("--first", default="P1", choices=["P1", "P2", "random"])
    s.add_argument("--life", type=int, default=20)
    s.add_argument("--force", action="store_true")

    s = sub.add_parser("show", help="テーブルを表示")
    s.add_argument("--hand", default="none", choices=["none", "P1", "P2", "both"])
    s = sub.add_parser("zone", help="公開領域の中身（zone P1:graveyard）")
    s.add_argument("zone")
    s = sub.add_parser("card", help="カードを読む（oid か名前）")
    s.add_argument("refs", nargs="+")
    s = sub.add_parser("log", help="記録を表示")
    s.add_argument("--tail", type=int, default=0)

    s = sub.add_parser("draw", help="ライブラリーの一番上から手札へ")
    s.add_argument("seat")
    s.add_argument("n", nargs="?", type=int, default=1)
    s.add_argument("--quiet", action="store_true")
    s = sub.add_parser("mill", help="ライブラリーの上から墓地へ")
    s.add_argument("seat")
    s.add_argument("n", type=int)
    s = sub.add_parser("look", help="ライブラリーの上N枚を見る（動かさない）")
    s.add_argument("seat")
    s.add_argument("n", type=int)
    s.add_argument("--by-effect", action="store_true")
    s = sub.add_parser("search", help="ライブラリーを名前順で見る（積み順は伏せる）")
    s.add_argument("seat")
    s.add_argument("name", nargs="?")
    s.add_argument("--by-effect", action="store_true")
    s = sub.add_parser("reveal", help="カードを公開したことを記録")
    s.add_argument("refs", nargs="+")
    s = sub.add_parser("move", help="領域を移す（move 12 battlefield / move 12 P2:graveyard）")
    s.add_argument("refs", nargs="+")
    s.add_argument("zone")
    s.add_argument("--top", action="store_true")
    s.add_argument("--bottom", action="store_true")
    s.add_argument("--index", type=int, help="ライブラリーの上から何枚目に入れるか（0=一番上）")
    s.add_argument("--controller", help="戦場・スタックでの操作する席")
    s.add_argument("--tapped", action="store_true")
    s.add_argument("--face-down", action="store_true")
    s = sub.add_parser("remove", help="トークン・コピー・能力の目印をテーブルから取り除く")
    s.add_argument("refs", nargs="+")
    for name in ("tap", "untap"):
        s = sub.add_parser(name)
        s.add_argument("refs", nargs="+")
    s = sub.add_parser("flip", help="裏向き・表向き・反対の面")
    s.add_argument("ref")
    s.add_argument("what", choices=["face-down", "face-up", "transform"])
    s = sub.add_parser("counter", help="counter <oid|P1> <名前> <+N|-N|=N>")
    s.add_argument("ref")
    s.add_argument("name")
    s.add_argument("delta")
    s = sub.add_parser("damage", help="damage <oid> <+N|-N|=N> / damage clear")
    s.add_argument("ref")
    s.add_argument("delta", nargs="?")
    s = sub.add_parser("life", help="life P1 -3 / life P1 =20")
    s.add_argument("seat")
    s.add_argument("delta")
    s = sub.add_parser("mana", help="マナ・ダイス mana P1 add RG / pay R / clear")
    s.add_argument("seat")
    s.add_argument("op", choices=["add", "pay", "clear"])
    s.add_argument("symbols", nargs="?")
    s = sub.add_parser("token", help="トークンを置く")
    s.add_argument("seat")
    s.add_argument("name", nargs="?")
    s.add_argument("--pt")
    s.add_argument("--types", help='"Artifact Creature Golem" のように空白区切り')
    s.add_argument("--text")
    s.add_argument("--colors")
    s.add_argument("--preset", choices=sorted(TOKEN_PRESETS))
    s.add_argument("-n", type=int, default=1)
    s.add_argument("--tapped", action="store_true")
    s.add_argument("--label")
    s = sub.add_parser("copy", help="コピーを置く（copy P1 <oid> --to stack|battlefield）")
    s.add_argument("seat")
    s.add_argument("ref")
    s.add_argument("--to", default="stack", choices=["stack", "battlefield"])
    s.add_argument("--label")
    s = sub.add_parser("ability", help="スタックに能力の目印を置く")
    s.add_argument("seat")
    s.add_argument("text")
    s.add_argument("--src")
    s.add_argument("--label")
    s = sub.add_parser("note", help='付箋 note <oid> "内容" [--until eot] / note rm N1')
    s.add_argument("ref")
    s.add_argument("args", nargs="*")
    s.add_argument("--until")
    s = sub.add_parser("memo", help="メモ帳 add / done / list")
    s.add_argument("op", choices=["add", "done", "list"])
    s.add_argument("value", nargs="*")
    s.add_argument("--player")
    s.add_argument("--at", help="時期のフェイズ名（表示だけ。自動では処理しない）")
    s.add_argument("--turn", help="N か next")
    s.add_argument("--reason")
    s.add_argument("--all", action="store_true")
    s = sub.add_parser("marker", help="マーカー（統治者・昼夜など）")
    s.add_argument("name")
    s.add_argument("--holder")
    s.add_argument("--value")
    s.add_argument("--remove", action="store_true")
    s = sub.add_parser("attach", help="下に重ねる attach <oid> --to <oid> / --off")
    s.add_argument("ref")
    s.add_argument("--to")
    s.add_argument("--off", action="store_true")
    s = sub.add_parser("attack", help="攻撃の位置に出す（タップはしない）")
    s.add_argument("refs", nargs="+")
    s.add_argument("--target", required=True)
    s = sub.add_parser("block", help="block <ブロッカー> <攻撃クリーチャー>")
    s.add_argument("blocker")
    s.add_argument("attacker")
    sub.add_parser("combat-clear", help="攻撃・ブロックの位置を戻す")
    s = sub.add_parser("turn", help="トラッカー turn next / turn N --active P1")
    s.add_argument("what")
    s.add_argument("--active")
    s = sub.add_parser("phase", help="トラッカーのフェイズを置く")
    s.add_argument("name")
    s = sub.add_parser("shuffle")
    s.add_argument("seat")
    s = sub.add_parser("mulligan", help="手札をライブラリーに戻してシャッフルし、引き直す")
    s.add_argument("seat")
    s.add_argument("--draw", type=int, default=7)
    s = sub.add_parser("roll")
    s.add_argument("dice")
    sub.add_parser("coin")
    s = sub.add_parser("pick")
    s.add_argument("items", nargs="+")
    s = sub.add_parser("say", help="記録に1行残す")
    s.add_argument("text")
    s.add_argument("--private")
    s = sub.add_parser("aid", help="読むだけの補助（判定ではない）")
    s.add_argument("what", choices=aid.TOPICS)
    s.add_argument("--phase")
    s = sub.add_parser("run", help="まとめて実行し、全部成功したときだけ1回保存")
    s.add_argument("file")
    s = sub.add_parser("undo", help="保存をN回分戻す")
    s.add_argument("n", nargs="?", type=int, default=1)
    s = sub.add_parser("end", help="結果を results.jsonl に記録")
    s.add_argument("--winner", required=True, help="P1 / P2 / draw")
    s.add_argument("--reason", required=True)
    s.add_argument("--tag")
    s.add_argument("--results")
    s = sub.add_parser("stats", help="results.jsonl を集計")
    s.add_argument("results")
    s.add_argument("--tag")
    return ap


HANDLERS = {
    "init": cmd_init, "show": cmd_show, "zone": cmd_zone, "card": cmd_card, "log": cmd_log,
    "draw": cmd_draw, "mill": cmd_mill, "look": cmd_look, "search": cmd_search,
    "reveal": cmd_reveal, "move": cmd_move, "remove": cmd_remove, "tap": cmd_tap,
    "untap": cmd_tap, "flip": cmd_flip, "counter": cmd_counter, "damage": cmd_damage,
    "life": cmd_life, "mana": cmd_mana, "token": cmd_token, "copy": cmd_copy,
    "ability": cmd_ability, "note": cmd_note, "memo": cmd_memo, "marker": cmd_marker,
    "attach": cmd_attach, "attack": cmd_attack, "block": cmd_block,
    "combat-clear": cmd_combat, "turn": cmd_turn, "phase": cmd_phase,
    "shuffle": cmd_shuffle, "mulligan": cmd_mulligan, "roll": cmd_roll, "coin": cmd_coin,
    "pick": cmd_pick, "say": cmd_say, "aid": cmd_aid, "run": cmd_run, "undo": cmd_undo,
    "end": cmd_end, "stats": cmd_stats,
}
NO_STATE = {"init", "undo", "stats"}


def main(argv=None):
    cardcache.force_utf8()
    args = build_parser().parse_args(argv)
    try:
        if args.cmd in NO_STATE:
            HANDLERS[args.cmd](args, None)
            return 0
        st = load(args)
        enforce_seat(args, st)
        result = HANDLERS[args.cmd](args, st)
        if result != "readonly":
            save(state_path(args), st)
    except Stop as e:
        print("停止: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
