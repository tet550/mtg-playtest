"""Mana quantities grouped by explicit notes; eligibility is decided by the AI."""
import re


def clear(player):
    player["pool"].clear()
    player.get("mana_groups", {}).clear()


def describe(player):
    rows = ["通常 pool=%s" % player["pool"]]
    rows.extend("%s pool=%s 注記=%s" % (key, group["pool"], group["note"])
                for key, group in player.get("mana_groups", {}).items())
    return rows


def symbols(text, spending=False):
    if not text or not re.fullmatch(r"[WUBRGC0-9]+", text):
        raise SystemExit("マナはWUBRGCで指定してください。支払い時だけ数字を使えます。")
    numbers = re.findall(r"[0-9]+", text)
    if numbers and not spending:
        raise SystemExit("mana addに数字は使えません。無色はCで指定してください。")
    return sum(map(int, numbers)), [c for c in text if c in "WUBRGC"]


def spend(pool, text):
    """Validate the whole payment before returning a replacement pool."""
    generic, colors = symbols(text, spending=True)
    remaining = dict(pool)
    for color in colors:
        if remaining.get(color, 0) <= 0:
            raise SystemExit("指定したグループのマナが足りません: %s / pool=%s" % (text, pool))
        remaining[color] -= 1
    if sum(remaining.values()) < generic:
        raise SystemExit("指定したグループのマナが足りません: %s / pool=%s" % (text, pool))
    # Preserve the existing generic-payment order: colorless, then stored colors.
    for color in ["C"] + [c for c in remaining if c != "C"]:
        paid = min(generic, remaining.get(color, 0))
        if paid:
            remaining[color] -= paid
            generic -= paid
    return {c: n for c, n in remaining.items() if n > 0}


def command(api, args, st):
    player = st["players"][args.player]
    group_id = args.group or "normal"
    groups = player.get("mana_groups", {})
    if args.note is not None and (args.op != "add" or not args.note.strip()):
        raise SystemExit("--noteはadd専用で、空の注記は指定できません。")
    if args.op in ("list", "clear") and args.symbols is not None:
        raise SystemExit("list/clearにはマナ記号を指定しません。")
    if args.op == "list":
        if args.group:
            raise SystemExit("listは全グループを表示します。--groupは不要です。")
    else:
        detail = ""
        if args.note is not None and args.group:
            raise SystemExit("新しい注記は--note、既存グループへの追加は--groupを使ってください。")
        if group_id != "normal" and group_id not in groups:
            raise SystemExit("この席にマナグループ %s はありません。mana %s listで確認してください。" % (group_id, args.player))
        pool = player["pool"] if group_id == "normal" else groups[group_id]["pool"]
        if args.op == "clear":
            if not args.group:
                clear(player)
            elif group_id == "normal":
                pool.clear()
            else:
                del groups[group_id]
        elif args.op == "add":
            _, colors = symbols(args.symbols)
            if args.note is not None:
                number = player.get("next_mana_group", 1)
                group_id = "M%d" % number
                player["next_mana_group"] = number + 1
                pool = {}
                player.setdefault("mana_groups", {})[group_id] = {"note": args.note, "pool": pool}
            for color in colors:
                pool[color] = pool.get(color, 0) + 1
        elif args.op == "spend":
            remaining = spend(pool, args.symbols)
            used = {c: n - remaining.get(c, 0) for c, n in pool.items() if n != remaining.get(c, 0)}
            note = "" if group_id == "normal" else " 注記=" + groups[group_id]["note"]
            pool.clear()
            pool.update(remaining)
            print("支払い %s %s: %s%s" % (args.player, group_id, used, note))
            detail = " paid=%s%s" % (used, note)
            if group_id != "normal" and not pool:
                del groups[group_id]
        api.log(st, "%s mana %s %s group=%s%s" %
                (args.player, args.op, args.symbols or "", "all" if args.op == "clear" and not args.group else group_id,
                 (" note=" + args.note if args.note is not None else "") + detail))
    for row in describe(player):
        print(args.player + " " + row)


def add_parser(sub):
    s = sub.add_parser("mana", help="通常・注記別のマナを記帳。用途の適否はAIが判断")
    s.add_argument("player", choices=["P1", "P2"])
    s.add_argument("op", choices=["add", "spend", "clear", "list"])
    s.add_argument("symbols", nargs="?", help="add:GGC / spend:2G。数字は支払い専用")
    s.add_argument("--note", help="add専用。注記付きの新グループM番号を作る")
    s.add_argument("--group", help="add/spend/clear:この席のM番号、またはnormal。spend省略は通常のみ、clear省略は全て")
