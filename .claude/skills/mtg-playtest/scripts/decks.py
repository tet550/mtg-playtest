#!/usr/bin/env python3
"""デッキリストの読み込み・登録・検証。

テキストのデッキリストを解釈し、**登録時に全カードを解決して検証する**。
表記揺れ・存在しないカード・4枚制限違反・枚数不足は、対局を始める前に潰しておきたい。
ゲームが始まってから気づくと、そのテストプレイの結論ごと使えなくなる。

保存形式の規定は references/deck-schema.md。

単体でも使える:
    python decks.py add burn.txt --name burn
    python decks.py list
    python decks.py show burn
    python decks.py verify burn
"""
import datetime
import json
import pathlib
import re
import sys

import cardcache

SCHEMA = "mtg-playtest/deck@2"
LEGACY_SCHEMAS = ("mtg-playtest/deck@1",)
DEFAULT_DIR = "decks"

# 1行の書式: 「4 稲妻」「4x Lightning Bolt」「4 Lightning Bolt (2XM) 129」
LINE = re.compile(r"^(\d+)\s*[xX]?\s+(.+?)$")
# Arena 形式の末尾に付くセット記号とコレクター番号を落とす
ARENA_TAIL = re.compile(r"\s*\((?:[A-Za-z0-9]{2,6})\)\s*[\w★-]*\s*$")

SECTIONS = {
    "main": ("deck", "main", "maindeck", "main deck", "デッキ", "メイン", "メインデッキ"),
    "sideboard": ("sideboard", "side", "sb", "サイドボード", "サイド"),
    "skip": ("commander", "companion", "統率者", "相棒"),
}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- 解釈

def parse_decklist(text):
    """テキストを (main, sideboard, warnings) に分ける。

    main/sideboard は [{"count": n, "name": str}] で、同名の行は合算する。
    解釈できない行は捨てずに warnings に残す（黙って無視すると枚数が合わなくなる）。
    """
    main, side, warnings = [], [], []
    target, skipping = main, False
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        head = re.sub(r"[:：]\s*$", "", line).casefold()
        if head in SECTIONS["sideboard"]:
            target, skipping = side, False
            continue
        if head in SECTIONS["main"]:
            target, skipping = main, False
            continue
        if head in SECTIONS["skip"]:
            skipping = True
            warnings.append("「%s」の節は構築戦の対象外なので読み飛ばしました。" % line)
            continue
        if skipping:
            continue
        m = LINE.match(line)
        if not m:
            warnings.append("解釈できない行: %r" % raw)
            continue
        name = ARENA_TAIL.sub("", m.group(2)).strip()
        if not name:
            warnings.append("カード名が読み取れない行: %r" % raw)
            continue
        _add(target, int(m.group(1)), name)
    return main, side, warnings


def _add(entries, count, name):
    key = cardcache.cache_key(name)
    for e in entries:
        if cardcache.cache_key(e["name"]) == key:
            e["count"] += count
            return
    entries.append({"count": count, "name": cardcache.normalize_name(name)})


# ---------------------------------------------------------------- 解決と検証

def resolve_entries(entries, cards_dir, offline=False, log=print):
    """各行を実在のカードに突き合わせ、**英語名に正規化する**。

    内部の同一性はすべて英語名で扱う。デッキリストに書かれた表記は
    source_name に残し、日本語名は printed に持つ（日英対応表の材料）。
    """
    for e in entries:
        written = e.get("source_name") or e["name"]
        e["source_name"] = written
        rec, how = cardcache.get(written, cards_dir, offline=offline)
        cardcache.report(written, rec, how)
        if rec.get("name") and not rec.get("unresolved"):
            e["name"] = rec["name"]
        e["printed"] = rec.get("printed_name")
        e["oracle_id"] = rec.get("oracle_id")
        e["types"] = rec.get("types", [])
        e["supertypes"] = rec.get("supertypes", [])
        e["mana_value"] = rec.get("mana_value", 0)
        e["colors"] = rec.get("colors", [])
        e["status"] = {"network": "ok", "cache": "ok", "corrected": "corrected",
                       "ambiguous": "ambiguous", "notfound": "unknown",
                       "unresolved": "unknown", "offline": "offline",
                       "missing": "offline"}.get(how, how)
    return entries


def unlimited(entry):
    """4枚制限の対象外か。基本土地と「何枚でも入れられる」カード。"""
    if "Basic" in entry.get("supertypes", []):
        return True
    return False


def validate(deck, unlimited_names=()):
    """構築戦のルールに照らして問題点を並べる。空なら適正。"""
    problems = []
    main, side = deck["main"], deck["sideboard"]
    if deck["main_total"] < 60:
        problems.append("メインデッキが %d枚です（60枚以上必要）。" % deck["main_total"])
    if deck["sideboard_total"] > 15:
        problems.append("サイドボードが %d枚です（15枚まで）。" % deck["sideboard_total"])

    # 同じカードはメインとサイドの合計で数える。表記が違っても oracle_id が同じなら同一。
    counts = {}
    for e in main + side:
        ident = e.get("oracle_id") or cardcache.cache_key(e["name"])
        c = counts.setdefault(ident, {"count": 0, "names": set(), "entry": e})
        c["count"] += e["count"]
        c["names"].add(e.get("source_name") or e["name"])
    for ident, c in counts.items():
        if unlimited(c["entry"]) or c["entry"]["name"] in unlimited_names:
            continue
        if c["count"] > 4:
            label = " / ".join(sorted(c["names"]))
            extra = "（表記違いを合算）" if len(c["names"]) > 1 else ""
            problems.append("《%s》が %d枚です（4枚まで）%s。" % (label, c["count"], extra))

    for e in main + side:
        written = e.get("source_name") or e["name"]
        if e.get("status") == "unknown":
            problems.append("《%s》は実在するカードとして解決できません"
                            "（自作カードなら card set で登録してください）。" % written)
        elif e.get("status") == "ambiguous":
            problems.append("《%s》は候補が複数あります。正式名で書いてください。" % written)
        elif e.get("status") == "corrected":
            problems.append("《%s》は正式名ではありません（%s として解決）。"
                            % (written, e["name"]))
    return problems


# ---------------------------------------------------------------- レコード

def build(name, text, cards_dir, source_file=None, fmt="standard",
          description="", offline=False):
    main, side, warnings = parse_decklist(text)
    resolve_entries(main, cards_dir, offline)
    resolve_entries(side, cards_dir, offline)
    deck = {
        "schema": SCHEMA,
        "key": cardcache.cache_key(name),
        "name": cardcache.normalize_name(name),
        "format": fmt,
        "description": description,
        "main": main,
        "sideboard": side,
        "main_total": sum(e["count"] for e in main),
        "sideboard_total": sum(e["count"] for e in side),
        # OS をまたいでも同じ値になるよう区切りは "/" に寄せる
        "source_file": pathlib.PurePath(source_file).as_posix() if source_file else None,
        "source_text": text,
        "warnings": warnings,
        "created_at": _now(),
        "updated_at": _now(),
    }
    deck["problems"] = validate(deck)
    deck["legal"] = not deck["problems"]
    return deck


REQUIRED = ("schema", "key", "name", "main", "main_total")


def validate_record(deck):
    problems = []
    if not isinstance(deck, dict):
        return ["JSON オブジェクトではありません"]
    for f in REQUIRED:
        if f not in deck:
            problems.append("必須フィールド %s がありません" % f)
    if deck.get("schema") != SCHEMA:
        problems.append("schema が %s ではありません (%s)" % (SCHEMA, deck.get("schema")))
    for f in ("main", "sideboard"):
        if f in deck and not isinstance(deck[f], list):
            problems.append("%s はリストである必要があります" % f)
    for e in deck.get("main", []) + deck.get("sideboard", []):
        if not isinstance(e, dict) or "count" not in e or "name" not in e:
            problems.append("main/sideboard の要素は count と name を持つ必要があります")
            break
        if not isinstance(e["count"], int) or e["count"] < 1:
            problems.append("count は1以上の整数である必要があります: %r" % e)
            break
    if "main_total" in deck and deck["main_total"] != sum(
            e["count"] for e in deck.get("main", []) if isinstance(e, dict)):
        problems.append("main_total が main の合計と一致しません")
    return problems


# ---------------------------------------------------------------- 読み書き

def path_for(dirpath, name):
    return pathlib.Path(dirpath) / (cardcache.slug(name) + ".json")


def save(dirpath, deck):
    import os
    d = pathlib.Path(dirpath)
    d.mkdir(parents=True, exist_ok=True)
    deck["updated_at"] = _now()
    p = path_for(d, deck["name"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(deck, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    os.replace(str(tmp), str(p))
    return p


def load(dirpath, name, quiet=False):
    p = path_for(dirpath, name)
    if not p.exists():
        return None
    try:
        deck = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        if not quiet:
            print("!! デッキを読めません %s (%s)" % (p, e), file=sys.stderr)
        return None
    if deck.get("schema") in LEGACY_SCHEMAS:
        if not quiet:
            print("!! %s は古い形式です。`deck verify` で更新してください"
                  "（source_text から作り直します）。" % p.name, file=sys.stderr)
        return None
    problems = validate_record(deck)
    if problems:
        if not quiet:
            print("!! デッキの形式が不正 %s: %s" % (p, " / ".join(problems)), file=sys.stderr)
        return None
    return deck


def listing(dirpath):
    out = []
    for p in sorted(pathlib.Path(dirpath).glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append(d)
    return out


def rebuild(dirpath, name, cards_dir, offline=False):
    """古い形式のデッキを source_text から作り直す。元テキストを保存している理由がこれ。"""
    raw = path_for(dirpath, name)
    if not raw.exists():
        return None
    old = json.loads(raw.read_text(encoding="utf-8"))
    if not old.get("source_text"):
        print("!! %s は source_text を持たないので作り直せません。" % name, file=sys.stderr)
        return None
    deck = build(old.get("name", name), old["source_text"], cards_dir,
                 source_file=old.get("source_file"), fmt=old.get("format", "standard"),
                 description=old.get("description", ""), offline=offline)
    deck["created_at"] = old.get("created_at", deck["created_at"])
    save(dirpath, deck)
    return deck


def card_names(deck):
    """シャッフル用に、メインデッキを1枚ずつに展開する。名前は英語名。"""
    out = []
    for e in deck["main"]:
        out.extend([e["name"]] * e["count"])
    return out


def resolve_source(spec, dirpath, cards_dir, offline=False):
    """デッキ名・.json・.txt のいずれでも受け取り、デッキ記録を返す。"""
    p = pathlib.Path(spec)
    if p.suffix.lower() == ".json" and p.exists():
        deck = load(p.parent, p.stem.rsplit("-", 1)[0], quiet=True)
        if deck:
            return deck
        return json.loads(p.read_text(encoding="utf-8"))
    if p.exists():
        return build(p.stem, p.read_text(encoding="utf-8"), cards_dir,
                     source_file=p, offline=offline)
    deck = load(dirpath, spec)
    if deck:
        return deck
    sys.exit("デッキが見つかりません: %s（登録名でもファイルパスでもありません）" % spec)


# ---------------------------------------------------------------- 表示

def stats(deck):
    """マナカーブ・タイプ・色の内訳。保存はせず、その都度数える（古くならないため）。"""
    curve, types, colors, lands = {}, {}, {}, 0
    for e in deck["main"]:
        t = e.get("types") or []
        if "Land" in t:
            lands += e["count"]
        else:
            curve[e.get("mana_value", 0)] = curve.get(e.get("mana_value", 0), 0) + e["count"]
        for x in (t or ["?"]):
            types[x] = types.get(x, 0) + e["count"]
        for c in (e.get("colors") or []):
            colors[c] = colors.get(c, 0) + e["count"]
    return {"curve": curve, "types": types, "colors": colors, "lands": lands}


def render(deck, verbose=False):
    lines = ["=== %s （%s） ===" % (deck["name"], deck.get("format", "standard"))]
    if deck.get("description"):
        lines.append(deck["description"])
    lines.append("メイン %d枚 / サイド %d枚 / %s"
                 % (deck["main_total"], deck.get("sideboard_total", 0),
                    "適正" if deck.get("legal") else "要修正"))
    st = stats(deck)
    lines.append("土地 %d / 呪文 %d" % (st["lands"], deck["main_total"] - st["lands"]))
    if st["curve"]:
        lines.append("マナカーブ: " + " ".join("%s:%d" % (k, st["curve"][k])
                                               for k in sorted(st["curve"])))
    if st["colors"]:
        lines.append("色: " + " ".join("%s%d" % (k, v) for k, v in sorted(st["colors"].items())))
    if verbose:
        for label, key in (("-- メイン --", "main"), ("-- サイド --", "sideboard")):
            if not deck.get(key):
                continue
            lines.append(label)
            rows = []
            for e in deck[key]:
                ja = e.get("printed") or ""
                if ja == e["name"]:
                    ja = ""
                if not ja and e.get("source_name") != e["name"]:
                    ja = e.get("source_name") or ""
                rows.append(["%2d" % e["count"], e["name"], _tag(e), ja])
            # 日本語名は最後の列に置く。空白で桁を揃える方式は「全角＝ASCII2文字分」を
            # 前提にしているが、表示側のフォントがその通りとは限らず、必ずズレる。
            # 揃える列を ASCII だけにして、日本語はその後ろに流すのが唯一確実。
            lines.extend(cardcache.table(rows, indent="  "))
    for w in deck.get("warnings", []):
        lines.append("!! " + w)
    for pb in deck.get("problems", []):
        lines.append("NG " + pb)
    return "\n".join(lines)


def render_listing(rows):
    """`deck list` の1行。デッキ名が日本語だと %-20s では桁が合わない。"""
    if not rows:
        return ["登録されたデッキはありません。`deck add <ファイル>` で登録します。"]
    # デッキ名は日本語がありうるので最後。前の列は ASCII だけで揃える。
    table = [[d.get("format", "") or "",
              "main%3d" % d.get("main_total", 0),
              "side%3d" % d.get("sideboard_total", 0),
              "ok" if d.get("legal") else "NG",
              d.get("name") or ""] for d in rows]
    return cardcache.table(table)


def _tag(e):
    s = e.get("status", "")
    if s == "ok":
        return "%s %s" % (e.get("mana_value", ""), "/".join(e.get("types", [])))
    return "[%s]" % s


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--cards-dir", default=cardcache.DEFAULT_DIR)
    ap.add_argument("--offline", action="store_true")
    sub = ap.add_subparsers(dest="op", required=True)
    s = sub.add_parser("add", help="テキストのデッキリストを登録")
    s.add_argument("file")
    s.add_argument("--name")
    s.add_argument("--format", default="standard")
    s.add_argument("--description", default="")
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("name")
    s = sub.add_parser("verify", help="登録済みデッキを再検証")
    s.add_argument("name", nargs="?")
    s = sub.add_parser("rm")
    s.add_argument("name")
    args = ap.parse_args()

    if args.op == "add":
        p = pathlib.Path(args.file)
        deck = build(args.name or p.stem, p.read_text(encoding="utf-8"), args.cards_dir,
                     source_file=p, fmt=args.format, description=args.description,
                     offline=args.offline)
        out = save(args.dir, deck)
        print(render(deck, verbose=True))
        print("→ %s" % out)
    elif args.op == "list":
        print(chr(10).join(render_listing(listing(args.dir))))
    elif args.op == "show":
        deck = load(args.dir, args.name) or sys.exit("見つかりません: %s" % args.name)
        print(render(deck, verbose=True))
    elif args.op == "verify":
        names = [args.name] if args.name else [d["name"] for d in listing(args.dir)]
        for n in names:
            deck = load(args.dir, n)
            if not deck:
                continue
            resolve_entries(deck["main"], args.cards_dir, args.offline)
            resolve_entries(deck["sideboard"], args.cards_dir, args.offline)
            deck["problems"] = validate(deck)
            deck["legal"] = not deck["problems"]
            save(args.dir, deck)
            print(render(deck))
    elif args.op == "rm":
        p = path_for(args.dir, args.name)
        if not p.exists():
            sys.exit("見つかりません: %s" % args.name)
        p.unlink()
        print("削除しました: %s" % p)


if __name__ == "__main__":
    main()
