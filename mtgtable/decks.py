"""デッキ登録（公開のサーバー）: デッキリストの検査と、所有者ごとの保存。

検査は登録・更新のたびに行い、通ったものだけを保存する。
- 書式: decklists/README.md と同じ（"4 Forest"、Deck / Sideboard 見出し、MTG Arena の書き出し）。読めない行は行番号つきで返す
- カード名: Scryfall の /cards/collection でまとめて引く（carddb.resolve_names）。見つからない名前は行番号つきで返す
- 構築: メイン 60 枚以上・サイドボード 15 枚以下・同じ名前は 4 枚まで（基本土地と「好きな枚数を入れてよい」カードは除く）
- フォーマット: Scryfall の legalities で、そのフォーマットで使えるか（free は見ない）。制限カード（restricted）は 1 枚まで
- 入力の大きさ: 文字数・行数・デッキ名の形・1人が登録できる数

デッキは表 decks（所有者・名前・フォーマット・本文・プレイ方針・正式なカード名の対応）。プレイ方針は、対局で
そのデッキを使う AI のプロンプトに入る（prompt.seat_system）。
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
from collections import Counter
from typing import Optional

from . import carddb
from .setup import _LINE, _MAIN, _SET_SUFFIX, _SIDE, _SKIP, Decklist
from .sqlstore import _now, connect

FORMATS = ("standard", "pioneer", "modern", "legacy", "vintage", "pauper", "historic", "timeless", "free")
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
MAX_TEXT = 20000
MAX_LINES = 250
MAX_COPIES_IN_LINE = 99
MAX_STRATEGY = 8000
MAX_DECKS = 50  # 1人が登録できる数
MIN_MAIN = 60
MAX_SIDE = 15
COPIES = 4
_ANY_NUMBER = "a deck can have any number of cards named"
_UP_TO = re.compile(r"a deck can have up to (\w+) cards named")
_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

SCHEMA = """
CREATE TABLE IF NOT EXISTS decks (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    format TEXT NOT NULL,
    text TEXT NOT NULL,
    strategy TEXT NOT NULL DEFAULT '',
    main INTEGER NOT NULL,
    sideboard INTEGER NOT NULL,
    cards TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (owner, name)
);
CREATE INDEX IF NOT EXISTS decks_owner ON decks (owner, updated_at);
"""


class Invalid(ValueError):
    """検査に通らなかった（サーバーは 400 で、errors を返す）。"""

    def __init__(self, result: dict):
        super().__init__("; ".join(e["message"] for e in result["errors"][:3]))
        self.result = result


def _error(message: str, line: Optional[int] = None, card: Optional[str] = None) -> dict:
    return {"line": line, "card": card, "message": message}


def parse(text: str) -> tuple:
    """(Decklist, 各エントリの行番号 [(区分, 枚数, 名前, 行)], errors)。setup.parse_decklist と同じ読み方で、
    読めない行は止めずに行番号つきの errors にする。"""
    deck, rows, errors = Decklist(name="deck"), [], []
    section = "main"
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        head = line.rstrip(":").strip().casefold()
        if head in _MAIN or head in _SIDE or head in _SKIP:
            section = "main" if head in _MAIN else "sideboard" if head in _SIDE else None
            continue
        if section is None:
            continue
        m = _LINE.match(line)
        if not m:
            errors.append(_error("読めない行です（「4 Forest」の形で書く）", i))
            continue
        n, card = int(m.group(1)), _SET_SUFFIX.sub("", m.group(2)).strip()
        if not 1 <= n <= MAX_COPIES_IN_LINE:
            errors.append(_error("枚数は 1〜%d で書く" % MAX_COPIES_IN_LINE, i, card))
            continue
        getattr(deck, section).append((n, card))
        rows.append((section, n, card, i))
    return deck, rows, errors


def _copy_limit(rec: dict, fmt: str) -> Optional[int]:
    """同じ名前を入れてよい枚数（無制限なら None）。"""
    if "Basic" in (rec.get("type_line") or "") and "Land" in (rec.get("type_line") or ""):
        return None
    text = " ".join([rec.get("oracle_text") or ""] + [f.get("oracle_text") or "" for f in rec.get("faces") or []]).lower()
    if _ANY_NUMBER in text:
        return None
    m = _UP_TO.search(text)
    if m and m.group(1) in _WORDS:
        return _WORDS[m.group(1)]
    if fmt != "free" and (rec.get("legalities") or {}).get(fmt) == "restricted":
        return 1
    return COPIES


def table_name(written: str, rec: dict) -> str:
    """卓に置く名前: 書いた名前がそのカードの名前（両面カードの表の面など）なら、大文字・小文字と空白だけ正式に直したもの。
    リポジトリのデッキリストと同じく、両面カードは表の面の名前になる（「Delver of Secrets」）。"""
    key = carddb._norm(written)
    return next((n for n in carddb.card_names(rec) if carddb._norm(n) == key), rec["name"])


def check(text, fmt: str = "standard", resolve=None) -> dict:
    """デッキリストを検査する。{"ok", "errors": [{"line", "card", "message"}], "warnings", "main", "sideboard",
    "cards": {書いた名前: 正式な名前}}。resolve はカード名を引く関数（既定は carddb.resolve_names。テストで差し替える）。"""
    resolve = resolve or carddb.resolve_names
    out = {"ok": False, "errors": [], "warnings": [], "main": 0, "sideboard": 0, "cards": {}}
    if not isinstance(text, str) or not text.strip():
        out["errors"].append(_error("デッキリストが空です"))
        return out
    if len(text) > MAX_TEXT or len(text.splitlines()) > MAX_LINES:
        out["errors"].append(_error("デッキリストが長すぎます（%d 文字・%d 行まで）" % (MAX_TEXT, MAX_LINES)))
        return out
    if fmt not in FORMATS:
        out["errors"].append(_error("フォーマットは %s のどれか" % " / ".join(FORMATS)))
        return out
    deck, rows, errors = parse(text)
    out["errors"] += errors
    out["main"], out["sideboard"] = deck.main_count, sum(n for n, _ in deck.sideboard)
    if out["main"] < MIN_MAIN:
        out["errors"].append(_error("メインデッキが %d 枚です（%d 枚以上）" % (out["main"], MIN_MAIN)))
    if out["sideboard"] > MAX_SIDE:
        out["errors"].append(_error("サイドボードが %d 枚です（%d 枚まで）" % (out["sideboard"], MAX_SIDE)))
    if not rows:
        out["ok"] = not out["errors"]
        return out
    try:
        res = resolve([card for _, _, card, _ in rows], need_legalities=fmt != "free")
    except LookupError as e:
        out["errors"].append(_error("カード名を確かめられませんでした（Scryfall: %s）。少し待ってからやり直す" % e))
        return out
    first_line = {}
    for _, _, card, line in rows:
        first_line.setdefault(card, line)
    for card in res["missing"]:
        out["errors"].append(_error("「%s」というカードが見つかりません（英語名で書く）" % card, first_line[card], card))
    found = res["found"]
    out["cards"] = {card: table_name(card, found[card]) for card in first_line if card in found}
    total, line_of = Counter(), {}
    for _, n, card, line in rows:
        if card in found:
            total[found[card]["name"]] += n
            line_of.setdefault(found[card]["name"], (line, found[card]))
    for name, n in total.items():
        line, rec = line_of[name]
        limit = _copy_limit(rec, fmt)
        if limit is not None and n > limit:
            out["errors"].append(_error("「%s」が %d 枚です（%d 枚まで）" % (name, n, limit), line, name))
        if fmt != "free":
            legal = (rec.get("legalities") or {}).get(fmt)
            if legal is None:
                out["warnings"].append(_error("「%s」が %s で使えるか分かりませんでした" % (name, fmt), line, name))
            elif legal not in ("legal", "restricted"):
                why = "禁止" if legal == "banned" else "使えない"
                out["errors"].append(_error("「%s」は %s では%sカードです" % (name, fmt, why), line, name))
    out["errors"].sort(key=lambda e: (e["line"] is not None, e["line"] or 0))
    out["ok"] = not out["errors"]
    return out


def decklist(text: str, cards: dict, name: str) -> Decklist:
    """登録したデッキの本文から、正式なカード名の Decklist（対局を作るとき）。"""
    deck, _, _ = parse(text)
    deck.name = name
    deck.main = [(n, cards.get(c, c)) for n, c in deck.main]
    deck.sideboard = [(n, cards.get(c, c)) for n, c in deck.sideboard]
    return deck


class Decks:
    """所有者ごとのデッキ（SQLite）。"""

    def __init__(self, db, resolve=None):
        self.db = db
        self.resolve = resolve  # カード名を引く関数（None で carddb.resolve_names）
        conn = connect(db)
        try:
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    def _run(self, sql: str, args=(), many: bool = False):
        conn = connect(self.db)
        try:
            cur = conn.execute(sql, args)
            return cur.fetchall() if many else cur.fetchone()
        finally:
            conn.close()

    def _write(self, sql: str, args) -> None:
        try:
            self._run(sql, args)
        except sqlite3.IntegrityError:  # 同じ名前を同時に登録した
            raise Invalid({"ok": False, "errors": [_error("同じ名前のデッキがあります")], "warnings": []})

    @staticmethod
    def _row(r, full: bool = False) -> dict:
        d = {"id": r[0], "name": r[1], "format": r[2], "main": r[3], "sideboard": r[4], "updated": r[5]}
        if full:
            d.update({"text": r[6], "strategy": r[7], "cards": json.loads(r[8])})
        return d

    _COLS = "id, name, format, main, sideboard, updated_at, text, strategy, cards"

    def list(self, owner: str) -> list:
        rows = self._run("SELECT %s FROM decks WHERE owner = ? ORDER BY updated_at DESC, name" % self._COLS,
                         (owner,), many=True)
        return [self._row(r) for r in rows]

    def get(self, owner: str, deck_id: str) -> dict:
        r = self._run("SELECT %s FROM decks WHERE owner = ? AND id = ?" % self._COLS, (owner, deck_id))
        if r is None:
            raise LookupError("no deck %r" % deck_id)
        return self._row(r, full=True)

    def _validate(self, owner: str, body: dict, deck_id: Optional[str] = None) -> tuple:
        name = body.get("name")
        fmt = body.get("format") or "standard"
        strategy = body.get("strategy") or ""
        pre = []
        if not isinstance(name, str) or not NAME.match(name):
            pre.append(_error("デッキ名は半角英小文字・数字・ハイフン（40 文字まで。例: mono-red）"))
        elif self._run("SELECT id FROM decks WHERE owner = ? AND name = ? AND id != ?", (owner, name, deck_id or "")):
            pre.append(_error("同じ名前のデッキがあります"))
        if not isinstance(strategy, str) or len(strategy) > MAX_STRATEGY:
            pre.append(_error("プレイ方針は %d 文字まで" % MAX_STRATEGY))
        result = check(body.get("text"), fmt, self.resolve)
        result["errors"] = pre + result["errors"]
        result["ok"] = not result["errors"]
        if not result["ok"]:
            raise Invalid(result)
        return name, fmt, strategy, result

    def create(self, owner: str, body: dict) -> dict:
        if self._run("SELECT COUNT(*) FROM decks WHERE owner = ?", (owner,))[0] >= MAX_DECKS:
            raise Invalid({"ok": False, "errors": [_error("デッキは %d 個まで（使わないものを消す）" % MAX_DECKS)],
                           "warnings": []})
        name, fmt, strategy, result = self._validate(owner, body)
        deck_id, now = secrets.token_hex(6), _now()
        self._write("INSERT INTO decks (id, owner, name, format, text, strategy, main, sideboard, cards, created_at, "
                  "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (deck_id, owner, name, fmt, body["text"], strategy, result["main"], result["sideboard"],
                   json.dumps(result["cards"], ensure_ascii=False), now, now))
        return dict(self.get(owner, deck_id), warnings=result["warnings"])

    def update(self, owner: str, deck_id: str, body: dict) -> dict:
        self.get(owner, deck_id)
        name, fmt, strategy, result = self._validate(owner, body, deck_id)
        self._write("UPDATE decks SET name = ?, format = ?, text = ?, strategy = ?, main = ?, sideboard = ?, cards = ?, "
                  "updated_at = ? WHERE owner = ? AND id = ?",
                  (name, fmt, body["text"], strategy, result["main"], result["sideboard"],
                   json.dumps(result["cards"], ensure_ascii=False), _now(), owner, deck_id))
        return dict(self.get(owner, deck_id), warnings=result["warnings"])

    def delete(self, owner: str, deck_id: str) -> None:
        self.get(owner, deck_id)
        self._run("DELETE FROM decks WHERE owner = ? AND id = ?", (owner, deck_id))
