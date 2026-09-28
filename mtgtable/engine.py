"""ActionGroup / Batch / Precondition（20〜23節）。

- ActionGroup: ゲーム上意味のある一まとまりの Operation。途中で失敗したら丸ごと取り消す
- Batch: 往復を減らすための実行単位。ゲーム上の意味は持たない
- Precondition: AI が判断した時点の前提がまだ成り立っているかの確認（ルールの合法性ではない）

Batch が止まるのは、ActionGroup の失敗と前提不成立だけ。新しい意思決定が要る所（知らないカードを
見てから選ぶ、相手の応答を待つ）で Batch を区切るのは操作する側（AI）の責任で、エンジンは止めない。
操作者が新しく知ったカードは結果の learned で返す。エイリアス（"as"）は Batch の間ずっと有効。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from . import info
from .model import GameState
from .operations import (Context, OperationError, apply_operation, normalize_refs, resolve_cards,
                         resolve_zone, _single)


class PreconditionFailed(Exception):
    pass


# ---------------------------------------------------------------- normalization

def normalize_batch(raw) -> dict:
    """Batch の略記を正規形 {"actor", "groups": [{"label", "pre", "ops"}]} にする。

    受け付ける形:
    - [op, op, ...]                         Operation の列 = 1つの ActionGroup
    - {"ops": [...], ...}                   1つの ActionGroup
    - {"groups": [{...}, ...], "actor": p}  Batch
    """
    if isinstance(raw, list):
        raw = {"ops": raw}
    if not isinstance(raw, dict):
        raise OperationError("batch must be a JSON object or list")
    if "groups" in raw:
        groups = raw["groups"]
    elif "ops" in raw:
        groups = [{k: v for k, v in raw.items() if k not in ("actor",)}]
    else:
        raise OperationError("batch needs 'groups' or 'ops'")
    out = []
    for i, g in enumerate(groups):
        if isinstance(g, list):
            g = {"ops": g}
        ops = g.get("ops")
        if not isinstance(ops, list) or not ops:
            raise OperationError("group %d has no ops" % i)
        pre = g.get("pre", g.get("preconditions", []))
        ng = {"label": g.get("label", ""), "pre": pre if isinstance(pre, list) else [pre], "ops": ops}
        if "actor" in g:
            # ActionGroup ごとの操作者（AI 同士の対戦で、相手の手番まで1つの Batch に入れるとき）
            if raw.get("actor") is not None:
                raise OperationError("group %d sets actor; per-group actors need the batch actor to be judge" % i)
            ng["actor"] = g["actor"]
        out.append(ng)
    return {"actor": raw.get("actor"), "groups": out}


# ---------------------------------------------------------------- preconditions

def check_precondition(ctx: Context, pre: dict) -> None:
    """前提1件を確かめる。成り立たなければ PreconditionFailed。

    - {"version": 12}                                 状態が AI の見た時点から変わっていない
    - {"card": "c3", "zone": "p1.hand"}               カードの所在（index / tapped / face_down /
                                                        controller / entered_at も確かめられる）
    - {"zone": "p1.library", "top": ["c9", "c4"]}     順序付き領域の上から n 枚
    - {"zone": "p1.hand", "size": 7}                  枚数
    - {"player": "p2", "life": 5}                     ライフ
    - {"counter": "c3", "kind": "+1/+1", "amount": 2}  カウンター
    - {"stack_size": 1} / {"stack_top": "s4"}
    - {"turn": 3, "step": "main", "active": "p1", "priority": "p1"}
    知らないカードを前提に使うことはできない（情報漏洩を防ぐため）。
    """
    s = ctx.state

    def fail(msg):
        raise PreconditionFailed(msg)

    if not isinstance(pre, dict):
        raise OperationError("precondition must be an object: %r" % (pre,))
    pre = normalize_refs(s, pre)
    try:
        if "version" in pre and s.version != int(pre["version"]):
            fail("state version is %d, expected %s" % (s.version, pre["version"]))
        if "card" in pre:
            cid = pre["card"]
            if cid not in s.cards:
                fail("card %s no longer exists" % cid)
            resolve_cards(ctx, cid)  # 知り得ないカードなら OperationError
            c = s.cards[cid]
            if "zone" in pre:
                zn = resolve_zone(ctx, pre["zone"], c.owner)
                if c.zone != zn:
                    fail("%s is not in %s" % (cid, zn))
                if "index" in pre and s.zones[zn].cards.index(cid) != int(pre["index"]):
                    fail("%s is not at index %s of %s" % (cid, pre["index"], zn))
            for attr in ("tapped", "face_down", "controller", "entered_at"):
                if attr in pre and getattr(c, attr) != pre[attr]:
                    fail("%s.%s is %r, expected %r" % (cid, attr, getattr(c, attr), pre[attr]))
        elif "zone" in pre:
            zn = resolve_zone(ctx, pre["zone"], pre.get("owner"))
            cards = s.zones[zn].cards
            if "size" in pre and len(cards) != int(pre["size"]):
                fail("%s has %d cards, expected %s" % (zn, len(cards), pre["size"]))
            if "top" in pre:
                want = list(pre["top"])
                for cid in want:
                    if cid not in s.cards or not info.can_reference(s, ctx.actor, cid):
                        fail("%s is not known to %s" % (cid, ctx.actor))
                if cards[:len(want)] != want:
                    fail("top of %s is not %s" % (zn, want))
        if "player" in pre and "life" in pre:
            pl = s.players.get(pre["player"])
            if pl is None or pl.life != int(pre["life"]):
                fail("life of %s is %s, expected %s" % (pre["player"], pl and pl.life, pre["life"]))
        if "counter" in pre:
            target = _single(ctx, pre["counter"])
            have = s.counters_on(target).get(pre.get("kind"), 0)
            if have != int(pre.get("amount", 0)):
                fail("%s has %d %s counter(s), expected %s" % (target, have, pre.get("kind"), pre.get("amount")))
        if "stack_size" in pre and len(s.stack.items) != int(pre["stack_size"]):
            fail("stack has %d items, expected %s" % (len(s.stack.items), pre["stack_size"]))
        if "stack_top" in pre and (not s.stack.items or s.stack.items[0].id != pre["stack_top"]):
            fail("top of stack is not %s" % pre["stack_top"])
        for attr in ("turn", "phase", "step", "active", "priority"):
            if attr in pre and getattr(s.turn, attr) != pre[attr]:
                fail("turn.%s is %r, expected %r" % (attr, getattr(s.turn, attr), pre[attr]))
    except OperationError as e:
        raise PreconditionFailed(str(e))


# ---------------------------------------------------------------- engine

@dataclass
class GroupResult:
    label: str
    status: str  # applied / failed / precondition_failed / skipped
    actor: Optional[str] = None
    error: str = ""
    results: list = field(default_factory=list)
    created: list = field(default_factory=list)
    aliases: dict = field(default_factory=dict)  # この ActionGroup で付けた・変わったエイリアス
    aliases_in: dict = field(default_factory=dict)  # 開始時に有効だったエイリアス（Replay 用）
    aliases_out: dict = field(default_factory=dict)
    learned: list = field(default_factory=list)  # 操作者が新しく知ったカード
    links_removed: list = field(default_factory=list)  # 領域移動で外れた Link
    events: list = field(default_factory=list)  # 全情報を含む。ログ用で AI には渡さない
    version: int = 0

    def public(self) -> dict:
        d = {"label": self.label, "status": self.status}
        if self.actor is not None:
            d["actor"] = self.actor
        for k in ("error", "created", "aliases", "learned", "links_removed", "results"):
            v = getattr(self, k)
            if v:
                d[k] = v
        if self.status == "applied":
            d["version"] = self.version
        return d


def _mask(state: GameState, actor: Optional[str], value):
    if isinstance(value, str):
        if value in state.cards and not info.can_reference(state, actor, value):
            return "hidden"
        return value
    if isinstance(value, list):
        return [_mask(state, actor, v) for v in value]
    if isinstance(value, dict):
        return {k: _mask(state, actor, v) for k, v in value.items()}
    return value


class Engine:
    """GameState に ActionGroup / Batch を適用する。永続化は store.GameStore が担う。"""

    def __init__(self, state: GameState):
        self.state = state

    def _rng(self) -> random.Random:
        return random.Random("%s:%s" % (self.state.seed, self.state.version))

    def apply_group(self, actor: Optional[str], group: dict, aliases: Optional[dict] = None) -> GroupResult:
        """ActionGroup を1つ、丸ごと適用するか丸ごと取り消す。aliases は Batch 内で前から引き継ぐもの。"""
        s = self.state
        if actor is not None and actor not in s.players:
            raise OperationError("unknown actor %r" % actor)
        res = GroupResult(label=group.get("label", ""), status="applied")
        before_state = s.clone()
        before_known = info.known_set(s, actor)
        res.aliases_in = {k: list(v) for k, v in (aliases or {}).items()}
        ctx = Context(state=s, actor=actor, rng=self._rng(), aliases={k: list(v) for k, v in res.aliases_in.items()})
        try:
            for pre in group.get("pre", []):
                check_precondition(ctx, pre)
        except PreconditionFailed as e:
            res.status, res.error = "precondition_failed", str(e)
            return res
        try:
            for i, op in enumerate(group["ops"]):
                try:
                    res.results.append(apply_operation(ctx, op))
                except OperationError as e:
                    raise OperationError("op %d (%s): %s" % (i, op.get("op") if isinstance(op, dict) else op, e))
        except OperationError as e:
            self.state = before_state
            res.status, res.error, res.results = "failed", str(e), []
            return res
        s.version += 1
        res.version = s.version
        res.events = ctx.events
        res.created = [x for x in ctx.created]
        # 無作為に非公開領域へ動いたカードなど、操作者が知り得ない id は結果に出さない
        res.results = _mask(s, actor, res.results)
        res.aliases_out = dict(ctx.aliases)
        res.aliases = _mask(s, actor, {k: v for k, v in ctx.aliases.items() if res.aliases_in.get(k) != v})
        res.links_removed = _mask(s, actor, ctx.links_removed)
        created = set(ctx.created)
        for cid in sorted(info.known_set(s, actor) - before_known - created,
                          key=lambda c: (s.cards[c].zone, c)):
            c = s.cards[cid]
            res.learned.append({"id": cid, "name": c.name, "zone": c.zone})
        return res

    def apply_batch(self, raw) -> dict:
        """Batch を先頭から順に適用する。止まるのは失敗と前提不成立だけ。

        優先権では止めない。相手の応答が要る場面では、AI が相手にパスの宣言を求めてから進める
        （宣言をもらわずに進めた場合、相手は割り込みたかった時点までの巻き戻しを請求できる）。"""
        batch = normalize_batch(raw)
        actor = batch["actor"]
        out = {"actor": actor, "groups": [], "applied": 0, "stopped": None}
        applied = []
        groups = batch["groups"]
        aliases = {}

        def actor_of(g):
            return g.get("actor", actor)

        for i, g in enumerate(groups):
            r = self.apply_group(actor_of(g), g, aliases)
            if "actor" in g:
                r.actor = g["actor"]
            out["groups"].append(r)
            if r.status != "applied":
                out["stopped"] = {"at": i, "reason": r.status, "error": r.error}
                break
            aliases = r.aliases_out
            applied.append((g, r))
            out["applied"] += 1
        skip_from = out["stopped"]["at"] + 1 if out["stopped"] else len(groups)
        for g in groups[skip_from:]:
            out["groups"].append(GroupResult(label=g.get("label", ""), status="skipped"))
        out["version"] = self.state.version
        out["_applied"] = applied  # store が log に書く
        return out


def public_result(result: dict) -> dict:
    """AI に返してよい形（events 等の全情報を除く）。"""
    return {"actor": result["actor"], "applied": result["applied"], "version": result["version"],
            "stopped": result["stopped"], "groups": [g.public() for g in result["groups"]]}
