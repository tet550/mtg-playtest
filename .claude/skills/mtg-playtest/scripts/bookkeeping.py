"""Declared work and reminders. No card text interpreter or trigger engine."""
import contextlib
import copy
import io
import pathlib
import re

import relations


# 「〜するたび／Whenever ... enters」だけを拾う。「これが戦場に出たとき」という
# 自分自身の ETB は、出したカードを見れば分かるので対象外。
_WATCH_RE = re.compile(r"(たび|whenever)", re.I)
_ENTER_RE = re.compile(r"(戦場に出る|enters)", re.I)
_SUPPRESS = ("能力を誘発させない", "cause abilities to trigger")
# 「これが戦場に出るか攻撃するたび」型は、そのカード自身が出たときにしか誘発しない。
# 他のパーマネントが出るたびに候補として出すと、実際に数えたい上陸・ETB誘発が
# 埋もれて流し読みになる。
_SELF_RE = re.compile(
    r"(これが|この[^、。]{0,12}が(?:戦場に出|攻撃)"
    r"|(?:when|whenever)\s+(?:this|it)\b[^,.]{0,30}\benters)", re.I)
# 「あなたがコントロールしている〜が戦場に出るたび」型は、相手のものが出ても誘発しない。
_YOURS_RE = re.compile(r"(あなたがコントロール|you control|under your control)", re.I)
# Only narrow, unambiguous subjects: leave mixed/unknown conditions for the AI.
_CREATURE_ONLY_ENTER_RE = re.compile(
    r"^\s*whenever\s+(?:a|another|one or more)\s+creatures?"
    r"(?:\s+you control)?\s+enters?\b", re.I)
_LAND_ONLY_ENTER_RE = re.compile(
    r"^\s*(?:(?:landfall|上陸)\s*[—–―−-]\s*)?"
    r"(?:whenever\s+(?:a|another|one or more)\s+lands?"
    r"(?:\s+you control)?\s+enters?\b"
    r"|(?:あなたがコントロールしている)?土地[１２12一二]?つ?が戦場に出るたび)", re.I)


def enter_watchers(api, st, oid):
    """あるパーマネントが戦場に出たことで誘発しうる、他のパーマネントを列挙する。

    上陸のように「土地を置くたびに数える」効果は、記憶に頼ると必ずどこかで落ちる。
    **誘発するかどうかは判定しない。**該当しそうな文を並べるだけ。

    ただし、**構造上ありえないもの**は落とす。自己参照（「これが戦場に出るか
    攻撃するたび」）と、自軍限定（「あなたがコントロールしている〜が出るたび」に
    相手のものが出た場合）の2つ。これを残すと毎ターン同じ空振りが並び、
    本当に数えたい誘発を見落とすようになる。
    """
    entered = api.obj(st, oid)
    who = entered.get("controller") or entered.get("owner")
    ec = api.card_of(st, entered)
    types = [t.lower() for t in ec.get("types", [])]
    # サブタイプ（Dwarf / Equipment …）も見る。部族で書かれた誘発
    #（「あなたがコントロールしているドワーフが戦場に出るたび」）は
    # タイプ名だけを探していると丸ごと落ちる。
    want = ["パーマネント", "permanent"] + [t for t in ec.get("subtypes", []) if t]
    for t, words in (("land", ("上陸", "landfall", "土地", "land")),
                     ("creature", ("クリーチャー", "creature")),
                     ("artifact", ("アーティファクト", "artifact")),
                     ("enchantment", ("エンチャント", "enchantment"))):
        if t in types:
            want += list(words)

    hits, suppressors = [], []
    for pid in ("P1", "P2"):
        for other in st["zones"]["%s:battlefield" % pid]:
            if int(other) == int(oid):
                continue
            w = st["objects"][str(other)]
            wc = api.card_of(st, w)
            text = wc.get("oracle", "") or ""
            if any(k in text.lower() for k in _SUPPRESS):
                suppressors.append((pid, api.disp_of(st, w), other))
            self_names = [n.lower() for n in
                          (w.get("name"), wc.get("printed_name")) if n]
            for line in text.splitlines():
                low = line.lower()
                if not (_WATCH_RE.search(low) and _ENTER_RE.search(low)):
                    continue
                condition = re.split(r"[,、]", low, maxsplit=1)[0]
                if _SELF_RE.search(condition) or any(n in condition for n in self_names):
                    continue                      # 自分が出たときにしか誘発しない
                mine = bool(_YOURS_RE.search(condition))
                if mine and who != pid:
                    continue                      # 自軍限定なのに出たのは相手のもの
                if "creature" not in types and _CREATURE_ONLY_ENTER_RE.search(line):
                    continue                      # 明示的なクリーチャー限定条件
                if "land" not in types and _LAND_ONLY_ENTER_RE.search(line):
                    continue                      # 明示的な土地限定条件
                # 自軍限定の「〜が戦場に出るたび」は、部族名・カード名など
                # こちらが語彙を持っていない書かれ方をしていても数え落とせない
                # （キーリの「ドワーフや装備品が出るたび」など）。名詞の一致は求めない。
                if not mine and not any(k.lower() in low for k in want):
                    continue
                hits.append((pid, api.disp_of(st, w), other, line.strip()))
    return hits, suppressors


def report_entries(api, st, oids):
    """Group entry hints, preserving distinct abilities; counts are not triggers."""
    players, candidates, suppressors = {}, {}, {}
    for oid in oids:
        entered = api.obj(st, oid)
        who = entered["controller"]
        players[who] = players.get(who, 0) + 1
        hits, blocked = enter_watchers(api, st, oid)
        for hit in hits:
            key = (who, *hit)
            candidates[key] = candidates.get(key, 0) + 1
        for blocked_by in blocked:
            suppressors[blocked_by] = None
    for who, count in players.items():
        reminders = list(reminder_messages(st, "enter", who, count))
        if not reminders and not any(key[0] == who for key in candidates) and not suppressors:
            continue
        print("戦場登場の確認: %s %s件（誘発数はAIが判断）" % (who, count))
        for (side, pid, name, other, line), matches in candidates.items():
            if side == who:
                print("!! 誘発の確認: %s %s(%s) — %s ／ 候補の登場 %s件"
                      % (pid, name, other, line, matches))
        for message in reminders:
            print(message)
    for pid, name, other in suppressors:
        print("!! ただし %s %s(%s) が誘発を抑止している可能性があります。"
              % (pid, name, other))


def require_ready(st):
    waiting = [p["id"] for p in st.get("pending", []) if p["status"] in ("pending", "resolving")]
    waiting += [p["id"] for p in st.get("links", []) if p["status"] == "pending"]
    if waiting:
        raise SystemExit("未処理: %s。pending listで確認し、登録・解決の続きを処理してください。" % ", ".join(waiting))


def guard(args, st):
    """A paused resolution allows inspection/registration, not unrelated actions."""
    if not st or not any(p["status"] == "resolving" for p in st.get("pending", [])):
        return
    if args.cmd in {"show", "hand", "view", "zone", "look", "log", "undo", "note"}:
        return
    if args.cmd == "pending" and args.op in {"add", "list", "resolve"}:
        return
    if args.cmd == "mana" and args.op == "list":
        return
    if args.cmd in {"fx", "linked", "remind"} and args.op in {"list", "check"}:
        return
    if args.cmd == "stack" and args.op == "show":
        return
    if args.cmd == "card" and args.op == "show":
        return
    raise SystemExit("解決途中です。効果の続きは pending resolve、訂正はundoを使用してください。")


def list_pending(st, all_items=False, include_links=True):
    for p in st.get("pending", []):
        if all_items or p["status"] not in ("done", "cancelled"):
            print("%s [%s] %s %s / stack=%s / 適用済み区間=%s" %
                  (p["id"], p["status"], p["controller"], p["text"],
                   p.get("stack_oid"), ",".join(p["parts"]) or "なし"))
    for link in st.get("links", []) if include_links else []:
        if link["status"] in ("pending", "stack"):
            print("%s [%s] 帰還誘発（linkedで処理。pendingへ重複登録しない）" % (link["id"], link["status"]))


def cmd_pending(api, args, st):
    if args.op == "list":
        list_pending(st, args.all)
        return
    if args.op == "add":
        if not args.value or not args.controller:
            raise SystemExit("pending add \"説明\" --controller P1 [--src oid] が必要です。")
        source = relations.parse_ref(api, st, args.src, stale=True) if args.src else None
        identity = "T%d" % st.get("next_pending", 1)
        st.setdefault("pending", []).append(dict(id=identity, text=args.value, controller=args.controller,
                                                  source=source, status="pending", stack_oid=None, parts={}))
        st["next_pending"] = st.get("next_pending", 1) + 1
        api.log(st, "%s 処理待ち登録: %s" % (identity, args.value))
        print("%s: %s（誘発・対象・順序はAIが確認）" % (identity, args.value))
        return
    p = relations.find(st, "pending", args.value)
    if p["status"] == "cancelled":
        raise SystemExit("%s は既にcancelledです。取消済みIDは再利用できません。"
                         "新しい処理は pending add で登録し、バッチ内は --label trigger → "
                         "pending stack $trigger で参照してください。"
                         "取消し自体の訂正は保存単位を確認してundoしてください（状態変更なし）。" % p["id"])
    if p["status"] == "done":
        raise SystemExit("%s は既に%sです。再適用できません。" % (p["id"], p["status"]))
    if args.op == "stack":
        if p["status"] != "pending":
            raise SystemExit("既にスタック登録済みです。")
        if any(x["status"] == "resolving" for x in st["pending"]):
            raise SystemExit("解決中のため、誘発のスタック登録は解決完了後です。")
        oid = api.push_ability(st, p["text"], p["controller"], args.targets, p["source"], p["id"])
        api.obj(st, oid)["pending_id"] = p["id"]
        p.update(status="stack", stack_oid=oid)
        api.log(st, "%s スタックへ [%s]" % (p["id"], oid))
        print("%s → スタック[%s]" % (p["id"], oid))
    elif args.op == "cancel":
        if not args.reason:
            raise SystemExit("取消しには --reason が必須です。")
        if p["status"] == "resolving":
            raise SystemExit("途中まで適用した解決は取消せません。undoで適用区間ごと戻してください。")
        if p["stack_oid"] is not None:
            api.remove_ability(st, p["stack_oid"])
        p.update(status="cancelled", reason=args.reason)
        api.log(st, "%s 取消: %s" % (p["id"], args.reason))
        print("%s 取消: %s" % (p["id"], args.reason))
    elif args.op == "resolve":
        resolve_part(api, args, st, p)


# Only state-local effect operations. No phase/SBA, nested batches, cache edits,
# result files, undo, or stack mutation. Draw/selection boundaries must be last.
EFFECT_COMMANDS = {"move", "tap", "untap", "life", "counter", "damage", "mod", "grant",
                   "mana", "token", "attach", "pcounter", "draw", "mill", "search", "shuffle",
                   "bottom", "look", "roll", "coin", "pick", "note", "show", "hand"}
OBSERVE_RESULT = {"draw", "search", "look", "roll", "coin", "pick"}


def resolve_part(api, args, st, p):
    if p["status"] not in ("stack", "resolving"):
        raise SystemExit("先にpending stackで登録してください。")
    if not st["zones"]["stack"] or st["zones"]["stack"][-1] != p["stack_oid"]:
        raise SystemExit("この処理待ち能力はスタックの一番上ではありません。")
    if p["status"] == "stack":
        require_ready(st)
    commands = getattr(args, 'commands', None)
    if not (args.file or commands) or not args.part:
        raise SystemExit("pending resolve T番号 (--file <実ファイル> または --do <操作>) --part <区間名> [--pause] が必要です。")
    if args.part in p["parts"]:
        raise SystemExit("区間 %s は適用済みです。再実行できません。" % args.part)
    prepared = []
    parser = api.build_parser()
    gl = ["--state", args.state, "--cards-dir", args.cards_dir, "--offline"]
    if args.seat:
        gl += ["--as", args.seat]
    text = '\n'.join(commands) + '\n' if commands else pathlib.Path(args.file).read_text(encoding="utf-8-sig")
    label = args.file or '--do'
    for n, line in api.command_lines(text):
        try:
            words = api.command_words(line)
            if not words or words[0] not in EFFECT_COMMANDS | {"fx", "linked"}:
                raise ValueError("解決用ファイルで使用できないコマンドです")
            a = api.parse_file_command(words, gl, parser)
            if a.cmd == "fx" and a.op not in ("add", "set", "remove"):
                raise ValueError("fxはadd/set/removeのみ使用できます")
            if a.cmd == "linked" and a.op != "exile":
                raise ValueError("linkedはexileのみ使用できます")
            if a.cmd == "note" and getattr(a, "event", None):
                raise ValueError("note --eventは解決用区間ではなくrun --compactの直下に置いてください")
            api.enforce_seat(a)
            prepared.append((n, line, a))
        except (ValueError, SystemExit) as error:
            raise SystemExit("%s:%s: %s（状態変更なし）" % (label, n, error))
    if any(a.cmd in OBSERVE_RESULT for _, _, a in prepared[:-1]):
        raise SystemExit("ドロー・サーチ・乱数・lookは区間の最後に置いて結果を確認してください（状態変更なし）。")
    draft = copy.deepcopy(st)
    pending = relations.find(draft, "pending", p["id"])
    pending["status"] = "resolving"
    offline = api._CTX["offline"]
    output = io.StringIO()
    try:
        api._CTX["offline"] = True
        with contextlib.redirect_stdout(output):
            for n, line, a in prepared:
                api.command_handlers()[a.cmd](a, draft)
    except (SystemExit, ValueError, KeyError, OSError) as error:
        raise SystemExit("解決区間 %s の %s:%s 失敗: %s（この区間は未適用）" % (args.part, label, n, error))
    finally:
        api._CTX["offline"] = offline
    if commands:
        args.file = str(api.input_files.save_commands(args.state, text))
        print('操作ファイル: ' + args.file)
    pending["parts"][args.part] = dict(file=str(args.file), commands=[line for _, line, _ in prepared])
    if not args.pause:
        api.remove_ability(draft, pending["stack_oid"])
        pending["status"] = "done"
    api.log(draft, "%s 区間=%s %s" % (p["id"], args.part, pending["status"]))
    st.clear()
    st.update(draft)
    print(output.getvalue(), end="")
    print("%s: 区間 %s を適用 / %s" % (p["id"], args.part, pending["status"]))


def reminder_messages(st, event, player=None, count=1):
    for r in st.get("reminders", []):
        if r["event"] == event and (not r["player"] or r["player"] == player):
            if r["source"] and not relations.live(st, r["source"]):
                continue
            yield "確認 %s: %s / %s %s件 — %s（誘発数は未判定）" % (r["id"], event, player or "共通", count, r["text"])


def notify(st, event, player=None, count=1):
    for message in reminder_messages(st, event, player, count):
        print(message)


def cmd_remind(api, args, st):
    if args.op == "list":
        for r in st.get("reminders", []):
            active = not r["source"] or relations.live(st, r["source"])
            print("%s [%s] %s %s: %s" % (r["id"], "有効" if active else "旧世代", r["event"], r["player"] or "両席", r["text"]))
    elif args.op == "remove":
        r = relations.find(st, "reminders", args.value)
        st["reminders"].remove(r)
        api.log(st, "確認メモ取消: " + r["id"])
        print("取消: " + r["id"])
    else:
        if not args.value or not args.on:
            raise SystemExit("remind add \"確認事項\" --on cast|enter|turn が必要です。")
        source = relations.parse_ref(api, st, args.src) if args.src else None
        identity = "R%d" % st.get("next_reminder", 1)
        st.setdefault("reminders", []).append(dict(id=identity, event=args.on, player=args.player, source=source, text=args.value))
        st["next_reminder"] = st.get("next_reminder", 1) + 1
        api.log(st, "%s 確認メモ: %s" % (identity, args.value))
        print(identity + ": " + args.value)


def add_parser(sub):
    s = sub.add_parser("pending", help="AIが判断した誘発の処理待ちと解決区間を管理")
    s.add_argument("op", choices=["add", "list", "stack", "resolve", "cancel"])
    s.add_argument("value", nargs="?", help="add:説明、他:T番号")
    s.add_argument("--controller", choices=["P1", "P2"], help="add時必須")
    s.add_argument("--src", help="任意の発生源oid/oid@世代。規則由来なら省略")
    s.add_argument("--targets", help="stack時の対象（AIが適正を確認）")
    source = s.add_mutually_exclusive_group()
    source.add_argument("--file", help="resolve:適用する既存コマンドの実ファイル")
    source.add_argument("--do", dest="commands", action="append", metavar="COMMAND",
                        help="resolve:短い操作を直接指定。複数操作は繰り返す。成功時に連番.mtgへ自動保存")
    s.add_argument("--part", help="resolve:再実行を防ぐ区間名、必須")
    s.add_argument("--pause", action="store_true", help="resolve:この区間を保存し、解決途中として続き待ち")
    s.add_argument("--reason", help="cancel:取消し・打ち消し等の理由、必須")
    s.add_argument("--all", action="store_true", help="list:完了・取消済みも表示")
    s = sub.add_parser("remind", help="指定した場面に短い確認事項を表示（自動誘発なし）")
    s.add_argument("op", choices=["add", "list", "remove"])
    s.add_argument("value", nargs="?", help="add:本文、remove:R番号")
    s.add_argument("--on", choices=["cast", "enter", "turn"])
    s.add_argument("--player", choices=["P1", "P2"], help="唱えた席/出た側/ターンの席。省略は両席")
    s.add_argument("--src", help="発生源oid。指定時はその世代が終了すると通知停止")
