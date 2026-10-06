"""Act / 手順 / Batch / Precondition（20〜23節）。

- Act: ルール上一体として行う処理の1セット（呪文を唱える、解決する、「捨てる。そうしたなら引く」）。
  途中で失敗したら丸ごと取り消す。log の1件・Undo の1単位。見てから選ぶときは cont でパートに分ける
  （パートごとに適用・log の1件。Undo は Act 単位）
- 手順（procedures.py）: 複数の Act の並びの省略（turn_start / turn_end）。順番が来たときの状態で展開する
- Batch: 往復を減らすための実行単位。ゲーム上の意味は持たない
- Precondition: AI が判断した時点の前提がまだ成り立っているかの確認（ルールの合法性ではない）

Batch が止まるのは、Act の失敗と前提不成立だけ。新しい意思決定が要る所（知らないカードを
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
                         resolve_zone, summarize_op, _single)
from .procedures import expand


class PreconditionFailed(Exception):
    pass


# ---------------------------------------------------------------- normalization

_ENTRY_KEYS = ("label", "pre", "actor", "proxy", "cont")


def normalize_entry(e, where: str = "act") -> dict:
    """Act の並びの要素1つを正規形にする。

    - {"act": [op, ...], "label"?, "pre"?, "actor"?, "proxy"?, "cont"?}  Act（パート）。cont: true は
      「この Act は次のパートに続く」（解決の途中で、新しく見た情報で選ぶために区切るとき）
    - {"proc": "turn_start", ...}                               手順（label / pre / actor / proxy も書ける）
    """
    if not isinstance(e, dict) or ("act" in e) == ("proc" in e):
        raise OperationError("%s must be an object with either 'act' or 'proc'" % where)
    pre = e.get("pre", [])
    out = {"label": e.get("label", ""), "pre": pre if isinstance(pre, list) else [pre]}
    if "act" in e:
        if not isinstance(e["act"], list) or not e["act"]:
            raise OperationError("%s has no ops" % where)
        out["act"] = e["act"]
        if e.get("cont"):
            out["cont"] = True
    else:
        if "cont" in e:
            raise OperationError("%s: cont is for an act, not a proc" % where)
        out["proc"] = {k: v for k, v in e.items() if k not in _ENTRY_KEYS}
    for k in ("actor", "proxy"):
        if k in e:
            out[k] = e[k]
    return out


def normalize_batch(raw) -> dict:
    """Batch {"actor", "label"?, "acts": [...]} を正規形にする（要素は normalize_entry の形）。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("acts"), list) or not raw["acts"]:
        raise OperationError("batch must be an object with a non-empty 'acts' list")
    out = []
    for i, e in enumerate(raw["acts"]):
        ng = normalize_entry(e, "act %d" % i)
        if "actor" in ng and "proxy" in ng:
            raise OperationError("act %d sets both actor and proxy" % i)
        if "actor" in ng and raw.get("actor") is not None:
            # Act ごとの操作者（AI 同士の対戦で、相手の手番まで1つの Batch に入れるとき）
            raise OperationError("act %d sets actor; per-act actors need the batch actor to be judge"
                                 " (to declare for another player, use \"proxy\")" % i)
        if "proxy" in ng:
            # 代理の宣言: 操作者（Batch の actor）が相手の宣言（ブロック・パスなど）を代わりに書く。
            # その Player として適用し log にも代理と残すが、結果（learned・id の伏せ字）は操作者から見た形で返す。
            # 妥当かどうかはエンジンは判定しない（指摘があれば AI が Undo で巻き戻す）
            if raw.get("actor") is None:
                raise OperationError("act %d sets proxy; proxy needs a player as the batch actor"
                                     " (a judge batch uses per-act \"actor\")" % i)
            if ng["proxy"] == raw.get("actor"):
                raise OperationError("act %d: proxy %s is the batch actor itself" % (i, ng["proxy"]))
        out.append(ng)
    return {"actor": raw.get("actor"), "label": raw.get("label", ""), "acts": out}


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
class ActResult:
    label: str
    status: str  # applied / failed / precondition_failed / skipped
    actor: Optional[str] = None
    proxy_by: Optional[str] = None  # 代理の宣言なら、代わりに書いた Player
    proc: str = ""  # 手順から展開した Act なら、その手順（"turn_start to=main1 2/4" など）
    error: str = ""
    results: list = field(default_factory=list)
    created: list = field(default_factory=list)
    aliases: dict = field(default_factory=dict)  # この Act で付けた・変わったエイリアス
    aliases_out: dict = field(default_factory=dict)
    learned: list = field(default_factory=list)  # 操作者が新しく知ったカード
    links_removed: list = field(default_factory=list)  # 領域移動で外れた Link
    steps: list = field(default_factory=list)  # 適用した基本の op と event（全情報。ログ用で AI には渡さない）
    version: int = 0

    @property
    def events(self) -> list:
        return [ev for st in self.steps for ev in st["events"]]

    def public(self) -> dict:
        d = {"status": self.status}
        for k in ("label", "proc", "actor", "proxy_by", "error", "created", "aliases", "learned",
                  "links_removed", "results"):
            v = getattr(self, k)
            if v:
                d[k] = v
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


def _summary(g: dict) -> str:
    return summarize_op({"op": g["proc"]["proc"], **g["proc"]}) if "proc" in g else \
        "; ".join(summarize_op(op) for op in g["act"])


class Engine:
    """GameState に Act / Batch を適用する。永続化は store.GameStore が担う。"""

    def __init__(self, state: GameState):
        self.state = state

    def _rng(self) -> random.Random:
        return random.Random("%s:%s" % (self.state.seed, self.state.version))

    def apply_act(self, actor: Optional[str], act: dict, aliases: Optional[dict] = None,
                  viewer: Optional[str] = "") -> ActResult:
        """Act を1つ、丸ごと適用するか丸ごと取り消す。aliases は Batch 内で前から引き継ぐもの。

        viewer は結果を受け取る Player（代理の宣言では操作者。省略で actor）。learned と id の伏せ字は viewer から見た形。"""
        s = self.state
        if actor is not None and actor not in s.players:
            raise OperationError("unknown actor %r" % actor)
        if viewer == "":
            viewer = actor
        res = ActResult(label=act.get("label", ""), status="applied", proc=act.get("proc_of", ""))
        before_state = s.clone()
        before_known = info.known_set(s, viewer)
        aliases_in = {k: list(v) for k, v in (aliases or {}).items()}
        ctx = Context(state=s, actor=actor, rng=self._rng(), aliases={k: list(v) for k, v in aliases_in.items()})
        try:
            for pre in act.get("pre", []):
                check_precondition(ctx, pre)
        except PreconditionFailed as e:
            res.status, res.error = "precondition_failed", str(e)
            return res
        try:
            for i, op in enumerate(act["act"]):
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
        res.steps = ctx.steps
        res.created = [x for x in ctx.created]
        # 無作為に非公開領域へ動いたカードなど、操作者が知り得ない id は結果に出さない
        res.results = _mask(s, viewer, res.results)
        res.aliases_out = dict(ctx.aliases)
        res.aliases = _mask(s, viewer, {k: v for k, v in ctx.aliases.items() if aliases_in.get(k) != v})
        res.links_removed = _mask(s, viewer, ctx.links_removed)
        created = set(ctx.created)
        for cid in sorted(info.known_set(s, viewer) - before_known - created,
                          key=lambda c: (s.cards[c].zone, c)):
            c = s.cards[cid]
            res.learned.append({"id": cid, "name": c.name, "zone": c.zone})
        return res

    def _expand(self, g: dict, actor: Optional[str]) -> list:
        """手順の要素 g を、今の状態で Act の並び（正規形）に展開する。手順の pre は最初の要素に、
        actor / proxy / label は全部に引き継ぐ。"""
        items = expand(Context(state=self.state, actor=actor), g["proc"])
        name = _summary(g)
        new = [normalize_entry(x, "%s: act %d" % (g["proc"]["proc"], i)) for i, x in enumerate(items)]
        for i, ng in enumerate(new):
            for k in ("actor", "proxy"):
                if k in g:
                    ng[k] = g[k]
            if g["label"] and not ng["label"]:
                ng["label"] = g["label"]
            if "act" in ng:
                ng["proc_of"] = "%s %d/%d" % (name, i + 1, len(new))
        if new:
            new[0]["pre"] = g["pre"] + new[0]["pre"]
        return new

    def apply_batch(self, raw) -> dict:
        """Batch を先頭から順に適用する。止まるのは失敗と前提不成立だけ。

        手順は、その順番が来たときの状態で Act の並びに展開してから適用する。
        優先権では止めない。相手の応答が要る場面では、AI が相手にパスの宣言を求めてから進める
        （宣言をもらわずに進めた場合、相手は割り込みたかった時点までの巻き戻しを請求できる）。"""
        batch = normalize_batch(raw)
        actor = batch["actor"]
        out = {"actor": actor, "label": batch["label"], "acts": [], "applied": 0, "stopped": None}
        applied = []
        queue = list(batch["acts"])
        aliases = {}

        def actor_of(g):
            return g.get("actor", g.get("proxy", actor))

        while queue:
            g = queue.pop(0)
            if "proc" in g:
                try:
                    queue[0:0] = self._expand(g, actor_of(g))
                except OperationError as e:
                    r = ActResult(label=g["label"], status="failed", proc=_summary(g), error=str(e))
                    out["acts"].append(r)
                    out["stopped"] = {"at": len(out["acts"]) - 1, "reason": r.status, "error": r.error}
                    break
                continue
            r = self.apply_act(actor_of(g), g, aliases, viewer=actor if "proxy" in g else "")
            if "actor" in g:
                r.actor = g["actor"]
            if "proxy" in g:
                r.actor, r.proxy_by = g["proxy"], actor
            out["acts"].append(r)
            if r.status != "applied":
                out["stopped"] = {"at": len(out["acts"]) - 1, "reason": r.status, "error": r.error}
                break
            aliases = r.aliases_out
            applied.append((g, r))
            out["applied"] += 1
        for g in queue:
            out["acts"].append(ActResult(label=g["label"] or _summary(g), status="skipped",
                                         proc=g.get("proc_of", "")))
        out["version"] = self.state.version
        out["_applied"] = applied  # store が log に書く
        return out


def public_result(result: dict) -> dict:
    """AI に返してよい形（events 等の全情報を除く）。"""
    return {"actor": result["actor"], "applied": result["applied"], "version": result["version"],
            "stopped": result["stopped"], "acts": [g.public() for g in result["acts"]]}
