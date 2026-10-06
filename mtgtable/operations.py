"""Operation（19節）: 卓上で行う基本操作。

カード効果そのものを表す高レベルな処理は持たない。各 Operation は
「カードを動かす」「カウンターを置く」のような紙でもできる操作だけを行い、
合法性（唱えられるか・対象は適正か）は判定しない。

検査するのは状態の整合性だけ:
- 指定したオブジェクトが存在するか
- 操作する Player が、指定したカードを知り得るか（非公開情報の漏洩防止）
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import info
from .model import (
    AttackAssignment, BlockAssignment, Card, Counter, Declaration, GameState, Link, Mana,
    Note, StackItem, TURN_SEQUENCE, NO_PRIORITY_STEPS, PREGAME, PLAYER_ZONES, zone_name,
)


class OperationError(Exception):
    """Operation を適用できない。Act ごと取り消される。"""


@dataclass
class Context:
    state: GameState
    actor: Optional[str]  # Player id。None は全知の Judge / Orchestrator / システム
    rng: random.Random = None  # Act ごとに seed と version から決まる（Replay で同じ結果になる）
    aliases: dict = field(default_factory=dict)  # "$name" -> list of ids
    events: list = field(default_factory=list)
    created: list = field(default_factory=list)
    links_removed: list = field(default_factory=list)  # 領域移動で外れた Link（AI が張り直せるように返す）
    # 実際に適用した基本の op（複合 op は展開し、エイリアスは id に置き換えたもの）。log に残し、Replay はこれだけを使う
    steps: list = field(default_factory=list)
    parents: list = field(default_factory=list)  # 今展開している複合 op の名前

    def event(self, text: str) -> None:
        self.events.append(text)


# ---------------------------------------------------------------- references

_BARE_ID = re.compile(r"^[a-z]+\d+$")
# 自由記述の値。id の補完をしない
_TEXT_KEYS = {"text", "name", "kind", "until", "label", "definition", "color", "duration", "zone",
              "to", "card_to", "status", "phase", "step", "position", "keep", "as", "op", "order"}


def normalize_refs(state: GameState, value, key=None):
    """"c12" のように # を省いた id を "#c12" に補う（そのオブジェクトが存在するときだけ）。"""
    if key in _TEXT_KEYS:
        return value
    if isinstance(value, str):
        if _BARE_ID.match(value) and state.object_kind("#" + value):
            return "#" + value
        return value
    if isinstance(value, list):
        return [normalize_refs(state, v) for v in value]
    if isinstance(value, dict):
        return {k: normalize_refs(state, v, k) for k, v in value.items()}
    return value


def _player(ctx: Context, pid) -> str:
    if pid in (None, "me", "$me"):
        if ctx.actor is None:
            raise OperationError("player must be given explicitly when acting as judge")
        return ctx.actor
    if pid == "active":
        return ctx.state.turn.active
    pid = _single(ctx, pid) if isinstance(pid, str) and pid.startswith("$") else pid
    if pid not in ctx.state.players:
        raise OperationError("unknown player %r" % (pid,))
    return pid


def resolve_zone(ctx: Context, name: str, owner: Optional[str] = None) -> str:
    """"p1.hand" / "battlefield" / "hand"（owner または操作者の領域）を完全名にする。"""
    if not isinstance(name, str):
        raise OperationError("zone must be a string, got %r" % (name,))
    if name in ctx.state.zones:
        return name
    if name in PLAYER_ZONES:
        who = owner or ctx.actor
        if who is None:
            raise OperationError("zone %r needs an owner (use e.g. p1.%s)" % (name, name))
        full = zone_name(who, name)
        if full in ctx.state.zones:
            return full
    raise OperationError("unknown zone %r" % (name,))


def _check_ref(ctx: Context, cid: str) -> str:
    if cid not in ctx.state.cards:
        raise OperationError("unknown card %r" % (cid,))
    if not info.can_reference(ctx.state, ctx.actor, cid):
        raise OperationError("card %s is not known to %s" % (cid, ctx.actor))
    return cid


def resolve_cards(ctx: Context, ref) -> list:
    """カード参照を id のリストにする。

    - "#c12"（# は省略可）                 id
    - "$tok"                             同じ Batch 内で "as" に付けた名前
    - ["#c1", "#c2"]                     複数
    - {"zone": "p1.library", "top": 2}   上から n 枚（bottom: n も可）
    - {"zone": "p1.library", "index": 0} 位置指定（負数は下から）
    - {"zone": "p2.hand", "random": 1}   無作為に n 枚
    - {"zone": "p1.hand", "all": true}   全部
    - {"zone": "p1.hand", "name": "Forest", "count": 1}
                                         知っているカードのうち名前が一致するもの
    位置・無作為の指定は中身を知らないカードにも使える（紙で上から引くのと同じ）。
    """
    s = ctx.state
    if ref is None:
        raise OperationError("card reference is required")
    if isinstance(ref, list):
        out = []
        for r in ref:
            out.extend(resolve_cards(ctx, r))
        return out
    if isinstance(ref, str):
        if ref.startswith("$"):
            if ref[1:] not in ctx.aliases:
                raise OperationError("unknown alias %r" % (ref,))
            return [cid for cid in ctx.aliases[ref[1:]] if cid in s.cards] or _missing(ref)
        return [_check_ref(ctx, ref)]
    if not isinstance(ref, dict) or "zone" not in ref:
        raise OperationError("bad card reference %r" % (ref,))
    zn = resolve_zone(ctx, ref["zone"], ref.get("owner"))
    cards = list(s.zones[zn].cards)
    if "top" in ref:
        n = int(ref["top"])
        if n > len(cards):
            raise OperationError("%s has only %d cards (asked top %d)" % (zn, len(cards), n))
        return cards[:n]
    if "bottom" in ref:
        n = int(ref["bottom"])
        if n > len(cards):
            raise OperationError("%s has only %d cards (asked bottom %d)" % (zn, len(cards), n))
        return cards[len(cards) - n:]
    if "index" in ref:
        i = int(ref["index"])
        if not -len(cards) <= i < len(cards):
            raise OperationError("%s has no card at index %d" % (zn, i))
        return [cards[i]]
    if "random" in ref:
        n = int(ref["random"])
        if n > len(cards):
            raise OperationError("%s has only %d cards (asked random %d)" % (zn, len(cards), n))
        return ctx.rng.sample(cards, n)
    if ref.get("all"):
        return cards
    if "name" in ref:
        n = int(ref.get("count", 1))
        hits = [cid for cid in cards if s.cards[cid].name == ref["name"]
                and info.knows_identity(s, ctx.actor, cid)]
        if len(hits) < n:
            raise OperationError("found %d known %r in %s (need %d)" % (len(hits), ref["name"], zn, n))
        return hits[:n]
    raise OperationError("card reference %r needs top/bottom/index/random/all/name" % (ref,))


def _missing(ref):
    raise OperationError("alias %s no longer refers to anything" % ref)


def _single(ctx: Context, ref) -> str:
    """単一オブジェクト参照。カード以外（Player / Mana / StackItem / Link / Note）も許す。"""
    s = ctx.state
    if isinstance(ref, str) and ref.startswith("$"):
        ids = ctx.aliases.get(ref[1:])
        if not ids:
            raise OperationError("unknown alias %r" % (ref,))
        if len(ids) != 1:
            raise OperationError("alias %s refers to %d objects; expected one" % (ref, len(ids)))
        ref = ids[0]
        if ref not in s.cards and s.object_kind(ref):
            return ref
    if isinstance(ref, str) and ref not in s.cards:
        if s.object_kind(ref) is None:
            raise OperationError("unknown object %r" % (ref,))
        return ref
    ids = resolve_cards(ctx, ref)
    if len(ids) != 1:
        raise OperationError("reference %r matched %d cards; expected one" % (ref, len(ids)))
    return ids[0]


def _objects(ctx: Context, ref) -> list:
    if isinstance(ref, list):
        return [_single(ctx, r) for r in ref]
    if isinstance(ref, dict):
        return resolve_cards(ctx, ref)
    if isinstance(ref, str) and ref.startswith("$"):
        return list(ctx.aliases.get(ref[1:], [])) or _missing(ref)
    return [_single(ctx, ref)]


def _alias(ctx: Context, params: dict, ids: list) -> None:
    ctx.created.extend(ids)
    if params.get("as"):
        ctx.aliases[params["as"]] = list(ids)


def _label(state: GameState, cid: str) -> str:
    c = state.cards.get(cid)
    return "%s <%s>" % (cid, c.name) if c else cid


def _labels(state: GameState, ids) -> str:
    """ログのイベント用の一覧: "#c3 <Forest>, #c9 <Grizzly Bears>"（list の repr にしない）。"""
    return ", ".join(_label(state, c) for c in ids) or "-"


# ---------------------------------------------------------------- card movement

def _drop_links(ctx: Context, cid: str) -> None:
    """cid が関わる Link を外す。source なら Link ごと、targets の1つなら cid だけ外す
    （targets が空になれば Link ごと）。外した Link は結果で AI に知らせる。"""
    s = ctx.state
    for l in list(s.links.values()):
        if l.source != cid and cid not in l.targets:
            continue
        ctx.links_removed.append({"id": l.id, "kind": l.kind, "source": l.source,
                                  "targets": list(l.targets), "text": l.text, "because": cid})
        if l.source == cid:
            del s.links[l.id]
            ctx.event("link %s removed (%s changed zones)" % (l.id, cid))
        else:
            l.targets.remove(cid)
            if not l.targets:
                del s.links[l.id]
                ctx.event("link %s removed (%s changed zones)" % (l.id, cid))
            else:
                ctx.event("link %s lost target %s" % (l.id, cid))


def _detach_object(ctx: Context, card: Card, keep: set) -> None:
    """領域を移ったカードは新しいオブジェクト（CR 400.7）。付いていた Counter / Note / Link を外す。"""
    s = ctx.state
    if "counters" not in keep:
        dropped = [c for c in s.counters if c.target == card.id]
        if dropped:
            s.counters = [c for c in s.counters if c.target != card.id]
            ctx.event("counters removed from %s: %s" % (card.id, {c.kind: c.amount for c in dropped}))
    if "notes" not in keep:
        for n in s.notes_on(card.id):
            del s.notes[n.id]
            ctx.event("note %s removed from %s" % (n.id, card.id))
    if "links" not in keep:
        _drop_links(ctx, card.id)


def _detach_from_battlefield(ctx: Context, card: Card) -> None:
    """戦場を離れたカードを起こし、表に戻し、戦闘から外す。"""
    s = ctx.state
    card.tapped = False
    card.face = 0
    before = len(s.combat.attacks) + len(s.combat.blocks)
    s.combat.attacks = [a for a in s.combat.attacks if a.attacker != card.id and a.target != card.id]
    live = {a.attacker for a in s.combat.attacks}
    s.combat.blocks = [b for b in s.combat.blocks
                       if b.blocker != card.id and b.attacker != card.id and b.attacker in live]
    if before != len(s.combat.attacks) + len(s.combat.blocks):
        ctx.event("%s removed from combat" % card.id)


def move_card(ctx: Context, cid: str, to: str, position=None, face_down=None,
              tapped=None, controller=None, keep=()) -> None:
    s = ctx.state
    card = s.cards[cid]
    src = s.zones[card.zone]
    dst = s.zones[to]
    src.cards.remove(cid)
    if src.kind == "stack":
        for it in [it for it in s.stack.items if it.card == cid]:
            s.stack.items.remove(it)
            ctx.event("stack item %s left the stack with its card" % it.id)
    if src.name != dst.name:
        _detach_object(ctx, card, set(keep))
    if src.kind == "battlefield" and dst.kind != "battlefield":
        _detach_from_battlefield(ctx, card)
    if dst.kind != "battlefield" and src.kind != dst.kind:
        card.controller = card.owner if dst.kind != "stack" else card.controller
    # 置き場所
    if position in (None, "top"):
        idx = 0 if dst.ordered else len(dst.cards)
    elif position == "bottom":
        idx = len(dst.cards)
    else:
        idx = int(position)
        if idx < 0:
            idx = len(dst.cards) + 1 + idx
        if not 0 <= idx <= len(dst.cards):
            raise OperationError("position %s out of range for %s" % (position, to))
    dst.cards.insert(idx, cid)
    card.zone = to
    if src.name != dst.name:
        card.entered_at = s.tick()
        if dst.kind == "battlefield":
            card.controlled_since = card.entered_at
    if face_down is not None:
        card.face_down = bool(face_down)
    elif src.name != dst.name:
        card.face_down = False
    if tapped is not None:
        card.tapped = bool(tapped)
    if controller is not None:
        if dst.kind == "battlefield" and controller != card.controller and src.name == dst.name:
            card.controlled_since = s.tick()
        card.controller = controller
    # 公開された移動なので、中身を知っている者は行き先の位置も分かる
    for k in s.knowledge.values():
        if cid in k:
            k[cid]["ordered"] = True
    ctx.event("move %s: %s -> %s%s" % (_label(s, cid), src.name, to,
                                        "" if position in (None, "top") else " @%s" % position))


def op_move(ctx: Context, p: dict) -> dict:
    """カードを別の領域へ動かす。to が "graveyard" 等の個人領域なら各カードの持ち主の領域。
    order: "random" で、複数枚を無作為の順で置く（「ライブラリーの一番下に無作為の順で」）。"""
    ids = resolve_cards(ctx, p.get("card", p.get("cards")))
    if "to" not in p:
        raise OperationError("move needs 'to'")
    controller = _player(ctx, p["controller"]) if p.get("controller") else None
    pos = p.get("position")
    shuffled = p.get("order") == "random"
    if p.get("order") not in (None, "random", "given"):
        raise OperationError("order must be random or given")
    placed = list(ids)
    if shuffled:
        ctx.rng.shuffle(placed)
    # 複数枚を "top" に置くときは、指定順の先頭が一番上になるように逆順で積む
    for cid in (list(reversed(placed)) if pos in (None, "top") else placed):
        to = resolve_zone(ctx, p["to"], ctx.state.cards[cid].owner)
        move_card(ctx, cid, to, pos, p.get("face_down"), p.get("tapped"), controller, p.get("keep", ()))
    if shuffled and len(ids) > 1:
        # 無作為の順なので、中身を知っていても誰もどれがどこかは分からない
        for k in ctx.state.knowledge.values():
            for cid in ids:
                if cid in k:
                    k[cid]["ordered"] = False
        ctx.event("placed in random order: %s" % _labels(ctx.state, placed))
    if p.get("as"):
        ctx.aliases[p["as"]] = ids
    return {"cards": ids}


def op_draw(ctx: Context, p: dict) -> dict:
    """ライブラリーの一番上から手札へ。空のライブラリーから引こうとしたら記録だけ残す。"""
    pid = _player(ctx, p.get("player"))
    n = int(p.get("count", 1))
    lib = ctx.state.zones[zone_name(pid, "library")]
    drawn = []
    for _ in range(n):
        if not lib.cards:
            ctx.event("%s attempted to draw from an empty library" % pid)
            break
        cid = lib.cards[0]
        move_card(ctx, cid, zone_name(pid, "hand"))
        drawn.append(cid)
    if p.get("as"):
        ctx.aliases[p["as"]] = drawn
    return {"cards": drawn, "short": n - len(drawn)}


def op_shuffle(ctx: Context, p: dict) -> dict:
    """領域をシャッフルする（既定は操作者のライブラリー）。全員、その中の位置の記憶を失う。"""
    zn = resolve_zone(ctx, p.get("zone", "library"), p.get("owner"))
    zone = ctx.state.zones[zn]
    ctx.rng.shuffle(zone.cards)
    info.forget_positions(ctx.state, zn)
    ctx.event("shuffle %s" % zn)
    return {}


def _set_flags(ctx: Context, p: dict, **fixed) -> dict:
    ids = resolve_cards(ctx, p.get("card", p.get("cards")))
    for cid in ids:
        c = ctx.state.cards[cid]
        for attr in ("tapped", "face_down", "face"):
            val = fixed.get(attr, p.get(attr))
            if val is not None:
                setattr(c, attr, int(val) if attr == "face" else bool(val))
        if p.get("controller"):
            new = _player(ctx, p["controller"])
            if new != c.controller:
                c.controller = new
                c.controlled_since = ctx.state.tick()  # コントロールが変わると召喚酔いからやり直し
        if p.get("name"):
            c.name = p["name"]
        ctx.event("set %s %s" % (_label(ctx.state, cid),
                                 {k: v for k, v in {**p, **fixed}.items() if k not in ("card", "cards")}))
    return {"cards": ids}


def op_tap(ctx, p):
    """カードをタップする。"""
    return _set_flags(ctx, p, tapped=True)


def op_untap(ctx, p):
    """カードをアンタップする。"""
    return _set_flags(ctx, p, tapped=False)


def op_untap_all(ctx: Context, p: dict) -> dict:
    """指定 Player がコントロールする戦場のパーマネントをすべてアンタップする（紙で盤面を起こす操作）。"""
    pid = _player(ctx, p.get("player"))
    ids = [cid for cid in ctx.state.zones["battlefield"].cards
           if ctx.state.cards[cid].controller == pid and ctx.state.cards[cid].tapped]
    for cid in ids:
        ctx.state.cards[cid].tapped = False
    ctx.event("untap all of %s: %s" % (pid, _labels(ctx.state, ids)))
    return {"cards": ids}


def op_set(ctx, p):
    """カードの物理状態（tapped / face_down / face / controller）を直接設定する。"""
    return _set_flags(ctx, p)


def op_create(ctx: Context, p: dict) -> dict:
    """トークン等を作る。definition（解釈しない）か copy_of（コピー元のカード）で中身を決める。"""
    s = ctx.state
    definition = dict(p.get("definition") or {})
    if p.get("copy_of"):
        # コピー: 元のカードを参照するだけで、テキストは写さない（元の定義が正本）
        src = s.cards.get(_single(ctx, p["copy_of"]))
        if src is None:
            raise OperationError("copy_of must refer to a card or token")
        base = {k: v for k, v in src.definition.items() if k != "copy_of"}
        definition = {**base, "copy_of": src.definition.get("copy_of", src.id), **definition}
        definition.setdefault("name", src.name)
    name = p.get("name") or definition.get("name")
    if not name:
        raise OperationError("create needs a name, definition.name or copy_of")
    definition.setdefault("name", name)
    controller = _player(ctx, p.get("controller"))
    owner = _player(ctx, p.get("owner") or controller)
    to = resolve_zone(ctx, p.get("zone", "battlefield"), owner)
    src = s.cards.get(_single(ctx, p["copy_of"])) if p.get("copy_of") else None
    ids = []
    for _ in range(int(p.get("count", 1))):
        cid = s.new_id("t" if p.get("token", True) else "x")
        s.cards[cid] = Card(id=cid, name=name, owner=owner, controller=controller, zone=to,
                            definition=definition, token=bool(p.get("token", True)),
                            tapped=bool(p.get("tapped", False)),
                            face_down=bool(p.get("face_down", False)), entered_at=s.tick(),
                            type_line=str(definition.get("type_line") or (src.type_line if src else "")))
        s.cards[cid].controlled_since = s.cards[cid].entered_at
        zone = s.zones[to]
        zone.cards.insert(0 if zone.ordered else len(zone.cards), cid)
        ids.append(cid)
    ctx.event("create in %s: %s" % (to, ", ".join(_label(s, i) for i in ids)))
    _alias(ctx, p, ids)
    return {"cards": ids}


def op_remove(ctx: Context, p: dict) -> dict:
    """オブジェクトをゲームから取り除く（トークンの消滅、コピーの消滅など）。"""
    s = ctx.state
    ids = resolve_cards(ctx, p.get("card", p.get("cards")))
    for cid in ids:
        card = s.cards[cid]
        zone = s.zones[card.zone]
        if zone.kind == "battlefield":
            _detach_from_battlefield(ctx, card)
        _detach_object(ctx, card, set())
        zone.cards.remove(cid)
        s.stack.items = [it for it in s.stack.items if it.card != cid]
        info.forget_card(s, cid)
        del s.cards[cid]
        ctx.event("remove %s <%s> from the game" % (cid, card.name))
    return {"cards": ids}


def _viewers(ctx: Context, to) -> list:
    if to in (None, "all"):
        return list(ctx.state.player_order)
    if isinstance(to, str):
        to = [to]
    return [_player(ctx, x) for x in to]


def op_reveal(ctx: Context, p: dict) -> dict:
    """カードを公開する。見た Player はその中身を覚える。"""
    ids = resolve_cards(ctx, p.get("card", p.get("cards")))
    for pid in _viewers(ctx, p.get("to")):
        for cid in ids:
            info.learn(ctx.state, pid, cid, True)
    ctx.event("reveal %s to %s" % (_labels(ctx.state, ids), p.get("to", "all")))
    return {"cards": ids}


def op_search(ctx: Context, p: dict) -> dict:
    """ライブラリーから名前でカードを探して動かし、シャッフルする（サーチ）。

    name: 名前（リストならどれか）、count（既定1。見つかった数が足りなければ失敗）、to / position / tapped、
    reveal（true で全員に公開）、shuffle（既定 true）、zone（既定は探す Player のライブラリー）、player（探す Player）。
    探した Player は中身を全部見るが、残りのカードは記憶に加えない（すぐシャッフルされ、結果の learned が長くなるだけなので）。
    動かしたカードは、探した Player が知っている扱いになる。同じライブラリーの上などへ置くときは、シャッフルしてから置く。
    """
    s = ctx.state
    pid = _player(ctx, p.get("player"))
    zn = resolve_zone(ctx, p.get("zone", "library"), pid)
    if s.zones[zn].kind != "library":
        raise OperationError("search looks in a library, not %s" % zn)
    names = p.get("name")
    if not names:
        raise OperationError("search needs 'name'")
    names = [names] if isinstance(names, str) else list(names)
    if "to" not in p:
        raise OperationError("search needs 'to'")
    n = int(p.get("count", 1))
    hits = [cid for cid in s.zones[zn].cards if s.cards[cid].name in names][:n]
    if len(hits) < n:
        raise OperationError("found %d of %s in %s (need %d)" % (len(hits), names, zn, n))
    to = resolve_zone(ctx, p["to"], s.cards[hits[0]].owner) if hits else None
    do_shuffle = p.get("shuffle", True)
    if do_shuffle and to == zn:
        for cid in hits:
            s.zones[zn].cards.remove(cid)
        ctx.rng.shuffle(s.zones[zn].cards)
        info.forget_positions(s, zn)
        s.zones[zn].cards.extend(hits)  # 一時的に置いてから、指定の位置へ動かし直す
    placed = list(reversed(hits)) if p.get("position") in (None, "top") else hits
    for cid in placed:
        move_card(ctx, cid, to, p.get("position"), p.get("face_down"), p.get("tapped"), None, ())
        info.learn(s, pid, cid, True)
    ctx.event("%s searches %s for %s: %s" % (pid, zn, " / ".join("<%s>" % x for x in names), _labels(s, hits)))
    if p.get("reveal"):
        for viewer in s.player_order:
            for cid in hits:
                info.learn(s, viewer, cid, True)
        ctx.event("reveal %s to all" % _labels(s, hits))
    if do_shuffle and to != zn:
        ctx.rng.shuffle(s.zones[zn].cards)
        info.forget_positions(s, zn)
        ctx.event("shuffle %s" % zn)
    if p.get("as"):
        ctx.aliases[p["as"]] = hits
    return {"cards": hits}


def op_look(ctx: Context, p: dict) -> dict:
    """Player がカードを見る（占術・手札を見る効果など）。他の Player は中身を知らない。"""
    pid = _player(ctx, p.get("player"))
    ids = resolve_cards(ctx, p.get("card", p.get("cards")))
    for cid in ids:
        info.learn(ctx.state, pid, cid, True)
    if p.get("as"):
        ctx.aliases[p["as"]] = ids
    ctx.event("%s looks at %s" % (pid, _labels(ctx.state, ids)))
    return {"cards": ids}


# ---------------------------------------------------------------- counters / notes / links

def _counter(ctx: Context, target: str, kind: str):
    for c in ctx.state.counters:
        if c.target == target and c.kind == kind:
            return c
    return None


def _change_counter(ctx: Context, p: dict, mode: str) -> dict:
    kind = p.get("kind")
    if not kind:
        raise OperationError("counter operation needs 'kind'")
    targets = _objects(ctx, p.get("target"))
    out = {}
    for t in targets:
        c = _counter(ctx, t, kind)
        cur = c.amount if c else 0
        amount = int(p.get("amount", 1))
        new = {"add": cur + amount, "remove": cur - amount, "set": amount}[mode]
        if new < 0:
            raise OperationError("%s has only %d %s counter(s)" % (t, cur, kind))
        if new == 0:
            if c:
                ctx.state.counters.remove(c)
        elif c:
            c.amount = new
        else:
            ctx.state.counters.append(Counter(target=t, kind=kind, amount=new))
        out[t] = new
        ctx.event("counter %s on %s: %d -> %d" % (kind, t, cur, new))
    return {"counters": out}


def op_counter_add(ctx, p):
    """カウンターを置く（target はカード・Player など。kind は解釈しない）。"""
    return _change_counter(ctx, p, "add")


def op_counter_remove(ctx, p):
    """カウンターを取り除く。"""
    return _change_counter(ctx, p, "remove")


def op_counter_set(ctx, p):
    """カウンターの数を設定する（0 で取り除く）。"""
    return _change_counter(ctx, p, "set")


def op_note_add(ctx: Context, p: dict) -> dict:
    """単一の対象について覚えておく情報（付箋）を付ける。"""
    if "text" not in p:
        raise OperationError("note_add needs 'text'")
    ids = []
    for t in _objects(ctx, p.get("target")):
        nid = ctx.state.new_id("n")
        ctx.state.notes[nid] = Note(id=nid, target=t, text=str(p["text"]), until=p.get("until"),
                                    turn=ctx.state.turn.turn)
        ids.append(nid)
        ctx.event("note %s on %s: %s" % (nid, t, p["text"]))
    _alias(ctx, p, ids)
    return {"notes": ids}


def op_note_update(ctx: Context, p: dict) -> dict:
    """Note の text / until を書き換える（同じ効果が重なったら1枚にまとめて更新する）。"""
    nid = _single(ctx, p.get("note"))
    n = ctx.state.notes.get(nid)
    if n is None:
        raise OperationError("unknown note %r" % (p.get("note"),))
    if "text" in p:
        n.text = str(p["text"])
    if "until" in p:
        n.until = p["until"]
    ctx.event("note %s on %s: %s" % (nid, n.target, n.text))
    return {"note": nid}


def op_note_remove(ctx: Context, p: dict) -> dict:
    """note（id）で1枚、または target / until で条件に合う Note をまとめて外す。"""
    s = ctx.state
    if p.get("note"):
        nids = [p["note"]] if isinstance(p["note"], str) else list(p["note"])
        nids = [ctx.aliases.get(n[1:], [n])[0] if n.startswith("$") else n for n in nids]
        for n in nids:
            if n not in s.notes:
                raise OperationError("unknown note %r" % n)
    else:
        if "target" not in p and "until" not in p:
            raise OperationError("note_remove needs note, target or until")
        targets = set(_objects(ctx, p["target"])) if "target" in p else None
        nids = [n.id for n in s.notes.values()
                if (targets is None or n.target in targets)
                and ("until" not in p or n.until == p["until"])]
    for n in nids:
        del s.notes[n]
    ctx.event("notes removed: %s" % ", ".join(nids))
    return {"notes": nids}


def op_link_add(ctx: Context, p: dict) -> dict:
    """複数オブジェクト間の関係（装備・エンチャント・対象・追放元など）を記録する。"""
    kind = p.get("kind")
    if not kind:
        raise OperationError("link_add needs 'kind'")
    source = _single(ctx, p.get("source"))
    targets = _objects(ctx, p.get("targets", p.get("target")))
    if not targets:
        raise OperationError("link_add needs at least one target")
    lid = ctx.state.new_id("l")
    ctx.state.links[lid] = Link(id=lid, kind=kind, source=source, targets=targets,
                                text=str(p.get("text", "")))
    ctx.event("link %s %s: %s -> %s" % (lid, kind, source, targets))
    _alias(ctx, p, [lid])
    return {"link": lid}


def op_link_remove(ctx: Context, p: dict) -> dict:
    """link（id）で1本、または object に関わる Link（kind で絞り込み可）を外す。"""
    s = ctx.state
    if p.get("link"):
        lids = [p["link"]] if isinstance(p["link"], str) else list(p["link"])
        lids = [ctx.aliases.get(l[1:], [l])[0] if l.startswith("$") else l for l in lids]
        for l in lids:
            if l not in s.links:
                raise OperationError("unknown link %r" % l)
    elif p.get("object"):
        oid = _single(ctx, p["object"])
        lids = [l.id for l in s.links_of(oid) if not p.get("kind") or l.kind == p["kind"]]
    else:
        raise OperationError("link_remove needs link or object")
    for l in lids:
        del s.links[l]
    ctx.event("links removed: %s" % ", ".join(lids))
    return {"links": lids}


# ---------------------------------------------------------------- stack

def op_stack_push(ctx: Context, p: dict) -> dict:
    """スタックに積む。card を渡すとそのカードをスタック領域へ動かす（呪文を唱える）。"""
    s = ctx.state
    kind = p.get("kind", "spell" if p.get("card") else "ability")
    card = None
    if p.get("card"):
        card = _single(ctx, p["card"])
        if s.cards[card].zone != "stack":
            move_card(ctx, card, "stack", "top", controller=_player(ctx, p.get("controller")))
    source = _single(ctx, p["source"]) if p.get("source") else None
    controller = _player(ctx, p.get("controller"))
    sid = s.new_id("s")
    item = StackItem(id=sid, kind=kind, controller=controller, card=card, source=source,
                     text=str(p.get("text", "")))
    s.stack.items.insert(0, item)
    # 唱えた・起動した Player が続けて優先権を持つ。パスの連続は途切れる
    s.turn.priority = controller
    s.turn.passed = []
    ctx.event("stack push %s %s card=%s source=%s %s" % (sid, kind, card, source, item.text))
    targets = p.get("targets")
    if targets:
        lid = s.new_id("l")
        s.links[lid] = Link(id=lid, kind="target", source=sid, targets=_objects(ctx, targets))
        ctx.event("link %s target: %s -> %s" % (lid, sid, s.links[lid].targets))
    _alias(ctx, p, [sid])
    return {"item": sid}


def op_stack_remove(ctx: Context, p: dict) -> dict:
    """スタックから取り除く（解決の終わり・打ち消し）。item を省略すると一番上。

    呪文のカードは card_to へ動かす。省略するとタイプ行から（パーマネントは戦場、インスタント・ソーサリーは墓地。
    打ち消しなら card_to: graveyard）。その StackItem が源の Link（対象など）も外す。
    """
    s = ctx.state
    if not s.stack.items:
        raise OperationError("the stack is empty")
    sid = _single(ctx, p["item"]) if p.get("item") else s.stack.items[0].id
    idx, item = s.find_stack_item(sid)
    if item is None:
        raise OperationError("unknown stack item %r" % sid)
    s.stack.items.pop(idx)
    for l in s.links_of(sid):
        del s.links[l.id]
    # 解決後はアクティブ・プレイヤーが優先権を得る
    s.turn.priority = s.turn.active
    s.turn.passed = []
    ctx.event("stack remove %s" % sid)
    if item.card and item.card in s.cards and s.cards[item.card].zone == "stack":
        to = resolve_zone(ctx, p.get("card_to") or _spell_destination(ctx, item.card), s.cards[item.card].owner)
        move_card(ctx, item.card, to, p.get("position"),
                  controller=item.controller if to == "battlefield" else None)
    return {"item": sid}


def op_stack_move(ctx: Context, p: dict) -> dict:
    """StackItem の位置を変える（同時に誘発した能力の順番を決め直すとき）。index 0 が一番上。"""
    s = ctx.state
    idx, item = s.find_stack_item(_single(ctx, p.get("item")))
    if item is None:
        raise OperationError("unknown stack item %r" % p.get("item"))
    s.stack.items.pop(idx)
    to = int(p.get("index", 0))
    s.stack.items.insert(max(0, min(to, len(s.stack.items))), item)
    ctx.event("stack move %s -> %d" % (item.id, to))
    return {"item": item.id}


# ---------------------------------------------------------------- combat

def op_attack(ctx: Context, p: dict) -> dict:
    """攻撃クリーチャーと攻撃先を置く。target は Player / プレインズウォーカー / バトル等。"""
    s = ctx.state
    attackers = resolve_cards(ctx, p.get("attacker", p.get("attackers")))
    target = _single(ctx, p.get("target"))
    for a in attackers:
        if s.cards[a].zone != "battlefield":
            raise OperationError("%s is not on the battlefield" % a)
        s.combat.attacks = [x for x in s.combat.attacks if x.attacker != a]
        s.combat.attacks.append(AttackAssignment(attacker=a, target=target))
        if p.get("tap", False):
            s.cards[a].tapped = True
        ctx.event("attack %s -> %s" % (_label(s, a), target))
    return {"attackers": attackers}


def op_block(ctx: Context, p: dict) -> dict:
    """ブロッカーとブロック先を置く。複数ブロックは同じ blocker で何度でも置ける。"""
    s = ctx.state
    blocker = _single(ctx, p.get("blocker"))
    attacker = _single(ctx, p.get("attacker"))
    if s.cards.get(blocker) is None or s.cards[blocker].zone != "battlefield":
        raise OperationError("%s is not on the battlefield" % blocker)
    if attacker not in {a.attacker for a in s.combat.attacks}:
        raise OperationError("%s is not attacking" % attacker)
    if any(b.blocker == blocker and b.attacker == attacker for b in s.combat.blocks):
        raise OperationError("%s already blocks %s" % (blocker, attacker))
    s.combat.blocks.append(BlockAssignment(blocker=blocker, attacker=attacker))
    ctx.event("block %s -> %s" % (_label(s, blocker), _label(s, attacker)))
    return {}


def op_combat_remove(ctx: Context, p: dict) -> dict:
    """クリーチャーを戦闘から取り除く（攻撃・ブロックの両方）。"""
    s = ctx.state
    ids = set(resolve_cards(ctx, p.get("card", p.get("cards"))))
    s.combat.attacks = [a for a in s.combat.attacks if a.attacker not in ids]
    live = {a.attacker for a in s.combat.attacks}
    s.combat.blocks = [b for b in s.combat.blocks if b.blocker not in ids and b.attacker in live]
    ctx.event("removed from combat: %s" % _labels(ctx.state, sorted(ids)))
    return {}


def op_combat_clear(ctx: Context, p: dict) -> dict:
    """戦闘の配置をすべて片付ける（戦闘フェイズの終わりなど）。"""
    ctx.state.combat.attacks.clear()
    ctx.state.combat.blocks.clear()
    ctx.event("combat cleared")
    return {}


# ---------------------------------------------------------------- mana

def op_mana_add(ctx: Context, p: dict) -> dict:
    """マナ・プールにマナを加える。用途制限などは note で付ける（Table Engine は解釈しない）。

    色・source・duration・note がすべて同じマナが既にあれば、その量に足す。
    """
    s = ctx.state
    pid = _player(ctx, p.get("player"))
    color = str(p.get("color", "C"))
    amount = int(p.get("amount", 1))
    if amount <= 0:
        raise OperationError("mana amount must be positive")
    source = _single(ctx, p["source"]) if p.get("source") else None
    pool = s.players[pid].mana_pool.mana
    notes = sorted([str(p["note"])] if p.get("note") else [])
    for m in pool:
        # 色・発生源・存続期間・Note が完全に同じなら区別する意味がないのでまとめる
        if (m.color == color and m.source == source and m.duration == p.get("duration")
                and sorted(n.text for n in s.notes_on(m.id)) == notes):
            m.amount += amount
            ctx.event("mana %s +%d %s into %s (now %d)" % (pid, amount, color, m.id, m.amount))
            if p.get("as"):
                ctx.aliases[p["as"]] = [m.id]  # 既存のマナに足しただけなので created には入れない
            return {"mana": m.id, "merged": True}
    mid = s.new_id("m")
    pool.append(Mana(id=mid, color=color, amount=amount, source=source, duration=p.get("duration")))
    if p.get("note"):
        nid = s.new_id("n")
        s.notes[nid] = Note(id=nid, target=mid, text=str(p["note"]), turn=s.turn.turn)
    ctx.event("mana %s +%d %s as %s%s" % (pid, amount, color, mid,
                                          " (%s)" % p["note"] if p.get("note") else ""))
    _alias(ctx, p, [mid])
    return {"mana": mid}


def op_mana_spend(ctx: Context, p: dict) -> dict:
    """マナを使う。mana（id）で指定するか、color で指定（制限 Note の無いマナから使う）。"""
    s = ctx.state
    pid = _player(ctx, p.get("player"))
    pool = s.players[pid].mana_pool.mana
    amount = int(p.get("amount", 1))
    if p.get("mana"):
        mid = _single(ctx, p["mana"])
        candidates = [m for m in pool if m.id == mid]
        if not candidates:
            raise OperationError("%s is not in %s's mana pool" % (mid, pid))
    elif p.get("color"):
        candidates = sorted([m for m in pool if m.color == p["color"]],
                            key=lambda m: (bool(s.notes_on(m.id)), bool(m.duration)))
    else:
        raise OperationError("mana_spend needs mana or color")
    left = amount
    spent = []
    for m in candidates:
        if left == 0:
            break
        take = min(left, m.amount)
        m.amount -= take
        left -= take
        spent.append((m.id, take))
        if m.amount == 0:
            pool.remove(m)
            for n in s.notes_on(m.id):
                del s.notes[n.id]
    if left:
        raise OperationError("%s lacks %d mana (%s)" % (pid, left, p.get("mana") or p.get("color")))
    ctx.event("mana %s spent %s" % (pid, spent))
    return {"spent": spent}


def op_mana_clear(ctx: Context, p: dict) -> dict:
    """マナ・プールを空にする。player 省略で全員。duration を渡すとその存続期間のものだけ残す。"""
    s = ctx.state
    pids = [_player(ctx, p["player"])] if p.get("player") else list(s.player_order)
    keep = p.get("keep_duration")
    for pid in pids:
        pool = s.players[pid].mana_pool.mana
        gone = [m for m in pool if not keep or m.duration != keep]
        for m in gone:
            pool.remove(m)
            for n in s.notes_on(m.id):
                del s.notes[n.id]
        if gone:
            ctx.event("mana pool of %s emptied: %s" % (pid, [(m.color, m.amount) for m in gone]))
    return {}


# ---------------------------------------------------------------- players / turn / declaration

_DAMAGE_NOTE = re.compile(r"^damage (\d+)$")


def _amount(p: dict) -> int:
    """ダメージ・ライフの量。負の値は書けない（計算で負になったら 0 として扱うのは AI。107.1b）。"""
    try:
        n = int(p.get("amount"))
    except (TypeError, ValueError):
        raise OperationError("amount must be an integer >= 0")
    if n < 0:
        raise OperationError("amount must be >= 0 (a negative result counts as 0)")
    return n


def _change_life(ctx: Context, pid: str, delta: int, what: str) -> dict:
    pl = ctx.state.players[pid]
    before = pl.life
    pl.life += delta
    ctx.event("%s: life %s %d -> %d" % (what, pid, before, pl.life))
    return {"life": pl.life}


def op_damage(ctx: Context, p: dict) -> dict:
    """ダメージを与える（120）。プレイヤーならライフを減らし、パーマネントなら `damage N`（until: end_of_turn）の
    Note に足す。apply: false なら与えた記録だけ残す（感染・萎縮・プレインズウォーカーなど、結果は AI が続けて書く）。
    amount 0 は何もしない（ダメージを与えなかったことになる。120.8）。"""
    n = _amount(p)
    target = _single(ctx, p.get("target"))
    s = ctx.state
    if target not in s.players and not (target in s.cards and s.cards[target].zone == "battlefield"):
        raise OperationError("damage target %s is not a player or a permanent on the battlefield" % target)
    if n == 0:
        return {}
    source = _single(ctx, p["source"]) if p.get("source") else None
    what = "damage %d to %s%s" % (n, _label(s, target), " from %s" % _label(s, source) if source else "")
    if p.get("apply", True) is False:
        ctx.event(what + " (result written separately)")
        return {}
    if target in s.players:
        return _change_life(ctx, target, -n, what)
    for note in s.notes_on(target):
        m = _DAMAGE_NOTE.match(note.text)
        if m and note.until == "end_of_turn":
            note.text = "damage %d" % (int(m.group(1)) + n)
            ctx.event("%s: note %s %s" % (what, note.id, note.text))
            return {"note": note.id}
    nid = s.new_id("n")
    s.notes[nid] = Note(id=nid, target=target, text="damage %d" % n, until="end_of_turn", turn=s.turn.turn)
    ctx.event("%s: note %s damage %d" % (what, nid, n))
    return {"note": nid}


def op_life_loss(ctx: Context, p: dict) -> dict:
    """ライフを失う（119.3。ライフの支払いもこれ）。amount 0 は何もしない。"""
    n = _amount(p)
    pid = _player(ctx, p.get("player"))
    return _change_life(ctx, pid, -n, "life loss %d" % n) if n else {}


def op_life_gain(ctx: Context, p: dict) -> dict:
    """ライフを得る（119.3）。amount 0 は何もしない（ライフを得たことにならない。119.9）。"""
    n = _amount(p)
    pid = _player(ctx, p.get("player"))
    return _change_life(ctx, pid, n, "life gain %d" % n) if n else {}


def op_player_set(ctx: Context, p: dict) -> dict:
    """Player の状態（status / name）を設定する。勝敗の判定自体は AI / Judge が行う。"""
    pl = ctx.state.players[_player(ctx, p.get("player"))]
    if "status" in p:
        pl.status = str(p["status"])
    if "name" in p:
        pl.name = str(p["name"])
    ctx.event("player %s set %s" % (pl.id, {k: v for k, v in p.items() if k != "player"}))
    return {}


def op_turn_set(ctx: Context, p: dict) -> dict:
    """Turn State を直接設定する（追加ターン・追加の戦闘・先制攻撃ダメージ・ステップ等）。"""
    t = ctx.state.turn
    for k in ("turn", "phase", "step"):
        if k in p:
            setattr(t, k, int(p[k]) if k == "turn" else str(p[k]))
    for k in ("active", "priority"):
        if k in p:
            setattr(t, k, _player(ctx, p[k]) if p[k] else None)
    if ("turn" in p or "active" in p) and t.active:
        ctx.state.turn_started[t.active] = ctx.state.tick()  # 追加ターンなども新しいターンの開始
    t.passed = []
    ctx.event("turn state: %s" % vars(t))
    return {}


def _step_key(name: str):
    """"main1" / "declare_attackers" などを TURN_SEQUENCE の (phase, step) にする。"""
    if name in ("main1", "main2"):
        return (name, "main")
    if name == "main":
        raise OperationError("'main' is ambiguous; use main1 or main2")
    hits = [k for k in TURN_SEQUENCE if k[1] == name]
    if not hits:
        raise OperationError("unknown step %r (choose from %s)" % (name, ", ".join(STEP_NAMES)))
    return hits[0]


STEP_NAMES = [ph if st == "main" else st for ph, st in TURN_SEQUENCE]


def op_step(ctx: Context, p: dict) -> dict:
    """名前で指定したステップまで進める（to: untap / upkeep / draw / main1 / beginning_of_combat /
    declare_attackers / declare_blockers / combat_damage / end_of_combat / main2 / end / cleanup）。

    今より前のステップを指定すると、次のターンのそのステップになる（クリンナップの後は次のターン）。
    ゲーム前（pregame）からは、先攻（active）の T1 のそのステップに入る。
    進めるのは Turn State だけ。アンタップ・ドロー・マナの消滅などは行わない。
    """
    s = ctx.state
    t = s.turn
    if not p.get("to"):
        raise OperationError("step needs 'to' (one of %s)" % ", ".join(STEP_NAMES))
    target = _step_key(p["to"])
    if (t.phase, t.step) == PREGAME:
        before = (t.turn, t.phase, t.step)
        t.turn = 1
        t.phase, t.step = target
        t.priority = None if t.step in NO_PRIORITY_STEPS else t.active
        t.passed = []
        s.turn_started[t.active] = s.tick()  # ゲーム前に出たパーマネントは T1 に召喚酔いでない
        ctx.event("step: turn %d %s/%s -> turn %d %s/%s (active %s)" % (before + (t.turn, t.phase, t.step, t.active)))
        return {"turn": t.turn, "phase": t.phase, "step": t.step}
    try:
        i = TURN_SEQUENCE.index((t.phase, t.step))
    except ValueError:
        raise OperationError("current step %s/%s is not in the standard sequence; use turn_set"
                             % (t.phase, t.step))
    j = TURN_SEQUENCE.index(target)
    if j == i:
        raise OperationError("already in %s; to go to the next turn's %s, go through cleanup first"
                             % (p["to"], p["to"]))
    before = (t.turn, t.phase, t.step)
    if j < i:
        t.turn += 1
        live = [pid for pid in s.player_order if s.players[pid].status == "playing"] or s.player_order
        cur = s.player_order.index(t.active) if t.active in s.player_order else -1
        for k in range(1, len(s.player_order) + 1):
            cand = s.player_order[(cur + k) % len(s.player_order)]
            if cand in live:
                t.active = cand
                break
    t.phase, t.step = target
    t.priority = None if t.step in NO_PRIORITY_STEPS else t.active
    t.passed = []
    if j < i:
        s.turn_started[t.active] = s.tick()  # 召喚酔いの表示用
    ctx.event("step: turn %d %s/%s -> turn %d %s/%s (active %s)" % (before + (t.turn, t.phase, t.step, t.active)))
    return {"turn": t.turn, "phase": t.phase, "step": t.step}


def op_priority(ctx: Context, p: dict) -> dict:
    """優先権を持つ Player を設定する。"""
    pid = p.get("player")
    ctx.state.turn.priority = _player(ctx, pid) if pid else None
    ctx.event("priority -> %s" % ctx.state.turn.priority)
    return {}


STANDING_UNTIL = ("stack", "step", "turn")


def _next_player(s: GameState, pid: str) -> Optional[str]:
    live = [x for x in s.player_order if s.players[x].status == "playing"]
    if not live:
        return None
    i = s.player_order.index(pid)
    for k in range(1, len(s.player_order) + 1):
        cand = s.player_order[(i + k) % len(s.player_order)]
        if cand in live:
            return cand
    return None


def _record_pass(ctx: Context, pid: str, text: str) -> None:
    s = ctx.state
    s.declarations.append(Declaration(seq=len(s.declarations) + 1, player=pid, kind="pass", text=text,
                                      turn=s.turn.turn, step=s.turn.step))
    if pid not in s.turn.passed:
        s.turn.passed.append(pid)
    ctx.event("%s passes%s" % (pid, " (%s)" % text if text else ""))


def op_pass(ctx: Context, p: dict) -> dict:
    """優先権をパスする。until を付けると継続的なパス（stack: スタックが空になるまで /
    step: このステップの間 / turn: このターンの間）。

    次の Player が継続的なパスを宣言していれば、その Player のパスもここで記録して回す。
    全員が続けてパスしたら priority は空になる（スタックの一番上を解決するか、ステップを進める）。
    """
    s = ctx.state
    pid = _player(ctx, p.get("player"))
    until = p.get("until")
    if until:
        if until not in STANDING_UNTIL:
            raise OperationError("until must be one of %s" % ", ".join(STANDING_UNTIL))
        s.standing_passes[pid] = {"until": until, "turn": s.turn.turn, "step": s.turn.step,
                                  "stack_seen": bool(s.stack.items)}
    _record_pass(ctx, pid, str(p.get("text", "")) or ("standing: until %s" % until if until else ""))
    auto = []
    cur = pid
    while True:
        live = [x for x in s.player_order if s.players[x].status == "playing"]
        if all(x in s.turn.passed for x in live):
            s.turn.priority = None
            ctx.event("all players passed in succession")
            return {"all_passed": True, "auto_passed": auto}
        nxt = _next_player(s, cur)
        if nxt in s.standing_passes and nxt not in s.turn.passed:
            _record_pass(ctx, nxt, "standing: until %s" % s.standing_passes[nxt]["until"])
            auto.append(nxt)
            cur = nxt
            continue
        s.turn.priority = nxt
        ctx.event("priority -> %s" % nxt)
        return {"all_passed": False, "priority": nxt, "auto_passed": auto}


def op_hold(ctx: Context, p: dict) -> dict:
    """継続的なパスを取り消す（次から自分で判断する）。"""
    pid = _player(ctx, p.get("player"))
    ctx.state.standing_passes.pop(pid, None)
    ctx.event("%s cancels standing pass" % pid)
    return {}


def expire_standing_passes(s: GameState) -> None:
    for pid, sp in list(s.standing_passes.items()):
        if sp["until"] == "turn" and s.turn.turn != sp["turn"]:
            del s.standing_passes[pid]
        elif sp["until"] == "step" and (s.turn.turn, s.turn.step) != (sp["turn"], sp["step"]):
            del s.standing_passes[pid]
        elif sp["until"] == "stack":
            if s.stack.items:
                sp["stack_seen"] = True
            elif sp["stack_seen"]:
                del s.standing_passes[pid]


def op_declare(ctx: Context, p: dict) -> dict:
    """盤面で表せない宣言（pass / concede など）。concede は status も conceded にする。"""
    s = ctx.state
    pid = _player(ctx, p.get("player"))
    kind = str(p.get("kind", "pass"))
    if kind == "pass":
        return op_pass(ctx, p)
    d = Declaration(seq=len(s.declarations) + 1, player=pid, kind=kind, text=str(p.get("text", "")),
                    turn=s.turn.turn, step=s.turn.step)
    s.declarations.append(d)
    if kind == "concede":
        s.players[pid].status = "conceded"
    ctx.event("declare %s %s %s" % (pid, kind, d.text))
    return {"declaration": d.seq}


# ---------------------------------------------------------------- registry

# ---------------------------------------------------------------- composite operations
# 1つの Act の中で使う書き方の省略。中で基本の op を順に適用するだけで、ルールの判定はしない。
# log には書いたとおり（複合 op のまま）残り、Replay も同じ手順を適用し直す。
# 複数の Act になるもの（turn_start / turn_end）は手順（procedures.py）。

_MANA_SPEC = re.compile(r"^(\d+)([WUBRGC])$")
_PAY_KEYS = ("from", "pool", "life")


def _sub(ctx: Context, op: dict) -> dict:
    return apply_operation(ctx, op)


def _mana_items(spec) -> list:
    """"U" / "UU" / "RG" / "5U" / {"color": "U", "amount": 5, "note": "..."} を [(色, 量, note)] に。"""
    if isinstance(spec, dict):
        return [(str(spec.get("color", "C")), int(spec.get("amount", 1)), spec.get("note"))]
    spec = str(spec).strip().replace("{", "").replace("}", "")
    m = _MANA_SPEC.match(spec)
    if m:
        return [(m.group(2), int(m.group(1)), None)]
    if not spec or any(ch not in "WUBRGC" for ch in spec):
        raise OperationError("mana spec %r: use letters of WUBRGC (e.g. U, UU, RG, 5U) or an object" % spec)
    out = {}
    for ch in spec:
        out[ch] = out.get(ch, 0) + 1
    return [(c, n, None) for c, n in out.items()]


def _pay(ctx: Context, pay, player=None) -> dict:
    """支払い。{"#c103": "U", "#c63": "G"}（タップしてマナを出し、全部使う）に、
    "pool": "UG" / {"U": 2}（既にプールにあるマナを使う）、"life": 1（ライフを払う）を足せる。
    {"from": {...}} と書いてもよい。マナを出すだけでタップしない発生源は {"color": .., "tap": false}。"""
    if not pay:
        return {}
    if not isinstance(pay, dict):
        raise OperationError("pay must be an object like {\"#c103\": \"U\", \"life\": 1}")
    sources = dict(pay.get("from") or {})
    sources.update({k: v for k, v in pay.items() if k not in _PAY_KEYS})
    pl = {"player": player} if player else {}
    added = []
    for ref, spec in sources.items():
        ref = normalize_refs(ctx.state, ref)
        if not (isinstance(spec, dict) and spec.get("tap") is False):
            _sub(ctx, {"op": "tap", "card": ref})
        for color, amount, note in _mana_items(spec):
            r = _sub(ctx, {"op": "mana_add", "color": color, "amount": amount, "source": ref,
                           **({"note": note} if note else {}), **pl})
            added.append((r["mana"], amount))
    for mid, amount in added:
        _sub(ctx, {"op": "mana_spend", "mana": mid, "amount": amount, **pl})
    pool = pay.get("pool")
    if pool:
        items = [(c, n) for c, n in pool.items()] if isinstance(pool, dict) else \
            [(c, n) for c, n, _ in _mana_items(pool)]
        for color, amount in items:
            _sub(ctx, {"op": "mana_spend", "color": color, "amount": int(amount), **pl})
    if pay.get("life"):
        _sub(ctx, {"op": "life_loss", "amount": int(pay["life"]), **pl})
    return {"paid": [m for m, _ in added]}


def op_pay(ctx: Context, p: dict) -> dict:
    """コストを払う（複合）: {"#c103": "U", "#c63": "G"} をタップしてマナを出して使う。pool / life も書ける。"""
    body = {k: v for k, v in p.items() if k not in ("player", "as")}
    return _pay(ctx, body, p.get("player"))


def _spell_destination(ctx: Context, card: str) -> str:
    """解決した呪文のカードの行き先。印刷されたタイプ行の表面（// の前）だけを見る。"""
    front = ctx.state.cards[card].type_line.split("//")[0]
    if not front.strip():
        raise OperationError("type line of %s is unknown; give 'card_to'" % card)
    return "graveyard" if ("Instant" in front or "Sorcery" in front) else "battlefield"


def op_cast(ctx: Context, p: dict) -> dict:
    """呪文を唱える（複合。601.2）: stack_push（スタックへ移し、対象を張る）→ pay → cost の op（追加コストの
    生け贄など）。解決は別の Act（効果の op → stack_remove）。as は唱えたカード。"""
    card = _single(ctx, p.get("card"))
    push = {"op": "stack_push", "card": card}
    for k in ("targets", "text", "controller"):
        if p.get(k):
            push[k] = p[k]
    sid = _sub(ctx, push)["item"]
    _pay(ctx, p.get("pay"))
    cost = p.get("cost") or []
    if not isinstance(cost, list):
        raise OperationError("cost must be a list of ops")
    for op in cost:
        _sub(ctx, op)
    if p.get("as"):
        ctx.aliases[p["as"]] = [card]
    return {"card": card, "item": sid}


def op_land(ctx: Context, p: dict) -> dict:
    """土地を戦場に出し、出せるマナを Note に書く（複合）。mana: "{U} or {R}"、tapped: true でタップイン。"""
    card = _single(ctx, p.get("card"))
    if not p.get("mana"):
        raise OperationError("land needs 'mana' (e.g. \"{U} or {R}\")")
    move = {"op": "move", "card": card, "to": "battlefield"}
    if p.get("tapped"):
        move["tapped"] = True
    _sub(ctx, move)
    text = str(p["mana"])
    note = _sub(ctx, {"op": "note_add", "target": card,
                      "text": text if text.startswith("mana:") else "mana: " + text})["notes"][0]
    if p.get("as"):
        ctx.aliases[p["as"]] = [card]
    if p.get("note_as"):
        ctx.aliases[p["note_as"]] = [note]
    return {"card": card, "note": note}


OPERATIONS: dict = {
    # カード移動
    "move": op_move, "draw": op_draw, "shuffle": op_shuffle,
    # Tap / 表裏 / 物理状態
    "tap": op_tap, "untap": op_untap, "untap_all": op_untap_all, "set": op_set,
    # 生成・削除
    "create": op_create, "remove": op_remove,
    # 情報
    "reveal": op_reveal, "look": op_look, "search": op_search,
    # Counter / Note / Link
    "counter_add": op_counter_add, "counter_remove": op_counter_remove, "counter_set": op_counter_set,
    "note_add": op_note_add, "note_update": op_note_update, "note_remove": op_note_remove,
    "link_add": op_link_add, "link_remove": op_link_remove,
    # Stack
    "stack_push": op_stack_push, "stack_remove": op_stack_remove, "stack_move": op_stack_move,
    # Combat
    "attack": op_attack, "block": op_block, "combat_remove": op_combat_remove,
    "combat_clear": op_combat_clear,
    # Mana
    "mana_add": op_mana_add, "mana_spend": op_mana_spend, "mana_clear": op_mana_clear,
    # Player / Turn / Declaration
    "damage": op_damage, "life_loss": op_life_loss, "life_gain": op_life_gain, "player_set": op_player_set, "turn_set": op_turn_set,
    "step": op_step, "priority": op_priority,
    "pass": op_pass, "hold": op_hold, "declare": op_declare,
    # 複合（1つの Act の中で使う書き方の省略）
    "pay": op_pay, "cast": op_cast, "land": op_land,
}


COMPOSITES = ("pay", "cast", "land")


def _bind_aliases(ctx: Context, value, key=None):
    """"$名前" を、その時点のエイリアスの id に置き換える（1つなら id、複数ならリスト）。"""
    if key in _TEXT_KEYS:
        return value
    if isinstance(value, str) and value.startswith("$") and value[1:] in ctx.aliases:
        ids = ctx.aliases[value[1:]]
        return ids[0] if len(ids) == 1 else list(ids)
    if isinstance(value, list):
        return [_bind_aliases(ctx, v) for v in value]
    if isinstance(value, dict):
        return {k: _bind_aliases(ctx, v, k) for k, v in value.items()}
    return value


def apply_operation(ctx: Context, op: dict) -> dict:
    """1つの Operation を適用する。基本の op は ctx.steps に記録する（複合 op は中の基本の op が記録される）。"""
    if not isinstance(op, dict) or "op" not in op:
        raise OperationError("operation must be an object with 'op': %r" % (op,))
    handler: Callable = OPERATIONS.get(op["op"])
    if handler is None:
        raise OperationError("unknown operation %r (see `ops` command)" % op["op"])
    params = normalize_refs(ctx.state, {k: v for k, v in op.items() if k != "op"})
    composite = op["op"] in COMPOSITES
    if composite:
        ctx.parents.append(op["op"])
        try:
            result = handler(ctx, params)
        finally:
            ctx.parents.pop()
    else:
        bound = {k: v for k, v in _bind_aliases(ctx, params).items() if k != "as"}
        if bound.get("player") == "active":
            bound["player"] = ctx.state.turn.active
        start = len(ctx.events)
        result = handler(ctx, params)
        step = {"op": {"op": op["op"], **bound}, "events": ctx.events[start:]}
        if ctx.parents:
            step["parent"] = ctx.parents[-1]
        ctx.steps.append(step)
    expire_standing_passes(ctx.state)
    info.refresh_knowledge(ctx.state)
    return result


_SUMMARY_KEYS = ("card", "cards", "attacker", "attackers", "blocker", "item", "source", "target", "targets",
                 "note", "link", "copy_of", "name", "kind", "to", "card_to", "status", "player", "amount",
                 "set", "color", "count", "until")


def _short(v) -> str:
    if isinstance(v, list):
        return ",".join(_short(x) for x in v)
    if isinstance(v, dict):
        if "zone" in v:
            sel = [k for k in ("top", "bottom", "index", "random", "all", "name") if k in v]
            return "%s[%s]" % (v["zone"], ",".join("%s=%s" % (k, v[k]) for k in sel))
        return "{...}"
    return str(v)


def summarize_op(op: dict) -> str:
    """log 用の短い要約（ラベルの無い Act を読めるようにする）。例: "move #c14 to=graveyard"。"""
    if not isinstance(op, dict):
        return str(op)
    parts = [str(op.get("op", "?"))]
    for k in _SUMMARY_KEYS:
        if k in op and op[k] not in (None, ""):
            parts.append(_short(op[k]) if k in ("card", "cards", "attacker", "attackers", "item")
                         else "%s=%s" % (k, _short(op[k])))
    return " ".join(parts)


def describe_operations() -> list:
    """`ops` コマンド用の一覧（名前と docstring の1行目）。"""
    out = []
    for name, fn in OPERATIONS.items():
        doc = (fn.__doc__ or "").strip().splitlines()
        out.append((name, doc[0] if doc else ""))
    return out
