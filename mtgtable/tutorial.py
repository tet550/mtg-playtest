"""チュートリアル（手元のサイト）: 決まった台本で1局を進め、盤面の操作を覚える。AI は使わない。

ホストは p1（緑）、相手は p2（赤。AI の席）。手札とライブラリーの順は決めてあり、相手の動きと審判の処理は台本
（STEPS）の返答を、ふだんの審判・AI の席と同じ形（prompt.judge_acts・prompt.intent_batch）で卓に書く。

- 台本は1本の並び。各段は「あなた」（ホストの操作を待つ。案内の文と、受け付ける操作）か、審判・相手の返答
- ホストの書き込みは、今の段の操作と合うものだけを受ける（合わなければ案内を添えて断る。盤面は変えない）
- 書き込みが通ったら段を進め、審判・相手の段を、次の「あなた」の段か決着まで続けて書く（run）
- 進み具合は卓の外の文書 "tutorial" に置く。チュートリアルの対局は Claude Code の監視（next）が拾わない

台本は卓の判定には使わない（エンジンも審判も、カードの文を読んで判定しない）。カードは名前で引いて id を決める。
"""
from __future__ import annotations

from typing import Callable, Optional

from . import carddb, play, prompt
from .setup import new_game, parse_decklist

GAME = "tutorial"
DOC = "tutorial"

# 台本の後も続けて操作を覚えられるよう、いろいろな操作が要るカードを入れる: マナ能力（<Llanowar Elves>）・対象を取る起動型
# 能力（<Prodigal Pyromancer>）・誘発（<Elvish Visionary> <Goblin Instigator> のトークン）・瞬速（<Ambush Viper>）・カウンター
# （<Battlegrowth>）・対象2つ（<Prey Upon>）・オーラ（<Rancor>）・装備（<Bonesplitter>）・X（<Fireball>）・モード（<Abrade>）・
# 追加コスト（<Tormenting Voice>）。土地は少なめ
GREEN = """Deck
20 Forest
4 Grizzly Bears
2 Giant Growth
2 Rampant Growth
4 Llanowar Elves
4 Elvish Visionary
4 Ambush Viper
4 Giant Spider
4 Battlegrowth
4 Prey Upon
4 Rancor
4 Bonesplitter
"""
RED = """Deck
20 Mountain
4 Raging Goblin
4 Lightning Bolt
4 Prodigal Pyromancer
4 Goblin Instigator
4 Hill Giant
4 Shock
4 Brute Force
4 Tormenting Voice
4 Fireball
4 Abrade
"""
# タイプ行（画面が土地か呪文かを見分ける。カードの情報が取れなくても進められるよう、ここに書いておく）
TYPE_LINES = {"Forest": "Basic Land — Forest", "Mountain": "Basic Land — Mountain",
              "Grizzly Bears": "Creature — Bear", "Raging Goblin": "Creature — Goblin Berserker",
              "Giant Growth": "Instant", "Lightning Bolt": "Instant", "Rampant Growth": "Sorcery",
              "Llanowar Elves": "Creature — Elf Druid", "Elvish Visionary": "Creature — Elf Shaman",
              "Ambush Viper": "Creature — Snake", "Giant Spider": "Creature — Spider", "Battlegrowth": "Instant",
              "Prey Upon": "Sorcery", "Rancor": "Enchantment — Aura", "Bonesplitter": "Artifact — Equipment",
              "Prodigal Pyromancer": "Creature — Human Wizard", "Goblin Instigator": "Creature — Goblin Rogue",
              "Hill Giant": "Creature — Giant", "Shock": "Instant", "Brute Force": "Instant",
              "Tormenting Voice": "Sorcery", "Fireball": "Sorcery", "Abrade": "Instant"}
# 手札（7枚）と、その後に引く順。台本で使わない分は、台本の後に使うカード（p1 の <Llanowar Elves> <Bonesplitter>）。
# p2 は台本の後に引くカードも積んでおく（p1 は <Rampant Growth> で切り直すので積めない）
ORDER = {"p1": ["Forest", "Forest", "Grizzly Bears", "Rampant Growth", "Giant Growth", "Forest", "Llanowar Elves",
                "Bonesplitter", "Forest"],
         "p2": ["Mountain", "Mountain", "Raging Goblin", "Lightning Bolt", "Mountain", "Raging Goblin", "Lightning Bolt",
                "Mountain", "Mountain",
                "Prodigal Pyromancer", "Goblin Instigator", "Mountain", "Fireball", "Abrade", "Mountain",
                "Tormenting Voice", "Shock", "Brute Force", "Hill Giant"]}


# ---------------------------------------------------------------- 台本の部品

def _ids(s, zone: str, name: str, controller: Optional[str] = None, tapped: Optional[bool] = None) -> list:
    return [c for c in s.zones[zone].cards if s.cards[c].name == name
            and (controller is None or s.cards[c].controller == controller)
            and (tapped is None or s.cards[c].tapped == tapped)]


def _one(s, zone, name, **kw) -> str:
    found = _ids(s, zone, name, **kw)
    if not found:
        raise LookupError("tutorial: no %s in %s" % (name, zone))
    return found[0]


def _bf(s, pid, name, **kw):
    return _one(s, "battlefield", name, controller=pid, **kw)


def _hand(s, pid, name):
    return _one(s, "%s.hand" % pid, name)


def you(ja: str, en: str, what: str, kind: Optional[str] = None, cards=(), then=(), targets: Optional[str] = None) -> dict:
    """ホストの段。what: declare / request / answer。cards: 依頼の行（play_land・cast・attack・block）の (種類, カード名) の並び。
    then: 受け付ける「その後」（空なら何でも）。targets: cast の行に要る対象のカード名。文のカード名は <英語名>（画面が訳す）。"""
    return {"role": "you", "text": {"ja": ja, "en": en}, "what": what, "kind": kind, "cards": list(cards),
            "then": tuple(then), "targets": targets}


def explain(ja: str, en: str) -> dict:
    """説明の段: 卓の操作は無く、読んで「次へ」を押す（next）。"""
    return {"role": "you", "text": {"ja": ja, "en": en}, "what": "next", "kind": None, "cards": [], "then": (), "targets": None}


def judge(reply: Callable) -> dict:
    return {"role": play.JUDGE, "reply": reply}


def opponent(reply: Callable) -> dict:
    return {"role": "p2", "reply": reply}


# ---------------------------------------------------------------- 台本

def _game_start(s):
    return {"batch": {"label": "Game start", "acts": [{"proc": "turn_start", "to": "main1"}]}, "message": "Game start"}


def _t1(s):
    return {"batch": {"label": "T1", "acts": [
        {"act": [{"op": "land", "card": _hand(s, "p1", "Forest"), "mana": "{G}"}]},
        {"proc": "turn_end"}]}, "rest": []}


def _p2_t1(s):
    m = _hand(s, "p2", "Mountain")
    return {"request": {"plan": [{"kind": "play_land", "cards": [m], "text": "Play <Mountain>"}], "then": "end_turn"}}


def _p2_t1_judge(s):
    return {"batch": {"label": "T1 (p2)", "acts": [
        {"act": [{"op": "land", "card": _hand(s, "p2", "Mountain"), "mana": "{R}"}]},
        {"proc": "turn_end"}]}, "rest": []}


def _t2(s):
    f = _hand(s, "p1", "Forest")
    old = _bf(s, "p1", "Forest")
    bears = _hand(s, "p1", "Grizzly Bears")
    return {"batch": {"label": "T2: <Forest>, <Grizzly Bears>", "acts": [
        {"act": [{"op": "land", "card": f, "mana": "{G}"}]},
        {"act": [{"op": "cast", "card": bears, "pay": {old: "G", f: "G"}}]},
        {"act": [{"op": "stack_remove"}], "label": "<Grizzly Bears> resolves"},
        {"proc": "turn_end"}]}, "rest": []}


def _p2_t2(s):
    m, gob = _hand(s, "p2", "Mountain"), _hand(s, "p2", "Raging Goblin")
    return {"request": {"plan": [{"kind": "play_land", "cards": [m], "text": "Play <Mountain>"},
                                 {"kind": "cast", "cards": [gob], "text": "Cast <Raging Goblin>"},
                                 {"kind": "attack", "cards": [gob], "targets": ["p1"], "text": "Attack p1 with <Raging Goblin>"}],
                        "then": "end_turn"}}


def _p2_t2_judge(s):
    m, gob = _hand(s, "p2", "Mountain"), _hand(s, "p2", "Raging Goblin")
    return {"batch": {"label": "T2 (p2): <Raging Goblin> attacks", "acts": [
        {"act": [{"op": "land", "card": m, "mana": "{R}"}]},
        {"act": [{"op": "cast", "card": gob, "pay": {m: "R"}}]},
        {"act": [{"op": "stack_remove"}], "label": "<Raging Goblin> resolves (haste)"},
        {"act": [{"op": "step", "to": "declare_attackers"}, {"op": "attack", "attacker": gob, "target": "p1", "tap": True}]},
        {"act": [{"op": "step", "to": "declare_blockers"}, {"op": "priority", "player": "p1"}]}]},
        "rest": [], "message": "p1 decides blocks"}


def _block_judge(s):
    gob, bears = _bf(s, "p2", "Raging Goblin"), _bf(s, "p1", "Grizzly Bears")
    return {"batch": {"label": "<Grizzly Bears> blocks <Raging Goblin>", "acts": [
        {"actor": "p1", "act": [{"op": "block", "blocker": bears, "attacker": gob}]},
        {"actor": "p2", "act": [{"op": "step", "to": "combat_damage"},
                                {"op": "damage", "target": bears, "amount": 1, "source": gob},
                                {"op": "damage", "target": gob, "amount": 2, "source": bears}]},
        {"actor": "p2", "act": [{"op": "move", "card": gob, "to": "graveyard"}], "label": "SBA: <Raging Goblin> dies"},
        {"actor": "p2", "act": [{"op": "step", "to": "end_of_combat"}, {"op": "combat_clear"}]},
        {"actor": "p2", "proc": "turn_end"}]}}


def _t3(s):
    f = _hand(s, "p1", "Forest")
    old = _ids(s, "battlefield", "Forest", controller="p1")
    rg = _hand(s, "p1", "Rampant Growth")
    lib = _ids(s, "p1.library", "Forest")
    return {"batch": {"label": "T3: <Forest>, <Rampant Growth>", "acts": [
        {"act": [{"op": "land", "card": f, "mana": "{G}"}]},
        {"act": [{"op": "cast", "card": rg, "pay": {old[0]: "G", old[1]: "G"}}]},
        {"act": [{"op": "look", "card": {"zone": "library", "all": True}}], "label": "<Rampant Growth>: look for a basic land"}]},
        "rest": [], "ask": {"to": "p1", "text": "<Rampant Growth>: choose a basic land card to put onto the battlefield tapped",
                            "cards": lib, "min": 1, "max": 1}}


def _rg_resolve(s):
    chosen = s.declarations[-1].cards[0]
    return {"batch": {"label": "<Rampant Growth> resolves", "acts": [
        {"act": [{"op": "move", "card": chosen, "to": "battlefield", "tapped": True},
                 {"op": "note_add", "target": chosen, "text": "mana: {G}"},
                 {"op": "shuffle", "zone": "library"}, {"op": "stack_remove"}]}]}, "rest": []}


def _attack_judge(s):
    bears = _bf(s, "p1", "Grizzly Bears")
    return {"batch": {"label": "<Grizzly Bears> attacks", "acts": [
        {"act": [{"op": "step", "to": "declare_attackers"}, {"op": "attack", "attacker": bears, "target": "p2", "tap": True}]},
        {"act": [{"op": "step", "to": "declare_blockers"}, {"op": "priority", "player": "p2"}]}]},
        "rest": [], "message": "p2 decides blocks"}


def _p2_bolt(s):
    bolt, bears = _hand(s, "p2", "Lightning Bolt"), _bf(s, "p1", "Grizzly Bears")
    return {"request": {"plan": [{"kind": "cast", "cards": [bolt], "targets": [bears],
                                  "text": "Cast <Lightning Bolt> targeting <Grizzly Bears>"},
                                 {"kind": "other", "text": "No blocks", "no_block": True}], "then": "pass"}}


def _bolt_judge(s):
    bolt, bears = _hand(s, "p2", "Lightning Bolt"), _bf(s, "p1", "Grizzly Bears")
    m = _bf(s, "p2", "Mountain", tapped=False)
    return {"batch": {"label": "<Lightning Bolt> → <Grizzly Bears>", "acts": [
        {"actor": "p2", "act": [{"op": "declare", "kind": "no_block"}]},
        {"actor": "p2", "act": [{"op": "cast", "card": bolt, "targets": [bears], "pay": {m: "R"}}, {"op": "pass"}]}]},
        "message": "p1 may respond"}


def _gg_judge(s):
    gg, bears = _hand(s, "p1", "Giant Growth"), _bf(s, "p1", "Grizzly Bears")
    f = _bf(s, "p1", "Forest", tapped=False)
    bolt = next(i for i in s.stack.items if i.controller == "p2")
    return {"batch": {"label": "<Giant Growth>, then <Lightning Bolt>, combat damage", "acts": [
        {"act": [{"op": "cast", "card": gg, "targets": [bears], "pay": {f: "G"}}]},
        {"act": [{"op": "note_add", "target": bears, "text": "+3/+3", "until": "end_of_turn"}, {"op": "stack_remove"}],
         "label": "<Giant Growth> resolves"},
        {"actor": "p2", "act": [{"op": "damage", "target": bears, "amount": 3, "source": bolt.card},
                                {"op": "stack_remove"}], "label": "<Lightning Bolt> resolves (5/5 survives)"},
        {"act": [{"op": "step", "to": "combat_damage"}, {"op": "damage", "target": "p2", "amount": 5, "source": bears}]},
        {"act": [{"op": "step", "to": "end_of_combat"}, {"op": "combat_clear"}, {"op": "step", "to": "main2"}]}]},
        "rest": []}


def _end_turn(s):
    return {"batch": {"label": "End of turn", "acts": [{"proc": "turn_end"}]}, "rest": []}


def _concede(s):
    return {"declare": {"kind": "concede", "text": "Tutorial complete"}}


STEPS = [
    you("手札を見て「キープ」を押します。",
        "Look at your hand and press \"Keep\".",
        "declare", kind="keep"),
    opponent(lambda s: {"declare": {"kind": "keep"}}),
    judge(_game_start),
    explain("操作はまず下書きにたまり、「審判に依頼」で送ると審判が盤面を動かします。",
            "Your actions pile up in a draft; send it with \"Ask the judge\" and the judge moves the board."),
    you("<Forest> を土地として出し、「ターン終了」を足して送ります。",
        "Play <Forest> as a land, add \"End turn\" and send.",
        "request", cards=[("play_land", "Forest")], then=("end_turn",)),
    judge(_t1),
    opponent(_p2_t1),
    judge(_p2_t1_judge),
    explain("審判が処理し、相手のターンも済みました。下書きの行は送る前なら × で外せます。",
            "The judge handled it, and the opponent's turn too. Before sending, × removes a draft line."),
    you("<Forest> を出し、<Grizzly Bears> を唱えて解決し、ターン終了で送ります。",
        "Play <Forest>, cast and resolve <Grizzly Bears>, then end the turn and send.",
        "request", cards=[("play_land", "Forest"), ("cast", "Grizzly Bears")], then=("end_turn",)),
    judge(_t2),
    opponent(_p2_t2),
    judge(_p2_t2_judge),
    explain("相手の番でも、ブロックなどあなたが決める所では審判が番を回してきます。",
            "On the opponent's turn too, the judge hands you the turn where you have something to decide."),
    you("<Grizzly Bears> → 攻撃中の <Raging Goblin> の順に押してブロックし、送ります。",
        "Click <Grizzly Bears>, then the attacking <Raging Goblin> to block, and send.",
        "request", cards=[("block", "Grizzly Bears")]),
    judge(_block_judge),
    you("<Forest> を出し、<Rampant Growth> を唱えて解決して送ります。",
        "Play <Forest>, cast and resolve <Rampant Growth>, and send.",
        "request", cards=[("play_land", "Forest"), ("cast", "Rampant Growth")], then=("continue",)),
    judge(_t3),
    you("審判の質問です。候補の <Forest> を1枚選んで決定します。",
        "A question from the judge: pick one <Forest> and confirm.",
        "answer"),
    judge(_rg_resolve),
    you("<Grizzly Bears> で攻撃し、「ターン終了」を足して送ります。",
        "Attack with <Grizzly Bears>, add \"End turn\" and send.",
        "request", cards=[("attack", "Grizzly Bears")], then=("end_turn",)),
    judge(_attack_judge),
    opponent(_p2_bolt),
    judge(_bolt_judge),
    you("<Lightning Bolt> に対応: <Giant Growth> を <Grizzly Bears> に唱えて送ります。",
        "Respond to <Lightning Bolt>: cast <Giant Growth> on <Grizzly Bears> and send.",
        "request", cards=[("cast", "Giant Growth")], targets="Grizzly Bears"),
    judge(_gg_judge),
    you("<Grizzly Bears> は生き残り 5 点与えました。「ターン終了」で送ります。",
        "<Grizzly Bears> survived and dealt 5. Send \"End turn\".",
        "request", then=("end_turn",)),
    judge(_end_turn),
]

DONE = {"ja": "完了です。続けるなら、Claude Code に「GUI の対局を回して」と頼みます。",
        "en": "Done. To go on, ask Claude Code to \"run the GUI game\"."}


# ---------------------------------------------------------------- 対局

def _arrange(s, pid: str, order: list) -> None:
    """手札とライブラリーの上を order の順に並べ直す（先頭 7 枚が手札）。"""
    hand, lib = s.zones["%s.hand" % pid], s.zones["%s.library" % pid]
    pool = hand.cards + lib.cards
    picked = []
    for name in order:
        cid = next(c for c in pool if s.cards[c].name == name and c not in picked)
        picked.append(cid)
    rest = [c for c in pool if c not in picked]
    hand.cards, lib.cards = picked[:7], picked[7:] + rest
    for c in hand.cards:
        s.cards[c].zone = hand.name
    for c in lib.cards:
        s.cards[c].zone = lib.name
    s.knowledge[pid] = {c: {"ordered": True} for c in hand.cards}  # 知っているのは手札だけ（並べ替えた先のライブラリーは知らない）


def create(games, offline: bool = False) -> str:
    """チュートリアルの対局を（あれば作り直して）作る。p1 はホストの席、p2 は AI の席。
    カードの情報（画面の詳細・日本語名）は、ふだんの対局と同じくデッキのキャッシュを用意する（取れなくても進められる）。"""
    decks = {"p1": parse_decklist(GREEN, "Tutorial (green)"), "p2": parse_decklist(RED, "Tutorial (red)")}
    s = new_game(decks, seed=1, hand=7, first="p1", type_lines=TYPE_LINES)
    for pid, d in decks.items():
        try:
            cache = carddb.build_deck_cache(d, fetch=not offline)
        except (LookupError, OSError):
            continue
        s.meta["decks"][pid].update(oracle=str(carddb.deck_cache_path(d)), oracle_missing=cache["missing"])
    for pid, order in ORDER.items():
        _arrange(s, pid, order)
    st = games.open(GAME)
    st.create(s, overwrite=True)
    st.write_doc("seats", {"p1": {"claimed": "tutorial"}})
    st.write_doc(DOC, {"step": 0})
    play.set_stops(st, "p1", ["opp:target"])  # 相手が自分のパーマネントを対象にしたら止まる（対応する段のため）
    return GAME


def active(store) -> bool:
    """台本で進んでいる間。最後に「このまま続ける」を選んだら（free）、ふつうの対局（審判と AI の席は Claude Code）になる。"""
    doc = store.read_doc(DOC)
    return doc is not None and not doc.get("free")


def _step(store) -> int:
    return int((store.read_doc(DOC) or {}).get("step", 0))


def _set_step(store, i: int) -> None:
    store.write_doc(DOC, {"step": i})


def status(store, lang: str = "ja") -> dict:
    """画面に出す今の段: {"step", "total", "text", "done", "goal", "choice"}。goal は今の段で受け付ける操作（画面が次に押す
    所を光らせる）。choice は台本を終えて、このまま続けるか終えるかを選ぶ所。"""
    i = _step(store)
    total = sum(1 for x in STEPS if x["role"] == "you")
    n = sum(1 for x in STEPS[:i] if x["role"] == "you")
    over = play.waiting_on(store.load()) is None
    if i >= len(STEPS) or over:
        return {"step": total, "total": total, "text": DONE, "done": True, "choice": not over}
    step = STEPS[i]
    if step["role"] != "you":
        return {"step": n + 1, "total": total, "text": None, "done": False}
    goal = {"what": step["what"], "kind": step["kind"], "cards": [list(c) for c in step["cards"]], "then": list(step["then"]),
            "targets": step["targets"]}
    return {"step": n + 1, "total": total, "text": step["text"], "done": False, "goal": goal}


def _names(s, ids) -> list:
    return [s.cards[c].name for c in ids or [] if c in s.cards]


def check(store, seat: str, what: str, body: dict) -> None:
    """ホストの書き込みが今の段の操作と合うか。合わなければ play.Refused（"tutorial:" で始まる。画面は今の段の案内を出し直す）。
    発言は通す。"""
    if what == "declare" and body.get("kind") == "say":
        return
    i = _step(store)
    step = STEPS[i] if i < len(STEPS) else None
    if step is None or step["role"] != "you":
        raise play.Refused("tutorial: wait for the opponent and the judge")
    refuse = play.Refused("tutorial: this step needs the action in the guide")
    if what != step["what"] or (step["kind"] and body.get("kind") != step["kind"]):
        raise refuse
    if what == "answer":
        if len(body.get("cards") or []) != 1:
            raise refuse
        return
    if what != "request":
        return
    s = store.load()
    lines = [x for x in body.get("plan") or [] if isinstance(x, dict) and x.get("kind") in ("play_land", "cast", "attack", "block")]
    got = [(x["kind"], (_names(s, x.get("cards")) or [""])[0]) for x in lines]
    if got != step["cards"]:
        raise refuse
    if step["targets"]:
        cast = next(x for x in lines if x["kind"] == "cast")
        if step["targets"] not in _names(s, cast.get("targets")):
            raise refuse
    if step["then"] and (body.get("then") or "continue") not in step["then"]:
        raise refuse


def next_step(store) -> None:
    """説明の段で「次へ」: 段を進め、審判と相手の段を次のホストの段まで書く。説明の段でなければ play.Refused。"""
    with store.lock():
        i = _step(store)
        if i >= len(STEPS) or STEPS[i].get("what") != "next":
            raise play.Refused("tutorial: this step needs the action in the guide")
        accepted(store)
    run(store)


def finish(store, keep: bool) -> None:
    """台本を終えた所で選ぶ。keep: このまま続ける（ふつうの対局にして、審判と相手は Claude Code が回す）。
    そうでなければ相手が投了して終える。台本の途中なら play.Refused。"""
    if _step(store) < len(STEPS) or not active(store) or play.waiting_on(store.load()) is None:
        raise play.Refused("tutorial: the tutorial is not over yet")
    if keep:
        store.write_doc(DOC, {"step": _step(store), "free": True})
        return
    state = store.load()
    store.apply(prompt.intent_batch(state, "p2", _concede(state)))


def accepted(store) -> None:
    """ホストの書き込みが通った: 段を進める。"""
    _set_step(store, _step(store) + 1)


def run(store, report=print) -> None:
    """審判と相手の段を、次のホストの段か決着まで書く。ふだんと同じ自動パス・自動のターン開始も挟む。"""
    for _ in range(len(STEPS) * 2):
        prompt.advance(store)
        i = _step(store)
        state = store.load()
        wait = play.waiting_on(state)
        if i >= len(STEPS) or wait is None or STEPS[i]["role"] == "you":
            return
        step = STEPS[i]
        if wait != step["role"]:
            report("[tutorial] step %d is for %s, but the table waits on %s" % (i, step["role"], wait))
            return
        reply = step["reply"](state)
        if wait == play.JUDGE:
            label, acts = prompt.judge_acts(state, reply)
            batch = {"actor": None, "label": "審判: " + (label or "処理"), "acts": acts}
        else:
            batch = prompt.intent_batch(state, wait, reply)
        result = store.apply(batch)
        if result["stopped"]:
            report("[tutorial] step %d stopped: %s" % (i, result["stopped"]))
            return
        _set_step(store, i + 1)
