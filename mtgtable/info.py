"""情報公開（16〜18節）。

GameState そのものと、各 Player から見える情報を分ける。

- 実際に見えている情報: 領域の公開範囲・表裏・Information Policy から毎回計算する
- 知っている情報 (KnownInformation): 一度見たカードの記憶。GameState.knowledge に残る

非公開領域の「知らないカード」は id も出さない。id を出すとシャッフル後も
同じカードを追跡できてしまい、紙では得られない情報になるため。
公開領域の裏向きカードは紙でも物理的に追跡できるので id だけは出す。
"""
from __future__ import annotations

from typing import Optional

from .model import GameState, INFO_POLICIES


# ---------------------------------------------------------------- visibility

def sees_by_rules(state: GameState, viewer: Optional[str], card_id: str) -> bool:
    """通常の公開範囲で、viewer がいまカードの中身を見られるか（Policy は含めない）。"""
    if viewer is None:
        return True
    card = state.cards[card_id]
    zone = state.zones[card.zone]
    if zone.visibility == "public":
        if not card.face_down:
            return True
        # 裏向きのパーマネントはコントローラーが見られる。追放の裏向き等は誰も見えない
        return zone.kind == "battlefield" and card.controller == viewer
    if zone.visibility == "owner":
        return zone.owner == viewer
    return False


def sees_by_policy(state: GameState, viewer: Optional[str], card_id: str) -> bool:
    if viewer is None:
        return True
    policy = state.info_policy.get(viewer, "normal")
    if policy == "omniscient":
        return True
    zone = state.zones[state.cards[card_id].zone]
    if zone.kind == "library":
        if policy == "all_libraries":
            return True
        if policy == "own_library" and zone.owner == viewer:
            return True
    return False


def knows_identity(state: GameState, viewer: Optional[str], card_id: str) -> bool:
    """viewer がカードの中身を知っている（見えている／覚えている）か。"""
    if viewer is None:
        return True
    return (card_id in state.knowledge.get(viewer, {})
            or sees_by_rules(state, viewer, card_id)
            or sees_by_policy(state, viewer, card_id))


def knows_position(state: GameState, viewer: Optional[str], card_id: str) -> bool:
    """順序付き非公開領域で、viewer がカードの位置まで知っているか。"""
    if viewer is None or sees_by_rules(state, viewer, card_id) or sees_by_policy(state, viewer, card_id):
        return True
    k = state.knowledge.get(viewer, {}).get(card_id)
    return bool(k and k.get("ordered"))


def can_reference(state: GameState, viewer: Optional[str], card_id: str) -> bool:
    """viewer がこのカードを id で指定してよいか。

    公開領域にある物（裏向きでも）と、中身を知っているカードだけ。
    知らない非公開カードの id を当て推量で使うと情報が漏れるので拒否する。
    """
    if viewer is None:
        return True
    zone = state.zones[state.cards[card_id].zone]
    if zone.visibility == "public":
        return True
    return knows_identity(state, viewer, card_id) and (
        not zone.ordered or knows_position(state, viewer, card_id))


def known_set(state: GameState, viewer: Optional[str]) -> set:
    return {cid for cid in state.cards if knows_identity(state, viewer, cid)}


# ---------------------------------------------------------------- knowledge

def learn(state: GameState, viewer: str, card_id: str, ordered: bool = True) -> None:
    k = state.knowledge.setdefault(viewer, {})
    entry = k.setdefault(card_id, {"ordered": ordered})
    entry["ordered"] = entry["ordered"] or ordered


def refresh_knowledge(state: GameState) -> None:
    """いま見えているカードをすべて記憶に加える。各 Operation の後に呼ぶ。"""
    for pid in state.player_order:
        for cid in state.cards:
            if sees_by_rules(state, pid, cid):
                learn(state, pid, cid, True)


def forget_positions(state: GameState, zone_name: str) -> None:
    """シャッフル後。中身の記憶は残すが、どこにあるかは分からなくなる。"""
    cards = set(state.zones[zone_name].cards)
    for k in state.knowledge.values():
        for cid in cards:
            if cid in k:
                k[cid]["ordered"] = False


def forget_card(state: GameState, card_id: str) -> None:
    for k in state.knowledge.values():
        k.pop(card_id, None)


def set_policy(state: GameState, player: str, policy: str) -> None:
    if policy not in INFO_POLICIES:
        raise ValueError("unknown info policy %r (choose from %s)" % (policy, ", ".join(INFO_POLICIES)))
    state.info_policy[player] = policy


# ---------------------------------------------------------------- PlayerView

def _card_view(state: GameState, viewer, cid: str, full: bool, names: bool = True) -> dict:
    c = state.cards[cid]
    v = {"id": cid}
    if full:
        if names:
            v["name"] = c.name
        extra = {k: x for k, x in c.definition.items() if k != "name"}
        if extra:
            v["definition"] = extra
    else:
        v["hidden"] = True
    v["owner"] = c.owner
    if c.controller != c.owner or state.zones[c.zone].kind == "battlefield":
        v["controller"] = c.controller
    for attr in ("token", "tapped", "face_down"):
        if getattr(c, attr):
            v[attr] = True
    if c.face:
        v["face"] = c.face
    if state.zones[c.zone].kind == "battlefield":
        if full and not c.face_down and "Land" in c.type_line:
            v["land"] = True
        elif summoning_sick(state, c):
            v["sick"] = True
    counters = state.counters_on(cid)
    if counters:
        v["counters"] = counters
    notes = state.notes_on(cid)
    if notes and full:
        v["notes"] = [_note_view(n) for n in notes]
    # このカードが関わる Link（装備先・追放元など）。同じ見た目でも付いている先が違えば別物として表示する
    links = []
    for l in state.links_of(cid):
        if l.source == cid:
            links.append("%s->%s" % (l.kind, ",".join(_ref_view(state, viewer, t) for t in l.targets)))
        else:
            links.append("%s<-%s" % (l.kind, _ref_view(state, viewer, l.source)))
    if links:
        v["links"] = links
    return v


def summoning_sick(state: GameState, c) -> bool:
    """戦場のクリーチャーが、コントローラーの直近のターンの開始時からコントロールされていないか。

    表示用の目安で、速攻などの能力は考えない（攻撃・{T} できるかの判断は AI が行う）。
    タイプ行が分かっていてクリーチャーでないものは対象外。裏向きのパーマネントはクリーチャーとして扱う。
    """
    if not c.face_down and c.type_line and "Creature" not in c.type_line:
        return False
    started = state.turn_started.get(c.controller)
    return started is None or c.controlled_since >= started


def _note_view(n) -> dict:
    v = {"id": n.id, "text": n.text}
    if n.until:
        v["until"] = n.until
    return v


def _ref_view(state: GameState, viewer, oid: str) -> str:
    """Link / Stack / Combat が指すオブジェクトを viewer に見せてよい形で。"""
    if oid in state.cards and not can_reference(state, viewer, oid):
        return "hidden"
    return oid


COLLAPSED_BY_DEFAULT = ("library", "graveyard")


def player_view(state: GameState, viewer: Optional[str], sideboard: bool = False,
                names: bool = True, library: bool = False, graveyard: bool = False) -> dict:
    """viewer（None なら全知の Judge）が知り得る情報だけを含む Player View。

    ライブラリーと墓地は既定では枚数だけ（library / graveyard で中身も出す）。サイドボードは既定では出さない。
    names: False ならカード名を出さない（id だけ）。
    """
    expand = {"library": library, "graveyard": graveyard}
    def cv(cid, full):
        return _card_view(state, viewer, cid, full, names)

    view = {
        "names": names,
        "viewer": viewer,
        "version": state.version,
        "policy": state.info_policy.get(viewer, "normal") if viewer else "omniscient",
        "turn": {
            "turn": state.turn.turn, "active": state.turn.active,
            "phase": state.turn.phase, "step": state.turn.step,
            "priority": state.turn.priority,
            "passed": list(state.turn.passed),
            "standing_passes": {pid: sp["until"] for pid, sp in state.standing_passes.items()},
        },
        "players": [],
        "zones": {},
        "stack": [],
        "combat": {"attacks": [], "blocks": []},
        "links": [],
        "declarations": [],
    }
    for pid in state.player_order:
        p = state.players[pid]
        pv = {"id": pid, "name": p.name, "life": p.life, "status": p.status, "mana": []}
        for m in p.mana_pool.mana:
            mv = {"id": m.id, "color": m.color, "amount": m.amount}
            if m.source:
                mv["source"] = _ref_view(state, viewer, m.source)
            if m.duration:
                mv["duration"] = m.duration
            ns = state.notes_on(m.id)
            if ns:
                mv["notes"] = [_note_view(n) for n in ns]
            pv["mana"].append(mv)
        counters = state.counters_on(pid)
        if counters:
            pv["counters"] = counters
        notes = state.notes_on(pid)
        if notes:
            pv["notes"] = [_note_view(n) for n in notes]
        view["players"].append(pv)

    for zname, zone in state.zones.items():
        if zone.kind == "sideboard" and not sideboard:
            continue
        zv = {"count": len(zone.cards)}
        if zone.kind in COLLAPSED_BY_DEFAULT and not expand[zone.kind]:
            zv["collapsed"] = True
            if zone.visibility != "public":
                n = sum(1 for cid in zone.cards if knows_identity(state, viewer, cid)
                        and knows_position(state, viewer, cid))
                if n:
                    zv["known_positions_count"] = n
            view["zones"][zname] = zv
            continue
        if zone.visibility == "public":
            zv["cards"] = [cv(cid, knows_identity(state, viewer, cid)) for cid in zone.cards]
        elif zone.ordered:
            positions, unordered = [], []
            for i, cid in enumerate(zone.cards):
                if not knows_identity(state, viewer, cid):
                    continue
                c = cv(cid, True)
                if knows_position(state, viewer, cid):
                    c["index"] = i
                    positions.append(c)
                else:
                    unordered.append(c)
            if positions:
                zv["known_positions"] = positions
            if unordered:
                zv["known_unordered"] = unordered
        else:
            known = [cid for cid in zone.cards if knows_identity(state, viewer, cid)]
            if len(known) == len(zone.cards):
                zv["cards"] = [cv(cid, True) for cid in known]
            else:
                zv["known"] = [cv(cid, True) for cid in known]
                zv["unknown"] = len(zone.cards) - len(known)
        view["zones"][zname] = zv

    for it in state.stack.items:
        iv = {"id": it.id, "kind": it.kind, "controller": it.controller}
        if it.card:
            iv["card"] = _ref_view(state, viewer, it.card)
        if it.source:
            iv["source"] = _ref_view(state, viewer, it.source)
        if it.text:
            iv["text"] = it.text
        links = [l.id for l in state.links_of(it.id)]
        if links:
            iv["links"] = links
        view["stack"].append(iv)

    view["combat"]["attacks"] = [{"attacker": a.attacker, "target": _ref_view(state, viewer, a.target)}
                                 for a in state.combat.attacks]
    view["combat"]["blocks"] = [{"blocker": b.blocker, "attacker": b.attacker}
                                for b in state.combat.blocks]
    for l in state.links.values():
        lv = {"id": l.id, "kind": l.kind, "source": _ref_view(state, viewer, l.source),
              "targets": [_ref_view(state, viewer, t) for t in l.targets]}
        if l.text:
            lv["text"] = l.text
        view["links"].append(lv)
    view["declarations"] = [
        {"seq": d.seq, "player": d.player, "kind": d.kind, "text": d.text}
        for d in state.declarations
        if d.turn == state.turn.turn and d.step == state.turn.step
    ]
    return view
