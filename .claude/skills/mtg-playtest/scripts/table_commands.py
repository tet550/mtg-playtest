"""コマンドごとの操作。保存の単位と引数解析は table.py が管理する。"""
import json
import pathlib
import random
import re

import aid
import cardcache
import decks
from table_model import (
    PHASE_NAMES, PILES, SCHEMA, SEATS, Stop, TOKEN_PRESETS, apply_delta, card,
    clear_table_marks, expand_refs, label, log, mana_symbols, name_of, new_object, obj,
    other, parse_zone, place, rng, seat_arg, shuffle_pile, visible, where, zone_of,
)
from table_store import read_results, save, state_path
from table_view import hand_line, pool_text, render, tracker_notices, until_text, zone_ja


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
    for ref in expand_refs(st, args.refs):
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
        if dest.endswith(":battlefield") and not (src or "").endswith(":battlefield"):
            o["arrived"] = st["tracker"]["turn"]
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
    for ref in expand_refs(st, args.refs):
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
    refs = expand_refs(st, args.refs)
    if args.all:
        if refs:
            raise Stop("--all と oid は一緒に書きません。")
        seat = seat_arg(args.all)
        refs = [x for x in st["zones"][seat + ":battlefield"] if obj(st, x)["tapped"] != (args.cmd == "tap")]
        if args.cmd == "tap":
            raise Stop("--all はアンタップ専用です。")
    elif not refs:
        raise Stop("%s <oid...>%s" % (args.cmd, " / untap --all P1" if args.cmd == "untap" else ""))
    for ref in refs:
        o = obj(st, ref)
        o["tapped"] = args.cmd == "tap"
    text = "%s: %s" % ("タップ" if args.cmd == "tap" else "アンタップ",
                       ", ".join(label(st, obj(st, r)) for r in refs) or "なし")
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
        colors = mana_symbols(args.symbols)
        if not colors:
            raise Stop("mana %s %s <色>。色は WUBRGC で書き、多いときは G:12 のように個数を添える"
                       "（不特定コストも払う色で書く）。" % (seat, args.op))
        after = dict(pool)
        for c in colors:
            if args.op == "add":
                after[c] = after.get(c, 0) + 1
            elif after.get(c, 0) <= 0:
                raise Stop("%s のマナ・ダイスに %s がありません（現在 {%s}）。" % (seat, c, pool_text(pool)))
            else:
                after[c] -= 1
        pool.clear()
        pool.update({c: n for c, n in after.items() if n})
    text = "%s マナ %s %s → {%s}" % (seat, args.op, " ".join(args.symbols), pool_text(pool))
    log(st, text)
    print(text)


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
        o["arrived"] = st["tracker"]["turn"]
        o["tapped"] = bool(args.tapped)
        made.append(o)
    text = "トークン生成: %s" % ", ".join(label(st, o) for o in made)
    log(st, text)
    print(text)
    return [o["oid"] for o in made]


def cmd_copy(args, st):
    seat = seat_arg(args.seat)
    src = obj(st, args.ref)
    dest = "stack" if args.to == "stack" else seat + ":battlefield"
    made = []
    for _ in range(args.n):
        o = new_object(st, seat, definition=dict(card(st, src)), kind="copy")
        o["controller"] = seat
        st["zones"][dest].append(o["oid"])
        if dest != "stack":
            o["arrived"] = st["tracker"]["turn"]
        made.append(o)
    text = "コピー生成: %s ← %s（%s）" % (", ".join(label(st, o) for o in made), label(st, src), zone_ja(dest))
    log(st, text)
    print(text)
    return [o["oid"] for o in made]


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
    if args.ref == "set":
        if len(args.args) != 2:
            raise Stop('note set N1 "新しい内容" [--until ...]')
        nid, text = args.args
        for o in st["objects"].values():
            for n in o["notes"]:
                if n["id"] == nid:
                    before = n["text"]
                    n["text"] = text
                    if args.until:
                        n["until"], n["turn"] = args.until, st["tracker"]["turn"]
                    line = "付箋%s を書き直した: %s「%s」→「%s」" % (nid, label(st, o), before, text)
                    log(st, line)
                    print(line)
                    return
        raise Stop("付箋 %s はありません。" % nid)
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
    args.refs = expand_refs(st, args.refs)
    target = args.target.upper() if args.target.upper() in SEATS else "[%d]" % obj(st, args.target)["oid"]
    for ref in args.refs:
        obj(st, ref)["attacking"] = target
    text = "攻撃の位置: %s → %s" % (", ".join(label(st, obj(st, r)) for r in args.refs), target)
    own = sorted({obj(st, r)["controller"] for r in args.refs if obj(st, r)["controller"] == target})
    if own:
        # ルールの判定ではなく事実の注記。自分の席に向けた指定は打ち間違いが多い。
        text += "（%s 自身の席）" % "・".join(own)
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
    args.refs = expand_refs(st, args.refs)
    text = "公開: %s" % ", ".join("[%d] %s（%s）" % (obj(st, r)["oid"], name_of(st, obj(st, r)),
                                                   zone_ja(zone_of(st, obj(st, r)["oid"])))
                                   for r in args.refs)
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
    aid.run(args.what, st, args)
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
