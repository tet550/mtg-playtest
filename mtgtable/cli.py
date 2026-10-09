"""コマンドライン入口。`python -m mtgtable <command> ...`

AI も人間も同じ操作系（Operation の Batch）で卓を動かす（26節）。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

from . import carddb, info, play
from .engine import public_result
from .model import INFO_POLICIES
from .operations import OperationError, describe_operations, summarize_op
from .procedures import describe_procedures
from .render import card_name, render_view, strip_names
from .setup import check_deck, load_decklist, new_game
from .store import GameStore, state_diff

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

def cmd_new(a):
    decks = {}
    for pid, path in _pairs(a.deck, "--deck").items():
        decks[pid] = load_decklist(path)
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


def cmd_replay(a):
    state = _store(a.game).replay(a.to)
    if a.json:
        _emit(state.to_dict())
    else:
        _view(state, _actor(a.as_), False, names=not a.no_names, **_zone_flags(a))


def cmd_diff(a):
    store = _store(a.game)
    before = store.replay(a.from_)
    after = store.replay(a.to if a.to is not None else store.cursor())
    for path, x, y in state_diff(before, after):
        if path.startswith("knowledge") and not a.all:
            continue
        print("%s: %s -> %s" % (path, json.dumps(x, ensure_ascii=False), json.dumps(y, ensure_ascii=False)))


def cmd_fork(a):
    _store(a.game).fork(a.dest, a.at, overwrite=a.force)
    print("forked %s -> %s" % (a.game, a.dest))


def cmd_policy(a):
    try:
        policies = _store(a.game).set_policies(_pairs(a.set, "policy"))
    except ValueError as e:
        raise SystemExit(str(e))
    print(", ".join("%s=%s" % kv for kv in policies.items()))


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


def cmd_ops(a):
    for name, doc in describe_operations():
        print("%-15s %s" % (name, doc))
    print("\n# 手順（複数の Act になる。acts に {\"proc\": 名前, ...} で書く）")
    for name, doc in describe_procedures():
        print("%-15s %s" % (name, doc))


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
        d = load_decklist(a.deck)
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


def cmd_export(a):
    from .web import export_site
    games = export_site(a.root, a.dest, a.game or None, offline=a.offline, db=DB)
    print("exported %d game(s) to %s: %s" % (len(games), a.dest, ", ".join(games)))


def cmd_serve(a):
    from .web import serve
    from . import llm
    if a.site and not DB:
        raise SystemExit("serve --site needs --db PATH (or MTGTABLE_DB)")
    ai = (a.ai or a.site) and not a.no_ai
    if ai:
        if not (a.play or a.site):
            raise SystemExit("serve --ai needs --play or --site")
        try:
            llm.check_ready()  # キー・SDK が足りなければ、対局を中断させる前にここで止める
        except llm.LLMError as e:
            raise SystemExit("%s（AI を回さずに動かすなら --no-ai）" % e)
    serve(a.root, a.host, a.port, offline=a.offline, play=a.play, db=DB, site=a.site, ai=ai)


def cmd_db_import(a):
    """対局フォルダを --db の DB へ移す（初期状態・log・現在状態・席の鍵・非公開の設定・プロンプト）。"""
    if not DB:
        raise SystemExit("db-import needs --db PATH (or MTGTABLE_DB)")
    from .sqlstore import SqliteGameStore
    for game in a.games:
        src = GameStore(game)
        if not src.exists():
            raise SystemExit("no game at %s" % game)
        try:
            SqliteGameStore(DB, src.name).import_from(src, overwrite=a.force)
        except (ValueError, FileExistsError) as e:
            raise SystemExit(str(e))
        print("imported %s -> %s#%s" % (game, DB, src.name))


def cmd_invite(a):
    """GUI で席を持つ人の URL を作る（鍵は URL の # の後ろ。サーバーには送られず、ページが読んで消す）。"""
    from urllib.parse import quote
    from . import play
    store = _store(a.game)
    if not store.exists():
        raise SystemExit("no game at %s" % a.game)
    try:
        token = play.invite(store, a.seat, ttl=a.ttl or None)
    except ValueError as e:
        raise SystemExit(str(e))
    where = "--db %s" % DB if DB else "--root %s" % store.root.resolve().parent
    base = a.base or "http://127.0.0.1:%d/" % a.port
    print("%s?game=%s&seat=%s#key=%s" % (base, quote(store.name), a.seat, token))
    print("(serve --play %s で開く。鍵を作り直すと前の URL は使えなくなる)" % where, file=sys.stderr)


def cmd_revoke(a):
    from . import play
    store = _existing(a.game)
    if not play.revoke(store, a.seat):
        raise SystemExit("%s has no key for %s" % (a.game, a.seat))
    print("revoked the key of %s in %s (invite で作り直すまで、その席は誰も使えない)" % (a.seat, a.game))


def _existing(game):
    store = _store(game)
    if not store.exists():
        raise SystemExit("no game at %s (python -m mtgtable new %s --deck p1=... で作る)" % (game, game))
    return store


def cmd_wait(a):
    """相手（GUI の人間など）が書いて、自分の番が来るまで待つ。来た Act を log と同じ形で出す。"""
    from . import play
    store = _existing(a.game)
    got = play.wait(store, a.as_, since=a.since, timeout=a.timeout, any_change=a.any)
    if got is None:
        print("timeout (cursor %d)" % store.cursor())
        sys.exit(3)
    if got["undone"]:
        print("undone: cursor %d -> %d（巻き戻された）" % (got["from"], got["cursor"]))
    for e in got["entries"]:
        if e.get("batch_label"):
            print("     B%-4d %s" % (e["batch"], e["batch_label"]))
        print(" %4d v%-4d %-5s %s" % (e["seq"], e["version"], e["actor"] or "judge", _entry_text(e)))
    state = store.load()
    t = state.turn
    print("cursor %d  T%d %s/%s  active %s  priority %s  waiting on %s"
          % (got["cursor"], t.turn, t.phase, t.step, t.active, t.priority or "-", play.waiting_on(state) or "-"))
    if a.prompt and play.waiting_on(state) == a.as_:
        _print_prompt(_prompt_build(store, a.as_))


def _prompt_build(store, role, error=None, mode=None):
    from . import prompt
    try:
        return prompt.build(store, role, error=error, mode=mode)
    except ValueError as e:
        raise SystemExit(str(e))


def _print_prompt(built):
    who = "審判" if built["role"] == "judge" else "%s（%s）" % (built["role"], built["mode"])
    print("prompt for %s: %s  (request: %s)" % (who, built["latest"], built["request"].name))


def cmd_prompt(a):
    """プロンプトをファイルに書き出す（手動でモデルに渡す。request.json は API の本文）。"""
    store = _existing(a.game)
    role = "judge" if a.judge else a.as_
    if not role:
        raise SystemExit("give --as pN or --judge")
    built = _prompt_build(store, role, mode="direct" if a.direct else None)
    if built["waiting_on"] != role:
        print("note: 今待たれているのは %s（%s の番ではない）" % (built["waiting_on"] or "-", role))
    _print_prompt(built)


def cmd_next(a):
    """審判か AI の席の番が来るまで待ち、その役割のプロンプトを書き出す（人間の番の間は待つ）。"""
    import time
    from . import play, prompt
    store = _existing(a.game)
    ai = a.ai or []
    deadline = time.monotonic() + a.timeout
    while True:
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
    _print_prompt(_prompt_build(store, role, mode="direct" if a.direct and role != "judge" else None))


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
    if out["result"]:
        _emit_result(public_result(out["result"]))
    if out["error"]:
        print("failed: %s" % out["error"])
    nxt = out["next"] or (prompt.auto_build(store) if out["ok"] else None)
    if nxt:
        if not out["ok"]:
            print("失敗の理由を付けてプロンプトを作り直した")
        _print_prompt(nxt)
    else:
        print("waiting on %s（人間の番。GUI で操作する）" % (play.waiting_on(store.load()) or "-"))
    if not out["ok"]:
        sys.exit(2)


def cmd_auto(a):
    """審判と AI の席を LLM の API で回す（人間の番になるまで。--watch なら人間の操作を待ちながら決着まで）。"""
    import os
    from . import llm, prompt
    store = _existing(a.game)
    if a.model:
        os.environ["MTGTABLE_MODEL"] = a.model
    if a.provider:
        os.environ["MTGTABLE_LLM_PROVIDER"] = a.provider
    try:
        llm.check_ready()
        print("審判 %s/%s、AI の席 %s/%s。AI の席: %s（鍵の無い席）"
              % (llm.provider(prompt.JUDGE), llm.model(prompt.JUDGE), llm.provider("p"), llm.model("p"),
                 ", ".join(a.ai or prompt.ai_seats(store)) or "なし"))
    except llm.LLMError as e:
        raise SystemExit(str(e))
    try:
        print(llm.run(store, a.ai, watch=a.watch, max_failures=a.max_failures, server=a.server or None))
    except llm.LLMError as e:
        raise SystemExit("止めた: %s" % e)
    except KeyboardInterrupt:
        print("止めた（Ctrl+C）")


def cmd_deck(a):
    d = load_decklist(a.file)
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
    ap.add_argument("--db", default=os.environ.get("MTGTABLE_DB") or None,
                    help="対局を SQLite の DB に置く（既定は環境変数 MTGTABLE_DB。無ければ対局フォルダ）。"
                         "GAME はパスの最後の名前を対局の id にする")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="対局を作る")
    p.add_argument("game")
    p.add_argument("--deck", action="append", help="p1=decklists/piza.txt（1つなら一人回し）")
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

    p = sub.add_parser("replay", help="log の途中時点の状態を再現する")
    p.add_argument("game")
    p.add_argument("--to", type=int)
    p.add_argument("--as", dest="as_", default="judge")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-names", action="store_true")
    p.add_argument("--full", action="store_true", help="サイドボード・ライブラリー・墓地も出す")
    p.set_defaults(fn=cmd_replay)

    p = sub.add_parser("diff", help="log の2時点間の State Diff")
    p.add_argument("game")
    p.add_argument("--from", dest="from_", type=int, required=True)
    p.add_argument("--to", type=int)
    p.add_argument("--all", action="store_true", help="knowledge の差分も出す")
    p.set_defaults(fn=cmd_diff)

    p = sub.add_parser("fork", help="途中時点から別の対局を作る")
    p.add_argument("game")
    p.add_argument("dest")
    p.add_argument("--at", type=int)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_fork)

    p = sub.add_parser("policy", help="Information Policy を変える（一人回し・テスト用）")
    p.add_argument("game")
    p.add_argument("set", nargs="+", help="p1=omniscient など")
    p.set_defaults(fn=cmd_policy)

    p = sub.add_parser("ids", help="id とカード名の対応（知っているカードだけ）")
    p.add_argument("game")
    p.add_argument("ids", nargs="*", help="#c12 など。省略で手札・戦場・スタック・墓地・追放の知っているカード全部")
    p.add_argument("--as", dest="as_", default="judge")
    p.set_defaults(fn=cmd_ids)

    p = sub.add_parser("ops", help="Operation と手順の一覧")
    p.set_defaults(fn=cmd_ops)

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

    p = sub.add_parser("export", help="観戦ビューアを静的サイトに書き出す（GitHub Pages 用。judge の席だけ）")
    p.add_argument("dest", help="書き出し先のフォルダ")
    p.add_argument("--root", default="playtest", help="対局フォルダを置く場所")
    p.add_argument("--game", action="append", help="書き出す対局（複数可。省略で全部）")
    p.add_argument("--offline", action="store_true", help="キャッシュに無いカード情報を取りに行かない")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("serve", help="観戦ビューア（ブラウザで対局を見る。読み取り専用）")
    p.add_argument("--root", default="playtest", help="対局フォルダを置く場所")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--offline", action="store_true", help="キャッシュに無いカード画像を取りに行かない")
    p.add_argument("--play", action="store_true", help="席の操作（GUI の対局）を受ける。invite した対局は席の鍵で守る")
    p.add_argument("--site", action="store_true",
                   help="公開のサーバーとして動かす（--db が要る。--play を含む）。所有者の鍵（Cookie）で、自分が席を持つ対局だけを"
                        "見せ、judge の席は出さない。書き込みは同じサイトからだけ。審判と AI の席もサーバーの中で回す")
    p.add_argument("--ai", action="store_true",
                   help="審判と AI の席をサーバーの中で回す（--play と。auto --watch の代わり。--site では既定）")
    p.add_argument("--no-ai", action="store_true", help="--site でも AI を回さない（手で回す・auto を使う）")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("db-import", help="対局フォルダを --db の DB へ移す")
    p.add_argument("games", nargs="+", help="対局フォルダ")
    p.add_argument("--force", action="store_true", help="DB に同じ名前の対局があれば置き換える")
    p.set_defaults(fn=cmd_db_import)

    p = sub.add_parser("invite", help="GUI で席を持つための URL（鍵付き）を作る")
    p.add_argument("game")
    p.add_argument("--seat", required=True, help="人間が持つ席（p1 など）")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--base", help="サーバーの URL（既定 http://127.0.0.1:PORT/）")
    p.add_argument("--ttl", type=float, default=168,
                   help="鍵の有効期限（時間。既定 168 = 7日。0 で期限なし）。公開のサーバーでは、席を取った後は Cookie で入る")
    p.set_defaults(fn=cmd_invite)

    p = sub.add_parser("revoke", help="席の鍵を失効させる（invite で作り直すまで、その席は誰も使えない）")
    p.add_argument("game")
    p.add_argument("--seat", required=True)
    p.set_defaults(fn=cmd_revoke)

    p = sub.add_parser("wait", help="相手が書いて自分の番が来るまで待つ（GUI の人間との対局で AI が使う）")
    p.add_argument("game")
    p.add_argument("--as", dest="as_", required=True, help="待つ Player（AI の席）")
    p.add_argument("--since", type=int, help="この件数より後を待つ（省略で今の cursor）")
    p.add_argument("--timeout", type=float, default=540, help="秒（既定 540。時間切れは終了コード 3）")
    p.add_argument("--any", action="store_true", help="相手が1件書くごとに返す（既定は自分の番・宣言・決着まで待つ）")
    p.add_argument("--prompt", action="store_true", help="自分の番が来たら、プロンプトも書き出す")
    p.set_defaults(fn=cmd_wait)

    p = sub.add_parser("next", help="審判か AI の席の番まで待ち、そのプロンプトを書き出す（手動でモデルに渡す）")
    p.add_argument("game")
    p.add_argument("--ai", action="append", help="AI の席（複数可）。審判の番は常にプロンプトを作る")
    p.add_argument("--timeout", type=float, default=540, help="秒（時間切れは終了コード 3）")
    p.add_argument("--direct", action="store_true", help="AI の席は審判を通さず Batch を直接書く（前の形。鍵の対局では使えない）")
    p.set_defaults(fn=cmd_next)

    p = sub.add_parser("auto", help="審判と AI の席を LLM の API で回す（提供元・キーは secrets/README.md）")
    p.add_argument("game")
    p.add_argument("--ai", action="append", help="AI の席（省略で鍵の無い席）")
    p.add_argument("--watch", action="store_true", help="人間の番になっても終わらず、操作を待って決着まで回す")
    p.add_argument("--model", help="モデル（全部の役割。省略で secrets/llm.json → 提供元の既定）")
    p.add_argument("--provider", choices=("openai", "anthropic"), help="提供元（全部の役割。省略で secrets/llm.json → openai）")
    p.add_argument("--max-failures", type=int, default=3, help="返答を続けて適用できなかったら止める回数")
    p.add_argument("--server", default="http://127.0.0.1:8765",
                   help="--watch で更新通知を受けるサーバー（serve の URL）。つながらなければ1秒ごとに見る。空で通知を使わない")
    p.set_defaults(fn=cmd_auto)

    p = sub.add_parser("prompt", help="プロンプト（API に送る形）を今すぐ書き出す")
    p.add_argument("game")
    p.add_argument("--as", dest="as_", help="AI の席")
    p.add_argument("--judge", action="store_true", help="審判のプロンプト")
    p.add_argument("--direct", action="store_true", help="AI の席が Batch を直接書く形（審判を通さない。鍵の対局では使えない）")
    p.set_defaults(fn=cmd_prompt)

    p = sub.add_parser("answer", help="Claude の返答を、最後に作ったプロンプトの役割として卓に書く")
    p.add_argument("game")
    p.add_argument("file", nargs="?", help="返答のテキスト（省略か - で標準入力）")
    p.add_argument("--as", dest="as_", help="役割を指定する（既定は最後に作ったプロンプトの役割）")
    p.add_argument("--judge", action="store_true", help="審判の返答として")
    p.set_defaults(fn=cmd_answer)

    p = sub.add_parser("deck", help="デッキリストを読んで枚数を確かめる")
    p.add_argument("file")
    p.add_argument("--fetch", action="store_true", help="キャッシュに無いカードを Scryfall から取得する")
    p.add_argument("--refresh", action="store_true", help="キャッシュを無視して取り直す")
    p.add_argument("--images", action="store_true", help="カード画像も取っておく（観戦ビューア用）")
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
