"""Rule Reference（28節）: AI が判断に使うカード情報の参照。

Rules Engine ではなく参照情報。Scryfall から英語名で取得し、キャッシュする。

- カード: `cards/<名前>-<hash>.json`（1枚1ファイル）
- デッキ: `cards/decks/<デッキ名>-<hash>.json`（そのデッキの全カードを1ファイルに束ねたもの。
  hash はデッキリストの中身から作るので、リストを直すと別のキャッシュになる）
- 画像: `cards/images/<名前>-<hash>[-<面>].jpg`（Scryfall の normal。観戦ビューアが表示に使う）
- マナ・シンボル: `cards/symbols/<記号>.svg`（{G} → G.svg、{W/U} → WU.svg）

置き場所はリポジトリ直下の `cards/`（環境変数 MTG_CARDS_DIR で変更可）。カードテキストは
第三者の著作物なのでリポジトリには含めない（.gitignore 済み）。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.scryfall.com/cards/named?exact="
COLLECTION_API = "https://api.scryfall.com/cards/collection"
COLLECTION_MAX = 75  # Scryfall の /cards/collection が1回に受ける名前の数
USER_AGENT = "mtgtable/0.1 (personal playtest tool)"
MIN_INTERVAL = 0.5  # Scryfall のレート制限に余裕を持たせる（429 が出たら待って再試行）
_last = [0.0]
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\s]+')

FIELDS = ("name", "mana_cost", "cmc", "type_line", "oracle_text", "power", "toughness",
          "loyalty", "defense", "colors", "color_identity", "keywords", "layout", "oracle_id")


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DECK_SCHEMA = "mtgtable/deck-oracle@1"


def cache_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("MTG_CARDS_DIR") or REPO_ROOT / "cards")


def _path(name: str) -> pathlib.Path:
    key = name.strip().casefold()
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return cache_dir() / ("%s-%s.json" % (_UNSAFE.sub("_", name.strip())[:60], h))


def _slim(data: dict) -> dict:
    rec = {k: data.get(k) for k in FIELDS if data.get(k) not in (None, "", [])}
    if data.get("card_faces"):
        rec["faces"] = [{k: f.get(k) for k in FIELDS if f.get(k) not in (None, "", [])}
                        for f in data["card_faces"]]
        for face, f in zip(rec["faces"], data["card_faces"]):
            if (f.get("image_uris") or {}).get("normal"):
                face["image"] = f["image_uris"]["normal"]
    if (data.get("image_uris") or {}).get("normal"):
        rec["image"] = data["image_uris"]["normal"]
    rec["scryfall_uri"] = data.get("scryfall_uri")
    if data.get("legalities"):
        rec["legalities"] = data["legalities"]  # フォーマットごとに使えるか（デッキ登録の検査）
    return rec


def _save(name: str, rec: dict) -> None:
    """カードのキャッシュに書く（正式な名前でも引けるように、名前が違えばその名前でも置く）。"""
    p = _path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    if rec.get("name") and rec["name"] != name:
        alias = _path(rec["name"])
        if not alias.exists():
            alias.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")


def _norm(name: str) -> str:
    """名前の比べ方: 大文字・小文字と、「//」の前後の空白の違いは見ない。"""
    return re.sub(r"\s*//\s*", " // ", name.strip()).casefold()


def card_names(rec: dict) -> list:
    """そのカードとして書いてよい名前（正式な名前と、両面・分割カードの各面の名前）。"""
    names = [rec.get("name") or ""] + [f.get("name") or "" for f in rec.get("faces") or []]
    names += (rec.get("name") or "").split(" // ")
    return [n for n in dict.fromkeys(names) if n]


def _post_collection(names: list) -> dict:
    body = json.dumps({"identifiers": [{"name": n} for n in names]}).encode("utf-8")
    req = urllib.request.Request(COLLECTION_API, data=body, method="POST",
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                          "Content-Type": "application/json"})
    for attempt in range(6):
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 5:
                time.sleep(max(float(e.headers.get("Retry-After") or 0), 1.0 * 2 ** attempt))
                continue
            raise LookupError("Scryfall collection request failed (HTTP %s)" % e.code)
        except (urllib.error.URLError, OSError) as e:
            raise LookupError("Scryfall collection request failed (%s)" % e)
        finally:
            _last[0] = time.time()
    raise LookupError("Scryfall collection request failed")


def resolve_names(names, need_legalities: bool = False) -> dict:
    """カード名をまとめて引く（デッキ登録の検査）。{"found": {書いた名前: rec}, "missing": [見つからない名前]}。

    キャッシュにあるものはそのまま使い（need_legalities なら、フォーマットの情報の無い古いキャッシュは引き直す）、
    残りを Scryfall の /cards/collection で 75 枚ずつ引く（1枚ずつより、ずっと少ない回数で済む）。"""
    found, todo = {}, []
    for name in dict.fromkeys(n.strip() for n in names if n and n.strip()):
        rec = lookup(name, offline=True)
        if rec and (rec.get("legalities") or not need_legalities):
            found[name] = rec
        else:
            todo.append(name)
    for i in range(0, len(todo), COLLECTION_MAX):
        chunk = todo[i:i + COLLECTION_MAX]
        # 分割カードの「Fire // Ice」は、/cards/collection では引けない（片方の面の名前なら引ける）。最初の面で引いて、
        # 返ってきたカードの名前と照らし合わせる
        cards = [_slim(c) for c in _post_collection([n.split("//")[0].strip() for n in chunk]).get("data") or []]
        for name in chunk:
            key = _norm(name)
            rec = next((c for c in cards if any(_norm(n) == key for n in card_names(c))), None)
            if rec:
                _save(name, rec)
                found[name] = rec
    return {"found": found, "missing": [n for n in todo if n not in found]}


def fetch(name: str, retries: int = 5) -> dict:
    req = urllib.request.Request(API + urllib.parse.quote(name),
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(retries + 1):
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return _slim(json.loads(r.read().decode("utf-8")))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise LookupError("Scryfall has no card named %r" % name)
            if e.code == 429 and attempt < retries:
                # レート制限。Retry-After があれば従い、無ければ指数的に待つ
                try:
                    delay = float(e.headers.get("Retry-After") or 0)
                except ValueError:
                    delay = 0
                time.sleep(max(delay, 1.0 * 2 ** attempt))
                continue
            raise LookupError("Scryfall request for %r failed (HTTP %s)" % (name, e.code))
        finally:
            _last[0] = time.time()
    raise LookupError("Scryfall request for %r failed" % name)


def lookup(name: str, offline: bool = False, refresh: bool = False):
    """カード情報を返す。キャッシュに無く offline なら None。"""
    p = _path(name)
    if p.exists() and not refresh:
        return json.loads(p.read_text(encoding="utf-8"))
    if offline:
        return None
    rec = fetch(name)
    _save(name, rec)
    return rec


def format_card(rec: dict) -> str:
    def one(r):
        head = "<%s>" % r.get("name", "?")
        if r.get("mana_cost"):
            head += "  " + r["mana_cost"]
        lines = [head, r.get("type_line", "")]
        if r.get("oracle_text"):
            lines.append(r["oracle_text"])
        stats = [("power", "toughness"), ("loyalty",), ("defense",)]
        for keys in stats:
            if all(r.get(k) is not None for k in keys):
                lines.append("/".join(str(r[k]) for k in keys) if len(keys) > 1
                             else "%s: %s" % (keys[0], r[keys[0]]))
        return "\n".join(x for x in lines if x)
    if rec.get("faces"):
        return "\n----\n".join(one(f) for f in rec["faces"])
    return one(rec)


# ---------------------------------------------------------------- images

IMAGE_HOSTS = (".scryfall.io",)  # 画像は Scryfall の画像サーバーからだけ取る


def image_path(name: str, face: int = 0) -> pathlib.Path:
    p = _path(name)
    return cache_dir() / "images" / (p.stem + ("-%d" % face if face else "") + ".jpg")


def _image_url(rec: dict, face: int) -> str:
    if face == 0 and rec.get("image"):
        return rec["image"]
    faces = rec.get("faces") or []
    return faces[face].get("image", "") if face < len(faces) else ""


def image_url(name: str, face: int = 0, offline: bool = False) -> str:
    """カード画像の Scryfall の URL（無ければ ""）。画像の URL を持たない古いオラクルのキャッシュは取り直す。"""
    rec = lookup(name, offline=offline)
    if rec and not offline and "image" not in rec and not any("image" in f for f in rec.get("faces") or []):
        rec = lookup(name, refresh=True)
    if rec and face == 0:
        # 名前が片方の面だけのとき（"Treasure" が "Dinosaur // Treasure" に当たるなど）は、その面の画像
        for i, f in enumerate(rec.get("faces") or []):
            if f.get("name", "").casefold() == name.strip().casefold() and rec.get("name") != name:
                face = i
    url = _image_url(rec or {}, face)
    host = urllib.parse.urlparse(url).hostname or ""
    return url if url and host.endswith(IMAGE_HOSTS) else ""


def image(name: str, face: int = 0, offline: bool = False):
    """カード画像のキャッシュのパス。無ければ Scryfall から取って保存する（offline なら None）。"""
    p = image_path(name, face)
    if p.exists():
        return p
    if offline:
        return None
    url = image_url(name, face)
    if not url:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.replace(p)
    return p


SYMBOL_URL = "https://svgs.scryfall.io/card-symbols/%s.svg"
_SYMBOL = re.compile(r"^[A-Z0-9]{1,4}$")


def symbol(code: str, offline: bool = False):
    """マナ・シンボルの SVG のキャッシュのパス。code は {} と / を除いたもの（"G"、"WU"、"2W"、"T"）。"""
    code = code.replace("/", "").upper()
    if not _SYMBOL.match(code):
        return None
    p = cache_dir() / "symbols" / ("%s.svg" % code)
    if p.exists():
        return p
    if offline:
        return None
    req = urllib.request.Request(SYMBOL_URL % code, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.replace(p)
    return p


def fetch_deck_images(deck) -> dict:
    """デッキの全カードの画像を先に取っておく。{名前: エラー} を返す。"""
    errors = {}
    for _, _, name in _entries(deck):
        try:
            if image(name) is None:
                errors[name] = "no image"
        except (LookupError, OSError) as e:
            errors[name] = str(e)
    return errors


# ---------------------------------------------------------------- deck cache

def _entries(deck) -> list:
    return [("main", n, c) for n, c in deck.main] + [("sideboard", n, c) for n, c in deck.sideboard]


def deck_hash(deck) -> str:
    body = "\n".join("%s\t%d\t%s" % e for e in sorted(_entries(deck)))
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:8]


def deck_cache_path(deck) -> pathlib.Path:
    return cache_dir() / "decks" / ("%s-%s.json" % (_UNSAFE.sub("_", deck.name)[:60], deck_hash(deck)))


def load_deck_cache(deck):
    """中身が今のデッキリストと一致するキャッシュがあれば返す。"""
    p = deck_cache_path(deck)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if data.get("schema") == DECK_SCHEMA and data.get("hash") == deck_hash(deck) else None


def build_deck_cache(deck, fetch: bool = True, refresh: bool = False) -> dict:
    """デッキの全カードのオラクルを1ファイルに束ねる。

    既にあって欠けが無ければそのまま返す。無いカードは fetch=True なら Scryfall から取る
    （カード単位のキャッシュも同時に作られる）。取れなかったカードは missing に残る。
    """
    cached = None if refresh else load_deck_cache(deck)
    if cached and not cached["missing"]:
        return cached
    cards, missing, errors = {}, [], {}
    for _, _, name in _entries(deck):
        if name in cards or name in missing:
            continue
        try:
            rec = lookup(name, offline=not fetch, refresh=refresh)
        except (LookupError, OSError) as e:
            rec = None
            errors[name] = str(e)
        if rec is None:
            missing.append(name)
        else:
            cards[name] = rec
    data = {"schema": DECK_SCHEMA, "deck": deck.name, "hash": deck_hash(deck),
            "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "main": [[n, c] for n, c in deck.main], "sideboard": [[n, c] for n, c in deck.sideboard],
            "cards": cards, "missing": missing, "errors": errors}
    p = deck_cache_path(deck)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


REMINDER = re.compile(r" ?\([^()]*\)")


def format_card_compact(rec: dict, count=None, brief: bool = False) -> str:
    """1枚を「枚数 名前 コスト — タイプ P/T」＋字下げした本文で。brief なら注釈文（括弧内）を省く。"""
    def head(r):
        parts = ["<%s>" % r.get("name", "?")]
        if r.get("mana_cost"):
            parts.append(r["mana_cost"])
        parts.append("— " + r.get("type_line", ""))
        if r.get("power") is not None and r.get("toughness") is not None:
            parts.append("%s/%s" % (r["power"], r["toughness"]))
        for k in ("loyalty", "defense"):
            if r.get(k) is not None:
                parts.append("%s %s" % (k, r[k]))
        return " ".join(parts)

    def body(r):
        text = r.get("oracle_text") or ""
        if brief:
            text = REMINDER.sub("", text)
        return ["    " + line for line in text.splitlines() if line.strip()]

    prefix = "%d " % count if count else ""
    if rec.get("faces"):
        lines = [prefix + "<%s>" % rec.get("name", "?")]
        for f in rec["faces"]:
            lines.append("  / " + head(f))
            lines.extend(body(f))
        return "\n".join(lines)
    return "\n".join([prefix + head(rec)] + body(rec))


def format_deck(data: dict, sideboard: bool = True, brief: bool = False) -> str:
    out = ["# %s (%s)" % (data["deck"], data["hash"])]
    sections = [("Deck", data["main"])] + ([("Sideboard", data["sideboard"])] if sideboard else [])
    for title, entries in sections:
        if not entries:
            continue
        out.append("## %s (%d)" % (title, sum(n for n, _ in entries)))
        for n, name in entries:
            rec = data["cards"].get(name)
            out.append(format_card_compact(rec, n, brief) if rec else "%d <%s>  [not cached]" % (n, name))
    if data["missing"]:
        out.append("missing: " + ", ".join(data["missing"]))
    return "\n".join(out)
