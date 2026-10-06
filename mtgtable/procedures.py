"""手順（proc）: 複数の Act を決まった順に並べたものの省略。

Act は「ルール上一体として行う処理の1セット」で、丸ごと適用するか丸ごと取り消す単位・log の1件・
Undo の1単位。ターンを始める・終えるのように複数の Act になる流れを、手順として持つ。

手順は、適用する直前にそのときの状態で Act の並びに展開する（engine.Engine.apply_batch）。
展開した Act は普通の Act と同じに1つずつ適用され、失敗したらその Act だけを取り消して止まる。
upkeep / end / cleanup は Act の並び（{"act": [...]} か手順）。
"""
from __future__ import annotations

from .model import PREGAME
from .operations import Context, OperationError


def _acts(p: dict, key: str) -> list:
    v = p.get(key) or []
    if not isinstance(v, list):
        raise OperationError("%s must be a list of acts" % key)
    return list(v)


def proc_turn_start(ctx: Context, p: dict) -> list:
    """次のターンを始める: アンタップ・ステップ（step untap → untap_all。ゲーム前からは先攻の T1）→
    アップキープの開始 → upkeep の各 Act → ドロー・ステップ（step draw → draw）→ to のステップへ
    （省略でドロー・ステップに留まる）。draw: 引く枚数（既定1。ゲームの最初のターンは0）。as は引いたカード。"""
    first_turn = (ctx.state.turn.phase, ctx.state.turn.step) == PREGAME
    n = p.get("draw", 0 if first_turn else 1)
    n = int(n if not isinstance(n, bool) else (1 if n else 0))
    draw_step = [{"op": "step", "to": "draw"}]
    if n or p.get("as"):
        draw = {"op": "draw", "player": "active", "count": n}
        if p.get("as"):
            draw["as"] = p["as"]
        draw_step.append(draw)
    out = [{"act": [{"op": "step", "to": "untap"}, {"op": "untap_all", "player": "active"}]},
           {"act": [{"op": "step", "to": "upkeep"}]}]
    out += _acts(p, "upkeep") + [{"act": draw_step}]
    if p.get("to") and p["to"] != "draw":
        out.append({"act": [{"op": "step", "to": p["to"]}]})
    return out


def proc_turn_end(ctx: Context, p: dict) -> list:
    """ターンを終える: 終了ステップの開始 → end の各 Act → クリンナップの開始 → cleanup の各 Act
    （手札の上限など）→ until=end_of_turn の Note を外し、全員のマナ・プールを空にする。"""
    return ([{"act": [{"op": "step", "to": "end"}]}] + _acts(p, "end")
            + [{"act": [{"op": "step", "to": "cleanup"}]}] + _acts(p, "cleanup")
            + [{"act": [{"op": "note_remove", "until": "end_of_turn"}, {"op": "mana_clear"}]}])


PROCEDURES: dict = {"turn_start": proc_turn_start, "turn_end": proc_turn_end}


def expand(ctx: Context, proc: dict) -> list:
    """手順を1つ、そのときの状態で Act の並びの要素に展開する。"""
    if proc.get("proc") not in PROCEDURES:
        raise OperationError("unknown proc %r (one of %s)" % (proc.get("proc"), ", ".join(PROCEDURES)))
    return PROCEDURES[proc["proc"]](ctx, {k: v for k, v in proc.items() if k != "proc"})


def describe_procedures() -> list:
    """`ops` コマンド用の一覧（名前と docstring の1行目）。"""
    return [(name, (fn.__doc__ or "").strip().splitlines()[0]) for name, fn in PROCEDURES.items()]
