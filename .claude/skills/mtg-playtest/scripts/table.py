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
import contextlib
import copy
import io
import pathlib
import re
import shlex
import sys

import aid
import cardcache
import decks
import table_commands as commands
from table_model import SEATS, Stop, TOKEN_PRESETS, seat_arg
from table_store import cmd_undo, load, save, state_path


LABEL_RE = re.compile(r"\$([a-z][a-z0-9_]*)")
FORBIDDEN_IN_RUN = {"init", "run", "undo", "end", "stats"}


def cmd_run(args, st):
    """行を上から実行し、全部成功したときだけ1回保存する（1手 = 1回の保存）。

    途中で止まったら出力も捨てる。適用していないドローや look の結果が見えると、
    ライブラリーの中身を覗いたのと同じになる。
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = _run_lines(args, st)
    print(buffer.getvalue(), end="")
    return result


def _run_lines(args, st):
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
        s = sub.add_parser(name, help="untap --all P1 でその席の戦場をまとめて起こす" if name == "untap" else None)
        s.add_argument("refs", nargs="*")
        s.add_argument("--all", metavar="SEAT")
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
    s.add_argument("symbols", nargs="*", help="RRG / G:12 / G:12 C")
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
    s.add_argument("-n", type=int, default=1)
    s.add_argument("--label")
    s = sub.add_parser("ability", help="スタックに能力の目印を置く")
    s.add_argument("seat")
    s.add_argument("text")
    s.add_argument("--src")
    s.add_argument("--label")
    s = sub.add_parser("note", help='付箋 note <oid> "内容" [--until eot] / note set N1 "内容" / note rm N1')
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
    "init": commands.cmd_init, "show": commands.cmd_show, "zone": commands.cmd_zone, "card": commands.cmd_card, "log": commands.cmd_log,
    "draw": commands.cmd_draw, "mill": commands.cmd_mill, "look": commands.cmd_look, "search": commands.cmd_search,
    "reveal": commands.cmd_reveal, "move": commands.cmd_move, "remove": commands.cmd_remove, "tap": commands.cmd_tap,
    "untap": commands.cmd_tap, "flip": commands.cmd_flip, "counter": commands.cmd_counter, "damage": commands.cmd_damage,
    "life": commands.cmd_life, "mana": commands.cmd_mana, "token": commands.cmd_token, "copy": commands.cmd_copy,
    "ability": commands.cmd_ability, "note": commands.cmd_note, "memo": commands.cmd_memo, "marker": commands.cmd_marker,
    "attach": commands.cmd_attach, "attack": commands.cmd_attack, "block": commands.cmd_block,
    "combat-clear": commands.cmd_combat, "turn": commands.cmd_turn, "phase": commands.cmd_phase,
    "shuffle": commands.cmd_shuffle, "mulligan": commands.cmd_mulligan, "roll": commands.cmd_roll, "coin": commands.cmd_coin,
    "pick": commands.cmd_pick, "say": commands.cmd_say, "aid": commands.cmd_aid, "run": cmd_run, "undo": cmd_undo,
    "end": commands.cmd_end, "stats": commands.cmd_stats,
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
