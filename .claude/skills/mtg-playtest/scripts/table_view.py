"""テーブルの表示。状態を読み、文字列を組み立てる。"""
import cardcache
from table_model import (
    MANA, PHASE_JA, card, due_memos, expired_notes, label, name_of, obj, other, visible,
    zone_of,
)


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
    if o.get("arrived") == st["tracker"]["turn"] and zone_of(st, o["oid"]).endswith(":battlefield"):
        bits.append("(このターンに出た)")
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
        bits.append("攻撃中→%s%s" % (o["attacking"], "（自分の席）" if o["attacking"] == o["controller"] else ""))
    if o.get("blocking") is not None:
        bits.append("ブロック中→[%d]" % o["blocking"])
    if o["controller"] != o["owner"]:
        bits.append("(オーナー %s)" % o["owner"])
    lines = [indent + " ".join(b for b in bits if b)]
    for n in o["notes"]:
        lines.append(indent + "   付箋%s「%s」%s" % (n["id"], n["text"], until_text(n)))
    return lines


def plain_land(st, o, viewer):
    """何も乗っていない土地は1行にまとめて表示する（紙でも奥に並べる）。"""
    c = card(st, o)
    if "Land" not in (c.get("types") or []) or not visible(st, o, viewer):
        return False
    if o["counters"] or o["damage"] or o["notes"] or o.get("attacking") or o.get("blocking") is not None:
        return False
    if o["controller"] != o["owner"] or o.get("kind") != "card":
        return False
    return not any(x.get("under") == o["oid"] for x in st["objects"].values())


def until_text(item):
    u = item.get("until")
    if not u:
        return ""
    if u == "eot":
        return "（T%dのターン終了時まで）" % item["turn"]
    return "（%s）" % u


def under_tree(st, host, viewer, indent, depth=0):
    """重ねたカードは重ねた先の下にだけ出す。戦場のものは状態も出す。"""
    lines = []
    for o in sorted(st["objects"].values(), key=lambda x: x["oid"]):
        if o.get("under") != host["oid"]:
            continue
        z = zone_of(st, o["oid"]) or ""
        if z.endswith(":battlefield") and depth < 5:
            sub = perm_line(st, o, viewer, indent + "   ")
            sub[0] = indent + "└ 下: " + sub[0].strip()
            if o["controller"] != host["controller"]:
                sub[0] += " 〔%sがコントロール〕" % o["controller"]
            lines += sub
            lines += under_tree(st, o, viewer, indent + "   ", depth + 1)
        else:
            lines.append(indent + "└ 下: %s（%s）" % (label(st, o, viewer), zone_ja(z)))
    return lines


def oid_ranges(ids):
    """[86, 87, 88, 90] → 86-88,90"""
    ids = sorted(ids)
    out, start, prev = [], ids[0], ids[0]
    for x in ids[1:] + [None]:
        if x is not None and x == prev + 1:
            prev = x
            continue
        out.append(str(start) if start == prev else "%d-%d" % (start, prev))
        if x is not None:
            start = prev = x
    return ",".join(out)


def tucked(st, o):
    """戦場にある別のカードの下に重ねてあるか。"""
    host = o.get("under")
    return host is not None and str(host) in st["objects"] and \
        (zone_of(st, host) or "").endswith(":battlefield") and \
        (zone_of(st, o["oid"]) or "").endswith(":battlefield")


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
        pool = pool_text(p["mana"])
        if pool:
            head += " マナ{%s}" % pool
        out.append(head)
        plain_lands, groups, order = [], {}, []
        for oid in z("battlefield"):
            o = obj(st, oid)
            if tucked(st, o):
                continue
            if plain_land(st, o, viewer):
                plain_lands.append(o)
                continue
            lines = perm_line(st, o, viewer, "   ")
            children = under_tree(st, o, viewer, "      ")
            if len(lines) == 1 and not children:
                # 同じ状態の同名カードは紙でも重ねて置く。1行にまとめる。
                key = lines[0].split("] ", 1)[-1]
                if key not in groups:
                    groups[key] = []
                    order.append(("group", key))
                groups[key].append(o["oid"])
            else:
                order.append(("lines", lines + children))
        for kind, item in order:
            if kind == "lines":
                out += item
            else:
                ids = groups[item]
                if len(ids) == 1:
                    out.append("   [%d] %s" % (ids[0], item))
                else:
                    out.append("   [%s] %s ×%d" % (oid_ranges(ids), item, len(ids)))
        if plain_lands:
            out.append("   土地: " + " / ".join(perm_line(st, o, viewer, "")[0] for o in plain_lands))
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


def pool_text(pool):
    """浮いているマナ。多い色は G×12 のように数で書く。"""
    return "".join((c * pool[c]) if pool[c] <= 5 else "%s×%d" % (c, pool[c])
                   for c in MANA if pool.get(c))
