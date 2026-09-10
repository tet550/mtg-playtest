#!/usr/bin/env python3
"""MTG テストプレイ用の盤面ステート管理 CLI。

設計方針:
  Python はルールを判定しない。**記帳**だけを担当する。
  ゾーン移動・ライフ・タップ・カウンター・ライブラリー順・ターン進行といった
  「機械的だが取り違えやすく、会話をまたぐと忘れる」情報を保持する。
  何が起きるか（カードテキストの解釈・対象の適正・誘発・レイヤー）は呼び出し側が決める。

カードの持ち方:
  - cards[名前]  … オラクル情報（静的・全コピー共通）。マナコスト、タイプ、P/T、テキスト
  - objects[oid] … 個々のカード実体（動的・固有ID）。同名4枚は別オブジェクトになる
  ゾーンは zones["P1:library"] のような **順序つきリスト**が正本。library は index 0 が一番上。

主なコマンド:
  init / show / hand / draw / move / tap / untap / life / counter / damage / mod
  mana / stack / phase / turn / sba / card / note / log / undo
  詳しくは `python mtg.py <コマンド> -h`。

状態ファイルは既定で ./playtest/state.json（--state で変更可）。
変更のたびにスナップショットを取るので undo で1手戻せる。
"""
import argparse
import datetime
import json
import pathlib
import random
import re
import shlex
import shutil
import sys
import unicodedata
import contextlib
import io
import tempfile

import compact_output
import relations
import bookkeeping
import mana

import cardcache
import decks

PHASES = [
    "beginning.untap", "beginning.upkeep", "beginning.draw",
    "precombat_main",
    "combat.begin", "combat.attackers", "combat.blockers",
    "combat.damage", "combat.end",
    "postcombat_main",
    "ending.end", "ending.cleanup",
]
ZONES = ["library", "hand", "battlefield", "graveyard", "exile"]
NO_PRIORITY = {"beginning.untap", "ending.cleanup"}  # 通常は優先権が発生しない

# ---------------------------------------------------------------- state I/O


def state_path(args):
    return pathlib.Path(args.state)


def load(args):
    p = state_path(args)
    if not p.exists():
        sys.exit("状態ファイルがありません: %s\n先に `mtg.py init` を実行してください。" % p)
    st = json.loads(p.read_text(encoding="utf-8"))
    relations.ensure(st)
    return st


def history_dir(p):
    """undo 用スナップショットの置き場。**状態ファイルごとに分ける。**

    分けないと、同じディレクトリに置いた別の対局（`--state playtest/branch-a.json`
    のような分岐や検証用のコピー）とスナップショットが混ざり、undo が
    まったく別のゲームの盤面を復元してしまう。
    """
    return p.parent / "history" / p.stem


def save(args, st, snapshot=True):
    relations.ensure(st)
    p = state_path(args)
    p.parent.mkdir(parents=True, exist_ok=True)
    if snapshot and p.exists():
        hist = history_dir(p)
        hist.mkdir(parents=True, exist_ok=True)
        n = len(list(hist.glob("*.json")))
        shutil.copy(p, hist / ("%04d.json" % n))
    p.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


PRIVATE_RE = re.compile(r"\[秘匿:(P[12])\]")


def log(st, msg, private=None):
    """ログに1行残す。

    private にプレイヤーを渡すと秘匿マークが付き、`log` の既定表示から外れる。
    対人モードで自分の手札や引いたカードをログに平文で残すと、`log` を見た
    相手に筒抜けになり、そのゲームのテスト結果が使えなくなる。
    """
    if private:
        msg = "[秘匿:%s] %s" % (private, msg)
    st["log"].append("T%s %s %s: %s" % (st["turn"], st["active"], st["phase"], msg))


# ---------------------------------------------------------------- lookup


def obj(st, oid):
    o = st["objects"].get(str(oid))
    if not o:
        sys.exit("oid %s は存在しません。" % oid)
    return o


def zone_of(st, oid):
    for z, lst in st["zones"].items():
        if int(oid) in lst:
            return z
    return None


def resolve_ref(st, ref):
    """oid か 名前@ゾーン で1つのオブジェクトを特定する。曖昧なら候補を出して止まる。"""
    if re.fullmatch(r"\d+", str(ref)):
        return int(ref)
    name, _, zspec = str(ref).partition("@")
    hits = []
    for z, lst in st["zones"].items():
        if zspec and not z.startswith(zspec):
            continue
        # 日本語名で指されても解決できるようにする（識別は英語名のままで、照合だけ広げる）
        hits.extend((oid, z) for oid in find_by_name(st, lst, name)[0])
    if not hits:
        sys.exit("該当なし: %s" % ref)
    if len(hits) > 1:
        cand = ", ".join("%s(%s)" % (o, z) for o, z in hits)
        sys.exit("複数該当します。oid で指定してください: %s" % cand)
    return hits[0][0]


norm_types = cardcache.norm_types
split_type_line = cardcache.split_type_line

# カード情報の解決順: このゲーム限定の上書き（トークン・自作カード）→ ディスクキャッシュ →
# Scryfall（初回のみ）。プロセス内でも memo して同じ名前を何度も取りに行かない。
_CTX = {"dir": cardcache.DEFAULT_DIR, "offline": False, "mem": {},
        "pre_seed": 0, "pre_seq": 0}


def cached_record(name):
    mem = _CTX["mem"]
    if name in mem:
        return mem[name]
    rec, how = cardcache.get(name, _CTX["dir"], offline=_CTX["offline"])
    if how == "network":
        print("[Scryfall] %s を取得しました" % name, file=sys.stderr)
    elif how == "unresolved":
        print("!! %s は未登録のままです。`card set %s ...` で登録してください。"
              % (name, name), file=sys.stderr)
    else:
        cardcache.report(name, rec, how)
    mem[name] = rec
    return rec


def _oracle_view(rec):
    """キャッシュのレコードを、エンジンが使う内部ビューに整える。"""
    return {
        "cost": rec.get("mana_cost", ""),
        "types": rec.get("types", []),
        "supertypes": rec.get("supertypes", []),
        "subtypes": rec.get("subtypes", []),
        "power": rec.get("power"),
        "toughness": rec.get("toughness"),
        "oracle": rec.get("oracle_text_printed") or rec.get("oracle_text_en", "") or "",
        "keywords": rec.get("keywords", []),
        "mana_value": rec.get("mana_value"),
        "jp": rec.get("printed_name"),
        "unresolved": rec.get("unresolved", False),
    }


def _token_view(st, name):
    """トークンの土台。トークン名は実在カードとは限らない（宝物・ツリーフォークなど）。

    キャッシュにあればコピー元のオラクルを土台にし、無ければ Scryfall には問い合わせない。
    """
    view = _oracle_view(cardcache.load(_CTX["dir"], name, quiet=True) or {})
    view["unresolved"] = False
    view.update(st.get("cards", {}).get(name, {}))   # 旧形式（名前キーのトークン定義）との互換
    return view


def card(st, name):
    """**名前だけ**で引くカード情報。

    返す辞書はキャッシュのレコードそのものではなく、内部用のビュー。
    state["cards"][name] に同じキーがあればそれが優先される（`card set --local`）。
    トークン個体の定義はここには乗らない。オブジェクトが手元にあるなら card_of を使う。
    """
    ov = st.get("cards", {}).get(name, {})
    if ov.get("token"):
        return _token_view(st, name)
    view = _oracle_view(cached_record(name))
    view.update(ov)
    return view


def card_of(st, o):
    """オブジェクト1個分のカード情報。**トークンの定義はここでしか効かない。**

    トークンの上書きを名前で共有すると、実在カードのコピー・トークンを作った瞬間に
    戦場や手札にある本物のカードまで書き換わってしまう（クロームドームでピザをコピー
    したら本物のピザまで 0/0 のクリーチャーになる、など）。
    だからトークンの定義は個体キー `#<oid>` に分けて持つ。
    """
    if o.get("kind") == "ability":
        return {"types": ["Ability"]}
    local = st.get("cards", {}).get(o["name"], {})
    if local.get("types") == ["Ability"]:
        # スタックに積んだ能力は実在カードではない。名前で Scryfall を引くと
        # 毎回「見つかりません」を吐き、負のキャッシュまで作ってしまう。
        return dict(local)
    view = _token_view(st, o["name"]) if o.get("token") else card(st, o["name"])
    ov = st.get("cards", {}).get(o.get("card_key") or "", {})
    if ov:
        view = dict(view)
        view.update(ov)
    return view



def pt(st, o):
    """現在の P/T。基本値 + カウンター + 修整。'*' 等で計算できない場合は None。"""
    c = card_of(st, o)
    base, extra, _ = relations.values(sys.modules[__name__], st, o["oid"])
    try:
        p, t = base if base is not None else (int(c.get("power")), int(c.get("toughness")))
    except (TypeError, ValueError):
        return None
    delta = o["counters"].get("+1/+1", 0) - o["counters"].get("-1/-1", 0)
    p += delta
    t += delta
    for m in o["mods"]:
        p += m.get("p", 0)
        t += m.get("t", 0)
    return p + extra[0], t + extra[1]


def new_object(oid, name, owner):
    return {"oid": oid, "incarnation": 1, "name": name, "owner": owner, "controller": owner,
            "tapped": False, "sick": True, "damage": 0, "counters": {},
            "mods": [], "grants": [], "attached_to": None,
            "facedown": False, "note": ""}


# ---------------------------------------------------------------- commands


def cmd_init(args, _):
    # 先手はゲーム2以降で切り替わる（直前のゲームの敗者が選ぶ）ので指定できるようにする。
    # ゲーム1の先手はダイス／コインで決めるが、`coin` は状態ファイルが要るので
    # init より前には使えない。ここで seed から決められるようにして、
    # 「先手抽選だけ seed の外で振る」（＝再現できない）状態をなくす。
    first = args.first
    if first == "random":
        first = random.Random("%s:first" % args.seed).choice(["P1", "P2"])
        print("先手抽選 (seed=%s): %s" % (args.seed, first))
    st = {"turn": 1, "active": first, "phase": "beginning.untap", "priority": first,
          "first": first,
          "players": {}, "objects": {}, "cards": {}, "zones": {"stack": []},
          "log": [], "next_oid": 1, "seed": args.seed, "rng_seq": 0,
          "effects": [], "combat": {"attackers": {}, "blocks": {}}, "passed": []}
    for pid, name, deck, as_name in (("P1", args.p1, args.deck1, args.deck1_name),
                                     ("P2", args.p2, args.deck2, args.deck2_name)):
        st["players"][pid] = {"name": name, "life": args.life, "poison": 0,
                              "pool": {}, "land_drops": 1, "lands_played": 0,
                              "mulligans": 0, "counters": {}}
        for z in ZONES:
            st["zones"]["%s:%s" % (pid, z)] = []
        names = []
        if deck:
            d = decks.resolve_source(deck, args.decks_dir, _CTX["dir"], _CTX["offline"])
            if not d.get("legal"):
                print("!! %s のデッキ「%s」に問題があります:" % (pid, d["name"]))
                for pb in d["problems"]:
                    print("   " + pb)
            # 集計はデッキ単位で読みたい。サイド後のリストは対局フォルダに置く運用なので
            # ファイル名がそのままデッキ名になると、同じ物理デッキがG1とG2で別行になる。
            # --deckN-name で素の構築の登録名に寄せ、実際に読んだ出所は deck_source に残す。
            st["players"][pid]["deck"] = as_name or d["name"]
            st["players"][pid]["deck_source"] = deck
            names = decks.card_names(d)
        random.Random("%s:deck:%s" % (args.seed, pid)).shuffle(names)
        for n in names:
            oid = st["next_oid"]
            st["next_oid"] += 1
            st["objects"][str(oid)] = new_object(oid, n, pid)
            st["zones"]["%s:library" % pid].append(oid)
        if names:
            print("%s (%s): %d枚をシャッフル" % (pid, name, len(names)))
    if getattr(args, "prefetch", False):
        names = sorted({o["name"] for o in st["objects"].values()})
        print("オラクル情報を取得します（%d種類）..." % len(names))
        for n in names:
            rec, how = cardcache.get(n, _CTX["dir"], offline=_CTX["offline"])
            if how in ("network", "notfound", "offline"):
                print("  %s %s" % (pad(n, 28), how))
    names = sorted({o["name"] for o in st["objects"].values()})
    rows = cardcache.glossary(names, _CTX["dir"])
    ja = sum(1 for r in rows if r["ja"])
    print("カード %d種（日本語名あり %d種）。対応表は `glossary` で見られます。"
          % (len(rows), ja))
    log(st, "ゲーム開始 seed=%s 先手=%s%s"
        % (args.seed, first, "（抽選）" if args.first == "random" else ""))
    save(args, st, snapshot=False)
    print("初期化しました → %s  （初手は `draw P1 7` `draw P2 7`）" % state_path(args))


def cmd_draw(args, st):
    pid, n = args.player, args.n
    lib = st["zones"]["%s:library" % pid]
    if len(lib) < n:
        print("!! %s のライブラリーは残り%d枚。空のライブラリーから引こうとした時点で敗北です。"
              % (pid, len(lib)))
    drawn = []
    for _ in range(min(n, len(lib))):
        oid = lib[0]
        relations.move_many(sys.modules[__name__], st, [(oid, "%s:hand" % pid, False)])
        drawn.append("%s(%d)" % (disp_oid(st, oid), oid))
    log(st, "%s が %d枚ドロー: %s" % (pid, n, ", ".join(drawn)),
        private=pid if args.quiet else None)
    if args.quiet:
        print("%s ドロー: %d枚（内容は伏せます）  （残り%d枚）"
              % (pid, len(drawn), len(lib)))
    else:
        print("%s ドロー: %s  （残り%d枚）" % (pid, ", ".join(drawn), len(lib)))


def cmd_move(args, st):
    oid = resolve_ref(st, args.ref)
    o = obj(st, oid)
    if args.zone == "stack" or ":" in args.zone:
        dest = args.zone
    else:
        dest = "%s:%s" % (args.owner or (o["controller"] if args.zone == "battlefield" and zone_of(st, oid) == "stack" else o["owner"]), args.zone)
    if dest not in st["zones"]:
        sys.exit("不明なゾーン: %s" % dest)
    src = zone_of(st, oid)
    if o.get("linked_id") or o.get("pending_id"):
        sys.exit("管理中の能力は linked / pending の解決・取消しで処理してください。")
    relations.move_many(sys.modules[__name__], st, [(oid, dest, args.top and dest.endswith("library"))])
    if dest.endswith("battlefield"):
        if not (src or "").endswith("battlefield"):
            o["sick"] = True
        # タップインを move → tap の2手順でやると、間に誘発の確認が挟まって
        # tap を忘れる。置換効果でタップ状態で出るものは1手で済ませる。
        if getattr(args, "tapped", False):
            o["tapped"] = True
        if args.owner:
            o["controller"] = args.owner
        types = [t.lower() for t in card_of(st, o).get("types", [])]
        # 「土地をプレイする」と「土地を戦場に出す」は別物。消尽・サーチ・
        # 各種の踏み倒しで手札から戦場に出す場合は土地セット権を消費しない。
        # ここを区別しないと、以後ずっと「2枚目です」と誤警告が出続ける。
        if src and src.endswith("hand") and "land" in types and not args.no_drop:
            pl = st["players"][o["controller"]]
            pl["lands_played"] += 1
            if pl["lands_played"] > pl["land_drops"]:
                print("!! 土地セットが%d枚目です（このターンは%d枚まで）。"
                      % (pl["lands_played"], pl["land_drops"]))
    log(st, "%s(%d) %s → %s" % (o["name"], oid, src, dest))
    print("%s(%d): %s → %s" % (o["name"], oid, src, dest))
    if o.get("token") and src and src.endswith("battlefield") and not dest.endswith("battlefield"):
        print("→ トークンなので、この後の状況起因処理で消滅します。")


# マナに用途制限のあるカードを見落とすと、支払えないコストを支払ってしまう。
# 判定はせず、該当する文があることだけ知らせる。
MANA_RESTRICTIONS = (
    "しか支払えない", "しか使えない", "spend this mana only",
)
MANA_ABILITY_RE = re.compile(r"^\s*\{?[^:\n]{0,40}\}?\s*[:：].*(を加える|add )", re.I)


def mana_notes(st, o):
    """タップしたパーマネントのマナ能力と、その用途制限を報告する。

    《レッドシフト》の「このマナは、能力を起動するためにしか支払えない」のような
    制限文は見落としやすく、見落とすと唱えられない呪文を唱えてしまう。
    """
    text = card_of(st, o).get("oracle", "") or ""
    lines = [l.strip() for l in text.splitlines() if MANA_ABILITY_RE.match(l)]
    restricted = [l.strip() for l in text.splitlines()
                  if any(k in l.lower() for k in MANA_RESTRICTIONS)]
    return lines, restricted


def cmd_tap(args, st):
    tapping = args.cmd == "tap"
    for ref in args.refs:
        o = obj(st, resolve_ref(st, ref))
        o["tapped"] = tapping
        log(st, "%s(%d) を%s" % (o["name"], o["oid"], "タップ" if o["tapped"] else "アンタップ"))
        if not tapping:
            continue
        lines, restricted = mana_notes(st, o)
        # 基本土地のマナ能力は毎回書くと雑音になるだけなので出さない（制限は必ず出す）
        basic = "Basic" in card_of(st, o).get("supertypes", [])
        for l in lines:
            if not basic and l not in restricted:
                print("   %s(%d) マナ能力: %s" % (disp_of(st, o), o["oid"], l))
        for l in restricted:
            print("!! %s(%d) のマナには用途制限があります: %s"
                  % (disp_of(st, o), o["oid"], l))
    print("ok")


def cmd_life(args, st):
    p = st["players"][args.player]
    before = p["life"]
    p["life"] = args.value if args.set else before + args.value
    log(st, "%s ライフ %d → %d" % (args.player, before, p["life"]))
    print("%s: %d → %d" % (args.player, before, p["life"]))


def cmd_loop(args, st):
    """呼び出し側が検証・合意した反復の純増分だけを一括記帳する。"""
    if args.count <= 0 or args.life < 0 or args.draw < 0:
        sys.exit("反復回数は正の整数、ライフ・ドローの純増は非負で指定してください。")
    if args.player not in st["players"]:
        sys.exit("不明なプレイヤーです。")
    if not args.proof.strip():
        sys.exit("1周のコスト・収支・反復条件の検証を --proof に記載してください。")
    if st["zones"].get("stack"):
        sys.exit("スタックを解決して反復の開始点に戻ってから実行してください。")
    if any(c not in "WUBRGC1" for c in args.mana):
        sys.exit("マナは WUBRGC1 で指定してください。")
    n = args.count * args.draw
    if n > len(st["zones"][args.player + ":library"]):
        sys.exit("ドローがライブラリー残数を超えます。反復回数を減らしてください。")
    if not (args.life or args.draw or args.mana):
        sys.exit("少なくとも1つの純増を指定してください。")
    p = st["players"][args.player]
    p["life"] += args.count * args.life
    for symbol in args.mana:
        p["pool"][symbol] = p["pool"].get(symbol, 0) + args.count
    log(st, "%s ループ %d回（1周: life=+%d mana=%s draw=%d）。検証・合意: %s"
        % (args.player, args.count, args.life, args.mana, args.draw, args.proof))
    if n:
        cmd_draw(argparse.Namespace(player=args.player, n=n, quiet=True), st)
    print("%s: ループ%d回を反映。life=%d pool=%s"
          % (args.player, args.count, p["life"], p["pool"]))


def cmd_counter(args, st):
    o = obj(st, resolve_ref(st, args.ref))
    kind = args.kind.lstrip("~")
    before = o["counters"].get(kind, 0)
    # 既定は加算。盤面を語るときは「いま何個あるか」で話すので、そのまま
    # 総数を書いて加算になり、静かに1個多い盤面で進んでしまう事故が起きる。
    # --set は「合計を N にする」。
    o["counters"][kind] = args.n if args.set else before + args.n
    delta = o["counters"][kind] - before
    if o["counters"][kind] <= 0:
        o["counters"].pop(kind)
    log(st, "%s(%d) %s カウンター %+d（合計 %d）"
        % (o["name"], o["oid"], kind, delta, o["counters"].get(kind, 0)))
    print("%s(%d) counters=%s" % (o["name"], o["oid"], o["counters"]))
    if delta and o.get("attached_to") and any(f["source"] == relations.ref(st, o["oid"]) and f.get("counter") == kind for f in st.get("fx", [])):
        target = o["attached_to"]
        print("→ fxで装着先[%s]を再計算: %s" % (target, pt(st, obj(st, target))))
    elif delta and o.get("attached_to"):
        host = st["objects"].get(str(o["attached_to"]))
        print("!! 装備先%s: カウンターでP/Tが変わるなら mod %s <+N/+M> --until attached --src %d --set（基本修整込みの合計）。"
              % ("%s(%d)" % (host["name"], host["oid"]) if host else "不明",
                 o["attached_to"], o["oid"]))


def cmd_damage(args, st):
    o = obj(st, resolve_ref(st, args.ref))
    o["damage"] += args.n
    log(st, "%s(%d) に%d点" % (o["name"], o["oid"], args.n))
    print("%s(%d) damage=%d" % (o["name"], o["oid"], o["damage"]))


def cmd_mod(args, st):
    o = obj(st, resolve_ref(st, args.ref))
    spec = args.pt.lstrip("~")
    m = re.fullmatch(r"([+-]\d+)/([+-]\d+)", spec)
    if not m:
        sys.exit("修整は +2/+2 の形式で指定してください。")
    if args.until == "attached" and not args.src:
        sys.exit("--until attached には、修整の出どころ（オーラ・装備）の oid を "
                 "--src で指定してください。外れたときに一緒に消すためです。")
    if args.until == "attached":
        source = resolve_ref(st, args.src)
        obj(st, source)
        relations.attached_check(sys.modules[__name__], st, source, o["oid"])
        args.src = str(source)
    if getattr(args, "set", False):
        if not args.src:
            sys.exit("mod --setには--srcが必要（同じsrc・期限の合計を置換）。")
        o["mods"] = [x for x in o["mods"] if not (str(x.get("src", "")) == str(args.src) and x.get("until") == args.until)]
    o["mods"].append({"p": int(m.group(1)), "t": int(m.group(2)),
                      "until": args.until, "src": args.src or ""})
    if getattr(args, "set", False):
        log(st, "%s(%d) のsrc=%s・%sの修整合計を%sに更新" % (o["name"], o["oid"], args.src, args.until, spec))
    else:
        log(st, "%s(%d) に %s (%s)" % (o["name"], o["oid"], spec, args.until))
    note = {"eot": "ターン終了時に消えます", "permanent": "消えません",
            "attached": "外れると消えます"}[args.until]
    print("%s(%d) → %s  [%s: %s]" % (o["name"], o["oid"], pt(st, o), args.until, note))


def stack_str(st):
    s = st["zones"]["stack"]
    if not s:
        return "（空）"
    items = " | ".join("[%d]%s" % (o, disp_oid(st, o)) for o in reversed(s))
    return items + "  ←上から"


def push_ability(st, text, controller, targets=None, source=None, ability_key=None):
    oid = st["next_oid"]
    st["next_oid"] += 1
    o = new_object(oid, text, controller)
    o.update(kind="ability", note="能力 対象=%s" % (targets or ""))
    if source:
        o.update(source_ref=source, ability_key=ability_key or text)
        o["note"] += " src=" + relations.label(source)
    st["objects"][str(oid)] = o
    st["zones"]["stack"].append(oid)
    st["passed"] = []
    return oid


def remove_ability(st, oid):
    o = obj(st, oid)
    if card_of(st, o).get("types") != ["Ability"] or zone_of(st, oid) != "stack":
        sys.exit("スタック上の能力ではありません。")
    st["zones"]["stack"].remove(oid)
    st["objects"].pop(str(oid))
    # Old saves shared definitions by display name. Retain them until the last
    # user disappears; new abilities carry their kind on the individual object.
    if (st.get("cards", {}).get(o["name"], {}).get("types") == ["Ability"]
            and not any(x["name"] == o["name"] for x in st["objects"].values())):
        st["cards"].pop(o["name"], None)
    st["passed"] = []


def push_spell(st, oid, controller, targets=None, cast=False):
    """Shared stack entry; only explicit casting records a cast."""
    bookkeeping.require_ready(st)
    if zone_of(st, oid) == "stack" or card_of(st, obj(st, oid)).get("types") == ["Ability"]:
        sys.exit("既にスタック上、または能力です。重複して登録できません。")
    if cast and "land" in [t.lower() for t in card_of(st, obj(st, oid)).get("types", [])]:
        sys.exit("土地は唱えません。")
    relations.move_many(sys.modules[__name__], st, [(oid, "stack", False)])
    obj(st, oid)["controller"] = controller
    obj(st, oid)["note"] = "対象=%s" % (targets or "")
    st["passed"] = []
    if cast:
        counts = st.setdefault("cast_counts", {}).setdefault(str(st["turn"]), {})
        counts[controller] = counts.get(controller, 0) + 1
        bookkeeping.notify(st, "cast", controller)


def cmd_stack(args, st):
    if args.op == "push":
        bookkeeping.require_ready(st)
        if args.ability:
            if getattr(args, "cast", False):
                sys.exit("能力は唱えません。--castは呪文専用です。")
            source_ref = relations.parse_ref(sys.modules[__name__], st, args.src) if getattr(args, "src", None) else None
            oid = push_ability(st, args.what, args.controller or st["active"], args.targets,
                               source_ref, getattr(args, "ability_key", None))
            log(st, "スタックへ(能力): %s(%d)" % (args.what, oid))
        else:
            oid = resolve_ref(st, args.what)
            push_spell(st, oid, args.controller or st["active"], args.targets, getattr(args, "cast", False))
            log(st, "スタックへ: %s(%d) 対象=%s" % (obj(st, oid)["name"], oid, args.targets))
        print("stack: %s" % stack_str(st))
    elif args.op == "pop":
        # 解決し終えた能力はどのゾーンにも行かず消滅する（CR 112.7）。
        # move で追放などに逃がすと、実在しないカード名として Scryfall に
        # 問い合わせに行き、以後ずっと警告を出し続ける。
        if args.what:
            oid = resolve_ref(st, args.what)
        else:
            if not st["zones"]["stack"]:
                sys.exit("スタックは空です。カードをmove済みならpop不要。")
            oid = st["zones"]["stack"][-1]
        ob = obj(st, oid)
        if ob.get("linked_id") or ob.get("pending_id"):
            sys.exit("管理中の能力は linked / pending の解決・取消しで処理してください。")
        if card_of(st, ob).get("types") != ["Ability"]:
            sys.exit("%s(%d) は能力ではありません。カードは `move %d <ゾーン>` で"
                     "行き先を明示してください。" % (ob["name"], oid, oid))
        remove_ability(st, oid)
        log(st, "能力が消滅: %s(%d)" % (ob["name"], oid))
        print("能力 %s(%d) は消滅しました。" % (ob["name"], oid))
        print("stack: %s" % stack_str(st))
    elif args.op == "resolve":
        bookkeeping.require_ready(st)
        if not st["zones"]["stack"]:
            sys.exit("スタックは空です。")
        oid = st["zones"]["stack"][-1]
        o = obj(st, oid)
        if o.get("pending_id"):
            print("応答確認後 pending resolve %s --file <file> --part <区間名>" % o["pending_id"])
            return
        if o.get("linked_id"):
            print("応答確認後 linked resolve %s（打ち消しは linked counter %s）" % (o["linked_id"], o["linked_id"]))
            return
        log(st, "解決: %s(%d)" % (o["name"], oid))
        print("解決: %s(%d) %s" % (o["name"], oid, o["note"]))
        if o.get("note", "").startswith("能力"):
            print("→ 効果を適用したら `stack pop` で取り除いてください。"
                  "能力は墓地へ行かず、消滅します。")
            return
        print("→ 効果を適用したら move で行き先を指定してください"
              "（呪文・能力は通常 %s:graveyard、パーマネント呪文は battlefield）。" % o["owner"])
    else:
        print("stack: %s" % stack_str(st))


def advance_to(st, target, on_draw=None):
    """目的のステップまで1つずつ進める。

    `phase set` の一発ジャンプと違い、途中のステップを実際に通るので
    マナ・プールの空化や戦闘記録のクリアが飛ばされない。通った道筋を返す。
    """
    path = []
    for _ in range(len(PHASES) * 2):
        if st["phase"] == target:
            return path
        i = PHASES.index(st["phase"])
        if i == len(PHASES) - 1:
            sys.exit("ターンの最後まで来ました。%s には到達できません（turn next が必要）。"
                     % target)
        st["phase"] = PHASES[i + 1]
        for p in st["players"].values():
            mana.clear(p)
        st["priority"] = st["active"]
        st["passed"] = []
        if not st["phase"].startswith("combat"):
            st["combat"] = {"attackers": {}, "blocks": {}}
        path.append(st["phase"])
        relations.on_step(sys.modules[__name__], st)
        if any(x["status"] == "pending" for x in st.get("links", [])):
            return path
        if st["phase"] == "beginning.draw" and on_draw:
            on_draw()
        if st["phase"] == target:
            return path
    sys.exit("フェイズ名が不正です: %s" % target)


def cmd_phase(args, st):
    if st["zones"]["stack"]:
        sys.exit("スタックが空ではありません。効果適用後、カードはmove／能力はstack pop。")
    relations.require_resolved(sys.modules[__name__], st)
    if args.op == "to":
        if args.value not in PHASES:
            sys.exit("フェイズ名: " + ", ".join(PHASES))
        path = advance_to(st, args.value)
        if path:
            log(st, "%s まで進行（経由: %s）" % (st["phase"], " → ".join(path)))
            print("経由: " + " → ".join(path))
        if st["turn"] > 1 and "beginning.draw" in path:
            print("!! phaseはドローしません。未処理なら draw %s 1（置換・誘発を確認）。" % st["active"])
        tag = "（優先権なし）" if st["phase"] in NO_PRIORITY else ""
        print("T%s %s / %s %s" % (st["turn"], st["active"], st["phase"], tag))
        return
    if args.op == "set":
        if args.value not in PHASES:
            sys.exit("フェイズ名: " + ", ".join(PHASES))
        relations.step_guard(sys.modules[__name__], st, args.value)
        st["phase"] = args.value
    else:
        i = PHASES.index(st["phase"])
        if i == len(PHASES) - 1:
            return cmd_turn(args, st)
        st["phase"] = PHASES[i + 1]
    relations.on_step(sys.modules[__name__], st)
    for p in st["players"].values():
        mana.clear(p)   # マナ・プールはステップ／フェイズの終わりに空になる
    st["priority"] = st["active"]
    st["passed"] = []
    if not st["phase"].startswith("combat"):
        st["combat"] = {"attackers": {}, "blocks": {}}
    tag = "（優先権なし）" if st["phase"] in NO_PRIORITY else ""
    if st["turn"] > 1 and (st["phase"] == "beginning.draw" or args.op == "set"):
        print("!! phaseはドローしません。未処理なら draw %s 1（二重ドロー注意）。" % st["active"])
    print("T%s %s / %s %s" % (st["turn"], st["active"], st["phase"], tag))


def cmd_turn(args, st):
    if getattr(args, "draw", False) and (not getattr(args, "to", None) or args.to not in PHASES or PHASES.index(args.to) < PHASES.index("beginning.draw")):
        sys.exit("--drawにはドロー以降の--toが必要: turn next --to precombat_main --draw（未実行）。")
    if st["zones"]["stack"]:
        sys.exit("スタックが空ではありません。効果適用後、カードはmove／能力はstack pop。")
    relations.require_resolved(sys.modules[__name__], st)
    if any(x["return"] == "next-end" and x["status"] == "waiting" and x.get("due_turn", 0) <= st["turn"] for x in st.get("links", [])):
        sys.exit("帰還の遅延誘発があります。phase to ending.endで終了ステップを処理してください。")
    relations.cleanup(st)
    st["combat"] = {"attackers": {}, "blocks": {}}
    st["passed"] = []
    st["turn"] += 1
    st["active"] = "P2" if st["active"] == "P1" else "P1"
    st["phase"] = "beginning.untap"
    ap = st["active"]
    st["priority"] = ap
    st["players"][ap]["lands_played"] = 0
    for oid in st["zones"]["%s:battlefield" % ap]:
        o = st["objects"][str(oid)]
        o["tapped"] = False   # アンタップ・ステップ
        o["sick"] = False     # 召喚酔いが解ける
    for p in st["players"].values():
        mana.clear(p)
    log(st, "ターン開始")
    print("=== T%s %s のターン（アンタップ済み） ===" % (st["turn"], ap))
    bookkeeping.notify(st, "turn", ap)
    target = getattr(args, "to", None)
    if not target:
        print("→ アンタップ済み。phaseで進行→未処理のドロー。turn nextの再実行は次ターン。")
        return

    def do_draw():
        # ドロー・ステップを通るときに引く。先手の第1ターンは引かないので --draw で選ばせる。
        args.player, args.n, args.quiet = ap, 1, getattr(args, "quiet", False)
        cmd_draw(args, st)

    path = advance_to(st, target, on_draw=do_draw if args.draw else None)
    if path:
        print("経由: " + " → ".join(path))
    if (not args.draw) and st["turn"] > 1 and "beginning.draw" in path:
        print("!! 通常ドロー未実行。未処理なら draw %s 1（置換・誘発を確認）。" % ap)
    tag = "（優先権なし）" if st["phase"] in NO_PRIORITY else ""
    print("T%s %s / %s %s" % (st["turn"], st["active"], st["phase"], tag))


def cmd_sba(args, st):
    """機械的に判定できる状況起因処理だけを報告する。適用は呼び出し側が判断する。"""
    found = []
    applying = getattr(args, "apply", False) or getattr(args, "apply_deaths", False)
    if applying and any(f.get("manual") for f in st.get("fx", [])):
        sys.exit("要裁定のfxがあります。fx listで確認してからSBAを適用してください。")
    orphaned = []
    for pid, p in st["players"].items():
        if p["life"] <= 0:
            found.append("%s のライフが%d → 敗北" % (pid, p["life"]))
        if p["poison"] >= 10:
            found.append("%s の毒カウンター%d → 敗北" % (pid, p["poison"]))
    legends = {}
    for oid in st["zones"]["P1:battlefield"] + st["zones"]["P2:battlefield"]:
        o = st["objects"][str(oid)]
        c = card_of(st, o)
        types = [t.lower() for t in c.get("types", [])]
        supers = [t.lower() for t in c.get("supertypes", [])]
        if "aura" in [x.lower() for x in c.get("subtypes", [])] and not o.get("attached_to"):
            found.append("%s(%d): 装着先のないオーラ → 墓地へ（授与等の例外は要裁定）" % (o["name"], oid))
            if not o.get("bestowed"):
                orphaned.append(oid)
        v = pt(st, o)
        if "creature" in types:
            if v is None:
                found.append("%s(%d): P/T未登録または'*' → 手動で確認" % (o["name"], oid))
            elif v[1] <= 0:
                found.append("%s(%d): タフネス%d → 墓地へ" % (o["name"], oid, v[1]))
            elif o["damage"] >= v[1]:
                found.append("%s(%d): %d点/タフネス%d → 破壊"
                             % (o["name"], oid, o["damage"], v[1]))
        if "planeswalker" in types and o["counters"].get("loyalty", 1) <= 0:
            found.append("%s(%d): 忠誠度0 → 墓地へ" % (o["name"], oid))
        if "legendary" in supers:
            key = (o["controller"], o["name"])
            legends[key] = legends.get(key, 0) + 1
    gone = []
    for oid_s, ob in list(st["objects"].items()):
        z = zone_of(st, int(oid_s))
        if ob.get("token") and z and not z.endswith("battlefield") and z != "stack":
            found.append("%s(%s): トークンが%sにある → 消滅" % (ob["name"], oid_s, z))
            gone.append((int(oid_s), z, ob))
    dead = []
    if getattr(args, "apply_deaths", False):
        for oid in list(st["zones"]["P1:battlefield"] + st["zones"]["P2:battlefield"]):
            o = st["objects"][str(oid)]
            c = card_of(st, o)
            if "creature" not in [t.lower() for t in c.get("types", [])]:
                continue
            v = pt(st, o)
            if v is None:
                continue
            if v[1] > 0 and o["damage"] < v[1]:
                continue
            if has_kw(st, oid, "破壊不能", "indestructible"):
                # 破壊不能はタフネス0では死ぬが致死ダメージでは死なない。
                # 機械的に切り分けられないので、ここでは触らず必ず知らせる。
                found.append("%s(%d): 破壊不能を持つので --apply-deaths では動かしません。"
                             "手で判断してください。" % (o["name"], oid))
                continue
            dead.append(oid)
        if dead or orphaned:
            moving = list(dict.fromkeys(dead + orphaned))
            relations.move_many(sys.modules[__name__], st, [(oid, "%s:graveyard" % obj(st, oid)["owner"], False) for oid in moving])
            for oid in moving:
                log(st, "SBAで墓地へ: [%s]" % oid)
                found.append("→ [%s] を墓地へ移しました（--apply-deaths）。" % oid)
        if dead:
            # 死んだブロッカーがいなくなったので、トークンの消滅判定をやり直す
            for oid_s, ob in list(st["objects"].items()):
                z = zone_of(st, int(oid_s))
                if (ob.get("token") and z and not z.endswith("battlefield")
                        and z != "stack" and (int(oid_s), z, ob) not in gone):
                    gone.append((int(oid_s), z, ob))
    if getattr(args, "apply", False) and not getattr(args, "apply_deaths", False) and orphaned:
        relations.move_many(sys.modules[__name__], st, [(oid, "%s:graveyard" % obj(st, oid)["owner"], False) for oid in orphaned])
        for oid in orphaned:
            log(st, "装着先のないオーラを墓地へ: [%s]" % oid)
    if gone and (getattr(args, "apply", False) or getattr(args, "apply_deaths", False)):
        # 消滅したトークンは墓地や追放に残らない（CR 111.7）。残しておくと
        # 「墓地のクリーチャー・カード」を数える効果がずれる。
        for oid_g, z, ob in gone:
            st["zones"][z].remove(oid_g)
            st["cards"].pop(ob.get("card_key") or "", None)
            st["objects"].pop(str(oid_g), None)
            log(st, "トークンが消滅: %s(%d)" % (ob["name"], oid_g))
        found.append("→ 上のトークン %d個を盤面から取り除きました（--apply）。" % len(gone))
    for (ctrl, name), n in legends.items():
        if n > 1:
            found.append("%s が伝説の《%s》を%d つ → 1つ残して墓地へ" % (ctrl, name, n))
    if found:
        print("\n".join("- " + f for f in found))
        if not (getattr(args, "apply", False) or getattr(args, "apply_deaths", False)):
            print("確認のみ：状態は変更していません。トークン消滅は `sba --apply`、"
                  "致死ダメージ等の適用は `sba --apply-deaths`。その他は手動で処理してください。")
    else:
        print("機械的に判定できる範囲では該当なし（誘発や継続的効果は別途確認）。")
    if applying and (dead or orphaned or gone):
        cmd_sba(args, st)  # stable point after simultaneous deaths / lost aura effects


def _set_overrides(ov, args):
    """`card set` の上書き項目を1つの辞書に適用する（--local と --oid で共通）。"""
    if args.types:
        ov["types"] = norm_types(args.types.split("/"))
    if args.supertypes:
        ov["supertypes"] = norm_types(args.supertypes.split("/"))
    if args.subtypes:
        ov["subtypes"] = norm_types(args.subtypes.split("/"))
    for k, v in (("cost", args.cost), ("power", args.power),
                 ("toughness", args.toughness), ("oracle", args.oracle)):
        if v is not None:
            ov[k] = v
    return ov


def cmd_card(args, st):
    if not args.name and not (args.op == "set" and args.oid):
        sys.exit("カード名を指定してください（`card set --oid` のときだけ省略できます）。")
    if args.op == "fetch":
        rec, how = cardcache.get(args.name, _CTX["dir"], refresh=args.refresh,
                                 offline=_CTX["offline"])
        _CTX["mem"].pop(args.name, None)
        print("[%s] %s" % (how, cardcache.summary(rec)))
        cardcache.report(args.name, rec, how)
        print("  → %s" % cardcache.path_for(_CTX["dir"], args.name))
        return
    if args.op == "show":
        rec, how = cardcache.get(args.name, _CTX["dir"], offline=True)
        print("[%s] %s" % (how, cardcache.summary(rec)))
        ov = (st or {}).get("cards", {}).get(args.name)
        if ov:
            print("  このゲーム限定の上書き: %s" % json.dumps(ov, ensure_ascii=False))
        return
    # op == "set"
    fields = {
        "types": args.types.split("/") if args.types else None,
        "supertypes": args.supertypes.split("/") if args.supertypes else None,
        "subtypes": args.subtypes.split("/") if args.subtypes else None,
        "mana_cost": args.cost, "power": args.power, "toughness": args.toughness,
        "loyalty": args.loyalty, "oracle_text_printed": args.oracle,
        "mana_value": args.mana_value,
    }
    if args.oid:
        # 個体1つだけを書き換える。「対象のアーティファクトが 0/0 のロボットになる」
        # 「土地が 0/0 のクリーチャーになる（土の技）」のように、
        # 同名の他のカードには影響しない効果はこちらを使う。
        oid = resolve_ref(st, args.oid)
        o = obj(st, oid)
        key = "#%d" % oid
        o["card_key"] = key
        ov = _set_overrides(st.setdefault("cards", {}).setdefault(key, {}), args)
        print("個体 %s(%d) だけを上書きしました: %s"
              % (o["name"], oid, json.dumps(ov, ensure_ascii=False)))
        return
    if args.local:
        ov = _set_overrides(st.setdefault("cards", {}).setdefault(args.name, {}), args)
        _CTX["mem"].pop(args.name, None)
        print("このゲーム限定で上書きしました: %s %s  （同名すべてに効きます）"
              % (args.name, json.dumps(ov, ensure_ascii=False)))
        return
    rec = cardcache.put_manual(args.name, _CTX["dir"], **fields)
    _CTX["mem"].pop(args.name, None)
    print("キャッシュに保存しました: %s" % cardcache.path_for(_CTX["dir"], args.name))
    print("  " + cardcache.summary(rec))


def cmd_deck(args, st):
    """デッキの登録・確認。登録時に全カードを解決して検証するのが要点。"""
    d = args.decks_dir
    if args.op == "add":
        path = pathlib.Path(args.what)
        deck = decks.build(args.name or path.stem, path.read_text(encoding="utf-8"),
                           _CTX["dir"], source_file=path, fmt=args.format,
                           description=args.description or "", offline=_CTX["offline"])
        out = decks.save(d, deck)
        # 対人モードでは、自分側のデッキ登録がそのまま相手に見えてしまう。
        # --quiet では枚数と適否だけ出し、中身は伏せる。
        if args.quiet:
            print("%s: メイン %d / サイド %d / %s"
                  % (deck["name"], deck["main_total"], deck.get("sideboard_total", 0),
                     "適正" if deck.get("legal") else "要修正"))
            for pb in deck.get("problems", []) + deck.get("warnings", []):
                print("  " + pb)
        else:
            print(decks.render(deck, verbose=True))
        print("→ %s" % out)
    elif args.op == "list":
        print(chr(10).join(decks.render_listing(decks.listing(d))))
    elif args.op == "show":
        deck = decks.load(d, args.what)
        if not deck:
            sys.exit("見つかりません: %s" % args.what)
        if getattr(args, "brief", False):
            print("%s: メイン%d / サイド%d（登録情報）" %
                  (deck["name"], deck["main_total"], deck.get("sideboard_total", 0)))
            for entry in deck["main"]:
                print("%d %s" % (entry["count"], entry["name"]))
            for problem in deck.get("problems", []) + deck.get("warnings", []):
                print("!! " + problem)
        else:
            print(decks.render(deck, verbose=True))
    elif args.op == "verify":
        names = [args.what] if args.what else [x.get("name") for x in decks.listing(d)]
        for n in names:
            deck = decks.load(d, n, quiet=True)
            if not deck:
                deck = decks.rebuild(d, n, _CTX["dir"], _CTX["offline"])
            if not deck:
                continue
            decks.resolve_entries(deck["main"], _CTX["dir"], _CTX["offline"])
            decks.resolve_entries(deck["sideboard"], _CTX["dir"], _CTX["offline"])
            deck["problems"] = decks.validate(deck)
            deck["legal"] = not deck["problems"]
            decks.save(d, deck)
            print(decks.render(deck))
    elif args.op == "rm":
        path = decks.path_for(d, args.what)
        if not path.exists():
            sys.exit("見つかりません: %s" % args.what)
        path.unlink()
        print("削除しました: %s" % path)


def cmd_glossary(args, st):
    """日英対応表。内部は英語名で動かすので、日本語で話すときはこれを見る。"""
    if args.deck:
        deck = decks.load(args.decks_dir, args.deck)
        if not deck:
            sys.exit("デッキが見つかりません: %s" % args.deck)
        names = [e["name"] for e in deck["main"] + deck.get("sideboard", [])]
    elif st:
        names = sorted({o["name"] for o in st["objects"].values()})
    else:
        sys.exit("対局中ではありません。--deck <デッキ名> を指定してください。")
    rows = cardcache.glossary(names, _CTX["dir"])
    text = cardcache.render_glossary(rows)
    print(text)
    if args.out:
        pathlib.Path(args.out).write_text(text + "\n", encoding="utf-8")
        print("→ %s" % args.out)


def cmd_hand(args, st):
    print("\n".join(render_hand(st, args.player, unsorted=args.unsorted)))


def cmd_note(args, st):
    text = " ".join(args.text) if isinstance(args.text, list) else args.text
    log(st, text, private=args.private)
    if args.private:
        print("記録しました（%s の秘匿メモ。`log --player %s` でのみ見えます）。"
              % (args.private, args.private))
    else:
        print("記録しました。")


def cmd_log(args, st):
    lines = st["log"]
    if not args.all:
        lines = [l for l in lines
                 if not (PRIVATE_RE.search(l)
                         and PRIVATE_RE.search(l).group(1) != args.player)]
    print("\n".join(lines[-args.n:]))


def cmd_undo(args, _):
    hist = history_dir(state_path(args))
    files = sorted(hist.glob("*.json")) if hist.exists() else []
    if not files:
        sys.exit("戻れる履歴がありません。")
    count = getattr(args, "undo_count", 1)
    if not 1 <= count <= len(files):
        sys.exit("undo は1〜%d保存分を指定してください（状態変更なし）。" % len(files))
    # Read and validate the target before changing either state or history.
    target = files[-count]
    restored = target.read_text(encoding="utf-8")
    json.loads(restored)
    state_path(args).write_text(restored, encoding="utf-8")
    for snapshot in files[-count:]:
        snapshot.unlink()
    print("%d手戻しました（残り履歴 %d）。" % (count, len(files) - count))


# 表示幅の計算は cardcache に1本化する。ここに別実装を置くと、
# deck show とスタックの桁揃えが少しずつ食い違う。
width = cardcache.width
pad = cardcache.pad


def disp(st, name):
    """表示用のカード名。日本語印刷名があれば優先する（英語名だけでは通じないため）。

    内部の識別は常に英語名のままで、ここは表示だけを日本語に寄せる。
    `--en` を付けると英語名で通す。
    """
    if _CTX.get("english"):
        return name
    return card(st, name).get("jp") or name


def disp_of(st, o):
    """オブジェクトの表示名。

    トークン名は実在カードとは限らないので、名前から引き直すと Scryfall に
    問い合わせに行って候補の羅列を吐いてしまう。オブジェクト経由で解決する。
    """
    if _CTX.get("english"):
        return o["name"]
    return card_of(st, o).get("jp") or o["name"]


def disp_oid(st, oid):
    """oid から表示名を作る。draw/mill/look/search もこれを通すこと。

    ここが英語名のままだと、盤面表示（日本語）と食い違う。読み手が英語名を
    自分で訳して盤面と突き合わせることになり、同じデッキに《始まりの町》と
    《バーシンセー》のような別カードが同居していると取り違える。
    """
    obj = st["objects"].get(str(oid))
    if obj is None:
        # 消滅したトークンなど、既に存在しないオブジェクトを戦闘記録などが
        # 指したまま残ることがある。表示のために落とさない。
        return "(消滅したオブジェクト #%s)" % oid
    return disp_of(st, obj)


def types_ja(types):
    return "/".join(cardcache.TYPES_JA.get(t, t) for t in types) or "?"


def hand_key(st, oid):
    """手札の並び順：土地 → マナ総量 → マナ・コスト → 名前。

    まず「土地を置くか」を決め、次に軽い順に唱えられるものを見ていく、という
    実際の手順に並びを合わせる。マナ総量が取れないカードは末尾に寄せる。
    """
    o = st["objects"][str(oid)]
    c = card_of(st, o)
    is_land = "Land" in c.get("types", [])
    mv = c.get("mana_value")
    return (0 if is_land else 1, 999 if mv is None else mv,
            c.get("cost", ""), o["name"])


def render_hand(st, pid, unsorted=False, indent="  "):
    """手札を桁の揃った表にして行のリストで返す。"""
    oids = st["zones"]["%s:hand" % pid]
    order = oids if unsorted else sorted(oids, key=lambda o: hand_key(st, o))
    rows = []
    for oid in order:
        o = st["objects"][str(oid)]
        c = card_of(st, o)
        types = c.get("types", [])
        mv = c.get("mana_value")
        kind = types_ja(types)
        if c.get("power") is not None:
            kind += " %s/%s" % (c["power"], c["toughness"])
        # 日本語を含むのは最後の1列だけにする。カード名とタイプの間に
        # 桁揃えを挟むと、フォント次第でそこから右が全部ズレる。
        rows.append(["-" if "Land" in types else ("?" if mv is None else str(mv)),
                     c.get("cost", "") or "-",
                     "[%d]%s ／ %s" % (oid, disp_of(st, o), kind)])
    head = "%s %s の手札 %d枚%s" % (pid, st["players"][pid]["name"], len(oids),
                                    "" if unsorted else "（土地 → マナ総量 順）")
    if not rows:
        return [head, indent + "（なし）"]
    # 見出しを必ず付ける。付けないと MV の列が枚数に見える。
    # oid は盤面表示と同じく [oid]カード名 の形にして、列としては持たない。
    rows.insert(0, ["MV", "コスト", "カード名 ／ タイプ"])
    w = [max(width(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = [head]
    for r in rows:
        lines.append(indent + "  ".join(pad(r[i], w[i]) for i in range(len(r))).rstrip())
    return lines


def perm_bits(st, oid):
    """oid を除いた表示部分。同一かどうかの判定にも使うので、
    見えている情報（P/T・ダメージ・カウンター・タップ・酔い）を全部含める。"""
    o = st["objects"][str(oid)]
    bits = [disp_of(st, o)]
    v = pt(st, o)
    if v:
        bits.append("%d/%d" % v)
    if o["damage"]:
        bits.append("dmg%d" % o["damage"])
    if o["counters"]:
        bits.append("+".join("%s x%d" % (k, n) for k, n in o["counters"].items()))
    if o["tapped"]:
        bits.append("(T)")
    if o["sick"] and "creature" in [t.lower() for t in card_of(st, o).get("types", [])]:
        bits.append("(酔)")
    if o.get("token"):
        bits.append("(token)")
    if granted(st, oid):
        bits.append("+" + "・".join(granted(st, oid)))
    if o.get("attached_to"):
        bits.append("→[%s]" % o["attached_to"])
    return " ".join(bits)


def combat_role(st, oid):
    """まとめ表示のキー用。攻撃中・ブロック中のものを他と混ぜない。"""
    cb = st.get("combat") or {}
    target = (cb.get("attackers") or {}).get(str(oid))
    if target:
        return "→%s" % target
    atk = (cb.get("blocks") or {}).get(str(oid))
    if atk is not None:
        return "block[%s]" % atk
    return ""


def fmt_perm(st, oid):
    # oid は必ず名前の前。名前の後ろに付けると、直前の P/T やカウンターの
    # 数字と地続きに読めてしまう（"兵士 2/2 +1/+1 x1[41]"）。
    return "[%d]%s" % (oid, perm_bits(st, oid))


def oid_ranges(oids):
    """[127,128,129,131] → "127-129,131"。まとめても個体を指せるようにする。"""
    oids = sorted(oids)
    out, i = [], 0
    while i < len(oids):
        j = i
        while j + 1 < len(oids) and oids[j + 1] == oids[j] + 1:
            j += 1
        out.append(str(oids[i]) if i == j else "%d-%d" % (oids[i], oids[j]))
        i = j + 1
    return ",".join(out)


def fmt_perm_group(st, oids):
    """同じ見た目のパーマネントを1行にまとめる。トークンが十数個並ぶと読めない。"""
    if len(oids) == 1:
        return fmt_perm(st, oids[0])
    return "[%s]%s x%d" % (oid_ranges(oids), perm_bits(st, oids[0]), len(oids))


# 戦場の並び：土地 → クリーチャー → アーティファクト → エンチャント →
# プレインズウォーカー → バトル → その他。手札の並び（土地が先）と揃えてある。
# 判定の優先はこれと別で、アーティファクト・クリーチャーはクリーチャー側に寄せる。
_BF_MATCH = ["Land", "Creature", "Planeswalker", "Battle", "Artifact", "Enchantment"]
_BF_ORDER = ["Land", "Creature", "Artifact", "Enchantment", "Planeswalker", "Battle",
             "Other"]


def bf_group(st, oid):
    types = card_of(st, st["objects"][str(oid)]).get("types", [])
    for t in _BF_MATCH:
        if t in types:
            return t
    return "Other"


def render_battlefield(st, pid, indent="   ", flat=False):
    """戦場をカードタイプごとに、1行1パーマネントで返す。

    タイプ名は**見出し行**にする。日本語のタイプ名を空白で桁揃えすると、
    「全角＝ASCII2文字分」を前提にした桁が表示側のフォント次第でズレる。
    揃えたい列に日本語を置かないのが唯一確実な直し方。

    同じ見た目のものは既定でまとめる（トークンが十数個並ぶと盤面が読めない）。
    oid は範囲表記で残すので、個体を指すことはできる。
    """
    bf = st["zones"]["%s:battlefield" % pid]
    if not bf:
        return [indent + "戦場: なし"]
    groups = {}
    for oid in bf:
        groups.setdefault(bf_group(st, oid), []).append(oid)
    keys = [k for k in _BF_ORDER if k in groups]
    lines = [indent + "戦場:"]
    for k in keys:
        lines.append(indent + "  " + (types_ja([k]) if k != "Other" else "その他"))
        items = sorted(groups[k], key=lambda x: (disp_oid(st, x), x))
        if flat:
            for x in items:
                lines.append(indent + "    " + fmt_perm(st, x))
            continue
        same = {}
        for x in items:
            same.setdefault((perm_bits(st, x), combat_role(st, x)), []).append(x)
        for oids in same.values():
            lines.append(indent + "    " + fmt_perm_group(st, oids))
    return lines


def render_pile(st, key, label, indent="   "):
    """墓地・追放を「カード名 xN」にまとめて全部出す。

    oid はここでは出さない。狙って触るときは `zone` を使う。
    まとめないと同名が並んで枚数が数えられず、全部出さないと
    「あの除去されたカードはまだ墓地にいるか」に答えられない。
    """
    oids = st["zones"][key]
    if not oids:
        return []
    counts = {}
    for oid in oids:                      # 古い順を保ったまま数える
        n = disp_oid(st, oid)
        counts[n] = counts.get(n, 0) + 1
    body = ", ".join("%s x%d" % (n, c) for n, c in counts.items())
    return [indent + "%s(%d): %s" % (label, len(oids), body)]


def cmd_show(args, st):
    tag = "（優先権なし）" if st["phase"] in NO_PRIORITY else ""
    print("=== Turn %s / %s / %s %s ===" % (st["turn"], st["active"], st["phase"], tag))
    if getattr(args, "next_oid", False):
        print("次の生成oid: %d" % st["next_oid"])
    for pid in ("P1", "P2"):
        p = st["players"][pid]
        z = st["zones"]
        mark = "★" if pid == st["active"] else " "
        line = ("%s%s %s: ライフ %d%s | 手札 %d | 山札 %d | 墓地 %d | 土地 %d/%d"
                % (mark, pid, p["name"], p["life"],
                   " 毒%d" % p["poison"] if p["poison"] else "",
                   len(z[pid + ":hand"]), len(z[pid + ":library"]),
                   len(z[pid + ":graveyard"]), p["lands_played"], p["land_drops"]))
        if p["pool"]:
            line += " | マナ %s" % p["pool"]
        if p.get("mana_groups"):
            line += " | " + " / ".join(mana.describe(p)[1:])
        print(line)
        for line in render_battlefield(st, pid, flat=getattr(args, "flat", False)):
            print(line)
        for line in render_pile(st, "%s:graveyard" % pid, "墓地"):
            print(line)
        for line in render_pile(st, "%s:exile" % pid, "追放"):
            print(line)
        if args.hand in (pid, "both"):
            print("   " + "\n   ".join(render_hand(st, pid, indent="  ")))
    if st.get("effects"):
        print("継続効果: " + " / ".join("[%s]%s" % (e["until"], e["text"])
                                        for e in st["effects"]))
    if (st.get("combat") or {}).get("attackers"):
        for a, target, blockers in pairs(st):
            bs = ", ".join("[%d]%s" % (b, disp_oid(st, b)) for b in blockers)
            print("戦闘: [%d]%s → %s" % (a, disp_oid(st, a), bs or target))
    print("スタック: %s" % stack_str(st))
    relations.describe(sys.modules[__name__], st)
    bookkeeping.list_pending(st, include_links=False)


def cmd_zone(args, st):
    """公開ゾーンの中身を全部出す。

    show の「墓地(直近)」は末尾4枚しか出さないので、「あの除去されたカードは
    まだ墓地にいるか」に答えられない。墓地・追放は公開情報なので、
    数えられる形で全部見せる。ライブラリーは隠匿情報なので断る。
    """
    ref = args.zone
    if ":" in ref:
        pid, zname = ref.split(":", 1)
    else:
        pid, zname = (args.player or st["active"]), ref
    pid = pid.upper()
    aliases = {"gy": "graveyard", "grave": "graveyard", "墓地": "graveyard",
               "exiled": "exile", "追放": "exile", "bf": "battlefield",
               "戦場": "battlefield", "手札": "hand", "山札": "library"}
    zname = aliases.get(zname, zname)
    if zname == "stack":
        print("スタック: %s" % stack_str(st))
        return
    key = "%s:%s" % (pid, zname)
    if key not in st["zones"]:
        sys.exit("不明なゾーン: %s\n指定できるのは %s（stack も可）"
                 % (key, ", ".join(sorted(k for k in st["zones"] if k != "stack"))))
    if zname == "library":
        sys.exit("ライブラリーの中身は隠匿情報です。上から N 枚だけ見るなら "
                 "`look %s N` を使ってください。" % pid)
    if zname == "hand":
        for line in render_hand(st, pid):
            print(line)
        return
    oids = st["zones"][key]
    print("%s %s の%s %d枚%s" % (pid, st["players"][pid]["name"], types_zone_ja(zname),
                                 len(oids),
                                 "（古い順）" if zname == "graveyard" else ""))
    if not oids:
        print("  （なし）")
        return
    rows = [["#", "oid", "カード名 ／ タイプ"]]
    for i, oid in enumerate(oids, 1):
        o = st["objects"][str(oid)]
        c = card_of(st, o)
        kind = types_ja(c.get("types", []))
        if c.get("power") is not None:
            kind += " %s/%s" % (c["power"], c["toughness"])
        rows.append([str(i), "[%d]@%d" % (oid, o.get("incarnation", 1)), "%s ／ %s" % (disp_of(st, o), kind)])
    w = [max(width(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  " + "  ".join(pad(r[i], w[i]) for i in range(len(r))).rstrip())


def types_zone_ja(z):
    return {"graveyard": "墓地", "exile": "追放領域",
            "battlefield": "戦場", "hand": "手札"}.get(z, z)


class _Args(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


def oracle_block(st, oid, indent="  "):
    """1枚分のオラクル全文。プレイヤー役はカードを引けないので、ここで全部渡す。"""
    ob = st["objects"][str(oid)]
    rec, _ = cardcache.get(ob["name"], _CTX["dir"], offline=True)
    body = cardcache.summary(rec)
    ov = st.get("cards", {}).get(ob.get("card_key") or ob["name"])
    lines = [indent + "[%d] %s" % (oid, l) if i == 0 else indent + "    " + l
             for i, l in enumerate(body.splitlines())]
    if ov:
        lines.append(indent + "    ※この個体の上書き: %s"
                     % json.dumps(ov, ensure_ascii=False))
    return lines


def mana_sources(st, pid):
    """今アンタップしていて、マナ能力を持つパーマネント。用途制限も添える。"""
    out = []
    for oid in st["zones"]["%s:battlefield" % pid]:
        ob = st["objects"][str(oid)]
        if ob["tapped"]:
            continue
        lines, restricted = mana_notes(st, ob)
        if not lines and not restricted:
            continue
        note = " ／ ".join(lines) or "（マナ能力）"
        if restricted:
            note += "  !!用途制限: " + " ／ ".join(restricted)
        out.append("  [%d]%s: %s" % (oid, disp_of(st, ob), note))
    return out


def cmd_view(args, st):
    """プレイヤー役に渡す1画面ぶんの情報。

    プレイヤー役のサブエージェントには**ファイルを触らせない**。状態ファイルを
    直接読めてしまうと、隠匿情報を守る仕組みが意味を失うため。
    その代わり、判断に要るものはここで全部渡す：盤面・自分の手札とその全文・
    公開されているパーマネントの全文・出せるマナ・墓地・追放・スタック・継続効果。
    """
    pid = args.player
    p = st["players"][pid]
    print("################ あなたは %s（%s）################" % (pid, p["name"]))
    cmd_show(_Args(hand=pid, flat=args.flat), st)
    src = mana_sources(st, pid)
    print("---- 今出せるマナ（アンタップのパーマネント）----")
    print(chr(10).join(src) if src else "  （なし）")
    print("---- 手札のテキスト ----")
    hand = st["zones"]["%s:hand" % pid]
    if not hand:
        print("  （なし）")
    for oid in sorted(hand, key=lambda x: hand_key(st, x)):
        print(chr(10).join(oracle_block(st, oid)))
    for side, label in ((pid, "自分"), (other_seat(pid), "相手")):
        bf = st["zones"]["%s:battlefield" % side]
        show = [x for x in bf
                if "Basic" not in card_of(st, st["objects"][str(x)]).get("supertypes", [])]
        if not show:
            continue
        print("---- %sの戦場のテキスト（公開情報）----" % label)
        for oid in show:
            print(chr(10).join(oracle_block(st, oid)))


# ---------------------------------------------------------------- 乱数

def rng(st):
    """seed + 両席共通の連番から乱数を作る。

    初期入力と操作列も同じなら再現可能。seedだけではAIの選択を固定しない。

    st が None のとき（対局開始前の `roll` / `coin` / `pick`）は、
    --seed と --seq から作る。連番を状態に持てないので、同じ --seq を
    指定すれば同じ結果が出る＝後から再現できる、という約束にしてある。
    """
    if st is None:
        seed, seq = _CTX["pre_seed"], _CTX["pre_seq"]
        return random.Random("%s:pre:%d" % (seed, seq)), seq
    st["rng_seq"] = st.get("rng_seq", 0) + 1
    return random.Random("%s:%d" % (st.get("seed"), st["rng_seq"])), st["rng_seq"]


def do_shuffle(st, pid):
    lib = st["zones"]["%s:library" % pid]
    r, n = rng(st)
    r.shuffle(lib)
    log(st, "%s のライブラリーをシャッフル (rng#%d, %d枚)" % (pid, n, len(lib)))
    return n


def cmd_shuffle(args, st):
    n = do_shuffle(st, args.player)
    print("%s のライブラリーをシャッフルしました (rng#%d, %d枚)"
          % (args.player, n, len(st["zones"]["%s:library" % args.player])))


def cmd_roll(args, st):
    m = re.fullmatch(r"(\d*)[dD](\d+)", args.spec)
    if not m:
        sys.exit("形式: 2d6 / d20 のように指定してください。")
    count = int(m.group(1) or 1)
    sides = int(m.group(2))
    r, n = rng(st)
    vals = [r.randint(1, sides) for _ in range(count)]
    msg = "%s → %s（合計 %d）" % (args.spec, vals, sum(vals))
    if st is not None:
        log(st, "ダイス %s [rng#%d]" % (msg, n))
    print(msg + ("" if st is not None else "  [対局前 seed=%s seq=%d]"
                 % (_CTX["pre_seed"], n)))


def cmd_coin(args, st):
    r, n = rng(st)
    vals = [r.choice(["表", "裏"]) for _ in range(args.n)]
    if st is not None:
        log(st, "コイン %s [rng#%d]" % (vals, n))
    print(" ".join(vals) + ("" if st is not None else "  [対局前 seed=%s seq=%d]"
                            % (_CTX["pre_seed"], n)))


def cmd_pick(args, st):
    r, n = rng(st)
    v = r.choice(args.choices)
    if st is not None:
        log(st, "ランダム選択 %s → %s [rng#%d]" % (args.choices, v, n))
    print(v + ("" if st is not None else "  [対局前 seed=%s seq=%d]"
               % (_CTX["pre_seed"], n)))


# ---------------------------------------------------------------- ライブラリー操作

def find_by_name(st, oids, query):
    """oid の並びを、英語名でも日本語名でも検索する。

    戻り値は (hits, resolved)。resolved=False は検索語が実在カードとして
    解決できなかった場合で、「探したが1枚も無かった」とは区別する。
    区別しないと、打ち間違いを空振り扱いしてシャッフルが走り、
    ライブラリーの順序を無意味に壊してしまう。
    """
    q = (query or "").strip()
    if not q:
        return [], False
    fold = cardcache.normalize_name

    def names_of(oid):
        n = st["objects"][str(oid)]["name"]
        return [x for x in (n, card(st, n).get("jp")) if x]

    for match in (lambda a, b: fold(a) == fold(b), lambda a, b: fold(b) in fold(a)):
        hits = [o for o in oids if any(match(x, q) for x in names_of(o))]
        if hits:
            return hits, True
    # そこに無いのは分かった。実在するカード名なのか（＝本当に空振りなのか）を確かめる。
    rec, _ = cardcache.get(q, _CTX["dir"], offline=_CTX["offline"])
    return [], not rec.get("unresolved", False)


def cmd_search(args, st):
    """ライブラリーを探して1枚持ってくる。既定で探した後にシャッフルする。"""
    pid = args.player
    lib = st["zones"]["%s:library" % pid]
    resolved = True
    if args.oid:
        oid = int(args.oid)
        if oid not in lib:
            sys.exit("oid %d は %s のライブラリーにありません。" % (oid, pid))
        hits = [oid]
    else:
        hits, resolved = find_by_name(st, lib, args.query)
    if not hits and not resolved:
        sys.exit("「%s」は実在するカード名として解決できません。綴りを確かめてください。\n"
                 "（打ち間違いの可能性があるので、ライブラリーはシャッフルしていません）"
                 % args.query)
    if not hits:
        print("該当なし。（見つからなかったことも公開情報になる）")
    elif len(hits) > 1:
        print("候補が複数あります。--oid で選んでください:")
        # 候補の表示順からも現在のライブラリー順を漏らさない。
        for o in sorted(hits, key=int)[:20]:
            print("  %4d %s" % (o, disp_oid(st, o)))
        sys.exit("サーチ未完了: --oidで選択（移動・シャッフルなし）。")
    else:
        oid = hits[0]
        dest = "%s:%s" % (pid, args.to)
        relations.move_many(sys.modules[__name__], st, [(oid, dest, False)])
        name = disp_oid(st, oid)
        log(st, "%s がライブラリーから %s(%d) を %s へ" % (pid, name, oid, args.to))
        print("%s(%d) → %s" % (name, oid, dest))
    if not args.no_shuffle:
        n = do_shuffle(st, pid)
        print("→ ライブラリーをシャッフルしました (rng#%d)" % n)


def cmd_look(args, st):
    lib = st["zones"]["%s:library" % args.player]
    print("%s のライブラリー上から%d枚（移動はしていない）:" % (args.player, args.n))
    for i, oid in enumerate(lib[:args.n], 1):
        print("  %d. %4d %s" % (i, oid, disp_oid(st, oid)))
    print("→ 順を変えるなら move <oid> library --top、下に置くなら move <oid> library")


def cmd_mill(args, st):
    pid = args.player
    lib = st["zones"]["%s:library" % pid]
    moved = []
    milled = list(lib[:max(0, args.n)])
    relations.move_many(sys.modules[__name__], st, [(oid, "%s:graveyard" % pid, False) for oid in milled])
    for oid in milled:
        moved.append("%s(%d)" % (disp_oid(st, oid), oid))
    log(st, "%s が %d枚切削: %s" % (pid, args.n, ", ".join(moved)))
    print("切削: %s  （残り%d枚）" % (", ".join(moved) or "なし", len(lib)))


def cmd_mulligan(args, st):
    """ロンドン・マリガン。手札を戻してシャッフルし、7枚引き直す。"""
    pid = args.player
    p = st["players"][pid]
    hand = st["zones"]["%s:hand" % pid]
    relations.move_many(sys.modules[__name__], st, [(oid, "%s:library" % pid, False) for oid in list(hand)])
    do_shuffle(st, pid)
    p["mulligans"] = p.get("mulligans", 0) + 1
    lib = st["zones"]["%s:library" % pid]
    for _ in range(min(7, len(lib))):
        relations.move_many(sys.modules[__name__], st, [(lib[0], "%s:hand" % pid, False)])
    log(st, "%s がマリガン（%d回目）" % (pid, p["mulligans"]))
    print("%s マリガン%d回目:" % (pid, p["mulligans"]))
    if getattr(args, "quiet", False):
        print("  %d枚（内容は伏せます）" % len(hand))
    else:
        for oid in hand:
            print("  %4d %s" % (oid, st["objects"][str(oid)]["name"]))
    print("→ キープするなら %d枚を `bottom %s <oid...>` でライブラリーの一番下へ。"
          % (p["mulligans"], pid))


def cmd_bottom(args, st):
    pid = args.player
    hand = st["zones"]["%s:hand" % pid]
    names = []
    for ref in args.refs:
        oid = resolve_ref(st, ref)
        if oid not in hand:
            sys.exit("oid %d は %s の手札にありません。" % (oid, pid))
        relations.move_many(sys.modules[__name__], st, [(oid, "%s:library" % pid, False)])
        names.append("%s(%d)" % (st["objects"][str(oid)]["name"], oid))
    quiet = getattr(args, "quiet", False)
    log(st, "%s が %s をライブラリーの下へ" % (pid, ", ".join(names)),
        private=pid if quiet else None)
    shown = "%d枚（内容は伏せます）" % len(names) if quiet else ", ".join(names)
    print("下に置いた: %s  （手札%d枚）" % (shown, len(hand)))


# ---------------------------------------------------------------- 盤面の補助

def cmd_token(args, st):
    """トークンを n 個作る。

    サブタイプ・タップ・攻撃中は「あとで手で足せばいい」ものではない。
    「Lizard で攻撃するたび」「Gnome でないクリーチャーで攻撃するたび」のように
    サブタイプを見る誘発があり、機動 (mobilize) や忍術のトークンは
    「タップ状態で攻撃している状態で」出る。ここで作れないと、
    毎回 card set --oid と tap と attack を継ぎ足すことになり、抜ける。
    """
    made = []
    for _ in range(max(1, args.n)):
        made.append(make_one_token(args, st))
    if args.attacking:
        cb = combat_state(st)
        defender = "P2" if args.player == "P1" else "P1"
        for oid in made:
            cb["attackers"][str(oid)] = args.target or defender
        print("→ %d体を「タップ状態で攻撃している」状態にしました（攻撃先 %s）。"
              % (len(made), args.target or defender))
    bookkeeping.report_entries(sys.modules[__name__], st, made)


def make_one_token(args, st):
    oid = st["next_oid"]
    st["next_oid"] += 1
    # トークン名も実在カードと同じ英語名に正規化する。揃えないと、コピー・トークンが
    # コピー元と別カード扱いになり、名前を見る効果（大釜・コピー・同名参照）が噛み合わない。
    rec = cardcache.load(_CTX["dir"], args.name, quiet=True)
    name = (rec or {}).get("name") or args.name
    key = "#%d" % oid
    o = new_object(oid, name, args.player)
    o["token"] = True
    o["card_key"] = key          # 定義は個体ごと。名前で共有すると本物のカードを汚染する
    st["objects"][str(oid)] = o
    c = st.setdefault("cards", {}).setdefault(key, {})
    c["token"] = True
    if args.types:
        c["types"] = norm_types([t for t in args.types.split("/") if t])
    elif not rec:
        c["types"] = ["Creature"]     # 実在カードとして解決できないトークンの既定
    if args.power is not None:
        c["power"] = args.power
    if args.toughness is not None:
        c["toughness"] = args.toughness
    if args.oracle:
        c["oracle"] = args.oracle
    if args.subtypes:
        c["subtypes"] = [t.strip() for t in args.subtypes.split("/") if t.strip()]
    if args.colors:
        c["colors"] = [x.strip().upper() for x in args.colors.replace("/", "").replace(",", "")]
    if args.tapped or args.attacking:
        o["tapped"] = True
    if args.attacking:
        o["sick"] = False            # 攻撃している状態で出るので、召喚酔いの警告は不要
    st["zones"]["%s:battlefield" % args.player].append(oid)
    log(st, "%s がトークン %s(%d) を生成%s"
        % (args.player, name, oid,
           "（タップ・攻撃中）" if args.attacking else ("（タップ状態）" if args.tapped else "")))
    print("トークン生成: %s(%d) → %s:battlefield%s"
          % (name, oid, args.player,
             " (T)(攻撃中)" if args.attacking else (" (T)" if args.tapped else "")))
    if rec and not args.types:
        print("→ 《%s》のコピーとしてタイプ・P/T を引き継ぎます（個体キー %s）。"
              % (cardcache.display_name(rec), key))
    return oid


def detach_all_from(st, host_oid):
    """host_oid に付いていた装備品・オーラを全部外す（CR 704.5n/704.5m）。"""
    freed = []
    for o in st["objects"].values():
        if o.get("attached_to") == host_oid:
            o["attached_to"] = None
            drop_attached_mods(st, o["oid"])
            freed.append("%s(%d)" % (o["name"], o["oid"]))
    return freed


def drop_attached_mods(st, src_oid):
    """src_oid のオーラ・装備に由来する `--until attached` の修整を全部外す。

    外れた／戦場を離れたオーラの +N/+N が残り続けると、
    盤面がじわじわ実際とずれる。外すのは記帳側の責任にする。
    """
    dropped = 0
    for o in st["objects"].values():
        before = len(o["mods"]) + len(o.get("grants", []))
        o["mods"] = [m for m in o["mods"]
                     if not (m.get("until") == "attached"
                             and str(m.get("src")) == str(src_oid))]
        if o.get("grants"):
            o["grants"] = [g for g in o["grants"]
                           if not (g.get("until") == "attached"
                                   and str(g.get("src")) == str(src_oid))]
        dropped += before - len(o["mods"]) - len(o.get("grants", []))
    return dropped


def cmd_grant(args, st):
    """装備・オーラ・呪文で与えられたキーワードを記録する。

    与えられた能力はカードのオラクルに書いていないので、記録しないと
    こちらの記憶にしか存在しなくなる。トランプルや二段攻撃は戦闘の計算を
    まるごと変えるので、そこが記憶頼りだと結果が信用できない。
    `mod` と同じで、`--until attached` は付与元の oid を --src で要求する。
    """
    o = obj(st, resolve_ref(st, args.ref))
    g = o.setdefault("grants", [])
    if args.clear:
        source = str(resolve_ref(st, args.src)) if args.src else None
        remaining = [x for x in g if source is not None and str(x.get("src")) != source]
        n = len(g) - len(remaining)
        o["grants"] = remaining
        print("%s(%d) の付与キーワード %d件を消しました。" % (disp_of(st, o), o["oid"], n))
        return
    if not args.keywords:
        sys.exit("付与するキーワードを指定してください（例: grant 53 トランプル 二段攻撃）。")
    if args.until == "attached" and not args.src:
        sys.exit("--until attached には、付与元（装備・オーラ）の oid を --src で"
                 "指定してください。外れたときに一緒に消すためです。")
    if args.until == "attached":
        source = resolve_ref(st, args.src)
        obj(st, source)
        relations.attached_check(sys.modules[__name__], st, source, o["oid"])
        args.src = str(source)
    for kw in args.keywords:
        g.append({"kw": kw, "until": args.until, "src": args.src or ""})
    log(st, "%s(%d) に %s を付与 (%s)"
        % (o["name"], o["oid"], "・".join(args.keywords), args.until))
    note = {"eot": "ターン終了時に消えます", "permanent": "消えません",
            "attached": "外れると消えます"}[args.until]
    print("%s(%d) 付与: %s  [%s]"
          % (disp_of(st, o), o["oid"],
             "・".join(x["kw"] for x in o["grants"]), note))


def granted(st, oid):
    return ([g["kw"] for g in st["objects"][str(oid)].get("grants", [])]
            + relations.values(sys.modules[__name__], st, oid)[2])


def cmd_attach(args, st):
    oid = resolve_ref(st, args.ref)
    o = obj(st, oid)
    if args.detach and args.to:
        sys.exit("--detach と --to は併用できません。")
    if not (zone_of(st, oid) or "").endswith(":battlefield"):
        sys.exit("装備・オーラは戦場に必要です。")
    c = card_of(st, o)
    subtypes = [x.lower() for x in c.get("subtypes", [])]
    if not any(x in subtypes for x in ("equipment", "aura", "fortification")):
        sys.exit("装着元のEquipment/Aura/Fortificationタイプを確認してください。")
    if args.detach:
        target = o["attached_to"]
        o["attached_to"] = None
        dropped = drop_attached_mods(st, oid)
        log(st, "%s(%d) を外した" % (o["name"], oid))
        print("外しました。" + ("（--until attached の修整 %d件も外しました）" % dropped
                                if dropped else ""))
        return
    if not args.to:
        sys.exit("attachは--toまたは--detachが必須です。")
    target = args.to if args.to in ("P1", "P2") else resolve_ref(st, args.to)
    if target in ("P1", "P2"):
        if "aura" not in subtypes:
            sys.exit("プレイヤーに装着できるのは適正なオーラだけです。")
    else:
        host = obj(st, target)
        if target == oid or not (zone_of(st, target) or "").endswith(":battlefield"):
            sys.exit("自分自身・戦場外には装着できません。")
        host_types = [x.lower() for x in card_of(st, host).get("types", [])]
        if "equipment" in subtypes and "creature" not in host_types:
            sys.exit("装備先はクリーチャーである必要があります。")
        if "fortification" in subtypes and "land" not in host_types:
            sys.exit("城砦の装着先は土地である必要があります。")
    old_target = o.get("attached_to")
    dropped = 0
    if o.get("attached_to") != target:
        dropped = drop_attached_mods(st, oid)
        # Becoming attached gives the attachment a new timestamp. Same-target
        # attach is not a new attachment event. IDs still identify corrections.
        effects = st.get("fx", [])
        moving = [f for f in effects if f["source"] == relations.ref(st, oid) and f["scope"] == "attached"]
        if moving:
            st["fx"] = [f for f in effects if f not in moving] + moving
    o["attached_to"] = target
    msg = "装着[%s]: %s → %s / 旧mod・grant除去 %s件" % (oid, old_target, target, dropped)
    log(st, msg)
    print(msg)
    for host_oid in dict.fromkeys([old_target, target]):
        if str(host_oid) in st["objects"]:
            print("→ [%s] P/T=%s 付与=%s" % (host_oid, pt(st, obj(st, host_oid)), granted(st, host_oid)))
    if "aura" in subtypes:
        print("!! オーラ固有のエンチャント条件・プロテクション等は裁定で確認してください。")
    if not any(f["source"] == relations.ref(st, oid) and f["scope"] == "attached" for f in st.get("fx", [])):
        print("!! 装着効果のfx定義がありません。カードテキストを確認して登録してください（旧mod/grantとの重複に注意）。")


def cmd_pcounter(args, st):
    p = st["players"][args.player]
    if args.kind == "poison":
        p["poison"] = max(0, p.get("poison", 0) + args.n)
        v = p["poison"]
    else:
        pc = p.setdefault("counters", {})
        pc[args.kind] = pc.get(args.kind, 0) + args.n
        if pc[args.kind] <= 0:
            pc.pop(args.kind)
        v = pc.get(args.kind, 0)
    log(st, "%s の %s カウンター %+d → %d" % (args.player, args.kind, args.n, v))
    print("%s %s = %d" % (args.player, args.kind, v))


def cmd_effect(args, st):
    fx = st.setdefault("effects", [])
    if args.op == "add":
        fx.append({"text": args.text, "until": args.until, "turn": st["turn"]})
        log(st, "効果を登録: %s (%s)" % (args.text, args.until))
    elif args.op == "clear":
        if args.text:
            st["effects"] = [e for e in fx if args.text not in e["text"]]
        else:
            st["effects"] = []
    for i, e in enumerate(st.get("effects", [])):
        print("  %d. [%s] %s" % (i, e["until"], e["text"]))
    if not st.get("effects"):
        print("  （継続中の効果・リマインダーなし）")


# ---------------------------------------------------------------- 戦闘

KEYWORDS = ["先制攻撃", "二段攻撃", "トランプル", "接死", "絆魂", "プロテクション", "威迫",
            "first strike", "double strike", "trample", "deathtouch", "lifelink", "menace"]


def combat_state(st):
    return st.setdefault("combat", {"attackers": {}, "blocks": {}})


def cmd_attack(args, st):
    if any(ref in ("P1", "P2") for ref in args.refs):
        sys.exit("attack は席を先頭に取りません: attack <oid...> --target P1|P2 [--no-tap]")
    cb = combat_state(st)
    defender = "P2" if st["active"] == "P1" else "P1"
    if st["phase"] != "combat.attackers":
        if st["phase"] in ("precombat_main", "combat.begin"):
            advance_to(st, "combat.attackers")
            print("→ 攻撃者を宣言するため combat.attackers へ進めました。")
        else:
            print("!! 現在%s。攻撃宣言はcombat.attackers。"
                  % st["phase"])
    for ref in args.refs:
        oid = resolve_ref(st, ref)
        o = obj(st, oid)
        warn = []
        if o["tapped"]:
            warn.append("タップ状態")
        if o["sick"] and not any(kw in ("速攻", "haste")
                                 for kw in (g.lower() for g in granted(st, oid))):
            warn.append("召喚酔い（速攻が必要）")
        if warn:
            print("!! %s(%d): %s。攻撃可能にする能力を確認。"
                  % (o["name"], oid, "・".join(warn)))
        cb["attackers"][str(oid)] = args.target or defender
        if not args.no_tap:
            o["tapped"] = True
        log(st, "%s(%d) が %s に攻撃" % (o["name"], oid, args.target or defender))
    print("攻撃: " + ", ".join("%s(%s)→%s" % (obj(st, o)["name"], o, t)
                               for o, t in cb["attackers"].items()))
    print("→ 警戒持ちは --no-tap を付ける。ブロックは `block <ブロッカー> <攻撃側>`")


def cmd_block(args, st):
    cb = combat_state(st)
    blocker = resolve_ref(st, args.blocker)
    attacker = resolve_ref(st, args.attacker)
    if str(attacker) not in cb["attackers"]:
        sys.exit("oid %d は攻撃クリーチャーとして宣言されていません。" % attacker)
    b = obj(st, blocker)
    if b["tapped"]:
        print("!! %s(%d) はタップ状態のためブロックできません。" % (b["name"], blocker))
    if "creature" not in [t.lower() for t in card_of(st, b).get("types", [])]:
        # 土地・アーティファクトを oid で取り違えて指定すると、P/T が無いので
        # ダメージ計算から静かに抜け落ちる。宣言の時点で気づけるようにする。
        print("!! %s(%d) はクリーチャーではありません。クリーチャー化しているなら "
              "`card set --oid` で P/T とタイプを入れてください。" % (b["name"], blocker))
    cb["blocks"][str(blocker)] = attacker
    log(st, "%s(%d) が %s(%d) をブロック"
        % (b["name"], blocker, obj(st, attacker)["name"], attacker))
    print("%s(%d) が %s(%d) をブロック" % (b["name"], blocker, obj(st, attacker)["name"], attacker))


def pairs(st):
    cb = combat_state(st)
    bf = set(st["zones"]["P1:battlefield"] + st["zones"]["P2:battlefield"])
    out = []
    for a, target in cb["attackers"].items():
        # 先制攻撃ステップで死んだブロッカーは、通常ステップにはもういない。
        # ここで落としておかないと「ブロックされたまま何も通らない」ことになり、
        # トランプルの通過ダメージを丸ごと取りこぼす（CR 702.19b）。
        blockers = [int(b) for b, at in cb["blocks"].items()
                    if at == int(a) and int(b) in bf]
        out.append((int(a), target, blockers))
    return out


def has_kw(st, oid, *words):
    """そのオブジェクトがキーワードを持つか。**付与されたものも見る。**

    トランプルや二段攻撃は装備品・オーラから来ることが多い。カード自身の
    オラクルしか見ないと、装備した瞬間から戦闘の計算が静かに間違い続ける。
    """
    c = card_of(st, obj(st, oid))
    text = ((c.get("oracle", "") or "") + " " + " ".join(c.get("keywords", []))
            + " " + " ".join(granted(st, oid))).lower()
    return any(w.lower() in text for w in words)


def strike_role(st, oid):
    """先制攻撃／二段攻撃の別。damage の --step で誰が殴るかを決める。"""
    double = has_kw(st, oid, "二段攻撃", "double strike")
    first = has_kw(st, oid, "先制攻撃", "first strike")
    if double:
        return "double"
    return "first" if first else "normal"


def deals_in(st, oid, step):
    if step == "all":
        return True
    role = strike_role(st, oid)
    if step == "first":
        return role in ("first", "double")
    return role in ("normal", "double")      # step == "regular"


CONDITIONAL_MARKERS = ["かぎり", "としても", "as long as", "if you control",
                       "をコントロールしているなら"]


def keyword_is_conditional(text, kw):
    """「〜をコントロールしているかぎり絆魂を持つ」型かどうか。

    条件を機械判定はしない。条件付きだと分かれば「確認しろ」と言えるので、
    ドワーフが死んだ後に絆魂ぶんのライフを足してしまう事故を防げる。
    """
    for line in text.splitlines():
        if kw.lower() in line.lower():
            low = line.lower()
            if any(m.lower() in low for m in CONDITIONAL_MARKERS):
                return True
    return False


def keyword_notes(st, oids, done=(), done_by_oid=None):
    """手で処理が要るキーワードを並べる。done に挙げたものは既に処理済みとして外す。

    done_by_oid は oid ごとの追加の除外（例: ブロックされなかった攻撃者のトランプル）。
    """
    notes = []
    done_by_oid = done_by_oid or {}
    for oid in oids:
        c = card_of(st, obj(st, oid))
        oracle = (c.get("oracle", "") or "")
        text = (oracle + chr(10) + " ".join(c.get("keywords", []))
                + chr(10) + chr(10).join(granted(st, oid)))
        skip = set(done) | set(done_by_oid.get(oid, ()))
        hit = []
        for k in KEYWORDS:
            if k.lower() not in text.lower() or k in skip:
                continue
            if keyword_is_conditional(text, k):
                hit.append(k + "（条件付き。条件を満たしているか確認）")
            else:
                hit.append(k)
        if hit:
            notes.append("%s(%d): %s" % (obj(st, oid)["name"], oid, "・".join(hit)))
    return notes


def cmd_combat(args, st):
    if args.op == "damage":
        relations.require_resolved(sys.modules[__name__], st)
    if args.op == "damage":
        if st["phase"] != "combat.damage":
            sys.exit("combat damageはcombat.damageで実行。応答・誘発確認後、phase to combat.damage。")
        if st["zones"]["stack"]:
            sys.exit("スタックが空ではありません。解決後に戦闘ダメージ。")
    cb = combat_state(st)
    if args.op == "clear":
        st["combat"] = {"attackers": {}, "blocks": {}}
        print("戦闘の記録をクリアしました。")
        return
    rows = pairs(st)
    if not rows:
        print("攻撃クリーチャーが宣言されていません。")
        return
    if args.op == "show":
        for a, target, blockers in rows:
            av = pt(st, obj(st, a))
            bs = ", ".join("[%d]%s%s" % (b, disp_oid(st, b),
                                         " %d/%d" % pt(st, obj(st, b)) if pt(st, obj(st, b)) else "")
                           for b in blockers) or "ブロックなし → %s" % target
            print("  [%d]%s%s → %s" % (a, disp_oid(st, a),
                                       " %d/%d" % av if av else "", bs))
        return
    # op == "damage"
    step = getattr(args, "step", "all") or "all"
    involved, applied, skipped = [], [], []
    trample_done = {}
    for a, target, blockers in rows:
        ao = obj(st, a)
        av = pt(st, ao)
        involved.append(a)
        if av is None:
            print("!! %s(%d) の P/T が未登録です。card set で登録してください。" % (ao["name"], a))
            continue
        att_deals = deals_in(st, a, step)
        if not att_deals:
            skipped.append("%s(%d)[%s]" % (disp_oid(st, a), a, strike_role(st, a)))
        if not blockers:
            # ブロックされていない、または全ブロッカーが先制攻撃ステップで死んだ場合。
            # 後者でもトランプルなら全ダメージがプレイヤーへ通る（CR 702.19b）。
            # どちらにせよ割り振りは起きないので、トランプルの手動処理は要らない。
            trample_done.setdefault(a, set()).update({"トランプル", "trample"})
            if not att_deals:
                continue
            if target in st["players"]:
                st["players"][target]["life"] -= av[0]
                applied.append("%s(%d) → %s に%d点" % (ao["name"], a, target, av[0]))
            else:
                t = obj(st, resolve_ref(st, target))
                t["damage"] += av[0]
                applied.append("%s(%d) → %s に%d点" % (ao["name"], a, t["name"], av[0]))
            continue
        involved += blockers
        remaining = av[0] if att_deals else 0
        trample = has_kw(st, a, "trample", "トランプル")
        # 致死ダメージは「割り振る前」の残りタフネスで数える。割り振った後に
        # 数えると必ず0になり、トランプルの超過分が全ダメージになってしまう。
        lethal = sum(max(0, (pt(st, obj(st, b)) or (0, 0))[1] - obj(st, b)["damage"])
                     for b in blockers if pt(st, obj(st, b)))
        excess = max(0, av[0] - lethal) if trample else 0
        for b in blockers:          # 宣言順に、致死ダメージ分を割り振る既定の配分
            bo = obj(st, b)
            bv = pt(st, bo)
            if bv is None:
                print("!! %s(%d) の P/T が未登録です。" % (bo["name"], b))
                continue
            assign = 0
            if att_deals:
                need = max(0, bv[1] - bo["damage"])
                # トランプル持ちは各ブロッカーに致死分だけ置いて残りを通す。
                # 単体ブロックのとき全部押し付けると、通過分が消える。
                assign = (min(remaining, need)
                          if (trample or len(blockers) > 1) else remaining)
                bo["damage"] += assign
                remaining -= assign
            blk_deals = deals_in(st, b, step)
            back = bv[0] if blk_deals else 0
            ao["damage"] += back
            if not blk_deals:
                skipped.append("%s(%d)[%s]" % (disp_oid(st, b), b, strike_role(st, b)))
            applied.append("%s(%d)/%s(%d): %d点 / %d点"
                           % (ao["name"], a, bo["name"], b, assign, back))
        if not att_deals:
            continue
        if trample and excess:
            if getattr(args, "trample", False) and target in st["players"]:
                st["players"][target]["life"] -= excess
                applied.append("%s(%d) トランプル超過 → %s に%d点"
                               % (ao["name"], a, target, excess))
            else:
                print("!! %s(%d): 致死%d点は適用済み、超過%d点→%sは未適用。補完する（次回は--trample）。"
                      % (ao["name"], a, lethal, excess, target))
        elif remaining > 0:
            print("!! %s(%d) に%d点の余りがあります。" % (ao["name"], a, remaining))
    log(st, "戦闘ダメージ[%s]: %s" % (step, " / ".join(applied)))
    print(chr(10).join("  " + a for a in applied) or "  （適用なし）")
    if step != "all" and skipped:
        print("  （このステップで殴らない: %s）" % ", ".join(sorted(set(skipped))))
    done = set()
    if step != "all":
        done |= {"先制攻撃", "first strike", "二段攻撃", "double strike"}
    if getattr(args, "trample", False):
        done |= {"トランプル", "trample"}
    notes = keyword_notes(st, involved, done, done_by_oid=trample_done)
    if notes:
        print(chr(10) + "!! 未適用（上記ダメージは適用済み）:")
        for n in notes:
            print("   " + n)
    if step == "first":
        print("→ sba --apply-deaths → 未決着ならcombat damage --step regular（貫通は--trample）。")
    else:
        print("→ sbaで確認・必要な適用 → combat clear。")


# ---------------------------------------------------------------- 進行補助・記録

def cmd_pass(args, st):
    bookkeeping.require_ready(st)
    passed = st.setdefault("passed", [])
    who = args.player or st.get("priority", st["active"])
    if who not in passed:
        passed.append(who)
    other = "P2" if who == "P1" else "P1"
    if len(passed) >= 2:
        st["passed"] = []
        if st["zones"]["stack"]:
            print("両者パス → スタックの一番上を解決します。`stack resolve`")
        else:
            print("両者パス → 次のステップへ。")
            return cmd_phase(argparse.Namespace(op="next", value=None, state=args.state), st)
    else:
        st["priority"] = other
        print("%s がパス → %s に優先権。" % (who, other))


def results_path(args):
    """結果ファイルの置き場。既定は状態ファイルの隣。

    1ゲームごとに状態ファイルを分ける運用（undo 履歴を混ぜないため）だと、
    ディレクトリを分けた瞬間に results.jsonl も分かれて集計が割れる。
    --results で1つに寄せられるようにしてある。`end` と `stats` で同じ値を使うこと。
    """
    if getattr(args, "results", None):
        return pathlib.Path(args.results)
    return state_path(args).parent / "results.jsonl"


def record_result(args, st, result, reason, concede_by=None):
    """results.jsonl に1行足す。`end` と `concede` の共通処理。"""
    if st.get("result") and not getattr(args, "force", False):
        sys.exit("このゲームは既に記録済みです（%s / %s）。二重に記録すると集計が狂います。"
                 "書き直すなら results.jsonl の該当行を消してから --force を付けてください。"
                 % (st["result"].get("winner"),
                    st["result"].get("reason") or "理由なし"))
    rec = {"at": datetime.datetime.now(datetime.timezone.utc)
                     .replace(microsecond=0).isoformat(),
           "tag": args.tag or "",
           "first": st.get("first"),
           "seed": st.get("seed"), "turn": st["turn"], "winner": result,
           "reason": reason or "",
           "concede": concede_by,
           "life": {p: st["players"][p]["life"] for p in st["players"]},
           "mulligans": {p: st["players"][p].get("mulligans", 0) for p in st["players"]},
           "names": {p: st["players"][p]["name"] for p in st["players"]},
           "decks": {p: st["players"][p].get("deck") for p in st["players"]},
           "deck_sources": {p: st["players"][p].get("deck_source")
                            for p in st["players"]}}
    path = results_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    st["result"] = {"winner": result, "reason": reason or "",
                    "concede": concede_by, "turn": st["turn"]}
    label = "引き分け" if result == "draw" else "勝者 %s" % result
    if concede_by:
        label += "（%s の投了）" % concede_by
    log(st, "ゲーム終了 %s %s" % (label, reason or ""))
    print("記録しました → %s" % path)
    print("  %s / %dターン / ライフ %s / マリガン %s"
          % (label, rec["turn"], rec["life"], rec["mulligans"]))
    return rec


def cmd_end(args, st):
    if bool(args.winner) == bool(args.draw):
        sys.exit("--winner か --draw のどちらか一方を指定してください。")
    record_result(args, st, "draw" if args.draw else args.winner, args.reason)


CONCEDE_MIN_REASON = 20


def cmd_concede(args, st):
    """投了（CR 104.3a）。優先権が無くてもいつでもでき、即座に敗北する。

    AI同士のテストプレイでは「決着済みの盤面を何十ターンも記帳し続ける」のを
    止めるために使う。ただし早すぎる投了は勝率も平均決着ターンも壊すので、
    負ける側のアウトが本当に0であることを --reason に書かせて記録に残す。
    """
    loser = args.player
    winner = "P2" if loser == "P1" else "P1"
    reason = " ".join(args.reason) if isinstance(args.reason, list) else (args.reason or "")
    if len(reason.strip()) < CONCEDE_MIN_REASON:
        sys.exit("投了には理由が要ります（--reason）。最低でも次の3点を書いてください:\n"
                 "  1. 相手の勝ち手順（何が何ターン後に決まるのか）\n"
                 "  2. こちらのアウト（手札・戦場・残りライブラリーに解答が何枚あるか）\n"
                 "  3. そのアウトが間に合わない根拠\n"
                 "盤面が悪いだけでは投了しないこと。勝ち筋が0だと示せないなら、"
                 "そのままプレイを続けて `end` で決着させる。")
    lo = st["players"][loser]
    print("%s (%s) が投了しました（CR 104.3a: 投了はいつでもでき、即座に敗北する）。"
          % (loser, lo["name"]))
    print("  投了時点: ライフ %d / 手札 %d枚 / 残りライブラリー %d枚 / ターン %d"
          % (lo["life"], len(st["zones"]["%s:hand" % loser]),
             len(st["zones"]["%s:library" % loser]), st["turn"]))
    record_result(args, st, winner, reason, concede_by=loser)


def group_key(rec):
    """集計の単位。タグがあればそれ、無ければデッキ名の組。

    席（P1/P2）で数えてはいけない。先手・後手は敗者が選ぶので席は入れ替わり、
    席ごとの勝率は意味を失う。勝者はデッキに読み替えて数える。
    """
    if rec.get("tag"):
        return rec["tag"]
    d = rec.get("decks") or {}
    names = [d.get(p) or p for p in ("P1", "P2")]
    return " vs ".join(sorted(names))


def render_group(key, recs):
    n = len(recs)
    lines = ["=== %s ===  %dゲーム" % (key, n)]
    decknames = []
    for r in recs:
        for p in ("P1", "P2"):
            dn = (r.get("decks") or {}).get(p) or p
            if dn not in decknames:
                decknames.append(dn)
    ties = sum(1 for r in recs if r.get("winner") == "draw")
    rows = []
    for dn in decknames:
        w = onplay_w = onplay_n = ondraw_w = ondraw_n = 0
        muls = []
        for r in recs:
            seats = [p for p in ("P1", "P2") if ((r.get("decks") or {}).get(p) or p) == dn]
            if not seats:
                continue
            seat = seats[0]
            won = r.get("winner") == seat      # "draw" はどちらの席とも一致しない
            w += 1 if won else 0
            muls.append((r.get("mulligans") or {}).get(seat, 0))
            if r.get("first") is None:
                continue
            if r["first"] == seat:
                onplay_n += 1
                onplay_w += 1 if won else 0
            else:
                ondraw_n += 1
                ondraw_w += 1 if won else 0
        row = [dn, "%d勝" % w, "(%.0f%%)" % (100.0 * w / n)]
        if ties:
            row.append("引分 %d" % ties)
        row += ["先手 %d/%d" % (onplay_w, onplay_n) if onplay_n else "先手 -",
                "後手 %d/%d" % (ondraw_w, ondraw_n) if ondraw_n else "後手 -",
                "マリガン平均 %.2f" % (sum(muls) / len(muls)) if muls else ""]
        rows.append(row)
    lines.extend(cardcache.table(rows, indent="  "))
    lines.append("  平均決着ターン %.1f" % (sum(r["turn"] for r in recs) / n))
    conceded = [r for r in recs if r.get("concede")]
    if conceded:
        # 投了で終えたゲームの「決着ターン」は投了した時点であって、実際に
        # ライフが0になるターンではない。混ぜたまま平均を読むと短く見える。
        played = [r for r in recs if not r.get("concede")]
        line = "  うち投了 %d件" % len(conceded)
        if played:
            line += "（投了を除いた平均決着ターン %.1f）" % (
                sum(r["turn"] for r in played) / len(played))
        lines.append(line)
        for r in conceded:
            lines.append("    - T%d %s が投了: %s"
                         % (r["turn"], r["concede"], r.get("reason") or "理由なし"))
    return lines


def cmd_stats(args, _):
    path = results_path(args)
    if not path.exists():
        sys.exit("まだ結果がありません: %s\n"
                 "（結果は状態ファイルと同じディレクトリに貯まります。"
                 "別の場所を見るなら --results で指定してください）" % path)
    recs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.tag:
        recs = [r for r in recs if r.get("tag") == args.tag]
    if args.deck:
        recs = [r for r in recs
                if args.deck in ((r.get("decks") or {}).get(p) or "" for p in ("P1", "P2"))]
    if not recs:
        sys.exit("条件に合うゲームがありません。")
    groups = {}
    for r in recs:
        groups.setdefault(group_key(r), []).append(r)
    if not args.all_in_one and len(groups) > 1:
        for k in sorted(groups):
            print(chr(10).join(render_group(k, groups[k])))
        print("--- 合計 %dゲーム / %dマッチアップ ---" % (len(recs), len(groups)))
        print("（マッチアップをまたいだ合計勝率は意味を持たないので出しません。"
              "まとめたいときは `end --tag` で同じ札を付けてください）")
        return
    for k in sorted(groups):
        print(chr(10).join(render_group(k, groups[k])))


# ---------------------------------------------------------------- CLI


def _pre_rng_args(s):
    """対局前に振るときの seed 指定。状態ファイルがあるときは無視される。"""
    s.add_argument("--seed", type=int,
                   help="対局前に振るときの種（状態ファイルが無いときは必須）")
    s.add_argument("--seq", type=int, default=0,
                   help="対局前に同じ seed で複数回振るときの連番（既定 0）")


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default="playtest/state.json")
    ap.add_argument("--as", dest="seat", choices=["P1", "P2"],
                    help="この席として実行する。相手の手札・ライブラリー・秘匿メモへの"
                         "参照を拒否し、隠匿情報の参照を audit.jsonl に残す")
    ap.add_argument("--cards-dir", default=cardcache.DEFAULT_DIR,
                    help="オラクル情報のキャッシュ先（既定: %s / 環境変数 MTG_CARDS_DIR）"
                         % cardcache.DEFAULT_DIR)
    ap.add_argument("--decks-dir", default=decks.DEFAULT_DIR,
                    help="登録済みデッキの保存先（既定: %s）" % decks.DEFAULT_DIR)
    ap.add_argument("--results", metavar="PATH",
                    help="結果ファイル（既定は状態ファイルと同じディレクトリの "
                         "results.jsonl）。連戦を別ディレクトリの状態ファイルで回すと "
                         "集計が割れるので、揃えたいときはここで1つを指す")
    ap.add_argument("--offline", action="store_true",
                    help="Scryfall に問い合わせない（キャッシュのみ使う）")
    ap.add_argument("--en", action="store_true",
                    help="カード名を英語で表示する（既定は日本語印刷名を優先）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="デッキを読み込んでゲームを開始")
    s.add_argument("--prefetch", action="store_true",
                   help="デッキ内の全カードのオラクル情報を先に取得しておく")
    s.add_argument("--p1", default="P1")
    s.add_argument("--p2", default="P2")
    s.add_argument("--deck1", help="登録名 または デッキリストのファイルパス")
    s.add_argument("--deck2", help="登録名 または デッキリストのファイルパス")
    s.add_argument("--deck1-name", help="集計に使うデッキ名。サイド後のリストをファイルで"
                                        "渡すときに、素の構築の登録名を指定する")
    s.add_argument("--deck2-name", help="同上（P2側）")
    s.add_argument("--seed", type=int, default=random.randrange(2 ** 32))
    s.add_argument("--life", type=int, default=20)
    s.add_argument("--first", choices=["P1", "P2", "random"], default="P1",
                   help="先手プレイヤー（既定 P1）。random は seed から抽選する"
                        "（ゲーム1の先手決め。後から同じ seed で再現できる）。"
                        "第1ターンのドローは手で飛ばすこと")

    s = sub.add_parser("run", help="コマンドを並べたファイルをまとめて実行")
    s.add_argument("file", help="1行1コマンド。# はコメント。- で標準入力")
    s.add_argument("--keep-going", action="store_true",
                   help="失敗しても続行する（既定はそこで停止）")
    s.add_argument("--quiet", action="store_true", help="実行するコマンド行を表示しない")
    s.add_argument("--compact", action="store_true",
                   help="既知の定型説明を省略。全出力は状態ファイル横のoutputに保存")
    s.add_argument("--delta", action="store_true",
                   help="--compact時、showをバッチ開始時／前回showからの差分にする")
    s = sub.add_parser("show", help="盤面を表示")
    s.add_argument("--next-oid", action="store_true", help="検証済み反復手順の生成用に次のoidを表示")
    s.add_argument("--flat", action="store_true",
                   help="同じ見た目のパーマネントをまとめず1個ずつ出す")
    s.add_argument("--hand", choices=["P1", "P2", "both"],
                   help="そのプレイヤーの手札も並べて出す（対人モードでは自分側だけ）")
    s = sub.add_parser("view", help="プレイヤー役に渡す1画面（盤面＋自分の手札＋全オラクル）")
    s.add_argument("player", choices=["P1", "P2"])
    s.add_argument("--flat", action="store_true",
                   help="同じ見た目のパーマネントをまとめずに出す")
    s = sub.add_parser("zone", help="公開ゾーンの中身を全部表示（zone P1:graveyard）")
    s.add_argument("zone", help="P1:graveyard / exile / battlefield / stack など")
    s.add_argument("--player", help="ゾーン名だけ書いたときの持ち主（既定はアクティブ・プレイヤー）")
    s = sub.add_parser("hand", help="手札を表示（既定で 土地 → マナ総量 順）")
    s.add_argument("player")
    s.add_argument("--unsorted", action="store_true", help="引いた順のまま出す")
    s = sub.add_parser("draw")
    s.add_argument("player")
    s.add_argument("n", type=int, nargs="?", default=1)
    s.add_argument("--quiet", action="store_true",
                   help="引いたカード名を伏せる（対人モードで相手の目に触れさせない）")
    s = sub.add_parser("move", help="ゾーン間の移動")
    s.add_argument("ref", help="oid または 名前@P1:hand")
    s.add_argument("zone", help="hand/battlefield/graveyard/exile/library/stack")
    s.add_argument("--owner", help="移動先の持ち主・コントローラー")
    s.add_argument("--top", action="store_true", help="ライブラリーの一番上へ")
    s.add_argument("--tapped", action="store_true",
                   help="タップ状態で戦場に出す（タップインの土地・置換効果）")
    s.add_argument("--no-drop", action="store_true",
                   help="手札から土地を戦場に出すが、土地セット権を消費しない"
                        "（消尽・踏み倒し・「戦場に出す」効果）")
    for name in ("tap", "untap"):
        s = sub.add_parser(name)
        s.add_argument("refs", nargs="+")
    s = sub.add_parser("life", help="life P2 -3 / life P2 20 --set")
    s.add_argument("player")
    s.add_argument("value", type=int)
    s.add_argument("--set", action="store_true")
    s = sub.add_parser("loop", help="検証・合意済みループの純増を有限回数まとめて記帳")
    s.add_argument("player", choices=["P1", "P2"])
    s.add_argument("count", type=int)
    s.add_argument("--life", type=int, default=0, help="1周のライフ純増")
    s.add_argument("--mana", default="", help="1周のマナ純増（例: GG）")
    s.add_argument("--draw", type=int, default=0, help="1周のドロー枚数")
    s.add_argument("--proof", required=True, help="コスト・収支・反復条件・相手の合意")
    s = sub.add_parser("counter", help="counter 17 +1/+1 2（加算）/ --set で総数指定")
    s.add_argument("ref")
    s.add_argument("kind")
    s.add_argument("n", type=int, nargs="?", default=1)
    s.add_argument("--set", action="store_true",
                   help="加算ではなく、そのカウンターの総数を N にする")
    s = sub.add_parser("damage")
    s.add_argument("ref")
    s.add_argument("n", type=int)
    s = sub.add_parser("mod", help="mod 17 +2/+2 --until eot")
    s.add_argument("ref")
    s.add_argument("pt")
    s.add_argument("--until", required=True,
                   choices=["eot", "permanent", "attached"],
                   help="eot=クリンナップで消える / permanent=消えない / "
                        "attached=つけている間だけ（オーラ・装備。--src に oid を渡す）")
    s.add_argument("--src")
    s.add_argument("--set", action="store_true", help="同じsrc・untilの修整合計を置換（基本修整も含む総量を指定）")
    mana.add_parser(sub)
    s = sub.add_parser("stack")
    s.add_argument("op", choices=["push", "resolve", "pop", "show"])
    s.add_argument("what", nargs="?")
    s.add_argument("--controller", choices=["P1", "P2"])
    s.add_argument("--targets")
    s.add_argument("--ability", action="store_true", help="カードではなく能力を乗せる")
    s.add_argument("--src", help="能力の発生源oid。世代を記録しlinked exile --viaで参照できる")
    s.add_argument("--ability-key", help="関連する能力の識別名（既定は説明文）")
    s.add_argument("--cast", action="store_true", help="push: AIが唱えたと確認した呪文。詠唱数を記録しcast確認メモを表示")
    s = sub.add_parser("phase")
    s.add_argument("op", choices=["next", "set", "to"],
                   help="next=1つ進む / to=そのステップまで1つずつ進む（推奨） / "
                        "set=途中を通らず直接その値にする")
    s.add_argument("value", nargs="?", help="to / set の目的ステップ名")
    s = sub.add_parser("turn")
    s.add_argument("op", nargs="?", default="next")
    s.add_argument("--to", metavar="PHASE",
                   help="ターン開始後、そのステップまで一気に進む（例: precombat_main）")
    s.add_argument("--draw", action="store_true",
                   help="--to の途中でドロー・ステップを通るとき1枚引く（先手第1ターンは付けない）")
    s.add_argument("--quiet", action="store_true", help="--draw のカード名を伏せる")
    s = sub.add_parser("sba", help="機械的に判定できる状況起因処理を報告")
    s.add_argument("--apply", action="store_true",
                   help="消滅したトークンを盤面から取り除く（判定はせず、この片付けだけ）")
    s.add_argument("--apply-deaths", action="store_true",
                   help="致死ダメージ／タフネス0のクリーチャーを墓地へ移す。"
                        "破壊不能を持つものは動かさず報告だけする。"
                        "先制攻撃ステップと通常ステップの間で使う")
    s = sub.add_parser("card", help="オラクル情報の取得・登録・表示")
    s.add_argument("op", choices=["fetch", "set", "show"])
    s.add_argument("name", nargs="?", help="--oid で個体を指すときは省略できる")
    s.add_argument("--refresh", action="store_true", help="fetch: キャッシュを無視して取り直す")
    s.add_argument("--local", action="store_true",
                   help="set: キャッシュではなくこのゲームだけの上書きにする（同名すべてに効く）")
    s.add_argument("--oid", metavar="OID",
                   help="set: そのオブジェクト1個だけを上書きする"
                        "（0/0クリーチャー化・土の技など、同名の他のカードに影響しない効果）")
    s.add_argument("--cost")
    s.add_argument("--types", help="スラッシュ区切り: Creature/Artifact")
    s.add_argument("--supertypes", help="Legendary など")
    s.add_argument("--subtypes")
    s.add_argument("--oracle")
    s.add_argument("--power")
    s.add_argument("--toughness")
    s.add_argument("--loyalty")
    s.add_argument("--mana-value", type=int)
    s = sub.add_parser("note", help="ログに1行残す")
    s.add_argument("text", nargs="+",
                   help="クォートしなくてもよい（空白込みで1行として記録する）")
    s.add_argument("--private", choices=["P1", "P2"],
                   help="そのプレイヤーの秘匿メモにする（`log` の既定表示から外れる）")
    s = sub.add_parser("undo", help="直前のN保存分を戻す（既定1）。表示コマンドは数えない")
    s.add_argument("undo_count", type=int, nargs="?", default=1, metavar="N")
    s = sub.add_parser("log")
    s.add_argument("n", type=int, nargs="?", default=20)
    s.add_argument("--player", choices=["P1", "P2"], default="P1",
                   help="このプレイヤー視点で表示する（既定 P1）")
    s.add_argument("--all", action="store_true", help="秘匿メモも含めて全部出す")
    s = sub.add_parser("shuffle", help="ライブラリーをシャッフル")
    s.add_argument("player")
    # 対局前（状態ファイルがまだ無い）でも振れるようにしてある。先手決めのように
    # init より前に必要な乱数が実際にあるため。そのときは --seed が要る。
    s = sub.add_parser("roll", help="roll 2d6 / roll d20")
    s.add_argument("spec")
    _pre_rng_args(s)
    s = sub.add_parser("coin", help="コイン投げ")
    s.add_argument("n", type=int, nargs="?", default=1)
    _pre_rng_args(s)
    s = sub.add_parser("pick", help="候補からランダムに1つ選ぶ")
    s.add_argument("choices", nargs="+")
    _pre_rng_args(s)
    s = sub.add_parser("search", help="ライブラリーを探して持ってくる（既定でシャッフル）")
    s.add_argument("player")
    s.add_argument("query", nargs="?", help="カード名の一部")
    s.add_argument("--oid", help="候補が複数のとき oid で指定")
    s.add_argument("--to", default="hand", help="持ってくる先 hand/battlefield/graveyard/exile")
    s.add_argument("--no-shuffle", action="store_true", help="探した後にシャッフルしない")
    s = sub.add_parser("look", help="ライブラリーの上から N 枚を見る（移動しない）")
    s.add_argument("player")
    s.add_argument("n", type=int, nargs="?", default=1)
    s = sub.add_parser("mill", help="ライブラリーの上から N 枚を墓地へ")
    s.add_argument("player")
    s.add_argument("n", type=int)
    s = sub.add_parser("mulligan", help="ロンドン・マリガン")
    s.add_argument("player")
    s.add_argument("--quiet", action="store_true", help="引き直した手札を伏せる")
    s = sub.add_parser("bottom", help="手札からライブラリーの一番下へ")
    s.add_argument("player")
    s.add_argument("refs", nargs="+")
    s.add_argument("--quiet", action="store_true", help="戻すカードを表示と公開ログで伏せる")
    s = sub.add_parser("token", help="トークンを生成")
    s.add_argument("player")
    s.add_argument("name", help="実在カード名ならコピー・トークンとしてそのオラクルを引き継ぐ")
    s.add_argument("--types",
                   help="スラッシュ区切り。省略時はコピー元のタイプ、"
                        "実在カードでなければ Creature")
    s.add_argument("--subtypes", help="スラッシュ区切り（Goblin/Warrior など）。"
                                      "サブタイプを見る誘発があるので省略しないこと")
    s.add_argument("--colors", help="WUBRG から（例 R、WU）")
    s.add_argument("--power")
    s.add_argument("--toughness")
    s.add_argument("--oracle")
    s.add_argument("-n", "--n", type=int, default=1, help="同じトークンを何個作るか")
    s.add_argument("--tapped", action="store_true", help="タップ状態で出す")
    s.add_argument("--attacking", action="store_true",
                   help="タップ状態で攻撃している状態で出す（機動・忍術など）")
    s.add_argument("--target", help="--attacking のときの攻撃先（既定は対戦相手）")
    s = sub.add_parser("grant", help="装備・オーラで与えられたキーワードを記録する")
    s.add_argument("ref")
    s.add_argument("keywords", nargs="*",
                   help="例: トランプル 二段攻撃（表記はカードのテキストに合わせる）")
    s.add_argument("--until", default="attached",
                   choices=["eot", "permanent", "attached"],
                   help="eot=クリンナップで消える / permanent=消えない / "
                        "attached=つけている間だけ（既定。--src に付与元の oid）")
    s.add_argument("--src", help="--until attached のときの付与元 oid")
    s.add_argument("--clear", action="store_true", help="--src併用でその発生源だけ取消し。省略時は旧grantを全部消す。fxはfx removeで取消し")
    s = sub.add_parser("attach", help="オーラ・装備をつける／外す")
    s.add_argument("ref")
    s.add_argument("--to")
    s.add_argument("--detach", action="store_true")
    s = sub.add_parser("pcounter", help="プレイヤーのカウンター（poison/energy など）")
    s.add_argument("player")
    s.add_argument("kind")
    s.add_argument("n", type=int, nargs="?", default=1)
    s = sub.add_parser("effect", help="盤面に効いている効果・遅延誘発のメモ")
    s.add_argument("op", choices=["add", "list", "clear"])
    s.add_argument("text", nargs="?")
    s.add_argument("--until", default="permanent", choices=["eot", "permanent"],
                   help="add: eot はターン終了で自動的に消える（既定 permanent）。"
                        "これは表示用のメモなので、消し忘れても盤面の数値は狂わない")
    s = sub.add_parser("attack", help="攻撃クリーチャーを指定（既定でタップ）")
    s.add_argument("refs", nargs="+")
    s.add_argument("--target", help="プレイヤーID か プレインズウォーカーの oid")
    s.add_argument("--no-tap", action="store_true", help="警戒など")
    s = sub.add_parser("block", help="block <ブロッカー> <攻撃側>")
    s.add_argument("blocker")
    s.add_argument("attacker")
    s = sub.add_parser("combat")
    s.add_argument("op", choices=["show", "damage", "clear"])
    s.add_argument("--step", choices=["first", "regular", "all"], default="all",
                   help="damage: 先制攻撃／二段攻撃がいるとダメージ・ステップは2回ある。"
                        "first=先制攻撃・二段攻撃を持つものだけが殴る／"
                        "regular=先制攻撃のみのものを除いて殴る（二段攻撃は2回目も殴る）／"
                        "all=区別しない（既定・従来どおり）")
    s.add_argument("--trample", action="store_true",
                   help="damage: トランプルの超過分を防御プレイヤーに適用する"
                        "（既定は警告だけ）")
    s = sub.add_parser("pass", help="優先権をパス（両者パスで次へ）")
    s.add_argument("player", nargs="?")
    s = sub.add_parser("concede", help="投了する（CR 104.3a）。即座に敗北し結果を記録する")
    s.add_argument("player", choices=["P1", "P2"], help="投了する側")
    s.add_argument("--reason", nargs="+", required=True,
                   help="相手の勝ち手順・こちらのアウト・間に合わない根拠。記録に残る")
    s.add_argument("--tag", help="集計の単位にする札。`end` と同じものを使う")
    s.add_argument("--force", action="store_true",
                   help="既に記録済みのゲームに上書きで追記する")

    s = sub.add_parser("end", help="ゲーム結果を results.jsonl に記録")
    s.add_argument("--force", action="store_true",
                   help="既に記録済みのゲームに上書きで追記する")
    s.add_argument("--winner", choices=["P1", "P2"])
    s.add_argument("--draw", action="store_true",
                   help="引き分けとして記録する（選択の余地のない無限ループ CR 104.4b、"
                        "同時に敗北条件を満たした場合など）")
    s.add_argument("--reason")
    s.add_argument("--tag", help="集計の単位にする札。同じマッチの連戦に同じ名前を付ける"
                                 "（サイド後を別名で登録していても1つにまとまる）")
    s = sub.add_parser("stats", help="results.jsonl を集計")
    s.add_argument("--tag", help="この札のゲームだけ集計する")
    s.add_argument("--deck", help="このデッキが関わったゲームだけ集計する")
    s.add_argument("--all-in-one", action="store_true",
                   help="マッチアップで分けずに1つにまとめる（意味を持つときだけ使う）")
    s = sub.add_parser("glossary", help="日英対応表を出す")
    s.add_argument("--deck", help="対局中でないときに対象デッキを指定")
    s.add_argument("--out", help="ファイルにも書き出す")
    s = sub.add_parser("deck", help="デッキの登録・確認・検証")
    s.add_argument("op", choices=["add", "list", "show", "verify", "rm"])
    s.add_argument("what", nargs="?", help="add はファイルパス、他は登録名")
    s.add_argument("--name", help="add: 登録名（既定はファイル名）")
    s.add_argument("--format", default="standard")
    s.add_argument("--description")
    s.add_argument("--brief", action="store_true", help="show: メインの名前・枚数と警告のみ")
    s.add_argument("--quiet", action="store_true",
                   help="add: 中身を伏せ、枚数と適否だけ出す（対人モードで自分側を登録するとき）")
    relations.add_parser(sub)
    bookkeeping.add_parser(sub)
    return ap


def other_seat(pid):
    return "P2" if pid == "P1" else "P1"


def audit(args, kind, target, allowed, why=""):
    """隠匿情報を読もうとした記録を、状態ファイルとは別に残す。

    undo 履歴や盤面を汚さずに「誰がどちらの手札を見たか」だけを積む。
    AI同士で回すとき、覗いていないことを事後に示せるのはこれだけ。
    """
    path = state_path(args).parent / "audit.jsonl"
    rec = {"at": datetime.datetime.now(datetime.timezone.utc)
                    .replace(microsecond=0).isoformat(),
           "seat": getattr(args, "seat", None) or "-",
           "state": str(state_path(args)),
           "cmd": kind, "target": target, "allowed": allowed}
    if why:
        rec["why"] = why
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    except OSError:
        pass          # 監査が書けないことでゲームを止めない


# 隠匿情報を読む／漏らしうるコマンドと、その「対象の席」の取り出し方。
def hidden_reads(args):
    """(種類, 対象の席) の列を返す。空なら隠匿情報に触っていない。"""
    c = args.cmd
    if c in ("hand", "view"):
        return [(c, args.player)]
    if c == "look":
        return [("look", args.player)]
    if c == "zone":
        ref = args.zone
        if ":" in ref and ref.split(":", 1)[1] in ("hand", "library"):
            return [("zone", ref.split(":", 1)[0].upper())]
        return []
    if c == "show" and getattr(args, "hand", None):
        return [("show --hand", x) for x in (("P1", "P2") if args.hand == "both"
                                             else (args.hand,))]
    if c == "log":
        return [("log --all", "P1"), ("log --all", "P2")] if args.all             else [("log --player", args.player)]
    if c == "draw" and not getattr(args, "quiet", False):
        return [("draw", args.player)]
    if c in ("mulligan", "bottom") and not getattr(args, "quiet", False):
        return [(c, args.player)]
    return []


def enforce_seat(args):
    """--as が指定されているとき、相手の隠匿情報への参照を止める。"""
    seat = getattr(args, "seat", None)
    reads = hidden_reads(args)
    if not seat:
        for kind, target in reads:
            audit(args, kind, target, True, "席指定なし")
        return
    for kind, target in reads:
        ok = (target == seat)
        audit(args, kind, target, ok)
        if not ok:
            sys.exit("--as %s で実行中です。%s の隠匿情報は読めません（%s %s）。%s"
                     % (seat, target, kind, target,
                        "相手のドロー・マリガンは --quiet を付けてください。"
                        if kind in ("draw", "mulligan", "bottom") else ""))
    if args.cmd == "note" and getattr(args, "private", None)             and args.private != seat:
        sys.exit("--as %s では %s の秘匿メモを書けません。" % (seat, args.private))


def command_lines(text):
    """Keep source line numbers for both sequential batches and atomic parts."""
    for number, raw in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        line = raw.strip()
        if line and not line.startswith("#"):
            yield number, line


def command_words(line):
    words = shlex.split(line)
    if any(word in ("-h", "--help") for word in words):
        raise ValueError("ヘルプはバッチ外で実行してください")
    return words


def parse_file_command(words, globals_, parser):
    with contextlib.redirect_stderr(io.StringIO()) as diagnostic:
        try:
            return parse_command(globals_ + words, parser)
        except SystemExit as error:
            lines = diagnostic.getvalue().strip().splitlines()
            raise ValueError(lines[-1] if lines else str(error)) from None


def run_batch(args):
    """コマンドを1行1つ並べたファイル（または標準入力）をまとめて実行する。

    1手ごとに呼び出しを分けると、1ゲームで100回を超える。AI同士で何十戦も
    回すときはそこが律速になる。1行ごとに load/save するので undo の粒度は
    手打ちと同じまま。
    """
    if args.delta and not args.compact:
        sys.exit("--delta は --compact と併用してください。")
    if args.file == "-":
        # ロケール依存を避けてバイト列から UTF-8 で読む。
        # reconfigure 済みでも、呼ばれる前に読まれる経路があると化ける。
        raw = getattr(sys.stdin, "buffer", None)
        text = raw.read().decode("utf-8-sig") if raw else sys.stdin.read()
    else:
        text = pathlib.Path(args.file).read_text(encoding="utf-8-sig")
    gl = ["--state", args.state, "--cards-dir", args.cards_dir,
          "--decks-dir", args.decks_dir]
    if args.results:
        gl += ["--results", args.results]
    if args.offline:
        gl.append("--offline")
    if args.en:
        gl.append("--en")
    if args.seat:
        gl += ["--as", args.seat]
    # Parse the entire batch before any state change. This validates syntax,
    # not future game choices or the legality of actions against evolving state.
    parser = build_parser()
    prepared, errors = [], []
    for source_line, line in command_lines(text):
        try:
            parts = command_words(line)
            commands = [parts]
            if parts[0] == "pass-both":
                if len(parts) != 2 or parts[1] not in ("P1", "P2") or args.seat:
                    raise ValueError("pass-both P1|P2 は席制限なしのバッチ専用です")
                commands = [["pass", parts[1]], ["pass", "P2" if parts[1] == "P1" else "P1"]]
            for command in commands:
                if command[0] == "run":
                    raise ValueError("run は入れ子にできません")
                parsed = parse_file_command(command, gl, parser)
                if parsed.cmd == "phase" and parsed.op in ("to", "set") and parsed.value not in PHASES:
                    raise ValueError("不明なフェイズ: " + str(parsed.value))
                prepared.append((source_line, shlex.join(command) if parts[0] == "pass-both" else line, command))
        except ValueError as error:
            errors.append("%s:%d: %s" % (args.file, source_line, error))
    if errors:
        print("構文検査失敗（状態変更なし）\n" + "\n".join(errors))
        sys.exit(1)
    done = failed = 0
    baseline = load(args) if args.delta and state_path(args).exists() else None
    baseline_view = None
    transcript = None
    if args.compact:
        output_dir = state_path(args).parent / "output" / state_path(args).stem
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="run-", suffix=".jsonl",
                                         dir=output_dir, delete=False) as output_file:
            transcript = pathlib.Path(output_file.name)
    record_count = 0
    def record_output(record):
        nonlocal record_count
        record_count += 1
        record["source_line"] = source_line
        with transcript.open("a", encoding="utf-8") as output_file:
            output_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    for source_line, line, parts in prepared:
        if not (args.quiet or args.compact):
            print("» " + line)
        sys.stdout.flush()       # エラーは stderr に出るので、順序が入れ替わらないよう流す
        try:
            if args.compact:
                captured = io.StringIO()
                try:
                    with contextlib.redirect_stdout(captured):
                        dispatch(gl + parts)
                except BaseException as error:
                    record_output({"command": line, "stdout": captured.getvalue(),
                                   "error": str(error)})
                    print(captured.getvalue(), end="")
                    print("失敗した行: " + line)
                    print("詳細ログ: %s:1-%d" % (transcript, record_count))
                    raise
                raw_output = captured.getvalue()
                record_output({"command": line, "stdout": raw_output})
                if args.delta and parts[0] == "show":
                    # dispatch already enforced the seat boundary for this exact show.
                    show_args = build_parser().parse_args(gl + parts)
                    view_key = (show_args.hand, show_args.flat, show_args.next_oid)
                    if baseline is not None and baseline_view in (None, view_key):
                        with contextlib.redirect_stdout(io.StringIO()) as prior:
                            cmd_show(show_args, baseline)
                        print(compact_output.delta(prior.getvalue(), raw_output), end="")
                    else:
                        print(raw_output, end="")
                    baseline = load(args)
                    baseline_view = view_key
                else:
                    print(compact_output.compact(raw_output, parts), end="")
            else:
                dispatch(gl + parts)
            done += 1
        except SystemExit as e:
            code = e.code
            if code in (0, None):
                done += 1
                continue
            if isinstance(code, str):
                print(code)
            failed += 1
            if not args.keep_going:
                print("--- %d件実行して停止しました（続けるなら --keep-going）" % done)
                sys.exit(1)
    print("--- %d件実行%s" % (done, "、%d件失敗" % failed if failed else ""))
    if transcript:
        print("詳細ログ: %s:1-%d" % (transcript, record_count))


def parse_command(argv, parser=None):
    argv = list(argv)
    # "-1/-1" のような値はオプションと誤認されるので、印を付けて位置引数として通す
    # （handler 側で lstrip("~") して戻す。--until などのフラグは壊さない）
    for name in ("counter", "mod"):
        if name in argv:
            i = argv.index(name) + 2
            if i < len(argv) and re.match(r"^-\d", argv[i]):
                argv[i] = "~" + argv[i]

    parser = parser or build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "turn" and args.draw and (not args.to or args.to not in PHASES or PHASES.index(args.to) < PHASES.index("beginning.draw")):
        parser.error("--drawにはドロー以降の--toが必要: turn next --to precombat_main --draw")
    return args


def command_handlers():
    return {
        "init": cmd_init, "show": cmd_show, "hand": cmd_hand, "draw": cmd_draw,
        "zone": cmd_zone, "view": cmd_view,
        "move": cmd_move, "tap": cmd_tap, "untap": cmd_tap, "life": cmd_life,
        "loop": cmd_loop, "counter": cmd_counter, "damage": cmd_damage, "mod": cmd_mod,
        "mana": lambda a, s: mana.command(sys.modules[__name__], a, s), "stack": cmd_stack, "phase": cmd_phase, "turn": cmd_turn,
        "sba": cmd_sba, "card": cmd_card, "note": cmd_note, "undo": cmd_undo,
        "log": cmd_log,
        "shuffle": cmd_shuffle, "roll": cmd_roll, "coin": cmd_coin, "pick": cmd_pick,
        "search": cmd_search, "look": cmd_look, "mill": cmd_mill,
        "mulligan": cmd_mulligan, "bottom": cmd_bottom, "token": cmd_token,
        "attach": cmd_attach, "grant": cmd_grant, "pcounter": cmd_pcounter, "effect": cmd_effect,
        "attack": cmd_attack, "block": cmd_block, "combat": cmd_combat,
        "pass": cmd_pass, "end": cmd_end, "concede": cmd_concede,
        "stats": cmd_stats, "deck": cmd_deck,
        "glossary": cmd_glossary,
        "fx": lambda a, s: relations.cmd_fx(sys.modules[__name__], a, s),
        "linked": lambda a, s: relations.cmd_linked(sys.modules[__name__], a, s),
        "pending": lambda a, s: bookkeeping.cmd_pending(sys.modules[__name__], a, s),
        "remind": lambda a, s: bookkeeping.cmd_remind(sys.modules[__name__], a, s),
    }


def dispatch(argv):
    args = parse_command(argv)
    _CTX["dir"] = args.cards_dir
    _CTX["offline"] = args.offline
    _CTX["english"] = args.en
    if args.cmd == "run":
        run_batch(args)
        return
    readonly = {"show", "hand", "zone", "view", "sba", "log", "init", "undo",
                "look", "stats", "deck", "glossary"}
    if args.cmd == "sba" and (getattr(args, "apply", False)
                              or getattr(args, "apply_deaths", False)):
        readonly.discard("sba")     # 片付けをするときだけ書き戻す
    if args.cmd == "card" and args.op in ("show", "fetch"):
        readonly.add("card")
    if args.cmd in ("fx", "linked") and args.op in ("list", "check"):
        readonly.add(args.cmd)
    if args.cmd in ("pending", "remind") and args.op == "list":
        readonly.add(args.cmd)
    if args.cmd == "mana" and args.op == "list":
        readonly.add("mana")
    # card はカード情報の管理なので、対局が始まっていなくても使える。
    stateless = {"init", "undo", "stats", "deck"}
    # roll / coin / pick は先手決めのように対局前にも要る。状態ファイルがあれば
    # 従来どおり seed+連番でログに残し、無ければ --seed から振る。
    if args.cmd in ("card", "glossary", "roll", "coin", "pick"):
        st = load(args) if state_path(args).exists() else None
    else:
        st = None if args.cmd in stateless else load(args)
    if args.cmd in ("roll", "coin", "pick"):
        if st is None:
            if args.seed is None:
                sys.exit("状態ファイルがありません。対局前に振るなら --seed を指定して"
                         "ください（例: coin --seed 12345）。後から同じ結果を再現できます。")
            _CTX["pre_seed"], _CTX["pre_seq"] = args.seed, args.seq
        elif args.seed is not None:
            print("!! --seedは無視（対局中のseed・連番を使用）。")
    enforce_seat(args)
    if st is None and args.cmd == "card" and (args.local or args.oid):
        sys.exit("--local / --oid はこの対局の盤面に対する上書きなので、対局中にのみ使えます。")
    bookkeeping.guard(args, st)
    command_handlers()[args.cmd](args, st)
    if args.cmd not in readonly and st is not None:
        save(args, st)


def main():
    dispatch(sys.argv[1:])


if __name__ == "__main__":
    main()
