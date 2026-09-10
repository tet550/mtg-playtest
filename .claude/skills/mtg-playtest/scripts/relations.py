"""Structured bookkeeping, not an Oracle interpreter.

All persisted references include a zone incarnation. Evaluated effects never
write derived values back into objects. The CLI supplies the mtg module as api
so this module is usable both from mtg.py and imported tests.
"""
import copy
import re


def ensure(st):
    if "objects" not in st:
        return
    if st.get("schema_version", 1) > 2:
        raise SystemExit("この状態ファイルは新しいschema_versionです。対応するCLIで開いてください。")
    st["schema_version"] = 2
    st.setdefault("fx", [])
    st.setdefault("links", [])
    st.setdefault("next_effect", 1)
    st.setdefault("next_link", 1)
    st.setdefault("source_history", {})
    for o in st.get("objects", {}).values():
        o.setdefault("incarnation", 1)


def ref(st, oid):
    o = st["objects"][str(oid)]
    return {"oid": int(oid), "incarnation": o.get("incarnation", 1)}


def live(st, r):
    o = st["objects"].get(str(r["oid"]))
    return bool(o and o.get("incarnation", 1) == r["incarnation"])


def label(r):
    return "%s@%s" % (r["oid"], r["incarnation"])


def parse_ref(api, st, value, stale=False):
    match = re.fullmatch(r"(\d+)@(\d+)", value or "")
    if match:
        r = {"oid": int(match[1]), "incarnation": int(match[2])}
        if stale and label(r) in st.get("source_history", {}):
            return r
        o = api.obj(st, r["oid"])
        if r["incarnation"] < 1 or r["incarnation"] > o.get("incarnation", 1):
            raise SystemExit("不正な世代: " + value)
        if not stale and not live(st, r):
            raise SystemExit("既に領域移動したオブジェクト: " + value)
        return r
    oid = api.resolve_ref(st, value)
    api.obj(st, oid)
    return ref(st, oid)


def battlefield(api, st, r):
    return live(st, r) and (api.zone_of(st, r["oid"]) or "").endswith(":battlefield")


def attached_check(api, st, source, target):
    if not battlefield(api, st, ref(st, source)):
        raise SystemExit("付与元は戦場に必要です。")
    if api.obj(st, source).get("attached_to") != target:
        raise SystemExit("--src の現在の装着先と付与対象が一致しません。先にattachしてください。")
    if any(f["source"] == ref(st, source) and f["scope"] == "attached" for f in st.get("fx", [])):
        raise SystemExit("この装着効果はfxで管理中です。二重計上を避け、fx setで訂正してください。")


def applicable(api, st, oid):
    current = ref(st, oid)
    for f in st.get("fx", []):
        if f["until"] == "source" or f["scope"] != "fixed":
            if not battlefield(api, st, f["source"]):
                continue
        if f["scope"] == "attached":
            source = api.obj(st, f["source"]["oid"])
            if source.get("attached_to") != oid or not battlefield(api, st, current):
                continue
        elif f["scope"] == "controller-creatures":
            source = api.obj(st, f["source"]["oid"])
            o = api.obj(st, oid)
            if not battlefield(api, st, current) or o["controller"] != source["controller"]:
                continue
            if "creature" not in [x.lower() for x in api.card_of(st, o).get("types", [])]:
                continue
        elif current not in f["targets"]:
            continue
        yield f


def values(api, st, oid):
    delta = [0, 0]
    base = None
    keywords = []
    for f in applicable(api, st, oid):
        if f.get("base") is not None:
            base = f["base"]
        amount = 0
        if f.get("counter") and live(st, f["source"]):
            amount = api.obj(st, f["source"]["oid"])["counters"].get(f["counter"], 0)
        for i in (0, 1):
            delta[i] += f["pt"][i] + amount * f["per_counter"][i]
        keywords.extend(f["grants"])
    return base, delta, keywords


def pt_arg(text):
    if not re.fullmatch(r"[+-]?\d+/[+-]?\d+", text):
        raise SystemExit("P/Tは +1/+0 または 2/2 の形式です（負数は --pt=-1/-1）。")
    return [int(x) for x in text.split("/")]


def find(st, kind, identity):
    for x in st.get(kind, []):
        if x["id"] == identity:
            return x
    raise SystemExit("該当するIDがありません: " + str(identity))


def cmd_fx(api, args, st):
    ensure(st)
    if args.op == "list":
        describe(api, st, effects=True)
        return
    if args.op == "check":
        issues = check(api, st)
        print("\n".join("!! " + x for x in issues) if issues else "関連付けの整合性: OK（未登録のカード効果・裁定は別途確認）")
        return
    if args.op == "remove":
        f = find(st, "fx", args.id)
        st["fx"].remove(f)
        api.log(st, "効果取消: " + f["id"])
        print("効果取消: " + f["id"])
        return
    old = find(st, "fx", args.id) if args.op == "set" else None
    if args.op == "add" and args.id:
        raise SystemExit("fx addは新しいIDを発行します。既存IDの訂正はfx setです。")
    if not args.src or not args.ability:
        raise SystemExit("fx add/set は --src と --ability が必須です。setは全定義を置換します。")
    source = parse_ref(api, st, args.src, stale=args.scope == "fixed")
    if (args.scope != "fixed" or args.until == "source") and not battlefield(api, st, source):
        raise SystemExit("常在型効果の発生源は戦場に必要です。")
    if args.scope != "fixed" and args.until != "source":
        raise SystemExit("attached/controller-creatures は --until source を指定してください。")
    targets = [parse_ref(api, st, t) for t in (args.targets or [])]
    if args.scope == "fixed" and not targets:
        raise SystemExit("fixedは--targetsが必須です（解決時の適用先を列挙）。")
    if args.scope != "fixed" and targets:
        raise SystemExit("常在型の適用先はscopeから計算します。--targetsは指定しません。")
    if args.counter and args.scope == "fixed":
        raise SystemExit("解決時に決まる数値は--ptに指定してください。カウンター連動は常在型専用です。")
    if bool(args.counter) != bool(args.per_counter):
        raise SystemExit("--counter と --per-counter は一緒に指定してください。")
    if not any((args.pt, args.base_pt, args.grant, args.manual, args.counter)):
        raise SystemExit("効果内容（--pt / --base-pt / --grant / --manual）が必要です。")
    if args.scope == "attached":
        legacy = [(o, field, e) for o in st["objects"].values() for field in ("mods", "grants")
                  for e in o.get(field, []) if e.get("until") == "attached" and str(e.get("src")) == str(source["oid"])]
        if legacy and not args.replace_legacy:
            raise SystemExit("同じ発生源の旧mod/grantがあります。合計を確認し --replace-legacy で一括移行してください。")
    else:
        legacy = []
        if args.replace_legacy:
            raise SystemExit("--replace-legacy は attached 専用です。")
    f = dict(id=old["id"] if old else "E%d" % st["next_effect"], source=source,
             ability=args.ability, scope=args.scope, until=args.until,
             targets=targets, pt=pt_arg(args.pt or "0/0"),
             base=pt_arg(args.base_pt) if args.base_pt else None,
             counter=args.counter, per_counter=pt_arg(args.per_counter or "0/0"),
             grants=args.grant or [], manual=args.manual, turn=st["turn"])
    for o, field, e in legacy:
        o[field].remove(e)
    if old:
        st["fx"][st["fx"].index(old)] = f  # correcting an effect preserves its timestamp
    else:
        st["fx"].append(f)
        st["next_effect"] += 1
    api.log(st, "効果登録: %s src=%s ability=%s" % (f["id"], label(source), f["ability"]))
    print("%s: src=%s %s / %s / %s / P/T=%s grants=%s%s" %
          (f["id"], label(source), f["ability"], f["scope"], f["until"], f["pt"], f["grants"],
           " / 要裁定: " + f["manual"] if f["manual"] else ""))


def check(api, st):
    issues = []
    for o in st.get("objects", {}).values():
        target = o.get("attached_to")
        if target is not None:
            if not battlefield(api, st, ref(st, o["oid"])):
                issues.append("[%s] 戦場外に装着関係があります" % o["oid"])
            if target not in ("P1", "P2") and (str(target) not in st["objects"] or
                    not battlefield(api, st, ref(st, target))):
                issues.append("[%s] 装着先が戦場にありません" % o["oid"])
        for e in o.get("mods", []) + o.get("grants", []):
            if e.get("until") == "attached":
                source = st["objects"].get(str(e.get("src")))
                if not source or source.get("attached_to") != o["oid"]:
                    issues.append("[%s] 旧mod/grantの発生源・装着先が不一致" % o["oid"])
    for f in st.get("fx", []):
        if f.get("manual"):
            issues.append("%s 要裁定: %s" % (f["id"], f["manual"]))
    for link in st.get("links", []):
        if link["status"] == "pending":
            issues.append("%s 帰還誘発をAPNAP順で linked trigger に登録してください" % link["id"])
    return issues


def require_resolved(api, st):
    api.bookkeeping.require_ready(st)
    issues = check(api, st)
    if issues:
        raise SystemExit("未処理の関連付けがあります:\n" + "\n".join(issues))


def describe(api, st, effects=False):
    for f in st.get("fx", []):
        if effects:
            print("%s src=%s ability=%s scope=%s until=%s targets=%s pt=%s base=%s counter=%s*%s grant=%s%s" %
                  (f["id"], label(f["source"]), f["ability"], f["scope"], f["until"],
                   ",".join(label(r) for r in f["targets"]), f["pt"], f["base"],
                   f["counter"], f["per_counter"], f["grants"], " 要裁定=" + f["manual"] if f["manual"] else ""))
    for link in st.get("links", []):
        cards = [label(r) for r in link["cards"] if live(st, r) and
                 (api.zone_of(st, r["oid"]) or "").endswith(":exile")]
        print("%s src=%s ability=%s cards=[%s] return=%s status=%s%s" %
              (link["id"], label(link["source"]), link["ability"], ",".join(cards),
               link["return"], link["status"], " play=" + str(link["permission"]) if link.get("permission") else ""))
    if not effects:
        for issue in check(api, st):
            print("!! " + issue)


def cleanup(st):
    """End-of-turn bookkeeping, shared by cleanup step and turn shortcut."""
    for o in st["objects"].values():
        o["damage"] = 0
        for field in ("mods", "grants"):
            o[field] = [e for e in o.get(field, []) if e.get("until") != "eot"]
    st["effects"] = [e for e in st.get("effects", []) if e.get("until") != "eot"]
    st["fx"] = [f for f in st.get("fx", []) if f["until"] != "eot"]
    for link in st.get("links", []):
        if (link.get("permission") or {}).get("until") == "eot":
            link["permission"] = None


def on_step(api, st):
    if st["phase"] == "ending.cleanup":
        cleanup(st)
    if st["phase"] == "ending.end":
        for link in st.get("links", []):
            if link["return"] == "next-end" and link["status"] == "waiting" and st["turn"] >= link.get("due_turn", 0):
                link["status"] = "pending"
                print("!! %s: 次の終了ステップの帰還誘発。linked triggerでスタックへ。" % link["id"])


def step_guard(api, st, target):
    if api.PHASES.index(target) > api.PHASES.index("ending.end"):
        if any(x["return"] == "next-end" and x["status"] == "waiting" and
               x.get("due_turn", 0) <= st["turn"] for x in st.get("links", [])):
            raise SystemExit("phase setでは帰還誘発を飛ばせません。phase to ending.endを使ってください。")


def move_many(api, st, moves):
    """Commit an already adjudicated simultaneous zone event, then consequences.

    moves is [(oid, destination, top)]. Does not run SBA or put triggers on the
    stack during resolution. Source-leaves return effects do not use the stack.
    """
    ensure(st)
    if len({m[0] for m in moves}) != len(moves):
        raise SystemExit("同じオブジェクトを一度の移動で重複指定できません。")
    for oid, dest, _ in moves:
        api.obj(st, oid)
        if dest not in st["zones"]:
            raise SystemExit("不明なゾーン: " + dest)
    leaving = []
    entering = []
    for oid, dest, top in moves:
        o = api.obj(st, oid)
        src = api.zone_of(st, oid)
        # Battlefield control changes are not zone changes.
        same_battlefield = (src or "").endswith(":battlefield") and dest.endswith(":battlefield")
        if dest.endswith(":battlefield") and not (src or "").endswith(":battlefield"):
            entering.append(oid)
        if src != dest and not same_battlefield:
            old = ref(st, oid)
            st["source_history"][label(old)] = {"controller": o["controller"], "name": o["name"]}
            spell_to_permanent = src == "stack" and dest.endswith(":battlefield")
            carried = {field: copy.deepcopy(o.get(field, [])) for field in ("mods", "grants")} if spell_to_permanent else {}
            if (src or "").endswith(":battlefield"):
                leaving.append((old, copy.deepcopy(o)))
                api.drop_attached_mods(st, oid)
                api.detach_all_from(st, oid)
            o.update(incarnation=o.get("incarnation", 1) + 1, tapped=False,
                     damage=0, counters={}, mods=[], grants=[], attached_to=None, note="",
                     controller=o["owner"], sick=True)
            o.update(carried)
            if spell_to_permanent:
                for f in st["fx"]:
                    if old in f["targets"]:
                        f["targets"] = [ref(st, oid) if r == old else r for r in f["targets"]]
            if not spell_to_permanent and not o.get("token") and o.get("card_key"):
                st.get("cards", {}).pop(o.pop("card_key"), None)
        if src:
            st["zones"][src].remove(oid)
        if top:
            st["zones"][dest].insert(0, oid)
        else:
            st["zones"][dest].append(oid)
        if dest.endswith(":battlefield"):
            if o["controller"] != dest.split(":")[0]:
                o["sick"] = True
            o["controller"] = dest.split(":")[0]
    for old, last in leaving:
        st["fx"] = [f for f in st["fx"] if not (f["source"] == old and
                     (f["until"] == "source" or f["scope"] != "fixed"))]
        for link in list(st["links"]):
            if link["source"] != old or link["status"] != "waiting":
                continue
            link["last_known_source"] = last
            if link["return"] == "until-source-leaves":
                return_cards(api, st, link)
            elif link["return"] == "leave-trigger":
                link["controller"] = last["controller"]
                link["status"] = "pending"
                print("!! %s: 離脱による帰還誘発。linked triggerでスタックへ。" % link["id"])
    api.bookkeeping.report_entries(api, st, entering)


def return_cards(api, st, link):
    moves = [(r["oid"], link["destinations"][label(r)], False) for r in link["cards"]
             if live(st, r) and (api.zone_of(st, r["oid"]) or "").endswith(":exile")
             and not api.obj(st, r["oid"]).get("token")]
    link["status"] = "returned"
    link["permission"] = None
    move_many(api, st, moves)
    for oid, dest, _ in moves:
        print("%s: [%s] → %s" % (link["id"], oid, dest))
    api.log(st, "%s 帰還: %s" % (link["id"], ",".join(str(x[0]) for x in moves) or "該当なし"))


def cmd_linked(api, args, st):
    ensure(st)
    if args.op == "list":
        describe(api, st)
        return
    if args.op == "exile":
        if args.via:
            if args.src or args.ability:
                raise SystemExit("--via と --src/--ability は併用できません。")
            ability = api.obj(st, api.resolve_ref(st, args.via))
            if not ability.get("source_ref") or api.zone_of(st, ability["oid"]) != "stack":
                raise SystemExit("--viaは--src付きでスタックに登録した能力を指定してください。")
            if st["zones"]["stack"][-1] != ability["oid"]:
                raise SystemExit("--viaの能力がスタックの一番上ではありません。")
            args.src = label(ability["source_ref"])
            args.ability = ability.get("ability_key") or "ability"
        if not args.src or not args.ability or not args.refs:
            raise SystemExit("linked exile は --src・--ability・追放するoidが必須です。")
        source = parse_ref(api, st, args.src, stale=True)
        if args.return_mode == "until-source-leaves" and not battlefield(api, st, source):
            print("指定された発生源は既に戦場を離れています。追放しません。")
            return
        if args.return_mode == "leave-trigger" and not battlefield(api, st, source):
            raise SystemExit("離脱済み発生源のETB/LTB順序は個別裁定が必要です。自動帰還を登録できません。")
        if bool(args.play_until) != bool(args.player):
            raise SystemExit("プレイ許可は --play-until と --player を一緒に指定してください。")
        refs = [parse_ref(api, st, r) for r in args.refs]
        if len({label(r) for r in refs}) != len(refs):
            raise SystemExit("追放対象が重複しています。")
        destinations = {}
        for r in refs:
            z = api.zone_of(st, r["oid"])
            if not z or z.endswith(":exile") or z == "stack":
                raise SystemExit("既に追放中・スタック上・領域外の対象はこの操作では扱えません。")
            if api.obj(st, r["oid"]).get("facedown"):
                raise SystemExit("裏向き追放は閲覧権の個別裁定が必要です。この操作は表向き専用です。")
            if r == source and args.return_mode in ("until-source-leaves", "leave-trigger"):
                raise SystemExit("発生源自身の追放・帰還は個別裁定が必要です。")
            owner = api.obj(st, r["oid"])["owner"]
            destinations[r["oid"]] = owner + ":" + (args.to or z.split(":")[1])
        link = dict(id="L%d" % st["next_link"], source=source, ability=args.ability,
                    cards=[], destinations={}, status="waiting", return_mode=args.return_mode,
                    controller=(st.get("source_history", {}).get(label(source)) or api.obj(st, source["oid"]))["controller"], permission=None)
        link["return"] = link.pop("return_mode")
        link["due_turn"] = st["turn"] + (1 if st["phase"] in ("ending.end", "ending.cleanup") else 0)
        move_many(api, st, [(r["oid"], api.obj(st, r["oid"])["owner"] + ":exile", False) for r in refs])
        link["cards"] = [ref(st, r["oid"]) for r in refs]
        link["destinations"] = {label(r): destinations[r["oid"]] for r in link["cards"]}
        if args.play_until:
            link["permission"] = dict(player=args.player, until=args.play_until,
                                      timing="normal", costs="normal", lands=True)
        st["links"].append(link)
        st["next_link"] += 1
        api.log(st, "%s 関連追放 src=%s ability=%s cards=%s" %
                (link["id"], label(source), args.ability, ",".join(label(r) for r in link["cards"])))
        describe(api, st)
        return
    if args.op == "play":
        if len(args.refs) != 2 or not args.player:
            raise SystemExit("linked play L番号 oid --player P1 の形式です。")
        link = find(st, "links", args.refs[0])
        r = parse_ref(api, st, args.refs[1])
        permit = link.get("permission")
        if not permit or permit["player"] != args.player or r not in link["cards"] or not (api.zone_of(st, r["oid"]) or "").endswith(":exile"):
            raise SystemExit("このプレイヤー・オブジェクトに有効なプレイ許可がありません。")
        require_resolved(api, st)
        if st.get("priority") != args.player:
            raise SystemExit("プレイする席に優先権がありません。")
        o = api.obj(st, r["oid"])
        types = [x.lower() for x in api.card_of(st, o).get("types", [])]
        main = st["active"] == args.player and st["phase"] in ("precombat_main", "postcombat_main") and not st["zones"]["stack"]
        if "land" in types:
            p = st["players"][args.player]
            if not main or p["lands_played"] >= p["land_drops"]:
                raise SystemExit("土地をプレイできるタイミング・回数ではありません。")
            move_many(api, st, [(r["oid"], args.player + ":battlefield", False)])
            p["lands_played"] += 1
        else:
            if not main and "instant" not in types and not api.has_kw(st, r["oid"], "flash", "瞬速"):
                raise SystemExit("通常のソーサリー・タイミングではありません。")
            api.push_spell(st, r["oid"], args.player, cast=True)
            print("!! コスト支払い・対象・追加制限は別途確認して記帳してください。")
        api.log(st, "%s の許可で %s が [%s] をプレイ" % (link["id"], args.player, r["oid"]))
        print("%s: [%s] → %s" % (link["id"], r["oid"], api.zone_of(st, r["oid"])))
        return
    if len(args.refs) != 1:
        raise SystemExit("linked %s はL番号を1つ指定してください。" % args.op)
    link = find(st, "links", args.refs[0])
    if args.op == "trigger":
        if link["status"] != "pending":
            raise SystemExit("帰還誘発待ちではありません。")
        name = "帰還 " + link["id"]
        oid = api.push_ability(st, name, link["controller"])
        o = api.obj(st, oid)
        o["linked_id"] = link["id"]
        link["status"] = "stack"
        api.log(st, "%s 帰還誘発をスタックへ [%s]" % (link["id"], oid))
        print("%s 帰還誘発をスタックへ [%s]（応答後 linked resolve / 打ち消しは linked counter）" % (link["id"], oid))
    elif args.op in ("resolve", "counter"):
        if args.op == "resolve":
            api.bookkeeping.require_ready(st)
        stack = st["zones"]["stack"]
        if not stack or api.obj(st, stack[-1]).get("linked_id") != link["id"]:
            raise SystemExit("指定の帰還誘発がスタックの一番上ではありません。")
        api.remove_ability(st, stack[-1])
        if args.op == "resolve":
            return_cards(api, st, link)
        else:
            link["status"] = "countered"
            api.log(st, link["id"] + " 帰還誘発を打ち消し")
            print(link["id"] + " 帰還誘発を打ち消し。カードは追放に残ります。")


def add_parser(sub):
    s = sub.add_parser("fx", help="構造化効果。装備先・カウンターから自動計算")
    s.add_argument("op", choices=["add", "set", "remove", "list", "check"])
    s.add_argument("id", nargs="?", help="set/removeのE番号")
    s.add_argument("--src", help="発生源oid（またはoid@世代）。add/set必須")
    s.add_argument("--ability", help="能力識別名。add/set必須")
    s.add_argument("--scope", choices=["attached", "fixed", "controller-creatures"], default="attached")
    s.add_argument("--targets", nargs="+", help="fixed専用。解決時の対象oidを列挙")
    s.add_argument("--until", choices=["source", "eot", "permanent"], default="source")
    s.add_argument("--pt", help="加算修整 +1/+0（負数は --pt=-1/-1）")
    s.add_argument("--base-pt", help="基本P/T設定 2/2。複数なら登録順に適用")
    s.add_argument("--grant", nargs="+", help="付与キーワード")
    s.add_argument("--counter", help="発生源のカウンター名。--per-counterと併用")
    s.add_argument("--per-counter", help="カウンター1個あたりの修整 +1/+0")
    s.add_argument("--manual", help="未対応の相互作用・条件。裁定完了まで戦闘計算等を停止")
    s.add_argument("--replace-legacy", action="store_true", help="同じ発生源の旧attached mod/grantを除去して移行")
    s = sub.add_parser("linked", help="関連追放・帰還誘発・通常条件のプレイ許可を記帳")
    s.add_argument("op", choices=["exile", "list", "trigger", "resolve", "counter", "play"])
    s.add_argument("refs", nargs="*", help="exile:対象oid群、play:L番号 oid、trigger/resolve/counter:L番号")
    s.add_argument("--src", help="発生源oid@世代（解決前離脱を区別）。exile必須")
    s.add_argument("--ability", help="関連する能力の識別名。exile必須")
    s.add_argument("--via", help="発生源・能力識別名をスタック上の能力oidから取得。--src/--abilityの代わり")
    s.add_argument("--return", dest="return_mode", choices=["until-source-leaves", "leave-trigger", "next-end", "none"], default="none")
    s.add_argument("--to", choices=["battlefield", "hand", "graveyard"], help="帰還先。既定は追放前の領域、所有者側")
    s.add_argument("--play-until", choices=["eot", "permanent"], help="通常のタイミング・コストで土地/呪文をプレイできる許可を記録")
    s.add_argument("--player", choices=["P1", "P2"], help="exile:許可を受ける席（--play-untilと併用）。play:プレイする席、必須")
