"""読むだけの補助。紙のプレイヤーが使うオラクル検索や計算機にあたる。

守ること:
1. 状態を変えない
2. 進行を止めない
3. 消してもテーブル（table.py）が動く

出すのはヒントであって判定ではない。漏れも誤りもありうるので、最後はカードを読んで決める。
"""
import re

TOPICS = ("triggers", "creatures", "check")

# 誘発型能力らしい行。英語オラクルと日本語の印刷文の両方を見る。
TRIGGER_RE = re.compile(
    r"^(?:[^—\n]{0,40}—\s*)?(?:When|Whenever|At the beginning|At end of|At the end of)\b"
    r"|(?:たび|とき|開始時|終了時)[、,]", re.I)
# 本文に When/Whenever が出てこない、キーワードで書かれた誘発型能力。
TRIGGER_KEYWORDS = ("Prowess", "Mobilize", "Job select", "Exploit", "Afterlife", "Evolve",
                    "Exalted", "Annihilator", "Battle cry", "Myriad", "Persist", "Undying",
                    "Cascade", "Ward", "Dethrone", "Training", "Backup", "Melee", "Renown")
PHASE_WORDS = {
    "beginning.upkeep": r"upkeep|アップキープ",
    "beginning.draw": r"draw step|ドロー・?ステップ",
    "precombat_main": r"precombat main|first main|戦闘前メイン",
    "combat.begin": r"beginning of combat|戦闘開始",
    "combat.attackers": r"attacks|attack with|攻撃する",
    "combat.blockers": r"blocks|becomes blocked|ブロック",
    "combat.damage": r"combat damage|戦闘ダメージ",
    "combat.end": r"end of combat|戦闘終了",
    "postcombat_main": r"postcombat main|second main|戦闘後メイン",
    "ending.end": r"end step|終了ステップ",
}


def texts(card):
    out = []
    for f in (card.get("faces") or [card]):
        for key in ("oracle_text_en", "oracle_text_printed", "text"):
            if f.get(key):
                out.extend(line.strip() for line in f[key].splitlines() if line.strip())
    return out


def strip_reminder(line):
    return re.sub(r"\([^)]*\)|（[^）]*）", "", line).strip()


def trigger_lines(card):
    lines = []
    for line in texts(card):
        body = strip_reminder(line)
        if TRIGGER_RE.search(body) or any(body.lower().startswith(k.lower()) for k in TRIGGER_KEYWORDS):
            lines.append(body)
    return list(dict.fromkeys(lines))


def run(topic, st, table, args):
    print("（補助: 判定ではありません。最後はカードを読んで決めてください）")
    {"triggers": triggers, "creatures": creatures, "check": check}[topic](st, table, args)


def triggers(st, table, args):
    phase = getattr(args, "phase", None)
    if phase and phase not in PHASE_WORDS:
        print("--phase に使える名前: %s" % " / ".join(PHASE_WORDS))
        return
    pattern = re.compile(PHASE_WORDS[phase], re.I) if phase else None
    found = 0
    for zone in ("P1:battlefield", "P2:battlefield", "P1:command", "P2:command"):
        for oid in st["zones"][zone]:
            o = table.obj(st, oid)
            if o.get("face_down"):
                continue
            for line in trigger_lines(table.card(st, o)):
                if pattern and not pattern.search(line):
                    continue
                print("%s %s: %s" % (zone.split(":")[0], table.label(st, o), line))
                found += 1
    if not found:
        print("該当する行はありません%s。" % ("（%s）" % phase if phase else ""))


def printed_pt(card):
    try:
        return int(card.get("power")), int(card.get("toughness"))
    except (TypeError, ValueError):
        return None


def creatures(st, table, args):
    rows = 0
    for seat in ("P1", "P2"):
        for oid in st["zones"][seat + ":battlefield"]:
            o = table.obj(st, oid)
            c = table.card(st, o)
            if "Creature" not in (c.get("types") or []) and c.get("power") is None:
                continue
            base = printed_pt(c)
            plus = o["counters"].get("+1/+1", 0) - o["counters"].get("-1/-1", 0)
            parts = ["%s %s 印刷%s/%s" % (seat, table.label(st, o), c.get("power"), c.get("toughness"))]
            if plus:
                parts.append("カウンター%+d/%+d" % (plus, plus))
            if base:
                parts.append("＝%d/%d（付箋は含まない）" % (base[0] + plus, base[1] + plus))
            if o["damage"]:
                parts.append("ダメージ%d" % o["damage"])
            if o["notes"]:
                parts.append("付箋 " + " / ".join("%s「%s」" % (n["id"], n["text"]) for n in o["notes"]))
            if base and base[1] + plus <= 0:
                parts.append("← タフネスが0以下に見える")
            elif base and o["damage"] and o["damage"] >= base[1] + plus:
                parts.append("← ダメージがタフネス以上に見える")
            print(" ".join(parts))
            rows += 1
    if not rows:
        print("戦場にクリーチャーは見当たりません。")


def check(st, table, args):
    """見落としやすい盤面の状態を並べる。SBAの判定ではなく、見る場所の案内。"""
    hints = []
    for seat, p in st["players"].items():
        if p["life"] <= 0:
            hints.append("%s のライフが%dです" % (seat, p["life"]))
        if p["counters"].get("poison", 0) >= 10:
            hints.append("%s の毒カウンターが%dです" % (seat, p["counters"]["poison"]))
        if p["mana"]:
            hints.append("%s のマナ・ダイスが残っています" % seat)
    legends = {}
    for seat in ("P1", "P2"):
        for oid in st["zones"][seat + ":battlefield"]:
            o = table.obj(st, oid)
            c = table.card(st, o)
            if "Legendary" in (c.get("supertypes") or []):
                legends.setdefault((seat, c.get("name")), []).append(o["oid"])
            subs = [s.lower() for s in c.get("subtypes") or []]
            if "aura" in subs and o.get("under") is None:
                hints.append("%s はオーラですが、どこにも重ねていません" % table.label(st, o))
            if c.get("loyalty") is not None and "loyalty" not in o["counters"]:
                hints.append("%s に忠誠カウンターが置かれていません" % table.label(st, o))
            if o["counters"].get("+1/+1") and o["counters"].get("-1/-1"):
                hints.append("%s に +1/+1 と -1/-1 の両方のカウンターがあります" % table.label(st, o))
    for (seat, name), ids in legends.items():
        if len(ids) > 1:
            hints.append("%s が伝説の %s を%d つ持っています %s" % (seat, name, len(ids), ids))
    for z, ids in st["zones"].items():
        if z.endswith(":battlefield") or z == "stack":
            continue
        for oid in ids:
            o = table.obj(st, oid)
            if o.get("kind") in ("token", "copy", "ability"):
                hints.append("%s（%s）が %s にあります" % (table.label(st, o), o["kind"], table.zone_ja(z)))
    for o, n in table.expired_notes(st):
        hints.append("付箋%s「%s」の期限を過ぎています（%s）" % (n["id"], n["text"], table.label(st, o)))
    for m in table.due_memos(st):
        hints.append("メモ%s「%s」の時期です" % (m["id"], m["text"]))
    for line in hints:
        print("- " + line)
    if not hints:
        print("目立つ点はありません。")
    creatures_hint = []
    for seat in ("P1", "P2"):
        for oid in st["zones"][seat + ":battlefield"]:
            o = table.obj(st, oid)
            c = table.card(st, o)
            base = printed_pt(c)
            if not base:
                continue
            plus = o["counters"].get("+1/+1", 0) - o["counters"].get("-1/-1", 0)
            if base[1] + plus <= 0 or (o["damage"] and o["damage"] >= base[1] + plus):
                creatures_hint.append(table.label(st, o))
    if creatures_hint:
        print("- 印刷値とカウンターだけで見ると、タフネスを超えるダメージ等がありそうなもの: %s"
              "（付箋の修整は含まない）" % ", ".join(creatures_hint))
