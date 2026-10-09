"""GUI からの対局（Human vs AI）: 席の鍵、手番の受け渡し、相手を待つ。

人間はブラウザ（`serve --play`）から、AI は CLI（`answer`）から、同じ対局フォルダに書く。席の Player が書けるのは
審判への依頼・回答と、盤面を変えない宣言だけで、盤面は審判が動かす（request_play_design）。
書き込みの排他は GameStore（.lock と expect）が受け持ち、ここでは次の4つを持つ:

- 席の鍵: `invite` が席ごとの鍵を作り、対局フォルダの seats.json にはハッシュだけを置く。
  サーバーは鍵を持つ人にだけ、その席としての書き込みを許す（席を名乗るだけでは書けない）
- 待たれている Player（waiting_on）: 優先権・スタック・ステップから「今、誰が動く番か」を決める。
  GUI の表示と `wait` の両方がこれを使う。エンジンの優先権の記録（pass）と同じ約束事で、ルールの判定はしない
- 待つ: AI が相手（人間）の番の間に止まっておき、自分の番が来たら新しい Act を受け取る（`wait`）
- 席の書き込み: 依頼（request）・宣言（declare）・回答（answer）を卓の宣言に変え、出せる場面かを確かめる（`seat_batch`）
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from typing import Optional

from . import carddb, info
from .model import GameState
from .store import GameStore, _dump


# ---------------------------------------------------------------- 席の鍵

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _seats_path(store: GameStore):
    return store.root / "seats.json"


def seats(store: GameStore) -> dict:
    p = _seats_path(store)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def invite(store: GameStore, seat: str) -> str:
    """seat の鍵を新しく作って返す（前の鍵は使えなくなる）。対局フォルダにはハッシュだけを残す。"""
    state = store.load()
    if seat not in state.players:
        raise ValueError("unknown seat %r (players: %s)" % (seat, ", ".join(state.player_order)))
    token = secrets.token_urlsafe(24)
    with store.lock():
        data = seats(store)
        data[seat] = {"token_sha256": _hash(token)}
        _dump(_seats_path(store), data)
    return token


def check_token(store: GameStore, seat: str, token: Optional[str]) -> bool:
    rec = seats(store).get(seat)
    return bool(token and rec) and secrets.compare_digest(rec["token_sha256"], _hash(token))


# ---------------------------------------------------------------- 手番の受け渡し

JUDGE = "judge"
REQUEST_KINDS = ("intent", "answer", "mulligan")  # Player → 審判。審判が ruled を書くまで審判を待つ（マリガンは審判が引き直す）


# 審判とのやりとりのうち、本人と審判にしか見せないもの（依頼の補足・質問・回答には手札や計画が入る）
PRIVATE_KINDS = info.PRIVATE_KINDS

# 止める場所（非公開）: "own:upkeep"（自分のターンのアップキープ）/ "opp:end"（相手のターンの終了ステップ）/
# "opp:spell"（相手が呪文・能力を積んだとき）/ "opp:attack"（相手が攻撃したとき）。卓の外の席のファイルに置き、
# 本人と審判のプロンプトにだけ出す。AI の Player は、対応したい場面（打ち消しを構えるなど）をここで審判に明示する
STOP_STEPS = ("upkeep", "draw", "main1", "beginning_of_combat", "declare_blockers", "combat_damage",
              "end_of_combat", "main2", "end")
STOP_EVENTS = ("spell", "attack")


def _step_name(t) -> str:
    return t.phase if t.step == "main" else t.step


def needs_player(state: GameState, seat: str, stops: list) -> bool:
    """席 seat（AI・人間とも）が優先権を持つ今、本人に聞く必要があるか（無ければ自動でパスしてよい）。

    聞くのは: 自分のターンでスタックが空（自分のプレイ）、自分の呪文・能力が一番上、攻撃されてブロックを決める所、
    止める場所（own/opp:<ステップ>、opp:spell、opp:attack）に当たる所。それ以外の応答の機会は自動でパスする
    （Player は対応したい場面を止める場所で明示する約束。審判は推測で止めない）。"""
    t = state.turn
    top = state.stack.items[0] if state.stack.items else None
    mine = t.active == seat
    if mine and top is None:
        return True
    if top is not None and top.controller == seat:
        return True
    if blocks_undecided(state, seat):
        return True  # ブロックを決める（優先権のパスではない）
    if not can_respond(state, seat):
        return False  # 止める場所に当たっても、何もできないなら聞かない
    if top is not None and "opp:spell" in stops:
        return True
    if not mine and state.combat.attacks and "opp:attack" in stops:
        return True
    return ("own:" if mine else "opp:") + _step_name(t) in stops


_MANA_SYMBOL = re.compile(r"\{(?:\d+|[WUBRGCSX](?:/[WUBRGCP])?)\}")
_LOYALTY_COST = re.compile(r"^[+\-−]?(?:\d+|X)$")
_FREE_CAST = ("rather than pay", "without paying")


def _oracle(card) -> Optional[dict]:
    """カードのオラクル（タイプ行・コスト・文。両面は全部の面をつなぐ）。分からなければ None。"""
    d = card.definition or {}
    if card.token and not d.get("copy_of"):  # 定義に文が無いトークン（兵士など）は、能力の無いものとして扱う
        return {"type_line": d.get("type_line") or card.type_line, "mana_cost": d.get("mana_cost") or "",
                "oracle_text": d.get("text") or d.get("oracle_text") or "", "keywords": []}
    rec = carddb.lookup(card.name, offline=True) if card.name else None
    if not rec:
        return None
    faces = rec.get("faces") or [rec]
    return {"type_line": " // ".join(f.get("type_line") or "" for f in faces) or rec.get("type_line") or "",
            "mana_cost": rec.get("mana_cost") or faces[0].get("mana_cost") or "",
            "oracle_text": "\n".join(f.get("oracle_text") or "" for f in faces),
            "keywords": rec.get("keywords") or []}


def _free_ability(text: str) -> bool:
    """マナも {T} も要らない起動型能力（「生け贄に捧げる: …」など。タップ状態でも起動できる）があるか。"""
    for line in text.split("\n"):
        line = re.sub(r"\([^)]*\)", "", line)  # 注釈文
        if ":" not in line:
            continue
        cost = line.split(":", 1)[0].strip()
        if not cost or _LOYALTY_COST.match(cost) or len(cost) > 80:
            continue  # 忠誠度能力（相手のターンには起動できない）・コストではない長い文
        if "{T}" in cost or "{Q}" in cost or _MANA_SYMBOL.search(cost):
            continue
        return True
    return False


def can_respond(state: GameState, seat: str) -> bool:
    """seat が今、優先権で何かできる見込みがあるか（無ければ止める場所に当たっても自動でパスする）。

    控えめに判断する（分からないものは「できる」）: マナ・プールにマナがある、アンタップ状態のパーマネントがある
    （マナ能力・{T} の能力）、タップ状態でもマナも {T} も要らない起動型能力を持つパーマネントがある、手札に 0 マナか
    代替コスト（「〜を支払うのではなく」「マナ・コストを支払うことなく」）で唱えられるインスタント・瞬速のカードがある、
    オラクルの分からないカードがある。"""
    if state.players[seat].mana_pool.mana:
        return True
    for cid in state.zones["battlefield"].cards:
        c = state.cards[cid]
        if c.controller != seat:
            continue
        if not c.tapped:
            return True
        o = _oracle(c)
        if o is None or (not c.face_down and _free_ability(o["oracle_text"])):
            return True
    for cid in state.zones["%s.hand" % seat].cards:
        o = _oracle(state.cards[cid])
        if o is None:
            return True
        text = o["oracle_text"].lower()
        instant = "Instant" in o["type_line"] or "Flash" in o["keywords"] or re.search(r"(?m)^flash\b", text)
        if any(p in text for p in _FREE_CAST):
            return True
        if instant and "Land" not in o["type_line"] and o["mana_cost"] in ("", "{0}"):
            return True
    return False


BLOCK_STEPS = ("declare_attackers", "declare_blockers")


def blocks_undecided(state: GameState, seat: str) -> bool:
    """攻撃されている seat が、まだブロックを決めていないか。攻撃・ブロックのステップで、seat のブロックが無く、
    この2つのステップで seat が no_block も、審判がまだ処理していないブロックの依頼（「ブロックする」「ブロックしない」の行）も
    出していない。審判が declare_blockers へ進めてから優先権を渡しても、自動パスで飛ばさない（ブロックは GUI・依頼で決める）。
    審判が処理したのにブロックも no_block も書かれていない依頼（ルールに合わず差し戻した）は数えない（決め直す）。"""
    t = state.turn
    if t.active == seat or t.step not in BLOCK_STEPS or not state.combat.attacks:
        return False
    if any(state.cards[b.blocker].controller == seat for b in state.combat.blocks if b.blocker in state.cards):
        return False
    last_ruled = max((d.seq for d in state.declarations if d.kind == "ruled"), default=0)
    return not any(d.player == seat and d.turn == t.turn and d.step in BLOCK_STEPS
                   and (d.kind == "no_block" or d.kind == "intent" and d.seq > last_ruled
                        and ("ブロック" in d.text or "block" in d.text.lower()))
                   for d in state.declarations)


def autopass(store: GameStore, limit: int = 20) -> list:
    """優先権を持つ席（AI・人間とも）に本人に聞く必要が無ければ、その席として自動でパスする（API を呼ばない・
    人間にパスを押させない）。書いた件の結果を返す。"""
    done = []
    for _ in range(limit):
        state = store.load()
        seat = state.turn.priority
        if (seat not in state.players or state.turn.turn == 0 or waiting_on(state) != seat
                or needs_player(state, seat, get_stops(store, seat))):
            break
        r = store.apply({"actor": seat, "label": "自動パス（止める場所に当たらない）",
                         "acts": [{"act": [{"op": "pass"}], "label": "自動パス（止める場所に当たらない）"}]})
        r.pop("_state", None)
        done.append(r)
        if r["stopped"]:
            break
    return done


def _private_path(store: GameStore, seat: str):
    return store.root / "private" / ("%s.json" % seat)


def get_stops(store: GameStore, seat: str) -> list:
    p = _private_path(store, seat)
    return list(json.loads(p.read_text(encoding="utf-8")).get("stops", [])) if p.exists() else []


def check_stops(codes) -> list:
    if not isinstance(codes, list) or not all(
            isinstance(c, str) and c.count(":") == 1 and c.split(":")[0] in ("own", "opp")
            and c.split(":")[1] in STOP_STEPS + STOP_EVENTS for c in codes):
        raise ValueError('stops must be a list like ["own:upkeep", "opp:end", "opp:spell"]')
    return list(dict.fromkeys(codes))


def set_stops(store: GameStore, seat: str, codes) -> list:
    codes = check_stops(codes)
    p = _private_path(store, seat)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data["stops"] = codes
    _dump(p, data)
    return codes


def requests(state: GameState) -> dict:
    """審判とのやりとりの未処理分。

    pending: 最後の ruled より後の intent / answer（審判が処理する）
    asks: まだ回答の無い ask（その Player が答える）。回答は、ask より後の同じ Player の answer"""
    decls = state.declarations
    last_ruled = max((d.seq for d in decls if d.kind == "ruled"), default=0)
    pending = [d for d in decls if d.kind in REQUEST_KINDS and d.seq > last_ruled]
    asks = [d for d in decls if d.kind == "ask"
            and not any(x.kind == "answer" and x.player == d.player and x.seq > d.seq for x in decls)]
    return {"pending": pending, "asks": asks}


def mulligans(state: GameState, pid: str) -> int:
    """ゲーム前に pid がマリガンした回数（キープしたら、この枚数を手札からライブラリーの下へ置く）。"""
    return sum(1 for d in state.declarations if d.turn == 0 and d.player == pid and d.kind == "mulligan")


def bottomed(store: GameStore, pid: str) -> int:
    """ゲーム前の最後のマリガンより後に、pid がライブラリーの下へ置いた枚数（記録から数える）。"""
    n = 0
    for e in store.read_log()[:store.cursor()]:
        if e["actor"] != pid:
            continue
        for st in e["steps"]:
            op = st["op"]
            if op["op"] == "declare" and op.get("kind") == "mulligan":
                n = 0
            elif op["op"] == "move" and op.get("position") == "bottom" and str(op.get("to", "")).endswith("library"):
                n += len(op["card"]) if isinstance(op["card"], list) else 1
            elif op["op"] == "step":
                return n  # T1 に入った後は数えない
    return n


def judged_game(state: GameState) -> bool:
    """審判が卓を動かしている対局か（審判の処理の印 ruled がある）。"""
    return any(d.kind == "ruled" for d in state.declarations)


def resolve_pending(state: GameState) -> bool:
    """審判のいる対局で、全員がパスしてスタックが残っている（審判が一番上を解決する）。"""
    return judged_game(state) and not state.turn.priority and bool(state.stack.items) and not requests(state)["asks"]


def next_turn_player(state: GameState) -> Optional[str]:
    """クリンナップで止まっているとき、次のターンの Player（そうでなければ None）。"""
    t = state.turn
    if t.turn == 0 or t.step != "cleanup" or not t.active:
        return None
    order = state.player_order
    return order[(order.index(t.active) + 1) % len(order)]


def turn_start_pending(state: GameState) -> bool:
    """審判のいる対局で、クリンナップで止まっている（審判が次のターンを始める。Player の依頼を待たない）。"""
    t = state.turn
    req = requests(state)
    return (judged_game(state) and next_turn_player(state) is not None and not t.priority
            and not state.stack.items and not req["pending"] and not req["asks"])


def request_then(text: str) -> Optional[str]:
    """依頼の文（request_text）の「その後: …」から then を読み戻す（end_turn / ステップ名など。読めなければ None）。"""
    m = re.search(r"(?m)^その後: (.+)$", text or "")
    if not m:
        return None
    word = m.group(1).strip()
    for k, v in THEN_JA.items():
        if word == v:
            return k
    step = re.match(r"^(\w+) まで進める$", word)
    return step.group(1) if step and step.group(1) in STEP_THEN else None


def continued_label(seq: int) -> str:
    """審判が依頼 #seq の続きを処理した印（ruled の文の頭。prompt.judge_acts が付ける）。"""
    return "#%d の続き" % seq


# ---- 進行中の計画（アクティブ・プレイヤーの依頼）と割り込み
#
# 依頼（intent）の行動の行は、審判が途中で止めることがある（相手の止める場所・ブロック・引いたカードを見て決める所）。
# 審判は止めるとき、まだ書いていない行の番号を返答の rest で知らせ、卓は ruled に of（計画の seq）と rest を残す。
# - 相手が対応しなかった（パス・ブロックしない）: 審判が残りと「その後」を続ける（continue_pending）
# - 相手が割り込んだ（呪文・能力・ブロックなどの依頼）・本人が決める所で止めた: 続けない。本人の番になったら、
#   残りの行と「その後」を本人に戻す（resume。GUI は下書きに戻し、AI はプロンプトで読む）。直してから送り直せる

def plan_in_progress(state: GameState):
    """このターンのアクティブ・プレイヤーの最後の依頼（無ければ None）。"""
    t = state.turn
    return max((d for d in state.declarations if d.kind == "intent" and d.player == t.active and d.turn == t.turn),
               key=lambda d: d.seq, default=None)


def plan_ruled(state: GameState, intent, reported: bool = False):
    """審判が計画 intent を最後に処理した ruled（reported なら、残りを知らせたもの）。無ければ None。"""
    return max((d for d in state.declarations if d.kind == "ruled" and d.of == intent.seq
                and (d.rest is not None or not reported)), key=lambda d: d.seq, default=None)


def plan_rest(state: GameState, intent) -> Optional[list]:
    """計画のまだ書いていない行の番号。行が無ければ []、審判が知らせていなければ None（分からない）。"""
    if not intent.plan:
        return []
    r = plan_ruled(state, intent, reported=True)
    return [i for i in r.rest if 1 <= i <= len(intent.plan)] if r else None


def intent_then(intent) -> Optional[str]:
    return intent.then or request_then(intent.text)


def then_pending(state: GameState, intent) -> bool:
    """依頼の「その後」（ターン終了・ステップまで進める）がまだ済んでいないか。"""
    t = state.turn
    then = intent_then(intent)
    if then == "end_turn":
        return t.step != "cleanup"
    now = _step_name(t)
    return then in STEP_THEN and now in STEP_THEN and STEP_THEN.index(then) > STEP_THEN.index(now)


def _no_action(d) -> bool:
    """相手の依頼が、盤面に割り込まないもの（行動の行が無い・「ブロックしない」だけ）か。"""
    if d.plan:
        return all("ブロックしない" in str(x.get("text", "")) for x in d.plan)
    return "ブロックしない" in d.text or not re.search(r"(?m)^行動:", d.text)


def interrupted(state: GameState, intent) -> bool:
    """計画を審判が止めた後（知らせが無ければ依頼の後）に、相手が割り込む依頼（呪文・能力・ブロックなど）を出したか。"""
    r = plan_ruled(state, intent)
    since = r.seq if r else intent.seq
    return any(d.kind == "intent" and d.player != intent.player and d.seq > since and not _no_action(d)
               for d in state.declarations)


def plan_unfinished(state: GameState, intent) -> bool:
    rest = plan_rest(state, intent)
    return bool(rest) or then_pending(state, intent)


def continue_pending(state: GameState) -> Optional[object]:
    """審判のいる対局で、アクティブ・プレイヤーの計画（依頼）が途中で、相手が対応しなかった（パス・ブロックしない）なら、
    その依頼（審判が残りの行と「その後」を続ける）。スタックが空で、全員がパスしたか、優先権がアクティブ・プレイヤーに
    戻った所で。

    相手が割り込んだら続けない（本人に戻す: resume）。審判が止めた後に相手がパスしていなければ（本人が決める所で
    止めた）続けない。審判が続きを処理して止めたなら（最後の ruled がその依頼の続き）、その後に誰かがパスするまでは
    呼ばない（同じ所で審判を呼び続けない）。"""
    t = state.turn
    if (t.turn == 0 or state.stack.items or t.step == "cleanup" or t.priority not in (None, t.active)
            or not judged_game(state)):
        return None
    req = requests(state)
    if req["pending"] or req["asks"]:
        return None
    intent = plan_in_progress(state)
    if intent is None or not plan_unfinished(state, intent) or interrupted(state, intent):
        return None
    decls = state.declarations
    r = plan_ruled(state, intent)
    since = r.seq if r else intent.seq
    if not any(d.player != t.active and d.kind in ("pass", "no_block") and d.seq > since for d in decls):
        return None  # 相手の応答を待って止めたのではない（本人が決める所で止めた）
    last_ruled = max((d for d in decls if d.kind == "ruled"), key=lambda d: d.seq, default=None)
    tried = last_ruled is not None and last_ruled.text.startswith(continued_label(intent.seq))
    if tried and not any(d.kind == "pass" and d.seq > last_ruled.seq for d in decls):
        return None
    return intent


def resume(state: GameState, seat: str) -> Optional[dict]:
    """seat の計画が途中で止まり（相手の割り込み・本人が決める所）、seat の番になったなら、残りの行と「その後」。
    {"of", "id", "lines", "then", "unknown"}。unknown: 審判がどこまで書いたか知らせていない（行は全部を返す）。"""
    t = state.turn
    if t.turn == 0 or t.active != seat or state.stack.items or waiting_on(state) != seat:
        return None
    intent = plan_in_progress(state)
    if intent is None or intent.player != seat:
        return None
    rest = plan_rest(state, intent)
    unknown = rest is None
    if unknown:
        if not interrupted(state, intent):
            return None
        rest = list(range(1, len(intent.plan) + 1))
    then = intent_then(intent) or "continue"
    if then != "continue" and then != "pass" and not then_pending(state, intent):
        then = "continue"
    if not rest and then in ("continue", "pass"):
        return None
    r = plan_ruled(state, intent)
    return {"of": intent.seq, "id": "%d:%d" % (intent.seq, r.seq if r else 0),
            "lines": [intent.plan[i - 1] for i in rest], "then": then, "unknown": unknown}


def game_start_pending(state: GameState) -> bool:
    """全員がキープし、審判がまだゲーム開始の処理（マリガンで下に置くカード・開始時の手札から使うカード）をしていないか。
    審判の処理は、最後のキープより後の ruled で終わったことになる。"""
    if state.turn.turn != 0:
        return False
    live = [p for p in state.player_order if state.players[p].status == "playing"]
    keeps = [d for d in state.declarations if d.turn == 0 and d.kind == "keep"]
    if not live or not all(any(d.player == p for d in keeps) for p in live):
        return False
    last = max(d.seq for d in keeps)
    return not any(d.kind == "ruled" and d.seq > last for d in state.declarations)


def waiting_on(state: GameState) -> Optional[str]:
    """今、卓が誰の操作を待っているか（Player の id か "judge"）。

    - 審判への依頼・回答が未処理なら審判。審判の質問に未回答なら、質問された Player
    - 優先権を持つ Player がいればその Player（呪文を唱えた後に相手へ渡すのは pass）
    - 全員がパスしてスタックが空で、アクティブ・プレイヤーの依頼の「その後」が済んでいなければ審判（continue_pending）
    - 全員がパスして優先権が空なら、スタックの一番上のコントローラー（解決する）。スタックが空ならアクティブ・
      プレイヤー（ステップを進める）
    - クリンナップで止まっていれば、審判のいる対局では審判（次のターンを始める: turn_start_pending）、
      審判のいない対局では次のターンの Player（turn_start する）
    - ゲーム前は、先攻からターン順で、まだキープを宣言していない Player。全員キープしたら審判（ゲーム開始の処理）、
      その後は先攻
    """
    t = state.turn
    live = [p for p in state.player_order if state.players[p].status == "playing"]
    if len(live) < 2 and len(state.player_order) > 1:
        return None  # 決着した
    req = requests(state)
    if req["pending"]:
        return JUDGE
    if req["asks"]:
        return req["asks"][0].player
    if t.turn == 0:
        order = state.player_order
        start = order.index(t.active) if t.active in order else 0
        kept = {d.player for d in state.declarations if d.turn == 0 and d.kind == "keep"}
        for pid in order[start:] + order[:start]:
            if pid in live and pid not in kept:
                return pid
        return JUDGE if game_start_pending(state) else t.active
    if t.priority:
        return t.priority
    if continue_pending(state):
        return JUDGE  # 依頼の「その後」（ターン終了など）の続きを審判が処理する
    if state.stack.items:
        # 全員がパスした。審判のいる対局（ruled がある）では、解決は審判がする。審判のいない対局ではコントローラー
        return JUDGE if judged_game(state) else state.stack.items[0].controller
    if turn_start_pending(state):
        return JUDGE
    return next_turn_player(state) or t.active


def decorate(view: dict, state: GameState) -> dict:
    """view に GUI 用の派生値を足す。依頼・質問は、見る人の分だけ（judge は全部）。"""
    view["turn"]["waiting_on"] = waiting_on(state)
    viewer = view.get("viewer")
    if state.turn.turn == 0 and viewer in state.players:
        # マリガンの後、キープするときに下へ置く枚数: 今の手札 −（初期手札 − マリガン回数）
        n = mulligans(state, viewer)
        size = len(state.zones["%s.hand" % viewer].cards)
        view["pregame"] = {"mulligans": n, "to_bottom": max(0, size - max(0, state.meta.get("hand", 7) - n))}
    req = requests(state)
    mine = lambda d: viewer is None or d.player == viewer  # noqa: E731
    view["requests"] = {
        "pending": [{"seq": d.seq, "player": d.player, "kind": d.kind, "text": d.text} for d in req["pending"] if mine(d)],
        "asks": [dict({"seq": d.seq, "player": d.player, "text": d.text, "choices": d.choices},
                      **({"cards": d.cards, "pick": d.pick} if d.cards else {})) for d in req["asks"] if mine(d)]}
    if viewer in state.players:
        view["resume"] = resume(state, viewer)  # 途中で止まった自分の計画の残り（GUI が下書きに戻す）
    view["card_kinds"] = card_kinds(view, state)
    for c in view["zones"]["battlefield"].get("cards") or []:
        pt = base_pt(state, c.get("id"))
        if pt:
            c["base_pt"] = pt
        kind = attackable(state, c.get("id"))
        if kind:
            c["attackable"] = kind
    return view


def attackable(state: GameState, cid: Optional[str]) -> Optional[str]:
    """戦場のカードが攻撃先になりうる種類か: 上を向いている面のタイプ行が Planeswalker なら "planeswalker"、
    Battle なら "battle"。GUI の攻撃先の候補用（攻撃できるか・誰が守るかの判断はしない。審判が行う）。"""
    card = state.cards.get(cid) if cid else None
    if card is None or card.face_down or not card.type_line:
        return None
    faces = card.type_line.split("//")
    face = faces[card.face] if 0 <= card.face < len(faces) else faces[0]
    if "Planeswalker" in face:
        return "planeswalker"
    if "Battle" in face:
        return "battle"
    return None


_PRINTED_PT: dict = {}  # (名前, 面) → P/T か ()（P/T が無い）。キャッシュに無いカードは覚えない（後で取得されうる）


def _printed_pt(name: str, face: int) -> Optional[tuple]:
    key = (name, face)
    if key not in _PRINTED_PT:
        rec = carddb.lookup(name, offline=True)
        if not rec:
            return None
        faces = rec.get("faces") or [rec]
        r = faces[face] if 0 <= face < len(faces) else faces[0]
        has = r.get("power") is not None and r.get("toughness") is not None
        _PRINTED_PT[key] = (str(r["power"]), str(r["toughness"])) if has else ()
    return _PRINTED_PT[key] or None


def base_pt(state: GameState, cid: Optional[str]) -> Optional[list]:
    """戦場のカードの、印刷された（トークンは定義の）パワー・タフネス。GUI が戦闘中に、カウンター・Note の
    「+2/+2」などを足して出す（ルールの判断はしない。表示の目安）。裏向き・P/T の無いカードは None。"""
    card = state.cards.get(cid) if cid else None
    if card is None or card.face_down:
        return None
    d = card.definition or {}
    if d.get("power") is not None and d.get("toughness") is not None:
        return [str(d["power"]), str(d["toughness"])]
    pt = _printed_pt(card.name, card.face) if card.name else None
    return list(pt) if pt else None


def card_kinds(view: dict, state: GameState) -> dict:
    """見えているカード（戦場の外）の種類: GUI のメニュー用。land: 土地として出せる（どちらかの面が土地）、
    spell: 唱えられる（土地でない面がある）、
    to: 解決したときの行き先（インスタント・ソーサリーは graveyard、それ以外は battlefield。表面のタイプ行で決める）。"""
    out = {}
    for key, z in view["zones"].items():
        if key == "battlefield":
            continue
        for part in ("cards", "known", "known_positions", "known_unordered"):
            for c in z.get(part) or []:
                card = state.cards.get(c.get("id")) if isinstance(c, dict) and c.get("name") else None
                if card is None or not card.type_line or card.face_down:
                    continue
                faces = card.type_line.split("//")
                kind = {"to": "graveyard" if ("Instant" in faces[0] or "Sorcery" in faces[0]) else "battlefield"}
                if any("Land" in f for f in faces):
                    kind["land"] = True
                if any(f.strip() and "Land" not in f for f in faces):
                    kind["spell"] = True
                out[card.id] = kind
    return out


# ---------------------------------------------------------------- 席の書き込み（依頼・宣言・回答だけ）

# 席の Player は卓に op を書けない。書けるのは審判への依頼（intent）・回答（answer）と、盤面を変えない宣言だけ。
# 盤面を変えるのは審判（request_play_design）。ここで席の API の本文を卓の宣言に変え、出せる場面かを確かめる。

class Refused(ValueError):
    """席がその書き込みをしてよい場面ではない（サーバーは 403 で断る）。"""


PLAN_KINDS = ("play_land", "cast", "activate", "attack", "block", "resolve", "target",
              "draw", "look", "reveal", "mill", "shuffle", "search", "step", "other")  # step: to のステップまで進める
STEP_THEN = ("upkeep", "draw", "main1", "beginning_of_combat", "declare_attackers", "declare_blockers",
             "combat_damage", "end_of_combat", "main2", "end")
THEN_JA = {"continue": "続ける（まだ自分の番）", "pass": "パス（相手に渡す）",
           "resolve": "解決まで（相手が対応しなければ、積んだものを解決して自分の番を続ける）", "end_turn": "ターン終了",
           "turn_start": "自分のターンを始める"}
DECLARE_KINDS = ("keep", "mulligan", "pass", "concede", "say")
MAX_PLAN = 40
MAX_TEXT = 1000
MAX_MEMO = 2000  # AI の memo（本人のプロンプトに戻るだけだが、際限なく膨らまないように切り詰める）


def _then_text(then: str) -> str:
    return THEN_JA.get(then) or "%s まで進める" % then


def _ref_ok(state: GameState, seat: str, ref: str, cards_only: bool) -> bool:
    if ref in state.cards:
        return info.can_reference(state, seat, ref)
    if cards_only:
        return False
    # マナ・プールのマナ（だれにも見える）は、使う行の対象に書ける
    return (ref in state.players or any(it.id == ref for it in state.stack.items)
            or any(m.id == ref for p in state.players.values() for m in p.mana_pool.mana))


def _refs(state: GameState, seat: str, value, what: str, cards_only: bool) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(r, str) for r in value) or len(value) > 60:
        raise ValueError("%s must be a list of ids" % what)
    out = [r if r in state.players else "#" + r.lstrip("#") for r in value]
    bad = [r for r in out if not _ref_ok(state, seat, r, cards_only)]
    if bad:
        raise Refused("%s: %s is not known to %s" % (what, ", ".join(bad), seat))
    return out


def _text(value, what: str, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError("%s must be a string" % what)
    value = value.strip()
    if required and not value:
        raise ValueError("%s is required" % what)
    if len(value) > MAX_TEXT:
        raise ValueError("%s is too long (max %d)" % (what, MAX_TEXT))
    return value


def _check_plan(state: GameState, seat: str, plan) -> list:
    if plan is None:
        return []
    if not isinstance(plan, list) or len(plan) > MAX_PLAN:
        raise ValueError("plan must be a list of at most %d lines" % MAX_PLAN)
    out = []
    for i, line in enumerate(plan, 1):
        if not isinstance(line, dict):
            raise ValueError("plan line %d must be an object" % i)
        kind = line.get("kind", "other")
        if kind not in PLAN_KINDS:  # 知らない種類は other に（文は審判が読む）
            kind = "other"
        row = {"kind": kind, "text": _text(line.get("text"), "plan line %d text" % i, required=True)}
        cards = _refs(state, seat, line.get("cards"), "plan line %d cards" % i, cards_only=True)
        targets = _refs(state, seat, line.get("targets"), "plan line %d targets" % i, cards_only=False)
        if cards:
            row["cards"] = cards
        if targets:
            row["targets"] = targets
        if line.get("count") is not None:
            n = line["count"]
            if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 99:
                raise ValueError("plan line %d: count must be 1-99" % i)
            row["count"] = n
        if kind == "step" and line.get("to") in STEP_THEN:
            row["to"] = line["to"]
        out.append(row)
    return out


def request_text(plan: list, then: str, comment: str) -> str:
    """依頼の文（本人と審判が読む）: 「行動: 1. …」「その後: …」「補足: …」。"""
    lines = []
    if plan:
        lines.append("行動:")
        lines += ["%d. %s" % (i, row["text"]) for i, row in enumerate(plan, 1)]
    lines.append("その後: %s" % _then_text(then))
    if comment:
        lines.append("補足: %s" % comment)
    return "\n".join(lines)


def _asked(state: GameState, seat: str) -> bool:
    return any(d.player == seat for d in requests(state)["asks"])


def _playing(state: GameState, seat: str) -> None:
    if seat not in state.players:
        raise Refused("unknown seat %r" % seat)
    if state.players[seat].status != "playing" or waiting_on(state) is None:
        raise Refused("the game is over for %s" % seat)


def _not_busy(state: GameState, seat: str) -> None:
    if waiting_on(state) == JUDGE:
        raise Refused("the judge is processing; wait for the ruling")
    if _asked(state, seat):
        raise Refused("the judge is asking %s a question; answer it first" % seat)


def seat_request(state: GameState, seat: str, body: dict) -> tuple:
    """依頼 {"plan": [{"kind", "text", "cards", "targets", "count"}], "then", "comment"} を卓の宣言に。
    返り値は (label, ops)。plan は op に展開せず、文と一緒に宣言に残す（審判が読んで卓に書く）。
    ゲーム前も出せる（盤面がおかしいという報告、開始時の手札から使うカードの申し出など）。"""
    _playing(state, seat)
    _not_busy(state, seat)
    plan = _check_plan(state, seat, body.get("plan"))
    then = body.get("then") or "continue"
    comment = _text(body.get("comment"), "comment")
    if not isinstance(then, str) or (then not in THEN_JA and then not in STEP_THEN):
        # 知らない「その後」は続けるにして、元の指定は補足に残す（審判が読んで判断する）
        comment = ("%s\n" % comment if comment else "") + "その後の指定: %s" % then
        comment = comment[:MAX_TEXT]
        then = "continue"
    if not plan and not comment and then == "continue":
        raise ValueError("the request is empty (add a plan line or a comment)")
    op = {"op": "declare", "kind": "intent", "text": request_text(plan, then, comment), "then": then}
    if plan:
        op["plan"] = plan
    return "依頼", [op]


def seat_declare(state: GameState, seat: str, body: dict) -> tuple:
    """決定の宣言 {"kind", "text"}（keep / mulligan / pass / concede / say）を卓の op に。返り値は (label, ops)。
    マリガンも宣言だけ（手札を戻す・シャッフル・引き直しは審判が書く）。"""
    kind = body.get("kind")
    if kind not in DECLARE_KINDS:
        raise ValueError("kind must be one of %s" % ", ".join(DECLARE_KINDS))
    text = _text(body.get("text"), "text", required=kind == "say")
    _playing(state, seat)
    if kind in ("keep", "mulligan"):
        _not_busy(state, seat)
        if state.turn.turn != 0:
            raise Refused("%s is only before the game starts" % kind)
        if any(d.turn == 0 and d.player == seat and d.kind == "keep" for d in state.declarations):
            raise Refused("%s has already kept" % seat)
        if waiting_on(state) != seat:
            raise Refused("it is not %s's turn to decide on the opening hand" % seat)
        return {"keep": "キープ", "mulligan": "マリガン"}[kind], [{"op": "declare", "kind": kind, "text": text}]
    if kind == "pass":
        _not_busy(state, seat)
        if state.turn.turn == 0 or state.turn.priority != seat:
            raise Refused("%s does not have priority" % seat)
        return "パス", [{"op": "pass"}]
    if waiting_on(state) == JUDGE:  # 審判の処理中は何も書かない（処理が済んでから）
        raise Refused("the judge is processing; wait for the ruling")
    label = {"concede": "投了", "say": "発言"}[kind]
    return label, [{"op": "declare", "kind": kind, "text": text}]


def seat_answer(state: GameState, seat: str, body: dict) -> tuple:
    """審判の質問への回答 {"text", "cards"} を卓の宣言に。返り値は (label, ops)。"""
    _playing(state, seat)
    if not _asked(state, seat):
        raise Refused("there is no question to %s" % seat)
    text = _text(body.get("text"), "text")
    cards = body.get("cards")
    if cards is not None:  # カードを選ぶ質問への回答（選ばないなら []）。候補か・枚数は卓（op_declare）が確かめる
        cards = _refs(state, seat, cards, "cards", cards_only=True)
        names = ", ".join("%s <%s>" % (c, state.cards[c].name) for c in cards)
        text = text or names or "選ばない"
        return "回答: %s" % text, [{"op": "declare", "kind": "answer", "text": text, "cards": cards}]
    if not text:
        raise ValueError("text is required")
    return "回答: %s" % text, [{"op": "declare", "kind": "answer", "text": text}]


SEAT_WRITES = {"request": seat_request, "declare": seat_declare, "answer": seat_answer}


def seat_batch(state: GameState, seat: str, what: str, body: dict) -> dict:
    """席の書き込み（request / declare / answer）を、その席を actor にした Batch に。"""
    if what not in SEAT_WRITES:
        raise ValueError("unknown seat write %r" % what)
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    label, ops = SEAT_WRITES[what](state, seat, body)
    return {"actor": seat, "label": label, "acts": [{"act": ops, "label": label}]}


# ---------------------------------------------------------------- 公開範囲

def judged_batches(entries: list) -> set:
    """審判の Batch（最後に ruled を書く）の番号。"""
    return {e["batch"] for e in entries if _rules(e)}


def _private_decl(e: dict) -> bool:
    return any(op["op"] == "declare" and op.get("kind") in PRIVATE_KINDS for op in e["act"])


# ---------------------------------------------------------------- 待つ

def _mine(entry: dict, me: str) -> bool:
    """me が書いた件か（me が代理で書いた相手の宣言も含む）。"""
    return entry.get("proxy_by") == me if entry.get("proxy_by") else entry["actor"] == me


def _declares(entry: dict) -> bool:
    return any(s["op"]["op"] in ("declare", "player_set") for s in entry["steps"])


def _rules(e: dict) -> bool:
    """審判が依頼を処理した印（ruled）を書いた件か。"""
    return any(s["op"]["op"] == "declare" and s["op"].get("kind") == "ruled" for s in e["steps"])


def last_own(store: GameStore, me: str) -> int:
    """me が最後に書いた件の番号（無ければ 0）。wait はここより後を見るので、apply と wait の間に
    相手が書いても取りこぼさない。"""
    for e in reversed(store.read_log()[:store.cursor()]):
        if _mine(e, me):
            return e["seq"]
    return 0


def wait(store: GameStore, me: str, since: Optional[int] = None, timeout: float = 600,
         interval: float = 0.5, any_change: bool = False) -> Optional[dict]:
    """自分（me）の番が来るまで待ち、since（省略で me が最後に書いた件）より後の件を返す。時間切れは None。

    相手の操作が1件ずつ届いても（人間がタップや土地を1つずつ書く）、待たれているのが自分になるか、
    相手の宣言（キープ・マリガン・ブロックなし・投了など）があるか、決着するまでは返さない。
    巻き戻された（cursor が since より前に戻った）ときも返す。any_change なら相手の書き込みごとに返す。"""
    start = last_own(store, me) if since is None else since
    deadline = time.monotonic() + timeout
    while True:
        cur = store.cursor()
        if cur < start:
            return {"from": start, "cursor": cur, "entries": [], "undone": True}
        theirs = [e for e in store.read_log()[start:cur] if not _mine(e, me)]
        if theirs and (any_change or waiting_on(store.load()) in (me, None) or any(map(_declares, theirs))):
            cur = store.cursor()  # 判定の間に相手がさらに書いていても、返すのは今の時点まで
            return {"from": start, "cursor": cur, "entries": store.read_log()[start:cur], "undone": False}
        if time.monotonic() > deadline:
            return None
        time.sleep(interval)


# ---------------------------------------------------------------- Player に見せる log

def player_log(store: GameStore, seat: str) -> list:
    """Player の席に見せる log。Act ごとの actor・ラベル・op の要約だけで、event（全情報）は出さない。

    今の CLI の対局で AI が報告に出している `log` と同じ範囲（ラベルと op の要約。カード名は id のまま）。
    見る人ごとの対局の記録（ai_play_and_log_design 6.2）ができたら置き換える。"""
    from .operations import summarize_op
    entries = store.read_log()[:store.cursor()]
    judged = judged_batches(entries)

    def _judged(e):
        return e.get("batch") in judged

    out = []
    for e in entries:
        prev_cont = e["seq"] > 1 and entries[e["seq"] - 2].get("cont")
        if _private_decl(e) and e["actor"] != seat:  # 相手の依頼・回答・質問は中身を伏せる
            out.append({"seq": e["seq"], "version": e["version"], "batch": e.get("batch"), "batch_label": "",
                        "actor": e["actor"] or "judge", "label": "（審判とのやりとり）", "summary": "", "proc": "",
                        "proxy_by": None, "cont": False, "continued": False, "undone": False, "steps": [],
                        "mine": False, "judge": _judged(e), "request": False, "texts": []})
            continue
        out.append({
            "seq": e["seq"], "version": e["version"], "batch": e.get("batch"),
            "batch_label": e.get("batch_label", ""), "actor": e["actor"] or "judge",
            "label": e["label"], "summary": "; ".join(summarize_op(op) for op in e["act"]),
            "proc": e.get("proc", ""), "proxy_by": e.get("proxy_by"),
            "cont": bool(e.get("cont")), "continued": bool(prev_cont), "undone": False, "steps": [],
            "mine": e["actor"] == seat and not e.get("proxy_by") and not _judged(e),
            # 審判の処理（ruled を含む Batch の件）か、審判への依頼・回答か（GUI の「送った依頼」の一覧）
            "judge": _judged(e),
            "request": any(op["op"] == "declare" and op.get("kind") in REQUEST_KINDS for op in e["act"]),
            "texts": [op.get("text", "") for op in e["act"] if op["op"] == "declare" and op.get("kind") in REQUEST_KINDS],
        })
    return out
