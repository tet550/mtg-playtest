"""コマンドライン入口。`python -m mtgtable <command> ...`

AI も人間も同じ操作系（Operation の Batch）で卓を動かす（26節）。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

from . import __version__, carddb, info, paths, play
from .engine import public_result
from .model import INFO_POLICIES
from .operations import OperationError, summarize_op
from .render import card_name, render_view, strip_names
from .setup import check_deck, load_decklist, new_game
from .store import GameStore

DB = None  # --db / 環境変数 MTGTABLE_DB: 対局を SQLite の DB に置く（GAME はパスの最後の名前を対局の id にする）


def _store(game):
    """GAME（対局フォルダのパス）の保存先。--db があれば、その DB の中の同じ名前の対局。"""
    if DB:
        from .sqlstore import SqliteGameStore
        try:
            return SqliteGameStore(DB, pathlib.Path(game).name)
        except ValueError as e:
            raise SystemExit(str(e))
    return GameStore(game)


def _utf8():
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _actor(value):
    return None if value in (None, "judge", "none", "all") else value


def _pairs(values, what):
    out = {}
    for v in values or []:
        if "=" not in v:
            raise SystemExit("%s must look like p1=VALUE, got %r" % (what, v))
        k, _, val = v.partition("=")
        out[k.strip()] = val.strip()
    return out


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def _emit_result(res):
    """apply の結果。Act は1つ1行（Act が多いので、字下げで量を増やさない）。"""
    head = {k: v for k, v in res.items() if k != "acts"}
    print(json.dumps(head, ensure_ascii=False)[:-1] + ', "acts": [')
    lines = [" " + json.dumps(x, ensure_ascii=False) for x in res["acts"]]
    print(",\n".join(lines))
    print("]}")


def _zone_flags(a):
    full = getattr(a, "full", False)
    return {"sideboard": full or getattr(a, "sideboard", False), "library": full or getattr(a, "library", False),
            "graveyard": full or getattr(a, "graveyard", False)}


def _view(state, actor, as_json, oracle=False, names=True, **zones):
    v = info.player_view(state, actor, names=names, **zones)
    if oracle and names:
        names = set()
        for zv in v["zones"].values():
            for key in ("cards", "known", "known_positions", "known_unordered"):
                names.update(c["name"] for c in zv.get(key, []) if "name" in c)
        v["oracle"] = {}
        for n in sorted(names):
            rec = carddb.lookup(n, offline=True)
            if rec:
                v["oracle"][n] = carddb.format_card(rec)
    if as_json:
        _emit(v)
    else:
        print(render_view(v))
        for n, text in v.get("oracle", {}).items():
            print("\n## " + text)


# ---------------------------------------------------------------- commands

def _deck(spec):
    """デッキリスト（ファイルか同梱のデッキの名前）。見つからなければ、同梱のデッキの名前を添えて終わる。"""
    try:
        return load_decklist(spec)
    except FileNotFoundError as e:
        raise SystemExit(str(e))


def cmd_new(a):
    decks = {}
    for pid, path in _pairs(a.deck, "--deck").items():
        decks[pid] = _deck(path)
    if not decks:
        raise SystemExit("give at least one --deck p1=PATH")
    for deck in decks.values():
        try:
            check_deck(deck)
        except ValueError as e:
            raise SystemExit(str(e))
    # デッキのオラクルを束ねたキャッシュを用意する（無いカードだけ取りに行く）。
    # タイプ行はカードに持たせ、表示の並べ替え（土地を前に）と召喚酔いの表示に使う
    caches = {pid: carddb.build_deck_cache(deck, fetch=not a.offline) for pid, deck in decks.items()}
    type_lines = {name: rec.get("type_line", "") for c in caches.values() for name, rec in c["cards"].items()}
    try:
        state = new_game(decks, seed=a.seed, life=a.life, first=a.first, hand=a.hand,
                         policies=_pairs(a.policy, "--policy"), type_lines=type_lines)
    except ValueError as e:
        raise SystemExit(str(e))
    for pid, deck in decks.items():
        state.meta["decks"][pid]["oracle"] = str(carddb.deck_cache_path(deck))
        state.meta["decks"][pid]["oracle_missing"] = caches[pid]["missing"]
    _store(a.game).create(state, overwrite=a.force)
    for pid, d in state.meta["decks"].items():
        print("%s: %s (%d cards, sideboard %d)" % (pid, d["name"], d["main"], d["sideboard"]))
        print("    oracle: %s%s" % (d["oracle"], "  missing: " + ", ".join(d["oracle_missing"])
                                   if d["oracle_missing"] else ""))
    print("created %s (seed %d, first %s%s)" % (a.game, a.seed, state.turn.active, "" if a.first else " by coin flip"))


def cmd_view(a):
    _view(_store(a.game).load(), _actor(a.as_), a.json, a.oracle, not a.no_names, **_zone_flags(a))


def cmd_apply(a):
    store = _store(a.game)
    text = sys.stdin.read() if a.file in (None, "-") else pathlib.Path(a.file).read_text(encoding="utf-8")
    try:
        batch = json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit("batch is not valid JSON: %s" % e)
    if a.as_ is not None:
        if isinstance(batch, dict):
            if "actor" in batch and batch["actor"] != _actor(a.as_):
                raise SystemExit("--as %s conflicts with actor %r in the batch" % (a.as_, batch["actor"]))
            batch = {**batch, "actor": _actor(a.as_)}
    if isinstance(batch, dict) and batch.get("actor") is not None and play.seats(store):
        # 鍵の対局（審判のいる GUI の対局）では、席は卓を動かさない（依頼・回答は answer、審判は --as judge）
        raise SystemExit("%s has seat keys; a seat writes only requests (prompt / answer). "
                         "Write board changes as the judge (--as judge)" % a.game)
    try:
        result = store.apply(batch)
    except OperationError as e:
        raise SystemExit("error: %s" % e)
    state = result.pop("_state")
    # learned（新しく知ったカード）は --no-names でも名前を残す。id と名前の対応を知る唯一の機会なので
    _emit_result(public_result(result))
    if a.view:
        _view(state, result["actor"], a.json, names=not a.no_names, **_zone_flags(a))
    if result["stopped"] and result["stopped"]["reason"] in ("failed", "precondition_failed"):
        sys.exit(2)


def cmd_undo(a):
    try:
        print("cursor -> %d" % _store(a.game).undo(a.n, to=a.to))
    except ValueError as e:
        raise SystemExit(str(e))


def cmd_redo(a):
    print("cursor -> %d" % _store(a.game).redo(a.n))


def _entry_text(e) -> str:
    text = e["label"] or "; ".join(summarize_op(op) for op in e["act"])
    if e.get("proc"):
        text = "[%s] %s" % (e["proc"], text)
    if e.get("proxy_by"):
        text = "[proxy by %s] %s" % (e["proxy_by"], text)
    return text


def _log_batches(entries, cur, last):
    """Batch ごとに1行（報告用）。Batch のラベルが無ければ各 Act の表示をつなげる。"""
    batches = []
    for e in entries:
        if batches and e.get("batch") is not None and batches[-1][0].get("batch") == e.get("batch"):
            batches[-1].append(e)
        else:
            batches.append([e])
    for b in batches[-last:] if last else batches:
        mark = " " if b[-1]["seq"] <= cur else "~"
        actors = []
        for e in b:
            if (e["actor"] or "judge") not in actors:
                actors.append(e["actor"] or "judge")
        seqs = "%d" % b[0]["seq"] if len(b) == 1 else "%d-%d" % (b[0]["seq"], b[-1]["seq"])
        text = b[0].get("batch_label") or " / ".join(_entry_text(e) for e in b)
        print("%s%9s v%-4d %-5s %s" % (mark, seqs, b[-1]["version"], ",".join(actors), text))


def cmd_log(a):
    store = _store(a.game)
    cur = store.cursor()
    entries = store.read_log()
    if a.batches is not None:
        _log_batches(entries, cur, a.batches)
        return
    shown = entries[-a.last:] if a.last else entries
    for e in shown:
        mark = " " if e["seq"] <= cur else "~"  # ~ = Undo 済み（Redo できる）
        if e.get("batch_label"):
            print("     B%-4d %s" % (e["batch"], e["batch_label"]))
        text = _entry_text(e)
        if e["seq"] > 1 and entries[e["seq"] - 2].get("cont"):
            text = "… " + text  # 前の件から続く Act のパート
        if e.get("cont"):
            text += " …"
        print("%s%4d v%-4d %-5s %s" % (mark, e["seq"], e["version"], e["actor"] or "judge", text))
        for st in e["steps"] if (a.steps or a.events) else []:
            if a.steps:
                print("          > %s%s" % (summarize_op(st["op"]), " (%s)" % st["parent"] if st.get("parent") else ""))
            if a.events:
                for ev in st["events"]:
                    print("            " + (strip_names(ev) if a.no_names else ev))










def cmd_ids(a):
    """id とカード名の対応を引く（名前無しの view を使うときの照会用）。"""
    state = _store(a.game).load()
    viewer = _actor(a.as_)
    if a.ids:
        cids = []
        for cid in a.ids:
            cid = cid if cid.startswith("#") else "#" + cid
            if cid not in state.cards:
                raise SystemExit("unknown card %s" % cid)
            cids.append(cid)
    else:
        cids = [cid for cid, c in state.cards.items()
                if state.zones[c.zone].kind in ("hand", "battlefield", "stack", "exile", "command", "graveyard")]
    for cid in cids:
        c = state.cards[cid]
        if info.knows_identity(state, viewer, cid):
            print("%s %s (%s)" % (cid, card_name(c.name), c.zone))
        elif a.ids:
            print("%s ? (not known to %s)" % (cid, viewer or "judge"))




def _known_card_names(state, viewer, ids):
    """viewer が中身を知っているカードの名前。ids 指定時はその中から（知らないカードは拒否）、
    省略時はゲーム内（サイドボード以外）で知っているカード全部。"""
    if ids:
        cids = []
        for cid in ids:
            cid = cid if cid.startswith("#") else "#" + cid
            if cid not in state.cards:
                raise SystemExit("unknown card %s" % cid)
            if not info.knows_identity(state, viewer, cid):
                raise SystemExit("card %s is not known to %s" % (cid, viewer or "judge"))
            cids.append(cid)
    else:
        cids = [cid for cid, c in state.cards.items()
                if state.zones[c.zone].kind != "sideboard" and info.knows_identity(state, viewer, cid)]
    names = []
    for cid in cids:
        if state.cards[cid].name not in names:
            names.append(state.cards[cid].name)
    return names


def cmd_oracle(a):
    if a.deck:
        d = _deck(a.deck)
        data = carddb.build_deck_cache(d, fetch=not a.offline, refresh=a.refresh)
        print(carddb.format_deck(data, sideboard=not a.no_sideboard, brief=a.brief))
        sys.exit(1 if data["missing"] else 0)
    names = list(a.names)
    if a.game:
        state = _store(a.game).load()
        names += _known_card_names(state, _actor(a.as_), a.card)
    if not names:
        raise SystemExit("give card names, --deck FILE, or --game GAME [--card ID ...]")
    status = 0
    for n in names:
        try:
            rec = carddb.lookup(n, offline=a.offline, refresh=a.refresh)
        except (LookupError, OSError) as e:
            print("%s: %s" % (n, e), file=sys.stderr)
            status = 1
            continue
        if rec is None:
            print("%s: not cached" % n, file=sys.stderr)
            status = 1
        elif a.json:
            _emit(rec)
        else:
            print(carddb.format_card_compact(rec, brief=a.brief))
    sys.exit(status)




def cmd_serve(a):
    from .web import serve
    db = _site_db()
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print("注意: %s で待ち受けている。通信は暗号化されない。ホスト（デッキ・対局を作る・履歴）は手元のブラウザ"
              "（127.0.0.1）からだけで、外からは招待の URL・席の鍵でその席だけ" % a.host, file=sys.stderr)
    serve(db, a.host, a.port, offline=a.offline)


def _site_db() -> str:
    """サイトの DB（--db か環境変数 MTGTABLE_DB。無ければデータ用フォルダの data/site.sqlite）。"""
    if DB:
        return DB
    path = paths.site_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _existing(game):
    store = _store(game)
    if not store.exists():
        raise SystemExit("no game at %s (python -m mtgtable new %s --deck p1=... で作る)" % (game, game))
    return store




def _prompt_build(store, role, error=None):
    from . import prompt
    try:
        return prompt.build(store, role, error=error)
    except ValueError as e:
        raise SystemExit(str(e))


def _print_prompt(built):
    who = "審判" if built["role"] == "judge" else "%s（%s）" % (built["role"], built["mode"])
    if built["cc"]:
        # Claude Code のエージェント: 固定部分（claude-code.md）は最初に1回だけ読み、毎回は user だけ読む
        print("prompt for %s" % who)
        print("  system: %s" % built["system"])
        print("  user:   %s" % built["user"])
    else:
        print("prompt for %s: %s" % (who, built["latest"]))


def _print_body(built):
    """Claude Code のエージェントに本文も出す（ファイルを読む1回を省く）。届けた盤面を、次の差分の元にする。"""
    from . import prompt
    print("----- %s の本文（Read しない）-----" % built["user"].name)
    print(built["user"].read_text(encoding="utf-8").rstrip())
    prompt.delivered(built)


def _print_compact(res):
    """Claude Code のエージェント向けの apply の結果: 全部適用できたら1行（盤面は次の本文に出る）。止まったら全部。"""
    if res["stopped"]:
        _emit_result(res)
    else:
        print("applied %d acts (v%d)" % (res["applied"], res["version"]))


def cmd_prompt(a):
    """プロンプトをファイルに書き出す（手動でモデルに渡す。latest.md をそのまま貼る）。"""
    store = _existing(a.game)
    role = "judge" if a.judge else a.as_
    if not role:
        raise SystemExit("give --as pN or --judge")
    built = _prompt_build(store, role)
    if built["waiting_on"] != role:
        print("note: 今待たれているのは %s（%s の番ではない）" % (built["waiting_on"] or "-", role))
    _print_prompt(built)


def _gui_games():
    """サイトの DB の、GUI の人間がいる（席の文書 "seats" がある）、決着していない対局。"""
    from .sqlstore import SqliteGames
    source = SqliteGames(_site_db())
    for gid in source.ids():
        st = source.open(gid)
        try:
            if play.seats(st) and play.waiting_on(st.load()) is not None:
                yield st
        except (OSError, LookupError, ValueError):
            continue


def _next_any(a):
    """対局を指定しない next: どれかの対局で審判か AI の席（鍵の無い席）の番が来るまで待つ（サイトで作った対局を
    Claude Code が拾う）。来たら game: と、answer に使うコマンドの頭を出してから、プロンプトを書き出す。"""
    import time
    from . import prompt
    deadline = time.monotonic() + a.timeout
    while True:
        for store in _gui_games():
            if prompt.leased(store):  # Claude Code のエージェントが受け持っている（自分で待っている）
                continue
            prompt.advance(store)
            role = prompt.next_role(store, prompt.ai_seats(store))
            if role:
                if a.claude_code:
                    prompt.write_claude_code(store)
                # CLI で対局を指す形（answer・log・oracle などに使う）: サイトの DB と対局の id
                head, target = "python -m mtgtable --db %s" % _site_db(), store.name
                print("game: %s <コマンド> %s" % (head, target))
                # この対局を受け持つエージェントが、自分で待つコマンド（起動するまでの間にほかの監視が拾わないよう、印も付ける）
                print("wait: %s next %s %s --claude-code --body --timeout 240"
                      % (head, target, " ".join("--ai %s" % p for p in prompt.ai_seats(store))))
                prompt.touch_lease(store)
                built = _prompt_build(store, role)
                _print_prompt(built)
                if a.body and built["cc"]:
                    _print_body(built)
                return
        if time.monotonic() > deadline:
            print("timeout: 審判か AI の席の番の対局は無い")
            sys.exit(3)
        time.sleep(1.0)


def cmd_next(a):
    """審判か AI の席の番が来るまで待ち、その役割のプロンプトを書き出す（人間の番の間は待つ）。"""
    from . import prompt
    if not a.game:
        return _next_any(a)
    store = _existing(a.game)
    ai = a.ai or []
    if a.claude_code:
        prompt.write_claude_code(store)
    if a.full:
        prompt.forget_delivered(store)
    _wait_role(store, ai, a.timeout, body=a.body)


def _wait_role(store, ai, timeout, body=False):
    """審判か AI の席の番が来るまで待ち、そのプロンプトを書き出す（next と answer --wait）。時間切れは終了コード 3。"""
    import time
    from . import play, prompt
    deadline = time.monotonic() + timeout
    touched = 0.0
    while True:
        if body and time.monotonic() - touched > 30:  # 待っているエージェントがこの対局を受け持っている印
            prompt.touch_lease(store)
            touched = time.monotonic()
        role = prompt.next_role(store, ai)
        if role:
            break
        wait = play.waiting_on(store.load())
        if wait is None:
            print("決着（待っている Player はいない）")
            return
        if time.monotonic() > deadline:
            print("timeout: %s を待っている" % wait)
            sys.exit(3)
        time.sleep(0.5)
    built = _prompt_build(store, role)
    _print_prompt(built)
    if body and built["cc"]:
        _print_body(built)


def cmd_answer(a):
    """Claude の返答（JSON のコード・ブロック）を、最後に作ったプロンプトの役割（審判か AI の席）として卓に書く。"""
    from . import play, prompt
    store = _existing(a.game)
    text = sys.stdin.read() if a.file in (None, "-") else pathlib.Path(a.file).read_text(encoding="utf-8")
    role = "judge" if a.judge else a.as_
    try:
        out = prompt.apply_response(store, text, role)
    except (ValueError, FileNotFoundError) as e:
        raise SystemExit(str(e))
    cc = prompt.claude_code_path(store).exists()
    if cc:
        prompt.touch_lease(store)
    if out["result"]:
        (_print_compact if cc else _emit_result)(public_result(out["result"]))
    if out["error"]:
        print("failed: %s" % out["error"])
    nxt = out["next"] or (prompt.auto_build(store) if out["ok"] else None)
    if nxt:
        if not out["ok"]:
            print("失敗の理由を付けてプロンプトを作り直した")
        _print_prompt(nxt)
        if nxt["cc"]:  # Claude Code のエージェントは続けて答える: ファイルを読み直す1回を省くため、本文もここに出す
            _print_body(nxt)
    elif play.waiting_on(store.load()) is None:
        print("決着（待っている Player はいない）")
    elif a.wait is not None and out["ok"]:  # 人間の番: next を呼び直す1回を省き、ここで待つ
        print("waiting on %s（人間の番。GUI で操作する）" % play.waiting_on(store.load()))
        _wait_role(store, prompt.ai_seats(store), a.wait, body=cc)
    else:
        print("waiting on %s（人間の番。GUI で操作する）" % play.waiting_on(store.load()))
    if not out["ok"]:
        sys.exit(2)




def cmd_deck(a):
    d = _deck(a.file)
    print("%s: main %d, sideboard %d" % (d.name, d.main_count, sum(n for n, _ in d.sideboard)))
    data = carddb.build_deck_cache(d, fetch=a.fetch, refresh=a.refresh)
    print("oracle cache: %s (%d cards%s)" % (carddb.deck_cache_path(d), len(data["cards"]),
                                             ", missing: " + ", ".join(data["missing"]) if data["missing"] else ""))
    for name, err in data["errors"].items():
        print("  %s: %s" % (name, err))
    if data["missing"] and not a.fetch:
        print("  (--fetch で Scryfall から取得)")
    if a.images:
        errors = carddb.fetch_deck_images(d)
        print("images: %s%s" % (carddb.cache_dir() / "images",
                                "  failed: " + ", ".join("%s (%s)" % kv for kv in errors.items()) if errors else ""))


def build_parser():
    ap = argparse.ArgumentParser(prog="mtgtable", description="AI が紙の MTG をプレイするためのデジタル卓")
    ap.add_argument("--version", action="version", version="mtgtable %s" % __version__)
    ap.add_argument("--db", default=os.environ.get("MTGTABLE_DB") or None,
                    help="対局を SQLite の DB に置く（既定は環境変数 MTGTABLE_DB。無ければ対局フォルダ）。"
                         "GAME はパスの最後の名前を対局の id にする。serve・対局を省いた next は、無ければサイトの DB "
                         "（データ用フォルダの data/site.sqlite）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="対局を作る")
    p.add_argument("game")
    p.add_argument("--deck", action="append", help="p1=decklists/piza.txt か、同梱のデッキの名前 p1=piza（1つなら一人回し）")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--life", type=int, default=20)
    p.add_argument("--first", help="先攻の Player（既定は seed で無作為に決める）")
    p.add_argument("--hand", type=int, default=7, help="初期手札の枚数（0 で引かない）")
    p.add_argument("--policy", action="append", help="p1=%s" % "|".join(INFO_POLICIES))
    p.add_argument("--offline", action="store_true", help="オラクルのキャッシュに無いカードを取りに行かない")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("view", help="Player View を表示する")
    p.add_argument("game")
    p.add_argument("--as", dest="as_", default="judge", help="p1 / p2 / judge")
    p.add_argument("--json", action="store_true")
    p.add_argument("--oracle", action="store_true", help="見えているカードのオラクル（キャッシュ済みのみ）も出す")
    p.add_argument("--sideboard", action="store_true", help="サイドボード（ゲーム外）も出す")
    p.add_argument("--library", action="store_true", help="ライブラリーの既知のカードも出す（既定は枚数だけ）")
    p.add_argument("--graveyard", action="store_true", help="墓地の中身も出す（既定は枚数だけ）")
    p.add_argument("--full", action="store_true", help="サイドボード・ライブラリー・墓地を全部出す")
    p.add_argument("--no-names", action="store_true", help="カード名を出さない（id だけ）。照会は ids コマンド")
    p.set_defaults(fn=cmd_view)

    p = sub.add_parser("apply", help="Operation の Batch（JSON）を適用する")
    p.add_argument("game")
    p.add_argument("file", nargs="?", help="JSON ファイル（省略か - で標準入力）")
    p.add_argument("--as", dest="as_", help="操作する Player（batch の actor を上書き。judge で全知）")
    p.add_argument("--view", action="store_true", help="適用後の Player View も出す")
    p.add_argument("--json", action="store_true", help="--view を JSON で")
    p.add_argument("--sideboard", action="store_true", help="--view にサイドボードも出す")
    p.add_argument("--library", action="store_true", help="--view にライブラリーの既知のカードも出す")
    p.add_argument("--graveyard", action="store_true", help="--view に墓地の中身も出す")
    p.add_argument("--full", action="store_true", help="--view に全部出す")
    p.add_argument("--no-names", action="store_true", help="--view にカード名を出さない（learned の名前は残る）")
    p.set_defaults(fn=cmd_apply)

    for name, fn in (("undo", cmd_undo), ("redo", cmd_redo)):
        p = sub.add_parser(name, help="Act 単位で%s" % ("戻す" if name == "undo" else "やり直す"))
        p.add_argument("game")
        p.add_argument("n", nargs="?", type=int, default=1)
        if name == "undo":
            p.add_argument("--to", type=int, help="log の N 件目の直後まで戻す（割り込みの巻き戻し。Act の途中なら、その Act の始まりまで）")
        p.set_defaults(fn=fn)

    p = sub.add_parser("log", help="Operation Log")
    p.add_argument("game")
    p.add_argument("--last", type=int)
    p.add_argument("--batches", type=int, nargs="?", const=0, metavar="N",
                   help="Batch ごとに1行で出す（N で最後の N 個。報告用）")
    p.add_argument("--steps", action="store_true", help="実際に適用した基本の op も出す（複合 op を展開したもの）")
    p.add_argument("--events", action="store_true", help="全情報のイベントも出す（観戦・デバッグ用）")
    p.add_argument("--no-names", action="store_true", help="イベントにカード名を出さない")
    p.set_defaults(fn=cmd_log)

    p = sub.add_parser("ids", help="id とカード名の対応（知っているカードだけ）")
    p.add_argument("game")
    p.add_argument("ids", nargs="*", help="#c12 など。省略で手札・戦場・スタック・墓地・追放の知っているカード全部")
    p.add_argument("--as", dest="as_", default="judge")
    p.set_defaults(fn=cmd_ids)

    p = sub.add_parser("oracle", help="カードのオラクル・テキスト（Scryfall キャッシュ）")
    p.add_argument("names", nargs="*", help="カード名（英語）")
    p.add_argument("--deck", help="デッキリストの全カードを1枚の一覧で（デッキのキャッシュから）")
    p.add_argument("--no-sideboard", action="store_true", help="--deck でサイドボードを省く")
    p.add_argument("--brief", action="store_true", help="注釈文（括弧内の説明）を省く")
    p.add_argument("--game", help="対局の中のカード。--card で id 指定、省略で --as が知っているカード全部")
    p.add_argument("--as", dest="as_", default="judge")
    p.add_argument("--card", action="append", help="--game と一緒に。#c12 など（複数可）")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_oracle)

    p = sub.add_parser("serve", help="手元のサイトを立てる（デッキの登録・対局を作る（AI と・招待した人と）・対局・履歴）。"
                                     "審判と AI の席は Claude Code が回す（next の監視と mtg-table エージェント）")
    p.add_argument("--host", default="127.0.0.1", help="待ち受けるアドレス（別の PC のゲストを招くなら 0.0.0.0。通信は暗号化されない）")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--offline", action="store_true", help="キャッシュに無いカード画像を取りに行かない")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("next", help="審判か AI の席の番まで待ち、そのプロンプトを書き出す")
    p.add_argument("game", nargs="?",
                   help="対局（省略で、サイトの DB の対局のどれか。AI の席は鍵の無い席）")
    p.add_argument("--ai", action="append", help="AI の席（複数可）。審判の番は常にプロンプトを作る")
    p.add_argument("--timeout", type=float, default=540, help="秒（時間切れは終了コード 3）")
    p.add_argument("--claude-code", action="store_true",
                   help="Claude Code のエージェント1つが審判と AI の席を兼ねる固定部分を prompts/claude-code.md に書き出す")
    p.add_argument("--body", action="store_true",
                   help="プロンプトの本文も出す（エージェントが自分で待つとき。ファイルを読む1回を省く）。対局を受け持つ印も付ける")
    p.add_argument("--full", action="store_true",
                   help="前に届けた盤面・オラクルの控えを消し、全部を送る（エージェントを起動し直したとき）")
    p.set_defaults(fn=cmd_next)

    p = sub.add_parser("prompt", help="プロンプトを今すぐ書き出す")
    p.add_argument("game")
    p.add_argument("--as", dest="as_", help="AI の席")
    p.add_argument("--judge", action="store_true", help="審判のプロンプト")
    p.set_defaults(fn=cmd_prompt)

    p = sub.add_parser("answer", help="Claude の返答を、最後に作ったプロンプトの役割として卓に書く")
    p.add_argument("game")
    p.add_argument("file", nargs="?", help="返答のテキスト（省略か - で標準入力）")
    p.add_argument("--as", dest="as_", help="役割を指定する（既定は最後に作ったプロンプトの役割）")
    p.add_argument("--judge", action="store_true", help="審判の返答として")
    p.add_argument("--wait", type=float, metavar="SECONDS",
                   help="人間の番になったら、審判か AI の席（鍵の無い席）の番まで待ってプロンプトを出す（next を呼び直す1回を省く。時間切れは終了コード 3）")
    p.set_defaults(fn=cmd_answer)

    p = sub.add_parser("deck", help="デッキリストを読んで枚数を確かめる")
    p.add_argument("file")
    p.add_argument("--fetch", action="store_true", help="キャッシュに無いカードを Scryfall から取得する")
    p.add_argument("--refresh", action="store_true", help="キャッシュを無視して取り直す")
    p.add_argument("--images", action="store_true", help="カード画像も取っておく（画面用）")
    p.set_defaults(fn=cmd_deck)
    return ap


def main(argv=None):
    _utf8()
    global DB
    a = build_parser().parse_args(argv)
    DB = a.db
    a.fn(a)


if __name__ == "__main__":
    main()
