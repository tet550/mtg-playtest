#!/usr/bin/env python3
"""カード・オラクル情報のローカルキャッシュ。

使用時に Scryfall から取得し、1枚1ファイルで保存する。以後はネットワークに出ない。
保存形式の規定は references/card-schema.md（このモジュールが唯一の実装）。

単体でも使える:
    python cardcache.py get 稲妻
    python cardcache.py get "Lightning Bolt" --refresh
    python cardcache.py path 僧院の速槍
    python cardcache.py list
"""
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import unicodedata


def force_utf8():
    """入出力を UTF-8 に固定する。

    Windows の Python 3.14 以前は、パイプ越しの stdout / stdin がロケール既定
    （cp932）になる。そのまま UTF-8 前提のツールに渡すと日本語が全部化けて、
    盤面もカード名も読めなくなる。**stdin も同じ**で、日本語を含むコマンドを
    パイプで流し込むと文字化けしたうえサロゲートが混ざり、印字時に落ちる。
    エントリポイントの先頭で呼ぶこと。
    """
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


force_utf8()

SCHEMA = "mtg-playtest/card@2"
ALIAS_SCHEMA = "mtg-playtest/alias@1"
LEGACY_CARD_SCHEMAS = ("mtg-playtest/card@1",)
DEFAULT_DIR = os.environ.get("MTG_CARDS_DIR", "cards")

# タイプ行の判定は英語で統一する。日本語表記で書かれても内部では英語に寄せる。
SUPERTYPES = ("Legendary", "Basic", "Snow", "World")
CARD_TYPES = ("Artifact", "Battle", "Creature", "Enchantment", "Instant", "Kindred",
              "Land", "Planeswalker", "Sorcery", "Tribal", "Dungeon", "Plane",
              "Scheme", "Vanguard", "Conspiracy", "Phenomenon", "Emblem")
JP_TYPES = {
    "クリーチャー": "Creature", "土地": "Land", "インスタント": "Instant",
    "ソーサリー": "Sorcery", "エンチャント": "Enchantment", "アーティファクト": "Artifact",
    "プレインズウォーカー": "Planeswalker", "バトル": "Battle", "部族": "Kindred",
    "伝説の": "Legendary", "伝説": "Legendary", "基本": "Basic", "氷雪": "Snow",
    "英雄譚": "Enchantment", "特殊": "",
}
# 表示用の英語→日本語。JP_TYPES の逆引きは「英雄譚→Enchantment」のような多対一が
# 混ざって戻せないので、表示に使う語だけを別に持つ。
TYPES_JA = {
    "Artifact": "アーティファクト", "Battle": "バトル", "Creature": "クリーチャー",
    "Enchantment": "エンチャント", "Instant": "インスタント", "Kindred": "部族",
    "Land": "土地", "Planeswalker": "PW", "Sorcery": "ソーサリー",
}
DASHES = "—–-"          # Scryfall は em dash。印刷面では別のダッシュが来ることがある
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
MIN_INTERVAL = 0.15     # Scryfall は 10 req/s が上限。余裕を見て 150ms 空ける
_last_request = [0.0]


# ---------------------------------------------------------------- 名前とキー

def normalize_name(name):
    """参照名の正規化。NFKC → 前後の空白除去 → 連続空白を1つに。大小文字は保持する。"""
    n = unicodedata.normalize("NFKC", str(name)).strip()
    return re.sub(r"\s+", " ", n)


def cache_key(name):
    """キャッシュ上の同一性を決めるキー。正規化名を小文字化したもの。"""
    return normalize_name(name).casefold()


def slug(name):
    """ファイル名。読める形 + キーの短縮ハッシュで一意にする。

    ハッシュを必ず付けるのは、ファイル名として使えない文字を落としたときの
    衝突（例 "A/B" と "A_B"）を避けるため。
    """
    key = cache_key(name)
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    body = _UNSAFE.sub("_", normalize_name(name)).replace(" ", "_")
    body = body.rstrip(". ")[:60] or "card"
    return "%s-%s" % (body, h)


def path_for(dirpath, name):
    return pathlib.Path(dirpath) / (slug(name) + ".json")


# ---------------------------------------------------------------- タイプ行

def norm_types(words):
    out = []
    for w in words:
        w = unicodedata.normalize("NFKC", str(w)).strip("・ 　")
        if not w:
            continue
        w = JP_TYPES.get(w, w)
        if w:
            out.append(w)
    return out


def split_type_line(line):
    """type_line を (supertypes, types, subtypes) に分ける。

    Scryfall の type_line は "Legendary Creature — Human Monk" の形。
    両面カードの "A // B" は面ごとに処理するので、ここでは先頭の面だけを見る。
    """
    line = unicodedata.normalize("NFKC", line or "").split("//")[0]
    left, right = line, ""
    for d in DASHES:
        if d in line:
            left, _, right = line.partition(d)
            break
    lwords = norm_types(re.split(r"[\s・]+", left))
    supers = [w for w in lwords if w in SUPERTYPES]
    types = [w for w in lwords if w not in SUPERTYPES]
    subs = norm_types(re.split(r"[\s・]+", right))
    return supers, types, subs


# ---------------------------------------------------------------- レコード

def blank_record(name):
    return {
        "schema": SCHEMA,
        "key": cache_key(name),
        "name": normalize_name(name),
        "en_name": None, "printed_name": None, "lang": None,
        "mana_cost": "", "mana_value": 0, "colors": [], "color_identity": [],
        "supertypes": [], "types": [], "subtypes": [],
        "type_line_en": "", "type_line_printed": None,
        "power": None, "toughness": None, "loyalty": None, "defense": None,
        "oracle_text_en": "", "oracle_text_printed": None,
        "keywords": [], "layout": "normal", "faces": [],
        "scryfall_id": None, "oracle_id": None, "scryfall_uri": None,
        "source": "manual", "unresolved": True, "corrected": False,
        "fetched_at": None, "updated_at": _now(),
    }


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _face(data):
    """1つの面から共通項目を取り出す。"""
    supers, types, subs = split_type_line(data.get("type_line") or "")
    return {
        "name": data.get("name"),
        "printed_name": data.get("printed_name"),
        "mana_cost": data.get("mana_cost") or "",
        "type_line_en": data.get("type_line") or "",
        "type_line_printed": data.get("printed_type_line"),
        "supertypes": supers, "types": types, "subtypes": subs,
        "power": data.get("power"), "toughness": data.get("toughness"),
        "loyalty": data.get("loyalty"), "defense": data.get("defense"),
        "oracle_text_en": data.get("oracle_text") or "",
        "oracle_text_printed": data.get("printed_text"),
    }


def from_scryfall(data):
    """Scryfall のカードオブジェクトを、このスキルの保存形式に変換する。

    **name は必ず英語名**。内部の同一性はすべて英語名で判断する。
    日本語などの印刷名は printed_name に置き、表示と別名レコードに使う。
    タイプ・P/T といった判定に使う値も必ず英語の type_line から作る。
    """
    rec = blank_record(data.get("name") or "?")
    faces = [_face(f) for f in data.get("card_faces", [])] or [_face(data)]
    head = faces[0]
    if data.get("card_faces"):
        # 分割・変身カードは先頭の面を代表値にする（裏面は faces で参照する）
        head = dict(head)
        head["mana_cost"] = head["mana_cost"] or data.get("mana_cost") or ""
    rec.update({
        "en_name": data.get("name"),
        "printed_name": data.get("printed_name") or head.get("printed_name"),
        "lang": data.get("lang"),
        "mana_cost": head["mana_cost"],
        "mana_value": int(data.get("cmc") or 0),
        "colors": data.get("colors") or head_colors(data),
        "color_identity": data.get("color_identity") or [],
        "supertypes": head["supertypes"], "types": head["types"],
        "subtypes": head["subtypes"],
        "type_line_en": data.get("type_line") or head["type_line_en"],
        "type_line_printed": data.get("printed_type_line") or head["type_line_printed"],
        "power": head["power"], "toughness": head["toughness"],
        "loyalty": head["loyalty"], "defense": head["defense"],
        "oracle_text_en": head["oracle_text_en"],
        "oracle_text_printed": head["oracle_text_printed"],
        "keywords": data.get("keywords") or [],
        "layout": data.get("layout") or "normal",
        "faces": faces if data.get("card_faces") else [],
        "scryfall_id": data.get("id"), "oracle_id": data.get("oracle_id"),
        "scryfall_uri": data.get("scryfall_uri"),
        "source": "scryfall", "unresolved": False, "corrected": False,
        "fetched_at": _now(), "updated_at": _now(),
    })
    return rec


def head_colors(data):
    for f in data.get("card_faces", []):
        if f.get("colors"):
            return f["colors"]
    return []


REQUIRED = ("schema", "key", "name", "types", "source")


def validate(rec):
    """壊れたファイルを黙って使わないための最低限の検査。問題点のリストを返す。"""
    problems = []
    if not isinstance(rec, dict):
        return ["JSON オブジェクトではありません"]
    if rec.get("schema") == ALIAS_SCHEMA:
        # 別名は指し先だけを持つ。カードの中身は正本にしかない。
        for f in ("key", "name", "alias_of", "en_name"):
            if not rec.get(f):
                problems.append("別名レコードに %s がありません" % f)
        return problems
    for f in REQUIRED:
        if f not in rec:
            problems.append("必須フィールド %s がありません" % f)
    if rec.get("schema") != SCHEMA:
        problems.append("schema が %s ではありません (%s)" % (SCHEMA, rec.get("schema")))
    for f in ("types", "supertypes", "subtypes", "colors", "keywords", "faces"):
        if f in rec and not isinstance(rec[f], list):
            problems.append("%s はリストである必要があります" % f)
    for f in ("power", "toughness", "loyalty"):
        if rec.get(f) is not None and not isinstance(rec[f], str):
            problems.append("%s は文字列か null である必要があります（'*' があるため）" % f)
    if rec.get("source") not in ("scryfall", "manual"):
        problems.append("source は scryfall か manual である必要があります")
    return problems


# ---------------------------------------------------------------- 読み書き

def load_raw(dirpath, name):
    """検査も別名の追跡もせずにファイルを読む。移行処理用。"""
    p = path_for(dirpath, name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def load(dirpath, name, quiet=False, _depth=0):
    """名前からカード情報を読む。別名なら英語名の正本まで辿る。"""
    p = path_for(dirpath, name)
    if not p.exists():
        return None
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        if not quiet:
            print("!! キャッシュを読めません %s (%s)" % (p, e), file=sys.stderr)
        return None
    if rec.get("schema") == ALIAS_SCHEMA:
        if _depth > 2 or not rec.get("en_name"):
            return None
        target = load(dirpath, rec["en_name"], quiet=quiet, _depth=_depth + 1)
        if target is None:
            return None
        target = dict(target)
        target["_alias"] = rec        # 参照に使われた名前。警告の判断に使う
        return target
    if rec.get("schema") in LEGACY_CARD_SCHEMAS:
        if not quiet:
            print("!! %s は古い形式です。`cardcache.py migrate` を実行してください。"
                  % p.name, file=sys.stderr)
        return None
    problems = validate(rec)
    if problems:
        if not quiet:
            print("!! キャッシュの形式が不正 %s: %s" % (p, " / ".join(problems)), file=sys.stderr)
        return None
    return rec


def save(dirpath, rec):
    """書き途中のファイルを残さないよう、一時ファイルに書いてから置き換える。"""
    d = pathlib.Path(dirpath)
    d.mkdir(parents=True, exist_ok=True)
    rec = {k: v for k, v in rec.items() if not k.startswith("_")}
    rec["updated_at"] = _now()
    p = path_for(d, rec["name"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    os.replace(str(tmp), str(p))
    return p


def alias_record(alias, rec, corrected=False):
    return {"schema": ALIAS_SCHEMA,
            "key": cache_key(alias),
            "name": normalize_name(alias),
            "alias_of": rec["key"],
            "en_name": rec["name"],
            "corrected": bool(corrected),
            "updated_at": _now()}


def write_alias(dirpath, alias, rec, corrected=False, overwrite=False):
    """別名（日本語名・略称・デッキリスト上の表記）から英語名への転送を書く。

    中身を複製せず**指し先だけ**を持つ。カードの情報が1箇所にしかないので、
    取り直したときに古い写しが残らない。
    """
    if not alias or cache_key(alias) == rec["key"]:
        return None
    p = path_for(dirpath, alias)
    if p.exists() and not overwrite:
        return None
    return save(dirpath, alias_record(alias, rec, corrected))


def save_card(dirpath, rec, requested=None, corrected=False):
    """英語名で正本を保存し、印刷名と参照名から別名を張る。"""
    paths = [save(dirpath, rec)]
    for alias in (rec.get("printed_name"), requested):
        wrote = write_alias(dirpath, alias, rec, corrected=(corrected and alias == requested))
        if wrote:
            paths.append(wrote)
    return paths


def aliases_of(dirpath, rec):
    """その英語名を指している別名をすべて集める（日英対応表の材料）。"""
    out = []
    key = rec.get("key")
    for f in pathlib.Path(dirpath).glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("schema") == ALIAS_SCHEMA and d.get("alias_of") == key:
            out.append(d["name"])
    return sorted(out)


# ---------------------------------------------------------------- Scryfall

def _http_json(url, timeout=15, retries=1):
    """Scryfall を1回叩く。429（レート制限）だけは待って再試行する。

    デッキ一括登録のように連続して引く場面では 429 に当たりうる。
    ここで諦めると「通信不可」として大量のカードが未解決のまま残るので、
    指示された秒数だけ待って1度だけやり直す。
    """
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "mtg-playtest-skill/1.0", "Accept": "application/json"})
    for attempt in range(retries + 1):
        wait = MIN_INTERVAL - (time.time() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        try:
            body = urllib.request.urlopen(req, timeout=timeout).read()
            _last_request[0] = time.time()
            return json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as e:
            _last_request[0] = time.time()
            if e.code == 429 and attempt < retries:
                delay = 60
                try:
                    delay = min(65, int(e.headers.get("Retry-After") or 60))
                except (TypeError, ValueError):
                    pass
                print("!! Scryfall のレート制限に当たりました。%d秒待って再試行します。"
                      % delay, file=sys.stderr)
                time.sleep(delay)
                continue
            raise
        except Exception:
            _last_request[0] = time.time()
            raise


def has_japanese(text):
    return bool(re.search(r"[ぁ-んァ-ヴ一-龥]", text or ""))


def name_variants(name):
    """表記揺れの候補を、確からしい順に返す。

    Scryfall の日本語名は完全一致でしか引けないため、句読点や空白の揺れは
    こちら側で吸収する必要がある（「敏捷なこそ泥,ラガバン」は 0件になる）。
    """
    out = [name]
    v = re.sub(r"[,，]", "、", name)
    v = re.sub(r"[･·]", "・", v)
    v = re.sub(r"\s+", "", v)
    if v not in out:
        out.append(v)
    v2 = re.sub(r"[、・]", "", v)      # 区切り記号を落とした形
    if v2 not in out:
        out.append(v2)
    return out


def name_fragments(name):
    """「敏捷なこそ泥、ラガバン」→「ラガバン」。最も特徴的な断片を長い順に返す。"""
    parts = [p for p in re.split(r"[、,・\s]+", name) if len(p) >= 2]
    return sorted(set(parts), key=len, reverse=True) if len(parts) > 1 else []


def _attempt(fn):
    """(結果, 状態) を返す。状態は ok / empty / error。

    404・400 は「該当なし」、それ以外の失敗は通信エラーとして扱う。
    ここを混ぜると、一時的な障害を「存在しないカード」として
    負のキャッシュに固定してしまう。
    """
    import urllib.error
    try:
        return fn(), "ok"
    except urllib.error.HTTPError as e:
        return None, ("empty" if e.code in (400, 404) else "error")
    except Exception:
        return None, "error"


def _search(query, timeout):
    import urllib.parse
    res = _http_json("https://api.scryfall.com/cards/search?q=" + urllib.parse.quote(query),
                     timeout)
    return res.get("data") or []


def _named_fuzzy(name, timeout):
    import urllib.parse
    return [_http_json("https://api.scryfall.com/cards/named?fuzzy=" + urllib.parse.quote(name),
                       timeout)]


def display_name(c):
    return c.get("printed_name") or c.get("name") or "?"


def fetch_scryfall(name, timeout=15):
    """カード名から Scryfall のカードを引く。戻り値は (data, status, candidates)。

      found     … そのままの名前で一意に決まった
      corrected … 表記揺れを補正して一意に決まった（呼び出し側に知らせること）
      ambiguous … 候補が複数。**推測しない**。candidates に表示名を入れて返す
      notfound  … Scryfall は答えたが該当なし（自作カードなど）
      error     … 通信できなかった

    日本語名は完全一致でしか引けないので、
    「完全一致 → 表記ゆれを直して完全一致 → 名前の部分一致 → 特徴的な断片で部分一致」
    の順に降りていく。英語名は Scryfall の fuzzy 検索がタイプミスまで吸収してくれる。
    """
    n = normalize_name(name)
    variants = name_variants(n)
    ja = has_japanese(n)

    steps = []
    if ja:
        for v in variants:
            steps.append(("exact", v, lambda v=v: _search('lang:ja !"%s"' % v.replace('"', ""),
                                                          timeout)))
        for v in variants:
            steps.append(("sub", v, lambda v=v: _search('lang:ja name:"%s"' % v.replace('"', ""),
                                                        timeout)))
        for frag in name_fragments(variants[-1]):
            steps.append(("frag", frag,
                          lambda f=frag: _search('lang:ja name:"%s"' % f.replace('"', ""),
                                                 timeout)))
        steps.append(("fuzzy", n, lambda: _named_fuzzy(n, timeout)))
    else:
        steps.append(("fuzzy", n, lambda: _named_fuzzy(n, timeout)))
        for v in variants:
            steps.append(("sub", v, lambda v=v: _search('lang:ja name:"%s"' % v.replace('"', ""),
                                                        timeout)))

    saw_answer = False
    for kind, used, fn in steps:
        data, st = _attempt(fn)
        if st == "error":
            continue
        saw_answer = True
        if not data:
            continue
        if len(data) > 1 and kind in ("sub", "frag"):
            return None, "ambiguous", [display_name(c) for c in data[:12]]
        card = data[0]
        # 返ってきた名前が求めた名前と実質同じなら found、違えば corrected。
        # fuzzy はタイプミスも吸収するので、直った事実を呼び出し側に伝える必要がある。
        # 両面・分割カードは面の名前で呼ぶのが普通（「Esper Origins」で
        # 「Esper Origins // Summon: Esper Maduin」を指す）。面名も一致とみなす。
        aliases = [card.get("name"), card.get("printed_name")]
        for f in card.get("card_faces", []):
            aliases += [f.get("name"), f.get("printed_name")]
        got = {normalize_name(x).casefold() for x in aliases if x}
        exact_hit = n.casefold() in got
        return card, ("found" if exact_hit else "corrected"), []
    return None, ("notfound" if saw_answer else "error"), []


# ---------------------------------------------------------------- 取得の入口

def get(name, dirpath=DEFAULT_DIR, refresh=False, offline=False, max_age_days=None):
    """カード情報を返す。戻り値は (record, 由来) で、由来は cache/network/missing。

    キャッシュにあればネットワークに出ない。無ければ取得して保存する。
    見つからない・オフラインのときは未解決レコードを返し、ゲームは止めない。
    """
    if not refresh:
        rec = load(dirpath, name)
        if rec and offline:
            return rec, "cache"
        if rec and not stale(rec, max_age_days):
            if rec.get("source") == "manual" and rec.get("unresolved"):
                # 手入力待ち。ネットワークには出ない
                return rec, "unresolved"
            return rec, "cache"
    if offline:
        return blank_record(name), "missing"
    data, status, candidates = fetch_scryfall(name)
    if status in ("found", "corrected"):
        rec = from_scryfall(data)
        # 補正したことは別名側に残す。次回キャッシュヒットしたときも警告できるようにするため
        # （デッキリストの誤記が一度通ったあと黙って通り続けるのを防ぐ）。
        save_card(dirpath, rec, requested=name, corrected=(status == "corrected"))
        rec = load(dirpath, name) or rec
        return rec, ("network" if status == "found" else "corrected")
    rec = load(dirpath, name)
    if rec:
        return rec, "cache"
    rec = blank_record(name)
    if status == "ambiguous":
        # どれを指すか決められない。推測して間違えるより、候補を見せて選んでもらう。
        rec["_candidates"] = candidates
        return rec, "ambiguous"
    if status == "notfound":
        # 存在しない名前を毎回問い合わせないよう、未解決のまま保存しておく
        save(dirpath, rec)
        return rec, "notfound"
    return rec, "offline"


def stale(rec, max_age_days):
    if not max_age_days or rec.get("source") != "scryfall" or not rec.get("fetched_at"):
        return False
    try:
        t = datetime.datetime.strptime(rec["fetched_at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    age = (datetime.datetime.utcnow() - t).days
    return age > max_age_days


def put_manual(name, dirpath=DEFAULT_DIR, **fields):
    """自作カードや、Scryfall に無いものを手入力で登録する。

    英語名が取れないので、書かれた名前をそのまま正本の name にする。
    """
    rec = load(dirpath, name) or blank_record(name)
    rec.pop("_alias", None)
    if fields.get("types"):
        rec["types"] = norm_types(fields["types"])
    if fields.get("supertypes"):
        rec["supertypes"] = norm_types(fields["supertypes"])
    if fields.get("subtypes"):
        rec["subtypes"] = norm_types(fields["subtypes"])
    for f in ("mana_cost", "power", "toughness", "loyalty",
              "oracle_text_en", "oracle_text_printed"):
        if fields.get(f) is not None:
            rec[f] = fields[f]
    if fields.get("mana_value") is not None:
        rec["mana_value"] = int(fields["mana_value"])
    rec["source"] = "manual"
    rec["unresolved"] = not rec["types"]
    if not rec.get("type_line_en"):
        rec["type_line_en"] = " ".join(rec["supertypes"] + rec["types"])
    save(dirpath, rec)
    return rec


def display_name(rec):
    """人に見せる名前。日本語印刷があればそれ、無ければ英語名。"""
    return rec.get("printed_name") or rec.get("name") or "?"


def glossary(names, dirpath=DEFAULT_DIR, offline=True):
    """英語名 → 日本語名の対応表を作る。ゲーム中の参照用。

    内部はすべて英語名で動かすので、日本語で書かれたデッキリストや
    会話とつなぐには、この対応表が要る。
    """
    rows, seen = [], set()
    for n in names:
        rec = load(dirpath, n, quiet=True)
        if rec is None:
            rows.append({"en": n, "ja": None, "cost": "", "types": [], "known": False})
            continue
        if rec["key"] in seen:
            continue
        seen.add(rec["key"])
        ja = rec.get("printed_name")
        if not ja:
            for a in aliases_of(dirpath, rec):
                if has_japanese(a):
                    ja = a
                    break
        rows.append({"en": rec["name"], "ja": ja, "cost": rec.get("mana_cost", ""),
                     "types": rec.get("types", []), "known": True})
    return sorted(rows, key=lambda r: r["en"])


def width(s):
    """端末上の表示幅。日本語の全角は2桁として数える。

    `%-18s` のような文字数ベースの桁揃えは、日本語名が混じった瞬間に崩れる
    （《ロケッティアの隊長、レッドシフト》は16文字だが32桁を占める）。
    表を作るところは全部ここを通すこと。
    """
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def pad(s, n):
    s = str(s)
    return s + " " * max(0, n - width(s))


def rpad(s, n):
    s = str(s)
    return " " * max(0, n - width(s)) + s


def table(rows, sep="  ", indent=""):
    """行（列のリスト）を表示幅で桁揃えして文字列のリストにする。"""
    if not rows:
        return []
    n = max(len(r) for r in rows)
    rows = [list(r) + [""] * (n - len(r)) for r in rows]
    w = [max(width(r[i]) for r in rows) for i in range(n)]
    return [indent + sep.join(pad(r[i], w[i]) for i in range(n)).rstrip() for r in rows]


def render_glossary(rows):
    w = max([len(r["en"]) for r in rows] + [8])
    out = ["%-*s  %s" % (w, "English", "日本語 / 備考"), "%s  %s" % ("-" * w, "-" * 20)]
    for r in rows:
        note = r["ja"] or ("（日本語名なし）" if r["known"] else "!! 未登録")
        out.append("%-*s  %s" % (w, r["en"], note))
    return "\n".join(out)


def migrate(dirpath):
    """card@1 のキャッシュを card@2（英語名が正本）へ移す。通信は要らない。"""
    d = pathlib.Path(dirpath)
    files = sorted(d.glob("*.json"))
    raws = []
    for f in files:
        try:
            raws.append((f, json.loads(f.read_text(encoding="utf-8"))))
        except Exception:
            print("読めないので飛ばします: %s" % f.name)
    canon, converted, removed = 0, 0, 0
    # 1) 英語名を正本として書き直す
    for f, rec in raws:
        if rec.get("schema") not in LEGACY_CARD_SCHEMAS or rec.get("alias_of"):
            continue
        en = rec.get("en_name") or rec["name"]
        new = dict(rec)
        new["schema"] = SCHEMA
        new["name"] = normalize_name(en)
        new["key"] = cache_key(en)
        new.pop("corrected", None)
        save(d, new)
        canon += 1
    # 2) 元の名前が英語名と違うなら、そこを別名に置き換える
    for f, rec in raws:
        if rec.get("schema") not in LEGACY_CARD_SCHEMAS:
            continue
        en = rec.get("en_name") or rec["name"]
        if cache_key(rec["name"]) == cache_key(en):
            continue
        target = load(d, en, quiet=True)
        if target is None:
            continue
        save(d, alias_record(rec["name"], target, corrected=rec.get("corrected", False)))
        converted += 1
    # 3) 中身を複製していた旧・別名ファイルの残骸を掃除
    for f in sorted(d.glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if rec.get("schema") in LEGACY_CARD_SCHEMAS:
            f.unlink()
            removed += 1
    print("正本 %d件 / 別名 %d件 / 削除 %d件" % (canon, converted, removed))


def _head(rec):
    pt = ""
    if rec.get("power") is not None:
        pt = "  %s/%s" % (rec["power"], rec["toughness"])
    elif rec.get("loyalty") is not None:
        pt = "  [忠誠度 %s]" % rec["loyalty"]
    elif rec.get("defense") is not None:
        pt = "  [防衛値 %s]" % rec["defense"]
    line = rec.get("type_line_printed") or rec.get("type_line_en") or ""
    return "%s  %s  %s%s" % (rec.get("name") or "?", rec.get("mana_cost", ""), line, pt)


def _body_lines(rec, indent):
    """表示するオラクル・テキストの行と、末尾に添える注記を返す。

    Scryfall の printed_text は、日本語版が途中までしか入っていないカードがある
    （クラスや英雄譚のように、レベル・章がぶら下がる形式で起きやすい）。
    行数が英語より少ないものは訳が欠けているとみなし、英語も併記する。
    欠けたまま出すと、あるはずの能力を見落としたまま判定してしまう。
    """
    ja = rec.get("oracle_text_printed") or ""
    en = rec.get("oracle_text_en") or ""
    if not ja:
        return ([indent + l for l in en.splitlines()],
                "日本語の印刷テキストが無いカード。英語オラクルを表示しています" if en else "")
    missing = len(en.splitlines()) - len(ja.splitlines()) if en else 0
    lines = [indent + l for l in ja.splitlines()]
    if missing > 0:
        lines.append(indent + "!! 日本語テキストが %d行 欠けています。英語オラクル全文:" % missing)
        lines += [indent + "   " + l for l in en.splitlines()]
    return lines, ""


def summary(rec, oneline=False):
    """カードを人が読める形にする。

    **テキストは絶対に切らない。** 途中で切ると、能力の本体（土の技の内容、
    behold や warp のリマインダー文など）が消えたまま解釈することになり、
    テストプレイの結論ごと壊れる。長さより正確さを取る。
    """
    if oneline:
        text = rec.get("oracle_text_printed") or rec.get("oracle_text_en") or ""
        return "%s\n  %s" % (_head(rec), text.replace("\n", " / "))

    out, notes = [_head(rec)], []
    faces = rec.get("faces") or []
    if faces:
        for f in faces:
            out.append("  --- %s" % _head(f))
            lines, note = _body_lines(f, "      ")
            out += lines
            notes.append(note)
    else:
        lines, note = _body_lines(rec, "  ")
        out += lines
        notes.append(note)
    for n in dict.fromkeys(n for n in notes if n):
        out.append("  （%s）" % n)
    return "\n".join(out)


def report(requested, rec, how):
    """表記揺れの補正や候補を、呼び出し側が同じ文言で伝えられるようにする。"""
    if how == "cache" and (rec.get("_alias") or {}).get("corrected"):
        print("→ 「%s」は正式名ではありません（「%s」として登録済み）。"
              % (requested, rec.get("name")), file=sys.stderr)
    if how == "corrected":
        actual = rec.get("name")
        if actual and normalize_name(actual) != normalize_name(requested):
            print("→ 「%s」は該当なし。「%s」として解決しました。"
                  % (requested, actual), file=sys.stderr)
    elif how == "ambiguous":
        print("!! 「%s」に一致するカードが複数あります。正式名で指定してください:"
              % requested, file=sys.stderr)
        for c in rec.get("_candidates", []):
            print("     %s" % c, file=sys.stderr)
    elif how == "notfound":
        print("!! 「%s」は Scryfall に見つかりません。自作カードなら card set で登録してください。"
              % requested, file=sys.stderr)
    elif how == "offline":
        print("!! 「%s」を取得できませんでした（通信不可）。" % requested, file=sys.stderr)


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=DEFAULT_DIR)
    sub = ap.add_subparsers(dest="op", required=True)
    s = sub.add_parser("get", help="取得（キャッシュ優先）")
    s.add_argument("name")
    s.add_argument("--refresh", action="store_true")
    s.add_argument("--offline", action="store_true")
    s = sub.add_parser("path", help="保存先パスを表示")
    s.add_argument("name")
    sub.add_parser("list", help="キャッシュ一覧")
    sub.add_parser("migrate", help="古い形式のキャッシュを英語名が正本の形へ移す")
    s = sub.add_parser("alias", help="別名（日本語名など）を英語名に結びつける")
    s.add_argument("alias")
    s.add_argument("en_name")
    s = sub.add_parser("glossary", help="日英対応表を表示")
    s.add_argument("names", nargs="*")
    s = sub.add_parser("verify", help="全ファイルの形式を検査")
    args = ap.parse_args()

    if args.op == "get":
        rec, how = get(args.name, args.dir, refresh=args.refresh, offline=args.offline)
        print("[%s] %s" % (how, summary(rec)))
        report(args.name, rec, how)
    elif args.op == "path":
        print(path_for(args.dir, args.name))
    elif args.op == "list":
        for p in sorted(pathlib.Path(args.dir).glob("*.json")):
            rec = json.loads(p.read_text(encoding="utf-8"))
            print("%-44s %-24s %s" % (p.name, rec.get("name"),
                                      rec.get("source") + ("(未解決)" if rec.get("unresolved") else "")))
    elif args.op == "migrate":
        migrate(args.dir)
    elif args.op == "alias":
        rec = load(args.dir, args.en_name)
        if rec is None:
            sys.exit("英語名が見つかりません: %s（先に get してください）" % args.en_name)
        rec.pop("_alias", None)
        p = write_alias(args.dir, args.alias, rec, overwrite=True)
        print("%s → %s  (%s)" % (args.alias, rec["name"], p))
    elif args.op == "glossary":
        names = args.names or [json.loads(f.read_text(encoding="utf-8")).get("name")
                               for f in sorted(pathlib.Path(args.dir).glob("*.json"))]
        print(render_glossary(glossary(names, args.dir)))
    elif args.op == "verify":
        bad = 0
        for p in sorted(pathlib.Path(args.dir).glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                print("NG %s: %s" % (p.name, e))
                bad += 1
                continue
            problems = validate(rec)
            if problems:
                bad += 1
                print("NG %s: %s" % (p.name, " / ".join(problems)))
            elif path_for(args.dir, rec["name"]).name != p.name:
                bad += 1
                print("NG %s: ファイル名が name (%s) と一致しません" % (p.name, rec["name"]))
        print("検査完了。問題 %d 件。" % bad)


if __name__ == "__main__":
    main()
