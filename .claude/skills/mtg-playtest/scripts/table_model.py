"""テーブルの状態・参照・物理操作。ファイルやCLI、表示には依存しない。"""
import random
import re
SCHEMA = "mtg-playtest/table@1"
SEATS = ("P1", "P2")
PILES = ("library", "hand", "battlefield", "graveyard", "exile", "command", "sideboard")
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


def where(st):
    t = st["tracker"]
    return "T%d %s %s" % (t["turn"], t["active"], PHASE_JA.get(t["phase"], t["phase"]))


def log(st, text, private=None):
    st["log"].append({"at": where(st), "text": text, "private": private})


def other(seat):
    return "P2" if seat == "P1" else "P1"


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


TWO_SIDED = ("transform", "modal_dfc", "double_faced_token", "reversible_card", "meld")


def card(st, o):
    """いま見えている面に印刷されている内容。トークン・能力・コピーは作成時の定義。

    両面カードは表と裏で別の面が見えるので、反対の面にしてあれば裏面の内容を返す。
    分割カードや出来事は1つの面に両方が印刷されているので、全体をそのまま返す。
    """
    c = o.get("def") or st["cards"].get(o["card"], {"name": o["card"]})
    faces = c.get("faces") or []
    if c.get("layout") in TWO_SIDED and len(faces) >= 2:
        face = dict(faces[1] if o.get("flipped") else faces[0])
        face["layout"] = c.get("layout")
        face["faces"] = []
        face["full_name"] = c.get("name")
        return face
    return c


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
             face_down=False, arrived=None)
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


def rng(st):
    st["rng_seq"] += 1
    return random.Random("%s:%d" % (st["seed"], st["rng_seq"])), st["rng_seq"]


def shuffle_pile(st, seat, pile="library"):
    r, n = rng(st)
    r.shuffle(st["zones"]["%s:%s" % (seat, pile)])
    return n


def expand_refs(st, refs):
    """86-91 のような範囲を、実在するオブジェクトの oid に広げる。"""
    out = []
    for ref in refs:
        m = re.fullmatch(r"\[?(\d+)-(\d+)\]?", str(ref))
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            found = [x for x in range(lo, hi + 1) if str(x) in st["objects"]]
            if not found:
                raise Stop("%s の範囲にオブジェクトはありません。" % ref)
            out += found
        else:
            out.append(ref)
    return out


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


def mana_symbols(words):
    """「RRG」「G:12」「G:12 C」を色の並びにする。不正なら空。"""
    out = []
    for w in words:
        m = re.fullmatch(r"([%s]):(\d+)" % MANA, w)
        if m:
            out += [m.group(1)] * int(m.group(2))
        elif re.fullmatch("[%s]+" % MANA, w):
            out += list(w)
        else:
            return []
    return out
