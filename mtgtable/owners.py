"""公開のサーバー（serve --site）の利用者: 所有者の鍵・復元 URL・対局の席との対応。アカウントは作らない。

- 所有者の鍵: 初めて来たブラウザに発行し、HttpOnly の Cookie に入れる。DB にはハッシュだけを置く。
  1人の所有者が複数の鍵（端末ごと）を持てる
- 復元の鍵: 別の端末・消えた Cookie に、同じ所有者を戻すための URL の鍵。作り直すと前の鍵は使えない。
  何度でも使える（端末を足すたびに使う）
- 参加者: 対局の席を持つ所有者。席の鍵（invite の URL）を初めて使った所有者が、その席を取る。取った後は Cookie だけで
  その席として読み書きでき、同じ席の鍵を他の人が使っても通らない
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Optional

from .sqlstore import _now, connect


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Owners:
    def __init__(self, db):
        self.db = db

    def _run(self, sql: str, args=(), many: bool = False):
        conn = connect(self.db)
        try:
            cur = conn.execute(sql, args)
            return cur.fetchall() if many else cur.fetchone()
        finally:
            conn.close()

    # ---- 所有者の鍵

    def create(self) -> tuple:
        """新しい所有者と、その鍵。(owner, key)。"""
        owner = secrets.token_hex(8)
        conn = connect(self.db)
        try:
            conn.execute("INSERT INTO owners (id, created_at) VALUES (?, ?)", (owner, _now()))
        finally:
            conn.close()
        return owner, self.add_key(owner)

    def add_key(self, owner: str) -> str:
        key = secrets.token_urlsafe(32)
        self._run("INSERT INTO owner_keys (key_sha256, owner, created_at) VALUES (?, ?, ?)", (_hash(key), owner, _now()))
        return key

    def owner_of(self, key: Optional[str]) -> Optional[str]:
        if not key:
            return None
        row = self._run("SELECT owner FROM owner_keys WHERE key_sha256 = ?", (_hash(key),))
        return row[0] if row else None

    # ---- 復元

    def new_recovery(self, owner: str) -> str:
        """復元の鍵を作り直して返す（前の鍵は使えなくなる）。"""
        token = secrets.token_urlsafe(32)
        self._run("UPDATE owners SET recovery_sha256 = ? WHERE id = ?", (_hash(token), owner))
        return token

    def recover(self, token: Optional[str]) -> Optional[str]:
        """復元の鍵の所有者に、この端末用の鍵を足す。(owner, key) か、鍵が違えば None。"""
        if not token:
            return None
        row = self._run("SELECT id FROM owners WHERE recovery_sha256 = ?", (_hash(token),))
        if not row:
            return None
        return row[0], self.add_key(row[0])

    # ---- 参加者

    def claim(self, game: str, seat: str, owner: str) -> str:
        """席がまだ誰のものでもなければ owner のものにする。席を持つ所有者を返す（先に取った人がいればその人）。"""
        self._run("INSERT OR IGNORE INTO participants (game, seat, owner, joined_at) VALUES (?, ?, ?, ?)",
                  (game, seat, owner, _now()))
        return self.seat_owner(game, seat)

    def seat_owner(self, game: str, seat: str) -> Optional[str]:
        row = self._run("SELECT owner FROM participants WHERE game = ? AND seat = ?", (game, seat))
        return row[0] if row else None

    def seats_of(self, owner: Optional[str], game: Optional[str] = None) -> dict:
        """owner が持つ席 {対局: 席}（game を渡すとその対局だけ）。"""
        if not owner:
            return {}
        if game is None:
            rows = self._run("SELECT game, seat FROM participants WHERE owner = ? ORDER BY joined_at", (owner,), many=True)
        else:
            rows = self._run("SELECT game, seat FROM participants WHERE owner = ? AND game = ?", (owner, game), many=True)
        return {g: s for g, s in rows}

    def seats_in(self, game: str) -> dict:
        """対局の、所有者が持つ席 {席: 所有者}（人間の席）。"""
        return {seat: o for seat, o in self._run("SELECT seat, owner FROM participants WHERE game = ?", (game,), many=True)}
