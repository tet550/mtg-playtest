"""公開のサーバーのレート制限: 送り元（IP）ごと・所有者の鍵ごとに、種類ごとの回数を時間の窓で数える。

1台のプロセスの中だけで数える（メモリ。再起動で消える）。窓の中の回数が上限を超えたら、残り時間（Retry-After）を返す。
上限は RULES（種類 → (回数, 秒)）。環境変数 MTGTABLE_RATE_<種類>（例: MTGTABLE_RATE_WRITE=120/60）で変えられる。
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Optional

# 種類 → (窓の中で許す回数, 窓の秒数)
RULES = {
    "read": (600, 60),          # GET（盤面の再生・一覧・画像など。1画面で何本も取る）
    "write": (120, 60),         # 席の操作・デッキ・招待・共有などの書き込み全部
    "owner": (20, 3600),        # 所有者の鍵を新しく作る（Cookie の無いブラウザ）。IP ごと
    "deck_check": (30, 600),    # デッキの検査・登録（Scryfall に問い合わせる）
    "create": (30, 3600),       # 対局・招待・共有 URL を作る
    "recover": (10, 3600),      # 復元 URL を使う（鍵の当て推量を遅くする）
}


def rules_from_env(rules: Optional[dict] = None) -> dict:
    out = dict(rules or RULES)
    for kind in out:
        raw = os.environ.get("MTGTABLE_RATE_%s" % kind.upper(), "").strip()
        if raw:
            try:
                n, sec = raw.split("/")
                out[kind] = (int(n), float(sec))
            except ValueError:
                raise ValueError("MTGTABLE_RATE_%s must look like 120/60 (times/seconds)" % kind.upper())
    return out


class RateLimiter:
    def __init__(self, rules: Optional[dict] = None, clock=time.monotonic):
        self.rules = rules or rules_from_env()
        self.clock = clock
        self._hits: dict = {}  # (種類, 誰) → deque[時刻]
        self._lock = threading.Lock()
        self._sweep_at = clock()

    def hit(self, kind: str, who: str) -> float:
        """1回数える。通れば 0、超えていれば待つべき秒数（数えない）。"""
        limit, window = self.rules[kind]
        now = self.clock()
        with self._lock:
            q = self._hits.setdefault((kind, who), deque())
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return max(0.0, q[0] + window - now)
            q.append(now)
            if now - self._sweep_at > 300:  # たまに、空になった鍵を捨てる（メモリが増え続けないように）
                self._sweep(now)
            return 0.0

    def _sweep(self, now: float) -> None:
        for key in [k for k, q in self._hits.items() if not q or q[-1] <= now - self.rules[k[0]][1]]:
            del self._hits[key]
        self._sweep_at = now
